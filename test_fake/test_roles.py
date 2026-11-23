"""离线测试：一首歌只能选一个角色（乐土用谁打）+ 10-20 顺带修的两处。不碰设备、不开真窗口、不轮询 adb。

运行（在副本根目录）：
  QT_QPA_PLATFORM=offscreen PATH=/opt/homebrew/bin:/usr/local/bin:$PATH \\
    venv-qt.nosync/bin/python test_fake/test_roles.py
  只跑 R8、指定代码目录（拿基线核对 R8 改前会失败）：... test_roles.py --r8 <代码目录>

R1 默认角色、换角色、记住（没选过角色时 state.json 不多键）、角色栏只在带角色的歌显示
R2 播放：命令追加 --role、日志第一行写角色、记录带 role / role_label；播放中换不了、按钮不能点、播放栏带角色
R3 名单里删掉选中的角色 → 回到默认
R4 角色面板：最近用过、搜索（名字 / 拼音首字母 / 搜不到）、回车选第一个、点格子选中
R5 普通歌不受影响：命令不加参数、记录不多字段
R6 顺序播放第一首启动不了：不弹模态框（只发通知）；单首点播启动不了照旧弹框（审查 M6）
R8 工具退出后留下占着输出管道的子进程：哨兵收尾把它收掉，记录照常写、退出码 0（审查 H1、H2）
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_subitems as ts  # noqa: E402  复用它的 Env（落盘指到临时目录、不写 tools.json）和 Checker

PY = str(HERE.parent / "bh3_auto" / ".venv" / "bin" / "python")
ROLES = {"arg": "--role", "default": "fuhua", "items": [
    {"id": "fuhua", "label": "符华·渡尘之羽", "desc": "元素普攻 · 低血流", "py": "fh", "tag": "已实测"},
    {"id": "newone", "label": "新角色（今晚设置）", "desc": "设好后填打法", "py": "xjs", "tag": "待实测"}]}


def tools(tmp: Path) -> list[dict]:
    fake = {"id": "fake_er", "name": "假乐土（离线测角色）", "description": "测试用：模拟乐土脚本的 --role 协议。", "env": "none",
            "workdir": str(HERE), "command": ["python", "fake_er.py", "--resume", "--from-main", "--home"], "python": PY,
            "need_device": False, "album": "测试", "roles": ROLES,
            "tasks": ["进乐土", "战斗", "收尾"],
            "report": {"stages": [{"task": 1, "match": "\"event\":\\s*\"entry_progress\""},
                                  {"task": 2, "match": "\"event\":\\s*\"observe\".*\"state\":\\s*\"battle\""},
                                  {"task": 3, "match": "\"event\":\\s*\"(?:complete|weekly_done|home_verified)\""}],
                       "facts": [{"label": "第 {1} 层", "match": "\"event\":\\s*\"progress\".*\"floor\":\\s*(\\d+)"}],
                       "outcomes": [{"match": "\"event\":\\s*\"complete\"", "text": "20000分结算完成，九档周常已领取"}]}}
    plain = ts.plain_tool(tmp)
    dev = dict(plain, id="needs_dev", name="要设备的歌（离线测启动不了）", need_device=True)
    orphan = {"id": "orphan", "name": "留子进程的歌", "description": "", "env": "none", "workdir": str(HERE),
              "command": ["python", "fake_orphan.py"], "python": PY, "need_device": False, "album": "测试", "tasks": []}
    return [fake, plain, dev, orphan]


def r8(env, c):
    """退出后留下 sleep 30 占着管道：改前要等 30 秒才写记录（界面一直「正在播放」），改后几秒内写完、子进程被收掉。"""
    c.start("R8 留下的子进程被哨兵收掉，不卡在正在播放")
    t0 = time.time()
    rec = env.play_and_wait("orphan", timeout=12)
    took = time.time() - t0
    if c.ok(rec is not None, f"12 秒内没写播放记录（改前的样子：管道被 sleep 占着）"):
        c.ok(rec["exit"] == 0 and rec["result"] == "done", f"退出码 / 结果：{rec['exit']} / {rec['result']}")
        pid = next((int(m.group(1)) for ln in env.log_lines(rec) for m in [re.search(r"留下子进程 (\d+)", ln)] if m), None)
        if c.ok(pid, "日志里没有子进程号"):
            time.sleep(0.5)
            alive = subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0
            c.ok(not alive, f"子进程 {pid} 还活着")
    print(f"  R8 用时 {took:.1f} 秒")


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--r8":
        root = Path(sys.argv[2]).resolve()
        tmp = Path(tempfile.mkdtemp(prefix="phantasia_r8_"))
        env = ts.Env(root, tmp, tools(tmp))
        c = ts.Checker()
        r8(env, c)
        bad = {k: v for k, v in c.res.items() if v}
        print("R8:", "通过" if not bad else bad)
        return 1 if bad else 0

    root = HERE.parent
    tmp = Path(tempfile.mkdtemp(prefix="phantasia_roles_"))
    env = ts.Env(root, tmp, tools(tmp))
    s, win, c = env.s, env.win, ts.Checker()

    # ---------------------------------------------------------------- R1
    c.start("R1 默认角色、换角色、记住")
    s.set_notify(True)                                     # 落一次盘
    c.ok("roles" not in env.state_json(), f"没选过角色，state.json 不该多 roles 键：{env.state_json().keys()}")
    c.ok((s.role_of("fake_er") or {}).get("id") == "fuhua", "默认应是 fuhua")
    c.ok(s.role_of(ts.plain_tool(tmp)["id"]) is None, "普通歌没有角色")
    c.ok(s.set_role("fake_er", "nobody") is False and (s.role_of("fake_er") or {}).get("id") == "fuhua", "不存在的角色不该换")
    n0 = len(ts.TOASTS)
    c.ok(s.set_role("fake_er", "fuhua") is False and any("现在就是「符华" in t for t in ts.TOASTS[n0:]),
         f"点了当前角色应提示没换：{ts.TOASTS[n0:]}")
    s.select("fake_er")
    env.pump(100)
    c.ok(win.role_wrap.isVisible(), "选中带角色的歌，角色栏应显示")
    c.ok(win.role_bar.name.text() == "符华·渡尘之羽" and win.role_bar.tag.isVisible(), f"角色栏：{win.role_bar.name.text()}")
    n_toast = len(ts.TOASTS)
    c.ok(s.set_role("fake_er", "newone") is True, "换成 newone 应成功")
    env.pump(100)
    c.ok(env.state_json().get("roles") == {"fake_er": "newone"}, f"state.json roles：{env.state_json().get('roles')}")
    c.ok(any("已换成「新角色" in t for t in ts.TOASTS[n_toast:]), f"提示：{ts.TOASTS[n_toast:]}")
    c.ok(win.role_bar.name.text() == "新角色（今晚设置）", f"角色栏没跟着换：{win.role_bar.name.text()}")
    s.select(ts.plain_tool(tmp)["id"])
    env.pump(100)
    c.ok(not win.role_wrap.isVisible(), "普通歌不该显示角色栏")
    s2 = env.Session()                                     # 重开一次：记住了
    c.ok((s2.role_of("fake_er") or {}).get("id") == "newone", "重开后应还是 newone")

    # ---------------------------------------------------------------- R2
    c.start("R2 播放传参、记录、播放中锁定")
    s.select("fake_er")
    seen = {}

    def during(e):
        if e.s.cur is None or not e.s.cur.tracker.facts():
            return False
        n = len(ts.TOASTS)
        seen["set"] = e.s.set_role("fake_er", "fuhua")
        e.win._open_roles()                               # 锁着：只提示，不开面板
        e.pump(50)
        seen["toasts"] = ts.TOASTS[n:]
        seen["btn"] = e.win.role_bar.btn.isEnabled()
        seen["eq"] = e.win.role_bar.eq.isVisible()
        seen["sub"] = e.win.role_bar.sub.text()
        seen["pname"] = e.win.pname.text()
        return True

    rec = env.play_and_wait("fake_er", during=during, timeout=30)
    if c.ok(rec is not None, "假乐土没播完"):
        call = env.calls[-1]
        c.ok(call["cmd"][-2:] == ["--role", "newone"], f"命令：{call['cmd']}")
        c.ok(rec.get("role") == "newone" and rec.get("role_label") == "新角色（今晚设置）", f"记录：{rec}")
        first = env.log_lines(rec)[0]
        c.ok("本次角色：新角色（今晚设置）；传给脚本 --role newone" in first, f"日志第一行：{first}")
        c.ok(rec.get("outcome") == "20000分结算完成，九档周常已领取", f"结束语：{rec.get('outcome')}")
    c.ok(seen.get("set") is False, "播放中换角色应被拒绝")
    c.ok(any("下次播放才生效" in t for t in seen.get("toasts", [])), f"播放中的提示：{seen.get('toasts')}")
    c.ok(seen.get("btn") is False and seen.get("eq") is True, f"播放中按钮 / 音柱：{seen.get('btn')} / {seen.get('eq')}")
    c.ok(str(seen.get("sub", "")).startswith("正在打 · 第 "), f"播放中小字：{seen.get('sub')}")
    c.ok("新角色（今晚设置）" in str(seen.get("pname")), f"播放栏歌名：{seen.get('pname')}")
    c.ok(s.role_recent("fake_er") == ["newone"], f"最近用过：{s.role_recent('fake_er')}")
    env.pump(200)
    from ui.window import ToolRow
    erow = next((r for r in win.findChildren(ToolRow) if getattr(r, "tid", None) == "fake_er"), None)
    row_txt, row_tip = (erow.res.text(), erow.res.toolTip()) if erow else ("", "")
    # 11-01 信息精简：歌单行第二行只写「短时间 + 结果」，角色挪进悬停；角色栏小字只写打法（不带括号补充和「上次…」）
    c.ok("新角色" not in row_txt and "新角色（今晚设置）" in row_tip, f"歌单行第二行 / 悬停：{row_txt} / {row_tip}")
    c.ok(win.role_bar.sub.text() == "设好后填打法", f"播完后角色栏小字应只写打法：{win.role_bar.sub.text()}")

    # ---------------------------------------------------------------- R3
    c.start("R3 选中的角色被删掉 → 回到默认")
    tool = s.tool("fake_er")
    old = tool["roles"]
    tool["roles"] = dict(old, items=[old["items"][0]])
    c.ok((s.role_of("fake_er") or {}).get("id") == "fuhua", "删掉 newone 后应回到 fuhua")
    tool["roles"] = old

    # ---------------------------------------------------------------- R4
    c.start("R4 角色面板")
    from ui.roles import RolePanel
    dlg = RolePanel(win, "假乐土", ROLES["items"], "fuhua", ["newone"])
    dlg.show()
    env.pump(100)
    c.ok(len(dlg.tiles) == 2 and dlg.recent_row.isVisible(), "两格 + 最近用过")
    dlg.search.setText("xjs")
    c.ok(dlg._shown == ["newone"], f"拼音首字母 xjs：{dlg._shown}")
    dlg.search.setText("渡尘")
    c.ok(dlg._shown == ["fuhua"], f"名字一部分：{dlg._shown}")
    dlg.search.setText("zzz")
    c.ok(dlg._shown == [] and "没找到" in dlg.count.text(), f"搜不到：{dlg._shown} {dlg.count.text()}")
    dlg.search.setText("")
    c.ok(dlg._shown == ["fuhua", "newone"], f"清空后全部：{dlg._shown}")
    dlg.search.setText("FH")
    dlg._pick_first()
    c.ok(dlg.chosen == "fuhua" and dlg.result() == 1, f"回车选第一个：{dlg.chosen}")
    dlg2 = RolePanel(win, "假乐土", ROLES["items"], "fuhua", [])
    dlg2.show()
    env.pump(50)
    c.ok(not dlg2.recent_row.isVisible(), "没有最近用过时这一行隐藏")
    dlg2.tiles[1].click()
    c.ok(dlg2.chosen == "newone", f"点格子：{dlg2.chosen}")
    dlg.grab().save("/tmp/phantasia_roles_panel.png")

    # ---------------------------------------------------------------- R5
    c.start("R5 普通歌不受影响")
    ptid = ts.plain_tool(tmp)["id"]
    rec = env.play_and_wait(ptid, timeout=30)
    if c.ok(rec is not None, "普通歌没播完"):
        c.ok(env.calls[-1]["cmd"] == env.registry.resolve_command(s.tool(ptid)) and "--role" not in env.calls[-1]["cmd"],
             f"普通歌命令：{env.calls[-1]['cmd']}")
        c.ok("role" not in rec and "role_label" not in rec, f"普通歌记录多了字段：{list(rec)}")

    # ---------------------------------------------------------------- R6
    c.start("R6 顺序播放第一首启动不了不弹框，单首点播照旧弹")
    import core.env as cenv
    real = cenv.device_online
    cenv.device_online = lambda *a, **k: False
    try:
        w0, n0 = len(ts.WARNS), len(ts.NOTIFIES)
        if "needs_dev" not in s.checked:
            s.toggle_check("needs_dev")
        s.checked = ["needs_dev"]
        s.play_checked()
        env.wait(lambda: not s.busy and s.pending is None, 10)
        env.pump(200)
        c.ok(len(ts.WARNS) == w0, f"顺序播放不该弹模态框：{ts.WARNS[w0:]}")
        c.ok(any("没能启动" in str(a[0]) for a in ts.NOTIFIES[n0:]), f"应发通知：{ts.NOTIFIES[n0:]}")
        s.play("needs_dev")
        env.wait(lambda: not s.busy and s.pending is None, 10)
        env.pump(200)
        c.ok(len(ts.WARNS) == w0 + 1, f"单首点播启动不了应弹框：{ts.WARNS[w0:]}")
    finally:
        cenv.device_online = real

    # ---------------------------------------------------------------- R8
    r8(env, c)

    win.grab().save("/tmp/phantasia_roles_window.png")
    print()
    bad = 0
    for k, v in c.res.items():
        print(f"{k}：{'通过' if not v else '没通过'}")
        for x in v:
            print("   -", x)
        bad += bool(v)
    print("截图：/tmp/phantasia_roles_window.png  /tmp/phantasia_roles_panel.png")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"临时目录已删除：{not tmp.exists()}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
