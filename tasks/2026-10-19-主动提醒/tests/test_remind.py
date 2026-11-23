"""remind.py 离线测试：不发消息、不碰真实的播放记录（状态、日志写到临时目录）。
运行：在任务目录下 /usr/bin/python3 -m unittest discover -s tests -v
"""
import itertools
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remind  # noqa: E402

T = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M")  # noqa: E731


def rec(tid, start, result="ok", missed=None, outcome=""):
    ts = T(start).timestamp()
    return {"id": tid, "start": ts, "end": ts + 600, "result": result,
            "missed": [{"no": 1, "name": "x", "reason": m} for m in (missed or [])], "outcome": outcome}


def by_key(items):
    return {i["key"].split("@")[0]: i for i in items}


class DailyTest(unittest.TestCase):
    def test_window(self):
        self.assertEqual(remind.daily_checks([], T("2026-10-20 01:59")), [])
        at2 = by_key(remind.daily_checks([], T("2026-10-20 02:00")))
        self.assertEqual(set(at2), {"daily:bh3_daily", "daily:nikke_daily", "daily:zzz_daily"})
        self.assertEqual(len(remind.daily_checks([], T("2026-10-20 03:00"))), 3)
        self.assertEqual(remind.daily_checks([], T("2026-10-20 04:00")), [])          # 4 点的已过截止
        self.assertEqual(remind.daily_checks([], T("2026-10-20 05:00")), [])

    def test_game_day_boundary(self):
        now = T("2026-10-20 02:00")
        self.assertFalse(by_key(remind.daily_checks([rec("bh3_daily", "2026-10-19 03:59")], now))
                         ["daily:bh3_daily"]["done"])               # 上一个游戏日跑的不算
        self.assertTrue(by_key(remind.daily_checks([rec("bh3_daily", "2026-10-19 04:00")], now))
                        ["daily:bh3_daily"]["done"])

    def test_results(self):
        now = T("2026-10-20 02:00")
        for result, done in (("ok", True), ("partial", True), ("done", True),
                             ("abnormal", False), ("stopped", False), ("unmet", False)):
            item = by_key(remind.daily_checks([rec("zzz_daily", "2026-10-19 23:37", result)], now))["daily:zzz_daily"]
            self.assertEqual(item["done"], done, result)
            if not done:
                self.assertIn("没跑成", item["why"])

    def test_not_run_mentions_last(self):
        item = by_key(remind.daily_checks([rec("nikke_daily", "2026-10-18 23:22")], T("2026-10-20 02:00")))
        self.assertIn("今天还没跑（上次 10-18 23:22）", item["daily:nikke_daily"]["why"])


class WeeklyTest(unittest.TestCase):
    def test_ma(self):
        now = T("2026-10-26 02:00")                                  # 周一
        self.assertEqual(by_key(remind.weekly_checks([], T("2026-10-26 01:59"))).get("weekly:ma"), None)
        self.assertTrue(by_key(remind.weekly_checks([rec("bh3_memorial_arena", "2026-10-21 23:00")], now))
                        ["weekly:ma"]["done"])
        self.assertFalse(by_key(remind.weekly_checks([rec("bh3_memorial_arena", "2026-10-20 03:59")], now))
                         ["weekly:ma"]["done"])                     # 周二 04:00 开新期，之前的不算
        item = by_key(remind.weekly_checks([rec("bh3_memorial_arena", "2026-10-21 23:00", "partial",
                                                ["新 BOSS「空之律者」没学过、没打（学法见 使用说明.md）"])], now))
        self.assertFalse(item["weekly:ma"]["done"])
        self.assertIn("没学过", item["weekly:ma"]["why"])

    def test_er(self):
        now = T("2026-10-26 02:00")
        self.assertTrue(by_key(remind.weekly_checks([rec("bh3_elysium_fuhua", "2026-10-25 23:05", "done",
                                                         outcome="本周九档已领，没开新一局")], now))["weekly:er"]["done"])
        self.assertFalse(by_key(remind.weekly_checks([rec("bh3_elysium_fuhua", "2026-10-25 23:05", "unmet",
                                                          outcome="已停止：剧情页缺少操作按钮")], now))["weekly:er"]["done"])
        self.assertFalse(by_key(remind.weekly_checks([rec("bh3_elysium_fuhua", "2026-10-19 03:00", "done")], now))
                         ["weekly:er"]["done"])                     # 上周的

    def test_ss(self):
        self.assertIsNone(by_key(remind.weekly_checks([], T("2026-10-20 22:59"))).get("weekly:ss"))
        now = T("2026-10-20 23:00")                                  # 周二 → 周三 22:00 结算
        item = by_key(remind.weekly_checks([], now))["weekly:ss"]
        self.assertEqual(item["deadline"], T("2026-10-21 22:00"))
        self.assertEqual(item["label"], "周三22:00结算")
        self.assertFalse(by_key(remind.weekly_checks([rec("bh3_superstring", "2026-10-18 00:18")], now))
                         ["weekly:ss"]["done"])                     # 上一期的
        self.assertTrue(by_key(remind.weekly_checks([rec("bh3_superstring", "2026-10-20 21:00")], now))
                        ["weekly:ss"]["done"])
        self.assertFalse(by_key(remind.weekly_checks([rec("bh3_superstring", "2026-10-19 10:00", outcome=
                                                          "超弦空间本期未开放（每周三、周日 22:00 结算后到下期开放前）")],
                                                     now))["weekly:ss"]["done"])
        sat = by_key(remind.weekly_checks([], T("2026-10-24 23:00")))["weekly:ss"]
        self.assertEqual(sat["deadline"], T("2026-10-25 22:00"))
        self.assertEqual(sat["label"], "周日22:00结算")
        self.assertIsNone(by_key(remind.weekly_checks([], T("2026-10-21 22:00"))).get("weekly:ss"))


