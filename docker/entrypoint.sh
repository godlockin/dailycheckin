#!/bin/sh
# 容器入口: 清 stale profile 锁 -> 启动 chromium CDP -> 等就绪 -> dailycheckin
set -e

CHROMIUM_FLAGS="${CHROMIUM_FLAGS:-}"
CDP_PORT="${DAILYCHECKIN_CDP_PORT:-9333}"
PROFILE_DIR=/dailycheckin/chrome-profile

# 1. 清掉上次崩溃残留的 profile 锁 (否则 chromium 拒绝启动, 9333 永不监听)
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket

# 2. 后台启动 chromium
chromium ${CHROMIUM_FLAGS} \
  --remote-debugging-port=${CDP_PORT} \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir="$PROFILE_DIR" \
  about:blank >/tmp/chromium.log 2>&1 &
CHROMIUM_PID=$!
echo "chromium started (pid=$CHROMIUM_PID)"

# 3. 等 CDP 就绪 (最多 40s); busybox wget 探活
READY=0
for i in $(seq 1 40); do
  if wget -q -T 2 -O - "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "Chromium CDP ready on port ${CDP_PORT} after ${i}s"
    READY=1
    break
  fi
  # chromium 进程死了 -> 清锁重启一次
  if ! kill -0 "$CHROMIUM_PID" 2>/dev/null; then
    echo "chromium exited early, clearing locks and retrying..."
    rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket
    chromium ${CHROMIUM_FLAGS} \
      --remote-debugging-port=${CDP_PORT} \
      --remote-debugging-address=0.0.0.0 \
      --user-data-dir="$PROFILE_DIR" \
      about:blank >/tmp/chromium.log 2>&1 &
    CHROMIUM_PID=$!
  fi
  sleep 1
done

if [ "$READY" != "1" ]; then
  echo "WARNING: Chromium CDP not ready after 40s; browser modules (ldoh/tushare) will fail"
  echo "--- chromium.log tail ---"
  tail -15 /tmp/chromium.log 2>/dev/null || true
fi

# 4. 跑 dailycheckin (透传参数)
exec dailycheckin "$@"