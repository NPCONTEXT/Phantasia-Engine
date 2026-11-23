"""崩坏3 · 超弦空间 整跑（幻想引擎「崩坏3 · 超弦空间」，每天勾着播放也行）。
流程：进超弦空间（判断本期开没开）→ 读本期状态（剩余时间 / 积分 / 小组排名 / 预计奖杯 / 四区剩余积分）→
  勾了「主环境三区」：左（边缘区）、上（密集区）没满就打 → 右（高危区）凹分（最多 5 场）；
  勾了「特异区」：下（特异区）没满就打；
  换阵容（作者）：同一行阵容两场没打满 / 没明显提升就换推荐阵容下一行；前三区每区最多 4 场，好用的那行记进档案下次先用；
  三区都满了领深渊任务 → 再读一次本期状态。
规矩（作者/ 10-17 定）：配队 = 推荐阵容「官方推荐」+ 完整度优先 + 第一行一键编队；前三区打满 = 6000 分（超时按进度给分不算满）；
  深渊任务三区打满后再领；每周三、周日 22:00 结算，结算后到下期开放前「未开放」只报不打；凹分不因小组第一停（周日晚才是终局）。
用法（任务目录下）：../../bh3_auto/.venv/bin/python ss_run.py --subitems [--pick 1,2 | --pick none] [--dry]
  --dry：只进去读状态、说会打什么，不开战、不领奖。
"""
from __future__ import annotations

import argparse, datetime as dt, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "2026-10-10-记忆战场"))
import ss_nav                                   # noqa: E402
import ss_lineup                                # noqa: E402
from ss_fight import fight, LINK_ERRORS, TeamChanged   # noqa: E402

HEAD = ["进超弦空间", "读本期状态"]
LABELS = ["主环境三区（边缘·密集·高危凹分）", "特异区"]      # 作者定：按两队分，固定叫法，换期不用重勾
SUB_FILE = HERE / "勾选项.json"
PROFILES = {"左": "edge_try", "上": "dense_try", "下": "special_try", "右": "risk_try"}
NAMES = {"左": "边缘区", "上": "密集区", "下": "特异区", "右": "高危区"}
PER_ROW = 2            # 同一行阵容最多打几场：两场没打满 / 没明显提升就换下一行（作者）
MOB_MAX = 4            # 前三区每区一次播放最多打几场（= 试 2 行）
RISK_MAX = 5           # 高危区一次播放最多打几场（作者D2）
MIN_GAIN = 10          # 高危区「明显提升」：比本期最好多 10 分以上（我定的；10-17 70 秒丢 231 分，约快 3 秒）
ROWS = (1, 2, 3)       # 推荐阵容（官方推荐、完整度优先）一屏的行
_FOUGHT = 0            # 这次播放开过几场（fight_safe 里数）：打过仗，最后才顺手领任务奖励和战令（11-21）


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_sub_file():
    items = [{"pos": i + 1, "label": lb} for i, lb in enumerate(LABELS)]
    SUB_FILE.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "items": items}, ensure_ascii=False, indent=1))


def parse_pick(s, n):
    if s is None:
        return list(range(1, n + 1))
    if s.strip().lower() == "none":
        return []
    return sorted({int(x) for x in s.split(",") if x.strip().isdigit() and 1 <= int(x) <= n})


def start_game():
    import ma_run                                # 记忆战场的启动（日常脚本的登录 / 弹窗处理 → 主界面）
    ma_run.start_game()


_REMAIN = None         # (剩余分钟, 读到的时刻)：结算前保护用
SETTLE_GUARD = 5       # 离结算不到 5 分钟不开新的一场（10-18 最后一场 21:57 多才打完，靠人盯着）


def remain_text(st):
    if st.get("settling"):
        return "结算中"
    m = st.get("remain_min")
    if m is None:
        return "剩余?"
    if m >= 1440:
        return f"剩余{m // 1440}天{m % 1440 // 60}小时"
    if m >= 60:
        return f"剩余{m // 60}小时"
    return f"剩余{m}分"


