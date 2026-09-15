"""掘金 (juejin.cn) 每日签到 + 免费抽奖。

签名策略: **页面上下文 fetch (CDP) 优先** — 掘金 API 对非浏览器请求有风控,
requests 直接调会返回空 body; 在已登录 tab 内 eval fetch 天然带完整环境。

认证: Chrome profile 登录态 (tab 不在则自动新开), 或 config cookie (fallback)。

API:
  POST /growth_api/v1/check_in       签到
  POST /growth_api/v1/lottery/draw   免费抽奖 (每日 1 次)
  GET  /growth_api/v1/get_cur_point  当前积分

配置 (config.json):
  "JUEJIN": [
    { "name": "我的账号" }        // 推荐: 直接用 Chrome 登录态
    // 或 { "name": "x", "cookie": "sessionid=..." }  (fallback HTTP 模式)
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.juejin")

API_BASE = "https://api.juejin.cn"
JUEJIN_QS = "?aid=2608&spider=0"  # 掘金 web 端点必需 query
DEFAULT_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "Origin": "https://juejin.cn",
    "Referer": "https://juejin.cn/",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}


class Juejin(CheckIn):
    name = "掘金"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        self.cookie = (self.account.get("cookie") or "").strip()
        self.cdp_port = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))

    # ------------------------------------------------------------ CDP 路径 (首选)

    def _cdp_fetch(self, bridge, tab: str, path: str, method: str = "GET", payload: dict | None = None) -> dict | None:
        """页面上下文 fetch 掘金 API. 返回 {status, body(dict) | raw(str)} 或 None."""
        body_js = json.dumps(json.dumps(payload or {})) if method == "POST" else "undefined"
        js = (
            "(async () => {"
            f"  const r = await fetch('{API_BASE}{path}{JUEJIN_QS}', {{"
            f"    method: '{method}', credentials: 'include',"
            "    headers: {'Content-Type': 'application/json'},"
            f"    body: {body_js}"
            "  });"
            "  return JSON.stringify({status: r.status, text: await r.text()});"
            "})()"
        )
        try:
            raw = bridge.eval(tab, js, await_promise=True)
            if not raw:
                return None
            out = json.loads(raw) if isinstance(raw, str) else raw
            try:
                out["body"] = json.loads(out.get("text") or "null")
            except Exception:
                out["body"] = None
            return out
        except Exception as e:
            logger.warning("cdp_fetch %s 失败: %s", path, e)
            return None

    def _sign_via_cdp(self, rec: dict[str, Any]) -> bool:
        """浏览器内完成 签到+抽奖+积分. 返回 True=已处理 (rec 已填充, 含失败)."""
        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError
            bridge = CDPBridge.start(port=self.cdp_port)
        except Exception as e:
            logger.warning("CDP start 失败: %s", e)
            return False
        opened_tab = None
        try:
            tab = bridge.attach_by_url("https://juejin.cn")
            if not tab:
                opened_tab = bridge.create_tab("https://juejin.cn/")
                tab = opened_tab
                bridge.wait(4000)

            # 1. 签到
            r = self._cdp_fetch(bridge, tab, "/growth_api/v1/check_in", "POST")
            d = (r or {}).get("body") or {}
            err_no = d.get("err_no")
            if err_no == 0:
                rec["checkin"] = "ok"
            elif err_no in (-1, 15001) or "签" in str(d.get("err_msg", "")):
                # 已签到 (掘金重复签到 err_no=-1/15001)
                rec["checkin"] = "already"
            else:
                rec["status"] = "login_failed"
                rec["message"] = f"check_in err_no={err_no}: {str(d.get('err_msg'))[:80]}"
                return True

            # 2. 免费抽奖 (失败不算签到失败)
            r = self._cdp_fetch(bridge, tab, "/growth_api/v1/lottery/draw", "POST")
            d = (r or {}).get("body") or {}
            if d.get("err_no") == 0:
                rec["lottery"] = ((d.get("data") or {}).get("lottery_name")) or "ok"
            else:
                rec["lottery"] = str(d.get("err_msg") or "已抽/无机会")[:30]

            # 3. 积分
            r = self._cdp_fetch(bridge, tab, "/growth_api/v1/get_cur_point")
            d = (r or {}).get("body") or {}
            if d.get("err_no") == 0:
                rec["points"] = d.get("data")
            else:
                rec["points"] = str(d.get("err_msg") or "?")[:30]

            rec["status"] = "ok"
            return True
        except Exception as e:
            logger.warning("CDP 签到异常: %s", e)
            return False
        finally:
            if opened_tab:
                try:
                    bridge.close_tab(opened_tab)
                except Exception:
                    pass
            try:
                bridge.quit()
            except Exception:
                pass

    # ------------------------------------------------------------ HTTP fallback

    def _cdp_cookie(self) -> str | None:
        from dailycheckin.utils.cdp_bridge import fetch_cookies
        return fetch_cookies(
            port=self.cdp_port,
            attach_urls=["https://juejin.cn"],
            open_url="https://juejin.cn/",
            cookie_urls=["https://juejin.cn/", "https://api.juejin.cn/"],
        )

    def _sign_via_http(self, rec: dict[str, Any]) -> bool:
        """requests + cookie 走 HTTP API (掘金有风控, 可能空 body — 仅作 fallback)."""
        cookie = self.cookie or self._cdp_cookie() or ""
        if not cookie:
            rec["status"] = "skipped"
            rec["message"] = "无 cookie 且 CDP 不可用"
            return True
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        s.headers["Cookie"] = cookie
        s.timeout = 30
        try:
            r = s.post(f"{API_BASE}/growth_api/v1/check_in{JUEJIN_QS}", data=json.dumps({}))
            d = r.json() if (r.text or "").strip().startswith("{") else {}
            if d.get("err_no") == 0:
                rec["checkin"] = "ok"
            elif "签" in str(d.get("err_msg", "")):
                rec["checkin"] = "already"
            else:
                rec["status"] = "login_failed"
                rec["message"] = f"HTTP check_in 空/异常响应 (可能风控): HTTP {r.status_code}"
                return True
            rec["status"] = "ok"
            return True
        except Exception as e:
            rec["status"] = "error"
            rec["message"] = f"HTTP 签到异常: {e}"
            return True

    # ------------------------------------------------------------ main

    def main(self) -> str:
        name = self.account.get("name") or "juejin"
        rec: dict[str, Any] = {"name": name}

        # 1. CDP 页面内签到 (首选, 过风控)
        handled = self._sign_via_cdp(rec)
        # 2. fallback: HTTP + cookie
        if not handled:
            handled = self._sign_via_http(rec)
        if not handled:
            rec["status"] = "skipped"
            rec["message"] = "CDP 与 HTTP 路径均不可用"
        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「掘金签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} 抽奖:{r.get('lottery', '-')} 积分:{r.get('points', '-')}"
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
    print(Juejin(cfg).main())
