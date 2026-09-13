"""完全复刻 debug_pan_spa2.py 的成功调用"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

# 完全用 debug_pan_spa2.py 第一段的代码 + encoded 字符串
encoded = "%2F%E8%A3%81%E5%88%A4%E6%96%87%E4%B9%A6%E5%85%AB%E9%87%8F%E6%95%B0%E6%8D%AE%EF%BC%88%E5%B7%B2%E5%AE%8C%E6%88%90%EF%BC%89"

result = b.eval(
    tab,
    f"""
    (async () => {{
      try {{
        const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
        const tv = await tvRes.json();
        const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
        const params = 'dir={encoded}&num=10&order=time&desc=1'
          + '&clienttype=0&app_id=250528&web=1'
          + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
        const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
        const text = await r.text();
        return JSON.stringify({{params, bdstoken, status: r.status, body: text.slice(0, 800)}});
      }} catch (e) {{ return 'ERR:' + e.message }}
    }})()
    """,
    await_promise=True,
)
data = json.loads(result)
print(f"params: {data['params']}", flush=True)
print(f"bdstoken: {data['bdstoken']}", flush=True)
print(f"status: {data['status']}", flush=True)
print(f"body: {data['body']}", flush=True)

b.quit()
