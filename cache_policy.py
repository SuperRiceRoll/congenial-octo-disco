"""
缓存策略 (Cache Policy) —— 本地快照的「新鲜度分级」与「抗崩参数」
================================================================
为 data_store 里的每个快照定义：

  * ttl     正常运行时的刷新周期（秒）。age <= ttl 视为「新鲜」
  * grace   陈旧容忍窗口（秒）。ttl < age <= ttl+grace 视为「陈旧但可用」
            （上游故障时，仍用上一次快照对外服务）
  * breaker_threshold  熔断阈值：某数据源连续失败 N 次后熔断
  * breaker_cooldown   熔断冷却时间（秒）：冷却期内不再打上游，避免雪崩
  * retry    单次拉取失败后的额外重试次数
  * backoff  重试退避基数（秒），第 i 次重试等待 backoff * 2^i

新鲜度分级（供前端展示 / 健康检查）：
  age <= ttl                → fresh       （新鲜）
  ttl < age <= ttl + grace  → stale       （陈旧，前端提示「数据可能延迟」）
  age >  ttl + grace        → unavailable （超期，前端提示「实时数据暂不可用」）

所有参数都可用环境变量覆盖，方便部署时调优。
"""
import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (ValueError, TypeError):
        return default


# ---------- 默认策略 ----------
DEFAULT_POLICY = {
    "ttl": _env_int("CACHE_DEFAULT_TTL", 1800),          # 30 分钟
    "grace": _env_int("CACHE_DEFAULT_GRACE", 21600),     # 6 小时
    "breaker_threshold": _env_int("BREAKER_THRESHOLD", 5),
    "breaker_cooldown": _env_int("BREAKER_COOLDOWN", 300),  # 5 分钟
    "retry": _env_int("UPSTREAM_RETRY", 2),
    "backoff": _env_float("UPSTREAM_BACKOFF", 1.0),
}

# ---------- 按 key 定制的策略（精确匹配） ----------
_EXACT = {
    # 天气：15 分钟刷新；上游挂了最多容忍 6 小时（几小时前的预报仍有参考价值）
    "weather": {"ttl": _env_int("PRELOAD_WEATHER_SEC", 900), "grace": _env_int("CACHE_WEATHER_GRACE", 21600)},
    # 空气质量：30 分钟刷新；AQI 变化慢，容忍 6 小时
    "air_quality": {"ttl": _env_int("PRELOAD_AIR_SEC", 1800), "grace": _env_int("CACHE_AIR_GRACE", 21600)},
    # 规则引擎建议（本地计算，无上游）
    "suggestions:all": {"ttl": 1800, "grace": 21600},
}

# ---------- 前缀匹配（key 以该前缀开头） ----------
_PREFIX = {
    # 公交：30 秒刷新；超过 5 分钟基本没有参考意义，明确提示
    "bus:": {"ttl": _env_int("PRELOAD_BUS_SEC", 30), "grace": _env_int("CACHE_BUS_GRACE", 300)},
    # AI 建议：30 分钟刷新；容忍 6 小时
    "ai_suggestions:": {"ttl": _env_int("PRELOAD_SUGGEST_SEC", 1800), "grace": _env_int("CACHE_SUGGEST_GRACE", 21600)},
}


def policy_for(key: str) -> dict:
    """返回某个快照 key 生效的完整策略（默认值 + 定制覆盖）"""
    policy = dict(DEFAULT_POLICY)
    if key in _EXACT:
        policy.update(_EXACT[key])
    else:
        for prefix, override in _PREFIX.items():
            if key.startswith(prefix):
                policy.update(override)
                break
    return policy
