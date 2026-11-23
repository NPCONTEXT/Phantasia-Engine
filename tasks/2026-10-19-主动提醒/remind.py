"""幻想引擎主动提醒：到点看播放记录，该做的没做完，就用 Server酱 推到微信（方糖服务号）。

规则：
- 每日：崩3、妮姬、绝区零 04:00 刷新 → 02:00 提醒。
  当天（按刷新时刻切的游戏日）播放结果 ok / partial / done 算跑过；abnormal / stopped / unmet 或没跑算没跑成。
- 周常：记忆战场周一 04:00 结算、乐土九档周一 04:00 刷新 → 周一 02:00 提醒；
  超弦空间周三、周日 22:00 结算 → 前一晚（周二、周六）23:00 提醒。
  本期有一次结果 ok 算做完（乐土要 done，超弦在「未开放 / 结算中」时跑的不算）。
- 提醒时刻到截止之间都算该提醒：Mac 睡着错过了，醒来还没过截止就补发；过了截止不发。
- 同一次检查里该发的合成一条；顺序播放正在跑（有工具进程，或 3 分钟内有日志在写）先不发，跑完再查。
- 同一项、同一个截止只发一次（状态.json 记着）。
只读幻想引擎的 runs/history.jsonl、runs/state.json、tools.json、logs/；不改它。

用法：
  python3 remind.py                                  正常检查（launchd 定时调用）
  python3 remind.py --now "2026-10-20 02:00" --dry   按指定时刻检查，只打印、不发、不写状态
  python3 remind.py --replay 2026-10-08 2026-10-19   回放：这些日子里每个提醒时刻会发什么
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGG = HERE.parents[1]                       # 项目根目录（Phantasia-Engine/）
HISTORY = AGG / "runs" / "history.jsonl"
UI_STATE = AGG / "runs" / "state.json"
TOOLS = AGG / "tools.json"
LOGS = AGG / "logs"
STATE = HERE / "状态.json"
LOG = HERE / "remind.log"
KEY_FILE = Path.home() / ".config" / "phantasia-engine" / "serverchan_sendkey"
API = "https://sctapi.ftqq.com/{key}.send"   # 官方文档：POST，title、desp（Markdown），返回 code 0 = 成功

RAN = ("ok", "partial", "done")              # 每日：算跑过
RESULT_CN = {"ok": "完成", "partial": "部分完成", "done": "完成", "abnormal": "出错",
             "stopped": "被停止", "unmet": "没达成"}
WEEK = "一二三四五六日"
LEAD = timedelta(hours=2)                    # 每日、记忆战场、乐土：截止前 2 小时提醒（作者）
TITLE_MAX = 20                               # 免费版卡片只显示标题；测试过 20 字以内显示完整
BUSY_SECONDS = 180                           # 这么久内有日志在写，就当顺序播放还在跑

# (工具 id, 标题短名, 说明里的名字, 刷新时刻)
DAILY = [("bh3_daily", "崩3", "崩坏3 · 日常/周常", 4),
         ("nikke_daily", "妮姬", "妮姬 · 日常", 4),
         ("zzz_daily", "绝区零", "绝区零 · 日常", 4)]


# ---------- 读播放记录 ----------
def load_history(path=HISTORY):
    """读不到就让它报错（不当成「什么都没跑」，否则会乱发）：后台定时任务可能被 macOS 挡在桌面文件夹外。"""
    rows = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("id") and isinstance(r.get("start"), (int, float)):
            rows.append(r)
    rows.sort(key=lambda r: r["start"])
    return rows


def runs_of(hist, tid, start, end):
    a, b = start.timestamp(), end.timestamp()
    return [r for r in hist if r["id"] == tid and a <= r["start"] < b]


def last_before(hist, tid, t):
    rs = [r for r in hist if r["id"] == tid and r["start"] < t.timestamp()]
    return rs[-1] if rs else None


def checked_tools():
    """顺序播放里勾着的工具；读不到返回 None（不提示）。"""
    try:
        return set(json.loads(UI_STATE.read_text(encoding="utf-8")).get("checked") or [])
    except (OSError, ValueError, AttributeError):
        return None


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def reason_of(r):
    for m in r.get("missed") or []:
        if isinstance(m, dict) and m.get("reason"):
            return m["reason"]
    return r.get("outcome") or ""


def describe(r):
    """一次播放的简述：「10-18 00:18 部分完成：出错：…」。"""
    why = reason_of(r)
    text = f"{fmt(r['start'])} {RESULT_CN.get(r.get('result'), r.get('result'))}"
    return f"{text}：{why[:60]}" if why else text


# ---------- 各项该不该提醒 ----------
def _item(key, short, name, full, kind, deadline, remind_at, done, why, verb, label):
    """short：几项合一条时标题里用的短名；name：只有这一项时标题里的名字。"""
    return {"key": key, "short": short, "name": name, "full": full, "kind": kind, "deadline": deadline,
            "remind_at": remind_at, "done": done, "why": why, "verb": verb, "label": label}


def daily_checks(hist, now):
    out = []
    for tid, short, full, reset_h in DAILY:
        deadline = now.replace(hour=reset_h, minute=0, second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)
        if now < deadline - LEAD:
            continue
        day_start = deadline - timedelta(days=1)
        rs = runs_of(hist, tid, day_start, deadline)
        good = [r for r in rs if r.get("result") in RAN]
        if good:
            why = f"今天已跑（{describe(good[-1])}）"
        elif rs:
            why = f"今天跑过但没跑成（{describe(rs[-1])}）"
        else:
            last = last_before(hist, tid, day_start)
            why = f"今天还没跑（上次 {fmt(last['start'])}）" if last else "今天还没跑"
        out.append(_item(f"daily:{tid}@{deadline:%Y%m%d%H%M}", short, short + "日常", full, "daily",
                         deadline, deadline - LEAD, bool(good), why, "还没跑", f"{deadline:%H:%M}刷新"))
    return out


def next_weekday_time(now, weekday, hour):
    """now 之后（不含 now）最近的「周 weekday 的 hour:00」，weekday 0 = 周一。"""
    t = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=(weekday - now.weekday()) % 7)
    return t if t > now else t + timedelta(days=7)


def weekly_checks(hist, now):
    out = []
    # 记忆战场：周二 04:00 开、周一 04:00 结算（作者确认）
    deadline = next_weekday_time(now, 0, 4)
    if now >= deadline - LEAD:
        start = deadline - timedelta(days=6)
        rs = runs_of(hist, "bh3_memorial_arena", start, deadline)
        done = any(r.get("result") == "ok" for r in rs)
        if done:
            why = "本期已打完"
        elif rs:
            why = f"本期还没打完（{describe(rs[-1])}）"
        else:
            why = "本期还没打"
        out.append(_item(f"weekly:ma@{deadline:%Y%m%d%H%M}", "记忆战场", "记忆战场", "崩坏3 · 记忆战场",
                         "weekly", deadline, deadline - LEAD, done, why, "还没打完", f"{deadline:%H:%M}结算"))
    # 乐土九档：周一 04:00 刷新（10-07 核实）
    deadline = next_weekday_time(now, 0, 4)
    if now >= deadline - LEAD:
        start = deadline - timedelta(days=7)
        rs = runs_of(hist, "bh3_elysium_fuhua", start, deadline)
        done = any(r.get("result") == "done" or "九档" in (r.get("outcome") or "") for r in rs)
        if done:
            why = "本周九档已领"
        elif rs:
            why = f"本周九档还没领（{describe(rs[-1])}）"
        else:
            why = "本周还没跑"
        out.append(_item(f"weekly:er@{deadline:%Y%m%d%H%M}", "乐土", "乐土九档", "崩坏3 · 乐土九档",
                         "weekly", deadline, deadline - LEAD, done, why, "还没领", f"{deadline:%H:%M}刷新"))
    # 超弦空间：周三、周日 22:00 结算，前一晚 23:00 提醒（作者）
    deadline = min(next_weekday_time(now, 2, 22), next_weekday_time(now, 6, 22))
    remind_at = deadline - timedelta(hours=23)
    if now >= remind_at:
        prev = deadline - timedelta(days=3 if deadline.weekday() == 2 else 4)
        rs = runs_of(hist, "bh3_superstring", prev, deadline)
        valid = [r for r in rs if not any(w in (r.get("outcome") or "") for w in ("未开放", "结算中"))]
        done = any(r.get("result") == "ok" for r in valid)
        if done:
            why = "本期已打满"
        elif valid:
            why = f"本期还没打满（{describe(valid[-1])}）"
        else:
            why = "本期还没打"
        out.append(_item(f"weekly:ss@{deadline:%Y%m%d%H%M}", "超弦", "超弦", "崩坏3 · 超弦空间", "weekly",
                         deadline, remind_at, done, why, "还没打满",
                         f"周{WEEK[deadline.weekday()]}{deadline:%H:%M}结算"))
    return out


def checks(hist, now):
    return daily_checks(hist, now) + weekly_checks(hist, now)


# ---------- 拼消息 ----------
def make_title(items):
    labels = {i["label"] for i in items}
    label = items[0]["label"] if len(labels) == 1 else f"{min(i['deadline'] for i in items):%H:%M}前"
    if len(items) == 1:
        cand = [f"【雨澜】{items[0]['name']}{items[0]['verb']}·{label}"]
    else:
        verbs = {i["verb"] for i in items}
        verb = verbs.pop() if len(verbs) == 1 else "还没做"
        cand = [f"【雨澜】{'、'.join(i['short'] for i in items)}{verb}·{label}"]
    if all(i["kind"] == "daily" for i in items):
        cand.append(f"【雨澜】{len(items)}个日常还没跑·{label}")
    cand.append(f"【雨澜】{len(items)}项还没做·{label}")
    for c in cand:
        if len(c) <= TITLE_MAX:
            return c
    return cand[-1][:TITLE_MAX]


def make_desp(items, checked=None):
    lines = []
    for dl in sorted({i["deadline"] for i in items}):
        lines.append(f"**截止 {dl:%m-%d} 周{WEEK[dl.weekday()]} {dl:%H:%M}**")
        lines.append("")
        for i in (x for x in items if x["deadline"] == dl):
            tail = ""
            tid = i["key"].split(":", 1)[1].split("@", 1)[0]
            tid = {"ma": "bh3_memorial_arena", "er": "bh3_elysium_fuhua", "ss": "bh3_superstring"}.get(tid, tid)
            if checked is not None and tid not in checked:
                tail = "；顺序播放里没勾它"
            lines.append(f"- {i['full']}：{i['why']}{tail}")
        lines.append("")
    lines.append("幻想引擎的提醒：只在没做完时发，做完了就不会再来。")
    return "\n".join(lines)


# ---------- 顺序播放在不在跑 ----------
def tool_scripts():
    try:
        tools = json.loads(TOOLS.read_text(encoding="utf-8")).get("tools") or []
    except (OSError, ValueError):
        return []
    names = []
    for t in tools:
        for part in t.get("command") or []:
            if isinstance(part, str) and part.endswith(".py"):
                names.append(part)
                break
    return names


def busy():
    """顺序播放 / 单独播放正在跑：返回原因；没在跑返回空串。"""
    names = tool_scripts()
    try:
        ps = subprocess.run(["/bin/ps", "-axo", "command"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        ps = ""
    for line in ps.splitlines():
        if "remind.py" in line:
            continue
        for n in names:
            if n in line:
                return f"有工具在跑（{n}）"
    newest = max((p.stat().st_mtime for p in LOGS.glob("*.log")), default=0)
    if time.time() - newest < BUSY_SECONDS:
        return f"{int(time.time() - newest)} 秒前还有日志在写"
    return ""


# ---------- 发送 ----------
def send(title, desp):
    """返回 (发出去了没有, 说明, 要不要下次再试)。网络错误等一会儿重试一次；接口报错不重试（失败的调用也算条数）。"""
    try:
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False, f"没有 SendKey 文件 {KEY_FILE}", False
    if not key.startswith("SCT"):
        return False, "SendKey 文件内容不是 SCT 开头", False
    body = json.dumps({"title": title, "desp": desp}, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(2):
        if attempt:
            time.sleep(20)
        req = urllib.request.Request(API.format(key=key), data=body, method="POST",
                                     headers={"Content-Type": "application/json;charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
        except Exception as e:  # 网络没通（刚醒来常见）：等 20 秒再试一次
            last = f"{type(e).__name__}: {e}".replace(key, "***")
            continue
        try:
            resp = json.loads(raw)
        except ValueError:
            resp = {}
        if isinstance(resp, dict) and resp.get("code") == 0:
            pid = (resp.get("data") or {}).get("pushid") if isinstance(resp.get("data"), dict) else None
            return True, f"pushid {pid}", False
        msg = resp.get("message") if isinstance(resp, dict) else ""
        return False, f"接口报错：{(msg or raw[:120]).replace(key, '***')}", False
    return False, f"网络错误：{last}", True


# ---------- 状态和日志 ----------
def load_state():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(s, dict):
            s.setdefault("sent", {})
            s.setdefault("seen", {})
            return s
    except (OSError, ValueError):
        pass
    return {"sent": {}, "seen": {}}


def prune(state, now):
    cut = (now - timedelta(days=14)).strftime("%Y%m%d%H%M")
    for k in ("sent", "seen"):
        state[k] = {key: v for key, v in state[k].items() if key.rsplit("@", 1)[-1] >= cut}


def log(line, now):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now:%m-%d %H:%M:%S}] {line}\n")


# ---------- 主流程 ----------
def run(now, dry=False, ignore_busy=False):
    hist = load_history()
    state = load_state()
    items = checks(hist, now)
    due = [i for i in items if not i["done"] and i["key"] not in state["sent"]]
    if dry:
        for i in items:
            print(f"  {i['key']}：{'已做完' if i['done'] else '没做完'}，{i['why']}")
        if due:
            print("  会发：" + make_title(due))
            print("  " + make_desp(due, checked_tools()).replace("\n", "\n  "))
        else:
            print("  不发")
        return 0
    for i in items:                      # 每项的判断变了才写日志，免得每 10 分钟一行
        seen = f"{'已做完' if i['done'] else '没做完'}：{i['why']}"
        if state["seen"].get(i["key"]) != seen:
            state["seen"][i["key"]] = seen
            log(f"{i['key']} {seen}", now)
    rc = 0
    if due:
        why_busy = "" if ignore_busy else busy()
        if why_busy:
            log(f"该发 {len(due)} 项，但{why_busy}，跑完再查", now)
        else:
            title, desp = make_title(due), make_desp(due, checked_tools())
            ok, info, retry = send(title, desp)
            log(f"{'发出' if ok else '没发出'}「{title}」（{info}）", now)
            if ok or not retry:          # 发出去了，或接口报错（不重试，免得用掉额度）
                for i in due:
                    state["sent"][i["key"]] = f"{now:%Y-%m-%dT%H:%M:%S}{'' if ok else ' 失败'}"
            rc = 0 if ok else 1
    prune(state, now)
    state["last_check"] = f"{now:%Y-%m-%dT%H:%M:%S}"   # 核对定时任务真在跑、真读到了记录
    state["history_rows"] = len(hist)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return rc


def replay(first, last):
    """回放：只用当时已经开跑的记录，看每个提醒时刻会不会发、发什么（不看「在不在跑」）。"""
    hist_all = load_history()
    day = datetime.strptime(first, "%Y-%m-%d")
    end = datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
    while day < end:
        times = [day.replace(hour=2), day.replace(hour=3)]
        if day.weekday() in (1, 5):          # 周二、周六 23:00 超弦
            times.append(day.replace(hour=23))
        for t in times:
            hist = [r for r in hist_all if r["start"] < t.timestamp()]
            running = [r for r in hist if r.get("end", 0) > t.timestamp()]
            fresh = [i for i in checks(hist, t) if not i["done"] and i["remind_at"] == t]
            tag = "（这时有工具在跑，实际会等跑完再查）" if running else ""
            if fresh:
                print(f"{t:%m-%d} 周{WEEK[t.weekday()]} {t:%H:%M} 发「{make_title(fresh)}」{tag}")
                for i in fresh:
                    print(f"    {i['full']}：{i['why']}")
            else:
                print(f"{t:%m-%d} 周{WEEK[t.weekday()]} {t:%H:%M} 不发{tag}")
        day += timedelta(days=1)


def selftest(now):
    """装好后从后台发一条测试消息：证明读得到播放记录、SendKey，也连得上网。"""
    hist = load_history()
    desp = (f"后台定时任务发的测试消息（{now:%m-%d %H:%M}）：读到播放记录 {len(hist)} 条，SendKey 和网络都通。\n\n"
            "以后只在没做完时提醒：每天 02:00（崩3、妮姬、绝区零；周一加记忆战场、乐土九档），"
            "周二、周六 23:00（超弦空间）。")
    ok, info, _ = send("【雨澜】提醒已装好·测试", desp)
    log(f"自检{'发出' if ok else '没发出'}（读到播放记录 {len(hist)} 条；{info}）", now)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="幻想引擎主动提醒")
    ap.add_argument("--now", help="按这个时刻检查，如 \"2026-10-20 02:00\"")
    ap.add_argument("--dry", action="store_true", help="只打印，不发、不写状态")
    ap.add_argument("--replay", nargs=2, metavar=("起", "止"), help="回放这段日子，如 2026-10-08 2026-10-19")
    ap.add_argument("--selftest", action="store_true", help="发一条「提醒已装好」测试消息（会用掉 1 条额度）")
    a = ap.parse_args(argv)
    if a.replay:
        replay(*a.replay)
        return 0
    if a.selftest:
        return selftest(datetime.now().replace(microsecond=0))
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else datetime.now().replace(microsecond=0)
    return run(now, dry=a.dry)


if __name__ == "__main__":
    sys.exit(main())
