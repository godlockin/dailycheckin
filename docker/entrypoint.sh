#!/bin/sh
# 容器入口:
#   Xvfb (虚拟显示 :99) -> Chromium 有头 + CDP -> x11vnc -> noVNC(websockify 6080)
# 用法:
#   (默认) 启动 GUI 栈常驻; 不跑 dailycheckin (由外部 cron 调 docker exec)
#   传 "sign" 参数: 启动 GUI 后再跑一次 dailycheckin 然后继续常驻
#
# 手动登录 (解决 tushare 图形验证码 / ldoh OAuth):
#   浏览器打开 http://localhost:6080/vnc.html -> 在虚拟桌面里操作 Chrome
set -e

CDP_PORT="${DAILYCHECKIN_CDP_PORT:-9333}"
PROFILE_DIR=/dailycheckin/chrome-profile
W="${SCREEN_WIDTH:-1280}"
H="${SCREEN_HEIGHT:-900}"
D="${SCREEN_DEPTH:-24}"
export DISPLAY=:99

# 1. 清掉上次崩溃残留的 profile 锁
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket

# 2. 启动 Xvfb 虚拟显示
echo "Starting Xvfb on :99 (${W}x${H}x${D})..."
Xvfb :99 -screen 0 "${W}x${H}x${D}" -ac -nolisten tcp &
sleep 2

# 3. 启动 Chromium (有头, CDP)
echo "Starting Chromium with CDP on port ${CDP_PORT}..."
chromium ${CHROMIUM_FLAGS} \
  --remote-debugging-port=${CDP_PORT} \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir="$PROFILE_DIR" \
  about:blank >/tmp/chromium.log 2>&1 &
CHROMIUM_PID=$!

# 4. 启动 x11vnc (把 :99 通过 VNC 暴露, 密码可选)
VNC_PASS="${VNC_PASSWORD:-}"
X11VNC_ARGS="-display :99 -forever -shared -rfbport 5900 -bg -o /tmp/x11vnc.log"
if [ -n "$VNC_PASS" ]; then
  mkdir -p /root/.vnc
  x11vnc -storepasswd "$VNC_PASS" /root/.vnc/passwd
  X11VNC_ARGS="$X11VNC_ARGS -rfbauth /root/.vnc/passwd"
fi
echo "Starting x11vnc on :5900 (password: $([ -n "$VNC_PASS" ] && echo set || echo none))..."
x11vnc $X11VNC_ARGS || echo "x11vnc start warning (will retry below)"

# 5. 启动 noVNC websockify: 6080 -> 5900
NOVNC_DIR="$(find /usr/share -maxdepth 3 -type d -name novnc 2>/dev/null | head -1)"
echo "Starting noVNC on :6080 (dir=$NOVNC_DIR)..."
websockify --web="$NOVNC_DIR" 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
WEBSOCKIFY_PID=$!

# 6. 等 CDP 就绪 (最多 40s)
READY=0
for i in $(seq 1 40); do
  if wget -q -T 2 -O - "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "Chromium CDP ready on port ${CDP_PORT} after ${i}s"
    READY=1
    break
  fi
  if ! kill -0 "$CHROMIUM_PID" 2>/dev/null; then
    echo "chromium exited early, retrying after clearing locks..."
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
  echo "WARNING: CDP not ready; see /tmp/chromium.log"
  tail -15 /tmp/chromium.log 2>/dev/null || true
fi
echo "GUI stack ready. Open http://localhost:6080/vnc.html to operate Chrome manually."

# 7. 可选: 传 "sign" 则跑一次 dailycheckin
if [ "${1:-}" = "sign" ]; then
  shift || true
  echo "Running dailycheckin once..."
  dailycheckin "$@" || echo "dailycheckin exited with error"
  echo "Sign run finished; keeping GUI stack alive."
fi

# 8. 常驻: 任一关键进程退出则整体退出 (交给 restart:always 重启)
trap 'echo "shutting down..."; kill $WEBSOCKIFY_PID $CHROMIUM_PID 2>/dev/null; exit 0' TERM INT
while true; do
  if ! kill -0 "$CHROMIUM_PID" 2>/dev/null; then
    echo "chromium died, exiting (container will restart)..."
    exit 1
  fi
  sleep 10
done