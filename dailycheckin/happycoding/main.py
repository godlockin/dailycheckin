"""HappyCoding (happycoding.xyz) 每日签到。

签到入口: 登录后 /profile 页面底部 "Daily Check-in" → 「Check in now」按钮。
奖励: 随机额度 (quota, 实测 +$5), 直接加余额。

认证: 完全靠 Chrome profile 登录态 (CDP attach 已登录 tab / 自动开 tab 导航 /profile)。

配置 (config.json):
  "HAPPYCODING": [
    { "name": "我的账号" }
  ]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.happycoding")

PROFILE_URL = "https://happycoding.xyz/profile"


class HappyCoding(CheckIn):
    name = "HappyCoding"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        self.cdp_port = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))

    def main(self) -> str:
        rec_name = self.account.get("name") or "happycoding"
        rec: dict[str, Any] = {"name": rec_name}

        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError
        except Exception as e:
            rec["status"] = "skipped"
            rec["message"] = f"CDP import 失败: {e}"
            return self._format([rec])

        bridge: CDPBridge | None = None
        opened_tab: str | None = None
        try:
            bridge = CDPBridge.start(port=self.cdp_port)

            # attach 任意已存在 happycoding tab (保持登录 cookie), 否则新开
            tab = None
            for t in bridge.tabs():
                if "happycoding.xyz" in (t.get("url") or ""):
                    tab = bridge._call("attach", tabId=t["id"])["tabId"]
                    break
            if not tab:
                opened_tab = bridge.create_tab(PROFILE_URL)
                tab = opened_tab
                bridge.wait(4000)

            # 导航到 /profile (同 tab, 保持会话)
            bridge.goto(tab, PROFILE_URL, 20000)
            bridge.wait(3000)

            # 探测登录态: 未登录会跳 /sign-in
            href = bridge.eval(tab, "location.href")
            if "/sign-in" in href:
                rec["status"] = "login_failed"
                rec["message"] = "happycoding.xyz 未登录 (请在 9333 Chrome 登录后重跑)"
                return self._format([rec])

            # 探测签到按钮 + 状态
            probe = bridge.eval(
                tab,
                r"""JSON.stringify((() => {
                  const btns = [...document.querySelectorAll('button')];
                  // 待签: "Check in now"; 已签: "Checked in" (disabled)
                  const checkinNow = btns.find(b => /check in now/i.test(b.textContent || ''));
                  const checkedIn = btns.find(b => /^checked in$/i.test((b.textContent||'').trim()));
                  const body = document.body.innerText || '';
                  const alreadyHint = /You can only check in once|Checked in/.test(body);
                  return {
                    hasBtn: !!checkinNow,
                    btnDisabled: checkinNow ? checkinNow.disabled : null,
                    already: !!checkedIn || alreadyHint,
                  };
                })())""",
            )
            p = probe if isinstance(probe, dict) else json.loads(probe or "{}")

            # 已签判定: 按钮变 "Checked in" (disabled) 或明确提示
            if p.get("already"):
                rec["checkin"] = "already"
                rec["message"] = "今日已签到 (Checked in)"
                rec["status"] = "ok"
                return self._format([rec])

            if not p.get("hasBtn"):
                rec["status"] = "login_disabled"
                rec["message"] = "找不到 'Check in now' 按钮 (页面改版?)"
                return self._format([rec])

            # 点击 Check in now
            clicked = bridge.eval(
                tab,
                r"""(() => {
                  const btn = [...document.querySelectorAll('button')]
                    .find(b => /check in now/i.test(b.textContent || ''));
                  if (!btn || btn.disabled) return false;
                  btn.click();
                  return true;
                })()""",
            )
            if not clicked:
                rec["status"] = "failed"
                rec["message"] = "Check in now 点击失败"
                return self._format([rec])

            # 等结果 + 验证 (toast: "Check-in successful! Received $N")
            reward = None
            for _ in range(8):
                bridge.wait(1000)
                reward = bridge.eval(
                    tab,
                    r"""(() => {
                      const body = document.body.innerText || '';
                      const m = body.match(/Check-in successful![^\n]{0,40}|Today \+(\$\d+)/);
                      return m ? (m[0].match(/\$\d+/) || [''])[0] || m[0] : null;
                    })()""",
                )
                if reward:
                    break

            rec["checkin"] = "ok"
            rec["reward"] = reward or "?"
            rec["message"] = f"签到成功 ({reward or '奖励未解析'})"
            rec["status"] = "ok"
            return self._format([rec])

        except Exception as e:
            rec["status"] = "error"
            rec["message"] = f"签到异常: {type(e).__name__}: {e}"
            return self._format([rec])
        finally:
            if opened_tab and bridge:
                try:
                    bridge.close_tab(opened_tab)
                except Exception:
                    pass
            if bridge:
                try:
                    bridge.quit()
                except Exception:
                    pass

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") in ("skipped", "login_disabled"))
        body = (
            f"「HappyCoding 签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<16} "
            f"签到:{r.get('checkin', '-')} 奖励:{r.get('reward', '-')} {r.get('message', '')[:40]}"
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
    print(HappyCoding(cfg).main())
