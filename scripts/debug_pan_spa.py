"""用 CDP 监听 Network.requestWillBeSent, 让 SPA 加载文件夹, 抓真实 list API"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

# 1. enable Network domain
b.send_cdp(tab, "Network.enable")
print("Network.enable OK", flush=True)

# 2. capture requestWillBeSent events by overriding WebSocket message handling
# 简化: 不用 events, 直接 navigate 后用 performance API 抓最近 fetch url

# 3. navigate to folder (let SPA load and call list)
folder_path = "/裁判文书八量数据(已完成)"
folder_url = "https://pan.baidu.com/disk/main#/index?path=" + folder_path
print(f"navigating to {folder_url}", flush=True)
b.goto(tab, folder_url, 30000)
b.wait(8000)  # 给 SPA 时间 fetch + render

# 4. 抓 performance entries (fetch + xhr)
print("\n=== fetch/xhr URLs from performance ===", flush=True)
entries = b.eval(
    tab,
    """(JSON.stringify(performance.getEntriesByType('resource')
      .filter(e => e.name.includes('/api/') || e.name.includes('pan.baidu.com'))
      .map(e => ({name: e.name, dur: Math.round(e.duration)}))))""",
    await_promise=True,
)
print(entries[:2000], flush=True)

# 5. 抓页面全局变量 / 缓存数据 (vue/react store)
print("\n=== window 全局 (pan.baidu.com SPA 状态) ===", flush=True)
keys = b.eval(
    tab,
    """JSON.stringify(Object.keys(window).filter(k => /^(__|pan|baidu|app|store|vue)/i.test(k)).slice(0,30))""",
)
print(keys, flush=True)

# 6. 抓最近一次 fetch response - 用浏览器内部缓存可能不行, 试 page.content 看有没有数据
content = b.eval(tab, "document.body.innerText.length")
print(f"\nbody length: {content}", flush=True)

# 7. 找页面里的文件数量显示
files_text = b.eval(
    tab,
    r"""JSON.stringify({
      title: document.title,
      h1: [...document.querySelectorAll('h1,h2')].map(e => e.innerText).slice(0,5),
      spans: [...document.querySelectorAll('span')].filter(e => /\d+/.test(e.innerText || '') && e.innerText.length < 30).map(e => e.innerText).slice(0, 20),
    })""",
)
print(f"\npage text: {files_text}", flush=True)

b.quit()
