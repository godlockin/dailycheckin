"""dailycheckin CDP bridge Python 包装: 启动 Node 子进程, 通过 stdio JSON RPC 暴露浏览器操作。

参见 ../cdp/bridge.mjs 协议定义。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

CDP_DEFAULT_PORT = int(os.environ.get("DAILYCHECKIN_CDP_PORT", "9333"))

_PATH_HERE = Path(__file__).resolve().parent
_BRIDGE_NODE = _PATH_HERE.parent.parent / "cdp" / "bridge.mjs"  # utils/ -> cdp/


def _resolve_node() -> str:
    """在 conda/minimal PATH 下找不到 'node' 时, 探测常见绝对路径。"""
    from shutil import which
    p = which("node")
    if p:
        return p
    for c in [
        "/usr/local/bin/node",
        "/opt/homebrew/bin/node",
        "/Users/chenchen/.nvm/versions/node/*/bin/node",
        "/Users/chenchen/.nvm/versions/node/*/bin/node",
    ]:
        import glob
        for hit in glob.glob(c):
            if hit and " " not in hit:
                return hit
    return "node"  # 留原值让 subprocess 报清楚


def is_cdp_alive(port: int | None = None, timeout: float = 2.0) -> bool:
    """轻量检测: 不启动 Node 子进程, 仅 ping Chrome /json/version.

    用于 dependency gate: 跳过缺 CDP 的运行, 避免拉起 Node 子进程后立即报错。
    """
    import urllib.request
    p = port or CDP_DEFAULT_PORT
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def fetch_cookies(
    port: int,
    attach_urls: list[str],
    open_url: str,
    cookie_urls: list[str] | None = None,
) -> str | None:
    """抓 Chrome cookie: 先 attach 已开 tab; 没有 tab 则新开 open_url 再抓。

    解决: 用户关掉站点 tab 后签到模块 skipped 的问题 — Chrome profile 里
    登录 cookie 持久化, 新开 tab 即自动带上登录态。

    参数:
      port: CDP 端口
      attach_urls: 尝试 attach 的 tab URL 前缀 (按顺序)
      open_url: 无 tab 时新开的页面
      cookie_urls: Network.getCookies 的 urls 参数 (默认用 attach_urls)

    返回 Cookie header 字符串 ("k=v; k2=v2"), 失败返回 None。
    """
    bridge: CDPBridge | None = None
    try:
        bridge = CDPBridge.start(port=port)
        tab = None
        for u in attach_urls:
            try:
                tab = bridge.attach_by_url(u)
            except Exception:
                tab = None
            if tab:
                break
        if not tab:
            try:
                tab = bridge.create_tab(open_url)
            except Exception:
                return None
            import time as _t
            _t.sleep(4)  # 等页面加载 + cookie 落定
        cookies = bridge.send_cdp(
            tab, "Network.getCookies", {"urls": cookie_urls or attach_urls}
        )
        parts = [f"{c['name']}={c['value']}" for c in (cookies or {}).get("cookies", [])]
        return "; ".join(parts) or None
    except Exception:
        return None
    finally:
        if bridge:
            try:
                bridge.quit()
            except Exception:
                pass


class CDPError(Exception):
    pass


class UnreachableError(CDPError):
    pass


class CDPBridge:
    """一次性启动一个 Node bridge 子进程, 复用其内部标签映射。

    用法:
        bridge = CDPBridge.start()  # 子进程拉起
        with bridge.tab() as t:
            t.goto(url)
            t.fetch("/api/sites")
        bridge.close()
    """

    _instances: list["CDPBridge"] = []

    @classmethod
    def start(cls, port: int | None = None, bridge_path: Path | None = None) -> "CDPBridge":
        port = port or CDP_DEFAULT_PORT
        bridge_path = bridge_path or _BRIDGE_NODE
        if not bridge_path.exists():
            raise CDPError(f"bridge.mjs not found at {bridge_path}")
        env = os.environ.copy()
        env["DAILYCHECKIN_CDP_PORT"] = str(port)
        proc = subprocess.Popen(
            [_resolve_node(), str(bridge_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=0,  # 无缓冲, 确保每次 write() 后立即被 Node 收到
        )
        b = cls(proc, port)
        cls._instances.append(b)
        # 等待 bridge.mjs 装好 stdin handler (主进程 import + cdpAlive 检查 ~500ms)
        import time as _t
        _t.sleep(0.5)
        return b

    def __init__(self, proc: subprocess.Popen, port: int):
        self.proc = proc
        self.port = port
        self._seq = 0
        self._lock = threading.Lock()
        self._pending: dict[int, Any] = {}
        # 后台读 stdout, 按行分发响应
        self._read_thread = threading.Thread(target=self._reader, daemon=True)
        self._read_thread.start()
        # 后台读 stderr (诊断), 转发到主进程 stderr
        self._err_thread = threading.Thread(target=self._err_reader, daemon=True)
        self._err_thread.start()

    def _reader(self):
        for line in self.proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip("\n")
            if line.startswith("CDP_BRIDGE_RES "):
                try:
                    obj = json.loads(line[len("CDP_BRIDGE_RES "):])
                except json.JSONDecodeError:
                    continue
                seq = obj.pop("__seq__", None)
                if seq is not None and seq in self._pending:
                    self._pending.pop(seq).set_result(obj)
        # stdout EOF → 唤醒所有等待者
        for ev in self._pending.values():
            ev.set_exception(CDPError("bridge closed"))
        self._pending.clear()

    _pending: dict[int, Any]  # declared per-instance

    def _err_reader(self):
        import sys as _sys
        for line in self.proc.stderr:  # type: ignore[union-attr]
            _sys.stderr.write(f"[cdp-bridge] {line}")

    def _call(self, op: str, **params) -> dict:
        self._seq += 1
        seq = self._seq
        req = {"__seq__": seq, "op": op, **params}
        line = json.dumps(req) + "\n"
        ev = threading.Event()
        box: dict[str, Any] = {}

        def set_result(obj):
            box["resp"] = obj
            ev.set()

        def set_exception(e):
            box["err"] = e
            ev.set()

        self._pending[seq] = type("Box", (), {"set_result": staticmethod(set_result), "set_exception": staticmethod(set_exception)})()
        with self._lock:
            self.proc.stdin.write(line)  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        if not ev.wait(timeout=60):
            self._pending.pop(seq, None)
            raise CDPError(f"CDP {op} timeout (no response in 60s)")
        if "err" in box:
            self._pending.pop(seq, None)
            raise box["err"]
        resp = box["resp"]
        if resp.get("unreachable"):
            raise UnreachableError(resp.get("error", "unreachable"))
        if not resp.get("ok"):
            raise CDPError(resp.get("error", "unknown bridge error"))
        return resp.get("data")

    def create_tab(self, url: str = "about:blank") -> str:
        info = self._call("createTab", url=url)
        return info["tabId"]

    def tabs(self) -> list[dict]:
        return self._call("tabs")

    def attach_by_url(self, url: str) -> str | None:
        for t in self.tabs():
            if t["url"].startswith(url):
                info = self._call("attach", tabId=t["id"])
                return info["tabId"]
        return None

    def click_text_on_url(self, url_contains: str, text: str) -> dict:
        """找到第一个 URL 含 url_contains 的标签, attach, 在该标签内点文字 (用于协助 OAuth 弹窗)"""
        return self._call("clickTextOnUrl", urlContains=url_contains, text=text)

    def goto(self, tab_id: str, url: str, timeout_ms: int = 30000) -> None:
        self._call("goto", tabId=tab_id, url=url, timeoutMs=timeout_ms)

    def eval(self, tab_id: str, expression: str, await_promise: bool = False) -> Any:
        # NOTE: 此 eval 是 CDP 的 `Runtime.evaluate` 包装, 表达式在浏览器 page-context
        # JS 沙箱里执行, 不在 Node 里. 调用方必须是同一宿主的可信代码 (本项目 dailycheckin
        # 模块), 不接受外部用户输入. 使用本方法相当于 Selenium 里的 execute_script。
        return self._call("eval", tabId=tab_id, expression=expression, awaitPromise=await_promise)

    def fetch(self, tab_id: str, url: str, method: str = "GET", headers: dict | None = None) -> dict | None:
        return self._call("fetch", tabId=tab_id, url=url, method=method, headers=headers)

    def click_text(self, tab_id: str, text: str) -> dict:
        return self._call("click", tabId=tab_id, text=text)

    def send_cdp(self, tab_id: str, method: str, params: dict | None = None) -> dict | None:
        """透传任意 CDP 命令 (如 Input.insertText / Input.dispatchKeyEvent)."""
        return self._call("sendCdp", tabId=tab_id, method=method, params=params or {})

    def wait(self, ms: int) -> None:
        self._call("wait", ms=ms)

    def close_tab(self, tab_id: str) -> None:
        self._call("close", tabId=tab_id)

    def quit(self) -> None:
        try:
            self._call("quit")
        except Exception:
            pass
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def __enter__(self) -> "CDPBridge":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.quit()

    # 上下文内拿一个 tab 句柄
    class _TabScope:
        def __init__(self, bridge: "CDPBridge", tab_id: str):
            self._bridge = bridge
            self.tab_id = tab_id

        def goto(self, url: str, timeout_ms: int = 30000) -> None:
            self._bridge.goto(self.tab_id, url, timeout_ms)

        def fetch(self, url: str, method: str = "GET", headers: dict | None = None) -> dict | None:
            return self._bridge.fetch(self.tab_id, url, method, headers)

        def eval(self, expr: str, await_promise: bool = False) -> Any:
            return self._bridge.eval(self.tab_id, expr, await_promise)

        def click_text(self, text: str) -> dict:
            return self._bridge.click_text(self.tab_id, text)

        def wait(self, ms: int) -> None:
            self._bridge.wait(ms)

    def tab(self, url: str = "about:blank") -> "_TabScope":
        tid = self.create_tab(url)
        return self._TabScope(self, tid)