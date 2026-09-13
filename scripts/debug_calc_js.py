"""debug calc_pan_folder_size 生成的 JS"""
from urllib.parse import quote

path = "/裁判文书八量数据(已完成)"
encoded_path = quote(path, safe="/")
print(f"encoded_path: {encoded_path}")

js = f"""
(async () => {{
  try {{
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    const params = 'dir={encoded_path}&num=1000&page=1&order=time&desc=1'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
    return JSON.stringify({{url: '/api/list?' + params, body: (await r.text()).slice(0, 500)}});
  }} catch (e) {{ return 'ERR:' + e.message }}
}})()
"""
print("=== generated JS ===")
print(js)

# 也 inline 跑测试
import sys
sys.path.insert(0, '/Users/chenchen/working/sourcecode/tools/dev-tools/dailycheckin')
from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")
result = b.eval(tab, js, await_promise=True)
print("\n=== result ===")
print(result)
b.quit()