def minutes_left():
    """按读到的剩余时间推算现在还剩几分钟；读不到 None。"""
    if _REMAIN is None:
        return None
    return _REMAIN[0] - (time.time() - _REMAIN[1]) / 60


def near_settle():
    m = minutes_left()
    if m is not None and m < SETTLE_GUARD:
        log(f"  离结算只剩约 {max(m, 0):.0f} 分钟，不开新的一场（结算前保护）")
        return True
    return False


def status(tag):
    """主页状态一行（给幻想引擎「事实」用，别带全角逗号）。返回 dict。"""
    global _REMAIN
    it, img = ss_nav.to_main(log)
    st = ss_nav.main_status(it, img)
    st["remain_min"] = ss_nav.remain_minutes(it)
    st["settling"] = ss_nav.settling(it)
    if st["remain_min"] is not None:
        _REMAIN = (st["remain_min"], time.time())
    me = st["me"]
    score = st["rows"][me]["score"] if me is not None else None
    tro = ss_nav.trophy_change(it, me)
    st.update(score=score, trophy=tro)
    tro_s = "?" if tro is None else f"{tro:+d}"
    log(f"本期状态（{tag}）：{remain_text(st)} 积分{score} 小组第{st['rank'] or '-'}名 预计奖杯{tro_s}")
    if tro is not None and tro < 0:
        log("  注意：按现在的排名结算会扣奖杯")
    return st


_ZONES = None


def zone_states(refresh=False):
    """四区状态；读一次留着（每读一遍要点四个节点、约 15 秒），打完一场再 refresh。"""
    global _ZONES
    if refresh or _ZONES is None:
        ss_nav.to_stage(log)
        _ZONES = ss_nav.zones(log)
    return _ZONES


def pf_path(pos):
    return HERE / "profiles" / f"{PROFILES[pos]}.json"


def env_key(pos):
    """本期这一区的环境：区名前缀 + 环境，如「狙击·远程」「协奏·交响」（关卡页读的，见 zone_states）；读不到 None。
    10-20 作者：「要不要记录环境，方便在高难度时复用经验」→ 每场结果记环境、最好的阵容行按环境分开记。"""
    info = (_ZONES or {}).get(pos) or {}
    name, env = info.get("name") or "", info.get("env") or ""
    pre = name.split("-")[0].strip() if "-" in name else ""
    m = re.search(r"([\u4e00-\u9fff]{2,4})环境", env)          # 识字常带个噪声符号：「⑦ 远程环境」
    if not pre or not m:
        return None
    return f"{pre}·{m.group(1)}"


def best_row(pos):
    """档案里记的「这个区在本期环境下用哪行阵容最好」（打满的那行 / 高危区分最高的那行）；本期环境没记过（或读不到环境）就第 1 行。
    10-20 起按环境分开记在 best_row_env，不再沿用别的环境的结论（旧的 best_row 字段只留作记录）。"""
    env = env_key(pos)
    try:
        r = (json.loads(pf_path(pos).read_text()).get("best_row_env") or {}).get(env) if env else None
        return r if r in ROWS else 1
    except Exception:
        return 1


def save_best_row(pos, row):
    env = env_key(pos)
    if not env:
        log(f"  {NAMES[pos]}没读到本期环境，这次的最好行（第 {row} 行）不记")
        return
    p = pf_path(pos)
    d = json.loads(p.read_text())
    m = d.setdefault("best_row_env", {})
    if m.get(env) != row:
        m[env] = row
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        log(f"  {NAMES[pos]}（{env}）以后先用推荐阵容第 {row} 行")


