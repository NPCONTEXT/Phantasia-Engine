"""自绘小组件: 卡片 / 按钮 / 开关 / 序号贴纸 / 播放圆钮 / 封面 / 分段进度 / 状态贴纸 / 页签 等"""
import math
import re
import time

from PySide6.QtCore import QMimeData, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QDrag, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy,
                               QTextEdit, QWidget)

from . import theme as T

C = T.C
NoPen = Qt.PenStyle.NoPen
AA = QPainter.RenderHint.Antialiasing


# ---------------------------------------------------------------- 动画时钟
class Anim(QObject):
    """全局动画时钟: 一个定时器带所有会动的小组件；系统开了「减少动态效果」就不动。"""
    tick = Signal()
    _inst = None

    def __init__(self):
        super().__init__()
        self.reduce = T.reduce_motion()
        self._frozen: float | None = None    # 11-02 暂停：时钟停在这一刻
        self._offset = 0.0                   # 累计停了多久，放开后从停下的地方接着动
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.tick)
        if not self.reduce:
            self._timer.start()

    def freeze(self, on: bool):
        """11-02 暂停（作者：专辑、歌单的按钮都停止旋转）：时钟停住，所有动画停在这一帧；放开后接着动。"""
        if on and self._frozen is None:
            self._frozen = time.monotonic()
            self.tick.emit()
            self._timer.stop()
        elif not on and self._frozen is not None:
            self._offset += time.monotonic() - self._frozen
            self._frozen = None
            if not self.reduce:
                self._timer.start()

    @property
    def frozen(self) -> bool:
        return self._frozen is not None

    @classmethod
    def get(cls) -> "Anim":
        if cls._inst is None:
            cls._inst = Anim()
        return cls._inst

    def now(self) -> float:
        if self.reduce:
            return 0.0
        return (self._frozen if self._frozen is not None else time.monotonic()) - self._offset


# ---------------------------------------------------------------- 画图小工具
def sticker(p: QPainter, r: QRectF, rad: float, fill, border=T.INK, bw: float = 2.0,
            shadow: float = 0.0, shadow_color=None):
    """贴纸块: 纯色底 + 描边 + 正下方不带模糊的硬投影。"""
    p.setPen(NoPen)
    if shadow:
        p.setBrush(C(shadow_color or border))
        p.drawRoundedRect(r.translated(0, shadow), rad, rad)
    if bw:
        p.setBrush(C(border))
        p.drawRoundedRect(r, rad, rad)
    p.setBrush(fill if isinstance(fill, QColor) else C(fill))
    p.drawRoundedRect(r.adjusted(bw, bw, -bw, -bw), max(0.0, rad - bw), max(0.0, rad - bw))


def paint_tick(p: QPainter, cx: float, cy: float, s: float, color, w: float = 2.5):
    pen = QPen(C(color), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(cx - 0.42 * s, cy + 0.02 * s), QPointF(cx - 0.12 * s, cy + 0.32 * s),
                              QPointF(cx + 0.42 * s, cy - 0.34 * s)]))


EQ_STILL = 0.45    # 11-02 作者：小音柱都静止——歌单那一行、歌词当前句的音柱固定画这一帧

# 11-02 拖动（作者：「直接改成拖动左侧清单到待播列表里…顺便把上下调整清单改成鼠标拖动，这样整个设计又统一了」）
MIME_TOOL = "application/x-phantasia-tool"      # 从歌单拖出来的一首（内容 = 工具 id）
MIME_QUEUE = "application/x-phantasia-queue"    # 待播列表里拖动的一首


