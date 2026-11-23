"""超弦空间战斗：继承记忆战场引擎 ma_battle.Battle（只 import 不改记忆战场的文件）。
多出来的：档案 loop.extra 里的额外按键（如特异区「关卡技能」漩涡键 (1747,933)，10-17 实拍），跟着每轮的武器技一起按。
注意：extra 挂在 tap("weapon") 上，档案 weapon_every 要是 1（每轮都按武器技）才每轮计数。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-10-记忆战场"))
import ma_battle                      # noqa: E402
from ma_battle import Battle          # noqa: E402

# 超弦空间结算页（10-17 实拍）：区名 / 最高连击数 / 战斗耗时 / 战斗得分 —— 打满、超时都是这页，「战斗耗时」「战斗得分」已在记忆战场那套里
ma_battle.END_WORDS = tuple(dict.fromkeys(ma_battle.END_WORDS + ("最高连击数",)))

# ---------- 10-25 爬楼梯：认「这一层清完、要走路上楼」 ----------
# 10-24 血棘边缘区分 4 层（左上「当前区域 1/4」），清一层后五个技能键同时变禁止图标、提示「向更高的楼层前进吧」，
# 要走楼梯上楼；以前的脚本只会原地按键，空打到 5 分钟超时。「当前区域 n/m」普通刷怪关也有，不能拿来认。
# 认法：五个键都像禁止图标。模板 tpl/ban_1024.png = 10-24 卡住那场 159 帧右下角取中位数（1200×752 坐标 (800,460)–(1160,730)）。
# 统计（侦察/ban_stats.py，10-25）：楼梯关清层后第一帧五键最低相似度就有 0.62–0.80；普通关 33 场 4565 帧最高 0.35
# （特异区四个键冷却时也是禁止图标，但武器位是亮着的关卡技能键，所以要五个键都像才算）。阈值 0.55，两边各留约 0.2。
BAN_TPL = cv2.imread(str(HERE / "tpl" / "ban_1024.png"), cv2.IMREAD_GRAYSCALE)
BAN_BOX = (800, 460, 1160, 730)
BAN_BTN = {"atk": (1100, 645), "evade": (960, 680), "weapon": (977, 550), "ult": (1107, 510), "elf": (852, 600)}  # = ma_battle.BTN / 2
BAN_R, BAN_PAD, BAN_TH = 38, 6, 0.55


def ban_scores(img) -> dict:
    """五个键和禁止图标模板的相似度（-1–1）。img 是 2400×1504 或 1200×752 的 BGR。"""
    k = img.shape[1] / 1200
    x0, y0, x1, y1 = BAN_BOX
    roi = img[int(y0 * k):int(y1 * k), int(x0 * k):int(x1 * k)]
    if k != 1:
        roi = cv2.resize(roi, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    out = {}
    for name, (x, y) in BAN_BTN.items():
        cx, cy = x - x0, y - y0
        t = BAN_TPL[cy - BAN_R:cy + BAN_R, cx - BAN_R:cx + BAN_R]
        win = g[max(0, cy - BAN_R - BAN_PAD):cy + BAN_R + BAN_PAD, max(0, cx - BAN_R - BAN_PAD):cx + BAN_R + BAN_PAD]
        out[name] = float(cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED).max())
    return out


def all_banned(img) -> bool:
    """五个键都是禁止图标（这一层清完、没有敌人）。"""
    return min(ban_scores(img).values()) > BAN_TH


class FloorClear(Exception):
    """战斗循环里认出「这一层清完」，跳出来走路。"""


# ---------- 11-01 有人退场 ----------
# 倒下本身不弹框，切到倒下的人才弹「本关卡无法复活」+ 确定（历史 10 场 101 次弹框全是刚切人后弹的，识字 101 次都认得出「无法复活」）。
# 记忆战场引擎的 QTE 那条路不看已倒下名单、倒人后头像顺序也对不上，11-01 特异区 70 秒起连弹 39 次，每次停 1–2 秒。
# 作者选「高危重开·前三区打完」：高危区（按用时算分，倒人必掉分）认出就放弃这场重开；前三区不再切人、不接 QTE，用在场的人打完保住进度。
class MemberDown(Exception):
    """要放弃的这场有人退场：跳出战斗循环去放弃。"""


# ---------- 11-03 亮背景误认 QTE ----------
# 11-03 新一期边缘区（异星肃卫-纠谬）右边是亮蓝玻璃墙，镜头对着它时头像外圈（r95–135）全是亮蓝背景，
# 记忆战场的 qte_lit 当成 QTE 亮（蓝 0.84、亮 0.85，线是 0.15 / 0.45）；战斗循环「QTE 亮就切人、这轮不打」→ 每 0.6 秒切一次、一下不打，
# 那场 p1 点了 333 次、局内 04:35 才打满（同环境密集区正常打 01:42）。真 QTE 光圈是贴着头像的一圈粗光环，背景是一整片：再看更外面一圈（r140–175）当对照，
# 头像外圈的亮像素占比要比它高出 QTE_MIN_GAP 才算。统计（侦察/qte_bg_stats.py，历史 89 场 14059 帧）：判定亮 4473 次里一半多
# 外圈和内圈差不到 0.1；抽 32 帧（肉眼分）：真光圈 19 帧有 18 帧差 0.18–0.57（漏 1 帧：亮背景上的白光圈，差 0.01），
# 背景 13 帧有 12 帧差 −0.14–0.12（放过 1 帧，差 0.22）。截帧是 1200 宽放大回 2400 量的，实战原图可能略有出入。
# 保险：6 秒内已接 3 次 QTE 又亮，当背景误认，停 10 秒不接 QTE（照常打、定时切人照旧）。
QTE_OUT_R = (140, 175)
QTE_MIN_GAP = 0.15
QTE_BURST_N, QTE_BURST_S, QTE_MUTE_S = 3, 6.0, 10.0


def ring_bright(img, c, r0, r1) -> float:
    """以 c 为圆心、半径 r0–r1 圆环里亮像素（V>200）的占比；出了画面的部分不算（p1/p2 的外圈右边出界）。img 是 2400×1504。"""
    x, y = c
    h, w = img.shape[:2]
    x0, x1, y0, y1 = max(0, x - r1), min(w, x + r1), max(0, y - r1), min(h, y + r1)
    yy, xx = np.mgrid[y0 - y:y1 - y, x0 - x:x1 - x]
    d2 = xx * xx + yy * yy
    m = (d2 >= r0 * r0) & (d2 < r1 * r1)
    v = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)[..., 2][m]
    return float((v > 200).mean())


def qte_lit(img, slot) -> bool:
    """记忆战场的 QTE 判定，再加背景对照：头像外圈比更外面一圈亮出 QTE_MIN_GAP 才算光圈。"""
    if not ma_battle.qte_lit(img, slot):
        return False
    c = ma_battle.BTN[slot]
    return ma_battle.ring_stats(img, c)[2] - ring_bright(img, c, *QTE_OUT_R) >= QTE_MIN_GAP


class SSBattle(Battle):
    def __init__(self, profile: dict, log_dir: Path, quit_on_down: bool = False):
        super().__init__(profile, log_dir)
        self.stairs_init()
        self.down_init(quit_on_down)

    def down_init(self, quit_on_down=False):
        """quit_on_down：有人退场就放弃这场（高危区，最后一场除外）；否则不再切人、打完。"""
        self.quit_on_down = quit_on_down
        self.down_at = None                     # 第一次认出有人退场的秒数
        self.no_switch = False
        self._qte_ts, self._qte_mute = [], 0.0  # 11-03 QTE 连着亮的保险：最近接 QTE 的时刻、停到哪一刻

    def on_no_revive(self):
        """战斗循环见到「本关卡无法复活」框时调用（返回后循环存图、点确定）。第一次先识字确认，认出就算有人退场。"""
        if self.down_at is not None:
            return super().on_no_revive()       # 已经不切人了还弹（不该有）：照旧记一行
        words = [w for w, _, _ in ma_battle.ocr_img(self.img)]
        if not any("无法复活" in w for w in words):
            self.log(f"  像「本关卡无法复活」框但没认出字：{words[:6]}，照旧点确定")
            return
        self.down_at = ma_battle.now() - self.t0
        if self.quit_on_down:
            raise MemberDown()
        self.stop_switching()

    def stop_switching(self):
        self.no_switch = True
        stay = self.pf.get("loop", {}).get("stay")
        if stay is not None:
            self.active = stay                  # 档案要是写了 stay，免得循环每轮想「回主 C」切不过去又白等 0.6 秒
        self.log("  有人退场（切人弹「本关卡无法复活」）：之后不再切人、不接 QTE，用在场的人打完")

    def qte_slot(self):
        """换成带背景对照的 qte_lit；6 秒内已接 3 次又亮就停 10 秒不接（见文件头 11-03）。"""
        if self.no_switch:
            return None
        t = ma_battle.now()
        if t < self._qte_mute:
            return None
        for slot in ("p1", "p2"):
            if qte_lit(self.img, slot):
                self._qte_ts = [x for x in self._qte_ts if t - x < QTE_BURST_S]
                if len(self._qte_ts) >= QTE_BURST_N:
                    self._qte_ts, self._qte_mute = [], t + QTE_MUTE_S
                    self.log(f"  {QTE_BURST_S:.0f} 秒内已接 {QTE_BURST_N} 次 QTE 又亮，多半是背景误认：停 {QTE_MUTE_S:.0f} 秒不接 QTE")
                    return None
                return slot
        return None

    def take_qte(self, slot):
        self._qte_ts.append(ma_battle.now())
        super().take_qte(slot)

    def next_alive(self):
        return None if self.no_switch else super().next_alive()

    def switch(self, k, why="切人"):
        if not self.no_switch:
            super().switch(k, why)

    def quit_down(self):
        """有人退场、这场要放弃：点确定关掉弹框 → 暂停 → 放弃战斗，返回 died；没放弃成就回到战斗、改成不切人打完，返回 None。"""
        for c in sorted(self._down):
            self.touch.up(c)
        self.save("member_down")
        self.log("  有人退场（切人弹「本关卡无法复活」）→ 放弃这场重开")
        self.touch.tap(1200, 866)               # 弹框的「确定」（同 ma_battle.loop）
        time.sleep(1.2)
        if self.abandon():
            return "died"
        self.log("  放弃没点成，回到战斗接着打")
        for word in ("再想想", "继续战斗"):     # 停在放弃确认框 / 暂停菜单就点回去；都不在就不点
            items = ma_battle.ocr_img(ma_battle.Battle.frame(self))
            pos = next(((x, y) for w, x, y in items if word in w), None)
            if pos:
                self.touch.tap(*pos, c=1, hold=0.08)
                time.sleep(1.5)
        self.stop_switching()
        return None

    def stairs_init(self):
        """爬楼梯用的状态；顺便记下哪些手指按着（战斗循环被 FloorClear 打断时，hold / joy 里按下的手指要抬起来）。"""
        self.watch_floor = False
        self._ban_t = 0.0
        self._ban_hist = []
        self.floors = 0
        self._down = set()
        down, up = self.touch.down, self.touch.up

        def _down(c, x, y):
            self._down.add(c); down(c, x, y)

        def _up(c):
            self._down.discard(c); up(c)
        self.touch.down, self.touch.up = _down, _up

    def floor_check(self, img) -> bool:
        """每 0.5 秒看一次五个键；最近 4 次里有 3 次都是禁止图标 → 这一层清完了。"""
        t = ma_battle.now()
        if t - self._ban_t < 0.5:
            return False
        self._ban_t = t
        self._ban_hist = (self._ban_hist + [all_banned(img)])[-4:]
        return sum(self._ban_hist) >= 3

    def frame(self, timeout=1.5):
        img = super().frame(timeout)
        if self.watch_floor and self.in_battle() and self.floor_check(img):
            self._ban_hist = []
            raise FloorClear()
        return img

    def loop(self, limit):
        """记忆战场的战斗循环外面包一层：认出「这一层清完」就跳出来走路上楼，走到了接着打，走不到返回 walk_up 的结果；
        要放弃的这场有人退场就跳出来放弃，返回 died（没放弃成就接着打）。"""
        self.watch_floor = True
        try:
            while True:
                try:
                    return super().loop(limit)
                except MemberDown:
                    r = self.quit_down()
                    if r:
                        return r
                except FloorClear:
                    for c in sorted(self._down):
                        self.touch.up(c)
                    self.floors += 1
                    self.log(f"  第 {self.floors} 层清完（五个技能键都是禁止图标），走路上楼")
                    self.save(f"floor_clear_{self.floors}")
                    r = self.walk_up(limit)
                    if r != "arrived":
                        return r
        finally:
            self.watch_floor = False

    def walk_up(self, limit) -> str:
        """清层后走到下一层：返回 arrived（敌人出现，交回战斗循环）或 stairs（没走上去，这场已放弃）。
        10-25 作者定先跑剩下三区：自动走楼梯还没写（本期边缘区作者手打满了，没法实测），先兜底——放弃这场，ss_run 报「请手动打」。"""
        self.log("  这关要爬楼梯，脚本还不会自动走：放弃这场")
        self.abandon()
        return "stairs"

    def abandon(self) -> bool:
        """暂停 → 「放弃战斗」→ 确认框里点放弃 / 确定（不点「再想想」「取消」）。返回是否点到了确认。"""
        for c in sorted(self._down):
            self.touch.up(c)
        pos = None
        for k in range(3):
            self.touch.tap(*ma_battle.BTN["pause"], c=1, hold=0.08)
            time.sleep(1.5)
            items = ma_battle.ocr_img(ma_battle.Battle.frame(self))
            pos = next(((x, y) for w, x, y in items if "放弃战斗" in w and y > 1200), None)
            if pos:
                break
            self.log(f"  第 {k + 1} 次点暂停没见到「放弃战斗」：{[w for w, _, _ in items][:6]}")
        if not pos:
            self.save("abandon_no_pause")
            return False
        self.touch.tap(*pos, c=1, hold=0.08)
        time.sleep(1.5)
        items = ma_battle.ocr_img(ma_battle.Battle.frame(self))
        self.save("abandon_dialog")
        self.log(f"  放弃确认框：{[w for w, _, _ in items][:10]}")
        ok = next(((x, y) for w, x, y in items if 800 < y < 1250 and "再想想" not in w and "取消" not in w
                   and any(k in w for k in ("放弃", "确定", "确认"))), None)
        if not ok:
            return False
        self.touch.tap(*ok, c=1, hold=0.08)
        time.sleep(3)
        return True

    def ss_clock(self):
        """右上角计时（从 00:00 往上数，10-18 实拍；记忆战场是顶部中间 05:00 倒数）→ 已用秒数；读不到 None。"""
        items = ma_battle.ocr_img(self.img[20:100, 1990:2200])
        for t, _, _ in items:
            t = t.replace(" ", "").replace("：", ":")
            if len(t) >= 5 and t[2] == ":" and t[:2].isdigit() and t[3:5].isdigit():
                return int(t[:2]) * 60 + int(t[3:5])
        return None

    def wait_fight_start(self, secs=10):
        """计时一开始走就出手。10-18 查日志：沿用记忆战场的等 05:00 倒数，每场干等 20 秒，局内已走 13 秒、额外得分 1000→959、被打掉 1/4 血。"""
        end = ma_battle.now() + secs
        last = None
        while ma_battle.now() < end:
            self.frame()
            if self.in_battle():
                c = self.ss_clock()
                last = c
                if c is not None and c >= 1:
                    self.log(f"  计时已开始走（{c // 60:02d}:{c % 60:02d}）")
                    return True
            time.sleep(0.3)
        self.log(f"  {secs} 秒没等到计时开走（最后读到 {last}），直接开局")
        return False

    def tap(self, name, hold=0.05):
        super().tap(name, hold)
        if name != "weapon":
            return
        self._cycle = getattr(self, "_cycle", 0) + 1
        for ex in self.pf.get("loop", {}).get("extra", []):
            if self._cycle % ex.get("every", 3) == 0:
                self.touch.tap(*ex["xy"], c=1, hold=0.05)
                self.counts[ex["name"]] = self.counts.get(ex["name"], 0) + 1
                self.wait(0.12)


def no_fox():
    """队里没花火：关掉花火演出画面判断（10-17 开场动画偏红被误认、点了两下）。"""
    ma_battle.fox_stage = lambda img: False


# ---------- 11-07 专属打法：档案写 "style": "fuhua_star" = 记忆战场陨影冰骸符华队的打法（ma_style_fuhua，只 import 不改） ----------
# 作者：「可以换成昨天战场的符华+希儿+那个啥那个专属打法试试，应该会快很多」（高危区推荐阵容第 2 行局内 05:03 只得 983）。
class _FuhuaSS:
    """混进「SSBattle + ma_style_fuhua.FuhuaStarBattle」的两处超弦改动（合成见 fuhua_class）：
    ① 专属打法换人都走 go：有人退场后（no_switch）不再换人，免得一直切到倒下的人、一直弹「本关卡无法复活」；
    ② 专属打法的 QTE 用 ma_style_fuhua 模块里的 qte_lit（记忆战场的，没有背景对照）：每轮出手时换成 style_qte
       （超弦带背景对照的 qte_lit + 6 秒 3 次停 10 秒的保险，同 SSBattle.qte_slot），出手完换回去。"""

    def go(self, who, why):
        if self.no_switch:
            return False
        return super().go(who, why)

    def act(self, n, lp):
        import ma_style_fuhua as fs
        orig = fs.qte_lit
        fs.qte_lit = self.style_qte
        try:
            return super().act(n, lp)
        finally:
            fs.qte_lit = orig

    def style_qte(self, img, slot) -> bool:
        if self.no_switch:
            return False
        t = ma_battle.now()
        if t < self._qte_mute or not qte_lit(img, slot):
            return False
        self._qte_ts = [x for x in self._qte_ts if t - x < QTE_BURST_S]
        if len(self._qte_ts) >= QTE_BURST_N:
            self._qte_ts, self._qte_mute = [], t + QTE_MUTE_S
            self.log(f"  {QTE_BURST_S:.0f} 秒内已接 {QTE_BURST_N} 次 QTE 又亮，多半是背景误认：停 {QTE_MUTE_S:.0f} 秒不接 QTE")
            return False
        self._qte_ts.append(t)      # 专属打法认到亮就马上换人（go），这里就记成接了一次
        return True


_FUHUA_CLS = None


def fuhua_class():
    """SSBattle + 符华专属打法合成的类（用到才 import ma_style_fuhua：它一 import 就读模板图）。
    MRO：_FuhuaSS → SSBattle → FuhuaStarBattle → Battle：战斗循环外层（楼梯、有人退场放弃 / 不切人）、开局等待、
    按键加关卡技能用超弦的；每轮出手（act）、星之环过场（after_gap）、认人用专属打法的。"""
    global _FUHUA_CLS
    if _FUHUA_CLS is None:
        import ma_style_fuhua as fs
        _FUHUA_CLS = type("SSFuhuaBattle", (_FuhuaSS, SSBattle, fs.FuhuaStarBattle), {})
    return _FUHUA_CLS
