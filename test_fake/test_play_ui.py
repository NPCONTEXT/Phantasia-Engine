"""离线测试：第五版的播放区 / 待播 / 拖动排序 / 监控（11-02，作者试用后提的）。
不碰设备（监控取帧换成什么都不做）、不开真窗口；拖动不经真鼠标，直接测放下以后调的函数和放下位置的算法。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_play_ui.py

U1 空闲时播放区 = 播放中的样子：选中的那首、00:00、空进度格、共 N 句、▶、下一首能点；什么都没选时按钮收起
U2 空闲「下一首」：有待播 = 换成待播第一首（并从待播拿掉）；没有 = 勾了的下一首（按序号转一圈）；都没勾 = 歌单下一行
U3 空闲点 ▶：播选中的那首，拖进待播的接在后面
U4 播放中「下一首」：有待播 = 下一首；没有 = 「结束这首」，确认框点取消不停、点结束才停
U5 加进待播 / 待播里挪位置：插入位置对、已在待播里就挪、正在播的那首加不进
U6 歌单里拖动排序：挪到某首前 / 后、跨专辑不行；放下位置按行的上半 / 下半算
U7 小音柱静止：过一会儿再画，像素一样
U8 监控：打开后歌单只剩一行（选中 / 正在播那首，写全名）、画面区出现；播放时那一行跟着正在播的；关掉恢复
U9 详情标题旁：正在播时不挂贴纸，待播时挂静止的「待播」
U10 待播列表：空的时候点封面也能开、写着怎么加；放下位置按行的上半 / 下半算
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_subitems as ts  # noqa: E402  复用它的 Env 和 Checker
from test_pause import has_line, pump_for, wait_idle, wait_line  # noqa: E402

PY39 = ts.FAKE_PY


def tools(tmp: Path) -> list[dict]:
    base = {"env": "none", "workdir": str(HERE), "python": PY39, "need_device": False, "album": "测试",
            "tasks": ["第一句", "第二句", "第三句"], "report_steps": True, "command": ["python", "fake_pause.py"]}
    wd = tmp / "plain"
    wd.mkdir(parents=True, exist_ok=True)
    other = {"id": "other", "name": "别的专辑 · 一首", "env": "none", "workdir": str(wd), "need_device": False,
             "album": "别的专辑", "tasks": ["一句"], "report_steps": True,
             "command": ["/bin/sh", "-c", "echo '== 一句'; echo '完成：一句=ok'"]}
    return [dict(base, id="fa", name="测试 · A"), dict(base, id="fb", name="测试 · B"),
            dict(base, id="fc", name="测试 · C"), other]


def ids(s) -> list[str]:
    return [t["id"] for t in s.ordered()]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_playui_")).resolve()
    ok_all = True
    try:
        env = ts.Env(ROOT, tmp, tools(tmp))
        s, win, c = env.s, env.win, ts.Checker()
        win.monitor.set_on = lambda on: None          # 监控取帧会连设备，这里不取
        store = {"data": env.registry.load()}         # 拖动排序要真保存：tools.json 换成内存里的一份
        env.registry.load = lambda: json.loads(json.dumps(store["data"]))
        env.registry.save = lambda d: store.update(data=json.loads(json.dumps({k: v for k, v in d.items() if not k.startswith("_")})))
        from ui import widgets as WG
        os.environ["FAKE_STEP_SEC"] = "0.3"

        c.start("U1 空闲时播放区 = 播放中的样子")
        s.select(None)
        env.pump()
        c.ok(not win.btn_pause.isVisible() and not win.btn_next.isVisible(), "什么都没选时播放区的按钮应收起")
        s.select("fa")
        env.pump()
        c.ok(win.pname.text() == "测试 · A", f"歌名：{win.pname.text()}")
        c.ok(win.ptime.isVisible() and win.ptime.text() == "00:00", f"计时：{win.ptime.text()}")
        c.ok(win.segs.isVisible() and win.segs._total == 3 and not win.segs._done and win.segs._cur is None,
             f"进度格：{win.segs._total} {win.segs._done} {win.segs._cur}")
        c.ok(win.pcnt.isVisible() and win.pcnt.text() == "共 3 句", f"句数：{win.pcnt.text()}")
        c.ok(win.btn_pause.isVisible() and win.btn_pause.isEnabled() and win.btn_pause._mode == "play", "暂停钮应显示成 ▶ 且能点")
        c.ok(win.btn_next.isVisible() and win.btn_next.isEnabled() and win.btn_next._mode == "next", "下一首应能点")

        c.start("U2 空闲「下一首」")
        s.checked = []
        s.select("fa")
        win._next_clicked()
        c.ok(s.selected == "fb", f"都没勾：应是歌单下一行 fb：{s.selected}")
        for _ in range(3):
            win._next_clicked()
        c.ok(s.selected == "fa", f"都没勾：转一圈回到 fa：{s.selected}")
        s.checked = ["fc", "fa"]                      # 按歌单顺序是 fa、fc
        s.select("fb")
        win._next_clicked()
        c.ok(s.selected == "fa", f"有勾选、当前不在勾选里：应是第一个勾的 fa：{s.selected}")
        win._next_clicked()
        c.ok(s.selected == "fc", f"勾了的下一首 fc：{s.selected}")
        win._next_clicked()
        c.ok(s.selected == "fa", f"勾了的转一圈回 fa：{s.selected}")
        s.enqueue("fb")
        win._next_clicked()
        c.ok(s.selected == "fb" and s.queue == [], f"有待播：换成待播第一首、从待播拿掉：{s.selected} / {s.queue}")
        s.checked = []

        c.start("U3 空闲点 ▶：播选中的，待播接在后面")
        n0 = len(s.records)
        s.select("fa")
        s.enqueue("fc")
        win._pause_clicked()
        c.ok(wait_idle(env, 20), "没播完")
        new = [r["id"] for r in s.records[:len(s.records) - n0]]
        c.ok(new == ["fc", "fa"], f"应先 fa 后 fc（记录新的在前）：{new}")

        c.start("U4 播放中「下一首」/「结束这首」")
        os.environ["FAKE_STEP_SEC"] = "2"
        s.play("fa")
        c.ok(wait_line(env, "fa", "== 第一句"), "没等到第一句")
        c.ok(win.btn_next._mode == "stop", f"没有待播时下一首应变成结束这首：{win.btn_next._mode}")
        asked = []
        env.W.dialogs.confirm_end = lambda *a: (asked.append(a), False)[1]
        win._next_clicked()
        pump_for(env, 0.3)
        c.ok(asked and s.cur is not None and not s.cur.stopping, "确认框点取消：不该停")
        s.enqueue("fb")
        env.pump()
        c.ok(win.btn_next._mode == "next", f"有待播时应是下一首：{win.btn_next._mode}")
        s.queue_remove("fb")
        env.pump()
        env.W.dialogs.confirm_end = lambda *a: True
        win._next_clicked()
        c.ok(wait_idle(env, 12), "确认结束后 12 秒内没停")
        c.ok(s.records[0]["id"] == "fa" and s.records[0]["result"] == "stopped", f"记录：{s.records[0]['id']} {s.records[0]['result']}")
        os.environ["FAKE_STEP_SEC"] = "0.3"

        c.start("U5 加进待播 / 待播里挪位置")
        s.queue.clear()
        s.enqueue("fa")
        s.enqueue("fb", 0)
        s.enqueue("fc")
        c.ok(s.queue == ["fb", "fa", "fc"], f"插入位置：{s.queue}")
        s.queue_move_to("fc", 0)
        c.ok(s.queue == ["fc", "fb", "fa"], f"挪到最前：{s.queue}")
        s.queue_move_to("fc", 3)
        c.ok(s.queue == ["fb", "fa", "fc"], f"挪到最后：{s.queue}")
        s.enqueue("fa", 3)
        c.ok(s.queue == ["fb", "fc", "fa"], f"已在待播里再拖进来 = 挪位置：{s.queue}")
        s.queue.clear()
        os.environ["FAKE_STEP_SEC"] = "1"
        s.play("fa")
        env.wait(lambda: s.cur is not None, 10)
        n_t = len(ts.TOASTS)
        c.ok(not s.enqueue("fa") and "fa" not in s.queue, "正在播的那首不该加进待播")
        c.ok(any("正在播" in t for t in ts.TOASTS[n_t:]), f"提示：{ts.TOASTS[n_t:]}")
        c.ok(wait_idle(env, 15), "没播完")
        os.environ["FAKE_STEP_SEC"] = "0.3"

        c.start("U6 歌单里拖动排序")
        s.move_to("fb", "fa", False)
        c.ok(ids(s)[:3] == ["fb", "fa", "fc"], f"fb 挪到 fa 前面：{ids(s)}")
        s.move_to("fb", "fc", True)
        c.ok(ids(s)[:3] == ["fa", "fc", "fb"], f"fb 挪到 fc 后面：{ids(s)}")
        n_t = len(ts.TOASTS)
        c.ok(not s.move_to("fa", "other", False) and ids(s)[:3] == ["fa", "fc", "fb"], "跨专辑不该挪")
        c.ok(any("同一张专辑" in t for t in ts.TOASTS[n_t:]), f"跨专辑提示：{ts.TOASTS[n_t:]}")
        s.move_to("fb", "fc", False)                  # 还原成 fa、fb、fc
        env.pump()
        box = win.rows_area.widget()
        row = win._rows["fc"]
        g = row.geometry()
        c.ok(box.target(g.top() + 3) == (row, False), "放在行的上半 = 挪到它前面")
        c.ok(box.target(g.bottom() - 3) == (row, True), "放在行的下半 = 挪到它后面")

        c.start("U7 小音柱静止")
        eq = WG.EqIcon()
        eq.resize(16, 14)
        a = eq.grab().toImage()
        pump_for(env, 0.4)
        b = eq.grab().toImage()
        c.ok(a == b, "歌词当前句的小音柱隔 0.4 秒再画应一样")
        sb = WG.SeqBadge()
        sb.set("eq", 0)
        sb.resize(28, 28)
        a = sb.grab().toImage()
        pump_for(env, 0.4)
        b = sb.grab().toImage()
        c.ok(a == b, "歌单那一行的小音柱隔 0.4 秒再画应一样")

        c.start("U8 监控收成一行")
        s.select("fb")
        env.pump()
        win._set_monitor(True)
        pump_for(env, 0.2)
        c.ok(list(win._rows) == ["fb"], f"打开监控后只剩选中那一行：{list(win._rows)}")
        c.ok(win._rows["fb"].name.text() == "测试 · B", f"那一行写全名：{win._rows['fb'].name.text()}")
        c.ok(win.mon_view.isVisible() and win.btn_mon.kind == "cy", "画面区应出现、监控按钮变青")
        os.environ["FAKE_STEP_SEC"] = "1"
        s.play("fa")
        c.ok(env.wait(lambda: list(win._rows) == ["fa"], 10), f"播放时那一行跟着正在播的：{list(win._rows)}")
        c.ok(wait_idle(env, 15), "没播完")
        win._set_monitor(False)
        pump_for(env, 0.2)
        c.ok(sorted(win._rows) == ["fa", "fb", "fc", "other"] and not win.mon_view.isVisible(), f"关掉后恢复：{sorted(win._rows)}")
        os.environ["FAKE_STEP_SEC"] = "0.3"

        c.start("U9 详情标题旁的贴纸")
        os.environ["FAKE_STEP_SEC"] = "1"
        s.play("fa")
        env.wait(lambda: s.cur is not None, 10)
        pump_for(env, 0.2)
        c.ok(not win.tag.isVisible(), "正在播的那首：标题旁不挂贴纸")
        s.enqueue("fb")
        s.select("fb")
        env.pump()
        c.ok(win.tag.isVisible() and win.tag._kind == "wait", f"待播的那首：挂静止的「待播」（{win.tag._kind}）")
        s.queue_remove("fb")
        c.ok(wait_idle(env, 15), "没播完")
        os.environ["FAKE_STEP_SEC"] = "0.3"

        c.start("U10 待播列表")
        s.queue.clear()
        env.pump()
        win._toggle_queue()
        env.pump()
        from PySide6.QtWidgets import QLabel
        texts = [w.text() for w in win.qpop.findChildren(QLabel)]
        c.ok(win.qpop.isVisible() and any("拖到封面上" in x for x in texts), f"空的时候也能开、写着怎么加：{texts}")
        win._close_queue()
        s.enqueue("fa")
        s.enqueue("fb")
        win._toggle_queue()
        env.pump()
        rows = win.qpop._qrows
        c.ok([r.tid for r in rows] == ["fa", "fb"], f"待播行：{[r.tid for r in rows]}")
        if len(rows) == 2:
            c.ok(win.qpop.drop_index(rows[0].geometry().center().y() - 2) == 0, "放在第一行上半 = 插到最前")
            c.ok(win.qpop.drop_index(rows[1].geometry().center().y() + 2) == 2, "放在最后一行下半 = 插到最后")
        win._close_queue()
        s.queue.clear()

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
