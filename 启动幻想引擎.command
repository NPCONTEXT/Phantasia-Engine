#!/bin/bash
# 幻想引擎 - 双击启动（界面用 venv-qt.nosync）
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec venv-qt.nosync/bin/python main.py "$@"
