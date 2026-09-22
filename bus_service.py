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
from datetime import datetime

from data_store import get_store
from upstream_client import (
    fetch_bus_upstream,
    init_bus_data_from_excel,
    list_bus_stops,
    UpstreamError,
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

    # 冷启动兜底：预载线程还没覆盖到该站点时补一次（成功才写入快照）
    print(f"[公交服务] 本地快照为空({stop_id})，冷启动兜底拉取一次上游数据")
    try:
        data = fetch_bus_upstream(stop_id)
        store.set(key, data)
        return data
    except UpstreamError as e:
        print(f"[公交服务] 冷启动兜底失败({stop_id}): {e}")
        return _placeholder(stop_id, str(e))


def _placeholder(stop_id: str, reason: str = "") -> dict:
    return {
        "站点编号": stop_id,
        "站点名称": stop_id,
        "巴士信息": [],
        "数据来源": "本地預載伺服器（暫無數據）",
        "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "提示": "即時到站數據暫不可用，請稍後再試",
    }
