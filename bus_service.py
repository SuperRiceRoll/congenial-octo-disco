"""
微服务3：公交服务 (Bus Service)
==============================
⚠️ 数据读取策略（2026-09-17 架构升级）：
  用户请求**只读本地预载快照**（data_store 的 bus:<stop_id>），
  由后台 preload_service 按周期（默认 30 秒）提前把**所有站点**的
  到站信息从 MongoDB Atlas 拉取并写入本地。
  用户链路不再直接访问外部数据库。

仅在本地快照为空（预载尚未完成）时做一次冷启动兜底拉取。
保留 `init_bus_data_from_excel` / `list_bus_stops` 供启动与预载使用。
"""
from data_store import get_store
from upstream_client import (
    fetch_bus_upstream,
    init_bus_data_from_excel,
    list_bus_stops,
)

__all__ = ["fetch_bus_data", "init_bus_data_from_excel", "list_bus_stops"]


def fetch_bus_data(stop_id: str = "M02"):
    """
    获取指定站点的巴士到站信息（来自本地预载服务器快照）。

    Args:
        stop_id: 站点编号，如 "M02"

    Returns:
        dict: 站点巴士信息
    """
    store = get_store()
    key = f"bus:{stop_id}"
    data = store.get(key)
    if data is not None:
        return data

    # 冷启动兜底：预载线程还没覆盖到该站点时补一次
    print(f"[公交服务] 本地快照为空({stop_id})，冷启动兜底拉取一次上游数据")
    data = fetch_bus_upstream(stop_id)
    store.set(key, data)
    return data
