FROM python:3.11-alpine

WORKDIR /dailycheckin

# Node.js (CDP bridge 子进程) + Chromium (默认 CDP target)
RUN set -ex \
    && apk update && apk upgrade \
    && apk add --no-cache \
        gcc g++ make libffi-dev openssl-dev \
        nodejs npm chromium chromium-chromedriver \
        nss freetype harfbuzz ttf-dejavu \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && pip install --no-cache-dir dailycheckin --upgrade \
    && ln -s /root/.local/bin/dailycheckin /usr/bin/dailycheckin

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
# alpine 容器无 GPU/Vulkan: 用老 headless 模式 + 软件 swiftshader 渲染
ENV CHROMIUM_FLAGS="--headless --no-sandbox --disable-gpu --disable-software-rasterizer --disable-dev-shm-usage --use-gl=swiftshader --no-first-run --hide-scrollbars --mute-audio --disable-extensions"

# 默认不启动; docker-compose 的 command 拉起 chromium + dailycheckin
CMD ["dailycheckin"]