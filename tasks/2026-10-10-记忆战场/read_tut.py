"""教程窗已打开时：右栏从顶滚到底，拼出完整文字（按行去重拼接）。用法：read_tut.py [输出文件]"""
import subprocess, sys, time
from pathlib import Path
from lineup_scan import ocr, grab
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bh3_auto"))
from lib.pe_platform import adb_path  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = [adb_path(), "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]

def sw(y1, y2, ms):
    subprocess.run(ADB + ["shell", f"input swipe 1990 {y1} 1990 {y2} {ms}"], timeout=15); time.sleep(1.2)

def lines():
    png, _ = grab()
    return [t for t, x, y in sorted(ocr(png), key=lambda a: a[2]) if 1650 < x < 2300 and 470 < y < 1100]

for _ in range(4):
    sw(600, 1100, 400)
text = lines()
for _ in range(25):
    sw(1000, 800, 1200)
    cur = lines()
    # 找 cur 开头和 text 结尾的最长重叠
    k = next((k for k in range(min(len(cur), len(text)), 0, -1) if text[-k:] == cur[:k]), 0)
    new = cur[k:]
    if not new:
        break
    text += new
out = "\n".join(text)
print(out)
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(out + "\n")