class MessageTest(unittest.TestCase):
    def test_single_titles(self):
        got = {i["key"].split("@")[0]: remind.make_title([i]) for i in remind.checks([], T("2026-10-26 02:00"))}
        self.assertEqual(got["daily:bh3_daily"], "【雨澜】崩3日常还没跑·04:00刷新")
        self.assertEqual(got["daily:zzz_daily"], "【雨澜】绝区零日常还没跑·04:00刷新")
        self.assertEqual(got["weekly:ma"], "【雨澜】记忆战场还没打完·04:00结算")
        self.assertEqual(got["weekly:er"], "【雨澜】乐土九档还没领·04:00刷新")
        ss = remind.weekly_checks([], T("2026-10-20 23:00"))[-1]
        self.assertEqual(remind.make_title([ss]), "【雨澜】超弦还没打满·周三22:00结算")

    def test_titles_within_limit(self):
        items = remind.checks([], T("2026-10-26 02:00"))             # 周一 02:00：3 个日常 + 记忆战场 + 乐土
        self.assertEqual(len(items), 5)
        for n in range(1, len(items) + 1):
            for combo in itertools.combinations(items, n):
                self.assertLessEqual(len(remind.make_title(list(combo))), remind.TITLE_MAX)
        self.assertEqual(remind.make_title(items[:2]), "【雨澜】崩3、妮姬还没跑·04:00刷新")
        self.assertEqual(remind.make_title(items[:3]), "【雨澜】3个日常还没跑·04:00刷新")
        self.assertEqual(remind.make_title(items), "【雨澜】5项还没做·04:00前")

    def test_desp_unchecked_hint(self):
        ss = remind.weekly_checks([], T("2026-10-20 23:00"))
        self.assertIn("顺序播放里没勾它", remind.make_desp(ss, {"bh3_daily"}))
        self.assertNotIn("没勾它", remind.make_desp(ss, {"bh3_superstring"}))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [mock.patch.object(remind, "STATE", d / "状态.json"),
                        mock.patch.object(remind, "LOG", d / "remind.log"),
                        mock.patch.object(remind, "load_history", return_value=[]),
                        mock.patch.object(remind, "checked_tools", return_value=None)]
        for p in self.patches:
            p.start()
        self.dir = d

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_send_once(self):
        now = T("2026-10-20 02:00")
        with mock.patch.object(remind, "busy", return_value=""), \
                mock.patch.object(remind, "send", return_value=(True, "pushid 1", False)) as s:
            self.assertEqual(remind.run(now), 0)
            self.assertEqual(s.call_count, 1)
            self.assertEqual(s.call_args[0][0], "【雨澜】3个日常还没跑·04:00刷新")
            remind.run(T("2026-10-20 02:10"))
            self.assertEqual(s.call_count, 1)                        # 同一截止不再发
        state = json.loads((self.dir / "状态.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["sent"]), 3)

    def test_busy_defers(self):
        with mock.patch.object(remind, "busy", return_value="有工具在跑（bh3_daily.py）"), \
                mock.patch.object(remind, "send") as s:
            remind.run(T("2026-10-20 02:00"))
            s.assert_not_called()
        self.assertIn("跑完再查", (self.dir / "remind.log").read_text(encoding="utf-8"))

    def test_network_error_retries_api_error_not(self):
        with mock.patch.object(remind, "busy", return_value=""):
            with mock.patch.object(remind, "send", return_value=(False, "网络错误：URLError", True)) as s:
                remind.run(T("2026-10-20 02:00"))
                remind.run(T("2026-10-20 02:10"))
                self.assertEqual(s.call_count, 2)                    # 网络错误：下次检查再试
            with mock.patch.object(remind, "send", return_value=(False, "接口报错：额度用完", False)) as s:
                remind.run(T("2026-10-20 02:20"))
                remind.run(T("2026-10-20 02:30"))
                self.assertEqual(s.call_count, 1)                    # 接口报错：不重试

    def test_unreadable_history_does_not_send(self):
        with mock.patch.object(remind, "load_history", side_effect=PermissionError("Operation not permitted")), \
                mock.patch.object(remind, "busy", return_value=""), mock.patch.object(remind, "send") as s:
            with self.assertRaises(PermissionError):
                remind.run(T("2026-10-20 02:00"))
            s.assert_not_called()

    def test_state_records_check(self):
        with mock.patch.object(remind, "busy", return_value=""), \
                mock.patch.object(remind, "send", return_value=(True, "pushid 1", False)):
            remind.run(T("2026-10-20 15:00"))
        state = json.loads((self.dir / "状态.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_check"], "2026-10-20T15:00:00")
        self.assertEqual(state["history_rows"], 0)

    def test_busy_by_fresh_log(self):
        logs = self.dir / "logs"
        logs.mkdir()
        f = logs / "zzz_daily_x.log"
        f.write_text("x", encoding="utf-8")
        with mock.patch.object(remind, "LOGS", logs), \
                mock.patch.object(remind, "tool_scripts", return_value=["no_such_tool_xyz.py"]):
            self.assertIn("日志在写", remind.busy())
            old = time.time() - 600
            os.utime(f, (old, old))
            self.assertEqual(remind.busy(), "")


if __name__ == "__main__":
    unittest.main()
