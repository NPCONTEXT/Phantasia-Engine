"""妮姬（胜利女神：新的希望，国服 com.tencent.nikke）日常一键（幻想引擎调用）。

竖屏，坐标基准 1504x2400（设备原生）。OCR 找字为主，图标按钮用实测坐标；每步失败只记日志并跳过。
游戏每天 4:00 刷新。任何花珠宝/付费的按钮一律不点（见 FORBID）。
用法：nikke_daily.py [--only 步骤1,步骤2,...] [--restart]
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
OCR_BIN = AGG / "bh3_auto/ocr/ocr"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = ["/opt/homebrew/bin/adb", "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
PKG = "com.tencent.nikke"
RECRUIT_NEXT = (1446, 1248)  # 招募页右侧「»」翻到下一个卡池（10-09 实测，共 4 个：新角色 / 上期特殊 / 普通 / 社交点数）
BASE_W, BASE_H = 1504, 2400
TMP = Path("/tmp/nikke")
TMP.mkdir(exist_ok=True)

# 这些字所在的按钮绝不点（花珠宝 / 付费 / 用掉活动道具）
FORBID = ("购买PASS", "补充签到", "获取3份物资", "招募10名", "珠宝商店", "付费商店")

HOME = (370, 2262)        # 左下「主页」小房子（前哨/方舟/商店/活动等页面都有）
RETURN = (195, 2265)      # 左下「返回」


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def norm(t: str) -> str:
    """中文比较用：只留汉字、数字和少量符号。"""
    return re.sub(r"[^一-鿿0-9/%×\-]", "", t)


def up(t: str) -> str:
    """英文比较用：去空格转大写。"""
    return re.sub(r"\s+", "", t).upper()


def mac_notify(title: str, body: str) -> None:
    """弹一条 macOS 系统通知（照幻想引擎 ui/device.py 的写法，失败就算了）。"""
    def esc(x: str) -> str:
        return x.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    try:
        subprocess.run(["/usr/bin/osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def has_ascii(s: str) -> bool:
    return bool(re.search(r"[A-Za-z]", s))


class Bot:
    def __init__(self) -> None:
        self.w, self.h = BASE_W, BASE_H
        self.img = None
        self.items: list[tuple[str, int, int]] = []
        self.note: str | None = None

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
        p = TMP / "cur.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        out = subprocess.run([str(OCR_BIN), str(p)], capture_output=True, text=True, timeout=30).stdout
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
        self._prev_tap = (x, y)
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, ms: int = 600, wait: float = 1.2) -> None:
        a, b = self._xy(x1, y1), self._xy(x2, y2)
        self.shell(f"input swipe {a[0]} {a[1]} {b[0]} {b[1]} {ms}", timeout=ms / 1000 + 15)
        time.sleep(wait)

    @staticmethod
    def _hit(t: str, key: str, exact: bool) -> bool:
        if has_ascii(key):
            a, b = up(t), up(key)
        else:
            a, b = norm(t), norm(key)
        return (a == b) if exact else (b in a)

    def find(self, key: str, exact: bool = False, region=None, fresh: bool = False):
        items = self.ocr() if fresh else self.items
        for t, x, y in items:
            if self._hit(t, key, exact) and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
                return x, y
        return None

    def find_crop(self, key: str, region=(0, 1600, 1504, 2400), exact: bool = False):
        """整屏 OCR 漏读时（例如橙色按钮上的白字），裁下这块区域再认一次。用当前 self.img。"""
        x1, y1, x2, y2 = region
        sx, sy = self.w / BASE_W, self.h / BASE_H
        roi = self.img[int(y1 * sy):int(y2 * sy), int(x1 * sx):int(x2 * sx)]
        for t, x, y in self.ocr_img(roi):
            if self._hit(t, key, exact):
                return x1 + int(x / sx), y1 + int(y / sy)
        return None

    def find_btn(self, key: str, region=(0, 1600, 1504, 2400), fresh: bool = False):
        pos = self.find(key, region=region, fresh=fresh)
        return pos or self.find_crop(key, region)

    def find_all(self, key: str, exact: bool = False, region=None):
        return [(x, y) for t, x, y in self.items
                if self._hit(t, key, exact) and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3]))]

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
            time.sleep(0.6)

    def snap(self, tag: str) -> None:
        if self.img is not None:
            p = TMP / f"{tag}_{time.strftime('%m%d-%H%M%S')}.png"
            cv2.imwrite(str(p), self.img)
            log(f"  截图 {p}")

    def sat(self, x: int, y: int, rw: int = 120, rh: int = 25) -> float:
        """(x,y) 附近的平均饱和度：按钮可点时是彩色（橙/蓝），灰掉时很低。"""
        sx, sy = self.w / BASE_W, self.h / BASE_H
        roi = self.img[int((y - rh) * sy):int((y + rh) * sy), int((x - rw) * sx):int((x + rw) * sx)]
        if roi.size == 0:
            return 0.0
        return float(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[..., 1].mean())

    # ---------- 弹窗 ----------
    def find_white_x(self, x1: int = 1150, y1: int = 90, x2: int = 1440, y2: int = 460, loose: bool = False):
        """右上区域找白色 X（登录活动弹窗/防御/邮箱/好友/任务/通行证实测都能找到，无弹窗页面零误报）。"""
        sx, sy = self.w / BASE_W, self.h / BASE_H
        roi = self.img[int(y1 * sy):int(y2 * sy), int(x1 * sx):int(x2 * sx)]
        if sx != 1 or sy != 1:
            roi = cv2.resize(roi, (x2 - x1, y2 - y1))
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        s = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[..., 1]
        # 先严格（纯白），找不到再宽松（活动弹窗的浅蓝白 X，亮度约 210），宽松时要求更像 X
        for gt, st_, need in ((225, 40, 0.7), (200, 80, 0.8)):
            m = ((g > gt) & (s < st_)).astype(np.uint8) * 255
            n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
            best = None
            for i in range(1, n):
                x, y, w, h, a = st[i]
                if not (30 <= w <= 90 and 30 <= h <= 90 and 0.75 <= w / h <= 1.33):
                    continue
                if not (0.10 <= a / (w * h) <= 0.45):
                    continue
                comp = lab[y:y + h, x:x + w] == i
                k = min(w, h)
                d1 = np.mean([comp[int(t * (h - 1) / (k - 1)), int(t * (w - 1) / (k - 1))] for t in range(k)])
                d2 = np.mean([comp[int(t * (h - 1) / (k - 1)), int((k - 1 - t) * (w - 1) / (k - 1))] for t in range(k)])
                if d1 > need and d2 > need and (best is None or d1 + d2 > best[0]):
                    best = (d1 + d2, x1 + x + w // 2, y1 + y + h // 2)
            if best:
                return best[1], best[2]
        # 11-19：X 碰着背景里的白云，和云连成一大块，上面按连通块找的两遍都认不出（累计登录活动「LIFESTYLE」弹窗，
        # 每天白等 80 秒）。第三遍不看连通块：两条对角线几乎全白、中心白、上下左右四个夹角至少三个不白，才算 X。
        # 只在调用方认出是累计登录活动弹窗时才走（loose）：单突结算页背景的白格栅交叉也会被它认成 X（66 张截图里 1 张）。
        if not loose:
            return None
        m = ((g > 200) & (s < 80)).astype(np.float32)
        H, W = m.shape
        for k in (80, 70, 60, 52, 44, 36):
            eye = np.eye(k, dtype=np.float32) / k
            d1 = cv2.filter2D(m, -1, eye, borderType=cv2.BORDER_CONSTANT)
            d2 = cv2.filter2D(m, -1, np.fliplr(eye).copy(), borderType=cv2.BORDER_CONSTANT)
            ys, xs = np.nonzero((d1 > 0.85) & (d2 > 0.85) & (m > 0))
            o = int(k * 0.3)
            best = None
            for y, x in zip(ys, xs):
                gaps = [(y - o, x), (y + o, x), (y, x - o), (y, x + o)]
                dark = sum(1 for yy, xx in gaps if 0 <= yy < H and 0 <= xx < W and m[yy, xx] == 0)
                if dark >= 3 and (best is None or d1[y, x] + d2[y, x] > best[0]):
                    best = (d1[y, x] + d2[y, x], x1 + int(x), y1 + int(y))
            if best:
                return best[1], best[2]
        return None

    _prev_tap = None
    _same_x = 0

    def _tap_x(self, pos, how: str) -> bool:
        """中间没点过别的、连续两次点同一个 X 它还在，说明被别的层挡住了，不再点它。"""
        prev = self._prev_tap
        if prev and abs(pos[0] - prev[0]) < 20 and abs(pos[1] - prev[1]) < 20:
            self._same_x += 1
        else:
            self._same_x = 0
        if self._same_x >= 2:
            return False
        log(f"  关闭 X{how} @({int(pos[0])},{int(pos[1])})")
        self.tap(*pos, wait=1.5)
        return True

    def close_x(self) -> bool:
        """关右上角 X：先找 OCR 读到的 X（排除顶部正中红魔插件那个），再找白色 X 图形。用当前 self.items/self.img。"""
        for t, x, y in self.items:
            if t.strip() in ("X", "×", "x", "✕") and x > 1150 and 100 < y < 1300:
                return self._tap_x((x, y), "")
        pos = self.find_white_x(loose=any("累计登入天数" in t for t, _, _ in self.items))
        if pos:
            return self._tap_x(pos, "(图形)")
        return False

    def close_notice(self) -> bool:
        """启动时的「公告」窗（实测标题 (784,529)、X (1272,522)）。"""
        pos = self.find("公告", exact=True, region=(550, 300, 1000, 900))
        if not pos:
            return False
        y = pos[1]
        xy = self.find_white_x(1150, y - 90, 1440, y + 90) or (1272, y - 7)
        log(f"  关公告 @{xy}")
        self.tap(*xy, wait=2)
        return True

    def dismiss_tip(self) -> bool:
        """「点击领取奖励」「点击屏幕继续」类提示：点那行字本身；获得新角色等演出：点右上「跳过」。"""
        for t, x, y in self.items:
            if (norm(t).endswith("跳过") or up(t).endswith("SKIP")) and y < 300 and x > 1100:
                log(f"  跳过演出 @({x},{y})")
                self.tap(x, y, wait=2.5)
                return True
        for key in ("模拟结束",):     # 各种结算层上的结束按钮
            pos = self.find(key, exact=True)
            if pos:
                log(f"  点「{key}」@{pos}")
                self.tap(*pos, wait=2.5)
                return True
        if any("RECRUITMENTRESULT" in up(t) for t, _, _ in self.items):
            cands = [(x, y) for x, y in self.find_all("确认", exact=True) if y > 1900]
            if cands:
                log(f"  招募结果页「确认」@{cands[0]}")
                self.tap(*cands[0], wait=2.5)
                return True
        for t, x, y in self.items:
            n = norm(t)
            if n.startswith("点击") and y > 1000 and len(n) <= 12:
                log(f"  关提示「{t}」@({x},{y})")
                self.tap(x, y, wait=1.5)
                return True
            if up(t) in ("TOUCHTOCONTINUE", "TAPTOCONTINUE"):
                self.tap(x, y, wait=1.5)
                return True
        return False

    def dismiss_rewards(self, rounds: int = 4) -> None:
        """领取后的奖励层（可能叠几层），一直点到没有提示为止。"""
        for _ in range(rounds):
            time.sleep(0.8)
            self.ocr()
            if not self.dismiss_tip():
                return

    # ---------- 导航 ----------
    LOBBY_MARKS = (("前哨基地", (200, 1800, 520, 1960)), ("方舟", (1150, 1550, 1450, 1700)),
                   ("作战出击", (1100, 1800, 1460, 1950)), ("队员招募", (1100, 2250, 1360, 2360)),
                   ("商店", (200, 1580, 460, 1700)))

    def is_lobby(self, fresh: bool = True) -> bool:
        if fresh:
            self.ocr()
        n = sum(1 for k, r in self.LOBBY_MARKS if self.find(k, region=r))
        return n >= 4

    def loading(self) -> bool:
        return any("LOADING" in up(t) or "LOADINE" in up(t) for t, _, _ in self.items)

    # ---------- 付费界面（10-16） ----------
    # 指挥官升级后回大厅会弹「FLASH SALE」限时礼包（¥6/¥30/¥68 三档，约 2 小时），点「点击关闭画面。」那行字关不掉；
    # 卡在这页时按固定坐标点大厅入口，前哨基地 (353,1865) 正好落在 ¥6 按钮 (333,1891) 上，弹出「购买商品」框（10-16 22:58 实拍）。
    # 规矩：认出付费界面就只点「取消」或关掉，不做别的点击；open() 点固定坐标前先识字看一眼。
    def money_screen(self) -> str | None:
        """当前 self.items 是付费界面吗：'buy' = 购买确认框，'flash' = 限时礼包页，None = 不是。¥ 只看原文（norm 会去掉）。"""
        prices = sum(1 for t, _, _ in self.items if t.strip()[:1] in ("¥", "￥"))
        names = [norm(t) for t, _, _ in self.items]
        if any(n.startswith("购买商品") for n in names) or (prices and "取消" in names and "购买" in names):
            return "buy"
        ups = [up(t) for t, _, _ in self.items]
        if any("选择一个购买" in n for n in names) or ("FLASH" in ups and "SALE" in ups) or prices >= 2:
            return "flash"
        return None

    def leave_money_screen(self) -> bool:
        """在付费界面上就离开，返回 True；不是付费界面返回 False。只用当前 self.items，只点「取消」「返回」，或重启游戏。
        10-16 实测：返回键在付费商店页无效；付费商店页点左下「返回」回大厅；升级自动弹的礼包点「点击关闭画面。」关不掉，
        重启游戏后不再弹（礼包挪进大厅「付费商店」，倒计时照走）。"""
        kind = self.money_screen()
        if not kind:
            return False
        self.money_hits = getattr(self, "money_hits", 0) + 1
        if self.money_hits == 1 or self.money_hits % 10 == 0:
            self.snap(f"money_{kind}")
        if kind == "buy":
            pos = self.find("取消", exact=True, region=(150, 1700, 1000, 2200))
            if pos:
                log(f"  付费确认框：点「取消」@{pos}（不买）")
                self.tap(*pos, wait=2)
            else:
                log("  付费确认框：没认到「取消」，不点，等 2 秒再看")
                time.sleep(2)
            return True
        if self.find("返回", region=(80, 2200, 330, 2330)):
            log("  付费商店页：点左下「返回」")
            self.tap(*RETURN, wait=3)
            return True
        if not getattr(self, "money_restarted", False):
            self.money_restarted = True
            log("  升级限时礼包关不掉：重启游戏（重启后不再弹，礼包挪进大厅「付费商店」）")
            if not self.ensure_game(restart=True):
                log(f"  重启没进到游戏：{self.start_fail}")
            return True
        log("  重启过还在限时礼包页：不点，等 2 秒再看")
        time.sleep(2)
        return True

    def to_lobby(self, rounds: int = 12) -> bool:
        tried_home = False
        # 11-18：同一处「关闭」点两次画面都没变 = 不是关闭按钮，往下走主页 / 返回。
        # 无限之塔选塔页上没开的塔标着「关闭」（状态字），11-17 连点 12 次回不到大厅，后面每步都跳过。留两次是给真弹窗第一下没点上（无线慢、动画没完）的机会。
        close_taps: list[list] = []          # [[x, y, 点过几次], …]
        for _ in range(rounds):
            if self.is_lobby():
                return True
            if self.loading():
                time.sleep(2)
                continue
            if self.leave_money_screen():
                continue
            if self.dismiss_tip():
                continue
            if self.close_x():
                continue
            c = self.find("关闭", exact=True, region=(0, 1200, 1504, 2350))
            if c:
                rec = next((r for r in close_taps if abs(r[0] - c[0]) <= 30 and abs(r[1] - c[1]) <= 30), None)
                if rec is None:
                    rec = [c[0], c[1], 0]
                    close_taps.append(rec)
                if rec[2] < 2:
                    rec[2] += 1
                    log(f"  回大厅：点「关闭」@{c}")
                    self.tap(*c, wait=2)
                    continue
                if rec[2] == 2:
                    rec[2] += 1
                    log(f"  回大厅：「关闭」@{c} 点了两次没反应，不是按钮（比如没开的塔），改点主页 / 返回")
            if self.find("大厅", region=(650, 2270, 850, 2350)):
                log("  回大厅：点底栏「大厅」")
                self.tap(752, 2290, wait=4)
                continue
            if self.find("返回", region=(80, 2200, 330, 2330)):
                if tried_home:          # 有的页面（咨询等）没有主页小房子，改点「返回」
                    log("  回大厅：点「返回」")
                    self.tap(*RETURN, wait=3)
                else:
                    log("  回大厅：点主页")
                    self.tap(*HOME, wait=5)
                tried_home = not tried_home
                continue
            log("  回大厅：认不出页面，等 2 秒")
            time.sleep(2)
        ok = self.is_lobby()
        if not ok:
            self.snap("not_lobby")
        return ok

    START_LIMIT = 10 * 60  # 启动到标题页最多等多久（作者定 10 分钟；等登录的时间另算）
    PROG_STALL = 60        # 超时后进度字样这么久没变才算卡住（还在下载就接着等）

    def ensure_game(self, restart: bool = False) -> bool:
        """启动到大厅。作者：「以目前加载完成页为准，有再点」——只在认得的页面上点（标题页、屏幕中间公告窗的 X），
        加载 / 更新资源 / 不认识的画面只等不点；没见过标题页就一直等，见过标题页之后的不认识画面（多半是奖励弹窗）才交给登录弹窗步骤。
        掉登录：等用户手动登录（另算 10 分钟）。更新资源确认框：发通知等用户点。
        10 分钟没进到标题页（且进度也不动了）算卡住：发通知、返回 False（start_fail 写原因）。"""
        self.start_fail = ""
        if restart:
            log("重启游戏（让登录弹窗重新出现）")
            self.shell(f"am force-stop {PKG}")
            time.sleep(2)
        focus = self.shell("dumpsys window | grep mCurrentFocus").decode(errors="ignore")
        launched = PKG not in focus
        if launched:
            log("启动游戏")
            self.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
            time.sleep(20)
        # 游戏本来就开着（单独播放不关游戏、可能停在任意内页）：当作已过标题页，不认识的画面直接交给后面步骤回大厅
        start, seen_title, update_told = time.time(), not launched, False
        prog, prog_t = None, time.time()
        while True:
            self.ocr()
            if self.is_lobby(fresh=False):
                return True
            p = self.progress_text()
            if p != prog:
                prog, prog_t = p, time.time()
            if self.loading():
                pass
            elif self.close_notice():
                continue
            elif self.login_page():
                t0 = time.time()
                if not self.wait_login():
                    self.start_fail = "账号没登录"
                    return False
                start += time.time() - t0  # 等登录的时间不算启动时限
                continue
            elif self.title_page():
                seen_title = True
                log("  标题页，点击进入游戏")
                self.tap(752, 1800, wait=6)
                continue
            elif self.update_prompt():
                if not update_told:
                    update_told = True
                    log("  游戏要更新资源（确认框）：请在设备上确认，脚本不点")
                    mac_notify("妮姬 · 日常：要更新资源", "游戏弹了资源更新确认框，请在设备上确认；脚本不点")
            elif seen_title:
                return True
            # 只等不点。超过时限、且进度字样 60 秒没变（还在下载就接着等）才算卡住
            if time.time() - start > self.START_LIMIT and time.time() - prog_t > self.PROG_STALL:
                break
            time.sleep(3)
        self.snap("start_stuck")
        log(f"  启动 {self.START_LIMIT // 60} 分钟还没进到标题页（截图已存），停止")
        mac_notify("妮姬 · 日常：启动卡住了", f"{self.START_LIMIT // 60} 分钟没进到标题页，这次日常没跑")
        self.start_fail = "启动卡住"
        return False

    def progress_text(self) -> str:
        """下载 / 加载进度字样（百分比、MB/KB），用来判断「还在动」。"""
        return "|".join(t for t, _, y in self.items if y < 2150 and re.search(r"\d\s*%|\d\s*[MK]B", up(t)))

    def update_prompt(self) -> bool:
        """资源更新 / 下载确认框（还没见过实物，按字认，只用来发通知、不点）。"""
        txt = [(t.replace(" ", ""), y) for t, _, y in self.items if y < 2150]
        return any(k in t for t, _ in txt for k in ("资源更新", "更新资源", "是否下载", "下载资源", "补丁")) or \
            (any("下载" in t for t, _ in txt) and any(k in t for t, _ in txt for k in ("确认", "确定")))

    def title_page(self) -> bool:
        """标题页「点击任意处进入游戏」。只看底部法律文字以上（y<2150）：防沉迷那段里有「后方可进入游戏」，
        10-09 登录页因此被当成标题页，在两个登录按钮中间 (752,1800) 连点了 5 次。
        重新登录后的标题页写的是「点击开始」（左栏多了「登出」，不碰）。"""
        r = (0, 0, 1504, 2150)
        return any(k in up(t) for t, _, y in self.items if y < 2150 for k in ("TOUCHTOSTART", "TAPTOSTART")) or \
            any(self.find(k, region=r) for k in ("点击任意", "进入游戏", "点击屏幕", "开始游戏", "点击开始"))

    def login_page(self) -> bool:
        """掉登录的登录页（10-09 实测）：「与微信好友玩」「与QQ好友玩」两个按钮 + 下面「开始登录」，三样见到两样就算。
        登录由用户自己来，脚本绝不点这几个按钮。看原文不走 find（norm 会把 QQ 去掉）。
        点了 QQ 之后的「账号密码登录」页也算登录页（等用户输密码）。"""
        txt = [t.replace(" ", "") for t, _, _ in self.items]
        marks = (any("微信好友" in t for t in txt), any("QQ好友" in up(t) for t in txt), any("开始登录" in t for t in txt))
        # 点了 QQ 之后的游戏内 QQ 账号密码页（10-09 实测）也算「正在登录」
        qq_form = any(k in t for t in txt for k in ("账号密码登录", "QQ密码", "服务协议和隐私政策"))
        return sum(marks) >= 2 or qq_form

    def wait_login(self, minutes: int = 10) -> bool:
        """账号掉登录：日志 + 系统通知叫用户手动登录，每 5 秒看一次，最多等 minutes 分钟。
        用户切去微信/QQ 确认时游戏不在前台，照样等；回到游戏、离开登录页、看到大厅/标题/加载/公告窗才算登好。"""
        log(f"  账号没登录（登录页）：请在设备上手动登录，最多等 {minutes} 分钟；脚本不点登录按钮")
        if not getattr(self, "_login_notified", False):
            self._login_notified = True
            mac_notify("妮姬 · 日常：账号掉登录了", f"请在设备上手动登录，最多等 {minutes} 分钟")
        end, last = time.time() + minutes * 60, time.time()
        while time.time() < end:
            time.sleep(5)
            focus = self.shell("dumpsys window | grep mCurrentFocus").decode(errors="ignore")
            if PKG not in focus:
                continue  # 在微信/QQ 里确认登录
            self.ocr()
            # 登好的样子：大厅 / 标题页「点击任意处」/ 加载 / 屏幕中间的公告窗。左栏「公告」按钮登录前也有，不算
            # （10-09 实测：点 QQ 后登录按钮先消失、只剩带「公告」的过渡画面，曾被当成登好了）
            if not self.login_page() and (self.is_lobby(fresh=False) or self.title_page() or self.loading()
                                          or self.find("公告", exact=True, region=(550, 300, 1000, 900))):
                log("  已登录，接着进游戏")
                return True
            if time.time() - last >= 60:
                last = time.time()
                log(f"  还没登录，接着等（{max(0, int((end - time.time()) / 60))} 分钟后放弃）")
        log(f"  等了 {minutes} 分钟还没登录，停止")
        mac_notify("妮姬 · 日常：没等到登录", f"等了 {minutes} 分钟，这次日常没跑")
        return False

    def open(self, xy, marks, timeout: float = 12, wait: float = 2.0, region=None) -> str | None:
        for _ in range(3):                      # 10-16：付费界面上不点固定坐标（先离开，离不开就不点）
            self.ocr()
            if not self.leave_money_screen():
                break
        else:
            log(f"  付费界面关不掉，不点 {xy}")
            self.snap("open_fail")
            return None
        self.tap(*xy, wait=wait)
        k, _ = self.wait_any(marks, timeout=timeout, region=region)
        if not k:
            self.snap("open_fail")
        return k

    def claim_all(self, region=None, label: str = "全部领取") -> bool:
        """点「全部领取」直到它变灰（任务页第一次领的是点数，第二次才领宝箱），最多 3 次。"""
        got = False
        for _ in range(3):
            self.ocr()
            pos = self.find_btn(label, region=region or (0, 1600, 1504, 2400))
            if not pos:
                return got
            if self.sat(*pos) < 35:
                if not got:
                    log(f"  「{label}」是灰的，没有可领的")
                return got
            log(f"  点「{label}」@{pos}")
            self.tap(*pos, wait=2)
            self.dismiss_rewards()
            got = True
        return got

    # ---------- 1 登录弹窗 ----------
    def step_popups(self) -> None:
        """处理大厅上的登录弹窗：领「全部领取」、跳过演出、关提示、关 X；在大厅连续 8 秒没有新弹窗才算完（整段最多 90 秒）。"""
        n, quiet, deadline = 0, None, time.time() + 90
        while time.time() < deadline:
            self.ocr()
            if self.loading():
                time.sleep(2)
                continue
            lobby = self.is_lobby(fresh=False)
            acted = True
            pos = None if lobby else self.find_btn("全部领取", region=(200, 1700, 1450, 2350))
            if self.leave_money_screen():
                pass
            elif self.dismiss_tip():
                pass
            elif pos and self.sat(*pos) > 35:
                log(f"  弹窗「全部领取」@{pos}")
                self.tap(*pos, wait=2.5)
                n += 1
            elif self.close_x():
                pass
            elif not lobby and self.find("返回", region=(80, 2200, 330, 2330)):
                log("  不在大厅，点主页")
                self.tap(*HOME, wait=5)
            else:
                acted = False
                if not lobby:
                    log(f"  未知页面，没有可点的（{int(deadline - time.time())} 秒后放弃）")
            if acted:
                quiet = None
                continue
            if lobby:
                quiet = quiet or time.time()
                if time.time() - quiet >= 8:
                    break
            time.sleep(1.5)
        log(f"  弹窗领取 {n} 次")
        if not self.to_lobby():
            self.note = "弹窗没清完"
            return
        # 10-14 作者：限时活动的内容都去掉（新活动误点进剧情卡住过），不再去限时活动列表补领签到

    def red_dots(self, x1: int = 1340, y1: int = 480, x2: int = 1400, y2: int = 2250):
        """限时活动列表横幅右上角的红点（实心小圆，x≈1369，面积约 300）。"""
        roi = self.img[y1:y2, x1:x2].astype(int)
        b, g, r = roi[..., 0], roi[..., 1], roi[..., 2]
        m = ((r > 200) & (g < 110) & (b < 110) & (r - g > 110)).astype(np.uint8) * 255
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
        out = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if 200 <= a <= 520 and 14 <= w <= 30 and 14 <= h <= 30 and a / (w * h) > 0.6:
                out.append((x1 + int(cen[i][0]), y1 + int(cen[i][1])))
        return sorted(out, key=lambda p: p[1])

    def banner_at(self, dy: int):
        y1, y2 = max(0, dy + 20), min(BASE_H, dy + 400)
        return cv2.resize(cv2.cvtColor(self.img[y1:y2, 130:1380], cv2.COLOR_BGR2GRAY), (100, 30)).astype(np.float32)

    def open_event_list(self) -> bool:
        return bool(self.open((118, 950), ("限时活动列表",), timeout=10))

    def claim_event_signins(self) -> int:
        if not self.to_lobby() or not self.open_event_list():
            return 0
        done, got, scrolls = [], 0, 0
        for _ in range(14):
            self.ocr()
            if not self.find("限时活动列表"):
                if not self.to_lobby() or not self.open_event_list():
                    break
                scrolls = 0
                continue
            todo = None
            for dx, dy in self.red_dots():
                if dy > 1800:      # 横幅只露出一角，先滑上来再点
                    continue
                sig = self.banner_at(dy)
                if not any(self.same_sig(sig, d) for d in done):
                    todo = (dy, sig)
                    break
            if todo is None:
                if scrolls >= 5:
                    break
                self.swipe(750, 2000, 750, 1200, 800)
                scrolls += 1
                continue
            dy, sig = todo
            done.append(sig)
            log(f"  红点横幅 @(750,{dy + 200})")
            self.tap(750, dy + 200, wait=3)
            self.ocr()
            pos = self.find_btn("全部领取")
            if pos and self.sat(*pos) > 35:
                log(f"  「全部领取」@{pos}")
                self.tap(*pos, wait=2.5)
                self.dismiss_rewards()
                got += 1
            else:
                log("  这个没有可领的「全部领取」，跳过")
            self.ocr()
            if not self.find("限时活动列表") and not self.close_x():
                self.tap(*RETURN, wait=3)
        return got

    # ---------- 2 前哨基地：收获 + 歼灭 ----------
    def to_outpost(self) -> bool:
        k = self.open((353, 1865), ("指挥中心", "派遣公告栏", "基础核心"), timeout=25, wait=3)
        return bool(k)

    def step_outpost(self) -> None:
        if not self.to_outpost():
            self.note = "没进前哨基地"
            return
        marks = ("防御前哨基地", "获得奖励")
        if not self.open((1180, 2260), marks, timeout=8):
            # 10-10：进了前哨、点的位置也对，8 秒没开窗（前 3 次都正常，推断加载中点击被吞）。
            # 先多等 3 秒看是不是只是慢（开了就别再点，免得点到窗口里的东西），还没开再点一次
            if not self.wait_any(marks, timeout=3)[0]:
                log("  防御窗口没开，再点一次")
                if not self.open((1180, 2260), marks, timeout=8):
                    self.note = "没打开防御窗口：主页挂机奖励没领"
                    return
        # 收获
        pos = self.find("获得奖励", region=(800, 1950, 1350, 2150))
        if pos:
            log(f"  点「获得奖励」@{pos}")
            self.tap(*pos, wait=2.5)
            self.dismiss_rewards()
        # 歼灭：每天首次免费。按钮右上有红点 = 今天免费那次还没用；确认框里「进行歼灭」旁边没有价格数字才点
        self.ocr()
        pos = self.find("一举歼灭", region=(250, 1950, 800, 2150))
        if not pos:
            self.note = "没找到一举歼灭"
            return
        if not self.red_dots(690, 1950, 780, 2030):
            log("  「一举歼灭」没有红点，今天的免费次数已用，不点")
            return
        log(f"  点「一举歼灭」@{pos}")
        self.tap(*pos, wait=2.5)
        self.ocr()
        btn = self.find("进行歼灭", exact=True, region=(600, 1750, 1350, 1950))
        if not btn:
            self.snap("annihilate_popup")
            self.note = "歼灭确认框没认出「进行歼灭」"
            self.tap_text("取消", exact=True, fresh=False)
            return
        price = [t for t, x, y in self.items if 600 < x < 1350 and 1760 < y < 1930 and re.search(r"\d", t)]
        if price:
            log(f"  「进行歼灭」旁边有数字 {price}，可能要花珠宝，不点")
            self.tap_text("取消", exact=True, fresh=False)
            self.note = "歼灭要花珠宝，没做"
            return
        log(f"  免费歼灭「进行歼灭」@{btn}")
        self.tap(*btn, wait=3)
        self.dismiss_rewards()

    # ---------- 3 派遣 ----------
    def step_dispatch(self) -> None:
        k = self.open((575, 1900), ("派遣目录", "全部派遣", "重置派遣目录"), timeout=10, wait=2.5)
        if not k:   # 大厅入口没开，走前哨基地里的
            if not self.to_lobby() or not self.to_outpost() or not self.open((855, 2262), ("派遣目录", "全部派遣"), timeout=10):
                self.note = "没打开派遣公告栏"
                return
        self.claim_all(region=(950, 1900, 1400, 2100))
        pos = self.find("全部派遣", region=(500, 1900, 1000, 2100), fresh=True)
        if pos and self.sat(*pos) > 35:
            log(f"  点「全部派遣」@{pos}")
            self.tap(*pos, wait=2)
            self.ocr()
            # 可能弹出确认（派遣队员列表），点里面的「派遣」/「确认」
            for key in ("确认", "派遣"):
                cands = [(x, y) for x, y in self.find_all(key, exact=True) if y > 1500]
                if cands:
                    btn = max(cands, key=lambda p: p[1])
                    log(f"  确认派遣 @{btn}")
                    self.tap(*btn, wait=2)
                    break
            self.dismiss_rewards()
        else:
            log("  「全部派遣」是灰的或没找到")

    # ---------- 4 邮箱 ----------
    def step_mail(self) -> None:
        if not self.open((1325, 75), ("邮箱",), timeout=8, region=(500, 250, 1000, 420)):
            self.note = "没打开邮箱"
            return
        self.claim_all(region=(800, 1950, 1400, 2120))

    # ---------- 5 好友 ----------
    def step_friend(self) -> None:
        if not self.open((1384, 780), ("好友目录",), timeout=8):
            self.note = "没打开好友"
            return
        pos = self.find("赠送", exact=True, region=(950, 1950, 1350, 2120))
        if pos and self.sat(*pos) > 35:
            log(f"  点「赠送」@{pos}")
            self.tap(*pos, wait=2.5)
            self.dismiss_rewards()
        else:
            log("  「赠送」是灰的或没找到（可能今天送过了）")

    # ---------- 6 咨询 ----------
    def step_consult(self) -> None:
        if not self.open((276, 2290), ("咨询",), timeout=8, region=(1100, 200, 1450, 380)):
            self.note = "没进妮姬页"
            return
        if not self.open((1285, 287), ("批量咨询", "咨询次数"), timeout=8):
            self.note = "没进咨询页"
            return
        pos = self.find("批量咨询", region=(1100, 2180, 1480, 2330))
        if not pos or self.sat(*pos) < 35:
            log("  「批量咨询」是灰的或没找到（可能今天咨询过了）")
            return
        log(f"  点「批量咨询」@{pos}")
        self.tap(*pos, wait=2.5)
        self.ocr()
        # 选择窗：游戏已自动选好（底部「消耗次数 10/10」），右边「批量咨询」确认、「关闭」
        cands = [(x, y) for x, y in self.find_all("批量咨询", exact=True) if 1950 < y < 2200]
        if not cands:
            self.snap("consult_popup")
            self.note = "批量咨询选择窗没认出按钮"
            self.tap_text("关闭", exact=True, fresh=False)
            return
        log(f"  确认批量咨询 @{cands[0]}")
        self.tap(*cands[0], wait=3)
        for _ in range(6):     # 咨询演出 / 奖励：跳过、点提示、确认
            self.ocr()
            if self.find("咨询次数") and not self.find("消耗次数"):
                break
            if self.dismiss_tip():
                continue
            c = [(x, y) for x, y in self.find_all("确认", exact=True) if y > 1200]
            if c:
                self.tap(*c[-1], wait=2)
                continue
            time.sleep(1.5)

    # ---------- 方舟 ----------
    def to_ark(self) -> bool:
        return bool(self.open((1293, 1632), ("模拟室", "拦截战", "竞技场", "无限之塔"), timeout=12, wait=3))

    # ---------- 7 竞技场 ----------
    TITLE = (0, 40, 420, 140)   # 左上角页面标题
    ARENA_RATIO = 0.9           # 新人竞技场：对手战力低于自己的 90% 才算「明显低」（阈值待作者定）

    def title_is(self, key: str, fresh: bool = True) -> bool:
        return bool(self.find(key, exact=True, region=self.TITLE, fresh=fresh))

    def open_arena(self, xy, title: str):
        """竞技场首页点卡片：左上标题变成 title →「in」；赛季末只闪一条「赛季已结束。敬请期待下个赛季！」
        （1 秒多就没了，10-07 实测）→「ended」；都没有 → None。卡片上也写着竞技场名，所以只认左上标题。"""
        self.tap(*xy, wait=0.3)
        end = time.time() + 10
        while time.time() < end:
            self.ocr()
            if self.find("赛季已结束", fresh=False):
                return "ended"
            if self.title_is(title, fresh=False):
                return "in"
            time.sleep(0.4)
        return None

    def arena_cat_bubble(self) -> bool:
        """猫冒着气泡「奖励累积率…% / 点击Me获得奖励」= 有挂机奖励可领；领完气泡消失（「点击领取 ▶」标签一直在，不能拿它判断）。"""
        return bool(self.find("奖励累积率", region=(100, 480, 700, 620)) or self.find("点击Me", region=(100, 480, 700, 620)))

    def claim_arena_cat(self) -> None:
        """特殊竞技场的挂机奖励：点猫头（10-09 实测在「点击领取 ▶」标签右下 (+234,+26)，以前点标签字本身没反应、记了 ok）
        → 「竞技场信息」窗点「领取」→ 关奖励提示 → 核对气泡没了。"""
        if not self.arena_cat_bubble():
            log("  猫没冒气泡，没有可领的挂机奖励")
            return
        lab = self.find("点击领取", region=(300, 380, 750, 520))
        cat = (lab[0] + 234, lab[1] + 26) if lab else (750, 475)
        log(f"  领竞技场挂机奖励：点猫 @{cat}")
        self.tap(*cat, wait=2.5)
        if self.wait_any(("竞技场信息",), timeout=6)[0]:
            if not self.tap_text("领取", exact=True, region=(850, 2080, 1250, 2220), fresh=False, wait=2.5):
                log("  竞技场信息窗里没认出「领取」，关掉")
                self.tap_text("取消", exact=True, region=(300, 2080, 700, 2220), fresh=False, wait=2)
            self.dismiss_rewards()
        self.ocr()
        if self.arena_cat_bubble():
            self.snap("arena_cat")
            log("  点完猫还在冒气泡：挂机奖励没领到（截图已存）")
            self.note = "竞技场挂机奖励没领到"

    def step_arena(self) -> None:
        if not self.to_ark() or not self.open((1072, 1650), ("新人竞技场",), timeout=10):
            self.note = "没进竞技场"
            return
        # 挂机奖励：在「特殊竞技场」页面左上角的猫「点击领取」；只领奖励，不碰那里的对战
        if self.open_arena((1100, 1300), "特殊竞技场") == "in":
            time.sleep(1.5)  # 标题先出来，左上的猫晚一点
            self.ocr()
            self.claim_arena_cat()
            self.ocr()
            if not self.title_is("特殊竞技场", fresh=False):
                self.close_x()
            self.tap(*RETURN, wait=3)
        if not self.wait_any(("新人竞技场",), timeout=8)[0]:
            if not self.to_lobby() or not self.to_ark() or not self.open((1072, 1650), ("新人竞技场",), timeout=10):
                self.note = "没回到竞技场首页"
                return
        k = self.open_arena((430, 1270), "新人竞技场")
        if k == "ended":
            log("  新人竞技场赛季已结束，等下个赛季，今天不打")
            return
        if k != "in" or not self.wait_any(("更新目录",), timeout=8)[0]:
            self.snap("open_fail")
            self.note = "没进新人竞技场"
            return
        done, miss = 0, 0
        while done < 5 and miss < 3:
            self.ocr()
            if not self.title_is("新人竞技场", fresh=False):
                log("  当前页面不是新人竞技场，停")
                self.snap("arena_not_rookie")
                break
            frees = self.find_all("免费", exact=True, region=(1050, 1250, 1350, 2100))
            if not frees:
                miss += 1
                time.sleep(2)
                continue
            miss = 0
            # 每行对手的战力在「免费」左上方（x≈900，y≈免费-45）；挑战力最低的打，读不到战力的排最后
            def power(f):
                nums = [int(re.sub(r"[^0-9]", "", t)) for t, x, y in self.items
                        if 780 < x < 1060 and abs(y - (f[1] - 45)) < 80 and re.fullmatch(r"[0-9,.]{5,9}", t.strip())]
                return min(nums) if nums else 10 ** 12
            mine = [int(re.sub(r"[^0-9]", "", t)) for t, x, y in self.items
                    if 1000 < x < 1350 and 640 < y < 750 and re.fullmatch(r"[0-9,.]{5,9}", t.strip())]
            my = max(mine) if mine else 0
            weak = [f for f in frees if my and power(f) < my * self.ARENA_RATIO]
            if weak:
                fx, fy = min(weak, key=power)
                log(f"  我的战力 {my}，对手 {[power(f) for f in frees]}，打明显低于我的 {power((fx, fy))}")
            else:
                fx, fy = min(frees, key=power)
                log(f"  我的战力 {my or '没读到'}，对手 {[power(f) for f in frees]}，没有明显低于我的，先打最低的")
            log(f"  第 {done + 1} 场：点「进入战斗」@({fx},{fy + 78})")
            self.tap(fx, fy + 78, wait=2.5)
            if not self.wait_any(("快速战斗",), timeout=8)[0]:
                self.snap("arena_prep")
                self.note = "竞技场准备页没出来"
                return
            # 开关 ON = 左半边蓝色（饱和度约 196）；OCR 读「ON」不稳，改看颜色
            if self.sat(389, 1996, 60, 22) < 100:
                log(f"  快速战斗开关是 OFF（饱和度 {self.sat(389, 1996, 60, 22):.0f}），点一下")
                self.tap(389, 1996, wait=1.5)
                self.ocr()
                if self.sat(389, 1996, 60, 22) < 100:
                    self.snap("arena_toggle")
                    self.note = "快速战斗开关打不开"
                    self.tap_text("返回", fresh=False)
                    return
            self.tap_text("进入战斗", region=(700, 1900, 1300, 2050), wait=4)
            end = time.time() + 40
            while time.time() < end:      # 结果页「点击进行下一步」→ 回到列表
                self.ocr()
                if self.dismiss_tip():
                    continue
                if self.title_is("新人竞技场", fresh=False) and self.find("更新目录"):
                    break
                if self.close_x():
                    continue
                time.sleep(1.5)
            done += 1
        log(f"  新人竞技场打了 {done} 场")
        if done < 5:
            self.note = f"新人竞技场只打了 {done} 场"

    # ---------- 8 模拟室 ----------
    def _sim_new_season(self) -> bool:
        """11-19：新赛季头一回进模拟室，弹「CMOS RESET / 模拟室超频新赛季已开始」挡住「开始模拟」：点它的「确认」再等「开始模拟」。"""
        self.ocr()
        if not self.find("新赛季已开始"):
            return False
        pos = self.find("确认", exact=True, region=(450, 1300, 1050, 1550))
        if not pos:
            return False
        log(f"  模拟室新赛季提示，点「确认」@{pos}")
        self.tap(*pos, wait=2)
        k, _ = self.wait_any(("开始模拟",), timeout=8)
        return bool(k)

    def step_sim(self) -> None:
        if not self.to_ark() or not (self.open((346, 1400), ("开始模拟",), timeout=10) or self._sim_new_season()):
            self.note = "没进模拟室"
            return
        if not self.tap_text("开始模拟", region=(500, 1350, 1000, 1480), wait=3):
            self.note = "没找到开始模拟"
            return
        pos = self.find("快速模拟", exact=True, region=(800, 1980, 1450, 2140), fresh=True)
        if not pos or self.sat(*pos) < 35:
            self.snap("sim_noquick")
            self.note = "快速模拟不可用（今天打过了？）"
            return
        log(f"  点「快速模拟」@{pos}")
        self.tap(*pos, wait=3)
        self.ocr()
        for key in ("确认", "快速模拟"):
            cands = [(x, y) for x, y in self.find_all(key, exact=True) if y > 1300 and abs(y - pos[1]) > 30]
            if cands:
                self.tap(*max(cands, key=lambda p: p[1]), wait=3)
                break
        self.dismiss_rewards()

    # ---------- 9 异常个体拦截战 ----------
    def _quick_ready(self):
        """「每周快速战斗」能点就返回坐标，灰的返回 None。面板滑入动画中颜色偏暗，判成灰的就再看一次。"""
        for i in range(2):
            pos = self.find("快速战斗", fresh=True, region=(800, 1950, 1450, 2120))
            if pos and self.sat(*pos) >= 35:
                return pos
            if i == 0:
                time.sleep(1.5)
        return None

    def _intercept_left(self) -> int | None:
        m = re.search(r"剩余拦截次数(\d)/3", "".join(norm(t) for t, _, _ in self.items))
        return int(m.group(1)) if m else None

    @battle("正在战斗（拦截战）")          # 11-02 作者：战斗不能暂停
    def intercept_fight(self) -> str:
        """每周快速战斗是灰的 = 本周还没打过（10-18 作者：每周一都要先手动打一轮，快速战斗才亮）→ 点「进入战斗」打一轮，
        等打完回到挑战面板。返回 done / none（今天次数用完了，不算没做）/ fail（note 已写）。
        结算页只点提示、「返回」「确认」，不点重新挑战；每 30 秒存一帧到 /tmp/nikke 留证据。"""
        self.ocr()
        left = self._intercept_left()
        if left == 0:
            log("  今天的拦截次数用完了")
            return "none"
        pos = self.find_btn("进入战斗", region=(750, 2100, 1450, 2290))
        if not pos:
            self.snap("intercept_no_enter")
            self.note = "每周快速战斗是灰的、也没找到「进入战斗」（截图已存）"
            return "fail"
        log(f"  每周快速战斗是灰的（本周还没打过），点「进入战斗」打一轮 @{pos}（剩余 {left}/3）")
        self.tap(*pos, wait=8)
        t0 = time.time(); last_snap = t0
        while time.time() - t0 < 300:
            time.sleep(5)
            self.ocr()
            if self.find("每周快速战斗") or self._intercept_left() is not None:
                log(f"  打完一轮，回到挑战面板（用时约 {int(time.time() - t0)} 秒）")
                return "done"
            if time.time() - last_snap > 30:
                self.snap("intercept_fight")
                last_snap = time.time()
            if self.dismiss_tip():
                continue
            r = [(x, y) for k in ("返回", "确认") for x, y in self.find_all(k, exact=True) if y > 1500]
            if r:
                log(f"  结算页，点 @{r[0]}")
                self.tap(*r[0], wait=4)
        self.snap("intercept_timeout")
        self.note = "拦截战打了 5 分钟没回到挑战面板（截图已存）"
        return "fail"

    def step_intercept(self) -> None:
        if not self.to_ark() or not self.open((603, 2050), ("拦截次数", "异常个体"), timeout=10):
            self.note = "没进拦截战"
            return
        if not self.find("异常个体拦截战", region=(0, 40, 600, 140)):
            log("  切到「异常个体拦截战」页签")
            self.tap(1242, 2250, wait=2.5)
        time.sleep(1.5)
        if not self.open((749, 1933), ("每周快速战斗", "快速战斗"), timeout=6) and \
                not self.open((749, 1933), ("每周快速战斗", "快速战斗"), timeout=8):
            self.note = "没打开异常个体挑战面板"
            return
        fought = False
        for i in range(3):
            pos = self._quick_ready()
            if not pos:
                if i == 0 and not fought:
                    r = self.intercept_fight()
                    if r == "done":
                        fought = True
                        continue
                    return
                if fought and i == 1:
                    self.snap("intercept_still_grey")
                    self.note = "打完一轮后每周快速战斗还是灰的（截图已存）"
                log("  快速战斗是灰的，停")
                break
            log(f"  第 {i + 1} 次「每周快速战斗」@{pos}")
            self.tap(*pos, wait=3)
            self.ocr()
            for key in ("确认", "快速战斗"):
                cands = [(x, y) for x, y in self.find_all(key) if y > 1300 and abs(y - pos[1]) > 30]
                if cands:
                    self.tap(*max(cands, key=lambda p: p[1]), wait=4)
                    break
            self.dismiss_rewards()
            self.ocr()
            m = re.search(r"剩余拦截次数(\d)/3", "".join(norm(t) for t, _, _ in self.items))
            if m and m.group(1) == "0":
                break

    # ---------- 10 无限之塔 ----------
    @battle("正在战斗（无限之塔）")
    def step_tower(self) -> None:
        if not self.to_ark() or not self.open((1150, 1000), ("无限之塔",), timeout=10, region=(500, 200, 1000, 340)):
            self.note = "没进无限之塔"
            return
        if not self.open((1062, 950), ("排名",), timeout=10):
            self.note = "没进主塔"
            return
        floor0 = self.tower_floor()
        self.tap(750, 990, wait=2.5)      # 当前层齿轮中心
        if not self.tap_text("进入战斗", region=(700, 2000, 1450, 2300), wait=8):
            self.snap("tower_panel")
            self.note = "塔的关卡面板没出来"
            return
        log(f"  试打第 {floor0} 层，等战斗结束（最多 4 分钟；中途的爆裂演出插画不用管）")
        end = time.time() + 240
        while time.time() < end:
            time.sleep(5)
            self.ocr()
            if self.dismiss_tip():
                continue
            if self.title_is("无限之塔", fresh=False) and self.find("排名"):
                break
            # 结算页（失败「OPERATION FAILED 逾时」/ 胜利）：只点「返回」，不点重新挑战/前往商店/招募
            if any("OPERATION" in up(t) for t, _, _ in self.items) or self.find("重新挑战") or self.find("下一关"):
                r = [(x, y) for x, y in self.find_all("返回", exact=True) if y > 1500]
                if r:
                    log(f"  结算页：{'失败' if any('FAIL' in up(t) for t, _, _ in self.items) else '结束'}，点「返回」@{r[0]}")
                    self.tap(*r[0], wait=4)
        else:
            self.note = "4 分钟没回到塔页面"
            self.snap("tower_timeout")
            return
        floor1 = self.tower_floor()
        won = floor0 and floor1 and floor1 > floor0
        log(f"  打完：{'过了，现在第 %s 层' % floor1 if won else '没过（还是第 %s 层）' % floor1}")

    def tower_floor(self) -> int | None:
        self.ocr()
        for t, x, y in self.items:
            m = re.fullmatch(r"(\d{2,4})\s*层?", t.strip())
            if m and 200 < x < 600 and 900 < y < 1100:
                return int(m.group(1))
        return None

    # ---------- 10b 单人突击（有活动才打）----------
    # 10-19 作者：「监测到活动入口就自己打……每天三次挑战机会……最后一个是锁阵容可以重复挑战五次累积最高分的战斗」；
    # 编队：「第一步骤是先自动编队最高战力，然后剩下的普通关不用改了」（上一期高分关最后用的是第五队，弱）。
    # 10-19 00:2x 侦察（tests/raid-*-20261019.png）：大厅左栏「单人突击」字 (306,801)、RAID 图标在字上方约 100；关卡页顶上 I–VII + 最后一关，
    # 「挑战」(749,1992)、「剩余挑战次数 2/3」(751,2145)；点挑战弹「确定要挑战所选择的普通难度吗？挑战时，将消耗挑战次数。」取消 (500,1502)、确认 (1065,1502)，
    # 确认那一下就扣次数（作者点进去没打也扣了），之后一定打完、不点「放弃战斗」；进去是编队页：「放弃战斗」(1247,1239)、TEAM SET 01–05、
    # 队员 (450,1790) 点开编队编辑「自动编队」(1011,2259)「保存」(1362,2258)、「进入战斗」(1101,2043)「剩余战斗次数 5/5」；
    # 战斗 AUTO 默认开；模拟战 01 队约 110 秒打空 IV（1.57 亿）；结算「点击任意处进行下一步」(751,2113) → 回关卡页。
    # 11-15 最后那关（挑战难度）并进来，每天打 1 次：见 raid_last。页面「剩余记录次数 N/3」、确认框「进行挑战难度将消耗记录次数。」、
    # 5 场各约 3 分钟、每场前自动编队、第 5 场后总结算「确认」(787,1824) 回最后那关页（tests/raid-last*-20261115.png 是 11-15 实拍）。
    RAID_TEAMS = ((713, 1614), (856, 1614), (996, 1614), (1139, 1614), (1279, 1614))

    RAID_NODES = (210, 365, 518, 673, 828, 983, 1139, 1294)   # 关卡页顶上 I–VII + 最后一关，y≈258

    def raid_node_px(self) -> list[int]:
        """顶上 8 个节点各自的橙色像素数（没图返回空表）。"""
        if self.img is None:
            return []
        sx, sy = self.w / BASE_W, self.h / BASE_H
        hsv = cv2.cvtColor(self.img, cv2.COLOR_BGR2HSV)
        counts = []
        for x in self.RAID_NODES:
            roi = hsv[int(183 * sy):int(333 * sy), int((x - 75) * sx):int((x + 75) * sx)]
            counts.append(int(((roi[..., 0] >= 5) & (roi[..., 0] <= 30) & (roi[..., 1] >= 120) & (roi[..., 2] >= 150)).sum()))
        return counts

    def raid_stage(self) -> int | None:
        """当前是第几关（1–8，8 = 最后那关）：当前关的齿轮圈是亮橙色（10-19 实拍 IV 3032 像素，打过的 339–835，锁着的 0）。认不出返回 None。
        11-16：第 7 关打过以后第 8 个骷髅节点也亮（925–2234 像素，推断在闪），常读成 None。"""
        counts = self.raid_node_px()
        if not counts:
            return None
        best = max(range(len(counts)), key=lambda i: counts[i])
        second = sorted(counts)[-2]
        return best + 1 if counts[best] >= 1500 and counts[best] >= 2 * second else None

    def raid_left(self) -> int | None:
        m = re.search(r"剩余挑战次数(\d)/3", "".join(norm(t) for t, _, _ in self.items))
        return int(m.group(1)) if m else None

    def is_raid_page(self) -> bool:
        """关卡页、上面没盖着确认框或提示（确认框是半透明的，底下的「挑战」「剩余挑战次数」照样认得出）。"""
        if any("确定要" in norm(t) or (norm(t).startswith("点击") and y > 1000) for t, x, y in self.items):
            return False
        return bool(self.find("单人突击", region=(0, 40, 420, 140)) and self.raid_left() is not None
                    and self.find("挑战", exact=True, region=(500, 1900, 1000, 2080)))

    def is_raid_last_page(self) -> bool:
        """最后那关（挑战难度）页：「剩余记录次数 N/3」（普通难度写「剩余挑战次数」，raid_left / is_raid_page 都不认）；
        上面没盖确认框、不是编队页（10-20 实拍 tests/raid-last*-20261020.png）。"""
        if any("确定要" in norm(t) for t, x, y in self.items) or self.find("放弃战斗"):
            return False
        return bool(self.find("单人突击", region=(0, 40, 420, 140)) and self.find("剩余记录次数"))

    def is_raid_team(self) -> bool:
        return bool(self.find("进入战斗", region=(800, 1980, 1450, 2130)) and self.find("放弃战斗"))

    def raid_last_open(self) -> bool:
        """顶上第 8 个节点（最后那关）解锁了：11-16 实拍 925–2234 像素；10-19 锁着时是 0。没图返回 False。"""
        counts = self.raid_node_px()
        return bool(counts) and counts[-1] >= 300

    def raid_cleared(self) -> bool:
        """当前关打过了、还有普通次数：「快速战斗」亮橙（11-16 实拍第 7 关饱和度 136；10-19 没打过的第 IV 关是灰的 9；次数用完也变灰），
        且最后那关已解锁（只有第 7 关打过才解锁）。没图返回 False，照旧真打。"""
        return self.img is not None and self.sat(1061, 1840) >= 90 and self.raid_last_open()

    def raid_quick(self, left: int) -> str:
        """当前关打过了：用「快速战斗」扫掉剩余普通次数，不真打（11-16 作者：「第七关还是可以每天快速战斗扫荡一下」）。
        「快速战斗」→ 次数框「按照自订的次数进行快速战斗。」→ MAX →「进行战斗」→ 结算页「点击任意处进行下一步」→ 回关卡页。
        11-16 04:19 实点一次：3/3 一次扫完、4 秒出结算。返回 ok / stop（note 已写）。"""
        log(f"  当前关打过了（快速战斗亮着），用快速战斗扫掉剩余 {left} 次，不真打")
        pos = self.find("快速战斗", region=(800, 1780, 1350, 1900))
        self.tap(*(pos or (1061, 1840)), wait=2)
        k, _ = self.wait_any(("按照自订的次数",), timeout=6)
        if not k:
            self.snap("raid_quick_no_dialog")
            self.note = "单人突击点了快速战斗没弹次数框（截图已存）"
            return "stop"

        def cancel(why: str) -> str:
            self.snap("raid_quick_dialog")
            c = self.find("取消", exact=True, region=(250, 1780, 650, 1910))
            if c:
                self.tap(*c, wait=2)
            self.note = f"单人突击快速战斗{why}（没扫；截图已存）"
            return "stop"

        mx = self.find("MAX", region=(950, 1620, 1150, 1750))
        if mx:
            self.tap(*mx, wait=1.5)
        self.ocr()
        m = re.search(r"(\d)/(\d)", "".join(norm(t) for t, x, y in self.items if 550 <= x <= 950 and 1640 <= y <= 1730))
        n = int(m.group(1)) if m else 0
        if n < 1:
            return cancel("次数没读出来")
        go = self.find("进行战斗", region=(700, 1790, 1250, 1910))
        if not go:
            return cancel("没找到进行战斗")
        self.tap(*go, wait=3)
        k, _ = self.wait_any(("点击任意处进行下一步",), timeout=20)
        if not k:
            self.snap("raid_quick_no_result")
            self.note = "单人突击快速战斗点了进行战斗没出结算（截图已存）"
            return "stop"
        for _ in range(4):
            self.tap(751, 2113, wait=3)
            self.ocr()
            if self.is_raid_page():
                break
        else:
            self.snap("raid_quick_back")
            self.note = "单人突击快速战斗结算后没回到关卡页（截图已存）"
            return "stop"
        log(f"  快速战斗扫了 {n} 次，剩余挑战次数 {self.raid_left()}/3")
        return "ok"

    def raid_to_last(self) -> None:
        """第 7 关打过了、普通次数用完：点顶上第 8 个节点去最后那关页（11-16 实点，不扣次数），交给 raid_last。"""
        log("  点顶上第 8 个节点去最后那关")
        self.tap(self.RAID_NODES[-1], 258, wait=3)
        self.ocr()
        if self.is_raid_last_page():
            self.raid_last()
        else:
            self.snap("raid_to_last")
            self.note = "单人突击点第 8 个节点没到最后那关页（截图已存）"

    def step_raid(self) -> None:
        self.ocr()
        ent = self.find("单人突击", exact=True, region=(150, 250, 460, 1350))   # 左栏图标随活动增减上下挪，只框 x
        if not ent:
            log("  大厅没有「单人突击」入口（没开活动），跳过")
            return
        if not self.open((ent[0], ent[1] - 100), ("剩余挑战次数", "剩余记录次数"), timeout=10):
            self.note = "没进单人突击"
            return
        self.raid_formed = False
        for _ in range(3):
            self.ocr()
            if self.is_raid_last_page():          # 10-20：普通关 I–VII 都打空后，一进来就是最后那关页
                self.raid_last()                  # 11-15 作者：「打」「最后那关写进日常」，每天 1 次
                return
            left = self.raid_left()
            if left == 0:
                log("  单人突击今天的 3 次用完了")
                if self.raid_last_open():         # 11-16：第 7 关打过了、普通次数今天已扫掉，最后那关还要看一眼
                    self.raid_to_last()
                return
            if not self.is_raid_page():
                self.snap("raid_page")
                self.note = "单人突击页面没认出来（截图已存）"
                return
            if self.raid_cleared():               # 11-16 作者：第 7 关打过了就每天快速战斗扫荡，不再真打
                if self.raid_quick(left) == "ok":
                    self.raid_to_last()
                return
            r = self.raid_challenge(left)
            if r == "last":                       # 这次把 VII 打空，游戏自动跳到了最后那关页
                self.ocr()
                if self.is_raid_last_page():
                    self.raid_last()
                else:
                    self.snap("raid_last_stage")
                    self.note = "单人突击打空普通关后没认出最后那关页（截图已存）"
                return
            if r != "ok":
                return

    def raid_challenge(self, left: int) -> str:
        """关卡页一次挑战：「挑战」→ 确认框是普通难度才确认（确认就扣次数）→ 编队页 01 队 + 自动编队（本次第一次）→
        进入战斗，打完没打死就换下一队接着打，回到关卡页算这次挑战结束。返回 ok / stop（note 已写）/ last（打空 VII、到了最后那关页）。"""
        stage = self.raid_stage()
        if stage == len(self.RAID_NODES):
            self.snap("raid_last_stage")
            log("  顶上亮的是最后那关、可这页还是普通难度的样子，不点")
            self.note = "单人突击顶上亮着最后那关但页面是普通难度的样子（截图已存）"
            return "stop"
        log(f"  当前第 {stage or '?'} 关")
        pos = self.find("挑战", exact=True, region=(500, 1900, 1000, 2080))
        self.tap(*(pos or (749, 1992)), wait=2)
        k, _ = self.wait_any(("确定要挑战",), timeout=6)
        if not k:
            self.snap("raid_no_confirm")
            self.note = "单人突击点了挑战没弹确认框（截图已存）"
            return "stop"
        if not self.find("普通难度"):
            self.snap("raid_not_normal")
            log("  确认框不是普通难度（多半到了最后那关），先不打")
            self.tap_text("取消", exact=True, fresh=False, wait=2)
            self.note = "单人突击确认框不是普通难度、可能是最后那关（没确认；截图已存）"
            return "stop"
        log(f"  挑战当前关（剩余 {left}/3），确认")
        self.tap_text("确认", exact=True, region=(850, 1400, 1300, 1600), fresh=False, wait=3)
        k, _ = self.wait_any(("放弃战斗",), timeout=12)
        if not k or not self.is_raid_team():
            self.snap("raid_no_team")
            self.note = "单人突击确认后没进编队页（次数已扣；截图已存）"
            return "stop"
        for team in range(5):
            self.tap(*self.RAID_TEAMS[team], wait=1.5)
            if team == 0 and not self.raid_formed:
                self.raid_auto_form()
            r = self.raid_fight(team)
            if r == "page":
                return "ok"
            if r == "last":
                return "last"
            if r != "team":
                return "stop"
            m = re.search(r"剩余战斗次数(\d)/5", "".join(norm(t) for t, _, _ in self.items))
            if m and m.group(1) == "0":
                break
            log(f"  第 {team + 1} 队打完没打空，换第 {team + 2} 队接着打")
        self.snap("raid_teams_used")
        self.note = "单人突击五队都打完了还在编队页（截图已存）"
        return "stop"

    RAID_LAST_PER_DAY = 1      # 11-15 作者：「打」「如果是单次最高肯定是打一次」——一次是 5 队伤害累计，多次之间只留最高记录，所以每天只打 1 次
    # 11-16 作者：「boss关只用打一次五队就行了，也就是挑战上面已经有伤害了，那就是打过了，可以跳过」→ 有最高记录就整期不打（raid_last 里先查）

    def raid_last_left(self) -> int | None:
        m = re.search(r"剩余记录次数(\d)/3", "".join(norm(t) for t, _, _ in self.items))
        return int(m.group(1)) if m else None

    def raid_last_record(self) -> int | None:
        """最后那关页「模拟战」右边记录框里的最高记录（11-15 实拍识成「1.004,899,809」，只取数字）。"""
        for t, x, y in self.items:
            if 900 <= x <= 1420 and 1780 <= y <= 1910:
                d = re.sub(r"\D", "", t)
                if len(d) >= 4:
                    return int(d)
        return None

    def raid_last(self) -> None:
        """最后那关（挑战难度）：本期还没有最高记录、今天也没打过，就打 1 次（11-16 起有记录就整期不打）。「挑战」→ 确认框是「将消耗记录次数」才确认（确认就扣次数）→ 5 队依次：点队页签、
        自动编队（打过一场的妮姬不能再上，自动编队会避开）、进入战斗、等打完；第 5 场后的总结算点「确认」回到最后那关页。不点「放弃战斗」。
        10-20、11-15 两次照这个流程手动打通（../2026-10-19-妮姬单人突击/raid_last_1020.py、raid_last_1115.py）。"""
        left = self.raid_last_left()
        if left is None:
            self.snap("raid_last_left")
            self.note = "单人突击最后那关没读到剩余记录次数（截图已存）"
            return
        rec = self.raid_last_record()
        if rec:
            log(f"  最后那关本期已有最高记录 {rec}，打过一次就够，不打")
            return
        if left <= 3 - self.RAID_LAST_PER_DAY:
            log(f"  最后那关今天打过了（剩余记录次数 {left}/3），每天只打 {self.RAID_LAST_PER_DAY} 次")
            return
        log(f"  到了最后那关（挑战难度），剩余记录次数 {left}/3，打 1 次（5 场）")
        pos = self.find("挑战", exact=True, region=(500, 1900, 1000, 2080))
        self.tap(*(pos or (749, 1992)), wait=2)
        k, _ = self.wait_any(("确定要挑战",), timeout=6)
        if not k:
            self.snap("raid_last_no_confirm")
            self.note = "单人突击最后那关点了挑战没弹确认框（截图已存）"
            return
        # 认「将消耗记录次数」；别拿「普通难度」排除——挑战难度的确认框自己就写着「（不与普通难度共用挑战次数。）」（11-15 实拍夹具测出来的）
        if not self.find("消耗记录次数") or self.find("消耗挑战次数"):
            self.snap("raid_last_confirm")
            log("  确认框不是「将消耗记录次数」那个，不确认")
            c = self.find("取消", region=(250, 1400, 700, 1600))
            if c:
                self.tap(*c, wait=2)
            self.note = "单人突击最后那关的确认框没认出来（没确认；截图已存）"
            return
        self.tap_text("确认", exact=True, region=(850, 1400, 1300, 1600), fresh=False, wait=3)
        k, _ = self.wait_any(("放弃战斗",), timeout=12)
        if not k or not self.is_raid_team():
            self.snap("raid_last_no_team")
            self.note = "单人突击最后那关确认后没进编队页（记录次数已扣；截图已存）"
            return
        for team in range(5):
            self.tap(*self.RAID_TEAMS[team], wait=1.5)
            self.raid_auto_form()
            r = self.raid_fight(team)
            if r == "last":
                break
            if r == "team":
                continue
            if r == "page":
                self.snap("raid_last_page")
                self.note = "单人突击最后那关打到一半回到了普通关卡页（截图已存）"
            return                     # fail：raid_fight 已经写了 note
        else:
            self.snap("raid_last_teams_used")
            self.note = "单人突击最后那关五队都打完了还在编队页（截图已存）"
            return
        self.ocr()
        rec = self.raid_last_record()
        log(f"  最后那关打完：最高记录 {rec if rec is not None else '没读到'}，剩余记录次数 {self.raid_last_left()}/3")

    def raid_auto_form(self) -> None:
        """01 队点开编队编辑 →「自动编队」(最高战力) →「保存」；作者：第一步先自动编队，剩下的普通关不用改。"""
        if not self.open((450, 1790), ("自动编队",), timeout=8, region=(800, 2180, 1250, 2330)):
            log("  没打开编队编辑，用现在的 01 队")
            return
        self.tap_text("自动编队", region=(800, 2180, 1250, 2330), fresh=False, wait=2)
        if self.tap_text("保存", region=(1200, 2180, 1500, 2330), wait=3):
            self.raid_formed = True
        if not self.wait_any(("放弃战斗",), timeout=8)[0]:
            self.snap("raid_form_back")
            log("  保存后没回到编队页")

    @battle("正在战斗（单人突击）")
    def raid_fight(self, team: int) -> str:
        """编队页点「进入战斗」→ 等打完。返回 page（回到关卡页）/ team（回到编队页：这队打完没打空）/ last（打空 VII 后跳到最后那关页）/ fail（note 已写）。
        结算只点提示和「确认」，不点「放弃战斗」；每 30 秒存一帧 /tmp/nikke/raid_fight_*。"""
        pos = self.find_btn("进入战斗", region=(800, 1980, 1450, 2130), fresh=True)
        if not pos:
            self.snap("raid_no_enter")
            self.note = "单人突击编队页没找到「进入战斗」（次数已扣；截图已存）"
            return "fail"
        log(f"  第 {team + 1} 队进入战斗 @{pos}")
        self.tap(*pos, wait=8)
        t0 = time.time(); last_snap = t0
        while time.time() - t0 < 300:
            time.sleep(5)
            self.ocr()
            if self.is_raid_page():
                log(f"  打完回到关卡页（用时约 {int(time.time() - t0)} 秒），剩余 {self.raid_left()}/3")
                return "page"
            if self.is_raid_last_page():          # 10-20 02:09：VII 打空后游戏自动跳到最后那关页，旧代码不认、干等 300 秒
                log(f"  打完到了最后那关页（用时约 {int(time.time() - t0)} 秒）")
                return "last"
            if self.is_raid_team():
                log(f"  打完回到编队页（用时约 {int(time.time() - t0)} 秒）")
                return "team"
            if time.time() - last_snap > 30:
                self.snap("raid_fight")
                last_snap = time.time()
            if self.dismiss_tip():
                continue
            r = [(x, y) for x, y in self.find_all("确认", exact=True) if y > 1300]
            if r:
                log(f"  点「确认」@{r[0]}")
                self.tap(*r[0], wait=3)
        self.snap("raid_timeout")
        self.note = "单人突击打了 5 分钟没回到关卡页（截图已存）"
        return "fail"

    # ---------- 11 招募 ----------
    def step_recruit(self) -> None:
        if not self.open((1226, 2290), ("招募1名", "招募队员"), timeout=10):
            self.note = "没进招募页"
            return
        # 10-09：新角色卡池排到第一页（只有「招募1名 ×300 / 10名 ×3000」，花珠宝），「每日免费1次」在上一期特殊招募
        # 卡池的最左按钮上；以前见字就点写死的 (317,1935)，在新卡池抽了一次。现在按字找、翻页找、点字本身，点前再核一次位置。
        pos = None
        for i in range(5):
            self.ocr()
            pos = self.find("每日免费", region=(100, 1950, 560, 2050))
            if pos or i == 4:
                break
            self.tap(*RECRUIT_NEXT, wait=2.5)  # 右箭头翻下一个卡池（只翻页）
        if not pos:
            log("  翻遍卡池没看到「每日免费1次」，今天不抽")
            return
        time.sleep(1)
        self.ocr()
        again = self.find("每日免费", region=(100, 1950, 560, 2050))
        if not again or abs(again[0] - pos[0]) > 40 or abs(again[1] - pos[1]) > 40:
            self.snap("recruit_moved")
            log("  「每日免费」位置变了，不抽（免得点到花珠宝的按钮）")
            self.note = "免费招募没抽：页面在变"
            return
        log(f"  免费招募 @{again}")
        self.tap(*again, wait=4)
        # 动画：点 SKIP / 跳过，再关结果
        for _ in range(12):
            self.ocr()
            if self.find("招募1名", region=(100, 1850, 560, 2050)) and not self.find("每日免费"):
                break
            p = self.find("SKIP") or self.find("跳过")
            if p:
                self.tap(*p, wait=3)
                continue
            if self.dismiss_tip():
                continue
            for key in ("确认", "关闭"):
                cands = self.find_all(key, exact=True)
                if cands:
                    self.tap(*cands[-1], wait=2)
                    break
            else:
                self.tap(752, 1200, wait=2)   # 动画中点屏幕加速
        self.ocr()
        if self.find("每日免费", region=(100, 1950, 560, 2050)):
            self.snap("recruit_free_left")
            self.note = "免费招募没生效（「每日免费」还在）"

    # ---------- 12 普通商店 ----------
    def to_shop(self) -> bool:
        return bool(self.open((320, 1632), ("百货商店",), timeout=10, region=(0, 40, 400, 140)))

    def buy_free(self) -> int:
        """买 100% SALE（价格 0）的商品。"""
        n = 0
        for _ in range(4):
            self.ocr()
            sale = [(x, y) for t, x, y in self.items if "100%" in t and 1300 < y < 2250]
            if not sale:
                break
            x, y = sale[0]
            log(f"  免费商品 @({x},{y - 250})")
            self.tap(x, y - 250, wait=2)
            self.ocr()
            btn = None
            for key in ("购买", "确认"):
                cands = [(bx, by) for bx, by in self.find_all(key, exact=True) if by > 1300]
                if cands:
                    btn = max(cands, key=lambda p: p[1])
                    break
            if not btn:
                self.snap("shop_popup")
                self.close_x()
                break
            self.tap(*btn, wait=2.5)
            self.dismiss_rewards()
            n += 1
        return n

    def refresh_cost(self):
        """刷新确认框「消耗资金 💎N」的 N。整屏 OCR 读不到框里的小数字，把那一行单独裁出来再读（10-06 截图实测读到 0）。"""
        pos = self.find("消耗资金", region=(400, 1150, 1100, 1400), fresh=False)
        if not pos or self.img is None:
            return None
        sx, sy = self.w / BASE_W, self.h / BASE_H
        x, y = pos
        crop = self.img[int((y - 38) * sy):int((y + 37) * sy), int((x - 83) * sx):int((x + 317) * sx)]
        f = TMP / "cost.png"  # 存 PNG：JPEG 会把这个小数字压糊，读不出来
        cv2.imwrite(str(f), crop)
        out = subprocess.run([str(OCR_BIN), str(f)], capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            try:
                t = json.loads(line)["text"].strip()
            except (json.JSONDecodeError, KeyError):
                continue
            if re.fullmatch(r"\d+", t):
                return int(t)
        return None

    REFRESH_JITTER = ((0, 0), (-12, -6), (12, 6), (-6, 12), (6, -12), (0, 0))  # 刷新图标 (775,1092) 周围，都在图标内

    def step_shop(self) -> None:
        if not self.to_shop():
            self.note = "没进商店"
            return
        self.tap(95, 937, wait=2)
        if not self.find("普通商店", fresh=True):
            self.note = "没切到普通商店"
            return
        n = self.buy_free()
        # 免费刷新：刷新按钮旁挂着「免费」才点；确认框里「消耗资金」读出来是 0 才点确认
        self.ocr()
        if self.find("免费", region=(650, 1020, 950, 1080)):
            log("  刷新按钮旁有「免费」，免费刷新")
            shown = False
            # 10-09 作者：刷新键「接触不是很好」（手点一下也会没反应），在图标范围内散开多点几下，弹了框就停。
            # 框弹出要 3–5 秒（10-09 实测），晚弹时下一下会落在框中间空白处（取消/确认在 y≈1500），不会误点。
            for i, (dx, dy) in enumerate(self.REFRESH_JITTER):
                if i:
                    self.ocr()
                    if self.find("是否更新商品"):  # 框晚弹出来了（会挡住「免费」，先认框）
                        shown = True
                        break
                    if not self.find("免费", region=(650, 1020, 950, 1080)):
                        break
                    log(f"  确认框没出来，换个位置再点刷新（第 {i + 1} 下）")
                self.tap(775 + dx, 1092 + dy, wait=0.5)
                if self.wait_any(("是否更新商品",), timeout=5)[0]:
                    shown = True
                    break
            if not shown:
                self.snap("shop_refresh")
                self.note = "刷新确认框没出来"
            elif self.refresh_cost() != 0:
                self.snap("shop_refresh_cost")
                log("  确认框里的消耗资金不是 0（或读不到），点取消，不刷新")
                self.tap_text("取消", exact=True, region=(200, 1400, 800, 1600), fresh=False, wait=2)
                self.note = "刷新确认框的消耗资金不是 0，没刷"
            else:
                cands = [(x, y) for x, y in self.find_all("确认", exact=True) if y > 1200]
                if cands:
                    log(f"  确认刷新（消耗资金 0）@{cands[-1]}")
                    self.tap(*cands[-1], wait=3)
                    n += self.buy_free()
                else:
                    self.note = "刷新确认框没认出「确认」"
        else:
            log("  刷新按钮旁没有「免费」，不刷新")
        log(f"  普通商店免费买了 {n} 次")

    # ---------- 13 竞技场商店 ----------
    WANT = ("手册选择宝箱", "模组高级增强器")
    # OCR 会把「手」读成「F」；10-30 买完变灰的那张只读出「选择宝箱」
    WANT_KEY = {"手册选择宝箱": "选择宝箱", "模组高级增强器": "模组高级增强"}
    # 10-30：原来 (850,2000)→(850,1300) 700ms 带惯性，实测一下滚 1100 左右（以前 7 次划完模组名字落在 y 1216–1290），
    # 多滚一点名字就被列表顶上（约 1160）遮住、认不出，之后只往下划回不来（10-30 漏买模组）。
    # 改成慢划 400 像素：实测每下滚约 500（一行 576），名字在可认区（y 1150–2250）至少露两屏。
    SHOP_SWIPE = (850, 1900, 850, 1500, 1500)

    def step_arena_shop(self) -> None:
        if not self.to_shop():
            self.note = "没进商店"
            return
        self.tap(95, 1505, wait=2)
        if not self.find("竞技场商店", fresh=True):
            self.note = "没切到竞技场商店"
            return
        miss, failed = self.arena_scan()
        notes = (["没找到：" + "、".join(miss)] if miss else []) + (["没买成：" + "、".join(failed)] if failed else [])
        if notes:
            self.note = "；".join(notes)

    def arena_scan(self, buy: bool = True) -> tuple[list[str], list[str]]:
        """从竞技场商店列表当前位置往下找 WANT 并买（buy=False 只记位置不买，真机试划用）。
        每轮重新识字：买完一样原地再认（10-30 以前接着用购买弹窗的识字结果）；没要找的就慢划一下，划不动 = 到底。
        返回 (没找到的, 点了没买成的)；有没找到的存一张图。"""
        done, failed, tries, last, swipes = [], [], {}, None, 0
        for _ in range(14):
            self.ocr()
            hit = None
            for want in self.WANT:
                if want in done or want in failed:
                    continue
                pos = self.find(self.WANT_KEY[want], region=(150, 1150, 1400, 2050))   # 名字 ≤2050：下面「剩余」那行才在屏内
                if pos:
                    hit = want, pos
                    break
            if hit:
                want, (x, y) = hit
                card = (x - 170, y, x + 170, y + 330)
                if self.find("剩余0", region=card) or self.find("售罄", region=card):
                    log(f"  「{want}」今天已买完")
                    done.append(want)
                    continue
                if not buy:
                    log(f"  （不买）认到「{want}」@({x},{y})")
                    done.append(want)
                    continue
                tries[want] = tries.get(want, 0) + 1
                log(f"  买「{want}」@({x},{y + 150})")
                self.tap(x, y + 150, wait=2)
                self.ocr()
                cands = [(bx, by) for bx, by in self.find_all("购买", exact=True) if by > 1300]
                if not cands:
                    self.snap("arena_shop_popup")
                    self.close_x()
                    if tries[want] >= 2:
                        failed.append(want)
                    continue
                self.tap(*max(cands, key=lambda p: p[1]), wait=2.5)
                self.dismiss_rewards()
                done.append(want)
                continue
            if len(done) + len(failed) == len(self.WANT):
                break
            sig = sorted((norm(t), y // 25) for t, x, y in self.items if 1150 <= y <= 2250)
            if sig == last or swipes >= 6:
                break
            last = sig
            self.swipe(*self.SHOP_SWIPE)
            swipes += 1
        miss = [w for w in self.WANT if w not in done and w not in failed]
        if miss:
            self.snap("arena_shop_miss")
        return miss, failed

    # ---------- 14 限时活动 ----------
    def banner_sig(self):
        self.img = self.grab()
        sx, sy = self.w / BASE_W, self.h / BASE_H
        roi = self.img[int(1940 * sy):int(2090 * sy), int(880 * sx):int(1420 * sx)]
        return cv2.resize(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (108, 30)).astype(np.float32)

    @staticmethod
    def same_sig(a, b) -> bool:
        return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0]) > 0.85

    def event_id(self) -> str | None:
        """进入活动后用「剩余时间」那行区分是哪个活动（每期不同；横幅会自己轮换，不能靠横幅图判断）。"""
        k, _ = self.wait_any(("活动地区", "剧情活动"), timeout=25, region=self.TITLE)
        if not k:
            self.snap("event_entry")
            return None
        for _ in range(4):
            time.sleep(1.5)
            self.ocr()
            for t, x, y in self.items:
                n = norm(t)
                if "剩余" in n and ("天" in n or "小时" in n):
                    return n
        return k

    @staticmethod
    def event_minutes(eid: str):
        """「剩余时间11小时8分钟」/「剩余13天」→ 剩余分钟数；认不出 None。"""
        d = re.search(r"(\d+)天", eid)
        h = re.search(r"(\d+)小时", eid)
        m = re.search(r"(\d+)分", eid)
        if not (d or h or m):
            return None
        return (int(d.group(1)) * 1440 if d else 0) + (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)

    def seen_event(self, eid: str, done: dict) -> bool:
        """同一活动再进来剩余时间会变（带分钟的每分钟都变，10-07 同一活动 11小时8分钟 / 11小时6分钟 做了两遍）：
        按「剩余分钟 + 已过去的分钟」和做过的比：显示到分钟的差 5 分钟内、只显示到小时的差 65 分钟内算同一个；
        只写到天的（一天内不变）按原文比。"""
        if eid in done:
            return True
        mins = self.event_minutes(eid)
        if mins is None or not ("分" in eid or "小时" in eid):
            return False
        tol = 5 if "分" in eid else 65
        now = time.time()
        for old, t0 in done.items():
            om = self.event_minutes(old)
            if om is not None and ("分" in old or "小时" in old) and abs((om - (now - t0) / 60) - mins) <= tol:
                return True
        return False

    def step_event(self) -> None:
        done, results, repeats = {}, [], 0
        for attempt in range(5):
            if not self.to_lobby():
                break
            if attempt:
                log("  点 ⟳ 换下一个活动横幅")
                self.tap(1445, 2120, wait=2)
            self.tap(1030, 2010, wait=3)
            eid = self.event_id()
            if not eid:
                results.append("没进到活动")
                continue
            if self.seen_event(eid, done):
                repeats += 1
                log(f"  这个活动（{eid}）做过了")
                if repeats >= 2:
                    break
                continue
            log(f"  进入活动（{eid}）")
            done[eid] = time.time()
            results.append(self.do_event())
        log(f"  一共做了 {len(done)} 个活动")
        bad = [r for r in results if r != "ok"]
        if bad:
            self.note = "；".join(bad)

    def to_story_home(self) -> bool:
        """从活动入口到「剧情活动」首页（有 ENTER 的那一页）。地图型活动先点 STORY II，灰的就点 STORY I。"""
        if self.title_is("剧情活动"):
            return True
        if not self.title_is("活动地区", fresh=False):
            self.snap("event_entry")
            return False
        stories = []
        for _ in range(4):           # 菜单是动画滑进来的，标题先出来，按钮晚一点
            stories = sorted([(y, x) for t, x, y in self.items if up(t).startswith("STORY") and y > 1600])
            if stories:
                break
            time.sleep(1.5)
            self.ocr()
        if len(stories) == 1 and self.sat(stories[0][1], stories[0][0], 150, 30) < 40:
            y, x = stories[0]                    # 只读到灰色的 STORY II：STORY I 在它正上方
            stories = [(y - 138, x + 50), (y, x)]
        for y, x in reversed(stories):          # 下面那个是 STORY II
            if self.sat(x, y, 150, 30) < 40:
                log(f"  ({x},{y}) 的 STORY 是灰的，跳过")
                continue
            log(f"  点 STORY @({x},{y})，等人物走过去")
            self.tap(x, y, wait=3)
            k, _ = self.wait_any(("剧情活动",), timeout=25, region=self.TITLE)
            if k:
                return True
            self.to_event_map()
        return False

    def to_event_map(self) -> None:
        for _ in range(3):
            self.ocr()
            if self.find("活动地区", region=(0, 40, 420, 140)):
                return
            self.tap(*RETURN, wait=3)

    def find_stage11(self):
        """关卡列表里找第 11 关：列表型直接认「X-11」；气泡型认出 10、12，取两者之间的那个气泡标签。"""
        for t, x, y in self.items:
            if re.search(r"\d-11$", up(t)):
                return x, y
        nums, labels = {}, []
        for t, x, y in self.items:
            u = up(t)
            m = re.fullmatch(r"1-?(\d{1,2})", u)
            if m:
                nums[int(m.group(1))] = (x, y)
            if u.startswith("EV") or u.startswith("EC"):
                labels.append((x, y))
        if 10 in nums and 12 in nums:
            y10, y12 = nums[10][1], nums[12][1]
            mid = [(x, y) for x, y in labels if min(y10, y12) + 150 < y < max(y10, y12) - 150]
            if mid:
                return mid[0]
            return (nums[10][0] + nums[12][0]) // 2, (y10 + y12) // 2
        return None

    def stage_title(self) -> str:
        roi = self.img[1290:1410, 100:760]
        return "".join(t for t, _, _ in self.ocr_img(roi))

    def quick_battle(self, times: int) -> bool:
        """关卡面板上点「快速战斗」→ 次数窗（剩余次数 / 1/5 / MAX / 取消 / 进行战斗）→ times>1 时点 MAX → 进行战斗。"""
        pos = None
        for _ in range(2):            # 面板滑入动画中颜色偏暗，判成灰的就再看一次
            # 按钮白字整屏 OCR 常读不出（10-07 挑战 1-4 面板：整屏没读到、裁剪重读才读到，饱和度 142 可点）
            pos = self.find_btn("快速战斗", region=(800, 1980, 1450, 2140), fresh=True)
            if pos and self.sat(*pos) >= 35:
                break
            time.sleep(1.5)
        else:
            log("  快速战斗是灰的")
            return False
        log(f"  点「快速战斗」@{pos}")
        self.tap(*pos, wait=1.5)
        k, go = self.wait_any(("进行战斗",), timeout=6)
        if not k:
            # 有的快速战斗（挑战关卡）不弹次数窗，直接出结果
            self.dismiss_rewards()
            return True
        if times > 1:
            mx = self.find("MAX", exact=True, region=(900, 1600, 1200, 1760))
            if mx:
                self.tap(*mx, wait=1)
                self.ocr()
            cnt = [norm(t) for t, x, y in self.items if 600 < x < 900 and 1640 < y < 1730]
            log(f"  次数 {cnt}")
            go = self.find("进行战斗", exact=True) or go
        log(f"  点「进行战斗」@{go}")
        self.tap(*go, wait=4)
        for _ in range(6):
            self.ocr()
            if self.dismiss_tip():
                continue
            if self.find_btn("快速战斗", region=(800, 1980, 1450, 2140)) and not self.find("进行战斗"):
                break
            time.sleep(1.5)
        return True

    def do_event(self) -> str:
        if not self.to_story_home():
            return "没进到剧情活动"
        res = []
        # 第 11 关扫荡 5 次
        _, p = self.wait_any(("ENTER", "NTER", "ENER"), timeout=8)     # 标题先出来，ENTER 晚一点
        entered = False
        for _ in range(2):
            log(f"  点 ENTER @{p or (760, 1750)}")
            self.tap(*(p or (760, 1750)), wait=3)
            if self.wait_any(("活动关卡",), timeout=10, region=self.TITLE)[0]:
                entered = True
                break
        if entered:
            pos = None
            for i in range(4):
                pos = self.find_stage11()
                if pos:
                    break
                self.swipe(750, 900 if i < 2 else 1900, 750, 1900 if i < 2 else 900, 700)
                self.ocr()
            if pos:
                log(f"  第 11 关 @{pos}")
                self.tap(*pos, wait=2.5)
                self.ocr()
                title = self.stage_title()
                log(f"  关卡面板标题「{title}」")
                if "11" in title or norm(title).startswith("1-1"):
                    if not self.quick_battle(5):
                        res.append("第11关快速战斗不可用")
                else:
                    res.append(f"点开的不是第11关（{title}）")
                self.close_x()
            else:
                self.snap("stage_list")
                res.append("没找到第11关")
        else:
            res.append("ENTER 没进关卡列表")
        # 挑战扫荡
        if not self.do_challenge():
            res.append("挑战没扫成")
        # 活动任务
        if not self.do_event_tasks():
            res.append("活动任务没领成")
        return "ok" if not res else "、".join(res)

    def do_challenge(self) -> bool:
        for _ in range(3):
            self.ocr()
            p = [(x, y) for t, x, y in self.items if norm(t) == "挑战" and y > 1700]
            if p:
                break
            self.tap(*RETURN, wait=3)
        else:
            return False
        self.tap(*p[0], wait=3)
        if not self.wait_any(("挑战关卡",), timeout=15, region=(0, 40, 420, 140))[0]:
            return False
        self.ocr()
        if any(norm(t) == "0/1" for t, x, y in self.items if x > 1250 and 650 < y < 770):
            log("  挑战次数 0/1，今天已经扫过")
            return True
        ch = [y for t, x, y in self.items if "CHALLENGESTAGE" in up(t)]
        clear = [y for t, x, y in self.items if "CLEARSTAGE" in up(t)]
        if ch:
            above = [y for y in clear if y < ch[0]]
            ty = max(above) if above else ch[0] - 350
        elif clear:
            ty = max(clear)
        else:
            self.snap("challenge_list")
            return False
        log(f"  挑战：点已通关最高一关 @(280,{ty})")
        self.tap(280, ty, wait=2.5)
        ok = self.quick_battle(1)
        self.ocr()
        self.close_x()
        return ok

    def do_event_tasks(self) -> bool:
        for _ in range(3):
            self.ocr()
            p = [(x, y) for t, x, y in self.items if norm(t) == "任务" and (y > 1700 or x > 1250)]
            if p:
                break
            self.tap(*RETURN, wait=3)
        else:
            return False
        self.tap(*p[0], wait=3)
        if not self.wait_any(("全部领取",), timeout=8)[0]:
            return False
        self.claim_all()
        return True

    # ---------- 15 任务 ----------
    def step_missions(self) -> None:
        if not self.open((1386, 600), ("每日任务",), timeout=8):
            self.note = "没打开任务"
            return
        self.claim_all(region=(900, 2080, 1450, 2230))
        if self.tap_text("每周任务", region=(400, 380, 800, 500), wait=2):
            self.claim_all(region=(900, 2080, 1450, 2230))

    # ---------- 16 通行证 ----------
    def step_pass(self) -> None:
        if not self.open((1205, 420), ("购买PASS", "RANK", "PASS"), timeout=8):
            self.note = "没打开通行证"
            return
        first = None
        for i in range(3):
            self.ocr()
            sig = cv2.resize(cv2.cvtColor(self.img[300:560, 200:1300], cv2.COLOR_BGR2GRAY), (110, 26)).astype(np.float32)
            if first is not None and self.same_sig(sig, first):
                break
            if first is None:
                first = sig
            if self.tap_text("任务", exact=True, region=(850, 700, 1300, 850), wait=2):
                self.claim_all(region=(400, 2150, 1100, 2330))
            if self.tap_text("奖励", exact=True, region=(250, 700, 650, 850), wait=2):
                self.claim_all(region=(400, 2150, 1100, 2330))
            log(f"  第 {i + 1} 个通行证处理完，翻下一个")
            self.tap(1442, 1195, wait=2.5)


# 10-14 作者要求去掉限时活动：event 不再进步骤表（step_event 等代码留着不调用）
STEPS: list[str] = ["popups", "outpost", "dispatch", "mail", "friend", "consult", "arena", "sim", "intercept",
                    "tower", "raid", "recruit", "shop", "arena_shop", "missions", "pass"]
NAMES: dict[str, str] = {
    "popups": "登录弹窗奖励", "outpost": "前哨基地：收获+歼灭", "dispatch": "派遣：全部领取+全部派遣",
    "mail": "邮箱", "friend": "好友：赠送友情点", "consult": "咨询：批量咨询",
    "arena": "竞技场：挂机奖励+新人竞技场5次", "sim": "模拟室：快速模拟", "intercept": "异常个体拦截战：快速战斗",
    "tower": "无限之塔：试打一层", "raid": "单人突击（有活动才打）", "recruit": "招募：每日免费一次", "shop": "普通商店：免费买+免费刷新",
    "arena_shop": "竞技场商店：手册选择宝箱+模组高级增强器",
    "missions": "每日/每周任务领取", "pass": "通行证领取(放最后)",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STEPS))
    ap.add_argument("--restart", action="store_true", help="先关掉游戏再打开（让登录弹窗重新出现）")
    args = ap.parse_args()
    steps = [s for s in args.only.split(",") if s in STEPS]
    bot = Bot()
    if not bot.ensure_game(args.restart):
        log("完成：" + "，".join(f"{NAMES[k]}={bot.start_fail or '没进到游戏'}" for k in steps))
        return 1
    result = {}
    for s in steps:
        log(f"== {NAMES[s]}")
        bot.note = None
        try:
            if s != "popups" and not bot.to_lobby():
                raise RuntimeError("回不到大厅")
            getattr(bot, f"step_{s}")()
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
