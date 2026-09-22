"""
本地预载服务器 (Local Preload Server)
=====================================
这是老师要求的「本地服务器 / 数据缓存层」核心组件。

它做四件事：
  1. **提前预载**：后台线程按固定周期，主动去上游（Open-Meteo / WAQI /
     MongoDB Atlas / 大模型）把数据拉回来，写入本地快照仓库 (data_store)。
  2. **隔离外呼**：用户请求链路（main.py → *_service.py）只读本地快照，
     绝不直接访问第三方网站 / 外部数据库，规避侵权与安全风险。
     ——「只有预载线程会调用 upstream_client」是硬性原则。
  3. **高可用**：上游偶发故障时，仍用上一次快照对外服务；进程重启后
     也能立刻用磁盘上的快照服务（零冷启动）。
  4. **抗崩溃**（2026-09-22 升级）：
       * 只有**成功**才写快照，失败只 `mark_failure`（保留旧数据，绝不覆盖）；
       * 单次调用带硬超时 + 指数退避重试；
       * 每个数据源配**熔断器**：连续失败达阈值后暂停一段时间不再打上游，
         冷却后半开试探，避免雪崩与无谓消耗。

默认预载周期（可用环境变量覆盖）：
  * 天气      PRELOAD_WEATHER_SEC    = 900   秒（15 分钟）
  * 空气质量  PRELOAD_AIR_SEC        = 1800  秒（30 分钟）
  * 公交      PRELOAD_BUS_SEC        = 30    秒（DSAT 每 30 秒刷新，需高频）
  * AI 建议   PRELOAD_SUGGEST_SEC    = 1800  秒（30 分钟）
  * 心跳 tick PRELOAD_TICK_SEC       = 15    秒
"""
import os
import random
import threading
import time
from datetime import datetime

from cache_policy import policy_for
from data_store import get_store
from upstream_client import (
    UpstreamError,
    fetch_weather_upstream,
    fetch_air_quality_upstream,
    fetch_bus_upstream,
    list_bus_stops,
)

