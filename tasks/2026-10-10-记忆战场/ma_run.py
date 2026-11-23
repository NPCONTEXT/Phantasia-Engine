"""崩坏3 · 记忆战场 整跑：进记忆战场 → 战场减负 → 认 BOSS → 有档案就一键编队正式打 → 结算。
没学过的 BOSS 只报「新 BOSS，要学」，不打。正式打输了：同模式再试 1 次，还输就开增益模式试（作者定：增益模式只在正式打不过时用）。
每场正式战斗占 1 次「今日剩余挑战次数」，次数不够就停。

用法：ma_run.py [--no-sweep] [--only 档案id,...] [--dry]   （--dry：只进页面、减负、认 BOSS，不开战）

幻想引擎里注册为「崩坏3 · 记忆战场(周常)」（report_steps）：日志「== 步骤名」报进度，最后一行「完成：步骤=结果，…」，不是 ok 的算未完成。
顺序播放会先关游戏，所以开头借日常脚本的 Bot.ensure_game() 冷启动到主界面。

--subitems（10-10 B 方案，幻想引擎「歌里子项打勾」用；不加时上面的老流程一字不改）：
  减负时顺手读三个 BOSS 的「名字 难度」写进 本期BOSS.json，日志打「子项：A|B|C」；
  和上次名单不一样（换期）就只减负、不开打，等用户重新勾；--pick 1,3 只打勾了的位置，--pick none 一个都不打。
  步骤标题用「== 标签原文」，汇总只列 进记忆战场、战场减负 和勾了的 BOSS。
"""
from __future__ import annotations

import argparse, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ma_nav                                   # noqa: E402
from ma_battle import Battle, make_battle, now  # noqa: E402

STEPS = ["进记忆战场", "战场减负", "BOSS 1（左栏第 1 个）", "BOSS 2（左栏第 2 个）", "BOSS 3（左栏第 3 个）"]
DAILY = HERE.parent / "2026-09-29-崩3日常任务"

SUB_FILE = HERE / "本期BOSS.json"
HEAD = ["进记忆战场", "战场减负"]
PERIOD_CHANGED = "本期 BOSS 换了、已更新名单、请重新勾选后再播放"   # 结果文字也别带「，」，免得和幻想引擎汇总的分隔混

