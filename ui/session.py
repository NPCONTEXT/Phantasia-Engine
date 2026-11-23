"""播放会话: 歌单(工具表) / 顺序播放队列 / 当前运行 / 日志缓冲 / 播放记录。界面只读它的状态、调它的方法。

规则（作者已定）:
- 顺序播放: 点下去那一刻，勾了的工具按歌单从上到下排成这一轮；运行中新点播放的排到队尾
- 某个工具未完成 / 失败 / 启动不了: 记下结果，继续播下一个
- 点正在播放那个的停止 = 只停这一个，待播的接着播；「停止连播」= 这一个停下、待播的全部取消
- 空闲时点某一行的播放 = 只播这一个
"""
import json
import re
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core import env, history, registry, runner, steps

_ERR = re.compile(r"Traceback|出错|\b\w*(Error|Exception)\b")


def classify(line: str, tracker: "steps.StepTracker | None" = None) -> str:
    """日志行的着色类别: hd 步骤 / 环节标题，er 出错，空 = 普通。
    传了 tracker 时要先把这一行喂给它（tracker.feed），这里只读它的判断结果。"""
    if tracker.header if tracker is not None else steps.HDR.match(line):
        return "hd"
    if tracker is not None and tracker.bad_hit:
        return "er"
    if _ERR.search(line):
        return "er"
    return ""


def album_of(tool: dict) -> str:
    """工具属于哪张专辑(哪个游戏): 先看 "album"，没写就取名字里「 · 」前面那一截，再没有归到「其它」。"""
    a = str(tool.get("album") or "").strip()
    if a:
        return a
    name = str(tool.get("name") or "")
    return name.split(" · ", 1)[0].strip() if " · " in name else "其它"


def is_limited(tool: dict) -> bool:
    """在「限时歌单」里（tools.json 里 "limited": true）：歌单底部折叠显示，不参与顺序播放。"""
    return bool(tool.get("limited"))


def move_groups(tools: list[dict]) -> list[list[dict]]:
    """上移 / 下移只在同一组里换位置：每张专辑一组（不含限时的），限时歌单单独一组。"""
    return [ts for _, ts in group_albums([t for t in tools if not is_limited(t)])] + [[t for t in tools if is_limited(t)]]


def sub_cfg(tool: dict | None) -> dict | None:
    """tools.json 里的 "subitems": 一首歌里能单独打勾的子项（例如记忆战场本期的三个 BOSS）。
    {"file": 子项文件(相对 workdir), "head": [固定句], "arg": 传勾选的参数名}；没有就是 None，一切照旧。"""
    sub = tool.get("subitems") if isinstance(tool, dict) else None
    return sub if isinstance(sub, dict) else None


def sub_head(tool: dict | None) -> list[str]:
    """带子项的歌里固定的那几句（子项前面的句子）。"""
    sub = sub_cfg(tool) or {}
    head = sub.get("head")
    return [str(x) for x in head] if isinstance(head, list) else []


def read_subitems(tool: dict | None) -> list[str] | None:
    """读脚本写的子项文件，按文件里的先后返回子项名字（第 1 个 = 位置 1）。
    文件不存在或读不懂 = 还没有子项，返回 None。"""
    sub = sub_cfg(tool)
    name = str((sub or {}).get("file") or "")
    if not name:
        return None
    try:
        path = registry.tool_path(tool.get("workdir")) / name
        items = json.loads(path.read_text(encoding="utf-8")).get("items")
        if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
            return None
        return [steps.clean_label(it.get("label")) or "（没有名字）" for it in items]
    except Exception:
        return None


def role_cfg(tool: dict | None) -> dict | None:
    """tools.json 里的 "roles": 一首歌可选的角色（例如乐土用谁打），只能选一个，播放时以 arg 传角色 id。
    {"arg": "--role", "default": "fuhua", "items": [{"id", "label", "short", "desc", "py", "tag"}]}；
    没有或 items 是空的 = None，一切照旧。"""
    r = tool.get("roles") if isinstance(tool, dict) else None
    if not isinstance(r, dict):
        return None
    items = [it for it in (r.get("items") or []) if isinstance(it, dict) and str(it.get("id") or "").strip()]
    return dict(r, items=items) if items else None


def role_short(item: dict | None) -> str:
    """角色的短名（歌单行、记录里用）：没写 short 就取名字里「·」前面那段。"""
    if not item:
        return ""
    return str(item.get("short") or str(item.get("label") or item.get("id") or "").split("·")[0]).strip()


def _clean_roles(raw) -> dict[str, str]:
    """state.json 里的 "roles": {工具id: 选的角色 id}；不认识的值丢掉。"""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k] = v.strip()
    return out


