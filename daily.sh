#!/bin/bash
# daily.sh - 一键跑所有每日签到任务 (连宿主 Agent Chrome 9333)
#
# 用途: bash daily.sh
# 计划任务: 0 9 * * * /path/to/daily.sh >> /tmp/daily.log 2>&1
#
# 前置条件:
#   1. Agent Chrome 在 9333 (你的常驻 Chrome, 已经登录了 tushare / ldoh)
#   2. ~/.zsh/env.zsh 配 TUSHARE_USERNAME / TUSHARE_PASSWORD
#   3. config/config.json 填好 pkulaw token
#   4. 在 Agent Chrome 里手动登录过 tushare.pro (一次, cookie 落 user-data-dir)
#
# 包含任务: 本仓库 12 个 fork 平台 (PKULAW/LDOH/TUSHARE/JUEJIN/CSDN/WEREAD/BAIDUWP/ZHIHU/NETEASE/ACWING/YOUKU/TENCENT_VIDEO)
# 及 Sitoi 上游全部 (B站/百度网盘/钉钉/etc., 看 config.json)

set -e
cd "$(dirname "$0")"

LOG=/tmp/daily-$(date +%Y%m%d-%H%M%S).log
exec > >(tee -a "$LOG") 2>&1
echo "[$(date '+%F %T')] daily start"

# 0. 拉起 Agent Chrome (如果没跑)
if ! curl -sf http://127.0.0.1:9333/json/version >/dev/null 2>&1; then
  echo "[$(date '+%T')] Agent Chrome 9333 不在, 尝试拉起..."
  if [ -x "/Applications/Agent Chrome.app/Contents/MacOS/Google Chrome" ]; then
    nohup "/Applications/Agent Chrome.app/Contents/MacOS/Google Chrome" \
      --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 \
      --user-data-dir=/tmp/agent-chrome-data \
      --no-first-run >/tmp/agent-chrome.log 2>&1 &
    sleep 5
  else
    echo "WARN: Agent Chrome 不在 9333, LDOH/TUSHARE 模块会失败 (PKULAW 仍可跑)"
  fi
fi

# 1. 加载用户凭证
[ -f ~/.zsh/env.zsh ] && source ~/.zsh/env.zsh
export TUSHARE_USERNAME TUSHARE_PASSWORD

# 2. 跑所有任务 (用 fork 源码, 默认连 9333)
cd /Users/chenchen/working/sourcecode/tools/dev-tools/dailycheckin
python3 -c "
from dailycheckin import CheckIn
print('可用签到任务:', sorted(c.__name__ for c in CheckIn.__subclasses__()))
print('说明: 包括 LDOH/PKULAW/TUSHARE 三个本仓库新增模块 + Sitoi 上游模块')
print()
" 2>&1

# 3. 跑主流程 (会按 config.json 自动启用各模块)
PYTHONPATH=. python3 -m dailycheckin.main "$@"

echo "[$(date '+%F %T')] daily done"