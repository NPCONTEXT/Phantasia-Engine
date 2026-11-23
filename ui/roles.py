"""角色：一首歌只能选一个（例如乐土用谁打）。详情卡片里一条「当前角色」+「切换角色」，点开角色面板（搜索 + 最近用过 + 网格）。
名单来自 tools.json 的 "roles"（session.role_cfg）；选了谁、传给脚本什么由 session 管，这里只负责画和挑。
2026-10-20 按作者定的「只能选一个、按 20 个角色设计、D1 弹出面板」做。"""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout,
                               QWidget)

from . import theme as T
from .dialogs import Dialog
from .widgets import AA, C, EqIcon, PillButton, StatusTag, paint_tick, sticker

TAG_KIND = {"已实测": "ok"}          # 其余非空的 tag（待实测等）用「待播」那种淡紫样式（金色方块是「手动停止」，别混）


def tag_kind(tag: str) -> str:
    return TAG_KIND.get(tag, "wait")


class RoleAvatar(QWidget):
    """圆形头像位：先用名字第一个字（以后换成备战页截的立绘小图）。"""

    def __init__(self, ch: str = "", size: int = 34, on: bool = False, parent=None):
        super().__init__(parent)
        self.ch, self.on = ch, on
        self.setFixedSize(size, size)

    def set(self, ch: str, on: bool):
        if (ch, on) != (self.ch, self.on):
            self.ch, self.on = ch, on
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(QPen(C(T.LAV if self.on else T.GREYLINE), 2))
        p.setBrush(C(T.LAVBG if self.on else T.GREY))
        p.drawEllipse(r)
        p.setPen(C(T.LAVTX if self.on else T.INK3))
        p.setFont(T.font(int(self.height() * 0.42), 800))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.ch[:1])


class RoleBar(QWidget):
    """详情卡片里的「当前角色」一条：头像、名字、打法 / 上次结果、实测标签、「切换角色」。"""
    switch = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 11)
        lay.setSpacing(10)
        self.avatar = RoleAvatar("", 36, True)
        lay.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(T.label("当前角色", 11, 700, T.MUTED))
        nm = QHBoxLayout()
        nm.setSpacing(6)
        self.name = T.label("", 14, 800)
        self.eq = EqIcon(T.LAVTX)
        nm.addWidget(self.name)
        nm.addWidget(self.eq)
        nm.addStretch(1)
        col.addLayout(nm)
        self.sub = T.label("", 11, 400, T.INK3)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.tag = StatusTag("ok", "已实测")
        lay.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn = PillButton("切换角色 ›", small=True, tip="打开角色面板，点一下就换，下次播放生效")
        self.btn.clicked.connect(self.switch)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(66)
        self._sig = None

    def set_role(self, item: dict, sub: str, playing: bool, locked: bool):
        label = str(item.get("label") or item.get("id") or "")
        tag = str(item.get("tag") or "")
        sig = (label, sub, tag, playing, locked)
        if sig == self._sig:
            return
        self._sig = sig
        self.avatar.set(label, True)
        self.name.setText(label)
        self.eq.setVisible(playing)
        self.sub.setText(sub)
        self.sub.setStyleSheet(f"color:{T.LAVTX if playing else T.INK3};background:transparent;")
        self.sub.setFont(T.font(11, 600 if playing else 400))
        self.tag.setVisible(bool(tag))
        if tag:
            self.tag.set(tag_kind(tag), tag)
        self.btn.setEnabled(not locked)
        self.btn.setToolTip("正在播放：这次的角色已经定了，下次播放才生效" if locked
                            else "打开角色面板，点一下就换，下次播放生效")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        sticker(p, QRectF(0, 0, self.width(), self.height() - 3), 12, "#FFFFFF", T.INK, 2.0, shadow=3)


