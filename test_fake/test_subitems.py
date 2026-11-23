"""离屏测试：一首歌里的子项打勾（记忆战场每期 3 个 BOSS 那种）。不碰设备、不开真窗口、不轮询 adb。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_subitems.py

做法：
- runs/ 和 logs/ 指到临时目录，测完删掉；tools.json 不读写（registry.load 换成内存里的工具表）
- 假脚本 fake_ma.py 复制到临时目录里跑，子项文件「本期BOSS.json」也写在那里，不污染 test_fake/
- DeviceWatcher.start 换成空函数（不轮询 adb）；系统通知、警告弹窗换成记录函数
- T6 另外把基线（git 标签「基线-1020」= 原版 v2.7.5；没有标签就用第一个提交）解到临时目录，用同一套步骤跑一遍普通工具，逐项比对
- T9 普通工具的播放栏（句名小标签 / 分段进度 /「第 n / N 句」）也拿基线那一遍逐字 + 逐像素比；
  T10 的预估时长拿基线的 core/history.py 对照
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
FAKE_PY = str(HERE.parent / "bh3_auto" / ".venv" / "bin" / "python")
HEAD = ["进记忆战场", "战场减负"]
LABELS_A = ["陨星-奈埃尔 SSS", "伪神奥托 SSS", "影骑士·月轮 SS"]
LABELS_B = ["魔女-帕凡 SSS", "天命主教 SS", "往世乐土守卫 SS"]
SHOT_IDLE = Path("/tmp/phantasia_subitems_idle.png")
SHOT_PLAY = Path("/tmp/phantasia_subitems_playing.png")
BASE_KEYS = ["id", "name", "start", "end", "dur", "result", "exit", "steps", "missed", "facts", "outcome",
             "log", "shot"]

NOTIFIES: list = []
WARNS: list = []
TOASTS: list = []


# ====================================================================== 环境
class Env:
    """把某个代码目录（当前副本或基线）的幻想引擎装进本进程，所有落盘位置指到临时目录。"""

    def __init__(self, root: Path, tmp: Path, tools: list[dict]):
        sys.path.insert(0, str(root))
        from core import history, registry, runner
        runs, logs = tmp / "runs", tmp / "logs"
        history.RUNS_DIR, history.HISTORY, history.STATE = runs, runs / "history.jsonl", runs / "state.json"
        runner.RUNS_DIR, runner.LOGS_DIR, runner.SHOTS_DIR = runs, logs, runs / "shots"
        assert str(registry.AGG_DIR) == str(root), f"导入的不是 {root}，而是 {registry.AGG_DIR}"
        data = {"app": {"name": "幻想引擎·离屏测试", "logo": "logo.png", "version": "test"}, "tools": tools}
        registry.load = lambda: json.loads(json.dumps(data))

        def no_save(*_a, **_k):
            raise RuntimeError("测试里不许写 tools.json")
        registry.save = no_save
        from PySide6.QtCore import qInstallMessageHandler
        from PySide6.QtWidgets import QApplication

        def quiet(_mode, _ctx, msg):        # offscreen 插件每次布局都喊一句 propagateSizeHints，滤掉
            if "propagateSizeHints" not in msg:
                print(msg, file=sys.stderr)
        qInstallMessageHandler(quiet)
        from ui import device, theme
        from ui import window as W
        device.DeviceWatcher.start = lambda self: None          # 不轮询 adb
        W.MainWindow.startup_check = False        # 11-01 设计三：启动自检会连真设备、可能弹窗，这里关掉（test_startup.py 单独测）
        W.notify = lambda *a, **k: NOTIFIES.append(a)            # 不弹系统通知
        W.dialogs.warn = lambda *a, **k: WARNS.append(a)         # 不弹模态框
        self.app = QApplication.instance() or QApplication([])
        self.app.setStyle("Fusion")
        self.app.setPalette(theme.palette())
        self.app.setFont(theme.font(13))
        self.app.setStyleSheet(theme.APP_QSS)
        from ui.session import Session
        self.history, self.runner, self.registry, self.W = history, runner, registry, W
        self.Session = Session
        self.s = Session()
        self.s.toast.connect(TOASTS.append)
        self.win = W.MainWindow(self.s, "test")
        self.win._quitting = True        # 万一被关窗也不走「停止全部 + 还原分辨率」
        self.win.resize(1420, 680)
        self.win.show()
        self.calls: list[dict] = []
        orig = self.s.runner.start

        def spy(tool, *a, **k):          # 记下交给 runner 的工具和最终命令；趁哨兵脚本还在读一份
            ok, msg = orig(tool, *a, **k)
            c = {"tool": tool, "cmd": registry.resolve_command(tool), "ok": ok, "msg": msg, "sentinel": None}
            rt = self.s.runner.last
            if ok and rt is not None and rt.sentinel is not None:
                try:
                    c["sentinel"] = rt.sentinel.read_text(encoding="utf-8")
                except Exception:
                    pass
            self.calls.append(c)
            return ok, msg
        self.s.runner.start = spy

    def pump(self, ms: int = 30):
        from PySide6.QtCore import QEventLoop
        self.app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, ms)

    def wait(self, cond, timeout: float = 30.0) -> bool:
        t0 = time.time()
        while not cond():
            if time.time() - t0 > timeout:
                return False
            self.pump()
            time.sleep(0.01)
        self.pump()
        return True

    def play_and_wait(self, tid: str, during=None, timeout: float = 30.0) -> dict | None:
        """播放一首并等它结束；during(env) 在运行中被反复调用，返回 True 后不再调用。返回这次的播放记录。"""
        n0 = len(self.s.records)
        self.s.play(tid)
        done_during = during is None
        t0 = time.time()
        while self.s.busy or len(self.s.records) == n0:
            if time.time() - t0 > timeout:
                return None
            if not done_during and self.s.cur is not None:
                done_during = bool(during(self))
            self.pump()
            time.sleep(0.01)
        self.pump()
        return self.s.records[0] if len(self.s.records) > n0 else None

    def lyric_texts(self) -> list[str]:
        return [r.nm.text() for r in self.win._lyrics]

    def lyric_box_texts(self) -> list[str]:
        """歌词区里所有直接放进去的 QLabel 文字（不在 _lyrics 里的那种，例如提示行）"""
        from PySide6.QtWidgets import QLabel
        out = []
        box = self.win.lyric_box
        for i in range(box.count()):
            w = box.itemAt(i).widget()
            if isinstance(w, QLabel):
                out.append(w.text())
        return out

    def state_json(self) -> dict:
        try:
            return json.loads(self.history.STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def log_lines(self, rec: dict) -> list[str]:
        return (self.runner.LOGS_DIR / rec["log"]).read_text(encoding="utf-8").splitlines()


class Checker:
    def __init__(self):
        self.res: dict[str, list[str]] = {}
        self.cur = ""

    def start(self, name: str):
        self.cur = name
        self.res.setdefault(name, [])

    def ok(self, cond, what: str):
        if not cond:
            self.res[self.cur].append(what)
        return bool(cond)


# ====================================================================== 普通工具（T6，当前版本和基线都跑这一段）
def plain_tool(tmp: Path) -> dict:
    wd = tmp / "plain"
    wd.mkdir(parents=True, exist_ok=True)
    script = "echo '== 第一句'; sleep 0.2; echo '== 第二句'; sleep 0.1; echo '完成：第一句=ok，第二句=ok'"
    return {"id": "plain_echo", "name": "假普通工具（只 echo）", "env": "none", "workdir": str(wd),
            "command": ["/bin/sh", "-c", script], "need_device": False, "tasks": ["第一句", "第二句"],
            "report_steps": True, "album": "测试"}


def _png_md5(w) -> str:
    """控件现在的样子（PNG 字节）的 md5，用来逐像素比对。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    w.grab().save(buf, "PNG")
    buf.close()
    return hashlib.md5(bytes(ba)).hexdigest()


