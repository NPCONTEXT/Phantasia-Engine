"""主窗口: 顶栏 / 左「歌单」/ 中「歌曲详情」(歌词·运行日志·播放记录) / 右 设备与环境+监控 / 底「正在播放」栏"""
import html
import re
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from core import env, history, registry, runner

from . import dialogs
from . import theme as T
from .device import DeviceWatcher, MonitorFeed, notify
from .popups import EventBubble, QueuePopup
from .roles import RoleBar, RolePanel
from .session import is_limited, role_cfg, role_short
from .widgets import (AA, Anim, C, Card, Disc, ElidedLabel, EqIcon, GridBg, LinkToggle, LogView, MiniPlay,
                      MonitorView, OptSwitch, PillButton, RoundCtl, SegProgress, SeqBadge,
                      StatusTag, Switch, TabBar, TickIcon, badge, notice_badge, num_pill,
                      round_pixmap, sticker, wait_tag, check_tag, MIME_TOOL, drag_tid, start_drag)

ENV_BADGE = {"2400x1504": "环境: 2400x1504", "native": "环境: 设备分辨率",
             "none": ""}      # 原生分辨率（不改动）是默认，不挂标签（11-01 信息精简）


def row_when(ts: float, now: float | None = None) -> str:
    """歌单行用的短时间（11-01 信息精简）：今天 19:16 → 19:16；昨天 19:16；更早 10/29；不是今年 2025/10/29。"""
    full = history.fmt_when(ts, now)
    if full.startswith("今天 "):
        return full[3:]
    if full.startswith(("昨天 ", "前天 ")):
        return full
    t = time.localtime(ts)
    n = time.localtime(time.time() if now is None else now)
    return f"{t.tm_mon}/{t.tm_mday}" if t.tm_year == n.tm_year else f"{t.tm_year}/{t.tm_mon}/{t.tm_mday}"
RESULT_TAG = {history.OK: "ok", history.DONE: "ok", history.PARTIAL: "bad", history.ABNORMAL: "bad",
              history.UNMET: "bad", history.STOPPED: "stop"}
ALBUM_COLORS = (T.PINK, T.CYAN, T.LAV, T.GOLD, T.OK, T.BLUSH)


def _scroll(inner: QWidget) -> QScrollArea:
    a = QScrollArea()
    a.setWidgetResizable(True)
    a.setFrameShape(QFrame.Shape.NoFrame)
    a.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    a.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    a.setWidget(inner)
    a.viewport().setAutoFillBackground(False)
    inner.setAutoFillBackground(False)
    return a


def _clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


# ================================================================ 歌单的一行
class ToolRow(QWidget):
    picked = Signal(str)
    check = Signal(str)
    limit = Signal(str)        # 右键「移进 / 移回限时歌单」
    review = Signal(str)       # 11-03 点「待验收 ›」：跳到这首播放记录里没验收的那条

    def __init__(self, tool: dict, title: str | None = None, parent=None):
        super().__init__(parent)
        self.tid = tool["id"]
        self._limited = is_limited(tool)
        self._sel = self._now = False
        self._press = None               # 11-02 按下的位置：移动超过拖动距离就拖起来（歌单里排序 / 拖到封面加待播）
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(8)
        self.badge = SeqBadge()
        self.badge.clicked.connect(lambda: self.check.emit(self.tid))
        lay.addWidget(self.badge)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        self.name = T.label(title or tool.get("name", "?"), 14, 700, wrap=True)
        self.name.setToolTip(tool.get("name", ""))
        col.addStretch(1)
        col.addWidget(self.name)
        self.sub = QWidget()
        sl = QHBoxLayout(self.sub)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        # 11-01 信息精简：第二行只写「短时间 + 结果」，角色和「约 N 分」挪进悬停提示；
        # 字能折行，再长也不会把整列撑宽（以前撑宽后右边的 ▶ 被挤出可视区）。
        # 11-02：这行字占满剩下的宽度、真放不下才折行——以前旁边有空白弹簧，字被挤窄，「1 句未完成」的「成」单独掉一行
        self.res = T.label("", 12, 400, T.INK3, wrap=True)
        self.wtag = wait_tag()
        self.ctag = check_tag()
        self.ctag.clicked.connect(lambda: self.review.emit(self.tid))
        sl.addWidget(self.res, 1)
        sl.addWidget(self.wtag)
        sl.addWidget(self.ctag)
        sl.addStretch(0)
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        # 11-02 作者试用后：「左侧歌单不做启动和停止按钮，而是改成选中对应任务，任务直接映射到右侧的播放栏，
        # 只能在播放栏启动和暂停，这样也降低了交互复杂度，统一入口」（以前行尾的停止钮是真杀进程，和暂停不一样，试用时误点过）
        self.setMinimumHeight(48)

    def refresh(self, rs: dict, selected: bool, last: dict | None, avg: float | None):
        state = rs["state"]
        self.badge.set(*rs["badge"])
        self.badge.setToolTip({"box": "打勾 = 加入顺序播放（正在播放时打勾 = 排进待播）", "tick": "已勾选，下次顺序播放时会播",
                               "num": "再点一下取消", "eq": "正在播放",
                               "fin": "这一轮已播放完", "err": "这一轮播过了，但没全部完成"}[rs["badge"][0]])
        show_sub = False
        self.wtag.setVisible(state == "wait")
        self.ctag.setVisible(bool(rs.get("check")))
        if state == "wait":
            self.res.setVisible(False)
            show_sub = True
        elif state == "idle" and last:
            r = last.get("result")
            bad = r in history.BAD
            short = "完成" if r in (history.OK, history.DONE) else history.result_text(last)
            self.res.setText(f"{row_when(last['start'])} " + ("· " if r == history.PARTIAL else "") + short)
            self.res.setFont(T.font(12, 600 if bad else 400))
            self.res.setStyleSheet(f"color:{T.BADTX if bad else T.INK3};background:transparent;")
            tip = f"上次：{history.fmt_when(last['start'])} {history.result_text(last)}"
            if last.get("role_label"):           # 带角色的歌：上次用的谁（只在悬停里写）
                tip += f" · {role_short({'label': last['role_label']})}"
            if avg:
                tip += f"\n常用时长：{history.fmt_about(avg)}（最近几次完整播放的平均）"
            self.res.setToolTip(tip)
            self.res.setVisible(True)
            show_sub = True
        else:
            self.res.setVisible(False)
        self.sub.setVisible(show_sub)
        now = state == "now"
        if (selected, now) != (self._sel, self._now):
            self._sel, self._now = selected, now
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self.picked.emit(self.tid)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        start_drag(self, MIME_TOOL, self.tid)

    def mouseReleaseEvent(self, e):
        self._press = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        act = menu.addAction("移回歌单" if self._limited else "移进限时歌单")
        if menu.exec(e.globalPos()) is act:
            self.limit.emit(self.tid)

    def paintEvent(self, _):
        if not (self._sel or self._now):
            return
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(0, 0, self.width(), self.height() - 2)
        if self._sel:
            sticker(p, r, 14, T.PINKBG if self._now else "#FFFFFF", shadow=2)
        else:
            sticker(p, r, 14, T.PINKBG, T.PINK)


class _RowsBox(QWidget):
    """11-02 歌单里拖动排序（作者：「把上下调整清单改成鼠标拖动」，代替上移 / 下移按钮）：
    放在哪一行的上半 / 下半，就挪到它前面 / 后面；只在同一张专辑里挪（跨专辑会提示）。拖动只改顺序，不碰正在播的。"""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._ind_y: float | None = None
        self.setAcceptDrops(True)

    def target(self, y: float):
        rows = [r for r in self.win._rows.values() if r.isVisible()]
        if not rows:
            return None, False
        for r in rows:
            g = r.geometry()
            if g.top() - 2 <= y <= g.bottom() + 2:
                return r, y > g.center().y()
        r = min(rows, key=lambda r: abs(r.geometry().center().y() - y))      # 专辑标题、空白处：算最近那一行
        return r, y > r.geometry().center().y()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_TOOL):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(MIME_TOOL):
            return
        e.acceptProposedAction()
        y = e.position().y()
        r, after = self.target(y)
        self._ind_y = None if r is None else float(r.geometry().bottom() + 2 if after else r.geometry().top() - 2)
        self.update()
        self.win._autoscroll(y)

    def dragLeaveEvent(self, e):
        self._ind_y = None
        self.update()

    def dropEvent(self, e):
        tid = drag_tid(e, MIME_TOOL)
        r, after = self.target(e.position().y())
        self._ind_y = None
        self.update()
        if tid and r is not None and r.tid != tid:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.win.s.move_to(tid, r.tid, after))   # 拖动结束后再改（会重建歌单行）

    def paintEvent(self, _):
        if self._ind_y is None:
            return
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setPen(QPen(C(T.PINKD), 3))
        p.drawLine(QPointF(10, self._ind_y), QPointF(self.width() - 10, self._ind_y))


class AlbumHeader(QWidget):
    """专辑(游戏)的标题行: 小封面色块 + 游戏名 + 几首 + 一条细线。"""

    def __init__(self, name: str, count: int, index: int, parent=None):
        super().__init__(parent)
        self._name, self._count = name, count
        self._color = ALBUM_COLORS[index % len(ALBUM_COLORS)]
        self.setFixedHeight(30 if index else 24)
        self.setToolTip(f"专辑 = 游戏：{name}，{count} 首")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        cy = self.height() - 12.0
        sticker(p, QRectF(8, cy - 8, 16, 16), 5, self._color)
        p.setPen(C(T.INK))
        f = T.font(13, 800)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(self._name)
        p.drawText(QRectF(32, cy - 12, tw + 4, 24), Qt.AlignmentFlag.AlignVCenter, self._name)
        f2 = T.font(12)
        p.setFont(f2)
        p.setPen(C(T.INK3))
        cnt = f"{self._count} 首"
        cw = p.fontMetrics().horizontalAdvance(cnt)
        p.drawText(QRectF(32 + tw + 8, cy - 12, cw + 4, 24), Qt.AlignmentFlag.AlignVCenter, cnt)
        x0 = 32 + tw + 8 + cw + 10
        if x0 < self.width() - 8:
            p.fillRect(QRectF(x0, cy - 0.75, self.width() - 6 - x0, 1.5), C(T.LINE))


