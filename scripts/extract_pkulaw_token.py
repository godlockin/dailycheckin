"""抓 PKULAW token (wso2_token / wso2_refresh_token) + 写回 config/config.json。

PKULAW 用 wso2 模式: token 存在 localStorage.wso2_token (JSON 包裹),
refresh_token 存在 wso2_refresh_token。

跑法: 先在 Chrome 里登录 PKULAW, 然后 python3 scripts/extract_pkulaw_token.py [--apply]
  --apply  实际写 config.json (默认 dry-run, 只打印)
"""
import argparse
import json
import shutil
import sys
from base64 import b64decode
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycheckin.utils.cdp_bridge import CDPBridge

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"


def decode_jwt_payload(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写 config.json (默认 dry-run)")
    ap.add_argument("--port", type=int, default=9333)
    args = ap.parse_args()

    bridge = CDPBridge.start(port=args.port)
    try:
        tab = bridge.attach_by_url("https://mcp.pkulaw.com")
        if not tab:
            print("ERROR: 未找到 mcp.pkulaw.com tab", file=sys.stderr)
            sys.exit(1)
        print(f"attached tab {tab}, href={bridge.eval(tab, 'location.href')}\n", flush=True)

        wso2_token_raw = bridge.eval(tab, "localStorage.getItem('wso2_token')") or ""
        wso2_refresh_raw = bridge.eval(tab, "localStorage.getItem('wso2_refresh_token')") or ""

        if not wso2_token_raw or wso2_token_raw == "null":
            print("ERROR: wso2_token 为空 — PKULAW 未登录", file=sys.stderr)
            sys.exit(2)

        token_obj = json.loads(wso2_token_raw)
        access_token = token_obj.get("data") or ""
        refresh_obj = json.loads(wso2_refresh_raw) if wso2_refresh_raw and wso2_refresh_raw != "null" else {}
        refresh_token = refresh_obj.get("data") or ""

        access_payload = decode_jwt_payload(access_token) or {}
        username = access_payload.get("preferred_username", "phone_user")
        exp_ts = access_payload.get("exp", 0)
        exp_str = datetime.utcfromtimestamp(exp_ts).strftime("%Y-%m-%d %H:%M UTC") if exp_ts else "?"

        print("=== PKULAW token info ===", flush=True)
        print(f"  username:        {username}", flush=True)
        print(f"  access_token:    {access_token[:50]}...{access_token[-20:]}  (len={len(access_token)})", flush=True)
        print(f"  access exp:      {exp_str}", flush=True)
        print(f"  refresh_token:   {refresh_token[:50]}...{refresh_token[-20:]}  (len={len(refresh_token)})", flush=True)
        refresh_payload = decode_jwt_payload(refresh_token) or {}
        rexp = refresh_payload.get("exp")
        if rexp:
            print(f"  refresh exp:     {datetime.utcfromtimestamp(rexp).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)

        payload = {
            "name": "我的账号",
            "token": access_token,
            "refresh_token": refresh_token,
            "client_id": "wso2",
        }

        # 读 config.json
        if not CONFIG_PATH.exists():
            print(f"\nERROR: {CONFIG_PATH} 不存在", file=sys.stderr)
            sys.exit(3)

        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        existing = cfg.get("PKULAW", [])
        print(f"\nconfig.json 当前 PKULAW 段: {len(existing)} 个账号", flush=True)

        new_pkulaw = [payload]  # 替换为最新一个 (跟现有 dailycheckin 风格一致)
        cfg["PKULAW"] = new_pkulaw

        if not args.apply:
            print("\n[dry-run] 不写文件. 重跑加 --apply 实际更新.", flush=True)
            print(f"\n新 PKULAW 段 (可手工粘贴):", flush=True)
            print(json.dumps({"PKULAW": new_pkulaw}, ensure_ascii=False, indent=2), flush=True)
            return

        # backup + write
        backup = CONFIG_PATH.with_suffix(".json.bak")
        shutil.copy2(CONFIG_PATH, backup)
        print(f"\nbackup -> {backup}", flush=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"updated {CONFIG_PATH}", flush=True)
        print("✓ PKULAW token 已写入 config/config.json", flush=True)

    finally:
        bridge.quit()


if __name__ == "__main__":
    main()
