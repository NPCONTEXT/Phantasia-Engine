"""两个浮在主窗口上的小部件: 雨澜的事件气泡 / 待播浮层。都是主窗口内部的子部件，不是独立窗口。"""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme as T
from .widgets import AA, C, NoPen, PillButton, sticker, MIME_QUEUE, MIME_TOOL, drag_tid, start_drag


def _tail(p: QPainter, cx: float, by: float, shadow: float = 3.0):
    """朝下的小尖角: 先画投影，再画白色三角并描两条斜边，最后把卡片底边那一小段盖成白色。"""
    tri = QPolygonF([QPointF(cx - 8.5, by - 1), QPointF(cx + 8.5, by - 1), QPointF(cx, by + 7.5)])
    p.setPen(NoPen)
    p.setBrush(C(T.INK))
    p.drawPolygon(tri.translated(0, shadow))
    p.setBrush(C("#FFFFFF"))
    p.drawPolygon(tri)
    pen = QPen(C(T.INK), 2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(cx - 8.5, by - 1), QPointF(cx, by + 7.5), QPointF(cx + 8.5, by - 1)]))
    p.fillRect(QRectF(cx - 7, by - 2.4, 14, 2.6), C("#FFFFFF"))


TAG = "雨澜"          # 气泡名牌上的名字（11-14 幻想引擎：对外的名字是引擎，出面说话的是雨澜）


class EventBubble(QWidget):
    """雨澜的事件气泡: 平时没有；只有「播不了」和「有步骤没完成 / 异常退出」才从播放栏封面上方冒出来，
    一直停到被点掉为止。点一下 = 收起，并跳到那个工具的「播放记录」。"""
    clicked = Signal(object)   # 带回 show_message 时给的 payload

    TOP, BOTTOM = 11, 12

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._payload = None
        self._tag_bg = T.BLUSH
        self.anchor = None       # 11-02 平衡-丙：() -> (封面中心横坐标, 播放栏上沿)；没有就按旧的左下角
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, self.TOP + 14, 16, self.BOTTOM + 9)
        lay.setSpacing(14)
        self.msg = QLabel()
        self.msg.setFont(T.font(14))
        self.msg.setTextFormat(Qt.TextFormat.RichText)
        self.msg.setStyleSheet(f"color:{T.INK};background:transparent;")
        self.go = QLabel("看播放记录 ›")
        self.go.setFont(T.font(12, 700))
        self.go.setStyleSheet(f"color:{T.LAVTX};background:transparent;")
        lay.addWidget(self.msg)
        lay.addWidget(self.go)
        self.hide()

    def show_message(self, html: str, payload=None, link: bool = True, tag_bg: str = T.BLUSH):
        """新的气泡顶掉旧的（一次只有一句）。"""
        self._payload = payload
        self._tag_bg = tag_bg
        self.go.setVisible(link)
        self.msg.setWordWrap(False)
        self.msg.setMinimumWidth(0)
        self.msg.setMaximumWidth(16777215)
        self.msg.setText(html)
        self.reposition()
        self.show()
        self.raise_()

    def reposition(self):
        par = self.parentWidget()
        if par is None:
            return
        ax, ay = self.anchor() if self.anchor else (48, par.height() - 72)
        x0 = max(14, ax - 34)            # 尖角画在气泡左边 34 处，对准封面中心
        max_w = par.width() - x0 - 14
        self.msg.setWordWrap(False)
        self.msg.setFixedWidth(self.msg.sizeHint().width())
        self.adjustSize()
        if self.width() > max_w:   # 一句话比窗口还宽: 折行，向上长高，尖角位置不变
            extra = self.width() - self.msg.width()
            self.msg.setWordWrap(True)
            self.msg.setFixedWidth(max(120, max_w - extra))
            self.adjustSize()
        self.move(x0, ay - 12 + self.BOTTOM - self.height())

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.hide()
            self.clicked.emit(self._payload)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        body = QRectF(0, self.TOP, self.width(), self.height() - self.TOP - self.BOTTOM)
        sticker(p, body, 16, "#FFFFFF", shadow=3)
        _tail(p, 34, body.bottom())
        f = T.font(12, 800)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(TAG)
        tag = QRectF(14, 0, tw + 20, 20)
        sticker(p, tag, 10, self._tag_bg)
        p.setPen(C(T.INK))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, TAG)


class _SeqDot(QWidget):
    def __init__(self, n: int, parent=None):
        super().__init__(parent)
        self._n = n
        self.setFixedSize(24, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        sticker(p, QRectF(0, 0, 24, 24), 12, T.PINK)
        p.setPen(C(T.INK))
        p.setFont(T.font(12, 800))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._n))


class _QRow(QWidget):
    """待播列表的一行：按住拖动调顺序（11-02 起代替上移 / 下移按钮），✕ 移出。"""

    def __init__(self, tid: str, parent=None):
        super().__init__(parent)
        self.tid = tid
        self._press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        start_drag(self, MIME_QUEUE, self.tid)

    def mouseReleaseEvent(self, e):
        self._press = None


