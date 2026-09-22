"""
微服务1：天气服务 (Weather Service)
==================================
⚠️ 数据读取策略（2026-09-17 架构升级）：
  用户请求**只读本地预载快照**（data_store），由后台 preload_service
  按周期（默认 15 分钟）提前拉取 Open-Meteo 数据并写入。
  用户链路不再直接访问第三方接口。

仅在本地快照为空（预载尚未完成）时做一次冷启动兜底拉取。
"""
from datetime import datetime

from data_store import get_store
from upstream_client import fetch_weather_upstream, UpstreamError

CACHE_KEY = "weather"


def fetch_weather():
    """
    返回澳门实时天气（来自本地预载服务器快照）。

    Returns:
        dict: 天气数据，包含温度、风速、更新时间、数据来源
    """
    store = get_store()
    data = store.get(CACHE_KEY)
    if data is not None:
        return data

    # 冷启动兜底：预载线程还没跑完时为请求补一次（成功才写入快照）
    print("[天气服务] 本地快照为空，冷启动兜底拉取一次上游数据")
    try:
        data = fetch_weather_upstream()
        store.set(CACHE_KEY, data)
        return data
    except UpstreamError as e:
        # 兜底也失败：返回友好占位，绝不写入坏数据污染快照
        print(f"[天气服务] 冷启动兜底失败: {e}")
        return _placeholder(str(e))


def _placeholder(reason: str = "") -> dict:
    return {
        "城市": "澳门",
        "当前温度": "--",
        "温度单位": "°C",
        "天气描述": "數據準備中",
        "风速": "--",
        "风速单位": "km/h",
        "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "数据来源": "本地預載伺服器（暫無數據）",
        "提示": "即時數據暫不可用，請稍後再試",
    }
