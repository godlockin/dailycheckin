"""深度探测: 打开站点 -> probe -> 点 Sign in -> 抓 HTML 看实际弹窗内容"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

SITES = [
    ("42公益站", "https://api.42w.shop/"),
    ("Huan API", "https://ai.huan666.de/"),
    ("ArkAPI", "https://windhub.cc/"),
    ("月城公益站", "https://52ccl.net/"),
]


def main():
    bridge = CDPBridge.start(port=9333)
    try:
        for name, url in SITES:
            print(f"\n========== {name} ({url}) ==========", flush=True)
            tab_id = bridge.create_tab(url)
            bridge.wait(3500)

            # 第一次 probe
            probe = bridge.eval(
                tab_id,
                r"""(() => {
                  const items = [...document.querySelectorAll('button, a, [role=button], .btn, input[type=submit]')]
                    .map(e => ({tag:e.tagName, text:(e.textContent||e.value||'').trim().slice(0,40), href:e.getAttribute('href')||'', disabled:!!e.disabled}));
                  return { url: location.href, title: document.title, count: items.length, items: items.slice(0, 30) };
                })()""",
            )
            print(f"  [初始页面] {probe.get('url')}", flush=True)
            print(f"  title: {probe.get('title')}", flush=True)
            print(f"  buttons/links: {probe.get('count')} (前 30):", flush=True)
            for it in probe.get("items", []):
                print(f"    {it['tag']:<8} text={it['text'][:30]!r:32} href={it['href'][:50]!r}", flush=True)

            # 找 Sign in / Login 按钮
            signin_btn = bridge.eval(
                tab_id,
                r"""(() => {
                  const els = [...document.querySelectorAll('button, a, [role=button], .btn, input[type=submit]')];
                  const re = /^(sign in|sign-in|login|登 录|登录|登入)$/i;
                  const found = els.find(e => re.test((e.textContent||e.value||'').trim()) && !e.disabled);
                  return found ? {tag:found.tagName, text:(found.textContent||found.value||'').trim(), href:found.getAttribute('href')||''} : null;
                })()""",
            )
            if not signin_btn:
                print(f"  无 Sign in 按钮, 跳过", flush=True)
                bridge.close_tab(tab_id)
                continue

            print(f"  找到 Sign in: {signin_btn}", flush=True)
            click = bridge.click_text(tab_id, signin_btn["text"])
            print(f"  click_text: {click}", flush=True)
            bridge.wait(4000)

            # 第二次 probe (Sign in 展开后)
            probe2 = bridge.eval(
                tab_id,
                r"""(() => {
                  const items = [...document.querySelectorAll('button, a, [role=button], .btn, input[type=submit]')]
                    .map(e => ({tag:e.tagName, text:(e.textContent||e.value||'').trim().slice(0,50), href:e.getAttribute('href')||'', disabled:!!e.disabled}));
                  return { url: location.href, count: items.length, items: items.slice(0, 60) };
                })()""",
            )
            print(f"  [展开后] {probe2.get('url')}", flush=True)
            print(f"  buttons/links: {probe2.get('count')} (前 60):", flush=True)
            for it in probe2.get("items", []):
                # 高亮含 linux/oauth/auth 的
                marker = ""
                if "linux" in it["text"].lower() or "linux" in it["href"].lower():
                    marker = " ⭐LinuxDo?"
                if "oauth" in it["text"].lower() or "oauth" in it["href"].lower():
                    marker += " ⭐OAuth?"
                print(f"    {it['tag']:<8} text={it['text'][:40]!r:42} href={it['href'][:60]!r}{marker}", flush=True)

            # 看 body 看是否有 LinuxDo 字样
            body_check = bridge.eval(
                tab_id,
                r"""(() => {
                  const text = (document.body.innerText || '');
                  const linuxMatch = text.match(/.{0,30}linux.{0,30}/gi) || [];
                  return linuxMatch.slice(0, 5);
                })()""",
            )
            print(f"  body 中 'linux' 上下文: {body_check}", flush=True)

            bridge.close_tab(tab_id)
    finally:
        bridge.quit()


if __name__ == "__main__":
    main()
