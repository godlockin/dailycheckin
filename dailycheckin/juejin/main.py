"""掘金 (juejin.cn) 每日签到 + 免费抽奖。

认证: cookie (sessionid / USER_ID)
  - 浏览器登录 juejin.cn, DevTools -> Network -> 复制 Cookie header
  - 或用 CDP 从 9333 Chrome 抓 (attach https://juejin.cn 自动 fallback)

API:
  POST /growth_api/v1/check_in       签到 (返回 + 积分)
  POST /growth_api/v1/lottery/draw   免费抽奖 (每日 1 次)
  GET  /growth_api/v1/get_cur_point  当前积分

配置 (config.json):
  "JUEJIN": [
    {
      "name": "我的账号",
      "cookie": "sessionid=xxx; USER_ID=xxx; ..."   // 可省, 走 CDP fallback
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

logger = logging.getLogger("dailycheckin.juejin")

API_BASE = "https://api.juejin.cn"
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

    def _session(self, cookie: str) -> requests.Session:
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS_BASE)
        if cookie:
            s.headers["Cookie"] = cookie
        s.timeout = 30
        return s

    def _cdp_cookie(self) -> str | None:
        """从 9333 Chrome 已登录的 juejin.cn 抓 Cookie header.

        JUEJIN 同时使用:
          - HTTP cookie (Network.getCookies) — 主要 sessionid
          - document.cookie (page-set) — 部分由前端 JS 写入

        这里合并 Network.getCookies + document.cookie, 去重同名 key.
        """
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
            opened_tab = None
            tab = bridge.attach_by_url("https://juejin.cn")
            if not tab:
                # tab 已被用户关掉: 新开一个 (Chrome profile 里登录态持久)
                opened_tab = bridge.create_tab("https://juejin.cn/")
                tab = opened_tab
                bridge.wait(4000)

            # 1. Network.getCookies (HTTP-set cookies)
            net_cookies = bridge.send_cdp(
                tab, "Network.getCookies",
                {"urls": ["https://juejin.cn/", "https://api.juejin.cn/"]},
            )
            net_parts = []
            for c in (net_cookies or {}).get("cookies", []):
                net_parts.append(f"{c['name']}={c['value']}")

            # 2. document.cookie (page-set cookies)
            doc_cookie = bridge.eval(tab, "document.cookie") or ""
            doc_parts = [p.strip() for p in doc_cookie.split(";") if p.strip()]

            # 3. 合并去重 (document.cookie 后, 后写覆盖)
            seen: set[str] = set()
            all_parts: list[str] = []
            for p in doc_parts + net_parts:
                if "=" not in p:
                    continue
                name = p.split("=", 1)[0].strip()
                if name in seen:
                    continue
                seen.add(name)
                all_parts.append(p)

            cookie_str = "; ".join(all_parts)
            logger.info("CDP: 抓到 juejin cookie (network=%d, document=%d, merged=%d)",
                        len(net_parts), len(doc_parts), len(all_parts))
            return cookie_str or None
        except Exception as e:
            logger.warning("CDP cookie 抓取失败: %s", e)
            return None
        finally:
            # 自动新开的 tab 用完即关
            if opened_tab:
                try:
                    bridge.close_tab(opened_tab)
                except Exception:
                    pass
            try:
                bridge.quit()
            except Exception:
                pass

    def _api(self, s: requests.Session, path: str, method: str = "GET", payload: dict | None = None):
        url = f"{API_BASE}{path}"
        if method == "GET":
            return s.get(url)
        return s.post(url, data=json.dumps(payload or {}))

    def _parse(self, resp: requests.Response) -> tuple[int, str, Any]:
        try:
            d = resp.json()
        except Exception:
            return resp.status_code, (resp.text or "")[:200], None
        err_no = d.get("err_no", -1) if isinstance(d, dict) else -1
        err_msg = d.get("err_msg", "") if isinstance(d, dict) else ""
        return err_no, err_msg, d

    def main(self) -> str:
        name = self.account.get("name") or "juejin"
        rec: dict[str, Any] = {"name": name}

        # 1. 取 cookie (config 优先, 失败 fallback CDP)
        cookie = self.cookie
        if not cookie:
            cookie = self._cdp_cookie() or ""
        if not cookie:
            rec.update(status="skipped", message="无 cookie 且 CDP fallback 也未抓到")
            return self._format([rec])

        s = self._session(cookie)

        # 2. 签到
        try:
            r = self._api(s, "/growth_api/v1/check_in", "POST", {})
            err_no, err_msg, _ = self._parse(r)
            if err_no != 0:
                if r.status_code in (401, 403):
                    ck = self._cdp_cookie()
                    if ck:
                        s.headers["Cookie"] = ck
                        r = self._api(s, "/growth_api/v1/check_in", "POST", {})
                        err_no, err_msg, _ = self._parse(r)
                if err_no != 0:
                    rec.update(status="login_failed", message=f"签到: {err_msg or f'HTTP {r.status_code}'}")
                    return self._format([rec])
            rec["checkin"] = "ok"
        except Exception as e:
            rec.update(status="error", message=f"签到异常: {e}")
            return self._format([rec])

        # 3. 免费抽奖 (每天 1 次, 失败不算签到失败)
        try:
            r = self._api(s, "/growth_api/v1/lottery/draw", "POST", {})
            err_no, err_msg, d = self._parse(r)
            if err_no == 0 and isinstance(d, dict):
                rec["lottery"] = (d.get("data") or {}).get("lottery_name") or "ok"
            else:
                rec["lottery"] = err_msg or f"HTTP {r.status_code}"
        except Exception as e:
            rec["lottery"] = f"异常: {e}"

        # 4. 当前积分
        try:
            r = self._api(s, "/growth_api/v1/get_cur_point", "GET")
            err_no, err_msg, d = self._parse(r)
            if err_no == 0 and isinstance(d, dict):
                rec["points"] = (d.get("data") or 0)
            else:
                rec["points"] = err_msg or f"HTTP {r.status_code}"
        except Exception as e:
            rec["points"] = f"异常: {e}"

        rec.update(status="ok")
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
