"""
上游数据适配器 (Upstream Data Client)
=====================================
**只有「本地预载服务器」(preload_service) 会调用本模块的函数**，
去访问第三方接口 / 外部数据库：

  * 天气     → Open-Meteo API
  * 空气质量 → WAQI API
  * 公交     → MongoDB Atlas 云端（Excel 本地降级）

用户请求链路（main.py → *_service.py）永远不会直接 import 这里的网络函数，
只读取 data_store 里的本地快照，从根本上隔离了「直接拿 API 访问别人数据库」的行为。
"""
import os
from datetime import datetime

import requests

# ---------- MongoDB 驱动 ----------
# 已降级到 pymongo 3.12 解决 Python 3.14 + OpenSSL 3.5 TLS 兼容问题
PYMONGO_AVAILABLE = True
try:
    from pymongo import MongoClient
except ImportError:
    PYMONGO_AVAILABLE = False

import pandas as pd

# Windows DNS 解析修复（dnspython 1.16 兼容）
try:
    import dns.resolver
    _dns = dns.resolver.Resolver(configure=False)
    _dns.nameservers = ['8.8.8.8', '1.1.1.1']
    dns.resolver.default_resolver = _dns
except Exception:
    pass


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(_BASE_DIR, "bus_data.xlsx")

# ---------- 天气 ----------
MACAU_LAT = 22.1987
MACAU_LON = 113.5439
WEATHER_URL = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={MACAU_LAT}&longitude={MACAU_LON}&current_weather=true"
)

WMO_DESC = {
    0: "晴朗", 1: "大部晴朗", 2: "多云", 3: "阴天",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "小阵雨", 81: "中阵雨", 82: "大阵雨",
    95: "雷暴", 96: "雷暴伴小雹", 99: "雷暴伴大雹",
}

# ---------- 空气质量 ----------
WAQI_STATION_ID = 5374  # 澳门北区监测站
WAQI_TOKEN = os.getenv("WAQI_TOKEN", "5308af67ea0b49a14c735f78401128d6e3f92a82")
WAQI_URL = f"https://api.waqi.info/feed/@{WAQI_STATION_ID}/?token={WAQI_TOKEN}"


def fetch_weather_upstream() -> dict:
    """[上游] 调用 Open-Meteo 获取澳门实时天气"""
    try:
        response = requests.get(WEATHER_URL, timeout=10)
        data = response.json()
        cw = data["current_weather"]
        wmo_code = cw.get("weathercode", 0)
        return {
            "城市": "澳门",
            "当前温度": cw["temperature"],
            "温度单位": "°C",
            "天气描述": WMO_DESC.get(wmo_code, "未知"),
            "天气代码": wmo_code,
            "风速": cw["windspeed"],
            "风速单位": "km/h",
            "风向": cw.get("winddirection", 0),
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "Open-Meteo API",
        }
    except Exception as e:
        return {
            "城市": "澳门",
            "当前温度": "--",
            "温度单位": "°C",
            "风速": "--",
            "风速单位": "km/h",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "Open-Meteo API",
            "错误": str(e),
        }


def fetch_air_quality_upstream() -> dict:
    """[上游] 调用 WAQI 获取澳门空气质量"""
    try:
        response = requests.get(WAQI_URL, timeout=10)
        data = response.json()
        if data.get("status") == "ok":
            d = data["data"]
            return {
                "城市": d["city"]["name"],
                "空气质量指数": d["aqi"],
                "PM2.5浓度": d["iaqi"].get("pm25", {}).get("v", "暂无"),
                "PM10浓度": d["iaqi"].get("pm10", {}).get("v", "暂无"),
                "数据更新时间": d["time"]["s"],
                "数据来源": "WAQI API",
            }
        return {
            "城市": "澳门",
            "空气质量指数": "暂无",
            "PM2.5浓度": "暂无",
            "PM10浓度": "暂无",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "WAQI API",
            "提示": "API返回状态异常",
        }
    except Exception as e:
        return {
            "城市": "澳门",
            "空气质量指数": "暂无",
            "PM2.5浓度": "暂无",
            "PM10浓度": "暂无",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据来源": "WAQI API",
            "错误": str(e),
        }


# ---------- MongoDB ----------
def _get_mongo_client():
    """获取 MongoDB 客户端（连接失败返回 None）"""
    if not PYMONGO_AVAILABLE:
        return None
    try:
        mongo_uri = os.getenv("MONGO_URI", "")
        if not mongo_uri:
            return None
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        return client
    except Exception as e:
        print(f"[上游] MongoDB 连接失败: {e}")
        return None


