"""尝试不同 endpoint + headers 调百度网盘 list API"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

# 多 endpoint 多 headers 试探
js = """
(async () => {
  const path = '/裁判文书八量数据(已完成)';
  const csrfMatch = document.cookie.match(/csrfToken=([^;]+)/);
  const csrf = csrfMatch ? csrfMatch[1] : '';
  const sekeyMatch = document.cookie.match(/sekey=([^;]+)/);
  const sekey = sekeyMatch ? sekeyMatch[1] : '';

  const tries = [
    {name: '/api/list no headers', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10', headers: {}},
    {name: '/api/list with Referer', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10', headers: {Referer: 'https://pan.baidu.com/disk/main'}},
    {name: '/api/list with csrf', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10', headers: {'X-CSRF-Token': csrf}},
    {name: '/api/list with csrf+ref', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10', headers: {'X-CSRF-Token': csrf, Referer: 'https://pan.baidu.com/disk/main'}},
    {name: '/api/list with sekey', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10&sekey=' + encodeURIComponent(sekey), headers: {}},
    {name: '/api/list sekey+csrf+ref', url: '/api/list?dir=' + encodeURIComponent(path) + '&num=10&sekey=' + encodeURIComponent(sekey), headers: {'X-CSRF-Token': csrf, Referer: 'https://pan.baidu.com/disk/main'}},
    {name: '/rest/2.0/pcs/file', url: '/rest/2.0/pcs/file?method=list&dir=' + encodeURIComponent(path) + '&limit=10', headers: {}},
  ];

  const results = [];
  for (const t of tries) {
    try {
      const r = await fetch(t.url, {credentials: 'include', headers: t.headers});
      const text = await r.text();
      results.push({name: t.name, status: r.status, body: text.slice(0, 400)});
    } catch (e) { results.push({name: t.name, err: String(e)}); }
  }
  return JSON.stringify({csrf, sekey, results});
})()
"""
result = b.eval(tab, js, await_promise=True)
data = json.loads(result)
print(f"csrf: {data['csrf'][:20]}", flush=True)
print(f"sekey: {data['sekey'][:30]}", flush=True)
print(flush=True)
for r in data['results']:
    print(f"=== {r['name']} ===", flush=True)
    print(f"  status: {r.get('status', '?')}", flush=True)
    print(f"  body: {r.get('body', r.get('err', '?'))}", flush=True)
    print(flush=True)

b.quit()
