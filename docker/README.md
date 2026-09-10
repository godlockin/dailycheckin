# docker 启动 / 配置 / 执行

完整实操指南。覆盖：`docker compose` 启动 → 配置 token → 触发签到 → 排查。

## 0. 前置条件

| 项目 | 说明 |
|---|---|
| Docker Desktop | macOS 29.4+ 已验证；需 `daemon.json` 加国内镜像 |
| 常驻 Chrome (可选) | Agent Chrome 跑在 `127.0.0.1:9333` 即可（容器自带 Chromium 兜底） |
| LinuxDo 账号 | ldoh 站聚合的 OAuth 起点，ldoh 模块要它已登录的 Chrome profile |
| pkulaw token | 浏览器登录 https://mcp.pkulaw.com 后从 DevTools 拿 JWT |

### 配 Docker 国内镜像（必做，否则拉 image 超时）

```bash
# ~/.docker/daemon.json
{
  "builder": {"gc": {"defaultKeepStorage": "20GB", "enabled": true}},
  "experimental": false,
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io",
    "https://mirror.iscas.ac.cn"
  ]
}
```

然后**重启 Docker Desktop**（菜单 → Troubleshoot → Restart 或 `pkill Docker`）。

## 1. 克隆 + 改 fork

```bash
cd ~/working/sourcecode/tools/dev-tools
git clone https://github.com/godlockin/dailycheckin.git
cd dailycheckin
git checkout feature/cdp-newapi-ldoh   # 等你合并到 main 后用 main
```

## 2. 改 `config.json`

```bash
cp docker/config.template.json config/config.json
$EDITOR config/config.json
```

### 关键字段

```json
{
  "PKULAW": [
    {
      "name": "我的账号",
      "token": "eyJhbGciOiJSUzI1NiIs...",
      "refresh_token": "eyJhbGciOiJSUzI1NiIs...(强烈推荐)",
      "client_id": "wso2"
    }
  ],
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
}
```

`PKULAW` 必须填（其他模块可全部注释掉只跑这两个）。`refresh_token` 让每日签到完全无人值守。

## 3. 启动容器

```bash
# 在 dailycheckin 项目根目录
docker compose -f docker/docker-compose.yml up -d --build

# 看启动日志
docker compose -f docker/docker-compose.yml logs -f dailycheckin
```

**启动流程**（容器内）：
1. 后台拉 chromium，`--headless --no-sandbox --use-gl=swiftshader` 软渲染
2. `chromium --remote-debugging-port=9333 --user-data-dir=/dailycheckin/chrome-profile` 启 CDP
3. `curl` 轮询 9333 端口（最多 15s）直到 ready
4. 跑 `dailycheckin` 主程序（dailycheckin 0.0.0 / pypi 安装的）

## 4. 触发签到

### 4.1 在容器里手动跑一次（推荐首次）

```bash
# PKULAW only
docker exec dailycheckin sh -c "PYTHONPATH=/dailycheckin python3 -c 'from dailycheckin.pkulaw.main import PkulawCheckIn; print(PkulawCheckIn([{\"name\":\"me\", \"token\":\"$TOKEN\"}]).main())'"

# LDOH only
docker exec dailycheckin sh -c "PYTHONPATH=/dailycheckin python3 -c 'from dailycheckin.ldoh.main import LdohCheckIn; print(LdohCheckIn({}).main())'"

# 全部 (用 dailycheckin CLI, --include 选模块)
docker exec dailycheckin dailycheckin --include PKULAW --include LDOH
```

### 4.2 在宿主机上跑（共享宿主 Chrome 9333）

```bash
# 如果你的 Agent Chrome 在 9333 跑, 容器可以复用同一份
DAILYCHECKIN_CDP_PORT=9333 docker compose -f docker/docker-compose.yml up -d
# 此时容器内 chromium 不必起, 直接连宿主 Chrome
```

**注意**：在容器里跑时 Chromium 跑在 headless 软渲染（慢但能跑）。复用宿主 Chrome 是最优实践。

## 5. 定时任务

容器本身只跑一次（启动时执行 `dailycheckin`）。要做定时，最简单两种：

