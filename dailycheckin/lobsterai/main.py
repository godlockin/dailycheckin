"""有道 LobsterAI 每日签到（+100 积分/号/天）。

认证: 鉴权用 cookie `lobsterai_web_session` (从已登录 Chrome profile 抓取), 非 Bearer token.
  字段名 access_token 保持向后兼容 (用户原 standalone 脚本用此名), 实际值 = session cookie.

配置 (config.json):
  "LOBSTERAI": [
    {
      "name": "我的账号",
      "uid": "13122249996",                     // 仅用于日志/显示
      "access_token": "rajGgiAgDPdkK3oD_BVH..." // 值 = lobsterai_web_session cookie
    }
  ]
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
import uuid
from typing import Any

from dailycheckin import CheckIn

logger = logging.getLogger("dailycheckin.lobsterai")

BASE = "https://lobsterai-server.youdao.com"
UPDATE_API = "https://api-overmind.youdao.com/openapi/get/luna/hardware/lobsterai/prod/update"


def _version_key(v: str):
    m = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-[0-9A-Za-z.-]+)?", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _resolve_client_version() -> str:
    with urllib.request.urlopen(UPDATE_API, timeout=15) as r:
        v = json.loads(r.read())["data"]["value"]["version"]
    if not _version_key(v):
        raise RuntimeError(f"官方更新接口返回的版本格式异常: {v!r}")
    return v


def _api(method: str, path: str, sess: str, body: dict | None = None) -> dict:
    """鉴权: Cookie: lobsterai_web_session=<sess> (无 Authorization 头 — 服务端忽略, 用 cookie 鉴权)."""
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Cookie": f"lobsterai_web_session={sess}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "LobsterAI/" + _CLIENT_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    if d.get("code") != 0:
        raise RuntimeError(f"code={d.get('code')} msg={d.get('message') or d.get('msg')}")
    if not isinstance(d.get("data"), dict):
        raise RuntimeError("data 为空（session cookie 可能已失效）")
    return d["data"]


# 模块级版本缓存 (避免每个账号重复请求官方更新接口)
_CLIENT_VERSION: str | None = None


class LobsterAI(CheckIn):
    name = "有道 LobsterAI"

    def __init__(self, check_item: dict[str, Any] | None = None):
        self.account = check_item or {}
        # access_token 字段实际值 = lobsterai_web_session cookie 字符串
        self.uid = str(self.account.get("uid") or "")
        self.session = (self.account.get("access_token") or "").strip()

    def main(self) -> str:
        global _CLIENT_VERSION
        name = self.account.get("name") or "lobsterai"
        rec: dict[str, Any] = {"name": name}

        if not self.session:
            rec["status"] = "skipped"
            rec["message"] = "config 缺 access_token (lobsterai_web_session cookie 值)"
            return self._format([rec])

        # 1. 解析 clientVersion (模块级缓存, 全天只请求 1 次)
        if _CLIENT_VERSION is None:
            try:
                _CLIENT_VERSION = _resolve_client_version()
            except Exception as e:
                rec["status"] = "error"
                rec["message"] = f"解析 clientVersion 失败: {e}"
                return self._format([rec])

        # 2. 签到流程
        try:
            msg, gained = self._do_checkin()
            rec["checkin"] = "ok" if "成功" in msg else "already" if "已签到" in msg else "no_slot"
            rec["message"] = msg
            if gained is not None:
                rec["reward"] = f"+{gained:g}"
        except Exception as e:
            msg = str(e)
            if "data 为空" in msg or "code=51102" in msg or "请先登录" in msg or "session cookie 可能已失效" in msg:
                rec["status"] = "login_failed"
            else:
                rec["status"] = "failed"
            rec["message"] = msg
            return self._format([rec])

        rec["status"] = "ok"
        return self._format([rec])

    def _do_checkin(self) -> tuple[str, float | None]:
        """返回 (状态描述, 获得积分). 抛异常 = 失败."""
        q = (
            f"placement=desktop_sidebar&clientVersion={_CLIENT_VERSION}"
            f"&containerApiVersion=2&platform=win32"
        )
        slot = _api("GET", f"/api/client-activities/slot?{q}", self.session)
        if slot.get("slotState") != "available" or not slot.get("activity"):
            return f"无可用活动 (slotState={slot.get('slotState')!r})", None
        code = slot["activity"]["activityCode"]
        rev = slot["activity"]["configRevision"]
        ctx = _api(
            "GET",
            f"/api/client-activities/{code}/context?configRevision={rev}",
            self.session,
        )
        if ctx["state"].get("claimedToday") or "check_in" not in (ctx.get("actions") or []):
            return "今天已签到, 跳过", None
        res = _api(
            "POST",
            f"/api/client-activities/{code}/actions/check_in",
            self.session,
            {
                "configRevision": rev,
                "idempotencyKey": str(uuid.uuid4()),
                "payload": {},
            },
        )
        result = res.get("result") or {}
        gained = next(
            (
                result[k]
                for k in ("creditsGranted", "rewardCredits", "credits")
                if isinstance(result.get(k), (int, float))
            ),
            None,
        )
        return "签到成功", gained

    def _format(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        body = (
            f"「有道 LobsterAI 签到」\n"
            f"总数 {len(results)} | 成功 {ok} | 失败 {failed} | 跳过 {skipped}\n"
        )
        body += "\n".join(
            f"{r.get('status', '?'):<14} {r.get('name', '?'):<14} "
            f"签到:{r.get('checkin', '-')} 奖励:{r.get('reward', '-')} {r.get('message', '')[:50]}"
            for r in results
        )
        return body


if __name__ == "__main__":
    import sys

    cfg = {}
    if not sys.stdin.istty():
        raw = sys.stdin.read() or "{}"
        cfg = json.loads(raw)
    elif len(sys.argv) > 1:
        cfg = json.loads(sys.argv[1])
    print(LobsterAI(cfg).main())
