"""假乐土：按乐土脚本的约定输出 JSON 事件行（角色、层数、积分、结束语），供幻想引擎「角色」离线测试和试用（不碰设备）。
--role 角色id（不给 = fuhua）；FAKE_SLEEP 每步秒数；FAKE_END=weekly_done / complete / stop 决定怎么结束。"""
import argparse, json, os, sys, time
ap = argparse.ArgumentParser(); ap.add_argument("--role", default=None)
ap.add_argument("--resume", action="store_true"); ap.add_argument("--from-main", action="store_true"); ap.add_argument("--home", action="store_true")
a = ap.parse_args()
sl = float(os.environ.get("FAKE_SLEEP", "0.4"))
end = os.environ.get("FAKE_END", "complete")
def ev(event, **kw):
    print(json.dumps(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), event=event, **kw), ensure_ascii=False), flush=True)
ev("role", id=a.role or "fuhua", label={"fuhua": "符华·渡尘之羽"}.get(a.role or "fuhua", a.role))
ev("entry_progress", page="chuji", weekly=0); time.sleep(sl)
if end == "weekly_done":
    ev("weekly_done", reason="本周九档已领，不开新一局"); ev("home_verified"); sys.exit(0)
for floor, score in ((8, 3000), (9, 6000), (10, 8000)):
    ev("progress", floor=floor, score=score); ev("observe", state="battle"); time.sleep(sl)
    ev("enter_verified", door="鏖灭", state="world", floor=floor + 1)
if end == "stop":
    ev("terminated", reason="假乐土：模拟中途停下"); sys.exit(2)
ev("complete", claimed_tiers=9, settlement_verified=True); ev("home_verified"); sys.exit(0)
