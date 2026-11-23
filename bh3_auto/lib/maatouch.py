"""MaaTouch 多指触控（minitouch 协议）：乐土一整局只起一个进程（2026-10-20 愈生佑翎一直按住普攻）。

  t = MaaTouch(adb).start()     # 推到设备、起进程、读它报的屏幕尺寸
  t.down(0, x, y); t.tap(1, x, y); t.up(0); t.close()
坐标就是屏幕坐标（横屏 2400×1504，鸣潮 10-17 实测）；它报的尺寸对不上就不用，免得点歪。
close() 先把按着的手指全部抬起，再关进程；进程不肯走就连设备上的一起杀掉，最后删掉推上去的文件。
"""
from __future__ import annotations
import queue, subprocess, threading, time
from pathlib import Path

LOCAL = Path(__file__).resolve().parents[1] / 'vendor' / 'maatouch' / 'universal' / 'maatouch'
REMOTE = '/data/local/tmp/er_maatouch'


class MaaTouch:
    def __init__(self, adb, size=(2400, 1504)):
        self.adb, self.size = adb, size
        self.proc = None
        self.header = None
        self.pressed = set()

    def _popen(self):
        return subprocess.Popen([self.adb.adb, '-s', self.adb.serial, 'shell',
                                 f'CLASSPATH={REMOTE} app_process / com.shxyke.MaaTouch.App'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def start(self, timeout=6.0):
        if b'er_maatouch' not in self.adb.shell('ls /data/local/tmp/', timeout=10):
            self.adb._run(['push', str(LOCAL), REMOTE], timeout=30)
        self.proc = self._popen()
        lines = queue.Queue()

        def pump(out):                                   # 读掉它的输出；只用得上开头报尺寸那一行
            for raw in out:
                lines.put(raw.decode(errors='replace').strip())
            lines.put(None)
        threading.Thread(target=pump, args=(self.proc.stdout,), name='maatouch-out', daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                break
            if line.startswith('^'):                     # ^ 最多几指 宽 高 最大压力
                self.header = line
                w, h = (int(v) for v in line.split()[2:4])
                if abs(w - self.size[0]) > 2 or abs(h - self.size[1]) > 2:
                    self.close()
                    raise RuntimeError(f'MaaTouch 报的屏幕是 {w}×{h}，不是 {self.size[0]}×{self.size[1]}，坐标会点歪')
                return self
        self.close()
        raise RuntimeError(f'MaaTouch {timeout:.0f} 秒内没报屏幕尺寸')

    def _send(self, *cmds):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError('MaaTouch 进程不在了')
        self.proc.stdin.write(('\n'.join(cmds) + '\nc\n').encode())
        self.proc.stdin.flush()

    def down(self, i, x, y):
        self._send(f'd {i} {round(x)} {round(y)} 50')
        self.pressed.add(i)

    def move(self, i, x, y):
        """按着的手指 i 拖到 (x, y)（11-12 推摇杆：先按在摇杆中心再拖出去）。"""
        self._send(f'm {i} {round(x)} {round(y)} 50')

    def up(self, i):
        self._send(f'u {i}')
        self.pressed.discard(i)

    def tap(self, i, x, y, ms=60):
        self.down(i, x, y)
        time.sleep(ms / 1000)
        self.up(i)

    def close(self):
        p, self.proc = self.proc, None
        try:
            if p is not None and p.poll() is None:
                try:
                    if self.pressed:
                        p.stdin.write(''.join(f'u {i}\n' for i in sorted(self.pressed)).encode() + b'c\n')
                        p.stdin.flush()
                    p.stdin.close()
                    p.wait(timeout=2)
                except Exception:
                    p.kill()
                    p.wait(timeout=2)
        finally:
            self.pressed.clear()
            try:   # 先删文件再杀进程；[h] 让这条 shell 命令自己的命令行匹配不上，免得把自己杀了
                self.adb.shell(f'rm -f {REMOTE}; pkill -f "com.shxyke.MaaTouc[h]"', timeout=10)
            except Exception:
                pass
