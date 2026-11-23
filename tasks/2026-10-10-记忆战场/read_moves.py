"""暂停菜单 →「出招表」：三个角色页签逐个点开，列表从顶滚到底，拼出出招文字。用法：read_moves.py 输出文件"""
import subprocess, sys, time
from pathlib import Path
from lineup_scan import ocr, grab
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bh3_auto"))
from lib.pe_platform import adb_path  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = [adb_path(), "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
TABS = (759, 1405, 2055)

def tap(x, y, w=1.5):
    subprocess.run(ADB + ["shell", f"input tap {x} {y}"], timeout=10); time.sleep(w)

def sw(y1, y2, ms):
    subprocess.run(ADB + ["shell", f"input swipe 1300 {y1} 1300 {y2} {ms}"], timeout=15); time.sleep(1.2)

def lines():
    png, _ = grab()
    it = [(t, x, y) for t, x, y in ocr(png) if 450 < x < 2360 and 280 < y < 1300]
    it.sort(key=lambda a: (round(a[2] / 25), a[1]))
    return [t for t, _, _ in it]

tap(177, 709, 2)                              # 出招表
out = []
for tx in TABS:
    tap(tx, 205, 1.5)
    name = [t for t, x, y in ocr(grab()[0]) if abs(x - tx) < 300 and 170 < y < 240]
    for _ in range(5):
        sw(500, 1250, 300)
    text = lines()
    for _ in range(20):
        sw(1150, 650, 1200)
        cur = lines()
        k = next((k for k in range(min(len(cur), len(text)), 0, -1) if text[-k:] == cur[:k]), 0)
        if not cur[k:]:
            break
        text += cur[k:]
    out.append(f"## {name}\n" + "\n".join(text))
    print(out[-1][:300], "...", flush=True)
Path(sys.argv[1]).write_text("\n\n".join(out) + "\n")
