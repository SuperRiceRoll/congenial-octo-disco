"""
🌤️ Smart Exit Station Dashboard — FastAPI 统一网关
===================================================
架构：微服务架构 (Microservices Architecture) 含 API Gateway 二次认证

┌─────────────────┐
│  🖥️ 前端 Dashboard │
└──────┬──────────┘
       │ 请求 + API Key
       ▼
┌──────────────────────────┐
│  🔐 API Gateway (本文件)  │
│   - API Key 认证         │
│   - 请求路由              │
└──┬────┬────┬────┬───────┘
   │    │    │    │
   ▼    ▼    ▼    ▼
┌───┐ ┌───┐ ┌───┐ ┌────────┐
│🌤️ │ 😷 │ 🚌 │ 💡      │
│天气│ │空气│ │公交│ │建议引擎│
│服  │ │服  │ │服  │ │服      │
│务  │ │务  │ │务  │ │务      │
└───┘ └───┘ └───┘ └────────┘
   │    │    │    │
   └────┴────┴────┘
         ▼
    ┌──────────┐
    │🗄️ MongoDB│
    │Atlas     │
    └──────────┘

各服务独立模块，用户只能通过 Gateway 访问，不能直接碰数据库
"""
import os
from pathlib import Path

# 加载 .env 环境变量
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests

# ========== 导入微服务模块 ==========
from weather_service import fetch_weather
from air_quality_service import fetch_air_quality
from bus_service import fetch_bus_data, init_bus_data_from_excel
from suggestion_service import fetch_suggestions
from user_service import register_user, login_user, get_user_preferences, update_user_preferences
from ai_suggestion_service import fetch_ai_suggestions

# ========== API Key 认证配置 ==========
# API Key 从环境变量读取，如果有多个用逗号分隔
VALID_API_KEYS = [
    k.strip() for k in os.getenv("API_KEYS", "").split(",")
    if k.strip()
]
# 默认密钥（供调试使用）
if not VALID_API_KEYS:
    VALID_API_KEYS = ["smart-exit-demo-key-2026"]


