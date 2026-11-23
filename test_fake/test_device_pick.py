"""离线测试（11-17 无线调试）：引擎怎么挑设备、挑中后写进 ANDROID_SERIAL。
不碰设备：core.env.adb / devices 全换成假的；跑的时候外面可以再套一层禁止启动 adb 的沙箱（sandbox-exec）。

运行（在副本根目录）：
  venv-qt.nosync/bin/python test_fake/test_device_pick.py
拿别的代码跑（例如改前的 core/）：环境变量 TEST_ROOT=那个目录
"""
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("TEST_ROOT") or HERE.parent)
os.environ.pop("ANDROID_SERIAL", None)          # 别让外面的环境变量影响「用户自己设了没有」
sys.path.insert(0, str(ROOT))
from core import env  # noqa: E402

SN = "TESTSN0001"
USB = SN
MDNS = f"adb-{SN}-Xy12Zw._adb-tls-connect._tcp"
IPP = "192.168.1.23:37000"
EMU = "emulator-5554"
MUMU = "127.0.0.1:16384"
OTHER_USB = "R58M12345XY"
OTHER_MDNS = "adb-R58M12345XY-Ab12Cd._adb-tls-connect._tcp"

fails, total = [], 0


def check(name, fn, want):
    global total
    total += 1
    try:
        got = fn()
    except Exception as e:      # 改前的代码没有这些函数：算失败，不中断
        got = f"出错 {type(e).__name__}: {e}"
    ok = got == want
    print(f"{name}：{'通过' if ok else '失败'}")          # 跟其他界面测试一样：一项一行，结尾是「：通过 / ：失败」
    if not ok:
        print(f"  - 得到 {got!r}，应为 {want!r}")
        fails.append(name)


def D(*pairs):
    """D((连接名, 状态), …) → devices() 的格式"""
    return [{"serial": s, "status": st} for s, st in pairs]


def fake_sn(s):
    """假的「这条连接是哪台设备」：ip:端口 查表；自动连的名字按 adb-<序列号>-<随机>._adb-tls-connect._tcp 取（独立写一遍，不借被测代码）。"""
    m = re.match(r"^adb-(.+)-[^-]+\._adb-tls-connect\._tcp$", s)
    return {IPP: SN}.get(s) or (m.group(1) if m else s)


def pick(devs, prev=None, user=None):
    d = env.pick_device(devs, prev, user, sn_of=fake_sn)
    return d and d["serial"]


# 真的 adb 一律不许叫（下面需要时再换成记录调用的假函数）
def no_adb(*a, **k):
    raise AssertionError(f"测试里不该真调 adb：{a}")


env.adb = no_adb

# ---- 连接种类 ----
for s, want in ((USB, "有线"), (MDNS, "无线"), (IPP, "无线"), (EMU, "模拟器"), (MUMU, "模拟器"), ("Android.local:5555", "无线")):
    check(f"连接种类 {s}", lambda s=s: env.conn_kind(s), want)

# ---- 挑哪条（纯函数）----
check("P1 只插线 → 插线", lambda: pick(D((USB, "device"))), USB)
check("P2 只有无线自动连 → 无线", lambda: pick(D((MDNS, "device"))), MDNS)
check("P3 插线 + 无线（同一台，无线排前面）→ 插线", lambda: pick(D((MDNS, "device"), (USB, "device"))), USB)
check("P4 无线自动连 + 手动 ip:端口（同一台）→ 自动连那条", lambda: pick(D((IPP, "device"), (MDNS, "device"))), MDNS)
check("P5 模拟器排前面 + 真机无线 → 真机", lambda: pick(D((EMU, "device"), (MDNS, "device"))), MDNS)
check("P6 只有模拟器 → 模拟器", lambda: pick(D((EMU, "device"))), EMU)
check("P7 没授权 / 离线的不算", lambda: pick(D((USB, "unauthorized"), (EMU, "offline"), (MDNS, "device"))), MDNS)
check("P8 用户自己设了 ANDROID_SERIAL → 只认它", lambda: pick(D((USB, "device"), (MDNS, "device")), user=MDNS), MDNS)
check("P9 用户设的那条不在线 → 没有设备", lambda: pick(D((USB, "device")), user=IPP), None)
check("P10 一台都没有 → None", lambda: pick(D()), None)
check("P11 两台真机，上次用的是第二台 → 还用它", lambda: pick(D((OTHER_USB, "device"), (MDNS, "device")), prev=SN), MDNS)
check("P12 两台真机，没有上次 → 按列表第一台", lambda: pick(D((OTHER_USB, "device"), (MDNS, "device"))), OTHER_USB)
check("P13 MuMu 那种 127.0.0.1 模拟器 + 真机插线 → 真机", lambda: pick(D((MUMU, "device"), (USB, "device"))), USB)
check("P14 显示名带序列号和连接种类",
      lambda: env.pick_device(D((MDNS, "device")), sn_of=fake_sn)["label"], f"{SN} · 无线")
