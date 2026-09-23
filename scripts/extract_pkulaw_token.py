"""抓 PKULAW token (wso2_token / wso2_refresh_token) + 写回 config/config.json。

PKULAW 用 wso2 模式: token 存在 localStorage.wso2_token (JSON 包裹),
refresh_token 存在 wso2_refresh_token。

跑法: 先在 Chrome 里登录 PKULAW, 然后 python3 scripts/extract_pkulaw_token.py [--apply]
  --apply  实际写 config.json (默认 dry-run, 只打印)
"""
import argparse
import json
import os
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
    ap.add_argument("--full", action="store_true", help="输出完整 token (默认 redact 前 30 字符)")
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
            print(f"\n新 PKULAW 段 (token 已 redact, 加 --full 看完整):", flush=True)
            redacted = json.loads(json.dumps(new_pkulaw))  # deep copy
            for acc in redacted:
                if "token" in acc:
                    acc["token"] = acc["token"][:30] + "...[REDACTED " + str(len(acc["token"])) + " chars]"
                if "refresh_token" in acc:
                    acc["refresh_token"] = acc["refresh_token"][:20] + "...[REDACTED]"
            print(json.dumps({"PKULAW": redacted}, ensure_ascii=False, indent=2), flush=True)
            if not args.full:
                print("\n  (注: 真实 token 已 redact. 加 --full 输出原始 token.)", flush=True)
            return

        # backup + write
        backup = CONFIG_PATH.with_suffix(".json.bak")
        shutil.copy2(CONFIG_PATH, backup)
        # backup 含完整 token, 同样收紧权限
        try:
            os.chmod(backup, 0o600)
        except OSError:
            pass
        # 轮转: 删除 7 天前的 .bak (每个备份日期是 daily-checkin 的命名, 保留最近 1 个)
        try:
            bak_dir = CONFIG_PATH.parent
            for old in sorted(bak_dir.glob("config.json.*.bak")):
                if old != backup and old.stat().st_mtime < (backup.stat().st_mtime - 7 * 86400):
                    old.unlink()
        except OSError:
            pass
        print(f"\nbackup -> {backup}", flush=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        # Token 是高敏感凭据: 600 (owner rw only), 避免被同机其他用户读
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError as e:
            print(f"warn: chmod 0600 失败 ({e}), 文件可能对其他用户可读", file=sys.stderr)
        print(f"updated {CONFIG_PATH} (mode 0600)", flush=True)
        print("✓ PKULAW token 已写入 config/config.json", flush=True)

    finally:
        bridge.quit()


if __name__ == "__main__":
    main()
