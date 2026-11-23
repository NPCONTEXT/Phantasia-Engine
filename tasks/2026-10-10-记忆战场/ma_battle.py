"""崩坏3 · 记忆战场 战斗引擎（训练 / 正式通用）。

一场战斗 = 开局动作表（按 BOSS 档案）+ 通用循环（连点普攻，轮流试武器技/必杀/人偶，定时闪避、切人）。
只在看得到暂停键（= 战斗界面）时出手；HUD 消失超过 10 秒就读字判结果，25 秒还认不出就存图退出，不空等。
每场存：每秒一帧 frames/、按钮亮度 hud.csv、事件 events.log，供下一轮调阈值和手法。

用法（在暂停菜单 / 训练选人页 / 战斗中都可以）：
  ma_battle.py 档案.json [--start restart|train|now] [--limit 秒]
"""
from __future__ import annotations

import argparse, json, subprocess, sys, threading, time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "bh3_auto"))
from lib.adb import Adb                      # noqa: E402
from lib.frame_stream import FrameStream     # noqa: E402
from lib.pe_platform import ocr_lines, tmp_dir  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）
from touch import Touch                      # noqa: E402
import ma_nav                                # noqa: E402

OCR = ROOT / "bh3_auto/ocr/ocr"
# 战斗 HUD（2400×1504，10-10 月轮队实拍 shots/m4_10_load_4.png）
BTN = {"atk": (2200, 1290), "evade": (1920, 1360), "weapon": (1955, 1100), "ult": (2215, 1020),
       "elf": (1705, 1200), "p1": (2250, 420), "p2": (2250, 680), "pause": (80, 60)}
JOY = (300, 1207)
PAUSE_TPL = cv2.imread(str(HERE / "tpl/pause.png"), cv2.IMREAD_GRAYSCALE)
STAR_TPL = cv2.imread(str(HERE / "tpl/star_face.png"), cv2.IMREAD_GRAYSCALE)   # 半分辨率 70×70，见 star_prompt
STAR_MIN = 0.7
RING_MIN = 0.2          # 11-05 星之环光圈：圆环一圈蓝紫光占比，见 star_ring
END_WORDS = ("战斗失败", "确认结算", "重新挑战", "战斗耗时", "战斗得分", "挑战成功", "挑战失败")


def now() -> float:
    return time.monotonic()


def pause_score(img) -> float:
    g = cv2.cvtColor(img[20:120, 30:140], cv2.COLOR_BGR2GRAY)
    return float(cv2.matchTemplate(g, PAUSE_TPL, cv2.TM_CCOEFF_NORMED).max())


def disc_stats(img, c, r=60):
    """按钮圆内的平均饱和度、亮度（判可用/冷却用，阈值从 hud.csv 学）。"""
    x, y = c
    roi = img[y - r:y + r, x - r:x + r]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m = np.zeros(roi.shape[:2], np.uint8)
    cv2.circle(m, (r, r), r, 255, -1)
    return float(hsv[..., 1][m > 0].mean()), float(hsv[..., 2][m > 0].mean())


