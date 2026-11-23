@echo off
chcp 65001 >nul
rem 幻想引擎 Windows 安装（实验性：还没在 Windows 实机上跑过）。
rem 建两个运行环境、装依赖（识字用 RapidOCR）、写设备序列号。只在项目目录里写东西，不要管理员权限，要联网下载依赖。
setlocal
cd /d "%~dp0"
rem 让下面跑的 Python 都按 UTF-8 输出（英文 Windows 默认 cp1252，中文提示会变成 \u 转义）
set "PYTHONUTF8=1"
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo 要 Python 3.12（识字用的 RapidOCR 还不支持 3.13）：到 https://www.python.org/downloads/ 装 3.12，装的时候勾上 py launcher，装好再跑
  exit /b 1
)
where adb >nul 2>nul || echo 提醒：PATH 里没找到 adb。装 Android SDK Platform-Tools 并加进 PATH，或者在 tools.json 的 app 里写 "adb": "它的完整路径"
where ffmpeg >nul 2>nul || echo 提醒：PATH 里没找到 ffmpeg（记忆战场、超弦空间、乐土要用）：winget install Gyan.FFmpeg
echo 1/3 界面环境 venv-qt.nosync
if not exist venv-qt.nosync\Scripts\python.exe %PY% -m venv venv-qt.nosync || exit /b 1
venv-qt.nosync\Scripts\python.exe -m pip install --quiet --upgrade pip
venv-qt.nosync\Scripts\python.exe -m pip install --quiet -r requirements-ui.txt || exit /b 1
echo 2/3 脚本环境 bh3_auto\.venv（含 RapidOCR）
if not exist bh3_auto\.venv\Scripts\python.exe %PY% -m venv bh3_auto\.venv || exit /b 1
bh3_auto\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
bh3_auto\.venv\Scripts\python.exe -m pip install --quiet -r requirements-scripts.txt || exit /b 1
echo 3/3 设备序列号
bh3_auto\.venv\Scripts\python.exe scripts\set_device.py || echo     先跳过。连上设备以后再跑一次：bh3_auto\.venv\Scripts\python.exe scripts\set_device.py
echo.
echo 装好了。双击「启动幻想引擎.bat」打开。
rem 设备序列号那步失败会留下 ERRORLEVEL 1，echo 不会清掉，这里明确返回 0
exit /b 0
