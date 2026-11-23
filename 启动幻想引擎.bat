@echo off
chcp 65001 >nul
rem 幻想引擎 Windows 启动（实验性：还没在 Windows 实机上跑过）。用带控制台的 python.exe 起、窗口最小化：
rem 各首歌的脚本、adb 都挂在这个控制台下，不会一个个弹黑窗；停止时整组收。
cd /d "%~dp0"
start "幻想引擎" /min venv-qt.nosync\Scripts\python.exe main.py