def recover(wait=360):
    """断线后：等游戏里这场自己结束（战斗界面没了），点掉结算回超弦空间；等不到返回 False。"""
    time.sleep(5)
    end = time.time() + wait
    while time.time() < end:
        try:
            it, img = ss_nav.ocr()
        except Exception as e:                       # adb 还没缓过来
            log(f"  截图失败（{type(e).__name__}），5 秒后再试")
            time.sleep(5)
            continue
        if ss_nav.page(it) in ("main", "stage", "team"):
            return True
        if ss_nav.has(it, "战斗得分") or ss_nav.has(it, "最高连击数"):
            ss_nav.settle(log)
            return True
        time.sleep(5)
    log(f"  断线后等了 {wait} 秒，画面还没回到超弦空间")
    return False


@ss_nav.battle("正在战斗（超弦空间）")      # 11-02 作者：战斗不能暂停（战斗里按住不放的多指触控）
def fight_safe(pos, row, quit_on_down=False, keep_team=False, pf=None):
    """打一场；fight 里重连一次还断，不让整轮停：等这场在游戏里结束、回到超弦空间，返回 None（算一场）。NavError 照旧往上抛。
    quit_on_down：有人退场就放弃这场（11-01 高危区用）；不给就不再切人、打完。
    11-07：带上 ss_lineup 和本期起点——选人页就是记下的队就不点推荐；keep_team + pf = 用户配的队 + 那份档案（对不上抛 TeamChanged）。"""
    global _FOUGHT
    _FOUGHT += 1
    try:
        return fight(pf or pf_path(pos), pos, log=log, row=row, env=env_key(pos), quit_on_down=quit_on_down,
                     keep_team=keep_team, mem=ss_lineup, period=period_key())
    except ss_nav.NavError:
        raise
    except LINK_ERRORS as e:
        log(f"  {NAMES[pos]}这一场连接断了（{type(e).__name__}: {e}），等游戏里这场结束再接着打")
        if not recover():
            raise
        return None


def down_note(r):
    """这场有人退场（前三区 / 高危区最后一场：之后没再切人、打完）就在结果后面注一句。"""
    d = r.get("down")
    return "" if d is None else f"（第 {d:.0f} 秒认出有人退场，之后没再切人）"


def row_order(pos):
    first = best_row(pos)
    return [first] + [r for r in ROWS if r != first]


def period_start(now=None):
    """本期开始 = 最近一次周三 / 周日 22:00（结算）之后。"""
    now = now or dt.datetime.now()
    for d in range(8):
        day = now - dt.timedelta(days=d)
        t = day.replace(hour=22, minute=0, second=0, microsecond=0)
        if day.weekday() in (2, 6) and t <= now:
            return t
    return now - dt.timedelta(days=7)


def period_key(now=None) -> str:
    """本期起点写成「YYYY-MM-DD HH:MM」，给 ss_lineup 认「同一期」（11-07）。"""
    return period_start(now).strftime("%Y-%m-%d %H:%M")


RISK_USER = HERE / "profiles" / "risk_fuhua.json"     # 11-07 作者：这一期高危区用他配的队 + 符华打法（档案 period = 本期起点才算）


