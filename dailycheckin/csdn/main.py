"""CSDN (csdn.net) 每日签到。

认证: cookie (UserName / UserInfo / dc_session_id)
  - 浏览器登录 csdn.net, DevTools -> Network -> 任意请求 -> 复制 Cookie header
  - 或用 CDP 从 9333 Chrome 抓 (attach https://csdn.net)

API:
  POST https://me.csdn.net/api/LuckyDraw_v2/sign_in   每日签到 (返回 +C 币)
  GET  https://me.csdn.net/api/LuckyDraw_v2/status    签到状态

配置 (config.json):
  "CSDN": [
    {
      "name": "我的账号",
      "cookie": "UserName=xxx; UserInfo=xxx; dc_session_id=xxx"   // 可省, 走 CDP fallback
    }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.csdn")

API_BASE = "https://me.csdn.net"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://csdn.net",
    "Referer": "https://csdn.net/",
    "Accept": "application/json, text/plain, */*",
}


class Csdn(CheckIn):
    name = "CSDN"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        self.cookie = (self.account.get("cookie") or "").strip()
        self.cdp_port = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))

    def _session(self, cookie: str) -> requests.Session:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        if cookie:
            s.headers["Cookie"] = cookie
        s.timeout = 30
        return s

    def _cdp_cookie(self) -> str | None:
        from dailycheckin.utils.cdp_bridge import fetch_cookies
        hosts = ["https://csdn.net", "https://blog.csdn.net", "https://me.csdn.net", "https://i.csdn.net"]
        cookie = fetch_cookies(
            port=self.cdp_port,
            attach_urls=hosts,
            open_url="https://www.csdn.net/",
            cookie_urls=[
                "https://csdn.net/", "https://blog.csdn.net/", "https://me.csdn.net/",
                "https://i.csdn.net/", "https://passport.csdn.net/",
            ],
        )
        if not cookie:
            logger.warning("CDP: csdn cookie 抓取失败")
        return cookie

    def _api(self, s: requests.Session, path: str, method: str = "GET"):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url)
        return s.post(url, data=json.dumps({}))

    def _parse(self, resp: requests.Response) -> tuple[int, str, Any]:
        try:
            d = resp.json()
        except Exception:
            return resp.status_code, (resp.text or "")[:200], None
        code = d.get("code") if isinstance(d, dict) else None
        msg = d.get("message") or d.get("msg") or "" if isinstance(d, dict) else ""
        return code if isinstance(code, int) else resp.status_code, str(msg), d

    def main(self) -> str:
        name = self.account.get("name") or "csdn"
        rec: dict[str, Any] = {"name": name}

        cookie = self.cookie
        if not cookie:
            cookie = self._cdp_cookie() or ""
        if not cookie:
            rec.update(status="skipped", message="无 cookie 且 CDP fallback 也未抓到")
            return self._format([rec])

        s = self._session(cookie)

        # 1. 签到
        try:
            r = self._api(s, "/api/LuckyDraw_v2/sign_in", "POST")
            code, msg, d = self._parse(r)
            # 401 / 403 / -1 等 -> 重新 CDP 抓
            if code in (-1, 401, 403) or (isinstance(d, dict) and d.get("code") == -1):
                ck = self._cdp_cookie()
                if ck:
                    s.headers["Cookie"] = ck
                    r = self._api(s, "/api/LuckyDraw_v2/sign_in", "POST")
                    code, msg, d = self._parse(r)
            # "已签到" 通常 code=0 + 含 "已" 字样
            if code == 0:
                rec["checkin"] = "ok"
                # 提取奖励
                if isinstance(d, dict):
                    data = d.get("data") or {}
                    if isinstance(data, dict):
                        amount = data.get("amount") or data.get("prizeDesc")
                        if amount:
                            rec["reward"] = str(amount)[:60]
            else:
                # 失败, 但可能是已签到
                if "已" in msg or "already" in msg.lower() or "重复" in msg:
                    rec["checkin"] = "already"
                else:
                    rec.update(status="login_failed", message=f"签到失败: {msg or f'HTTP {r.status_code}'}")
                    return self._format([rec])
        except Exception as e:
            rec.update(status="error", message=f"签到异常: {e}")
            return self._format([rec])

        # 2. 状态查询 (可选, 让输出更丰富)
        try:
            r = self._api(s, "/api/LuckyDraw_v2/status", "GET")
            _, _, d = self._parse(r)
            if isinstance(d, dict) and isinstance(d.get("data"), dict):
                st = d["data"]
                rec["status_total"] = st.get("total")
                rec["status_signed"] = st.get("signed")
        except Exception as e:
            rec["status"] = f"异常: {e}"

        rec.update(status="ok")
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「CSDN 签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} 奖励:{r.get('reward', '-')} "
            f"状态:{r.get('status_total', '-')}/{r.get('status_signed', '-')}"
            for r in results
        )
        return body


if __name__ == "__main__":
    import sys

    cfg = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read() or "{}"
        cfg = json.loads(raw)
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(Csdn(cfg).main())
