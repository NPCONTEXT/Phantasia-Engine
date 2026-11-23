"""进度解析: 从工具日志里读出「现在到哪一步」和一些要点。两种来源:

1. 分句汇报(两个日常脚本): 日志里的「== 步骤名」和最后一行「完成：步骤=结果，…」。
2. 循环环节(乐土等不分句的脚本): 脚本本身不改，按 tools.json 里每个工具的 "report" 配置，
   用正则去认它们已有的输出:
     "stages":   [{"task": 第几句(从 1 起), "match": 正则}]      匹配到 → 当前环节跳到那一句
     "facts":    [{"label": "已打 {n} 场", "match": 正则, "kind": "count"|"capture", ...}]
                 count = 每匹配一次加 1；capture = 取正则分组，label 里用 {1} {2} 引用
                 可选 "start"(计数起点) "total"(总数) "progress"("pos" 当前第几个 / "amount" 已达成多少)
                 "progress_label"(进度条旁边的文字，可用 {n} {total})
     "outcomes": [{"match": 正则, "text": 结束语, "bad": true|false}]   记进播放记录；bad = 没达成目标
3. 子项（tools.json 里带 "subitems" 的工具，例如记忆战场本期的三个 BOSS）: 句子 = 固定的几句 + 子项，
   脚本运行中打「子项：A|B|C」时，固定句后面的子项当场换成这一串（换期时界面跟着改名）。
"""
import re
from difflib import SequenceMatcher

_TS = r"(?:\[\d\d:\d\d:\d\d\]\s*)?"
HDR = re.compile(rf"^{_TS}==(?!=)\s*(.+?)\s*$")   # 正好两个等号；「=== xxx ===」这种横幅不算
FIN = re.compile(rf"^{_TS}完成[：:]\s*(.+?)\s*$")
SUB = re.compile(rf"^{_TS}子项[：:]\s*(.*?)\s*$")


def clean_label(text) -> str:
    """子项名字去掉全角「，」「=」（会把「完成：」那行汇总切乱）和「|」（「子项：」行的分隔符），再去掉首尾空白。"""
    return str(text or "").replace("，", "").replace("=", "").replace("|", "").strip()


def match_step(name: str, tasks: list[str], after: int = -1) -> int | None:
    """把日志里的步骤名对到 tools.json 的 tasks 下标。
    两边文字不完全一样（例如日志写「冒险委托」，tasks 写「冒险委托(水晶>…)」），
    先比文字相似度，比不出来就按顺序取下一项。"""
    if not tasks:
        return None
    name = name.strip()
    best, best_s = None, 0.0
    for i, t in enumerate(tasks):
        if t == name:
            s = 2.0
        elif t.startswith(name) or name.startswith(t):
            s = 1.5
        else:
            s = SequenceMatcher(None, name, t).ratio()
        if i > after:
            s += 0.05  # 分数接近时优先取还没到的那一项
        if s > best_s:
            best, best_s = i, s
    if best_s >= 0.5:
        return best
    nxt = after + 1
    return nxt if nxt < len(tasks) else None


def parse_summary(text: str) -> list[tuple[str, str]]:
    """「签到弹窗=ok，冒险委托=没进入委托板」 -> [(名, 结果), …]"""
    items: list[list[str]] = []
    for part in text.split("，"):
        if "=" in part:
            k, v = part.rsplit("=", 1)
            items.append([k.strip(), v.strip()])
        elif items:  # 结果文字里自带逗号
            items[-1][1] += "，" + part
    return [(k, v) for k, v in items]


def _fmt(label: str, n=None, total=None, groups=()) -> str:
    def rep(m):
        k = m.group(1)
        if k == "n":
            return "" if n is None else str(n)
        if k == "total":
            return "" if total is None else str(total)
        if k.isdigit():
            i = int(k) - 1
            return str(groups[i]) if 0 <= i < len(groups) and groups[i] is not None else ""
        return m.group(0)
    return re.sub(r"\{(\w+)\}", rep, label)


class Fact:
    """从日志里读出的一条要点: 计数(count) 或取值(capture)。"""

    def __init__(self, cfg: dict):
        self.label = str(cfg.get("label", ""))
        self.rx = re.compile(cfg["match"])
        self.kind = cfg.get("kind", "capture")
        self.n = int(cfg.get("start", 0))
        self.groups: tuple | None = None
        self.total = cfg.get("total")
        self.progress = cfg.get("progress")
        self.progress_label = cfg.get("progress_label")

    def feed(self, line: str) -> bool:
        m = self.rx.search(line)
        if not m:
            return False
        if self.kind == "count":
            self.n += 1
        else:
            self.groups = m.groups()
        return True

    def value(self) -> int | None:
        if self.kind == "count":
            return self.n
        if self.groups is None:
            return None
        nums = [int(g) for g in self.groups if g and g.isdigit()]
        return sum(nums) if nums else None

    def text(self) -> str | None:
        if self.kind == "count":
            return _fmt(self.label, n=self.n, total=self.total) if self.n > 0 else None
        if self.groups is None:
            return None
        return _fmt(self.label, n=self.value(), total=self.total, groups=self.groups)


