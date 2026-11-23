"""进程管家: 哨兵脚本 + 进程组管理 + 日志捕获, 关闭/崩溃都不留孤儿"""
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from core import registry

AGG_DIR = registry.AGG_DIR
RUNS_DIR = AGG_DIR / "runs"
LOGS_DIR = AGG_DIR / "logs"
IS_WIN = os.name == "nt"
SENTINEL_PY = Path(__file__).resolve().parent / "sentinel.py"


def _has_console() -> bool:
    """Windows：本进程有没有控制台（用 pythonw 起就没有，CTRL_BREAK 发不出去）。"""
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def py_sentinel() -> bool:
    """11-23：Windows 没有 bash，用 Python 版哨兵（core/sentinel.py）；Mac 上默认还用下面的 bash 版，PE_SENTINEL=py 时也用 Python 版。"""
    return IS_WIN or os.environ.get("PE_SENTINEL") == "py"

SENTINEL_TMPL = """#!/bin/bash
# 哨兵: 进程组内管理工具, 聚合前台死亡时自动清理并还原分辨率
AGG_PID="$1"
RES="$2"
ADB="${3:-adb}"
PRE_KILL="${4:-}"
save_pid=""
cleanup() {
    if [ -n "$save_pid" ] && kill -0 "$save_pid" 2>/dev/null; then
        kill -TERM "$save_pid" 2>/dev/null; sleep 2
        kill -0 "$save_pid" 2>/dev/null && kill -KILL "$save_pid" 2>/dev/null
    fi
    # 工具自己起的子进程（同一进程组）也收掉，哨兵自己除外：界面被强退时不留孤儿，
    # 正常结束时也不让留下的子进程一直占着日志管道、界面停在「正在播放」（10-20 审查 H1、H2）
    # 写死系统自带的 /usr/bin/pgrep，不靠 PATH；万一没有就在日志里说一声，别悄悄失效（10-20 复查）
    if [ -x /usr/bin/pgrep ]; then
        for p in $(/usr/bin/pgrep -g $$ 2>/dev/null); do [ "$p" != "$$" ] && kill -KILL "$p" 2>/dev/null; done
    else
        echo "[$(date +%H:%M:%S)] [引擎] 找不到 /usr/bin/pgrep，工具留下的子进程没法收掉"
    fi
    if [ "$RES" != "none" ]; then "$ADB" shell wm size reset >/dev/null 2>&1; fi
}
trap cleanup TERM INT EXIT
# 环境准备: 切换分辨率（通用 WxH，如 2400x1504 本机基准）
case "$RES" in
  native) "$ADB" shell wm size reset >/dev/null 2>&1 ;;
  none|"") ;;
  [0-9]*x[0-9]*) "$ADB" shell wm size "$RES" >/dev/null 2>&1 ;;
esac
# 顺序播放：先关掉这个工具的游戏，让脚本从冷启动开始
if [ -n "$PRE_KILL" ]; then
    echo "[$(date +%H:%M:%S)] [引擎] 顺序播放：先关掉游戏 $PRE_KILL"
    "$ADB" shell am force-stop "$PRE_KILL" >/dev/null 2>&1
    sleep 2
fi
# 启动工具(同进程组)
{CMD} &
save_pid=$!
# 看门狗: 聚合前台被强杀时立即清理
while kill -0 "$save_pid" 2>/dev/null; do
    kill -0 "$AGG_PID" 2>/dev/null || { exit 1; }
    sleep 3
done
wait "$save_pid"
RC=$?
if [ "$RES" != "none" ]; then "$ADB" shell wm size reset >/dev/null 2>&1; fi
exit $RC
"""


class RunningTool:
    """一个正在运行的工具实例"""

    def __init__(self, tool_id: str, name: str, proc: subprocess.Popen,
                 log_path: Path, started: float, resolution: str,
                 sentinel: Path | None = None):
        self.id = tool_id
        self.name = name
        self.proc = proc
        self.log_path = log_path
        self.started = started
        self.ended: float | None = None
        self.resolution = resolution
        self.sentinel = sentinel
        self.done = False
        self.exit_code: int | None = None
        self.stopped_by_user = False
        self.seq = False                 # 顺序播放里的一首：开跑前、结束后都关游戏
        self.package = ""                # 这个工具对应的游戏包名（tools.json 的 package）
        self.need_device = True
        self.shot: str | None = None     # 结束截图文件名（runs/shots/ 下）
        self.note = ""                   # 开头先写进日志的一行说明（例如这次勾了哪几个子项）
        self.ctl: Path | None = None     # 11-02 暂停协议的控制目录（runs/ctl/…；脚本那边 lib/play_ctl.py）

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def alive(self) -> bool:
        return not self.done and self.proc.poll() is None

    @property
    def uptime(self) -> str:
        s = int(time.time() - self.started)
        return f"{s // 3600}h{(s % 3600) // 60}m{s % 60}s"


