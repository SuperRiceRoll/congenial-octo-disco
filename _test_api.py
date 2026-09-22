"""API 集成自测（不联网、不启动预载线程）。运行：python _test_api.py"""
import os
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(BASE, "data", "snapshots.json")
BAK = SNAP + ".testbak"
had = os.path.exists(SNAP)
if had:
    shutil.copyfile(SNAP, BAK)

try:
    import main

    store = main.get_store()
    # 灌入“成功快照”，模拟预载已完成
    store.set("weather", {"城市": "澳门", "当前温度": 29, "风速": 12,
                          "天气描述": "多云", "数据更新时间": "2026-09-22 20:00:00",
                          "数据来源": "本地預載伺服器（上游：Open-Meteo API）"})
    store.set("air_quality", {"城市": "澳门", "空气质量指数": 42, "PM2.5浓度": 10,
                              "PM10浓度": 20, "数据更新时间": "2026-09-22 20:00:00",
                              "数据来源": "本地預載伺服器（上游：WAQI API）"})
    store.set("bus:M02", {"站点编号": "M02", "站点名称": "妈阁",
                          "巴士信息": [{"线路": "3", "预计到站(分钟)": "2", "方向": "关闸"}],
                          "数据来源": "本地預載伺服器（上游：MongoDB Atlas 雲端）"})

    d = main.get_dashboard("M02", "")
    print("[dashboard] 天气温度 =", d["天气"]["温度"])
    print("[dashboard] 空气质量指数 =", d["空气质量"]["指数"])
    print("[dashboard] 更新时间 =", d.get("更新时间"))
    print("[dashboard] 数据新鲜度 =", {k: v["level"] for k, v in d["数据新鲜度"].items()})
    assert d["数据新鲜度"]["天气"]["level"] == "fresh"
    assert d["数据新鲜度"]["公交"]["level"] == "fresh"
    assert isinstance(d["建议"].get("建议列表"), list)

    h = main.health_check()
    print("[health] status =", h["status"], "| services =", h["services"])
    assert h["status"] == "ok"

    ds = main.data_status()
    print("[data-status] 运行中 =", ds["运行中"], "| 快照数 =", len(ds["本地快照"]))

    print("\nOK: API 集成测试通过")
finally:
    if had and os.path.exists(BAK):
        shutil.move(BAK, SNAP)
    elif os.path.exists(BAK):
        os.remove(BAK)
