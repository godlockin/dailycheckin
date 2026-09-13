"""用 CDP 打开 LDOH 几个代表站, 抓 title / href / body 摘要, 让用户了解实际页面"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

SITES = [
    ("Any Router", "https://anyrouter.top/", "登录即签到 (实战可跑)"),
    ("42公益站", "https://api.42w.shop/", "linuxdo_checkin 能跑, dailycheckin 跑不通"),
    ("Huan API", "https://ai.huan666.de/", "linuxdo_checkin 能跑, dailycheckin 跑不通"),
    ("ArkAPI", "https://windhub.cc/", "多模型 + Claude Code + 翻译"),
    ("雨落千息", "https://platform.rainflowtb.com/", "纯国模, 可LDC支付"),
]


def main():
    bridge = CDPBridge.start(port=9333)
    try:
        for name, url, note in SITES:
            tab_id = bridge.create_tab(url)
            bridge.wait(3500)
            print(f"\n=== {name} ===")
            print(f"  note:     {note}")
            print(f"  url:      {url}")
            try:
                href = bridge.eval(tab_id, "location.href")
                print(f"  href:     {href}")
                title = bridge.eval(tab_id, "document.title")
                print(f"  title:    {title}")
                body = bridge.eval(tab_id, "(document.body.innerText || '').slice(0, 400)")
                print(f"  body[:400]:")
                for line in body.splitlines():
                    if line.strip():
                        print(f"    {line.strip()[:120]}")
                # 探测登录入口
                has_linuxdo = bridge.eval(
                    tab_id,
                    "[...document.querySelectorAll('button,a,[role=button]')]"
                    ".some(e => /linux|linuxdo/i.test((e.textContent||'')+(e.getAttribute('href')||'')))"
                )
                print(f"  hasLinuxDoButton: {has_linuxdo}")
            except Exception as e:
                print(f"  ERROR: {type(e).__name__}: {e}")
            bridge.close_tab(tab_id)
    finally:
        bridge.quit()


if __name__ == "__main__":
    main()
