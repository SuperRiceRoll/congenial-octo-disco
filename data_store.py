"""
本地快照存储 (Local Snapshot Store)
===================================
「本地预载服务器」的数据仓库。

设计原则：
  * 所有上游数据（天气 / 空气质量 / 公交 / AI 建议）都先由后台预载线程
    拉取并**写入这里**；
  * 用户请求只**从这里读取**，绝不直接访问第三方接口或外部数据库；
  * 内存为主 + 磁盘 JSON 持久化 —— 即使进程重启，也能立刻用上一次的快照
    对外服务，做到「零冷启动、零直接外呼」。

每个 key 保存：value（原始数据）+ updated_at（写入时间）。
即使数据「过期」，也会保留最后一次快照继续对外服务（缓存服务器的核心价值）。
"""
import json
import os
import threading
import time
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
_SNAPSHOT_FILE = os.path.join(_DATA_DIR, "snapshots.json")


class SnapshotStore:
    """线程安全的本地快照仓库（内存 + 磁盘持久化）"""

    def __init__(self, persist_path: str = None):
        self._lock = threading.RLock()
        self._data = {}
        self._persist_path = persist_path
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if self._persist_path and os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded
            except Exception:
                self._data = {}

    def _persist(self):
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            tmp = self._persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._persist_path)
        except Exception:
            pass

    # ---------- 读写 ----------
    def set(self, key: str, value):
        """写入/更新一个快照"""
        with self._lock:
            now = time.time()
            self._data[key] = {
                "value": value,
                "updated_at": now,
                "updated_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._persist()

    def get(self, key: str):
        """读取快照的 value（不存在返回 None）"""
        with self._lock:
            entry = self._data.get(key)
            return entry["value"] if entry else None

    def entry(self, key: str):
        with self._lock:
            return self._data.get(key)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def age(self, key: str):
        """距离上次更新的秒数；不存在返回 None"""
        with self._lock:
            entry = self._data.get(key)
            return None if not entry else round(time.time() - entry["updated_at"], 1)

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def status(self):
        """返回所有快照的新鲜度状态"""
        now = time.time()
        with self._lock:
            out = {}
            for k, v in self._data.items():
                out[k] = {
                    "updated_at": v.get("updated_str", ""),
                    "age_seconds": round(now - v.get("updated_at", now), 1),
                }
            return out


# ---------- 全局单例 ----------
_store = None
_store_lock = threading.Lock()


def get_store() -> SnapshotStore:
    """获取全局快照仓库（单例）"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SnapshotStore(_SNAPSHOT_FILE)
    return _store
