"""三个 BOSS 的精选阵容 → 头像聚类成角色 → 找三队女武神互不重复的组合。
用法：lineup_solve.py [阈值0.8]   产出 lineups/clusters.jpg（每行一个角色的所有头像，供人工核对）、lineups/plan.json"""
import itertools, json, sys
from pathlib import Path
import cv2, numpy as np
HERE = Path(__file__).resolve().parent
L = HERE / "lineups"
BOSSES = {"nael": "陨星-奈埃尔", "otto": "伪神奥托", "moon": "影骑士·月轮"}
WEIGHT = {"nael": 1.0, "otto": 1.0, "moon": 0.5}       # 月轮简单，排名靠后也行
TH = float(sys.argv[1]) if len(sys.argv) > 1 else 0.8

def score(a, b):
    c = a[25:99, 25:99]
    return max(float(cv2.matchTemplate(b, c, cv2.TM_CCOEFF_NORMED).max()),
               float(cv2.matchTemplate(a, b[25:99, 25:99], cv2.TM_CCOEFF_NORMED).max()))

faces, lineups = [], {}
for b in BOSSES:
    rows = json.loads((L / b / "rows.json").read_text())
    lineups[b] = []
    for r in rows:
        ids = []
        for k in range(4):
            img = cv2.imread(str(L / b / f"r{r['row']:02d}_{k}.png"))
            faces.append({"boss": b, "row": r["row"], "k": k, "img": img}); ids.append(len(faces) - 1)
        lineups[b].append({"row": r["row"], "pct": r["pct"], "faces": ids})

# 并查集聚类（女武神和人偶分开聚）
par = list(range(len(faces)))
def find(i):
    while par[i] != i:
        par[i] = par[par[i]]; i = par[i]
    return i
for i, j in itertools.combinations(range(len(faces)), 2):
    if (faces[i]["k"] == 3) != (faces[j]["k"] == 3):
        continue
    if find(i) != find(j) and score(faces[i]["img"], faces[j]["img"]) > TH:
        par[find(i)] = find(j)
roots = {}
for i in range(len(faces)):
    roots.setdefault(find(i), []).append(i)
cid = {r: n for n, r in enumerate(sorted(roots, key=lambda r: (faces[r]["k"] == 3, -len(roots[r]))))}
for i in range(len(faces)):
    faces[i]["cid"] = cid[find(i)]
# 核对图：每个角色一行，最多 14 个头像
rowsimg = []
for r in sorted(roots, key=lambda r: cid[r]):
    tiles = [cv2.resize(faces[i]["img"], (90, 90)) for i in roots[r][:14]]
    tiles += [np.zeros((90, 90, 3), np.uint8)] * (14 - len(tiles))
    line = np.hstack(tiles)
    cv2.putText(line, str(cid[r]), (2, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    rowsimg.append(line)
cv2.imwrite(str(L / "clusters.jpg"), np.vstack(rowsimg))
print(f"头像 {len(faces)} 个 → 角色 {sum(1 for r in roots if faces[r]['k'] != 3)} 个、人偶 {sum(1 for r in roots if faces[r]['k'] == 3)} 个；核对图 lineups/clusters.jpg")

for b in BOSSES:
    for lu in lineups[b]:
        lu["valks"] = sorted(faces[i]["cid"] for i in lu["faces"][:3]); lu["elf"] = faces[lu["faces"][3]]["cid"]
best = []
for a in lineups["nael"]:
    for o in lineups["otto"]:
        if set(a["valks"]) & set(o["valks"]):
            continue
        for m in lineups["moon"]:
            if set(m["valks"]) & (set(a["valks"]) | set(o["valks"])):
                continue
            elf_clash = len({a["elf"], o["elf"], m["elf"]}) < 3
            cost = a["row"] * WEIGHT["nael"] + o["row"] * WEIGHT["otto"] + m["row"] * WEIGHT["moon"] + (5 if elf_clash else 0)
            best.append((cost, a, o, m, elf_clash))
best.sort(key=lambda t: t[0])
plan = [{"cost": c, "nael": a["row"], "otto": o["row"], "moon": m["row"], "elf_clash": e,
         "valks": {"nael": a["valks"], "otto": o["valks"], "moon": m["valks"]},
         "pct": {"nael": a["pct"], "otto": o["pct"], "moon": m["pct"]}} for c, a, o, m, e in best[:15]]
(L / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
print(f"不重复组合 {len(best)} 个；前 10：")
for p in plan[:10]:
    print(f"  代价{p['cost']:.1f} 奈埃尔第{p['nael']}套 奥托第{p['otto']}套 月轮第{p['moon']}套 人偶撞{p['elf_clash']} 完整度{p['pct']} 角色{p['valks']}")
for b in BOSSES:
    print(b, [(lu["row"], lu["valks"], lu["elf"]) for lu in lineups[b][:6]])
