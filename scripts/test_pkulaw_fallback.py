"""测试 PKULAW fallback 链路, 不修改 config.json。

策略:
  1. 用 Python 构造过期 JWT token (decode 不验签, 任意签名都行)
  2. 用真实 refresh_token (从 config.json 读但不修改)
  3. 调 Pkulaw._try_refresh -> 应先试 refresh_token grant 失败 -> fallback 到 CDP
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"


def fake_expired_token() -> str:
    """构造一个过期 JWT, decode 不验签所以签名无关"""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT", "kid": "fake"}, separators=(",", ":")).encode()
    ).rstrip(b"=").decode("ascii")
    payload = json.dumps({
        "exp": int(time.time()) - 3600,
        "iat": int(time.time()) - 7200,
        "iss": "https://cas.pkulaw.com/auth/realms/fabao",
        "preferred_username": "test_user",
    }, separators=(",", ":"))
    pl = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")
    return f"{header}.{pl}.fakesignature"


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    real_account = cfg["PKULAW"][0]
    test_account = {
        "name": "test",
        "token": fake_expired_token(),
        # 用真 refresh_token — 但预期 refresh_token grant 失败 (unauthorized_client)
        "refresh_token": real_account.get("refresh_token", "fake_refresh"),
        "client_id": "wso2",
    }

    from dailycheckin.pkulaw.main import Pkulaw
    p = Pkulaw(test_account)

    print("=== Step 1: Pkulaw._try_refresh ===", flush=True)
    print("  期望: refresh_token grant 失败 (wso2 confidential client) -> fallback CDP", flush=True)
    result = p._try_refresh(test_account)
    if result:
        print(f"  ✓ result.access_token len = {len(result.get('access_token', ''))}", flush=True)
        print(f"  ✓ result.refresh_token len = {len(result.get('refresh_token', ''))}", flush=True)
        print(f"  ✓ CDP fallback 成功!", flush=True)
    else:
        print(f"  ✗ 返回 None (CDP fallback 也失败 — 可能 Chrome 里 mcp.pkulaw.com 未登录)", flush=True)
        print(f"    验证: 在 9333 Chrome 里访问 mcp.pkulaw.com/console/points 看是否要重新登录", flush=True)

    print("\n=== Step 2: Pkulaw.main() (完整流程, 用过期 token + 真 refresh_token + 真 CDP) ===", flush=True)
    # 把 test_account 装回 instance, 让 main() 用我们构造的过期 token
    p.account = test_account
    out = p.main()
    print(out, flush=True)
    print("\n  上面输出 '✅' 行: PKULAW 签到成功 (走 CDP 拿到了有效 token)", flush=True)


if __name__ == "__main__":
    main()
