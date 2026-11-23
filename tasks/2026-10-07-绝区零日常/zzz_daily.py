"""绝区零（国服 com.miHoYo.Nap）日常一键（幻想引擎调用）。

横屏，坐标基准 2400x1504（设备原生横屏）。OCR 找字为主，图标按钮用实测坐标；每步失败只记日志并跳过。
游戏每天 4:00 刷新。花菲林 / 以太电池 / 补充电量 / 自动战斗（要消耗道具）的按钮一律不点。
例外（10-18 作者）：电量不够打下一场时，只从「储蓄电量」取差额（reserve_top_up），储蓄电量也不够就不打。
流程（作者给的）：启动 → 点击进入 → 签到弹窗 → 快捷手册「日常」逐项「前往」：
  喝茶（汀曼大师，选第二个「鲜萃红茶」+60 电量）→ 零食盲盒 → 录像店（宣传员安比 + 推荐上架 + 开始营业）
  → 训练 · 实战模拟室 · 基础材料 · 赏金专项（银币）· 默认配队 · 出战，每场 100 电量，最多 3 场
  → 领日常活跃度奖励。
识字用本目录 ocr_rev2（锁定 Vision 旧版识别，新版在快捷手册页会把方向判反）。
用法：zzz_daily.py [--only start,tea,snack,video,battle,claim] [--battles N]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

try:        # 11-02 暂停协议（幻想引擎的 lib/play_ctl.py，经 PYTHONPATH 传进来）；单独运行时 import 不到，用什么都不做的替身
    from play_ctl import pause_point, battle
except ImportError:
    import contextlib

    def pause_point():
        pass

    class battle(contextlib.ContextDecorator):
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

HERE = Path(__file__).resolve().parent
OCR_BIN = HERE / "ocr_rev2"
sys.path.insert(0, str(HERE.parent.parent / "bh3_auto"))
from lib.pe_platform import adb_path, ocr_lines, tmp_dir  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）
TPL = HERE / "tpl"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = [adb_path(), "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
PKG = "com.miHoYo.Nap"
BASE_W, BASE_H = 2400, 1504
TMP = tmp_dir("zzz")
CLAIM_BTN_MIN = 3000    # 11-24：7 天签到活动那排亮黄像素超过这个数 = 有没领的「领取」按钮（没领约 11000、领过 0，见 tests/test_signin_20261124.py）
TMP.mkdir(exist_ok=True)

# 这些字所在的按钮绝不点
FORBID = ("自动战斗", "购买", "补充", "以太电池", "储蓄电量", "充值")

BIG = (1905, 1165)        # 大世界右下最大的按钮 = 对话/互动（战斗里同位置是普攻）
RETURN = (172, 158)       # 菜单页左上红色返回
QUICK = (1975, 114)       # 右上「快捷手册」（Z 字头像图标）：先按模板 tpl/quick.png 找，找不到才点这里（10-20 新版本顶栏没了城募，它从 1830 挪到 1975，原 1830 是活动 EVENTS）
QUICK_X = (2258, 395)     # 快捷手册右上关闭
DIALOG = (1200, 1275)     # 对话框正文，点一下翻页
# 战斗按钮
ATK, SPEC, ULT, SWITCH = (1905, 1170), (1692, 1290), (2118, 792), (2118, 1038)
STAMINA_COST = 100
SNACK_BOX = (1662, 662)   # 零食盲盒第一排中间那盒
COMBO = 6                 # 每轮连点普攻次数（一整套普攻连段）


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def norm(t: str) -> str:
    return re.sub(r"[^一-鿿0-9/%×\-]", "", t)


class Bot:
    def __init__(self) -> None:
        self.w, self.h = BASE_W, BASE_H
        self.img = None
        self.items: list[tuple[str, int, int]] = []
        self.note: str | None = None
        self.signin_claimed = False     # 这次运行点过今天那格
        self.signin_swiped = False      # 签到格子已经往右滑过一次
        self.signin_got = False         # 这次运行点完今天那格后见过「获得」提示（11-23）
        self.signin_miss = 0            # 点了黄格却没出「获得」的次数（11-23）
        self.signin_btn = 0             # 点 7 天签到活动「领取」按钮的次数（11-24）
        self.tpl = {k: cv2.imread(str(TPL / f"{k}.png")) for k in ("atk", "return", "quick", "city")}

    # ---------- 基础 ----------
    def adb(self, args: list[str], timeout: float = 20) -> bytes:
        return subprocess.run(ADB + args, capture_output=True, timeout=timeout).stdout

    def shell(self, cmd: str, timeout: float = 20) -> bytes:
        return self.adb(["shell", cmd], timeout)

    def grab(self):
        pause_point()        # 11-02 幻想引擎要暂停就停在截图前（战斗里不停），继续后重新截图
        raw = self.adb(["exec-out", "screencap"])
        if len(raw) > 16:
            w, h = np.frombuffer(raw[:8], "<u4")
            hdr = len(raw) - int(w) * int(h) * 4
            if hdr in (12, 16):
                a = np.frombuffer(raw[hdr:], np.uint8).reshape(int(h), int(w), 4)
                return cv2.cvtColor(a, cv2.COLOR_RGBA2BGR)
        png = self.adb(["exec-out", "screencap", "-p"])
        return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)

    def ocr_img(self, img) -> list[tuple[str, int, int]]:
        p = TMP / "cur.png"
        cv2.imwrite(str(p), img)
        out = ocr_lines(p, timeout=30, binary=OCR_BIN)
        res = []
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            res.append((d["text"], int(d["x"] + d["w"] / 2), int(d["y"] + d["h"] / 2)))
        return res

    def ocr(self) -> list[tuple[str, int, int]]:
        self.img = self.grab()
        self.h, self.w = self.img.shape[:2]
        sx, sy = BASE_W / self.w, BASE_H / self.h
        self.items = [(t, int(x * sx), int(y * sy)) for t, x, y in self.ocr_img(self.img)]
        return self.items

    def _xy(self, x, y):
        return int(x * self.w / BASE_W), int(y * self.h / BASE_H)

    def tap(self, x: int, y: int, wait: float = 1.5) -> None:
        self.shell("input tap %d %d" % self._xy(x, y))
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, ms: int = 600, wait: float = 1.2) -> None:
        a, b = self._xy(x1, y1), self._xy(x2, y2)
        self.shell(f"input swipe {a[0]} {a[1]} {b[0]} {b[1]} {ms}", timeout=ms / 1000 + 15)
        time.sleep(wait)

    def find(self, key: str, exact: bool = False, region=None, fresh: bool = False):
        items = self.ocr() if fresh else self.items
        k = norm(key)
        for t, x, y in items:
            a = norm(t)
            if (a == k if exact else k in a) and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
                return x, y
        return None

    def find_all(self, key: str, region=None):
        k = norm(key)
        return [(x, y) for t, x, y in self.items
                if k in norm(t) and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3]))]

    def tap_text(self, key: str, exact: bool = False, region=None, wait: float = 1.5, fresh: bool = True) -> bool:
        if any(f in key for f in FORBID):
            log(f"  !! 拒绝点禁点按钮「{key}」")
            return False
        pos = self.find(key, exact, region, fresh)
        if pos:
            log(f"  点「{key}」@{pos}")
            self.tap(*pos, wait=wait)
            return True
        return False

    def wait_any(self, keys, timeout: float = 10, region=None):
        end = time.time() + timeout
        while True:
            self.ocr()
            for k in keys:
                pos = self.find(k, region=region)
                if pos:
                    return k, pos
            if time.time() > end:
                return None, None
            time.sleep(0.8)

    def snap(self, tag: str) -> None:
        if self.img is not None:
            p = TMP / f"{tag}_{time.strftime('%m%d-%H%M%S')}.png"
            cv2.imwrite(str(p), self.img)
            log(f"  截图 {p}")

    def match(self, name: str, cx: int, cy: int, r: int = 60, img=None) -> float:
        img = self.img if img is None else img
        t = self.tpl[name]
        h, w = t.shape[:2]
        roi = img[max(0, cy - h // 2 - r):cy + h // 2 + r, max(0, cx - w // 2 - r):cx + w // 2 + r]
        if roi.shape[0] < h or roi.shape[1] < w:
            return 0.0
        return float(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED).max())

    def color_count(self, x1, y1, x2, y2, hue: tuple[int, int], smin=150, vmin=150) -> int:
        hsv = cv2.cvtColor(self.img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        m = (hsv[..., 0] >= hue[0]) & (hsv[..., 0] <= hue[1]) & (hsv[..., 1] > smin) & (hsv[..., 2] > vmin)
        return int(m.sum())

    def bounty_selected(self) -> bool:
        """赏金专项卡被选中 = 卡周围的高亮框。10-07 是黄框（色相 25–30，约 7900 像素），10-09 变成黄绿框（35–40，约 6600）；
        这期每张卡还多了黄色「双倍」角标（色相 20–25，约 1800，选没选中都有，旧规则 20–35 > 2000 因此判错）→ 数 25–45、超过 4000。"""
        return self.color_count(985, 960, 1405, 1175, (25, 45), 150, 180) > 4000

    def red_count(self, x1, y1, x2, y2) -> int:
        return self.color_count(x1, y1, x2, y2, (0, 8)) + self.color_count(x1, y1, x2, y2, (172, 180))

    # ---------- 页面判定 ----------
    def is_world(self) -> bool:
        """大世界：左上「星期X」+「AM/PM New Eridu」。"""
        if self.find("今日最大活跃度"):
            return False
        return bool(self.find("星期", region=(250, 60, 560, 140)) or
                    any("Eridu" in t for t, x, y in self.items if x < 700 and y < 200))

    def loading_tip(self) -> bool:
        """加载页：左下「No.31 COMBAT」一类编号 + 一段玩法提示。"""
        return any(re.match(r"^No\.\s*\d+", t) and x < 700 and y > 1150 for t, x, y in self.items)

    def find_icon(self, name: str, region=(1450, 40, 2400, 190), th: float = 0.8):
        """当前画面 region 里按模板找图标中心（基准坐标），找不到 None。顶栏图标随版本 / 活动左右挪，别写死坐标（10-20）。"""
        if self.img is None:
            return None
        img = self.img if self.img.shape[1] == BASE_W else cv2.resize(self.img, (BASE_W, BASE_H))
        x1, y1, x2, y2 = region
        t = self.tpl[name]
        _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(img[y1:y2, x1:x2], t, cv2.TM_CCOEFF_NORMED))
        if mx < th:
            return None
        h, w = t.shape[:2]
        return x1 + loc[0] + w // 2, y1 + loc[1] + h // 2

    def has_return(self) -> bool:
        return self.match("return", *RETURN) > 0.8

    def in_quick(self) -> bool:
        return bool(self.find("今日最大活跃度") or (self.find("日常", exact=True, region=(1100, 230, 1400, 320))
                                                and self.find("训练", exact=True, region=(1350, 230, 1650, 320))))

    def dialog_open(self) -> bool:
        """对话框：中间说话人名字 y≈1091，正文 y≈1230–1300。"""
        name = any(1050 <= y <= 1130 and 900 <= x <= 1500 for t, x, y in self.items)
        body = any(1200 <= y <= 1320 and len(norm(t)) >= 4 for t, x, y in self.items)
        return name and body

    def confirm_got(self) -> bool:
        """「获得 … 确认」奖励提示。"""
        if self.find("获得", exact=True, region=(1000, 500, 1400, 640)):
            pos = self.find("确认", exact=True, region=(1000, 980, 1800, 1110))
            if pos:
                log(f"  关「获得」提示 @{pos}")
                self.tap(*pos, wait=2)
                return True
        return False

    def to_world(self, rounds: int = 12) -> bool:
        for _ in range(rounds):
            self.ocr()
            if self.is_world():
                return True
            if self.confirm_got():
                continue
            if self.in_quick():
                log("  关快捷手册")
                self.tap(*QUICK_X, wait=2)
                continue
            for k in ("不喝了", "暂时不用"):
                if self.tap_text(k, exact=True, fresh=False, wait=2):
                    break
            else:
                if self.has_return():
                    log("  点左上返回")
                    self.tap(*RETURN, wait=3)
                elif self.dialog_open():
                    self.tap(*DIALOG, wait=1.5)
                elif len(self.items) <= 2 or self.loading_tip():      # 加载中（黑屏 / 只剩 UID / 提示页）
                    time.sleep(2)
                else:
                    self.snap("unknown")
                    time.sleep(2)
        log("  回不到大世界")
        return False

    # ---------- 启动 + 签到弹窗 ----------
    def ensure_game(self) -> None:
        focus = self.shell("dumpsys window | grep mCurrentFocus").decode(errors="ignore")
        if PKG not in focus:
            log("启动游戏")
            self.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
            time.sleep(15)

    def step_start(self) -> None:
        self.ensure_game()
        end = time.time() + 300
        signed = False
        while time.time() < end:
            self.ocr()
            if self.is_world():
                if not signed:
                    log("  到大世界（没见到签到弹窗，可能今天已领或没有）")
                return
            if any(k in t for t, _, _ in self.items for k in ("更新", "下载")) and self.find("确认"):
                self.snap("update")
                self.note = "游戏要更新，没替你点"
                raise RuntimeError("游戏要更新")
            pos = self.find("点击进入游戏")
            if pos:
                log("  标题页，点击进入游戏")
                self.tap(*pos, wait=8)
                continue
            if self.confirm_got():
                self.signin_got = self.signin_got or self.signin_claimed   # 点完格子「获得」提示晚出来的也算领到
                continue
            if self.signin_popup():
                signed = True
                continue
            if self.in_quick():
                self.tap(*QUICK_X, wait=2)
                continue
            if self.has_return():
                self.tap(*RETURN, wait=3)
                continue
            if self.dialog_open():
                self.tap(*DIALOG, wait=1.5)
                continue
            time.sleep(3)
        self.note = "5 分钟没进到大世界"
        self.snap("start_fail")

    def signin_popup(self) -> bool:
        """签到弹窗（「惊喜放映企划」等）：一排「NN DAY」卡片，今天那格的角标是黄色、没打勾。
        点今天那格领，领过（没有黄色角标）就点左上返回 (172,80) 关掉。"""
        # 11-24：RapidOCR（Windows）常把卡片上的「DAY」读成「DA」；Vision 不会单独读出「DA」，Mac 上不变
        days = [(x, y) for t, x, y in self.items if ("DAY" in t.upper() or t.strip().upper() == "DA") and 600 <= y <= 720]
        if not days:
            return False
        # 11-24：7 天签到活动那种弹窗，卡片下面一排是「领取×60」/「已领取×60」，只认按钮、不按颜色找格子（见 signin_event_row）
        row = [(t, x, y) for t, x, y in self.items if "领取" in t and 1030 <= y <= 1110 and 600 <= x <= 2360]
        if row:
            return self.signin_event_row(row)
        # 11-23：7 天签到活动「决战于黎明之前」第 7 天的大奖格整张是黄的，今天那格领完后它被当成「今天那格」，
        # 点了 8 下都没反应（每下存一张图，白花 45 秒；颜色稍一变才碰巧停下，不变的话会点到 5 分钟超时）。
        # 黄格点了没出「获得」累计 2 次就不再点：第 2 下留给第一下没点上的情况。
        give_up = self.signin_miss >= 2
        today = None if give_up else self.signin_today_x()
        if today:
            log(f"  签到弹窗：领今天那格 @({today - 60},830)")
            self.tap(today - 60, 830, wait=3)
            self.signin_claimed = True
            self.ocr()
            if self.confirm_got():
                self.signin_got = True
            else:
                self.signin_miss += 1
                self.snap("signin")
            return True
        # 10-11：10-08 起每次都判「今天已领」关掉，实际攒了 3 天没领（作者登录时发现，点一下领了三天）。
        # 推断（没截到图）：攒了几天后弹窗停在前面领过的格子，能领的在右边屏幕外 → 先往右滑一次再找。
        if not self.signin_claimed and not self.signin_swiped:
            self.signin_swiped = True
            log("  签到弹窗：没看到今天那格，往右滑一下再找")
            self.swipe(1900, 830, 900, 830, 600, wait=1.5)
            return True
        if give_up and not self.signin_got:   # 黄格还在、点了 2 次没反应，这次运行也没见过「获得」：不当 ok
            self.snap("signin_noreact")
            log("  签到弹窗：黄色那格点了 2 次都没出「获得」，可能没领，关掉")
            self.note = "签到弹窗点了没反应，可能没领（截图已存）"
        elif give_up:
            log("  签到弹窗：今天已领；还有一格黄的点了 2 次没反应（多半是还没到日子的大奖格），关掉")
        elif self.signin_claimed:
            log("  签到弹窗：今天已领，关掉")
        else:                          # 这次没领过却没认出今天那格：不再当 ok（今天领过时游戏不弹这个窗，10-07 侦察）
            self.snap("signin_unknown")
            log("  签到弹窗：没认出今天那格，可能没领，关掉")
            self.note = "签到弹窗没认出今天那格，可能没领（截图已存）"
        self.tap(172, 80, wait=3)
        return True

    def signin_event_row(self, row) -> bool:
        """7 天签到活动弹窗（「决战于黎明之前」）：今天能领的那格下面是亮黄的「领取×60」按钮，领过的变灰、读成「已领取×60」
        （11-21、11-22、11-24 领之前、11-21 和 11-23 领之后的图都是这样）。第 7 天大奖格整张金黄，按颜色找常先挑中它
        （11-21 第二下才点对，11-24 两下都点在大奖格、第 4 天没领），所以这种弹窗只认按钮：「领取」开头的点它（最多 2 下），
        只剩「已领取」就当今天领过了、关掉。没读到「领取」但那一排有亮黄按钮：不乱点，关掉报可能没领。"""
        btn = next(((x, y) for t, x, y in row if norm(t).startswith("领取")), None)
        if btn and self.signin_btn < 2:
            log(f"  签到弹窗（7 天活动）：点今天的「领取」@{btn}")
            self.tap(*btn, wait=3)
            self.signin_btn += 1
            self.signin_claimed = True
            self.ocr()
            if self.confirm_got():
                self.signin_got = True
            else:
                self.snap("signin_btn")
            return True
        if btn:
            self.snap("signin_btn_noreact")
            log("  签到弹窗（7 天活动）：「领取」点了 2 次还在，可能没领，关掉")
            self.note = "签到弹窗「领取」点了没反应，可能没领（截图已存）"
        elif self.signin_btn:
            log("  签到弹窗（7 天活动）：今天的「领取」点过了、变成「已领取」，关掉")
        elif self.claim_btn_pixels() > CLAIM_BTN_MIN:
            self.snap("signin_btn_unread")
            log("  签到弹窗（7 天活动）：那一排有亮黄按钮但没读出「领取」，可能没领，关掉")
            self.note = "签到弹窗有「领取」按钮没读出字，可能没领（截图已存）"
        else:
            log("  签到弹窗（7 天活动）：只有「已领取」，今天已领，关掉")
        self.tap(172, 80, wait=3)
        return True

    def claim_btn_pixels(self) -> int:
        """7 天活动卡片下面那排（y 1045–1095）亮黄的像素数：没领的「领取」按钮底色 BGR(0,214,233)、色相 28。
        离线实测：没领的约 11000、领过的和普通月签到弹窗都是 0。"""
        hsv = cv2.cvtColor(self.img[1045:1095, 600:2360], cv2.COLOR_BGR2HSV)
        m = (hsv[..., 0] >= 25) & (hsv[..., 0] <= 32) & (hsv[..., 1] > 200) & (hsv[..., 2] > 180)
        return int(m.sum())

    def signin_today_x(self):
        """整排按颜色找今天那格的黄角标，返回角标中心 x（基准坐标），没有返回 None。
        10-12：黄角标那格识字读不出「DAY」（只读出 08/09/14 三格），只在 DAY 字的位置查颜色就漏了 → 改成整排扫。
        离线实测（色相 18–32、90×80 窗口）：今天那格 4200–4800 像素，其余格 ≤61。"""
        best, x0 = 0, None
        for x in range(600, 2360, 10):
            c = self.color_count(x - 45, 603, x + 45, 683, (18, 32))
            if c > best:
                best, x0 = c, x
        if best <= 1500:
            return None
        hsv = cv2.cvtColor(self.img[603:683, x0 - 60:x0 + 60], cv2.COLOR_BGR2HSV)
        m = (hsv[..., 0] >= 18) & (hsv[..., 0] <= 32) & (hsv[..., 1] > 150) & (hsv[..., 2] > 150)
        return int(x0 - 60 + np.nonzero(m)[1].mean())

    # ---------- 快捷手册 ----------
    def open_quick(self, tab: str = "日常") -> bool:
        self.ocr()                      # 一定要现截：上一步可能刚关掉手册，旧的识字结果会骗人
        if not self.in_quick():
            if not self.to_world():
                return False
            xy = self.find_icon("quick")
            if not xy:
                log(f"  顶栏没认出快捷手册图标，按默认位置点 {QUICK}")
                xy = QUICK
            self.tap(*xy, wait=2.5)
            self.ocr()
            if not self.in_quick():
                self.snap("quick_fail")
                return False
        pos = self.find(tab, exact=True, region=(850, 230, 2100, 320))
        if pos:
            self.tap(*pos, wait=2)
            self.ocr()
        return True

    def stamina(self) -> int | None:
        """快捷手册顶栏「240/240」或选关页/结算页「140/100」的左边数字。"""
        for t, x, y in self.items:
            m = re.match(r"^\s*(\d{1,3})\s*/\s*(\d{2,3})\s*$", t)
            if m and ((1650 <= x <= 1900 and 120 <= y <= 190) or (y > 1300 and x > 1000)):
                return int(m.group(1))
        return None

    def find_daily(self, key: str):
        """日常页找含 key 的卡片：返回 ("go", 前往坐标) / ("done", None) / (None, None)。先滑到最左再往右找。
        找不到时把几屏读到的卡片标题记进 self.daily_seen、存截图（10-11：「刮刮乐」没找到又没留证据，分不清是今天没有还是没认出）。"""
        self.daily_seen = []
        for _ in range(2):
            self.swipe(1050, 800, 2050, 800, 500, wait=1)
        for i in range(4):
            self.ocr()
            for t, x, y in self.items:
                if 400 <= x <= 2250 and 600 <= y <= 800 and len(t.strip()) >= 2 and t.strip() not in self.daily_seen:
                    self.daily_seen.append(t.strip())
            keys = (key,) if isinstance(key, str) else tuple(key)   # 可以给几个别名，任一认到就算（OCR 读错一个词也不怕）
            pos = next((p for k in keys for p in [self.find(k, region=(400, 600, 2250, 800))] if p), None)
            if pos:
                prog = sorted((abs(px - pos[0]), t) for t, px, py in self.items
                              if "进度" in t and 590 <= py <= 670 and abs(px - pos[0]) < 200)
                if prog:
                    m = re.search(r"(\d+)\s*/\s*(\d+)", prog[0][1])
                    if m and int(m.group(1)) >= int(m.group(2)) > 0:
                        return "done", None
                x = pos[0] + 50
                near = sorted(((abs(bx - x), t, bx, by) for t, bx, by in self.items
                               if 1140 <= by <= 1240 and abs(bx - x) < 260 and
                               ("前往" in t or "已完成" in t or "进行中" in t)))
                if near:
                    _, t, bx, by = near[0]
                    return ("go", (bx, by)) if "前往" in t else ("done", None)
                # 活跃度满 400 后，没做的卡片「前往」变成「• 活跃度已满」+ 右边「›」箭头（文字右 206 像素）
                full = [(abs(bx - x), bx, by) for t, bx, by in self.items
                        if "活跃度已满" in t and 1140 <= by <= 1240 and abs(bx - x) < 260]
                if full:
                    _, bx, by = min(full)
                    return "go", (bx + 206, by - 18)
                self.snap("daily_card")
                return None, None
            self.swipe(2050, 800, 1050, 800, 600, wait=1.2)
        self.snap("daily_missing")
        return None, None

    def go_task(self, key) -> str:
        """key 可以是几个别名的元组（日志里只写第一个）。快捷手册日常页点 key 那张卡的「前往」，传送后等到大世界。返回 go / done / missing（日常页没这张卡）/ fail。"""
        name = key if isinstance(key, str) else key[0]
        if not self.open_quick("日常"):
            return "fail"
        kind, pos = self.find_daily(key)
        if kind == "done":
            log(f"  「{name}」今天已完成")
            self.tap(*QUICK_X, wait=2)
            return "done"
        if kind != "go":
            log(f"  日常页没找到「{name}」，读到的卡：{getattr(self, 'daily_seen', [])}（截图已存）")
            return "missing"
        log(f"  「{name}」前往 @{pos}")
        self.tap(*pos, wait=3)
        self.ocr()
        if self.find("是否立即传送"):
            self.tap_text("确认", exact=True, fresh=False, wait=3)
        end = time.time() + 40
        while time.time() < end:
            self.ocr()
            if self.is_world():
                time.sleep(1.5)
                return "go"
            time.sleep(1.5)
        return "fail"

    def talk(self, keys, timeout: float = 15):
        """点大按钮开始对话，点对话框翻页，直到出现 keys 里的某个选项。"""
        self.tap(*BIG, wait=2.5)
        end = time.time() + timeout
        while time.time() < end:
            self.ocr()
            for k in keys:
                pos = self.find(k)
                if pos and pos[0] > 1500:
                    return k, pos
            if self.dialog_open():
                self.tap(*DIALOG, wait=1.5)
            elif self.is_world():
                self.tap(*BIG, wait=2.5)
            else:
                return None, None      # 不是对话（例如直接进了某个界面），交给调用方
        return None, None

    # ---------- 喝茶 ----------
    def step_tea(self) -> None:
        r = self.go_task("咖啡")
        if r == "done":
            return
        if r != "go":
            self.note = "没传送到茶馆"
            return
        k, _ = self.talk(("来杯", "不喝了"))
        opts = sorted(self.find_all("来杯", region=(1500, 600, 2300, 1000)), key=lambda p: p[1])
        if len(opts) < 2:
            self.snap("tea_opts")
            self.note = f"没看到两个饮品选项（{len(opts)} 个）"
            self.to_world()
            return
        log(f"  选第二个饮品 @{opts[1]}")
        self.tap(*opts[1], wait=2)
        for _ in range(15):
            self.ocr()
            if self.is_world():
                break
            if self.confirm_got():
                continue
            if self.find("跳过", region=(1900, 0, 2400, 250)):
                self.tap_text("跳过", fresh=False, wait=2)
            elif self.dialog_open():
                self.tap(*DIALOG, wait=1.5)
            else:
                self.tap(*DIALOG, wait=2)
        self.to_world()

    # ---------- 零食盲盒 ----------
    def step_snack(self) -> None:
        # 卡片全文「进行一次刮刮乐、占卜或抽取饼铺盲盒」：三个词任一认到都算。
        # 10-10 起这张卡有时写成「去报刊亭刮卡签到」，圆按钮去的仍是零食铺（作者实测）→ 加「刮卡」，照旧开盲盒
        r = self.go_task(("刮刮乐", "饼铺盲盒", "占卜", "刮卡"))
        if r == "done":
            return
        if r == "missing":
            self.note = "日常页没找到「刮刮乐」卡（截图已存）"
            return
        if r != "go":
            self.note = "没传送到饼铺"
            return
        self.tap(*BIG, wait=3)
        self.ocr()
        if self.find("今日已领取"):
            log("  今天的盲盒已经领过")
            self.to_world()
            return
        if not (self.find("零食盲盒") or self.find("集卡兑换")):
            self.snap("snack")
            self.note = "没进到零食盲盒"
            self.to_world()
            return
        # 作者：「盲盒随便点就行，你可以固定一个坐标」→ 点第一排中间那盒
        log(f"  点盲盒 @{SNACK_BOX}")
        self.tap(*SNACK_BOX, wait=3)
        opened = False
        for _ in range(12):
            self.ocr()
            if self.find("点击盲盒"):             # 选中后中间出现一个大盒子：「点击盲盒，查看今天的『卡片』吧！」
                log("  点开盲盒")
                self.tap(1200, 750, wait=3)
                opened = True
                continue
            if self.confirm_got():
                continue
            if self.find("分享") and self.find("确定", exact=True, region=(1000, 1150, 1400, 1300)):
                log("  开奖卡片页，点「确定」（不点分享）")
                self.tap_text("确定", exact=True, region=(1000, 1150, 1400, 1300), fresh=False, wait=2.5)
                continue
            if self.is_world():
                break
            if opened and self.find("集卡兑换"):
                break                             # 开完回到了盲盒机页面（底字变成「今日已领取同类型奖励」）
            if self.dialog_open():
                self.tap(*DIALOG, wait=1.5)
            else:
                self.tap(1200, 1400, wait=2)      # 开盒演出 / 卡片展示：点屏幕下方空白处继续
        self.snap("snack_after")
        self.to_world()

    # ---------- 录像店 ----------
    def step_video(self) -> None:
        r = self.go_task("录像店")
        if r == "done":
            return
        if r != "go":
            self.note = "没传送到录像店"
            return
        k, pos = self.talk(("查看经营状况", "暂时不用"))
        if k != "查看经营状况":
            self.snap("video_talk")
            self.note = "对话里没有「查看经营状况」"
            self.to_world()
            return
        self.tap(*pos, wait=3)
        for _ in range(6):
            self.ocr()
            if self.find("昨日账本"):
                log("  关昨日账本")
                self.tap(1828, 273, wait=2)
                continue
            if self.confirm_got():
                continue
            break
        if not self.find("开始营业"):
            self.snap("video_main")
            self.note = "经营页没有「开始营业」（可能已在营业）"
            self.to_world()
            return
        # 宣传员：不是安比就换成安比
        if not self.find("安比", region=(950, 780, 1230, 870)):
            self.tap(1087, 1039, wait=2.5)
            self.ocr()
            a = self.find("安比", exact=True, region=(500, 600, 2300, 800))
            if not a:
                self.snap("promoter")
                self.note = "宣传员列表里没找到安比"
                self.to_world()
                return
            log(f"  宣传员选安比 @{a}")
            self.tap(a[0], 600, wait=1.5)
            self.tap_text("确定", exact=True, region=(1900, 1280, 2300, 1420), wait=2.5)
            self.ocr()
        # 录像带：有空格就「推荐上架」
        if self.tape_count() < 3:
            self.tap(1452, 1039, wait=2.5)
            if not self.tap_text("推荐上架", region=(1300, 1280, 1900, 1420), wait=2.5):
                self.snap("tapes")
                self.note = "录像带页没有「推荐上架」"
                self.to_world()
                return
            self.ocr()
        if not self.find("安比", region=(950, 780, 1230, 870)):
            self.snap("video_check")
            self.note = "宣传员不是安比，没开始营业"
            self.to_world()
            return
        self.tap_text("开始营业", region=(1850, 1270, 2300, 1360), wait=3)
        for _ in range(6):
            self.ocr()
            if self.confirm_got():
                continue
            if self.find("确认", exact=True) and not self.find("开始营业"):
                self.snap("video_confirm")
                self.tap_text("确认", exact=True, fresh=False, wait=2.5)
                continue
            break
        self.snap("video_after")
        self.to_world()

    def tape_count(self) -> int:
        """经营页三格录像带上方的片名（y≈818）有几个；空格时那里没有字。"""
        return sum(1 for t, x, y in self.items if 1300 <= x <= 2250 and 780 <= y <= 860 and len(norm(t)) >= 2)

    # ---------- 实战模拟室 · 赏金 ----------
    def to_bounty(self) -> int | None:
        """快捷手册 → 训练 → 实战模拟室 → 基础材料「前往」→ 选赏金专项。返回当前电量（读不到 None）。"""
        if not self.open_quick("训练"):
            return None
        if not self.tap_text("实战模拟室", exact=True, region=(150, 600, 500, 760), wait=2, fresh=False):
            self.snap("sim_tab")
            return None
        self.ocr()
        # 喝过茶后顶上多一条「咖啡增益」横幅，各行会往下挪，所以按「基础材料」那一行找它下方最近的「前往」
        row = self.find("基础材料", region=(500, 450, 1200, 1300))
        go = sorted(((y - row[1], x, y) for t, x, y in self.items if "前往" in t and 0 < y - row[1] < 220)) if row else []
        go = [(x, y) for _, x, y in go]
        if not go:
            self.snap("basic_mat")
            return None
        self.tap(*go[0], wait=3)
        self.ocr()
        if self.find("是否立即传送"):
            self.tap_text("确认", exact=True, fresh=False, wait=3)
        k, pos = self.wait_any(("赏金专项",), timeout=30)
        if not pos:
            self.snap("bounty_page")
            return None
        self.tap(pos[0] + 110, 1050, wait=1.5)
        self.ocr()
        if not self.bounty_selected():
            self.snap("bounty_select")
            log("  赏金专项没选中")
            return None
        return self.stamina()

    def btn_sv(self, img, cx: int, cy: int, r: int = 60) -> tuple[float, float]:
        hsv = cv2.cvtColor(img[cy - r:cy + r, cx - r:cx + r], cv2.COLOR_BGR2HSV)
        return float(hsv[..., 1].mean()), float(hsv[..., 2].mean())

    def wait_hud(self, timeout: float = 45) -> bool:
        """出战后等普攻键出现再连点（加载约 12 秒 + 出场演出）。10-24：没进战斗就闭眼连点，点进了配队页的邦布头像。
        等不到：画面是配队页 / 邦布出战页 → 截图、返回 False（不打）；别的画面照旧开打（沿用以前的做法）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            img = self.grab()
            if self.match("atk", *ATK, img=img) > 0.6:
                log(f"  进战斗（等了 {int(time.time() - t0)} 秒）")
                return True
            time.sleep(1)
        self.ocr()
        if self.find("邦布出战") or self.find("出战", region=(1950, 1280, 2300, 1420)) or self.find("预备编队"):
            self.snap("not_in_battle")
            log(f"  {int(timeout)} 秒没进战斗，还在配队 / 邦布页，这场不打")
            return False
        log(f"  {int(timeout)} 秒没见到普攻键，照旧开打")
        return True

    @battle("正在战斗（绝区零）")          # 11-02 作者：战斗不能暂停
    def fight(self, limit: float = 300) -> str:
        """作者：技能、大招、连携都要靠普攻多段攒能量/打出重击，所以每轮先连点 COMBO 下打完一整套普攻，
        再看按钮：大招亮了（暗时饱和度≈9、亮度≈47）就放，特殊技变彩色（能量够，饱和度>100；不够时 25–55）就放，否则接着普攻。
        普攻键不见了：认结算页 / 失败页；连携技的样子还没见过，先存帧（/tmp/zzz/fight_*.jpg）。返回 win / fail / timeout / not_started（没进战斗，见 wait_hud）。"""
        t0 = time.time()
        if not self.wait_hud():
            return "not_started"
        gone = 0
        n_ult = n_spec = 0
        while time.time() - t0 < limit:
            for _ in range(COMBO):
                self.shell(f"input tap {ATK[0]} {ATK[1]}")
            img = self.grab()
            if self.match("atk", *ATK, img=img) > 0.6:
                gone = 0
                us, uv = self.btn_sv(img, *ULT)
                if us > 60 or uv > 90:
                    self.shell(f"input tap {ULT[0]} {ULT[1]}")
                    n_ult += 1
                    cv2.imwrite(str(TMP / f"fight_ult_{int(time.time() - t0):03d}.jpg"), img)
                elif self.btn_sv(img, *SPEC)[0] > 100:
                    self.shell(f"input tap {SPEC[0]} {SPEC[1]}")
                    n_spec += 1
                continue
            gone += 1
            if gone % 2 == 1:
                cv2.imwrite(str(TMP / f"fight_{int(time.time() - t0):03d}.jpg"), img)
            self.img = img
            self.items = [(t, x, y) for t, x, y in self.ocr_img(img)]
            if self.find("挑战结果") or self.find("再来一次"):
                log(f"  战斗结束，用时约 {int(time.time() - t0)} 秒（大招 {n_ult} 次、强化特殊技 {n_spec} 次）")
                return "win"
            if self.find("挑战失败") or self.find("战斗失败"):
                self.snap("fight_fail")
                return "fail"
        self.ocr()
        self.snap("fight_timeout")
        return "timeout"

    # ---------- 补电量：只从储蓄电量取差额 ----------
    # 10-18 作者：「可以跟崩坏3差不多，从储存电量里取，如果取完了也不够那就不打」。
    # 10-18 23:57 实机：选关页电量不够点「下一步」→「恢复电量」框（储蓄电量 1252 默认选中、副标题「取出等量储备电量」/ 以太电池 3 / 菲林；
    # 取消 (991,1124)、确认 (1466,1124)）→ 确认 →「快捷使用」框（标题行「储蓄电量 × 1252」、兑换数量默认就是差额 13、确认 (1201,1089)、叉 (1736,407)）。
    # 以太电池、菲林不碰：副标题和标题行都认出是储蓄电量、兑换数量正好是差额才点确认，否则关框。
    def reserve_top_up(self, need: int) -> str:
        """选关页、电量不够时：点「下一步」→ 从储蓄电量取 need 点。返回 ok / short（储蓄电量不够差额，没取）/ fail（画面不对，已关框）。"""
        if not self.tap_text("下一步", region=(1950, 1280, 2300, 1420), wait=2):
            self.snap("reserve_no_next")
            return "fail"
        k, _ = self.wait_any(("恢复电量",), timeout=6, region=(800, 400, 1600, 600))
        if not k:
            self.snap("reserve_no_dialog")
            self._reserve_close()
            return "fail"
        self.ocr()
        if not self.find("取出等量储备电量"):            # 选中的不是储蓄电量（以太电池 / 菲林），不点
            self.snap("reserve_not_selected")
            self._reserve_close()
            return "fail"
        lab = self.find("储蓄电量", region=(700, 820, 1300, 940))
        have = None
        for t, x, y in (self.items if lab else []):
            if abs(x - lab[0]) < 120 and 0 < lab[1] - y < 90 and re.fullmatch(r"\d{1,4}", t.strip()):
                have = int(t.strip())
        if have is None or have < need:
            log(f"  储蓄电量 {have} 不够差额 {need}，不取")
            self._reserve_close()
            return "short"
        if not self.tap_text("确认", exact=True, region=(1300, 1050, 1650, 1200), fresh=False, wait=2):
            self.snap("reserve_no_confirm")
            self._reserve_close()
            return "fail"
        k, _ = self.wait_any(("快捷使用",), timeout=6)
        if not k:
            self.snap("reserve_no_quick")
            self._reserve_close()
            return "fail"
        self.ocr()
        row = self.find("兑换数量")
        num = None
        for t, x, y in (self.items if row else []):
            if abs(y - row[1]) < 30 and x > row[0] + 100 and re.fullmatch(r"\d{1,3}", t.strip()):
                num = int(t.strip())
        if not self.find("储蓄电量", region=(800, 560, 1500, 670)) or num != need:
            self.snap("reserve_amount")
            log(f"  快捷使用框不对（兑换数量 {num}，差额 {need}），不取")
            self._reserve_close()
            return "fail"
        log(f"  从储蓄电量取 {need}（储蓄电量 {have}）")
        if not self.tap_text("确认", exact=True, region=(1000, 1040, 1400, 1140), fresh=False, wait=2.5):
            self.snap("reserve_no_confirm2")
            self._reserve_close()
            return "fail"
        self.ocr()
        self.confirm_got()
        return "ok"

    def _reserve_close(self) -> None:
        """关「快捷使用」/「恢复电量」框：叉号或「取消」，不点确认。"""
        for _ in range(3):
            self.ocr()
            if self.find("快捷使用"):
                self.tap(1736, 407, wait=1.5)
            elif self.find("恢复电量", region=(800, 400, 1600, 600)):
                if not self.tap_text("取消", exact=True, fresh=False, wait=1.5):
                    return
            else:
                return

    def _start_from_select(self) -> bool:
        """选关页 →「下一步」→ 配队页「出战」。补完电量可能已经停在配队页，有「出战」就直接点。"""
        k, _ = self.wait_any(("出战", "下一步"), timeout=6, region=(1950, 1280, 2300, 1420))
        if k == "下一步" and not self.tap_text("下一步", region=(1950, 1280, 2300, 1420), fresh=False, wait=3):
            k = None
        if k is None:
            self.note = "没找到「下一步」"
            return False
        # 10-24 03:12：配队页还在加载（立绘全黑）时点的「出战」没生效，fight() 闭眼连点普攻，正好点到右边邦布头像，
        # 进了「邦布出战」页空点 5 分钟。点完确认「出战」没了才算出发；还在就再点，最多 3 次。
        for i in range(3):
            if not self.tap_text("出战", region=(1950, 1280, 2300, 1420), wait=3):
                if i == 0:
                    self.snap("team")
                    self.note = "配队页没找到「出战」"
                    return False
                return True
            if not self.find("出战", region=(1950, 1280, 2300, 1420), fresh=True):
                return True
            log(f"  点了「出战」还在配队页（第 {i + 1} 次）")
        self.snap("team_stuck")
        self.note = "点了 3 次「出战」还在配队页"
        return False

    def step_battle(self, max_runs: int = 3) -> None:
        sta = self.to_bounty()
        if sta is None:
            self.note = "没进到赏金专项选关页"
            self.to_world()
            return
        done = 0
        on_select = True              # True = 在选关页（开打要「下一步」→「出战」）；False = 在结算页
        log(f"  电量 {sta}")
        while done < max_runs and sta is not None:
            if sta < STAMINA_COST:
                if not on_select:     # 结算页上补不了：先「完成」退出，再回选关页补
                    self.tap_text("完成", exact=True, region=(1800, 1280, 2300, 1420), wait=8)
                    self.to_world()
                    sta = self.to_bounty()
                    on_select = True
                    if sta is None:
                        self.note = f"只打了 {done} 场（补电量时没回到赏金专项选关页）"
                        break
                    continue
                r = self.reserve_top_up(STAMINA_COST - sta)
                if r == "fail":
                    self.note = f"只打了 {done} 场（从储蓄电量补电量没成功；截图已存）"
                if r != "ok":
                    break
                self.ocr()
                sta = self.stamina() or STAMINA_COST
            if on_select:
                if not self._start_from_select():
                    break
                on_select = False
            r = self.fight()
            if r != "win":
                self.note = (f"第 {done + 1} 场没进战斗（还在配队 / 邦布页）" if r == "not_started"
                             else f"第 {done + 1} 场没打赢（{r}）")
                self.to_world()
                return
            done += 1
            self.ocr()
            sta = self.stamina()
            log(f"  第 {done} 场完成，电量剩 {sta}")
            if done < max_runs and sta is not None and sta >= STAMINA_COST:
                self.tap_text("再来一次", region=(1300, 1280, 1800, 1420), wait=5)
            elif done >= max_runs or sta is None:
                self.tap_text("完成", exact=True, region=(1800, 1280, 2300, 1420), wait=8)
        if done < max_runs and sta is not None and sta < STAMINA_COST and not self.note:
            log(f"  电量 {sta} 不够再打一场，储蓄电量也补不上")
            self.note = f"只打了 {done} 场（电量不够）"
        self.to_world()
        log(f"  实战模拟室打了 {done} 场")

    # ---------- 领活跃度奖励 ----------
    NODES = (922, 1281, 1644, 2004)

    def step_claim(self) -> None:
        if not self.open_quick("日常"):
            self.note = "打不开快捷手册"
            return
        got = 0
        for _ in range(6):
            self.ocr()
            red = [x for x in self.NODES if self.red_count(x - 90, 360, x + 10, 420) > 150]
            if not red:
                break
            log(f"  领活跃度 {['100', '200', '300', '400'][self.NODES.index(red[0])]}")
            self.tap(red[0], 430, wait=2.5)
            self.ocr()
            if self.confirm_got():
                got += 1
            else:
                self.snap("claim")
                break
        m = self.find("今日最大活跃度")
        log(f"  领了 {got} 档")
        self.tap(*QUICK_X, wait=2)

    # ---------- 周常：丽都周纪（作者加）----------
    WEEK_COLS = (343, 738, 1130)          # 九宫格三列中心 x
    WEEK_BARS = (620, 908, 1196)          # 三行底条 y（格子中心在底条上方 ~130）

    def week_green(self, x: int, y: int) -> bool:
        """格子底条绿 = 完成可领；没完成、领过的都是蓝底（10-09 实测：绿 4500–4960 像素，蓝/领过 0–30）。"""
        hsv = cv2.cvtColor(self.img[y - 12:y + 12, x - 150:x + 150], cv2.COLOR_BGR2HSV)
        return int(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 50) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 120)).sum()) > 2000

    def week_dot_ready(self):
        """右侧「积分特赏」有可领的档：亮黄绿光圈带「+」（色相 35–48，10-09 实测可领约 2270 像素；
        领过的绿勾和进度线是 50–60，这个范围 0 像素）。列表会自己滚，位置不固定，整列找；返回最上面那个光圈的 y，没有 None。"""
        hsv = cv2.cvtColor(self.img[380:1160, 1968:2018], cv2.COLOR_BGR2HSV)
        m = (hsv[..., 0] >= 35) & (hsv[..., 0] <= 48) & (hsv[..., 1] >= 100) & (hsv[..., 2] >= 200)
        rows = np.nonzero(m.sum(axis=1) >= 3)[0]
        if int(m.sum()) < 300 or not len(rows):
            return None
        seg = rows[rows <= rows[0] + 60]
        return int(380 + seg.mean())

    def step_weekly(self) -> None:
        """作者：「任务界面、丽都周纪左侧9宫格，没完成的是蓝色底，完成的是绿色闪光动效；每个都点完后，右侧进度条也点一下」。
        实测：点绿格本身就领了（底条变蓝写「完成」）；已经是选中状态的格子要点中间「领取」。"""
        if not self.open_quick("日常"):
            self.note = "打不开快捷手册"
            return
        if not self.tap_text("丽都周纪", region=(300, 1100, 900, 1260), fresh=False, wait=3):
            self.snap("weekly_entry")
            self.note = "没找到「丽都周纪」"
            return
        if not self.wait_any(("积分特赏",), timeout=8)[0]:
            self.snap("weekly_page")
            self.note = "没进到丽都周纪"
            return
        got = left = 0
        for by in self.WEEK_BARS:
            for x in self.WEEK_COLS:
                self.ocr()  # 闪光动效，每格都现截
                if not self.week_green(x, by):
                    continue
                log(f"  周纪格子可领 @({x},{by - 130})")
                self.tap(x, by - 130, wait=2)
                self.ocr()
                self.confirm_got()
                if self.week_green(x, by) and self.tap_text("领取", exact=True, region=(1350, 1130, 1800, 1250), wait=2):
                    self.ocr()
                    self.confirm_got()
                self.ocr()
                if self.week_green(x, by):
                    left += 1
                    self.snap("weekly_cell")
                else:
                    got += 1
        log(f"  周纪格子领了 {got} 个")
        n_dot = 0
        for _ in range(3):  # 右侧进度条：点最上面的亮光圈，一下就把能领的档全领了（10-09 实测）
            self.ocr()
            y = self.week_dot_ready()
            if y is None:
                break
            log(f"  积分特赏可领 @(1993,{y})")
            self.tap(1993, y, wait=2.5)
            self.ocr()
            self.confirm_got()
            n_dot += 1
        log(f"  积分特赏点了 {n_dot} 下")
        if left:
            self.note = f"周纪还有 {left} 格是绿的没领掉"
        self.tap(175, 155, wait=2.5)  # 左上返回 → 快捷手册
        self.ocr()
        if self.in_quick():
            self.tap(*QUICK_X, wait=2)

    # ---------- 丽都城募：成长任务（作者：「点进任务右边的丽都城募，成长任务，全部领取，流程放在日常最后」）----------
    CITY = (1964, 116)        # 顶栏「快捷手册」右边的绿色小人图标（10-11 实测 shots/g01–g06）；现在按模板 tpl/city.png 找，这个只留作记录（10-20 新版本顶栏没有它了）
    GROWTH_TABS = ("今日任务", "本周任务", "本期任务")

    def claimable(self) -> int:
        """成长任务列表右边能点的「领取」个数（不算「已领取」「全部领取」；没做完的是「前往」，绝不点）。"""
        return sum(1 for t, x, y in self.items if norm(t) == "领取" and 1950 <= x <= 2300 and 380 <= y <= 1250)

    def open_city(self) -> bool:
        """10-22（3.3）：每个版本第一次进丽都城募是「开启」页：左「成长计划」、右「典藏计划」两个购买计划 + 右下「开启丽都城募」。
        作者定：只点右下「开启丽都城募」（点一次就展开），两个购买计划不碰。返回点没点。"""
        pos = self.find("开启丽都城募", region=(1700, 1250, 2400, 1450))
        if not pos:
            return False
        log(f"  丽都城募要先开启：点「开启丽都城募」@{pos}（两个购买计划不碰）")
        self.tap(*pos, wait=3)
        return True

    def step_growth(self) -> None:
        """丽都城募 →「成长任务」→ 今日 / 本周 / 本期三个页签，有「领取」就点右下「全部领取」再核对。
        只点页签和「全部领取」；「前往」「购买等级」「解锁丽都城募」都不点。每个版本第一次进来的开启页只点「开启丽都城募」（10-22）。"""
        if not self.to_world():
            self.note = "没回到大世界"
            return
        xy = self.find_icon("city")
        if not xy:
            self.snap("city_missing")
            self.note = "顶栏没找到丽都城募图标（截图已存）"
            return
        self.tap(*xy, wait=3)
        got = self.wait_any(("成长任务",), timeout=8, region=(1500, 100, 2000, 220))[0]
        if not got and self.open_city():
            got = self.wait_any(("成长任务",), timeout=8, region=(1500, 100, 2000, 220))[0]
        if not got:
            self.snap("city_fail")
            self.note = "没进到丽都城募"
            self.to_world()
            return
        self.tap_text("成长任务", exact=True, region=(1500, 100, 2000, 220), fresh=False, wait=2.5)
        got, left = 0, []
        for tab in self.GROWTH_TABS:
            if not self.tap_text(tab, exact=True, region=(1400, 260, 2300, 350), wait=2.5):
                left.append(f"「{tab}」页签没找到")
                continue
            self.ocr()
            n = self.claimable()
            if not n:
                continue
            log(f"  {tab}：{n} 个可领，点「全部领取」")
            for _ in range(2):
                self.tap_text("全部领取", exact=True, region=(1850, 1290, 2350, 1410), wait=3)
                for _ in range(3):           # 领完可能弹「获得」提示
                    self.ocr()
                    if not self.confirm_got():
                        break
                if not self.claimable():
                    break
            # 弹了不认识的框（比如升级）会挡住列表、读不到「领取」，不能当成领完了
            if not self.find("全部领取", exact=True, region=(1850, 1290, 2350, 1410)):
                self.snap("growth_popup")
                left.append(f"{tab}领完后页面不认识（截图已存）")
                break
            m = self.claimable()
            got += n - m
            if m:
                self.snap("growth_left")
                left.append(f"{tab}还有 {m} 个没领掉")
        log(f"  成长任务领了 {got} 个")
        if left:
            self.note = "；".join(left)
        self.tap(*RETURN, wait=3)
        self.to_world()


STEPS: list[str] = ["start", "tea", "snack", "video", "battle", "claim", "weekly", "growth"]
NAMES: dict[str, str] = {
    "start": "启动+签到弹窗", "tea": "喝茶(+60电量)", "snack": "零食盲盒",
    "video": "录像店经营(安比+推荐上架)", "battle": "实战模拟室·赏金专项", "claim": "领日常活跃度奖励",
    "weekly": "丽都周纪：周常领取", "growth": "丽都城募：成长任务领取",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STEPS))
    ap.add_argument("--battles", type=int, default=3)
    args = ap.parse_args()
    steps = [s for s in args.only.split(",") if s in STEPS]
    bot = Bot()
    result = {}
    for s in steps:
        log(f"== {NAMES[s]}")
        bot.note = None
        try:
            if s == "battle":
                bot.step_battle(args.battles)
            else:
                getattr(bot, f"step_{s}")()
            result[s] = bot.note or "ok"
        except Exception:
            traceback.print_exc()
            result[s] = bot.note or "error"
            log(f"  {NAMES[s]} 出错，跳过")
            if s == "start":
                break
    log("完成：" + "，".join(f"{NAMES[k]}={v}" for k, v in result.items()))
    return 0 if all(v == "ok" for v in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
