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

ADD . /dailycheckin

# 默认 CDP 端口; 通过环境变量可改
ENV DAILYCHECKIN_CDP_PORT=9333

# 默认 Chromium 远程调试端口
ENV CHROMIUM_FLAGS="--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run"

ENTRYPOINT ["start.sh"]