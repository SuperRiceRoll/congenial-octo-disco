"""
微服务6：AI 智能建议引擎 (AI Suggestion Engine)
================================================
将天气、空气质量、公交数据发送给大语言模型，生成个性化出行建议
替代原来的规则引擎（保留作为降级方案）

支持的 AI 平台：
- DeepSeek（推荐，国内可用，注册送额度）
- 智谱AI (GLM) 
- 阿里通义千问
"""
import os
import json
from datetime import datetime
import requests

from data_store import get_store
from upstream_client import UpstreamError

# ========== 配置 ==========
# 从环境变量读取 AI 配置
AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")       # deepseek | glm | qwen
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_API_URL = os.getenv("AI_API_URL", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")

# ========== API 端点配置 ==========
API_ENDPOINTS = {
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat"
    },
    "glm": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash"
    },
    "qwen": {
        "url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        "model": "qwen-turbo"
    }
}


def _get_ai_config():
    """获取 AI 配置（支持环境变量覆盖）"""
    provider = AI_PROVIDER.lower()
    if AI_API_URL and AI_MODEL:
        return AI_API_URL, AI_MODEL

    defaults = API_ENDPOINTS.get(provider, API_ENDPOINTS["deepseek"])
    return defaults["url"], defaults["model"]


def _build_prompt(weather_data: dict, air_data: dict, bus_data: dict, user_tags: list = None) -> str:
    """构造发送给 AI 的 prompt"""
    temp = weather_data.get("当前温度", "未知")
    wind = weather_data.get("风速", "未知")
    humidity = weather_data.get("湿度", "未知")
    weather_desc = weather_data.get("天气描述", "未知")

    aqi = air_data.get("空气质量指数", "未知")
    aqi_level = air_data.get("等级", "未知")
    pm25 = air_data.get("PM2.5浓度", "未知")
    pm10 = air_data.get("PM10浓度", "未知")

    bus_list = bus_data.get("巴士信息", [])
    stop_name = bus_data.get("站点名称", bus_data.get("站点编号", "当前站点"))

    # 公交摘要
    if bus_list:
        route_summary = "、".join([b.get("线路", "?") for b in bus_list[:5]])
        bus_summary = f"站点「{stop_name}」有 {len(bus_list)} 路巴士（{route_summary}等）"
    else:
        bus_summary = f"站点「{stop_name}」暂无巴士信息"

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 用户个性化标签 → 提示词
    user_context = ""
    if user_tags:
        tag_prompt_map = {
            "sun_protection": "用户特别在意防晒，请提醒防晒措施（涂防晒霜、戴帽子、带伞）。",
            "like_walking": "用户喜欢步行，请给出步行相关的建议（路程距离、炎热程度、是否适合步行）。",
            "like_transit": "用户偏好公共交通，请着重推荐巴士出行方案。",
            "like_driving": "用户偏好开车，请提醒交通拥堵情况和停车建议。",
            "health_asthma": "用户有哮喘或呼吸道敏感，请特别关注空气质量对呼吸的影响。",
            "health_joint": "用户有关节问题，请提醒天气湿度变化对关节的影响。",
            "pet_dog": "用户要遛狗，请给出适合遛狗的时段和天气提醒。",
            "with_kid": "用户带小孩出门，请给出带娃出门的特别提醒（防晒、饮水、穿衣）。"
        }
        user_prompts = []
        for tag in user_tags:
            if tag in tag_prompt_map:
                user_prompts.append(f"- {tag_prompt_map[tag]}")
        if user_prompts:
            user_context = "\n【用户个性化偏好】\n" + "\n".join(user_prompts)

    prompt = f"""你是一个澳门居民出行助手，请根据以下实时数据，给出3条简短实用的出行建议。

当前时间：{current_time}

【天气状况】
温度：{temp}°C
天气：{weather_desc}
风速：{wind} km/h
湿度：{humidity}%

【空气质量】
AQI：{aqi}（{aqi_level}）
PM2.5：{pm25} ug/m³
PM10：{pm10} ug/m³

【公共交通】
{bus_summary}
{user_context}
请按照 JSON 格式回复，不要包含其他内容：
```json
{{
  "suggestions": [
    {{"icon": "👕", "category": "穿衣", "message": "..."}},
    {{"icon": "🚶", "category": "出行", "message": "..."}},
    {{"icon": "💊", "category": "健康", "message": "..."}}
  ]
}}
```
语气亲切友好，每条建议一句话。"""
    return prompt


