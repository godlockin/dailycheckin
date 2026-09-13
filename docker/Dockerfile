FROM python:3.11-alpine

WORKDIR /dailycheckin

# Node.js (CDP bridge 子进程) + Chromium (有头, 跑在 Xvfb 上) + VNC/noVNC
RUN set -ex \
    && apk update && apk upgrade \
    && apk add --no-cache \
        gcc g++ make libffi-dev openssl-dev \
        nodejs npm chromium chromium-chromedriver \
        nss freetype harfbuzz ttf-dejavu \
        xvfb xvfb-run x11vnc novnc websockify \
        ttf-freefont font-noto-cjk \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && pip install --no-cache-dir dailycheckin --upgrade \
    && ln -s /root/.local/bin/dailycheckin /usr/bin/dailycheckin

# 复制入口脚本
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 直接 COPY 项目子目录 (避免 docker/Dockerfile ADD . 把整个项目扔进去)
COPY dailycheckin /dailycheckin/dailycheckin
COPY cdp /dailycheckin/cdp
COPY docker/config.template.json /dailycheckin/config.template.json

# 用我们 fork 的源码覆盖 pip 安装的 dailycheckin (否则新模块 ldoh/pkulaw 等不被发现)
RUN rm -rf /root/.local/lib/python3.11/site-packages/dailycheckin /usr/local/lib/python3.11/site-packages/dailycheckin && \
    cp -R /dailycheckin/dailycheckin /usr/local/lib/python3.11/site-packages/ && \
    mkdir -p /usr/local/lib/python3.11/site-packages/cdp && \
    cp /dailycheckin/cdp/bridge.mjs /usr/local/lib/python3.11/site-packages/cdp/ && \
    find /usr/local/lib/python3.11/site-packages/dailycheckin -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true

# 默认 CDP 端口; 通过环境变量可改
ENV DAILYCHECKIN_CDP_PORT=9333
ENV DISPLAY=:99
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=900
ENV SCREEN_DEPTH=24
# 有头 chromium flags (Xvfb 提供显示, 去掉 --headless 以降低验证码触发概率)
ENV CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run --disable-features=Vulkan --window-size=1280,900"

# 容器入口: Xvfb + Chromium(CDP) + x11vnc + noVNC 常驻
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD []