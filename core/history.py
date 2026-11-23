"""播放记录(运行记录): runs/history.jsonl，一行一条；另有界面小状态 runs/state.json。"""
import json
import time
from pathlib import Path

from core import registry

RUNS_DIR = registry.AGG_DIR / "runs"
HISTORY = RUNS_DIR / "history.jsonl"
STATE = RUNS_DIR / "state.json"

# result 取值
OK, PARTIAL, DONE, ABNORMAL, STOPPED = "ok", "partial", "done", "abnormal", "stopped"
UNMET = "unmet"   # 脚本自己正常结束了，但没达成目标（例如重打多次仍未三星）
COMPLETE = (OK, PARTIAL, DONE, UNMET)  # 算「完整播放过一次」的结果，用来估常用时长
BAD = (PARTIAL, ABNORMAL, UNMET)       # 需要用户留意的结果


def load(limit: int = 500) -> list[dict]:
    """读最近 limit 条，新的在前。坏行跳过。"""
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            rec = json.loads(ln)
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
        except Exception:
            continue
    out.reverse()
    return out


def append(rec: dict) -> None:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_state() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(d: dict) -> None:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_name(STATE.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except Exception:
        pass


def _n_picks(rec: dict) -> int | None:
    """带子项的歌这条记录勾了几个子项: 没有 "picks" 字段（旧记录 / 普通的歌）或读不懂 = None；
    "picks" 为 None（开播时还没有名单、没传勾选）按 0 个算。"""
    if "picks" not in rec:
        return None
    p = rec.get("picks")
    if p is None:
        return 0
    return len(p) if isinstance(p, list) else None


def avg_duration(records: list[dict], tool_id: str, n: int = 5, n_picks: int | None = None) -> float | None:
    """最近 n 次完整播放的平均耗时(秒)；一次都没有返回 None。
    n_picks: 带子项的歌下次会勾几个子项；给了就优先只用勾选个数相同的完整记录，一条都没有再按全部记录算。"""
    recs = [r for r in records
            if r.get("id") == tool_id and r.get("result") in COMPLETE and r.get("dur")]
    if n_picks is not None:
        recs = [r for r in recs if _n_picks(r) == n_picks] or recs
    durs = [r.get("dur", 0) for r in recs]
    durs = durs[:n]
    return sum(durs) / len(durs) if durs else None


def fmt_when(ts: float, now: float | None = None) -> str:
    """今天 05:48 / 昨天 05:48 / 前天 05:52 / 9月30日 06:10"""
    now = time.time() if now is None else now
    t, n = time.localtime(ts), time.localtime(now)
    day0 = time.mktime((n.tm_year, n.tm_mon, n.tm_mday, 0, 0, 0, 0, 0, -1))
    hm = time.strftime("%H:%M", t)
    if ts >= day0:
        return f"今天 {hm}"
    if ts >= day0 - 86400:
        return f"昨天 {hm}"
    if ts >= day0 - 2 * 86400:
        return f"前天 {hm}"
    if t.tm_year == n.tm_year:
        return f"{t.tm_mon}月{t.tm_mday}日 {hm}"
    return f"{t.tm_year}年{t.tm_mon}月{t.tm_mday}日 {hm}"


def fmt_dur(sec: float) -> str:
    """03:12 / 1:07:25"""
    s = max(0, int(sec))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_about(sec: float) -> str:
    """约 9 分 / 约 2 小时"""
    m = sec / 60
    if m < 1:
        return "不到 1 分"
    if m < 100:
        return f"约 {round(m)} 分"
    return f"约 {m / 60:.1f} 小时"


def result_text(rec: dict, unit: str = "句") -> str:
    """结果用语: 全部完成 / N 句未完成 / 正常结束 / 未达成 / 异常退出 / 手动停止"""
    r = rec.get("result")
    if r == OK:
        return "全部完成"
    if r == PARTIAL:
        return f"{len(rec.get('missed') or []) or 1} {unit}未完成"
    if r == DONE:
        return "正常结束"
    if r == STOPPED:
        return "手动停止"
    if r == UNMET:
        return "未达成"
    return "异常退出"
