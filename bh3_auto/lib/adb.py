"""adb 设备封装：截图 / 点击 / 滑动 / 按键 / 任意 shell。

坐标体系：一律使用「当前截图尺寸」的像素坐标（原生或 wm 覆盖后的显示分辨率，
由 screencap 实际返回尺寸决定）。上层调用时先 screencap() 拿尺寸，再按其坐标系给坐标。
"""
from __future__ import annotations

import io
import os
import subprocess
import time

# 基础分辨率：所有人工标注坐标都以这个尺寸为基准，运行时按实际截图尺寸等比换算
BASE_W, BASE_H = 2400, 1504


class Adb:
    def __init__(self, serial: str = "", adb_path: str = "/opt/homebrew/bin/adb"):
        # 11-17 无线调试：没指定就用幻想引擎经 ANDROID_SERIAL 给的设备；单独运行时才用下面这个序列号
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"
        self.adb = adb_path

    def _run(self, args: list[str], timeout: float = 30) -> bytes:
        cmd = [self.adb, "-s", self.serial] + args
        return subprocess.run(cmd, capture_output=True, timeout=timeout).stdout

    def shell(self, cmd: str, timeout: float = 30) -> bytes:
        return self._run(["shell", cmd], timeout=timeout)

    def screencap(self) -> bytes:
        return self._run(["exec-out", "screencap", "-p"], timeout=20)

    # ---------- 输入 ----------
    def tap(self, x: int, y: int, delay_after: float = 0.08) -> None:
        self._run(["shell", f"input tap {int(x)} {int(y)}"], timeout=10)
        if delay_after:
            time.sleep(delay_after)

    def swipe(self, x1, y1, x2, y2, duration_ms: int = 300) -> None:
        self._run(
            ["shell", f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"],
            timeout=15,
        )

    def key(self, keycode: str) -> None:
        self._run(["shell", f"input keyevent {keycode}"], timeout=10)

    def back(self) -> None:
        self.key("KEYCODE_BACK")

    def home(self) -> None:
        self.key("KEYCODE_HOME")

    # ---------- 坐标换算 ----------
    @staticmethod
    def scale_base(x: float, y: float, w: int, h: int):
        """把 BASE 分辨率坐标换算到实际截图尺寸 (w, h)。"""
        return int(x * w / BASE_W), int(y * h / BASE_H)


if __name__ == "__main__":
    a = Adb()
    img = a.screencap()
    print("screencap bytes:", len(img))
    a.tap(1200, 752)
    print("tap ok")
