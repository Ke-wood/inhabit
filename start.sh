#!/usr/bin/env bash
# ./start.sh            → 菜单
# ./start.sh panel      → 面板
set -e
cd "$(dirname "$0")"
exec python3 inhabit.py "$@"
