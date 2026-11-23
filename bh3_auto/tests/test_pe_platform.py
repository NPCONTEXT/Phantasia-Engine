"""离线测试（11-23）：lib/pe_platform.py。Mac 那条路真跑；Windows 那条路把模块里的 IS_WIN / IS_MAC 改掉、用假目录走一遍。
不碰设备，也不调真的 adb。运行（在项目根目录）：
  bh3_auto/.venv/bin/python -B -m unittest bh3_auto/tests/test_pe_platform.py -v
"""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BH3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BH3))
from lib import pe_platform as pp  # noqa: E402


def make_exe(path: Path, body: str = "#!/bin/sh\necho hi\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class PePlatform(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pe_platform_"))
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in ("PE_ADB", "PE_FFMPEG", "PE_OCR", "PE_TMP"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        pp._rapid = None

    # ---- adb / ffmpeg
    def test_a1_env_first(self):
        adb = make_exe(self.tmp / "my" / "adb-yyb")      # 名字别以 /adb 结尾：跑测试套的沙箱会把它当成真 adb、连「能不能执行」都答否
        os.environ["PE_ADB"] = str(adb)
        self.assertEqual(pp.adb_path(), str(adb))

    def test_a2_path_second(self):
        adb = make_exe(self.tmp / "bin" / "adb")
        with mock.patch.object(pp.shutil, "which", lambda n: str(adb) if n == "adb" else None):
            self.assertEqual(pp.adb_path(), str(adb))

    def test_a3_env_missing_file_ignored(self):
        os.environ["PE_ADB"] = str(self.tmp / "nope" / "adb")
        with mock.patch.object(pp.shutil, "which", lambda n: "/x/adb"):
            self.assertEqual(pp.adb_path(), "/x/adb")

    def test_a4_mac_fallback_homebrew(self):
        """Mac 上 PATH 里没有：落到 Homebrew 的位置（和以前写死的一样）"""
        with mock.patch.object(pp, "IS_WIN", False), mock.patch.object(pp.shutil, "which", lambda n: None), \
                mock.patch.object(pp, "_first", lambda paths: None):
            self.assertEqual(pp.adb_path(), "/opt/homebrew/bin/adb")
            self.assertEqual(pp.ffmpeg_path(), "/opt/homebrew/bin/ffmpeg")

    def test_a5_windows_sdk_location(self):
        """装成 Windows：PATH 里没有时找 %LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe"""
        la = self.tmp / "LocalAppData"
        adb = make_exe(la / "Android" / "Sdk" / "platform-tools" / "adb.exe")
        os.environ["LOCALAPPDATA"] = str(la)
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(pp.shutil, "which", lambda n: None):
            self.assertEqual(os.path.normpath(pp.adb_path()), os.path.normpath(str(adb)))

    def test_a6_windows_nothing(self):
        os.environ["LOCALAPPDATA"] = str(self.tmp / "empty")
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(pp.shutil, "which", lambda n: None):
            self.assertEqual(pp.adb_path(), "adb.exe")
            self.assertEqual(pp.ffmpeg_path(), "ffmpeg.exe")

    def test_a7_windows_winget_ffmpeg(self):
        la = self.tmp / "LocalAppData"
        ff = make_exe(la / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        os.environ["LOCALAPPDATA"] = str(la)
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(pp.shutil, "which", lambda n: None):
            self.assertEqual(os.path.normpath(pp.ffmpeg_path()), os.path.normpath(str(ff)))

    # ---- 临时目录
    def test_t1_mac_tmp(self):
        with mock.patch.object(pp, "IS_WIN", False):
            p = pp.tmp_dir("pe_platform_test_dir")
        self.assertEqual(p, Path("/tmp/pe_platform_test_dir"))
        self.assertTrue(p.is_dir())
        p.rmdir()

    def test_t3_pe_tmp_env(self):
        """PE_TMP 换根目录（跑测试时和正在播放的脚本分开）"""
        os.environ["PE_TMP"] = str(self.tmp / "root")
        with mock.patch.object(pp, "IS_WIN", False):
            self.assertEqual(pp.tmp_dir("nikke"), self.tmp / "root" / "nikke")

    def test_t2_windows_tmp(self):
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(pp.tempfile, "gettempdir", lambda: str(self.tmp)):
            p = pp.tmp_dir("nikke")
        self.assertEqual(p, self.tmp / "nikke")
        self.assertTrue(p.is_dir())

    # ---- 识字
    def test_o1_engine_choice(self):
        with mock.patch.object(pp, "IS_MAC", True):
            self.assertEqual(pp.ocr_engine(), "vision")
        with mock.patch.object(pp, "IS_MAC", False):
            self.assertEqual(pp.ocr_engine(), "rapid")
        os.environ["PE_OCR"] = "rapid"
        with mock.patch.object(pp, "IS_MAC", True):
            self.assertEqual(pp.ocr_engine(), "rapid")

    @unittest.skipIf(os.name == "nt", "Vision 识字小程序只在 Mac 上用")
    def test_o2_vision_runs_binary(self):
        fake = make_exe(self.tmp / "ocr", '#!/bin/sh\necho "{\\"text\\": \\"$1\\", \\"conf\\": 1, \\"x\\": 1, \\"y\\": 2, \\"w\\": 3, \\"h\\": 4}"\n')
        os.environ["PE_OCR"] = "vision"
        out = pp.ocr_lines("/some/图.png", binary=fake)
        self.assertEqual(out.strip(), '{"text": "/some/图.png", "conf": 1, "x": 1, "y": 2, "w": 3, "h": 4}')

    def test_o3_rapid_format(self):
        """RapidOCR 的四角框换成左上角 + 宽高；双引号换单引号；没有结果时输出空串"""
        os.environ["PE_OCR"] = "rapid"
        pp._rapid = lambda path: ([[[[10, 20], [110, 22], [111, 50], [9, 48]], '确定"', 0.98765]], None)
        out = pp.ocr_lines("x.png")
        self.assertEqual(out, '{"text": "确定\'", "conf": 0.988, "x": 9, "y": 20, "w": 102, "h": 30}\n')
        pp._rapid = lambda path: (None, None)
        self.assertEqual(pp.ocr_lines("x.png"), "")

    def test_o4_rapid_missing_says_so(self):
        os.environ["PE_OCR"] = "rapid"
        pp._rapid = None
        with mock.patch.dict(sys.modules, {"rapidocr_onnxruntime": None}):
            with self.assertRaises(RuntimeError) as cm:
                pp.ocr_lines("x.png")
        self.assertIn("没装 RapidOCR", str(cm.exception))

    # ---- 中文路径（Windows 上自动换的 imread / imwrite；在 Mac 上强行换一次测，测完换回）
    def test_u1_unicode_cv2(self):
        import cv2
        import numpy as np
        orig = (cv2.imread, cv2.imwrite)
        try:
            pp.unicode_cv2()
            self.assertTrue(getattr(cv2, "_pe_unicode", False))
            img = np.zeros((20, 30, 3), np.uint8)
            img[5:10, 5:20] = (10, 200, 30)
            p = self.tmp / "妮姬日常" / "模板 小图.png"
            p.parent.mkdir()
            self.assertTrue(cv2.imwrite(str(p), img))
            back = cv2.imread(str(p))
            self.assertTrue(np.array_equal(back, img))
            g = cv2.imread(p, cv2.IMREAD_GRAYSCALE)          # Path 也行、灰度也行
            self.assertEqual(g.shape, (20, 30))
            j = self.tmp / "截图.jpg"
            self.assertTrue(cv2.imwrite(str(j), img, [cv2.IMWRITE_JPEG_QUALITY, 92]))
            self.assertEqual(cv2.imread(str(j)).shape, (20, 30, 3))
            self.assertIsNone(cv2.imread(str(self.tmp / "没有这个.png")))
            (self.tmp / "空.png").write_bytes(b"")
            self.assertIsNone(cv2.imread(str(self.tmp / "空.png")))
        finally:
            cv2.imread, cv2.imwrite = orig
            cv2._pe_unicode = False

    # ---- 通知、锁
    def test_n1_notify_non_mac_prints(self):
        buf = io.StringIO()
        with mock.patch.object(pp, "IS_MAC", False), contextlib.redirect_stdout(buf):
            pp.notify("妮姬 · 日常", "要更新资源")
        self.assertEqual(buf.getvalue(), "[通知] 妮姬 · 日常：要更新资源\n")

    def test_l1_lock_twice_blocks(self):
        p = self.tmp / "dev.lock"
        a = open(p, "a+")
        b = open(p, "a+")
        try:
            pp.lock_nb(a)
            with self.assertRaises(BlockingIOError):
                pp.lock_nb(b)
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
