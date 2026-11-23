"""lineup_scan 的事后去重：新行和前 3 行逐个头像做带位移的模板比对（中心 90×90 在 124×124 里找），4 个都像就算同一行。
用法：lineup_dedupe.py BOSS代号  → 写 lineups/<代号>/lineups.json（去重后按顺序编号，带原始行号与 OCR 行号）"""
import json, sys
from pathlib import Path
import cv2
HERE = Path(__file__).resolve().parent

def same_face(a, b):
    c = a[17:107, 17:107]
    return float(cv2.matchTemplate(b, c, cv2.TM_CCOEFF_NORMED).max())

def load(d, n):
    return [cv2.imread(str(d / f"r{n:02d}_{k}.png")) for k in range(4)]

def main(boss):
    d = HERE / "lineups" / boss
    rows = json.loads((d / "rows.json").read_text())
    keep = []
    for r in rows:
        faces = load(d, r["row"])
        dup = None
        for k in keep[-3:]:
            if min(same_face(faces[i], k["faces"][i]) for i in range(4)) > 0.85:
                dup = k; break
        if dup:
            dup["also"].append(r["row"]); dup["ocr_no"] = dup["ocr_no"] or r["ocr_no"]
            continue
        keep.append({"raw": r["row"], "also": [], "pct": r["pct"], "ocr_no": r["ocr_no"], "faces": faces})
    outl = []
    for i, k in enumerate(keep, 1):
        outl.append({"no": i, "raw": k["raw"], "dups": k["also"], "pct": k["pct"], "ocr_no": k["ocr_no"]})
    (d / "lineups.json").write_text(json.dumps(outl, ensure_ascii=False, indent=1))
    bad = [o for o in outl if o["ocr_no"] and o["ocr_no"] not in ("7",) and int(o["ocr_no"]) != o["no"]]
    print(f"{boss}: 原 {len(rows)} 行 → 去重后 {len(outl)} 套；OCR 行号对不上的：{[(o['no'], o['ocr_no']) for o in bad]}")
    for o in outl:
        print(f"  {o['no']:2d} 原{o['raw']}{'+'+str(o['dups']) if o['dups'] else ''} {o['pct']} OCR号{o['ocr_no']}")

if __name__ == "__main__":
    main(sys.argv[1])