def start_drag(src: QWidget, mime: str, tid: str):
    """拖起来：带着这一行的样子（半透明）走，放下由目标处理；拖动本身不改任何东西。"""
    md = QMimeData()
    md.setData(mime, tid.encode("utf-8"))
    drag = QDrag(src)
    drag.setMimeData(md)
    pm = src.grab()
    if not pm.isNull():
        faded = QPixmap(pm.size())
        faded.setDevicePixelRatio(pm.devicePixelRatio())
        faded.fill(Qt.GlobalColor.transparent)
        p = QPainter(faded)
        p.setOpacity(0.75)
        p.drawPixmap(0, 0, pm)
        p.end()
        drag.setPixmap(faded)
        drag.setHotSpot(QPoint(24, pm.height() // max(1, int(pm.devicePixelRatio())) // 2))
    drag.exec(Qt.DropAction.MoveAction)


def drag_tid(e, mime: str) -> str | None:
    md = e.mimeData()
    return bytes(md.data(mime)).decode("utf-8") if md.hasFormat(mime) else None


def paint_eq(p: QPainter, x: float, bottom: float, color, t: float):
    """跳动的音柱 =「正在播放」的统一记号。三根竖条，宽 13 高 12。"""
    p.setPen(NoPen)
    p.setBrush(C(color))
    for i, (h, delay) in enumerate(((7, 0.3), (12, 0.6), (9, 0.1))):
        k = 0.7 - 0.3 * math.cos(2 * math.pi * ((t + delay) / 0.9))
        hh = h * k
        p.drawRoundedRect(QRectF(x + i * 5, bottom - hh, 3, hh), 1, 1)


def paint_stripes(p: QPainter, r: QRectF, rad: float, t: float):
    """深粉 / 浅粉竖条纹，向右平移。"""
    path = QPainterPath()
    path.addRoundedRect(r, rad, rad)
    p.save()
    p.setClipPath(path)
    p.setPen(NoPen)
    p.fillRect(r, C(T.PINK))
    off = (t / 0.7 * 12) % 12
    x = r.left() - 12 + off
    p.setBrush(C(T.PINKD))
    while x < r.right():
        p.drawRect(QRectF(x, r.top(), 6, r.height()))
        x += 12
    p.restore()


def paint_triangle(p: QPainter, cx: float, cy: float, w: float, h: float, color, direction: str = "right"):
    p.setPen(NoPen)
    p.setBrush(C(color))
    if direction == "right":
        pts = [QPointF(cx - w / 2, cy - h / 2), QPointF(cx - w / 2, cy + h / 2), QPointF(cx + w / 2, cy)]
    elif direction == "up":
        pts = [QPointF(cx - w / 2, cy + h / 2), QPointF(cx + w / 2, cy + h / 2), QPointF(cx, cy - h / 2)]
    else:
        pts = [QPointF(cx - w / 2, cy - h / 2), QPointF(cx + w / 2, cy - h / 2), QPointF(cx, cy + h / 2)]
    p.drawPolygon(QPolygonF(pts))


def round_pixmap(path: str, size: int, dpr: float) -> QPixmap:
    """把图片裁成圆形头像（按屏幕倍率出图，Retina 上不糊）。"""
    px = max(1, int(round(size * dpr)))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    src = QPixmap(path)
    p = QPainter(out)
    p.setRenderHints(AA | QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, px, px))
    p.setClipPath(clip)
    if src.isNull():
        p.fillRect(0, 0, px, px, C(T.PINK))
    else:
        s = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((px - s.width()) // 2, (px - s.height()) // 2, s)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


# ---------------------------------------------------------------- 贴纸卡片
class Card(QWidget):
    """贴纸卡片: 白底、2px 描边、圆角 16、正下方 3px 硬投影。"""
    SHADOW = 3

    def __init__(self, pad: int = 16, parent=None):
        super().__init__(parent)
        self.pad = pad
        self.setContentsMargins(pad, pad, pad, pad + self.SHADOW)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        sticker(p, QRectF(0, 0, self.width(), self.height() - self.SHADOW), 16, "#FFFFFF", shadow=self.SHADOW)


class GridBg(QWidget):
    """窗口底纹: 银白底 + 20px 间距的浅蓝方格线。"""

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), C(T.CREAM))
        p.setPen(QPen(C(T.PINK, 0.18), 1))
        w, h = self.width(), self.height()
        for x in range(0, w, 20):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 20):
            p.drawLine(0, y, w, y)


