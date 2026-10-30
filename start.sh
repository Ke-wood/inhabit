#!/usr/bin/env bash
# 控制面板：./start.sh [端口]
set -e
cd "$(dirname "$0")"
exec python3 tools/panel.py "${1:-8788}"
