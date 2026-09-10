#!/usr/bin/env node
/**
 * dailycheckin CDP bridge: Node 子进程, 通过 stdio JSON RPC 暴露浏览器操作。
 * Python (或任何宿主) 通过 stdio 调用, 无第三方依赖, Node 24+ 自带 WebSocket。
 *
 * 协议: 一行一个 JSON 对象
 *   请求: {"op": "<operation>", ...params}
 *   响应: {"ok": true, "data": ...} 或 {"ok": false, "error": "..."}
 *
 * 支持 op:
 *   - goto {url, tabId?, timeoutMs?}
 *   - eval {expression, awaitPromise?, tabId?}
 *   - fetch {url, method?, headers?, tabId?}  # 页面上下文 fetch, 自动带 cookie
 *   - click {text, tabId?}                    # 真实坐标点击 (绕过弹窗拦截)
 *   - wait {ms}
 *   - tabs {}                                  # 列出所有 page 标签
 *   - attach {tabId}                           # 绑定到现有标签
 *   - detach {tabId?}
 *   - close {tabId?}
 *   - quit
 */
'use strict';

import { writeFileSync } from 'node:fs';

const CDP_PORT = parseInt(process.env.DAILYCHECKIN_CDP_PORT || '9333', 10);
const CDP_URL = `http://127.0.0.1:${CDP_PORT}`;
const RESPONSE_PREFIX = 'CDP_BRIDGE_RES ';

function log(msg) {
  process.stderr.write(`[cdp-bridge] ${msg}\n`);
}

async function cdpAlive() {
  try {
    const r = await fetch(`${CDP_URL}/json/version`, { signal: AbortSignal.timeout(2000) });
    return r.ok;
  } catch {
    return false;
  }
}

class Tab {
  constructor(info) {
    this.id = info.id;
    this.seq = 0;
    this.pending = new Map();
    this.ws = new WebSocket(info.webSocketDebuggerUrl);
    this.ready = new Promise((res, rej) => {
      this.ws.onopen = () => res();
      this.ws.onerror = (e) => rej(new Error(`ws error: ${e?.message || e}`));
    });
    this.ws.onmessage = (ev) => {
      const m = JSON.parse(typeof ev.data === 'string' ? ev.data : ev.data.toString());
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? rej(new Error(`${m.error.message} (${m.error.code})`)) : res(m.result);
      }
    };
  }

  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.seq;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          rej(new Error(`CDP ${method} ${timeoutMs / 1000}s timeout`));
        }
      }, timeoutMs);
    });
  }

  async eval(expression, awaitPromise = false) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise,
    });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description?.slice(0, 200) || 'eval exception');
    }
    return r.result?.value;
  }

  async fetchIn(url, method = 'GET', headers = null) {
    return this
      .eval(
        `fetch(${JSON.stringify(url)}, { method: ${JSON.stringify(method)}, credentials: 'include', headers: ${JSON.stringify(
          headers || {}
        )} })`
          + `.then(async r => ({ status: r.status, body: await r.json().catch(() => null) }))`
          + `.catch(e => ({ status: 0, body: null, err: String(e) }))`,
        true
      )
      .catch(() => null);
  }

  async realClickByText(text) {
    const rect = await this
      .eval(
        `(() => { const els=[...document.querySelectorAll('button, a, [role=button]')];`
          + ` const el=els.find(e => new RegExp(${JSON.stringify(text)}, 'i').test((e.textContent || '') + (e.getAttribute('href') || '')));`
          + ` if (!el || el.disabled) return null;`
          + ` el.setAttribute('data-cdp-bridge-target', '1');`
          + ` const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`
      )
      .catch(() => null);
    if (!rect) return { clicked: false, reason: 'not_found_or_disabled' };
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: rect.x, y: rect.y, button: 'none' }).catch(() => {});
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: rect.x, y: rect.y, button: 'left', clickCount: 1 });
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: rect.x, y: rect.y, button: 'left', clickCount: 1 });
    return { clicked: true };
  }

  async goto(url, timeoutMs = 30000) {
    await this.send('Page.enable').catch(() => {});
    try {
      await this.send('Page.navigate', { url });
    } catch (e) {
      const err = new Error(`goto ${e.message}`);
      err.unreachable = /ERR_NAME|ERR_CONNECTION|ERR_TIMED_OUT/i.test(e.message);
      throw err;
    }
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      await new Promise((r) => setTimeout(r, 300));
      try {
        const href = await this.eval('location.href');
        if (href.startsWith('chrome-error://')) {
          const err = new Error('chrome-error page (unreachable)');
          err.unreachable = true;
          throw err;
        }
        if ((await this.eval('document.readyState')) === 'complete') return;
      } catch (e) {
        if (e.unreachable) throw e;
      }
    }
  }

  close() {
    try {
      this.ws.close();
    } catch {}
  }
}

const tabs = new Map(); // tabId -> Tab

