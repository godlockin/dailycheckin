"""让 SPA 真正定位到目标文件夹 (用 hashchange / location.assign), 然后 fetch list"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

# 1. 通过 location.hash 设置路径 (触发 SPA 内部路由)
folder = "/裁判文书八量数据(已完成)"
encoded = "%2F%E8%A3%81%E5%88%A4%E6%96%87%E4%B9%A6%E5%85%A8%E9%87%8F%E6%95%B0%E6%8D%AE%EF%BC%88%E5%B7%B2%E5%AE%8C%E6%88%90%EF%BC%89"

print("Setting hash to trigger SPA route...", flush=True)
b.eval(tab, f"window.location.hash = '#/index?category=all&path={encoded}'")
b.wait(8000)

# 2. 用 bdstoken 调新版 /api/list (clienttype=0&app_id=250528&web=1)
result = b.eval(
    tab,
    f"""
    (async () => {{
      try {{
        // 拿 bdstoken (新版 gettemplatevariable)
        const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
        const tv = await tvRes.json();
        const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
        const params = 'dir={encoded}&num=10&order=time&desc=1' +
                       '&clienttype=0&app_id=250528&web=1' +
                       (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
        const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
        const text = await r.text();
        return JSON.stringify({{bdstoken: bdstoken.slice(0,20), status: r.status, body: text.slice(0, 1000)}});
      }} catch(e) {{ return 'ERR:' + e.message }}
    }})()
    """,
    await_promise=True,
)
print(result, flush=True)

# 3. 试新版不同 endpoint
for ep in [
    "/api/file/list",
    "/api/rapid/list",
    "/api/folder/list",
    "/api/filemetas",
    "/api/filemanager",
]:
    res = b.eval(
        tab,
        f"""
        (async () => {{
          try {{
            const r = await fetch('{ep}?path={encoded}&clienttype=0', {{credentials: 'include'}});
            return JSON.stringify({{ep: '{ep}', status: r.status, body: (await r.text()).slice(0, 200)}});
          }} catch(e) {{ return JSON.stringify({{ep: '{ep}', err: String(e)}}) }}
        }})()
        """,
        await_promise=True,
    )
    print(f"\n=== {ep} ===", flush=True)
    print(res, flush=True)

b.quit()
