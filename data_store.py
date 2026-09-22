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

抗崩溃设计（2026-09-22 升级）：
  * 只有**成功**的预载才会 `set()` 覆盖快照；失败调用 `mark_failure()`，
    只记录失败次数与错误，**绝不覆盖上一次的好数据**；
  * 每个快照带「新鲜度分级」（fresh / stale / unavailable），由 cache_policy
    的 ttl + grace 计算，供前端与健康检查判断数据是否可信；
  * 磁盘写入采用「临时文件 + os.replace」原子替换，避免写一半崩溃导致文件损坏。

每个 key 保存：value（原始数据）+ updated_at（写入时间）+ 新鲜度元数据。
即使数据「过期」，也会保留最后一次快照继续对外服务（缓存服务器的核心价值）。
"""
import json
import os
import threading
import time
from datetime import datetime

from cache_policy import policy_for

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
_SNAPSHOT_FILE = os.path.join(_DATA_DIR, "snapshots.json")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
                    self._data = {k: self._normalize(v) for k, v in loaded.items()}
            except Exception:
                self._data = {}

    @staticmethod
    def _normalize(entry: dict) -> dict:
        """兼容旧版本快照（缺少新字段时补默认值）"""
        if not isinstance(entry, dict):
            return {"value": entry, "updated_at": time.time(), "updated_str": _now_str(),
                    "status": "ok", "fail_count": 0, "last_attempt_at": time.time(),
                    "last_attempt_str": _now_str(), "last_error": None}
        e = dict(entry)
        ts = e.get("updated_at")
        if not ts:
            e["updated_at"] = None
        e.setdefault("updated_str", "")
        e.setdefault("status", "ok")
        e.setdefault("fail_count", 0)
        e.setdefault("last_attempt_at", e.get("updated_at"))
        e.setdefault("last_attempt_str", e.get("updated_str", ""))
        e.setdefault("last_error", None)
        return e

    def _persist(self):
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            tmp = self._persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._persist_path)  # 原子替换
        except Exception:
            pass

    # ---------- 读写 ----------
    def set(self, key: str, value, status: str = "ok"):
        """写入/更新一个快照（仅在**成功**获取数据后调用）"""
        with self._lock:
            now = time.time()
            nowstr = _now_str()
            self._data[key] = {
                "value": value,
                "updated_at": now,
                "updated_str": nowstr,
                "status": status,
                "fail_count": 0,
                "last_attempt_at": now,
                "last_attempt_str": nowstr,
                "last_error": None,
            }
            self._persist()

    def mark_failure(self, key: str, error: str = ""):
        """
        记录一次**拉取失败**：保留旧的 value / updated_at 不变，
        只累加失败次数、记录最后错误与尝试时间。
        —— 这样上游挂掉时，用户仍能读到上一次的好数据。
        """
        with self._lock:
            now = time.time()
            entry = self._data.get(key)
            if entry is None:
                entry = {"value": None, "updated_at": None, "updated_str": "",
                         "status": "unavailable", "fail_count": 0,
                         "last_attempt_at": None, "last_attempt_str": "", "last_error": None}
            else:
                entry = self._normalize(entry)
            entry["fail_count"] = int(entry.get("fail_count", 0)) + 1
            entry["last_attempt_at"] = now
            entry["last_attempt_str"] = _now_str()
            entry["last_error"] = str(error)[:500]
            entry["status"] = "degraded"
            self._data[key] = entry
            self._persist()

    def get(self, key: str):
        """读取快照的 value（不存在返回 None）"""
        with self._lock:
            entry = self._data.get(key)
            return entry["value"] if entry else None

    def entry(self, key: str):
        with self._lock:
            e = self._data.get(key)
            return dict(e) if e else None

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def age(self, key: str):
        """距离上次成功更新的秒数；不存在/从未成功返回 None"""
        with self._lock:
            entry = self._data.get(key)
            if not entry or not entry.get("updated_at"):
                return None
            return round(time.time() - entry["updated_at"], 1)

    def freshness(self, key: str) -> dict:
        """
        返回快照的新鲜度：fresh / stale / unavailable，
        以及 age、ttl、grace、更新时间和失败信息。
        """
        policy = policy_for(key)
        ttl, grace = policy["ttl"], policy["grace"]
        with self._lock:
            entry = self._data.get(key)
        if not entry or not entry.get("updated_at"):
            return {
                "key": key, "level": "unavailable", "ttl": ttl, "grace": grace,
                "age_seconds": None, "updated_at": "", "stale": True, "available": False,
                "fail_count": (entry or {}).get("fail_count", 0),
                "last_error": (entry or {}).get("last_error"),
            }
        age = time.time() - entry["updated_at"]
        if age <= ttl:
            level = "fresh"
        elif age <= ttl + grace:
            level = "stale"
        else:
            level = "unavailable"
        return {
            "key": key, "level": level, "ttl": ttl, "grace": grace,
            "age_seconds": round(age, 1), "updated_at": entry.get("updated_str", ""),
            "stale": level != "fresh", "available": level != "unavailable",
            "fail_count": entry.get("fail_count", 0),
            "last_error": entry.get("last_error"),
        }

    def get_with_meta(self, key: str):
        """读取快照：返回 {'value': ..., **entry, 'freshness': {...}}；不存在返回 None"""
        with self._lock:
            entry = self._data.get(key)
        if not entry:
            return None
        out = dict(entry)
        out["freshness"] = self.freshness(key)
        return out

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def status(self):
        """返回所有快照的新鲜度状态"""
        with self._lock:
            keys = list(self._data.keys())
        out = {}
        for k in keys:
            f = self.freshness(k)
            out[k] = {
                "updated_at": f["updated_at"],
                "age_seconds": f["age_seconds"],
                "level": f["level"],
                "status": (self.entry(k) or {}).get("status", "ok"),
                "fail_count": f["fail_count"],
                "last_error": f.get("last_error"),
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