# 预载 AI 建议时覆盖的标签集合（空 = 无个性化标签的默认建议）
_DEFAULT_TAG_SETS = [
    [],
    ["sun_protection"],
    ["like_walking"],
    ["like_transit"],
    ["like_driving"],
    ["health_asthma"],
    ["health_joint"],
    ["pet_dog"],
    ["with_kid"],
]


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Preloader:
    """后台预载调度器（单进程单实例）"""

    def __init__(self):
        self.store = get_store()
        self._stop_evt = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._status = {}
        self._next_due = {}
        self._breakers = {}

        self.ref_stop = os.getenv("PRELOAD_REF_STOP", "M02")
        self.tick = int(os.getenv("PRELOAD_TICK_SEC", "15"))
        self.enable_suggestions = os.getenv("PRELOAD_SUGGESTIONS", "1") not in ("0", "false", "False")

        self.weather_sec = int(os.getenv("PRELOAD_WEATHER_SEC", "900"))
        self.air_sec = int(os.getenv("PRELOAD_AIR_SEC", "1800"))
        self.bus_sec = int(os.getenv("PRELOAD_BUS_SEC", "30"))
        self.suggest_sec = int(os.getenv("PRELOAD_SUGGEST_SEC", "1800"))

        self.sources = [
            {"name": "weather", "label": "天氣", "interval": self.weather_sec,
             "run": self._run_weather},
            {"name": "air_quality", "label": "空氣質量", "interval": self.air_sec,
             "run": self._run_air},
            {"name": "bus", "label": "公交", "interval": self.bus_sec,
             "run": self._run_bus},
        ]
        if self.enable_suggestions:
            self.sources.append(
                {"name": "suggestions", "label": "AI建議", "interval": self.suggest_sec,
                 "run": self._run_suggestions}
            )

        for src in self.sources:
            self._next_due[src["name"]] = 0.0
            src["policy"] = policy_for(self._policy_key(src["name"]))
            self._breakers[src["name"]] = {
                "fail_streak": 0, "open_until": 0.0, "state": "closed", "opened_count": 0,
            }
            self._status[src["name"]] = {
                "label": src["label"],
                "interval_seconds": src["interval"],
                "last_run": None,
                "last_ok": None,
                "last_error": None,
                "last_duration_ms": None,
                "run_count": 0,
                "last_count": 0,
                "skipped_count": 0,
                "breaker_state": "closed",
                "fail_streak": 0,
            }

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _policy_key(name: str) -> str:
        return {
            "weather": "weather",
            "air_quality": "air_quality",
            "bus": "bus:M02",
            "suggestions": "ai_suggestions:all",
        }.get(name, name)

    def _src_policy(self, name: str) -> dict:
        for src in self.sources:
            if src["name"] == name:
                return src["policy"]
        return policy_for(name)

    def _retry(self, fn, *args, policy=None, **kwargs):
        """带指数退避 + 抖动的重试；仅重试 UpstreamError"""
        policy = policy or {}
        attempts = int(policy.get("retry", 2)) + 1
        backoff = float(policy.get("backoff", 1.0))
        last = None
        for i in range(attempts):
            try:
                return fn(*args, **kwargs)
            except UpstreamError as e:
                last = e
                if i < attempts - 1:
                    time.sleep(backoff * (2 ** i) + random.uniform(0, 0.3))
        raise last

    def _store_snapshot(self, key: str, value, upstream: str, status: str = "ok"):
        """写入本地快照，并把『数据来源』标注为本机预载服务器（保留上游信息）"""
        if isinstance(value, dict):
            value = dict(value)
            origin = value.get("数据来源", upstream)
            value["数据来源"] = f"本地預載伺服器（上游：{origin}）"
            value["预载时间"] = _now_str()
        self.store.set(key, value, status=status)

    @staticmethod
    def _is_degraded_source(data: dict) -> bool:
        """判断这份公交数据是否来自本地降级（Excel）而非云端 MongoDB"""
        src = str(data.get("数据来源", "")) if isinstance(data, dict) else ""
        return ("Excel" in src) or ("本地" in src)

    # ---------------- 各数据源（失败即抛 UpstreamError） ----------------
    def _run_weather(self):
        try:
            data = self._retry(fetch_weather_upstream, policy=self._src_policy("weather"))
        except UpstreamError as e:
            self.store.mark_failure("weather", str(e))
            raise
        self._store_snapshot("weather", data, "Open-Meteo API")
        return 1

    def _run_air(self):
        try:
            data = self._retry(fetch_air_quality_upstream, policy=self._src_policy("air_quality"))
        except UpstreamError as e:
            self.store.mark_failure("air_quality", str(e))
            raise
        self._store_snapshot("air_quality", data, "WAQI API")
        return 1

    def _run_bus(self):
        stops = list_bus_stops()
        if not stops:
            stops = [self.ref_stop]
        policy = self._src_policy("bus")
        ok = 0
        errors = []
        for stop in stops:
            key = f"bus:{stop}"
            try:
                data = self._retry(fetch_bus_upstream, stop, policy=policy)
                status = "degraded" if self._is_degraded_source(data) else "ok"
                self._store_snapshot(key, data, "MongoDB Atlas 雲端", status=status)
                ok += 1
            except UpstreamError as e:
                self.store.mark_failure(key, str(e))
                errors.append(stop)
        # 只有当**所有**站点都失败时，才认为该数据源整体失败（触发熔断）
        if ok == 0 and errors:
            raise UpstreamError(f"公交 {len(errors)} 个站点全部失败，例: {errors[:3]}")
        if errors:
            print(f"[预载服务器] 公交部分站点失败 {len(errors)}/{len(stops)}: {errors[:3]}")
        return ok

    def _run_suggestions(self):
        # 延迟导入，避免与 ai_suggestion_service 形成循环依赖
        from ai_suggestion_service import (
            generate_ai_suggestions,
            suggestion_cache_key,
            fetch_ai_suggestions,
            AI_API_KEY,
        )

        weather = self.store.get("weather") or {}
        air = self.store.get("air_quality") or {}
        bus = self.store.get(f"bus:{self.ref_stop}") or {}

        # 未配置 AI Key：退回纯本地的规则引擎（不联网），保证建议卡有内容
        if not AI_API_KEY:
            for tags in _DEFAULT_TAG_SETS:
                fetch_ai_suggestions(weather, air, bus, user_tags=list(tags))
            return len(_DEFAULT_TAG_SETS)

        policy = self._src_policy("suggestions")
        ok = 0
        last_err = None
        for tags in _DEFAULT_TAG_SETS:
            key = suggestion_cache_key(tags)
            try:
                self._retry(generate_ai_suggestions, weather, air, bus,
                            user_tags=list(tags), policy=policy)
                ok += 1
            except UpstreamError as e:
                last_err = e
                self.store.mark_failure(key, str(e))
        if ok == 0 and last_err:
            raise last_err
        return ok

    # ---------------- 调度 ----------------
    def _run_one(self, src):
        name = src["name"]
        breaker = self._breakers[name]
        now = time.time()

        # 熔断打开：跳过本轮，省资源、避免雪崩
        if breaker["open_until"] > now:
            with self._lock:
                st = self._status[name]
                st["skipped_count"] += 1
                st["breaker_state"] = "open"
            print(f"[预载服务器] {src['label']} 熔断中，跳过本轮（剩余 {int(breaker['open_until'] - now)}s）")
            return

        t0 = time.time()
        ok = True
        err = None
        count = 0
        try:
            count = src["run"]()
        except Exception as e:
            ok = False
            err = str(e)
        dur = round((time.time() - t0) * 1000)

        if ok:
            breaker["fail_streak"] = 0
            breaker["state"] = "closed"
            breaker["open_until"] = 0.0
        else:
            breaker["fail_streak"] += 1
            threshold = int(src["policy"].get("breaker_threshold", 5))
            if breaker["fail_streak"] >= threshold:
                breaker["state"] = "open"
                breaker["open_until"] = time.time() + int(src["policy"].get("breaker_cooldown", 300))
                breaker["opened_count"] += 1
            else:
                breaker["state"] = "degraded"

        with self._lock:
            st = self._status[name]
            st["last_run"] = _now_str()
            st["last_ok"] = ok
            st["last_error"] = err
            st["last_duration_ms"] = dur
            st["run_count"] += 1
            st["last_count"] = count
            st["breaker_state"] = breaker["state"]
            st["fail_streak"] = breaker["fail_streak"]

        flag = "OK" if ok else "X"
        extra = ""
        if not ok and breaker["state"] == "open":
            extra = f"  → 熔断打开 {int(src['policy'].get('breaker_cooldown', 300))}s"
        print(f"[预载服务器] {src['label']} 预载 [{flag}] {count} 项 ({dur}ms)"
              + (f" 错误: {err}" if err else "") + extra)

    def preload_once(self, names=None):
        """立即预载（阻塞）。names=None 表示全部；可指定部分源"""
        for src in self.sources:
            if names and src["name"] not in names:
                continue
            self._run_one(src)
            self._next_due[src["name"]] = time.time() + src["interval"]

    def _loop(self):
        while not self._stop_evt.is_set():
            now = time.time()
            for src in self.sources:
                if now >= self._next_due[src["name"]]:
                    self._run_one(src)
                    self._next_due[src["name"]] = time.time() + src["interval"]
            self._stop_evt.wait(self.tick)

    def start(self, warm=True):
        if self._thread and self._thread.is_alive():
            return
        if warm:
            # 只预热核心数据源（快），保证首个用户请求即有数据；
            # AI 建议等慢源交给后台线程，不阻塞启动（避免 Render 健康检查超时）
            print("[预载服务器] 启动预热：立即预载核心数据源（天气/空氣/公交）…")
            self.preload_once(names=["weather", "air_quality", "bus"])
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, name="preloader", daemon=True)
        self._thread.start()
        print(f"[预载服务器] 后台预载线程已启动 (tick={self.tick}s)")

    def stop(self):
        self._stop_evt.set()

    # ---------------- 状态 ----------------
    def breakers(self):
        with self._lock:
            return {k: dict(v) for k, v in self._breakers.items()}

    def status(self):
        with self._lock:
            sources = {k: dict(v) for k, v in self._status.items()}
        for name, b in self.breakers().items():
            if name in sources:
                sources[name]["breaker"] = b
        return {
            "运行中": bool(self._thread and self._thread.is_alive()),
            "tick_seconds": self.tick,
            "数据源": sources,
            "本地快照": self.store.status(),
        }


# ---------- 全局单例 ----------
_preloader = None
_preloader_lock = threading.Lock()


def get_preloader() -> Preloader:
    global _preloader
    if _preloader is None:
        with _preloader_lock:
            if _preloader is None:
                _preloader = Preloader()
    return _preloader
