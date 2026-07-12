"""
微服务3：公交服务 (Bus Service)
负责从 MongoDB 云端数据库读取巴士到站数据
预留 DSAT SOAP Web Service 接口
Excel 本地文件作为降级备份
支持 Redis 缓存
"""
import os
import pandas as pd
from datetime import datetime
from redis_cache import get_cache, CacheTTL

# MongoDB 驱动
# 已降级到 pymongo 3.12 解决 Python 3.14 + OpenSSL 3.5 TLS 兼容问题
PYMONGO_AVAILABLE = True

try:
    from pymongo import MongoClient
except ImportError:
    PYMONGO_AVAILABLE = False

# Windows DNS 解析修复（dnspython 1.16 兼容）
try:
    import dns.resolver
    _dns = dns.resolver.Resolver(configure=False)
    _dns.nameservers = ['8.8.8.8', '1.1.1.1']
    dns.resolver.default_resolver = _dns
except Exception:
    pass


def _get_mongo_client():
    """获取 MongoDB 客户端"""
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
        print(f"[公交服务] MongoDB 连接失败: {e}")
        return None


def init_bus_data_from_excel():
    """
    将 Excel 公交数据导入 MongoDB Atlas（仅首次运行）
    这是从 Excel 迁移到云端 MongoDB 的关键步骤
    """
    client = _get_mongo_client()
    if not client:
        print("[公交服务] MongoDB 未连接，跳过数据导入")
        return False

    db = client[os.getenv("MONGO_DB_NAME", "smart_exit")]
    collection = db["bus_stops"]

    if collection.count_documents({}) > 0:
        print(f"[公交服务] MongoDB 已有 {collection.count_documents({})} 条公交数据")
        return True

    excel_file = "bus_data.xlsx"
    if not os.path.exists(excel_file):
        print(f"[公交服务] 未找到 {excel_file}，跳过导入")
        return False

    try:
        df = pd.read_excel(excel_file)
        records = df.to_dict(orient="records")
        if records:
            collection.insert_many(records)
            print(f"[公交服务] [OK] 成功导入 {len(records)} 条公交数据到 MongoDB Atlas")
        return True
    except Exception as e:
        print(f"[公交服务] [X] Excel 导入失败: {e}")
        return False


def fetch_bus_data(stop_id: str = "M02"):
    """
    获取指定站点的巴士到站信息

    数据获取优先级:
        1. Redis 缓存 (30秒 TTL)
        2. MongoDB Atlas 云端数据库
        3. Excel 本地文件（降级备份）

    Args:
        stop_id: 站点编号，如 "M02"

    Returns:
        dict: 站点巴士信息
    """
    cache = get_cache()
    cache_key = f"bus:stop:{stop_id}"

    # 1. Redis 缓存
    cached = cache.get(cache_key)
    if cached:
        return cached

    # 2. MongoDB Atlas 云端
    client = _get_mongo_client()
    if client:
        try:
            db = client[os.getenv("MONGO_DB_NAME", "smart_exit")]
            collection = db["bus_stops"]
            stop_buses = list(collection.find({"站点编号": stop_id}, {"_id": 0}))

            if stop_buses:
                bus_list = [{
                    "线路": str(b.get("线路", "?")),
                    "预计到站(分钟)": str(b.get("预计到站(分钟)", "?")),
                    "方向": str(b.get("方向", ""))
                } for b in stop_buses]

                result = {
                    "站点编号": stop_id,
                    "站点名称": str(stop_buses[0].get("站点名称", stop_id)),
                    "巴士信息": bus_list,
                    "数据来源": "MongoDB Atlas 云端",
                    "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                cache.set(cache_key, result, ttl=CacheTTL.BUS)
                return result
        except Exception as e:
            print(f"[公交服务] MongoDB 查询失败: {e}")

    # 3. Excel 本地降级
    return _fetch_bus_from_excel(stop_id)


def _fetch_bus_from_excel(stop_id: str):
    """从 Excel 文件读取巴士信息（降级备份）"""
    excel_file = "bus_data.xlsx"

    if not os.path.exists(excel_file):
        return {
            "站点编号": stop_id,
            "站点名称": "示例站点",
            "巴士信息": [
                {"线路": "3", "预计到站(分钟)": "2", "方向": "关闸"},
                {"线路": "10", "预计到站(分钟)": "5", "方向": "妈阁"}
            ],
            "数据来源": "示例数据（无数据源）",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "提示": "请创建 bus_data.xlsx 或配置 MongoDB Atlas"
        }

    try:
        df = pd.read_excel(excel_file)
        stop_buses = df[df["站点编号"] == stop_id]

        if len(stop_buses) == 0:
            return {
                "站点编号": stop_id,
                "站点名称": "未找到",
                "巴士信息": [],
                "数据来源": "Excel 本地",
                "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "提示": f"未找到站点 {stop_id}，请检查数据"
            }

        bus_list = []
        for _, row in stop_buses.iterrows():
            bus_list.append({
                "线路": str(row["线路"]),
                "预计到站(分钟)": str(row["预计到站(分钟)"]),
                "方向": str(row["方向"])
            })

        result = {
            "站点编号": stop_id,
            "站点名称": str(stop_buses.iloc[0]["站点名称"]),
            "巴士信息": bus_list,
            "数据来源": "Excel 本地（MongoDB 未配置）",
            "数据更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # 也缓存 Excel 结果（短 TTL）
        cache = get_cache()
        cache.set(f"bus:stop:{stop_id}", result, ttl=CacheTTL.BUS)
        return result

    except Exception as e:
        return {
            "站点编号": stop_id,
            "站点名称": "读取失败",
            "巴士信息": [],
            "数据来源": "无",
            "错误信息": str(e)
        }
