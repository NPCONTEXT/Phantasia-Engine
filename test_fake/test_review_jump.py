"""离线测试：歌单行点「待验收 ›」跳到播放记录（11-03 作者：「待验收藏得太深了，需要你加一个左侧列表点击待验收，
自动跳转到对应播放记录区域」，挑了版本二：标签加「›」、跳到后那条记录描金框 2 秒）。不碰设备、不开真窗口。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_review_jump.py

J1 有结束截图没验收的歌，歌单行显示「待验收 ›」、手形；没截图的不显示
J2 点「待验收 ›」：选中这首、切到「播放记录」、没验收的那条滚到可见区最上面、盖一层金框；2 秒后金框撤掉、卡片自己的边框不动
J3 没验收的那条不是最新一条（上面压着 12 条新记录）：照样滚到它
J4 点标签不经过歌单行（行没记下按下位置 = 不会拖起来），另一首选中时点也跳对
J5 验收以后那行的「待验收 ›」消失
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
from test_pause import pump_for  # noqa: E402


def tools() -> list[dict]:
    base = {"env": "none", "workdir": str(HERE), "need_device": False, "album": "测试", "tasks": ["一句"],
            "report_steps": True, "command": ["/bin/sh", "-c", "echo ok"]}
    return [dict(base, id="fa", name="测试 · A"), dict(base, id="fb", name="测试 · B"), dict(base, id="fc", name="测试 · C")]


def rec(tid: str, start: float, shot: str = "") -> dict:
    r = {"id": tid, "name": {"fa": "测试 · A", "fb": "测试 · B", "fc": "测试 · C"}[tid], "start": start, "end": start + 60,
         "dur": 60.0, "result": "ok", "exit": 0, "steps": 1, "missed": [], "facts": [], "outcome": "", "log": f"{tid}_{int(start)}.log"}
    if shot:
        r["shot"] = shot
    return r


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_review_")).resolve()
    ok_all = True
    try:
        (tmp / "runs" / "shots").mkdir(parents=True)
        from PySide6.QtGui import QColor, QImage
        img = QImage(240, 150, QImage.Format.Format_RGB32)
        img.fill(QColor("#336699"))
        for n in ("fa_old.jpg", "fb_new.jpg"):
            img.save(str(tmp / "runs" / "shots" / n))
        now = time.time()
        lines = [rec("fa", now - 9000, "fa_old.jpg")]                       # A：没验收的那条比较旧……
        lines += [rec("fa", now - 8000 + i * 100) for i in range(12)]          # ……上面压着 12 条新的、没截图
        lines += [rec("fb", now - 500, "fb_new.jpg"), rec("fc", now - 400)]     # B：最新一条就没验收；C：没截图
        (tmp / "runs" / "history.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                                    encoding="utf-8")
        env = ts.Env(ROOT, tmp, tools())
        s, win, c = env.s, env.win, ts.Checker()
        win.monitor.set_on = lambda on: None
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        W = env.W
        pump_for(env, 0.5)

        def row(tid):
            return next(r for r in win.findChildren(W.ToolRow) if r.tid == tid)

        def card_of(r):
            for i in range(win.hist_box.count()):
                w = win.hist_box.itemAt(i).widget()
                if isinstance(w, W.RecCard) and w.rec.get("shot") == r.get("shot") and w.rec.get("start") == r.get("start"):
                    return w
            return None

        def click(tag):
            QTest.mouseClick(tag, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 9))
            pump_for(env, 0.3)

        c.start("J1 标签")
        for tid, want in (("fa", True), ("fb", True), ("fc", False)):
            tag = row(tid).ctag
            c.ok(tag.isVisible() == want, f"{tid} 待验收显示应为 {want}，实际 {tag.isVisible()}")
        tag = row("fa").ctag
        c.ok(tag.text() == "待验收 ›", f"标签字：{tag.text()!r}")
        c.ok(tag.cursor().shape() == Qt.CursorShape.PointingHandCursor, "标签不是手形")
        c.ok(tag.width() >= tag.sizeHint().width(), f"标签被挤窄：宽 {tag.width()} < 需要 {tag.sizeHint().width()}")

        c.start("J2 点了跳过去")
        s.select("fc")
        win.tabs.set_current("steps")
        pump_for(env, 0.3)
        click(row("fb").ctag)
        pend = s.pending_shot("fb")
        card = card_of(pend)
        c.ok(s.selected == "fb", f"选中的是 {s.selected}")
        c.ok(win.stack.currentIndex() == 2, f"中间页签不是播放记录：{win.stack.currentIndex()}")
        c.ok(card is not None, "播放记录里没找到没验收的那条")
        if card is not None:
            bar = win.hist_area.verticalScrollBar()
            c.ok(bar.value() == min(card.y(), bar.maximum()), f"没滚到那条：滚动 {bar.value()}，那条在 {card.y()}")
            from PySide6.QtWidgets import QFrame
            ov = card.findChild(QFrame, "recflash")
            c.ok(ov is not None and ov.isVisible() and ov.geometry() == card.rect(), "到了以后没描金框（或金框没盖满卡片）")
            c.ok("1.5px solid" in card.styleSheet(), "卡片自己的边框被改了")
            pump_for(env, 2.3)
            c.ok(card.findChild(QFrame, "recflash") is None, "2 秒后金框没撤掉")

        c.start("J3 不是最新一条也滚到")
        click(row("fa").ctag)
        pend = s.pending_shot("fa")
        card = card_of(pend)
        c.ok(s.selected == "fa", f"选中的是 {s.selected}")
        c.ok(card is not None, "播放记录里没找到 A 没验收的那条")
        if card is not None:
            bar = win.hist_area.verticalScrollBar()
            c.ok(card.y() > 0 and bar.maximum() > 0, f"夹具没起作用：那条在 {card.y()}、能滚 {bar.maximum()}")
            c.ok(bar.value() == min(card.y(), bar.maximum()) and bar.value() > 0, f"没滚到那条：滚动 {bar.value()}，那条在 {card.y()}")

        c.start("J4 点标签不经过歌单行")
        r = row("fb")
        r._press = None
        s.select("fa")
        pump_for(env, 0.2)
        QTest.mousePress(r.ctag, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 9))
        c.ok(r._press is None, "按下传到了歌单行（会被当成要拖动）")
        QTest.mouseRelease(r.ctag, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 9))
        pump_for(env, 0.3)
        c.ok(s.selected == "fb", f"另一首选中时点 B 的标签，选中的是 {s.selected}")

        c.start("J5 验收以后标签消失")
        s.accept_shot(s.pending_shot("fb"))
        pump_for(env, 0.5)
        c.ok(not row("fb").ctag.isVisible(), "验收后 B 还挂着待验收")
        c.ok(row("fa").ctag.isVisible(), "A 的待验收不该跟着没")

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
