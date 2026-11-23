"""暂停协议（11-02）。脚本这边用这几样：

    pause_point()            放在截图函数开头：幻想引擎要暂停、又不在战斗里，就停在这里，继续后再截图；停过返回 True
    with battle("正在战斗（…）"):   包住战斗；也能当装饰器 @battle("…")。战斗里幻想引擎点暂停会被弹回
    mark("…", say="…") / mark(None)   给「每轮看一眼页面再动作」的脚本（乐土）：这一轮能不能停；
                             不能停时幻想引擎弹回，气泡说 say（不给就说「why。这一场打完再停。」）

幻想引擎播放时给脚本两个环境变量：PLAY_CTL = 这次播放的控制目录；PYTHONPATH 带上本文件所在的目录。
- 幻想引擎要暂停：在控制目录放一个 pause 文件；删掉 = 继续。
- 脚本报告：控制目录里的 state.json，{"state": "run" | "paused" | "battle", "why": "…"}；有这个文件 = 这首接上了暂停。
- 暂停的时间从 time.time / time.monotonic 里扣掉：暂停前正在等的页面，继续后不会因为停太久被算成超时。
- 不经幻想引擎运行（没有 PLAY_CTL）时什么都不做，计时也不改。
脚本里这样引（单独运行时 import 不到，用什么都不做的替身）：

    try:
        from play_ctl import pause_point, battle, mark
    except ImportError:
        import contextlib
        def pause_point(): return False
        def mark(*a, **k): pass
        class battle(contextlib.ContextDecorator):
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *e): return False

要兼容 Python 3.9（bh3_auto 的 .venv）。
"""
import contextlib
import json
import os
import time

_DIR = os.environ.get("PLAY_CTL") or ""
ON = bool(_DIR) and os.path.isdir(_DIR)
_PAUSE = os.path.join(_DIR, "pause") if ON else ""
_STATE = os.path.join(_DIR, "state.json") if ON else ""
_real_time = time.time
_real_mono = time.monotonic
_paused = 0.0          # 这个进程累计停了多少秒
_battles = []          # 正在进行的战斗（可以嵌套），记 (why, say)
_marked = None         # mark() 记的「这一轮不能停」(why, say)；None = 能停


def _time():
    return _real_time() - _paused


def _mono():
    return _real_mono() - _paused


def _write(state, why="", say=""):
    tmp = "%s.%d.tmp" % (_STATE, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"state": state, "why": why, "say": say or "", "pid": os.getpid()}, f, ensure_ascii=False)
        os.replace(tmp, _STATE)
    except OSError:
        pass


def _read():
    try:
        with open(_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _say(text):
    print("[%s] [引擎] %s" % (time.strftime("%H:%M:%S"), text), flush=True)


def _show():
    """按现在的战斗栈 / mark 写 state.json。"""
    if _battles:
        _write("battle", *_battles[-1])
    elif _marked:
        _write("battle", *_marked)
    else:
        _write("run")


def mark(why=None, say=""):
    """每轮看完页面调一次：能停的页面 mark(None)，不能停的 mark("正在…", say="…到哪个页面才能停")。只在变了时写文件。"""
    global _marked
    new = (why, say or "") if why else None
    if not ON or new == _marked:
        return
    _marked = new
    _show()


def pause_point():
    """幻想引擎要暂停（有 pause 文件）、又不在战斗里，就停在这里；pause 文件删掉后接着往下走。停过返回 True。
    战斗可能是别的进程标的（脚本另起子进程打仗），所以也看 state.json 里是不是 battle。"""
    global _paused
    if not ON or _battles or _marked or not os.path.exists(_PAUSE):
        return False
    if _read().get("state") == "battle":
        return False
    t0 = _real_mono()
    _write("paused")
    _say("已暂停")
    while os.path.exists(_PAUSE):
        time.sleep(0.3)
    d = _real_mono() - t0
    _paused += d
    _write("run")
    m, s = divmod(int(round(d)), 60)
    _say("继续（停了 %d 分 %d 秒）" % (m, s) if m else "继续（停了 %d 秒）" % s)
    return True


class battle(contextlib.ContextDecorator):
    """战斗：进去时告诉幻想引擎「战斗中」（这期间点暂停会被弹回，气泡说 why），出来恢复。"""

    def __init__(self, why="正在战斗", say=""):
        self.why, self.say = why, say or ""

    def __enter__(self):
        if ON:
            _battles.append((self.why, self.say))
            _show()
        return self

    def __exit__(self, *exc):
        if ON and _battles:
            _battles.pop()
            _show()
        return False


if ON:
    time.time = _time
    time.monotonic = _mono
    if not os.path.exists(_STATE):      # 脚本另起的子进程也会 import：别把父进程标的「战斗中」盖成 run
        _write("run")
