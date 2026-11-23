"""环境管家: adb 设备检测 / 分辨率切换 / 自检"""
import os
import re
import shutil
import subprocess
from pathlib import Path

RES_BASE = "2400x1504"    # 本机基准分辨率（设备 2400x1504）
RES_NATIVE = "native"
RES_NONE = "none"  # 不切换分辨率(用于无需手机的测试工具)

# 从 .app 启动时 PATH 里没有 homebrew，这里把常见位置都找一遍
_ADB_CANDIDATES = (
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
    str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
)
EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")


def adb_bin() -> str:
    found = shutil.which("adb")
    if found:
        return found
    for p in _ADB_CANDIDATES:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return _ADB_CANDIDATES[0]


def adb_found() -> bool:
    p = adb_bin()
    return os.path.isfile(p) and os.access(p, os.X_OK)


def child_env() -> dict:
    """给工具子进程用的环境变量: PATH 补上 adb 所在目录和 homebrew，Python 输出不缓冲。"""
    e = dict(os.environ)
    parts = [str(Path(adb_bin()).parent), *EXTRA_PATH]
    cur = e.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    have = cur.split(os.pathsep)
    e["PATH"] = os.pathsep.join([p for p in dict.fromkeys(parts) if p not in have] + [cur])
    e.setdefault("PYTHONUNBUFFERED", "1")
    return e


def adb(*args, timeout: int = 15) -> subprocess.CompletedProcess:
    """调用 adb。找不到 adb / 超时都不抛异常，返回 returncode 非 0 的结果。"""
    cmd = [adb_bin(), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "adb 超时")
    except Exception as e:  # FileNotFoundError 等
        return subprocess.CompletedProcess(cmd, 127, "", f"{type(e).__name__}: {e}")


def devices() -> list[dict]:
    """返回 [{serial, status}]"""
    r = adb("devices")
    out = []
    for line in (r.stdout or "").splitlines():
        if line.startswith(("List of", "*")):  # 表头 / 「daemon not running」之类的提示
            continue
        parts = line.split()
        if parts:
            out.append({"serial": parts[0], "status": parts[1] if len(parts) > 1 else "?"})
    return out


# ---- 11-17 无线调试：挑设备 ----
# 同一台设备可能同时有几条连接：插线的（序列号）、无线自动连上的（adb-<序列号>-xxxx._adb-tls-connect._tcp）、
# 手动连的（ip:端口）；应用宝之类的模拟器也会出现在列表里（emulator-5554、127.0.0.1:端口）。
# 挑法：用户自己设了 ANDROID_SERIAL 就只认它；否则按设备归并，上次用的那台优先、真机优先于模拟器、
# 其余按 adb 列出来的顺序；同一台里 插线 > 无线（自动连）> 无线（ip:端口）。
# 挑中的连接名写进 os.environ["ANDROID_SERIAL"]：引擎自己不带 -s 的 adb 命令、每首歌的子进程（child_env 复制环境）都跟着它走。
_USER_SERIAL = os.environ.get("ANDROID_SERIAL") or None
_MDNS = re.compile(r"^adb-(.+)-[^-]+\._adb-tls-connect\._tcp\.?$")
_last_sn: str | None = None
_sn_cache: dict[str, str] = {}


def conn_kind(serial: str) -> str:
    """连接种类：有线 / 无线 / 模拟器。"""
    if serial.startswith(("emulator-", "127.0.0.1:", "localhost:")):
        return "模拟器"
    if _MDNS.match(serial) or re.fullmatch(r"[\w.\-]+:\d+", serial):
        return "无线"
    return "有线"


def _rank(serial: str) -> int:
    """同一台设备里的先后：插线 0、无线自动连 1、无线 ip:端口 2、模拟器 3。"""
    k = conn_kind(serial)
    if k == "有线":
        return 0
    if k == "无线":
        return 1 if _MDNS.match(serial) else 2
    return 3


def _sn_of(serial: str) -> str:
    """这条连接属于哪台设备（序列号）：自动连的名字里带着；ip:端口 问一次设备并记下（问不到就先用连接名）。"""
    m = _MDNS.match(serial)
    if m:
        return m.group(1)
    if conn_kind(serial) != "无线":
        return serial
    if serial not in _sn_cache:
        r = adb("-s", serial, "shell", "getprop", "ro.serialno", timeout=5)
        sn = (r.stdout or "").strip()
        if r.returncode != 0 or not sn:
            return serial
        _sn_cache[serial] = sn
    return _sn_cache[serial]


