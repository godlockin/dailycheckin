"""直接 inline 测 calc_pan_folder_size.fetch_list 的等价 JS"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge
from calc_pan_folder_size import fetch_list

# 测试 1: 直接 inline 跑 (旧方法 + bdstoken + clienttype 等)
b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

path = "/裁判文书八量数据(已完成)"
print(f"=== test 1: fetch_list({path!r}) ===")
result = fetch_list(b, tab, path, page=1)
print(f"  result: {json.dumps(result, ensure_ascii=False)[:500]}", flush=True)

# 测试 2: inline JS 直接对比
js = """
(async () => {
  try {
    const path = "/裁判文书八量数据(已完成)";
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {credentials: 'include'});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    const params = 'dir=' + encodeURIComponent(path) + '&num=10&order=time&desc=1'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {credentials: 'include'});
    return JSON.stringify({params, status: r.status, body: (await r.text()).slice(0, 500)});
  } catch(e) { return 'ERR:' + e.message }
})()
"""
print("\n=== test 2: inline JS same logic ===")
print(b.eval(tab, js, await_promise=True), flush=True)

# 测试 3: 用 hardcoded 用户原始 URL 编码
js3 = """
(async () => {
  try {
    const dir = '%2F%E8%A3%81%E5%88%A4%E6%96%87%E4%B9%A6%E5%85%AB%E9%87%8F%E6%95%B0%E6%8D%AE%EF%BC%88%E5%B7%B2%E5%AE%8C%E6%88%90%EF%BC%89';
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {credentials: 'include'});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    const params = 'dir=' + dir + '&num=10&order=time&desc=1'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {credentials: 'include'});
    return JSON.stringify({params, status: r.status, body: (await r.text()).slice(0, 300)});
  } catch(e) { return 'ERR:' + e.message }
})()
"""
print("\n=== test 3: hardcoded encoded dir ===")
print(b.eval(tab, js3, await_promise=True), flush=True)

b.quit()
