"""11-07：记住每区选人页上用的是什么队，打过的区不再重新点推荐。
作者：「只是这一期用，但如果是打过的，就不重新选推荐了（但提前是脚本得记住用的是什么，别匹配错了）」。
记：一键编队之后 / 认定用户配的队之后，存这一区「本期起点、用的什么（rowN = 推荐第 N 行 / user = 用户配的队）、
    队长技能那行「xx作为队长时」的 xx、左边三张女武神卡的灰度小图」。
比：同一期 + 要用的就是记下的那个 + 队长字像（识字会错字：11-07「渡」读成「波」，按相似度）+ 三张卡都像 → 同一队，不用再点推荐。
    只看队长不够：11-07 作者把 2、3 号对调过一次，队长一样、站位不一样，专属打法就认不到星之环技能键。
三张卡：11-07 实拍 侦察/team_fp_1107：同一队前后两张相似度 1.00，不同人之间 ≤ 0.10。
文件：profiles/lineup_mem.json（文字）+ profiles/lineup_mem/<区>_<n>.png（64×120 灰度小图）。"""
from __future__ import annotations

import difflib, json, math, time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
MEM = HERE / "profiles" / "lineup_mem.json"
IMG = HERE / "profiles" / "lineup_mem"
KEYS = {"左": "edge", "上": "dense", "右": "risk", "下": "special"}
SLOTS = ((110, 300, 366, 780), (446, 300, 704, 780), (782, 300, 1040, 780))   # 选人页三张卡（2400×1504）
SIM_MIN = 0.8           # 三张卡每张都要这么像
CAP_MIN = 0.6           # 队长字相似度（「浮生•波尘之羽」对「浮生·渡尘之羽」0.71）


def cards(img):
    return [cv2.resize(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (64, 120), interpolation=cv2.INTER_AREA)
            for x0, y0, x1, y1 in SLOTS]


def captain(it):
    """队长技能那行「xx作为队长时，…」的 xx；没读到 None。"""
    for t, _, _ in it:
        if "作为队长时" in t:
            return t.split("作为队长时")[0].strip() or None
    return None


def sim(a, b) -> float:
    v = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0])
    return v if math.isfinite(v) else 0.0


def label(what) -> str:
    if what == "user":
        return "用户配的队"
    if isinstance(what, str) and what.startswith("row"):
        return f"推荐第 {what[3:]} 行"
    return str(what)


def load() -> dict:
    try:
        return json.loads(MEM.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save(d: dict):
    MEM.parent.mkdir(parents=True, exist_ok=True)
    MEM.write_text(json.dumps(d, ensure_ascii=False, indent=1))


def remember(pos, what, period, it, img, note="") -> dict:
    """记下这一区现在选人页上的队。period：本期起点「YYYY-MM-DD HH:MM」（ss_run.period_key）。"""
    d = load()
    IMG.mkdir(parents=True, exist_ok=True)
    for k, c in enumerate(cards(img), 1):
        cv2.imwrite(str(IMG / f"{KEYS[pos]}_{k}.png"), c)
    d[pos] = {"period": period, "what": what, "captain": captain(it), "time": time.strftime("%m-%d %H:%M"), "note": note}
    save(d)
    return d[pos]


def check(pos, what, period, it, img):
    """选人页现在的队是不是记下的那个、而且记的就是 what。返回 (是不是, 说明)。"""
    m = load().get(pos)
    if not m:
        return False, "这一区还没记过用的队"
    if m.get("period") != period:
        return False, f"记的是别的一期（{m.get('period')}）的队"
    if m.get("what") != what:
        return False, f"记的是{label(m.get('what'))}、这次要用{label(what)}"
    cap = captain(it)
    if not cap or not m.get("captain") or difflib.SequenceMatcher(None, cap, m["captain"]).ratio() < CAP_MIN:
        return False, f"队长对不上（记的「{m.get('captain')}」、现在「{cap}」）"
    sims = []
    for k, c in enumerate(cards(img), 1):
        old = cv2.imread(str(IMG / f"{KEYS[pos]}_{k}.png"), cv2.IMREAD_GRAYSCALE)
        if old is None:
            return False, "记的小图不见了"
        sims.append(sim(old, c))
    s = "/".join(f"{v:.2f}" for v in sims)
    if min(sims) < SIM_MIN:
        return False, f"三张卡对不上（相似度 {s}）"
    return True, f"就是记下的{label(what)}（队长「{cap}」、三张卡相似度 {s}）"
