"""
本地预载服务器 (Local Preload Server)
=====================================
这是老师要求的「本地服务器 / 数据缓存层」核心组件。

它做三件事：
  1. **提前预载**：后台线程按固定周期，主动去上游（Open-Meteo / WAQI /
     MongoDB Atlas / 大模型）把数据拉回来，写入本地快照仓库 (data_store)。
  2. **隔离外呼**：用户请求链路（main.py → *_service.py）只读本地快照，
     绝不直接访问第三方网站 / 外部数据库，规避侵权与安全风险。
  3. **高可用**：上游偶发故障时，仍用上一次快照对外服务；进程重启后
     也能立刻用磁盘上的快照服务（零冷启动）。

默认预载周期（可用环境变量覆盖）：
  * 天气      PRELOAD_WEATHER_SEC    = 900   秒（15 分钟）
  * 空气质量  PRELOAD_AIR_SEC        = 1800  秒（30 分钟，符合老师「预载 30 分钟」要求）
  * 公交      PRELOAD_BUS_SEC        = 30    秒（DSAT 每 30 秒刷新，需高频）
  * AI 建议   PRELOAD_SUGGEST_SEC    = 1800  秒（30 分钟）
  * 心跳 tick PRELOAD_TICK_SEC       = 15    秒
"""
import os
import threading
import time
from datetime import datetime

from data_store import get_store
from upstream_client import (
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

        self.ref_stop = os.getenv("PRELOAD_REF_STOP", "M02")
        self.tick = int(os.getenv("PRELOAD_TICK_SEC", "15"))
        self.enable_suggestions = os.getenv("PRELOAD_SUGGESTIONS", "1") not in ("0", "false", "False")

        self.weather_sec = int(os.getenv("PRELOAD_WEATHER_SEC", "900"))
        self.air_sec = int(os.getenv("PRELOAD_AIR_SEC", "1800"))
        self.bus_sec = int(os.getenv("PRELOAD_BUS_SEC", "30"))
        self.suggest_sec = int(os.getenv("PRELOAD_SUGGEST_SEC", "1800"))

        self.sources = [
            {"name": "weather", "label": "天氣", "interval": self.weather_sec, "run": self._run_weather},
            {"name": "air_quality", "label": "空氣質量", "interval": self.air_sec, "run": self._run_air},
            {"name": "bus", "label": "公交", "interval": self.bus_sec, "run": self._run_bus},
        ]
        if self.enable_suggestions:
            self.sources.append(
                {"name": "suggestions", "label": "AI建議", "interval": self.suggest_sec, "run": self._run_suggestions}
            )

        for src in self.sources:
            self._next_due[src["name"]] = 0.0
            self._status[src["name"]] = {
                "label": src["label"],
                "interval_seconds": src["interval"],
                "last_run": None,
                "last_ok": None,
                "last_error": None,
                "last_duration_ms": None,
                "run_count": 0,
            }

    # ---------------- 存储辅助 ----------------
    def _store_snapshot(self, key: str, value, upstream: str):
        """写入本地快照，并把『数据来源』标注为本机预载服务器（保留上游信息）"""
        if isinstance(value, dict):
            value = dict(value)
            origin = value.get("数据来源", upstream)
            value["数据来源"] = f"本地預載伺服器（上游：{origin}）"
            value["预载时间"] = _now_str()
        self.store.set(key, value)

    # ---------------- 各数据源 ----------------
    def _run_weather(self):
        data = fetch_weather_upstream()
        self._store_snapshot("weather", data, "Open-Meteo API")
        return 1

    def _run_air(self):
        data = fetch_air_quality_upstream()
        self._store_snapshot("air_quality", data, "WAQI API")
        return 1

    def _run_bus(self):
        stops = list_bus_stops()
        if not stops:
            stops = [self.ref_stop]
        count = 0
        for stop in stops:
            try:
                data = fetch_bus_upstream(stop)
                self._store_snapshot(f"bus:{stop}", data, "MongoDB Atlas 雲端")
                count += 1
            except Exception as e:
                print(f"[预载服务器] 公交 {stop} 预载失败: {e}")
        return count

    def _run_suggestions(self):
        # 延迟导入，避免与 ai_suggestion_service 形成循环依赖
        from ai_suggestion_service import fetch_ai_suggestions

        weather = self.store.get("weather") or {}
        air = self.store.get("air_quality") or {}
        bus = self.store.get(f"bus:{self.ref_stop}") or {}

        count = 0
        for tags in _DEFAULT_TAG_SETS:
            try:
                fetch_ai_suggestions(weather, air, bus, user_tags=list(tags))
                count += 1
            except Exception as e:
                print(f"[预载服务器] 建议预载失败 {tags}: {e}")
        return count

    # ---------------- 调度 ----------------
    def _run_one(self, src):
        name = src["name"]
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
        with self._lock:
            st = self._status[name]
            st["last_run"] = _now_str()
            st["last_ok"] = ok
            st["last_error"] = err
            st["last_duration_ms"] = dur
            st["run_count"] += 1
            st["last_count"] = count
        flag = "OK" if ok else "X"
        print(f"[预载服务器] {src['label']} 预载 [{flag}] {count} 项 ({dur}ms)"
              + (f" 错误: {err}" if err else ""))

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
    def status(self):
        with self._lock:
            sources = {k: dict(v) for k, v in self._status.items()}
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