# ---------------------------------------------------------------- 按钮
class PillButton(QAbstractButton):
    """胶囊按钮: 高 30（小号 26），2px 硬投影；悬停上移 1px，按下下移 2px 且投影消失。
    kind: def 白 / pri 粉 / cy 青 / dan 红字红边；icon: sq 方块 / next 下一首 / play 三角 / up / down / x"""
    KINDS = {
        "def": ("#FFFFFF", "#DCE7FB", T.PINKD, T.INK, T.INK),
        "pri": (T.PINK, "#DCE7FB", T.PINKD, T.INK, T.INK),
        "cy": (T.CYAN, "#A9F0F7", "#4FD3E0", T.INK, T.INK),
        "dan": ("#FFFFFF", T.BADBG, "#F7B9C4", T.BADTX, T.BAD),
    }

    def __init__(self, text: str = "", kind: str = "def", small: bool = False, icon: str | None = None,
                 tip: str | None = None, parent=None):
        super().__init__(parent)
        self.kind, self.small, self.icon = kind, small, icon
        self._h = 26 if small else 30
        self.setText(text)
        self.setFont(T.font(12 if small else 13, 700))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if tip:
            self.setToolTip(tip)

    def set_kind(self, kind: str):
        self.kind = kind
        self.update()

    def setText(self, text: str):
        super().setText(text)
        self.updateGeometry()
        self.update()

    def _icon_w(self) -> float:
        return {"sq": 10, "next": 12, "play": 8, "up": 10, "down": 10, "x": 10}.get(self.icon or "", 0)

    def sizeHint(self) -> QSize:
        if not self.text():
            return QSize(self._h, self._h + 4)
        fm = QFontMetricsF(self.font())
        pad = 10 if self.small else 13
        w = pad * 2 + fm.horizontalAdvance(self.text())
        if self.icon:
            w += self._icon_w() + 6
        return QSize(math.ceil(w), self._h + 4)

    minimumSizeHint = sizeHint

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        en = self.isEnabled()
        down = self.isDown() and en
        hov = self.underMouse() and en and not down
        bg, bg_hov, bg_down, fg, bd = self.KINDS[self.kind]
        dy = 1 + (2 if down else (-1 if hov else 0))
        sh = 0 if (down or not en) else (3 if hov else 2)
        if not en:
            p.setOpacity(0.42)
        r = QRectF(0, dy, self.width(), self._h)
        sticker(p, r, self._h / 2, bg_down if down else (bg_hov if hov else bg), bd, 2, sh)
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        iw = self._icon_w()
        total = tw + iw + (6 if (iw and tw) else 0)
        x = r.left() + (r.width() - total) / 2
        cy = r.center().y()
        if self.icon:
            self._paint_icon(p, x, cy, fg)
            x += iw + 6
        if self.text():
            p.setPen(C(fg))
            p.setFont(self.font())
            p.drawText(QRectF(x, r.top(), tw + 2, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

    def _paint_icon(self, p: QPainter, x: float, cy: float, fg):
        ic = self.icon
        if ic == "sq":
            p.setPen(NoPen)
            p.setBrush(C(fg))
            p.drawRoundedRect(QRectF(x, cy - 5, 10, 10), 2, 2)
        elif ic == "play":
            paint_triangle(p, x + 4, cy, 8, 10, fg)
        elif ic == "next":
            paint_triangle(p, x + 4, cy, 8, 10, fg)
            p.drawRoundedRect(QRectF(x + 9, cy - 5, 3, 10), 1, 1)
        elif ic == "up":
            paint_triangle(p, x + 5, cy, 10, 7, fg, "up")
        elif ic == "down":
            paint_triangle(p, x + 5, cy, 10, 7, fg, "down")
        elif ic == "x":
            pen = QPen(C(T.BADTX), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x + 1, cy - 4), QPointF(x + 9, cy + 4))
            p.drawLine(QPointF(x + 9, cy - 4), QPointF(x + 1, cy + 4))


class Switch(QAbstractButton):
    """开关: 开 = 青色；小号放在页签栏右端。"""

    def __init__(self, small: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self._w, self._hh = (32, 18) if small else (38, 22)
        self.setFixedSize(self._w, self._hh)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        paint_switch(p, QRectF(0, 0, self._w, self._hh), self.isChecked())


def paint_switch(p: QPainter, r: QRectF, on: bool):
    sticker(p, r, r.height() / 2, T.CYAN if on else "#DDE2EC")
    d = r.height() - 6
    x = r.right() - 3 - d if on else r.left() + 3
    sticker(p, QRectF(x, r.top() + 3, d, d), d / 2, "#FFFFFF")


class OptSwitch(QAbstractButton):
    """顶栏的「完成通知」胶囊: 文字 + 开关，点哪里都能切换。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setFont(T.font(13, 600))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(math.ceil(12 + fm.horizontalAdvance(self.text()) + 7 + 38 + 10), 34)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(0, 1, self.width(), 30)
        sticker(p, r, 15, T.CYANBG if self.isChecked() else "#FFFFFF")
        p.setPen(C(T.INK))
        p.setFont(self.font())
        p.drawText(QRectF(12, 1, self.width() - 60, 30), Qt.AlignmentFlag.AlignVCenter, self.text())
        paint_switch(p, QRectF(self.width() - 10 - 38, 5, 38, 22), self.isChecked())


class LinkToggle(QAbstractButton):
    """「展开说明 / 收起说明」小字按钮（薰衣草紫 + 小箭头）。"""

    def __init__(self, text_off: str, text_on: str, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self._t = (text_off, text_on)
        self.setFont(T.font(12, 700))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(math.ceil(max(fm.horizontalAdvance(t) for t in self._t) + 18), 20)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        txt = self._t[1 if self.isChecked() else 0]
        p.setFont(self.font())
        p.setPen(C(T.LAVTX))
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(txt)
        p.drawText(QRectF(0, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, txt)
        pen = QPen(C(T.LAVTX), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = tw + 10, self.height() / 2
        if self.isChecked():   # 已展开: 箭头朝上
            p.drawPolyline(QPolygonF([QPointF(cx - 4, cy + 2), QPointF(cx, cy - 2), QPointF(cx + 4, cy + 2)]))
        else:
            p.drawPolyline(QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)]))


# ---------------------------------------------------------------- 状态贴纸 / 徽标
class StatusTag(QWidget):
    """状态贴纸: run 正在播放 / wait 待播 / ok / bad / stop 手动停止 / idle 空闲。总是带形状，不单靠颜色。"""
    STY = {"run": (T.PINKBG, T.PINKD, T.PINKTX), "wait": (T.LAVBG, T.LAV, T.LAVTX),
           "ok": (T.OKBG, T.OK, T.OKTX), "bad": (T.BADBG, "#EE8FA0", T.BADTX),
           "stop": (T.GOLDBG, T.GOLD, T.GOLDTX), "idle": (T.GREY, T.GREYLINE, T.INK2)}

    def __init__(self, kind: str = "idle", text: str = "", min_w: int = 0, parent=None):
        super().__init__(parent)
        self._kind, self._text, self._min_w = kind, text, min_w
        self.setFont(T.font(12, 700))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        Anim.get().tick.connect(self._tick)

    def set(self, kind: str, text: str):
        if (kind, text) != (self._kind, self._text):
            self._kind, self._text = kind, text
            self.updateGeometry()
            self.update()

    def _tick(self):
        if self._kind == "run" and self.isVisible():
            self.update()

    def _inner_w(self) -> float:
        fm = QFontMetricsF(self.font())
        return 7 + (13 if self._kind == "run" else 10) + 5 + fm.horizontalAdvance(self._text) + 9

    def sizeHint(self) -> QSize:
        return QSize(math.ceil(max(self._inner_w(), self._min_w)), 22)

    minimumSizeHint = sizeHint

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        bg, bd, fg = self.STY[self._kind]
        r = QRectF(0, 0, self.width(), 22)
        sticker(p, r, 11, bg, bd, 1.5)
        x = (self.width() - self._inner_w()) / 2 + 7
        cy = 11.0
        k = self._kind
        iw = 10
        if k == "run":
            paint_eq(p, x, cy + 6, fg, Anim.get().now())
            iw = 13
        elif k == "wait":
            p.setPen(QPen(C(fg), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x + 1, cy - 4, 8, 8))
        elif k == "ok":
            paint_tick(p, x + 5, cy, 9, fg, 2)
        elif k == "bad":
            p.setPen(NoPen)
            p.setBrush(C(T.BAD))
            p.drawRoundedRect(QRectF(x + 3, cy - 5, 4, 10), 2, 2)
        elif k == "stop":
            p.setPen(NoPen)
            p.setBrush(C(fg))
            p.drawRoundedRect(QRectF(x + 1, cy - 4, 8, 8), 2, 2)
        else:
            p.setPen(NoPen)
            p.setBrush(C("#B7C0D2"))
            p.drawEllipse(QRectF(x + 1, cy - 4, 8, 8))
        p.setPen(C(fg))
        p.setFont(self.font())
        p.drawText(QRectF(x + iw + 5, 0, self.width(), 22), Qt.AlignmentFlag.AlignVCenter, self._text)


def badge(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setFont(T.font(12, 600))
    lb.setStyleSheet(f"background:{T.GREY};border:1.5px solid {T.LINE};border-radius:8px;"
                     f"color:{T.INK2};padding:1px 8px;")
    lb.setFixedHeight(22)
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lb


def notice_badge(text: str = "") -> QLabel:
    """金色提示徽标（例如「限时活动已结束」）。"""
    lb = QLabel(text)
    lb.setFont(T.font(12, 700))
    lb.setStyleSheet(f"background:{T.GOLDBG};border:1.5px solid {T.GOLD};border-radius:8px;"
                     f"color:{T.GOLDTX};padding:1px 8px;")
    lb.setFixedHeight(22)
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lb


def num_pill(text: str = "") -> QLabel:
    lb = QLabel(text)
    lb.setFont(T.font(12, 700))
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setStyleSheet(f"background:{T.INK};color:#FFFFFF;border-radius:9px;padding:0 5px;")
    lb.setFixedHeight(18)
    lb.setMinimumWidth(20)
    return lb


def wait_tag() -> QLabel:
    lb = QLabel("待播")
    lb.setFont(T.font(12, 700))
    lb.setStyleSheet(f"background:{T.LAVBG};border:1.5px solid {T.LAV};border-radius:9px;"
                     f"color:{T.LAVTX};padding:0 6px;")
    lb.setFixedHeight(18)
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lb.setToolTip("待播 = 排队中：前面的播放完就轮到它")
    return lb


class ClickTag(QLabel):
    """能点的小标签：按下自己接住（不传给歌单行，不会选中那行、也不会拖起来），在标签上松开才算点了。"""
    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def check_tag() -> ClickTag:
    # 11-03 作者：「待验收藏得太深了，需要你加一个左侧列表点击待验收，自动跳转到对应播放记录区域」，挑了版本二（加「›」、到了描金框）
    lb = ClickTag("待验收 ›")
    lb.setFont(T.font(12, 700))
    lb.setStyleSheet(f"background:{T.GOLDBG};border:1.5px solid {T.GOLD};border-radius:9px;"
                     f"color:{T.GOLDTX};padding:0 6px;")
    lb.setFixedHeight(18)
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lb.setToolTip("待验收 = 最近一次结束时留了截图还没看过：点一下跳到这首的播放记录，看图、验收")
    return lb


# ---------------------------------------------------------------- 歌单行的两个零件
class SeqBadge(QWidget):
    """行首勾选位: box 没勾 / tick 勾了但不在这一轮 / num 序号 / eq 正在播放 / fin 这一轮已完成 / err 有问题"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode, self._num = "box", 0
        self._dim = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(T.font(12, 800))
        Anim.get().tick.connect(self._tick)

    def set(self, mode: str, num: int = 0):
        if (mode, num) != (self._mode, self._num):
            self._mode, self._num = mode, num
            self.update()

    def set_dim(self, on: bool):
        """半透明（和不可用的胶囊按钮一样淡）：表示现在点了不改，例如正在播放时歌词里的子项勾选框。"""
        if on != self._dim:
            self._dim = on
            self.setCursor(Qt.CursorShape.ArrowCursor if on else Qt.CursorShape.PointingHandCursor)
            self.update()

    def _tick(self):
        pass                 # 11-02 音柱静止，不用跟着时钟重画

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def mousePressEvent(self, e):
        e.accept()   # 不让这次点击再去「选中这一行」

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if self._dim:
            p.setOpacity(0.42)
        m = self._mode
        if m in ("box", "tick"):
            r = QRectF(4, 4, 20, 20)
            sticker(p, r, 7, T.PINK if m == "tick" else "#FFFFFF")
            if m == "tick":
                paint_tick(p, 14, 14, 11, T.INK)
            return
        r = QRectF(2, 2, 24, 24)
        fill = {"num": T.PINK, "eq": T.PINK, "fin": T.OK, "err": T.BLUSH}[m]
        sticker(p, r, 12, fill)
        if m == "num":
            p.setPen(C(T.INK))
            p.setFont(self.font())
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self._num))
        elif m == "eq":
            paint_eq(p, 14 - 6.5, 20, T.INK, EQ_STILL)
        elif m == "fin":
            paint_tick(p, 14, 14, 11, T.INK)
        else:
            p.setPen(NoPen)
            p.setBrush(C(T.INK))
            p.drawRoundedRect(QRectF(12.75, 7, 2.5, 8), 1, 1)
            p.drawRoundedRect(QRectF(12.75, 17, 2.5, 2.5), 1, 1)