### 5.1 launchd（推荐 macOS）

```bash
cat > ~/Library/LaunchAgents/com.godlockin.dailycheckin.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.godlockin.dailycheckin</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/chenchen/working/sourcecode/tools/dev-tools/dailycheckin/docker/run-host.sh</string>
    </array>
    <key>StartCalendarInterval</key><dict>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>7</integer>
    </dict>
    <key>StandardOutPath</key><string>/tmp/claude-tasks/dailycheckin-stdout.log</string>
    <key>StandardErrorPath</key><string>/tmp/claude-tasks/dailycheckin-stderr.log</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.godlockin.dailycheckin.plist
```

`run-host.sh`:
```bash
#!/bin/bash
cd "$(dirname "$0")/.."
docker exec dailycheckin dailycheckin --include PKULAW --include LDOH
```

### 5.2 容器内 cron (crond + crontab_list.sh)

`docker_start.sh` 是 Sitoi 上游自带的 crontab 方案。把 LDOH/PKULAW 加到 `crontab_list.sh`：
```bash
'0 9 * * * dailycheckin'  # 每天 9 点
```

## 6. 看日志

```bash
# 容器内所有输出
docker logs dailycheckin

# 实时跟踪
docker logs -f dailycheckin

# 最近 100 行
docker logs --tail 100 dailycheckin

# 容器内 dailycheckin 自己的日志（如果挂载了 ./logs）
ls -la ./logs/
```

## 7. 更新 / 重建

```bash
# 改代码后重新构建 (利用缓存, 快)
docker compose -f docker/docker-compose.yml build

# 改 Dockerfile 或要完全重建
docker compose -f docker/docker-compose.yml build --no-cache

# 重启容器
docker compose -f docker/docker-compose.yml up -d

# 看新容器日志
docker logs -f dailycheckin
```

## 8. 停止 / 清理

```bash
# 停容器 (保留 chrome-profile 卷, 下次启动 LinuxDo 还在)
docker compose -f docker/docker-compose.yml down

# 停 + 删命名卷 (会清空 LinuxDo 登录态, 下次需重新登录 ldoh)
docker compose -f docker/docker-compose.yml down --volumes

# 完全清掉镜像
docker rmi godlockin/dailycheckin:latest
```

## 9. 排查

| 症状 | 检查 | 解决 |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop 启了吗 | `open -a Docker` 等鲸鱼变绿 |
| 拉 image 超时 (IPv6 fail) | `daemon.json` 有 `registry-mirrors` 吗 | 加后 `pkill Docker` 重启 |
| 容器起但 9333 不通 | `docker logs dailycheckin \| grep CDP` | 看 chromium 是否被 GPU 报错 |
| Chromium Vulkan 错误 | alpine 容器无 GPU | 改用 `--use-gl=swiftshader` (已加) |
| dailycheckin 跑出 `login_failed: token 过期` | token 是否真过期 | 浏览器重新登录拷新 token |
| ldoh 站 OAuth 站方坏了 | 看 `/tmp/claude-tasks/dailycheckin-*.log` | 这些是站方问题, 跳过 |
| pkulaw refresh 401 | Keycloak client 是 confidential | config 加 `"client_secret": "..."` |

## 10. 一键复现

```bash
# 验证镜像 + 跑 ldoh + pkulaw (假设 config 已配好)
bash docker/docker-test.sh
```

`docker-test.sh` 已生成在 `docker/`：
1. `docker build` 镜像
2. `docker compose up -d` 容器
3. 等 15s
4. `docker exec` 跑 ldoh 模块
5. 打印结果

## 11. 备份与迁移

- **配置**: `config/config.json` 必备份（含 token, 丢了全要重拷）
- **chrome-profile**: 命名卷 `docker_chrome-profile`，在 `docker volume ls` 可见。
  - 迁移到新机器: `docker run --rm -v docker_chrome-profile:/src -v $(pwd):/dst alpine tar cvf /dst/profile.tar /src`
- **镜像**: `docker save godlockin/dailycheckin:latest | gzip > dailycheckin.tar.gz`
