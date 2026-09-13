"""pkulaw 北大法宝 Keycloak token 自动续期。

JWT 是 Keycloak 颁发的 (iss: cas.pkulaw.com/auth/realms/fabao)。
典型 access_token 寿命 30min, refresh_token 寿命 7-30天。

如果 config.json 提供了 `refresh_token` (推荐), 会在调用 claim
之前 / 收到 401 之后自动用 refresh_token 换新 access_token + refresh_token,
并原子写回 config.json。

要拿 refresh_token: 见 README - 用 `python -m dailycheckin.pkulaw.login_helper`
或浏览器 DevTools 从 keycloak session 里取。
"""
from __future__ import annotations

import json
import logging
import os
import time
from base64 import b64decode
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("dailycheckin.pkulaw.auth")

# Keycloak realm 从 token 的 iss 字段自动抽取, 也可手动覆盖
DEFAULT_KEYCLOAK_BASE = "https://cas.pkulaw.com"
DEFAULT_REALM = "fabao"
REFRESH_TIMEOUT = 20


def decode_jwt(token: str) -> dict[str, Any] | None:
    """不验签, 仅 base64-decode JWT payload (中间那段)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # 补齐 padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return None


def is_token_expired(token: str, skew_sec: int = 60) -> bool:
    """JWT exp < now()+skew_sec 即视为过期 (含即将过期)"""
    payload = decode_jwt(token)
    if not payload or "exp" not in payload:
        return True
    return payload["exp"] < time.time() + skew_sec


def token_endpoint(realm: str, base: str = DEFAULT_KEYCLOAK_BASE) -> str:
    return f"{base}/auth/realms/{realm}/protocol/openid-connect/token"


def refresh_tokens(
    refresh_token: str,
    *,
    realm: str = DEFAULT_REALM,
    base: str = DEFAULT_KEYCLOAK_BASE,
    client_ids: list[str | None] | None = None,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """调 Keycloak refresh_token grant, 返回新 token dict。

    兼容策略: 当一个 client_id 报 unauthorized_client 时, 自动 fallback 下一个。
    常见原因: wso2 client 配成 confidential (要求 client_secret), 但 PKULAW 用 public 模式登录。
    Fallback 顺序覆盖常见 public client 配置。

    成功: {"access_token": ..., "refresh_token": ..., "expires_in": ..., "refresh_expires_in": ...}
    失败抛 RefreshError (所有 client_id 都失败后)。
    """
    if client_ids is None:
        client_ids = [None, "wso2", "wso2-is", "account", "public-cli", "mcp"]
    last_err: str = ""
    for cid in client_ids:
        data: dict[str, Any] = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        if cid:
            data["client_id"] = cid
        if client_secret:
            data["client_secret"] = client_secret
        try:
            resp = requests.post(
                token_endpoint(realm, base),
                data=data,
                timeout=REFRESH_TIMEOUT,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as e:
            last_err = f"client_id={cid!r}: request error: {e}"
            continue
        if resp.status_code == 200:
            payload = resp.json()
            if "access_token" in payload:
                return payload
            last_err = f"client_id={cid!r}: no access_token in response"
            continue
        last_err = f"client_id={cid!r}: HTTP {resp.status_code}: {resp.text[:200]}"
    raise RefreshError(f"all client_ids failed: {last_err}")


class RefreshError(Exception):
    pass


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """原子写: tmp + rename, 避免半写文件"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def persist_new_tokens(config_path: Path, account_idx: int, new_access: str, new_refresh: str) -> bool:
    """把新 token 写回 config.json (按账号 index 定位). 失败返回 False (不抛)."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        accounts = data.get("PKULAW") or []
        if not (0 <= account_idx < len(accounts)):
            return False
        accounts[account_idx]["token"] = new_access
        if new_refresh:
            accounts[account_idx]["refresh_token"] = new_refresh
        # 移除旧 expires_at 缓存 (如存在)
        accounts[account_idx].pop("expires_at", None)
        _atomic_write(config_path, data)
        return True
    except Exception as e:
        logger.warning("持久化新 token 失败: %s", e)
        return False


def find_config_path() -> Path | None:
    """猜 config.json 位置 (按 dailycheckin 约定顺序)."""
    cwd = Path.cwd()
    for rel in [
        "config/config.json",
        "../config/config.json",
        "config.json",
        "../config.json",
    ]:
        p = (cwd / rel).resolve()
        if p.exists():
            return p
    return None


def parse_iss_for_realm(token: str, default_base: str = DEFAULT_KEYCLOAK_BASE) -> tuple[str, str] | None:
    """从 JWT iss 字段解析 (base, realm). iss 形如 https://cas.pkulaw.com/auth/realms/fabao

    返回的 base 是 issuer 的"根" URL (无 /auth): https://cas.pkulaw.com
    """
    payload = decode_jwt(token)
    if not payload or "iss" not in payload:
        return None
    iss = payload["iss"]
    parts = iss.rstrip("/").split("/")
    if "realms" in parts:
        i = parts.index("realms")
        if i + 1 < len(parts):
            realm = parts[i + 1]
            # base 截到 "realms" 之前再 strip "/auth" 等 keycloak 标准路径
            base = "/".join(parts[:i]).rstrip("/")
            # 如果 base 以 "/auth" 或 "/auth/" 结尾, 去掉 (Keycloak 标准 layout)
            if base.endswith("/auth"):
                base = base[:-5]
            return base, realm
    return None