class StepTracker:
    """喂日志行，记住现在到第几步、读到了哪些要点、哪些步没完成。"""

    def __init__(self, tasks: list[str] | None, report: dict | None = None, sub_head: int | None = None):
        self.tasks = list(tasks or [])
        # 带子项的工具: 前 sub_head 句是固定句，后面是子项；不设就不认「子项：」行
        self.sub_head = sub_head
        self.cur: int | None = None          # 当前步骤下标
        self.seen: list[int] = []            # 出现过的步骤下标(按先后)
        self.summary: list[tuple[int | None, str, str]] | None = None  # (下标, 日志里的名字, 结果)
        self.outcome: tuple[str, bool] | None = None                   # (结束语, 是否没达成)
        report = report if isinstance(report, dict) else {}
        self._stages: list[tuple[re.Pattern, int]] = []
        self._facts: list[Fact] = []
        self._outs: list[tuple[re.Pattern, str, bool]] = []
        for x in report.get("stages") or []:
            try:
                self._stages.append((re.compile(x["match"]), int(x["task"]) - 1))
            except Exception:
                continue        # 配置里某条写坏了就跳过那一条
        for x in report.get("facts") or []:
            try:
                self._facts.append(Fact(x))
            except Exception:
                continue
        for x in report.get("outcomes") or []:
            try:
                self._outs.append((re.compile(x["match"]), str(x.get("text", "")), bool(x.get("bad"))))
            except Exception:
                continue
        self.cyclic = bool(self._stages)     # 循环环节型: 环节会反复出现，不打「已完成」的勾
        self.header = False                  # 最近喂的这一行是不是「进入新一步 / 新环节」的那一行
        self.bad_hit = False                 # 最近喂的这一行是不是「没达成」的那一行

    @property
    def has_steps(self) -> bool:
        return bool(self.seen)

    def feed(self, line: str) -> bool:
        """返回 True 表示状态变了。喂完可以看 header / bad_hit 知道这一行该怎么上色。"""
        self.header = self.bad_hit = False
        if self.sub_head is not None:
            m = SUB.match(line)
            if m:
                self._set_subitems(m.group(1))
                return True
        m = HDR.match(line)
        if m:
            self.header = True
            idx = match_step(m.group(1), self.tasks, self.cur if self.cur is not None else -1)
            if idx is not None:
                self.cur = idx
                if idx not in self.seen:
                    self.seen.append(idx)
                return True
            return False
        m = FIN.match(line)
        if m:
            out, after = [], -1
            for name, res in parse_summary(m.group(1)):
                idx = match_step(name, self.tasks, after)
                if idx is not None:
                    after = idx
                out.append((idx, name, res))
            self.summary = out
            return True
        changed = False
        for rx, idx in self._stages:
            if rx.search(line):
                if 0 <= idx < len(self.tasks) and idx != self.cur:
                    self.cur = idx
                    if idx not in self.seen:
                        self.seen.append(idx)
                    changed = self.header = True     # 只有「换环节」的那一行算标题行
                break
        for f in self._facts:
            if f.feed(line):
                changed = True
        # 结束语：一行命中多条、或后面的行又命中，后命中的覆盖先命中的——tools.json 里更具体的那条要排在后面
        # （例：乐土「complete」和「complete…settlement_verified: false」）（10-20 审查 M5）
        for rx, text, bad in self._outs:
            mm = rx.search(line)
            if mm:
                self.outcome = (_fmt(text, groups=mm.groups()), bad)
                self.bad_hit = bad
                changed = True
        return changed

    def _set_subitems(self, text: str):
        """「子项：A|B|C」: 固定句保留，后面换成这一串。"""
        labels = [clean_label(x) or "（没有名字）" for x in text.split("|")] if text.strip() else []
        self.tasks = self.tasks[:self.sub_head] + labels
        n = len(self.tasks)
        self.seen = [i for i in self.seen if i < n]      # 句子变少了: 越界的下标丢掉
        if self.cur is not None and self.cur >= n:
            self.cur = None

    def done_set(self) -> set[int]:
        """已经做过的步骤(不含当前这一步)。循环环节型没有「做完」的说法。"""
        if self.cyclic:
            return set()
        return {i for i in self.seen if i != self.cur}

    def missed(self) -> list[dict]:
        """没完成的步骤: [{no, name, reason}]，no 从 1 起；对不上 tasks 的 no 为 0。"""
        if not self.summary:
            return []
        out = []
        for idx, name, res in self.summary:
            if res == "ok":
                continue
            full = self.tasks[idx] if idx is not None and idx < len(self.tasks) else name
            out.append({"no": (idx + 1) if idx is not None else 0, "name": full,
                        "reason": "出错" if res == "error" else res})
        return out

    def facts(self) -> list[str]:
        """读到的要点，按配置顺序，还没读到的不出现。"""
        return [t for t in (f.text() for f in self._facts) if t]

    def progress(self) -> dict | None:
        """能画成进度条的那一条: {value, total, style, text}；没有就返回 None。"""
        for f in self._facts:
            if f.progress and f.total:
                if f.value() is None:      # 还没读到数，先不画
                    return None
                v = int(f.value())
                total = int(f.total)
                return {"value": max(0, min(v, total)), "total": total, "style": f.progress,
                        "text": _fmt(f.progress_label or "{n} / {total}", n=v, total=total)}
        return None

    def headline(self) -> str | None:
        """没有总数时放在进度条旁边的一句（第一条计数型要点）。"""
        for f in self._facts:
            if f.kind == "count" and f.text():
                return f.text()
        return None
