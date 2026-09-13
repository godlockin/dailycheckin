"""网易云音乐 (music.163.com) 每日签到 + 体验金奖励

认证: cookie (MUSIC_U / __csrf / os / appver / ...)
  - 浏览器登录 music.163.com 后 DevTools -> Network -> 任意请求 -> 复制 Cookie
  - 或用 CDP 从 9333 Chrome 抓 (attach https://music.163.com)

API:
  POST https://music.163.com/api/point/dailyTask   每日签到 (返回 +云贝)
  GET  https://music.163.com/api/v1/user/info     用户信息

配置 (config.json):
  "NETEASE": [
    {
      "name": "我的账号",
      "cookie": "MUSIC_U=xxx; __csrf=xxx; ..."   // 可省, 走 CDP fallback
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

logger = logging.getLogger("dailycheckin.netease")

API_BASE = "https://music.163.com"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://music.163.com",
    "Referer": "https://music.163.com/",
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded",
}


class Netease(CheckIn):
    name = "网易云音乐"

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
        """从 9333 Chrome 已登录的 music.163.com 抓 Cookie header."""
        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge
        except Exception as e:
            logger.warning("CDP import 失败: %s", e)
            return None
        try:
            bridge = CDPBridge.start(port=self.cdp_port)
        except Exception as e:
            logger.warning("CDP start 失败 (port=%d): %s", self.cdp_port, e)
            return None
        try:
            # 试多个网易云域名 (music.163.com / interface.music.163.com)
            tab = None
            for host in ("https://music.163.com", "https://interface.music.163.com"):
                tab = bridge.attach_by_url(host)
                if tab:
                    break
            if not tab:
                logger.warning("CDP: 未找到 music.163.com tab, 请在 Chrome 登录")
                return None
            cookies = bridge.send_cdp(
                tab, "Network.getCookies",
                {"urls": ["https://music.163.com/", "https://interface.music.163.com/"]},
            )
            parts = []
            for c in (cookies or {}).get("cookies", []):
                parts.append(f"{c['name']}={c['value']}")
            cookie_str = "; ".join(parts)
            return cookie_str or None
        except Exception as e:
            logger.warning("CDP cookie 抓取失败: %s", e)
            return None
        finally:
            try:
                bridge.quit()
            except Exception:
                pass

    def _api(self, s: requests.Session, path: str, method: str = "POST", data: dict | None = None):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url)
        return s.post(url, data=data or {})

    def _parse(self, resp: requests.Response) -> tuple[bool, str, Any]:
        try:
            d = resp.json()
        except Exception:
            return False, f"非 JSON 响应: HTTP {resp.status_code}, body[0:200]={resp.text[:200]}", None
        if not isinstance(d, dict):
            return False, f"响应非 dict: {d!r}", d
        code = d.get("code")
        msg = d.get("msg") or ""
        # 网易云常见 code: 200 (成功), -460 (登录过期), 301 (未登录)
        if code == 200:
            return True, msg, d
        # 已签到通常 code= -2 / "已签到" 等
        if isinstance(msg, str) and ("已" in msg or "签到" in msg):
            return True, msg, d
        return False, f"code={code} msg={msg}", d

    def main(self) -> str:
        name = self.account.get("name") or "netease"
        rec: dict[str, Any] = {"name": name}

        if not self.cookie:
            rec.update(status="skipped", message="无 cookie 且 CDP fallback 也未抓到")
            return self._format([rec])

        s = self._session()

        # 1. 每日签到
        try:
            r = self._api(s, "/api/point/dailyTask", "POST", {"type": "1"})
            ok, msg, body = self._parse(r)
            if ok:
                rec["checkin"] = "ok"
                if isinstance(body, dict):
                    rec["reward"] = str(body.get("point") or body.get("reward") or "")[:50]
            elif "301" in msg or "登录" in msg or "未登录" in msg:
                rec.update(status="login_failed", message=f"签到: {msg}")
                return self._format([rec])
            else:
                # 可能是已签到 (code != 200 但 msg 含已签)
                if "已" in msg or "重复" in msg:
                    rec["checkin"] = "already"
                else:
                    rec.update(status="login_failed", message=f"签到: {msg}")
                    return self._format([rec])
        except Exception as e:
            rec.update(status="error", message=f"签到异常: {e}")
            return self._format([rec])

        # 2. 用户信息 (可选)
        try:
            r = self._api(s, "/api/v1/user/info", "GET")
            _, _, body = self._parse(r)
            if isinstance(body, dict) and isinstance(body.get("profile"), dict):
                p = body["profile"]
                rec["user_id"] = p.get("userId") or p.get("id")
                rec["nickname"] = p.get("nickname")
        except Exception as e:
            rec.setdefault("_info_err", str(e))

        rec.update(status="ok")
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「网易云音乐签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} 奖励:{r.get('reward', '-')} "
            f"用户:{r.get('nickname', '-')}"
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
    print(Netease(cfg).main())
