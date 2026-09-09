"""站点列表刷新工具: 通过 CDP bridge 登录 ldoh 后, 拉 /api/sites 并合并到 sites.json。

用法:
    python -m dailycheckin.ldoh.refresh [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dailycheckin.ldoh.main import LOGIN_IS_CHECKIN_HOSTS_DEFAULT, RED_TAGS, SITES_FILE
from dailycheckin.utils.cdp_bridge import CDPBridge, UnreachableError

LDOH_URL = "https://ldoh.105117.xyz/"


def fetch_sites(bridge: CDPBridge, tab_id: str, ldoh_url: str) -> dict | None:
    """打开 ldoh, 触发 LinuxDo OAuth (若 Chrome 已登录则秒过), 拉 /api/sites。"""
    try:
        bridge.goto(tab_id, ldoh_url, 30000)
    except UnreachableError as e:
        print(f"ERROR ldoh 不可达: {e}", file=sys.stderr)
        return None
    bridge.wait(2500)
    # 验证登录
    user = bridge.eval(tab_id, "localStorage.getItem('user')")
    if not user or user == "null":
        print("WARN: Chrome 中 ldoh 未登录 (localStorage.user 为空), 请在浏览器里登录一次后重试", file=sys.stderr)
        return None
    # 拉 /api/sites
    res = bridge.fetch(tab_id, "/api/sites")
    if not res or res.get("status") != 200:
        print(f"ERROR /api/sites 返回 {res}", file=sys.stderr)
        return None
    return res["body"]


def main():
    p = argparse.ArgumentParser(description="刷新 ldoh 站点列表")
    p.add_argument("--ldoh-url", default=LDOH_URL)
    p.add_argument("--output", "-o", default=str(SITES_FILE))
    p.add_argument("--cdp-port", type=int, default=9333)
    args = p.parse_args()

    bridge = CDPBridge.start(port=args.cdp_port)
    try:
        tab_id = bridge.create_tab()
        try:
            payload = fetch_sites(bridge, tab_id, args.ldoh_url)
        finally:
            bridge.close_tab(tab_id)
    finally:
        bridge.quit()

    if not payload:
        sys.exit(1)

    raw_sites = payload.get("sites") or []
    LIC = set(LOGIN_IS_CHECKIN_HOSTS_DEFAULT)
    out = []
    for s in raw_sites:
        tags = set(s.get("tags") or [])
        dead = s.get("isRunaway") or s.get("isFakeCharity") or (tags & RED_TAGS)
        host = (s.get("apiBaseUrl") or "").split("//", 1)[1].split("/", 1)[0].lower().replace("www.", "")
        if not host:
            continue
        if dead:
            continue
        if not (s.get("supportsCheckin") or host in LIC):
            continue
        out.append({
            "name": s.get("name") or host,
            "apiBaseUrl": (s.get("apiBaseUrl") or "").rstrip("/"),
            "supportsCheckin": bool(s.get("supportsCheckin")),
            "checkinUrl": s.get("checkinUrl") or "",
            "isRunaway": bool(s.get("isRunaway")),
            "isFakeCharity": bool(s.get("isFakeCharity")),
            "tags": sorted(tags),
        })

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"sites": out, "_meta": {"source": args.ldoh_url, "updated": datetime.now().strftime("%Y-%m-%d")}},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(out)} sites -> {out_path}")


if __name__ == "__main__":
    main()