def player_snap(env: Env, pix: bool = False) -> dict:
    """底部播放栏现在显示什么：分段进度、「第 n / N 句」（基线还有句名小标签）；pix=True 再加控件的逐像素 md5。
    11-02 起播放区没有句名小标签（作者：「单步的摘要可以不要」），只有基线代码里才有 chip。"""
    win = env.win
    chip, segs, pcnt = getattr(win, "chip", None), win.segs, win.pcnt
    out = {"cnt": [pcnt.text(), pcnt.isVisible()],
           "segs": [segs._total, sorted(segs._done), segs._cur, segs._busy, segs.isVisible()],
           "tag": win.ptag.text() if win.ptag.isVisible() else "", "name": win.pname.text()}
    if chip is not None:
        out["chip"] = [chip._no, chip._text, chip.isVisible()]
    parts = [("segs", segs), ("cnt", pcnt)] + ([("chip", chip)] if chip is not None else [])
    if pix:
        from PySide6.QtCore import QEvent
        from ui.widgets import Anim
        env.app.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)   # 只把布局排好，不处理新日志行
        a = Anim.get()
        old = a.reduce
        a.reduce = True                  # 条纹动画的相位固定在 0，两边才能逐像素比
        try:
            out["size"] = {k: [w.width(), w.height()] for k, w in parts}
            out["px"] = {k: _png_md5(w) for k, w in parts}
        finally:
            a.reduce = old
    return out