class LimitedHeader(QWidget):
    """歌单最底下的「限时歌单 · N 首」折叠条：上面一条虚线，点一下展开 / 收起。"""
    toggled = Signal()

    def __init__(self, count: int, opened: bool, parent=None):
        super().__init__(parent)
        self._count, self._open = count, opened
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("限时活动的歌放在这里，平时收起；选中一首后在中间详情里点「移回歌单」，或右键歌单行")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        pen = QPen(C("#C9D1E0"), 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(8, 6, self.width() - 8, 6)
        cy = 25.0
        # 小沙漏：两个三角
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C(T.GOLD))
        x = 12.0
        p.drawPolygon(QPolygonF([QPointF(x, cy - 8), QPointF(x + 12, cy - 8), QPointF(x + 6, cy)]))
        p.drawPolygon(QPolygonF([QPointF(x + 6, cy), QPointF(x, cy + 8), QPointF(x + 12, cy + 8)]))
        p.setPen(C(T.INK2))
        p.setFont(T.font(13, 800))
        txt = f"限时歌单 · {self._count} 首"
        tw = p.fontMetrics().horizontalAdvance(txt)
        p.drawText(QRectF(32, cy - 12, tw + 4, 24), Qt.AlignmentFlag.AlignVCenter, txt)
        pen = QPen(C(T.INK2), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        ax = self.width() - 26.0
        if self._open:
            p.drawPolyline(QPolygonF([QPointF(ax - 5, cy - 2), QPointF(ax, cy + 3), QPointF(ax + 5, cy - 2)]))
        else:
            p.drawPolyline(QPolygonF([QPointF(ax - 2, cy - 5), QPointF(ax + 3, cy), QPointF(ax - 2, cy + 5)]))


# ================================================================ 歌词的一行
class LyricRow(QWidget):
    picked = Signal()      # 点了子项句前面的勾选框

    def __init__(self, no: int, text: str, parent=None, *, pick: bool | None = None, locked: bool = False):
        """pick 不是 None = 子项句（例如本期的一个 BOSS）: 前面带勾选框，没勾的字灰显；
        locked = 这首正在播放 / 正要启动，勾选框半透明、点了不改。"""
        super().__init__(parent)
        self._state = ""
        self._muted = pick is False
        lay = QHBoxLayout(self)
        lay.setSpacing(10)
        self.no = T.label(f"{no:02d}", 12, 700, T.INK3, mono=True)
        self.no.setFixedWidth(22)
        self.nm = T.label(text, 13, 400, wrap=True)
        self.tick = TickIcon()
        self.eq = EqIcon()
        self.box = None
        lay.addWidget(self.no, 0, Qt.AlignmentFlag.AlignTop)
        if pick is not None:
            self.box = SeqBadge()
            self.box.set("tick" if pick else "box")
            self.box.set_dim(locked)
            self.box.setToolTip("正在播放：这次的勾选已经定了，下次播放才生效" if locked else
                                ("勾上了：播放时做这一项（点一下取消）" if pick else "没勾：播放时跳过这一项（点一下勾上）"))
            self.box.clicked.connect(self.picked)
            lay.setAlignment(self.no, Qt.AlignmentFlag.AlignVCenter)     # 勾选框比字高，序号跟着居中
            lay.addWidget(self.box, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.nm, 1)
        lay.addWidget(self.tick)
        lay.addWidget(self.eq)
        self.set_state("todo")

    def set_state(self, s: str):
        """todo 还没到 / done 已完成(变淡带勾) / now 正在播放(放大加粉底)"""
        if s == self._state:
            return
        self._state = s
        now = s == "now"
        self.layout().setContentsMargins(10, 13 if now else 5, 10, 13 if now else 5)
        self.nm.setFont(T.font(15 if now else 13, 800 if now else 400))
        color = T.MUTED if self._muted else ("#6B7388" if s == "done" else T.INK)   # 没勾的子项一直灰显
        self.nm.setStyleSheet(f"color:{color};background:transparent;")
        self.no.setFont(T.font(13 if now else 12, 700, mono=True))
        self.no.setStyleSheet(f"color:{T.PINKTX if now else T.INK3};background:transparent;")
        self.no.setContentsMargins(0, 2 if now else 1, 0, 0)
        self.tick.setVisible(s == "done")
        self.eq.setVisible(now)
        self.update()

    def paintEvent(self, _):
        if self._state != "now":
            return
        p = QPainter(self)
        p.setRenderHint(AA)
        sticker(p, QRectF(0, 5, self.width(), self.height() - 10), 10, T.PINKBG, T.PINKD, 1.5)


class EmptyBox(QWidget):
    """空状态: 虚线框 + 两行字。"""

    def __init__(self, title: str = "", sub: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)
        self.t = T.label(title, 14, 700, T.INK2)
        self.s = T.label(sub, 13, 400, T.INK3)
        for lb in (self.t, self.s):
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(self.t)
        lay.addWidget(self.s)
        lay.addStretch(1)

    def set(self, title: str, sub: str):
        self.t.setText(title)
        self.s.setText(sub)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        p.setBrush(C(T.PAPER))
        p.setPen(QPen(C("#C9D1E0"), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)


class RecCard(QFrame):
    """播放记录的一条: 结果贴纸 + 开始时间 + 耗时；有没完成的句子就直接写在下面；有结束截图就带缩略图和「验收」。
    看过了（点「知道了」或截图已验收）的记录，红字收成一行灰字。"""
    view_log = Signal(dict)
    view_shot = Signal(dict)
    accept_shot = Signal(dict)
    mark_seen = Signal(dict)

    def __init__(self, rec: dict, with_name: bool, seen: bool = False, parent=None):
        super().__init__(parent)
        outcome = str(rec.get("outcome") or "").strip()
        red = bool(rec.get("missed")) or bool(outcome and rec.get("result") == history.UNMET)
        self.rec = rec
        self.setObjectName("rec")
        self.setStyleSheet(f"#rec{{background:#FFFFFF;border:1.5px solid {T.LINE};border-radius:12px;}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(StatusTag(RESULT_TAG.get(rec.get("result"), "bad"), history.result_text(rec), 96))
        when = f"{history.fmt_when(rec.get('start', 0))} · 耗时 {history.fmt_dur(rec.get('dur', 0))}"
        col = QVBoxLayout()
        col.setSpacing(0)
        if with_name:
            col.addWidget(T.label(rec.get("name", "?"), 13, 700, wrap=True))
        col.addWidget(T.label(when, 13, 400, T.INK2, wrap=True))     # 能折行：再长也不把卡片撑出可视区
        row.addLayout(col, 1)
        b = PillButton("看日志", small=True)
        b.clicked.connect(lambda: self.view_log.emit(rec))
        row.addWidget(b)
        if red and not seen:
            b0 = PillButton("知道了", small=True)
            b0.setToolTip("看过了：下面的红字收成一行灰字（播放记录保留）")
            b0.clicked.connect(lambda: self.mark_seen.emit(rec))
            row.addWidget(b0)
        v.addLayout(row)
        if rec.get("role_label"):                # 带角色的歌：这次用的谁（11-01 从标题行挪下来）
            v.addWidget(T.label(f"角色 {rec['role_label']}", 12, 400, T.INK3, wrap=True))
        facts = [str(x) for x in rec.get("facts") or []]
        if facts:                                # 11-01 信息精简：事实行不平铺，放进悬停
            self.setToolTip("  ｜  ".join(facts))
        shot = runner.SHOTS_DIR / str(rec.get("shot") or "") if rec.get("shot") else None
        if shot is not None and shot.is_file():
            srow = QHBoxLayout()
            srow.setSpacing(10)
            thumb = QLabel()
            pm = QPixmap(str(shot))
            if not pm.isNull():
                thumb.setPixmap(pm.scaledToHeight(96, Qt.TransformationMode.SmoothTransformation))
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.setToolTip("结束截图：点一下看大图")
            thumb.mousePressEvent = lambda e: self.view_shot.emit(rec)
            srow.addWidget(thumb)
            scol = QVBoxLayout()
            scol.setSpacing(6)
            scol.addWidget(T.label("结束截图（还没验收）", 12, 700, T.GOLDTX))
            b2 = PillButton("看大图", small=True)
            b2.clicked.connect(lambda: self.view_shot.emit(rec))
            b3 = PillButton("验收", "pri", small=True)
            b3.setToolTip("看过了：删掉这张截图（播放记录保留）")
            b3.clicked.connect(lambda: self.accept_shot.emit(rec))
            brow = QHBoxLayout()
            brow.setSpacing(6)
            brow.addWidget(b2)
            brow.addWidget(b3)
            brow.addStretch(1)
            scol.addLayout(brow)
            scol.addStretch(1)
            srow.addLayout(scol, 1)
            v.addLayout(srow)
        if red and seen:
            parts = [outcome] if (outcome and rec.get("result") == history.UNMET) else []
            for m in rec.get("missed") or []:
                head = (f"{m['no']:02d} " if m.get("no") else "") + str(m.get("name", ""))
                parts.append(f"{head}：{m.get('reason', '')}")
            v.addWidget(T.label("；".join(parts), 12, 400, T.MUTED, wrap=True))     # 灰字本身就表示看过了
            if outcome and rec.get("result") != history.UNMET:
                v.addWidget(T.label(outcome, 13, 700, T.OKTX, wrap=True))
            return
        if outcome and rec.get("result") == history.UNMET:
            box = QFrame()
            box.setObjectName("miss")
            box.setStyleSheet(f"#miss{{background:{T.BADBG};border-radius:9px;}}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 6, 10, 6)
            bl.addWidget(T.label(outcome, 13, 800, T.BADTX, wrap=True))
            v.addWidget(box)
        elif outcome:
            v.addWidget(T.label(outcome, 13, 700, T.OKTX, wrap=True))
        for m in rec.get("missed") or []:
            box = QFrame()
            box.setObjectName("miss")
            box.setStyleSheet(f"#miss{{background:{T.BADBG};border-radius:9px;}}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 6, 10, 6)
            bl.setSpacing(0)
            head = (f"{m['no']:02d} " if m.get("no") else "") + str(m.get("name", ""))
            bl.addWidget(T.label(head, 13, 400, T.BADTX, wrap=True))
            bl.addWidget(T.label(str(m.get("reason", "")), 13, 800, T.BADTX, wrap=True))
            v.addWidget(box)

    def flash(self, ms: int = 2000):
        """11-03 版本二：从歌单「待验收 ›」跳过来时描一圈金框，ms 毫秒后撤掉。
        金框是盖在卡片上的一层（点得穿），不改卡片自己的边框，里面的字不挪位置；挂在卡片上，卡片先被清掉也跟着没。"""
        ov = QFrame(self)
        ov.setObjectName("recflash")
        ov.setStyleSheet(f"#recflash{{background:transparent;border:3px solid {T.GOLD};border-radius:12px;}}")
        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ov.setGeometry(self.rect())
        ov.show()
        ov.raise_()
        QTimer.singleShot(ms, ov, ov.deleteLater)


def _short(name) -> str:
    """气泡里的句名只说到冒号、括号前（雨澜话少；全名在播放记录里）。"""
    s = str(name)
    for ch in "：:（(":
        i = s.find(ch)
        if i > 0:
            s = s[:i]
    return s.strip() or str(name)


def _stop(text: str) -> str:
    """补上句号（已经有标点收尾就不补）。"""
    t = str(text).strip()
    return t if not t or t[-1] in "。！？.!?…" else t + "。"


class LogoDot(QWidget):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path, self._pix = path, None
        self.setFixedSize(28, 28)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(AA | QPainter.RenderHint.SmoothPixmapTransform)
        sticker(p, QRectF(0, 0, 28, 28), 14, "#FFFFFF")
        dpr = self.devicePixelRatioF()
        if self._pix is None or abs(self._pix.devicePixelRatio() - dpr) > 0.01:
            self._pix = round_pixmap(self._path, 24, dpr)
        p.drawPixmap(QPoint(2, 2), self._pix)


# ================================================================ 主窗口
class MainWindow(QMainWindow):
    def __init__(self, session, version: str, on_quit=None):
        super().__init__()
        self.s = session
        self.version = version
        self._on_quit = on_quit or (lambda: None)
        self._quitting = False
        self._rows: dict[str, ToolRow] = {}
        self._lyrics: list[LyricRow] = []
        self._detail_id: str | None = None      # 中列现在画的是哪个工具
        self._lyric_cur: int | None = None
        self._lyric_sig = None                   # 歌词按这个签名重建（工具 / 句子 / 子项勾选 / 是否在播）
        self._desc_open = False
        self._hist_sig = None
        self._hist_all = False
        self._hist_view: dict | None = None      # 正在「看日志」的那条记录
        self._follow_id: str | None = None       # 11-02 跟着播放器：上次跟过去的那一首（只在换歌那一刻跟，中途点别的不拉回）
        self._mon_on = False                     # 11-02 监控开着：歌单收成一行，下面是设备画面
        self._mon_row_id: str | None = None
        self._dev = None
        self._dev_size = "--"
        self._check_bad: list[str] = []
        self._ratio_asked: set[str] = set()
        self._limited_open = False      # 限时歌单默认收起（作者：「正常不显示限时歌单」）
        self._last_limited_row = None
        self._issues: list[tuple[str, str, str | None]] = []   # 还没被点掉的坏消息: (工具名, 气泡文字, 工具 id)
        app_cfg = session.data.get("app", {})
        self.app_name = app_cfg.get("name", "幻想引擎")
        self.logo = str(registry.AGG_DIR / (app_cfg.get("logo") or "logo.png"))
        mark = registry.AGG_DIR / (app_cfg.get("mark") or "mark.png")     # 11-14 左上角是引擎的标记（魔方）；封面、气泡、确认框是雨澜
        self.mark = str(mark) if mark.exists() else self.logo

        self.setWindowTitle(f"{self.app_name} v{version}")
        self.resize(1040, 712)               # 11-02 作者：界面的比例再拉长一点（940 → 1040，宽高比 1.32 → 1.46，接近常见的音乐播放器）
        self.setMinimumSize(920, 640)        # 11-02 播放区挪进右栏后 820 宽起播放中都不截断（渲染实测），最窄仍留 920
        central = GridBg()
        self.setCentralWidget(central)
        # 11-02（作者：「我觉得可以参考音乐播放器的布局，下方播放栏不贯穿了，放右侧」「大布局读法一就行」）：
        # 顶栏照旧横贯；下面左边歌单一直通到窗口底，右边从上到下是详情、播放区
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top())
        body = QHBoxLayout()
        body.setContentsMargins(16, 8, 16, 12)
        body.setSpacing(12)
        body.addWidget(self._build_playlist())
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._build_detail(), 1)
        right.addWidget(self._build_player())
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        # 提示（「已验收，截图已删除」等）：以前在播放栏最右端占一格，播放栏变窄后改成浮在播放栏上方几秒
        self.toast_lb = QLabel(central)
        self.toast_lb.setFont(T.font(12, 700))
        self.toast_lb.setVisible(False)

        self.bubble = EventBubble(central)
        self.bubble.anchor = lambda: (self.disc.mapTo(central, QPoint(self.disc.width() // 2, 0)).x(),
                                      self.player.mapTo(central, QPoint(0, 0)).y())
        self.bubble.clicked.connect(self._bubble_clicked)
        self.qpop = QueuePopup(central, session)

        self.devwatch = DeviceWatcher(parent=self)
        self.devwatch.updated.connect(self._on_device)
        self.monitor = MonitorFeed(self)
        self.monitor.frame.connect(self.mon_view.set_frame)
        QTimer.singleShot(1500, self._startup_check)      # 11-01 设计三：每次启动后台自检一遍
        self.monitor.failed.connect(self.mon_view.set_message)

        s = self.s
        s.changed.connect(self.refresh)
        s.tools_changed.connect(self._rebuild_rows)
        s.log_added.connect(self._log_added)
        s.log_reset.connect(self._log_reset)
        s.event.connect(self._on_event)
        s.toast.connect(self.show_toast)

        for key, delta in ((Qt.Key.Key_Up, -1), (Qt.Key.Key_Down, 1)):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda d=delta: self._move_selected(d))

        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._tick_time)
        self._clock.start()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast_lb.setVisible(False))

        QApplication.instance().installEventFilter(self)
        if not s.tool(s.selected):
            s.selected = s.tools[0]["id"] if s.tools else None
        self._rebuild_rows()
        self.devwatch.start()

    # ------------------------------------------------------------ 顶栏
    def _build_top(self) -> QWidget:
        top = QWidget()
        top.setFixedHeight(46)
        lay = QHBoxLayout(top)
        lay.setContentsMargins(16, 8, 16, 0)
        lay.setSpacing(8)
        lay.addWidget(LogoDot(self.mark))
        lay.addWidget(T.label(self.app_name, 15, 800))
        lay.addStretch(1)      # 11-01 设计三：设备状态、分辨率按钮不放顶栏——启动自检管（比例不对弹窗问），手动切换在「环境自检」里
        self.sw_notify = OptSwitch("完成通知")
        self.sw_notify.setChecked(self.s.notify)
        self.sw_notify.setToolTip("开着：每个工具跑完、整轮跑完各弹一条系统通知；关掉就不弹")
        self.sw_notify.toggled.connect(self.s.set_notify)
        lay.addWidget(self.sw_notify)
        b1 = PillButton("环境自检")
        self.btn_check = b1          # 设备没连上 / 上次自检有问题时变红边，悬停写原因（代替原来的设备卡）
        b_about = PillButton("关于", tip="使用教程、更新日志；排查用的「进程树」也在这里")
        b3 = PillButton("退出(停止全部)", "dan")
        b1.clicked.connect(self._open_selfcheck)
        b_about.clicked.connect(lambda: dialogs.AboutDialog(self, self.logo, self.app_name, self.version).exec())
        b3.clicked.connect(self.quit_all)
        for b in (b1, b_about, b3):     # 11-01 信息精简：「进程树」挪进「关于」
            lay.addWidget(b)
        return top

    def _build_playlist(self) -> QWidget:
        card = Card()
        card.setFixedWidth(300)          # 11-02 平衡-丙：348 → 320，让给右栏的播放栏；作者「左侧歌单再收窄一点点」→ 300
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(7)
        h3 = T.label("歌单", 14, 800)
        h3.setToolTip("歌单 = 工具列表，每个工具是一首歌")
        head.addWidget(h3)
        head.addStretch(1)
        self.btn_seq = PillButton("顺序播放 (0)", "pri", True,
                                  tip="顺序播放 = 一键串跑：按序号从小到大，一个接一个运行")
        # 11-02 作者：「新增」不要了（脚本都是 Claude 写的），这个位置改成监控开关——打开后变动的就是这一栏，和按钮在同一块
        self.btn_mon = PillButton("监控", "def", True,
                                  tip="打开：歌单收成正在播的那一行，下面显示设备实时画面（只在内存里，不存盘）；再点一下关掉")
        self.btn_seq.clicked.connect(self._seq_clicked)
        self.btn_mon.clicked.connect(lambda: self._set_monitor(not self._mon_on))
        for b in (self.btn_seq, self.btn_mon):        # 11-02 上移 / 下移按钮去掉：歌单里直接拖动排序（键盘 ↑↓ 还能用）
            head.addWidget(b)
        v.addLayout(head)
        v.addSpacing(10)
        inner = _RowsBox(self)
        self.rows_box = QVBoxLayout(inner)
        self.rows_box.setContentsMargins(0, 0, 0, 2)
        self.rows_box.setSpacing(4)
        self.rows_area = _scroll(inner)
        v.addWidget(self.rows_area, 1)
        self.mon_view = MonitorView()                # 11-02：监控画面在歌单这一栏里（以前是单独的置顶小窗）
        self.mon_view.setVisible(False)
        v.addSpacing(8)
        v.addWidget(self.mon_view, 1)
        return card

    def _build_detail(self) -> QWidget:
        card = Card()
        card.setMinimumWidth(420)
        self.detail_card = card
        v = QVBoxLayout(card)
        v.setContentsMargins(2, 0, 2, 0)
        v.setSpacing(0)
        dh = QHBoxLayout()
        dh.setSpacing(10)
        self.title = T.label("", 17, 800, wrap=True)
        self.tag = StatusTag("idle", "空闲")
        dh.addWidget(self.title, 1)
        dh.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(dh)
        v.addSpacing(8)
        # 11-01 信息精简：环境标签（只在要改分辨率时才有）、限时通知、常用时长放一行；都没有就整行收起不占高度。
        # 「移进 / 移回限时歌单」按钮去掉，歌单行右键菜单里照样有。
        self.badge_row = QWidget()
        br = QHBoxLayout(self.badge_row)
        br.setContentsMargins(0, 0, 0, 8)
        br.setSpacing(6)
        self.env_badge = badge("")
        self.notice_badge = notice_badge("")
        self.dur_lb = T.label("", 12, 400, T.MUTED)
        self.dur_lb.setToolTip("常用时长：最近几次完整播放的平均耗时")
        br.addWidget(self.env_badge)
        br.addWidget(self.notice_badge)
        br.addWidget(self.dur_lb)
        br.addStretch(1)
        v.addWidget(self.badge_row)
        # 说明: 默认折成一行，点「展开说明」看全文
        self.desc_fold = QWidget()
        fl = QHBoxLayout(self.desc_fold)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)
        self.desc_one = ElidedLabel()
        self.btn_unfold = LinkToggle("展开说明", "展开说明")
        self.btn_unfold.clicked.connect(lambda: self._set_desc_open(True))
        fl.addWidget(self.desc_one, 1)
        fl.addWidget(self.btn_unfold)
        v.addWidget(self.desc_fold)
        self.desc_full = QWidget()
        dl = QVBoxLayout(self.desc_full)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(4)
        self.desc_text = T.label("", 13, 400, T.INK2, wrap=True)
        self.desc_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.addWidget(self.desc_text)
        hl.addStretch(1)
        self.desc_area = _scroll(holder)
        dl.addWidget(self.desc_area)
        fr = QHBoxLayout()
        fr.addStretch(1)
        self.btn_fold = LinkToggle("收起说明", "收起说明")
        self.btn_fold.setChecked(True)
        self.btn_fold.clicked.connect(lambda: self._set_desc_open(False))
        fr.addWidget(self.btn_fold)
        dl.addLayout(fr)
        self.desc_full.setVisible(False)
        v.addWidget(self.desc_full)
        v.addSpacing(12)
        # 带角色的歌（乐土）：当前角色 +「切换角色」；别的歌整条隐藏、不占位
        self.role_bar = RoleBar()
        self.role_bar.switch.connect(self._open_roles)
        self.role_wrap = QWidget()
        rw = QVBoxLayout(self.role_wrap)
        rw.setContentsMargins(0, 0, 0, 12)
        rw.setSpacing(0)
        rw.addWidget(self.role_bar)
        self.role_wrap.setVisible(False)
        v.addWidget(self.role_wrap)

        self.tabs = TabBar([("steps", "歌词"), ("log", "运行日志"), ("hist", "播放记录")])
        self.tabs.btns["steps"].setToolTip("歌词 = 任务项：这个工具要做的每一步，一步一句")
        self.tabs.btns["log"].setToolTip("实时滚动，自动跟到最新一行")
        self.tabs.btns["hist"].setToolTip("播放记录 = 运行记录")
        self.all_lb = T.label("全部歌曲", 12, 700, T.INK2)
        self.all_sw = Switch(small=True)
        self.all_sw.setToolTip("打开后列出全部歌曲的记录，并在每条前面写上歌名")
        self.all_sw.toggled.connect(self._set_hist_all)
        self.tabs.corner.addWidget(self.all_lb)
        self.tabs.corner.addWidget(self.all_sw)
        self.tabs.changed.connect(self._tab_changed)
        v.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_pane_steps())
        self.stack.addWidget(self._build_pane_log())
        self.stack.addWidget(self._build_pane_hist())
        self.stack.setMinimumHeight(200)
        v.addWidget(self.stack, 1)
        self._tab_changed("steps")
        return card

    def _build_pane_steps(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(6)
        self.lyric_hint = T.label("这首歌不分句汇报进度：歌词不高亮，实时情况看「运行日志」", 12, 400, T.INK3, wrap=True)
        self.lyric_hint.setContentsMargins(10, 0, 10, 0)
        self.lyric_hint.setVisible(False)
        v.addWidget(self.lyric_hint)
        self.lyric_facts = T.label("", 12, 600, T.INK2, wrap=True)
        self.lyric_facts.setStyleSheet(f"color:{T.INK2};background:{T.GREY};border-radius:9px;padding:5px 10px;")
        self.lyric_facts.setToolTip("从这个工具的日志里读出来的要点，随运行更新")
        self.lyric_facts.setVisible(False)
        v.addWidget(self.lyric_facts)
        inner = QWidget()
        self.lyric_box = QVBoxLayout(inner)
        self.lyric_box.setContentsMargins(0, 0, 4, 0)
        self.lyric_box.setSpacing(0)
        self.lyric_area = _scroll(inner)
        v.addWidget(self.lyric_area, 1)
        return w

    def _build_pane_log(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        self.logbar = QWidget()
        lb = QHBoxLayout(self.logbar)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(10)
        self.pinfo = T.label("", 12, 400, T.INK3, wrap=True)
        self.pinfo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pinfo.setToolTip("这次运行的进程号和日志文件")
        btn = PillButton("清空显示", small=True, tip="只清空这里的显示，不删除日志文件")
        btn.clicked.connect(lambda: self.s.clear_log(self.s.selected))
        lb.addWidget(self.pinfo, 1)
        lb.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        v.addWidget(self.logbar)
        self.log_view = LogView()
        v.addWidget(self.log_view, 1)
        self.log_empty = EmptyBox("还没开始播放", "播放后日志在这里滚动")
        v.addWidget(self.log_empty, 1)
        return w

    def _build_pane_hist(self) -> QWidget:
        self.hist_stack = QStackedWidget()
        # 第 1 页: 记录列表
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 12, 0, 0)
        inner = QWidget()
        self.hist_box = QVBoxLayout(inner)
        self.hist_box.setContentsMargins(0, 0, 6, 3)
        self.hist_box.setSpacing(8)
        self.hist_area = _scroll(inner)
        v.addWidget(self.hist_area, 1)
        self.hist_empty = EmptyBox("还没有播放记录", "播放过一次之后，结果会记在这里")
        v.addWidget(self.hist_empty, 1)
        self.hist_stack.addWidget(page)
        # 第 2 页: 某一次的日志（只读）
        page2 = QWidget()
        v2 = QVBoxLayout(page2)
        v2.setContentsMargins(0, 10, 0, 0)
        v2.setSpacing(10)
        crumb = QHBoxLayout()
        crumb.setSpacing(10)
        back = PillButton("‹ 返回记录", small=True)
        back.clicked.connect(self._hist_back)
        self.crumb_text = T.label("", 13, 400, T.INK2, wrap=True)
        self.crumb_tag = StatusTag("ok", "")
        crumb.addWidget(back)
        crumb.addWidget(self.crumb_text, 1)
        crumb.addWidget(self.crumb_tag)
        v2.addLayout(crumb)
        self.hist_log = LogView()
        v2.addWidget(self.hist_log, 1)
        self.hist_stack.addWidget(page2)
        return self.hist_stack

    # ------------------------------------------------------------ 底部「正在播放」栏
    def _build_player(self) -> QWidget:
        """11-02 播放区是右栏底下的一张卡片，两行：第 1 行 歌名；第 2 行 已播时长 · 进度格 · 第几句 · 暂停 · 下一首。
        左边是封面：播放时转；有待播时鼠标移上去右上角出个数，点封面看待播。
        当前步骤小块去掉了（作者：「单步的摘要可以不要，因为歌词里面有，下面也有第几步的实时进度」）；
        「停止连播」并进歌单头的「顺序播放」；停这一首用歌单那一行的停止钮。"""
        bar = Card(pad=10)
        bar.setFixedHeight(88)
        self.player = bar
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(12)
        self.disc = Disc(self.logo)
        self.disc.setToolTip("雨澜的封面：播放时慢慢转，空闲时静止；有事的时候气泡从这里冒出来")
        lay.addWidget(self.disc, 0, Qt.AlignmentFlag.AlignVCenter)

        mid = QVBoxLayout()
        mid.setSpacing(8)
        mid.addStretch(1)
        l1 = QHBoxLayout()
        l1.setSpacing(10)
        self.ptag = T.label("正在播放", 12, 800, T.PINKTX)
        self.pname = T.label("还没开始播放", 15, 800, T.INK2)
        self.pname.installEventFilter(self)
        self.disc.clicked.connect(self._toggle_queue)      # 11-02：待播收进封面，点封面看
        self.disc.drag_hover.connect(self._disc_drag_hover)
        self.disc.dropped.connect(lambda tid: QTimer.singleShot(0, lambda: self.s.enqueue(tid)))
        l1.addWidget(self.ptag)
        l1.addWidget(self.pname)
        l1.addStretch(1)
        mid.addLayout(l1)
        l2 = QHBoxLayout()
        l2.setSpacing(10)
        self.ptime = T.label("00:00", 13, 700, mono=True)
        self.ptime.setToolTip("已播时长")
        self.segs = SegProgress()
        self.segs.setToolTip("绿 = 已完成，条纹 = 正在进行，白 = 还没到；一格是一句（循环型工具是一层 / 一关 / 一颗星）")
        self.segs.setMinimumWidth(110)
        self.pcnt = T.label("", 12, 400, T.INK2)
        # 11-02：这一行有 36 高的圆钮，时长、句数按自己的高度垂直居中，不被拉高
        l2.addWidget(self.ptime, 0, Qt.AlignmentFlag.AlignVCenter)     # 11-01 信息精简：空闲时的「点歌单里的 ▶ 播放一首」去掉
        l2.addWidget(self.segs, 1, Qt.AlignmentFlag.AlignVCenter)
        l2.addWidget(self.pcnt, 0, Qt.AlignmentFlag.AlignVCenter)
        l2.addStretch(0)
        self._l2 = l2
        self._l2_si = l2.count() - 1
        # 11-02 作者：「暂停和下一首是两个小圆钮，停止就算了，因为停止在最上边有停止连播，每个歌单也有单独的停止」
        # 暂停：脚本停在下一次截图前（战斗中不停，按钮弹回、雨澜的气泡说一声）；下一首 = 停掉这一个、接着播待播的第 1 个
        self.btn_pause = RoundCtl()
        self.btn_pause.set_mode("pause")
        self.btn_pause.clicked.connect(self._pause_clicked)
        self.btn_next = RoundCtl()
        self.btn_next.set_mode("next")
        self.btn_next.clicked.connect(self._next_clicked)
        l2.addSpacing(2)
        l2.addWidget(self.btn_pause)
        l2.addWidget(self.btn_next)
        mid.addLayout(l2)
        mid.addStretch(1)
        lay.addLayout(mid, 1)
        return bar

    # ============================================================ 歌单
    def _mon_focus(self) -> str | None:
        s = self.s
        return s.cur.id if s.cur else (s.pending or s.selected)

    def _rebuild_rows(self):
        _clear(self.rows_box)
        self._rows = {}
        def add_row(t, title):
            row = ToolRow(t, title)
            row.picked.connect(self.s.select)
            row.check.connect(self.s.toggle_check)
            row.limit.connect(self._toggle_limited)
            row.review.connect(self.jump_to_shot)
            self.rows_box.addWidget(row)
            self._rows[t["id"]] = row
            return row
        if self._mon_on:
            # 11-02 作者：「打开监控后，歌单收缩成一行（当前播放内容），监控从下面长出来」——只留一行、写全名，歌单区按这一行的高度收起
            tid = self._mon_focus()
            self._mon_row_id = tid
            tool = self.s.tool(tid) if tid else None
            if tool is not None:
                row = add_row(tool, tool.get("name", "?"))
                self.rows_area.setFixedHeight(max(row.sizeHint().height(), row.minimumHeight()) + 8)
            else:
                self.rows_area.setFixedHeight(0)
            self.refresh()
            return
        self.rows_area.setMinimumHeight(0)
        self.rows_area.setMaximumHeight(16777215)
        for ai, (album, tools) in enumerate(self.s.albums()):
            self.rows_box.addWidget(AlbumHeader(album, len(tools), ai))
            for t in tools:
                add_row(t, self.s.short_name(t))
        self.rows_box.addStretch(1)
        limited = self.s.limited()
        self._last_limited_row = None
        if limited:
            head = LimitedHeader(len(limited), self._limited_open)
            head.toggled.connect(self._fold_limited)
            self.rows_box.addWidget(head)
            if self._limited_open:
                for t in limited:
                    add_row(t, t.get("name", "?"))     # 不分专辑，写全名
                self._last_limited_row = self._rows[limited[-1]["id"]]
        self.refresh()

    def _fold_limited(self):
        self._limited_open = not self._limited_open
        self._rebuild_rows()
        if self._limited_open and self._last_limited_row is not None:   # 展开后把限时歌单里的几首滚进视野
            row = self._last_limited_row
            QTimer.singleShot(80, lambda: self._ensure_visible(row))

    def _follow(self, tid: str):
        """11-02 作者：「随播放器选中对应歌单，展开歌词」——换到新的一首时选中它、滚到看得见，详情切到歌词页。"""
        if not (self.s.cur and self.s.cur.id == tid):
            return
        self.s.select(tid)
        self.tabs.set_current("steps")
        row = self._rows.get(tid)
        if row is not None:
            QTimer.singleShot(0, lambda r=row: self._ensure_visible(r))

    def _ensure_visible(self, row):
        try:
            self.rows_area.ensureWidgetVisible(row, 0, 12)
        except RuntimeError:       # 这期间歌单又重建过，旧行已删
            pass

    def _toggle_limited(self, tid: str | None):
        tool = self.s.tool(tid)
        if tool:
            self.s.set_limited(tid, not is_limited(tool))

    def _move_selected(self, delta: int):
        if self.s.selected and QApplication.activeModalWidget() is None:
            self.s.move(self.s.selected, delta)

    def _seq_clicked(self):
        if self.s.queue:
            self.s.stop_after_current()
        else:
            self._confirm_seq()

    def _confirm_seq(self):
        ids = self.s.checked_ordered()
        if not ids or self.s.busy:
            return
        names = [self.s.short_name(self.s.tool(i)) for i in ids]
        if dialogs.confirm_seq(self, self.logo, names):
            self.s.play_checked()

    def _open_shot(self, rec: dict | None):
        p = self.s.shot_path(rec) if rec else None
        if not p:
            self.show_toast("这张截图已经不在了")
            return
        cap = f"{rec.get('name', '?')} · {history.fmt_when(rec.get('end') or rec.get('start', 0))} 结束时的画面 · {history.result_text(rec)}"
        d = dialogs.ShotDialog(self, str(p), "结束截图", cap)
        d.exec()
        if d.accepted_shot:
            self.s.accept_shot(rec)

    # ============================================================ 整体刷新
    def refresh(self):
        s = self.s
        sel = s.selected
        for tid, row in self._rows.items():
            row.refresh(s.row_state(tid), tid == sel, s.last_record(tid), s.avg(tid))
        n = len(s.checked_ordered())
        # 11-02 作者：「停止连播跟顺序播放合并成一个按钮，顺序播放开启后就变成停止连播红色按钮就行，也不要动效」
        # 「停止连播是播完当前就不播了，要想直接杀死进程，右上角有按钮了」→ 有待播时是红色「停止连播」（清掉待播，这首播完就停）；
        # 没有待播（点过停止连播、或单独播一首）变回「顺序播放」，在播时灰着
        if s.queue:
            self.btn_seq.set_kind("dan")
            self.btn_seq.setText("停止连播")
            self.btn_seq.setEnabled(True)
            self.btn_seq.setToolTip("停止连播：待播的全部取消，正在播的这一首播完就停（要马上停：点这一行的停止钮，或右上角退出）")
        else:
            self.btn_seq.set_kind("pri")
            self.btn_seq.setText(f"顺序播放 ({n})")
            self.btn_seq.setEnabled(n > 0 and not s.busy)
            self.btn_seq.setToolTip("正在播放，播完才能顺序播放" if s.busy else
                                    "顺序播放 = 一键串跑：按序号从小到大，一个接一个运行")
        self._refresh_detail()
        self._refresh_player()
        if self._mon_on and self._mon_focus() != self._mon_row_id:     # 监控开着时那一行跟着正在播的走
            QTimer.singleShot(0, self._rebuild_rows)
        cur_id = s.cur.id if s.cur else None
        if cur_id != self._follow_id:
            self._follow_id = cur_id
            if cur_id:
                QTimer.singleShot(0, lambda t=cur_id: self._follow(t))
        if self.qpop.isVisible():
            self.qpop.rebuild()          # 11-02 空了也不关（写着怎么加），点外面或再点封面才关

    # ------------------------------------------------------------ 中列
    def _refresh_detail(self):
        s = self.s
        tool = s.tool(s.selected)
        if tool is None:
            self.title.setText("请在左边选一首")
            self.tag.setVisible(False)
            self.badge_row.setVisible(False)
            self.desc_fold.setVisible(False)
            self.desc_full.setVisible(False)
            self.role_wrap.setVisible(False)
            return
        tid = tool["id"]
        if tid != self._detail_id:
            self._detail_id = tid
            self._lyric_cur = None
            self.title.setText(tool.get("name", "?"))
            eb = ENV_BADGE.get(tool.get("env", ""), "")
            self.env_badge.setText(eb)
            self.env_badge.setVisible(bool(eb))
            note = str(tool.get("notice") or "").strip()
            self.notice_badge.setText(note)
            self.notice_badge.setVisible(bool(note))
            desc = tool.get("description", "") or ""
            self.desc_one.setText(desc)
            self.desc_text.setText(desc)
            self._has_desc = bool(desc.strip())
            self._desc_multi = "\n" in desc.strip()
            self._apply_desc()
            self._load_log(tid)
            self._hist_view = None
            self._hist_sig = None
        self._sync_lyrics(tool)
        self._refresh_role(tool)
        avg = s.avg(tid)
        self.dur_lb.setText(f"常用时长 {history.fmt_about(avg)}" if avg else "")
        self.dur_lb.setVisible(bool(avg))
        self.badge_row.setVisible(any(w.isVisibleTo(self.badge_row) for w in (self.env_badge, self.notice_badge, self.dur_lb)))
        rs = s.row_state(tid)["state"]
        self.tag.set({"now": "run", "wait": "wait", "idle": "idle"}[rs],
                     {"now": "正在播放", "wait": "待播", "idle": "空闲"}[rs])
        self.tag.setToolTip({"now": "正在播放 = 运行中", "wait": "待播 = 排队中", "idle": ""}[rs])
        self.tag.setVisible(rs == "wait")      # 11-01 空闲时不挂「空闲」；11-02 作者：正在播放时这里不要动效（播放区已经在播），只在待播时挂「待播」
        self._refresh_lyrics(tool)
        self._refresh_logbar(tid, rs)
        self.tabs.btns["log"].set_extra(live=bool(s.cur and s.cur.id == tid))
        self._refresh_hist()

    def _refresh_role(self, tool: dict):
        """带角色的歌: 当前角色一条。正在播放这首时显示这次真用的那个角色（带音柱、第几层），按钮不能点。"""
        s = self.s
        it = s.role_of(tool["id"])
        if it is None:
            self.role_wrap.setVisible(False)
            return
        st = s.cur if (s.cur and s.cur.id == tool["id"]) else None
        show = (st.role if st is not None and st.role else None) or it
        if st is not None:
            facts = st.tracker.facts()
            sub = "正在打" + (f" · {facts[0]}" if facts else "")
        else:
            # 11-01 信息精简：只留打法；括号里的补充（「（角色教学推荐一）」）和「上次 …」不写——上次结果歌单、播放记录都有
            sub = re.sub(r"\s*[（(][^）)]*[）)]", "", str(show.get("desc") or "")).strip()
        self.role_bar.set_role(show, sub, playing=st is not None, locked=s.pick_locked(tool["id"]))
        self.role_wrap.setVisible(True)

    def _open_roles(self):
        s = self.s
        tid = s.selected
        tool = s.tool(tid)
        cfg = role_cfg(tool)
        if not cfg:
            return
        if s.pick_locked(tid):
            self.show_toast("正在播放，换角色下次播放才生效")
            return
        cur = s.role_of(tid) or {}
        dlg = RolePanel(self, str(tool.get("name") or ""), cfg["items"], cur.get("id"), s.role_recent(tid))
        if dlg.exec() and dlg.chosen:
            s.set_role(tid, dlg.chosen)

    def _apply_desc(self):
        show = self._has_desc
        self.desc_fold.setVisible(show and not self._desc_open)
        self.desc_full.setVisible(show and self._desc_open)
        if show and not self._desc_open:
            QTimer.singleShot(0, self._check_fold_btn)
        if show and self._desc_open:
            QTimer.singleShot(0, self._fit_desc)

    def _check_fold_btn(self):
        self.btn_unfold.setVisible(self._desc_multi or self.desc_one.is_elided())

    def _fit_desc(self):
        """说明展开后有多长显示多长；页签区至少留 200 高，再长就在说明区里滚动。"""
        if not self.desc_full.isVisible():
            return
        w = max(100, self.desc_area.viewport().width() - 8)
        need = self.desc_text.heightForWidth(w)
        if need < 0:
            need = self.desc_text.sizeHint().height()
        cap = max(60, self.detail_card.height() - 330)
        self.desc_area.setFixedHeight(min(need + 2, cap))

    def _set_desc_open(self, on: bool):
        self._desc_open = on
        self.btn_unfold.setChecked(False)
        self.btn_fold.setChecked(True)
        self._apply_desc()

    def _sync_lyrics(self, tool: dict):
        """签名变了才重建歌词。普通的歌只看工具 id（和以前一样只在换歌时重建）；
        带子项的歌还看句子、勾选、是否正在播放这首 / 能不能改勾选、有没有子项文件。"""
        view = self.s.sub_view(tool["id"])
        if view is None:
            sig = (tool["id"],)
        else:
            sig = (tool["id"], tuple(view["lines"]), tuple(view["picks"]), view["playing"],
                   view["locked"], view["nofile"])
        if sig == self._lyric_sig:
            return
        self._lyric_sig = sig
        self._lyric_cur = None
        self._build_lyrics(tool, view)

    def _build_lyrics(self, tool: dict, view: dict | None = None):
        """view 是 session.sub_view 的结果（带子项的歌）；歌词行和运行中的 tracker 用同一份句子，下标一一对应。"""
        _clear(self.lyric_box)
        self._lyrics = []
        tasks = view["lines"] if view else (tool.get("tasks") or [])
        self.tabs.btns["steps"].set_extra(count=str(len(tasks)) if tasks else "")
        if not tasks:
            self.lyric_box.addWidget(T.label("(无细项, 整段脚本自动执行)", 13, 400, T.INK3))
        for i, tn in enumerate(tasks, 1):
            if view and i > view["head"]:          # 子项句: 前面带勾选框
                pos = i - view["head"]
                row = LyricRow(i, tn, pick=pos in view["picks"], locked=view["locked"])
                row.picked.connect(lambda t=tool["id"], p=pos: self.s.toggle_pick(t, p))
            else:
                row = LyricRow(i, tn)
            self.lyric_box.addWidget(row)
            self._lyrics.append(row)
        if view and view["nofile"]:                # 不可点、不进 self._lyrics
            hint = T.label("还没读到本期 BOSS：先播放一次（不勾也行）", 12, 400, T.MUTED, wrap=True)
            hint.setContentsMargins(42, 6, 10, 0)
            self.lyric_box.addWidget(hint)
        self.lyric_box.addStretch(1)

    def _refresh_lyrics(self, tool: dict):
        st = self.s.cur
        mine = st is not None and st.id == tool["id"]
        cur, done, hint, facts = None, set(), False, []
        if mine:
            tr = st.tracker
            facts = tr.facts()
            if tr.has_steps:
                cur, done = tr.cur, tr.done_set()
            elif not st.by_steps and self._lyrics:
                hint = True
        self.lyric_hint.setVisible(hint)
        self.lyric_facts.setText("  ｜  ".join(facts))
        self.lyric_facts.setVisible(bool(facts))
        for i, row in enumerate(self._lyrics):
            row.set_state("now" if i == cur else ("done" if i in done else "todo"))
        if cur != self._lyric_cur:
            self._lyric_cur = cur
            if cur is not None and cur < len(self._lyrics):
                row = self._lyrics[cur]
                QTimer.singleShot(0, lambda r=row: self._ensure_row(r))

    def _ensure_row(self, row):
        try:
            self.lyric_area.ensureWidgetVisible(row, 0, 60)
        except RuntimeError:
            pass

    # ------------------------------------------------------------ 运行日志页签
    def _load_log(self, tid: str):
        buf = self.s.logs.get(tid)
        self.log_view.set_live(False)
        self.log_view.set_lines(list(buf.lines) if buf else [])
        self.log_view.set_live(bool(buf and buf.live))

    def _log_added(self, tid: str, text: str, kind: str):
        if tid == self.s.selected:
            self.log_view.add(text, kind)

    def _log_reset(self, tid: str):
        if tid == self.s.selected:
            self._load_log(tid)
            self.refresh()

    def _refresh_logbar(self, tid: str, row_state: str):
        buf = self.s.logs.get(tid)
        has = bool(buf and (buf.lines or buf.live))
        self.log_view.setVisible(has)
        self.logbar.setVisible(has)
        self.log_empty.setVisible(not has)
        if has:
            self.log_view.set_live(buf.live)
            self.pinfo.setText(f"PID {buf.pid} · logs/{buf.file}" if buf.live
                               else f"已结束 · logs/{buf.file}")
        elif row_state == "wait":
            self.log_empty.set("待播", "前面的播放完就轮到它")
        else:
            self.log_empty.set("还没开始播放", "播放后日志在这里滚动")

    # ------------------------------------------------------------ 播放记录页签
    def _set_hist_all(self, on: bool):
        self._hist_all = on
        self._hist_view = None
        self._refresh_hist()

    def _refresh_hist(self):
        s = self.s
        if self._hist_view is not None:
            self.hist_stack.setCurrentIndex(1)
            return
        self.hist_stack.setCurrentIndex(0)
        sig = (s.selected, self._hist_all, len(s.records), time.strftime("%Y%m%d"), s.shot_ver)
        if sig == self._hist_sig:
            return
        self._hist_sig = sig
        recs = s.records if self._hist_all else [r for r in s.records if r.get("id") == s.selected]
        recs = recs[:60]
        _clear(self.hist_box)
        for rec in recs:
            card = RecCard(rec, self._hist_all, s.is_seen(rec))
            card.view_log.connect(self._hist_open)
            card.view_shot.connect(self._open_shot)
            card.accept_shot.connect(self.s.accept_shot)
            card.mark_seen.connect(self.s.mark_seen)
            self.hist_box.addWidget(card)
        self.hist_box.addStretch(1)
        self.hist_area.setVisible(bool(recs))
        self.hist_empty.setVisible(not recs)

    def _hist_open(self, rec: dict):
        self._hist_view = rec
        self.crumb_text.setText(f"{history.fmt_when(rec.get('start', 0))} · 耗时 {history.fmt_dur(rec.get('dur', 0))}")
        self.crumb_text.setToolTip(f"logs/{rec.get('log', '')}")
        self.crumb_tag.set(RESULT_TAG.get(rec.get("result"), "bad"), history.result_text(rec))
        path = runner.LOGS_DIR / str(rec.get("log", ""))
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
        except Exception:
            lines = None
        if lines is None:
            self.hist_log.set_lines([("这一次的日志文件已经不在了。", "sys")])
        else:
            from core import steps
            from .session import classify
            tool = self.s.tool(rec.get("id")) or {}
            # 带子项的歌每次句子不一样，记录里存了这次的句子；旧记录没有就用 tools.json 的
            rtasks = rec.get("tasks")
            tasks = rtasks if (isinstance(rtasks, list) and rtasks) else (tool.get("tasks") or [])
            tracker = steps.StepTracker(tasks, tool.get("report"))
            keys = [str(m.get("reason", "")) for m in rec.get("missed") or []
                    if m.get("reason") and m.get("reason") not in ("出错", "运行到这里时异常退出")]
            out = []
            for ln in lines:
                tracker.feed(ln)
                kind = classify(ln, tracker)
                mark = tracker.bad_hit or ("出错，跳过" in ln) or (kind != "hd" and any(k in ln for k in keys) and "完成：" not in ln)
                out.append((ln, kind, mark))
            self.hist_log.set_lines(out or [("（这一次没有任何输出）", "sys")])
        self.hist_stack.setCurrentIndex(1)

    def _hist_back(self):
        self._hist_view = None
        self._refresh_hist()

    def _tab_changed(self, key: str):
        self.stack.setCurrentIndex({"steps": 0, "log": 1, "hist": 2}[key])
        show = key == "hist"
        self.all_lb.setVisible(show)
        self.all_sw.setVisible(show)
        if key == "log":
            QTimer.singleShot(0, self.log_view.scroll_to_end)

    def jump_to_history(self, tid: str | None, all_songs: bool = False):
        if tid and self.s.tool(tid):
            self.s.select(tid)
        if self.all_sw.isChecked() != all_songs:
            self.all_sw.setChecked(all_songs)
        self._hist_view = None
        self.tabs.set_current("hist")
        self._refresh_hist()

    def jump_to_shot(self, tid: str):
        """11-03 作者：歌单行点「待验收 ›」→ 选中这首、切到「播放记录」、没验收的那条滚到最上面并描金框 2 秒（作者挑的版本二）。"""
        rec = self.s.pending_shot(tid)
        self.jump_to_history(tid)
        if rec is not None:
            QTimer.singleShot(0, lambda: self._show_rec(rec))

    def _show_rec(self, rec: dict):
        # 卡片刚重建还没排版（滚动范围是 0）：按视口宽度先把内容排好，滚动范围才对
        inner, vp = self.hist_area.widget(), self.hist_area.viewport()
        lay = inner.layout()
        h = lay.totalHeightForWidth(vp.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        inner.resize(vp.width(), max(h, vp.height()))
        for i in range(self.hist_box.count()):
            w = self.hist_box.itemAt(i).widget()
            if isinstance(w, RecCard) and (w.rec is rec or (w.rec.get("id"), w.rec.get("start")) == (rec.get("id"), rec.get("start"))):
                self.hist_area.verticalScrollBar().setValue(w.y())
                w.flash()
                return

    # ------------------------------------------------------------ 播放栏
    def _refresh_player(self):
        s = self.s
        st = s.cur
        upcoming = s.pending or (s.queue[0] if (s._gap and s.queue) else None)
        running = st is not None
        self.disc.set_playing(running)
        has_q = bool(s.queue)
        if running:
            tr = st.tracker
            self.ptag.setText("正在停止…")
            self.ptag.setVisible(st.stopping)        # 11-01 信息精简：「正在播放」不写，封面在转、进度在走就是在播
            self.pname.setText(st.name + (f" · {role_short(st.role)}" if st.role else ""))
            self.pname.setStyleSheet(f"color:{T.INK};background:transparent;")
            self.pname.setCursor(Qt.CursorShape.PointingHandCursor)
            self.pname.setToolTip("点歌名：在歌单里选回正在播放的这一个")
            total = len(tr.tasks)
            at = tr.has_steps and tr.cur is not None and tr.cur < total
            # 带子项的歌: 播放栏只数这次要跑的句子（固定句 + 勾了的子项），没勾的子项不占格、不算序号；
            # 当前句不在要跑的句子里（理论上不会）就退回按全部句子算
            run = st.run_idx()
            if run is not None and at and tr.cur not in run:
                run = None
            shown = len(run) if run is not None else total      # 播放栏里一共几句
            if at:
                no = run.index(tr.cur) + 1 if run is not None else tr.cur + 1
            cnt = ""
            if tr.cyclic:               # 循环环节型: 进度条画层数 / 星级 / 关数，没有总数就画条纹
                prog = tr.progress()
                if prog and prog["style"] == "pos":
                    v = prog["value"]
                    self.segs.set_steps(prog["total"], set(range(max(0, v - 1))), v - 1 if v >= 1 else None)
                    cnt = prog["text"]
                elif prog:
                    self.segs.set_steps(prog["total"], set(range(prog["value"])), None)
                    cnt = prog["text"]
                else:
                    self.segs.set_busy()
                    cnt = tr.headline() or ""
            elif at and run is not None:
                done = tr.done_set()
                self.segs.set_steps(shown, {k for k, i in enumerate(run) if i in done}, no - 1)
                cnt = f"第 {no} / {shown} 句"
            elif at:
                self.segs.set_steps(total, tr.done_set(), tr.cur)
                cnt = f"第 {tr.cur + 1} / {total} 句"
            elif st.by_steps and shown:
                self.segs.set_steps(shown, set(), None)
                cnt = "准备中"
            else:
                self.segs.set_busy()
            self.pcnt.setText(cnt)
            self.pcnt.setVisible(bool(cnt))
            self.ptime.setVisible(True)
            self.segs.setVisible(True)
            self._tick_time()
        elif upcoming:
            tool = s.tool(upcoming) or {}
            self.ptag.setText("即将播放")
            self.ptag.setVisible(True)
            self.pname.setText(tool.get("name", ""))
            self.pname.setStyleSheet(f"color:{T.INK};background:transparent;")
            self.pname.unsetCursor()
            self.pname.setToolTip("")
            for w in (self.ptime, self.segs, self.pcnt):
                w.setVisible(False)
        else:
            # 11-02 作者：「空闲不用单独做，就用播放中的UI，只不过计时未开始，进度未开始，按钮处于暂停状态，下一首也能用」
            self.ptag.setVisible(False)
            sel = s.tool(s.selected)
            self.pname.setText(sel.get("name", "?") if sel else "在左边选一首")
            self.pname.setStyleSheet(f"color:{T.INK if sel else T.INK2};background:transparent;")
            self.pname.unsetCursor()
            self.pname.setToolTip("")
            n = self._idle_steps(sel)
            self.ptime.setText("00:00")
            self.ptime.setVisible(sel is not None)
            if n:
                self.segs.set_steps(n, set(), None)
                self.pcnt.setText(f"共 {n} 句")
            self.segs.setVisible(bool(n))
            self.pcnt.setVisible(bool(n))
        self._l2.setStretch(self._l2_si, 0 if self.segs.isVisibleTo(self.player) else 1)
        can = running and not st.stopping
        paused = bool(running and st.paused)
        idle = not s.busy and s.tool(s.selected) is not None
        if idle:
            self.btn_pause.set_mode("play")
            self.btn_pause.setToolTip("播放这首" + ("（拖进待播的接在后面）" if has_q else ""))
            self.btn_next.set_mode("next")
            self.btn_next.setToolTip("换到下一首要播的：先看待播，再看勾了的（按序号），都没有就是歌单里的下一行")
        else:
            self.btn_pause.set_mode("play" if paused else "pause")
            self.btn_pause.setToolTip("继续：从停下的地方接着做（继续前最好把游戏放回暂停时那一页）" if paused else
                                      "暂停：脚本停在下一次截图前；战斗中不能停")
            self.btn_next.set_mode("next" if has_q else "stop")
            self.btn_next.setToolTip("下一首：停掉这一个，接着播待播的第 1 个" if has_q else
                                     "结束这首（没有待播；点了先问一下）")
        self.btn_pause.setVisible(idle or running)
        self.btn_pause.setEnabled(idle or can)
        self.btn_next.setVisible(idle or running)
        self.btn_next.setEnabled(idle or can)
        self.disc.set_queue(len(s.queue))
        # 11-02 作者：暂停时「专辑停止旋转（歌单的按钮也记得停止旋转）」→ 全局动画时钟停住，所有在动的一起停
        Anim.get().freeze(paused)

    def _idle_steps(self, tool: dict | None) -> int:
        """空闲时播放区的进度格画几格：带子项的歌 = 固定句 + 勾了的子项，别的 = 歌词句数。"""
        if not tool:
            return 0
        view = self.s.sub_view(tool["id"])
        if view is not None:
            return view["head"] + len(view["picks"])
        return len(tool.get("tasks") or [])

    def _tick_time(self):
        st = self.s.cur
        if st is not None:
            self.ptime.setText(history.fmt_dur(st.active()))     # 11-02 暂停的时间不算

    def _pause_clicked(self):
        s = self.s
        if not s.busy:              # 11-02 空闲：暂停钮显示成 ▶，点了播放区这首（= 选中的那首），拖进待播的接在后面
            if s.selected:
                s.play(s.selected)
            return
        msg = s.pause_toggle()
        if msg:                     # 没停（战斗中 / 这首没接上暂停）：按钮不变，雨澜的气泡说一声
            self._say(msg)

    def _next_clicked(self):
        """11-02：空闲 = 换到下一首要播的（先待播、再勾了的、都没有就下一行）；播放中有待播 = 停掉这首接着播下一首；
        没有待播 = 「结束这首」，先弹确认框（作者：「结束这首加确认框」）。"""
        s = self.s
        if not s.busy:
            tid = s.cue_next(s.selected)
            if tid:
                s.select(tid)
                row = self._rows.get(tid)
                if row is not None:
                    QTimer.singleShot(0, lambda r=row: self._ensure_visible(r))
            return
        if s.queue:
            s.stop_current()
        elif s.cur and not s.cur.stopping and dialogs.confirm_end(self, self.logo, s.cur.name):
            s.stop_current()

    def _say(self, text: str, ms: int = 4500):
        """雨澜的气泡说一句临时的话（几秒后自己收起）；收起后还有没点掉的坏消息就接着显示那个。"""
        self._say_tok = getattr(self, "_say_tok", 0) + 1
        tok = self._say_tok
        self.bubble.show_message(html.escape(text), ("say", None), link=False)
        QTimer.singleShot(ms, lambda: self._unsay(tok))

    def _unsay(self, tok: int):
        if tok != getattr(self, "_say_tok", 0) or not self.bubble.isVisible() or self.bubble._payload != ("say", None):
            return
        self.bubble.hide()
        if self._issues:
            self._show_issues()

    def show_toast(self, text: str, bad: bool = False):
        self.toast_lb.setText(("! " if bad else "✓ ") + text)
        self.toast_lb.setStyleSheet(f"color:{T.BADTX if bad else T.OKTX};background:#FFFFFF;"
                                    f"border:2px solid {T.INK};border-radius:12px;padding:4px 12px;")
        self._place_toast()
        self.toast_lb.setVisible(True)
        self.toast_lb.raise_()
        self._toast_timer.start(6000 if bad else 4000)

    def _place_toast(self):
        """浮在播放栏上方、靠右（左边留给从封面冒出来的气泡）。"""
        self.toast_lb.adjustSize()
        c = self.centralWidget()
        tl = self.player.mapTo(c, QPoint(0, 0))
        x = tl.x() + self.player.width() - self.toast_lb.width() - 12
        y = tl.y() - self.toast_lb.height() - 8
        if self.bubble.isVisible() and x < self.bubble.x() + self.bubble.width():     # 气泡也在：叠到气泡上面，不互相挡
            y = self.bubble.y() + self.bubble.TOP - self.toast_lb.height() - 6
        self.toast_lb.move(max(8, x), y)

    # ------------------------------------------------------------ 待播浮层 / 事件气泡
    def _toggle_queue(self):
        if self.qpop.isVisible():
            self._close_queue()
        else:
            self._open_queue()

    def _open_queue(self):
        c = self.disc.mapTo(self.centralWidget(), QPoint(self.disc.width() // 2, 0))
        self.qpop.open_at(c.x(), c.y() + 4)

    def _disc_drag_hover(self, on: bool):
        """拖着歌停在封面上一会儿（0.6 秒）：展开待播列表，可以直接放到想要的位置（11-02 作者：可以试试）。"""
        if on:
            QTimer.singleShot(600, lambda: self.disc._drop_hover and not self.qpop.isVisible() and self._open_queue())

    def _autoscroll(self, y_in_box: float):
        """在歌单里拖动、碰到可视区上下边时往那边滚一点。"""
        sb = self.rows_area.verticalScrollBar()
        top, vh = sb.value(), self.rows_area.viewport().height()
        if y_in_box - top < 28:
            sb.setValue(sb.value() - 16)
        elif top + vh - y_in_box < 28:
            sb.setValue(sb.value() + 16)

    def _close_queue(self):
        self.qpop.hide()

    def _issue(self, name: str, text: str, tid: str | None):
        """坏消息气泡: 停到被点掉为止。没点掉之前又来了新的，就合成一句，免得前面的被顶掉看不到。"""
        self._issues.append((name, text, tid))
        self._show_issues()

    def _show_issues(self):
        if not self._issues:
            return
        if len(self._issues) == 1:
            name, text, tid = self._issues[0]
            self.bubble.show_message(text, ("one", tid), link=tid is not None)
            return
        # 按「歌」说（10-07 作者：「5件事」改成「5首歌」）：同一首出了几次问题算一首
        songs = list(dict.fromkeys(i[0] for i in self._issues))
        if len(songs) == 1:
            sid = next((i[2] for i in self._issues if i[2]), None)
            self.bubble.show_message(
                f"<b style='color:{T.BADTX}'>{html.escape(songs[0])}</b>："
                f"<b style='color:{T.BADTX}'>{len(self._issues)} 次</b>没播好。", ("one", sid), link=sid is not None)
        else:
            names = "、".join(html.escape(n) for n in songs)
            self.bubble.show_message(
                f"<b style='color:{T.BADTX}'>{len(songs)} 首</b>没播好：{names}。", ("all", None))

    def _bubble_clicked(self, payload):
        if payload and payload[0] == "say":           # 临时的话：点掉就收起；还有坏消息就接着显示
            self._show_issues()
            return
        self._issues.clear()
        kind, tid = payload if payload else ("one", None)
        if kind == "all":
            self.jump_to_history(None, all_songs=True)
        elif tid:
            self.jump_to_history(tid)

    def _on_event(self, ev: dict):
        kind = ev.get("kind")
        s = self.s
        if kind == "say":                             # 11-02 会话想让雨澜说一句（暂停没停成等）
            self._say(str(ev.get("text") or ""))
            return
        if kind == "finished":
            rec = ev["rec"]
            res, name = rec["result"], html.escape(rec["name"])
            dur = history.fmt_dur(rec["dur"])
            missed = rec.get("missed") or []
            if res == history.PARTIAL:
                names = "、".join(html.escape(_short(m["name"])) for m in missed[:3]) + ("…" if len(missed) > 3 else "")
                self._issue(rec["name"], f"<b style='color:{T.BADTX}'>{name}</b>：{len(missed)} 句没做完。{names}。",
                            rec["id"])
            elif res == history.ABNORMAL:
                self._issue(rec["name"], f"<b style='color:{T.BADTX}'>{name}</b>：异常退出。", rec["id"])
            elif res == history.UNMET:
                self._issue(rec["name"], f"<b style='color:{T.BADTX}'>{name}</b>：没达成。"
                                         f"{html.escape(_stop(rec.get('outcome') or ''))}", rec["id"])
            if s.notify and res != history.STOPPED:
                if res == history.OK:
                    notify(f"{rec['name']} 已完成", f"{rec.get('steps') or ''} 项全部完成 · 耗时 {dur}".strip())
                elif res == history.DONE:
                    oc = str(rec.get("outcome") or "")
                    notify(f"{rec['name']} 已完成", (f"{oc} · " if oc else "") + f"耗时 {dur}")
                elif res == history.UNMET:
                    notify(f"{rec['name']} 没达成目标", f"{rec.get('outcome') or ''} · 耗时 {dur}")
                elif res == history.PARTIAL:
                    m = missed[0]
                    head = (f"第 {m['no']:02d} 项 " if m.get("no") else "") + str(m["name"])
                    more = f" 等 {len(missed)} 项" if len(missed) > 1 else ""
                    notify(f"{rec['name']} 有 {len(missed)} 项未完成", f"{head}：{m['reason']}{more} · 耗时 {dur}")
                else:
                    notify(f"{rec['name']} 异常退出", f"退出码 {rec.get('exit')} · 耗时 {dur}")
        elif kind == "nostart":
            name = html.escape(ev["name"])
            self._issue(ev["name"], f"<b style='color:{T.BADTX}'>{name}</b>：播不了。{html.escape(_stop(ev['reason']))}", None)
            if ev.get("manual"):
                dialogs.warn(self, self.logo, f"{ev['name']} 播不了", ev["reason"])
            elif s.notify:
                notify(f"{ev['name']} 没能启动", ev["reason"])
        elif kind == "round" and s.notify:
            recs = ev["recs"]
            good = sum(1 for r in recs if r["result"] in (history.OK, history.DONE))
            part = sum(1 for r in recs if r["result"] in (history.PARTIAL, history.UNMET))
            crash = sum(1 for r in recs if r["result"] == history.ABNORMAL)
            stop = sum(1 for r in recs if r["result"] == history.STOPPED)
            bits = [f"{good} 个完成"] if good else []
            if part:
                bits.append(f"{part} 个有未完成项")
            if crash:
                bits.append(f"{crash} 个异常退出")
            if stop:
                bits.append(f"{stop} 个手动停止")
            if ev.get("fail"):
                bits.append(f"{ev['fail']} 个没能启动")
            notify(f"顺序运行结束（共 {ev['total']} 个）", "，".join(bits) + f" · 总耗时 {history.fmt_dur(ev['dur'])}")

    # ------------------------------------------------------------ 设备 / 监控
    # 11-01 设计三：设备状态不单独显示了。启动时后台自检一遍：有问题「环境自检」按钮变红边 + 提示一句；
    # 设备比例和脚本基准（2400x1504，16:10）不一样就弹窗问要不要切换。启动时没连设备、后来才连上的，连上时问一次。
    startup_check = True        # 离线测试、渲染设计稿时关掉（不碰设备、不弹窗）

    def _on_device(self, dev, size: str):
        was = self._dev
        self._dev, self._dev_size = dev, size
        self._mark_check()
        if dev and not was and self.startup_check and getattr(self, "_startup_done_once", False):
            serial = dev.get("sn") or dev.get("serial")      # 11-17：插线、无线算同一台，只问一次
            if serial and serial not in self._ratio_asked:
                self.s.bg(env.wm_size, lambda sz, sr=serial: self._ask_ratio(sz, sr))

    def _mark_check(self):
        bad = getattr(self, "_check_bad", [])
        if self._dev is None:
            tip, red = "没连上设备：数据线连好或打开无线调试（点开看全部自检项）", True
        else:
            tip = f"设备 {self._dev.get('label') or self._dev.get('serial', '?')} · 分辨率 {getattr(self, '_dev_size', '?')}"
            tip += f"\n上次自检 {len(bad)} 项有问题，点开看" if bad else "\n上次自检全部通过"
            red = bool(bad)
        self.btn_check.set_kind("dan" if red else "def")
        self.btn_check.setToolTip(tip)

    def _open_selfcheck(self):
        d = dialogs.SelfCheckDialog(self, self.s)
        d.done_check.connect(self._check_result)
        d.exec()

    def _check_result(self, results):
        if isinstance(results, list):
            self._check_bad = [t for ok, t in results if not ok]
            self._mark_check()

    def _startup_check(self):
        if not self.startup_check:
            return
        tools = list(self.s.tools)

        def work():
            res = env.check_environment(tools)
            return res, (env.wm_size() if env.device_online() else None)
        self.s.bg(work, self._startup_result)

    def _startup_result(self, out):
        self._startup_done_once = True
        if isinstance(out, Exception):
            return
        res, size = out
        self._check_result(res)
        if self._check_bad:
            self.show_toast(f"环境自检：{len(self._check_bad)} 项有问题，点「环境自检」看", bad=True)
        if size:
            self._ask_ratio(size, (self._dev or {}).get("sn") or (self._dev or {}).get("serial") or "?")

    def _ask_ratio(self, size: str, serial: str):
        """设备比例和脚本基准不一样：问要不要切到 2400x1504（退出时会自动还原）。同一台设备这次只问一次。"""
        self._ratio_asked.add(serial)
        if not isinstance(size, str) or env.ratio_ok(size):
            return
        if dialogs.confirm_resolution(self, self.logo, size, env.RES_BASE):
            self.set_res(env.RES_BASE)

    def set_res(self, mode: str):
        def done(ok):
            good = ok is True
            self.show_toast(f"分辨率已设置: {mode}" if good else "分辨率没设置成（设备未连接？）")
            self.devwatch.poke()
        self.s.bg(lambda: env.set_resolution(mode) if env.device_online() else False, done)

    def _set_monitor(self, on: bool):
        """11-02 作者：监控不单独弹窗——打开后歌单收成一行（正在播的那首），设备画面占满歌单这一栏下面；不做展开动画。"""
        self._mon_on = on
        self.btn_mon.set_kind("cy" if on else "def")
        self.mon_view.set_on(on)
        self.mon_view.setVisible(on)
        self.monitor.set_on(on)
        self._rebuild_rows()

    # ------------------------------------------------------------ 事件
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            if obj is self.pname and self.s.cur:
                self.s.select(self.s.cur.id)
                return True
            if self.qpop.isVisible() and isinstance(obj, QWidget) and obj.window() is self:
                if not (obj is self.qpop or self.qpop.isAncestorOf(obj) or obj is self.disc):
                    self._close_queue()
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast_lb.isVisible():
            self._place_toast()
        if self.bubble.isVisible():
            self.bubble.reposition()
        if self.qpop.isVisible():
            self._close_queue()
        if self._detail_id and not self._desc_open:
            QTimer.singleShot(0, self._check_fold_btn)
        QTimer.singleShot(0, self._fit_desc)

    def closeEvent(self, e):
        if self._quitting:
            e.accept()
            return
        e.ignore()
        self.quit_all()

    def quit_all(self):
        """退出(停止全部): 确认后在后台停工具、还原分辨率；adb 卡住也不会关不掉窗口。"""
        if self._quitting:
            return
        if not dialogs.confirm_quit(self, self.s, self.logo):
            return
        self._quitting = True
        self.show_toast("正在停止全部并还原环境…")
        self.setEnabled(False)
        self.monitor.set_on(False)
        self.devwatch.stop()
        self.s.shutdown(self._finish_quit)
        QTimer.singleShot(15000, self._finish_quit)   # 兜底: 最多等 15 秒

    def _finish_quit(self):
        if getattr(self, "_quit_done", False):
            return
        self._quit_done = True
        self._on_quit()
        QApplication.instance().quit()
