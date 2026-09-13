"""百度网盘 /api/list 加 X-Requested-With 头, 模拟 XHR fetch (反爬 bypass)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

js = """
(async () => {
  const path = '/裁判文书八量数据(已完成)';
  const url = '/api/list?dir=' + encodeURIComponent(path) + '&num=10&order=time&desc=1';
  const tries = [
    {name: 'xhr-fetch + xmlhttprequest', headers: {'X-Requested-With': 'XMLHttpRequest'}},
    {name: 'xhr-fetch + csrf + xmlhttprequest', headers: {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': (document.cookie.match(/csrfToken=([^;]+)/) || ['',''])[1]}},
    {name: 'xhr-fetch + csrf + ref', headers: {'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': (document.cookie.match(/csrfToken=([^;]+)/) || ['',''])[1], 'Referer': 'https://pan.baidu.com/disk/main'}},
  ];
  const out = [];
  for (const t of tries) {
    try {
      const r = await fetch(url, {credentials: 'include', headers: t.headers});
      out.push({name: t.name, status: r.status, body: (await r.text()).slice(0, 600)});
    } catch (e) { out.push({name: t.name, err: String(e)}); }
  }
  return JSON.stringify(out);
})()
"""
result = b.eval(tab, js, await_promise=True)
data = json.loads(result)
for r in data:
    print(f"=== {r['name']} ===", flush=True)
    print(f"  status: {r.get('status', '?')}", flush=True)
    print(f"  body: {r.get('body', r.get('err', '?'))}", flush=True)
    print(flush=True)

b.quit()
