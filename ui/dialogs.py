"""弹窗: 新增工具 / 环境自检 / 进程树 / 退出确认 / 提示。弹窗正文不用歌单用语，字段和现版一致。"""
import os
import re
import shlex
import subprocess
import time

from PySide6.QtCore import Signal, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QPlainTextEdit, QScrollArea, QTextBrowser, QVBoxLayout, QWidget)

from core import env, registry

from . import theme as T
from .widgets import AA, C, NoPen, PillButton, StatusTag, TabBar, paint_tick, round_pixmap, sticker


class Dialog(QDialog):
    def __init__(self, parent, title: str, width: int):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(width)
        self.setStyleSheet(f"QDialog{{background:{T.CREAM};}}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(0)

    def footer(self, *buttons):
        row = QHBoxLayout()
        row.setContentsMargins(0, 14, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.body.addLayout(row)


class _Combo(QComboBox):
    """下拉框右端画一个小箭头（样式表里把系统自带的箭头关掉了）。"""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(AA)
        pen = QPen(C(T.INK), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 18, self.height() / 2
        p.drawPolyline(QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)]))


class _Box(QFrame):
    """弹窗里的白色描边框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("box")
        self.setStyleSheet(f"#box{{background:{T.PAPER};border:2px solid {T.INK};border-radius:12px;}}")


# ---------------------------------------------------------------- 新增工具
class AddToolDialog(Dialog):
    FIELDS = [("name", "名称", True, "建议写成「游戏名 · 功能」，前半截会当作专辑名"),
              ("desc", "描述", False, "这个工具会做什么（显示在中列详情里）"),
              ("workdir", "工作目录(相对聚合目录)", False, "例如 tasks/2026-10-05-新脚本"),
              ("cmd", "命令(一行)", True, "例如 python new_script.py"),
              ("py", "Python 解释器(venv 相对路径, 可空)", False, "例如 ../../bh3_auto/.venv/bin/python"),
              ("precheck", "自检文件(逗号分隔)", False, "例如 new_script.py, templates")]

    def __init__(self, parent):
        super().__init__(parent, "新增工具", 520)
        self.tool: dict | None = None
        self.inputs: dict[str, QLineEdit] = {}
        for key, text, must, ph in self.FIELDS:
            lb = QLabel(text + (f" <span style='color:{T.BADTX}'>*</span>" if must else ""))
            lb.setFont(T.font(12, 700))
            lb.setStyleSheet(f"color:{T.INK2};")
            self.body.addWidget(lb)
            self.body.addSpacing(3)
            ed = QLineEdit()
            ed.setFont(T.font(12, mono=True) if key in ("workdir", "cmd", "py", "precheck") else T.font(13))
            ed.setPlaceholderText(ph)
            ed.textChanged.connect(lambda _=None, e=ed: self._unmark(e))
            self.inputs[key] = ed
            self.body.addWidget(ed)
            self.body.addSpacing(9)
        lb = QLabel("分辨率")
        lb.setFont(T.font(12, 700))
        lb.setStyleSheet(f"color:{T.INK2};")
        self.body.addWidget(lb)
        self.body.addSpacing(3)
        self.env = _Combo()
        self.env.setView(QListView())
        self.env.setFont(T.font(13))
        self.env.addItems(["2400x1504(本机基准)", "native(设备分辨率)", "none(不切换)"])
        self.body.addWidget(self.env)
        self.err = T.label("", 12, 700, T.BADTX)
        self.err.setVisible(False)
        self.body.addSpacing(6)
        self.body.addWidget(self.err)
        cancel = PillButton("取消")
        save = PillButton("保存", "pri")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        self.footer(cancel, save)
        self.inputs["name"].setFocus()

    def _unmark(self, ed: QLineEdit):
        if ed.property("bad"):
            ed.setProperty("bad", False)
            ed.style().unpolish(ed)
            ed.style().polish(ed)

    def _save(self):
        name = self.inputs["name"].text().strip()
        cmd = self.inputs["cmd"].text().strip()
        if not name or not cmd:
            for key, val in (("name", name), ("cmd", cmd)):
                ed = self.inputs[key]
                ed.setProperty("bad", not val)
                ed.style().unpolish(ed)
                ed.style().polish(ed)
            self.err.setText("名称和命令不能为空")
            self.err.setVisible(True)
            return
        try:
            command = shlex.split(cmd)
        except ValueError:
            command = cmd.split()
        mode = self.env.currentText().split("(")[0]
        self.tool = {
            "id": "tool_" + time.strftime("%m%d%H%M%S"),
            "name": name,
            "description": self.inputs["desc"].text().strip(),
            "env": mode,
            "workdir": self.inputs["workdir"].text().strip(),
            "command": command,
            "python": self.inputs["py"].text().strip() or None,
            "precheck": [x.strip() for x in self.inputs["precheck"].text().split(",") if x.strip()],
            "need_device": mode != "none",
            "tasks": [],
        }
        self.accept()


# ---------------------------------------------------------------- 环境自检
class _CheckIcon(QWidget):
    def __init__(self, ok: bool, parent=None):
        super().__init__(parent)
        self._ok = ok
        self.setFixedSize(16, 20)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(0, 2, 16, 16)
        sticker(p, r, 8, T.OK if self._ok else T.BAD, bw=1.5)
        if self._ok:
            paint_tick(p, 8, 10, 8, "#FFFFFF", 2)
        else:
            p.setPen(NoPen)
            p.setBrush(C("#FFFFFF"))
            p.drawRoundedRect(QRectF(7, 5.5, 2, 6), 1, 1)
            p.drawRoundedRect(QRectF(7, 12.5, 2, 2), 1, 1)


class SelfCheckDialog(Dialog):
    """通过的行打绿勾，有问题的行标红并写明缺什么。检查在后台线程里做，不卡界面。
    11-01 设计三：底部放两个分辨率按钮（原来在右栏设备卡上）；查完把结果发给主窗口（「环境自检」按钮红不红）。"""
    done_check = Signal(object)

    def __init__(self, parent, session):
        super().__init__(parent, "环境自检", 560)
        self.summary = QHBoxLayout()
        self.summary.setSpacing(8)
        self.summary.setContentsMargins(0, 0, 0, 10)
        self.wait = T.label("检查中…", 13, 400, T.INK2)
        self.summary.addWidget(self.wait)
        self.summary.addStretch(1)
        self.body.addLayout(self.summary)
        box = _Box()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 4, 8)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.rows = QVBoxLayout(inner)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(2)
        self.rows.addStretch(1)
        area.setWidget(inner)
        area.viewport().setAutoFillBackground(False)
        inner.setAutoFillBackground(False)
        area.setMinimumHeight(300)
        bl.addWidget(area)
        self.body.addWidget(box)
        b_res = PillButton("设为 2400x1504", tip="脚本按 2400x1504（16:10）写的；设备比例不一样时切过去（退出时会自动还原）")
        b_nat = PillButton("恢复设备分辨率", tip="改回设备原本的分辨率")
        b_res.clicked.connect(lambda: parent.set_res(env.RES_BASE))
        b_nat.clicked.connect(lambda: parent.set_res(env.RES_NATIVE))
        close = PillButton("关闭")
        close.clicked.connect(self.accept)
        self.footer(b_res, b_nat, close)
        tools = list(session.tools)
        session.bg(lambda: env.check_environment(tools), self._fill)

    def _fill(self, results):
        self.done_check.emit(results)
        if isinstance(results, Exception):
            self.wait.setText(f"自检没跑完: {results}")
            return
        try:
            self.wait.setVisible(False)
        except RuntimeError:   # 弹窗已经关了
            return
        good = sum(1 for ok, _ in results if ok)
        bad = len(results) - good
        self.summary.insertWidget(0, StatusTag("ok", f"{good} 项通过"))
        if bad:
            self.summary.insertWidget(1, StatusTag("bad", f"{bad} 项有问题"))
        for i, (ok, text) in enumerate(results):
            sub = text.startswith("  ")
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(24 if sub else 0, 0, 0, 0)
            rl.setSpacing(8)
            rl.addWidget(_CheckIcon(ok), 0, Qt.AlignmentFlag.AlignTop)
            color = T.BADTX if not ok else (T.INK2 if sub else "#2E3446")
            lb = T.label(text.strip(), 12, 400 if ok else 700, color, mono=True, wrap=True)
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rl.addWidget(lb, 1)
            self.rows.insertWidget(i, row)


# ---------------------------------------------------------------- 进程树
def proc_tree_text() -> str:
    """只列出幻想引擎自己和它拉起的进程。"""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,etime=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return f"无法读取进程: {e}"
    rows, kids = {}, {}
    for ln in out.splitlines():
        parts = ln.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        rows[pid] = (ppid, parts[2], parts[3])
        kids.setdefault(ppid, []).append(pid)
    me = os.getpid()
    if me not in rows:
        return "(无相关进程)"
    base = str(registry.AGG_DIR) + "/"
    lines = [f"{'PID':<7}{'PPID':<7}{'ELAPSED':<12}COMMAND"]

    def walk(pid: int, depth: int):
        ppid, et, cmd = rows[pid]
        if cmd.startswith("ps -axo"):
            return
        cmd = re.sub(r"\S*/Python\.app/Contents/MacOS/Python", "python", cmd.replace(base, ""))
        lines.append(f"{pid:<7}{ppid:<7}{et:<12}{'  ' * depth}{cmd}")
        for k in sorted(kids.get(pid, [])):
            walk(k, depth + 1)
    walk(me, 0)
    return "\n".join(lines) if len(lines) > 1 else "(无相关进程)"


class ProcTreeDialog(Dialog):
    def __init__(self, parent):
        super().__init__(parent, "进程树", 720)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(T.font(12, mono=True))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setMinimumHeight(300)
        self.text.setPlainText(proc_tree_text())
        self.body.addWidget(self.text)
        self.body.addSpacing(8)
        self.body.addWidget(T.label("只列出幻想引擎自己和它拉起的工具进程；没有相关进程时显示「(无相关进程)」。",
                                    12, 400, T.INK3, wrap=True))
        close = PillButton("关闭")
        close.clicked.connect(self.accept)
        self.footer(close)


# ---------------------------------------------------------------- 关于
DOCS = registry.AGG_DIR / "docs"
ABOUT_TABS = [("guide", "使用教程", "使用教程.md"), ("log", "更新日志", "更新日志.md")]
_ABOUT_CSS = f"""
p.h2 {{ font-size:15px; font-weight:800; color:{T.INK}; margin-top:14px; margin-bottom:4px; }}
p.h3 {{ font-size:13px; font-weight:800; color:{T.PINKTX}; margin-top:10px; margin-bottom:2px; }}
p, li {{ font-size:13px; color:{T.INK}; }}
li {{ margin-bottom:3px; }}
code {{ font-family:Menlo; font-size:12px; color:{T.LAVTX}; }}
"""


def _inline(text: str) -> str:
    import html as _html
    t = _html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


def md_to_html(md: str) -> str:
    """只认这几样: ## / ### 标题、「- 」「1. 」列表、空行分段、**粗体**、`代码`。够教程和更新日志用。"""
    out, mode = [], ""

    def close():
        nonlocal mode
        if mode:
            out.append(f"</{mode}>")
            mode = ""
    for raw in md.splitlines():
        line = raw.rstrip()
        m_ol = re.match(r"^\d+[.、]\s+(.*)$", line)
        if line.startswith("# "):
            continue                      # 一级标题就是页签名，不重复显示
        if line.startswith("### "):
            close()
            out.append(f'<p class="h3">{_inline(line[4:])}</p>')
        elif line.startswith("## "):
            close()
            out.append(f'<p class="h2">{_inline(line[3:])}</p>')
        elif line.startswith("- "):
            if mode != "ul":
                close()
                out.append("<ul>")
                mode = "ul"
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif m_ol:
            if mode != "ol":
                close()
                out.append("<ol>")
                mode = "ol"
            out.append(f"<li>{_inline(m_ol.group(1))}</li>")
        elif not line.strip():
            close()
        else:
            close()
            out.append(f"<p>{_inline(line)}</p>")
    close()
    return f"<style>{_ABOUT_CSS}</style>" + "\n".join(out)