def ring_stats(img, c, r0=95, r1=135):
    """头像外圈环形区：黄、蓝、亮像素占比。QTE 可用时外圈有蓝黄大光环（10-10 月轮实测：亮 0.5–0.7，平时 0.05–0.2）。"""
    x, y = c
    roi = img[y - r1:y + r1, x - r1:x + r1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    yy, xx = np.mgrid[-r1:r1, -r1:r1]
    m = (xx ** 2 + yy ** 2 >= r0 * r0) & (xx ** 2 + yy ** 2 < r1 * r1)
    h, s, v = hsv[..., 0][m], hsv[..., 1][m], hsv[..., 2][m]
    yel = float(((h >= 15) & (h <= 35) & (s > 120) & (v > 170)).mean())
    blu = float(((h >= 95) & (h <= 115) & (s > 120) & (v > 170)).mean())
    return yel, blu, float((v > 200).mean())


def qte_lit(img, slot) -> bool:
    yel, blu, bright = ring_stats(img, BTN[slot])
    return bright > 0.45 and (blu > 0.15 or yel > 0.05)


def no_revive_dialog(img) -> bool:
    """战斗里有人倒下弹「本关卡无法复活」+「确定」：游戏停住，暂停键仍在（10-15 年年糕糕号实测卡了 90 秒）。
    特征：中间横条 y540–580 是均匀暗灰（标准差约 4，正常战斗 ≥45），确定按钮区是黄色。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    band = img[540:580, 200:2200]
    b, g, r = img[835:895, 1060:1340].reshape(-1, 3).mean(0)
    return band.std() < 12 and band.mean() < 60 and r > 170 and g > 150 and b < 120


def fox_stage(img) -> bool:
    """10-15 托纳提乌换队（花火队长）：花火大招后进「演出」选人画面，没有暂停键，不点就一直等。
    特征：左上第一张卡片（红色，推测是「选择自己」）红色占比约 0.75、右下大狐面按钮约 0.22；正常战斗卡片区几乎 0。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    def red(roi):
        h, s, v = roi[..., 0], roi[..., 1], roi[..., 2]
        return float((((h < 10) | (h > 170)) & (s > 120) & (v > 90)).mean())
    return red(hsv[190:250, 90:240]) > 0.5 and red(hsv[1180:1300, 2060:2180]) > 0.12


def star_prompt(img):
    """10-29 塔之鉑梏队（炽愿吉星 / 次生银翼 / 逆命魔龙）：人偶键 200/200 放「星之环」后，暂停键和技能键都消失，
    右下只剩一个带圈的灰发头像（手拿青色圆盘），不点要干等约 13 秒（作者：星之环开启后有必杀图标）。
    缩到半分辨率和头像模板比（模板截自 logs.nosync/tazhi_real_1029-234703 第 192.0 秒那帧）：
    两场 4 段 38 帧里 36 帧 0.79–1.00（另 2 帧是进出过渡），其余 45 场 1.1 万帧最高 0.52。
    返回 (分数, 头像中心 2400 基准坐标)。只认这一个头像：换了人就认不出，照旧等。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    win = cv2.cvtColor(cv2.resize(img[830:1150, 1870:2170], (150, 160)), cv2.COLOR_BGR2GRAY)
    _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, STAR_TPL, cv2.TM_CCOEFF_NORMED))
    return float(mx), ((loc[0] + 935 + 35) * 2, (loc[1] + 415 + 35) * 2)


def star_ring(img):
    """11-05：星之环带圈头像会换人（用户配的炽愿吉星队三个人、毕舍遮队金发都出现过），star_prompt 按脸只认得 10-29 那张灰发 →
    认右下那个圈：霍夫圆心在 (2012,1027)±35、半径 105–155，圆环一圈（r-8 到 r+16）蓝紫光（H 100–165、S≥80、V≥120）占比。
    先缩到半分辨率再放大回来（历史录帧是 1200 宽，阈值按它定）。历史录帧只看暂停键认不到的帧：
    星之环长段有圆 75 帧、占比 ≥0.2 的 74 帧；大招短段 957 帧过 8 帧（1 帧是真星之环）；结束画面 108 帧过 0。
    大招里一闪而过的圆靠 star_check「连着两次认到才点」挡掉。返回 (占比, 圆心 2400 基准) 或 (0.0, None)。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    roi = cv2.resize(cv2.resize(img[760:1270, 1740:2280], (270, 255)), (540, 510))
    g = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 1.5)
    cs = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.5, minDist=200, param1=90, param2=35, minRadius=105, maxRadius=155)
    if cs is None:
        return 0.0, None
    for x, y, r in cs[0][:3]:
        x, y, r = int(x), int(y), int(r)
        if abs(x + 1740 - 2012) > 35 or abs(y + 760 - 1027) > 35:
            continue
        yy, xx = np.ogrid[:roi.shape[0], :roi.shape[1]]
        d2 = (xx - x) ** 2 + (yy - y) ** 2
        ring = (d2 >= (r - 8) ** 2) & (d2 <= (r + 16) ** 2)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0][ring], hsv[..., 1][ring], hsv[..., 2][ring]
        glow = float(((h >= 100) & (h <= 165) & (s >= 80) & (v >= 120)).mean()) if h.size else 0.0
        return glow, (x + 1740, y + 760)
    return 0.0, None


