"""直接 debug pan.baidu.com /api/list 响应"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")
print(f"tab: {tab}", flush=True)
print(f"href: {b.eval(tab, 'location.href')}", flush=True)

# 1. 测试 bdstoken API
print("\n=== /api/bdstoken ===", flush=True)
tk_raw = b.eval(
    tab,
    """(async () => {
      try {
        const r = await fetch('/api/bdstoken', {credentials: 'include'});
        return JSON.stringify({status: r.status, body: await r.text()});
      } catch(e) { return 'ERR:' + e.message }
    })()""",
    await_promise=True,
)
print(tk_raw, flush=True)

# 2. 测试 /api/list 直接调用(无 bdstoken)
print("\n=== /api/list without bdstoken ===", flush=True)
list_raw = b.eval(
    tab,
    """(async () => {
      try {
        const path = '/裁判文书八量数据(已完成)';
        const r = await fetch('/api/list?dir=' + encodeURIComponent(path) + '&num=100', {credentials: 'include'});
        return JSON.stringify({status: r.status, body: (await r.text()).slice(0, 500)});
      } catch(e) { return 'ERR:' + e.message }
    })()""",
    await_promise=True,
)
print(list_raw, flush=True)

# 3. 测试 /api/list WITH bdstoken
print("\n=== /api/list with bdstoken ===", flush=True)
list_raw2 = b.eval(
    tab,
    """(async () => {
      try {
        const tk = await fetch('/api/bdstoken', {credentials: 'include'});
        const tkj = await tk.json();
        const bdstoken = (tkj.disk || '');
        const path = '/裁判文书八量数据(已完成)';
        const url = '/api/list?dir=' + encodeURIComponent(path) + '&num=100&bdstoken=' + encodeURIComponent(bdstoken);
        const r = await fetch(url, {credentials: 'include'});
        return JSON.stringify({
          bdstoken: bdstoken,
          status: r.status,
          body: (await r.text()).slice(0, 500)
        });
      } catch(e) { return 'ERR:' + e.message }
    })()""",
    await_promise=True,
)
print(list_raw2, flush=True)

b.quit()
