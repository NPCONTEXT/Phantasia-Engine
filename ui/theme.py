"""配色 / 字体 / 全局样式（版式取自设计稿 A3「贴纸手账 · 歌单版」；11-14 幻想引擎版本二：配色跟着雨澜走，
银白底、冰蓝强调、金色只留给名牌和待验收。常量名沿用旧的：PINK 一族现在是冰蓝，BLUSH 是淡金）"""
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QLabel

INK, INK2, INK3 = "#232838", "#525A70", "#636B83"
LINE, CREAM = "#DCE3F0", "#F4F6FB"
PINK, PINKD, PINKBG, PINKTX = "#BCD0F7", "#7D9CE0", "#E9F0FE", "#2F56A6"
CYAN, CYANBG, CYANTX = "#9FE3EE", "#E6F8FB", "#0B6E80"
LAV, LAVBG, LAVTX = "#C3B5E8", "#F0ECFB", "#5E4BA6"
GOLD, GOLDBG, GOLDTX = "#EBC462", "#FFF4D6", "#855C00"
BLUSH, OK, OKBG, OKTX = "#F2D98C", "#5CC9A7", "#DDF6EC", "#12775A"
BAD, BADBG, BADTX = "#D63F5C", "#FFE3E8", "#B3243F"
GREY, GREYLINE, MUTED = "#EEF1F6", "#CDD5E3", "#8891A6"
PAPER = "#FBFCFE"   # 日志区 / 空状态的底色

_fam_ui: list[str] = []
_fam_mono: list[str] = []


def C(hexstr: str, alpha: float | None = None) -> QColor:
    c = QColor(hexstr)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def _init_fonts():
    global _fam_ui, _fam_mono
    have = set(QFontDatabase.families())
    _fam_ui = [f for f in ("SF Pro Rounded", "PingFang SC") if f in have]
    _fam_mono = [f for f in ("SF Mono", "Menlo") if f in have]
    if "PingFang SC" in have:
        _fam_mono.append("PingFang SC")


def font(px: int, weight: int = 400, mono: bool = False) -> QFont:
    if not _fam_ui and not _fam_mono:
        _init_fonts()
    f = QFont()
    fams = _fam_mono if mono else _fam_ui
    if fams:
        f.setFamilies(fams)
    f.setPixelSize(px)
    f.setWeight({400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold,
                 700: QFont.Weight.Bold, 800: QFont.Weight.ExtraBold}.get(weight, QFont.Weight.Normal))
    return f


def label(text: str = "", px: int = 13, weight: int = 400, color: str = INK,
          mono: bool = False, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(px, weight, mono))
    lb.setStyleSheet(f"color:{color};background:transparent;")
    lb.setWordWrap(wrap)
    if wrap:
        lb.setTextFormat(Qt.TextFormat.PlainText)
    return lb


def reduce_motion() -> bool:
    """系统开了「减少动态效果」就把动画全停掉。"""
    try:
        r = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "1"
    except Exception:
        return False


def palette() -> QPalette:
    """固定浅色调色板：系统是深色模式时也不跟着变黑。"""
    p = QPalette()
    R = QPalette.ColorRole
    for role, col in ((R.Window, CREAM), (R.WindowText, INK), (R.Base, "#FFFFFF"), (R.AlternateBase, GREY),
                      (R.Text, INK), (R.Button, "#FFFFFF"), (R.ButtonText, INK), (R.Highlight, PINK),
                      (R.HighlightedText, INK), (R.ToolTipBase, "#FFFFFF"), (R.ToolTipText, INK),
                      (R.PlaceholderText, MUTED), (R.Link, LAVTX)):
        p.setColor(role, QColor(col))
    return p


APP_QSS = f"""
QToolTip {{ background:#FFFFFF; color:{INK}; border:1px solid {INK}; padding:4px 7px; font-size:12px; }}
QScrollBar:vertical {{ background:transparent; width:8px; margin:2px 0 2px 2px; }}
QScrollBar::handle:vertical {{ background:#C9D1E0; border-radius:3px; min-height:28px; }}
QScrollBar::handle:vertical:hover {{ background:#B7C0D2; }}
QScrollBar:horizontal {{ background:transparent; height:8px; margin:2px 2px 0 2px; }}
QScrollBar::handle:horizontal {{ background:#C9D1E0; border-radius:3px; min-width:28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QScrollArea {{ background:transparent; border:none; }}
QLineEdit {{ border:2px solid {INK}; border-radius:10px; padding:4px 10px; background:#FFFFFF; color:{INK};
            min-height:20px; selection-background-color:{PINK}; selection-color:{INK}; }}
QLineEdit:focus {{ background:{CYANBG}; }}
QLineEdit[bad="true"] {{ border-color:{BAD}; background:{BADBG}; }}
QComboBox {{ border:2px solid {INK}; border-radius:10px; padding:4px 10px; background:#FFFFFF; color:{INK};
            min-height:20px; }}
QComboBox::drop-down {{ border:none; width:26px; }}
QComboBox::down-arrow {{ image:none; width:0; height:0; }}
QComboBox QAbstractItemView {{ border:2px solid {INK}; background:#FFFFFF; color:{INK}; outline:0;
            selection-background-color:{PINKBG}; selection-color:{INK}; padding:2px; }}
QPlainTextEdit {{ background:{PAPER}; color:#2E3446; border:2px solid {INK}; border-radius:12px;
            padding:6px 8px; selection-background-color:{PINK}; selection-color:{INK}; }}
QMenu {{ background:#FFFFFF; color:{INK}; border:1px solid {INK}; padding:4px; }}
QMenu::item {{ padding:4px 16px; border-radius:4px; }}
QMenu::item:selected {{ background:{PINKBG}; }}
QMenu::item:disabled {{ color:{MUTED}; }}
"""