class RoleTile(QAbstractButton):
    """角色面板里的一格：头像 + 名字 + 实测标签（没写就写打法），选中的紫底带勾。"""

    def __init__(self, item: dict, on: bool, width: int = 180, parent=None):
        super().__init__(parent)
        self.item, self.on = item, on
        self.rid = str(item.get("id"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedSize(width, 54)
        self.setToolTip(str(item.get("desc") or item.get("label") or self.rid))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 9)
        lay.setSpacing(8)
        label = str(item.get("label") or self.rid)
        lay.addWidget(RoleAvatar(label, 30, on))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(T.label(label, 13, 800 if on else 700, T.INK))
        tag = str(item.get("tag") or "")
        color = {"ok": T.OKTX, "wait": T.LAVTX}[tag_kind(tag)] if tag else T.INK3
        col.addWidget(T.label(tag or str(item.get("desc") or ""), 11, 600 if tag else 400, color))
        lay.addLayout(col, 1)

    def sizeHint(self):
        return QSize(self.width(), 54)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(AA)
        hov = self.underMouse() and not self.on
        sticker(p, QRectF(0, 0, self.width(), self.height() - 2), 11, T.LAVBG if self.on else "#FFFFFF",
                T.LAV if (self.on or hov) else T.GREYLINE, 2.0 if (self.on or hov) else 1.5)
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(C(T.LAV))
            p.drawEllipse(QRectF(self.width() - 26, 9, 16, 16))
            paint_tick(p, self.width() - 18, 17, 9, "#FFFFFF", 2.2)


def matches(item: dict, q: str) -> bool:
    """搜索：名字、短名、打法、id、拼音首字母短码（py）里包含输入就算；不分大小写，空输入全算。"""
    q = q.strip().lower()
    if not q:
        return True
    hay = " ".join(str(item.get(k) or "") for k in ("label", "short", "desc", "id", "py")).lower()
    return q in hay


class RolePanel(Dialog):
    """选角色的弹出面板：搜索框、最近用过、三列网格。点一格 = 选它并关掉；回车 = 选搜出来的第一个。"""
    COLS, TILE_W = 3, 180

    def __init__(self, parent, song: str, items: list[dict], cur: str | None, recent: list[str]):
        super().__init__(parent, "选择角色", 18 * 2 + self.COLS * self.TILE_W + (self.COLS - 1) * 10 + 14)
        self.items, self.cur = items, cur
        self.chosen: str | None = None
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(T.label("选择角色", 16, 800))
        head.addWidget(T.label(f"{song} · 共 {len(items)} 个", 12, 600, T.MUTED), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.body.addLayout(head)
        self.body.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜名字、打法或拼音首字母（如 fh）")
        self.search.setFixedHeight(34)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._pick_first)
        self.body.addWidget(self.search)
        self.body.addSpacing(10)
        by_id = {str(it.get("id")): it for it in items}
        rec = [r for r in recent if r in by_id]
        self.recent_row = QWidget()
        rr = QHBoxLayout(self.recent_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(8)
        rr.addWidget(T.label("最近用过", 12, 800, T.INK2))
        for rid in rec:
            b = PillButton(str(by_id[rid].get("label") or rid), small=True)
            b.clicked.connect(lambda _=False, r=rid: self._pick(r))
            rr.addWidget(b)
        rr.addStretch(1)
        self.recent_row.setVisible(bool(rec))
        self.body.addWidget(self.recent_row)
        self.body.addSpacing(8 if rec else 0)
        self.count = T.label("", 12, 800, T.INK2)
        self.body.addWidget(self.count)
        self.body.addSpacing(6)
        inner = QWidget()
        self.grid = QGridLayout(inner)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.tiles: list[RoleTile] = []
        for it in items:
            t = RoleTile(it, str(it.get("id")) == cur, self.TILE_W)
            t.clicked.connect(lambda _=False, r=str(it.get("id")): self._pick(r))
            self.tiles.append(t)
        self.area = QScrollArea()
        self.area.setWidget(inner)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows = min(5, (len(items) + self.COLS - 1) // self.COLS)
        self.area.setFixedHeight(rows * 64 - 10 + 4)
        self.body.addWidget(self.area)
        self.body.addSpacing(10)
        self.body.addWidget(T.label("点一下就换成它，下次播放生效；正在播放这首时不能换。", 12, 400, T.INK3))
        cancel = PillButton("取消")
        cancel.clicked.connect(self.reject)
        self.footer(cancel)
        self._filter("")
        self.search.setFocus()

    def _filter(self, q: str):
        shown = [t for t in self.tiles if matches(t.item, q)]
        for t in self.tiles:
            self.grid.removeWidget(t)
            t.setVisible(False)
        for n, t in enumerate(shown):
            self.grid.addWidget(t, n // self.COLS, n % self.COLS)
            t.setVisible(True)
        self._shown = [t.rid for t in shown]
        self.count.setText(f"全部角色（{len(self.tiles)}）" if not q.strip() else
                           (f"找到 {len(shown)} 个" if shown else "没找到，换个字试试"))

    def _pick_first(self):
        if self._shown:
            self._pick(self._shown[0])

    def _pick(self, rid: str):
        self.chosen = rid
        self.accept()
