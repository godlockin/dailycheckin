"""reload + inline test"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

b = CDPBridge.start(port=9333)
tab = b.attach_by_url("https://pan.baidu.com")

print("Reloading page...", flush=True)
try:
    b.goto(tab, "https://pan.baidu.com/disk/main#/index?path=/", 30000)
except Exception as e:
    print(f"reload failed: {e}", flush=True)
time.sleep(10)

print(f"href: {b.eval(tab, 'location.href')}", flush=True)

# 抓 bdstoken + 测 list
js = """
(async () => {
  try {
    const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {credentials: 'include'});
    const tv = await tvRes.json();
    const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
    const path = '/裁判文书全量数据(已完成)';
    const params = 'dir=' + encodeURIComponent(path) + '&num=10&order=time&desc=1'
      + '&clienttype=0&app_id=250528&web=1'
      + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
    const r = await fetch('/api/list?' + params, {credentials: 'include'});
    const text = await r.text();
    return JSON.stringify({
      bdstoken: bdstoken.slice(0,30),
      tv_body: JSON.stringify(tv).slice(0, 200),
      status: r.status,
      body: text.slice(0, 600)
    });
  } catch(e) { return 'ERR:' + e.message }
})()
"""

# 试 3 次
for i in range(3):
    print(f"\n=== try {i+1} ===", flush=True)
    print(b.eval(tab, js, await_promise=True), flush=True)
    time.sleep(3)

b.quit()
