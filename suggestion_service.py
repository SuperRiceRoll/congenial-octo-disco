"""
微服务4：建议引擎 (Suggestion Engine)
根据天气、空气质量、公交数据，动态生成个性化出行建议
（规则引擎降级方案；结果写入本地预载快照，不直接外呼上游）
"""
from datetime import datetime
from data_store import get_store

# 建议的严重等级
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"
LEVEL_TIP = "tip"


def evaluate_weather(weather_data: dict, preferences: dict = None) -> list:
    """根据天气数据生成建议"""
    suggestions = []
    if not weather_data:
        return suggestions

    temp = weather_data.get("当前温度")
    wind = weather_data.get("风速")

    if temp is not None:
        try:
            temp = float(temp)
        except (ValueError, TypeError):
            temp = None

    if wind is not None:
        try:
            wind = float(wind)
        except (ValueError, TypeError):
            wind = None

    # 高温预警
    if temp is not None:
        if temp >= 35:
            suggestions.append({
                "type": "weather",
                "icon": "🔥",
                "message": "极端高温！尽量避免午后外出，注意防暑降温",
                "level": LEVEL_WARNING
            })
        elif temp >= 33:
            suggestions.append({
                "type": "weather",
                "icon": "🥵",
                "message": "高温预警！注意防晒，多喝水",
                "level": LEVEL_WARNING
            })
        elif temp >= 30:
            suggestions.append({
                "type": "weather",
                "icon": "🌡️",
                "message": f"今日气温{temp}°C，较热，建议穿着清凉",
                "level": LEVEL_INFO
            })
        elif temp < 10:
            suggestions.append({
                "type": "weather",
                "icon": "❄️",
                "message": f"气温仅{temp}°C，注意保暖",
                "level": LEVEL_INFO
            })

    # 大风预警
    if wind is not None and wind >= 40:
        suggestions.append({
            "type": "weather",
            "icon": "💨",
            "message": f"风速达{wind}km/h，风力较大，注意安全",
            "level": LEVEL_WARNING
        })

    return suggestions


def evaluate_air_quality(air_data: dict, preferences: dict = None) -> list:
    """根据空气质量数据生成建议"""
    suggestions = []
    if not air_data:
        return suggestions

    aqi = air_data.get("空气质量指数")
    pm25 = air_data.get("PM2.5浓度")
    pm10 = air_data.get("PM10浓度")

    if aqi is not None:
        try:
            aqi = float(aqi)
        except (ValueError, TypeError):
            aqi = None

    if aqi is not None:
        if aqi > 150:
            suggestions.append({
                "type": "air",
                "icon": "😷",
                "message": "空气质量差！建议佩戴口罩，减少户外活动",
                "level": LEVEL_WARNING
            })
        elif aqi > 100:
            suggestions.append({
                "type": "air",
                "icon": "😷",
                "message": "空气质量不理想，敏感人群建议佩戴口罩",
                "level": LEVEL_WARNING
            })
        elif aqi > 50:
            suggestions.append({
                "type": "air",
                "icon": "😶",
                "message": "空气质量一般，敏感人群注意防护",
                "level": LEVEL_INFO
            })
        else:
            suggestions.append({
                "type": "air",
                "icon": "🌿",
                "message": "空气质量优良，适合户外活动",
                "level": LEVEL_TIP
            })

    return suggestions


def evaluate_bus(bus_data: dict, preferences: dict = None) -> list:
    """根据公交数据生成建议"""
    suggestions = []
    if not bus_data:
        return suggestions

    bus_list = bus_data.get("巴士信息", [])
    if not bus_list:
        suggestions.append({
            "type": "bus",
            "icon": "🚏",
            "message": "当前站点暂无巴士信息，请查看其他站点",
            "level": LEVEL_INFO
        })
        return suggestions

    # 统计线路数量
    route_count = len(bus_list)

    # 计算即将到站的巴士
    immediate_buses = 0
    for b in bus_list:
        try:
            mins = int(b.get("预计到站(分钟)", 99))
            if mins <= 5:
                immediate_buses += 1
        except (ValueError, TypeError):
            pass

    if immediate_buses > 0:
        suggestions.append({
            "type": "bus",
            "icon": "🚌",
            "message": f"有 {immediate_buses} 路巴士即将到站（5分钟内），请做好准备",
            "level": LEVEL_TIP
        })

    if route_count == 0:
        suggestions.append({
            "type": "bus",
            "icon": "🚏",
            "message": "当前站点暂无巴士信息",
            "level": LEVEL_INFO
        })

    return suggestions


def evaluate_combined(weather_data: dict, air_data: dict, bus_data: dict,
                      preferences: dict = None) -> list:
    """整合所有维度的建议，按优先级排序"""
    all_suggestions = []
    all_suggestions.extend(evaluate_weather(weather_data, preferences))
    all_suggestions.extend(evaluate_air_quality(air_data, preferences))
    all_suggestions.extend(evaluate_bus(bus_data, preferences))

    # 按严重等级排序
    level_order = {LEVEL_WARNING: 0, LEVEL_INFO: 1, LEVEL_TIP: 2}
    all_suggestions.sort(key=lambda s: level_order.get(s.get("level", LEVEL_TIP), 99))

    return all_suggestions


def fetch_suggestions(weather_data: dict, air_data: dict, bus_data: dict,
                      preferences: dict = None) -> dict:
    """外部接口：获取所有建议"""
    store = get_store()
    cache_key = "suggestions:all"

    cached = store.get(cache_key)
    if cached:
        return cached

    suggestions = evaluate_combined(weather_data, air_data, bus_data, preferences)

    result = {
        "建议列表": suggestions,
        "建议数量": len(suggestions),
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    store.set(cache_key, result)  # 写入本地预载快照
    return result
