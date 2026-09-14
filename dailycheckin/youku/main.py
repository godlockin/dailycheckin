"""优酷 (youku.com) 每日签到 + 会员积分

API:
  POST https://vip.youku.com/api/ajax/signIn?utdid=...  每日签到
  GET  https://vip.youku.com/api/ajax/user/getUserInfo  用户信息

Cookie: _m_h5_tk / login_uk / youku_login_token (登录后浏览器 DevTools)

配置 (config.json):
  "YOUKU": [
    { "name": "我的账号", "cookie": "_m_h5_tk=xxx; login_uk=xxx; ..." }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.youku")

API_BASE = "https://vip.youku.com"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://vip.youku.com",
    "Referer": "https://vip.youku.com/",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


class Youku(CheckIn):
    name = "优酷"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        self.cdp_port = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))
        self.cookie = (self.account.get("cookie") or "").strip()
        if not self.cookie:
            self.cookie = self._cdp_cookie() or ""

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        if self.cookie:
            s.headers["Cookie"] = self.cookie
        s.timeout = 30
        return s

    def _cdp_cookie(self) -> str | None:
        from dailycheckin.utils.cdp_bridge import fetch_cookies
        cookie = fetch_cookies(
            port=self.cdp_port,
            attach_urls=["https://vip.youku.com", "https://www.youku.com"],
            open_url="https://www.youku.com/",
            cookie_urls=["https://vip.youku.com/", "https://www.youku.com/", "https://api.youku.com/"],
        )
        if not cookie:
            logger.warning("CDP: youku cookie 抓取失败")
        return cookie

    def _api(self, s: requests.Session, path: str, method: str = "POST", data: dict | None = None):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url)
        return s.post(url, data=data or {})

    def _parse(self, resp: requests.Response) -> tuple[bool, str, Any]:
        try:
            d = resp.json()
        except Exception:
            return False, f"非 JSON: HTTP {resp.status_code}, body[0:200]={resp.text[:200]}", None
        if not isinstance(d, dict):
            return False, f"响应非 dict: {d!r}", d
        # 优酷常见: status / code / retcode
        code = d.get("status") or d.get("code") or d.get("retcode")
        msg = d.get("message") or d.get("msg") or d.get("errorMsg") or ""
        # 成功状态: status==200 or code==200
        if code in (200, "200", 0, "0") or d.get("success") is True:
            return True, str(msg), d
        # 已签到通常 msg 含"已"
        if "已" in str(msg) or "already" in str(msg).lower():
            return True, str(msg), d
        return False, f"code={code} msg={msg}", d

    def main(self) -> str:
        name = self.account.get("name") or "youku"
        rec: dict[str, Any] = {"name": name}

        if not self.cookie:
            rec["status"] = "skipped"
            rec["message"] = "无 cookie 且 CDP fallback 也未抓到"
            return self._format([rec])

        s = self._session()

        # 1. 签到
        try:
            r = self._api(s, "/api/ajax/signIn", "POST", {})
            ok, msg, body = self._parse(r)
            if ok:
                rec["checkin"] = "ok"
                if isinstance(body, dict):
                    data = body.get("data") or body
                    if isinstance(data, dict):
                        rec["reward"] = str(data.get("reward") or data.get("points") or data.get("score") or "")[:50]
            else:
                # 检查是否已签到
                if "已" in msg:
                    rec["checkin"] = "already"
                else:
                    rec["status"] = "login_failed"
                    rec["message"] = "签到失败: " + msg
                    return self._format([rec])
        except Exception as e:
            rec["status"] = "error"
            rec["message"] = "签到异常: " + str(e)
            return self._format([rec])

        rec["status"] = "ok"
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = "「优酷签到」\n"
        body += "总数 " + str(len(results)) + " | 成功 " + str(ok) + " | 失败 " + str(failed) + " | 跳过 " + str(skipped) + "\n"
        for r in results:
            body += r.get("status", "?") + " " * (14 - len(r.get("status", "?"))) + " "
            body += r.get("name", "?") + " " * (14 - len(r.get("name", "?"))) + " "
            body += "签到:" + str(r.get("checkin", "-")) + " 奖励:" + str(r.get("reward", "-")) + "\n"
        return body


if __name__ == "__main__":
    import sys

    cfg = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read() or "{}"
        cfg = json.loads(raw)
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(Youku(cfg).main())
