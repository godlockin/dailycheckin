"""pkulaw 北大法宝每日签到 (Bearer Token)

简单的 HTTP-only 签到: POST /api-portal/rewards/daily/claim + Bearer JWT.
Token 由 keycloak 颁发, 1 天左右有效, 需用户在浏览器登录后从 DevTools 拷贝过来
(参考 README)。

使用:
  config.json 里加 "PKULAW": [{"token": "<JWT>", "name": "<昵称>"}]
  跑 dailycheckin --include PKULAW
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests
from dailycheckin import CheckIn

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

# 常见提示
ALREADY_PAT = re.compile(r"已签|已领取|今日已|already|重复|完成", re.I)
SUCCESS_PAT = re.compile(r"成功|签到|领取|获得|success|ok", re.I)
TOKEN_EXPIRED_PAT = re.compile(r"token|unauthor|expired|invalid_grant|401")


class PkulawCheckIn(CheckIn):
    name = "Pkulaw 北大法宝"

    def __init__(self, check_item: dict[str, Any]):
        self.check_item = check_item or {}

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        s.timeout = 30
        return s

    def _verify(self, token: str) -> tuple[bool, str]:
        """可选: 调 /profile 验证 token 是否过期, 顺便拿昵称"""
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
        # 找昵称
        data_field = data.get("data") if isinstance(data, dict) else None
        name = ""
        if isinstance(data_field, dict):
            name = data_field.get("preferred_username") or ""
        if not name and isinstance(data, dict):
            name = data.get("preferred_username") or ""
        return True, name

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
            # 先验 token (token 过期直接报错, 不调 claim)
            ok, info = self._verify(token)
            if not ok:
                rec.update(status="login_failed", message=info)
                results.append(rec)
                continue
            if info and not name:
                rec["name"] = info
                name = info

            # POST 签到
            s = self._session()
            s.headers["Authorization"] = f"Bearer {token}"
            try:
                resp = s.post(CLAIM_URL, data=json.dumps({}))
            except Exception as e:
                rec.update(status="error", message=f"请求异常: {e}")
                results.append(rec)
                continue

            text = (resp.text or "").strip()
            if resp.status_code == 401 or TOKEN_EXPIRED_PAT.search(text):
                rec.update(status="login_failed", message=f"token 过期 (HTTP {resp.status_code})")
                results.append(rec)
                continue
            if resp.status_code != 200:
                rec.update(status="failed", message=f"HTTP {resp.status_code}: {text[:120]}")
                results.append(rec)
                continue

            # 尝试解析 JSON
            data: Any = None
            try:
                data = resp.json()
            except Exception:
                pass

            msg = ""
            success_flag = False
            already_flag = False
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
            rec["message"] = msg[:150]
            rec["status"] = "already" if already_flag else ("ok" if success_flag or SUCCESS_PAT.search(msg) else "failed")
            results.append(rec)
            time.sleep(1)  # 礼貌节流

        # 汇总
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