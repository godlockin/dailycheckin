"""pkulaw 北大法宝每日签到 (Bearer Token, 自动 OAuth refresh)

Token 由 Keycloak (cas.pkulaw.com/auth/realms/fabao) 颁发。
提供 refresh_token 后, 模块会在以下时机自动续期:
  1. 调用前检查 exp; 即将过期 (60s 内) 时主动 refresh
  2. 任何请求返回 401 时, 自动 refresh 一次再 retry

新 token 原子写回 config.json, 不需要人工干预。

配置 (config.json):
  "PKULAW": [
    {
      "name": "我的账号",
      "token": "eyJhbGc...",
      "refresh_token": "eyJhbGc...",
      "client_id": "wso2"
    }
  ]

要拿 refresh_token: 浏览器登录 pkulaw, DevTools -> Application ->
Cookies/LocalStorage 找 keycloak session 里的 refresh_token.
也可以用 `python -m dailycheckin.pkulaw.login_helper` (CDP 自动捕获).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests
from dailycheckin import CheckIn

from dailycheckin.pkulaw.auth import (
    RefreshError,
    decode_jwt,
    find_config_path,
    is_token_expired,
    parse_iss_for_realm,
    persist_new_tokens,
    refresh_tokens,
)

logger = logging.getLogger("dailycheckin.pkulaw")

API_BASE = "https://gateway.pkulaw.com"
CLAIM_URL = f"{API_BASE}/api-portal/rewards/daily/claim"
PROFILE_URL = f"{API_BASE}/api-portal/profile"

DEFAULT_HEADERS_BASE = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh-TW;q=0.7,zh;q=0.6",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "DNT": "1",
    "Origin": "https://mcp.pkulaw.com",
    "Pragma": "no-cache",
    "Referer": "https://mcp.pkulaw.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

ALREADY_PAT = re.compile(r"已签|已领取|今日已|already|重复|完成", re.I)
SUCCESS_PAT = re.compile(r"成功|签到|领取|获得|success|ok", re.I)
TOKEN_EXPIRED_PAT = re.compile(r"token|unauthor|expired|invalid_grant|401")


class PkulawCheckIn(CheckIn):
    name = "Pkulaw 北大法宝"

    def __init__(self, check_item: dict[str, Any]):
        self.check_item = check_item or []
        # 复用 dailycheckin 约定: config.json 找
        self.config_path = find_config_path()

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        s.timeout = 30
        return s

    # ------------------------------------------------------------------ refresh

    def _try_refresh(self, account: dict[str, Any]) -> dict[str, Any] | None:
        """调 Keycloak refresh_token grant. 成功返回新 token dict, 失败返回 None.

        不抛, 失败时让上层记 login_failed.
        """
        refresh_token = (account.get("refresh_token") or "").strip()
        if not refresh_token:
            return None
        # 从 token 拿 realm
        realm_info = parse_iss_for_realm(account.get("token", ""))
        base, realm = (realm_info if realm_info else (None, None))
        if not base or not realm:
            base, realm = "https://cas.pkulaw.com", "fabao"
        try:
            new_tokens = refresh_tokens(
                refresh_token,
                realm=realm,
                base=base,
                client_id=account.get("client_id") or None,
                client_secret=account.get("client_secret") or None,
            )
            return new_tokens
        except RefreshError as e:
            logger.warning("refresh_token 失败: %s", e)
            return None
        except Exception as e:
            logger.warning("refresh_token 异常: %s", e)
            return None

    def _persist(self, idx: int, account: dict[str, Any], new_access: str, new_refresh: str) -> None:
        if not self.config_path:
            logger.info("未找到 config.json, 新 token 仅本次生效 (重启后丢失)")
            return
        if persist_new_tokens(self.config_path, idx, new_access, new_refresh):
            account["token"] = new_access
            if new_refresh:
                account["refresh_token"] = new_refresh
            print(f"  [refresh] 已写回新 token 到 {self.config_path}", flush=True)
        else:
            print(f"  [refresh] 写回 {self.config_path} 失败", flush=True)

    # ------------------------------------------------------------------ verify / claim

    def _verify(self, token: str) -> tuple[bool, str]:
        s = self._session()
        s.headers["Authorization"] = f"Bearer {token}"
        try:
            r = s.get(PROFILE_URL)
        except Exception as e:
            return False, f"profile 异常: {e}"
        if r.status_code == 401:
            return False, "token 过期或无效 (HTTP 401)"
        if r.status_code != 200:
            return False, f"profile HTTP {r.status_code}"
        try:
            data = r.json()
        except Exception:
            return True, "token 有效 (无法解析 profile)"
        data_field = data.get("data") if isinstance(data, dict) else None
        name = ""
        if isinstance(data_field, dict):
            name = data_field.get("preferred_username") or ""
        if not name and isinstance(data, dict):
            name = data.get("preferred_username") or ""
        return True, name

    def _claim(self, token: str) -> requests.Response:
        s = self._session()
        s.headers["Authorization"] = f"Bearer {token}"
        return s.post(CLAIM_URL, data=json.dumps({}))

    # ------------------------------------------------------------------ main

    def main(self) -> str:
        results: list[dict[str, Any]] = []
        for idx, account in enumerate(self.check_item or []):
            token = (account.get("token") or "").strip()
            name = (account.get("name") or f"账号{idx + 1}").strip()
            rec: dict[str, Any] = {"name": name}

            if not token:
                rec.update(status="skipped", message="token 为空, 跳过")
                results.append(rec)
                continue

            # 1. 主动续期: token 即将过期
            if is_token_expired(token, skew_sec=60):
                refreshed = self._try_refresh(account)
                if refreshed:
                    token = refreshed["access_token"]
                    self._persist(idx, account, token, refreshed.get("refresh_token", ""))
                    print(f"  [refresh] {name} 主动续期成功", flush=True)
                else:
                    rec.update(status="login_failed", message="token 过期且 refresh_token 失败")
                    results.append(rec)
                    continue

            # 2. 验证 token
            ok, info = self._verify(token)
            if not ok:
                # 401 触发 fallback refresh
                if "401" in info or "过期" in info:
                    refreshed = self._try_refresh(account)
                    if refreshed:
                        token = refreshed["access_token"]
                        self._persist(idx, account, token, refreshed.get("refresh_token", ""))
                        ok, info = self._verify(token)
                if not ok:
                    rec.update(status="login_failed", message=info)
                    results.append(rec)
                    continue

            if info and not (account.get("name")):
                rec["name"] = info
                name = info

            # 3. POST claim
            try:
                resp = self._claim(token)
            except Exception as e:
                rec.update(status="error", message=f"请求异常: {e}")
                results.append(rec)
                continue

            # 4. 401 → 一次 refresh + retry
            if resp.status_code == 401:
                refreshed = self._try_refresh(account)
                if refreshed:
                    token = refreshed["access_token"]
                    self._persist(idx, account, token, refreshed.get("refresh_token", ""))
                    try:
                        resp = self._claim(token)
                    except Exception as e:
                        rec.update(status="error", message=f"refresh 后请求异常: {e}")
                        results.append(rec)
                        continue
                else:
                    rec.update(status="login_failed", message="401 且 refresh_token 失败")
                    results.append(rec)
                    continue

            text = (resp.text or "").strip()
            if resp.status_code != 200 and not TOKEN_EXPIRED_PAT.search(text):
                rec.update(status="failed", message=f"HTTP {resp.status_code}: {text[:120]}")
                results.append(rec)
                continue
            if resp.status_code != 200:
                rec.update(status="login_failed", message=f"token 过期 (HTTP {resp.status_code})")
                results.append(rec)
                continue

            data: Any = None
            try:
                data = resp.json()
            except Exception:
                pass

            msg = ""
            success_flag = False
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("msg") or "")
                success_flag = bool(data.get("success", False))
                if isinstance(data.get("data"), dict):
                    inner_msg = str(data["data"].get("message") or data["data"].get("msg") or "")
                    if inner_msg and not msg:
                        msg = inner_msg
            if not msg:
                msg = text[:120]

            if ALREADY_PAT.search(msg):
                already_flag = True
            elif SUCCESS_PAT.search(msg) or success_flag:
                already_flag = False
            else:
                already_flag = False
            rec["message"] = msg[:150]
            rec["status"] = "already" if already_flag else ("ok" if success_flag or SUCCESS_PAT.search(msg) else "failed")
            results.append(rec)
            time.sleep(1)

        ok = sum(1 for r in results if r.get("status") == "ok")
        already = sum(1 for r in results if r.get("status") == "already")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「Pkulaw 北大法宝签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 已签 {already} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<12} {r.get('name', '?'):<20} {(r.get('message') or '')[:60]}"
            for r in results
        )
        return body


if __name__ == "__main__":
    import sys

    cfg = []
    if not sys.stdin.isatty():
        cfg = json.loads(sys.stdin.read() or "[]")
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(PkulawCheckIn(cfg).main())