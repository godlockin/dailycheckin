"""AcWing (acwing.com) 每日打卡 + 积分

API:
  POST https://www.acwing.com/api/activity/check_in/   每日打卡
  GET  https://www.acwing.com/api/user/                用户信息

Cookie: sessionid (登录后浏览器 DevTools -> Network 任意请求 -> Cookie header)

配置 (config.json):
  "ACWING": [
    { "name": "我的账号", "cookie": "sessionid=xxx; ..." }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.acwing")

API_BASE = "https://www.acwing.com"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.acwing.com",
    "Referer": "https://www.acwing.com/",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


class Acwing(CheckIn):
    name = "AcWing"

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
        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge
            bridge = CDPBridge.start(port=self.cdp_port)
            try:
                tab = bridge.attach_by_url("https://www.acwing.com")
                if not tab:
                    logger.warning("CDP: 未找到 acwing.com tab, 请在 Chrome 登录")
                    return None
                cookies = bridge.send_cdp(
                    tab, "Network.getCookies",
                    {"urls": ["https://www.acwing.com/"]},
                )
                parts = []
                for c in (cookies or {}).get("cookies", []):
                    parts.append(f"{c['name']}={c['value']}")
                cookie_str = "; ".join(parts)
                return cookie_str or None
            finally:
                try:
                    bridge.quit()
                except Exception:
                    pass
        except Exception as e:
            logger.warning("CDP cookie 抓取失败: %s", e)
            return None

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
        errcode = d.get("error_code") or d.get("errno") or d.get("code")
        errmsg = d.get("error_message") or d.get("msg") or d.get("message") or ""
        # AcWing 0 表示成功
        if errcode in (0, None):
            return True, str(errmsg), d
        # "已签到" / "已打卡" 也算成功
        if "已" in str(errmsg) or "已打卡" in str(errmsg) or "已经" in str(errmsg):
            return True, str(errmsg), d
        return False, f"errcode={errcode} errmsg={errmsg}", d

    def main(self) -> str:
        name = self.account.get("name") or "acwing"
        rec: dict[str, Any] = {"name": name}

        if not self.cookie:
            rec["status"] = "skipped"
            rec["message"] = "无 cookie 且 CDP fallback 也未抓到"
            return self._format([rec])

        s = self._session()

        # 1. 打卡
        try:
            r = self._api(s, "/api/activity/check_in/", "POST", {})
            ok, msg, body = self._parse(r)
            if ok:
                rec["checkin"] = "ok"
                if isinstance(body, dict):
                    rec["reward"] = str(body.get("reward") or body.get("point") or body.get("data") or "")[:60]
            else:
                rec["status"] = "login_failed"
                rec["message"] = "打卡失败: " + msg
                return self._format([rec])
        except Exception as e:
            rec["status"] = "error"
            rec["message"] = "打卡异常: " + str(e)
            return self._format([rec])

        rec["status"] = "ok"
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = "「AcWing 打卡」\n"
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
    print(Acwing(cfg).main())