class ToolRunner:
    def __init__(self, on_log=None, on_state=None, on_exit=None, on_line=None):
        # on_log(line: str), on_state(running: RunningTool | None), on_exit(rt: RunningTool),
        # on_line(rt, line: str)；这些回调都可能在后台线程里被调用
        self.on_log = on_log or (lambda line: None)
        self.on_line = on_line or (lambda rt, line: None)
        self.on_state = on_state or (lambda rt: None)
        self.on_exit = on_exit or (lambda rt: None)
        self.current: RunningTool | None = None
        self.last: RunningTool | None = None   # 最近一次成功启动的实例（current 可能已被清空）
        self._lock = threading.Lock()
        self._buf: deque[str] = deque(maxlen=4000)

    # ---------- 启动 ----------
    def start(self, tool: dict, device_checked: bool = False, seq: bool = False,
              note: str = "") -> tuple[bool, str]:
        """device_checked=True 表示调用方已经确认过设备在线，这里不再同步调 adb。
        seq=True 表示顺序播放：有 package 的工具开跑前关一次游戏，结束截图后再关掉（被用户停止的不关）。
        note 非空时，作为这次日志的第一行（带「[引擎]」）写进去。"""
        from core import env
        with self._lock:
            if self.current and self.current.alive:
                return False, f"已有工具在运行: {self.current.name}"
            if tool.get("need_device", True) and not device_checked:
                if not env.device_online():
                    return False, "没有在线设备"
            cmd = registry.resolve_command(tool)
            if not cmd:
                return False, "命令为空, 检查 tools.json"
            workdir = registry.tool_path(tool.get("workdir"))
            if not workdir.is_dir():
                return False, f"工作目录不存在: {tool.get('workdir')}"

            # restart_first: 先杀掉可能残留的同类型旧进程(独立于本聚合器)
            for pat in tool.get("kill_patterns", []) or []:
                _pkill(pat)

            res = tool.get("env", "none")
            log_path = LOGS_DIR / f"{tool.get('id', 'tool')}_{time.strftime('%Y%m%d-%H%M%S')}.log"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            RUNS_DIR.mkdir(parents=True, exist_ok=True)

            sentinel = None
            if not py_sentinel():
                sentinel = RUNS_DIR / f"sentinel_{uuid.uuid4().hex[:8]}.sh"
                script = SENTINEL_TMPL.replace("{CMD}", " ".join(shlex.quote(c) for c in cmd))
                sentinel.write_text(script, encoding="utf-8")
                os.chmod(sentinel, 0o755)
            # 11-02 暂停协议：每次播放一个控制目录。界面放 pause 文件 = 请暂停；脚本写 state.json 报告（有它 = 接上了暂停）
            ctl = RUNS_DIR / "ctl" / f"{tool.get('id', 'tool')}_{uuid.uuid4().hex[:8]}"
            ctl.mkdir(parents=True, exist_ok=True)
            cenv = env.child_env()
            cenv["PLAY_CTL"] = str(ctl)
            lib = str(registry.AGG_DIR / "lib")
            cenv["PYTHONPATH"] = lib + (os.pathsep + cenv["PYTHONPATH"] if cenv.get("PYTHONPATH") else "")

            try:
                pkg = str(tool.get("package") or "") if seq else ""
                if sentinel is None:     # Python 版哨兵：命令跟在 -- 后面
                    argv = [sys.executable, str(SENTINEL_PY), str(os.getpid()), res, env.adb_bin(), pkg, "--", *cmd]
                else:
                    argv = ["/bin/bash", str(sentinel), str(os.getpid()), res, env.adb_bin(), pkg]
                group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WIN
                         else {"start_new_session": True})      # 独立进程组（停止时整组收）
                proc = subprocess.Popen(
                    argv,
                    cwd=str(workdir),
                    env=cenv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    **group,
                )
            except Exception as e:
                if sentinel is not None:
                    _unlink(sentinel)
                shutil.rmtree(ctl, ignore_errors=True)
                return False, f"启动失败: {e}"

            rt = RunningTool(tool.get("id", "?"), tool.get("name", "?"), proc,
                             log_path, time.time(), res, sentinel)
            rt.seq = seq
            rt.package = str(tool.get("package") or "")
            rt.need_device = bool(tool.get("need_device", True))
            rt.note = note or ""
            rt.ctl = ctl

            # hide_window: 后台调用时尽量让 GUI 不打扰(最小化兜底)
            if tool.get("hide_window"):
                threading.Thread(target=self._hide_gui_window, args=(proc.pid,), daemon=True).start()
            self.current = rt
            self.last = rt
            threading.Thread(target=self._pump, args=(rt,), daemon=True).start()
            self.on_state(rt)
            return True, f"已启动 PID={proc.pid} (进程组 {proc.pid})"

    # ---------- 日志管道 ----------
    def _pump(self, rt: RunningTool):
        try:
            with rt.log_path.open("w", encoding="utf-8") as f:
                if rt.note:      # 日志文件由这里独占写，开头的说明也只能在这里写，免得被覆盖
                    line = f"[{time.strftime('%H:%M:%S')}] [引擎] {rt.note}"
                    f.write(line + "\n")
                    f.flush()
                    self._buf.append(line)
                    self.on_log(line)
                    self.on_line(rt, line)
                for raw in rt.proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if not line:
                        continue
                    f.write(line + "\n")
                    f.flush()
                    self._buf.append(line)
                    self.on_log(line)
                    self.on_line(rt, line)
        except Exception:
            pass
        rt.exit_code = rt.proc.wait()
        self._finish_device(rt)
        rt.ended = time.time()
        rt.done = True
        if self.current is rt:
            self.current = None
        if rt.sentinel:
            _unlink(rt.sentinel)
        if rt.ctl:
            shutil.rmtree(rt.ctl, ignore_errors=True)
        self.on_state(None)
        self.on_exit(rt)

    # ---------- 结束收尾：截图 / 顺序播放关游戏（后台线程里跑） ----------
    def _note(self, rt: RunningTool, text: str):
        line = f"[{time.strftime('%H:%M:%S')}] [引擎] {text}"
        try:
            with rt.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self._buf.append(line)
        self.on_log(line)
        self.on_line(rt, line)

    def _finish_device(self, rt: RunningTool):
        if not rt.need_device:
            return
        from core import env
        rt.shot = take_shot(rt.id)
        self._note(rt, f"结束截图已保存：{rt.shot}" if rt.shot else "结束截图没截成（设备没响应？）")
        if rt.seq and rt.package and not rt.stopped_by_user:
            r = env.adb("shell", "am", "force-stop", rt.package)
            self._note(rt, f"顺序播放：已关掉游戏 {rt.package}" if r.returncode == 0 else f"顺序播放：关游戏失败（{r.stderr.strip()}）")

    # ---------- 暂停（11-02；脚本那边配合：lib/play_ctl.py） ----------
    def ctl_state(self) -> dict | None:
        """正在播的这首自己报告的状态 {"state": "run" | "paused" | "battle", "why": …}；None = 这首没接上暂停。"""
        rt = self.current
        if not (rt and rt.ctl):
            return None
        try:
            return json.loads((rt.ctl / "state.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def set_pause(self, on: bool) -> bool:
        """放 / 删「请暂停」标记。脚本在下一次截图前看到就停住（战斗里不停），删掉就接着走。"""
        rt = self.current
        if not (rt and rt.ctl and rt.alive):
            return False
        p = rt.ctl / "pause"
        try:
            if on:
                p.touch()
            elif p.exists():
                p.unlink()
        except OSError:
            return False
        return True

    # ---------- 停止 ----------
    def stop(self, timeout: float = 8.0) -> bool:
        """停止当前工具（会阻塞最多 timeout 秒，界面里请放到后台线程调用）。"""
        rt = self.current
        if rt and rt.ctl:            # 11-02：先撤掉「请暂停」，停住的脚本收到 SIGTERM 照样结束
            try:
                (rt.ctl / "pause").unlink()
            except OSError:
                pass
        if not rt or not rt.alive:
            return True
        rt.stopped_by_user = True
        try:
            if IS_WIN:      # 11-23：哨兵是用 CREATE_NEW_PROCESS_GROUP 起的，整组发 CTRL_BREAK，哨兵收到后收掉工具
                if not _has_console():      # 11-24：CTRL_BREAK 只送得到共用同一个控制台的进程（用启动幻想引擎.bat 起就有）
                    raise OSError("没有控制台，CTRL_BREAK 送不到")
                os.kill(rt.pid, signal.CTRL_BREAK_EVENT)
            else:
                pgid = os.getpgid(rt.pid)
                os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if IS_WIN:      # 11-24：只结束哨兵会把工具留下，直接收整棵树
                    subprocess.run(["taskkill", "/PID", str(rt.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    rt.proc.terminate()
            except Exception:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline and rt.alive:
            time.sleep(0.3)
        if rt.alive:
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(rt.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    os.killpg(os.getpgid(rt.pid), signal.SIGKILL)
            except Exception:
                try:
                    rt.proc.kill()
                except Exception:
                    pass
        # 防御性还原分辨率(哨兵也会还原, 幂等)；工具本来就不改分辨率的不动
        if rt.resolution not in ("none", "", None):
            from core import env
            env.set_resolution(env.RES_NATIVE)
        return True

    def tail(self, n: int = 200) -> str:
        return "\n".join(list(self._buf)[-n:])

    @staticmethod
    def _hide_gui_window(pid: int):
        """启动数秒后尝试最小化目标 GUI 窗口(失败无妨, MFW 自身的 auto_minimize 已兜底)"""
        time.sleep(6)
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to tell (first process whose unix id is {pid}) '
                 f'to set miniaturized of window 1 to true'],
                capture_output=True, timeout=10)
        except Exception:
            pass


SHOTS_DIR = RUNS_DIR / "shots"


def take_shot(tool_id: str) -> str | None:
    """截设备当前画面，缩小存成 JPG（约 150KB）到 runs/shots/，返回文件名；失败返回 None。"""
    from core import env
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([env.adb_bin(), "exec-out", "screencap", "-p"], capture_output=True, timeout=15)
        if r.returncode != 0 or len(r.stdout) < 1000:
            return None
        name = f"{tool_id}_{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        tmp = SHOTS_DIR / (name + ".png")
        tmp.write_bytes(r.stdout)
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/sips", "-Z", "1000", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                            str(tmp), "--out", str(SHOTS_DIR / name)], capture_output=True, timeout=30)
        else:       # 11-23：没有 sips，用 Qt 缩到长边 1000、存成 JPG（质量 80）
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImage
            img = QImage(str(tmp))
            if not img.isNull():
                if max(img.width(), img.height()) > 1000:
                    img = img.scaled(1000, 1000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                img.save(str(SHOTS_DIR / name), "JPG", 80)
        _unlink(tmp)
        return name if (SHOTS_DIR / name).exists() else None
    except Exception:
        return None


def _pkill(pattern: str):
    if IS_WIN:          # 11-23：歌单里没有用 kill_patterns 的歌，Windows 上先不做
        return
    try:
        subprocess.run(["pkill", "-x", pattern], capture_output=True)
        subprocess.run(["pkill", "-f", pattern], capture_output=True)
    except Exception:
        pass


def _unlink(p: Path):
    try:
        p.unlink()
    except Exception:
        pass


def cleanup_stale(log_keep: int = 300, log_days: int = 60) -> tuple[int, int]:
    """启动时清理: 上次遗留的哨兵脚本；日志只删「超过 log_days 天且排在最新 log_keep 份之后」的。
    返回 (删掉的哨兵数, 删掉的日志数)。"""
    now = time.time()
    n_sent = n_log = 0
    try:
        for d in (RUNS_DIR / "ctl").glob("*"):        # 11-02 暂停协议的控制目录：上次没收掉的
            if d.is_dir() and now - d.stat().st_mtime > 120:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
    try:
        for p in RUNS_DIR.glob("sentinel_*.sh"):
            try:
                if now - p.stat().st_mtime > 120:  # 留两分钟余量，避免碰到刚生成的
                    p.unlink()
                    n_sent += 1
            except Exception:
                pass
    except Exception:
        pass
    try:
        logs = sorted((p for p in LOGS_DIR.glob("*.log") if p.is_file()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for p in logs[log_keep:]:
            try:
                if now - p.stat().st_mtime > log_days * 86400:
                    p.unlink()
                    n_log += 1
            except Exception:
                pass
    except Exception:
        pass
    try:                    # 11-03 作者：旧的战斗截图、乐土日志只留最近 14 天（放进废纸篓，见 core/old_logs.py）
        from core import old_logs
        old_logs.clean()
    except Exception:
        pass
    return n_sent, n_log
