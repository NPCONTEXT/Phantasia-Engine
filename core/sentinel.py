"""播放哨兵 · Python 版（11-23）：做的事和 core/runner.py 里 bash 版（SENTINEL_TMPL）一样。
Windows 上用它；Mac 上默认还用 bash 版，环境变量 PE_SENTINEL=py 时也用它（测试用）。只用标准库，Python 3.9 起都能跑。

用法：python sentinel.py 引擎PID 分辨率 adb路径 先关的包名 -- 工具命令 参数…
1. 分辨率：native 还原；WxH（如 2400x1504）切过去；none 或空不动。
2. 先关的包名不空（顺序播放）：日志里说一声，am force-stop，等 2 秒，让脚本从冷启动开始。
3. 起工具，输出直接接到引擎的日志管道（继承本进程的 stdout / stderr）。
4. 看门狗：每 3 秒看一眼引擎还在不在；不在了就收掉工具、还原分辨率，退出码 1。
5. 工具结束：工具自己起的、还没退出的子进程也收掉（不留孤儿占着日志管道），改过分辨率就还原，用工具的退出码退出。
收到停止（Mac / Linux：SIGTERM、SIGINT；Windows：CTRL_BREAK）照第 4 步收尾。

收进程树：Mac / Linux 靠进程组（引擎用 start_new_session 起本进程，本进程就是组长，工具和它的子进程都在组里）；
Windows 把工具放进一个「作业对象」（关掉就连带结束里面所有进程），建不成就退回 taskkill /T。
"""
import os
import signal
import subprocess
import sys
import time

IS_WIN = os.name == "nt"
STILL_ACTIVE = 259


def _k32():
    """kernel32，几个用到的函数声明好参数和返回值（11-24：不声明时返回值按 32 位 int，64 位上的 HANDLE 会被截断）。"""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    H, D, B = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    for name, res, args in (("OpenProcess", H, (D, B, D)), ("GetExitCodeProcess", B, (H, ctypes.POINTER(D))),
                            ("CloseHandle", B, (H,)), ("CreateJobObjectW", H, (ctypes.c_void_p, wintypes.LPCWSTR)),
                            ("SetInformationJobObject", B, (H, ctypes.c_int, ctypes.c_void_p, D)),
                            ("AssignProcessToJobObject", B, (H, H)), ("TerminateJobObject", B, (H, ctypes.c_uint))):
        f = getattr(k, name)
        f.restype, f.argtypes = res, list(args)
    return k


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] [引擎] ") + msg, flush=True)


def alive(pid: int) -> bool:
    """进程还在不在。Windows 上不能用 os.kill(pid, 0)：那会直接把它结束掉。"""
    if pid <= 0:
        return False
    if IS_WIN:
        import ctypes
        from ctypes import wintypes
        kernel32 = _k32()
        h = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WinJob:
    """Windows 作业对象：工具和它起的子进程都在里面，terminate() 一次全结束；本进程退出时句柄关掉也会全结束。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        kernel32 = _k32()
        self.kernel32 = kernel32
        self.h = kernel32.CreateJobObjectW(None, None)
        if not self.h:
            raise OSError("CreateJobObjectW 失败")

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]      # DWORD 一律写 c_uint32：Windows 上 DWORD 就是 4 字节，这样在 Mac 上也能核对布局

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(self.h, 9, ctypes.addressof(info), ctypes.sizeof(info)):   # 9 = ExtendedLimitInformation
            raise OSError("SetInformationJobObject 失败")

    def add(self, pid: int) -> bool:
        hp = self.kernel32.OpenProcess(0x0101, False, pid)          # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if not hp:
            return False
        ok = bool(self.kernel32.AssignProcessToJobObject(self.h, hp))
        self.kernel32.CloseHandle(hp)
        return ok

    def terminate(self) -> None:
        self.kernel32.TerminateJobObject(self.h, 1)


def wm(adb: str, *args: str) -> None:
    try:
        subprocess.run([adb, "shell", "wm", "size", *args], capture_output=True, timeout=15)
    except Exception:
        pass


def main(argv) -> int:
    if "--" not in argv or argv.index("--") < 4:
        print("用法：sentinel.py 引擎PID 分辨率 adb 先关的包名 -- 命令…", file=sys.stderr)
        return 2
    cut = argv.index("--")
    agg_pid, res, adb, pre_kill = int(argv[0] or 0), argv[1], argv[2] or "adb", argv[3]
    cmd = argv[cut + 1:]
    if not cmd:
        return 2
    changed_res = res not in ("none", "")
    if res == "native":
        wm(adb, "reset")
    elif changed_res and "x" in res:
        wm(adb, res)
    if pre_kill:
        log("顺序播放：先关掉游戏 " + pre_kill)
        try:
            subprocess.run([adb, "shell", "am", "force-stop", pre_kill], capture_output=True, timeout=20)
        except Exception:
            pass
        time.sleep(2)

    job = None
    if IS_WIN:
        try:
            job = WinJob()
        except Exception as e:
            log("建不了作业对象（%s），停止时改用 taskkill 收进程树" % e)
    proc = subprocess.Popen(cmd)
    if job and not job.add(proc.pid):
        log("工具没放进作业对象，停止时改用 taskkill 收进程树")
        job = None
    state = {"done": False}

    def cleanup(code: int) -> int:
        if state["done"]:
            return code
        state["done"] = True
        if proc.poll() is None:
            if IS_WIN:
                if job:
                    job.terminate()
                else:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    proc.terminate()
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                except Exception:
                    pass
        if IS_WIN:
            if job:
                job.terminate()                               # 工具已经退出、它起的子进程还在的，一起收掉
        else:
            kill_group_rest()
        if changed_res:
            wm(adb, "reset")
        return code

    def on_stop(signum, frame):
        sys.exit(cleanup(1))

    if IS_WIN:
        signal.signal(signal.SIGBREAK, on_stop)
    else:
        signal.signal(signal.SIGTERM, on_stop)
        signal.signal(signal.SIGINT, on_stop)
    try:
        while proc.poll() is None:
            if agg_pid and not alive(agg_pid):
                return cleanup(1)
            for _ in range(30):                               # 3 秒，分成小段：工具一结束就不用干等
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
        return cleanup(proc.returncode)
    except SystemExit:
        raise
    except BaseException:
        return cleanup(1)


def kill_group_rest() -> None:
    """Mac / Linux：同一进程组里除了本进程以外的都结束掉（bash 版用 /usr/bin/pgrep -g 做的那一步）。"""
    try:
        pgid = os.getpgid(0)
        if pgid != os.getpid():                               # 不是组长（没经引擎起）：不乱杀别人的组
            return
        out = subprocess.run(["/usr/bin/pgrep", "-g", str(pgid)], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        log("找不到 /usr/bin/pgrep，工具留下的子进程没法收掉")
        return
    for tok in out.split():
        try:
            p = int(tok)
            if p != os.getpid():
                os.kill(p, signal.SIGKILL)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
