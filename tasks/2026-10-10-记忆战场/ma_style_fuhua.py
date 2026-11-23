"""陨影冰骸 · 符华专属打法（11-05 作者教的，档案写 "style": "fuhua_star" 就用它，见 ma_battle.make_battle）。
示范录像：超弦 logs.nosync/demo_1105-033608（作者手打，局内 02:14 / 47072；按键时间线 touch_timeline_1105.txt）。
队伍（官方推荐第 3 行）：1 号符华（浮生·渡尘之羽，队长技能带星之环）/ 2 号希儿 / 3 号白发青底 + 红发人偶。
作者的规矩：
- 只在符华、希儿之间换；3 号除了星之环里的技能键，其他时候不点。
- 符华在前台：普攻 + 偶尔武器技 + 大招能放就放；QTE 只接希儿。希儿在前台：普攻攒满放大招；QTE 只接符华。
- 人偶键满 150 就点 = 开星之环；开了要尽快让符华在前台。符华在前台时两个头像变成水滴形技能键：
  先点下面那个（3 号），等冷却转完一圈再点上面那个（希儿），然后放符华大招；星之环结束回到符华、希儿轮换。
  （作者嘴上说「先点 2 号再点 3 号」，示范录像四次都是先点下面 3 号、再点上面希儿，这里照录像。）
- 爬楼梯打灯那段不管。
认人：右侧上下两个头像的位置不固定（点哪格，那格的人就和在场的人换位），按头像模板认是谁，没出现的那个就在场；
连着两帧一样才算数。模板 tpl/fs_*.png 是示范录像半分辨率（1200 宽）灰度 50×50。
"""
from __future__ import annotations

import cv2
import numpy as np

from ma_battle import HERE, Battle, now, qte_lit

PEOPLE = ("符华", "希儿", "3号")
TPLS = {k: cv2.imread(str(HERE / "tpl" / f"fs_{k}.png"), cv2.IMREAD_GRAYSCALE)
        for k in ("fuhua_a", "fuhua_b", "seele_a", "seele_b", "third_a", "third_b", "seele_star", "third_star")}
WHO = {"fuhua": "符华", "seele": "希儿", "third": "3号"}
WIN = {"p1": (130, 300, 1040, 1200), "p2": (290, 460, 1040, 1200)}     # 半分辨率 y0, y1, x0, x1（上、下头像）
WHO_MIN = 0.65          # 认人分数下限
WHO_GAP = 0.08          # 第一名比第二名至少高这么多
STAR_MIN = 0.7          # 下面那格认成「3 号水滴形」到这个分数 = 符华在前台的星之环技能键
READY_V = 110           # 水滴形技能键能按：头像圆里平均亮度（示范：能按 140–233，刚按完 66–75）
ELF_FULL_V = 225        # 人偶键满 150：按键圆里平均亮度（示范：四次点之前连着 2–12 帧 240–254；其余偶尔一闪）
QTE_TO = {"符华": "希儿", "希儿": "符华"}
STAY_MAX = {"希儿": 15.0, "符华": 25.0}     # 一直没等到 QTE 就自己换（示范：希儿 6–13 秒、符华 7–36 秒一轮）
STAR_END_MISS = 2       # 星之环技能键连着几轮没认到才算星之环完了（11-07 超弦高危区：77.4 秒一帧特效两格都像符华，
                        # 当成完了、重认后又从「点下面」开始，上面希儿一次没点到）


def read_slots(img):
    """认右侧上下两个头像：返回 {"p1": (谁, 分数, 领先, 中心 2400 基准, 是不是 3 号水滴形), "p2": ...}。"""
    small = cv2.resize(img, (1200, 752)) if img.shape[1] != 1200 else img
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    out = {}
    for slot, (y0, y1, x0, x1) in WIN.items():
        win = g[y0:y1, x0:x1]
        best = {}
        star = 0.0
        for k, t in TPLS.items():
            r = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            who = WHO[k.split("_")[0]]
            if mx > best.get(who, (-1,))[0]:
                best[who] = (mx, ((loc[0] + x0 + 25) * 2, (loc[1] + y0 + 25) * 2))
            if k == "third_star":
                star = mx
        order = sorted(best.items(), key=lambda kv: -kv[1][0])
        (who, (sc, c)), second = order[0], order[1][1][0]
        out[slot] = (who, float(sc), float(sc - second), c, star >= STAR_MIN)
    return out