check("P15 另一台的无线不会跟这台合并",
      lambda: sorted(env.pick_device(D((x, "device")), sn_of=fake_sn)["sn"] for x in (OTHER_MDNS, MDNS)), sorted([SN, "R58M12345XY"]))

# ---- 认是哪台设备（_sn_of），用假的 adb 记调用 ----
calls = []


def fake_adb_getprop(*a, **k):
    calls.append(a)
    if a[:2] == ("-s", IPP):
        return subprocess.CompletedProcess(a, 0, SN + "\n", "")
    return subprocess.CompletedProcess(a, 1, "", "device not found")


env.adb = fake_adb_getprop
check("S1 自动连的名字里直接取序列号、不问设备", lambda: (env._sn_of(MDNS), len(calls)), (SN, 0))
check("S2 ip:端口 问一次设备", lambda: (env._sn_of(IPP), len(calls)), (SN, 1))
check("S3 第二次用记下的，不再问", lambda: (env._sn_of(IPP), len(calls)), (SN, 1))
check("S4 问不到就先用连接名、下次再问", lambda: (env._sn_of("10.0.0.9:5555"), env._sn_of("10.0.0.9:5555"), len(calls)),
      ("10.0.0.9:5555", "10.0.0.9:5555", 3))
check("S5 插线、模拟器不问设备", lambda: (env._sn_of(USB), env._sn_of(EMU), len(calls)), (USB, EMU, 3))
env.adb = no_adb

# ---- device_online：挑中后写进 ANDROID_SERIAL，子进程环境跟着变 ----
state = {"devs": []}
env.devices = lambda: state["devs"]
env._sn_of = fake_sn
env._last_sn = None


def online(devs):
    state["devs"] = devs
    d = env.device_online()
    return d and d["serial"], os.environ.get("ANDROID_SERIAL"), env.child_env().get("ANDROID_SERIAL")


check("D1 只有无线 → 用无线，子进程也拿到", lambda: online(D((MDNS, "device"))), (MDNS, MDNS, MDNS))
check("D2 插上线 → 换成插线", lambda: online(D((MDNS, "device"), (USB, "device"))), (USB, USB, USB))
check("D3 拔掉线 → 回到无线", lambda: online(D((MDNS, "device"))), (MDNS, MDNS, MDNS))
check("D4 都断了 → None，环境变量清掉", lambda: online(D()), (None, None, None))
check("D5 另一台真机先连上在用，这台后连上 → 继续用在用的那台（不来回换）",
      lambda: (online(D((OTHER_USB, "device")))[0], online(D((OTHER_USB, "device"), (MDNS, "device")))[0]),
      (OTHER_USB, OTHER_USB))
env._last_sn = SN
check("D6 上次用的是这台 → 两台都在时还用它", lambda: online(D((OTHER_USB, "device"), (MDNS, "device")))[0], MDNS)
env._last_sn = None
check("D8 模拟器先在（被挑中），真机晚连上 → 换成真机，模拟器不粘",
      lambda: (online(D((EMU, "device")))[0], online(D((EMU, "device"), (MDNS, "device")))[0]), (EMU, MDNS))


def user_set():
    os.environ["ANDROID_SERIAL"] = IPP
    env._USER_SERIAL = IPP
    try:
        r = online(D((USB, "device"), (IPP, "device")))
        r2 = online(D((USB, "device")))
        return r, r2
    finally:
        env._USER_SERIAL = None
        os.environ.pop("ANDROID_SERIAL", None)


check("D7 用户自己设了：只认它，不改他的环境变量；它不在线就是没设备",
      user_set, ((IPP, IPP, IPP), (None, IPP, IPP)))

# ---- 环境自检显示连接种类 ----
env._last_sn = None
state["devs"] = D((MDNS, "device"))
env.adb = lambda *a, **k: subprocess.CompletedProcess(a, 0, "Physical size: 2400x1504\n", "")
check("C1 环境自检写「设备在线: 序列号 · 无线」",
      lambda: [t for ok, t in env.check_environment([]) if t.startswith("设备在线")], [f"设备在线: {SN} · 无线"])

print(f"共 {total} 项，失败 {len(fails)} 项")
sys.exit(1 if fails else 0)