class QueuePopup(QWidget):
    """待播浮层: 列出这一轮还没播的工具，从上到下就是接下来的顺序；拖动调顺序、✕ 移出；
    11-02 起也接住从歌单拖来的歌（放在哪就插在哪）。空的时候写怎么加。"""
    TOP, BOTTOM = 0, 12

    def __init__(self, parent: QWidget, session):
        super().__init__(parent)
        self.s = session
        self._anchor_x = 0   # 尖角要指向的横坐标（11-02 起是播放区封面的中心；以前是「待播 N」贴纸）
        self._anchor_y = None
        self._tail_x = 0.0
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(12, 12, 12, self.BOTTOM + 8)
        self.box.setSpacing(0)
        self._qrows: list[_QRow] = []
        self._ind_y: float | None = None     # 拖动时插入位置那条线
        self.setAcceptDrops(True)
        self.hide()

    def open_at(self, anchor_x: int, anchor_y: int | None = None):
        self._anchor_x = anchor_x
        self._anchor_y = anchor_y       # 11-02 平衡-丙：播放栏上沿（播放栏挪进右栏后不再贴着窗口底）
        self.rebuild()
        self.show()
        self.raise_()

    def rebuild(self):
        while self.box.count():
            it = self.box.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        q = list(self.s.queue)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(4, 0, 4, 6)
        hl.setSpacing(8)
        hl.addWidget(T.label(f"待播 {len(q)} 首", 13, 800))
        hl.addWidget(T.label("从上到下依次播放 · 拖动调顺序", 12, 400, T.INK3))
        hl.addStretch(1)
        self.box.addWidget(head)
        self._qrows = []
        if not q:
            tip = T.label("还没有待播：把歌单里的歌拖到封面上，或者直接拖到这里", 13, 400, T.INK3)
            tip.setContentsMargins(4, 8, 4, 8)
            self.box.addWidget(tip)
        base = len(self.s.round_done) + (1 if (self.s.cur or self.s.pending) else 0)
        for i, tid in enumerate(q):
            tool = self.s.tool(tid) or {}
            row = _QRow(tid)
            row.setObjectName("qrow")
            if i:
                row.setStyleSheet(f"#qrow{{border-top:1.5px solid {T.LINE};}}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 6, 4, 6)
            rl.setSpacing(6)
            rl.addWidget(_SeqDot(base + i + 1))
            name = T.label(tool.get("name", tid), 14, 700)
            name.setContentsMargins(4, 0, 2, 0)
            rl.addWidget(name, 1)
            grip = T.label("⠿", 16, 700, T.INK3)
            grip.setToolTip("按住拖动调顺序")
            rl.addWidget(grip)
            rm = PillButton("", "dan", True, "x", tip="移出待播：这一轮不播它")
            rm.clicked.connect(lambda _=False, t=tid: self.s.queue_remove(t))
            rl.addWidget(rm)
            self.box.addWidget(row)
            self._qrows.append(row)
        # 浮层已经显示时，新加进来的子部件要到下一轮事件循环才会显示；这里先显示出来，尺寸才算得对
        for i in range(self.box.count()):
            w = self.box.itemAt(i).widget()
            if w is not None:
                w.show()
        self.box.activate()
        self.reposition()

    def reposition(self):
        par = self.parentWidget()
        if par is None:
            return
        self.setMinimumWidth(392)
        self.adjustSize()
        x = self._anchor_x - 44          # 11-02：从封面往右展开（以前从右下角「待播 N」往左展开）
        x = max(8, min(x, par.width() - self.width() - 8))
        top = self._anchor_y if self._anchor_y is not None else par.height() - 72
        self.move(x, top - 6 - self.height() + self.BOTTOM - 8)
        self._tail_x = max(24.0, min(float(self._anchor_x - x), self.width() - 24.0))
        self.update()

    def mousePressEvent(self, e):
        e.accept()

    # ---------- 拖动（11-02）：从歌单拖来 = 插进待播；待播里拖 = 调顺序；拖动不打断正在播的 ----------
    def drop_index(self, y: float) -> int:
        for i, r in enumerate(self._qrows):
            if y < r.geometry().center().y():
                return i
        return len(self._qrows)

    def _ind_for(self, i: int) -> float:
        if not self._qrows:
            return float(self.box.itemAt(0).widget().geometry().bottom() + 4)
        if i < len(self._qrows):
            return float(self._qrows[i].geometry().top())
        return float(self._qrows[-1].geometry().bottom())

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_TOOL) or e.mimeData().hasFormat(MIME_QUEUE):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_TOOL) or e.mimeData().hasFormat(MIME_QUEUE):
            e.acceptProposedAction()
            self._ind_y = self._ind_for(self.drop_index(e.position().y()))
            self.update()

    def dragLeaveEvent(self, e):
        self._ind_y = None
        self.update()

    def dropEvent(self, e):
        i = self.drop_index(e.position().y())
        self._ind_y = None
        self.update()
        # 拖动结束后再改（改完会重建这些行，被拖的那一行就在里面）
        tid = drag_tid(e, MIME_QUEUE)
        if tid:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.s.queue_move_to(tid, i))
            return
        tid = drag_tid(e, MIME_TOOL)
        if tid:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.s.enqueue(tid, i))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        body = QRectF(0, 0, self.width(), self.height() - self.BOTTOM)
        sticker(p, body, 14, "#FFFFFF", shadow=3)
        _tail(p, self._tail_x, body.bottom())
        if self._ind_y is not None:          # 插入位置：一条粉线（静止）
            p.setPen(QPen(C(T.PINKD), 3))
            p.drawLine(QPointF(16, self._ind_y), QPointF(self.width() - 16, self._ind_y))
