"""假记忆战场：按约定协议输出，供幻想引擎子项打勾离线测试（不碰设备）。
--subitems 开子项协议；--pick 1,3 / none；环境变量 FAKE_LABELS="A SSS|B SSS|C SS" 模拟本期 BOSS（换期就换这串）；FAKE_SLEEP 每步秒数。
FAKE_FAIL_AT 模拟中途出错：「进记忆战场」/「战场减负」（还没打「子项：」行）/「boss2」（打左栏第 2 个时）。
出错后照真脚本的约定补汇总：固定句和勾了的 BOSS 都要列出来，没做到的写「没做到（前面出错）」；
勾了的 BOSS 开打前就按上次名单记下，所以「子项：」行之前出错也会列出（换期时不列，等用户重新勾）。"""
import argparse, json, os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(); ap.add_argument("--subitems", action="store_true"); ap.add_argument("--pick", default=None)
a = ap.parse_args()
labels = os.environ.get("FAKE_LABELS", "陨星-奈埃尔 SSS|伪神奥托 SSS|影骑士·月轮 SS").split("|")
sl = float(os.environ.get("FAKE_SLEEP", "0.5"))
fail = os.environ.get("FAKE_FAIL_AT", "")
def log(m): print(time.strftime("[%H:%M:%S] ") + m, flush=True)
def pick_list(n):
    if a.pick is None:
        return list(range(1, n + 1))
    return [] if a.pick == "none" else [int(x) for x in a.pick.split(",") if x and 1 <= int(x) <= n]
f = HERE / "本期BOSS.json"
prev = [it["label"] for it in json.loads(f.read_text())["items"]] if f.exists() else None
res, cur = {}, None
picked = [prev[p - 1] for p in pick_list(len(prev))] if prev else []
log("—— 假记忆战场开始")
try:
    cur = "进记忆战场"; log("== " + cur); time.sleep(sl)
    if fail == cur:
        raise RuntimeError("没认出记忆战场入口")
    res[cur] = "ok"
    cur = "战场减负"; log("== " + cur); time.sleep(sl)
    if fail == cur:
        raise RuntimeError("减负按钮没认出来")
    f.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "items": [{"pos": i + 1, "label": l, "learned": True, "done": 5, "total": 6} for i, l in enumerate(labels)]},
                            ensure_ascii=False, indent=1))
    log("子项：" + "|".join(labels))
    if prev != labels:                       # 和真脚本一致：第一次（没有上次名单）也只读名单、不开打
        res[cur] = "本期 BOSS 换了、已更新名单、请重新勾选后再播放"
        picked = []
    else:
        res[cur] = "ok"
        picks = pick_list(len(labels))
        picked = [labels[p - 1] for p in picks]
        for p in picks:
            cur = labels[p - 1]; log(f"== {cur}"); time.sleep(sl)
            if fail == f"boss{p}":
                raise RuntimeError("开战后 25 秒没进战斗界面")
            res[cur] = "ok"
except RuntimeError as e:
    log(f"  出错：{e}")
    res[cur] = f"出错：{e}"
for st in ["进记忆战场", "战场减负"] + picked:
    res.setdefault(st, "没做到（前面出错）")
log("完成：" + "，".join(f"{k}={v}" for k, v in res.items()))
