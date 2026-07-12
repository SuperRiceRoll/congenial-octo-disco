"""
微服务2：空气质量服务 (Air Quality Service)
负责调用 WAQI API 获取澳门空气质量数据
支持 Redis 缓存（过期数据降级）
"""
import requests
from datetime import datetime
from redis_cache import get_cache, CacheTTL

# WAQI 配置
WAQI_STATION_ID = 5374  # 澳门北区监测站
WAQI_TOKEN = "5308af67ea0b49a14c735f78401128d6e3f92a82"

# 缓存键
CACHE_KEY = "air_quality:macau:current"


def fetch_air_quality():
    """
    从 WAQI API 获取澳门空气质量数据

    Returns:
        dict: 空气质量数据，包含 AQI、PM2.5、PM10、更新时间、数据来源
    """
    cache = get_cache()

    # 尝试从 Redis 读取缓存
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached

    # 调用 WAQI API
    url = f"https://api.waqi.info/feed/@{WAQI_STATION_ID}/?token={WAQI_TOKEN}"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("status") == "ok":
            result = {
                "城市": data["data"]["city"]["name"],
                "空气质量指数": data["data"]["aqi"],
                "PM2.5浓度": data["data"]["iaqi"].get("pm25", {}).get("v", "暂无"),
                "PM10浓度": data["data"]["iaqi"].get("pm10", {}).get("v", "暂无"),
                "数据更新时间": data["data"]["time"]["s"],
                "数据来源": "WAQI API"
            }
            # 缓存正常数据
            cache.set(CACHE_KEY, result, ttl=CacheTTL.AIR_QUALITY)
        else:
            result = {
                "城市": "澳门",
                "空气质量指数": "暂无",
                "PM2.5浓度": "暂无",
                "PM10浓度": "暂无",
                "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "数据来源": "WAQI API",
                "提示": "API返回状态异常"
            }

        return result

    except Exception as e:
        # API 失败时返回过期缓存
        if cached:
            cached["数据更新时间"] = f"(缓存) {cached.get('数据更新时间', '未知')}"
            return cached

        return {
            "城市": "澳门",
            "空气质量指数": "暂无",
            "PM2.5浓度": "暂无",
            "PM10浓度": "暂无",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "WAQI API",
            "错误": str(e)
        }