# OCR 常把名字读歪（奈埃亦、奥栖、月轮前带逗号），按关键字配档案
# 10-23：摩录多读成过 摩彔多 / 摩灵多 / 摩泉多，奔狼的领主读成过 奔狼的领生
KEYS = {"nael": ("奈埃",), "otto": ("奥托", "伪神", "奥栖"), "moon": ("月轮", "影骑"),
        "moluduo": ("摩录", "摩彔", "摩灵", "摩泉", "录多", "彔多"), "benlang": ("奔狼",), "ashibo": ("阿湿", "湿波")}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (HERE / "logs.nosync" / "ma_run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_profiles(include_unlearned=False):
    """默认只要学会的（learned 有值）；include_unlearned=True 连没学会的也要（只用来给标签取标准名）。"""
    out = {}
    for p in sorted((HERE / "profiles").glob("*.json")):
        d = json.loads(p.read_text())
        if d.get("learned") or include_unlearned:
            out[d["id"]] = d
    return out


def match(name, profiles, diff=None):
    """按名字配档案；给了难度（--subitems 流程）就只认难度一样的档案（档案没写 difficulty 的不挑）。"""
    ok = {pid: pf for pid, pf in profiles.items()
          if not diff or not pf.get("difficulty") or pf["difficulty"] == diff}
    for pid, keys in KEYS.items():
        if pid in ok and name and any(k in name for k in keys):
            return ok[pid]
    for pf in ok.values():                       # 新学的档案：按 boss 名前两个字
        if name and pf["boss"][:2] in name:
            return pf
    return None


def diff_from_total(total):
    """本期实测：SSS 的 BOSS 6 关、SS 的 5 关（10-10 一期三个 BOSS 的观察，推断）。作者说只有 SSS、SS 两种。"""
    return {6: "SSS", 5: "SS"}.get(total)


def clean_label(t):
    """幻想引擎按全角「，」和「=」切汇总、「子项：」行按「|」切，标签里都不能有；OCR 的「•」统一成「·」。"""
    t = (t or "").replace("•", "·").replace("，", "").replace(",", "").replace("=", "").replace("|", "")  # 「子项：」行用 | 分隔
    for ch in "「」『』【】《》〈〉“”‘’\"'":      # 11-04：OCR 把名字前的装饰读成「（「陨影冰骸）；BOSS 名里没有这些括号引号，去掉；圆括号不动
        t = t.replace(ch, "")
    return " ".join(t.split())


def boss_label(bs, pf, diff):
    name = pf["boss"] if pf else clean_label(bs.get("name")) or f"左栏第 {bs['idx'] + 1} 个"
    return clean_label(f"{name} {diff}" if diff else name)


def parse_pick(s, n):
    """--pick：None = 全勾；none / 空 = 一个都不勾；"1,3" = 第 1、3 个（超出本期个数的丢掉）。"""
    if s is None:
        return list(range(1, n + 1))
    if s.strip().lower() in ("", "none"):
        return []
    out = []
    for x in s.split(","):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= n and int(x) not in out:
            out.append(int(x))
    return sorted(out)


def guess_diff(name, named):
    """11-11：关数读不出（11-11 00:23 减负后祸斗读成 None/None）时按档案猜难度：只有一种难度配得上、且档案写了这个难度才算，
    同名首领 SSS、SS 都有档案就不猜。"""
    found = [df for df in ("SSS", "SS") if (match(name, named, df) or {}).get("difficulty") == df]
    return found[0] if len(found) == 1 else None


def period_labels(bosses, profiles, named):
    """本期三只的 (难度, 学会的档案, 有的档案, 标签)。难度先按关数，读不出再按档案猜（11-11），
    这样有档案的首领标签固定是「档案名 难度」，不会因为这次读没读到关数少个「SSS」、被当成换期。"""
    diffs = [diff_from_total(bs["total"]) or guess_diff(bs["name"], named) for bs in bosses]
    pfs = [match(bs["name"], profiles, df) for bs, df in zip(bosses, diffs)]
    npfs = [match(bs["name"], named, df) for bs, df in zip(bosses, diffs)]
    labels = [boss_label(bs, npf, df) for bs, npf, df in zip(bosses, npfs, diffs)]
    return diffs, pfs, npfs, labels


def _bare(label):
    """标签去掉末尾难度（「 SSS」「 SS」）：关数读不出时标签不带难度，比名字时不该因此差一截。"""
    s = (label or "").rstrip()
    for suf in (" SSS", " SS"):
        if s.endswith(suf):
            return s[:-len(suf)].rstrip()
    return s


def same_list(prev, labels, ratio=0.85, prev_items=None, bosses=None):
    """10-15：没学会的 BOSS 标签用 OCR 名字，会错字（托纳提乌 → 托纳提写）；每个位置相似度 ≥ ratio 就算同一份名单。
    11-19：没学过的首领上次整行读坏（「神机 海姆这亦」、关数 None/None），这次读对「神机 海姆达尔 SSS」（相似度 0.56），
    被当成换期。放宽一档：去掉难度比名字，三个里至少两个 ≥ ratio、剩下那个 ≥ 0.6，而且每个位置的进度没倒退
    （换期时新首领进度从 0 起；上次或这次读不出的位置不比），也算同一期。幻想引擎那头（ui/session.py same_labels）还是原标准，
    所以判成同一期时 main_subitems 照旧沿用上次的标签，幻想引擎看到的名单不变、勾选不清。"""
    from difflib import SequenceMatcher
    if not prev or len(prev) != len(labels):
        return False
    if all(SequenceMatcher(None, a, b).ratio() >= ratio for a, b in zip(prev, labels)):
        return True
    r = [SequenceMatcher(None, _bare(a), _bare(b)).ratio() for a, b in zip(prev, labels)]
    if len(r) < 3 or sum(x >= ratio for x in r) < len(r) - 1 or min(r) < 0.6:
        return False
    for it, bs in zip(prev_items or [], bosses or []):
        a, b = (it or {}).get("done"), (bs or {}).get("done")
        if isinstance(a, int) and isinstance(b, int) and b < a:
            return False
    return True


def read_prev_items():
    try:
        return list(json.loads(SUB_FILE.read_text())["items"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_prev_labels():
    try:
        return [it["label"] for it in json.loads(SUB_FILE.read_text())["items"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_sub_file(bosses, labels, pfs, diffs):
    items = [{"pos": bs["idx"] + 1, "label": lb, "name": bs.get("name"), "difficulty": df,
              "done": bs.get("done"), "total": bs.get("total"), "learned": pf["id"] if pf else None}
             for bs, lb, pf, df in zip(bosses, labels, pfs, diffs)]
    tmp = SUB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "items": items}, ensure_ascii=False, indent=1))
    tmp.replace(SUB_FILE)


def start_game():
    """游戏没开就冷启动，等到主界面（复用日常脚本的登录/弹窗处理），再关掉签到等弹窗回主界面。"""
    sys.path.insert(0, str(DAILY))
    import bh3_daily                              # noqa: E402
    bot = bh3_daily.Bot()
    bot.ensure_game()
    if not bot.to_main(rounds=10):
        raise ma_nav.NavError("开跑前没回到主界面")


@ma_nav.battle("正在战斗（记忆战场）")      # 11-02 作者：战斗不能暂停（战斗里按住不放的多指触控，停了会一直按着）
def fight_once(pf, idx, buff, limit=900):   # 10-15：年年糕糕号 v2 一场游戏内 06:53、真实约 9.5 分钟（大招停表）
    ma_nav.to_real_team(idx, pf["lineup"]["row"], log, tab=pf["lineup"].get("tab", "selected"),
                        force=bool(pf["lineup"].get("force")))
    ma_nav.set_buff(buff, log)
    ma_nav.press_fight(log)
    log_dir = HERE / "logs.nosync" / f"{pf['id']}_real_{time.strftime('%m%d-%H%M%S')}"
    b = make_battle(pf, log_dir)              # 11-05：档案写了 style 就用专属打法
    try:
        if not b.wait_battle(25):
            b.save("no_battle")
            raise ma_nav.NavError("开战后 25 秒没进战斗界面")
        b.t0 = now()
        b.log(f"正式战斗开始（增益模式 {'开' if buff else '关'}）")
        b.wait_fight_start()
        b.run_steps(pf.get("opening", []))
        res = b.loop(limit)
        gone = getattr(b, "hud_gone_at", None)
        b.log(f"结果 {res}，实际用时 {now() - b.t0:.1f} 秒（战斗界面消失在 {gone if gone is None else round(gone, 1)} 秒）")
    finally:
        b.close()
    time.sleep(3)
    ma_nav.settle(log)
    return res, (round(gone, 1) if gone else gone), str(log_dir)


def run_boss(bs, profiles, a, diff=None):
    """打一个 BOSS，返回结果文字（ok = 打完或本期早已打完）。"""
    pf = match(bs["name"], profiles, diff)
    log(f"  左栏第 {bs['idx'] + 1} 个：{bs['name']}，进度 {bs['done']}/{bs['total']}，档案 {pf['id'] if pf else '无'}")
    if bs["done"] is not None and bs["total"] and bs["done"] >= bs["total"]:
        # 10-16：开打前那次读数可能是减负刚结束时读的（空之律者 4/5 读成 41/5 → 没打还记 ok），判「打完」前回记忆战场页重读一次
        fresh = ma_nav.ma_status(ma_nav.to_ma_page(log))[0][bs["idx"]]
        if fresh["done"] is not None and fresh["total"] and fresh["done"] >= fresh["total"]:
            log("  本期已打完")
            return "ok"
        log(f"  重读进度 {fresh['done']}/{fresh['total']}，不算打完")
    if not pf:
        what = f"{clean_label(bs['name'])} {diff}" if diff else clean_label(bs["name"])
        return f"新 BOSS「{what}」没学过、没打（学法见 使用说明.md）"
    if a.only and pf["id"] not in a.only:
        return "ok"
    if a.dry:
        return f"--dry 没开战（档案 {pf['id']} 第 {pf['lineup']['row']} 套）"
    # 10-15：减负不一定推到最后一关（空之律者减负后 2/5），打赢一关重新读进度，没满接着打下一关；
    # 每关仍是正常 2 次、再增益 2 次（作者：增益只在正式打不过时开）
    fails = 0
    for _ in range(12):
        cur = None
        for k in range(2):                          # 进度读不出再读一次
            if k:
                time.sleep(2)                       # 10-16：可能读在进度圈动画里，等一下再读
            bosses, left = ma_nav.ma_status(ma_nav.to_ma_page(log))
            cur = bosses[bs["idx"]]
            if cur["done"] is not None and cur["total"]:
                break
        if cur["done"] is None or not cur["total"]:
            return "进度读不出、停下没打"
        if cur["done"] >= cur["total"]:
            return "ok"
        if left is not None and left <= 0:
            return f"今日挑战次数用完、打到 {cur['done']}/{cur['total']}"
        if fails >= 4:
            return f"第 {cur['done'] + 1} 关正常 2 次、增益 2 次都没打过"
        buff = fails >= 2
        log(f"  打第 {cur['done'] + 1}/{cur['total']} 关")
        try:
            res, gone, d = fight_once(pf, bs["idx"], buff)
        except ma_nav.NoTries:                      # 10-15：剩余次数读不出时，点开战才知道没次数
            log("  弹「挑战次数不足」，今天打不了了")
            return f"今日挑战次数用完、打到 {cur['done']}/{cur['total']}"
        log(f"  {pf['boss']} 增益{'开' if buff else '关'} → {res}（战斗界面 {gone} 秒，日志 {d}）")
        fails = 0 if res == "win" else fails + 1
    return "打了 12 场还没打完"


def main_subitems(a, profiles):
    """幻想引擎子项打勾流程（--subitems）。返回退出码。"""
    log(f"—— 记忆战场整跑开始（子项模式，--pick {a.pick}）；已学档案 {list(profiles)}")
    result, cur = {}, None
    prev = read_prev_labels()
    # 勾了的 BOSS 开打前就按上次名单记下：中途出错（包括还没读到本期名单就出错）时，汇总照样列出、补成「没做到」
    picked_labels = [prev[p - 1] for p in parse_pick(a.pick, len(prev))] if prev else []
    try:
        cur = HEAD[0]; log(f"== {cur}")
        start_game()
        ma_nav.to_ma_from_anywhere(log)
        result[cur] = "ok"
        cur = HEAD[1]; log(f"== {cur}")
        if a.no_sweep:
            log("  --no-sweep 跳过")
        else:
            log(f"  战场减负 {ma_nav.sweep_all(log)} 次")
        it = ma_nav.to_ma_page(log)
        bosses, left = ma_nav.ma_status(it)
        if left is None:
            bosses, left = ma_nav.ma_status()
        named = load_profiles(include_unlearned=True)            # 有档案（没学会也算）就用档案的标准名，标签不随 OCR 变
        diffs, pfs, npfs, labels = period_labels(bosses, profiles, named)
        changed = not same_list(prev, labels, prev_items=read_prev_items(), bosses=bosses)
        if not changed and prev != labels:                    # 只差 OCR 错字：有档案的用标准名，其余沿用上次的
            labels = [new if npf else old for new, old, npf in zip(labels, prev, npfs)]
            log(f"  名单和上次只差 OCR 错字，按同一期处理：{labels}")
        write_sub_file(bosses, labels, pfs, diffs)
        log("子项：" + "|".join(labels))
        log(f"  今日剩余挑战次数 {left}；本期：" + "；".join(
            f"{lb}（{bs['done']}/{bs['total']}，{'档案 ' + pf['id'] if pf else '没学过'}）" for bs, lb, pf in zip(bosses, labels, pfs)))
        if changed:
            log(f"  名单和上次不一样（上次 {prev}），这次只减负、不开打")
            result[cur] = PERIOD_CHANGED
            picked_labels = []                      # 换期：等用户重新勾，这次汇总不列 BOSS
        else:
            result[cur] = "ok"
            picks = parse_pick(a.pick, len(bosses))
            picked_labels = [labels[p - 1] for p in picks]
            log(f"  这次勾了：{picks or '一个都没勾（只减负）'}")
            for pos in picks:
                bs, lb = bosses[pos - 1], labels[pos - 1]
                cur = lb; log(f"== {cur}")
                result[cur] = run_boss(bs, profiles, a, diffs[pos - 1])
    except ma_nav.Settling as e:
        log(f"  记忆战场本期结算中：{e}；新一期开了下次播放再打")
        result = {st: "ok" for st in HEAD}             # 这期没有能打的（同超弦「本期未开放」，作者定的做法）；不列上期 BOSS
        picked_labels = []
    except Exception as e:
        log(f"  出错：{type(e).__name__}: {e}")
        result[cur] = f"出错：{e}".replace("，", "、").replace("=", "：")[:120]
    for st in HEAD + picked_labels:
        result.setdefault(st, "没做到（前面出错）")
    log("—— 整跑结束")
    log("完成：" + "，".join(f"{k}={v}" for k, v in result.items()))
    return 0 if all(v == "ok" for v in result.values()) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--subitems", action="store_true", help="幻想引擎子项打勾流程（见文件头）")
    ap.add_argument("--pick", default=None, help="配合 --subitems：1,3 只打这几个位置；none 一个都不打；不给 = 全打")
    a = ap.parse_args()
    a.only = set(filter(None, a.only.split(",")))
    if a.subitems:
        return main_subitems(a, load_profiles())
    profiles = load_profiles()
    log(f"—— 记忆战场整跑开始；已学档案 {list(profiles)}")
    result, cur = {}, None
    try:
        cur = STEPS[0]; log(f"== {cur}")
        start_game()
        ma_nav.to_ma_from_anywhere(log)
        result[cur] = "ok"
        cur = STEPS[1]; log(f"== {cur}")
        if a.no_sweep:
            log("  --no-sweep 跳过")
        else:
            log(f"  战场减负 {ma_nav.sweep_all(log)} 次")
        result[cur] = "ok"
        it = ma_nav.to_ma_page(log)
        bosses, left = ma_nav.ma_status(it)
        if left is None:                                  # 10-10 01:54 读成 None 过一次，再读一帧
            bosses, left = ma_nav.ma_status()
        log(f"  今日剩余挑战次数 {left}")
        for bs in bosses:
            cur = STEPS[2 + bs["idx"]]; log(f"== {cur}")
            result[cur] = run_boss(bs, profiles, a)
    except Exception as e:                                # 导航认不出 / 战斗异常：记在当前这一步，后面的不做
        log(f"  出错：{type(e).__name__}: {e}")
        result[cur] = f"出错：{e}".replace("，", "、").replace("=", "：")[:120]
    for st in STEPS:
        result.setdefault(st, "没做到（前面出错）")
    log("—— 整跑结束")
    log("完成：" + "，".join(f"{k}={v}" for k, v in result.items()))
    return 0 if all(v == "ok" for v in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
