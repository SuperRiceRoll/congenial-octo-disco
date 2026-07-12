"""
微服务5：用户认证服务 (User Authentication Service)
=================================================
功能：用户注册、登录、Token 管理、偏好存储
数据：MongoDB 存储用户信息
"""
import os
import hashlib
import time
import uuid
from datetime import datetime
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# MongoDB 连接（复用已有连接配置）
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "smart_exit")

_client = None
_db = None


def _get_db():
    """获取 MongoDB 数据库连接"""
    global _client, _db
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        _db = _client[MONGO_DB_NAME]
        # 确保用户集合有唯一索引
        _db.users.create_index("username", unique=True)
        _db.users.create_index("email", unique=True)
    return _db


def _hash_password(password: str) -> str:
    """SHA-256 密码哈希（带盐）"""
    salt = "smart_exit_salt_2026"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _generate_token(username: str) -> str:
    """生成简易 token"""
    raw = f"{username}_{uuid.uuid4().hex}_{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ========== 公开 API ==========

def register_user(username: str, email: str, password: str) -> dict:
    """
    用户注册
    返回: {"success": True, "message": "...", "token": "xxx"}
          {"success": False, "message": "用户名已存在"}
    """
    if not username or not email or not password:
        return {"success": False, "message": "请填写所有字段"}

    if len(username) < 3:
        return {"success": False, "message": "用户名至少3个字符"}

    if len(password) < 6:
        return {"success": False, "message": "密码至少6个字符"}

    if "@" not in email:
        return {"success": False, "message": "邮箱格式不正确"}

    try:
        db = _get_db()
        user = {
            "user_id": str(uuid.uuid4()),
            "username": username,
            "email": email,
            "password_hash": _hash_password(password),
            "preferences": {
                "favorite_stops": ["M02", "M62"],
                "hide_air_quality": False,
                "reminder_time": "08:00"
            },
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_login": None
        }
        db.users.insert_one(user)

        token = _generate_token(username)

        return {
            "success": True,
            "message": "注册成功 🎉",
            "token": token,
            "user": {
                "user_id": user["user_id"],
                "username": username,
                "email": email,
                "preferences": user["preferences"]
            }
        }

    except DuplicateKeyError:
        # 检查是用户名重复还是邮箱重复
        db = _get_db()
        if db.users.find_one({"username": username}):
            return {"success": False, "message": "用户名已存在，请换一个"}
        return {"success": False, "message": "该邮箱已被注册"}

    except Exception as e:
        return {"success": False, "message": f"注册失败: {str(e)}"}


def login_user(username: str, password: str) -> dict:
    """
    用户登录
    返回: {"success": True, "message": "...", "token": "xxx", "user": {...}}
          {"success": False, "message": "用户名或密码错误"}
    """
    if not username or not password:
        return {"success": False, "message": "请填写用户名和密码"}

    try:
        db = _get_db()
        # 支持用户名或邮箱登录
        user = db.users.find_one({
            "$or": [
                {"username": username},
                {"email": username}
            ]
        })

        if not user:
            return {"success": False, "message": "用户名或密码错误"}

        if user["password_hash"] != _hash_password(password):
            return {"success": False, "message": "用户名或密码错误"}

        # 更新最后登录时间
        db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": {"last_login": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}
        )

        token = _generate_token(username)

        return {
            "success": True,
            "message": "登录成功 👋",
            "token": token,
            "user": {
                "user_id": user["user_id"],
                "username": user["username"],
                "email": user["email"],
                "preferences": user.get("preferences", {})
            }
        }

    except Exception as e:
        return {"success": False, "message": f"登录失败: {str(e)}"}


def get_user_preferences(user_id: str) -> dict:
    """获取用户偏好"""
    try:
        db = _get_db()
        user = db.users.find_one({"user_id": user_id})
        if user:
            return {"success": True, "preferences": user.get("preferences", {})}
        return {"success": False, "message": "用户不存在"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def update_user_preferences(user_id: str, preferences: dict) -> dict:
    """更新用户偏好"""
    try:
        db = _get_db()
        result = db.users.update_one(
            {"user_id": user_id},
            {"$set": {"preferences": preferences}}
        )
        if result.modified_count > 0:
            return {"success": True, "message": "偏好已更新 ✅"}
        return {"success": False, "message": "未找到用户"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def verify_token(token: str) -> Optional[dict]:
    """
    Token 验证（简易版）
    返回用户信息或 None
    """
    # 简易实现：token 存于内存/请求中，不做持久化
    # 生产环境应用 JWT
    if token:
        return {"user_id": "token_user", "username": "token_user"}
    return None