def _call_ai_api(prompt: str) -> dict:
    """调用 AI API"""
    if not AI_API_KEY:
        return {"error": "未配置 AI API Key", "fallback": True}

    api_url, model = _get_ai_config()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AI_API_KEY}"
    }

    # 通义千问的请求格式不同
    if AI_PROVIDER.lower() == "qwen":
        payload = {
            "model": model,
            "input": {
                "messages": [{"role": "user", "content": prompt}]
            }
        }
    else:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 500
        }

    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=15
        )

        if response.status_code != 200:
            return {"error": f"API 返回 {response.status_code}", "fallback": True}

        data = response.json()

        # 解析不同平台的返回格式
        if AI_PROVIDER.lower() == "qwen":
            content = data.get("output", {}).get("text", "")
        else:
            content = data["choices"][0]["message"]["content"]

        # 提取 JSON（去掉 markdown 代码块）
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            result = json.loads(content)
            return result
        except json.JSONDecodeError:
            # AI 返回的不是 JSON，直接作为文本返回
            return {
                "suggestions": [
                    {"icon": "🤖", "category": "AI建议", "message": content}
                ]
            }

    except requests.exceptions.Timeout:
        return {"error": "AI API 请求超时", "fallback": True}
    except Exception as e:
        return {"error": str(e), "fallback": True}


# ========== 从 suggestion_service 导入规则引擎作为降级方案 ==========
from suggestion_service import evaluate_combined as _rule_engine


def suggestion_cache_key(user_tags) -> str:
    """统一的建议缓存 key（预载线程与用户请求链路必须一致）"""
    tags = user_tags or []
    tag_suffix = ",".join(sorted(tags)) if tags else ""
    return f"ai_suggestions:{tag_suffix}" if tag_suffix else "ai_suggestions:all"


def _rule_result(weather_data: dict, air_data: dict, bus_data: dict, note: str = "规则引擎") -> dict:
    rule_suggestions = _rule_engine(weather_data, air_data, bus_data)
    return {
        "建议列表": rule_suggestions,
        "建议数量": len(rule_suggestions),
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "来源": note,
        "provider": "rule_engine",
    }


def generate_ai_suggestions(weather_data: dict, air_data: dict, bus_data: dict, user_tags: list = None) -> dict:
    """
    [仅由本地预载线程调用] 真正调用大模型生成建议并写入快照。
    失败时抛 UpstreamError，交由预载线程保留上一次好快照（绝不覆盖）。
    """
    if user_tags is None:
        user_tags = []
    if not AI_API_KEY:
        raise UpstreamError("未配置 AI API Key")

    prompt = _build_prompt(weather_data, air_data, bus_data, user_tags=user_tags)
    ai_result = _call_ai_api(prompt)
    if "fallback" in ai_result:
        raise UpstreamError(ai_result.get("error", "AI 调用失败"))

    result = {
        "建议列表": ai_result.get("suggestions", []),
        "建议数量": len(ai_result.get("suggestions", [])),
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "来源": "AI 智能引擎 🤖",
        "provider": AI_PROVIDER,
    }
    get_store().set(suggestion_cache_key(user_tags), result)
    return result


def fetch_ai_suggestions(weather_data: dict, air_data: dict, bus_data: dict, user_tags: list = None) -> dict:
    """
    [用户请求链路调用] **只读本地快照，绝不外呼第三方**。
    命中快照 → 直接返回；未命中 → 用本地规则引擎降级并缓存；
    AI 建议由后台预载线程定时生成（见 preload_service._run_suggestions）。
    - user_tags: 用户个性化标签列表，如 ["sun_protection", "like_walking"]
    """
    if user_tags is None:
        user_tags = []

    store = get_store()
    cache_key = suggestion_cache_key(user_tags)
    cached = store.get(cache_key)
    if cached is not None:
        return cached

    # 未命中：绝不同步调用大模型（否则又变回「用户请求直连第三方」）。
    # 先用本地规则引擎给出可用建议，后台预载线程随后会补齐 AI 版本。
    result = _rule_result(weather_data, air_data, bus_data, note="规则引擎（AI 建议预载中）")
    store.set(cache_key, result, status="degraded")
    return result
