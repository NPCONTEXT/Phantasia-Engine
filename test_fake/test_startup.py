"""离屏测试（11-01 设计三）：每次启动后台自检；设备比例和脚本基准不一样就弹窗问要不要切换。
不碰设备：core.env 里查设备的几个函数全换成假的；确认框和切分辨率换成记录函数。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_startup.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_subitems as ts  # noqa: E402  复用它的 Env（落盘指到临时目录、不写 tools.json）和 Checker


def main() -> int:
    root = HERE.parent
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_startup_"))
    env = ts.Env(root, tmp, [ts.plain_tool(tmp)])
    win, c = env.win, ts.Checker()
    t0 = time.time()
    while time.time() - t0 < 2.0:          # 让窗口自己那次 1.5 秒后的自检先过去（Env 里关着，不会跑）
        env.pump(50)
    from core import env as E
    from ui import window as W
    asks, sets = [], []

    def fake_confirm(parent, logo, cur, base):
        asks.append((cur, base))
        return True
    W.dialogs.confirm_resolution = fake_confirm
    win.set_res = lambda mode: sets.append(mode)
    state = {"dev": {"serial": "FAKE1"}, "size": "1080x2340",
             "res": [(True, "adb: /x/adb"), (False, "工具[假] 文件自检 缺: a.py")]}
    E.check_environment = lambda tools: state["res"]
    E.device_online = lambda: state["dev"]
    E.wm_size = lambda: state["size"]

    # ---------------------------------------------------------------- S1
    c.start("S1 比例算法（长边 / 短边，横竖都算）")
    for size, ok in (("1504x2400", True), ("2400x1504 (Override)", True), ("2560x1600", True),
                     ("1080x2340", False), ("1920x1080", False), ("未知", True), ("", True)):
        c.ok(E.ratio_ok(size) is ok, f"{size!r} 应{'算' if ok else '不算'}和 2400x1504 同比例")

    # ---------------------------------------------------------------- S2
    c.start("S2 启动：比例不对 → 问一次 → 点切换就切；自检有问题 → 按钮红边 + 提示")
    win.startup_check = True
    win._on_device(state["dev"], state["size"])
    win._startup_check()
    c.ok(env.wait(lambda: getattr(win, "_startup_done_once", False), 10), "自检 10 秒没回来")
    env.pump(100)
    c.ok(asks == [("1080x2340", "2400x1504")], f"应问一次：{asks}")
    c.ok(sets == ["2400x1504"], f"点了切换应切到 2400x1504：{sets}")
    c.ok(win.btn_check.kind == "dan", f"自检有问题时「环境自检」应红边：{win.btn_check.kind}")
    c.ok("1 项有问题" in win.toast_lb.text() and win.toast_lb.isVisible(), f"提示：{win.toast_lb.text()!r}")

    # ---------------------------------------------------------------- S3
    c.start("S3 比例对、自检全过 → 不问、按钮正常")
    asks.clear()
    sets.clear()
    state.update(size="1504x2400", res=[(True, "adb: /x/adb")])
    win._ratio_asked.clear()
    win._startup_done_once = False
    win._startup_check()
    c.ok(env.wait(lambda: win._startup_done_once, 10), "自检 10 秒没回来")
    env.pump(100)
    c.ok(asks == [] and sets == [], f"不该问、不该切：{asks} {sets}")
    c.ok(win.btn_check.kind == "def", f"全过时按钮应是普通样子：{win.btn_check.kind}")

    # ---------------------------------------------------------------- S4
    c.start("S4 启动时没连设备、后来连上 → 连上时问一次；同一台不再问")
    asks.clear()
    sets.clear()
    state.update(size="1080x2340")
    win._ratio_asked.clear()
    win._on_device(None, "--")
    c.ok(win.btn_check.kind == "dan", f"没连设备时「环境自检」应红边：{win.btn_check.kind}")
    win._on_device({"serial": "FAKE2"}, "1080x2340")
    c.ok(env.wait(lambda: bool(asks), 10), "连上 10 秒还没问")
    env.pump(100)
    c.ok(asks == [("1080x2340", "2400x1504")], f"连上时应问一次：{asks}")
    win._on_device(None, "--")
    win._on_device({"serial": "FAKE2"}, "1080x2340")
    t0 = time.time()
    while time.time() - t0 < 0.5:
        env.pump(50)
    c.ok(len(asks) == 1, f"同一台设备这次不该再问：{asks}")

    # ---------------------------------------------------------------- S5
    c.start("S5 startup_check 关着时什么都不做")
    asks.clear()
    win.startup_check = False
    win._startup_done_once = False
    win._ratio_asked.clear()
    win._startup_check()
    t0 = time.time()
    while time.time() - t0 < 0.5:
        env.pump(50)
    c.ok(not win._startup_done_once and asks == [], f"关着还跑了：{win._startup_done_once} {asks}")

    bad = 0
    for k, v in c.res.items():
        print(f"{k}：{'通过' if not v else '没通过'}")
        for x in v:
            print("   -", x)
        bad += bool(v)
    shutil.rmtree(tmp, ignore_errors=True)
    print("临时目录已删除：", not tmp.exists())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
