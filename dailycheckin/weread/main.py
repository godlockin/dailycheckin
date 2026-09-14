"""微信读书 (weread.qq.com) 每日签到 + 体验金奖励

认证: cookie (wr_skey / wr_vid / wr_gid / wr_name)
  - 浏览器登录 weread.qq.com 后 DevTools -> Network -> 任意请求 -> 复制 Cookie
  - 或用 CDP 从 9333 Chrome 抓 (attach https://weread.qq.com)

API (基于公开):
  POST https://weread.qq.com/web/sign/add   每日签到 (返回 +体验金)
  GET  https://weread.qq.com/web/user/info  用户信息

配置 (config.json):
  "WEREAD": [
    {
      "name": "我的账号",
      "cookie": "wr_skey=xxx; wr_vid=xxx; ..."   // 可省, 走 CDP fallback
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

logger = logging.getLogger("dailycheckin.weread")

API_BASE = "https://weread.qq.com"
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://weread.qq.com",
    "Referer": "https://weread.qq.com/",
    "Accept": "application/json, text/plain, */*",
}


class Weread(CheckIn):
    name = "微信读书"

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
            attach_urls=["https://weread.qq.com"],
            open_url="https://weread.qq.com/",
            cookie_urls=["https://weread.qq.com/"],
        )
        if not cookie:
            logger.warning("CDP: weread cookie 抓取失败")
        return cookie

    def _api(self, s: requests.Session, path: str, method: str = "POST", payload: dict | None = None):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url)
        return s.post(url, data=json.dumps(payload or {}))

    def _parse(self, resp: requests.Response) -> tuple[bool, str, Any]:
        """返回 (成功?, 消息, body)"""
        try:
            d = resp.json()
        except Exception:
            return False, f"非 JSON 响应: HTTP {resp.status_code}, body[0:200]={resp.text[:200]}", None
        if not isinstance(d, dict):
            return False, f"响应非 dict: {d!r}", d
        # 微信读书常见字段: errcode / errmsg / succ
        succ = d.get("succ")
        errcode = d.get("errcode")
        errmsg = d.get("errmsg") or d.get("msg") or ""
        if succ is True or errcode == 0:
            return True, errmsg, d
        return False, f"errcode={errcode} errmsg={errmsg}", d

    def main(self) -> str:
        name = self.account.get("name") or "weread"
        rec: dict[str, Any] = {"name": name}

        if not self.cookie:
            rec.update(status="skipped", message="无 cookie 且 CDP fallback 也未抓到")
            return self._format([rec])

        s = self._session()

        # 1. 签到 (尝试多种 endpoint 兼容版本差异)
        for path in ["/web/sign/add", "/web/api/checkin", "/api/v2/sign"]:
            try:
                r = self._api(s, path, "POST", {})
                ok, msg, body = self._parse(r)
                if ok:
                    reward = ""
                    if isinstance(body, dict):
                        data = body.get("data") or {}
                        reward = (
                            data.get("rewardDesc")
                            or data.get("rewardName")
                            or data.get("reward")
                            or data.get("exp")
                            or ""
                        )
                    rec["checkin"] = "ok"
                    rec["reward"] = str(reward)[:60]
                    break
                # "已签到" 也算成功
                if "已签" in msg or "already" in msg.lower() or "已领取" in msg or "已签到" in msg:
                    rec["checkin"] = "already"
                    break
            except Exception as e:
                rec.setdefault("_err", []).append(f"{path}: {e}")
        else:
            # 全部 endpoint 都失败
            rec.update(status="login_failed", message=rec.pop("_err", ["未知错误"])[-1])
            return self._format([rec])

        # 2. 用户信息 (可选)
        try:
            r = self._api(s, "/web/user/info", "GET")
            _, _, body = self._parse(r)
            if isinstance(body, dict):
                data = body.get("data") or body
                rec["balance"] = data.get("balance") or data.get("coin") or data.get("experience")
                rec["vip_status"] = data.get("vipStatus") or data.get("isVip")
        except Exception as e:
            rec.setdefault("_info_err", str(e))

        rec.update(status="ok")
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「微信读书签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} 奖励:{r.get('reward', '-')} 余额:{r.get('balance', '-')}"
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
    print(Weread(cfg).main())
