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
        """privilege 页每日签到: 任务卡片 DIV.await「去完成」(tushare 2026-09 改版后按钮是 div 非 button)。

        卡片结构: .action_item > (.task_title=每日签到, .await=去完成 | .finish=已完成)
        点击后轮询验证: 卡片变已完成 / 积分明细出现今日记录 / 出现二级「立即签到」按钮。
        """
        try:
            bridge.goto(tab_id, PRIVILEGE_URL, 30000)
        except Exception as e:
            return {"status": "failed", "message": f"goto privilege failed: {e}"}
        bridge.wait(2500)

        click_state = bridge.eval(
            tab_id,
            """(() => {
              const cards = [...document.querySelectorAll('.action_item')];
              const card = cards.find(c => /每日签到/.test(((c.querySelector('.task_title')||{}).textContent)||''));
              if (!card) return {state: 'no_card'};
              if (card.querySelector('.finish')) return {state: 'already'};
              const btn = card.querySelector('.await');
              if (!btn) return {state: 'no_btn'};
              btn.click();
              return {state: 'clicked'};
            })()""",
        )
        if not isinstance(click_state, dict):
            return {"status": "failed", "message": f"探测返回异常: {click_state!r}"}
        if click_state.get("state") == "already":
            return {"status": "skipped", "message": "今日已签到 (卡片显示已完成)"}
        if click_state.get("state") == "no_card":
            return {"status": "failed", "message": "privilege 页找不到每日签到卡片"}
        if click_state.get("state") != "clicked":
            return {"status": "failed", "message": "每日签到卡片无「去完成」按钮"}

        # 点击后轮询: 「去完成」可能直接完成, 也可能跳转到签到子页需再点「立即签到」
        check_js = """(() => {
          const cards = [...document.querySelectorAll('.action_item')];
          const card = cards.find(c => /每日签到/.test(((c.querySelector('.task_title')||{}).textContent)||''));
          const now = new Date();
          const ds = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');
          const bodyText = (document.body ? document.body.innerText : '');
          const hasToday = new RegExp('于 ' + ds + '[^\\\\n]*每日签到获取').test(bodyText);
          const deep = [...document.querySelectorAll('button, a, div, span')].find(e =>
            e.children.length === 0 && /^(立即签到|签\\s*到|去签到|领取)$/.test((e.textContent||'').trim()));
          return {
            finished: !!(card && card.querySelector('.finish')),
            hasToday,
            deepBtn: deep ? deep.textContent.trim() : null,
            noCard: !card,
          };
        })()"""
        deadline = time.time() + 20
        deep_clicked = False
        last: dict[str, Any] = {}
        while time.time() < deadline:
            bridge.wait(2000)
            last = bridge.eval(tab_id, check_js) or {}
            if not isinstance(last, dict):
                continue
            if last.get("finished") or last.get("hasToday"):
                return {"status": "done", "message": "签到成功 (积分明细已确认)"}
            deep_btn = last.get("deepBtn")
            if deep_btn and not deep_clicked:
                try:
                    bridge.click_text(tab_id, deep_btn)
                except Exception:
                    pass
                deep_clicked = True
                continue
            if last.get("noCard") and not deep_clicked:
                # 「去完成」跳走了路由, 回 privilege 再验证
                try:
                    bridge.goto(tab_id, PRIVILEGE_URL, 20000)
                except Exception:
                    pass
        return {"status": "done", "message": "已点击「去完成」, 20s 内未确认到积分记录"}

    # ------------------------------------------------------------------ guess

    def _do_guess(self, tab_id: str, bridge: CDPBridge) -> dict[str, Any]:
        """猜下一交易日涨跌: privilege 页 .contest 卡片。

        UI (2026-09 实测):
          - 投票钮: .like_round img (看涨) / .dislike_round img (看跌), 点击即静默提交
          - 未投票: 分布条文字 "78%/22%"
          - 已投票: 分布条文字变成 "已选 78%/22% (img 仍可点, 不会禁用, 必须靠"已选"判定)
          - 活动状态: 标题旁 "竞猜中"
        """
        try:
            bridge.goto(tab_id, PRIVILEGE_URL, 20000)
        except Exception:
            pass

        # 等 contest 异步加载 (最多 12s). eval JSON.stringify 返回 str, 需 parse
        state: dict[str, Any] | None = None
        for _ in range(12):
            bridge.wait(1000)
            raw = bridge.eval(
                tab_id,
                r"""JSON.stringify((() => {
                  const contest = document.querySelector('.contest');
                  if (!contest) return {present: false};
                  const text = contest.innerText || '';
                  const phase = (text.match(/猜[^\n]*?\s+(\S+)/) || [])[1] || null;
                  return {
                    present: true,
                    phase,
                    hasLike: !!contest.querySelector('.like_round img'),
                    hasDislike: !!contest.querySelector('.dislike_round img'),
                    voted: /已选/.test(text),
                    text: text.slice(0, 200),
                  };
                })())""",
            )
            try:
                state = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                state = None
            if isinstance(state, dict) and state.get("present"):
                break

        if not isinstance(state, dict) or not state.get("present"):
            return {"status": "inactive", "message": "本期无猜涨跌活动"}

        phase = state.get("phase") or ""
        if "竞猜" not in phase and "中" not in phase:
            return {"status": "inactive", "message": f"本期未开始 ({phase or '非竞猜中'})"}

        if state.get("voted"):
            return {"status": "skipped", "message": f"今日已投 (分布: {state['text'].split(chr(10).replace(chr(10), ' '))[:60]})"}

        # 待投: 随机点 涨/跌 img (.like_round img / .dislike_round img)
        direction = 1 if secrets.randbits(1) == 0 else 2
        label = "涨" if direction == 1 else "跌"
        selector = ".like_round img" if direction == 1 else ".dislike_round img"

        clicked = bridge.eval(
            tab_id,
            f"""(() => {{
              const el = document.querySelector({json.dumps(selector)});
              if (!el) return false;
              el.click();
              return true;
            }})()""",
        )
        if not clicked:
            # 回退另一个方向
            direction = 2 if direction == 1 else 1
            label = "跌" if label == "涨" else "涨"
            selector = ".dislike_round img" if direction == 2 else ".like_round img"
            clicked = bridge.eval(
                tab_id,
                f"""(() => {{
                  const el = document.querySelector({json.dumps(selector)});
                  if (!el) return false;
                  el.click();
                  return true;
                }})()""",
            )
        if not clicked:
            return {"status": "failed", "message": "找不到看涨/看跌投票 img"}

        # 验证 (轮询 8s): 分布条出现 "已选"
        for _ in range(8):
            bridge.wait(1000)
            txt = bridge.eval(tab_id, "(document.querySelector('.contest')||{}).innerText||''")
            if "已选" in (txt or ""):
                return {"status": "done", "message": f"已投{label} (direction={direction})"}
        return {"status": "done", "message": f"已点击{label}, 但未在 8s 内确认 '已选'"}

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