"""
微服务1：天气服务 (Weather Service)
负责调用 Open-Meteo API 获取澳门实时天气数据
支持 Redis 缓存（过期数据降级）
"""
import requests
from datetime import datetime
from redis_cache import get_cache, CacheTTL

# 澳门坐标
MACAU_LAT = 22.1987
MACAU_LON = 113.5439

# 缓存键
CACHE_KEY = "weather:macau:current"


def fetch_weather():
    """
    从 Open-Meteo API 获取澳门实时天气

    Returns:
        dict: 天气数据，包含温度、风速、更新时间、数据来源
    """
    cache = get_cache()

    # 尝试从 Redis 读取缓存
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached

    # 调用 Open-Meteo API
    url = f"https://api.open-meteo.com/v1/forecast?latitude={MACAU_LAT}&longitude={MACAU_LON}&current_weather=true"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        # 映射 WMO 天气代码到中文描述
        wmo_code = data["current_weather"].get("weathercode", 0)
        weather_desc_map = {
            0: "晴朗", 1: "大部晴朗", 2: "多云", 3: "阴天",
            45: "雾", 48: "雾凇",
            51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨",
            71: "小雪", 73: "中雪", 75: "大雪",
            80: "小阵雨", 81: "中阵雨", 82: "大阵雨",
            95: "雷暴", 96: "雷暴伴小雹", 99: "雷暴伴大雹"
        }
        weather_desc = weather_desc_map.get(wmo_code, "未知")

        result = {
            "城市": "澳门",
            "当前温度": data["current_weather"]["temperature"],
            "温度单位": "°C",
            "天气描述": weather_desc,
            "天气代码": wmo_code,
            "风速": data["current_weather"]["windspeed"],
            "风速单位": "km/h",
            "风向": data["current_weather"].get("winddirection", 0),
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "Open-Meteo API"
        }

        # 写入缓存
        cache.set(CACHE_KEY, result, ttl=CacheTTL.WEATHER)
        return result

    except Exception as e:
        # API 失败时返回过期缓存
        if cached:
            cached["数据更新时间"] = f"(缓存) {cached.get('数据更新时间', '未知')}"
            return cached

        return {
            "城市": "澳门",
            "当前温度": "--",
            "温度单位": "°C",
            "风速": "--",
            "风速单位": "km/h",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "Open-Meteo API",
            "错误": str(e)
        }
