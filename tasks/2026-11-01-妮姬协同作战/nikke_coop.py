"""妮姬 · 协同作战（国服《胜利女神：新的希望》，竖屏 1504x2400），幻想引擎单独一首。

活动开着时（国服一般周五 12:00 到周日 23:59:59），用掉当天的「剩余奖励次数」（每天 3 次，4:00 刷新）：
  大厅左栏「协同作战」→「开始匹配」→ 难度框只选「普通」（支援自动战斗、打到目标自动结束）→「确认」
  →「已完成匹配」点「接受」→ 选人页点「准备」（默认打勾的是列表第一个 = 战斗力最高；作者「你先选」）
  → 自动战斗（11-01 实测约 30 秒打到第 9 阶）→ 结算页点「点击任意处进行下一步」→ 回协同作战页，次数减 1，打到 0 为止。
不做：挑战难度（手动、排名，「若未以正常方式参加挑战难度，可能会给予部分处罚」——绝不点）；建队伍 / 邀请；
废铁商店；次数用完后的无奖励场。
没开活动（大厅没有「协同作战」）或不在开放期（页面没有「开始匹配」）→ 跳过，不算未完成。
页面坐标都是 11-01 真机实测。
用法：nikke_coop.py [--max N]（最多打几局，默认打到剩余奖励次数为 0）
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-10-06-妮姬日常"))
from nikke_daily import Bot, log, norm, up  # noqa: E402
from nikke_daily import battle as xa_battle  # noqa: E402  11-02 暂停协议（没经幻想引擎运行时是替身）

ENTRY_REGION = (150, 650, 480, 1300)   # 大厅左栏「协同作战」字的范围（11-01 在 (304,803)）；图标在字上方约 100
NORMAL = (479, 1174)                   # 难度框「普通」（选中是蓝底，饱和度 192；没选中白底 1.5）
CHALLENGE = (1021, 1174)               # 难度框「挑战」：绝不点
NEXT = (751, 2112)                     # 结算页「点击任意处进行下一步」
MATCH_WAIT = 5 * 60                    # 排队配对最多等 5 分钟，超时点顶栏「取消」
ROUND_LIMIT = 9 * 60                   # 一局（开始匹配到回协同作战页）最多 9 分钟；11-01 实测约 2.5 分钟
SETTLE = 8                             # 接受后没开打就回到协同作战页：页面要稳定这么多秒才算配对被取消（中途会闪一下）


class Coop(Bot):
    # ---------- 认页面（都只看当前 self.items / self.img） ----------
    def has(self, key: str, region=None, exact: bool = False) -> bool:
        return self.find(key, exact=exact, region=region) is not None

    def entry(self):
        """大厅左栏「协同作战」字的位置（没开活动时没有）。"""
        return self.find("协同作战", region=ENTRY_REGION)

    def diff_dialog(self) -> bool:
        """难度框：「请选择匹配难度。」+ 普通 / 挑战。"""
        return self.has("请选择匹配难度") or (self.has("普通", exact=True, region=(300, 1080, 660, 1270))
                                         and self.has("挑战", exact=True, region=(840, 1080, 1200, 1270)))

    def normal_selected(self) -> bool:
        return self.sat(*NORMAL, 150, 50) > 100 and self.sat(*CHALLENGE, 150, 50) < 60

    def accept_dialog(self) -> bool:
        """「【协同作战】NORMAL 已完成匹配。」框（接受前后都在，接受后「接受」按钮消失）。"""
        return self.has("已完成匹配")

    def accept_btn(self):
        return self.find("接受", exact=True, region=(850, 1550, 1300, 1730))

    def queue_bar(self) -> bool:
        """顶栏排队条：「连线中」，或计时「00:00:07」+ 右上「取消」。"""
        if self.has("连线中", region=(700, 150, 1300, 340)):
            return True
        timer = any(re.fullmatch(r"\d\d:\d\d:\d\d", t.strip()) for t, x, y in self.items if 850 < x < 1300 and 240 < y < 340)
        return timer or self.has("取消", exact=True, region=(1250, 220, 1480, 330))

    def pick_page(self) -> bool:
        """选人页：左上「WEAK POINT」+ 自己那位上方的「MY UNIT」（OCR 读成 MYUNIY；11-01 21:16 排第 1 位时只读出「UNIY」，
        当时没认出、没点准备，倒数完游戏用默认那个照样开打），或者底下的「准备」/「取消」。"""
        weak = any("WEAK" in up(t) for t, x, y in self.items if y < 450)
        mine = any("UNI" in up(t) for t, x, y in self.items if 420 < y < 560)
        return weak and (mine or self.ready_btn() is not None or self.readied())

    def ready_btn(self):
        return self.find("准备", exact=True, region=(550, 2190, 950, 2340))

    def readied(self) -> bool:
        """点完「准备」同一位置变「取消」（这个「取消」不能点，点了就取消准备）。"""
        return self.has("取消", exact=True, region=(550, 2190, 950, 2340))

    def battle(self) -> bool:
        """战斗中：左上「AUTO」（OCR 有时读成 AUTD）+ 右上倒计时「01:55」。"""
        auto = any(up(t).startswith("AUT") for t, x, y in self.items if x < 450 and y < 170)
        timer = any(re.fullmatch(r"0\d:\d\d", t.strip()) for t, x, y in self.items if x > 1150 and y < 170)
        return auto and timer

    def coop_loading(self) -> bool:
        """读条：日常的 loading() 只认 LOADING / LOADINE；这里的花体字还会读成 LOAUING、LOADIN6（11-01 录屏）。"""
        return self.loading() or any(up(t).startswith("LOA") for t, x, y in self.items if 1150 < y < 1400)

    def result_page(self) -> bool:
        return self.has("总伤害") and self.has("点击任意处进行下一步")

    def coop_page(self) -> bool:
        """协同作战页本身（上面没盖难度框 / 匹配框 / 排队条）。"""
        return (self.has("开始匹配", region=(900, 1780, 1400, 1900)) and self.has("奖励次", region=(900, 1880, 1400, 1950))
                and not self.diff_dialog() and not self.accept_dialog() and not self.queue_bar())

    def left(self) -> int | None:
        """「剩余奖励次数 2/3」→ 2。整屏读不出就裁那一块再认一次。"""
        for t, x, y in self.items:
            if "奖励次" in norm(t) and 900 < x < 1400 and 1880 < y < 1950:
                m = re.search(r"(\d)\s*/\s*(\d)", t)
                if m:
                    return int(m.group(1))
        if self.img is None:
            return None
        sx, sy = self.w / 1504, self.h / 2400
        for t, x, y in self.ocr_img(self.img[int(1885 * sy):int(1945 * sy), int(950 * sx):int(1350 * sx)]):
            m = re.search(r"(\d)\s*/\s*(\d)", t)
            if m:
                return int(m.group(1))
        return None

    def damage(self) -> str | None:
        for t, x, y in self.items:
            if re.fullmatch(r"\d{1,3}(,\d{3})+", t.strip()) and y > 1250:
                return t.strip()
        return None

    # ---------- 进协同作战页 ----------
    def open_coop(self) -> str:
        """返回 'ok' / 'closed'（大厅没入口 = 没开活动）/ 'off'（页面没有开始匹配 = 不在开放期）/ 其他 = 失败原因。"""
        self.ocr()
        pos = self.entry()
        if not pos:
            return "closed"
        icon = (pos[0], pos[1] - 100)          # 单人突击 10-19：点字不一定开，点字上方的图标
        log(f"  点「协同作战」图标 @{icon}")
        k = self.open(icon, ("开始匹配", "剩余奖励次数", "剩余时间", "排名列表"), timeout=15, wait=3)
        if not k:
            return "点了协同作战图标没进到协同作战页（截图已存）"
        time.sleep(1.5)
        self.ocr()
        if self.coop_page():
            return "ok"
        if self.has("开始匹配"):
            time.sleep(2)
            self.ocr()
            if self.coop_page():
                return "ok"
        self.snap("coop_off")
        return "off"

    # ---------- 打一局 ----------
    def pick_normal(self) -> bool:
        """难度框里点「普通」并核对它是选中的（挑战没选中）才返回 True。"""
        for _ in range(2):
            log(f"  难度框：点「普通」@{NORMAL}")
            self.tap(*NORMAL, wait=1.2)
            self.ocr()
            if self.diff_dialog() and self.normal_selected():
                return True
        self.snap("coop_diff")
        return False

    @xa_battle("正在协同作战（匹配和战斗中）")     # 11-02 作者：战斗不能暂停；在线配对停了也会被取消
    def coop_round(self, n: int, before: int) -> str:
        """打一局。返回 'ok'；'requeue'（配对被取消，回到了协同作战页）；其他 = 未完成原因。"""
        pos = self.find_btn("开始匹配", region=(900, 1780, 1400, 1900))
        if not pos:
            self.snap("coop_nostart")
            return "没找到「开始匹配」"
        log(f"  第 {n} 局：点「开始匹配」@{pos}（剩余奖励次数 {before}）")
        self.tap(*pos, wait=2)
        k, _ = self.wait_any(("请选择匹配难度", "普通"), timeout=8)
        if not k or not self.diff_dialog():
            self.snap("coop_nodiff")
            return "点开始匹配后没出难度框（截图已存）"
        if not self.pick_normal():
            return "难度框里没确认选上「普通」，没开始（挑战难度不打，截图已存）"
        conf = self.find("确认", exact=True, region=(500, 1560, 1000, 1690))
        if not conf:
            self.snap("coop_noconfirm")
            return "难度框里没找到「确认」（截图已存）"
        log(f"  难度「普通」已选中，点「确认」@{conf}")
        self.tap(*conf, wait=1.5)

        t0 = time.time()
        queue_since = None
        page_since = None
        accepted = readied = fought = False
        result = None
        unknown = 0
        while time.time() - t0 < ROUND_LIMIT:
            self.ocr()
            if self.leave_money_screen():
                continue
            if self.result_page():
                if result is None:
                    result = self.damage() or "?"
                    log(f"  结算：总伤害 {result}，点「点击任意处进行下一步」")
                self.tap(*NEXT, wait=3)
                continue
            if self.accept_dialog():
                page_since = None
                btn = self.accept_btn()
                if btn:
                    log(f"  已完成匹配：点「接受」@{btn}")
                    self.tap(*btn, wait=1.2)
                    accepted = True
                else:
                    time.sleep(1)       # 接受过了，等其他人
                continue
            if self.pick_page():
                page_since = None
                if not readied:
                    btn = self.ready_btn()
                    if btn:
                        log(f"  选人页：用默认打勾的妮姬，点「准备」@{btn}")
                        self.tap(*btn, wait=1.2)
                        self.ocr()
                        readied = self.readied()
                        if not readied:
                            log("  点了「准备」没变「取消」，下一轮再看")
                    elif self.readied():
                        readied = True
                time.sleep(1)
                continue
            if self.battle():
                page_since = None
                if not fought:
                    log("  进战斗（普通难度自动战斗），等打完")
                    fought = True
                time.sleep(2)
                continue
            if self.coop_loading() or any("MISSIONCLEAR" in up(t) for t, _, _ in self.items):
                time.sleep(1.5)
                continue
            if self.queue_bar():
                page_since = None
                if queue_since is None:
                    queue_since = time.time()
                    log("  排队配对中…")
                if time.time() - queue_since > MATCH_WAIT:
                    c = self.find("取消", exact=True, region=(1250, 220, 1480, 330))
                    if c:
                        log(f"  配对等了 {MATCH_WAIT // 60} 分钟没配上，点顶栏「取消」@{c}")
                        self.tap(*c, wait=2)
                    self.snap("coop_queue_timeout")
                    return f"配对等了 {MATCH_WAIT // 60} 分钟没配上，已取消"
                time.sleep(1.5)
                continue
            if self.coop_page():
                now = self.left()
                if result is not None:
                    if now is not None and now < before:
                        log(f"  回到协同作战页：剩余奖励次数 {before} → {now}")
                        return "ok"
                    time.sleep(2)
                    self.ocr()
                    now = self.left()
                    if now is not None and now < before:
                        log(f"  回到协同作战页：剩余奖励次数 {before} → {now}")
                        return "ok"
                    self.snap("coop_left_same")
                    return f"打完一局剩余奖励次数没减（{before} → {now}，截图已存）"
                if page_since is None:
                    page_since = time.time()
                elif time.time() - page_since >= SETTLE:
                    if fought:
                        self.snap("coop_noresult")
                        return "打完回到协同作战页但没见到结算页（截图已存）"
                    return "requeue"
                time.sleep(1.5)
                continue
            page_since = None
            unknown += 1
            if unknown in (8, 30):
                self.snap("coop_unknown")
            if self.dismiss_tip():
                continue
            time.sleep(1.5)
        self.snap("coop_round_timeout")
        return f"一局 {ROUND_LIMIT // 60} 分钟还没回到协同作战页（截图已存）"

    # ---------- 步骤 ----------
    def step_enter(self) -> None:
        self.coop_state = self.open_coop()
        if self.coop_state == "closed":
            log("  大厅没有「协同作战」入口：现在没开活动（国服一般周五 12:00 到周日 23:59:59），跳过")
        elif self.coop_state == "off":
            log("  协同作战页上没有「开始匹配」：不在开放期（多半是奖励兑换 / 排名结算期间），跳过")
        elif self.coop_state != "ok":
            self.note = self.coop_state

    def step_fight(self, max_rounds: int) -> None:
        st = getattr(self, "coop_state", None)
        if st != "ok":
            if st in ("closed", "off"):
                log("  没开活动，不打")
            else:
                self.note = "没进到协同作战页"
            return
        self.ocr()
        left = self.left()
        if left is None:
            self.snap("coop_noleft")
            self.note = "读不出剩余奖励次数（截图已存）"
            return
        log(f"  剩余奖励次数 {left}/3")
        played = requeues = 0
        while left > 0 and played < max_rounds:
            r = self.coop_round(played + 1, left)
            if r == "requeue":
                requeues += 1
                if requeues > 3:
                    self.note = "配对连着 4 次被取消（有人没接受），停"
                    break
                log("  配对被取消（多半有人没接受），重新开始匹配")
                continue
            if r != "ok":
                self.note = r
                break
            played += 1
            self.ocr()
            left = self.left()
            if left is None:
                self.note = "打完一局后读不出剩余奖励次数"
                break
        log(f"  协同作战：打了 {played} 局，剩余奖励次数 {left}")


STEPS = ["enter", "fight"]
NAMES = {"enter": "进协同作战", "fight": "普通难度：打完今天的奖励次数"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3, help="最多打几局（默认 3 = 一天的奖励次数）")
    args = ap.parse_args()
    bot = Coop()
    if not bot.ensure_game(False):
        log("完成：" + "，".join(f"{NAMES[k]}={bot.start_fail or '没进到游戏'}" for k in STEPS))
        return 1
    result = {}
    for s in STEPS:
        log(f"== {NAMES[s]}")
        bot.note = None
        try:
            if s == "enter" and not bot.to_lobby():
                raise RuntimeError("回不到大厅")
            if s == "enter":
                bot.step_enter()
            else:
                bot.step_fight(args.max)
            result[s] = bot.note or "ok"
        except Exception:
            traceback.print_exc()
            result[s] = "error"
            log(f"  {NAMES[s]} 出错，跳过")
    bot.to_lobby()
    log("完成：" + "，".join(f"{NAMES[k]}={v}" for k, v in result.items()))
    return 0 if all(v == "ok" for v in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
