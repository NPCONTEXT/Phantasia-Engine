"""推荐阵容窗（精选+完整度优先）里翻到第 row 行，点它的「查看教程」或「一键编队」。
用法：goto_row.py row [tut|team] [record秒数]   record>0 时先在设备上开录屏 /sdcard/ma_tut_<row>.mp4 再点（教程视频打开就自动播）。"""
import json, subprocess, sys, time
from collections import Counter
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "bh3_auto"))
from lib.pe_platform import adb_path  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）

from lineup_scan import ocr, grab, swipe, screen_rows      # 复用

import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = [adb_path(), "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
row = int(sys.argv[1]); what = sys.argv[2] if len(sys.argv) > 2 else "tut"
rec = int(sys.argv[3]) if len(sys.argv) > 3 else 0

def locate():
    png, img = grab(); items = ocr(png); cur = screen_rows(img, items)
    reads = [(i, int(r["ocr_no"])) for i, r in enumerate(cur) if r["ocr_no"]]
    if any(v != 7 for _, v in reads):          # 斜体 1 常读成 7：有别的读数就不信 7
        reads = [(i, v) for i, v in reads if v != 7]
    offs = Counter(v - i for i, v in reads)
    if not offs:
        return None, cur
    off, cnt = offs.most_common(1)[0]
    for i, r in enumerate(cur):
        if i + off == row:
            return r, cur
    return (off, cur)

for _ in range(12):
    swipe(600, 1150, 300)
for step in range(40):
    hit, cur = locate()
    if isinstance(hit, dict):
        y = hit["py"] + 68                      # 「查看教程」「一键编队」字在头像中心下方约 68
        x = 1806 if what == "tut" else 1977
        proc = None
        if rec:
            proc = subprocess.Popen(ADB + ["shell", f"screenrecord --time-limit {rec} --bit-rate 16000000 /sdcard/ma_tut_{row}.mp4"])
            time.sleep(1.5)
        subprocess.run(ADB + ["shell", f"input tap {x} {y}"], timeout=10)
        print(f"第 {row} 行在 y≈{hit['py']}，点了「{'查看教程' if what == 'tut' else '一键编队'}」@({x},{y})", flush=True)
        if proc:
            time.sleep(4)
            png, _ = grab()
            print("右栏：", [t for t, x2, y2 in ocr(png) if 1650 < x2 < 2300 and 380 < y2 < 1250], flush=True)
            proc.wait()                      # 等录屏结束，不留孤儿进程
            print("录屏结束", flush=True)
        sys.exit(0)
    swipe(1050, 850, 1500)
print("没翻到这一行"); sys.exit(1)