class AboutDialog(Dialog):
    """关于: 版本 + 使用教程 + 更新日志。正文在 docs/ 下的两个 .md 文件里，改文件就能改内容。"""

    def __init__(self, parent, logo: str, app_name: str, version: str):
        super().__init__(parent, "关于", 680)
        head = QHBoxLayout()
        head.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(52, 52)
        pic.setPixmap(round_pixmap(logo, 52, self.devicePixelRatioF()))
        head.addWidget(pic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(T.label(f"{app_name} v{version}", 17, 800))
        col.addWidget(T.label("挂机聚合助手：把设备上的游戏日常脚本收在一起，一键播放、排队连播、记下每次的结果", 13, 400, T.INK2, wrap=True))
        col.addWidget(T.label("封面人物：凌雨澜（作者原创小说里的角色）", 12, 400, T.INK3))
        head.addLayout(col, 1)
        self.body.addLayout(head)
        self.body.addSpacing(12)
        self.tabs = TabBar([(k, t) for k, t, _ in ABOUT_TABS])
        self.tabs.changed.connect(self._show)
        self.body.addWidget(self.tabs)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setFont(T.font(13))
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet(f"QTextBrowser{{background:#FFFFFF;border:2px solid {T.INK};border-top:none;"
                                f"border-bottom-left-radius:12px;border-bottom-right-radius:12px;padding:4px 10px;}}")
        self.view.setMinimumHeight(400)
        self.body.addWidget(self.view)
        proc = PillButton("进程树", tip="看幻想引擎自己和它启动的工具进程（排查用；11-01 从顶栏挪来）")
        proc.clicked.connect(lambda: ProcTreeDialog(self).exec())
        close = PillButton("关闭")
        close.clicked.connect(self.accept)
        self.footer(proc, close)
        self._show("guide")

    def _show(self, key: str):
        name = next(f for k, _, f in ABOUT_TABS if k == key)
        try:
            md = (DOCS / name).read_text(encoding="utf-8")
        except Exception:
            md = f"（找不到 docs/{name}）"
        self.view.setHtml(md_to_html(md))
        self.view.verticalScrollBar().setValue(0)


# ---------------------------------------------------------------- 退出确认 / 提示
class _Ask(Dialog):
    def __init__(self, parent, title: str, logo: str, head: str, lines: list[str], width: int = 400):
        super().__init__(parent, title, width)
        row = QHBoxLayout()
        row.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(52, 52)
        pic.setPixmap(round_pixmap(logo, 52, self.devicePixelRatioF()))
        row.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(T.label(head, 14, 800, wrap=True))
        for i, ln in enumerate(lines):
            col.addWidget(T.label(ln, 13, 400, T.INK if i == 0 else T.INK2, wrap=True))
        col.addStretch(1)
        row.addLayout(col, 1)
        self.body.addLayout(row)


def confirm_quit(parent, session, logo: str) -> bool:
    lines = ["退出将停止全部工具并还原手机分辨率, 确认退出?"]
    if session.cur:
        lines.append(f"正在播放：{session.cur.name}")
    if session.queue:
        lines.append(f"还有 {len(session.queue)} 首待播")
    d = _Ask(parent, "幻想引擎", logo, "要退出吗？", lines)
    cancel = PillButton("取消")
    ok = PillButton("退出并停止全部", "dan")
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    d.footer(cancel, ok)
    return d.exec() == QDialog.DialogCode.Accepted


def confirm_end(parent, logo: str, name: str) -> bool:
    """11-02 作者：「结束这首加确认框」——没有待播时播放区的「下一首」变成「结束这首」，点了先问一句（会直接停掉脚本）。"""
    d = _Ask(parent, "幻想引擎", logo, "结束这首？",
             [f"正在播放：{name}", "脚本会被直接停掉，游戏停在当前画面；只想停一会儿请点暂停。"])
    cancel = PillButton("取消")
    ok = PillButton("结束这首", "dan")
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    d.footer(cancel, ok)
    return d.exec() == QDialog.DialogCode.Accepted


def resolution_dialog(parent, logo: str, cur: str, base: str) -> Dialog:
    """11-01 设计三：启动自检发现设备比例和脚本基准不一样时问一句（拆出来是为了离屏能画）。"""
    lines = [f"设备现在是 {cur}，脚本是按 {base}（16:10）写的，比例不一样时点的位置会偏。",
             f"切到 {base} 只改屏幕显示比例，不动设备里的东西；退出时会自动改回来。",
             "以后想手动切换或改回，在「环境自检」里。"]
    d = _Ask(parent, "幻想引擎", logo, "设备比例和脚本不一样，要切换分辨率吗？", lines, 440)
    no = PillButton("先不用")
    ok = PillButton(f"切换到 {base}", "pri")
    no.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    d.footer(no, ok)
    return d


def confirm_resolution(parent, logo: str, cur: str, base: str) -> bool:
    return resolution_dialog(parent, logo, cur, base).exec() == QDialog.DialogCode.Accepted


def warn(parent, logo: str, head: str, text: str):
    d = _Ask(parent, "幻想引擎", logo, head, [text], 420)
    ok = PillButton("知道了", "pri")
    ok.clicked.connect(d.accept)
    d.footer(ok)
    d.exec()


class ShotDialog(Dialog):
    """看结束截图的大图；「验收」= 删掉这张图（播放记录保留）。"""

    def __init__(self, parent, path: str, title: str, caption: str):
        super().__init__(parent, title, 520)
        self.accepted_shot = False
        self.body.addWidget(T.label(caption, 13, 600, T.INK2, wrap=True))
        img = QImage(path)
        pic = QLabel()
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if img.isNull():
            pic.setText("这张截图已经不在了")
        else:
            screen = self.screen().availableGeometry() if self.screen() else None
            max_h = int((screen.height() if screen else 900) * 0.72)
            pm = QPixmap.fromImage(img)
            pic.setPixmap(pm.scaled(480, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
        self.body.addWidget(pic)
        close = PillButton("关闭")
        ok = PillButton("验收（删掉这张图）", "pri")
        close.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        self.footer(close, ok)

    def _ok(self):
        self.accepted_shot = True
        self.accept()


def confirm_seq(parent, logo: str, names: list[str]) -> bool:
    lines = ["开启后，幻想引擎会在后台自动按顺序运行勾选的工具，不需要你看着。",
             "每个工具开始前和结束后，都会关掉它对应的游戏（杀进程），免得几个游戏同时开着把内存占满；"
             "做完的结果看播放记录里的结束截图。",
             "关掉之后，设备的后台应用列表里可能还留着这些游戏的卡片，那只是系统缓存的画面，进程已经结束，不影响。",
             "需要中途手动操作的工具，不建议加入顺序播放。",
             "这一轮：" + " → ".join(names)]
    d = _Ask(parent, "幻想引擎", logo, "开启顺序播放？", lines, 460)
    cancel = PillButton("取消")
    ok = PillButton("开始顺序播放", "pri")
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    d.footer(cancel, ok)
    return d.exec() == QDialog.DialogCode.Accepted