def pick_device(devs: list[dict], prev_sn: str | None = None, user_serial: str | None = None,
                sn_of=None) -> dict | None:
    """从 devices() 的结果里挑一条连接（不改任何状态，测试直接调）。返回 {serial, status, sn, kind, label} 或 None。"""
    sn_of = sn_of or _sn_of
    online = [d["serial"] for d in devs if d.get("status") == "device"]
    if user_serial:
        chosen = user_serial if user_serial in online else None
        sn = sn_of(chosen) if chosen else None
    else:
        groups: dict[str, list[str]] = {}
        for s in online:
            groups.setdefault(sn_of(s), []).append(s)
        if not groups:
            return None
        best = {g: min(conns, key=_rank) for g, conns in groups.items()}
        real = [g for g in groups if conn_kind(best[g]) != "模拟器"]
        cands = real or list(groups)            # 有真机就只在真机里挑：模拟器先出现也不会粘住
        sn = prev_sn if prev_sn in cands else cands[0]
        chosen = best[sn]
    if not chosen:
        return None
    kind = conn_kind(chosen)
    return {"serial": chosen, "status": "device", "sn": sn, "kind": kind, "label": f"{sn} · {kind}"}


def device_online() -> dict | None:
    """挑一台在线设备（规则见上面的说明），并写进 ANDROID_SERIAL；没有就返回 None。"""
    global _last_sn
    dev = pick_device(devices(), _last_sn, _USER_SERIAL)
    if not _USER_SERIAL:
        if dev:
            os.environ["ANDROID_SERIAL"] = dev["serial"]
        else:
            os.environ.pop("ANDROID_SERIAL", None)
    if dev:
        _last_sn = dev["sn"]
    return dev


def wm_size() -> str:
    r = adb("shell", "wm", "size")
    m = re.findall(r"(\d+)x(\d+)", r.stdout or "")
    if len(m) >= 2:
        return f"{m[1][0]}x{m[1][1]} (Override)" if m[1] != m[0] else f"{m[0][0]}x{m[0][1]}"
    if m:
        return f"{m[0][0]}x{m[0][1]}"
    return "未知"


def set_resolution(mode: str) -> bool:
    """mode: 2400x1504(强制基准) | native(设备当前) | none(不动)
    任意 WxH 字符串亦可（如 1920x1080）。"""
    if mode == RES_NONE:
        return True
    if mode == RES_NATIVE:
        return adb("shell", "wm", "size", "reset").returncode == 0
    if re.fullmatch(r"\d+x\d+", mode or ""):
        return adb("shell", "wm", "size", mode).returncode == 0
    return False


def ratio_ok(size: str, base: str = RES_BASE, tol: float = 0.02) -> bool:
    """11-01 设计三：设备现在的显示比例和脚本基准一样吗（只看长边 / 短边，横竖都算）。
    size 是 wm_size() 的结果，「1504x2400」「2400x1504 (Override)」都行；读不出来的当作没问题（不弹窗）。"""
    m = re.search(r"(\d+)x(\d+)", size or "")
    b = re.fullmatch(r"(\d+)x(\d+)", base)
    if not m or not b:
        return True
    w, h = int(m.group(1)), int(m.group(2))
    bw, bh = int(b.group(1)), int(b.group(2))
    return abs(max(w, h) / min(w, h) - max(bw, bh) / min(bw, bh)) < tol


def is_16_9() -> bool:
    r = adb("shell", "wm", "size")
    m = re.findall(r"(\d+)x(\d+)", r.stdout or "")
    if len(m) < 2:
        return False
    w, h = int(m[1][0]), int(m[1][1])
    return abs(w / h - 16 / 9) < 0.02


def check_environment(tools: list[dict]) -> list[tuple[bool, str]]:
    """环境自检, 返回 [(ok: bool, text)]；以两个空格开头的行是上一行的附属项。"""
    results: list[tuple[bool, str]] = []
    if adb_found():
        results.append((True, f"adb: {adb_bin()}"))
    else:
        results.append((False, "adb 未找到(需安装 platform-tools)"))
    dev = device_online()
    if dev:
        results.append((True, f"设备在线: {dev.get('label') or dev['serial']}"))
        results.append((True, f"当前分辨率: {wm_size()}"))
    else:
        results.append((False, "没有在线设备(插线/开无线调试)"))
    from core import registry

    for t in tools:
        files = registry.precheck_files(t)
        missing = [p for p in files if not p.exists()]
        ok = not missing
        extra = f" 缺: {', '.join(str(p.name) for p in missing[:3])}" if missing else ""
        results.append((ok, f"工具[{t.get('name', '?')}] 文件自检{extra}"))
        py = registry.tool_python(t, registry.tool_path(t.get("workdir")))
        if py:
            okpy = Path(py).exists()
            results.append((okpy, f"  venv 解释器: {t.get('python')}{'' if okpy else ' (不存在)'}"))
    return results
