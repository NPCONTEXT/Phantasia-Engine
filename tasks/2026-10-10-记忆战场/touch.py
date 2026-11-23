"""maatouch 持久连接（minitouch 协议）：按下/移动/抬起/多指，坐标用 2400×1504 基准。
起进程前先查设备上有没有 /data/local/tmp/maatouch，没有就从项目里（bh3_auto/vendor/maatouch/universal/maatouch）推上去，
已经有就不动、不覆盖（11-23 补：以前假定设备上早就有——这台 09-05 手动推过一次，新设备上一开战就报「maatouch 没起来」）。
用完 close()，不留后台进程；推上去的文件留在设备上，下次直接用。"""
import subprocess, threading, time
from pathlib import Path

ADB = "/opt/homebrew/bin/adb"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
SERIAL = os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"
BASE_W, BASE_H = 2400, 1504
REMOTE = "/data/local/tmp/maatouch"
LOCAL = Path(__file__).resolve().parents[2] / "bh3_auto" / "vendor" / "maatouch" / "universal" / "maatouch"   # 项目里带的那份
_ready = False          # 这个进程里查过（或推过）就不再查：一次播放会开好几场


def ensure_on_device():
    """设备上没有 maatouch 就从项目里推上去；有就不动。查不了（adb 不通、超时）就不推，交给起进程那一步照旧报错。"""
    global _ready
    if _ready:
        return
    try:
        r = subprocess.run([ADB, "-s", SERIAL, "shell", f"[ -f {REMOTE} ] && echo have || echo none"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return
    out = r.stdout.strip()
    if out.endswith("have"):
        _ready = True
        return
    if not out.endswith("none"):
        return
    if not LOCAL.is_file():
        raise RuntimeError(f"设备上没有 maatouch，项目里也找不到 {LOCAL}")
    p = subprocess.run([ADB, "-s", SERIAL, "push", str(LOCAL), REMOTE], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"maatouch 推不上设备：{(p.stderr or p.stdout).strip()[-200:]}")
    print(f"  [touch] 设备上没有 maatouch，已从项目推上去：{REMOTE}", flush=True)
    _ready = True


class Touch:
    def __init__(self):
        ensure_on_device()
        self.p = subprocess.Popen([ADB, "-s", SERIAL, "shell",
                                   f"CLASSPATH={REMOTE} app_process / com.shxyke.MaaTouch.App"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.header = []
        t0 = time.time()
        while time.time() - t0 < 6:
            line = self.p.stdout.readline()
            if not line:
                break
            self.header.append(line.strip())
            if line.startswith("$"):
                break
        info = next((h for h in self.header if h.startswith("^")), None)
        if not info:
            raise RuntimeError(f"maatouch 没起来：{self.header}")
        _, self.max_contacts, self.max_x, self.max_y, self.max_p = info.split()[:5]
        self.max_x, self.max_y = int(self.max_x), int(self.max_y)
        self.lock = threading.Lock()

    def _xy(self, x, y):
        return int(x * self.max_x / BASE_W), int(y * self.max_y / BASE_H)

    def send(self, s):
        with self.lock:
            self.p.stdin.write(s)
            self.p.stdin.flush()

    def down(self, c, x, y):
        X, Y = self._xy(x, y); self.send(f"d {c} {X} {Y} 50\nc\n")

    def move(self, c, x, y):
        X, Y = self._xy(x, y); self.send(f"m {c} {X} {Y} 50\nc\n")

    def up(self, c):
        self.send(f"u {c}\nc\n")

    def tap(self, x, y, c=0, hold=0.05):
        self.down(c, x, y); time.sleep(hold); self.up(c)

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.terminate()
        try:
            self.p.wait(3)
        except subprocess.TimeoutExpired:
            self.p.kill()


if __name__ == "__main__":
    t = Touch()
    print("header:", t.header)
    t.close()
