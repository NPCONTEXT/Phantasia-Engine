"""B5：用副本的真代码（离屏）真播一次记忆战场：勾 1、3 → 播放 → 真脚本在设备上跑（--dry 不开打）。
只读 / 不开打；结束截图存 /tmp/phantasia_b5_*.png。"""
import json, sys, tempfile, time
from pathlib import Path
COPY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COPY / "test_fake"))
import test_subitems as T

tools = [t for t in json.loads((COPY / "tools.json").read_text(encoding="utf-8"))["tools"] if t["id"] == "bh3_memorial_arena"]
assert tools and "--dry" in tools[0]["command"], tools
tmp = Path(tempfile.mkdtemp(prefix="phantasia_b5_"))
env = T.Env(COPY, tmp, tools)
s, win = env.s, env.win
s.select("bh3_memorial_arena"); env.pump()
print("空闲歌词：", env.lyric_texts())
boxes = [r.box is not None for r in win._lyrics]
print("勾选框：", boxes)
idx2 = [i for i, b in enumerate(boxes) if b][1]          # 第 2 个子项
win._lyrics[idx2].box.clicked.emit(); env.pump()
print("state picks：", env.state_json().get("picks"))
win.grab().save("/tmp/phantasia_b5_idle.png")
mid = {}
def during(e):
    st = e.s.cur
    if st and st.tracker.cur is not None and st.tracker.cur >= 2 and "shot" not in mid:
        mid["tasks"] = list(st.tracker.tasks); mid["cur"] = st.tracker.cur
        e.win.grab().save("/tmp/phantasia_b5_playing.png"); mid["shot"] = True
        return True
    return False
rec = env.play_and_wait("bh3_memorial_arena", during, timeout=360)
print("命令：", env.calls[-1]["cmd"] if env.calls else None)
print("运行中：", mid)
print("记录：", json.dumps({k: rec.get(k) for k in ("result", "steps", "missed", "picks", "tasks", "dur")} if rec else None, ensure_ascii=False))
if rec:
    print("日志：\n" + "\n".join(env.log_lines(rec)))
win.grab().save("/tmp/phantasia_b5_after.png")