class PlayButton(QAbstractButton):
    """行尾圆钮: play 播放 / stop 正在播放(点它只停这一首) / dis 已在待播 / busy 正在停止"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "play"
        self.setFixedSize(44, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        Anim.get().tick.connect(self._tick)

    def set_mode(self, mode: str):
        if mode != self._mode:
            self._mode = mode
            self.setEnabled(mode in ("play", "stop"))
            self.update()

    def _tick(self):
        if self._mode in ("stop", "busy") and self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        m = self._mode
        cx, cy = 22.0, 22.0
        r = QRectF(cx - 16, cy - 16, 32, 32)
        if m == "dis":
            p.setOpacity(0.32)
        if m in ("stop", "busy"):
            sticker(p, r, 16, T.PINKD, shadow=2)
            p.setPen(NoPen)
            p.setBrush(C("#FFFFFF"))
            p.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 2, 2)
            pen = QPen(C(T.CYAN), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ang = (Anim.get().now() / 1.3 * 360) % 360
            p.drawArc(QRectF(cx - 20.5, cy - 20.5, 41, 41), int(-ang * 16), 180 * 16)
            return
        hov = self.underMouse() and self.isEnabled()
        sticker(p, r, 16, T.PINK if hov else "#FFFFFF", shadow=0 if m == "dis" else 2)
        paint_triangle(p, cx + 1.5, cy, 10, 12, T.INK)


# ---------------------------------------------------------------- 播放栏的零件
class RoundCtl(QAbstractButton):
    """播放区进度格右边的小圆钮。11-02 作者：「暂停和下一首是两个小圆钮，停止就算了」——
    mode：next 下一首（停掉这一个、接着播待播的第 1 个）/ pause 暂停（两竖）/ play 继续（三角）/ stop 停止方块（备用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "stop"
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_mode(self, mode: str):
        if mode != self._mode:
            self._mode = mode
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        if not self.isEnabled():
            p.setOpacity(0.35)
        cx, cy = 18.0, 17.0
        hov = self.underMouse() and self.isEnabled()
        sticker(p, QRectF(cx - 15, cy - 15, 30, 30), 15, T.PINK if hov else "#FFFFFF", shadow=2)
        p.setPen(NoPen)
        p.setBrush(C(T.INK))
        if self._mode == "next":
            paint_triangle(p, cx - 1.5, cy, 9, 11, T.INK)
            p.setBrush(C(T.INK))
            p.drawRoundedRect(QRectF(cx + 4, cy - 5.5, 3, 11), 1, 1)
        elif self._mode == "pause":
            p.drawRoundedRect(QRectF(cx - 5, cy - 5.5, 3.5, 11), 1, 1)
            p.drawRoundedRect(QRectF(cx + 1.5, cy - 5.5, 3.5, 11), 1, 1)
        elif self._mode == "play":
            paint_triangle(p, cx + 1.5, cy, 10, 12, T.INK)
        else:
            p.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 2, 2)


