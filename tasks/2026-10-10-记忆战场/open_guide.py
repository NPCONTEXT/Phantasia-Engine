"""从「推荐阵容」窗或 BOSS 选人/详情页退回记忆战场页，选中左栏第 idx 个 BOSS（0/1/2），进阵容攻略并切精选+完整度优先 ON。
用法：open_guide.py idx   成功打印 OK，否则打印看到的文字并退出码 1。"""
import json, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
OCR = HERE.parent.parent / "bh3_auto/ocr/ocr"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = ["/opt/homebrew/bin/adb", "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
BOSS_Y = (300, 500, 690)

def tap(x, y, w=2.5):
    subprocess.run(ADB + ["shell", f"input tap {x} {y}"], timeout=10); time.sleep(w)

def ocr():
    p = Path("/tmp/ma_nav.png"); p.write_bytes(subprocess.run(ADB + ["exec-out", "screencap", "-p"], capture_output=True, timeout=20).stdout)
    out = []
    for line in subprocess.run([str(OCR), str(p)], capture_output=True, text=True, timeout=30).stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((d["text"].strip(), d["x"] + d["w"] // 2, d["y"] + d["h"] // 2))
    return out

def has(items, key, region=None):
    for t, x, y in items:
        if key in t and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
            return x, y
    return None

idx = int(sys.argv[1])
for _ in range(8):                                   # 退回记忆战场页（右下「战场减负」）
    it = ocr()
    if has(it, "战场减负"):
        break
    if has(it, "作者", (1700, 380, 2300, 470)):          # 教程窗盖在推荐阵容上，先关它
        tap(2332, 65)
    elif has(it, "推荐阵容"):
        tap(2084, 234)
    else:
        tap(93, 67, 3)
else:
    print("没回到记忆战场页", [t for t, _, _ in it][:30]); sys.exit(1)
tap(190, BOSS_Y[idx]); tap(2109, 1430, 4)
it = ocr()
if not has(it, "开始战斗", (1900, 1300, 2400, 1480)):
    print("没进 BOSS 详情页", [t for t, _, _ in it][:30]); sys.exit(1)
name = [t for t, x, y in it if 100 <= x <= 600 and 150 <= y <= 260]
tap(2115, 1393, 4); tap(1649, 223, 3); tap(1286, 381, 2.5)
it = ocr()
if not has(it, "ON", (1900, 340, 2100, 420)):
    tap(2053, 381, 2.5); it = ocr()
ok = has(it, "精选推荐") and has(it, "ON", (1900, 340, 2100, 420)) and has(it, "查看教程")
print("OK" if ok else "没切到精选+完整度ON", name)
sys.exit(0 if ok else 1)
