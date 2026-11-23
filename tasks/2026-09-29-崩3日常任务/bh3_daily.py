"""崩坏3 日常/周常一键脚本（幻想引擎调用）。

流程：签到弹窗 → 一键减负(周常有就做, 万象虚境拉满10次, 跳过冒险委托) → 舰团委托
      → 材料活动减负 → 家园(金币/打工/完成远征+一键远征) → 任务一键领取+历练礼盒(每日任务给 60 体力)
      → 冒险委托(周一/四/六开新一轮, 每周一后先激活「后崩坏书 第二章」; 水晶>相转移镜面/高级技能材料>黑白核心>任选;
        体力不够就从储存体力按需取; 能接几个接几个, 满5个再一键减负; 接了就再领一次任务奖励)
      → 商店(后勤终端金币商品全买不刷新 + 虚境商店每周 1 星石时序通行证) → 活动页领取(只进红点) → 左上 bp 战令(作战历练箱周一刷新 + 每日等级奖励) → 邮件一键领取
全部基于 OCR 找字点击；坐标基准 2400x1504。每步失败只记日志并跳过。
用法：bh3_daily.py [--only sign,jianfu,guild,material,home,claim,adventure,shop,activity,bp,mail]
                  [--adv-accept N] [--adv-refresh N]
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
AGG = HERE.parent.parent
sys.path.insert(0, str(AGG / "bh3_auto"))
from lib.adb import Adb, BASE_W, BASE_H  # noqa: E402

OCR_BIN = AGG / "bh3_auto/ocr/ocr"
PKG = "com.miHoYo.enterprise.NGHSoD"
TMP = Path("/tmp/bh3daily")
TMP.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def norm(t: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff0-9/×]", "", t)


class NavigationError(RuntimeError):
    """未确认主界面时停止日常，避免在活动/弹窗上继续点击。"""


class Bot:
    def __init__(self) -> None:
        self.adb = Adb()
        self.w, self.h = BASE_W, BASE_H
        self.img = None
        self.items: list[tuple[str, int, int]] = []

    # ---------- 基础 ----------
    def grab(self):
        """原始 RGBA 截图（不压 PNG，约快 1 秒）；失败回退 PNG。"""
        pause_point()        # 11-02 幻想引擎要暂停就停在截图前（战斗里不停），继续后重新截图
        raw = self.adb._run(["exec-out", "screencap"], timeout=20)
        if len(raw) > 16:
            w, h = np.frombuffer(raw[:8], "<u4")
            hdr = len(raw) - int(w) * int(h) * 4
            if hdr in (12, 16):
                a = np.frombuffer(raw[hdr:], np.uint8).reshape(int(h), int(w), 4)
                return cv2.cvtColor(a, cv2.COLOR_RGBA2BGR)
        png = self.adb.screencap()
        return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)

    def red_dot(self, x1, y1, x2, y2) -> bool:
        """区域内是否有活动红点（橙红/粉红色实心圆）。基准坐标。"""
        sx, sy = self.w / BASE_W, self.h / BASE_H
        roi = self.img[int(y1 * sy):int(y2 * sy), int(x1 * sx):int(x2 * sx)]
        if roi.size == 0:
            return False
        b, g, r = roi[..., 0].astype(int), roi[..., 1].astype(int), roi[..., 2].astype(int)
        mask = (r > 220) & (g < 130) & (b < 140) & (r - g > 120)
        return int(mask.sum()) > 120

    def ocr(self) -> list[tuple[str, int, int]]:
        """截图+OCR，返回 [(文字, x, y)]，坐标已换算到 2400x1504 基准。"""
        self.img = self.grab()
        self.h, self.w = self.img.shape[:2]
        p = TMP / "cur.jpg"
        cv2.imwrite(str(p), self.img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        out = subprocess.run([str(OCR_BIN), str(p)], capture_output=True, text=True, timeout=30).stdout
        sx, sy = BASE_W / self.w, BASE_H / self.h
        res = []
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            cx = (d["x"] + d["w"] / 2) * sx
            cy = (d["y"] + d["h"] / 2) * sy
            res.append((d["text"], int(cx), int(cy)))
        self.items = res
        return res

    def tap(self, x: int, y: int, wait: float = 2.0) -> None:
        rx, ry = Adb.scale_base(x, y, self.w, self.h)
        self.adb.tap(rx, ry, delay_after=0)
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, ms: int = 500, wait: float = 1.5) -> None:
        a = Adb.scale_base(x1, y1, self.w, self.h)
        b = Adb.scale_base(x2, y2, self.w, self.h)
        self.adb.swipe(*a, *b, duration_ms=ms)
        time.sleep(wait)

    def find(self, key: str, exact: bool = False, region=None, fresh: bool = False):
        """找文字；region=(x1,y1,x2,y2) 基准坐标。返回 (x,y) 或 None。"""
        items = self.ocr() if fresh else self.items
        for t, x, y in items:
            n = norm(t)
            ok = (n == key) if exact else (key in n)
            if ok and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
                return x, y
        return None

    def find_all(self, key: str, exact: bool = False, region=None):
        out = []
        for t, x, y in self.items:
            n = norm(t)
            ok = (n == key) if exact else (key in n)
            if ok and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
                out.append((x, y))
        return out

    def tap_text(self, key: str, exact: bool = False, region=None, wait: float = 1.5, fresh: bool = True) -> bool:
        pos = self.find(key, exact, region, fresh)
        if pos:
            log(f"  点「{key}」@{pos}")
            self.tap(*pos, wait=wait)
            return True
        return False

    def wait_text(self, key: str, timeout: float = 12, exact: bool = False, region=None):
        end = time.time() + timeout
        while time.time() < end:
            pos = self.find(key, exact, region, fresh=True)
            if pos:
                return pos
            time.sleep(1)
        return None

    def confirm(self, rounds: int = 3) -> None:
        """连续关掉「确定 / 获得奖励」类对话框。冒险委托刷新类弹窗不点确定（等于继续刷新/花水晶），改点取消。"""
        for _ in range(rounds):
            pos = self.find("确定", exact=True, region=(700, 900, 1800, 1350), fresh=True)
            if not pos:
                return
            if self._danger():
                log("  弹窗在冒险委托列表/刷新提示上，改点取消")
                self.tap_text("取消", exact=True, fresh=False, wait=1.5)
                return
            log(f"  点「确定」@{pos}")
            self.tap(*pos, wait=1.5)

    # 冒险委托列表页/刷新提示：(2300,200) 正好是「刷新」按钮，「确定」= 继续刷新，这些界面上只能取消/返回
    DANGER = ("接取冒险委托", "刷新价格", "免费刷新次数", "还要刷新", "水晶奖励任务")

    def _danger(self) -> bool:
        return any(k in norm(t) for t, _, _ in self.items for k in self.DANGER)

    def stamina(self):
        self.ocr()
        for t, x, y in self.items:
            m = re.search(r"(\d{1,3})/240", t.replace(" ", ""))
            if m and y < 120:
                return int(m.group(1))
        return None

    # ---------- 导航 ----------
    def save_evidence(self, name: str) -> None:
        """保留当前截图和 OCR，供卡住时定位；不额外操作设备。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        p = TMP / f"{name}_{stamp}"
        if self.img is not None:
            cv2.imwrite(str(p.with_suffix('.png')), self.img)
        p.with_suffix('.json').write_text(json.dumps(self.items, ensure_ascii=False, indent=2))
        log(f"  诊断截图/OCR：{p}")

    def _nav_hit(self, name: str, region, threshold: float = 0.9):
        template = cv2.imread(str(HERE / "nav_tpl" / name), cv2.IMREAD_GRAYSCALE)
        if template is None or self.img is None:
            return None
        img = cv2.resize(self.img, (BASE_W, BASE_H))
        x1, y1, x2, y2 = region
        roi = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
            return None
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        if score >= threshold:
            return x1 + loc[0] + template.shape[1] // 2, y1 + loc[1] + template.shape[0] // 2
        return None

    def _nav_match(self, name: str, region, threshold: float = 0.9) -> bool:
        return self._nav_hit(name, region, threshold) is not None

    def _activity_back(self) -> bool:
        # 回忆玩具小屋等活动：左上是纯图标，OCR 不会出现「返回」。
        return bool(self.find("活动剩余时间", region=(300, 0, 1500, 160))
                    or self._nav_match("activity_back.png", (0, 0, 220, 180)))

    def _main_visible(self) -> bool:
        # 海报后方的任务/活动文字有时仍可见，不能只靠这两个字认主界面。
        overlay = self.is_sign_panel() or self._danger() or self._activity_back()
        overlay = overlay or any("空白" in norm(t) and "关闭" in norm(t) for t, _, _ in self.items)
        overlay = overlay or bool(self.find("确定", exact=True) or self.find("快捷购买"))
        overlay = overlay or any(t.strip() in ("X", "x", "×", "✕") and x >= 1300 and y <= 800
                                 for t, x, y in self.items)
        if overlay or self.img is None:
            return False
        img = cv2.resize(self.img, (BASE_W, BASE_H))
        icon = img[150:285, 50:200]
        if float(np.all(icon > 190, axis=2).mean()) < 0.04:  # 弹窗背后的主界面被压暗。
            return False
        return bool(self.find("任务", exact=True, region=(0, 150, 400, 300))
                    and self.find("活动", exact=True, region=(0, 280, 450, 420))
                    and self.find("舰团", exact=True, region=(1450, 1300, 1950, 1504))
                    and self.find("家园", exact=True, region=(1900, 1300, 2400, 1504)))

    def is_main(self) -> bool:
        self.ocr()
        return self._main_visible()

    def to_main(self, rounds: int = 6) -> bool:
        for _ in range(rounds):
            if self.is_main():
                time.sleep(0.6)
                if self.is_main():    # 连续两帧确认，避免签到关闭后的过渡帧。
                    return True
                continue
            if self.close_x():
                continue
            if self.tap_text("主菜单", region=(250, 20, 700, 120), fresh=False, wait=0.5):
                end = time.time() + 8
                while time.time() < end:
                    if self.is_main():
                        time.sleep(0.6)
                        if self.is_main():
                            return True
                continue
            if self._danger():
                log("  在冒险委托列表/刷新提示上，只取消/返回")
                if not (self.tap_text("取消", exact=True, fresh=False, wait=1.5)
                        or self.tap_text("返回", region=(0, 0, 300, 120), fresh=False, wait=3)):
                    self.adb.back()
                    time.sleep(2)
                continue
            if self.find("获得奖励", fresh=False) and self.find("确定", exact=True, fresh=False):
                self.confirm()
                continue
            # 冒险委托板/开放世界：左上只有「返回」没有「主菜单」
            if self.tap_text("返回", region=(0, 0, 300, 120), fresh=False, wait=3):
                continue
            if self.in_chuji():
                log("  出击页 → 点房子图标回主界面")
                self.tap(378, 78, wait=3)
                continue
            if self._activity_back():
                log("  活动左上纯图标 → 退出活动")
                self.tap(100, 80, wait=2)
                continue
            if self.find("退出游戏", fresh=False):
                self.tap_text("取消", exact=True, fresh=False)
                break
            # 未识别的页面只走返回键，不盲点右上角（可能是活动入口/刷新）。
            log("  未识别返回入口，使用系统返回键")
            self.adb.back()
            time.sleep(2)
        if self.is_main():
            time.sleep(0.6)
            if self.is_main():
                return True
        self.save_evidence("navigation_failed")
        return False

    def in_chuji(self) -> bool:
        """出击页（从开放世界退出、或误点签到后的活动海报会来这里）：左上只有「<」和房子图标 (378,78) 没有文字；
        顶部页签「推荐/出击/挑战/活动」不管停在哪个页签都在。"""
        return bool(self.find("挑战", exact=True, region=(1200, 230, 1550, 320), fresh=False)
                    and self.find("出击", exact=True, region=(900, 230, 1200, 320), fresh=False))

    def close_x(self) -> bool:
        """点弹窗/活动海报右上角的叉号（OCR 读成 X/×）。"""
        for t, x, y in self.items:
            if t.strip() in ("X", "x", "×", "✕") and x >= 1300 and y <= 800:
                # 顶栏货币「× 14372」的乘号会被单独读成 X，右边紧跟一串数字的不是叉号（10-07 在委托列表页连点了 10 次）
                if any(0 < x2 - x < 260 and abs(y2 - y) < 30 and len(re.sub(r"\D", "", t2)) >= 3
                       for t2, x2, y2 in self.items):
                    continue
                log(f"  点叉号 @({x}, {y})")
                self.tap(x, y, wait=2)
                return True
        pos = self._nav_hit("dialog_close.png", (1200, 150, 2350, 850), threshold=0.92)
        if pos:
            log(f"  图像识别弹窗叉号 @{pos}")
            self.tap(*pos, wait=2)
            return True
        return False

    def in_task(self) -> bool:
        return bool(self.find("一键减负", region=(0, 1300, 500, 1500), fresh=True))

    def to_task(self) -> bool:
        if not self.to_main():
            log("  回主界面失败")
            return False
        self.tap(150, 225, wait=0.8)
        # 任务界面会停在上次的页签（如作战奖励），先切回左栏「作战任务」
        end = time.time() + 12
        while time.time() < end:
            if self.in_task():
                return True
            if self.tap_text("作战任务", region=(0, 180, 450, 460), fresh=False, wait=0.8):
                continue
        log("  没进入作战任务页（找不到一键减负）")
        return False

    def task_go(self, name: str) -> bool:
        """任务列表按名字找行，点同一格下方的「前往」。列表按「可领 → 进行中 → 已完成」排，前面几步刚做完还没领的
        任务（周一周常重置后更多）会把要找的行挤到很下面（10-07：只滑一次漏找了金币采集、冒险委托），所以一直往下滑到底。"""
        prev = None
        for _ in range(8):
            self.ocr()
            pos = self.find(name)
            if pos:
                x, y = pos
                for t, bx, by in self.items:
                    if norm(t) == "前往" and abs(bx - (x + 505)) < 150 and 60 < by - y < 190:
                        log(f"  任务「{name}」→ 前往 @({bx},{by})")
                        self.tap(bx, by, wait=4)
                        return True
                log(f"  任务「{name}」无「前往」（可能已完成）")
                return False
            names = sorted(norm(t) for t, x, y in self.items if 450 < x and 280 < y < 1350 and len(norm(t)) >= 3)
            if names == prev:  # 滑不动了 = 到底
                break
            prev = names
            self.swipe(1400, 1150, 1400, 450)
        log(f"  任务列表没找到「{name}」")
        return False

    # ---------- 启动 ----------
    def ensure_game(self) -> None:
        focus = self.adb.shell("dumpsys window | grep mCurrentFocus").decode(errors="ignore")
        if PKG not in focus:
            log("启动游戏")
            self.adb.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
            time.sleep(20)
        end = time.time() + 180
        while time.time() < end:
            self.ocr()
            if self.is_sign_panel() or self.is_main():
                return
            if self.find("主菜单", region=(250, 20, 700, 120), fresh=False):
                log("  已在游戏内其他界面，回主菜单")
                self.to_main()
                return
            # 上次被中断停在冒险委托/开放世界/出击页：别在 (1200,1100) 盲点（那是给登录页「点击进入」的），按返回链退出
            if (self._danger() or self.in_chuji() or self._activity_back()
                    or self.find("返回", region=(0, 0, 300, 120), fresh=False)):
                log("  停在冒险委托/开放世界/出击页，先退回主界面")
                self.to_main(rounds=10)
                return
            if self.find("确定", exact=True, fresh=False):
                self.confirm()
                continue
            self.tap(1200, 1100, wait=4)
        log("等待进入游戏超时，继续尝试")

    SIGN_TABS = ("行于曙光", "每日签到", "月卡福利")

    def is_sign_panel(self) -> bool:
        # 左栏页签会随活动增减上下漂移（如「行于曙光」下线后整体上移 140px），
        # 只框 x<450 的左栏、不框 y。
        # 10-09：每天第一次登录会单独弹「月卡福利」（左栏只有这一个页签），以前要两个都在才认，空等 180 秒后被关掉、漏领；
        # 现在任意一个在就算（主界面左栏只有「福利」，不会误认）。
        return any(self.find(t, region=(0, 0, 450, 900)) for t in self.SIGN_TABS)

    # ---------- 月卡（10-18 作者：月卡到期要提示；累计领满 15 次的 500 水晶在页面最右边水晶堆上，满了会发光）----------
    MC_REMIND_DAYS = 3        # 剩几天起在播放记录里提醒续（10-18 定 3 天，用户可改）
    MC_PILE = (2260, 1040)    # 月卡页最右边的水晶堆（10-18 实拍 sign-monthcard-claimed-20261018：「×500」在 (2273,1149) 上方）

    def _monthcard_read(self):
        """月卡页现截一帧：返回 (剩余天数, 累计次数)，读不到的是 None。逐条认，不拼接（「×500」紧挨「7/15天」会拼成 5007）。"""
        self.ocr()
        days = prog = None
        for t, x, y in self.items:
            n = norm(t)
            if 850 <= y <= 1200:
                m = re.search(r"剩余(\d+)天", n)
                p = re.fullmatch(r"(\d+)/15天?", n)
                days = int(m.group(1)) if m else days
                prog = int(p.group(1)) if p else prog
        return days, prog

    def _monthcard_page(self) -> bool:
        """已经在月卡福利页、当天的「领取」处理过：读「剩余N天」，≤ MC_REMIND_DAYS 记提醒；累计「15/15天」就点水晶堆领 500。
        只点水晶堆和「领取」，横幅「30元3100水晶」这类购买入口一律不点。
        返回 False = 这一帧已经不是签到 / 福利面板（10-20 00:34：领完月卡点确定，面板自己关了、紧接着弹活动公告），调用方回主界面后去福利再看。"""
        days, prog = self._monthcard_read()
        if days is None and prog is None and not self.is_sign_panel():
            log("  月卡页已经关了（多半弹了活动公告），回主界面后去福利再看")
            return False
        log(f"  月卡：剩余 {days} 天，累计 {prog}/15")
        if days is None:
            self.save_evidence("monthcard_no_days")
            self._add_note("月卡页没读到剩余天数（可能到期了；截图已存）")
        elif days <= self.MC_REMIND_DAYS:
            self._add_note(f"月卡还剩 {days} 天：记得续")
        if prog == 15:
            log(f"  月卡累计 15/15：点水晶堆领 500 水晶 @{self.MC_PILE}")
            self.tap(*self.MC_PILE, wait=2.5)
            self.confirm()
            if self._monthcard_read()[1] == 15:
                self.save_evidence("monthcard_pile")
                self._add_note("月卡累计 500 水晶没领到（截图已存）")
        return True

    def _monthcard_from_welfare(self) -> None:
        """签到面板里没处理到月卡（没弹签到面板 / 面板里没有月卡页签）→ 主界面左栏「福利」→「月卡福利」再看一次。"""
        self.ocr()
        w = self.find("福利", exact=True, region=(150, 400, 320, 560))
        if not w:
            log("  主界面左栏没认出「福利」，月卡这次没看")
            return
        self.tap(w[0] - 110, w[1] - 5, wait=3)      # 点字不一定开，点左边的礼盒图标（10-18 实机）
        self.ocr()
        pos = self.find("月卡福利", region=(0, 0, 450, 900))
        if not pos:
            if self.find("签到奖励") or self.find("分享邀请", region=(0, 0, 450, 900)):
                self.save_evidence("monthcard_missing")
                self._add_note("福利里没有「月卡福利」页签：月卡可能到期了")
            else:
                log("  没打开福利页，月卡这次没看")
        else:
            self.tap(*pos, wait=2.5)
            if self.tap_text("领取", exact=True, region=(900, 1200, 1900, 1490), wait=2.5):
                log("  月卡福利 已领取")
                self.confirm()
            if not self._monthcard_page():
                self.save_evidence("monthcard_welfare_gone")
                self._add_note("福利里的月卡页没认出来（截图已存）")
        self.to_main(rounds=10)

    # ---------- 1 签到 ----------
    def step_sign(self) -> None:
        self.ocr()
        if not self.is_sign_panel():
            log("  没有签到面板，跳过")
        card_seen = False
        for _ in range(3):  # 月卡福利、每日签到可能一个关掉再弹下一个
            if not self.is_sign_panel():
                break
            # 页签按 OCR 定位，不写死 y；活动页签不存在就跳过
            for tab in self.SIGN_TABS:
                pos = self.find(tab, region=(0, 0, 450, 900))
                if not pos:
                    # 10-18 22:52：领完「每日签到」点了确定，紧接着找「月卡福利」用的是奖励框那一帧的识字，页签其实在 → 重新认一次
                    time.sleep(1)
                    self.ocr()
                    pos = self.find(tab, region=(0, 0, 450, 900))
                if not pos:
                    log(f"  无「{tab}」页签，跳过")
                    continue
                self.tap(*pos, wait=2.5)
                if self.tap_text("领取", exact=True, region=(900, 1200, 1900, 1490), wait=2.5):
                    log(f"  {tab} 已领取")
                    self.confirm()
                if tab == "月卡福利":
                    card_seen = self._monthcard_page() or card_seen
            self.ocr()
            if not self.close_x():
                log("  签到面板未识别到叉号，使用返回键关闭")
                self.adb.back()
                time.sleep(3)
            time.sleep(2)
            self.ocr()
        # 只关已识别的叉号/走返回链，回到主界面是签到步骤的硬条件。
        if not self.to_main(rounds=10):
            self.note = "签到后未确认回到主界面，已停止后续日常"
            raise NavigationError(self.note)
        log("  签到后主界面已连续确认")
        if not card_seen:
            self._monthcard_from_welfare()
            if not self.to_main(rounds=10):
                self.note = "看完月卡未确认回到主界面，已停止后续日常"
                raise NavigationError(self.note)

    # ---------- 2 一键减负（周常：万象虚境/每周答题/模拟作战室/拓境战幕；跳过冒险委托）----------
    def step_jianfu(self) -> None:
        if not self.to_task():
            return
        self.tap(194, 1409, wait=3)
        done: set[str] = set()
        for page in range(2):
            for _ in range(6):
                self.ocr()
                target = None
                # 卡片宽约 520，标题在卡片中上 (y≈455)，按钮在同卡片下部；冒险委托卡片按钮偏左
                titles = sorted((x, norm(t)) for t, x, y in self.items
                                if 420 <= y <= 490 and len(norm(t)) >= 3)
                for bx, by in self.find_all("减负", exact=True, region=(0, 950, 2400, 1070)):
                    cand = [(x, n) for x, n in titles if x - 300 <= bx <= x + 300]
                    if not cand:
                        continue
                    tx, title = min(cand, key=lambda c: abs(c[0] - bx))
                    if any(k in title for k in ("冒险", "委托", "委扌")) or title in done:
                        continue
                    if self.find("已完成", region=(tx - 300, 850, tx + 300, 930), fresh=False):
                        continue
                    target = (title, bx, by)
                    break
                if not target:
                    break
                title, bx, by = target
                done.add(title)
                log(f"  减负「{title}」")
                self.tap(bx, by, wait=2)
                self.ocr()
                if self.find("冒险委托", region=(700, 250, 1700, 360), fresh=False):
                    log("  误开冒险委托弹窗，关闭")
                    self.adb.back()
                    time.sleep(1.5)
                    continue
                if "万象" in title or self.find("减负次数", fresh=False):
                    log("  万象虚境：次数拉满 10")
                    self.swipe(1068, 1073, 1900, 1073, ms=800, wait=1.5)
                    self.ocr()
                if not self.tap_text("确定", exact=True, region=(700, 950, 1900, 1350), wait=3, fresh=False):
                    log(f"  「{title}」没有确定按钮，关闭弹窗")
                    self.tap(1903, 257, wait=2)
                    continue
                self.confirm()
            if page == 0:
                self.swipe(1800, 700, 400, 700, ms=600)
        if not done:
            log("  没有可减负的周常项")
        self.tap_text("关闭", exact=True, region=(900, 1150, 1500, 1350), wait=2)

    # ---------- 3 舰团委托 ----------
    def step_guild(self) -> None:
        if not self.to_task():
            self.note = "舰团委托：未进入任务页"
            return
        if not self.task_go("舰团委托"):
            if not self._task_done("舰团委托"):
                self.note = "舰团委托：未找到前往入口，未确认已完成"
                return
            # 今天提交已做完（任务没有「前往」）也要领奖池：主界面「舰团」→ 大厅底栏「委托回收」（10-13 侦察）
            if self._guild_open_from_hall():
                self._guild_pool()
            else:
                self._add_note("舰团奖池：没从舰团大厅进到回收委托页，没领")
            return
        if not self.wait_text("今日剩余次数", timeout=10):
            log("  没进入舰团委托页")
            self.note = "舰团委托：未进入回收委托页"
            return
        if self.tap_text("申请新委托", wait=3):
            self.tap_text("一键申请", region=(1900, 150, 2400, 330), wait=3)
            self.confirm()
            if not self.find("今日剩余次数", fresh=True):
                self.tap(95, 67, wait=3)
        self._guild_process()
        self._guild_pool()

    def _guild_open_from_hall(self) -> bool:
        if not self.to_main():
            return False
        if not self.tap_text("舰团", exact=True, region=(1450, 1300, 1950, 1504), wait=3):
            return False
        pos = self.wait_text("委托回收", timeout=12, region=(600, 1300, 1100, 1460))
        if not pos:
            return False
        self.tap(*pos, wait=3)
        return bool(self.wait_text("今日剩余次数", timeout=10))

    def _guild_pool(self) -> None:
        """回收委托页右侧「舰团奖池」页签：每天 04:00 按贡献发昨天的奖励，点底部黄色「领取」。
        10-13 作者：「现在只提交素材，不领取昨天的奖励」。随机奖励的勾选是用户自己定的，不动。"""
        tab = self.find("舰团奖池", region=(2050, 350, 2400, 520), fresh=True)
        if not tab:
            if self.note:  # 前面提交已经出错离开了这一页，原因已记
                log("  不在回收委托页，舰团奖池没领")
            else:
                self._add_note("舰团奖池：没找到页签，没领")
            return
        self.tap(*tab, wait=2.5)
        btn = self.find("领取", exact=True, region=(1400, 1250, 2000, 1480), fresh=True)
        if not btn or not self._guild_submit_ready(btn):
            log("  舰团奖池没有可点的「领取」（今天已领或没有奖励）")
            return
        log(f"  领舰团奖池 @{btn}")
        self.tap(*btn, wait=2.5)
        self.confirm()
        again = self.find("领取", exact=True, region=(1400, 1250, 2000, 1480), fresh=True)
        if again and self._guild_submit_ready(again):
            self.save_evidence("guild_pool_after")
            self._add_note("舰团奖池：点了「领取」按钮还是亮的（截图已存）")
        else:
            log("  舰团奖池已领")

    def _guild_process(self) -> None:
        """处理回收委托页，包括最后一次提交后的奖励弹窗。"""
        previous, repeated, purchases = None, 0, 0
        for i in range(80):
            self.ocr()
            left = self._guild_left()
            if left == 0 and not self.find("放入舰团奖励池", exact=True, region=(700, 900, 1900, 1350)):
                log("  今日剩余次数 0，舰团委托完成")
                self.tap_text("取消", exact=True, region=(600, 1000, 1200, 1250), wait=2, fresh=False)
                return
            # 只跟踪次数、材料进度和流程弹窗；整页 OCR 顺序/细微漂移不能掩盖卡住。
            state = (left, tuple(sorted(norm(t) for t, x, y in self.items
                           if (600 <= x <= 1600 and 1250 <= y <= 1480)
                           or (norm(t) in ("提交委托", "放入舰团奖励池", "确定", "快捷购买")))))
            repeated = repeated + 1 if state == previous else 0
            previous = state
            if repeated >= 3:
                self.note = "舰团委托：连续4次页面未推进，已停止重复点击"
                self.save_evidence("guild_stuck")
                self.to_main()
                return
            # 弹窗会压暗背景提交按钮，先处理弹窗，再判断真正的灰色提交。
            hit = False
            for key in ("放入舰团奖励池", "提交委托"):
                if self.tap_text(key, exact=True, region=(700, 900, 1900, 1350), fresh=False, wait=2.5):
                    hit = True
                    break
            if hit:
                continue
            if self.find("确定", exact=True, fresh=False):
                self.confirm()
                continue
            submit = self.find("提交", exact=True, region=(1400, 1300, 2100, 1480))
            if self.find("快捷购买") or (submit and not self._guild_submit_ready(submit)):
                if purchases >= 4:
                    self.note = "舰团委托：补料4次后仍无法提交，停止购买"
                    self.save_evidence("guild_buy_limit")
                    self.to_main()
                    return
                if not self._guild_buy():
                    self.save_evidence("guild_purchase_failed")
                    self.to_main()
                    return
                purchases += 1
                previous, repeated = None, 0
                continue
            if self.tap_text("提交", exact=True, region=(1400, 1300, 2100, 1480), wait=2.5, fresh=False):
                continue
            log("  舰团委托：没有可点的提交目标，结束")
            self.note = "舰团委托：未确认次数用完，且没有可点的提交目标"
            self.save_evidence("guild_no_target")
            return
        self.note = "舰团委托：超过80次状态处理仍未完成"
        self.save_evidence("guild_round_limit")

    def _task_done(self, name: str) -> bool:
        """无前往按钮不等于完成：读同一任务格的 N/N；领过奖励的任务那里改显示绿色「已完成」（10-13 截图）。"""
        pos = self.find(name)
        if not pos:
            return False
        for t, x, y in self.items:
            if not (abs(y - pos[1]) < 65 and 350 < x - pos[0] < 750):
                continue
            if norm(t) == "已完成":
                return True
            m = re.fullmatch(r"(\d+)/(\d+)", norm(t))
            if m:
                return int(m.group(1)) >= int(m.group(2)) > 0
        return False

    def _guild_left(self):
        pos = self.find("今日剩余次数", fresh=False)
        if not pos:
            return None
        for t, x, y in self.items:
            # OCR 实测把 7/8 读成 718；允许 / 漏读或读成 1，且只取次数标签附近。
            m = re.fullmatch(r"([0-8])(?:/|1)?8", norm(t).replace("今日剩余次数", ""))
            if m and abs(x - pos[0]) < 250 and -20 <= y - pos[1] < 180:
                return int(m.group(1))
        return None

    def _guild_submit_ready(self, pos) -> bool:
        img = cv2.resize(self.img, (BASE_W, BASE_H))
        x, y = pos
        roi = img[max(0, y-25):min(BASE_H, y+25), max(0, x-100):min(BASE_W, x+100)]
        b, g, r = cv2.split(roi)
        return float(((r > 160) & (g > 130) & (b < 140)).mean()) > 0.45

    def _guild_material(self):
        """选中材料的白色外框明显大于其他卡片，避免把材料名文字当入口。"""
        img = cv2.resize(self.img, (BASE_W, BASE_H))
        mask = cv2.inRange(img, (210, 210, 210), (255, 255, 255))
        mask[:250, :] = 0
        mask[1050:, :] = 0
        mask[:, 2000:] = 0
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cards = [cv2.boundingRect(c) for c in contours
                 if 260 <= cv2.boundingRect(c)[2] <= 340
                 and 300 <= cv2.boundingRect(c)[3] <= 380]
        if len(cards) == 1:
            x, y, w, h = cards[0]
            return x + w // 2, y + h // 2
        return None

    def _guild_owned(self):
        for t, x, y in self.items:
            m = re.fullmatch(r"拥有(\d+)", norm(t))
            if m and 400 <= x <= 800 and 850 <= y <= 1050:
                return int(m.group(1))
        return None

    def _guild_gold(self):
        values = [int(norm(t)) for t, x, y in self.items
                  if y < 130 and 1500 < x < 2350 and norm(t).isdigit() and len(norm(t)) >= 6]
        return max(values) if values else None

    def _guild_buy(self) -> bool:
        """灰色提交 → 选中材料 → 快捷购买 → 最大99 → 金币购买；检查真实购买结果。"""
        self.ocr()
        if not self.find("快捷购买"):
            material = self._guild_material()
            if not material:
                self.note = "舰团缺料：未识别到选中的材料卡片"
                return False
            log(f"  提交不可用，点选中材料 @{material}")
            self.tap(*material, wait=1.5)
            if not self.wait_text("快捷购买", timeout=6):
                self.note = "舰团缺料：材料入口未出现快捷购买"
                return False
        owned, gold = self._guild_owned(), self._guild_gold()
        if not self.tap_text("最大", region=(1000, 1050, 1350, 1230), fresh=False, wait=0.7):
            self.note = "舰团缺料：快捷购买没有最大按钮"
            return False
        self.ocr()
        quantity = next((int(norm(t)) for t, x, y in self.items
                         if 820 <= x <= 970 and 1080 <= y <= 1200 and norm(t).isdigit()), None)
        buy = self.find("购买", region=(1400, 1050, 1950, 1230))
        price = next((int(m.group(1)) for t, x, y in self.items
                      if 1400 <= x <= 1950 and 1050 <= y <= 1230
                      and (m := re.search(r"(\d+)购买", norm(t)))), None)
        if quantity != 99 or not buy or price is None:
            self.note = f"舰团缺料：未确认99件购买数量/价格（数量={quantity}）"
            return False
        if not self._nav_match("guild_coin.png", (1380, 1070, 1640, 1230)):
            self.note = "舰团缺料：未确认购买货币为金币，停止购买"
            return False
        log(f"  快捷购买数量已确认99，金币价格 {price}，原有 {owned}")
        self.tap(*buy, wait=2)
        for _ in range(4):
            self.ocr()
            now_owned, now_gold = self._guild_owned(), self._guild_gold()
            if ((owned is not None and now_owned is not None and now_owned >= owned + 99)
                    or (gold is not None and now_gold is not None and gold - now_gold == price)):
                log(f"  99件金币购买已确认（拥有 {now_owned}，金币 {now_gold}）")
                if self.find("快捷购买"):
                    if not self.close_x():
                        self.adb.back()
                        time.sleep(1.5)
                self.confirm()
                return True
            if self.find("获得奖励") or self.find("购买成功"):
                self.confirm()
            time.sleep(0.7)
        self.note = "舰团缺料：购买后未确认库存/金币变化，停止重复购买"
        return False

    # ---------- 4 材料活动（先于远征，优先把体力给材料）----------
    def step_material(self) -> None:
        if not self.to_task() or not self.task_go("材料活动"):
            return
        if not self.tap_text("一键减负", region=(1900, 1300, 2400, 1480), wait=3):
            log("  材料页没找到一键减负")
            return
        if self.tap_text("减负", exact=True, region=(900, 1000, 1500, 1150), wait=3):
            self.confirm()

    # ---------- 5 家园：金币 / 打工 / 远征 ----------
    def step_home(self) -> None:
        # 金币采集任务已完成时没有「前往」，改从主界面右下「家园」进，打工/远征照做
        via_task = self.to_task() and self.task_go("金币采集")
        if not via_task:
            if not self.to_main():
                return
            log("  从主界面「家园」进入")
            self.tap(2140, 1428, wait=1)
        if not self.wait_text("家园福利", timeout=15):
            log("  没进入家园")
            return
        # 家园福利上的红色金币气泡亮着才点（10-07：任务列表没认出「金币采集」、改走主界面进家园时漏领过）。
        # 气泡进家园后过一会儿才弹出（10-07 16:25：任务说没领、第一帧却没气泡 → 漏领），最多再看 5 秒
        bubble = self._home_gold_bubble()
        for _ in range(4):
            if bubble:
                break
            time.sleep(1.2)
            self.img = self.grab()
            bubble = self._home_gold_bubble()
        if not bubble and via_task:
            self.save_evidence("home_no_bubble")
            log("  金币采集任务还没完成，但家园里一直没看到金币气泡（截图已存）")
            self.note = "金币采集任务没完成，家园里没看到金币气泡"
        if bubble:
            log("  领家园金币（气泡）")
            self.tap(607, 285, wait=2.5)
            self.confirm()
        else:
            log("  家园金币气泡不在（今天已领）")
        # 打工
        if self.tap_text("打工", exact=True, region=(1650, 1340, 1950, 1480), wait=3):
            if self.tap_text("领取奖励", region=(2000, 1350, 2400, 1490), wait=3):
                self.confirm()
            if self.tap_text("一键打工", region=(1650, 1350, 2100, 1490), wait=3):
                self.tap_text("一键打工", region=(1200, 1100, 1900, 1350), wait=3)
                self.confirm()
            self.tap(93, 65, wait=3)
        # 远征：先逐个「完成远征」领上一轮奖励，再一键远征（体力不够从储存体力取差额，见 _expedition）
        if not self._open_expedition():
            self._add_note("没找到「远征」入口，远征没派")
            return
        if self._expedition(final=False) == "short":
            # 10-15：远征排在「任务领取」前面，每日任务那 60 体力还没领；储存体力也空时先去领，回来再派一次（作者定）
            log("  体力不够、储存体力也空：先去任务页一键领取（每日任务给 60 体力），回来再派一次")
            self.step_claim()
            if self.to_main():
                self.tap(2140, 1428, wait=1)
            if self.wait_text("家园福利", timeout=15) and self._open_expedition():
                self._expedition(final=True)
            else:
                self._add_note("领完任务奖励后没回到远征页，远征没派")

    def _open_expedition(self) -> bool:
        """家园页点「远征」，先逐个「完成远征」领上一轮奖励；没找到入口返回 False。"""
        if not self.tap_text("远征", exact=True, region=(1350, 1340, 1650, 1480), wait=3):
            return False
        self.confirm()
        for _ in range(8):
            if not self.tap_text("完成远征", region=(1550, 300, 2000, 1480), wait=2):
                break
            self.confirm()
        return True

    def _add_note(self, msg: str) -> None:
        log(f"  {msg}")
        self.note = f"{self.note}；{msg}" if getattr(self, "note", None) else msg

    # ---------- 远征：一键远征 ----------
    # 10-09 作者定：优先保证每天 110 体力派满 6 次，体力不够从储存体力取差额，剩下的体力给冒险委托（不够再考虑降到 5 次，作者再定）。
    # 一键远征窗（10-09 实测 shots/ex01_onekey.png）：「将根据上次远征结果再次派遣」，表头 消耗体力 (1195,484) / 远征次数 (1689,484)，
    # 数值 110 @(1195,647)、6 @(1689,646)；按钮 取消派遣 (859,1249) / 远征派遣 (1534,1247)。
    # 体力不够点远征派遣 → 游戏弹「体力补充」（10-09 21:27 实机：没派出去，汇总还记了 ok）。
    def _exp_left(self):
        """远征页顶上「今日远征可用体力 480/480」→ (剩余, 总数)；读不到 None（一键远征窗会挡住它）。"""
        for t, x, y in self.items:
            m = re.search(r"(\d{1,3})/(\d{3})", t.replace(" ", ""))
            if m and 1000 <= x <= 1500 and 180 <= y <= 300:
                return int(m.group(1)), int(m.group(2))
        return None

    def _exp_dialog(self):
        """一键远征窗里的 (消耗体力, 远征次数)；不是这个窗或读不到消耗返回 None。"""
        if not self.find("消耗体力", region=(1000, 420, 1400, 540)):
            return None
        cost = times = None
        for t, x, y in self.items:
            s = t.replace(" ", "")
            if s.isdigit() and 580 <= y <= 720:
                if 1050 <= x <= 1350:
                    cost = int(s)
                elif 1550 <= x <= 1850:
                    times = int(s)
        return (cost, times) if cost else None

    def _exp_open(self):
        """点「一键远征」，返回 (消耗, 次数)；窗没认出来就关掉返回 None。"""
        if not self.tap_text("一键远征", region=(1900, 1330, 2400, 1480), wait=2.5):
            return None
        self.ocr()
        info = self._exp_dialog()
        if not info:
            self.save_evidence("exp_dialog")
            if not self.tap_text("取消派遣", fresh=False, region=(600, 1150, 1150, 1330), wait=2):
                self.close_x()
        return info

    def _expedition(self, final: bool = True) -> str | None:
        """一键远征。体力不够且储存体力取不到时返回 "short"（final=False 时不记未完成，交给 step_home 先领任务奖励再派）。"""
        self.ocr()
        left0 = self._exp_left()
        if left0 and left0[0] < left0[1]:
            log(f"  今日远征可用体力 {left0[0]}/{left0[1]}：今天已派过，不再派")
            return
        info = self._exp_open()
        if not info:
            self._add_note("一键远征窗没认出来，远征没派（截图已存）")
            return
        cost, times = info
        stamina = self.stamina()
        log(f"  一键远征：消耗体力 {cost}，次数 {times}，现有体力 {stamina}，今日可用 {left0}")
        self.tap_text("远征派遣", fresh=False, region=(1250, 1150, 1850, 1330), wait=2.5)
        if self.wait_text("体力补充", timeout=4, region=(900, 250, 1500, 400)):
            need = cost - (stamina or 0)
            if need <= 0:  # 读到的体力够、游戏却说不够 = 读错了，和冒险委托一样按 0 算
                need = cost
            log(f"  体力不够（现有 {stamina}，要 {cost}），从储存体力取 {need}")
            got = self._take_in_dialog(need)
            self.ocr()
            if self.find("体力补充"):
                self.close_x()
            if got <= 0:
                self._exp_cancel()
                if final:
                    self._add_note(f"远征体力不够（现有 {stamina}，要 {cost}），储存体力没取到，没派")
                else:
                    log(f"  远征体力不够（现有 {stamina}，要 {cost}），储存体力也没取到")
                return "short"
            # 取完再派：窗还开着就直接点；关了先核对今天没派出去（防重复派），再重开
            self.ocr()
            if not self.find("远征派遣", region=(1250, 1150, 1850, 1330)):
                left = self._exp_left()
                if left and left[0] < left[1]:
                    log(f"  取完体力游戏已经派出去了（今日可用 {left[0]}/{left[1]}）")
                    return
                if not self._exp_open():
                    self._add_note(f"取了储存体力 {got} 后没能再打开一键远征，没派")
                    return
            self.tap_text("远征派遣", region=(1250, 1150, 1850, 1330), wait=2.5)
            if self.wait_text("体力补充", timeout=4, region=(900, 250, 1500, 400)):
                self.close_x()
                self._exp_cancel()
                self._add_note(f"取了储存体力 {got} 还不够 {cost}，远征没派")
                return
        self.confirm()
        self.ocr()
        left = self._exp_left()
        if left0 and left and left[0] < left0[0]:
            log(f"  远征已派出：今日可用 {left0[0]} → {left[0]}")
            return
        self.save_evidence("exp_after")
        self._add_note(f"点了远征派遣，今日远征可用体力 {left0} → {left}，没确认派出去（截图已存）")

    def _exp_cancel(self) -> None:
        """一键远征窗还开着就点「取消派遣」。"""
        self.ocr()
        self.tap_text("取消派遣", fresh=False, region=(600, 1150, 1150, 1330), wait=2)

    def _home_gold_bubble(self) -> bool:
        """家园页左上「家园福利」机器顶上的金币气泡（≈607,285）。两种样子都算有金币可领：
        红底白图标（10-07 实测 9055 个深红像素）、白底橙图标（10-09 实测 2663 个橙像素，当时「金币采集」任务没完成）；领完两种都是 0。"""
        y0, y1 = int(220 * self.h / BASE_H), int(360 * self.h / BASE_H)
        x0, x1 = int(540 * self.w / BASE_W), int(680 * self.w / BASE_W)
        roi = self.img[y0:y1, x0:x1]
        b, g, r = (roi[..., i].astype(int) for i in range(3))
        red = int(((r > 140) & (g < 80) & (b < 70) & (r - g > 90)).sum())
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        orange = int(((hsv[..., 0] >= 8) & (hsv[..., 0] <= 25) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 120)).sum())
        return red > 2000 or orange > 1500

    # ---------- 6 任务一键领取 + 历练礼盒 ----------
    def step_claim(self) -> None:
        if not self.to_task():
            self.note = getattr(self, "note", None) or "没进入任务页、任务奖励没领"      # 11-21：以前悄悄算 ok
            return
        if self.tap_text("一键领取", region=(1950, 150, 2400, 300), wait=3):
            self.confirm()
        log("  点历练值礼盒")
        self.tap(960, 1425, wait=3)
        self.confirm()
        self._levelup_popup()

    # ---------- 每周分享（周常，10-15 作者要求）----------
    def step_share(self) -> None:
        """任务页「每周分享」→ 前往 → 福利「分享邀请·分享精选内容」→ 点第一张内容 → 预览框下面一排分享平台点「微博」。
        设备没装微博：游戏里打开「微博分享」网页（登录页，什么都不填），点左上「关闭」回来就算分享（10-15 实测 0/1 → 1/1）。
        最后那个蓝色图标（推断复制链接）不算。要是跳到了别的 App（以后装了微博），按 Home 再把游戏拉回来（作者说的切前后台）。
        奖励不在这里领，留给后面「任务领取+礼盒」一键领取。
        10-16：卡片位置随状态变、完成后「前往」按钮还在，做没做用 _share_done 往下找那一格读；
        10-15 分享完马上回任务页读记了「没算上」，其实算上了 → 没读到就等 3 秒重进任务页再读，最多读 3 次。"""
        if not self.to_task():
            return
        if self._share_done():
            log("  每周分享本周已做完")
            return
        if not self.task_go("每周分享"):
            self.note = "没找到每周分享的前往"
            return
        if not self.wait_text("分享精选内容", timeout=10):
            self.note = "没进分享页"
            return
        if not self._share_from_page():
            self.note = "分享页没点成（见日志）"
            return
        for k in range(3):
            if k:
                log(f"  还没读到每周分享完成，等 3 秒重进任务页再读（第 {k} 次）")
                time.sleep(3)
            if not self.to_task():
                self.note = "分享后回不到任务页"
                return
            if self._share_done():
                log("  每周分享 1/1，奖励留给「任务领取+礼盒」")
                return
        self.save_evidence("share_not_counted")
        self.note = "每周分享没算上（已留截图）"

    def _share_done(self) -> bool:
        """任务页从当前屏往下找「每周分享」那一格，读到 1/1 或绿色「已完成」返回 True，找不到返回 False。
        10-16：卡片位置随完成状态变（10-13 在第一格、10-16 在第三排），完成后「前往」按钮还在，不能拿「前往」判断。"""
        prev = None
        for _ in range(8):
            self.ocr()
            if self.find("每周分享"):
                return self._task_done("每周分享")
            names = sorted(norm(t) for t, x, y in self.items if 450 < x and 280 < y < 1350 and len(norm(t)) >= 3)
            if names == prev:          # 滑不动了 = 到底
                return False
            prev = names
            self.swipe(1400, 1150, 1400, 450)
        return False

    def _share_from_page(self) -> bool:
        """已在「分享邀请」页：切到「分享精选内容」子页签 → 点第一张内容 → 认预览框下面的微博图标点它 → 关掉微博网页 / 切回游戏。
        没进精选内容或没认出图标返回 False。10-15：从福利进来默认停在「我的邀请码」子页签，两个子页签的字一直都在，
        所以要点页签后等只有精选内容里才有的「当期内容」。"""
        self.tap(1900, 100, wait=1.5)                # 「分享精选内容」子页签
        if not self.wait_text("当期内容", timeout=8):
            log("  没切到「分享精选内容」（找不到「当期内容」）")
            return False
        self.tap(737, 446, wait=3)                   # 第一张内容 → 预览框 + 分享平台一排
        self.img = self.grab()
        pos = self._nav_hit("share_weibo.png", (250, 1100, 1650, 1450), threshold=0.8)
        if not pos:
            log("  预览框下面没认出微博图标，关掉预览框")
            self.tap(2165, 218, wait=2)
            return False
        log(f"  点微博分享 @{pos}")
        self.tap(*pos, wait=3)
        focus = self.adb.shell("dumpsys window | grep mCurrentFocus").decode(errors="ignore")
        if "com.sina.weibo.sdk.web" in focus:
            log("  游戏里打开了微博分享网页（没装微博），什么都不填，点「关闭」")
            self.tap(50, 56, wait=3)
        else:
            log(("  跳到了别的 App" if PKG not in focus else "  点完还在游戏里") + "，模拟切前后台：Home 再切回游戏")
            self.adb.shell("input keyevent KEYCODE_HOME")
            time.sleep(3)
            self.adb.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
            time.sleep(6)
        return True

    def _levelup_popup(self) -> bool:
        """bp 整 10 级升级弹窗（黄底白字「领取奖励」）：领历练值礼盒 / 作战历练箱后、进 bp 页时都可能弹（作者）。
        点掉再走 _bp_dialogs 收后续确定框；返回是否弹过。"""
        self.ocr()
        if not self.find("领取奖励", region=(600, 900, 1800, 1500), fresh=False):
            return False
        log("  bp 升级奖励弹窗，先领掉")
        self.tap_text("领取奖励", region=(600, 900, 1800, 1500), fresh=False, wait=2.5)
        self._bp_dialogs()
        return True

    # ---------- 8 左上 bp 战令：作战历练箱(每周一刷新) + 每日等级奖励 ----------
    def step_bp(self) -> None:
        if not self.to_main():
            self.note = getattr(self, "note", None) or "没回到主界面、战令没看"          # 11-21：以前悄悄算 ok
            return
        # 红点 = 历练箱可领 或 等级奖励可领（每天任务做完升级后就会亮）
        if not self.red_dot(700, 10, 800, 90):
            log("  bp 无红点，跳过")
            return
        self.tap(720, 65, wait=0.8)
        # bp 页停在上次的页签；「作战任务」页签展开时「作战奖励」会下移到 y≈666
        if not self.wait_text("先锋凭证", timeout=6):
            # 10-09：bp 升级（40 级）弹窗盖住页面，黄底白字「领取奖励」→ 先领掉（_bp_dialogs 本来就认这个按钮）
            self._levelup_popup()
            if not self.wait_text("先锋凭证", timeout=4):
                self.tap_text("作战奖励", region=(0, 330, 450, 800), wait=2)
                if not self.wait_text("先锋凭证", timeout=8):
                    self.save_evidence("bp_not_in")
                    log("  没进入作战奖励页（截图已存）")
                    self.note = "没进入作战奖励页，等级奖励没领"
                    return
        # 历练箱只在可领时出现在右下，领过就消失；找不到就跳过（别点兜底坐标，那里是推广图）
        pos = self.find("作战历练箱", region=(1900, 1100, 2400, 1350), fresh=False)
        if pos:
            log(f"  点作战历练箱 @({pos[0] - 180}, {pos[1]})")
            self.tap(pos[0] - 180, pos[1], wait=2.5)
            # 11-21：箱子有开箱动画，奖励窗晚一拍才弹（11-16 没等到就去判等级奖励，被它挡着记成「无可领」）
            if not self._bp_wait_dialog():
                log("  点了作战历练箱但没等到奖励弹窗")
            self._bp_dialogs()
            self.ocr()
            if self.find("作战历练箱", region=(1900, 1100, 2400, 1350), fresh=False):
                log("  点完作战历练箱还在（截图已存）")
                self.save_evidence("bp_box_left")
                self.note = "作战历练箱点了没领到"
        else:
            log("  无作战历练箱（本周已领）")
        # 「作战奖励」页签红点 = 还有等级奖励（历练箱已在上面领掉）；光晕判定作补充（10-07 实跑只靠光晕漏判过）
        self.ocr()
        dot, glow = self._bp_tab_dot(), self._bp_level_glow()
        if dot is None:                 # 没读到页签（多半还有弹窗挡着）：再收一次弹窗重读
            self._bp_dialogs()
            self.ocr()
            dot = self._bp_tab_dot()
        if not (dot or glow):
            if dot is None:
                log("  没读到「作战奖励」页签，等级奖励领没领没核对（截图已存）")
                self.save_evidence("bp_tab_unread")
                self.note = self.note or "战令等级奖励领没领没核对（没读到页签）"
            else:
                log("  等级奖励无可领")
            return
        # 11-21：100 级以后等级格全打了勾，每升一级的奖励在最右「100+级」那一列的箱子里（发光、写着 已领/总数），点一下全领。
        # 以前只会点第一列：11-11 起那里是已领的 95 级格子，天天记「可领、点第一列领取」其实没领（作者发现）。
        plus = self._bp_plus_col()
        for act in (("first", "plus") if (glow or not plus) else ("plus", "first")):
            if act == "plus":
                if not plus:
                    continue
                log(f"  「100+级」箱子可领（页签红点 {dot}），点箱子 @({plus[0]}, 1040)")
                self.tap(plus[0], 1040, wait=2.5)
                self._bp_wait_dialog()
            else:
                log(f"  等级奖励可领（页签红点 {dot}，发光 {glow}），点第一列领取")
                self.tap(763, 624, wait=2.5)
            self._bp_dialogs()
            self.ocr()
            dot, plus = self._bp_tab_dot(), self._bp_plus_col()
            if dot is False:
                break
        if dot:
            log("  领完页签还有红点（截图已存）")
            self.save_evidence("bp_level_left")
            self.note = self.note or "战令等级奖励没领完（页签还有红点）"
        elif dot is None:
            log("  领完没读到「作战奖励」页签，领没领没核对（截图已存）")
            self.save_evidence("bp_tab_unread")
            self.note = self.note or "战令等级奖励领没领没核对（没读到页签）"

    def _bp_tab_dot(self):
        """左栏「作战奖励」页签右上角的红点；页签位置按 OCR 找（作战任务展开时会下移）。
        返回 True / False；没读到页签（被弹窗挡着）返回 None，别当成「没红点」（11-21）。"""
        pos = self.find("作战奖励", region=(0, 250, 450, 800), fresh=False)
        if not pos:
            return None
        x, y = pos
        return self.red_dot(x + 40, y - 70, x + 150, y + 20)

    def _bp_plus_col(self):
        """作战奖励页最右那一列的表头是不是「100+级」（100 级以后才是；之前这一列是下一个整十级的预览）。
        返回表头坐标或 None。norm 会把加号去掉、和旁边「100级」那一列分不开，所以按原文认。"""
        for t, x, y in self.items:
            if 1650 <= x <= 1900 and 380 <= y <= 500 and re.search(r"100\s*[+＋十]", t):
                return x, y
        return None

    def _bp_wait_dialog(self, timeout: float = 8) -> bool:
        """点完箱子等奖励弹窗出来（最多 timeout 秒）。认到「确定」「领取奖励」或「获得奖励」标题就算出来了。"""
        end = time.time() + timeout
        while True:
            self.ocr()
            if (self.find("确定", exact=True, region=(700, 900, 1800, 1350), fresh=False)
                    or self.find("领取奖励", region=(900, 1280, 1500, 1420), fresh=False)
                    or self.find("获得奖励", region=(900, 300, 1500, 450), fresh=False)):
                return True
            if time.time() >= end:
                return False
            time.sleep(0.8)

    def _bp_dialogs(self) -> None:
        """确定 → 升级提示「领取奖励」→ 一键领取「确定」。一键领取弹窗左边是「购买凭证」(花钱)，只精确点「确定」。
        历练箱领完弹的是没有「确定」的叉号奖励窗（10-07 实跑：它盖住格子，等级奖励判不出），用叉号关掉。"""
        quiet = 0
        for _ in range(10):
            self.ocr()
            if (self.tap_text("确定", exact=True, region=(700, 900, 1800, 1350), fresh=False, wait=2)
                    or self.tap_text("领取奖励", region=(900, 1280, 1500, 1420), fresh=False, wait=2.5)
                    or self.close_x()):
                quiet = 0
                continue
            quiet += 1
            if quiet >= 2:              # 11-21：连着两次都没认到才算收完（下一个弹窗常晚一拍才出来）
                break
            time.sleep(1.2)

    def _bp_level_glow(self, frames: int = 8) -> bool:
        """作战奖励页第一列等级格是否发光（可领）。网格总从最低未领等级排起，只看第一列先锋行（免费行）。
        判据：卡片左右外侧 ~10px 的淡紫(B>235)占比。可领光晕呼吸闪烁，亮帧 1.0、暗帧 0.0 交替；
        稀有物品自带的装饰光恒定，先锋行实测最高 0.64。所以要同时见到亮帧(≥0.8)和暗帧(≤0.3)才算可领。"""
        cx = 763
        bright = dark = False
        for _ in range(frames):
            img = self.grab()
            if img.shape[:2] != (BASE_H, BASE_W):
                img = cv2.resize(img, (BASE_W, BASE_H))
            fr = []
            for xa, xb in ((cx - 78, cx - 69), (cx + 69, cx + 78)):
                s = img[585:670, xa:xb].reshape(-1, 3).astype(int)
                fr.append(((s[:, 0] > 235) & (s[:, 1] > 110) & (s[:, 2] > 105)).mean())
            bright |= min(fr) >= 0.8
            dark |= max(fr) <= 0.3
            if bright and dark:
                return True
            time.sleep(0.3)
        return False

    # ---------- 9 邮件：一键领取附件（没附件只弹「暂时没有可以领取的附件」，无害）----------
    def step_mail(self) -> None:
        if not self.to_main():
            return
        self.tap(185, 605, wait=1)  # 点图标；点「邮件」两个字没反应
        btn = self.wait_text("一键领取", timeout=10, exact=True, region=(1900, 1330, 2400, 1490))
        if not btn:
            log("  没进入邮件页")
            return
        # 同排有「快捷删除」、上方有「删除」，只精确点「一键领取」
        log(f"  点「一键领取」@{btn}")
        self.tap(*btn, wait=1.2)
        if self.find("暂时没有可以领取", fresh=True):
            log("  没有可领取的附件")
        else:
            self.confirm()
        self.to_main()

    # ---------- 冒险委托：进开放世界 → 走到委托板 ----------
    # 出生点和镜头每次逐像素一致（2026-10-03 实测三次），所以用固定动作走到板前，最后靠 OCR「查看委托」收尾
    JOY = (265, 1250)                                   # 摇杆中心
    ADV_CARDS = ((40, 800), (820, 1580), (1600, 2360))  # 委托列表 3 张卡的 x 区间
    ADV_TPL = HERE / "adv_tpl"                          # crystal_*=水晶(1档) t2_*=相转移镜面/高级技能材料(2档) core_*=黑白核心(3档)
    adv_max_accept = 5
    adv_max_refresh = 3  # 10-11 作者定：每个空位最多刷 3 次（10-10 第 1 个空位把 15 次免费刷新用光，后面 4 个空位没得刷）
    ADV_MAP = "后崩坏书 第二章"   # 作者定：每周一 4 点后第一次「开启」先激活这张图（一周一张权限卡）
    ADV_COST = 40                # Lv75–80 轮每张 40 体力（10-03 实测），卡面消耗读不到时按这个算

    def hold(self, dx: int, dy: int, sec: float) -> None:
        """按住摇杆偏移 (dx, dy) 持续 sec 秒；设备端 sleep 计时（input swipe 推杆幅度线性爬升，不可重复）。"""
        jx, jy = self.JOY
        x, y = Adb.scale_base(jx, jy, self.w, self.h)
        mx, my = Adb.scale_base(jx + dx // 2, jy + dy // 2, self.w, self.h)
        ex, ey = Adb.scale_base(jx + dx, jy + dy, self.w, self.h)
        self.adb.shell(f"input motionevent DOWN {x} {y}; input motionevent MOVE {mx} {my}; "
                       f"input motionevent MOVE {ex} {ey}; sleep {sec}; input motionevent UP {ex} {ey}",
                       timeout=sec + 15)
        time.sleep(0.5)

    def adv_enter_world(self) -> bool:
        """任务页「冒险委托→前往」直达开放世界；周常做满没有「前往」时走 出击→开放世界（同一出生点）。"""
        if not (self.to_task() and self.task_go("冒险委托")):
            if not self.to_main():
                return False
            log("  从 出击→开放世界 进入")
            self.tap(2205, 746, wait=3)
            self.tap_text("出击", exact=True, region=(900, 220, 1200, 330), wait=2)
            self.tap(200, 700, wait=1)
        if not self.wait_text("折叠", timeout=40, exact=True, region=(2200, 80, 2400, 170)):
            self._adv_snap("enter_world")
            log("  没进入开放世界（截图已存 /tmp/bh3daily）")
            return False
        time.sleep(2)  # 等出生镜头稳定
        return True

    def _sign_x(self):
        """车库招牌「AUTO-REPARATUR」的 x（出生镜头 ≈1729，右拖 300 后 ≈710）；OCR 拉丁字母不能走 norm。"""
        xs = [x for t, x, y in self.items if "AUTO" in t.upper() and y < 400]
        return xs[0] if xs else None

    def _wait_still(self, timeout: float = 8) -> None:
        """等画面稳定：4 点刷新后首次进世界加载慢，画面还在动时拖镜头会被吞掉（2026-10-03 04:11 实测失败）。"""
        prev, end = None, time.time() + timeout
        while time.time() < end:
            g = cv2.cvtColor(cv2.resize(self.grab(), (480, 300)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None and float(np.abs(g - prev).mean()) < 3:
                return
            prev = g
            time.sleep(0.5)

    def adv_go_board(self) -> bool:
        """出生点 → 委托板前 → 点「查看委托」→ 委托界面。
        镜头拖 3 次都没转就不往前走（10-13：照走走到了地图屏前），置 adv_cam_stuck 让 step_adventure 退出重进一次。"""
        self._wait_still()
        self.adv_cam_stuck = False
        for _ in range(3):
            self.ocr()
            self.swipe(1500, 800, 1800, 800, ms=300, wait=1.2)  # 镜头右转，委托板转到正前方
            self.ocr()
            sx = self._sign_x()
            if sx is None or sx < 1500:  # 整个没转才重拖，转了一部分不重拖（避免转过头）
                break
            log(f"  镜头没转过去（招牌 x={sx}），等 2 秒再拖")
            time.sleep(2)
        else:
            self.adv_cam_stuck = True
            self._adv_snap("cam_stuck")
            log("  镜头拖了 3 次都没转过去，不往前走（截图已存 /tmp/bh3daily）")
            return False
        self.hold(0, -180, 1.0)                            # 前推
        self.hold(90, -160, 0.6)                           # 右前推 → 绿色菱形下方
        fails = 0
        for i in range(8):
            pos = self.wait_text("看委托", timeout=2.5, region=(1200, 650, 2000, 880))
            if pos:
                log(f"  点「查看委托」@{pos}（修正 {i} 次）")
                self.tap(*pos, wait=1)
                if self.wait_text("冒险委托", timeout=10, region=(0, 120, 1000, 260)):
                    return True
                # 10-07 两次：人站在板前、按钮没了、界面也没开（点击没生效）→ 再找按钮再点，最多补 2 次
                fails += 1
                self._adv_snap("board_open_fail")
                if fails > 2:
                    log("  点了 3 次查看委托都没进委托界面（截图已存）")
                    return False
                log("  点了查看委托但没进委托界面（截图已存），再找一次按钮")
                continue
            if self.find("查看背包", fresh=False):
                log("  站到箱子旁了，右移")
                self.hold(160, 0, 0.3)
            else:
                log("  没出现查看委托，右前方微调")
                self.hold(80, -140, 0.25)
        self._adv_snap("board_fail")
        log("  没走到委托板（截图已存 /tmp/bh3daily）")
        return False

    def adv_exit(self) -> bool:
        """委托列表/委托板/开放世界/出击页 → 主界面。to_main 在这些界面只取消/返回/点房子图标，不盲点。"""
        try:
            ok = self.to_main(rounds=10)
        except Exception:
            traceback.print_exc()
            ok = False
        if not ok:
            log("  冒险委托退出后没回到主界面")
            self.note = (getattr(self, "note", None) or "") + "；退出后没回到主界面"
        return ok

    # ---------- 10 冒险委托：周一/四/六开新一轮；能接几个接几个，满 5 个再一键减负 ----------
    def step_adventure(self) -> None:
        self.adv_accepted = 0
        self.adv_store_empty = False  # 取过一次储存体力取不到 → 本次后面的空位不再刷新
        self.adv_cam_stuck = False
        try:
            ok = self.adv_enter_world() and self.adv_go_board()
            if not ok and self.adv_cam_stuck:  # 10-13 作者定：镜头拖不动 → 退出开放世界重进一次，还不行报未完成
                log("  退出开放世界，重进一次再拖镜头")
                ok = self.adv_exit() and self.adv_enter_world() and self.adv_go_board()
            if ok:
                self._adv_round()
            else:
                self.note = "镜头拖不动，没走到委托板" if self.adv_cam_stuck else "未进入委托板"
        finally:
            self.adv_exit()  # 中途出异常也要离开委托界面，否则后续步骤会在列表页上乱点
        if self.adv_accepted:
            log(f"  本次接了 {self.adv_accepted} 个委托，再领一次任务奖励")
            self.step_claim()

    def _adv_board_ocr(self) -> list:
        """委托板识字，返回「选择」坐标。卡片有出场动画：没认出「选择」、也不是 5 格全接满 / 要开新一轮时，等 1.5 秒再认，最多认 3 次。
        10-18 00:15：进委托界面第一次没认出「选择」就收工，紧接着再认就有空位，那天 5 个空位一个没接。"""
        for i in range(3):
            if i:
                time.sleep(1.5)
            self.ocr()
            slots = self.find_all("选择", exact=True, region=(0, 300, 2000, 1300))
            if slots or self.find("可以开启新的冒险委托", fresh=False) or \
                    len(self.find_all("委托人", region=(0, 300, 2400, 1400))) >= 5:
                return slots
        return []

    def _adv_round(self) -> None:
        """接委托，做完或停下都直接返回；没接满的原因写进 self.note（幻想引擎据此显示未完成）。"""
        self._adv_board_ocr()
        if self.find("可以开启新的冒险委托", fresh=False) and not self._adv_open():
            return
        while self.adv_accepted < self.adv_max_accept:
            slots = self._adv_board_ocr()
            if not slots:
                break
            # 「选择」一次往往只认得出一个；空位数按「一共 5 格 − 已接的(卡上有「委托人」)」来算
            taken = len(self.find_all("委托人", region=(0, 300, 2400, 1400)))
            empty = max(5 - taken, len(slots))
            log(f"  空位 {empty} 个（已接 {taken}/5），点「选择」@{slots[0]}")
            self.tap(*slots[0], wait=1)
            if not self.wait_text("接取冒险委托", timeout=8, region=(0, 130, 600, 260)):
                log("  没进入委托列表")
                self.note = f"没进入委托列表（本次已接 {self.adv_accepted} 个）"
                return
            # 体力按单张补：挑好卡、接取前不够再取「这张消耗 − 现有体力」（10-07 作者：不同委托消耗不一样，别按总数取）。
            # 储存体力已经取不到时，体力不够最便宜的一张就不刷新、不接，免得白用免费刷新。
            stamina = self._adv_stamina()
            costs = [c for c in (self._adv_cost(i, None) for i in range(3)) if c]
            if self.adv_store_empty and (stamina is None or stamina < min(costs or [self.ADV_COST])):
                log(f"  体力不够（{stamina}）且储存体力已取不到，不刷新、不接")
                self.tap_text("返回", region=(0, 0, 300, 120), wait=2)
                self.note = f"体力不够：本次接了 {self.adv_accepted} 个，还空 {empty} 个"
                return
            if not self._adv_pick_and_accept():
                self.note = self.note or f"接取中止（本次已接 {self.adv_accepted} 个，原因见日志）"
                return
            self.adv_accepted += 1
        if self._adv_board_ocr():
            log(f"  本次接取 {self.adv_accepted} 个，未满 5 个，暂不减负")
            if not self.note and self.adv_accepted < self.adv_max_accept:
                self.note = f"未接满：本次接了 {self.adv_accepted} 个"
        elif self.find("委托人", fresh=False):
            self._adv_jianfu()
        else:
            log("  委托板没有可减负的委托")

    def _adv_open(self) -> bool:
        """委托板显示「可以开启新的冒险委托」：点「开启」。每周一 4 点后第一次开启先进选地图页，激活 ADV_MAP 后
        回到委托板，再点一次「开启」。返回 True = 委托板上有「选择」空位了。"""
        for _ in range(2):
            pos = self.find("开启", exact=True, region=(900, 650, 1500, 820), fresh=False)
            if not pos:
                log("  委托板显示可开启，但没找到「开启」按钮")
                self.note = "委托板显示可开启，但没找到「开启」按钮"
                return False
            log(f"  点「开启」@{pos}")
            self.tap(*pos, wait=2)
            if self.wait_text("选择", timeout=8, exact=True, region=(0, 300, 2000, 1300)):
                return True
            if self.find("权限激活", fresh=False) or self.find("激活需要", fresh=False):
                if not self._adv_activate_map():
                    return False
                if not self.wait_text("开启", timeout=10, exact=True, region=(900, 650, 1500, 820)):
                    self._adv_snap("after_activate")
                    log("  激活地图后没回到委托板（截图已存）")
                    self.note = "激活地图后没回到委托板"
                    return False
                continue
            if self.find("可以开启新的冒险委托", fresh=True):
                log("  「开启」不可用（本轮已开过，等下个周一/四/六 4 点后）")
            else:
                self._adv_snap("open_unknown")
                log("  开启后出现未知界面，停止（截图已存）")
                self.note = "点「开启」后出现未知界面，没接委托（截图已存 /tmp/bh3daily）"
            return False
        self.note = "激活地图后「开启」仍没出现空位"
        return False

    def _adv_activate_map(self) -> bool:
        """选地图页（「每周重置获取1个权限卡，只能选项一张地图进行权限激活」）：点 ADV_MAP 卡下方「激活需要×1」
        → 确认框「是否确定激活<地图>地图本周权限？」→「确认并前往」（10-07 实测：人不传送，直接回委托板）。"""
        self.ocr()
        key = norm(self.ADV_MAP)
        title = next(((x, y) for t, x, y in self.items if norm(t) == key and y < 400), None)
        btn = title and next(((x, y) for t, x, y in self.items
                              if "激活需要" in t and abs(x - title[0]) < 300 and y > 1000), None)
        card = next((t for t, x, y in self.items if "权限申请书" in t), "")
        m = re.search(r"(\d+)/\d+", card)
        if not btn or (m and int(m.group(1)) == 0):
            self._adv_snap("map_page")
            log(f"  选地图页上找不到「{self.ADV_MAP}」的激活按钮，或权限卡已用完（{card}），停止（截图已存）")
            self.note = f"选地图页没能激活「{self.ADV_MAP}」，没接委托"
            return False
        log(f"  本周还没激活地图，点「{self.ADV_MAP}」的「激活需要×1」@{btn}")
        self.tap(*btn, wait=2.5)
        self.ocr()
        ask = next((t for t, x, y in self.items if "是否确定激活" in t), "")
        go = self.find("确认并前往", fresh=False)
        if key not in norm(ask) or not go:
            self._adv_snap("map_confirm")
            log(f"  激活确认框不对（{ask!r}），点取消并停止（截图已存）")
            self.tap_text("取消", exact=True, fresh=False, wait=2)
            self.note = f"激活「{self.ADV_MAP}」的确认框没认出来，没接委托"
            return False
        log(f"  点「确认并前往」@{go}")
        self.tap(*go, wait=3)
        return True

    # ---------- 储存体力：「体力补充」弹窗 →「取出体力」页签，按需取 ----------
    # 体力不够时在委托列表点「接取」，游戏会直接弹出体力补充（10-07 作者实测）；主界面体力「+」也是同一个弹窗。
    # 滑条 0–300：0 在 x≈806，每 1 点 ≈4.09px（10-07 实测刻度 60/120/180/240 = 1051/1295/1541/1785，滑块 278 在 1946）；
    # 直接拖到目标读数会多 0~2 点，微调一次少走约 1 点
    STAMINA_X0, STAMINA_PX, STAMINA_Y = 806, 4.09, 656

    def _stamina_pick(self):
        """「立刻领取 ×N」的 N = 滑条当前要取的体力；读不到 None。"""
        for t, x, y in self.items:
            m = re.search(r"[×xX](\d+)", t.replace(" ", ""))
            if m and 950 <= x <= 1300 and 880 <= y <= 990:
                return int(m.group(1))
        return None

    def _adv_top_up(self, need: int, idx: int) -> int:
        """委托列表上体力不够接第 idx 张：点它的「接取」，游戏会弹体力补充（10-07 作者实测），取 need 点。
        要是先弹的是接取确认框，点确定（体力不够游戏接不了，会接着弹补充）。返回取到的点数；-1 = 其实够、已接上。"""
        x0, x1 = self.ADV_CARDS[idx]
        log(f"  体力不够，点第 {idx + 1} 张「接取」叫出体力补充（要取 {need}）")
        self.tap((x0 + x1) // 2, 1284, wait=2)
        self.ocr()
        if self.find("是否消耗", fresh=False) and not self.find("体力补充", fresh=False):
            self.tap_text("确定", exact=True, region=(1300, 930, 1700, 1070), wait=2, fresh=False)
            if self.wait_text("玩法说明", timeout=3, region=(0, 1200, 500, 1400)):
                log("  体力其实够，已接上")
                return -1
        if not self.wait_text("体力补充", timeout=6, region=(900, 250, 1500, 400)):
            self._adv_snap("no_top_up")
            log("  没弹出体力补充（截图已存）")
            return 0
        got = self._take_in_dialog(need)
        self.ocr()
        if self.find("是否消耗", fresh=False):  # 取完要是直接跳到接取确认，先取消，回列表按正常流程挑卡
            self.tap_text("取消", exact=True, region=(700, 930, 1100, 1070), wait=2, fresh=False)
        return got

    def _take_in_dialog(self, need: int) -> int:
        """已打开的体力补充弹窗：只在「取出体力」页签操作（同窗「体力兑换」页签要花水晶，绝不点）；
        拖滑条到 need（存的不够就全取），核对「立刻领取 ×N」后点「取出体力」。返回取的点数，0 = 没取。"""
        self.ocr()
        if not self.find("请选择需要取出的体力值", fresh=False):
            self.save_evidence("stamina_tab")
            log("  体力补充没停在「取出体力」页签，不操作，关掉（截图已存）")
            self.close_x()
            return 0
        stored = self._stamina_pick()  # 打开时滑条默认拉满 = 全部储存
        if not stored:
            log(f"  没有可取的储存体力（读到 {stored}）")
            self.close_x()
            return 0
        target, cur = min(need, stored), stored
        x = lambda v: int(self.STAMINA_X0 + v * self.STAMINA_PX)
        for _ in range(5):
            if target <= cur <= target + 1:
                break
            # 10-07 实测拖动总比想拖的少走约 1 点（且读数从不低于目标），所以往目标方向多拖 1 点再读
            dest = target - 1 if cur > target else target + 1
            self.swipe(x(cur), self.STAMINA_Y, x(dest), self.STAMINA_Y, ms=700, wait=1)
            self.ocr()
            cur = self._stamina_pick()
            if cur is None:
                break
        if cur is None or not target <= cur <= target + 3:
            self.save_evidence("stamina_slider")
            log(f"  滑条没拖到 {target}（读到 {cur}），不取，关掉（截图已存）")
            self.close_x()
            return 0
        if not self.tap_text("取出体力", exact=True, region=(1100, 1050, 1750, 1200), wait=2.5):
            log("  没找到「取出体力」按钮，关掉")
            self.close_x()
            return 0
        log(f"  取出储存体力 {cur}（储存 {stored}）")
        for _ in range(3):  # 可能有「获得物品」类确定框；弹窗还开着就关掉
            self.ocr()
            if self.find("体力补充", fresh=False):
                self.close_x()
            elif not self.find("是否消耗", fresh=False) and self.tap_text(
                    "确定", exact=True, region=(700, 900, 1800, 1350), fresh=False, wait=2):
                pass
            else:
                break
        return cur

    def _adv_snap(self, tag: str) -> None:
        cv2.imwrite(str(TMP / f"adv_{tag}_{time.strftime('%m%d-%H%M%S')}.png"), self.img)

    def _adv_free_refresh(self):
        """列表页右上：「免费刷新次数：N」→ N；显示「刷新价格」(要花水晶) → 0；读不到 → None。"""
        for t, x, y in self.items:
            if 1500 <= x <= 2150 and 150 <= y <= 270:
                if "刷新价格" in t:
                    return 0
                m = re.search(r"免费刷新次数\D*(\d+)", t.replace(" ", ""))
                if m:
                    return int(m.group(1))
        return None

    def _adv_stamina(self):
        for t, x, y in self.items:
            if 1450 <= x <= 1720 and 20 <= y <= 110:
                m = re.search(r"(\d+)", t)
                if m:
                    return int(m.group(1))
        return None

    def _adv_stamina_retry(self):
        """读体力；读不出重截一次再读（10-09 体力正好 0 时读出过 None）。"""
        stamina = self._adv_stamina()
        if stamina is None:
            self.ocr()
            stamina = self._adv_stamina()
        return stamina

    def _adv_cost(self, i: int, default=-1):
        x0, x1 = self.ADV_CARDS[i]
        for t, x, y in self.items:
            m = re.search(r"消耗\D*(\d+)", t.replace(" ", ""))
            if m and x0 <= x <= x1 and 380 <= y <= 500:
                return int(m.group(1))
        return self.ADV_COST if default == -1 else default

    def _adv_scan(self) -> list[tuple[int, str, float]]:
        """每张卡 (最高优先档, 模板名, 水晶模板最高分)；奖励行 7 项只露 5 项，左滑看完再滑回。档位越小越优先，4=无。"""
        tpls = []
        for p in sorted(self.ADV_TPL.glob("*.png")):
            tier = 1 if p.name.startswith("crystal") else 2 if p.name.startswith("t2_") else 3
            tpls.append((tier, p.stem, cv2.imread(str(p))))
        imgs = [self.grab()]
        for x0, x1 in self.ADV_CARDS:
            self.swipe(x1 - 120, 1150, x0 + 140, 1150, ms=400, wait=0.3)
        time.sleep(0.6)
        imgs.append(self.grab())
        for x0, x1 in self.ADV_CARDS:
            self.swipe(x0 + 140, 1150, x1 - 120, 1150, ms=400, wait=0.3)
        out = []
        for x0, x1 in self.ADV_CARDS:
            best, crystal = (4, ""), 0.0
            for img in imgs:
                if img.shape[:2] != (BASE_H, BASE_W):
                    img = cv2.resize(img, (BASE_W, BASE_H))
                row = img[1080:1230, x0:x1]
                for tier, name, t in tpls:
                    # 水晶样本取自减负框（图标约为列表卡的 1.2 倍），多尺度匹配
                    for s in ((0.7, 0.8, 0.9, 1.0, 1.1) if tier == 1 else (1.0,)):
                        tt = t if s == 1.0 else cv2.resize(t, None, fx=s, fy=s)
                        sc = float(cv2.matchTemplate(row, tt, cv2.TM_CCOEFF_NORMED).max())
                        if tier == 1:
                            crystal = max(crystal, sc)
                        if sc >= 0.8 and tier < best[0]:
                            best = (tier, name)
            out.append((best[0], best[1], round(crystal, 2)))
        return out

    def _adv_pick_and_accept(self) -> bool:
        """列表页：有水晶就接；否则免费刷新找水晶（刷到 0 为止，绝不花水晶刷新）；刷完按 2/3 档挑，都没有接第 1 张。"""
        crystal_hint = False
        refreshed = 0
        while True:
            self.ocr()
            free = self._adv_free_refresh()
            scan = self._adv_scan()
            tier, idx = min((s[0], i) for i, s in enumerate(scan))
            log(f"  卡片优先档 {[s[0] for s in scan]}（{[s[1] for s in scan]}，水晶分 {[s[2] for s in scan]}），"
                f"免费刷新剩 {free}，本空位已刷 {refreshed}")
            if tier == 1 or refreshed >= self.adv_max_refresh or not free or self.find("刷新价格", fresh=False):
                break
            log("  点「刷新」（免费）")
            self.tap(2237, 210, wait=1.5)
            refreshed += 1
            self.ocr()
            if self.find("水晶奖励", fresh=False) or self.find("还要刷新", fresh=False):
                log("  弹窗：委托中有水晶奖励任务 → 取消刷新")
                if not self.tap_text("取消", exact=True, wait=1.5, fresh=False):
                    self._adv_snap("crystal_popup")
                    log("  水晶提示框取消失败，停止（截图已存）")
                    return False
                crystal_hint = True  # 刷新已取消，卡片没变，沿用上面的扫描结果
                break
            if self.find("取消", exact=True, fresh=False):
                self._adv_snap("refresh_dialog")
                log("  刷新后出现未知弹窗，取消并停止（截图已存）")
                self.tap_text("取消", exact=True, wait=1.5, fresh=False)
                return False
        if crystal_hint and tier != 1:
            # 游戏保证 3 张里有水晶任务：取水晶分明显最高的那张（比第二名高 0.15），分不出来就留给手动
            ranked = sorted(((s[2], i) for i, s in enumerate(scan)), reverse=True)
            if ranked[0][0] >= 0.5 and ranked[0][0] - ranked[1][0] >= 0.15:
                tier, idx = 1, ranked[0][1]
                log(f"  按水晶分选第 {idx + 1} 张（{ranked[0][0]} vs {ranked[1][0]}）")
            else:
                self._adv_snap("crystal_unknown")
                log("  游戏提示有水晶任务，但认不出是哪张，留给手动（截图已存）")
                return False
        cost, stamina = self._adv_cost(idx), self._adv_stamina_retry()
        why = scan[idx][1] or ("水晶(按分)" if tier == 1 else "任选")
        log(f"  选第 {idx + 1} 张（档 {tier} {why}），消耗 {cost}，体力 {stamina}")
        if stamina is None:
            # 重读还读不出就按 0 走取体力：其实够的话点接取游戏直接接上（_adv_top_up 返回 -1），不会多取
            self._adv_snap("stamina_unread")
            log("  体力读不出，按 0 处理（截图已存）")
            stamina = 0
        if stamina < cost and not self.adv_store_empty:
            got = self._adv_top_up(cost - stamina, idx)
            if got == -1:
                return True
            self.ocr()
            stamina = self._adv_stamina_retry()
            if stamina is None or stamina < cost:
                self.adv_store_empty = True
        if stamina is None or stamina < cost:
            log(f"  体力不够（{stamina}，这张要 {cost}），停止接取")
            self.tap_text("返回", region=(0, 0, 300, 120), wait=2)
            self.note = f"体力不够：本次接了 {self.adv_accepted} 个"
            return False
        x0, x1 = self.ADV_CARDS[idx]
        self.tap((x0 + x1) // 2, 1284, wait=1.5)
        if self.find("体力补充", fresh=True):
            log("  点接取弹出了体力补充（体力读数不对？），关掉并停止")
            self.close_x()
            return False
        if not self.wait_text("是否消耗", timeout=6, region=(700, 600, 1700, 800)):
            self._adv_snap("accept_no_dialog")
            log("  点接取后没出现确认框，停止（截图已存）")
            return False
        self.tap_text("确定", exact=True, region=(1300, 930, 1700, 1070), wait=2, fresh=False)
        # 列表页标题「接取冒险委托」也含「冒险委托」，用委托板独有的「玩法说明」判断
        if not self.wait_text("玩法说明", timeout=8, region=(0, 1200, 500, 1400)):
            log("  接取后没回到委托板")
            return False
        return True

    def _adv_jianfu(self) -> None:
        """满 5 个：「一键减负」→ 默认全选（消耗减负券，不额外耗体力）→ 确定 → 获得奖励 → 确定。"""
        if not self.tap_text("一键减负", region=(1950, 1150, 2400, 1280), wait=2):
            log("  没找到一键减负")
            return
        if not self.wait_text("全选", timeout=6, region=(600, 880, 900, 1020)):
            log("  没出现减负框")
            return
        tk = next((t for t, x, y in self.items if "/" in t and 1000 <= x <= 1450 and 900 <= y <= 1000), "?")
        log(f"  减负券 {tk}")
        self.tap_text("确定", exact=True, region=(900, 1080, 1500, 1200), wait=2.5, fresh=False)
        if self.wait_text("获得奖励", timeout=8, region=(900, 250, 1500, 420)):
            log("  一键减负完成，领取奖励")
            self.tap_text("确定", exact=True, region=(900, 1080, 1500, 1220), wait=2, fresh=False)
        self.confirm()

    # ---------- 商店：后勤终端金币商品全买（不刷新）+ 虚境商店每周 1 星石的时序通行证 ----------
    # 10-07 作者：「补给-商店-日常商店-后勤终端，把金币能买的东西都买了，不刷新；虚境商店（每周一次，有红点），
    # 把红点的免费（1星石）的时序通行证买了，其他的没有了」。右上「可重置次数 / 水晶×100」是花水晶刷新，绝不点。
    SHOP_COLS = (658, 1038, 1418, 1798, 2178)   # 商品格中心 x；价格按钮 y 两行 826 / 1444，格子本体在按钮上方 ~266
    SHOP_ROWS = (826, 1444)

    def _top_number(self, x1: int, x2: int):
        """顶栏 x1–x2 之间的货币数（≥3 位数字）；读不到 None。"""
        for t, x, y in self.items:
            n = re.sub(r"\D", "", t)
            if x1 <= x <= x2 and y < 120 and len(n) >= 3:
                return int(n)
        return None

    def _shop_gold_cards(self, skip) -> list:
        """后勤终端里价格按钮是金币图标、没售罄的格子 [(x, 按钮y)]（金币模板实测 0.96–1.0，水晶/紫币 ≤0.41）。"""
        out = []
        for by in self.SHOP_ROWS:
            for cx in self.SHOP_COLS:
                if (cx, by) in skip:
                    continue
                if any("售罄" in t and abs(x - cx) < 150 and abs(y - by) < 60 for t, x, y in self.items):
                    continue
                if self._shop_icon_gold(cx, by):
                    out.append((cx, by))
        return out

    def _shop_icon_gold(self, cx: int, by: int) -> bool:
        tpl = cv2.imread(str(HERE / "nav_tpl" / "shop_gold.png"))
        img = cv2.resize(self.img, (BASE_W, BASE_H)) if self.img.shape[:2] != (BASE_H, BASE_W) else self.img
        roi = img[max(by - 60, 0):by + 60, max(cx - 200, 0):cx + 40]
        if tpl is None or roi.shape[0] < tpl.shape[0] or roi.shape[1] < tpl.shape[1]:
            return False
        return float(cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED).max()) >= 0.8

    def _shop_dialog_buy(self, want_currency: int, what: str, need_name: str = "", max_price: int | None = None) -> bool:
        """已点开的「购买可获得」弹窗：右上显示的就是这件商品用的货币余额，等于 want_currency 才点「购买」；
        否则关掉。碎片已溢出的「是否继续购买」照点确定（作者：金币能买的都买）。"""
        self.ocr()
        if not self.find("购买可获得", fresh=False):
            log(f"  {what}：没弹出购买框")
            return False
        top = self._top_number(1900, 2400)
        btn = next(((x, y) for t, x, y in self.items if "购买" in t and x > 1300 and 1050 < y < 1220), None)
        name = next((t for t, x, y in self.items if 1050 < x < 1800 and 420 < y < 520), "?")
        if top != want_currency or not btn:
            log(f"  {what}「{name}」右上货币 {top} ≠ {want_currency}（或没找到购买按钮），不买，关掉")
            self.close_x()
            return False
        price = re.sub(r"\D", "", next((t for t, x, y in self.items if "购买" in t and x > 1300 and 1050 < y < 1220), ""))
        if need_name not in norm(name) or (max_price is not None and price and int(price) > max_price):
            log(f"  {what}：弹窗是「{name}」、标价 {price or '?'}，和要买的不符，不买，关掉")
            self.close_x()
            return False
        log(f"  买「{name}」@{btn}")
        self.tap(*btn, wait=2.5)
        self.ocr()
        if self.find("已经溢出", fresh=False):
            log("  提示已溢出，继续购买")
            self.tap_text("确定", exact=True, region=(1200, 950, 1850, 1100), fresh=False, wait=2.5)
            self.ocr()
        if self.find("购买可获得", fresh=False):  # 买完弹窗还在 = 没买成
            log("  买完购买框还在，关掉")
            self.close_x()
            return False
        return True

    def _shop_tab(self, name: str, parent: str | None = None) -> bool:
        """左栏切页签；子页签（后勤终端/虚境商店）没露出来时先点父页签（日常商店/玩法商店）展开。"""
        for _ in range(3):
            self.ocr()
            pos = self.find(name, region=(150, 150, 450, 1480), fresh=False)
            if pos:
                self.tap(*pos, wait=2.5)
                return True
            if parent and not self.tap_text(parent, region=(150, 150, 450, 1480), fresh=False, wait=2):
                break
        log(f"  没找到商店页签「{name}」")
        return False

    def step_shop(self) -> None:
        if not self.to_main():
            return
        self.ocr()
        crystal = self._top_number(2000, 2300)  # 主界面顶栏水晶数，买东西时的货币绝不能是它
        self.tap(2170, 1040, wait=3)  # 补给
        pos = self.wait_text("前往商店", timeout=8, region=(200, 1050, 800, 1180))
        if pos:
            self.tap(*pos, wait=4)
        else:
            log("  没找到「前往商店」")
            self.note = "没进商店"
            return
        try:
            self._shop_logistics()
            self._shop_void_pass(crystal)
        finally:
            self.to_main()

    def _shop_logistics(self) -> None:
        if not (self._shop_tab("后勤终端", "日常商店") and self.wait_text("刷新倒计时", timeout=6)):
            self.note = "没进后勤终端"
            return
        bought, skip = 0, set()
        for k in range(12):
            self.ocr()
            gold = self._top_number(1600, 1900)
            cards = self._shop_gold_cards(skip)
            if k == 0:
                self.save_evidence("shop_logistics")  # 10-07 只买到 1 件，留图核对有没有漏认
                log(f"  后勤终端认出 {len(cards)} 件金币标价、没售罄的商品（截图已存 /tmp/bh3daily）")
            if not cards or gold is None:
                break
            cx, by = cards[0]
            skip.add((cx, by))
            self.tap(cx, by - 266, wait=2.5)
            if self._shop_dialog_buy(gold, "后勤终端"):
                bought += 1
        log(f"  后勤终端买了 {bought} 件金币商品（不刷新）")

    def _shop_void_pass(self, crystal) -> None:
        if not self._shop_tab("虚境商店", "玩法商店"):
            self.note = "没进虚境商店"
            return
        self.tap_text("通用兑换", region=(450, 150, 900, 290), wait=2)
        # 每周一次：标价 1 的「时序通行证」那一格（旁边还有 80 / 42水晶 的通行证，不买）。
        # 切页签后画面可能还在刷（03:16 实跑第一帧没读全），读 3 帧，认出「标价 1」或「已售罄」才算数
        card = sold = None
        for _ in range(3):
            self.ocr()
            for t, x, y in self.items:
                if "时序通行证" not in norm(t) or not 250 < y < 1400:
                    continue
                price = next((p for p, px, py in self.items
                              if re.fullmatch(r"\d+", p.strip()) and abs(px - x - 38) < 90 and 100 < py - y < 200), None)
                if price == "1":
                    card = (x, y)
                elif any("售罄" in t2 and abs(x2 - x) < 150 and 0 < y2 - y < 220 for t2, x2, y2 in self.items):
                    sold = True
            if card or sold:
                break
            time.sleep(1)
        if not card:
            # 买过的格子会排到列表最后（10-07 实测），所以这一屏看不到标价 1 的那格 = 本周已买
            log("  时序通行证本周已买（已售罄）" if sold else "  这一屏没有标价 1 的时序通行证（本周已买过会排到最后），不买")
            return
        self.tap(card[0], card[1] - 110, wait=2.5)
        self.ocr()
        top = self._top_number(1900, 2400)
        if crystal is not None and top == crystal:
            log(f"  时序通行证弹窗右上是水晶（{top}），不买，关掉")
            self.close_x()
            return
        if top is None:
            log("  时序通行证弹窗读不到货币，不买，关掉")
            self.close_x()
            return
        if self._shop_dialog_buy(top, "虚境商店", need_name="时序通行证", max_price=1):
            log("  虚境商店买了 1 星石的时序通行证")

    # ---------- 7 活动页：只点「领取」----------
    def step_activity(self) -> None:
        if not self.to_main():
            return
        self.tap(185, 352, wait=4)
        if not self.wait_text("主菜单", timeout=10):
            return
        # 只进左栏带红点的活动（红点在标签右上角 x≈380-445）
        seen: set[str] = set()
        for scroll in range(3):
            for _ in range(10):
                self.ocr()
                tabs = [(norm(t), x, y) for t, x, y in self.items
                        if x < 450 and 150 < y < 1500 and len(norm(t)) >= 3 and norm(t) not in seen]
                hit = None
                for name, x, y in tabs:
                    if self.red_dot(370, y - 110, 450, y + 20):
                        hit = (name, x, y)
                        break
                if not hit:
                    break
                name, x, y = hit
                seen.add(name)
                log(f"  活动「{name}」有红点")
                self.tap(x, y, wait=2)
                self._claim_page(name)
            self.swipe(250, 1300, 250, 500, ms=600)
        self.to_main()

    def _claim_page(self, name: str) -> None:
        for _ in range(8):
            self.ocr()
            btns = self.find_all("领取", exact=True, region=(1900, 300, 2400, 1480))
            if not btns:
                return
            log(f"  活动「{name}」领取 @{btns[0]}")
            self.tap(*btns[0], wait=2.5)
            self.confirm()


# 任务领取挪到冒险委托前（10-07 作者：每日任务给的 60 体力用在委托上）
STEPS = ["sign", "jianfu", "guild", "material", "home", "share", "claim", "adventure", "shop", "activity", "bp", "mail"]
NAMES = {"sign": "签到弹窗", "jianfu": "一键减负(周常)", "guild": "舰团委托", "material": "材料活动",
         "home": "家园金币/打工/远征", "share": "每周分享", "adventure": "冒险委托", "claim": "任务领取+礼盒", "shop": "商店(后勤终端+虚境)",
         "activity": "活动领取",
         "bp": "bp战令(历练箱+等级奖励)", "mail": "邮件一键领取"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STEPS))
    ap.add_argument("--adv-accept", type=int, default=5, help="冒险委托本次最多接取几个（测试用）")
    ap.add_argument("--adv-refresh", type=int, default=3,
                    help="冒险委托每个空位最多点几次免费刷新（测试用；还受屏幕上剩余免费次数限制，绝不花水晶刷新）")
    args = ap.parse_args()
    steps = [s for s in args.only.split(",") if s in STEPS]
    bot = Bot()
    bot.adv_max_accept, bot.adv_max_refresh = args.adv_accept, args.adv_refresh
    bot.ensure_game()
    if "sign" not in steps and not bot.to_main():
        log("开跑前未确认主界面，停止日常")
        return 1
    result = {}
    for s in steps:
        log(f"== {NAMES[s]}")
        bot.note = None  # 步骤可写入「未完成」原因（目前只有冒险委托用）
        try:
            getattr(bot, f"step_{s}")()
            result[s] = bot.note or "ok"
        except NavigationError as e:
            result[s] = str(e)
            log(f"  {e}")
            break
        except Exception:
            traceback.print_exc()
            result[s] = "error"
            log(f"  {NAMES[s]} 出错，跳过")
            if s == "sign":
                log("  签到步骤异常，停止后续日常")
                break
    bot.to_main()
    log("完成：" + "，".join(f"{NAMES[k]}={v}" for k, v in result.items()))
    return 0 if all(v == "ok" for v in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