class Disc(QWidget):
    """雨澜的封面: 播放时慢慢转 + 外面一段青色弧线绕圈；空闲时静止。"""

    clicked = Signal()       # 11-02：点封面 = 打开待播列表（空的时候也能开，里面写怎么加）
    drag_hover = Signal(bool)  # 11-02 拖着歌停到封面上 / 离开
    dropped = Signal(str)      # 11-02 歌拖到封面上松手 = 加进待播（末尾）

    def __init__(self, logo_path: str, parent=None):
        super().__init__(parent)
        self._path = logo_path
        self._playing = False
        self._queue = 0          # 11-02 平衡-丙（作者：「待播列表直接塞进左侧播放封面里，点击可以查看」）
        self._hover = False      # 11-02 作者：角标挡着转圈——「鼠标移动到专辑封面，才出现小数字气泡」
        self._pix: QPixmap | None = None
        self._t0 = 0.0
        self._hold = 0.0   # 停下时的角度，下次接着转
        self._drop_hover = False
        self.setFixedSize(64, 68)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点封面看待播；把歌单里的歌拖到封面上 = 加进待播")
        Anim.get().tick.connect(self._tick)

    def set_queue(self, n: int):
        if n != self._queue:
            self._queue = n
            self.setToolTip(f"待播 {n} 首：点封面查看，拖动调顺序、✕ 移出；把歌单里的歌拖到封面上 = 加进待播" if n else
                            "点封面看待播；把歌单里的歌拖到封面上 = 加进待播")
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_TOOL):
            e.acceptProposedAction()
            self._drop_hover = True
            self.update()
            self.drag_hover.emit(True)

    def dragLeaveEvent(self, e):
        self._drop_hover = False
        self.update()
        self.drag_hover.emit(False)

    def dropEvent(self, e):
        tid = drag_tid(e, MIME_TOOL)
        self._drop_hover = False
        self.update()
        self.drag_hover.emit(False)
        if tid:
            e.acceptProposedAction()
            self.dropped.emit(tid)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def set_playing(self, on: bool):
        if on == self._playing:
            return
        now = Anim.get().now()
        if on:
            self._t0 = now
        else:
            self._hold = self._angle(now)
        self._playing = on
        self.update()

    def _angle(self, now: float) -> float:
        if not self._playing:
            return self._hold
        return (self._hold + (now - self._t0) / 8.0 * 360) % 360

    def _tick(self):
        if self._playing and self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(AA | QPainter.RenderHint.SmoothPixmapTransform)
        cx, cy = 32.0, 32.0
        now = Anim.get().now()
        p.setPen(NoPen)
        p.setBrush(C(T.INK))
        p.drawEllipse(QPointF(cx, cy + 3), 26, 26)        # 硬投影
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle(now))
        p.setBrush(C(T.INK))
        p.drawEllipse(QPointF(0, 0), 26, 26)
        p.setBrush(C(T.PINK))
        p.drawEllipse(QPointF(0, 0), 24, 24)
        p.setPen(QPen(C(T.PINKD), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0, 0), 21.25, 21.25)
        p.setPen(NoPen)
        p.setBrush(C("#FFFFFF"))
        p.drawRoundedRect(QRectF(-3, -24, 6, 3), 1.5, 1.5)   # 顶上一小块白: 看得出在转
        p.setBrush(C(T.INK))
        p.drawEllipse(QPointF(0, 0), 18, 18)
        dpr = self.devicePixelRatioF()
        if self._pix is None or abs(self._pix.devicePixelRatio() - dpr) > 0.01:
            self._pix = round_pixmap(self._path, 32, dpr)
        p.drawPixmap(QPointF(-16, -16), self._pix)
        p.restore()
        if self._playing:
            pen = QPen(C(T.CYAN), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ang = (now / 1.6 * 360) % 360
            p.drawArc(QRectF(cx - 30.5, cy - 30.5, 61, 61), int(-ang * 16), 180 * 16)
        if self._drop_hover:             # 拖着歌停在封面上：静止的青色圈，表示松手就加进待播
            pen = QPen(C(T.CYAN), 4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 30.5, 30.5)
        if self._queue and (self._hover or self._drop_hover):   # 鼠标移上来才画右上角的待播个数（和原来「待播 N」贴纸里的数字同一种黑底白字）
            txt = str(self._queue)
            f = T.font(11, 800)
            p.setFont(f)
            w = max(20.0, QFontMetricsF(f).horizontalAdvance(txt) + 10)
            r = QRectF(self.width() - w - 1, 1, w, 20)
            sticker(p, r, 10, T.INK, "#FFFFFF", 1.5)
            p.setPen(C("#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)


class SegProgress(QWidget):
    """分段进度条: 一格 = 一句；绿 = 已完成，条纹 = 正在播放，白 = 还没到。
    不分句汇报的工具用一整条滚动条纹。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total, self._done, self._cur, self._busy = 0, set(), None, False
        self.setFixedHeight(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        Anim.get().tick.connect(self._tick)

    def set_steps(self, total: int, done: set, cur: int | None):
        self._total, self._done, self._cur, self._busy = total, set(done), cur, False
        self.update()

    def set_busy(self):
        self._busy = True
        self.update()

    def _tick(self):
        if self.isVisible() and (self._busy or self._cur is not None):
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        now = Anim.get().now()
        w, h = float(self.width()), 12.0
        if self._busy or self._total <= 0:
            r = QRectF(0, 0, w, h)
            p.setPen(NoPen)
            p.setBrush(C(T.INK))
            p.drawRoundedRect(r, 6, 6)
            paint_stripes(p, r.adjusted(1.5, 1.5, -1.5, -1.5), 4.5, now)
            return
        n = self._total
        gap = 3.0 if n <= 24 else 2.0
        sw = (w - gap * (n - 1)) / n
        for i in range(n):
            r = QRectF(i * (sw + gap), 0, sw, h)
            rad = min(6.0, sw / 2)
            p.setPen(NoPen)
            p.setBrush(C(T.INK))
            p.drawRoundedRect(r, rad, rad)
            inner = r.adjusted(1.5, 1.5, -1.5, -1.5)
            ir = max(0.0, rad - 1.5)
            if i == self._cur:
                paint_stripes(p, inner, ir, now)
            else:
                p.setBrush(C(T.OK if i in self._done else "#FFFFFF"))
                p.drawRoundedRect(inner, ir, ir)


class MiniPlay(QWidget):
    """提示文字里的小播放圆钮图标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        sticker(p, QRectF(0, 0, 18, 18), 9, "#FFFFFF", bw=1.5)
        paint_triangle(p, 9.8, 9, 6, 8, T.INK)


class EqIcon(QWidget):
    """单独一组跳动的音柱（歌词当前句行尾）。"""

    def __init__(self, color=T.PINKTX, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 14)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        paint_eq(p, 1.5, 13, self._color, EQ_STILL)


class TickIcon(QWidget):
    def __init__(self, color=T.OKTX, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 14)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        paint_tick(p, 8, 7, 11, self._color)


# ---------------------------------------------------------------- 页签
class TabButton(QAbstractButton):
    def __init__(self, key: str, text: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setFont(T.font(13, 700))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.count: str = ""
        self.live = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        Anim.get().tick.connect(self._tick)

    def set_extra(self, count: str = "", live: bool = False):
        if (count, live) != (self.count, self.live):
            self.count, self.live = count, live
            self.updateGeometry()
            self.update()

    def _tick(self):
        if self.live and self.isVisible():
            self.update()

    def _count_w(self) -> float:
        return max(20.0, QFontMetricsF(T.font(12, 700)).horizontalAdvance(self.count) + 10) if self.count else 0

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        w = 12 + fm.horizontalAdvance(self.text()) + 12
        if self.count:
            w += 6 + self._count_w()
        if self.live:
            w += 8 + 6
        return QSize(math.ceil(w), 34)

    minimumSizeHint = sizeHint

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        on = self.isChecked()
        top = 0 if on else 2
        h = self.height()
        # 只画上半边圆角: 画一个比控件高的圆角矩形，下半截被控件边界裁掉
        p.setPen(NoPen)
        p.setBrush(C(T.INK))
        p.drawRoundedRect(QRectF(0, top, self.width(), h + 20), 12, 12)
        p.setBrush(C("#FFFFFF" if on else T.GREY))
        p.drawRoundedRect(QRectF(2, top + 2, self.width() - 4, h + 20), 10, 10)
        if not on:   # 没选中的页签压在底线上面，底线从它下面穿过
            p.fillRect(QRectF(0, h - 2, self.width(), 2), C(T.INK))
        x = 12.0
        cy = top + (h - 2 - top) / 2 + 1
        if self.live:
            a = Anim.get().now()
            k = 0.625 + 0.375 * math.cos(2 * math.pi * a / 1.1)
            p.setBrush(C(T.PINKD, k))
            p.drawEllipse(QPointF(x + 4, cy), 4, 4)
            x += 14
        p.setPen(C(T.INK if on else T.INK2))
        p.setFont(self.font())
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(self.text())
        p.drawText(QRectF(x, cy - 12, tw + 2, 24), Qt.AlignmentFlag.AlignVCenter, self.text())
        x += tw + 6
        if self.count:
            nw = self._count_w()
            nr = QRectF(x, cy - 9, nw, 18)
            p.setPen(NoPen)
            p.setBrush(C(T.INK))
            p.drawRoundedRect(nr, 9, 9)
            p.setPen(C("#FFFFFF"))
            p.setFont(T.font(12, 700))
            p.drawText(nr, Qt.AlignmentFlag.AlignCenter, self.count)


class TabBar(QWidget):
    """页签栏: 底下一条 2px 描边线，选中的页签是白底并盖住底线。右端可以放一个小控件。"""
    changed = Signal(str)

    def __init__(self, tabs: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 4, 0)
        lay.setSpacing(6)
        self.btns: dict[str, TabButton] = {}
        for key, text in tabs:
            b = TabButton(key, text)
            b.clicked.connect(lambda _=False, k=key: self.set_current(k))
            lay.addWidget(b)
            self.btns[key] = b
        lay.addStretch(1)
        self.corner = QHBoxLayout()
        self.corner.setContentsMargins(0, 0, 0, 4)
        self.corner.setSpacing(7)
        lay.addLayout(self.corner)
        self._cur = ""
        self.set_current(tabs[0][0], emit=False)

    def current(self) -> str:
        return self._cur

    def set_current(self, key: str, emit: bool = True):
        if key not in self.btns:
            return
        changed = key != self._cur
        self._cur = key
        for k, b in self.btns.items():
            b.setChecked(k == key)
        if changed and emit:
            self.changed.emit(key)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(QRectF(0, self.height() - 2, self.width(), 2), C(T.INK))


# ---------------------------------------------------------------- 单行省略文字
class ElidedLabel(QWidget):
    """说明折成一行时用: 放不下就在行尾加省略号（全窗口只有这一处会省略）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self.setFont(T.font(13))
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setText(self, text: str):
        self._text = " ".join(text.split())
        self.update()

    def is_elided(self) -> bool:
        return QFontMetricsF(self.font()).horizontalAdvance(self._text) > self.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(C(T.INK2))
        fm = p.fontMetrics()
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width()))


# ---------------------------------------------------------------- 日志
_TS = re.compile(r"^(\[\d\d:\d\d:\d\d\])(.*)$")


class LogView(QPlainTextEdit):
    """等宽日志区: 时间戳变淡，「== 步骤名」深粉，系统行青色，出错行红色；实时滚动时最新一行有底色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(T.font(12, 400, mono=True))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setMaximumBlockCount(5000)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._live = False
        self._fmt: dict[str, QTextCharFormat] = {}
        for k, (col, bold) in {"": ("#2E3446", False), "t": (T.INK3, False), "hd": (T.PINKTX, True),
                               "sys": (T.CYANTX, False), "er": (T.BADTX, True)}.items():
            f = QTextCharFormat()
            f.setForeground(C(col))
            if bold:
                f.setFontWeight(700)
            self._fmt[k] = f
        self._mark = QTextCharFormat(self._fmt[""])
        self._mark.setBackground(C(T.BADBG))

    def set_live(self, live: bool):
        self._live = live
        self._refresh_tail()

    def clear_all(self):
        self.clear()
        self.setExtraSelections([])

    def add(self, text: str, kind: str = "", mark: bool = False):
        sb = self.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cur.insertBlock()
        m = _TS.match(text)
        body_fmt = self._mark if mark else self._fmt.get(kind, self._fmt[""])
        if m and not mark:
            cur.insertText(m.group(1), self._fmt["t"])
            cur.insertText(m.group(2), body_fmt)
        else:
            cur.insertText(text, body_fmt)
        if at_bottom:
            sb.setValue(sb.maximum())
        self._refresh_tail()

    def set_lines(self, lines):
        """lines: [(text, kind)] 或 [(text, kind, mark)]"""
        self.setUpdatesEnabled(False)
        self.clear_all()
        for item in lines:
            self.add(*item)
        self.setUpdatesEnabled(True)
        self.scroll_to_end()

    def scroll_to_end(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_tail(self):
        if not self._live or self.document().isEmpty():
            self.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(C(T.GOLDBG))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        sel.cursor = cur
        self.setExtraSelections([sel])


# ---------------------------------------------------------------- 监控
class MonitorView(QWidget):
    """监控画面: 截图按比例缩进深色框里居中，竖屏左右留边、横屏上下留边，不拉伸。
    监控关着时，如果有还没验收的结束截图，就把它摆出来（点一下看大图）。"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self._img: QImage | None = None
        self._still: QImage | None = None
        self._cap = ""
        self._msg = "监控已关闭"
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFont(T.font(13))

    def set_on(self, on: bool):
        self._on = on
        self._img = None
        self._msg = "等待画面…" if on else "监控已关闭"
        self.update()

    def set_frame(self, img: QImage):
        if self._on:
            self._img = img
            self.update()

    def set_message(self, msg: str):
        if self._on:
            self._img = None
            self._msg = msg
            self.update()

    def set_still(self, img: QImage | None, caption: str = ""):
        self._still = img if (img is not None and not img.isNull()) else None
        self._cap = caption
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._still else Qt.CursorShape.ArrowCursor)
        self.setToolTip("点一下看大图、验收" if self._still else "")
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._on and self._still is not None:
            self.clicked.emit()

    def _draw_fit(self, p: QPainter, box: QRectF, img: QImage):
        iw, ih = img.width(), img.height()
        k = min(box.width() / iw, box.height() / ih)
        w, h = iw * k, ih * k
        target = QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(target, 4, 4)
        p.setClipPath(clip)
        p.drawImage(target, img)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(AA | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if not self._on and self._still is not None:
            p.setBrush(C(T.GOLDBG))
            p.setPen(QPen(C(T.GOLD), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 12, 12)
            cap_h = 40
            self._draw_fit(p, r.adjusted(8, 8, -8, -8 - cap_h), self._still)
            p.setPen(C(T.GOLDTX))
            p.setFont(T.font(12, 700))
            p.drawText(QRectF(r.left() + 8, r.bottom() - cap_h - 4, r.width() - 16, cap_h),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._cap)
            return
        if not self._on:
            p.setBrush(C(T.GREY))
            pen = QPen(C("#B7C0D2"), 2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 12, 12)
            p.setPen(C(T.INK2))
            p.setFont(self.font())
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._msg)
            return
        sticker(p, r, 12, T.INK)
        if self._img is None or self._img.isNull():
            p.setPen(C("#B7C0D2"))
            p.setFont(self.font())
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._msg)
            return
        box = r.adjusted(8, 8, -8, -8)
        iw, ih = self._img.width(), self._img.height()
        k = min(box.width() / iw, box.height() / ih)
        w, h = iw * k, ih * k
        target = QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(target, 4, 4)
        p.setClipPath(clip)
        p.drawImage(target, self._img)
