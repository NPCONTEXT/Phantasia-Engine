"""跨平台的几样小工具（11-23 Windows 准备）：adb、ffmpeg 在哪，临时目录，识字，系统通知，设备锁。

Mac 上和以前的写法一样：Homebrew 的 adb / ffmpeg、/tmp、Vision 识字小程序（bh3_auto/ocr/ocr）、osascript 通知、fcntl 锁。
Windows 上：adb / ffmpeg 先用幻想引擎给的路径、再从 PATH 和常见位置找；临时目录用系统的；识字用 RapidOCR；
通知先只打一行日志；锁用 msvcrt。

脚本这样引（bh3_auto 要在 sys.path 里）：
    from lib.pe_platform import adb_path, ffmpeg_path, tmp_dir, ocr_lines, notify, lock_nb

环境变量（都可以不设）：
    PE_ADB       幻想引擎挑中的 adb（引擎会设；想和应用宝这类自带 adb 的程序共用一个版本，就在引擎那边指定）
    PE_FFMPEG    指定 ffmpeg
    PE_OCR       vision | rapid，不设时 Mac 用 vision、别的系统用 rapid
    PE_TMP       临时目录的根（跑测试时设，和正在播放的脚本分开）
要兼容 Python 3.9（bh3_auto 的 .venv）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
BH3 = Path(__file__).resolve().parents[1]          # bh3_auto
VISION_OCR = BH3 / "ocr" / "ocr"                     # Mac 上 install.sh 现编的 Vision 识字小程序


def _usable(p) -> bool:
    return bool(p) and os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK))


def _first(paths):
    for p in paths:
        if _usable(p):
            return str(p)
    return None


def _win_tool_candidates(name: str) -> list:
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    if name == "adb":
        return [os.path.join(la, "Android", "Sdk", "platform-tools", "adb.exe"),
                r"C:\platform-tools\adb.exe", r"C:\Android\platform-tools\adb.exe"]
    return [os.path.join(la, "Microsoft", "WinGet", "Links", name + ".exe"),
            os.path.join(pf, name, "bin", name + ".exe"), os.path.join("C:\\", name, "bin", name + ".exe")]


def _find_tool(name: str, env_key: str) -> str:
    env = os.environ.get(env_key)
    if _usable(env):
        return env
    found = shutil.which(name)
    if found:
        return found
    if IS_WIN:
        cands = _win_tool_candidates(name)
    else:
        cands = ["/opt/homebrew/bin/" + name, "/usr/local/bin/" + name]
        if name == "adb":
            cands.append(str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
    # 都没找到：Mac 上还是返回 Homebrew 那个（和以前写死的一样，报错时看得出找的是哪）
    return _first(cands) or (name + ".exe" if IS_WIN else "/opt/homebrew/bin/" + name)


def adb_path() -> str:
    """幻想引擎给的 PE_ADB → PATH 里的 adb → 各平台常见位置。"""
    return _find_tool("adb", "PE_ADB")


def ffmpeg_path() -> str:
    return _find_tool("ffmpeg", "PE_FFMPEG")


def tmp_dir(name: str = "") -> Path:
    """临时目录：Mac 上照旧是 /tmp（以前写死的就是它），Windows 上用系统临时目录。会顺手建好。
    环境变量 PE_TMP 可以换一个根目录（11-24：跑离线测试时用，免得和正在播放的脚本共用 /tmp/nikke 这些文件、互相顶掉截图）。"""
    env = os.environ.get("PE_TMP")
    base = Path(env) if env else (Path(tempfile.gettempdir()) if IS_WIN else Path("/tmp"))
    p = base / name if name else base
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- 中文路径
def unicode_cv2() -> None:
    """Windows：OpenCV 的 imread / imwrite 读写不了中文路径（11-24 审查指出；本项目的任务目录都是中文名，模板小图全在里面）。
    换成先用 numpy 读写文件字节、再让 OpenCV 编解码的版本，用法和返回值照旧（读不到返回 None，写不成返回 False）。
    在 Windows 上 import 本模块时自动换；Mac 上不动。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return
    if getattr(cv2, "_pe_unicode", False):
        return

    def imread(filename, flags=cv2.IMREAD_COLOR):
        try:
            data = np.fromfile(str(filename), dtype=np.uint8)
        except (OSError, ValueError):
            return None
        return cv2.imdecode(data, flags) if data.size else None

    def imwrite(filename, img, params=None):
        ext = os.path.splitext(str(filename))[1] or ".png"
        ok, buf = cv2.imencode(ext, img, list(params) if params else [])
        if not ok:
            return False
        try:
            buf.tofile(str(filename))
        except OSError:
            return False
        return True

    cv2._pe_orig = (cv2.imread, cv2.imwrite)
    cv2.imread, cv2.imwrite = imread, imwrite
    cv2._pe_unicode = True


if IS_WIN:
    unicode_cv2()


