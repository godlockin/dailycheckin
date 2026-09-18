"""薄荷 API (x666.me) 每日签到 + 转盘。

真实签到页: up.x666.me (x666.me 主页的 "前往签到" 链接跳过去)。
模块直接打开 up.x666.me (cookie 跨子域共享, 不依赖 x666.me 入口按钮位置),
x666.me 流程作为 fallback (若 up.x666.me 打不开/未登录, 尝试 x666.me 点 "前往签到")。

流程 (浏览器内 UI 点击, 跟 tushare/zhihu 类似):
  1. 打开 up.x666.me (优先) / x666.me (fallback)
  2. 找签到按钮, 点击; 若已是 "今日已签到" -> 直接完成
  3. 找转盘按钮, 点击 (会有 modal "开始抽奖" 再点一次)
  4. 验证: button 变 "今日已签到" 或 body 含 "签到成功"

无需 config: 完全靠 Chrome profile 登录态。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.bohe")


class Bohe(CheckIn):
    name = "薄荷 API"

    # 签到 + 转盘按钮的关键词 (按优先级)
    SIGN_BUTTON_TEXTS = (
        "前往签到",
        "签到",
        "打卡",
    )
    SPIN_BUTTON_TEXTS = (
        "转盘",
        "今日签到",  # 也是转盘按钮 (转盘是主操作)
        "抽奖",
    )

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}

    def main(self) -> str:
        name = self.account.get("name") or "bohe"
        rec: dict[str, Any] = {"name": name}

        try:
            from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError, UnreachableError
        except Exception as e:
            rec["status"] = "skipped"
            rec["message"] = f"CDP import 失败: {e}"
            return self._format([rec])

        bridge: CDPBridge | None = None
        opened_tab: str | None = None
        try:
            bridge = CDPBridge.start(port=9333)
        except CDPError as e:
            rec["status"] = "error"
            rec["message"] = f"CDP start 失败: {e}"
            return self._format([rec])

        try:
            # 1. 优先 attach up.x666.me (真实签到页); 失败回退 x666.me
            tab = bridge.attach_by_url("https://up.x666.me")
            if not tab:
                tab = bridge.create_tab("https://up.x666.me/")
                opened_tab = tab
                bridge.wait(4000)
            logger.info("bohe: href=%s", bridge.eval(tab, "location.href"))

            # 2. 探测 up.x666.me 登录态 + 签到/转盘按钮
            status = self._probe_up(bridge, tab)
            if not isinstance(status, dict):
                # up.x666.me 未加载 (可能 cookie 没同步), 回退 x666.me 流程
                logger.info("bohe: up.x666.me 探测失败, 回退 x666.me 流程")
                if opened_tab:
                    try:
                        bridge.close_tab(opened_tab)
                    except Exception:
                        pass
                    opened_tab = None
                tab = bridge.create_tab("https://x666.me/")
                opened_tab = tab
                bridge.wait(4000)
                clicked = self._click_any(bridge, tab, self.SIGN_BUTTON_TEXTS)
                if not clicked:
                    rec["status"] = "login_disabled"
                    rec["message"] = "x666.me 找不到 '前往签到' 按钮 (可能未登录或入口改版)"
                    return self._format([rec])
                logger.info("bohe: 点击 '前往签到' -> %s", clicked)
                bridge.wait(4000)
                status = self._probe_up(bridge, tab)
                if not isinstance(status, dict):
                    rec["status"] = "login_disabled"
                    rec["message"] = "跳转 up.x666.me 后页面异常, 未登录?"
                    return self._format([rec])

            # 3. 已签到判定
            if status.get("signedText") and not status.get("signText"):
                rec["checkin"] = "already"
                rec["message"] = f"今日已签到 (按钮: {status['signedText']})"
                rec["status"] = "ok"
                return self._format([rec])

            # 4. 点签到按钮 (主)
            clicked_any = False
            for txt in self.SIGN_BUTTON_TEXTS:
                r = self._click_any(bridge, tab, [txt])
                if r:
                    logger.info("bohe: clicked %r", r)
                    clicked_any = True
                    break
            if clicked_any:
                bridge.wait(3000)

            # 5. 点转盘
            for txt in self.SPIN_BUTTON_TEXTS:
                r = self._click_any(bridge, tab, [txt])
                if r:
                    logger.info("bohe: spin clicked %r", r)
                    bridge.wait(3000)
                    # 转盘可能弹 modal, 找 "开始抽奖/立即抽奖"
                    self._click_any(bridge, tab, ["开始抽奖", "立即抽奖", "开始", "GO", "抽奖"])
                    bridge.wait(2000)
                    break

            # 6. 验证结果
            bridge.wait(1500)
            after = self._probe_signed(bridge, tab)
            if after and "已签" in after:
                rec["checkin"] = "ok"
                rec["message"] = "签到成功"
            else:
                rec["checkin"] = "clicked"
                rec["message"] = f"已点击, 验证未确认: {after}"
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

    @staticmethod
    def _probe_up(bridge, tab) -> dict | None:
        """探测 up.x666.me 页面: 签到/转盘/已签到 按钮状态."""
        return bridge.eval(
            tab,
            """(() => {
              const items = [...document.querySelectorAll('button, a, [role=button], div, span')];
              const findText = (re) => items.find(e => e.children.length === 0
                && re.test((e.textContent||'').trim()))?.textContent?.trim() || null;
              return {
                signText: findText(/^(签到|打卡)$/),
                signedText: findText(/今日已签到|签到完成|已签|签到成功/),
                spinText: findText(/转盘|抽奖|spin|lottery/i),
                hasBody: (document.body.innerText || '').length > 100,
              };
            })()""",
        )

    @staticmethod
    def _probe_signed(bridge, tab) -> str | None:
        return bridge.eval(
            tab,
            """(() => {
              const items = [...document.querySelectorAll('button, span, div')];
              const signed = items.find(e => e.children.length === 0
                && /今日已签到|签到完成|已签|签到成功/.test((e.textContent||'').trim()));
              return signed ? signed.textContent.trim() : null;
            })()""",
        )

    @staticmethod
    def _click_any(bridge, tab, texts) -> str | None:
        """在 tab 内找第一个匹配 texts 任意一个的 button/a/div (textContent), click, 返回匹配的 text."""
        rx = "/(" + "|".join(t for t in texts) + ")/"
        result = bridge.eval(
            tab,
            f"""(() => {{
              const rx = new RegExp({rx});
              const items = [...document.querySelectorAll('button, a, [role=button], div.btn, span.btn')];
              const target = items.find(e => e.children.length === 0 && rx.test((e.textContent||'').trim()));
              if (!target) return null;
              if (target.disabled) return null;
              target.click();
              return target.textContent.trim();
            }})()""",
        )
        return result if isinstance(result, str) else None

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") in ("skipped", "login_disabled"))
        body = (
            f"「薄荷 API 签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} {r.get('message', '')[:50]}"
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
    print(Bohe(cfg).main())