def _clean_picks(raw) -> dict[str, list[int]]:
    """state.json 里的 "picks": {工具id: [勾了的位置, 从 1 起]}；不认识的值丢掉。"""
    out: dict[str, list[int]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = sorted({x for x in v if isinstance(x, int) and not isinstance(x, bool) and x >= 1})
    return out


def _clean_labels(raw) -> dict[str, list[str]]:
    """state.json 里的 "pick_labels": {工具id: 勾选时的本期名单}；不认识的值丢掉。"""
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
    return out


def same_labels(a: list[str], b: list[str], ratio: float = 0.85) -> bool:
    """两份本期名单算不算同一期：个数一样、每个位置相似度 ≥ ratio。
    OCR 错字（托纳提乌 → 托纳提写）不算换期；和 ma_run.py 的 same_list 同一个标准。"""
    from difflib import SequenceMatcher
    return len(a) == len(b) and all(SequenceMatcher(None, x, y).ratio() >= ratio for x, y in zip(a, b))


def group_albums(tools: list[dict]) -> list[tuple[str, list[dict]]]:
    """按专辑分组；专辑的先后 = 它的第一个工具在 tools.json 里的先后，组内保持原顺序。"""
    groups: dict[str, list[dict]] = {}
    for t in tools:
        groups.setdefault(album_of(t), []).append(t)
    return list(groups.items())


class LogBuf:
    def __init__(self):
        self.lines: deque[tuple[str, str]] = deque(maxlen=5000)
        self.pid: int | None = None
        self.file: str = ""
        self.live = False


class RunState:
    def __init__(self, tool: dict, rt: runner.RunningTool, picks: list[int] | None = None,
                 role: dict | None = None):
        self.id = tool["id"]
        self.role = role                 # 带角色的歌: 这次用的角色（tools.json roles.items 里的那一项），其余 None
        self.name = tool.get("name", "?")
        self.tool = tool
        self.rt = rt
        # 带子项的歌: 固定句个数 / 这次传给脚本的勾选（None = 开播时还没有子项文件，没传；记忆战场脚本这次只读名单不开打）
        self.sub_head = len(sub_head(tool)) if sub_cfg(tool) else None
        self.picks = picks
        self.tracker = steps.StepTracker(tool.get("tasks") or [], tool.get("report"), sub_head=self.sub_head)
        # 开播时传勾选所依据的名单；运行中「子项：」行换成别的名单 = 换期，脚本这次只减负不开打
        self.launch_labels = list(self.tracker.tasks[self.sub_head:]) if (self.sub_head is not None and picks is not None) else None
        self.stopping = False
        # 11-02 暂停：点了暂停就算暂停（界面马上停）；脚本在下一次截图前停住后回报 confirmed
        self.paused = False
        self.paused_since: float | None = None
        self.paused_total = 0.0            # 累计暂停秒数：已播时长、播放记录的耗时都扣掉
        self.pause_confirmed = False
        # 能不能知道「现在到哪一句」: 分句汇报的脚本，或配了 "report" 环节识别的
        self.by_steps = bool(tool.get("report_steps")) or self.tracker.cyclic

    @property
    def started(self) -> float:
        return self.rt.started

    def paused_secs(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return self.paused_total + ((now - self.paused_since) if (self.paused and self.paused_since) else 0.0)

    def active(self, now: float | None = None) -> float:
        """已播时长（扣掉暂停）。"""
        now = time.time() if now is None else now
        return max(0.0, now - self.rt.started - self.paused_secs(now))

    def run_idx(self) -> list[int] | None:
        """带子项的歌这次要跑的句子（tracker.tasks 里的下标，从小到大）: 固定句全部 + 勾了的子项；
        没传勾选（picks 为 None）就只有固定句。子项按当时的 tracker.tasks 算位置（运行中可能被「子项：」行换过），
        超出的不算；运行中名单和开播时不一样（换期）也只有固定句。不带子项返回 None。"""
        if self.sub_head is None:
            return None
        n = len(self.tracker.tasks)
        idx = set(range(min(self.sub_head, n)))
        if self.launch_labels is not None and self.tracker.tasks[self.sub_head:] != self.launch_labels:
            return sorted(idx)            # 换期：脚本只减负不开打（ma_run.py「名单和上次不一样」那一支），只算固定句
        idx |= {self.sub_head + p - 1 for p in (self.picks or []) if self.sub_head + p - 1 < n}
        return sorted(idx)


class Session(QObject):
    changed = Signal()                 # 状态变了，界面整体刷新一次
    tools_changed = Signal()           # 工具表本身变了（新增 / 排序 / 重读）
    log_added = Signal(str, str, str)  # tool_id, 文本, 类别
    log_reset = Signal(str)            # tool_id: 这个工具的日志缓冲重开或被清空
    event = Signal(dict)               # 要告诉用户的事: finished / nostart / round
    toast = Signal(str)                # 播放栏最右端的一次性小提示

    _sig_log = Signal(object, str)
    _sig_exit = Signal(object)
    _sig_call = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = registry.load()
        self.tools: list[dict] = self.data.get("tools", [])
        self.records: list[dict] = history.load()
        st = history.load_state()
        self.checked: list[str] = [i for i in st.get("checked", []) if self.tool(i)]
        self.notify: bool = bool(st.get("notify", True))
        self.selected: str | None = st.get("selected") if self.tool(st.get("selected")) else None
        self.seen: list[str] = [str(k) for k in st.get("seen", [])][-300:]   # 点过「知道了」的播放记录
        self.picks: dict[str, list[int]] = _clean_picks(st.get("picks"))      # 子项勾选；没有键 = 全勾
        self.pick_labels: dict[str, list[str]] = _clean_labels(st.get("pick_labels"))   # 勾选对应的本期名单（换期清空用）
        self.roles: dict[str, str] = _clean_roles(st.get("roles"))           # 选的角色；没有键 = 默认角色

        self.cur: RunState | None = None
        self.queue: list[str] = []
        self.pending: str | None = None      # 正在查设备、马上要启动的那个
        self._pending_manual = False
        self._gap = False                    # 两个工具之间的短暂间隔
        self.round_done: list[str] = []      # 这一轮已经播过的
        self.round_res: dict[str, str] = {}  # 这一轮的结果记号: fin / err
        self.round_recs: list[dict] = []
        self.round_fail = 0                  # 这一轮没能启动的个数
        self.round_t0 = 0.0
        self.shot_ver = 0                    # 验收（删截图）/ 知道了 一次加 1，播放记录页据此重画
        self.round_seq = False               # 这一轮是「顺序播放」：有 package 的工具开跑前、结束后都关游戏
        self.logs: dict[str, LogBuf] = {}

        self.runner = runner.ToolRunner(on_line=self._cb_line, on_exit=self._cb_exit)
        self._sig_log.connect(self._on_log)
        self._sig_exit.connect(self._on_exit)
        self._sig_call.connect(self._on_call)

    # ------------------------------------------------------------ 后台线程小工具
    def bg(self, fn, done=None):
        """在后台线程跑 fn，结果回到界面线程交给 done(result)。出异常时 result 是那个异常。"""
        def work():
            try:
                res = fn()
            except Exception as e:
                res = e
            if done is not None:
                self._sig_call.emit(done, res)
        threading.Thread(target=work, daemon=True).start()

    @Slot(object, object)
    def _on_call(self, fn, res):
        fn(res)

    def _cb_line(self, rt, line: str):  # 后台线程
        self._sig_log.emit(rt, line)

    def _cb_exit(self, rt):             # 后台线程
        self._sig_exit.emit(rt)

    # ------------------------------------------------------------ 查询
    def tool(self, tid: str | None) -> dict | None:
        return next((t for t in self.tools if t.get("id") == tid), None)

    @property
    def busy(self) -> bool:
        return self.cur is not None or self.pending is not None or self._gap

    def albums(self) -> list[tuple[str, list[dict]]]:
        return group_albums([t for t in self.tools if not is_limited(t)])

    def limited(self) -> list[dict]:
        return [t for t in self.tools if is_limited(t)]

    def ordered(self) -> list[dict]:
        """歌单里从上到下的顺序（先按专辑分组）。顺序播放就按这个顺序。"""
        return [t for _, ts in self.albums() for t in ts]

    def short_name(self, tool: dict) -> str:
        """歌单行里的名字: 专辑标题已经写了游戏名，行里就不再重复那一截。"""
        name, pre = str(tool.get("name", "?")), album_of(tool) + " · "
        return name[len(pre):] if name.startswith(pre) and len(name) > len(pre) else name

    def can_move(self, tid: str | None, delta: int) -> bool:
        for ts in move_groups(self.tools):
            ids = [t["id"] for t in ts]
            if tid in ids:
                return 0 <= ids.index(tid) + delta < len(ids)
        return False

    def checked_ordered(self) -> list[str]:
        return [t["id"] for t in self.ordered() if t.get("id") in self.checked]

    def last_record(self, tid: str) -> dict | None:
        return next((r for r in self.records if r.get("id") == tid), None)

    def avg(self, tid: str) -> float | None:
        """歌单行的「约 N 分」。带子项的歌勾得越多跑得越久: 优先只看勾选个数和现在一样的记录
        （还没有本期名单时，下次播放不传勾选，按 0 个算）。"""
        tool = self.tool(tid)
        if sub_cfg(tool):
            labels = read_subitems(tool)
            k = len(self.picks_for(tid, len(labels))) if labels is not None else 0
            return history.avg_duration(self.records, tid, n_picks=k)
        return history.avg_duration(self.records, tid)

    def row_state(self, tid: str) -> dict:
        """歌单一行该怎么画: state now/wait/idle；badge (样式, 序号)；play play/stop/dis/busy；check 待验收"""
        rs = self._row_state(tid)
        rs["check"] = bool(self.pending_shot(tid)) and rs["state"] == "idle"
        return rs

    def _row_state(self, tid: str) -> dict:
        if self.cur and self.cur.id == tid:
            return {"state": "now", "badge": ("eq", 0), "play": "busy" if self.cur.stopping else "stop"}
        if self.pending == tid:
            return {"state": "now", "badge": ("eq", 0), "play": "dis"}
        if tid in self.queue:
            pos = len(self.round_done) + (1 if (self.cur or self.pending) else 0) + self.queue.index(tid) + 1
            return {"state": "wait", "badge": ("num", pos), "play": "dis"}
        if self.busy:
            mark = self.round_res.get(tid)
            if mark:
                return {"state": "idle", "badge": (mark, 0), "play": "play"}
            return {"state": "idle", "badge": ("tick" if tid in self.checked else "box", 0), "play": "play"}
        order = self.checked_ordered()
        if tid in order:
            return {"state": "idle", "badge": ("num", order.index(tid) + 1), "play": "play"}
        return {"state": "idle", "badge": ("box", 0), "play": "play"}

    # ------------------------------------------------------------ 结束截图 / 验收
    @staticmethod
    def shot_path(rec: dict):
        name = str(rec.get("shot") or "")
        p = runner.SHOTS_DIR / name if name else None
        return p if (p and p.is_file()) else None

    def pending_shot(self, tid: str | None = None) -> dict | None:
        """最近一条还没验收（截图还在）的播放记录；tid 为空就不分工具。"""
        for r in self.records:
            if (tid is None or r.get("id") == tid) and self.shot_path(r):
                return r
        return None

    def accept_shot(self, rec: dict):
        """验收 = 删掉这张结束截图（播放记录保留）。"""
        p = self.shot_path(rec)
        if p:
            try:
                p.unlink()
            except Exception:
                pass
        self.shot_ver += 1
        self.toast.emit("已验收，截图已删除")
        self.changed.emit()

    # ------------------------------------------------------------ 看过了：红字收成一行灰字
    @staticmethod
    def rec_key(rec: dict) -> str:
        return f"{rec.get('id')}@{rec.get('start')}"

    def is_seen(self, rec: dict) -> bool:
        """点过「知道了」，或结束截图已经验收（记录里有截图名、图已删）。"""
        return self.rec_key(rec) in self.seen or (bool(rec.get("shot")) and not self.shot_path(rec))

    def mark_seen(self, rec: dict):
        """知道了 = 只在 runs/state.json 记一个标记，播放记录本身不改。"""
        k = self.rec_key(rec)
        if k not in self.seen:
            self.seen = (self.seen + [k])[-300:]
            self.save_state()
        self.shot_ver += 1
        self.changed.emit()

    # ------------------------------------------------------------ 小状态落盘
    def save_state(self):
        st = {"checked": self.checked, "notify": self.notify, "selected": self.selected,
              "seen": self.seen, "picks": self.picks, "pick_labels": self.pick_labels}
        if self.roles:
            st["roles"] = self.roles
        history.save_state(st)

    def set_notify(self, on: bool):
        self.notify = on
        self.save_state()

    def select(self, tid: str | None):
        if tid != self.selected:
            self.selected = tid
            self.save_state()
            self.changed.emit()

    # ------------------------------------------------------------ 角色（一首歌只能选一个，例如乐土用谁打）
    def role_of(self, tid: str) -> dict | None:
        """这首歌现在选的角色（roles.items 里的那一项）；没选过、或选的那个已从名单删掉 = 默认角色（没写默认就第一个）。
        不带角色的歌返回 None。"""
        cfg = role_cfg(self.tool(tid))
        if not cfg:
            return None
        items = cfg["items"]
        for rid in (self.roles.get(tid), cfg.get("default")):
            it = next((x for x in items if x.get("id") == rid), None)
            if it:
                return it
        return items[0]

    def set_role(self, tid: str, rid: str) -> bool:
        """换角色：马上记住，下次播放生效；正在播放 / 正要启动这首时不能换。返回换没换。"""
        cfg = role_cfg(self.tool(tid))
        it = next((x for x in (cfg or {}).get("items", []) if x.get("id") == rid), None)
        if it is None:
            return False
        if self.pick_locked(tid):
            self.toast.emit("正在播放，换角色下次播放才生效")
            return False
        if (self.role_of(tid) or {}).get("id") == rid:
            self.toast.emit(f"现在就是「{it.get('label') or rid}」，没换")      # 面板里点了当前角色也给个回应（10-20 复查）
            return False
        self.roles[tid] = rid
        self.save_state()
        self.toast.emit(f"已换成「{it.get('label') or rid}」，下次播放用它")
        self.changed.emit()
        return True

    def role_recent(self, tid: str, n: int = 4) -> list[str]:
        """最近用过的角色 id：按播放记录从新到旧，去重，最多 n 个。"""
        out: list[str] = []
        for r in self.records:
            rid = r.get("role")
            if r.get("id") == tid and isinstance(rid, str) and rid and rid not in out:
                out.append(rid)
                if len(out) >= n:
                    break
        return out

    def role_last(self, tid: str, rid: str) -> dict | None:
        """这个角色最近一条播放记录。"""
        return next((r for r in self.records if r.get("id") == tid and r.get("role") == rid), None)

    def _role_plan(self, tool: dict) -> tuple[dict, dict, str]:
        """开播那一刻定下用哪个角色: 返回 (追加了 --role 的浅拷贝, 角色那一项, 日志说明)。不改 self.tools 里的原字典。"""
        cfg = role_cfg(tool) or {}
        it = self.role_of(tool["id"])
        arg = str(cfg.get("arg") or "")
        extra = [arg, str(it["id"])] if arg else []
        run = dict(tool, command=list(tool.get("command") or []) + extra)
        note = f"本次角色：{it.get('label') or it['id']}"
        if extra:
            note += f"；传给脚本 {arg} {it['id']}"
        return run, it, note

    # ------------------------------------------------------------ 子项勾选（一首歌里的几项，例如本期 BOSS）
    def picks_for(self, tid: str, n: int) -> list[int]:
        """这首歌现在勾了哪几个位置（从 1 起）；没记过 = 全勾，超出本期个数的不算。"""
        saved = self.picks.get(tid)
        if saved is None:
            return list(range(1, n + 1))
        return [x for x in saved if 1 <= x <= n]

    def sync_picks(self, tid: str, labels: list[str] | None) -> bool:
        """本期名单和勾选时记下的不是同一期（换期）→ 勾选清成全不勾，等用户重新勾（10-15 作者定）。
        第一次见到名单 / 只差 OCR 错字：只记下新名单，勾选不动。返回这次是不是清空了。"""
        if labels is None:
            return False
        snap = self.pick_labels.get(tid)
        if snap == labels:
            return False
        cleared = snap is not None and not same_labels(snap, labels)
        if cleared:
            self.picks[tid] = []
        self.pick_labels[tid] = list(labels)
        self.save_state()
        if cleared:
            self.toast.emit("本期 BOSS 换了，勾选已清空，请重新勾选")
        return cleared

    def pick_locked(self, tid: str) -> bool:
        """正在播放 / 正要启动的这首：勾选这次已经定了，不能改。"""
        return bool((self.cur and self.cur.id == tid) or self.pending == tid)

    def toggle_pick(self, tid: str, pos: int):
        tool = self.tool(tid)
        if not sub_cfg(tool):
            return
        if self.pick_locked(tid):
            self.toast.emit("正在播放，勾选下次播放才生效")
            return
        labels = read_subitems(tool)
        self.sync_picks(tid, labels)
        n = len(labels) if labels is not None else 0
        if not 1 <= pos <= n:            # 名单文件在外面变了（界面还是旧的）：提示并按新名单重画
            self.toast.emit("本期名单变了，已按新名单刷新")
            self.changed.emit()
            return
        cur = self.picks_for(tid, n)
        if pos in cur:
            cur.remove(pos)
        else:
            cur.append(pos)
        if sorted(cur) == list(range(1, n + 1)):
            self.picks.pop(tid, None)        # 全勾回到默认（以后多出来的子项也默认勾上）
        else:
            self.picks[tid] = sorted(cur)
        self.save_state()
        self.changed.emit()

    def sub_view(self, tid: str) -> dict | None:
        """带子项的歌在歌词里怎么画；不带子项返回 None。
        lines 句子（正在播放 = 这次运行的句子，可能被「子项：」行更新；空闲 = 固定句 + 子项文件里的名字）
        head 固定句个数 / picks 勾了的位置（正在播放 = 这次真要跑的，和播放栏同一个来源）/ playing 正在播这首 /
        locked 勾选不能改 / nofile 还没有子项文件"""
        tool = self.tool(tid)
        if not sub_cfg(tool):
            return None
        head = sub_head(tool)
        st = self.cur if (self.cur and self.cur.id == tid) else None
        if st is not None:
            lines = list(st.tracker.tasks)
            # 和播放栏一样按 RunState.run_idx 换算: 没传勾选（开播时还没名单，这次只读名单、不开打）= 一个都不勾
            picks = [i - st.sub_head + 1 for i in (st.run_idx() or []) if i >= st.sub_head]
            nofile = False
        else:
            labels = read_subitems(tool)
            self.sync_picks(tid, labels)
            nofile = labels is None
            lines = head + (labels or [])
            picks = self.picks_for(tid, len(labels or []))
        return {"lines": lines, "head": len(head), "picks": picks, "playing": st is not None,
                "locked": self.pick_locked(tid), "nofile": nofile}

    def _sub_plan(self, tool: dict) -> tuple[dict, list[int] | None, str]:
        """开播那一刻定下这次的句子和参数: 返回 (交给 runner / RunState 的浅拷贝, 这次的勾选, 日志说明)。
        不改 self.tools 里的原字典。"""
        sub = sub_cfg(tool) or {}
        head = sub_head(tool)
        labels = read_subitems(tool)
        command = list(tool.get("command") or [])
        if labels is None:
            run = dict(tool, command=command, tasks=list(head))
            return run, None, "本次勾选：还没读到本期名单，不传勾选（这次脚本先读名单、只减负不开打）"
        self.sync_picks(tool["id"], labels)
        picks = self.picks_for(tool["id"], len(labels))
        val = ",".join(str(x) for x in picks) if picks else "none"
        arg = str(sub.get("arg") or "")
        extra = [arg, val] if arg else []
        run = dict(tool, command=command + extra, tasks=head + labels)
        if picks:
            note = (f"本次勾选：第 {'、'.join(str(x) for x in picks)} 个"
                    f"（{'、'.join(labels[x - 1] for x in picks)}）")
        else:
            note = "本次勾选：一个都没勾，只做固定的几句"
        if extra:
            note += f"；传给脚本 {arg} {val}"
        return run, picks, note

    # ------------------------------------------------------------ 勾选 / 播放 / 停止
    def toggle_check(self, tid: str):
        if is_limited(self.tool(tid) or {}) and tid not in self.checked and tid not in self.queue:
            self.toast.emit("限时歌单里的歌不参与顺序播放")
            return
        if tid in self.queue:            # 待播的再点一下 = 移出这一轮，并取消勾选
            self.queue.remove(tid)
            if tid in self.checked:
                self.checked.remove(tid)
        elif (self.cur and self.cur.id == tid) or self.pending == tid:
            return
        elif tid in self.checked:
            self.checked.remove(tid)
        else:
            self.checked.append(tid)
        self.save_state()
        self.changed.emit()

    def play(self, tid: str):
        """点某一行的播放圆钮。空闲 = 只播这一个；有工具在跑 = 排到待播的最后。"""
        if not self.tool(tid):
            return
        if self.busy:
            if (self.cur and self.cur.id == tid) or self.pending == tid or tid in self.queue:
                return
            self.queue.append(tid)
            self.toast.emit("已加入待播")
            self.changed.emit()
            return
        self._begin_round([tid] + [q for q in self.queue if q != tid])     # 11-02 空闲时拖进待播的，接在这首后面

    def play_checked(self):
        ids = self.checked_ordered()
        if ids and not self.busy:
            self._begin_round(ids + [q for q in self.queue if q not in ids], seq=True)     # 11-02 空闲时拖进待播的接在勾了的后面

    def stop_current(self):
        """只停正在播放的这一个；有待播就接着播下一个。"""
        st = self.cur
        if not st or st.stopping:
            return
        st.stopping = True
        self.changed.emit()
        self.bg(self.runner.stop)

    # ------------------------------------------------------------ 暂停（11-02；脚本那边：lib/play_ctl.py）
    def pause_toggle(self) -> str | None:
        """点暂停钮。返回 None = 照做了（暂停或继续）；返回一句话 = 没停（按钮弹回），界面用雨澜的气泡说这句话。
        作者：「肯定不强行暂停，可以改成战斗不能暂停，点击暂停后直接回弹按钮，气泡提醒正在战斗之类的，请等待」"""
        st = self.cur
        if st is None or st.stopping:
            return None
        if st.paused:                                       # 继续
            self.runner.set_pause(False)
            st.paused_total = st.paused_secs()
            st.paused, st.paused_since, st.pause_confirmed = False, None, False
            self.changed.emit()
            return None
        state = self.runner.ctl_state()
        if state is None:
            return "这首停不了。还没接上暂停。"
        if state.get("state") == "battle":
            return state.get("say") or f"{state.get('why') or '在打'}。这一场打完再停。"
        if not self.runner.set_pause(True):
            return None
        st.paused, st.paused_since, st.pause_confirmed = True, time.time(), False
        self.changed.emit()
        QTimer.singleShot(2000, lambda s=st: self._pause_check(s))
        return None

    def _pause_check(self, st):
        """点了暂停 2 秒，脚本还没停住：要么进了战斗（撤回暂停、说一声），要么在一个长等待里（说一声，等它做完）。"""
        if self.cur is not st or not st.paused or st.pause_confirmed:
            return
        state = self.runner.ctl_state() or {}
        if state.get("state") == "paused":
            st.pause_confirmed = True
            return
        if state.get("state") == "battle":
            self.runner.set_pause(False)
            st.paused, st.paused_since = False, None          # 没真停过，这 2 秒不算暂停
            self.changed.emit()
            self.event.emit({"kind": "say", "text": state.get("say") or f"{state.get('why') or '在打'}。这一场打完再停。"})
            return
        self.event.emit({"kind": "say", "text": "这一步做完，停。"})

    def stop_after_current(self):
        """11-02 作者：「停止连播是播完当前就不播了，要想直接杀死进程，右上角有按钮了」——
        只清掉待播；正在播（或马上要开播）的这一首不打断，播完这一轮就结束。"""
        if not self.queue:
            return
        self.queue.clear()
        self.toast.emit("已停止连播：这首播完就停" if (self.cur or self.pending) else "已停止连播")
        self.changed.emit()

    def stop_all(self):
        """这一个马上停下，待播的全部取消（11-02 起界面上不再直接用：歌单头的「停止连播」改成 stop_after_current）。"""
        self.queue.clear()
        if self.cur:
            self.stop_current()
            self.changed.emit()
        elif self.pending or self._gap:
            self.pending = None
            self._end_round()

    def queue_move(self, tid: str, delta: int):
        if tid in self.queue:
            i = self.queue.index(tid)
            j = i + delta
            if 0 <= j < len(self.queue):
                self.queue[i], self.queue[j] = self.queue[j], self.queue[i]
                self.changed.emit()

    def enqueue(self, tid: str, index: int | None = None) -> bool:
        """11-02 拖到封面 / 待播列表 = 加进待播（空闲时也行：点播放时接在播放区那首后面）；已在待播里就挪到这个位置。
        拖动只改以后的顺序，不碰正在播的。"""
        if not self.tool(tid):
            return False
        if (self.cur and self.cur.id == tid) or self.pending == tid:
            self.toast.emit("这首正在播")
            return False
        if tid in self.queue:
            return self.queue_move_to(tid, len(self.queue) if index is None else index)
        n = len(self.queue)
        self.queue.insert(n if index is None else max(0, min(index, n)), tid)
        self.toast.emit("已加入待播")
        self.changed.emit()
        return True

    def queue_move_to(self, tid: str, index: int) -> bool:
        """待播里拖动调顺序：index 是按拖动前的列表算的插入位置（放在第 index 个前面；等于长度 = 放到最后）。"""
        if tid not in self.queue:
            return False
        i = self.queue.index(tid)
        q = [x for x in self.queue if x != tid]
        j = index - 1 if index > i else index
        q.insert(max(0, min(j, len(q))), tid)
        if q == self.queue:
            return False
        self.queue = q
        self.changed.emit()
        return True

    def queue_remove(self, tid: str):
        if tid in self.queue:
            self.queue.remove(tid)
            self.changed.emit()

    # ------------------------------------------------------------ 一轮的推进
    def cue_next(self, cur: str | None) -> str | None:
        """空闲时播放区的「下一首」（11-02 作者：「下一首也能用方便切换清单中下一首待播的」）：
        有待播就换成待播的第一首（从待播里拿出来）；没有就换成勾了的下一首（按序号，转一圈）；一首都没勾就是歌单里的下一行。"""
        if self.busy:
            return None
        if self.queue:
            tid = self.queue.pop(0)
            self.changed.emit()
            return tid
        order = self.checked_ordered()
        if order:
            return order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        ids = [t["id"] for t in self.ordered()]
        if not ids:
            return None
        return ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]

    def _begin_round(self, ids: list[str], seq: bool = False):
        self.round_done, self.round_res, self.round_recs = [], {}, []
        self.round_fail = 0
        self.round_seq = seq
        self.round_t0 = time.time()
        self.queue = list(ids)
        # 单首点播启动不了 = 弹框告诉你；顺序播放（后台全自动）启动不了只发气泡 + 通知，不弹框挡住后面的（10-20 审查 M6）
        self._advance(manual=not seq)

    def _advance(self, manual: bool = False):
        self._gap = False
        while self.queue and not self.tool(self.queue[0]):
            self.queue.pop(0)
        if not self.queue:
            self._end_round()
            return
        tid = self.queue.pop(0)
        tool = self.tool(tid)
        self.pending, self._pending_manual = tid, manual
        self.changed.emit()
        if tool.get("need_device", True):
            self.bg(env.device_online, lambda dev, t=tid: self._launch(t, dev))
        else:
            self._launch(tid, True)

    def _launch(self, tid: str, dev):
        if self.pending != tid:     # 查设备的这一会儿被「停止连播」取消了
            return
        tool = self.tool(tid)
        manual = self._pending_manual
        self.pending = None
        if tool is None:
            self._after_one()
            return
        run_tool, picks, note = tool, None, ""
        role = None
        if isinstance(dev, Exception) or not dev:
            ok, msg = False, "没有在线设备(插线/开无线调试)"
        else:
            try:
                if sub_cfg(tool):        # 带子项的歌: 这一刻读子项文件和勾选，定下这次的句子和参数
                    run_tool, picks, note = self._sub_plan(tool)
                if role_cfg(tool):       # 带角色的歌: 这一刻定下用哪个角色，命令追加 --role 角色id
                    run_tool, role, rnote = self._role_plan(run_tool)
                    note = f"{note}；{rnote}" if note else rnote
                ok, msg = self.runner.start(run_tool, device_checked=True, seq=self.round_seq, note=note)
            except Exception as e:
                ok, msg = False, f"启动失败: {e}"
        if not ok:
            self.round_done.append(tid)
            self.round_res[tid] = "err"
            self.round_fail += 1
            self.event.emit({"kind": "nostart", "id": tid, "name": tool.get("name", "?"),
                             "reason": msg, "manual": manual})
            self._after_one()
            return
        rt = self.runner.last
        self.cur = RunState(run_tool, rt, picks, role)
        buf = LogBuf()
        buf.pid, buf.file, buf.live = rt.pid, rt.log_path.name, True
        self.logs[tid] = buf
        self.log_reset.emit(tid)
        self.changed.emit()

    @Slot(object, str)
    def _on_log(self, rt, line: str):
        st = self.cur
        if not st or st.rt is not rt:
            return
        changed = st.tracker.feed(line)
        if st.paused and "[引擎] 已暂停" in line:          # 11-02 脚本停住了（lib/play_ctl.py 打的行）
            st.pause_confirmed = True
        kind = classify(line, st.tracker)
        self.logs[st.id].lines.append((line, kind))
        self.log_added.emit(st.id, line, kind)
        if changed:
            self.changed.emit()

    @Slot(object)
    def _on_exit(self, rt):
        st = self.cur
        if not st or st.rt is not rt:
            return
        tr = st.tracker
        missed = tr.missed()
        if tr.summary is not None:
            result = history.PARTIAL if missed else history.OK
        elif rt.stopped_by_user:
            result = history.STOPPED
        elif tr.outcome and tr.outcome[1]:
            result = history.UNMET
        elif rt.exit_code == 0:
            result = history.DONE
        else:
            result = history.ABNORMAL
            if tr.cur is not None:   # 记下停在哪一句
                missed = [{"no": tr.cur + 1, "name": tr.tasks[tr.cur], "reason": "运行到这里时异常退出"}]
        end = rt.ended or time.time()
        n_steps = len(tr.tasks)
        picks = None
        if st.sub_head is not None:      # 带子项的歌: 只算固定句 + 勾了的子项；没传勾选（还没名单）记 None，只算固定句
            picks = st.picks
            n_steps = len(st.run_idx() or [])      # 固定句 + 实际要跑的子项（换期、没传勾选时只有固定句）
        paused = st.paused_secs(end)                       # 11-02 暂停的时间不算进耗时
        rec = {"id": st.id, "name": st.name, "start": rt.started, "end": end,
               "dur": round(end - rt.started - paused, 1), "result": result, "exit": rt.exit_code,
               "steps": n_steps if (tr.has_steps and not tr.cyclic) else 0, "missed": missed,
               "facts": tr.facts(), "outcome": tr.outcome[0] if tr.outcome else "", "log": rt.log_path.name,
               "shot": rt.shot or ""}
        if st.sub_head is not None:
            rec["picks"] = list(picks) if picks is not None else None
            rec["tasks"] = list(tr.tasks)
        if paused >= 1:
            rec["paused"] = round(paused, 1)
        if st.role is not None:
            rec["role"] = str(st.role.get("id"))
            rec["role_label"] = str(st.role.get("label") or st.role.get("id"))
        history.append(rec)
        self.records.insert(0, rec)

        tail = f"[{time.strftime('%H:%M:%S')}] ■ 工具已结束：{history.result_text(rec)}，耗时 {history.fmt_dur(rec['dur'])}"
        if st.tool.get("env", "none") not in ("none", "", None):
            tail += "（分辨率已还原）"
        try:
            with rt.log_path.open("a", encoding="utf-8") as f:
                f.write(tail + "\n")
        except Exception:
            pass
        buf = self.logs.get(st.id)
        if buf:
            buf.live = False
            buf.lines.append((tail, "sys"))
            self.log_added.emit(st.id, tail, "sys")

        self.round_done.append(st.id)
        if result in (history.OK, history.DONE):
            self.round_res[st.id] = "fin"
        elif result in history.BAD:
            self.round_res[st.id] = "err"
        self.round_recs.append(rec)
        self.cur = None
        self.event.emit({"kind": "finished", "rec": rec})
        self._after_one()

    def _after_one(self):
        if self.queue:
            self._gap = True
            self.changed.emit()
            # 给 adb / 游戏留一口气再接下一个；顺序播放刚关过游戏，多等一会儿
            QTimer.singleShot(3000 if self.round_seq else 1200, self._gap_done)
        else:
            self._end_round()

    def _gap_done(self):
        if not self._gap:
            return
        self._advance()

    def _end_round(self):
        n = len(self.round_done)
        recs, fail, t0 = self.round_recs, self.round_fail, self.round_t0
        self.round_done, self.round_res, self.round_recs = [], {}, []
        self.round_fail = 0
        self._gap = False
        self.pending = None
        self.round_seq = False
        if n >= 2:
            self.event.emit({"kind": "round", "recs": recs, "total": n, "fail": fail,
                             "dur": time.time() - t0})
        self.changed.emit()

    # ------------------------------------------------------------ 日志显示
    def clear_log(self, tid: str):
        buf = self.logs.get(tid)
        if buf:
            buf.lines.clear()
            self.log_reset.emit(tid)

    # ------------------------------------------------------------ 工具表: 重读 / 排序 / 新增
    def _adopt(self, data: dict):
        self.data = data
        self.tools = data.get("tools", [])
        self.checked = [i for i in self.checked if self.tool(i)]
        if not self.tool(self.selected):
            self.selected = self.tools[0]["id"] if self.tools else None
        self.tools_changed.emit()
        self.changed.emit()

    def move(self, tid: str, delta: int) -> bool:
        """把一个工具在歌单里上移 / 下移并保存。先重读文件再改，避免盖掉别处的修改。"""
        fresh = registry.load()
        if fresh.get("_load_error"):
            self.toast.emit("tools.json 读不出来，没保存")
            return False
        tools = fresh.get("tools", [])
        i = j = None
        for ts in move_groups(tools):          # 只在同一张专辑（或限时歌单）里换位置
            ids = [t.get("id") for t in ts]
            if tid in ids:
                k = ids.index(tid)
                if 0 <= k + delta < len(ids):
                    i, j = tools.index(ts[k]), tools.index(ts[k + delta])
                break
        if i is None or j is None:
            return False
        tools[i], tools[j] = tools[j], tools[i]
        try:
            registry.save(fresh)
        except Exception as e:
            self.toast.emit(f"保存失败: {e}")
            return False
        self._adopt(fresh)
        self.toast.emit("已保存歌单顺序")
        return True

    def move_to(self, tid: str, target: str, after: bool) -> bool:
        """11-02 歌单里拖动排序：把 tid 挪到 target 前面（after=True 挪到后面）并保存；只在同一张专辑（或限时歌单）里挪。
        先重读文件再改，避免盖掉别处的修改；这组在 tools.json 里占的位置不变，只是按新顺序重新排进去。"""
        if tid == target:
            return False
        fresh = registry.load()
        if fresh.get("_load_error"):
            self.toast.emit("tools.json 读不出来，没保存")
            return False
        tools = fresh.get("tools", [])
        grp = next((ts for ts in move_groups(tools) if tid in [t.get("id") for t in ts]), None)
        ids = [t.get("id") for t in grp] if grp else []
        if target not in ids:
            self.toast.emit("只能在同一张专辑里拖动排序")
            return False
        new_ids = [x for x in ids if x != tid]
        new_ids.insert(new_ids.index(target) + (1 if after else 0), tid)
        if new_ids == ids:
            return False
        by_id = {t.get("id"): t for t in grp}
        for pos, x in zip(sorted(tools.index(t) for t in grp), new_ids):
            tools[pos] = by_id[x]
        try:
            registry.save(fresh)
        except Exception as e:
            self.toast.emit(f"保存失败: {e}")
            return False
        self._adopt(fresh)
        self.toast.emit("已保存歌单顺序")
        return True

    def set_limited(self, tid: str, on: bool) -> bool:
        """把一首歌移进 / 移回限时歌单并保存（先重读文件再改）。移进去的顺带取消顺序播放勾选。"""
        fresh = registry.load()
        if fresh.get("_load_error"):
            self.toast.emit("tools.json 读不出来，没保存")
            return False
        tool = next((t for t in fresh.get("tools", []) if t.get("id") == tid), None)
        if tool is None or is_limited(tool) == on:
            return False
        if on:
            tool["limited"] = True
        else:
            tool.pop("limited", None)
        try:
            registry.save(fresh)
        except Exception as e:
            self.toast.emit(f"保存失败: {e}")
            return False
        if on and tid in self.checked:
            self.checked.remove(tid)
            self.save_state()
        self._adopt(fresh)
        self.toast.emit("已移进限时歌单（歌单最底下）" if on else "已移回歌单")
        return True

    def add_tool(self, tool: dict) -> tuple[bool, str]:
        fresh = registry.load()
        if fresh.get("_load_error"):
            return False, "tools.json 读不出来，为免覆盖原文件，没有保存"
        fresh.setdefault("tools", []).append(tool)
        try:
            registry.save(fresh)
        except Exception as e:
            return False, f"保存失败: {e}"
        self._adopt(fresh)
        self.toast.emit(f"已新增工具: {tool.get('name', '')}")
        return True, ""

    # ------------------------------------------------------------ 退出
    def shutdown(self, done):
        """停止全部并还原分辨率（后台线程里做，做完回调 done()）。"""
        self.queue.clear()
        self.pending = None
        self._gap = False
        had_cur = self.cur is not None

        def work():
            try:
                self.runner.stop()
            except Exception:
                pass
            try:
                if env.device_online():
                    env.set_resolution(env.RES_NATIVE)
            except Exception:
                pass
            return had_cur
        self.bg(work, lambda _res: done())