# ---------------------------------------------------------------- 识字
def ocr_engine() -> str:
    e = (os.environ.get("PE_OCR") or "").strip().lower()
    if e in ("vision", "rapid"):
        return e
    return "vision" if IS_MAC else "rapid"


def ocr_lines(image, timeout: float = 30, binary=None) -> str:
    """识字，返回和 Vision 小程序一样的文字：一行一条 JSON（text、conf、x、y、w、h；x、y 是字框左上角，原图像素）。
    binary：Mac 上换一个 Vision 小程序（绝区零锁定旧版识别用的 ocr_rev2）；用 RapidOCR 时不管它。
    Vision 那条路和以前一样直接跑小程序（超时照样抛 TimeoutExpired）；RapidOCR 在本进程里跑，第一次调用时加载模型。"""
    if ocr_engine() == "vision":
        return subprocess.run([str(binary or VISION_OCR), str(image)], capture_output=True, text=True,
                              timeout=timeout).stdout
    return rapid_lines(str(image))


_rapid = None


def _rapid_engine():
    global _rapid
    if _rapid is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError("没装 RapidOCR（pip install rapidocr-onnxruntime），识字用不了：%s" % e)
        # 11-24 对比截图后定的：不先缩图（默认长边超过 2000 就缩，2400×1504 的小字会糊）
        _rapid = RapidOCR(max_side_len=3000)
    return _rapid


def _prep(path: str):
    """给 RapidOCR 的图先在亮度上做一遍分块拉对比（CLAHE）：被弹窗压暗的页面上，深灰字压在深蓝底的标签它原本框不出来，
    Vision 认得出（11-24 超弦主页「无限榜单」「兑换商店」「深渊任务」）。图的大小不变，坐标照旧。读不了图就原样交给它。"""
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return path
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lum, a, b = cv2.split(lab)
        lum = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lum)
        return cv2.cvtColor(cv2.merge([lum, a, b]), cv2.COLOR_LAB2BGR)
    except Exception:
        return path


def rapid_lines(path: str) -> str:
    """RapidOCR 的结果换成 Vision 小程序的输出格式。双引号换成单引号（Vision 小程序也这么做）。"""
    result, _ = _rapid_engine()(_prep(path))
    out = []
    for box, text, score in result or []:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x, y = min(xs), min(ys)
        out.append(json.dumps({"text": normalize_text(text).replace('"', "'"), "conf": round(float(score), 3),
                               "x": int(x), "y": int(y), "w": int(max(xs) - x), "h": int(max(ys) - y)},
                              ensure_ascii=False))
    return "".join(line + "\n" for line in out)


_rapid_rec = None


def rapid_text_line(img) -> str:
    """把一小块图整块当成一行字认（不找框），返回认出的字。单独一个数字这类 RapidOCR 框不出来的，用它补
    （11-24 记忆战场「今日剩余挑战次数」是 0 时，找框那条路什么都读不出，整块认能读成「数0」）。只在 RapidOCR 那条路上用。"""
    global _rapid_rec
    if _rapid_rec is None:
        from rapidocr_onnxruntime import RapidOCR
        _rapid_rec = RapidOCR(use_det=False, use_cls=False)
    res, _ = _rapid_rec(img)
    return "".join(normalize_text(str(r[0])) for r in (res or []))


# RapidOCR 常见的形近字认错，统一成正确的（只收不会和别的词撞的整词，对比截图后补）
# 11-24：试过「处关宋」→「处关闭」（乱涂的关闭提示），乱涂 test_unknown 在 RapidOCR 下随即绕进一小时的长循环、拖慢了正在播放的正式版，撤掉；
# 乱涂不公开、Windows 上不用，不为它加。
_FIX = {"售馨": "售罄", "公苦": "公告"}


def normalize_text(text: str) -> str:
    """RapidOCR 和 Vision 写法不一样、但意思一样的地方，在这里统一（对比截图后补）：去掉首尾空格，改掉常见的形近字。"""
    text = text.strip()
    for a, b in _FIX.items():
        text = text.replace(a, b)
    return text


# ---------------------------------------------------------------- 通知、锁
def notify(title: str, body: str) -> None:
    """系统通知。Mac 用 osascript；别的系统先只打一行日志（幻想引擎的播放记录里看得到）。失败不报错。"""
    if IS_MAC:
        def esc(s):
            return s.replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.run(["/usr/bin/osascript", "-e", 'display notification "%s" with title "%s"' % (esc(body), esc(title))],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    else:
        print("[通知] %s：%s" % (title, body), flush=True)


def lock_nb(f) -> None:
    """对打开的文件加独占锁，不等：已经被别人锁着就抛 BlockingIOError（和 fcntl.flock 的 LOCK_NB 一样）。"""
    if IS_WIN:
        import msvcrt
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise BlockingIOError(str(e))
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
