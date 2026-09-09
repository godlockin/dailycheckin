# ldoh 模块 (LD OPEN HUB 公益站聚合签到)

`dailycheckin/ldoh` 通过 Node CDP 子进程驱动常驻 Chrome, 复用其 LinuxDo
登录态, 对 https://ldoh.105117.xyz/ 聚合的 new-api 公益站逐个 OAuth 登录
+ 签到。

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│  dailycheckin.main() (Python, requests-based, Sitoi 上游兼容)    │
│  └── CheckIn.__subclasses__() 自动发现 LdohCheckIn               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  dailycheckin.ldoh.main.LdohCheckIn                              │
│    ├── utils/cdp_bridge.CDPBridge   (Python → Node via stdio)    │
│    ├── utils/newapi                 (签到端点探测器)              │
│    ├── ldoh/sites.json              (静态默认站点表)             │
│    └── ldoh/refresh.py              (运行时刷新工具)             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  cdp/bridge.mjs (Node 24+, 零依赖, stdio line-delimited JSON)    │
│    └── ws://127.0.0.1:9333 → CDP Runtime.evaluate/Input/Page    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                       Chrome (Agent / Chromium)
                       LinuxDo session 持久化
```

## 协议 (`bridge.mjs`)

每个 Python 调用发一行 JSON, Node 回一行 JSON (带 `__seq__` 关联)：

| op | 用途 |
|---|---|
| `tabs` | 列出所有 page 标签 |
| `createTab {url}` | 新建标签 |
| `attach {tabId?url?}` | 绑定现有标签 |
| `attachMatching {urlContains}` | 自动找到含 url 子串的标签并 attach |
| `clickTextOnUrl {urlContains,text}` | attach + 真实坐标点击文字 |
| `goto {tabId,url,timeoutMs}` | 导航 + 等 complete |
| `eval {tabId,expression,awaitPromise}` | Runtime.evaluate |
| `fetch {tabId,url,method,headers}` | 页面 fetch, 自动带 cookie |
| `wait {ms}` | sleep |
| `close {tabId}` / `detach {tabId}` / `quit` | 标签/进程管理 |

## 快速开始

### 1. 依赖
- Python 3.9+
- Node.js 18+ (只需 stdlib, 无第三方依赖)
- Chrome 或 Chromium 在 `127.0.0.1:9333` 提供远程调试 (推荐常驻的 Agent Chrome)

### 2. ldoh 登录 (一次性)
打开 `https://ldoh.105117.xyz/` 在你常驻的 Chrome 里手动登录一次。LinuxDo
OAuth 完成后 ldoh 会设 `localStorage.user`, 后续自动复用。

### 3. 跑一次试试
```bash
export PYTHONPATH="$(pwd)"

# 手动指定 Python (如果默认 python3 没有 pycryptodome):
PY=/Users/chenchen/miniconda3-arm64/bin/python3

# dry-run (只列站, 不签):
$PY -m dailycheckin.ldoh.refresh --output /tmp/sites.json

# 实际跑 (默认排除标红/不支持, 跑所有支持签到的):
$PY -c "import sys; sys.path.insert(0, '.'); from dailycheckin.ldoh.main import LdohCheckIn; print(LdohCheckIn({}).main())"
```

### 4. 接入 dailycheckin 主流程
在 `config/config.json` 加:
```json
"LDOH": [
  {
    "ldoh_url": "https://ldoh.105117.xyz/",
    "exclude_domains": [],
    "max_sites": 0,
    "refresh_on_start": false,
    "login_is_checkin": ["ps.air-outer.com", "anyrouter.top"],
    "interactive_login_wait_sec": 120
  }
]
```

`refresh_on_start=true` 时主流程会先拉 ldoh 最新列表再签到; `false` 用内置 sites.json 离线签。

### 5. Docker
```bash
cd docker
docker compose up -d
```
默认在容器内启动 Chromium + 持久 profile 卷 (`chrome-profile`)。也可设置 `DAILYCHECKIN_CDP_PORT=宿主端口` 复用宿主 Chrome (Chrome 需 `--remote-debugging-address=0.0.0.0`)。

## 配置 (`config.json` `LDOH` 块)

| 字段 | 含义 |
|---|---|
| `ldoh_url` | ldoh 入口 (默认 https://ldoh.105117.xyz/) |
| `exclude_domains` | 跳过 host 列表 |
| `max_sites` | 调试: 只处理前 N 站 (0 = 全部) |
| `refresh_on_start` | 运行时是否拉 ldoh 刷新 sites.json |
| `login_is_checkin` | 登录即签到的站 (无需 API 端点) |
| `interactive_login_wait_sec` | 兜底: 等用户首次登录 LinuxDo 的超时 |

## 标红/可用过滤

- 标红 = `isRunaway` / `isFakeCharity` / tags 含「无法使用/无法访问/无法登录/已无法LD登录/无法签到」
- 可签到 = `supportsCheckin=true` 或在 `login_is_checkin` 列表里
- 默认 33 站; `refresh_on_start=true` 时拉 ldoh 实时获取

## 签到端点兼容 (`utils/newapi.py`)

依次探测 6 个端点 (POST/GET × checkin/check_in/check-in)：

```
POST /api/user/checkin      # 主路径
GET  /api/user/checkin
POST /api/user/check_in     # snake_case
GET  /api/user/check_in
POST /api/user/check-in     # kebab-case
GET  /api/user/check-in
```

兼容 new-api / Veloera / OneAPI 等 fork。认证用 session cookie + 可选 `New-Api-User` / `Veloera-User` header (从 `localStorage.user.id` 或 `/api/user/self` 读出)。

## 实战踩坑

1. **合成 `el.click()` 触发的 `window.open` 会被 Chrome 弹窗拦截** — 必须用 `Input.dispatchMouseEvent` 真实坐标点击。
2. **OAuth `code` 一次性, 手动 `fetch("/api/oauth/linuxdo?code=X")` 会烧掉前端流程** — 让前端自己处理回调, 我们等 `localStorage.user` 出现即可。
3. **Cloudflare 五秒盾 (Just a moment...)** — 等自动通过 25s, 否则记 `cloudflare` 跳过。
4. **SPA 渲染慢 (10-15s)** — 按钮/候选轮询 22 轮 (~28s)。
5. **`/login` 路径上的 `<a href="/login">` 点击会重载当前页** — 已在登录路径时绝不再点 Sign in, 用 button 元素匹配。
6. **大量站服务端 OAuth 已坏 ("Failed to get token from Linux DO")** — 站方配置问题, 与本模块无关, 记 `login_failed` 等站长修。

## 已知限制 (未来 PR)

- 多 OAuth 标签页并发点击已实现 (`clickTextOnUrl`); 但站点侧弹窗与本地 tab 切换的 race condition 仍需手动点一次
- Cloudflare Turnstile 验证码未自动通过
- 同站点多账号未实现 (`config.json` 接受 list, 当前 main() 只跑第一个)