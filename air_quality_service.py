"""
微服务2：空气质量服务 (Air Quality Service)
==========================================
⚠️ 数据读取策略（2026-09-17 架构升级）：
  用户请求**只读本地预载快照**（data_store），由后台 preload_service
  按周期（默认 30 分钟）提前拉取 WAQI 数据并写入。
  用户链路不再直接访问第三方接口。

仅在本地快照为空（预载尚未完成）时做一次冷启动兜底拉取。
"""
from data_store import get_store
from upstream_client import fetch_air_quality_upstream

CACHE_KEY = "air_quality"


def fetch_air_quality():
    """
    返回澳门空气质量（来自本地预载服务器快照）。

    Returns:
        dict: 空气质量数据，包含 AQI、PM2.5、PM10、更新时间、数据来源
    """
    store = get_store()
    data = store.get(CACHE_KEY)
    if data is not None:
        return data

    print("[空气服务] 本地快照为空，冷启动兜底拉取一次上游数据")
    data = fetch_air_quality_upstream()
    store.set(CACHE_KEY, data)
    return data