async function createTab(url = 'about:blank') {
  const r = await fetch(`${CDP_URL}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  const info = await r.json();
  if (info.error) throw new Error(`/json/new failed: ${info.error}`);
  const t = new Tab(info);
  await t.ready;
  tabs.set(t.id, t);
  return { tabId: t.id, url: info.url };
}

async function listTabs() {
  const r = await fetch(`${CDP_URL}/json`);
  const arr = await r.json();
  return Array.isArray(arr) ? arr.filter((t) => t.type === 'page').map((t) => ({ id: t.id, url: t.url })) : [];
}

async function closeTab(tabId) {
  if (!tabId) return { closed: 0 };
  try {
    await fetch(`${CDP_URL}/json/close/${tabId}`);
  } catch {}
  tabs.delete(tabId);
  return { closed: 1 };
}

function getTab(tabId) {
  const t = tabs.get(tabId);
  if (!t) throw new Error(`tab ${tabId} not found (forgot to attach?)`);
  return t;
}

async function handleOp(req) {
  const op = req.op;
  try {
    switch (op) {
      case 'goto': {
        let tab;
        if (req.tabId) {
          tab = getTab(req.tabId);
        } else {
          tab = (await createTab(req.url || 'about:blank'))._tab || null;
        }
        if (req.url && !req.tabId) {
          // createTab already navigated if url provided
        } else if (req.url) {
          await tab.goto(req.url, req.timeoutMs || 30000);
        }
        return { ok: true, data: { tabId: tab?.id || req.tabId } };
      }
      case 'eval': {
        const tab = getTab(req.tabId);
        const data = await tab.eval(req.expression, !!req.awaitPromise);
        return { ok: true, data };
      }
      case 'sendCdp': {
        const tab = getTab(req.tabId);
        const data = await tab.send(req.method, req.params || {});
        return { ok: true, data };
      }
      case 'fetch': {
        const tab = getTab(req.tabId);
        const data = await tab.fetchIn(req.url, req.method || 'GET', req.headers || null);
        return { ok: true, data };
      }
      case 'click': {
        const tab = getTab(req.tabId);
        const data = await tab.realClickByText(req.text || '');
        return { ok: true, data };
      }
      case 'wait': {
        await new Promise((r) => setTimeout(r, req.ms || 1000));
        return { ok: true };
      }
      case 'tabs': {
        return { ok: true, data: await listTabs() };
      }
      case 'createTab': {
        const info = await createTab(req.url || 'about:blank');
        return { ok: true, data: info };
      }
      case 'attach': {
        const all = await listTabs();
        const target = all.find((t) => t.id === req.tabId) || all.find((t) => t.url === req.url);
        if (!target) throw new Error('no matching tab');
        const r = await fetch(`${CDP_URL}/json`);
        const arr = await r.json();
        const info = arr.find((t) => t.id === target.id);
        const t = new Tab(info);
        await t.ready;
        tabs.set(t.id, t);
        return { ok: true, data: { tabId: t.id, url: t.ws.url } };
      }
      case 'attachMatching': {
        // 找到第一个 URL 包含 req.urlContains 的标签, attach, 返回 tabId
        const all = await listTabs();
        const target = all.find((t) => (t.url || '').includes(req.urlContains));
        if (!target) return { ok: true, data: { tabId: null } };
        const r = await fetch(`${CDP_URL}/json`);
        const arr = await r.json();
        const info = arr.find((t) => t.id === target.id);
        const t = new Tab(info);
        await t.ready;
        tabs.set(t.id, t);
        return { ok: true, data: { tabId: t.id, url: info.url } };
      }
      case 'clickTextOnUrl': {
        // 找到第一个 URL 包含 req.urlContains 的标签, attach, 在该标签内点文字
        const all = await listTabs();
        const target = all.find((t) => (t.url || '').includes(req.urlContains));
        if (!target) return { ok: true, data: { clicked: false, reason: 'no_matching_tab' } };
        const r = await fetch(`${CDP_URL}/json`);
        const arr = await r.json();
        const info = arr.find((t) => t.id === target.id);
        const t = new Tab(info);
        await t.ready;
        tabs.set(t.id, t);
        const clicked = await t.realClickByText(req.text || '');
        return { ok: true, data: { ...clicked, tabId: t.id } };
      }
      case 'detach': {
        const t = getTab(req.tabId);
        t.close();
        tabs.delete(t.id);
        return { ok: true };
      }
      case 'close': {
        const data = await closeTab(req.tabId);
        return { ok: true, data };
      }
      case 'quit':
        process.exit(0);
      default:
        return { ok: false, error: `unknown op: ${op}` };
    }
  } catch (e) {
    return { ok: false, error: e.message, unreachable: !!e.unreachable };
  }
}

function write(obj) {
  process.stdout.write(RESPONSE_PREFIX + JSON.stringify(obj) + '\n');
}

/** 关联请求和响应: 调用方在 req 里塞 __seq__, 响应里原样回传 */
function makeWriteWithSeq(seq) {
  return (obj) => process.stdout.write(RESPONSE_PREFIX + JSON.stringify({ __seq__: seq, ...obj }) + '\n');
}

async function main() {
  if (!(await cdpAlive())) {
    log(`CDP not alive on port ${CDP_PORT}; bridge will fail until Chrome is launched`);
  } else {
    log(`CDP ready on port ${CDP_PORT}`);
  }

  let buf = '';
  process.stdin.setEncoding('utf-8');
  process.stdin.on('data', async (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let req;
      try {
        req = JSON.parse(line);
      } catch (e) {
        write({ ok: false, error: `bad json: ${e.message}` });
        continue;
      }
      const seq = req.__seq__;
      const respWrite = typeof seq === 'number' ? makeWriteWithSeq(seq) : write;
      const resp = await handleOp(req);
      respWrite(resp);
    }
  });
  process.stdin.on('end', () => process.exit(0));
}

main().catch((e) => {
  write({ ok: false, error: `bridge fatal: ${e.message}` });
  setTimeout(() => process.exit(1), 100);
});