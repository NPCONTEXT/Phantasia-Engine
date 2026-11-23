"""超弦空间 打一场（C2 起试打用）：在选人页点「开始战斗」→ 记忆战场战斗引擎（ma_battle.Battle，只 import 不改）打 → 结算页截图识字。
用法（任务目录下）：../../bh3_auto/.venv/bin/python ss_fight.py profiles/<档案>.json [--zone 左|上|右|下] [--limit 秒]
  --zone：从游戏任意页进关卡页 → 选这个区 → 战斗准备 → 推荐阵容一键编队（10-17 作者定）→ 打 → 结算点回主页 → 结果记进档案。
  不带 --zone：游戏要停在选人页，打完只截结算页（C2 用法）。
不认识的开战弹框只报不点。"""
from __future__ import annotations

import argparse, json, subprocess, sys, time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
MA = HERE.parent / "2026-10-10-记忆战场"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MA))
import ma_battle                      # noqa: E402
from ma_battle import now             # noqa: E402
import ss_nav                         # noqa: E402
import ss_battle                      # noqa: E402
from ss_battle import SSBattle, no_fox   # noqa: E402


LINK_ERRORS = (BrokenPipeError, ConnectionError, TimeoutError, OSError, subprocess.SubprocessError)


class TeamChanged(Exception):
    """要用用户配的队（keep_team + mem），选人页的队却和记住的对不上：不打、不替用户换队（11-07）。"""


def new_battle(pf, log_dir, quit_on_down=False):
    """按档案 style 建战斗对象（11-07）：没写 = SSBattle 通用循环；"fuhua_star" = 超弦 + 记忆战场陨影冰骸符华专属打法
    （ss_battle.fuhua_class）。SSBattle 按本模块的名字取，测试替换 ss_fight.SSBattle 照样生效。"""
    if pf.get("style") == "fuhua_star":
        return ss_battle.fuhua_class()(pf, log_dir, quit_on_down)
    return SSBattle(pf, log_dir, quit_on_down)


def run_battle(pf, log_dir, limit, quit_on_down=False):
    """开战后的战斗循环。连接断了（10-18 00:33 adb 服务被重启、触控管道断，整轮报错）就重连一次接着打这一场；
    再断往上抛（ss_run.fight_safe 接住）。quit_on_down：有人退场就放弃这场（res = died，11-01 高危区用）。
    返回 (res, 战斗界面消失的秒数, 第一次认出有人退场的秒数)。"""
    b = new_battle(pf, log_dir, quit_on_down)
    try:
        if not b.wait_battle(40):
            b.save("no_battle")
            raise ss_nav.NavError("开战后 40 秒没进战斗界面")
        b.t0 = now()
        b.log("进入战斗")
        b.wait_fight_start()
        b.run_steps(pf.get("opening", []))
        try:
            res = b.loop(limit)
        except LINK_ERRORS as e:
            used = now() - b.t0
            b.log(f"连接断了（{type(e).__name__}: {e}），{used:.0f} 秒处重连一次接着打")
            b.close()
            time.sleep(3)
            b = new_battle(pf, log_dir.with_name(log_dir.name + "_重连"), quit_on_down)
            if not b.wait_battle(15):
                raise
            b.t0 = now() - used                    # loop 按 t0 算限时和定时切人，接着原来的时间走
            b.log(f"重连上了，接着打（已用 {used:.0f} 秒）")
            res = b.loop(limit)
        gone = getattr(b, "hud_gone_at", None)
        b.log(f"结果 {res}，实际用时 {now() - b.t0:.1f} 秒（战斗界面消失在 {gone if gone is None else round(gone, 1)} 秒），按键计数 {b.counts}")
    finally:
        b.close()
    return res, gone, getattr(b, "down_at", None)