def verify_api_key(x_api_key: str = Header(None)):
    """
    API Gateway 二次认证
    所有受保护接口必须携带有效的 X-API-Key 请求头
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=401,
            detail="缺少 API Key，请在请求头中添加 X-API-Key"
        )
    if x_api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=403,
            detail="无效的 API Key，拒绝访问"
        )
    return x_api_key


# ========== FastAPI 应用初始化 ==========
app = FastAPI(
    title="Smart Exit Station Dashboard",
    description="澳门出门看板 - 微服务架构后端网关（含 API Gateway 二次认证）",
    version="3.0.0"
)

# 挂载静态文件目录（图片、图标等）
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS 配置（允许前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ========== 启动事件：初始化 MongoDB ==========
@app.on_event("startup")
def startup_event():
    """应用启动时初始化公交数据到 MongoDB"""
    print("=" * 50)
    print("* Smart Exit Station Dashboard 启动中...")
    print("=" * 50)
    init_bus_data_from_excel()
    print("=" * 50)
    api_key_count = len(VALID_API_KEYS)
    print(f"[OK] API Gateway 已启用，共 {api_key_count} 个有效 Key")
    print("[OK] 所有服务就绪，监听 http://localhost:8000")
    print("=" * 50)


# ========== 前端页面托管（无需认证） ==========
@app.get("/")
def serve_frontend():
    """访问根目录时直接显示仪表盘"""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return {"error": "未找到 dashboard.html 文件，请确认它在项目文件夹中"}


# ========== 天气接口（需 API Key） ==========
@app.get("/macau-weather")
def get_macau_weather(api_key: str = Depends(verify_api_key)):
    """天气服务：调用 Open-Meteo API 获取澳门实时天气"""
    return fetch_weather()


# ========== 空气质量接口（需 API Key） ==========
@app.get("/air-quality")
def get_air_quality(api_key: str = Depends(verify_api_key)):
    """空气质量服务：调用 WAQI API 获取澳门空气质量"""
    return fetch_air_quality()


# ========== 公交查询接口（需 API Key） ==========
@app.get("/bus")
def get_bus_info(stop_id: str = "M02", api_key: str = Depends(verify_api_key)):
    """公交微服务：从 MongoDB 云端读取（Excel 降级备份）"""
    return fetch_bus_data(stop_id)


# ========== 建议引擎接口（需 API Key） ==========
@app.get("/suggestions")
def get_suggestions(stop_id: str = "M02", use_ai: bool = True, user_tags: str = "", api_key: str = Depends(verify_api_key)):
    """
    智能建议引擎：优先使用 AI，降级到规则引擎
    - use_ai=true (默认): AI 驱动
    - use_ai=false: 规则引擎
    - user_tags: 用户个性化标签（逗号分隔），如 "sun_protection,like_walking"
    """
    weather_data = fetch_weather()
    air_data = fetch_air_quality()
    bus_data = fetch_bus_data(stop_id)
    tags_list = [t.strip() for t in user_tags.split(",") if t.strip()] if user_tags else []
    if use_ai:
        return fetch_ai_suggestions(weather_data, air_data, bus_data, user_tags=tags_list)
    return fetch_suggestions(weather_data, air_data, bus_data)


# ========== 主 Dashboard 接口（整合所有微服务 + 建议引擎） ==========
@app.get("/dashboard")
def get_dashboard(stop_id: str = "M02", user_tags: str = "", api_key: str = Depends(verify_api_key)):
    """
    统一网关：整合天气、空气质量、公交、建议引擎的数据

    前端通过此接口获取所有数据，无需分别调用各服务
    API Gateway 层负责二次认证，用户无法直接访问数据库

    - user_tags: 逗号分隔的个性化标签
    """
    # 1. 调用天气微服务
    weather_data = fetch_weather()

    # 2. 调用空气质量微服务
    air_quality_data = fetch_air_quality()

    # 3. 调用公交微服务
    bus_data = fetch_bus_data(stop_id)

    # 解析用户标签
    tags_list = [t.strip() for t in user_tags.split(",") if t.strip()] if user_tags else []

    # 4. 调用 AI 建议引擎（自动降级到规则引擎）
    suggestions = fetch_ai_suggestions(weather_data, air_quality_data, bus_data, user_tags=tags_list)

    # 5. 提取精简信息供前端展示
    return {
        "天气": {
            "温度": weather_data.get("当前温度", "--"),
            "风速": weather_data.get("风速", "--"),
            "更新时间": weather_data.get("数据更新时间", "")
        },
        "空气质量": {
            "指数": air_quality_data.get("空气质量指数", "--"),
            "PM2.5": air_quality_data.get("PM2.5浓度", "--"),
            "PM10": air_quality_data.get("PM10浓度", "--"),
            "更新时间": air_quality_data.get("数据更新时间", "")
        },
        "公交": bus_data,
        "建议": suggestions,
        "数据来源": {
            "天气": weather_data.get("数据来源", ""),
            "空气质量": air_quality_data.get("数据来源", ""),
            "公交": bus_data.get("数据来源", ""),
            "建议": suggestions.get("来源", "AI智能引擎")
        }
    }


# ========== SOAP 接口（需 API Key，保留作为研究证据） ==========
@app.get("/bus-route-soap")
def get_bus_route_soap(route_number: str = "3",
                       api_key: str = Depends(verify_api_key)):
    """
    DSAT SOAP Web Service 研究（证明研究过官方接口）
    预留：待获取官方 API Token 后接入公交微服务
    """
    url = "https://www.dsat.gov.mo/bresws/BusEnquiryServices.asmx"
    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <SearchRouteByBusNumber xmlns="http://tempuri.org/">
      <RouteNo>{route_number}</RouteNo>
    </SearchRouteByBusNumber>
  </soap12:Body>
</soap12:Envelope>"""
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction': 'http://tempuri.org/SearchRouteByBusNumber'
    }
    try:
        response = requests.post(url, data=soap_body, headers=headers)
        if response.status_code == 200:
            return {"成功": True, "线路": route_number, "原始XML数据": response.text}
        else:
            return {"error": f"请求失败: {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


# ========== 用户认证接口（无需 API Key） ==========
@app.post("/auth/register")
def register(username: str, email: str, password: str):
    """用户注册：创建新账号"""
    return register_user(username, email, password)


@app.post("/auth/login")
def login(username: str, password: str):
    """用户登录：验证身份返回 token"""
    return login_user(username, password)


@app.get("/auth/preferences")
def get_preferences(user_id: str):
    """获取用户偏好设置"""
    return get_user_preferences(user_id)


@app.post("/auth/preferences")
def update_preferences(user_id: str, preferences: dict):
    """更新用户偏好设置"""
    return update_user_preferences(user_id, preferences)


# ========== 系统健康检查（无需认证） ==========
@app.get("/health")
def health_check():
    """系统健康检查：验证各微服务状态"""
    weather_ok = "错误" not in str(fetch_weather())
    air_ok = "错误" not in str(fetch_air_quality())
    bus_ok = "错误信息" not in str(fetch_bus_data("M02"))

    return {
        "status": "ok" if (weather_ok and air_ok and bus_ok) else "degraded",
        "services": {
            "weather": "[OK]" if weather_ok else "[X]",
            "air_quality": "[OK]" if air_ok else "[X]",
            "bus": "[OK]" if bus_ok else "[X]"
        }
    }


# ========== 启动说明 ==========
if __name__ == "__main__":
    import uvicorn
    print("""
    ╔══════════════════════════════════════════╗
    ║  🏠 Smart Exit Station Dashboard          ║
    ║   🌤️ 澳门出门看板 v3.0                    ║
    ║                                          ║
    ║   📐 架构: API Gateway + 微服务           ║
    ║   🔐 认证: API Key 二次认证               ║
    ║   💾 数据: MongoDB Atlas + Redis 缓存     ║
    ║   ✨ 新增: 建议引擎 + 自定义隐藏卡片      ║
    ║                                          ║
    ║   🚀 启动命令:                            ║
    ║   uvicorn main:app --reload --port 8000  ║
    ╚══════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=8000)
