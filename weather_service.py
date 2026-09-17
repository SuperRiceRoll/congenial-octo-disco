"""
微服务1：天气服务 (Weather Service)
==================================
⚠️ 数据读取策略（2026-09-17 架构升级）：
  用户请求**只读本地预载快照**（data_store），由后台 preload_service
  按周期（默认 15 分钟）提前拉取 Open-Meteo 数据并写入。
  用户链路不再直接访问第三方接口。

仅在本地快照为空（预载尚未完成）时做一次冷启动兜底拉取。
"""
from data_store import get_store
from upstream_client import fetch_weather_upstream

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

    # 冷启动兜底：预载线程还没跑完时为请求补一次，并写入本地快照
    print("[天气服务] 本地快照为空，冷启动兜底拉取一次上游数据")
    data = fetch_weather_upstream()
    store.set(CACHE_KEY, data)
    return data
