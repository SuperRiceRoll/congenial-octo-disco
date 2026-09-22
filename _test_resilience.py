"""抗崩溃 / 缓存逻辑离线自测（不联网）。运行：python _test_resilience.py"""
import os
import tempfile
import time

# 测试期把重试关掉，避免退避 sleep
os.environ["UPSTREAM_RETRY"] = "0"
os.environ.setdefault("MONGO_URI", "")

from data_store import SnapshotStore          # noqa: E402
from cache_policy import policy_for           # noqa: E402
from upstream_client import UpstreamError     # noqa: E402
import preload_service                        # noqa: E402
import ai_suggestion_service as ais           # noqa: E402

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name}")


print("1) 快照：成功写入 + 读取")
tmp = os.path.join(tempfile.gettempdir(), "snap_test.json")
if os.path.exists(tmp):
    os.remove(tmp)
store = SnapshotStore(tmp)
store.set("weather", {"当前温度": 30, "数据来源": "x"})
check("get 返回数据", store.get("weather")["当前温度"] == 30)
check("freshness=fresh", store.freshness("weather")["level"] == "fresh")

print("2) 失败不覆盖：mark_failure 保留旧数据")
store.mark_failure("weather", "boom")
check("value 仍是旧的好数据", store.get("weather")["当前温度"] == 30)
check("仍为 fresh（时间戳未变）", store.freshness("weather")["level"] == "fresh")
check("fail_count 记 1", store.entry("weather")["fail_count"] == 1)
check("last_error 记录", store.entry("weather")["last_error"] == "boom")

print("3) 持久化：重新加载后仍保留")
store2 = SnapshotStore(tmp)
check("磁盘恢复数据", (store2.get("weather") or {}).get("当前温度") == 30)

print("4) 新鲜度分级：stale / unavailable")
store.set("air_quality", {"aqi": 50})
pol = policy_for("air_quality")
with store._lock:
    store._data["air_quality"]["updated_at"] = time.time() - pol["ttl"] - 10
check("超过 ttl → stale", store.freshness("air_quality")["level"] == "stale")
with store._lock:
    store._data["air_quality"]["updated_at"] = time.time() - pol["ttl"] - pol["grace"] - 10
check("超过 ttl+grace → unavailable", store.freshness("air_quality")["level"] == "unavailable")

print("5) 策略：公交 ttl=30 / grace=300")
p = policy_for("bus:M02")
check("bus ttl=30", p["ttl"] == 30)
check("bus grace=300", p["grace"] == 300)

print("6) 上游失败：预载保留旧快照 + 熔断计数")
pl = preload_service.get_preloader()
pl.store = store
store.set("weather", {"当前温度": 30, "数据来源": "x"})
orig = preload_service.fetch_weather_upstream
preload_service.fetch_weather_upstream = lambda: (_ for _ in ()).throw(UpstreamError("模拟第三方维护"))
try:
    pl._run_one(pl.sources[0])  # weather
    check("失败后旧数据仍在", store.get("weather")["当前温度"] == 30)
    check("fail_count 累加", store.entry("weather")["fail_count"] >= 1)
    check("breaker=degraded", pl.breakers()["weather"]["state"] == "degraded")
    for _ in range(10):
        pl._run_one(pl.sources[0])
    check("连续失败 → 熔断 open", pl.breakers()["weather"]["state"] == "open")
    before = pl._status["weather"]["skipped_count"]
    pl._run_one(pl.sources[0])
    check("熔断期跳过本轮", pl._status["weather"]["skipped_count"] == before + 1)
finally:
    preload_service.fetch_weather_upstream = orig

print("7) AI 不进用户链路：未配置 Key 时 generate 抛错，fetch 绝不调用大模型")
try:
    ais.generate_ai_suggestions({}, {}, {}, [])
    check("generate 抛 UpstreamError", False)
except UpstreamError:
    check("generate 抛 UpstreamError", True)

# 模拟“已配置 Key”，并让真正的外呼函数直接爆炸；fetch 绝不能走到它
_real_call = ais._call_ai_api
_real_key = ais.AI_API_KEY
ais.AI_API_KEY = "fake-key"
ais._call_ai_api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("用户链路竟然在调大模型！"))
try:
    w = {"当前温度": 33}
    a = {"空气质量指数": 60}
    b = {"站点名称": "M02", "巴士信息": [{"线路": "3", "预计到站(分钟)": "3"}]}
    res = ais.fetch_ai_suggestions(w, a, b, user_tags=["zzz_nonexistent_tag"])
    check("miss 时未外呼大模型", True)
    check("fetch 返回建议列表", isinstance(res.get("建议列表"), list) and len(res["建议列表"]) > 0)
    check("fetch 来源=规则引擎", "规则引擎" in res.get("来源", ""))
finally:
    ais.AI_API_KEY = _real_key
    ais._call_ai_api = _real_call

print(f"\n结果：{ok} 通过 / {fail} 失败")
raise SystemExit(1 if fail else 0)
