"""离线测试（11-23 跨平台）：Python 版哨兵（core/sentinel.py）在本机真跑；Windows 那几处路径换算装成 Windows 走一遍。
不碰设备：adb 换成临时目录里的假的（Mac / Linux 是 sh 脚本，Windows 是 .cmd 包一层 Python；名字不以 /adb 结尾，外面套禁止启动 adb 的沙箱也能跑）。
Mac 和 Windows 都能跑（11-24 起，GitHub 的 Windows 机器每次推送都跑一遍）。运行（在项目根目录）：
  venv-qt.nosync/bin/python -B -m unittest test_fake/test_platform.py -v
  Windows：venv-qt.nosync\\Scripts\\python.exe -B -m unittest test_fake\\test_platform.py -v
拿别的代码跑：环境变量 TEST_ROOT=那个目录
"""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("TEST_ROOT") or HERE.parent)
sys.path.insert(0, str(ROOT))
SENTINEL = ROOT / "core" / "sentinel.py"
IS_WIN = os.name == "nt"

# 测试自己的输出用 UTF-8（英文 Windows 的控制台、管道是 cp1252，用例说明里的中文会报错）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FAKE_ADB = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
"""
FAKE_ADB_PY = """import os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
"""


def alive(pid):
    """进程还在不在。Windows 上不能用 os.kill(pid, 0)：它会直接结束那个进程。"""
    if IS_WIN:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_uint32()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Sentinel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pe_sentinel_"))
        if IS_WIN:
            (self.tmp / "fake_adb.py").write_text(FAKE_ADB_PY, encoding="utf-8")
            self.adb = self.tmp / "fake_adb.cmd"
            self.adb.write_text('@"%s" "%%~dp0fake_adb.py" %%*\r\n' % sys.executable, encoding="utf-8")
        else:
            self.adb = self.tmp / "fake_adb"
            self.adb.write_text(FAKE_ADB)
            self.adb.chmod(self.adb.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "adb.log"
        self.log.write_text("")
        self.env = dict(os.environ, FAKE_LOG=str(self.log), PYTHONDONTWRITEBYTECODE="1")
        if IS_WIN:      # 和引擎给子进程的环境一样（core/env.py child_env）：输出用 UTF-8
            self.env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    def adb_calls(self):
        return [l.strip() for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def start(self, res, pkg, tool, agg_pid=None):
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WIN else {"start_new_session": True}
        return subprocess.Popen([sys.executable, str(SENTINEL), str(agg_pid or os.getpid()), res, str(self.adb), pkg, "--", *tool],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                env=self.env, **group)

    def test_s1_normal_exit_code_and_output(self):
        """S1 工具正常结束：输出原样带出来，退出码照工具的；分辨率 none 不碰 adb"""
        p = self.start("none", "", [sys.executable, "-c", "print('== 签到'); print('完成：签到=ok'); raise SystemExit(3)"])
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 3, out)
        self.assertIn("== 签到", out)
        self.assertIn("完成：签到=ok", out)
        self.assertEqual(self.adb_calls(), [])

    def test_s2_prekill_and_resolution(self):
        """S2 顺序播放先关游戏、切分辨率，结束后还原"""
        p = self.start("2400x1504", "com.example.game", [sys.executable, "-c", "print('hi')"])
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("[引擎] 顺序播放：先关掉游戏 com.example.game", out)
        self.assertEqual(self.adb_calls(), ["shell wm size 2400x1504", "shell am force-stop com.example.game", "shell wm size reset"])

    def test_s3_engine_dies(self):
        """S3 引擎没了：3 秒内收掉工具、还原分辨率，退出码 1"""
        fake_engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        p = self.start("native", "", [sys.executable, "-c", "import time; print('running', flush=True); time.sleep(60)"],
                       agg_pid=fake_engine.pid)
        time.sleep(1.5)
        fake_engine.kill()
        fake_engine.wait()
        t = time.time()
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 1, out)
        self.assertLess(time.time() - t, 8)
        self.assertEqual(self.adb_calls(), ["shell wm size reset", "shell wm size reset"])

    def test_s4_stop(self):
        """S4 引擎点停止（Mac / Linux：整组 SIGTERM；Windows：整组 CTRL_BREAK）：工具结束，哨兵退出码 1、还原分辨率"""
        p = self.start("2400x1504", "", [sys.executable, "-c", "import time; print('running', flush=True); time.sleep(60)"])
        line = p.stdout.readline()
        self.assertIn("running", line)
        if IS_WIN:
            try:
                os.kill(p.pid, signal.CTRL_BREAK_EVENT)
            except OSError as e:      # 没有控制台时送不出去；引擎这时直接 taskkill（core/runner.py），这里测不到
                p.kill()
                p.communicate(timeout=20)
                self.skipTest("没有控制台，CTRL_BREAK 送不出去：%s" % e)
        else:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 1, out)
        self.assertEqual(self.adb_calls()[-1], "shell wm size reset")

    def test_s5_leftover_child_killed(self):
        """S5 工具自己起的子进程，工具退出后还在的，也收掉（不留孤儿占着日志管道）"""
        pidfile = self.tmp / "child.pid"
        code = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL); "
                "open(sys.argv[1], 'w').write(str(p.pid))")
        p = self.start("none", "", [sys.executable, "-c", code, str(pidfile)])
        out, _ = p.communicate(timeout=20)
        child = int(pidfile.read_text())
        time.sleep(0.5)
        self.assertFalse(alive(child), "工具留下的子进程还在")

    def test_s6_bad_args(self):
        """S6 参数不对：退出码 2，不起任何东西"""
        r = subprocess.run([sys.executable, str(SENTINEL), "1", "none"], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 2)


class WindowsPaths(unittest.TestCase):
    def test_w1_venv_python(self):
        """W1 装成 Windows：…/bin/python 换成 …/Scripts/python.exe，别的不动；不是 Windows 时原样"""
        from core import registry
        with mock.patch.object(registry, "IS_WIN", True):
            self.assertEqual(registry.win_venv_python("../../bh3_auto/.venv/bin/python"), "../../bh3_auto/.venv/Scripts/python.exe")
            self.assertEqual(registry.win_venv_python(".venv/bin/python"), ".venv/Scripts/python.exe")
            self.assertEqual(registry.win_venv_python("bh3_daily.py"), "bh3_daily.py")
        with mock.patch.object(registry, "IS_WIN", False):
            self.assertEqual(registry.win_venv_python("../../bh3_auto/.venv/bin/python"), "../../bh3_auto/.venv/bin/python")

    def test_w2_commands_from_real_tools_json(self):
        """W2 装成 Windows：歌单里每首歌的启动命令第一项都换成 Scripts/python.exe（乐土那种命令里直接写解释器的也换）"""
        from core import registry
        tools = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))["tools"]
        with mock.patch.object(registry, "IS_WIN", True):
            for t in tools:
                cmd = registry.resolve_command(t)
                if t.get("python") or str((t.get("command") or [""])[0]).endswith("bin/python"):
                    self.assertTrue(cmd[0].replace("\\", "/").endswith("Scripts/python.exe"), (t["id"], cmd[0]))

    def test_w3_precheck(self):
        """W3 装成 Windows：自检清单不查 Vision 识字小程序；Homebrew 的 ffmpeg 按 PATH 找；解释器换路径"""
        from core import registry
        tool = {"workdir": "tasks/x", "precheck": ["a.py", "../../bh3_auto/.venv/bin/python", "../../bh3_auto/ocr/ocr",
                                                   "ocr_rev2", "/opt/homebrew/bin/ffmpeg"]}
        with mock.patch.object(registry, "IS_WIN", True), mock.patch("shutil.which", lambda n: "C:/ff/ffmpeg.exe" if n == "ffmpeg" else None), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PE_FFMPEG", None)
            got = [str(p).replace("\\", "/") for p in registry.precheck_files(tool)]
        self.assertEqual(len(got), 3, got)
        self.assertTrue(got[0].endswith("tasks/x/a.py"))
        self.assertTrue(got[1].endswith("bh3_auto/.venv/Scripts/python.exe"))
        self.assertEqual(got[2], "C:/ff/ffmpeg.exe")
        with mock.patch.object(registry, "IS_WIN", False):
            got_mac = registry.precheck_files(tool)
        self.assertEqual(len(got_mac), 5)

    def test_w3b_precheck_pe_ffmpeg(self):
        """W3b 装成 Windows：自检里 ffmpeg 先认 PE_FFMPEG 指定的（11-24），不在 PATH 也算有"""
        from core import registry
        tmp = Path(tempfile.mkdtemp(prefix="pe_ff_"))
        ff = tmp / "ffmpeg.exe"
        ff.write_text("x")
        tool = {"workdir": "tasks/x", "precheck": ["/opt/homebrew/bin/ffmpeg"]}
        with mock.patch.object(registry, "IS_WIN", True), mock.patch("shutil.which", lambda n: None), \
                mock.patch.dict(os.environ, {"PE_FFMPEG": str(ff)}):
            got = registry.precheck_files(tool)
        self.assertEqual([str(p) for p in got], [str(ff)])

    def test_w4_configured_adb(self):
        """W4 指定 adb：PE_ADB 或 tools.json 的 app.adb 指到存在的文件就用它；文件不在当没指定；子进程环境带上 PE_ADB"""
        from core import env, registry
        tmp = Path(tempfile.mkdtemp(prefix="pe_adb_"))
        mine = tmp / "adb-yyb"
        mine.write_text("#!/bin/sh\n")
        mine.chmod(0o755)
        with mock.patch.dict(os.environ, {"PE_ADB": str(mine)}):
            self.assertEqual(env.adb_bin(), str(mine))
            self.assertEqual(env.child_env()["PE_ADB"], str(mine))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PE_ADB", None)
            with mock.patch.object(registry, "load", lambda: {"app": {"adb": str(mine)}}):
                self.assertEqual(env.adb_bin(), str(mine))
            with mock.patch.object(registry, "load", lambda: {"app": {"adb": str(tmp / "nope")}}):
                self.assertNotEqual(env.adb_bin(), str(tmp / "nope"))

    def test_w5_py_sentinel_switch(self):
        """W5 哨兵怎么选：Mac 默认 bash 版；PE_SENTINEL=py 或 Windows 用 Python 版"""
        from core import runner
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PE_SENTINEL", None)
            with mock.patch.object(runner, "IS_WIN", False):
                self.assertFalse(runner.py_sentinel())
            with mock.patch.object(runner, "IS_WIN", True):
                self.assertTrue(runner.py_sentinel())
            os.environ["PE_SENTINEL"] = "py"
            with mock.patch.object(runner, "IS_WIN", False):
                self.assertTrue(runner.py_sentinel())

    def test_w6_child_env_utf8(self):
        """W6 Windows 上给子进程设 PYTHONUTF8=1、PYTHONIOENCODING=utf-8（引擎按 UTF-8 读脚本输出）；Mac 上不设"""
        from core import env
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHONUTF8", None)
            os.environ.pop("PYTHONIOENCODING", None)
            with mock.patch.object(env, "IS_WIN", True):
                e = env.child_env()
                self.assertEqual((e.get("PYTHONUTF8"), e.get("PYTHONIOENCODING")), ("1", "utf-8"))
            with mock.patch.object(env, "IS_WIN", False):
                e = env.child_env()
                self.assertNotIn("PYTHONUTF8", e)
                self.assertNotIn("PYTHONIOENCODING", e)


if __name__ == "__main__":
    unittest.main()
