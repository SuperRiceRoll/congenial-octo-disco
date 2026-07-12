"""
Redis 缓存公共模块
================
为各微服务提供统一的 Redis 缓存接口
Redis 不可用时自动降级，不影响核心功能
"""
import os
import json
from typing import Optional

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# 缓存 TTL 配置（秒）
class CacheTTL:
    """各数据源的推荐缓存时间"""
    WEATHER = 900        # 15分钟 — 天气变化慢
    AIR_QUALITY = 1800   # 30分钟 — AQI 更新频率低
    BUS = 30             # 30秒 — 公交数据实时性要求高（DSAT 每30秒刷新）


class RedisCache:
    """
    Redis 缓存封装

    用法:
        cache = RedisCache()
        data = cache.get("key") or fetch_from_api()
        cache.set("key", data, ttl=300)
    """

    def __init__(self):
        self._client: Optional["redis.Redis"] = None
        self._available = False
        self._init_client()

    def _init_client(self):
        """初始化 Redis 连接（不阻塞）"""
        if not REDIS_AVAILABLE:
            return

        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", 6379))
        password = os.getenv("REDIS_PASSWORD", "") or None

        # 先探测端口，不阻塞等待
        import socket as _socket
        try:
            s = _socket.socket()
            s.settimeout(0.5)
            s.connect((host, port))
            s.close()
        except Exception:
            self._client = None
            self._available = False
            return

        try:
            self._client = redis.Redis(
                host=host,
                port=port,
                password=password,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1
            )
            self._client.ping()
            self._available = True
        except Exception:
            self._client = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def get(self, key: str) -> Optional[dict]:
        """获取缓存数据"""
        if not self._available or not self._client:
            return None
        try:
            data = self._client.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    def set(self, key: str, value: dict, ttl: int = 300):
        """写入缓存"""
        if not self._available or not self._client:
            return
        try:
            self._client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        except Exception:
            pass

    def delete(self, key: str):
        """删除缓存"""
        if not self._available or not self._client:
            return
        try:
            self._client.delete(key)
        except Exception:
            pass


# 全局单例
_cache_instance: Optional[RedisCache] = None


def get_cache() -> RedisCache:
    """获取全局缓存实例（单例模式）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RedisCache()
    return _cache_instance
