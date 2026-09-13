"""inline 测试 fetch_list 的 JS"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

# 完全复刻 fetch_list 内的 JS, 但用 raw response 不 r.json()
path = "/裁判文书八量数据(已完成)"
import json as _json
path_json = _json.dumps(path)

js = f"""
(async () => {{
  try {{
    const path = {path_json};
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    const params = 'dir=' + encodeURIComponent(path) + '&num=10&page=1&order=time&desc=1'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
    const text = await r.text();
    return JSON.stringify({{
      tv_status: tvRes.status,
      bdstoken: bdstoken,
      params: params,
      r_status: r.status,
      body: text.slice(0, 500)
    }});
  }} catch(e) {{ return 'ERR:' + e.message }}
}})()
"""
print(b.eval(tab, js, await_promise=True), flush=True)

# 也试不用 bdstoken 看 errno 是什么 (确认 bdstoken 是必需的)
js2 = f"""
(async () => {{
  const r = await fetch('/api/list?dir=' + encodeURIComponent('/裁判文书八量数据(已完成)') + '&num=10&clienttype=0&app_id=250528&web=1', {{credentials: 'include'}});
  return JSON.stringify({{status: r.status, body: (await r.text()).slice(0, 200)}});
}})()
"""
print("\n=== without bdstoken ===", flush=True)
print(b.eval(tab, js2, await_promise=True), flush=True)

# 试 用当前页面 hash route 的 path
js3 = f"""
(async () => {{
  try {{
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    // 试空 dir (root)
    const params = 'dir=' + encodeURIComponent('/') + '&num=10'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
    return JSON.stringify({{status: r.status, body: (await r.text()).slice(0, 300)}});
  }} catch(e) {{ return 'ERR:' + e.message }}
}})()
"""
print("\n=== root / ===", flush=True)
print(b.eval(tab, js3, await_promise=True), flush=True)

b.quit()