def fight(pf_path, zone=None, limit=600, log=print, row=1, env=None, quit_on_down=False, keep_team=False, mem=None, period=None):   # 10-27：420 → 600，量子边缘区换区停表，一场实际约 7 分半
    """打一场。zone 给了：从任意页进这个区 → 推荐阵容一键编队 → 打 → 结算回主页 → 结果记进档案；没给：游戏要停在选人页、打完只截结算页。
    quit_on_down：有人退场就放弃这场（res = died）；不给就不再切人、打完（11-01 作者定：高危区重开、前三区打完）。
    返回 {res, score, game_time, secs, full, row, down, log_dir}，down = 第一次认出有人退场的秒数（没人退场 None）。不认识的开战弹框只报不点（抛 NavError）。
    keep_team：不一键编队，用选人页现成的队（11-07 作者手配符华队试高危区），记录里 row 记 None、rec 记「现成的队」。
    mem（ss_lineup 模块）+ period（本期起点，ss_run.period_key）：11-07，记住每区用的队——
      推荐模式：选人页就是记下的「推荐第 row 行」→ 不点推荐直接打（rec 记「记住的队」）；对不上 → 一键编队、再记下；
      keep_team：选人页得是记下的「用户配的队」，对不上抛 TeamChanged（不打、不替用户换队，截图在 shots/）。"""
    pf_path = Path(pf_path)
    pf = json.loads(pf_path.read_text())
    if not pf.get("fox"):
        no_fox()
    rec, remember = None, False
    if zone:
        ss_nav.to_stage(log)
        it, img = ss_nav.to_team(zone, log)
        what = "user" if keep_team else f"row{row}"
        same, why = mem.check(zone, what, period, it, img) if mem else (False, "")
        if keep_team:
            if mem and not same:
                cv2.imwrite(str(HERE / "shots" / f"队对不上_{time.strftime('%m%d-%H%M%S')}.png"), img)
                raise TeamChanged(why)
            row, rec = None, "现成的队"
            log(f"  不一键编队：用选人页现成的队（{why if mem else '--keep-team'}）")
        elif same:
            rec = "记住的队"
            log(f"  不点推荐：选人页{why}，直接开打")
        else:
            if mem:
                log(f"  {why} → 推荐第 {row} 行一键编队")
            rec = ss_nav.apply_recommend(log, row=row)
            remember = mem is not None
    it, img = ss_nav.ocr()
    if ss_nav.page(it) != "team":
        raise ss_nav.NavError(f"不在选人页：{[t for t, _, _ in it][:20]}")
    if remember:
        mem.remember(zone, f"row{row}", period, it, img)
        log(f"  记下这一区现在的队：推荐第 {row} 行")
    log(f"  点「开始战斗」（{pf.get('zone')}）")
    ss_nav.tap(*ss_nav.TEAM_START, 3)
    it, img = ss_nav.ocr()
    words = [t for t, _, _ in it]
    if ss_nav.page(it) == "team" or ss_nav.has(it, "确定") or ss_nav.has(it, "确认") or ss_nav.has(it, "取消"):
        cv2.imwrite(str(HERE / "shots" / f"开战弹框_{time.strftime('%m%d-%H%M%S')}.png"), img)
        raise ss_nav.NavError(f"开战后还在选人页或弹了框，没处理：{words[:25]}")
    log_dir = HERE / "logs.nosync" / f"{pf['id']}_{time.strftime('%m%d-%H%M%S')}"
    res, gone, down = run_battle(pf, log_dir, limit, quit_on_down)
    time.sleep(3)
    it, img = ss_nav.ocr()
    cv2.imwrite(str(log_dir / "after.png"), img)
    out = {"res": res, "score": None, "game_time": None, "secs": None, "full": None, "row": row,
           "down": down, "log_dir": str(log_dir)}
    if zone:
        r, layer = ss_nav.settle(log)
        cap = 4000 if zone == "右" else 6000
        out.update(score=r.get("score"), game_time=r.get("time"), secs=r.get("secs"), full=(r.get("score") or 0) >= cap)
        pf.setdefault("results", []).append({
            "time": time.strftime("%m-%d %H:%M"), "zone": zone, "res": res, "full": out["full"], "game_time": r.get("time"),
            "score": r.get("score"), "layer": layer, "hud_gone_s": round(gone, 1) if gone else gone,
            "row": row, "rec": rec, "env": env, "down_s": None if down is None else round(down, 1), "log": log_dir.name})
        pf_path.write_text(json.dumps(pf, ensure_ascii=False, indent=1))
    else:
        out.update(ss_nav.read_result(it))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--limit", type=float, default=600,
                    help="战斗循环上限（秒）；关卡限时 5 分钟 + 出场 / 大招停表 / 换区停表余量（10-27 五个区域的关一场实际约 7 分半）")
    ap.add_argument("--zone", choices=list(ss_nav.NODES), help="从任意页进这个区（含推荐阵容一键编队），打完结算回主页并记档案")
    ap.add_argument("--keep-team", action="store_true",
                    help="带 --zone 时不一键编队，用选人页现成的队（11-07：高危区试用户手配的符华队）")
    a = ap.parse_args()
    r = fight(a.profile, a.zone, a.limit, lambda m: print(m, flush=True), keep_team=a.keep_team)
    print(f"{'打满' if r['full'] else '没打满'}：得分 {r['score']}，局内 {r['game_time']}；战斗目录 {r['log_dir']}", flush=True)


if __name__ == "__main__":
    main()
