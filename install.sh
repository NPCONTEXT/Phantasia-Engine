#!/bin/bash
# 幻想引擎安装：建两个运行环境、装依赖、编两个识字小程序、写设备序列号。
# 只在项目目录里写东西，不动系统，不要管理员密码。重复跑没关系。
#
#   ./install.sh
#   PY_UI=/path/to/python3.13 ./install.sh      界面用别的 Python（默认找 python3.13）
set -euo pipefail
cd "$(dirname "$0")"

PY_UI="${PY_UI:-python3.13}"
PY_SCRIPTS="${PY_SCRIPTS:-/usr/bin/python3}"
ZZZ="tasks/2026-10-07-绝区零日常"

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "缺 $1：$2"; exit 1; }
}
need "$PY_UI" "界面要 Python 3.13（brew install python@3.13），或者用 PY_UI=你的python ./install.sh 指定"
need "$PY_SCRIPTS" "脚本用 macOS 自带的 /usr/bin/python3，装 Xcode 命令行工具就有（xcode-select --install）"
need swiftc "编识字程序要 Xcode 命令行工具（xcode-select --install）"
[ -x /opt/homebrew/bin/adb ] || echo "提醒：没找到 /opt/homebrew/bin/adb（brew install android-platform-tools）。脚本里写的就是这个路径。"
[ -x /opt/homebrew/bin/ffmpeg ] || echo "提醒：没找到 /opt/homebrew/bin/ffmpeg（brew install ffmpeg）。记忆战场、超弦空间、乐土要用它。"

echo "1/4 界面环境 venv-qt.nosync（$("$PY_UI" --version 2>&1)）"
[ -x venv-qt.nosync/bin/python ] || "$PY_UI" -m venv venv-qt.nosync
venv-qt.nosync/bin/python -m pip install --quiet --upgrade pip
venv-qt.nosync/bin/python -m pip install --quiet -r requirements-ui.txt

echo "2/4 脚本环境 bh3_auto/.venv（$("$PY_SCRIPTS" --version 2>&1)）"
[ -x bh3_auto/.venv/bin/python ] || "$PY_SCRIPTS" -m venv bh3_auto/.venv
bh3_auto/.venv/bin/python -m pip install --quiet --upgrade pip
bh3_auto/.venv/bin/python -m pip install --quiet -r requirements-scripts.txt

echo "3/4 编识字程序（用的是 macOS 自带的 Vision）"
swiftc -O bh3_auto/ocr/ocr.swift -o bh3_auto/ocr/ocr
swiftc -O "$ZZZ/ocr_rev2.swift" -o "$ZZZ/ocr_rev2"

echo "4/4 设备序列号"
if /usr/bin/python3 scripts/set_device.py; then
    :
else
    echo "    先跳过。连上设备以后再跑一次：python3 scripts/set_device.py"
fi

echo
echo "装好了。双击「幻想引擎.app」，或者在终端运行 ./启动幻想引擎.command"
