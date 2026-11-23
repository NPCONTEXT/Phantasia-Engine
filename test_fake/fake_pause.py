"""假工具：测暂停协议（lib/play_ctl.py），不碰设备。
每 0.1 秒「截一次图」（调 pause_point）；第二句是一场战斗（包在 battle 里，战斗中点暂停应被弹回）。
环境变量 FAKE_STEP_SEC：每句多久（默认 1.5 秒）。每句结束打一行「第 N 句用时 X 秒」——用 time.monotonic 算，暂停的时间应被扣掉。"""
import os
import time

try:
    from play_ctl import pause_point, battle
except ImportError:          # 不经幻想引擎运行时没有这个模块
    import contextlib

    def pause_point():
        pass

    class battle(contextlib.ContextDecorator):
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

N = float(os.environ.get("FAKE_STEP_SEC", "1.5"))


def log(m):
    print(time.strftime("[%H:%M:%S] ") + m, flush=True)


def step(name, fight=False):
    log("== " + name)
    t0 = time.monotonic()

    def loop():
        while time.monotonic() - t0 < N:
            pause_point()
            time.sleep(0.1)
    if fight:
        with battle("正在战斗（假战斗）", os.environ.get("FAKE_SAY", "")):     # FAKE_SAY：弹回时幻想引擎说的整句
            loop()
    else:
        loop()
    log("%s用时 %.1f 秒" % (name, time.monotonic() - t0))


step("第一句")
step("第二句", fight=True)
step("第三句")
log("完成：第一句=ok，第二句=ok，第三句=ok")
