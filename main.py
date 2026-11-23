#!/usr/bin/env python3
"""幻想引擎 — 原生桌面版(PySide6)「贴纸手账 · 歌单版」

界面在 ui/，进程管理 / 环境 / 配置在 core/。旧的 Tkinter 版留在 main_tk_v1.3.py（用 .venv 运行）。
运行环境在 venv-qt.nosync/（目录名不能以点开头: 桌面被 iCloud 同步，点开头的目录里的文件会被系统
标成「隐藏」，Qt 会因此找不到自己的插件）。
"""
import fcntl
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

VERSION = "4.1.7"
LOCK = ROOT / ".agg.lock"
_lock_fd: int | None = None


def _alert(text: str):
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display dialog "{text}" buttons {{"好"}} default button 1 with title "幻想引擎"'],
                       capture_output=True, timeout=120)
    except Exception:
        pass


def _other_instance() -> int:
    """已有实例在运行时返回它的 PID，否则拿到锁并返回 0。
    用文件锁判断（原子、不怕 PID 复用）；另外兼容旧 Tk 版只往文件里写 PID 的做法。"""
    global _lock_fd
    try:
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return 0    # 锁文件都建不了（权限 / 只读盘），不拦着启动
    try:
        old = int(os.read(fd, 32).decode("ascii", "ignore").strip() or "0")
    except Exception:
        old = 0
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return old or -1
    if old and old != os.getpid():   # 没人持锁，但文件里有 PID: 可能是旧 Tk 版
        try:
            os.kill(old, 0)
            cmd = subprocess.run(["ps", "-p", str(old), "-o", "command="],
                                 capture_output=True, text=True, timeout=5).stdout
            if str(ROOT) in cmd or "main_tk" in cmd:
                os.close(fd)
                return old
        except Exception:
            pass
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass
    _lock_fd = fd
    return 0


def release_lock():
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.ftruncate(_lock_fd, 0)
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None


def activate_existing(pid: int):
    if pid <= 0:
        return
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        'tell application "System Events" to set frontmost of '
                        f'(first process whose unix id is {pid}) to true'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _unhide_runtime():
    """桌面被 iCloud 同步时，系统偶尔会给运行环境里的文件打上「隐藏」标记，Qt 会因此找不到自己的插件。
    启动时看一眼插件目录，发现了就把整个运行环境的标记清掉。"""
    try:
        import stat

        import PySide6
        plugins = Path(PySide6.__file__).parent / "Qt" / "plugins"
        hidden = any(os.lstat(f).st_flags & stat.UF_HIDDEN
                     for d in ("platforms", "styles", "imageformats", "iconengines")
                     for f in (plugins / d).glob("*"))
        if hidden or (os.lstat(plugins).st_flags & stat.UF_HIDDEN):
            subprocess.run(["/usr/bin/chflags", "-R", "nohidden", sys.prefix], capture_output=True, timeout=60)
    except Exception:
        pass


def main() -> int:
    other = _other_instance()
    if other:
        activate_existing(other)
        return 0
    _unhide_runtime()
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        _alert(f"幻想引擎: 界面运行环境(venv-qt.nosync)用不了，请重新安装。\\n{type(e).__name__}")
        return 1

    from core import runner
    from ui import dialogs, theme
    from ui.session import Session
    from ui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("幻想引擎")
    app.setApplicationDisplayName("幻想引擎")
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.font(13))
    app.setStyleSheet(theme.APP_QSS)
    app.setQuitOnLastWindowClosed(False)
    icon = ROOT / "AppIcon_1024.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    session = Session()
    win = MainWindow(session, VERSION, on_quit=release_lock)
    win.show()
    win.raise_()
    win.activateWindow()

    err = session.data.get("_load_error")
    if err:
        dialogs.warn(win, win.logo, "tools.json 读不出来",
                     f"{err}\n歌单暂时是空的。修好文件后重开；为免覆盖原文件，这次不会保存任何改动。")
    threading.Thread(target=runner.cleanup_stale, daemon=True).start()

    rc = app.exec()
    release_lock()
    return rc


if __name__ == "__main__":
    sys.exit(main())
