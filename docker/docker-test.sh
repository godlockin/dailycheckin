#!/bin/bash
# docker-test.sh - 一键验证 docker 镜像可起 + ldoh 模块可跑
# 用法: 启动 Docker Desktop 后, 跑这个脚本
set -e
cd "$(dirname "$0")/.."

DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
if ! "$DOCKER" info > /dev/null 2>&1; then
    echo "ERROR: Docker daemon 没起来. 先 'open -a Docker' 等鲸鱼图标变绿"
    exit 1
fi

echo "[1/4] docker build..."
"$DOCKER" build -t godlockin/dailycheckin:test -f docker/Dockerfile .

echo "[2/4] docker compose up..."
"$DOCKER" compose -f docker/docker-compose.yml up -d

echo "[3/4] 等容器 ready (10-15s)..."
sleep 15

echo "[4/4] 进容器跑 ldoh 模块..."
"$DOCKER" exec dailycheckin sh -c "PYTHONPATH=/dailycheckin python3 -c 'from dailycheckin.ldoh.main import LdohCheckIn; print(LdohCheckIn({}).main())'"

echo
echo "完成. 日志: 'docker compose -f docker/docker-compose.yml logs -f'"
echo "停止: 'docker compose -f docker/docker-compose.yml down'"