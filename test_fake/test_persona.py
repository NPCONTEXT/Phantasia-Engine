"""离线测试：幻想引擎版本二的人格化位置（11-14 作者：「APP改名叫幻想引擎」「凌雨澜负责接替专辑的封面人物以及聊天气泡」
「版本二」）。不碰设备、不开真窗口。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_persona.py [代码目录]
  代码目录不写就是本副本；写一个解出来的旧版本目录（git archive 基线-1114）应当全不过——用来确认这些用例真的在查改动。

N1 名牌和名字：气泡名牌写「雨澜」；确认框标题、系统通知副标题、没配名字时的默认名都是「幻想引擎」
N2 左上角用引擎的标记（mark.png），封面还用头像（logo.png）
N3 配色是版本二（银白底、冰蓝、淡金名牌），全局样式里没有旧的粉色滚动条
N4 坏消息气泡的说法：没做完只报句名的前半截、异常退出、没达成、播不了都带句号
N5 几首都出问题时合成一句
N6 暂停没停成时说的两句（另一句由 test_pause 的 P1、P3 查）
"""
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent
sys.path.insert(0, str(HERE))
import test_subitems as ts  # noqa: E402  复用它的 Env 和 Checker
from test_pause import bubble_text, pump_for  # noqa: E402


def tools() -> list[dict]:
    base = {"env": "none", "workdir": str(HERE), "need_device": False, "album": "测试", "tasks": ["一句"],
            "report_steps": True, "command": [sys.executable, "-c", "print('ok')"]}
    return [dict(base, id="fa", name="测试 · A"), dict(base, id="fb", name="测试 · B")]


