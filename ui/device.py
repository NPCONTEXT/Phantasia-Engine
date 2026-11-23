"""后台线程里干的三件事: 轮询设备状态 / 监控截图 / 发 macOS 系统通知。界面线程不直接调 adb。"""
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

from core import env


class DeviceWatcher(QObject):
    """每隔几秒查一次设备和分辨率，结果用信号送回界面。"""
    updated = Signal(object, str)   # 设备 dict 或 None, 分辨率文字

    def __init__(self, interval: float = 3.0, parent=None):
        super().__init__(parent)
        self._interval = interval
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def poke(self):
        """马上再查一次（例如刚改完分辨率）。"""
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def _loop(self):
        while not self._stop:
            try:
                dev = env.device_online()
                size = env.wm_size() if dev else "--"
            except Exception:
                dev, size = None, "--"
            if self._stop:
                break
            self.updated.emit(dev, size)
            self._wake.wait(self._interval)
            self._wake.clear()


class MonitorFeed(QObject):
    """监控: 开着的时候不停截设备最新一帧（只在内存里，不落盘）。"""
    frame = Signal(QImage)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self._thread: threading.Thread | None = None

    def set_on(self, on: bool):
        self._on = on
        if on and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        ev = threading.Event()
        while self._on:
            wait = 0.8
            try:
                r = subprocess.run([env.adb_bin(), "exec-out", "screencap", "-p"],
                                   capture_output=True, timeout=8)
                img = QImage.fromData(r.stdout, "PNG") if (r.returncode == 0 and r.stdout) else QImage()
                if img.isNull():
                    self.failed.emit("拿不到画面（设备未连接？）")
                    wait = 2.0
                else:
                    if max(img.width(), img.height()) > 900:   # 缩小一点再送给界面，省内存
                        img = img.scaled(900, 900, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                    if self._on:
                        self.frame.emit(img)
            except Exception:
                self.failed.emit("拿不到画面（adb 没响应）")
                wait = 2.0
            ev.wait(wait)


def notify(title: str, body: str = "", subtitle: str = "幻想引擎"):
    """发一条 macOS 系统通知（PySide6 没有原生接口，借 osascript 发；失败就算了）。"""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle)}"'

    def work():
        try:
            subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=10)
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
