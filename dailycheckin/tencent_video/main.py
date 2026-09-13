"""腾讯视频 (v.qq.com) 每日签到 + V 成长值

API:
  POST https://vip.video.qq.com/fv/handle?action=signin&pgv_info=...  每日签到
  GET  https://vip.video.qq.com/fv/handle?action=queryInfo              V 成长值查询

Cookie: vuser_id / vqq_vuserid / openid / access_token (登录后浏览器 DevTools)

配置 (config.json):
  "TENCENT_VIDEO": [
    { "name": "我的账号", "cookie": "vuser_id=xxx; vqq_vuserid=xxx; ..." }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.tencent_video")

API_BASE = "https://vip.video.qq.com"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://v.qq.com",
    "Referer": "https://v.qq.com/",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


class TencentVideo(CheckIn):
    name = "腾讯视频"

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
                # 试多个腾讯视频域名
                tab = None
                for host in ("https://v.qq.com", "https://vip.video.qq.com", "https://film.qq.com"):
                    tab = bridge.attach_by_url(host)
                    if tab:
                        break
                if not tab:
                    logger.warning("CDP: 未找到 v.qq.com tab, 请在 Chrome 登录")
                    return None
                cookies = bridge.send_cdp(
                    tab, "Network.getCookies",
                    {"urls": [
                        "https://v.qq.com/",
                        "https://vip.video.qq.com/",
                        "https://film.qq.com/",
                    ]},
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

    def _api(self, s: requests.Session, path: str, method: str = "GET", params: dict | None = None):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url, params=params or {})
        return s.post(url, params=params or {})

    def _parse(self, resp: requests.Response) -> tuple[bool, str, Any]:
        try:
            # 腾讯视频返回可能是 JSON 或 JSONP
            text = resp.text or ""
            if text.startswith("__jp"):
                # JSONP 格式: __jp({...})
                text = text[text.index("(") + 1: text.rindex(")")]
            d = json.loads(text)
        except Exception:
            return False, f"非 JSON: HTTP {resp.status_code}, body[0:200]={resp.text[:200]}", None
        if not isinstance(d, dict):
            return False, f"响应非 dict: {d!r}", d
        # 腾讯视频常见: ret / retMsg / errMsg / msg
        ret = d.get("ret") if "ret" in d else None
        if ret is None:
            ret = d.get("code") or d.get("status")
        retmsg = d.get("retMsg") or d.get("msg") or d.get("errMsg") or ""
        # ret=0 / 200 / 200.0 通常表示成功
        if ret in (0, 200, "0", "200"):
            return True, str(retmsg), d
        # 已签到通常 retMsg 含"已"
        if "已" in str(retmsg) or "已签" in str(retmsg) or "已领取" in str(retmsg):
            return True, str(retmsg), d
        # ret=100005 等可能是登录过期
        if "login" in str(retmsg).lower() or "登录" in str(retmsg) or ret in (100005, 100006):
            return False, "login_required: " + str(retmsg), d
        return False, f"ret={ret} retMsg={retmsg}", d

    def main(self) -> str:
        name = self.account.get("name") or "tencent_video"
        rec: dict[str, Any] = {"name": name}

        if not self.cookie:
            rec["status"] = "skipped"
            rec["message"] = "无 cookie 且 CDP fallback 也未抓到"
            return self._format([rec])

        s = self._session()

        # 1. 签到
        try:
            r = self._api(s, "/fv/handle", "GET", {"action": "signin"})
            ok, msg, body = self._parse(r)
            if ok:
                rec["checkin"] = "ok"
                if isinstance(body, dict):
                    rec["reward"] = str(body.get("score") or body.get("growth") or "")[:50]
            else:
                if "login" in msg:
                    rec["status"] = "login_failed"
                    rec["message"] = "签到: cookie 无效, 需重新抓"
                    return self._format([rec])
                if "已" in msg:
                    rec["checkin"] = "already"
                else:
                    rec["status"] = "login_failed"
                    rec["message"] = "签到: " + msg
                    return self._format([rec])
        except Exception as e:
            rec["status"] = "error"
            rec["message"] = "签到异常: " + str(e)
            return self._format([rec])

        # 2. 查询 V 成长值
        try:
            r = self._api(s, "/fv/handle", "GET", {"action": "queryInfo"})
            _, _, body = self._parse(r)
            if isinstance(body, dict):
                rec["v_score"] = str(body.get("score") or body.get("growth") or body.get("vipScore") or "")[:30]
        except Exception:
            pass

        rec["status"] = "ok"
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = "「腾讯视频签到」\n"
        body += "总数 " + str(len(results)) + " | 成功 " + str(ok) + " | 失败 " + str(failed) + " | 跳过 " + str(skipped) + "\n"
        for r in results:
            body += r.get("status", "?") + " " * (14 - len(r.get("status", "?"))) + " "
            body += r.get("name", "?") + " " * (14 - len(r.get("name", "?"))) + " "
            body += "签到:" + str(r.get("checkin", "-")) + " V值:" + str(r.get("v_score", "-")) + "\n"
        return body


if __name__ == "__main__":
    import sys

    cfg = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read() or "{}"
        cfg = json.loads(raw)
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(TencentVideo(cfg).main())
