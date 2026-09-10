# pkulaw 北大法宝每日签到

HTTP-only 签到, **支持 Keycloak OAuth 自动续期**。配合 Sitoi 的 dailycheckin 框架,
自动发现 (CheckIn.__subclasses__)。

## 协议

- 签到端点: `POST https://gateway.pkulaw.com/api-portal/rewards/daily/claim` (空 body `{}`)
- 认证: `Authorization: Bearer <JWT>`
- 用户态校验: `GET /api-portal/profile` (顺便拿昵称)
- Token 来源: Keycloak `cas.pkulaw.com/auth/realms/fabao`

JWT `iss` 自动从 token 抽取, 续期端点 `https://cas.pkulaw.com/auth/realms/fabao/protocol/openid-connect/token`。

## 自动续期

| 触发条件 | 行为 |
|---|---|
| JWT `exp` < now+60s (即将过期) | **主动**用 `refresh_token` 换新 token, 写回 config.json |
| 请求返回 HTTP 401 | **被动**用 `refresh_token` 换新 token + 自动 retry 一次, 写回 config.json |
| `refresh_token` 也过期/缺失 | 报 `login_failed`, 提示用户重新登录 |

新 token 原子写 (`*.tmp` + rename), 不会半写文件。config 路径按 dailycheckin 约定搜
(`config/config.json` / `config.json` / `../config.json`)。

## 配置 `config.json`

```json
"PKULAW": [
  {
    "name": "我的账号",
    "token": "eyJhbGciOiJSUzI1NiIs...",
    "refresh_token": "eyJhbGciOiJSUzI1NiIs...",
    "client_id": "wso2"
  },
  {
    "name": "另一账号",
    "token": "..."
  }
]
```

`refresh_token` 字段是可选的, 但 **强烈推荐配置**, 这样签到能完全无人值守。
`client_id` 默认 `wso2` (从 token `aud` 推断), 大多数情况不用改。

## 怎么拿 token

### 方法 1: 浏览器 DevTools (推荐)

1. Chrome 打开 https://mcp.pkulaw.com/ 登录
2. F12 → Application → Storage → Local Storage → 找 keycloak session
3. 复制 `access_token` 和 `refresh_token` 字段 (JWT 格式 `xxx.yyy.zzz`)
4. 贴到 config.json

### 方法 2: login_helper 自动化捕获

利用常驻 Chrome (你的 Agent Chrome :9333) 自动完成登录并抓 token:

```bash
# 注意: 需要在 9333 端口有常驻 Chrome, 且你已登录 ldoh (cookie 在 Chrome 里)
python -m dailycheckin.pkulaw.login_helper --cdp-port 9333
```

会:
1. 在 Chrome 新建标签打开 keycloak 登录
2. 等你手动登录 (60s 超时)
3. 跳到 ldoh 后从 localStorage 抓 access_token
4. 输出 JSON (含 `token` + `refresh_token`) 直接粘到 config

**注意**: `refresh_token` 通常不在 localStorage, 而是在 keycloak session iframe
或 keycloak cookie 里. helper 会自动尝试几种 client_id 探测;
拿不到时, 你需手动从 DevTools 取.

## 运行

```bash
# 干跑 (只看登录态)
PYTHONPATH=. python3 -c "from dailycheckin.pkulaw.main import PkulawCheckIn; print(PkulawCheckIn([{'token':'xxx'}]).main())"

# 完整 dailycheckin 主流程 (会签所有配置的平台)
dailycheckin --include PKULAW
```

Docker 里:
```bash
docker exec -it dailycheckin sh -c \
  "PYTHONPATH=/dailycheckin python3 -c \
   'from dailycheckin.pkulaw.main import PkulawCheckIn; print(PkulawCheckIn([]).main())'"
```

或直接放 config.json 里跑 dailycheckin.

## 失败排查

| 状态 | 原因 | 怎么办 |
|---|---|---|
| `login_failed: token 过期且 refresh_token 失败` | refresh_token 失效 | 浏览器重新登录拿新 token |
| `login_failed: 401 且 refresh_token 失败` | 同上 | 同上 |
| `login_failed: token 过期或无效 (HTTP 401)` | 初始 token 就过期, 又没 refresh_token | 浏览器重新登录 |
| `failed: HTTP 500/502/503` | pkulaw 服务端问题 | 稍后重试 |
| `error: 请求异常` | 网络/超时 | 检查网络或 retry |

## 已知限制

- Keycloak `refresh_token` 寿命约 7-30 天, 过期需浏览器重新登录
- 如果 keycloak 端点需 `client_secret`, 在 config 加 `"client_secret": "..."` 字段
- container 中如无写权限, 续期仍能跑 (仅本次有效), 但下次启动 token 又过期
- 不支持多账号并行的节流策略 (按 list 顺序串行, 每账号 1s)