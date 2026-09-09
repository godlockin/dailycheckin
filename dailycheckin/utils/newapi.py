"""new-api (及其 fork) 签到端点探测器。

各 fork 共用同一组 REST 端点 + 变体; 用户认证走 session cookie + 可选 header
(New-Api-User / Veloera-User)。本模块通过 HTTP-only fetch 探测可用端点,
返回 {ok, message, already, status}。
"""
from __future__ import annotations

import re
from typing import Any

ENDPOINTS = [
    ("POST", "/api/user/checkin"),
    ("GET", "/api/user/checkin"),
    ("POST", "/api/user/check_in"),
    ("GET", "/api/user/check_in"),
    ("POST", "/api/user/check-in"),
    ("GET", "/api/user/check-in"),
]

ALREADY_PAT = re.compile(r"已签到|重复签到|already|今日已|无需签到|不能重复")
OK_PAT = re.compile(r"签到成功|成功|success|ok|获得|积分|额度|余额|quota", re.I)


def try_checkin(bridge, tab_id: str, user_id: str | None) -> dict[str, Any]:
    """通过 CDP 页面上下文 fetch 依次尝试各签到端点。

    bridge: CDPBridge 实例
    tab_id: 已经在站点域的 tab 句柄
    user_id: localStorage.user.id 或 /api/user/self 返回的 id; 用于 New-Api-User header
    """
    headers = None
    if user_id:
        headers = {"New-Api-User": user_id, "Veloera-User": user_id}
    last_msg = ""
    for method, path in ENDPOINTS:
        res = bridge.fetch(tab_id, path, method, headers)
        if not res:
            continue
        status, body = res.get("status"), res.get("body")
        if status == 404 or body is None:
            continue
        msg = ""
        success = False
        if isinstance(body, dict):
            msg = str(body.get("message") or body.get("msg") or "")
            success = bool(body.get("success", False))
        if status == 401 or "未登录" in msg or "无权" in msg:
            last_msg = msg or "未登录"
            continue
        if ALREADY_PAT.search(msg):
            return {"status": "already", "message": msg}
        if success or OK_PAT.search(msg):
            return {"status": "ok", "message": msg}
        last_msg = msg or f"HTTP {status}"
        if msg:
            return {"status": "failed", "message": last_msg}
    return {"status": "no_api", "message": last_msg or "无可用签到端点"}


def get_user_id(bridge, tab_id: str) -> str | None:
    """从 localStorage.user 读 id; 失败返回 None。"""
    return bridge.eval(
        tab_id,
        "(() => { try { const u = JSON.parse(localStorage.getItem('user') || 'null');"
        " return u && u.id != null ? String(u.id) : null } catch { return null } })()",
        await_promise=False,
    )


def fetch_self(bridge, tab_id: str, user_id: str | None) -> dict[str, Any] | None:
    """验证当前 tab 是否已登录; 返回 /api/user/self 的 data 部分, 未登录返回 None。"""
    headers = None
    if user_id:
        headers = {"New-Api-User": user_id, "Veloera-User": user_id}
    res = bridge.fetch(tab_id, "/api/user/self", "GET", headers)
    if not res:
        return None
    body = res.get("body")
    if res.get("status") == 200 and isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            return data
    return None