def rec(tid: str, name: str, result: str, **kw) -> dict:
    r = {"id": tid, "name": name, "start": 1.0, "end": 61.0, "dur": 60.0, "result": result, "exit": 0, "steps": 3,
         "missed": [], "facts": [], "outcome": "", "log": f"{tid}.log"}
    r.update(kw)
    return r


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hxyq_persona_")).resolve()
    ok_all = True
    try:
        (tmp / "runs" / "shots").mkdir(parents=True)
        env = ts.Env(ROOT, tmp, tools())
        s, win, c, W, H = env.s, env.win, ts.Checker(), env.W, env.history
        win.monitor.set_on = lambda on: None
        from ui import device, dialogs, popups, session, theme
        pump_for(env, 0.4)

        c.start("N1 名牌和名字")
        c.ok(getattr(popups, "TAG", None) == "雨澜", f"气泡名牌应是「雨澜」：{getattr(popups, 'TAG', None)!r}")
        n_title = inspect.getsource(dialogs).count('_Ask(parent, "幻想引擎", logo,')
        c.ok(n_title == 5, f"5 个确认框的标题都应是「幻想引擎」：{n_title} 个")
        c.ok((device.notify.__defaults__ or ("",))[-1] == "幻想引擎", f"系统通知副标题：{device.notify.__defaults__!r}")
        c.ok('app_cfg.get("name", "幻想引擎")' in inspect.getsource(W.MainWindow.__init__), "没配名字时默认应叫「幻想引擎」")

        c.start("N2 左上角是引擎的标记")
        mark = getattr(win, "mark", None)
        c.ok(bool(mark) and mark.endswith("mark.png") and Path(mark).exists(), f"窗口应认到 mark.png：{mark!r}")
        dots = win.findChildren(W.LogoDot)
        c.ok(len(dots) == 1 and dots[0]._path == mark, f"左上角圆点应画 mark.png：{[d._path for d in dots]}")
        c.ok(win.disc._path.endswith("logo.png"), f"封面应还是头像 logo.png：{win.disc._path}")

        c.start("N3 配色是版本二")
        c.ok((theme.CREAM, theme.PINK, theme.BLUSH, theme.INK) == ("#F4F6FB", "#BCD0F7", "#F2D98C", "#232838"),
             f"底色 / 强调 / 名牌 / 墨线：{(theme.CREAM, theme.PINK, theme.BLUSH, theme.INK)}")
        c.ok("#D9C6CF" not in theme.APP_QSS and "#C9D1E0" in theme.APP_QSS, "全局样式的滚动条应换成冷色")
        c.ok(W.ALBUM_COLORS[0] == theme.PINK, "第一张专辑的色块应跟着强调色")
        c.ok(win.bubble._tag_bg == theme.BLUSH, f"名牌底色应是淡金：{win.bubble._tag_bg}")

        c.start("N4 坏消息气泡的说法")
        missed = [{"name": "竞技场：挂机奖励+新人竞技场5次", "reason": "没进去", "no": 7},
                  {"name": "招募(每日免费一次)", "reason": "没点到", "no": 12}]
        win._on_event({"kind": "finished", "rec": rec("fa", "测试 · A", H.PARTIAL, missed=missed)})
        t = bubble_text(win)
        c.ok("：2 句没做完。竞技场、招募。" in t, f"没做完：{t!r}")
        c.ok("挂机奖励" not in t and "每日免费" not in t, f"句名只说到冒号、括号前：{t!r}")
        win._issues.clear()
        win._on_event({"kind": "finished", "rec": rec("fa", "测试 · A", H.ABNORMAL, exit=3)})
        c.ok(bubble_text(win).endswith("</b>：异常退出。"), f"异常退出：{bubble_text(win)!r}")
        win._issues.clear()
        win._on_event({"kind": "finished", "rec": rec("fa", "测试 · A", H.UNMET, outcome="18000 分")})
        c.ok(bubble_text(win).endswith("</b>：没达成。18000 分。"), f"没达成：{bubble_text(win)!r}")
        win._issues.clear()
        win._on_event({"kind": "nostart", "name": "测试 · B", "reason": "没连上设备"})
        c.ok(bubble_text(win).endswith("</b>：播不了。没连上设备。"), f"播不了：{bubble_text(win)!r}")
        win._issues.clear()
        win._on_event({"kind": "nostart", "name": "测试 · B", "reason": "设备锁屏了。"})
        c.ok(bubble_text(win).endswith("</b>：播不了。设备锁屏了。"), f"原因自带句号时不重复：{bubble_text(win)!r}")

        c.start("N5 几首都出问题")
        win._issues.clear()
        win._on_event({"kind": "nostart", "name": "测试 · A", "reason": "没连上设备"})
        win._on_event({"kind": "nostart", "name": "测试 · A", "reason": "没连上设备"})
        t = bubble_text(win)
        c.ok("测试 · A</b>：" in t and "2 次</b>没播好。" in t, f"同一首两次：{t!r}")
        win._on_event({"kind": "nostart", "name": "测试 · B", "reason": "没连上设备"})
        t = bubble_text(win)
        c.ok("2 首</b>没播好：测试 · A、测试 · B。" in t, f"两首：{t!r}")
        win._issues.clear()
        win.bubble.hide()

        c.start("N6 暂停没停成时的说法")
        src = inspect.getsource(session)
        c.ok(src.count("。这一场打完再停。") == 2, "战斗中点暂停的两处都应是「……。这一场打完再停。」")
        c.ok('"text": "这一步做完，停。"' in src, "2 秒没停住时应说「这一步做完，停。」")
        c.ok("打完这一场再暂停" not in src and "这一下做完就停" not in src, "旧说法不该还在")
        c.ok('"[引擎] 已暂停" in line' in src, "认「已暂停」要认「[引擎]」前缀")
        from core import runner as _rn
        rsrc = inspect.getsource(_rn)
        c.ok(rsrc.count("[引擎]") == 5, f"日志前缀应全是「[引擎]」：{rsrc.count('[引擎]')} 处")
        c.ok('cenv["PLAY_CTL"]' in rsrc, "控制目录的环境变量应叫 PLAY_CTL")
        c.ok((ROOT / 'lib' / 'play_ctl.py').is_file(), "暂停模块应叫 lib/play_ctl.py")
        win._say("这一步做完，停。", ms=10 ** 6)
        pump_for(env, 0.2)
        b = win.bubble
        c.ok(b.isVisible() and b.x() >= 0 and b.x() + b.width() <= win.width() and b.y() >= 0, f"气泡应整个在窗口里：{b.geometry()}")

        for name, errs in c.res.items():
            print(f"{name}：{'通过' if not errs else '失败'}")
            for e in errs:
                print("  - " + e)
            ok_all = ok_all and not errs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("临时目录已删除：", not tmp.exists())
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
