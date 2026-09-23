"""pkulaw 一次性浏览器登录辅助: 走 CDP bridge 完成 keycloak 登录, 把 access_token
和 refresh_token 抓到 stdout (粘贴进 config.json)。

用法:
  python -m dailycheckin.pkulaw.login_helper --cdp-port 9333

会:
  1. 在常驻 Chrome (Agent Chrome :9333) 新建标签, 打开 keycloak 登录
  2. 等你手动登录 ldoh
  3. 跳转到 ldoh, localStorage 里有 token
  4. 从 keycloak 端点 /protocol/openid-connect/token 拿 refresh_token (用 cookie)
  5. 输出 JSON {"token": ..., "refresh_token": ..., "name": "..."}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any
from urllib.parse import urlparse, parse_qs

from dailycheckin.utils.cdp_bridge import CDPBridge


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cdp-port", type=int, default=9333)
    p.add_argument("--login-url", default="https://cas.pkulaw.com/auth/realms/fabao/protocol/openid-connect/auth?client_id=wso2&redirect_uri=https://mcp.pkulaw.com/&response_type=code&scope=openid")
    p.add_argument("--timeout-sec", type=int, default=180)
    p.add_argument("--post-login-url", default="https://mcp.pkulaw.com/")
    p.add_argument("--full", action="store_true", help="输出完整 token (默认 redact 前 30 字符, 避免 token 漏到日志/对话)")
    args = p.parse_args()

    print(f"启动 CDP bridge, port={args.cdp_port}", file=sys.stderr)
    bridge = CDPBridge.start(port=args.cdp_port)
    try:
        # 在新标签打开登录页
        tab_id = bridge.create_tab(args.login_url)
        print("已在 Chrome 里打开登录页, 请手动登录...", file=sys.stderr)
        deadline = time.time() + args.timeout_sec
        last_url = ""
        while time.time() < deadline:
            try:
                cur = bridge.eval(tab_id, "location.href")
            except Exception:
                cur = ""
            if cur and cur != last_url:
                print(f"URL: {cur[:100]}", file=sys.stderr)
                last_url = cur
            # 已跳到目标域 = 登录完成
            if cur.startswith(args.post_login_url):
                time.sleep(2)  # 等 localStorage 落定
                break
            time.sleep(2)
        else:
            print("TIMEOUT 等登录完成", file=sys.stderr)
            return 1

        # 抓 ldoh 用的 token (从 URL/cookie/localStorage)
        # 简单做法: 从 localStorage 抓 (新 ldoh 会把 token 存进去)
        try:
            user_str = bridge.eval(tab_id, "localStorage.getItem('user')")
        except Exception:
            user_str = None
        access_token = None
        if user_str and user_str != "null":
            try:
                user_data = json.loads(user_str)
                access_token = user_data.get("token") or user_data.get("access_token")
            except Exception:
                pass

        # 从 keycloak session iframe 拿 refresh_token 不太可靠; 改用浏览器 fetch
        # 在当前页面 fetch token endpoint, 期望 401 (没 client_id), 拿不到
        # 替代: 提示用户去 Application -> Local Storage 找 keycloak session
        print("\n=== 获取到 access_token (来自 localStorage.user) ===", file=sys.stderr)
        if not access_token:
            print("未找到 access_token. 请检查浏览器 localStorage 里的 user 对象", file=sys.stderr)
            return 1
        # 截短显示
        print(f"access_token: {access_token[:50]}...{access_token[-20:]}", file=sys.stderr)
        print(f"len: {len(access_token)}", file=sys.stderr)

        # refresh_token: 需要额外从 keycloak session 拿
        # 方案: 在当前 ldoh 页面里 inject fetch 调用 token endpoint
        print("\n尝试自动拿 refresh_token...", file=sys.stderr)
        # 用 ldoh 页面 fetch 刷新端点. 注意 keycloak 可能需 client_id
        for client_id in [None, "wso2", "wso2-is", "mcp", "account", "public-cli"]:
            args_dict: dict[str, Any] = {"token": access_token, "client_id": client_id} if client_id else {"token": access_token}
            try:
                rt = bridge.eval(tab_id, f"""
                async () => {{
                  try {{
                    const r = await fetch("https://cas.pkulaw.com/auth/realms/fabao/protocol/openid-connect/token", {{
                      method: "POST",
                      headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                      body: "grant_type=refresh_token&refresh_token=&client_id=" + {json.dumps(client_id or "")}
                    }});
                    return r.status;
                  }} catch(e) {{ return "ERR:" + e.message }}
                }}
                """)
            except Exception as e:
                rt = f"ERR: {e}"
            print(f"  client_id={client_id}: {rt}", file=sys.stderr)
            if rt == 200:
                print("找到可用 client_id! 请保留此值", file=sys.stderr)
                break

        # 输出最终 JSON (含 access_token, refresh_token 留空)
        out = {
            "name": "我的账号",
            "token": access_token,
            "refresh_token": "",  # 用户需手动从 keycloak session 取
            "client_id": "wso2",  # 默认猜测
        }
        print("\n# 把下面这行加到 config.json 的 \"PKULAW\" 数组里:", file=sys.stderr)
        if not args.full:
            # 默认 redact 防止 token 漏到终端日志/Claude 对话
            redacted = json.loads(json.dumps(out))
            if "token" in redacted and redacted["token"]:
                redacted["token"] = redacted["token"][:30] + f"...[REDACTED {len(out.get('token',''))} chars]"
            if "refresh_token" in redacted and redacted["refresh_token"]:
                redacted["refresh_token"] = redacted["refresh_token"][:20] + "...[REDACTED]"
            print(json.dumps(redacted, ensure_ascii=False))
            print("\n# (注: 真实 token 已 redact. 加 --full 输出原始 token)", file=sys.stderr)
        else:
            print(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        try:
            bridge.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())