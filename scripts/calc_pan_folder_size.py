"""计算百度网盘指定文件夹总大小 (用 CDP 抓已登录 tab + fetch 文件列表 API)

用法: python3 scripts/calc_pan_folder_size.py [path]
  path: 百度网盘路径, 默认 "/裁判文书八量数据(已完成)"
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge, CDPError


def human(n: int) -> str:
    """数字 -> 人类可读 (B / KB / MB / GB / TB)"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def fetch_list(bridge, tab_id: str, path: str, page: int = 1) -> dict:
    """通过 CDP 页面 fetch 调用百度网盘 /api/list (带 cookie 自动认证)

    新版百度网盘 API 要求:
      - 必带 clienttype=0&app_id=250528&web=1
      - bdstoken 必须与 gettemplatevariable 在同一 fetch 上下文内获取 (一次性 token)
      - 否则返回 errno=-9 (误报为未登录)

    注意: Python urllib.parse.quote 不编码 () 等 unreserved 字符, 让 JS encodeURIComponent 处理.
    """
    import json as _json
    path_json = _json.dumps(path)
    js = f"""
        (async () => {{
          try {{
            const path = {path_json};
            // 先取 bdstoken (新版是一次性 token, 必须在同一 fetch chain 内使用)
            const tvRes = await fetch('/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=[%22bdstoken%22]', {{credentials: 'include'}});
            const tv = await tvRes.json();
            const bdstoken = (tv && tv.result && tv.result.bdstoken) || '';
            const params = 'dir=' + encodeURIComponent(path) + '&num=1000&page={page}&order=time&desc=1'
              + '&clienttype=0&app_id=250528&web=1'
              + (bdstoken ? '&bdstoken=' + encodeURIComponent(bdstoken) : '');
            const r = await fetch('/api/list?' + params, {{credentials: 'include'}});
            return JSON.stringify(await r.json());
          }} catch (e) {{
            return JSON.stringify({{err: String(e)}});
          }}
        }})()
    """
    raw = bridge.eval(tab_id, js, await_promise=True)
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def walk(bridge, tab_id: str, path: str, depth: int = 0, max_depth: int = 10):
    """递归遍历目录, 累加文件大小, 返回 (总字节, 文件数, 目录数, 详细列表)
    详细列表 [(path, size, is_dir)]
    """
    if depth > max_depth:
        return 0, 0, 0, [(path, -1, "max_depth")]
    resp = fetch_list(bridge, tab_id, path)
    if resp.get("err"):
        print(f"  {'  '*depth}ERROR {path}: {resp['err']}", flush=True)
        return 0, 0, 0, []
    if resp.get("errno") not in (0, None):
        print(f"  {'  '*depth}API_ERR {path}: errno={resp.get('errno')} err_msg={resp.get('err_msg') or resp.get('show_msg') or ''}", flush=True)
        return 0, 0, 0, []
    items = resp.get("list") or []
    total = 0
    file_count = 0
    dir_count = 0
    detail = []
    for it in items:
        name = it.get("server_filename", "?")
        size = int(it.get("size", 0))
        is_dir = int(it.get("isdir", 0)) == 1
        path_full = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
        if is_dir:
            dir_count += 1
            sub_total, sub_files, sub_dirs, sub_detail = walk(bridge, tab_id, path_full, depth + 1, max_depth)
            total += sub_total
            file_count += sub_files
            dir_count += sub_dirs
            detail.extend(sub_detail)
        else:
            file_count += 1
            total += size
            detail.append((path_full, size, False))
    return total, file_count, dir_count, detail


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/裁判文书八量数据(已完成)"
    print(f"Path: {path}", flush=True)
    print(f"Connecting to CDP 9333...", flush=True)
    try:
        bridge = CDPBridge.start(port=9333)
    except CDPError as e:
        print(f"ERROR: CDP start failed: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        tab_id = bridge.attach_by_url("https://pan.baidu.com")
        if not tab_id:
            for t in bridge.tabs():
                if "baidu.com" in t.get("url", ""):
                    tab_id = t["id"]
                    break
        if not tab_id:
            print("ERROR: 未找到 baidu.com tab", file=sys.stderr)
            sys.exit(2)

        # 刷新页面拿新 bdstoken (每次 fetch 都会消耗 bdstoken, 旧的可能已失效)
        try:
            bridge.goto(tab_id, "https://pan.baidu.com/disk/main#/index?path=/", 30000)
            bridge.wait(4000)
        except Exception:
            pass
        print(f"attached tab: {tab_id}", flush=True)
        href = bridge.eval(tab_id, "location.href")
        print(f"href: {href}", flush=True)

        t0 = time.time()
        total, files, dirs, detail = walk(bridge, tab_id, path)
        elapsed = time.time() - t0

        print(f"\n========== 汇总 ==========", flush=True)
        print(f"  总大小:   {human(total)} ({total:,} bytes)", flush=True)
        print(f"  文件数:   {files}", flush=True)
        print(f"  子目录数: {dirs}", flush=True)
        print(f"  耗时:     {elapsed:.1f}s", flush=True)

        if detail:
            print(f"\n========== 前 20 大文件 ==========", flush=True)
            for p, s, is_dir in sorted([(p, s, d) for p, s, d in detail if not d and s > 0], key=lambda x: -x[1])[:20]:
                print(f"  {human(s):<12} {p}", flush=True)
    finally:
        try:
            bridge.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