def boss_hp(img):
    """顶部 BOSS 血条里红/橙色像素占比（粗看掉血用）。血条 y≈70–105（2400 基准，训练实拍 m4_23_hold），x 560–1700。"""
    roi = img[70:105, 560:1710]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = ((hsv[..., 0] < 12) | (hsv[..., 0] > 165)) & (hsv[..., 1] > 120) & (hsv[..., 2] > 120)
    return float(red.mean())


def ocr_img(img):
    p = tmp_dir() / "ma_battle_ocr.png"
    cv2.imwrite(str(p), img)
    out = []
    for line in ocr_lines(p, timeout=30).splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((d["text"].strip(), d["x"] + d["w"] // 2, d["y"] + d["h"] // 2))
    return out


class Battle:
    def __init__(self, profile: dict, log_dir: Path):
        self.pf = profile
        self.log_dir = log_dir
        (log_dir / "frames").mkdir(parents=True, exist_ok=True)
        self.ev = (log_dir / "events.log").open("a", encoding="utf-8")
        self.hud = (log_dir / "hud.csv").open("a", encoding="utf-8")
        self.hud.write("t,pause," + ",".join(f"{k}_s,{k}_v" for k in ("atk", "evade", "weapon", "ult", "elf", "p1", "p2")) + ",boss\n")
        self.adb = Adb()
        self.stream = FrameStream(self.adb, log_dir).start()
        self.touch = Touch()
        self.t0 = now()
        self.seq = 0
        self.img = None
        self.last_frame_save = 0.0
        self.last_hud_log = 0.0
        self.counts = {}
        self.team = profile.get("team", ["A", "B", "C"])
        self.active = 0                                    # 开局是队长（第 0 位）
        self.dead = set()                                  # 10-15：切到倒下的队友会弹「本关卡无法复活」，记下来不再切
        self.last_switch = None                            # (切之前在场的, 目标, 时刻)

    # ---------- 基础 ----------
    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')} +{now() - self.t0:6.1f}s] {msg}"
        print(line, flush=True)
        self.ev.write(line + "\n"); self.ev.flush()

    def frame(self, timeout=1.5):
        try:
            self.seq, _, self.img = self.stream.read(sequence=self.seq, timeout=timeout)
        except TimeoutError:
            self.seq, _, self.img = self.stream.read(timeout=3)
        t = now() - self.t0
        if t - self.last_frame_save >= 1.0:
            self.last_frame_save = t
            cv2.imwrite(str(self.log_dir / "frames" / f"{t:07.1f}.jpg"), cv2.resize(self.img, (1200, 752)),
                        [cv2.IMWRITE_JPEG_QUALITY, 70])
        if t - self.last_hud_log >= 0.5:
            self.last_hud_log = t
            vals = [f"{v:.0f}" for k in ("atk", "evade", "weapon", "ult", "elf", "p1", "p2") for v in disc_stats(self.img, BTN[k])]
            self.hud.write(f"{t:.1f},{pause_score(self.img):.2f}," + ",".join(vals) + f",{boss_hp(self.img):.3f}\n")
        return self.img

    def in_battle(self) -> bool:
        return pause_score(self.img) > 0.8

    def tap(self, name, hold=0.05):
        x, y = BTN[name]
        self.touch.tap(x, y, c=1, hold=hold)
        self.counts[name] = self.counts.get(name, 0) + 1

    def star_check(self) -> bool:
        """星之环头像（见 star_prompt）在：每秒最多点一次头像，返回 True；每段第一次记一行，整场第一次存全图。"""
        score, (x, y) = star_prompt(self.img)
        how = "分数"
        if score < STAR_MIN:                    # 11-05：脸不是 10-29 那张 → 认右下的圈，连着两次（1.5 秒内）认到才点
            glow, c = star_ring(self.img)
            if c is None or glow < RING_MIN:
                return False
            t, last = now(), getattr(self, "ring_seen", -9.0)
            self.ring_seen = t
            if t - last > 1.5:
                return True                     # 第一次认到：先不点、这一帧也不判结束，等下一帧确认
            score, (x, y), how = glow, c, "光圈"
        t = now()
        if getattr(self, "star_seg", None) is None:
            self.star_seg = [t, 0]
            self.log(f"  星之环头像（{how} {score:.2f}）：点头像 ({x}, {y})")
            if not getattr(self, "star_saved", False):
                self.star_saved = True
                self.save("star_prompt")
        if t - getattr(self, "star_last", 0.0) >= 1.0:
            self.star_last = t
            self.star_seg[1] += 1
            self.touch.tap(x, y, c=1, hold=0.05)
            self.counts["star"] = self.counts.get("star", 0) + 1
        return True

    def star_done(self):
        """回到战斗界面：记一下这段点了几次、从头像出现到回战斗界面多久（10-29 不点时约 13 秒）。"""
        seg = getattr(self, "star_seg", None)
        if seg is not None:
            self.log(f"  星之环这段结束：点了 {seg[1]} 次，从头像出现到回战斗界面 {now() - seg[0]:.1f} 秒")
            self.star_seg = None

    def short(self, k):
        return self.team[k].split("（")[0]

    def switch(self, k, why="切人"):
        """右侧上头像 = 队伍顺序里在场者的下一位，下头像 = 再下一位（10-10 月轮实测）。"""
        n = len(self.team)
        if k == self.active or k in self.dead:
            return
        slot = "p1" if k == (self.active + 1) % n else "p2"
        self.tap(slot)
        self.log(f"  {why}：{self.short(self.active)} → {self.short(k)}（点{slot}）")
        self.last_switch = (self.active, k, now())
        self.active = k

    def next_alive(self):
        """队伍顺序里在场者之后第一个没倒下的；都倒了返回 None。"""
        n = len(self.team)
        for step in range(1, n):
            k = (self.active + step) % n
            if k not in self.dead:
                return k
        return None

    def on_no_revive(self):
        """弹了「本关卡无法复活」：刚切过人（3 秒内）就是切到了倒下的队友 → 记下、在场的退回原来那位。"""
        if self.last_switch and now() - self.last_switch[2] < 3.0:
            prev, k, _ = self.last_switch
            self.dead.add(k); self.active = prev; self.last_switch = None
            self.log(f"  {self.short(k)} 已倒下（切过去弹「本关卡无法复活」），以后不切给她；在场仍是 {self.short(prev)}")
        else:
            self.log("  弹「本关卡无法复活」（不是刚切人引起的），点确定继续")

    def qte_slot(self):
        for slot in ("p1", "p2"):
            if qte_lit(self.img, slot):
                return slot
        return None

    def take_qte(self, slot):
        n = len(self.team)
        k = (self.active + 1) % n if slot == "p1" else (self.active + 2) % n
        self.tap(slot)
        self.log(f"  QTE：{self.short(self.active)} → {self.short(k)}（{slot} 亮）")
        self.last_switch = (self.active, k, now())
        self.active = k

    def hold(self, name, secs):
        x, y = BTN[name]
        self.touch.down(1, x, y)
        end = now() + secs
        while now() < end:
            self.frame(); time.sleep(0.05)
        self.touch.up(1)

    def joy(self, dx, dy, secs):
        self.touch.down(0, *JOY); self.touch.move(0, JOY[0] + dx, JOY[1] + dy)
        end = now() + secs
        while now() < end:
            self.frame(); time.sleep(0.05)
        self.touch.up(0)

    def wait(self, secs):
        end = now() + secs
        while now() < end:
            self.frame(); time.sleep(0.05)

    # ---------- 开局 / 循环 ----------
    def run_steps(self, steps):
        for st in steps:
            op = st[0]
            self.frame()
            if op == "wait":
                self.wait(st[1]); continue
            if not self.wait_battle(8):
                self.log(f"  动作 {st} 前不在战斗界面，跳过开局剩余")
                return False
            if op == "tap":
                self.tap(st[1])
            elif op == "hold":
                self.hold(st[1], st[2])
            elif op == "joy":
                self.joy(st[1], st[2], st[3])
            elif op == "switch":                    # ["switch", 队伍序号]
                self.switch(st[1])
            elif op == "qte":                       # ["qte", 最多等几秒, 没亮就切谁]
                end = now() + st[1]
                slot = None
                while now() < end and not slot:
                    self.frame(); slot = self.qte_slot() if self.in_battle() else None
                    if not slot:
                        self.tap("atk"); self.wait(0.12)
                if slot:
                    self.take_qte(slot)
                else:
                    self.switch(st[2], "QTE 没亮，直接切")
            elif op == "taps":                      # ["taps", 按钮, 次数, 间隔]
                for _ in range(st[2]):
                    self.tap(st[1]); self.wait(st[3])
            self.log(f"  开局 {st}")
        return True

    def clock(self):
        """顶部中间倒计时（05:00 起倒数）→ 剩余秒数；读不到 None。"""
        items = ocr_img(self.img[215:300, 980:1260])
        for t, _, _ in items:
            t = t.replace(" ", "").replace("：", ":")
            if len(t) >= 5 and t[2] == ":" and t[:2].isdigit() and t[3:5].isdigit():
                return int(t[:2]) * 60 + int(t[3:5])
        return None

    def wait_fight_start(self, secs=20):
        """BOSS 出场动画期间暂停键已经在了，但按键没用；等倒计时从 05:00 开始走再出手。"""
        end = now() + secs
        last = None
        while now() < end:
            self.frame()
            if self.in_battle():
                c = self.clock()
                last = c
                if c is not None and c < 300:
                    self.log(f"  倒计时已开始走（{c // 60:02d}:{c % 60:02d}）")
                    return True
            time.sleep(0.3)
        self.log(f"  {secs} 秒没等到倒计时开走（最后读到 {last}），直接开局")
        return False

    def wait_battle(self, secs) -> bool:
        end = now() + secs
        while now() < end:
            self.frame()
            if self.in_battle():
                return True
            time.sleep(0.1)
        return False

    def loop(self, limit):
        lp = self.pf.get("loop", {})
        n = 0
        gone_since = None
        while now() - self.t0 < limit:
            self.frame()
            if fox_stage(self.img):
                self.fox_taps = getattr(self, "fox_taps", 0) + 1
                if self.fox_taps == 1 or self.fox_taps % 20 == 0:
                    self.save("fox_stage")
                if self.fox_taps % 4 != 0:
                    self.log(f"  花火演出选人画面：点左上第一张（选择自己）再点狐面按钮（第 {self.fox_taps} 次）")
                    self.touch.tap(165, 220); self.wait(0.8)
                else:
                    self.log(f"  花火演出选人画面还在：只点狐面按钮（第 {self.fox_taps} 次）")
                self.touch.tap(2120, 1240); self.wait(1.5)
                gone_since = None
                continue
            if no_revive_dialog(self.img):
                self.on_no_revive()
                self.save("no_revive")
                self.touch.tap(1200, 866); self.wait(1.2)
                continue
            if not self.in_battle():
                gone_since = gone_since or now()
                gone = now() - gone_since
                if gone < 25 and self.star_check():       # 10-29 星之环头像：在就点，这时不判结束
                    time.sleep(0.15)
                    continue
                if gone > 3 and now() - getattr(self, "last_end_check", 0) > 2:
                    self.last_end_check = now()
                    res = self.check_end()
                    if res:
                        self.hud_gone_at = gone_since - self.t0
                        return res
                if gone > 25:
                    self.save("unknown")
                    return "unknown"
                time.sleep(0.15)
                continue
            if gone_since is not None:
                self.after_gap(now() - gone_since)
            gone_since = None
            self.star_done()
            n += 1
            self.act(n, lp)
            if n % 50 == 0:
                self.log(f"  循环 {n} 轮，按键计数 {self.counts}，BOSS 血条红占比 {boss_hp(self.img):.3f}")
        self.save("timeout")
        return "timeout"

    def after_gap(self, secs):
        """战斗界面消失 secs 秒后回来（大招、演出、星之环过场）。通用打法不管；专属打法用它认「刚开了星之环」。"""

    def act(self, n, lp):
        """战斗界面里出一轮手（通用循环，11-05 从 loop 里拆出来，专属打法覆盖它，见 make_battle）。"""
        stay = lp.get("stay")
        if stay is not None and self.active != stay:
            self.switch(stay, "回主 C"); self.wait(0.6)
        if lp.get("qte", True) and stay is None:
            slot = self.qte_slot()
            if slot:
                self.take_qte(slot); self.wait(0.5)
                return
        for _ in range(lp.get("atk_per_cycle", 3)):
            self.tap("atk"); self.wait(0.1)
        for key, every in (("weapon", lp.get("weapon_every", 1)), ("ult", lp.get("ult_every", 2)),
                           ("elf", lp.get("elf_every", 4)), ("evade", lp.get("evade_every", 8))):
            if every and n % every == 0:
                self.tap(key); self.wait(0.12)
        sw = lp.get("switch_every", 12)
        if sw and stay is None and n % sw == 0:
            k = self.next_alive()
            if k is not None:
                self.switch(k, "定时切人"); self.wait(0.6)

    def check_end(self):
        items = ocr_img(self.img)
        words = [t for t, _, _ in items]
        hit = [w for w in words if any(k in w for k in END_WORDS)]
        if hit:
            self.save("end")
            self.log(f"  结束画面：{hit}；全部文字前 20：{words[:20]}")
            return "fail" if any("失败" in w for w in hit) else "win"
        return None

    def save(self, tag):
        cv2.imwrite(str(self.log_dir / f"{tag}_{now() - self.t0:06.1f}.png"), self.img)

    def close(self):
        for f in (self.ev, self.hud):
            f.close()
        self.touch.close()
        self.stream.stop()


def start_battle(b: Battle, how: str):
    """把画面带进战斗：restart = 暂停菜单「重新开始」→ 确认；train = 训练选人页「开始训练」；now = 已在战斗。"""
    b.frame()
    if how == "restart":
        b.touch.tap(774, 1402); b.wait(1.5)
        items = ocr_img(b.frame())
        pos = [(x, y) for t, x, y in items if t == "重新开始" and y < 1300]
        if pos:
            b.touch.tap(*pos[0]); b.log(f"  确认重新开始 @{pos[0]}")
            end = now() + 6                           # 10-15：确认后马上读到的还是重开前的旧画面（倒计时 02:59），先等战斗界面消失
            while now() < end:
                b.frame()
                if not b.in_battle():
                    break
                time.sleep(0.1)
    elif how == "train":
        b.touch.tap(2097, 1398)
    if not b.wait_battle(25):
        b.save("no_battle")
        raise RuntimeError("25 秒没进战斗界面")
    b.t0 = now()                                  # 实际用时从进战斗算
    b.log("进入战斗")


def make_battle(pf, log_dir):
    """按档案 style 建战斗对象：没写 = 通用循环；"fuhua_star" = 陨影冰骸符华专属打法（11-05 作者教的，见 ma_style_fuhua.py）。"""
    if pf.get("style") == "fuhua_star":
        import ma_style_fuhua
        return ma_style_fuhua.FuhuaStarBattle(pf, log_dir)
    return Battle(pf, log_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--start", default="restart", choices=("restart", "train", "now"))
    ap.add_argument("--train", action="store_true", help="从记忆战场里任意菜单页导航进训练（按档案 boss_idx / lineup.row 配队），打完回记忆战场页")
    ap.add_argument("--limit", type=float, default=900)
    a = ap.parse_args()
    pf = json.loads(Path(a.profile).read_text())
    log_dir = HERE / "logs.nosync" / f"{pf['id']}_{time.strftime('%m%d-%H%M%S')}"
    if a.train:
        ma_nav.to_train_team(pf["boss_idx"], pf["lineup"]["row"], tab=pf["lineup"].get("tab", "selected"))
        a.start = "train"
    b = make_battle(pf, log_dir)
    try:
        start_battle(b, a.start)
        b.wait_fight_start()
        b.run_steps(pf.get("opening", []))
        res = b.loop(a.limit)
        gone = getattr(b, "hud_gone_at", None)
        b.log(f"结果 {res}，实际用时 {now() - b.t0:.1f} 秒（战斗界面消失在 {gone if gone is None else round(gone, 1)} 秒），按键计数 {b.counts}")
    finally:
        b.close()
    if a.train:
        time.sleep(3)
        ma_nav.to_ma_page()
        print("已回记忆战场页", flush=True)


if __name__ == "__main__":
    main()