def run_plain(env: Env) -> dict:
    """普通工具从选中、播放到看历史日志的全过程，返回可以拿来比对的结果（去掉时间、文件名）。"""
    s, win = env.s, env.win
    s.set_notify(True)                   # 让两边都落一次 state.json，好比对顶层键
    s.select("plain_echo")
    env.pump()
    idle_lyrics = env.lyric_texts()
    idle_boxes = [getattr(r, "box", None) is not None for r in win._lyrics]
    mid = {}
    player = {}                          # 播放栏：每一句第一次出现时的样子（T9 和基线比）

    def during(e):
        st = e.s.cur
        if st is None:
            return False
        tr = st.tracker
        if tr.has_steps and "tasks" not in mid:
            mid["tasks"] = list(tr.tasks)
            mid["lyrics"] = e.lyric_texts()
            mid["tool_is_same_dict"] = st.tool is e.s.tool("plain_echo")
        if tr.cur is not None and str(tr.cur) not in player:
            player[str(tr.cur)] = player_snap(e, pix=True)
        return "tasks" in mid and str(len(tr.tasks) - 1) in player
    rec = env.play_and_wait("plain_echo", during)
    call = env.calls[-1] if env.calls else {}
    out = {
        "cmd": call.get("cmd"),
        "runner_tool_is_same_dict": call.get("tool") is s.tool("plain_echo"),
        "idle_lyrics": idle_lyrics, "idle_boxes": idle_boxes,
        "mid": mid,
        "player": player,
        "after_lyrics": env.lyric_texts(),
        "tab_count": win.tabs.btns["steps"].count,
        "rec_keys": list(rec.keys()) if rec else None,
        "rec": {k: v for k, v in (rec or {}).items() if k not in ("start", "end", "dur", "log")},
        "log": [re.sub(r"耗时 .*$", "耗时 …", re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", ln))
                for ln in (env.log_lines(rec) if rec else [])],
        "state_keys": sorted(env.state_json().keys()),
    }
    # 看历史日志：标题行着色
    if rec:
        win._hist_open(rec)
        env.pump()
        out["hist_text"] = win.hist_log.toPlainText().splitlines()
        out["hist_text"] = [re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", ln) for ln in out["hist_text"] if "耗时" not in ln]
        win._hist_back()
    return out


def plain_only(root: Path):
    """子进程模式：在 root 那份代码上跑 run_plain，把结果打成一行 JSON。"""
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_base_"))
    try:
        env = Env(root, tmp, [plain_tool(tmp)])
        out = run_plain(env)
        out.pop("runner_tool_is_same_dict", None)
        print("PLAIN_JSON " + json.dumps(out, ensure_ascii=False, default=str))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================== 主测试
def main() -> int:
    root = HERE.parent
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_sub_"))
    fake_dir = tmp / "fake"
    fake_dir.mkdir()
    shutil.copy2(HERE / "fake_ma.py", fake_dir / "fake_ma.py")
    sub_file = fake_dir / "本期BOSS.json"
    fake = {"id": "fake_ma", "name": "假记忆战场（离线测子项，不碰设备）", "env": "none", "workdir": str(fake_dir),
            "command": ["python", "fake_ma.py", "--subitems"], "python": FAKE_PY, "precheck": ["fake_ma.py"],
            "need_device": False, "tasks": list(HEAD), "report_steps": True, "album": "测试",
            "subitems": {"file": "本期BOSS.json", "head": list(HEAD), "arg": "--pick"}}
    fake_orig = json.loads(json.dumps(fake))
    os.environ["FAKE_LABELS"] = "|".join(LABELS_A)
    c = Checker()
    try:
        env = Env(root, tmp, [fake, plain_tool(tmp)])
        s, win = env.s, env.win
        from core import steps

        # ---------------------------------------------------------------- T0 tracker 单元
        c.start("T0 StepTracker 子项行")
        tr = steps.StepTracker(HEAD)
        c.ok(not tr.feed("[10:00:00] 子项：A|B") and tr.tasks == HEAD, "没设 sub_head 的 tracker 不该认「子项：」行")
        tr = steps.StepTracker(HEAD, sub_head=2)
        c.ok(tr.feed("[10:00:00] 子项：A，x|B=y | C ") and tr.tasks == HEAD + ["Ax", "By", "C"],
             f"带时间前缀的子项行没换成 head+去符号的名字：{tr.tasks}")
        c.ok(tr.feed("子项：D") and tr.tasks == HEAD + ["D"], f"不带时间前缀的子项行：{tr.tasks}")

        # ---------------------------------------------------------------- T1 没有子项文件
        c.start("T1 没有子项文件")
        os.environ["FAKE_SLEEP"] = "0.3"
        c.ok(not sub_file.exists(), "开测前子项文件就已经存在")
        s.select("fake_ma")
        env.pump()
        c.ok(env.lyric_texts() == HEAD, f"空闲歌词应只有 head 两句：{env.lyric_texts()}")
        hint = "还没读到本期 BOSS：先播放一次（不勾也行）"
        c.ok(hint in env.lyric_box_texts(), f"缺提示行：{env.lyric_box_texts()}")
        c.ok(all(r.nm.text() != hint for r in win._lyrics), "提示行不该进 _lyrics")
        mid = {}

        def t1_during(e):
            st = e.s.cur
            if st and len(st.tracker.tasks) == 5:
                mid["tasks"] = list(st.tracker.tasks)
                mid["lyrics"] = e.lyric_texts()
                mid["boxes"] = [r.box is not None for r in e.win._lyrics]
                mid["modes"] = [r.box._mode for r in e.win._lyrics if r.box is not None]
                mid["muted"] = [r._muted for r in e.win._lyrics]
                mid["cnt"] = player_snap(e)["cnt"]
                mid["hint"] = hint in e.lyric_box_texts()
                return True
            return False
        rec = env.play_and_wait("fake_ma", t1_during)
        call = env.calls[-1]
        c.ok("--pick" not in call["cmd"], f"命令不该带 --pick：{call['cmd']}")
        c.ok(call["sentinel"] and "fake_ma.py --subitems" in call["sentinel"] and "--pick" not in call["sentinel"],
             "哨兵脚本里的命令不对（应有 --subitems、无 --pick）")
        c.ok(mid.get("tasks") == HEAD + LABELS_A, f"运行中 tracker.tasks 没变成 head+3：{mid.get('tasks')}")
        c.ok(mid.get("lyrics") == HEAD + LABELS_A, f"运行中歌词没按新列表重建：{mid.get('lyrics')}")
        c.ok(mid.get("boxes") == [False, False, True, True, True], f"运行中勾选框位置不对：{mid.get('boxes')}")
        # 没传勾选 = 这次只读名单、不开打：歌词里的子项不勾、灰显，和播放栏（只数固定句 2 句）对得上（审查 10-10）
        c.ok(mid.get("modes") == ["box"] * 3, f"没传勾选时运行中子项应全是空框：{mid.get('modes')}")
        c.ok(mid.get("muted") == [False, False, True, True, True], f"没传勾选时运行中子项应灰显：{mid.get('muted')}")
        c.ok(mid.get("cnt") == ["第 2 / 2 句", True], f"没传勾选时播放栏应只数固定句：{mid.get('cnt')}")
        c.ok(mid.get("hint") is False, "运行中不该再显示提示行")
        c.ok(rec is not None, "没有等到播放记录")
        if rec:
            # 和真脚本一致：第一次没有名单 = 只读名单、只减负，不开打 →「战场减负」报换期，记 1 句未完成
            c.ok(rec["result"] == env.history.PARTIAL, f"result={rec['result']} missed={rec['missed']}")
            c.ok([m["name"] for m in rec["missed"]] == ["战场减负"], f"missed={rec['missed']}")
            c.ok(rec["steps"] == 2, f"steps={rec['steps']}")
            c.ok("picks" in rec and rec["picks"] is None, f"没传勾选应记 None：picks={rec.get('picks')}")
            c.ok(rec.get("tasks") == HEAD + LABELS_A, f"tasks={rec.get('tasks')}")
            log = env.log_lines(rec)
            c.ok("本次勾选：还没读到本期名单" in log[0], f"日志第一行不是勾选说明：{log[0]}")
        c.ok(sub_file.exists(), "假脚本没写出子项文件")
        c.ok(env.lyric_texts() == HEAD + LABELS_A, f"结束后空闲歌词应是 head+文件里的名字：{env.lyric_texts()}")
        c.ok(hint not in env.lyric_box_texts(), "有子项文件后不该再显示提示行")
        c.ok([r.box._mode for r in win._lyrics[2:]] == ["tick"] * 3, "没勾选记录时应全勾")
        c.ok("fake_ma" not in env.state_json().get("picks", {}), "没点过勾选，state.json 不该有 fake_ma 的 picks")
        c.ok(env.state_json().get("pick_labels", {}).get("fake_ma") == LABELS_A, "第一次见到名单应记下快照（勾选不动）")

        # ---------------------------------------------------------------- T2 取消第 2 个
        c.start("T2 取消第 2 个")
        win._lyrics[3].box.clicked.emit()          # 等于点了第 2 个 BOSS 前面的勾选框
        env.pump()
        c.ok(env.state_json().get("picks") == {"fake_ma": [1, 3]}, f"state.json picks={env.state_json().get('picks')}")
        s.select("plain_echo")
        s.toggle_check("fake_ma")
        s.toggle_check("fake_ma")
        s.select("fake_ma")
        env.pump()
        c.ok(env.state_json().get("picks") == {"fake_ma": [1, 3]}, "select / toggle_check 之后 picks 被冲掉了")
        c.ok(env.Session().picks == {"fake_ma": [1, 3]}, "重开会话没读回 picks")
        good = env.history.STATE.read_text(encoding="utf-8")
        bad = json.loads(good)
        bad["picks"] = {"fake_ma": [3, "x", 0, -1, True, 1, 3, 2.5], "bad": "zz", "x2": None}
        env.history.STATE.write_text(json.dumps(bad), encoding="utf-8")
        c.ok(env.Session().picks == {"fake_ma": [1, 3]}, "读入时没过滤掉非法值")
        env.history.STATE.write_text(good, encoding="utf-8")
        row2 = win._lyrics[3]
        c.ok(row2.box._mode == "box" and row2._muted, "第 2 个应是空框 + 灰显")
        c.ok(env.W.T.MUTED in row2.nm.styleSheet(), f"第 2 个字没灰显：{row2.nm.styleSheet()}")
        c.ok([r.box._mode for r in win._lyrics[2:]] == ["tick", "box", "tick"], "勾选框状态不对")
        c.ok(not any(r.box._dim for r in win._lyrics[2:]), "空闲时勾选框不该半透明")
        win.grab().save("/tmp/_phantasia_full.png")          # 只为触发一次完整布局
        win.detail_card.grab().save(str(SHOT_IDLE))
        mid = {}

        def t2_during(e):
            st = e.s.cur
            if st and st.tracker.cur is not None and st.tracker.cur >= 2:
                mid["lyrics"] = e.lyric_texts()
                mid["muted"] = [r._muted for r in e.win._lyrics]
                mid["css3"] = e.win._lyrics[3].nm.styleSheet()
                mid["same_copy"] = e.s.cur.tool is e.calls[-1]["tool"]
                return True
            return False
        rec = env.play_and_wait("fake_ma", t2_during)
        call = env.calls[-1]
        c.ok(call["cmd"][-2:] == ["--pick", "1,3"], f"命令末尾应是 --pick 1,3：{call['cmd']}")
        c.ok(call["sentinel"] and "fake_ma.py --subitems --pick 1,3" in call["sentinel"], "哨兵脚本里没有 --pick 1,3")
        c.ok(call["tool"] is not s.tool("fake_ma"), "交给 runner 的应是浅拷贝，不是 self.tools 里的原字典")
        c.ok(mid.get("same_copy") is True, "RunState 和 runner.start 拿到的应是同一份浅拷贝")
        c.ok(s.tool("fake_ma") == fake_orig, "self.tools 里的 fake_ma 被改了")
        c.ok(mid.get("muted") == [False, False, False, True, False], f"运行中第 2 个应灰显：{mid.get('muted')}")
        c.ok(env.W.T.MUTED in (mid.get("css3") or ""), "运行中 set_state 把灰显冲掉了")
        if c.ok(rec is not None, "没有等到播放记录"):
            c.ok(rec["result"] == "ok", f"result={rec['result']}")
            c.ok(rec["missed"] == [], f"missed={rec['missed']}")
            c.ok(rec["steps"] == 4, f"steps={rec['steps']}")
            c.ok(rec.get("picks") == [1, 3], f"picks={rec.get('picks')}")
            c.ok(rec.get("tasks") == HEAD + LABELS_A and len(rec["tasks"]) == 5, f"tasks={rec.get('tasks')}")
            log = env.log_lines(rec)
            c.ok(log[0].endswith("本次勾选：第 1、3 个（陨星-奈埃尔 SSS、影骑士·月轮 SS）；传给脚本 --pick 1,3"),
                 f"日志第一行：{log[0]}")
            c.ok(not any(ln.endswith("== 伪神奥托 SSS") for ln in log), "脚本打了没勾的第 2 个（没收到 --pick）")
            c.ok(sum(1 for ln in log if re.search(r"\] == ", ln)) == 4, "脚本应只打 4 个「== 」标题行")
            # 看历史日志用记录里的 tasks
            win._hist_open(rec)
            env.pump()
            c.ok(win.hist_stack.currentIndex() == 1, "看日志页没打开")
            win._hist_back()

        # ---------------------------------------------------------------- T5 播放中改勾选被拒绝
        c.start("T5 播放中 toggle_pick 被拒绝")
        before_state = env.state_json().get("picks")
        mid = {}

        def t5_during(e):
            st = e.s.cur
            if st and st.tracker.cur is not None and st.tracker.cur >= 2:     # 正在打第 1 个 BOSS
                n0 = len(TOASTS)
                e.s.toggle_pick("fake_ma", 2)                 # 直接调
                e.win._lyrics[2].box.clicked.emit()            # 点界面上的勾选框
                e.pump()
                mid["toasts"] = TOASTS[n0:]
                mid["picks"] = dict(e.s.picks)
                mid["dim"] = [r.box._dim for r in e.win._lyrics[2:]]
                mid["modes"] = [r.box._mode for r in e.win._lyrics[2:]]
                e.win.detail_card.grab().save(str(SHOT_PLAY))
                return True
            return False
        rec = env.play_and_wait("fake_ma", t5_during)
        c.ok(mid.get("picks") == {"fake_ma": [1, 3]}, f"播放中 picks 变了：{mid.get('picks')}")
        c.ok(len(mid.get("toasts") or []) == 2 and all("下次播放才生效" in t for t in mid["toasts"]),
             f"应提示两次「下次播放才生效」：{mid.get('toasts')}")
        c.ok(mid.get("dim") == [True, True, True], f"播放中勾选框应半透明：{mid.get('dim')}")
        c.ok(mid.get("modes") == ["tick", "box", "tick"], f"播放中勾选框显示的是这次的勾选：{mid.get('modes')}")
        c.ok(env.state_json().get("picks") == before_state, "state.json 的 picks 变了")
        c.ok(rec is not None and rec.get("picks") == [1, 3], "这次的记录 picks 应仍是 [1,3]")
        s.pending = "fake_ma"                           # 「正要启动」那一刻也不能改
        n0 = len(TOASTS)
        s.toggle_pick("fake_ma", 2)
        s.pending = None
        c.ok(s.picks == {"fake_ma": [1, 3]} and len(TOASTS) == n0 + 1, "正要启动时也应拒绝")
        env.pump()
        c.ok(not any(r.box._dim for r in win._lyrics[2:]), "播完后勾选框应恢复可点")

        # ---------------------------------------------------------------- T3 全不勾
        c.start("T3 全不勾")
        os.environ["FAKE_SLEEP"] = "0.05"
        s.toggle_pick("fake_ma", 1)
        s.toggle_pick("fake_ma", 3)
        env.pump()
        c.ok(env.state_json().get("picks") == {"fake_ma": []}, f"全不勾应存空列表：{env.state_json().get('picks')}")
        c.ok(all(r._muted for r in win._lyrics[2:]), "全不勾时三个子项都应灰显")
        rec = env.play_and_wait("fake_ma")
        call = env.calls[-1]
        c.ok(call["cmd"][-2:] == ["--pick", "none"], f"命令末尾应是 --pick none：{call['cmd']}")
        c.ok(call["sentinel"] and "--pick none" in call["sentinel"], "哨兵脚本里没有 --pick none")
        if c.ok(rec is not None, "没有等到播放记录"):
            c.ok(rec["result"] == "ok", f"result={rec['result']} missed={rec['missed']}")
            c.ok(rec["steps"] == 2, f"steps={rec['steps']}")
            c.ok(rec.get("picks") == [], f"picks={rec.get('picks')}")
            log = env.log_lines(rec)
            c.ok(sum(1 for ln in log if re.search(r"\] == ", ln)) == 2, "只该走 head 两句")
            c.ok("一个都没勾" in log[0], f"日志第一行：{log[0]}")

        # ---------------------------------------------------------------- T4 换期
        c.start("T4 换期")
        s.toggle_pick("fake_ma", 1)
        s.toggle_pick("fake_ma", 3)            # 回到 [1,3]
        n4 = len(TOASTS)
        os.environ["FAKE_LABELS"] = "|".join(LABELS_B)
        c.ok(env.lyric_texts() == HEAD + LABELS_A, "开播前歌词应还是上一期的名字")
        mid = {}

        def t4_changed():
            # 接在界面之后的 changed 槽：「子项：」行一到，界面刚按新名单重建完就记下来（换期时脚本紧接着就结束）
            st = s.cur
            if "lyrics" not in mid and st and st.tracker.tasks[2:] == LABELS_B:
                mid["lyrics"] = env.lyric_texts()
                mid["cnt"] = win.pcnt.text()
                mid["boxes"] = [r.box._mode for r in win._lyrics if r.box is not None]
        s.changed.connect(t4_changed)
        rec = env.play_and_wait("fake_ma")
        s.changed.disconnect(t4_changed)
        call = env.calls[-1]
        c.ok(call["cmd"][-2:] == ["--pick", "1,3"], f"开播时按旧名单传勾选：{call['cmd']}")
        if c.ok(rec is not None, "没有等到播放记录"):
            c.ok(rec["result"] == "partial", f"result={rec['result']}")
            c.ok(len(rec["missed"]) == 1, f"missed={rec['missed']}")
            if rec["missed"]:
                m = rec["missed"][0]
                c.ok(m["name"] == "战场减负" and m["no"] == 2 and "本期 BOSS 换了" in m["reason"], f"missed[0]={m}")
            c.ok(rec.get("tasks") == HEAD + LABELS_B, f"记录 tasks 应是新名字：{rec.get('tasks')}")
            c.ok(rec.get("picks") == [1, 3], f"picks={rec.get('picks')}")
            c.ok(rec.get("steps") == 2, f"换期只跑固定句，steps 应为 2：{rec.get('steps')}")
        c.ok(mid.get("lyrics") == HEAD + LABELS_B, f"运行中歌词没换成新名字：{mid.get('lyrics')}")
        c.ok(mid.get("boxes") == ["box"] * 3, f"换期后歌词子项应全不勾（这次不开打）：{mid.get('boxes')}")
        c.ok(mid.get("cnt", "").endswith("/ 2 句"), f"换期后播放栏应只数固定句：{mid.get('cnt')}")
        c.ok(env.lyric_texts() == HEAD + LABELS_B, f"结束后歌词应是新名字：{env.lyric_texts()}")
        # 10-15 作者定：换期自动清空勾选，等用户重新勾
        env.pump()
        c.ok(env.state_json().get("picks") == {"fake_ma": []}, f"换期后勾选应清成全不勾：{env.state_json().get('picks')}")
        c.ok(env.state_json().get("pick_labels", {}).get("fake_ma") == LABELS_B, "换期后快照应换成新名单")
        c.ok(sum("勾选已清空" in t for t in TOASTS[n4:]) == 1, f"换期清空应提示一次：{TOASTS[n4:]}")
        c.ok([r.box._mode for r in win._lyrics[2:]] == ["box"] * 3, "换期后歌词子项应全不勾")
        s.toggle_pick("fake_ma", 1)
        s.toggle_pick("fake_ma", 3)            # 重新勾回 1,3，后面接着测
        c.ok(s.picks == {"fake_ma": [1, 3]}, f"重新勾回 1,3：{s.picks}")

        # ---------------------------------------------------------------- T8 中途出错
        # 脚本端约定：出错后「完成：」汇总照样列出固定句和勾了的 BOSS，没打到的写「没做到（前面出错）」，
        # 幻想引擎照汇总记未完成（审查 10-10：真脚本原先漏列没打到的 BOSS，幻想引擎就少算）
        c.start("T8 中途出错：勾了没打到的也算未完成")
        s.set_notify(True)
        late = "没做到（前面出错）"
        cases = [
            ("勾 1,3、打第 1 个时出错", None, "boss1",
             [(3, LABELS_B[0], "出错：开战后 25 秒没进战斗界面"), (5, LABELS_B[2], late)], 4, [1, 3]),
            ("全勾、打第 1 个时出错", 2, "boss1",
             [(3, LABELS_B[0], "出错：开战后 25 秒没进战斗界面"), (4, LABELS_B[1], late), (5, LABELS_B[2], late)],
             5, [1, 2, 3]),
            ("勾 1,3、减负时出错（还没打「子项：」行）", 2, "战场减负",
             [(2, "战场减负", "出错：减负按钮没认出来"), (3, LABELS_B[0], late), (5, LABELS_B[2], late)], 4, [1, 3]),
            ("勾 1,3、进记忆战场就出错", None, "进记忆战场",
             [(1, "进记忆战场", "出错：没认出记忆战场入口"), (2, "战场减负", late), (3, LABELS_B[0], late),
              (5, LABELS_B[2], late)], 4, [1, 3]),
        ]
        try:
            for title, flip, fail_at, want, want_steps, want_picks in cases:
                if flip:
                    s.toggle_pick("fake_ma", flip)
                os.environ["FAKE_FAIL_AT"] = fail_at
                n0 = len(NOTIFIES)
                rec = env.play_and_wait("fake_ma")
                if not c.ok(rec is not None, f"{title}：没有等到播放记录"):
                    continue
                got = [(m["no"], m["name"], m["reason"]) for m in rec["missed"]]
                c.ok(rec["result"] == "partial", f"{title}：result={rec['result']}")
                c.ok(got == want, f"{title}：missed={got}")
                c.ok(rec["steps"] == want_steps, f"{title}：steps={rec['steps']}")
                c.ok(rec.get("picks") == want_picks, f"{title}：picks={rec.get('picks')}")
                c.ok(rec.get("tasks") == HEAD + LABELS_B, f"{title}：tasks={rec.get('tasks')}")
                c.ok(env.history.result_text(rec).startswith(f"{len(want)} "),
                     f"{title}：结果用语={env.history.result_text(rec)}")
                c.ok(NOTIFIES[n0:] and f"有 {len(want)} 项未完成" in NOTIFIES[-1][0],
                     f"{title}：通知={NOTIFIES[n0:]}")
        finally:
            os.environ.pop("FAKE_FAIL_AT", None)

        # ---------------------------------------------------------------- T9 播放栏只数要跑的句子
        # 带子项的歌：分段格、「第 n / N 句」、句名小标签的序号只算固定句 + 勾了的子项；没勾的不占格
        c.start("T9 播放栏只数要跑的句子")
        os.environ["FAKE_SLEEP"] = "0.3"          # 每句停 0.3 秒，每一句都来得及看一眼播放栏
        c.ok(s.picks == {"fake_ma": [1, 3]} and env.lyric_texts() == HEAD + LABELS_B, "开测前应是新名单、勾 1,3")

        def bar_run():
            """播放一次 fake_ma，记下播放栏在每一句（tracker 下标）第一次出现时的样子，按出现先后。"""
            seen: dict = {}

            def during(e):
                tr = e.s.cur.tracker
                if tr.cur is not None and tr.cur not in seen:
                    seen[tr.cur] = player_snap(e)
                return False
            rec = env.play_and_wait("fake_ma", during)
            return rec, seen

        def want(no: int, n: int, name: str, done: list[int], row: int | None = None) -> dict:
            """no / n：播放栏「第 no / n 句」和分段（只数要跑的）。name、row 是以前句名小标签用的，11-02 去掉小标签后不再比。"""
            return {"cnt": [f"第 {no} / {n} 句", True], "segs": [n, done, no - 1, False, True]}

        def same(got: dict | None, exp: dict) -> bool:
            return got is not None and all(got.get(k) == v for k, v in exp.items())

        rec, seen = bar_run()
        c.ok(rec is not None and rec["result"] == "ok", f"勾 1,3：result={rec and rec['result']}")
        c.ok(list(seen) == [0, 1, 2, 4], f"勾 1,3：走过的句子（下标）应是 0,1,2,4：{list(seen)}")
        for idx, exp in ((0, want(1, 4, HEAD[0], [])), (1, want(2, 4, HEAD[1], [0])),
                         (2, want(3, 4, LABELS_B[0], [0, 1])), (4, want(4, 4, LABELS_B[2], [0, 1, 2], row=5))):
            c.ok(same(seen.get(idx), exp), f"勾 1,3、第 {idx + 1} 句：\n    应是 {exp}\n    实际 {seen.get(idx)}")
        s.toggle_pick("fake_ma", 1)
        s.toggle_pick("fake_ma", 3)
        c.ok(s.picks == {"fake_ma": []}, f"全不勾：picks={s.picks}")
        rec, seen = bar_run()
        c.ok(rec is not None and rec["result"] == "ok", f"全不勾：result={rec and rec['result']}")
        c.ok(list(seen) == [0, 1], f"全不勾：走过的句子应是 0,1：{list(seen)}")
        for idx, exp in ((0, want(1, 2, HEAD[0], [])), (1, want(2, 2, HEAD[1], [0]))):
            c.ok(same(seen.get(idx), exp), f"全不勾、第 {idx + 1} 句：\n    应是 {exp}\n    实际 {seen.get(idx)}")
        s.toggle_pick("fake_ma", 1)
        s.toggle_pick("fake_ma", 3)                # 回到 [1,3]
        os.environ["FAKE_SLEEP"] = "0.05"

        # 不跑脚本、直接摆一个 RunState 喂日志行：准备中 / 没传勾选 / 当前句是没勾的（退回全部）/ 运行中名单变了
        from types import SimpleNamespace
        from ui.session import RunState

        def bar_unit(picks, tasks, lines, lyr: dict | None = None):
            """lyr 给了就顺带记下歌词里子项的勾选框（lyr["modes"]），查歌词和播放栏用的是不是同一份「要跑的句子」。"""
            st = RunState(dict(s.tool("fake_ma"), tasks=list(tasks)), SimpleNamespace(started=time.time()), picks)
            for ln in lines:
                st.tracker.feed(ln)
            s.cur = st
            try:
                win._refresh_player()
                if lyr is not None:
                    win._sync_lyrics(s.tool("fake_ma"))
                    lyr["modes"] = [r.box._mode for r in win._lyrics if r.box is not None]
                return player_snap(env)
            finally:
                s.cur = None
                win._refresh_player()
                if lyr is not None:
                    win._sync_lyrics(s.tool("fake_ma"))

        prep = {"cnt": ["准备中", True]}       # 句名小标签此时隐藏、里面是上一次的残留，单独查可见性
        # 最后一项：歌词里子项的勾选框应是什么样（和播放栏同一份「要跑的句子」）；None = 不查
        cases = [
            ("勾 1,3、还没到第一句（准备中）", [1, 3], HEAD + LABELS_B, [],
             dict(prep, segs=[4, [], None, False, True]), ["tick", "box", "tick"]),
            ("没传勾选、准备中", None, HEAD, [], dict(prep, segs=[2, [], None, False, True]), []),
            ("没传勾选、运行中读到名单后到第 2 句", None, HEAD, ["子项：甲 SSS|乙 SS|丙 SSS", "== 进记忆战场", "== 战场减负"],
             want(2, 2, HEAD[1], [0]), ["box"] * 3),
            ("勾 1,3、当前句是没勾的第 2 个（退回按全部 5 句算）", [1, 3], HEAD + LABELS_B,
             ["== 进记忆战场", "== 战场减负", f"== {LABELS_B[1]}"], want(4, 5, LABELS_B[1], [0, 1]), None),
            # 运行中名单变了 = 换期：脚本不会再打 BOSS（只减负），播放栏、歌词都只算固定句；
            # 下面两例故意喂了 BOSS 标题行，当前句不在要跑的句子里 → 退回按全部句子算
            ("勾 1,3、运行中名单变成 2 个（换期）", [1, 3], HEAD + LABELS_B,
             ["子项：甲 SSS|乙 SS", "== 进记忆战场", "== 战场减负", "== 甲 SSS"], want(3, 4, "甲 SSS", [0, 1]),
             ["box", "box"]),
            ("勾 1,3、运行中名单变成 4 个（换期）", [1, 3], HEAD + LABELS_B,
             ["子项：丁 SSS|戊 SS|己 SSS|庚 SS", "== 进记忆战场", "== 己 SSS"], want(5, 6, "己 SSS", [0]),
             ["box", "box", "box", "box"]),
            ("勾 1,3、运行中名单没变（同一期）", [1, 3], HEAD + LABELS_B,
             ["子项：" + "|".join(LABELS_B), "== 进记忆战场", "== 战场减负", f"== {LABELS_B[2]}"],
             want(4, 4, LABELS_B[2], [0, 1], row=5), ["tick", "box", "tick"]),
        ]
        for title, picks, tasks, lines, exp, modes in cases:
            lyr: dict = {}
            got = bar_unit(picks, tasks, lines, lyr if modes is not None else None)
            c.ok(same(got, exp), f"{title}：\n    应是 {exp}\n    实际 {got}")
            if modes is not None:
                c.ok(lyr.get("modes") == modes, f"{title}：歌词子项勾选框应是 {modes}，实际 {lyr.get('modes')}")
        got = bar_unit([1, 3], HEAD + LABELS_B, [])
        c.ok("chip" not in got, f"11-02 起播放区不该有句名小标签：{got.get('chip')}")

        # ---------------------------------------------------------------- T6 回归：普通工具
        c.start("T6 回归：没有 subitems 的工具")
        cur = run_plain(env)
        tool = s.tool("plain_echo")
        c.ok(cur["runner_tool_is_same_dict"], "普通工具交给 runner 的应是原字典（没复制、没改）")
        c.ok(cur["cmd"] == tool["command"], f"命令变了：{cur['cmd']}")
        c.ok(cur["mid"].get("tasks") == tool["tasks"], f"tracker.tasks：{cur['mid'].get('tasks')}")
        c.ok(cur["mid"].get("tool_is_same_dict"), "RunState.tool 应是原字典")
        c.ok(cur["idle_boxes"] == [False, False], "普通工具歌词不该有勾选框")
        c.ok(cur["rec_keys"] == BASE_KEYS, f"记录字段：{cur['rec_keys']}")
        c.ok(cur["rec"].get("steps") == 2 and cur["rec"].get("result") == "ok", f"记录：{cur['rec']}")
        c.ok(not cur["log"][0].startswith("[引擎]"), f"普通工具日志第一行不该有勾选说明：{cur['log'][0]}")
        # 和改之前的基线逐项比（播放栏 player 交给 T9 比）
        base = mine = None
        base_dir = tmp / "base"
        base_dir.mkdir()
        # 基线：有「基线-1020」标签（作者开发库里 v2.7.5 的那个提交）才比；公开仓库没有这个标签，和旧版逐项比的部分跳过
        first = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "基线-1020^{commit}"],
                               capture_output=True, text=True).stdout.split()
        if not first:
            print("  （没有「基线-1020」标签：T6、T9、T10 里和旧版逐项比的部分跳过）")
        if first:
            arc = subprocess.run(["git", "-C", str(root), "archive", first[0]], capture_output=True)
            subprocess.run(["tar", "-x", "-C", str(base_dir)], input=arc.stdout, check=True)
            # 11-14 幻想引擎版本二有意换了配色：基线套上现在的 theme.py 再比，逐像素比的是版式和文字，不是颜色
            shutil.copyfile(root / "ui" / "theme.py", base_dir / "ui" / "theme.py")
            p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--plain-only", str(base_dir)],
                               capture_output=True, text=True, timeout=120,
                               env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("PLAIN_JSON ")), None)
            if c.ok(line, f"基线子进程没给结果：{p.stdout[-500:]} {p.stderr[-1500:]}"):
                base = json.loads(line[len("PLAIN_JSON "):])
                mine = json.loads(json.dumps({k: v for k, v in cur.items() if k != "runner_tool_is_same_dict"},
                                             ensure_ascii=False, default=str))
                for k in sorted(set(base) | set(mine)):
                    if k == "player":
                        continue
                    if k == "state_keys":     # state.json 多了 picks、pick_labels（10-15 换期清空）两个顶层键是规格要求的
                        c.ok("picks" in mine[k] and set(mine[k]) - set(base[k]) <= {"picks", "pick_labels"} and set(base[k]) <= set(mine[k]),
                             f"state.json 顶层键：基线 {base[k]} / 现在 {mine[k]}")
                        continue
                    c.ok(base.get(k) == mine.get(k), f"和基线不一样：{k}\n    基线 {base.get(k)}\n    现在 {mine.get(k)}")
                print(f"  T6 已和基线（{first[0][:7]}）逐项比对：{', '.join(k for k in sorted(base) if k != 'player')}")

        # ---------------------------------------------------------------- T9（续）普通工具的播放栏和基线比
        c.start("T9 播放栏只数要跑的句子")
        pl = cur["player"]
        c.ok(list(pl) == ["0", "1"], f"普通工具：两句都该看到播放栏：{list(pl)}")
        for k, exp in (("0", want(1, 2, "第一句", [])), ("1", want(2, 2, "第二句", [0]))):
            c.ok(same(pl.get(k), exp), f"普通工具第 {int(k) + 1} 句：\n    应是 {exp}\n    实际 {pl.get(k)}")
        if first and c.ok(base is not None and mine is not None, "没有基线结果，普通工具的播放栏没法和改之前比"):
            bp, mp = base.get("player") or {}, mine.get("player") or {}
            c.ok(list(bp) == ["0", "1"] and all("px" in v for v in bp.values()), f"基线没截到两句的播放栏：{bp}")
            # 11-01 信息精简：正常播放时不再挂「正在播放」字样（基线挂着）；其余逐字 + 逐像素照旧和基线比
            def no_tag(d):
                out = {}
                for k, v in d.items():
                    v2 = {kk: vv for kk, vv in v.items() if kk not in ("tag", "chip")}     # 11-02 句名小标签去掉了，基线还有
                    # 11-01 设计三：播放条右端提示位平时收起，进度条变长——进度条只比状态（几格、哪格完成），不比宽度和像素
                    for key in ("size", "px"):
                        if key in v2:
                            v2[key] = {kk: vv for kk, vv in v2[key].items() if kk not in ("segs", "chip")}
                    out[k] = v2
                return out
            c.ok(no_tag(bp) == no_tag(mp), f"普通工具播放栏和基线不一样（逐字 + 逐像素）：\n    基线 {bp}\n    现在 {mp}")
            c.ok(all(v.get("tag") == "正在播放" for v in bp.values()) and all(v.get("tag") == "" for v in mp.values()),
                 f"「正在播放」字样：基线应显示、现在应不显示：{[v.get('tag') for v in bp.values()]} / {[v.get('tag') for v in mp.values()]}")
            print(f"  T9 普通工具播放栏已和基线（{first[0][:7]}）逐字 + 逐像素比对：{len(bp)} 句 × segs / cnt（句名小标签 11-02 去掉了）")

        # ---------------------------------------------------------------- T11 换期自动清空：错字不算、外面换了名单也清
        c.start("T11 换期自动清空：OCR 错字不算换期")
        s.select("fake_ma")                      # T6 选中了普通工具；歌词区画这首时才读名单比快照（开播、点勾选时也会比）
        env.pump()
        good_txt = sub_file.read_text(encoding="utf-8")

        def write_labels(labels):
            d = json.loads(good_txt)
            for it, lb in zip(d["items"], labels):
                it["label"] = lb
            sub_file.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            s.changed.emit()
            env.pump()
        n11 = len(TOASTS)
        try:
            typo = [LABELS_B[0].replace("帕凡", "帕风")] + LABELS_B[1:]
            write_labels(typo)
            c.ok(s.picks == {"fake_ma": [1, 3]}, f"只差 OCR 错字不该清空：{s.picks}")
            c.ok(s.pick_labels.get("fake_ma") == typo, f"快照应换成新写法：{s.pick_labels.get('fake_ma')}")
            c.ok(not any("勾选已清空" in t for t in TOASTS[n11:]), f"只差错字不该提示：{TOASTS[n11:]}")
            write_labels(["深渊-吞噬者 SSS", "黑渊白花 SS", "空之律者 SS"])     # 不播放、文件被外面换成别的一期
            c.ok(s.picks == {"fake_ma": []}, f"外面换了名单也应清空：{s.picks}")
            c.ok(env.state_json().get("picks") == {"fake_ma": []}, "清空要写进 state.json")
            c.ok(sum("勾选已清空" in t for t in TOASTS[n11:]) == 1, f"应提示一次：{TOASTS[n11:]}")
            s.toggle_pick("fake_ma", 2)
            c.ok(s.picks == {"fake_ma": [2]}, f"清空后能重新勾：{s.picks}")
            c.ok(env.Session().pick_labels.get("fake_ma") == ["深渊-吞噬者 SSS", "黑渊白花 SS", "空之律者 SS"], "重开会话没读回快照")
        finally:
            sub_file.write_text(good_txt, encoding="utf-8")           # 放回 LABELS_B，状态直接设回「新名单、勾 1,3」给 T10 用
            s.pick_labels["fake_ma"] = list(LABELS_B)
            s.picks["fake_ma"] = [1, 3]
            s.save_state()
            s.changed.emit()
            env.pump()
        c.ok(s.picks == {"fake_ma": [1, 3]} and env.lyric_texts() == HEAD + LABELS_B, f"T11 收尾应回到新名单、勾 1,3：{s.picks}")

        # ---------------------------------------------------------------- T10 歌单行「约 N 分」按勾选个数估
        c.start("T10 预估时长按勾选个数")
        H = env.history
        bh = None
        bpath = base_dir / "core" / "history.py"
        if first and c.ok(bpath.exists(), "没有基线的 history.py，没法和改之前比"):
            spec = importlib.util.spec_from_file_location("base_history", bpath)
            bh = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bh)
        t0 = time.time()

        def r(tid, dur, result="ok", **kw):
            return {"id": tid, "name": tid, "start": t0 - dur, "end": t0, "dur": dur, "result": result, **kw}
        recs = [                                                # 新的在前
            r("fake_ma", 400, picks=[1, 3]),
            r("fake_ma", 500, picks="坏值"),                    # 读不懂的 picks：不算哪一档，只在退回时算
            r("fake_ma", 600, picks=[1, 2, 3]),
            r("plain_echo", 50),
            r("fake_ma", 100, picks=[]),
            r("fake_ma", 120, "partial", picks=None),         # 没传勾选 = 按 0 个算
            r("fake_ma", 999, "stopped", picks=[1]),          # 手动停止不算完整
            r("fake_ma", 300, picks=[2, 3]),
            r("fake_ma", 800),                                 # 改之前的旧记录：没有 picks 字段，不算哪一档
            r("plain_echo", 70, picks=[1]),
            r("plain_echo", 90, "abnormal"),
            r("fake_ma", 200, picks=[1, 3]),
            r("fake_ma", 220, picks=[2, 3]),
            r("fake_ma", 240, picks=[1, 2]),
            r("fake_ma", 260, picks=[1, 3]),                   # 勾 2 个的第 6 条：超出最近 5 条
            r("fake_ma", 0, picks=[1, 3]),                     # 没有耗时的不算
        ]
        for k, exp in ((2, 272), (3, 600), (0, 110), (1, 344), (4, 344), (None, 344)):
            got = H.avg_duration(recs, "fake_ma", n_picks=k)
            c.ok(got == exp, f"avg_duration(n_picks={k}) 应是 {exp}：{got}")
        c.ok(H.avg_duration(recs, "fake_ma", n=2, n_picks=2) == 350, "n=2 时勾 2 个的应只取最近 2 条")
        c.ok(H.avg_duration(recs, "nobody", n_picks=0) is None, "没有记录的应是 None")
        if bh is not None:
            for tid in ("fake_ma", "plain_echo", "nobody"):
                for n in (5, 2):
                    c.ok(H.avg_duration(recs, tid, n) == bh.avg_duration(recs, tid, n),
                         f"不传 n_picks 时和基线不一样：{tid} n={n}")
            c.ok(H.avg_duration(recs, "plain_echo") == 60, "普通工具应是 (50+70)/2")
        # 接到歌单行上：按现在的勾选个数取
        saved = s.records
        moved = sub_file.with_name("本期BOSS.json.bak")
        row = win._rows["fake_ma"]

        def tip_dur(r):
            """11-01 信息精简后「约 N 分」不在歌单行上，写在第二行的悬停提示里：「常用时长：约 N 分（…）」"""
            m = re.search(r"常用时长：(.+?)（", r.res.toolTip())
            return (m.group(1), True) if m else ("", False)

        def est():
            s.changed.emit()
            env.pump()
            return (s.avg("fake_ma"),) + tip_dur(row)
        try:
            s.records = list(recs)
            c.ok(s.picks == {"fake_ma": [1, 3]}, f"开测前应勾 1,3：{s.picks}")
            c.ok(est() == (272, H.fmt_about(272), True), f"勾 1,3（2 个）：{est()}")
            s.select("fake_ma")
            env.pump()
            c.ok(win.dur_lb.text() == f"常用时长 {H.fmt_about(272)}" and win.dur_lb.isVisible(),
                 f"中栏标题下的常用时长：{win.dur_lb.text()}")
            s.toggle_pick("fake_ma", 2)
            c.ok(est() == (600, H.fmt_about(600), True), f"全勾（3 个）：{est()}")
            for pos in (1, 2, 3):
                s.toggle_pick("fake_ma", pos)
            c.ok(est() == (110, H.fmt_about(110), True), f"全不勾（0 个）：{est()}")
            s.toggle_pick("fake_ma", 1)
            c.ok(est() == (344, H.fmt_about(344), True), f"勾 1 个（没有同档记录，退回全部）：{est()}")
            s.toggle_pick("fake_ma", 3)
            c.ok(s.picks == {"fake_ma": [1, 3]} and est()[0] == 272, "回到勾 1,3")
            sub_file.rename(moved)                 # 没有名单：下次播放不传勾选，按 0 个算
            c.ok(est() == (110, H.fmt_about(110), True), f"没有名单时应按 0 个算：{est()}")
            moved.rename(sub_file)
            c.ok(est()[0] == 272, "名单放回后应回到勾 1,3 的估计")
            prow = win._rows["plain_echo"]
            if bh is not None:
                c.ok(s.avg("plain_echo") == bh.avg_duration(s.records, "plain_echo") == 60,
                     f"普通工具的预估和基线不一样：{s.avg('plain_echo')}")
            c.ok(tip_dur(prow) == (H.fmt_about(60), True), f"普通工具歌单行悬停：{prow.res.toolTip()}")
        finally:
            if moved.exists():
                moved.rename(sub_file)
            s.records = saved
            s.changed.emit()
            env.pump()

        # ---------------------------------------------------------------- T7 截图
        c.start("T7 截图")
        for pth in (SHOT_IDLE, SHOT_PLAY):
            c.ok(pth.exists() and pth.stat().st_size > 5000, f"截图没存成：{pth}")
        c.ok(not WARNS, f"测试中弹过警告框：{WARNS}")
    finally:
        try:
            env.win.devwatch.stop()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        Path("/tmp/_phantasia_full.png").unlink(missing_ok=True)

    print()
    bad = 0
    for name, errs in c.res.items():
        if errs:
            bad += 1
            print(f"{name}：失败")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"{name}：通过")
    print(f"截图：{SHOT_IDLE}  {SHOT_PLAY}")
    print(f"临时目录已删除：{not tmp.exists()}")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--plain-only":
        plain_only(Path(sys.argv[2]).resolve())
        sys.exit(0)
    sys.exit(main())
