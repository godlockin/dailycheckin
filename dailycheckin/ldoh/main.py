"""LD OPEN HUB (ldoh.105117.xyz) 公益站自动签到模块。

通过 Node CDP sidecar (utils/cdp_bridge.py) 驱动常驻 Chrome, 复用
该 Chrome 的 LinuxDo 会话, 对 ldoh 聚合的 new-api 公益站逐个 OAuth
登录 + 签到。

与现有 platform 模块 (bilibili, tieba 等) 风格保持一致:
  - 继承 CheckIn 基类
  - main(check_item) 返回签到结果字符串
  - 由 dailycheckin/configs.py 通过类名自动发现
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dailycheckin import CheckIn
from dailycheckin.utils.cdp_bridge import CDPBridge, UnreachableError
from dailycheckin.utils.newapi import fetch_self, get_user_id, try_checkin

logger = logging.getLogger("dailycheckin.ldoh")

DEFAULT_LDOH_URL = "https://ldoh.105117.xyz/"
SITES_FILE = Path(__file__).parent / "sites.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # ldoh/ -> dailycheckin/ -> root/

# 标红 tag → 视为跑路/不可用
RED_TAGS = {"无法使用", "无法访问", "无法登录", "已无法LD登录", "无法签到"}
DEAD_STATUS_TOKENS = {"down", "dead", "offline", "red", "error", "disabled", "blocked"}
# 登录即签到的站点 (无需 API 签到端点)
LOGIN_IS_CHECKIN_HOSTS_DEFAULT = ("ps.air-outer.com", "anyrouter.top")


class LdohCheckIn(CheckIn):
    name = "LD OPEN HUB"

    def __init__(self, check_item: dict | None = None):
        self.check_item = check_item or {}
        self.ldoh_url = self.check_item.get("ldoh_url") or DEFAULT_LDOH_URL
        self.exclude = set(self.check_item.get("exclude_domains") or [])
        # 自动加载 data/skip_sites.json (熔断: 同站连续失败 2 次跳过)
        skip_path = Path(self.check_item.get("skip_sites_file") or PROJECT_ROOT / "data" / "skip_sites.json")
        try:
            if skip_path.exists():
                skip = json.loads(skip_path.read_text(encoding="utf-8"))
                self.exclude |= {h for h, n in (skip or {}).items() if (n or 0) >= 2}
        except Exception as e:
            print(f"WARN: 读 skip_sites.json 失败: {e}")
        self.max_sites = int(self.check_item.get("max_sites") or 0)
        self.refresh_on_start = bool(self.check_item.get("refresh_on_start"))
        self.login_is_checkin = set(
            self.check_item.get("login_is_checkin") or list(LOGIN_IS_CHECKIN_HOSTS_DEFAULT)
        )
        self.sites_file = Path(self.check_item.get("sites_file") or SITES_FILE)

    # ------------------------------------------------------------------ refresh

    def _refresh_sites(self, bridge: CDPBridge, tab_id: str) -> list[dict[str, Any]] | None:
        """通过 CDP 页面 fetch 拉 ldoh /api/sites; 若未登录返回 None。"""
        user = bridge.eval(tab_id, "localStorage.getItem('user')")
        if not user or user == "null":
            return None
        res = bridge.fetch(tab_id, "/api/sites")
        if not res or res.get("status") != 200 or not isinstance(res.get("body"), dict):
            return None
        sites = (res["body"] or {}).get("sites") or []
        try:
            self.sites_file.parent.mkdir(parents=True, exist_ok=True)
            self.sites_file.write_text(
                json.dumps(
                    {"sites": sites, "_meta": {
                        "source": self.ldoh_url,
                        "updated": datetime.now().strftime("%Y-%m-%d"),
                    }},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"WARN: 写 sites.json 失败: {e}")
        return sites

    # ------------------------------------------------------------------ sites

    def _load_sites(self) -> list[dict[str, Any]]:
        """加载站点列表: 静态默认 + 启动时可选刷新。"""
        sites: list[dict] = []
        if self.sites_file.exists():
            try:
                data = json.loads(self.sites_file.read_text(encoding="utf-8"))
                sites = list(data.get("sites") or [])
            except Exception as e:
                logger.warning("sites.json 解析失败: %s", e)
        return self._filter(sites)

    def _filter(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for s in raw:
            api_url = s.get("apiBaseUrl")
            if not api_url:
                continue
            host = urlparse(api_url).hostname.lower().removeprefix("www.")
            if not host or "." not in host or host in seen:
                continue
            seen.add(host)
            tags = set(s.get("tags") or [])
            dead = bool(
                s.get("isRunaway")
                or s.get("isFakeCharity")
                or bool(tags & RED_TAGS)
                or s.get("status") in DEAD_STATUS_TOKENS
            )
            supports = bool(s.get("supportsCheckin")) or host in self.login_is_checkin
            out.append(
                {
                    "host": host,
                    "url": api_url.rstrip("/"),
                    "name": s.get("name") or host,
                    "dead": dead,
                    "supportsCheckin": supports,
                    "checkinUrl": s.get("checkinUrl") or "",
                    "tags": sorted(tags),
                }
            )
        return [s for s in out if not s["dead"] and s["supportsCheckin"] and s["host"] not in self.exclude]

    # ------------------------------------------------------------------ main

    def main(self) -> str:
        lines: list[str] = []

        def log(msg: str) -> None:
            s = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
            lines.append(s)
            print(s)

        sites = self._load_sites()
        log(f"加载 {len(sites)} 个待签站点 (排除 {len(self.exclude)} 个 + 已过滤标红/不支持)")
        if not sites:
            return "「LD OPEN HUB 公益站签到」\n无待签站点"
        if self.max_sites:
            sites = sites[: self.max_sites]

        try:
            bridge = CDPBridge.start()
        except Exception as e:
            return f"「LD OPEN HUB 公益站签到」\n启动 CDP bridge 失败: {e}"

        try:
            tab_id = bridge.create_tab()
            # 1. ldoh 登录 (复用 Chrome 已有的 LinuxDo 会话, 跳到 ldoh 首页)
            log(f"打开 ldoh: {self.ldoh_url}")
            try:
                bridge.goto(tab_id, self.ldoh_url, 30000)
            except UnreachableError as e:
                return f"「LD OPEN HUB 公益站签到」\nldoh 不可达: {e}"
            bridge.wait(2000)

            # 1.5 启动时刷新 (可选): 已在 ldoh 已登录状态, 直接拉 /api/sites
            if self.refresh_on_start:
                refreshed = self._refresh_sites(bridge, tab_id)
                if refreshed is not None:
                    sites = self._filter(refreshed)
                    log(f"启动时刷新: 共 {len(sites)} 个待签站点")
                    if not sites:
                        return "「LD OPEN HUB 公益站签到」\n刷新后无待签站点"
                    if self.max_sites:
                        sites = sites[: self.max_sites]

            # 2. 逐站签到
            results: list[dict[str, Any]] = []
            for i, s in enumerate(sites, 1):
                log(f"[{i}/{len(sites)}] {s['name']} ({s['host']})")
                rec = {"host": s["host"], "name": s["name"], "url": s["url"]}
                try:
                    rec.update(self._process_site(bridge, tab_id, s))
                except UnreachableError as e:
                    rec["status"] = "unreachable"
                    rec["message"] = str(e)[:120]
                except Exception as e:  # 单站异常不影响全局
                    rec["status"] = "error"
                    rec["message"] = str(e)[:120]
                log(f"  -> {rec.get('status', '?')}: {(rec.get('message') or '')[:60]}")
                results.append(rec)
                bridge.wait(2000)

            bridge.close_tab(tab_id)
        finally:
            bridge.quit()

        # 熔断回路: 失败站点 +1, 成功重置; 写回 skip_sites.json
        try:
            skip_path = PROJECT_ROOT / "data" / "skip_sites.json"
            existing = {}
            if skip_path.exists():
                existing = json.loads(skip_path.read_text(encoding="utf-8"))
            for r in results:
                h = r["host"]
                if r.get("status") in ("failed", "error", "login_failed"):
                    existing[h] = (existing.get(h) or 0) + 1
                elif r.get("status") in ("ok", "already"):
                    existing.pop(h, None)  # 成功就清零
            skip_path.parent.mkdir(parents=True, exist_ok=True)
            skip_path.write_text(json.dumps(existing, indent=1), encoding="utf-8")
        except Exception as e:
            print(f"WARN: 写 skip_sites.json 失败: {e}")

        ok = sum(1 for r in results if r.get("status") == "ok")
        already = sum(1 for r in results if r.get("status") == "already")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error", "login_failed"))
        body = "「LD OPEN HUB 公益站签到」\n" + f"总数 {len(results)} | 成功 {ok} | 已签 {already} | 失败 {failed}\n"
        body += "\n".join(f"{r.get('status', '?'):<12} {r['host']:<38} {(r.get('message') or '')[:50]}" for r in results)
        return body

    # ------------------------------------------------------------------ 单站

    def _process_site(self, bridge: CDPBridge, tab_id: str, site: dict[str, Any]) -> dict[str, Any]:
        # 1. 打开登录页 (多路径候选: 各 fork 不一样)
        login_paths = self.check_item.get("login_paths") or ("/login", "/sign-in", "/auth/login")
        page_loaded_at = None
        for p in login_paths:
            try:
                bridge.goto(tab_id, f"{site['url']}{p}", 30000)
            except UnreachableError as e:
                return {"status": "unreachable", "message": str(e)[:120]}
            bridge.wait(2500)
            # 检查是否找到 LinuxDO 按钮; 找到了就停
            try:
                probe = bridge.eval(
                    tab_id,
                    "(() => { const els=[...document.querySelectorAll('button, a, [role=button]')];"
                    " return els.some(e => /linux|linuxdo/i.test((e.textContent||'')+(e.getAttribute('href')||''))); })()",
                )
                if probe:
                    page_loaded_at = p
                    break
            except Exception:
                pass
        if not page_loaded_at:
            return {"status": "login_disabled", "message": f"所有登录路径都找不到 LinuxDO 按钮: {login_paths}"}

        # 2. 找登录按钮 (注意: 禁用按钮也算找到, 后面单独判断)
        btn = bridge.eval(
            tab_id,
            "(() => { const els=[...document.querySelectorAll('button, a, [role=button]')];"
            " const el=els.find(e => /linux|linuxdo/i.test((e.textContent||'')+(e.getAttribute('href')||'')));"
            " return el ? {tag:el.tagName, txt:(el.textContent||'').trim().slice(0,30), disabled:el.disabled} : null; })()",
        )
        if not btn:
            return {"status": "login_disabled", "message": "无 LinuxDO 按钮"}
        if btn.get("disabled"):
            return {"status": "login_disabled", "message": "登录按钮禁用"}

        # 3. 真实坐标点击 (避免弹窗拦截器拦下 window.open)
        click = bridge.click_text(tab_id, "linux")
        if not click.get("clicked"):
            return {"status": "login_failed", "message": f"点击无效果: {click.get('reason')}"}
        bridge.wait(2500)

        # 4. 等 OAuth 流程: 协助 connect/linux.do 标签 + 轮询 localStorage.user
        ok = self._wait_session(bridge, tab_id, site["host"], 90)
        if not ok:
            return {"status": "login_failed", "message": "OAuth 后仍未建立会话"}

        # 5. 验证登录
        uid = get_user_id(bridge, tab_id)
        me = fetch_self(bridge, tab_id, uid)
        if not me:
            return {"status": "login_failed", "message": "OAuth 后仍未建立会话"}

        # 6. 登录即签到 模式
        if site["host"] in self.login_is_checkin:
            return {"status": "ok", "message": "登录即签到", "user": me.get("username")}

        # 7. 探测新-api 签到端点
        ck = try_checkin(bridge, tab_id, uid)
        return {
            "status": ck["status"],
            "message": ck["message"][:150],
            "user": me.get("username") or me.get("display_name") or uid,
        }

    def _wait_session(self, bridge: CDPBridge, main_tab: str, target_host: str, wait_sec: int) -> bool:
        """扫浏览器标签: 在 connect.linux.do/linux.do 上点"允许", 等 localStorage.user 建立。"""
        deadline = time.time() + wait_sec
        last_assist = 0.0
        while time.time() < deadline:
            if time.time() - last_assist >= 2:
                for pattern in ("connect.linux.do", "linux.do"):
                    try:
                        click = bridge.click_text_on_url(pattern, "allow|允许|授权|approve|accept|authorize|同意")
                        if click.get("clicked"):
                            print(f"  在 {pattern} 上点了允许按钮")
                            last_assist = time.time()
                    except Exception:
                        pass
            try:
                has = bridge.eval(main_tab, "localStorage.getItem('user')")
            except Exception:
                has = None
            if has and has != "null":
                return True
            time.sleep(2)
        return False


if __name__ == "__main__":
    # 单独调试: echo '{}' | python -m dailycheckin.ldoh.main
    import sys
    cfg = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    print(LdohCheckIn(cfg).main())