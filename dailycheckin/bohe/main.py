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

    # 签到 = 点转盘(薄荷 site 上转盘就是签到入口, 没有单独的"签到"按钮)
    # 不再用 _click_any (text 模糊匹配), 直接 _click_spin (.spin-button)
    SPIN_BUTTON_TEXTS = (
        "转盘",  # 备用文本匹配
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

            # 2. 探测 up.x666.me: 登录态 + 转盘/签到状态
            status = self._probe_up(bridge, tab)
            if not isinstance(status, dict) or not status.get("present"):
                # up.x666.me 探测失败, 回退 x666.me 流程
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
                # 直接点"前往签到" nav 链接 (无需 _click_any 模糊匹配)
                clicked = bridge.eval(
                    tab,
                    r"""(() => {
                      const a = [...document.querySelectorAll('a[href]')]
                        .find(x => /签到/.test(x.textContent || ''));
                      if (a) { a.click(); return a.href || 'clicked'; }
                      return null;
                    })()""",
                )
                if not clicked:
                    rec["status"] = "login_disabled"
                    rec["message"] = "x666.me 找不到 '前往签到' 链接 (可能未登录或入口改版)"
                    return self._format([rec])
                logger.info("bohe: 点击 '前往签到' -> %s", clicked)
                bridge.wait(4000)
                status = self._probe_up(bridge, tab)
                if not isinstance(status, dict) or not status.get("present"):
                    rec["status"] = "login_disabled"
                    rec["message"] = "跳转 up.x666.me 后页面异常, 未登录?"
                    return self._format([rec])

            # 2a. 登录态检测 (up.x666.me body 顶部有 "登录" link 提示)
            if status.get("showLogin"):
                rec["status"] = "login_failed"
                rec["message"] = "up.x666.me 未登录 (请在 9333 Chrome 登录 up.x666.me 后重跑)"
                return self._format([rec])

            # 2b. 今日已签到判定
            if status.get("signedText"):
                rec["checkin"] = "already"
                rec["message"] = f"今日已签到 ({status['signedText']})"
                rec["status"] = "ok"
                return self._format([rec])

            # 2c. 页面加载中 (今日已签过/转盘动画中), 不重复点击, 视为 ok
            if status.get("isLoading"):
                rec["checkin"] = "already"
                rec["message"] = "今日已签 (转盘加载中)"
                rec["status"] = "ok"
                return self._format([rec])

            # 3. 点 .spin-button (转盘) — 薄荷 site 上转盘是签到入口
            spin_clicked = self._click_spin(bridge, tab)
            if not spin_clicked:
                # fallback 文本匹配
                spin_clicked = self._click_any(bridge, tab, ["转盘", "立即抽奖", "GO", "开始"])
            if not spin_clicked:
                rec["status"] = "login_disabled"
                rec["message"] = "找不到转盘按钮 (.spin-button) — 页面改版?"
                return self._format([rec])
            logger.info("bohe: 转盘点击成功")
            bridge.wait(5000)  # 转盘动画 + 抽奖 + 写库

            # 处理转盘后弹出的确认/结果 modal
            self._click_any(bridge, tab, ["确定", "确认", "收下", "关闭", "我知道了"])
            bridge.wait(3500)  # 弹窗 modal 动画

            # 4. 验证: 等最多 12s (页面有 '加载中' 期间, 等 hasResult / signedText)
            after = None
            for _ in range(6):
                after = self._probe_signed(bridge, tab)
                if after:
                    break
                bridge.wait(2000)
            if after:
                rec["checkin"] = "ok"
                rec["message"] = f"签到成功 ({after})"
            else:
                # 兜底: 看 .result-title (转盘抽奖结果) / body 含恭喜获得
                fallback = self._probe_up(bridge, tab)
                rt = (fallback or {}).get("resultTitle") or ""
                body = (fallback or {}).get("bodySample") or ""
                if (fallback and (fallback.get("hasResult") or "恭喜" in rt
                                   or "已签" in body or "签到成功" in body)):
                    rec["checkin"] = "ok"
                    rec["message"] = f"已签到 (兜底: {rt.strip() or 'body 含已签'})"
                else:
                    rec["checkin"] = "clicked"
                    rec["message"] = "已点击转盘, 12s 内未检测到 '恭喜获得/已签到' 反馈 (今日可能已签过, 或站点改版)"
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
        """探测 up.x666.me 页面: 转盘按钮 / 已签到 / 登录态 / 加载中.

        用字符串拼接构造 regex (避开 f-string / raw-string 转义陷阱).
        """
        return bridge.eval(
            tab,
            "(() => {"
            "  const body = document.body.innerText || '';"
            "  const spin = document.querySelector('.spin-button, [class*=spin]');"
            "  const resultTitle = document.querySelector('.result-title');"
            "  const has = function(pat) { return new RegExp(pat).test(body); };"
            "  const signed = has('今日已签到') || has('签到完成');"
            "  const result = has('恭喜获得') || has('已领取') || has('本次获得');"
            "  const loading = has('加载中');"
            "  const showLogin = new RegExp('(^|[^\\\\s])\\\\s*登录\\\\s*([^\\\\s]|$)').test(body) && !spin;"
            "  return {"
            "    present: body.length > 50,"
            "    spinExists: !!spin,"
            "    spinDisabled: spin ? !!spin.disabled : null,"
            "    spinText: spin ? (spin.textContent || '').trim() : null,"
            "    signedText: signed ? (body.match(/今日已签到.{0,30}/) || [''])[0] : null,"
            "    hasResult: result,"
            "    isLoading: loading,"
            "    showLogin: showLogin,"
            "    resultTitle: resultTitle ? resultTitle.textContent.trim() : null,"
            "    bodySample: body.slice(0, 400),"
            "  };"
            "})()",
        )

    @staticmethod
    def _probe_signed(bridge, tab) -> str | None:
        """验证签到成功: body 含 '恭喜获得' / '已签到' / '已领取' 等关键文字."""
        return bridge.eval(
            tab,
            """(() => {
              const body = document.body.innerText || '';
              const m = body.match(/(恭喜获得[^\\n]{0,20}|已领取[^\\n]{0,20}|签到成功|今日已签到|签到完成)/);
              return m ? m[0] : null;
            })()""",
        )

    @staticmethod
    def _click_spin(bridge, tab) -> bool:
        """点击 .spin-button (转盘圆形按钮, 既是签到也是抽奖).

        实测: 真触发元素是 .spin-button 内含文字 "开始转动" 的 span,
              直接 click .spin-button 经常不生效, click "开始转动" 文字才触发 API.
        """
        return bridge.eval(
            tab,
            """(() => {
              const spin = document.querySelector('.spin-button, [class*=spin]');
              if (!spin) return false;
              if (spin.disabled) return false;
              // 优先: 在 spin 内找含 "开始" 文字的可点元素
              const inner = [...spin.querySelectorAll('*')].find(e =>
                e.children.length === 0 && /开始|转动|签到/.test((e.textContent || '').trim()));
              if (inner) { inner.click(); return true; }
              // fallback: 点 spin 自身
              spin.click();
              return true;
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