def risk_user_mode():
    """这一期高危区用不用用户配的队：RISK_USER 档案的 period 就是本期起点 → 返回档案路径；否则 None（回到推荐阵容）。"""
    try:
        pf = json.loads(RISK_USER.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return RISK_USER if pf.get("period") == period_key() else None


def remind_new_period(write=True):
    """换期后：上一期高危区用的是用户配的队、这一期回到推荐阵容 → 记一行「提醒：…」（幻想引擎播放记录的事实），每期只提醒一次。"""
    try:
        pf = json.loads(RISK_USER.read_text())
    except (FileNotFoundError, ValueError):
        return
    cur = period_key()
    if not pf.get("period") or pf["period"] >= cur or pf.get("reminded") == cur:
        return
    log("提醒：新一期高危区回到推荐阵容（上一期用的是你配的符华队）")
    if write:
        pf["reminded"] = cur
        RISK_USER.write_text(json.dumps(pf, ensure_ascii=False, indent=1))


def period_best(pos, now=None):
    """档案 results 里本期已打出的最好分；没有 None。results 的 time 是「10-18 21:24」，没写年份。"""
    now = now or dt.datetime.now()
    start = period_start(now)
    best = None
    try:
        results = json.loads(pf_path(pos).read_text()).get("results", [])
    except Exception:
        return None
    for r in results:
        try:
            t = dt.datetime.strptime(f"{now.year}-{r['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if t > now:
            t = t.replace(year=now.year - 1)
        if t >= start and r.get("score") is not None and (best is None or r["score"] > best):
            best = r["score"]
    return best


def risk_best(z):
    """高危区本期最好 = 4000 − 剩余积分；读出来比档案里本期已打出的还低 = 读错了（10-18 两次把 180 读成 1160），用档案的。"""
    left = z["右"]["left"]
    v = 4000 - left if left is not None and 0 <= left <= 4000 else None
    pb = period_best("右")
    if pb is not None and (v is None or v < pb):
        if left is not None:
            log(f"  高危区剩余积分读成 {left}（算出最好 {v}），比本期已打出的 {pb} 还低 → 按 {pb} 算")
        return pb
    return v


def fill_zone(pos, a):
    """前三区：没满就打。同一行阵容打 PER_ROW 场没打满就换下一行（作者），一次播放最多 MOB_MAX 场。返回 ok / 原因。"""
    z = zone_states()
    left, on = z[pos]["left"], z[pos]["enabled"]
    if left == 0:
        log(f"  {NAMES[pos]}已打满")
        return "ok"
    if not on:
        return f"{NAMES[pos]}还锁着"
    rows = row_order(pos)
    if a.dry:
        log(f"  [--dry] {NAMES[pos]}剩 {left} 分没打 → 会打（先用推荐阵容第 {rows[0]} 行）")
        return "ok"
    n, tried, broken, downs = 0, [], 0, 0
    for row in rows:
        tried.append(row)
        for k in range(PER_ROW):
            if n >= MOB_MAX or broken >= 2 or near_settle():
                break
            r = fight_safe(pos, row)
            n += 1
            if r is None:
                broken += 1
                continue
            broken = 0
            if r.get("res") == "stairs":               # 10-25：清层后要爬楼梯，脚本还不会走 → 换阵容也没用，这区停
                z = zone_states(refresh=True)
                log(f"  {NAMES[pos]}这关要爬楼梯（清一层后要走上楼），脚本还不会自动走：这一区不再重试")
                return f"{NAMES[pos]}要爬楼梯，脚本还不会走，请手动打一次（剩 {z[pos]['left']} 分）"
            zone_states(refresh=True)
            downs += r.get("down") is not None
            log(f"  {NAMES[pos]}第 {n} 场（第 {row} 行）：局内 {r['game_time']} 得分 {r['score']}{'（打满）' if r['full'] else ''}{down_note(r)}")
            if r["full"]:
                save_best_row(pos, row)
                return "ok"
        if n >= MOB_MAX or broken >= 2 or near_settle():
            break
        log(f"  {NAMES[pos]}第 {row} 行打了 {PER_ROW} 场没打满 → 换阵容")
    z = zone_states()
    return (f"{NAMES[pos]}打了 {n} 场没打满（试了第 {'、'.join(map(str, tried))} 行、剩 {z[pos]['left']} 分"
            f"{f'、{downs} 场有人退场' if downs else ''}）")


def push_risk(a):
    """高危区：没打过先打通；再凹分。同一行连续 PER_ROW 场没明显提升（< MIN_GAIN）就换下一行（作者），
    所有行都试过还没提升、或打满 RISK_MAX 场就停。返回结果文字。
    11-01 作者定：有人退场就放弃这场重开（算一场、算一次没提升）；最后一场有人退场也打完，留个分。"""
    z = zone_states()
    if not z["右"]["enabled"]:
        return "高危区还锁着（前面几区没打完）"
    best = risk_best(z)
    rows = row_order("右")
    log(f"  高危区现在最好 {best}（剩余积分 {z['右']['left']}）")
    if best is not None and best >= 4000:
        return "ok"
    upf = risk_user_mode()
    if upf:
        return push_risk_user(a, upf, best)
    remind_new_period(write=not a.dry)
    if a.dry:
        log(f"  [--dry] 会凹高危区：先用第 {rows[0]} 行，同一行连续 {PER_ROW} 场没提升换下一行，最多 {RISK_MAX} 场")
        return "ok"
    n, stall, ri, best_r, broken = 0, 0, 0, None, 0
    times = []
    while n < RISK_MAX and ri < len(rows):
        if near_settle():
            break
        row = rows[ri]
        r = fight_safe("右", row, quit_on_down=n < RISK_MAX - 1)
        n += 1
        if r is None:
            times.append(f"{row}行断线")
            broken += 1
            if broken >= 2:
                log("  连着两场连接断了，这次不打了")
                break
            continue
        broken = 0
        if r.get("res") == "stairs":                   # 10-25：同上，高危区要是也分层要走路，这次不凹了
            log("  高危区这关要爬楼梯，脚本还不会自动走：这次不凹了")
            return "高危区要爬楼梯，脚本还不会走，请手动打"
        if r.get("res") == "died":                     # 11-01：有人退场、已放弃（分数没变，不用重读四区）
            times.append(f"{row}行退场")
            log(f"  高危区第 {n} 场（第 {row} 行）：有人退场，已放弃这场，重开")
            no_gain = True
        else:
            zone_states(refresh=True)
            sc = r["score"] or 0
            times.append(f"{row}行{r['game_time']}/{sc}")
            gain = None if best is None else sc - best
            if best is None or sc > best:
                log(f"  高危区第 {n} 场（第 {row} 行）：局内 {r['game_time']} 得分 {sc} → 新最好（原 {best}）{down_note(r)}")
                best, best_r = sc, row
                save_best_row("右", row)              # 当场存：10-18 00:33 整轮中断，前面打出的最好行没存上
            else:
                log(f"  高危区第 {n} 场（第 {row} 行）：局内 {r['game_time']} 得分 {sc}（最好 {best}）{down_note(r)}")
            no_gain = gain is not None and gain < MIN_GAIN
        if no_gain:
            stall += 1
            if stall >= PER_ROW:
                ri, stall = ri + 1, 0
                if ri < len(rows):
                    log(f"  第 {row} 行连续 {PER_ROW} 场没明显提升 → 换第 {rows[ri]} 行")
        else:
            stall = 0
    if best_r is not None:
        save_best_row("右", best_r)
    log(f"  高危区这次打了 {n} 场（{' '.join(times)}），本期最好 {best}")
    return "ok"


def push_risk_user(a, upf, best):
    """11-07 作者定（只这一期）：高危区用他配的队（选人页现成的）+ 符华专属打法（档案 risk_fuhua.json），不点推荐、不换行；
    最多 RISK_MAX 场，连着 PER_ROW 场没明显提升就停；选人页的队和记住的对不上（被改过）→ 不打，不替他换队（ss_lineup.check）。"""
    if a.dry:
        ss_nav.to_stage(log)
        it, img = ss_nav.to_team("右", log)
        ok, why = ss_lineup.check("右", "user", period_key(), it, img)
        log(f"  [--dry] 高危区本期用用户配的队：选人页{why}；{'会直接开打' if ok else '不会打'}（最多 {RISK_MAX} 场、连着 {PER_ROW} 场没提升就停）")
        return "ok" if ok else "高危区的队和记住的不一样、没打"
    n, stall, broken, times = 0, 0, 0, []
    while n < RISK_MAX:
        if near_settle():
            break
        try:
            r = fight_safe("右", None, quit_on_down=n < RISK_MAX - 1, keep_team=True, pf=upf)
        except TeamChanged as e:
            log(f"  高危区选人页的队和记住的不一样（{e}）：这次不打高危区、不替你换队（截图在 shots/）")
            return "高危区的队和记住的不一样、没打"
        n += 1
        if r is None:
            times.append("断线")
            broken += 1
            if broken >= 2:
                log("  连着两场连接断了，这次不打了")
                break
            continue
        broken = 0
        if r.get("res") == "stairs":
            log("  高危区这关要爬楼梯，脚本还不会自动走：这次不凹了")
            return "高危区要爬楼梯、脚本还不会走、请手动打"
        if r.get("res") == "died":
            times.append("退场")
            log(f"  高危区第 {n} 场（用户配的队）：有人退场，已放弃这场，重开")
            no_gain = True
        else:
            zone_states(refresh=True)
            sc = r["score"] or 0
            times.append(f"{r['game_time']}/{sc}")
            gain = None if best is None else sc - best
            if best is None or sc > best:
                log(f"  高危区第 {n} 场（用户配的队）：局内 {r['game_time']} 得分 {sc} → 新最好（原 {best}）{down_note(r)}")
                best = sc
            else:
                log(f"  高危区第 {n} 场（用户配的队）：局内 {r['game_time']} 得分 {sc}（最好 {best}）{down_note(r)}")
            no_gain = gain is not None and gain < MIN_GAIN
        stall = stall + 1 if no_gain else 0
        if stall >= PER_ROW:
            log(f"  连着 {PER_ROW} 场没明显提升，这次不凹了")
            break
    log(f"  高危区这次打了 {n} 场（用户配的队 + 符华打法：{' '.join(times)}），本期最好 {best}")
    return "ok"


def claim_after():
    """打过仗以后顺手把任务奖励和战令等级奖励领掉（作者选的补法「超弦打完顺手领」）。
    日常这首排在超弦前面：任务页「无尽深渊」那条要超弦打完才完成，打完升的战令等级也是，当晚没人领。
    用日常脚本现成的两步（代码在 bh3_daily.py，这里只调用）。不打「== 」行、不进汇总（引擎按歌词对步骤）；
    出岔子写「收尾领取未完成」。不往外抛异常。"""
    log("  打过仗了，顺手领任务奖励和战令等级奖励（日常脚本的两步）")
    try:
        import ma_run                               # 借它找到日常脚本目录
        if str(ma_run.DAILY) not in sys.path:
            sys.path.insert(0, str(ma_run.DAILY))
        import bh3_daily
        bot = bh3_daily.Bot()
    except Exception as e:
        log(f"  收尾领取未完成：没起来（{type(e).__name__}: {e}）")
        return
    for step in ("claim", "bp"):
        name = bh3_daily.NAMES[step]
        bot.note = None
        try:
            getattr(bot, f"step_{step}")()
        except Exception as e:
            log(f"  收尾领取未完成：{name} 出错（{type(e).__name__}: {e}）")
            continue
        log(f"  收尾领取未完成：{name}（{bot.note}）" if bot.note else f"  收尾领取：{name} 做了")


def main_subitems(a):
    global _FOUGHT
    _FOUGHT = 0
    picks = parse_pick(a.pick, len(LABELS))
    write_sub_file()
    log(f"—— 超弦空间整跑开始（--pick {a.pick}{'，--dry' if a.dry else ''}）")
    log("子项：" + "|".join(LABELS))
    global _ZONES
    _ZONES = None
    result, cur = {}, None
    picked = [LABELS[p - 1] for p in picks]
    try:
        cur = HEAD[0]; log(f"== {cur}")
        start_game()
        state, words, _ = ss_nav.enter(log)
        result[cur] = "ok"
        cur = HEAD[1]; log(f"== {cur}")
        if state == "settling":
            status("开始")                          # 结算期主页照样能读排名、预计奖杯
            log("本期结算中（每周三、周日 22:00–22:30，开始战斗是灰的），什么都不打")
            result[cur] = "ok"
            for lb in picked:
                result[lb] = "ok"
        elif state == "preparing":                  # 11-05：结算后到下期开放前，主页能进、开始战斗灰，右下「准备中 01天13小时」
            log(f"本期准备中（上期已结算、下期还没开：{ss_nav.preparing(words) or '准备中'}；开始战斗是灰的），什么都不打")
            result[cur] = "ok"
            for lb in picked:
                result[lb] = "ok"
            log("本期状态（开始）：准备中")
        elif state != "open":
            log(f"本期未开放（每周三、周日 22:00 结算，到下期开放前进不去）：{words[:12]}")
            result[cur] = "ok"
            for lb in picked:
                result[lb] = "ok"
            log("本期状态（开始）：未开放")
        else:
            st0 = status("开始")
            z = zone_states(refresh=True)
            if st0["remain_min"] is not None and st0["remain_min"] < 240:
                log(f"  本期只剩 {remain_text(st0)[2:]} 就结算")
            result[cur] = "ok"
            log(f"  这次勾了：{picks or '一个都没勾（只看状态）'}")
            if 1 in picks:
                cur = LABELS[0]; log(f"== {cur}")
                msgs = [m for m in (fill_zone("左", a), fill_zone("上", a)) if m != "ok"]
                if 2 in picks:                     # 高危区要前面几区都动过才解锁（10-17：边缘满 + 密集满 + 特异打过）
                    cur = LABELS[1]; log(f"== {cur}")
                    result[cur] = fill_zone("下", a)
                    cur = LABELS[0]
                m = push_risk(a)
                if m != "ok":
                    msgs.append(m)
                result[LABELS[0]] = "ok" if not msgs else "、".join(msgs)
            elif 2 in picks:
                cur = LABELS[1]; log(f"== {cur}")
                result[cur] = fill_zone("下", a)
            z = zone_states()
            if not a.dry and all(z[p]["left"] == 0 for p in ("左", "上", "下")):
                n = ss_nav.claim_tasks(log)
                log(f"  深渊任务：领了 {n} 项" if n else "  深渊任务：没有可领的（领过了）")
            st1 = status("结束")
            tro = "?" if st1["trophy"] is None else f"{st1['trophy']:+d}"
            log(f"本期：积分 {st0['score']} → {st1['score']}；小组第 {st1['rank']} 名；预计奖杯 {tro}；"
                f"四区剩余积分 左{z['左']['left']} 上{z['上']['left']} 下{z['下']['left']} 右{z['右']['left']}")
            if not a.dry and _FOUGHT:               # 11-21：这次打过仗才领（没开、准备中、只看状态都不领）
                claim_after()
    except Exception as e:
        log(f"  出错：{type(e).__name__}: {e}")
        result[cur] = f"出错：{e}".replace("，", "、").replace("=", "：")[:120]
    for st in HEAD + picked:
        result.setdefault(st, "没做到（前面出错）")
    log("—— 整跑结束")
    order = HEAD + picked + [k for k in result if k not in HEAD + picked]      # 汇总顺序和歌词一致
    log("完成：" + "，".join(f"{k}={result[k]}" for k in order if k in result))
    return 0 if all(v == "ok" for v in result.values()) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subitems", action="store_true", help="幻想引擎子项模式（必带）")
    ap.add_argument("--pick", default=None, help="勾了的子项位置 1,2；none = 都不勾；不带 = 全勾")
    ap.add_argument("--dry", action="store_true", help="只进去读状态、说会打什么，不开战不领奖")
    a = ap.parse_args()
    sys.exit(main_subitems(a))


if __name__ == "__main__":
    main()
