"""知乎 (zhihu.com) 每日签到

知乎签到没有公开稳定 API, 用 CDP 模拟浏览器:
  - 打开 zhihu.com 已登录 tab
  - 找 "签到" / "已签到" 按钮
  - 点击签到按钮
  - 拿按钮文字确认状态

配置 (config.json):
  "ZHIHU": [
    { "name": "我的知乎账号" }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.zhihu")

# 按钮正则 (JS 端使用)
SIGN_JS = r"签\s*到"
SIGNED_JS = r"已\s*签|今日已|签到完成|签到成功"


class Zhihu(CheckIn):
    name = "知乎"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        self.cdp_port = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))

    def _probe_sign_btn(self, bridge, tab) -> dict[str, Any]:
        """探测签到按钮状态"""
        return bridge.eval(
            tab,
            "(() => {"
            "  const btnRe = /" + SIGN_JS + "/;"
            "  const signedRe = /" + SIGNED_JS + "/;"
            "  const els = [...document.querySelectorAll('button, a, [role=button], .Button')];"
            "  const items = els.map(e => ({tag:e.tagName, text:(e.textContent||'').trim().slice(0,30)}));"
            "  const signBtn = items.find(i => btnRe.test(i.text) && !signedRe.test(i.text));"
            "  const signedBtn = items.find(i => signedRe.test(i.text));"
            "  return {signBtn: signBtn||null, signedBtn: signedBtn||null, href: location.href};"
            "})()",
        )

    def _probe_signed_after(self, bridge, tab) -> dict[str, Any]:
        """点击后验证签到成功"""
        return bridge.eval(
            tab,
            "(() => {"
            "  const signedRe = /" + SIGNED_JS + "/;"
            "  const els = [...document.querySelectorAll('button, a, [role=button], .Button, .Modal')];"
            "  const signed = els.find(e => signedRe.test((e.textContent||'').trim()));"
            "  return {signed_text: signed ? (signed.textContent||'').trim().slice(0,60) : null};"
            "})()",
        )

    def main(self) -> str:
        name = self.account.get("name") or "zhihu"
        rec: dict[str, Any] = {"name": name}

        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError, UnreachableError
        except Exception as e:
            rec["status"] = "skipped"
            rec["message"] = "CDP import 失败: " + str(e)
            return self._format([rec])

        bridge = None
        try:
            bridge = CDPBridge.start(port=self.cdp_port)
        except CDPError as e:
            rec["status"] = "error"
            rec["message"] = "CDP start 失败: " + str(e)
            return self._format([rec])

        try:
            tab = bridge.attach_by_url("https://www.zhihu.com")
            if not tab:
                rec["status"] = "skipped"
                rec["message"] = "CDP: 未找到 zhihu.com tab, 请在 Chrome 登录"
                return self._format([rec])

            try:
                bridge.goto(tab, "https://www.zhihu.com/", 30000)
            except UnreachableError as e:
                rec["status"] = "unreachable"
                rec["message"] = "打开 zhihu 失败: " + str(e)
                return self._format([rec])
            bridge.wait(3000)

            probe = self._probe_sign_btn(bridge, tab)
            if not isinstance(probe, dict):
                rec["status"] = "error"
                rec["message"] = "probe 返回异常: " + str(probe)
                return self._format([rec])

            if probe.get("signedBtn"):
                rec["checkin"] = "already"
                rec["status"] = "ok"
                rec["result"] = "今日已签到"
                return self._format([rec])

            sign_btn = probe.get("signBtn")
            if not sign_btn:
                rec["status"] = "login_disabled"
                rec["message"] = "未找到签到按钮 (probe=" + str(probe) + ")"
                return self._format([rec])

            click = bridge.click_text(tab, sign_btn["text"])
            if not click.get("clicked"):
                reason = click.get("reason") or "未知"
                rec["status"] = "error"
                rec["message"] = "点击签到按钮失败: " + reason
                return self._format([rec])
            bridge.wait(3000)

            after = self._probe_signed_after(bridge, tab)
            signed_text = after.get("signed_text") if isinstance(after, dict) else None
            if signed_text:
                rec["checkin"] = "ok"
                rec["status"] = "ok"
                rec["result"] = signed_text
            else:
                rec["checkin"] = "clicked"
                rec["status"] = "ok"
                rec["result"] = "已点击, 状态待确认"
        except Exception as e:
            rec["status"] = "error"
            rec["message"] = "签到异常: " + type(e).__name__ + ": " + str(e)
        finally:
            if bridge:
                try:
                    bridge.quit()
                except Exception:
                    pass

        return self._format([rec])

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed", "unreachable"))
        skipped = sum(1 for r in results if r.get("status") in ("skipped", "login_disabled"))
        body = "「知乎签到」\n"
        body += "总数 " + str(len(results)) + " | 成功 " + str(ok) + " | 失败 " + str(failed) + " | 跳过 " + str(skipped) + "\n"
        for r in results:
            result = r.get("result") or r.get("message") or "-"
            body += r.get("status", "?") + " " * (14 - len(r.get("status", "?"))) + " "
            body += r.get("name", "?") + " " * (14 - len(r.get("name", "?"))) + " "
            body += "签到:" + str(r.get("checkin", "-")) + " 结果:" + str(result)[:60] + "\n"
        return body


if __name__ == "__main__":
    import sys

    cfg = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read() or "{}"
        cfg = json.loads(raw)
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(Zhihu(cfg).main())
