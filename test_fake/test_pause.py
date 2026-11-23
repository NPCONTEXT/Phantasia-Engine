"""离线测试：暂停 + 停止连播新语义 + 跟着播放器（11-02）。
不碰设备、不开真窗口、不轮询 adb。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_pause.py

P1 没接上暂停的歌：点暂停弹回，幻想引擎说「没接上暂停」
P2 暂停 / 继续：脚本 2 秒内停住、停着时不往下走、已播时长和动画都停；继续后接着走；暂停的时间不算进脚本自己的计时和记录耗时
P3 战斗中点暂停：弹回，幻想引擎说「正在战斗（假战斗）。这一场打完再停。」，不放暂停标记
P4 暂停中停止：照样停下，控制目录收掉，动画恢复
P5 停止连播：只清掉待播，这首不打断、播完就停；按钮变回灰的「顺序播放」
P6 跟着播放器：开播、换到下一首时选中它、页签切到歌词；同一首播放中点别的歌不被拉回
P7 模块单独跑（没有 PLAY_CTL）：什么都不做、计时不改（Python 3.9）
P8 脚本另起的子进程不把「战斗中」盖掉，战斗里子进程不停
P9 动画时钟：冻住时不走，放开后从停下的地方接着走
P10 mark（给乐土这种每轮看页面的脚本）：标了不能停就不停、能停时停过返回 True、state.json 带 say
P11 弹回时幻想引擎说脚本给的整句（say）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_subitems as ts  # noqa: E402  复用它的 Env（落盘指到临时目录、不写 tools.json）和 Checker

PY39 = ts.FAKE_PY        # bh3_auto 的 .venv（Python 3.9），顺带测模块兼容 3.9
LIB = ROOT / "lib"


def tools(tmp: Path) -> list[dict]:
    base = {"env": "none", "workdir": str(HERE), "python": PY39, "need_device": False, "album": "测试",
            "tasks": ["第一句", "第二句", "第三句"], "report_steps": True, "command": ["python", "fake_pause.py"]}
    wd = tmp / "slow"
    wd.mkdir(parents=True, exist_ok=True)
    slow = {"id": "slow_sh", "name": "没接暂停的慢工具", "env": "none", "workdir": str(wd), "need_device": False,
            "album": "测试", "tasks": ["第一句", "第二句"], "report_steps": True,
            "command": [sys.executable, "-c", "import time; print('== 第一句', flush=True); time.sleep(1.5); print('== 第二句', flush=True); time.sleep(0.3); print('完成：第一句=ok，第二句=ok', flush=True)"]}
    return [dict(base, id="fake_pause", name="假暂停工具 A"), dict(base, id="fake_pause2", name="假暂停工具 B"), slow]


def pump_for(env, sec: float):
    t0 = time.time()
    while time.time() - t0 < sec:
        env.pump()
        time.sleep(0.01)


def has_line(env, tid: str, text: str) -> bool:
    buf = env.s.logs.get(tid)
    return bool(buf) and any(text in ln for ln, _k in buf.lines)


def wait_line(env, tid: str, text: str, timeout: float = 10) -> bool:
    return env.wait(lambda: has_line(env, tid, text), timeout)


def wait_idle(env, timeout: float = 30) -> bool:
    return env.wait(lambda: not env.s.busy, timeout)


def bubble_text(win) -> str:
    return win.bubble.msg.text() if win.bubble.isVisible() else ""


def p7_p8_p9(env, c, tmp: Path):
    clean = {k: v for k, v in os.environ.items() if k != "PLAY_CTL"}
    clean["PYTHONPATH"] = str(LIB)
    if os.name == "nt":      # 11-24：和引擎给脚本的环境一样（core/env.py child_env），输出用 UTF-8
        clean.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    c.start("P7 模块单独跑（没有 PLAY_CTL）")
    code = ("import sys, time, play_ctl as x\n"
            "assert not x.ON and time.time is x._real_time and time.monotonic is x._real_mono\n"
            "x.pause_point()\n"
            "with x.battle('战斗'):\n    pass\n"
            "@x.battle('装饰器')\ndef f():\n    return 1\n"
            "assert f() == 1\n"
            "print('ok', sys.version_info[:2])")
    r = subprocess.run([PY39, "-c", code], env=clean, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    c.ok(r.returncode == 0 and r.stdout.startswith("ok (3, 12)" if os.name == "nt" else "ok (3, 9)"), f"返回 {r.returncode}：{r.stdout.strip()} {r.stderr.strip()[-300:]}")

    c.start("P8 子进程不把「战斗中」盖掉")
    ctl = tmp / "ctl_p8"
    ctl.mkdir()
    (ctl / "pause").touch()
    code = ("import json, os, subprocess, sys\n"
            "from play_ctl import battle\n"
            "def st():\n    return json.load(open(os.path.join(os.environ['PLAY_CTL'], 'state.json'), encoding='utf-8'))\n"
            "with battle('父进程的战斗'):\n"
            "    child = \"import time, play_ctl as x; t = time.time(); x.pause_point(); print('没停' if time.time() - t < 1 else '停了')\"\n"
            "    r = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True, timeout=10)\n"
            "    s = st()\n"
            "    print(r.stdout.strip(), s['state'], s['why'])\n"
            "print(st()['state'])")
    r = subprocess.run([PY39, "-c", code], env=dict(clean, PLAY_CTL=str(ctl)), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    out = r.stdout.strip().splitlines()
    c.ok(out == ["没停 battle 父进程的战斗", "run"], f"输出：{out} {r.stderr.strip()[-300:]}")

    c.start("P10 mark：每轮报能不能停；停过返回 True")
    ctl = tmp / "ctl_p10"
    ctl.mkdir()
    (ctl / "pause").touch()
    code = ("import json, os, threading\n"
            "from play_ctl import mark, pause_point\n"
            "def st():\n    return json.load(open(os.path.join(os.environ['PLAY_CTL'], 'state.json'), encoding='utf-8'))\n"
            "mark('正在打乐土', say='乐土正在战斗或找门，到选刻印页时才能停')\n"
            "s = st()\nprint(s['state'], s['why'], s['say'])\n"
            "print('标了不能停：', pause_point())\n"
            "mark(None)\nprint(st()['state'])\n"
            "threading.Timer(0.5, lambda: os.remove(os.path.join(os.environ['PLAY_CTL'], 'pause'))).start()\n"
            "print('能停：', pause_point())\n")
    r = subprocess.run([PY39, "-c", code], env=dict(clean, PLAY_CTL=str(ctl)), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    out = [ln for ln in r.stdout.strip().splitlines() if "[引擎]" not in ln]
    c.ok(out == ["battle 正在打乐土 乐土正在战斗或找门，到选刻印页时才能停", "标了不能停： False", "run", "能停： True"],
         f"输出：{out} {r.stderr.strip()[-300:]}")

    c.start("P9 动画时钟冻住 / 放开")
    from ui.widgets import Anim
    a = Anim.get()
    if a.reduce:
        print("  P9 系统开了「减少动态效果」，动画时钟本来就不走，跳过")
        return
    a.freeze(False)
    a.freeze(True)
    f1 = a.now()
    pump_for(env, 0.3)
    c.ok(a.now() == f1, f"冻住时不该走：{f1} → {a.now()}")
    a.freeze(False)
    t2 = a.now()
    c.ok(abs(t2 - f1) < 0.05, f"放开后应从停下的地方接着走：{f1} → {t2}")
    pump_for(env, 0.2)
    c.ok(a.now() > t2 + 0.1, "放开后应接着走")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_pause_")).resolve()
    ok_all = True
    try:
        env = ts.Env(ROOT, tmp, tools(tmp))
        s, win, c = env.s, env.win, ts.Checker()
        from ui.widgets import Anim
        p7_p8_p9(env, c, tmp)

        c.start("P1 没接上暂停的歌")
        s.play("slow_sh")
        c.ok(wait_line(env, "slow_sh", "== 第一句"), "没等到第一句")
        win._pause_clicked()
        c.ok(s.cur is not None and not s.cur.paused, "没接上暂停的歌不该进入暂停")
        c.ok("没接上暂停" in bubble_text(win), f"幻想引擎气泡：{bubble_text(win)!r}")
        c.ok(wait_idle(env, 15), "没播完")

        c.start("P2 暂停 / 继续")
        os.environ["FAKE_STEP_SEC"] = "1.5"
        s.play("fake_pause")
        c.ok(wait_line(env, "fake_pause", "== 第一句"), "没等到第一句")
        pump_for(env, 0.3)
        win._pause_clicked()
        st = s.cur
        c.ok(st is not None and st.paused, "点了暂停应进入暂停")
        c.ok(win.btn_pause._mode == "play", f"暂停钮应变成继续：{win.btn_pause._mode}")
        c.ok(Anim.get().frozen or Anim.get().reduce, "暂停时动画时钟应停住")
        c.ok(wait_line(env, "fake_pause", "[引擎] 已暂停", 2), "2 秒内脚本应停住（日志「[引擎] 已暂停」）")
        c.ok(st.pause_confirmed, "看到「已暂停」后应记成脚本已停住")
        ctl = s.runner.current.ctl
        c.ok(json.loads((ctl / "state.json").read_text(encoding="utf-8")).get("state") == "paused", "state.json 应是 paused")
        t_text, a_now = win.ptime.text(), Anim.get().now()
        pump_for(env, 2.2)
        c.ok(not has_line(env, "fake_pause", "== 第二句"), "停着时不该往下走")
        c.ok(win.ptime.text() == t_text, f"已播时长停着时不该走：{t_text} → {win.ptime.text()}")
        c.ok(Anim.get().now() == a_now, "动画时钟停着时不该走")
        win._pause_clicked()
        c.ok(not st.paused and not Anim.get().frozen and win.btn_pause._mode == "pause", "点继续后应恢复")
        c.ok(wait_line(env, "fake_pause", "[引擎] 继续", 2), "继续后日志应有「[引擎] 继续」")
        c.ok(wait_idle(env, 15), "没播完")
        rec = s.records[0]
        used = [float(m.group(1)) for ln in env.log_lines(rec) for m in [re.search(r"第一句用时 ([\d.]+) 秒", ln)] if m]
        c.ok(used and used[0] < 1.5 + 0.8, f"脚本自己的计时应扣掉暂停（第一句约 1.5 秒）：{used}")
        wall = rec["end"] - rec["start"]
        c.ok(rec.get("paused", 0) >= 2.0, f"记录里的暂停时长：{rec.get('paused')}")
        c.ok(abs((wall - rec["dur"]) - rec.get("paused", 0)) < 0.3, f"耗时应扣掉暂停：总共 {wall:.1f}，耗时 {rec['dur']}，暂停 {rec.get('paused')}")
        c.ok(rec["result"] == "ok", f"结果：{rec['result']}")

        c.start("P3 战斗中点暂停")
        s.play("fake_pause")
        c.ok(wait_line(env, "fake_pause", "== 第二句"), "没等到第二句（战斗）")
        pump_for(env, 0.3)
        win._pause_clicked()
        c.ok(s.cur is not None and not s.cur.paused, "战斗中不该进入暂停")
        c.ok("正在战斗（假战斗）。这一场打完再停。" in bubble_text(win), f"幻想引擎气泡：{bubble_text(win)!r}")
        c.ok(not (s.runner.current.ctl / "pause").exists(), "战斗中不该放暂停标记")
        c.ok(win.btn_pause._mode == "pause", f"暂停钮应弹回：{win.btn_pause._mode}")
        c.ok(wait_idle(env, 15), "没播完")
        c.ok(not any("[引擎] 已暂停" in ln for ln in env.log_lines(s.records[0])), "这次不该停过")

        c.start("P11 弹回时幻想引擎说脚本给的整句")
        os.environ["FAKE_SAY"] = "假战斗：等到选刻印页时才能停，请稍后再点"
        s.play("fake_pause")
        c.ok(wait_line(env, "fake_pause", "== 第二句"), "没等到第二句（战斗）")
        pump_for(env, 0.3)
        win._pause_clicked()
        c.ok(bubble_text(win) == "假战斗：等到选刻印页时才能停，请稍后再点", f"幻想引擎气泡：{bubble_text(win)!r}")
        c.ok(wait_idle(env, 15), "没播完")
        os.environ.pop("FAKE_SAY", None)

        c.start("P4 暂停中停止")
        s.play("fake_pause")
        c.ok(wait_line(env, "fake_pause", "== 第一句"), "没等到第一句")
        pump_for(env, 0.2)
        win._pause_clicked()
        c.ok(wait_line(env, "fake_pause", "[引擎] 已暂停", 2), "没停住")
        ctl = s.runner.current.ctl
        t0 = time.time()
        s.stop_current()
        c.ok(wait_idle(env, 12), "暂停中点停止，12 秒内没停下")
        took = time.time() - t0
        c.ok(s.records[0]["result"] == "stopped", f"结果：{s.records[0]['result']}（{took:.1f} 秒）")
        c.ok(not ctl.exists(), f"控制目录应收掉：{ctl}")
        c.ok(not Anim.get().frozen, "停下后动画时钟应放开")

        c.start("P5 停止连播 = 清掉待播、这首播完就停")
        os.environ["FAKE_STEP_SEC"] = "0.6"
        n0 = len(s.records)
        s.play("fake_pause")
        env.wait(lambda: s.cur is not None, 10)
        s.play("fake_pause2")
        env.pump()
        c.ok(s.queue == ["fake_pause2"], f"第二首应排进待播：{s.queue}")
        c.ok(win.btn_seq.text() == "停止连播", f"有待播时歌单头应是停止连播：{win.btn_seq.text()}")
        win._seq_clicked()
        env.pump()
        c.ok(s.queue == [] and s.cur is not None and not s.cur.stopping, "只清待播，这首不该停")
        c.ok(any("这首播完就停" in t for t in ts.TOASTS[-3:]), f"提示：{ts.TOASTS[-3:]}")
        c.ok(win.btn_seq.text().startswith("顺序播放") and not win.btn_seq.isEnabled(), f"在播时应是灰的顺序播放：{win.btn_seq.text()}")
        c.ok(wait_idle(env, 15), "没播完")
        new = s.records[:len(s.records) - n0]
        c.ok([r["id"] for r in new] == ["fake_pause"] and new[0]["result"] == "ok", f"新记录：{[(r['id'], r['result']) for r in new]}")

        c.start("P6 跟着播放器")
        s.select("slow_sh")
        env.pump()
        s.play("fake_pause")
        c.ok(env.wait(lambda: s.selected == "fake_pause", 5), f"开播后应选中正在播的：{s.selected}")
        s.play("fake_pause2")
        s.select("slow_sh")
        win.tabs.set_current("hist")
        pump_for(env, 0.3)
        c.ok(s.selected == "slow_sh", "同一首播放中点别的歌，不该被拉回")
        c.ok(env.wait(lambda: s.cur is not None and s.cur.id == "fake_pause2", 15), "没轮到第二首")
        pump_for(env, 0.3)
        c.ok(s.selected == "fake_pause2" and win.tabs._cur == "steps", f"换到第二首后应选中它、切到歌词页：{s.selected} / {win.tabs._cur}")
        c.ok(wait_idle(env, 15), "没播完")

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
