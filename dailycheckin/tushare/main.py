"""tushare.pro 每日签到 + 猜涨跌 (CDP 浏览器自动化)

参照 freqtrade-learning/aastocks/portfolio/tushare_tasks.py 的协议,
通过 Node CDP sidecar (cdp/bridge.mjs) 驱动常驻 Chrome 完成:

  1. 检查登录态 (页面文本含用户名)
  2. 未登录 -> navigate to /#/login, 填手机号+密码, 提交
  3. navigate to /#/user/privilege (签到页)
  4. eval JS 拿任务列表, 找 DAILY_SIGN 任务; 未完成则点 sign 按钮
  5. navigate to 猜涨跌页; period.status==1 且未投 -> 投 1 或 2 (random)

账号从 config.json 取, 没填则用环境变量 TUSHARE_USERNAME / TUSHARE_PASSWORD
(参考 freqtrade-learning 的 ~/.zsh/env.zsh)。

配置 (config.json):
  "TUSHARE": [
    {
      "name": "我的账号",
      "username": "13122249996",   // 可省, 走 TUSHARE_USERNAME 环境变量
      "password": "StevenChen001!", // 可省, 走 TUSHARE_PASSWORD 环境变量
      "cdp_port": 9333              // 默认 9333
    }
  ]
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import time
from typing import Any

from dailycheckin import CheckIn
from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError, UnreachableError, is_cdp_alive

logger = logging.getLogger("dailycheckin.tushare")

TUSHARE_WEBORDER = "https://tushare.pro/weborder/"
LOGIN_CHECK_RE = "陈升|ID：\\s*\\d+"
CDP_DEFAULT_PORT = 9333
LOGIN_URL = f"{TUSHARE_WEBORDER}#/login"
PRIVILEGE_URL = f"{TUSHARE_WEBORDER}#/user/privilege"
GUESS_URL_CANDIDATES = [
    f"{TUSHARE_WEBORDER}#/task/guess",
    f"{TUSHARE_WEBORDER}#/guess",
    f"{TUSHARE_WEBORDER}#/user/guess",
    f"{TUSHARE_WEBORDER}#/task/lucky",
]


class Tushare(CheckIn):
    name = "Tushare 每日签到 + 猜涨跌"

    def __init__(self, check_item: dict | None = None):
        self.check_item = check_item or {}
        self.username = (
            self.check_item.get("username")
            or os.environ.get("TUSHARE_USERNAME", "").strip()
        )
        self.password = (
            self.check_item.get("password")
            or os.environ.get("TUSHARE_PASSWORD", "").strip()
        )
        self.cdp_port = int(self.check_item.get("cdp_port") or CDP_DEFAULT_PORT)

    # ------------------------------------------------------------------ login

    def _is_logged_in(self, tab_id: str, bridge: CDPBridge) -> bool:
        try:
            return bool(bridge.eval(
                tab_id,
                f"(() => /{LOGIN_CHECK_RE}/.test(document.body ? document.body.innerText : ''))()",
            ))
        except Exception:
            return False

    def _do_login(self, tab_id: str, bridge: CDPBridge) -> tuple[bool, str]:
        """返回 (成功?, 原因). 成功时 reason=''; 失败区分 captcha/credential/timeout."""
        try:
            bridge.goto(tab_id, LOGIN_URL, 30000)
        except Exception as e:
            logger.warning("goto login failed: %s", e)
            return False, f"打开登录页失败: {e}"
        bridge.wait(3000)
        # tushare 默认微信扫码 tab; 确认密码登录 input 存在, 否则点 "密码登录" tab
        has_pw = bridge.eval(
            tab_id,
            "(() => document.querySelector('input[type=password]') !== null)()",
        )
        if not has_pw:
            try:
                bridge.click_text_on_url("tushare.pro", "密码登录")
                bridge.wait(1500)
            except Exception as e:
                logger.warning("click 密码登录 tab failed: %s", e)
        try:
            # 用 native setter + input 事件填值 (Vue/React 受控组件兼容, 比 Input.insertText 可靠)
            fill_phone = bridge.eval(
                tab_id,
                f"""(() => {{
                  const inp = document.querySelector('input[placeholder*="手机号"], input[placeholder*="邮箱"]');
                  if (!inp) return false;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(inp, {json.dumps(self.username)});
                  inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                  inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                  return inp.value.length > 0;
                }})()""",
            )
            if not fill_phone:
                return False, "找不到账号输入框"
            bridge.wait(400)
            fill_pw = bridge.eval(
                tab_id,
                f"""(() => {{
                  const inp = document.querySelector('input[type=password]');
                  if (!inp) return false;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(inp, {json.dumps(self.password)});
                  inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                  inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                  return inp.value.length > 0;
                }})()""",
            )
            if not fill_pw:
                return False, "找不到密码输入框"
            bridge.wait(400)
            # 点提交后再看是否弹图形验证码 (填值阶段 tushare 可能不显示, 提交后才出)
            clicked = bridge.eval(
                tab_id,
                """(() => {
                  const btns = [...document.querySelectorAll('button')];
                  const target = btns.find(b => b.textContent.trim() === '登录' && !b.disabled);
                  if (!target) return false;
                  target.click();
                  return true;
                })()""",
            )
            if not clicked:
                return False, "找不到登录按钮"
        except Exception as e:
            logger.warning("login form interaction failed: %s", e)
            return False, f"填表异常: {e}"
        deadline = time.time() + 30
        while time.time() < deadline:
            bridge.wait(1500)
            if self._is_logged_in(tab_id, bridge):
                return True, ""
            # 提交后弹图形验证码 (headless/异地 IP 常见)
            if bridge.eval(tab_id, "(() => /请输入图形验证码|图形验证/.test(document.body.innerText))()"):
                return False, "要求图形验证码 (需在常驻浏览器里先登录, 复用 cookie)"
            if bridge.eval(tab_id, "(() => /密码错误|账号或密码|用户不存在/.test(document.body.innerText))()"):
                return False, "账号或密码错误"
        return False, "登录超时 (30s 未跳转)"

    # ------------------------------------------------------------------ sign

    def _do_sign(self, tab_id: str, bridge: CDPBridge) -> dict[str, Any]:
        try:
            bridge.goto(tab_id, PRIVILEGE_URL, 30000)
        except Exception as e:
            return {"status": "failed", "message": f"goto privilege failed: {e}"}
        bridge.wait(2500)
        # 读 Vue store / window globals 找任务列表
        tasks = bridge.eval(
            tab_id,
            """(() => {
              const dump = (o) => { try { return JSON.stringify(o).slice(0, 4000) } catch(e) { return null } };
              const w = window;
              let raw = w.__TUSHARE_TASKS__ || w.tasks || w.dailyTasks;
              if (raw) return dump(raw);
              const root = document.querySelector('#app') || document.body;
              if (root && root.__vue__) {
                try { return dump(root.__vue__.$store?.state?.tasks || root.__vue__?.tasks || root.__vue__); } catch(e) {}
              }
              return null;
            })()""",
        )
        already = False
        if tasks:
            try:
                arr = json.loads(tasks) if isinstance(tasks, str) else tasks
                if isinstance(arr, list):
                    for entry in arr:
                        if (entry or {}).get("task_code") == "DAILY_SIGN":
                            already = bool(entry.get("is_completed"))
                            break
            except Exception as e:
                logger.warning("parse tasks json failed: %s", e)
        if already:
            return {"status": "skipped", "message": "DAILY_SIGN 已完成"}
        try:
            click = bridge.click_text_on_url("tushare.pro", "签到|立即签到|去签到|领取|完成签到")
            if not click.get("clicked"):
                return {"status": "failed", "message": "找不到签到按钮"}
        except Exception as e:
            return {"status": "failed", "message": f"点击签到失败: {e}"}
        bridge.wait(2500)
        return {"status": "done", "message": "已点击签到"}

    # ------------------------------------------------------------------ guess

    def _do_guess(self, tab_id: str, bridge: CDPBridge) -> dict[str, Any]:
        for url in GUESS_URL_CANDIDATES:
            try:
                bridge.goto(tab_id, url, 30000)
            except Exception:
                continue
            bridge.wait(2500)
            data = bridge.eval(
                tab_id,
                """(() => {
                  const w = window;
                  const root = document.querySelector('#app') || document.body;
                  let guess = w.__TUSHARE_GUESS__;
                  if (!guess && root && root.__vue__) {
                    try { guess = root.__vue__.$store?.state?.guess || root.__vue__?.guess; } catch(e) {}
                  }
                  if (!guess) {
                    const txt = document.body ? document.body.innerText : '';
                    const active = /进行中|投票中|猜/i.test(txt);
                    return JSON.stringify({period: {status: active ? 1 : 0}, user_guess: {}});
                  }
                  return JSON.stringify(guess);
                })()""",
            )
            if not data:
                continue
            try:
                info = json.loads(data) if isinstance(data, str) else data
            except Exception:
                continue
            period = (info or {}).get("period") or {}
            user_guess = (info or {}).get("user_guess") or {}
            if int(period.get("status", 0)) != 1:
                return {"status": "inactive", "message": f"本期未开始 (status={period.get('status')})"}
            if user_guess.get("vote"):
                return {"status": "skipped", "message": f"已投 (vote={user_guess.get('vote')})"}
            direction = 1 if secrets.randbits(1) == 0 else 2
            label = "涨" if direction == 1 else "跌"
            try:
                click = bridge.click_text_on_url("tushare.pro", "^(看涨|涨|up|1)$")
                if not click.get("clicked"):
                    click = bridge.click_text_on_url("tushare.pro", "^(看跌|跌|down|2)$")
                    if click.get("clicked"):
                        direction = 2
            except Exception as e:
                return {"status": "failed", "message": f"点击投票失败: {e}"}
            if not click.get("clicked"):
                return {"status": "failed", "message": "找不到看涨/看跌按钮"}
            bridge.wait(2000)
            return {"status": "done", "message": f"已投{label} (direction={direction})"}
        return {"status": "failed", "message": "所有 猜涨跌 URL 都不可达"}

    # ------------------------------------------------------------------ main

    def main(self) -> str:
        # Dependency gate: skip when Node / CDP not available
        if not shutil.which("node"):
            return "「Tushare 每日签到 + 猜涨跌」\n跳过: 需 node (CDP bridge)"
        if not is_cdp_alive(self.cdp_port):
            return f"「Tushare 每日签到 + 猜涨跌」\n跳过: CDP 未就绪 (port {self.cdp_port})"
        rec_name = self.check_item.get("name") or "tushare"
        results: dict[str, dict[str, Any]] = {"sign": {}, "guess": {}}
        bridge: CDPBridge | None = None
        tab_id: str | None = None
        try:
            bridge = CDPBridge.start(port=self.cdp_port)
            tab_id = bridge.create_tab()
            # 先导航到 privilege 页; 若未登录会自动跳 login
            bridge.goto(tab_id, PRIVILEGE_URL, 30000)
            bridge.wait(3000)
            if not self._is_logged_in(tab_id, bridge):
                # 无凭证: 依赖常驻浏览器已有 cookie, 提示人工先登录
                if not self.username or not self.password:
                    results["sign"] = {
                        "status": "login_failed",
                        "message": "未登录且无账号密码 (在常驻 Chrome 里登录 tushare 一次, 或配 username/password)",
                    }
                    return self._format(results, rec_name)
                ok, reason = self._do_login(tab_id, bridge)
                if not ok:
                    results["sign"] = {"status": "login_failed", "message": reason or "登录失败"}
                    return self._format(results, rec_name)
            results["sign"] = self._do_sign(tab_id, bridge)
            results["guess"] = self._do_guess(tab_id, bridge)
            bridge.close_tab(tab_id)
        except (CDPError, UnreachableError) as e:
            results["sign"] = {"status": "error", "message": f"CDP 错误: {e}"}
        finally:
            if bridge:
                try:
                    bridge.quit()
                except Exception:
                    pass

        return self._format(results, rec_name)

    def _format(self, results: dict[str, dict[str, Any]], name: str) -> str:
        sign = results.get("sign", {})
        guess = results.get("guess", {})
        ok = sum(1 for r in (sign, guess) if r.get("status") in ("done", "skipped", "inactive"))
        failed = sum(1 for r in (sign, guess) if r.get("status") in ("failed", "error", "login_failed", "unreachable"))
        body = f"「Tushare 每日签到 + 猜涨跌」\n账号 {name} | 成功 {ok} | 失败 {failed}\n"
        body += f"  签到:   {sign.get('status','?') :<14} {sign.get('message','')}\n"
        body += f"  猜涨跌: {guess.get('status','?') :<14} {guess.get('message','')}\n"
        return body


if __name__ == "__main__":
    import sys

    cfg: dict = {}
    if not sys.stdin.isatty():
        cfg = json.loads(sys.stdin.read() or "{}")
    print(Tushare(cfg).main())