def init_bus_data_from_excel():
    """
    将 Excel 公交数据导入 MongoDB Atlas（仅首次运行，无数据时才导入）
    这是从 Excel 迁移到云端 MongoDB 的关键步骤
    """
    client = _get_mongo_client()
    if not client:
        print("[上游] MongoDB 未连接，跳过数据导入")
        return False

    db = client[os.getenv("MONGO_DB_NAME", "smart_exit")]
    collection = db["bus_stops"]

    if collection.count_documents({}) > 0:
        print(f"[上游] MongoDB 已有 {collection.count_documents({})} 条公交数据")
        return True

    if not os.path.exists(EXCEL_FILE):
        print(f"[上游] 未找到 {EXCEL_FILE}，跳过导入")
        return False

    try:
        df = pd.read_excel(EXCEL_FILE)
        records = df.to_dict(orient="records")
        if records:
            collection.insert_many(records)
            print(f"[上游] [OK] 成功导入 {len(records)} 条公交数据到 MongoDB Atlas")
        return True
    except Exception as e:
        print(f"[上游] [X] Excel 导入失败: {e}")
        return False


def list_bus_stops() -> list:
    """
    枚举所有站点编号（供预载服务器一次性预载全部站点）。
    优先 MongoDB，降级 Excel。
    """
    stops = []

    client = _get_mongo_client()
    if client:
        try:
            db = client[os.getenv("MONGO_DB_NAME", "smart_exit")]
            stops = [str(s) for s in db["bus_stops"].distinct("站点编号") if s]
        except Exception as e:
            print(f"[上游] 枚举站点失败(MongoDB): {e}")

    if not stops and os.path.exists(EXCEL_FILE):
        try:
            df = pd.read_excel(EXCEL_FILE)
            stops = [str(s) for s in df["站点编号"].dropna().unique()]
        except Exception as e:
            print(f"[上游] 枚举站点失败(Excel): {e}")

    return stops


def fetch_bus_upstream(stop_id: str = "M02") -> dict:
    """
    [上游] 读取指定站点的巴士到站信息。
    优先级：MongoDB Atlas 云端 → Excel 本地降级。
    """
    client = _get_mongo_client()
    if client:
        try:
            db = client[os.getenv("MONGO_DB_NAME", "smart_exit")]
            stop_buses = list(db["bus_stops"].find({"站点编号": stop_id}, {"_id": 0}))
            if stop_buses:
                bus_list = [{
                    "线路": str(b.get("线路", "?")),
                    "预计到站(分钟)": str(b.get("预计到站(分钟)", "?")),
                    "方向": str(b.get("方向", "")),
                } for b in stop_buses]
                return {
                    "站点编号": stop_id,
                    "站点名称": str(stop_buses[0].get("站点名称", stop_id)),
                    "巴士信息": bus_list,
                    "数据来源": "MongoDB Atlas 云端",
                    "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
        except Exception as e:
            print(f"[上游] MongoDB 查询失败: {e}")

    return _fetch_bus_from_excel(stop_id)


def _fetch_bus_from_excel(stop_id: str):
    """[上游] Excel 本地降级"""
    if not os.path.exists(EXCEL_FILE):
        return {
            "站点编号": stop_id,
            "站点名称": "示例站点",
            "巴士信息": [
                {"线路": "3", "预计到站(分钟)": "2", "方向": "关闸"},
                {"线路": "10", "预计到站(分钟)": "5", "方向": "妈阁"},
            ],
            "数据来源": "示例数据（无数据源）",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "提示": "请创建 bus_data.xlsx 或配置 MongoDB Atlas",
        }

    try:
        df = pd.read_excel(EXCEL_FILE)
        stop_buses = df[df["站点编号"] == stop_id]
        if len(stop_buses) == 0:
            return {
                "站点编号": stop_id,
                "站点名称": "未找到",
                "巴士信息": [],
                "数据来源": "Excel 本地",
                "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "提示": f"未找到站点 {stop_id}，请检查数据",
            }
        bus_list = [{
            "线路": str(row["线路"]),
            "预计到站(分钟)": str(row["预计到站(分钟)"]),
            "方向": str(row["方向"]),
        } for _, row in stop_buses.iterrows()]
        return {
            "站点编号": stop_id,
            "站点名称": str(stop_buses.iloc[0]["站点名称"]),
            "巴士信息": bus_list,
            "数据来源": "Excel 本地（MongoDB 未配置）",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {
            "站点编号": stop_id,
            "站点名称": "读取失败",
            "巴士信息": [],
            "数据来源": "无",
            "错误信息": str(e),
        }
