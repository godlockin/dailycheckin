# tushare.pro 每日签到 + 猜涨跌

CDP 浏览器自动化模块, 参照 `freqtrade-learning/aastocks/portfolio/tushare_tasks.py` 协议。

## 做什么

1. 打开 `tushare.pro/weborder/#/user/privilege`, 检查登录态
2. 未登录 → 自动填手机号+密码登录 (native setter, Vue 兼容)
3. **DAILY_SIGN** 任务未完成 → 点签到
4. **猜涨跌** period.status==1 且未投 → 用 `secrets.randbits(1)` 随机投涨/跌 (密码学安全, 不可被 patch)

幂等: 已签到/已投票会跳过, 不会重复操作。

## 配置

```json
"TUSHARE": [
  {
    "name": "我的账号",
    "username": "13122249996",
    "password": "xxxx",
    "cdp_port": 9333
  }
]
```

`username/password` 也可走环境变量 `TUSHARE_USERNAME` / `TUSHARE_PASSWORD`
(参考 `~/.zsh/env.zsh`)。`cdp_port` 默认 9333。

## ⚠️ 图形验证码限制 (重要)

tushare 对**新 IP / headless 容器 / 异地登录**会弹图形验证码:

```
请输入图形验证码
```

容器内 chromium 是全新 profile + 数据中心 IP, **首次登录几乎必然触发**,
无法自动通过。两个解法:

### 方案 A (推荐): 常驻浏览器先登录, 模块复用 cookie

在你的 **Agent Chrome (127.0.0.1:9333)** 里手动登录一次 tushare:

1. 启动 Agent Chrome (带 `--remote-debugging-port=9333`)
2. 打开 https://tushare.pro/weborder/#/login
3. 手动完成登录 + 验证码
4. 之后 cookie 长期有效, 模块直接复用 session 只做签到/投票

本机跑 (连 Agent Chrome):
```bash
cd /Users/chenchen/working/sourcecode/tools/dev-tools/dailycheckin
TUSHARE_USERNAME=xxx TUSHARE_PASSWORD=xxx \
  PYTHONPATH=. python3 -c "from dailycheckin.tushare.main import Tushare; print(Tushare({}).main())"
```

### 方案 B: 容器连宿主 Chrome

容器内 chromium 容易触发验证码。让容器复用宿主 Agent Chrome:

```yaml
# docker-compose.yml 加 host 网络访问
extra_hosts:
  - "host.docker.internal:host-gateway"
```
然后 `cdp_port` 仍指 9333, cdp_bridge 连 `host.docker.internal:9333`
(需给 bridge.mjs 的 CDP_URL 支持 host 覆盖)。

## 运行

```bash
# 单独跑
dailycheckin --include TUSHARE

# 全部
dailycheckin
```

## 状态

| status | 含义 |
|---|---|
| `done` | 签到/投票成功 |
| `skipped` | 今天已签到/已投票 |
| `inactive` | 猜涨跌本期未开始 |
| `login_failed` | 未登录 (含验证码拦截, message 会说明) |
| `failed` | 页面元素找不到 / 接口异常 |

## 与 freqtrade-learning 的关系

逻辑等价于 `TushareTaskClient.run_daily_tasks()`:
- `_already_signed`: task_code == "DAILY_SIGN" && is_completed
- `_already_voted`: user_guess.vote 非空
- `_guess_active`: period.status == 1
- `_vote_direction`: `1 if secrets.randbits(1)==0 else 2` (涨=1/跌=2)

差异: freqtrade 版把 HTTP callable 注入 (测试友好); 本模块直接用 CDP
驱动真实浏览器, 因为 tushare weborder 的登录态绑定浏览器 cookie/session。
