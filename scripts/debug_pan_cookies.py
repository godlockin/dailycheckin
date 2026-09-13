"""看 Chrome 9333 上 baidu.com 域的 cookie, 拿 BDUSS/sekey"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

cookies = b.send_cdp(tab, "Network.getCookies", {"urls": ["https://pan.baidu.com/", "https://pcs.baidu.com/"]})
print("=== baidu cookies ===", flush=True)
for c in (cookies or {}).get("cookies", []):
    name = c.get("name")
    val = c.get("value", "")
    domain = c.get("domain")
    print(f"  {domain:30s} {name:30s} len={len(val)} {val[:60]!r}", flush=True)

# 也拿一下页面 document.cookie
print("\n=== document.cookie ===", flush=True)
print(b.eval(tab, "document.cookie"), flush=True)

# 测试新 API 格式 (sekey)
print("\n=== /api/list with sekey ===", flush=True)
result = b.eval(
    tab,
    """(async () => {
      try {
        const cookies = document.cookie;
        const sekeyMatch = cookies.match(/sekey=([^;]+)/);
        const sekey = sekeyMatch ? sekeyMatch[1] : '';
        const path = '/裁判文书八量数据(已完成)';
        const url = '/api/list?dir=' + encodeURIComponent(path) + '&num=10&sekey=' + encodeURIComponent(sekey);
        const r = await fetch(url, {credentials: 'include'});
        return JSON.stringify({sekey: sekey.slice(0,30), status: r.status, body: (await r.text()).slice(0, 800)});
      } catch(e) { return 'ERR:' + e.message }
    })()""",
    await_promise=True,
)
print(result, flush=True)

b.quit()
