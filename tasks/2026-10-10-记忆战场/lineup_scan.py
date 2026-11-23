"""精选推荐阵容扫描：推荐阵容窗已打开、已切精选+完整度优先时运行。从顶翻到底，按出现顺序给每行编号，存完整度、OCR 行号、4 个头像裁图。
行定位用「阵容完整度」字样（行号是斜体，OCR 常把 1 读成 7）；相邻两屏按头像比对去重，每次只翻约 1.5 行保证有重叠。
用法：lineup_scan.py BOSS代号    产出 lineups/<代号>/rows.json + rNN_k.png（k=0..2 女武神，3 人偶）"""
import json, subprocess, sys, time
from pathlib import Path
import cv2, numpy as np
HERE = Path(__file__).resolve().parent
OCR = HERE.parent.parent / "bh3_auto/ocr/ocr"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = ["/opt/homebrew/bin/adb", "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
PX = (500, 676, 852, 1054)          # 3 女武神 + 人偶 头像中心 x（2400 基准）

def grab():
    png = subprocess.run(ADB + ["exec-out", "screencap", "-p"], capture_output=True, timeout=20).stdout
    return png, cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)

def ocr(png):
    p = Path("/tmp/ma_scan.png"); p.write_bytes(png); out = []
    for line in subprocess.run([str(OCR), str(p)], capture_output=True, text=True, timeout=30).stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((d["text"].strip(), d["x"] + d["w"] // 2, d["y"] + d["h"] // 2))
    return out

def swipe(y1, y2, ms):
    subprocess.run(ADB + ["shell", f"input swipe 1200 {y1} 1200 {y2} {ms}"], timeout=15); time.sleep(1.5)

def sig(img, y):
    v = [cv2.resize(cv2.cvtColor(img[y - 50:y + 50, px - 50:px + 50], cv2.COLOR_BGR2GRAY), (24, 24)).astype(np.float32).ravel()
         for px in PX]
    v = np.concatenate(v); v -= v.mean(); return v / (np.linalg.norm(v) + 1e-6)

def screen_rows(img, items):
    res = []
    for t, x, y in items:
        if "完整度" in t and 1200 <= x <= 1450:
            py = y - 33
            if py - 62 < 470 or y + 40 > 1185:
                continue
            pct = [t2 for t2, x2, y2 in items if "%" in t2 and 1550 <= x2 <= 1720 and abs(y2 - y) <= 20]
            no = [t2 for t2, x2, y2 in items if t2.isdigit() and 300 <= x2 <= 400 and -40 <= y2 - py <= 40]
            res.append({"py": py, "pct": pct[0] if pct else None, "ocr_no": no[0] if no else None, "sig": sig(img, py)})
    return sorted(res, key=lambda r: r["py"])

from collections import Counter

def main():
    boss = sys.argv[1]
    out = HERE / "lineups" / boss
    if out.exists():
        for f in out.iterdir():
            f.unlink()
    out.mkdir(parents=True, exist_ok=True)
    for _ in range(12):
        swipe(600, 1150, 300)
    rows, best, stale = {}, 0, 0
    for step in range(45):
        png, img = grab(); cur = screen_rows(img, ocr(png))
        # 行号：同屏能读的行号减行序取众数（斜体 1 常读成 7，单独一个读数不信）
        offs = Counter(int(r["ocr_no"]) - i for i, r in enumerate(cur) if r["ocr_no"])
        if not offs:
            print(f"  第{step}屏：可见 {len(cur)} 行，没读到行号，跳过", flush=True)
        else:
            off, cnt = offs.most_common(1)[0]
            if step == 0 and cnt < 2:
                off = 1                       # 顶部第一屏就是 1、2、3
            nums = []
            for i, r in enumerate(cur):
                n = i + off
                nums.append(n)
                if n not in rows:
                    for k, px in enumerate(PX):
                        cv2.imwrite(str(out / f"r{n:02d}_{k}.png"), img[r["py"] - 62:r["py"] + 62, px - 62:px + 62])
                    rows[n] = {"row": n, "pct": r["pct"], "ocr_no": r["ocr_no"], "votes": dict(offs)}
            print(f"  第{step}屏：行 {nums} 读数 {[r['ocr_no'] for r in cur]}", flush=True)
        top = max(rows) if rows else 0
        stale = stale + 1 if top <= best else 0
        best = max(best, top)
        if stale >= 3:
            break
        swipe(1050, 850, 1500)
    miss = [n for n in range(1, best + 1) if n not in rows]
    (out / "rows.json").write_text(json.dumps([rows[n] for n in sorted(rows)], ensure_ascii=False, indent=1))
    print(f"{boss}: 共 {len(rows)} 行（最大行号 {best}，漏 {miss}）；完整度 {[rows[n]['pct'] for n in sorted(rows)]}")


if __name__ == "__main__":
    main()