def slot_ready(img, center) -> bool:
    """水滴形技能键能不能按：以头像中心为圆心（半分辨率半径 24）平均亮度。"""
    small = cv2.resize(img, (1200, 752)) if img.shape[1] != 1200 else img
    v = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 2]
    cx, cy = center[0] // 2, center[1] // 2
    yy, xx = np.ogrid[:v.shape[0], :v.shape[1]]
    m = (xx - cx) ** 2 + (yy - cy) ** 2 <= 24 * 24
    return float(v[m].mean()) > READY_V


def elf_full(img) -> bool:
    """人偶键（星之环能级）满 150：半分辨率 (852,600) 半径 26 平均亮度 ≥ ELF_FULL_V。"""
    small = cv2.resize(img, (1200, 752)) if img.shape[1] != 1200 else img
    v = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 2]
    yy, xx = np.ogrid[:v.shape[0], :v.shape[1]]
    m = (xx - 852) ** 2 + (yy - 600) ** 2 <= 26 * 26
    return float(v[m].mean()) >= ELF_FULL_V


class FuhuaStarBattle(Battle):
    def __init__(self, profile, log_dir):
        super().__init__(profile, log_dir)
        self.field = None             # 认定在场的人
        self.field_since = now()
        self.cand = (None, 0)         # 连着几帧推出同一个在场的人
        self.slots = None
        self.star = None              # 星之环这一段：{"t0", "bot", "top"}
        self.star_until = 0.0         # 刚开星之环（人偶键点过 + 过场）到这个时刻前，希儿在场就切回符华
        self.last_elf = -99.0         # 上次点人偶键（满了才点）
        self.elf_seen = 0             # 连着几轮看到人偶键满
        self.star_seen = 0            # 连着几轮看到星之环技能键
        self.star_miss = 0            # 星之环这一段里连着几轮没认到技能键

    # ---------- 认人 ----------
    def look(self):
        s = read_slots(self.img)
        self.slots = s
        a, b = s["p1"], s["p2"]
        ok = a[1] >= WHO_MIN and b[1] >= WHO_MIN and a[2] >= WHO_GAP and b[2] >= WHO_GAP and a[0] != b[0]
        guess = ({*PEOPLE} - {a[0], b[0]}).pop() if ok else None
        if guess is not None:
            n = self.cand[1] + 1 if self.cand[0] == guess else 1
            self.cand = (guess, n)
            if n >= 2 and guess != self.field:
                self.log(f"  认人：在场 {guess}（上 {a[0]} {a[1]:.2f}，下 {b[0]} {b[1]:.2f}）")
                self.field, self.field_since = guess, now()
        return s

    def slot_of(self, who):
        for slot in ("p1", "p2"):
            if self.slots and self.slots[slot][0] == who and self.slots[slot][1] >= WHO_MIN:
                return slot
        return None

    def go(self, who, why):
        slot = self.slot_of(who)
        if slot is None:
            return False
        self.touch.tap(*self.slots[slot][3], c=1, hold=0.05)
        self.counts[slot] = self.counts.get(slot, 0) + 1
        self.log(f"  {why}：{self.field} → {who}（点{slot}）")
        self.cand = (who, 1)
        self.field, self.field_since = who, now()
        self.wait(0.5)
        return True

    # ---------- 一轮 ----------
    def after_gap(self, secs):
        """人偶键（满了才点）点完 4 秒内战斗界面消失 1–4 秒 = 星之环过场（示范 1.9 秒；大招过场约 5 秒不算）。"""
        if 1.0 <= secs <= 4.0 and now() - self.last_elf <= secs + 4.0:
            self.star_until = now() + 22.0
            self.log(f"  星之环开了（人偶键点过、过场 {secs:.1f} 秒）")

    def act(self, n, lp):
        s = self.look()
        star_keys = s["p2"][4] and s["p1"][0] == "希儿"
        self.star_seen = self.star_seen + 1 if star_keys else 0
        if self.star_seen >= 2 or star_keys and self.star is not None:      # 连着两轮认到才算（示范回放有两帧一闪）
            self.star_miss = 0
            return self.star_step(s)
        if star_keys:                       # 第一轮看到：先只普攻，别放大招、别换人，等下一轮确认
            for _ in range(2):
                self.tap("atk"); self.wait(0.1)
            return
        if self.star is not None:
            self.star_miss = getattr(self, "star_miss", 0) + 1
            if self.star_miss < STAR_END_MISS:      # 只少了一轮：先当星之环还开着，只普攻，下一轮再看
                self.log(f"  星之环技能键这一轮没认到（上 {s['p1'][0]} {s['p1'][1]:.2f}，下 {s['p2'][0]} {s['p2'][1]:.2f}），先当还开着")
                for _ in range(2):
                    self.tap("atk"); self.wait(0.1)
                return
            self.star_miss = 0
            st = self.star
            self.log(f"  星之环技能键没了：下面{'点了' if st['bot'] else '没点'}、上面{'点了' if st['top'] else '没点'}，"
                     f"持续 {now() - st['t0']:.1f} 秒")
            self.star = None
            self.star_until = 0.0       # 11-05 真机第一场：星之环完了还在「换符华到前台」，和 QTE 来回抢了 7 秒
        f = self.field
        if f == "3号":
            if self.go("符华", "3 号不该在场，换回符华") or self.go("希儿", "3 号不该在场，换回希儿"):
                return
        if f == "希儿" and now() < self.star_until:
            if self.go("符华", "星之环开着，换符华到前台"):
                return
        if f in QTE_TO:
            want = QTE_TO[f]
            for slot in ("p1", "p2"):
                if s[slot][0] == want and qte_lit(self.img, slot):
                    self.go(want, "QTE")
                    return
        for _ in range(3):
            self.tap("atk"); self.wait(0.1)
        if f == "符华" and n % 2 == 0 or f == "希儿" and n % 4 == 0:
            self.tap("weapon"); self.wait(0.12)
        self.tap("ult"); self.wait(0.12)
        self.elf_seen = self.elf_seen + 1 if elf_full(self.img) else 0
        if f in QTE_TO and self.elf_seen >= 2 and now() - self.last_elf > 5.0:
            self.tap("elf"); self.last_elf = now(); self.wait(0.12)
            self.log(f"  人偶键满了，点它开星之环（{f} 在场）")
        if n % lp.get("evade_every", 4) == 0:
            self.tap("evade"); self.wait(0.12)
        if f in STAY_MAX and now() - self.field_since > STAY_MAX[f] and now() >= self.star_until:
            self.go(QTE_TO[f], f"{f} 在场超过 {STAY_MAX[f]:.0f} 秒没等到 QTE，自己换")

    def star_step(self, s):
        """符华在前台、两个头像是水滴形技能键：先点下面（3 号），冷却转完再点上面（希儿），然后放符华大招。"""
        if self.field != "符华":
            self.field, self.field_since = "符华", now()
        st = self.star
        if st is None:
            st = self.star = {"t0": now(), "bot": None, "top": None}
            self.log("  星之环技能键出来了（符华在前台）")
            self.save("fs_star")
        for _ in range(2):
            self.tap("atk"); self.wait(0.1)
        if st["bot"] is None:
            if slot_ready(self.img, s["p2"][3]):
                self.touch.tap(*s["p2"][3], c=1, hold=0.05)
                self.counts["star_bot"] = self.counts.get("star_bot", 0) + 1
                st["bot"] = now()
                self.log(f"  星之环：点下面 3 号技能键 {s['p2'][3]}")
                self.wait(0.4)
            return
        if st["top"] is None:
            if now() - st["bot"] >= 3.0 and slot_ready(self.img, s["p1"][3]):
                self.touch.tap(*s["p1"][3], c=1, hold=0.05)
                self.counts["star_top"] = self.counts.get("star_top", 0) + 1
                st["top"] = now()
                self.log(f"  星之环：冷却转完，点上面希儿技能键 {s['p1'][3]}（距下面 {st['top'] - st['bot']:.1f} 秒）")
                self.wait(0.4)
            return
        self.tap("ult"); self.wait(0.12)
