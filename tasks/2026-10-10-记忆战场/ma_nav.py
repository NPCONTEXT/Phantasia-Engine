"""记忆战场 菜单导航（2400×1504）：回记忆战场页、选 BOSS、进训练/正式选人页、一键编队、收结算。
坐标和页面都是 10-10 实测。每步认字确认，认不出就抛 NavError，不盲点。"""
from __future__ import annotations

import json, subprocess, time
from pathlib import Path

try:        # 11-02 暂停协议（幻想引擎的 lib/play_ctl.py，经 PYTHONPATH 传进来）；单独运行时 import 不到，用什么都不做的替身
    from play_ctl import pause_point, battle, mark
except ImportError:
    import contextlib

    def pause_point():
        return False

    def mark(*a, **k):
        pass

    class battle(contextlib.ContextDecorator):
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

HERE = Path(__file__).resolve().parent
import sys  # noqa: E402
sys.path.insert(0, str(HERE.parent.parent / "bh3_auto"))
from lib.pe_platform import adb_path, ocr_engine, ocr_lines, rapid_text_line, tmp_dir  # noqa: E402  11-23 跨平台（Mac 上照旧：Homebrew 的 adb、/tmp、Vision 识字）
OCR = HERE.parent.parent / "bh3_auto/ocr/ocr"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = [adb_path(), "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
BOSS_Y = (300, 500, 690)          # 左栏 0 陨星-奈埃尔 / 1 伪神奥托 / 2 影骑士·月轮（本期顺序）


class NavError(RuntimeError):
    pass


class Settling(NavError):
    """出击页「记忆战场」卡片底下是「⚠结算中」：上期完了、下期还没开，点了一直转圈进不去（10-20 00:53 实拍）。"""


class NoTries(NavError):
    """今日挑战次数用完：点开战弹「挑战次数不足，请明日再战。」（10-15 实拍）。"""


def tap(x, y, w=2.5):
    subprocess.run(ADB + ["shell", f"input tap {x} {y}"], timeout=10)
    time.sleep(w)


def ocr():
    pause_point()        # 11-02 幻想引擎要暂停就停在截图前（战斗里不停），继续后重新截图
    p = tmp_dir() / "ma_nav.png"
    p.write_bytes(subprocess.run(ADB + ["exec-out", "screencap", "-p"], capture_output=True, timeout=20).stdout)
    out = []
    for line in ocr_lines(p, timeout=30).splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append((d["text"].strip(), d["x"] + d["w"] // 2, d["y"] + d["h"] // 2))
    return out


def has(items, key, region=None):
    for t, x, y in items:
        if key in t and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
            return x, y
    return None


def to_ma_page(log=print):
    """从记忆战场里的任意菜单页 / 结算页退回记忆战场页（右下「战场减负」），顺手关掉状态框。"""
    for _ in range(10):
        it = ocr()
        if has(it, "战场减负", (1500, 1350, 1950, 1500)):
            if has(it, "挑战进度") and has(it, "确定", (1000, 1000, 1400, 1130)):
                tap(1198, 1067, 2)
                continue
            return it
        if has(it, "确定", (1850, 1300, 2300, 1460)):            # 训练/正式 战斗结果页
            tap(2059, 1386, 3)
        elif has(it, "战斗失败") or has(it, "战斗耗时"):
            tap(1200, 1300, 3)
        elif has(it, "作者", (1700, 380, 2300, 470)):
            tap(2332, 65, 2)
        elif has(it, "推荐阵容") and has(it, "整体完整度优先"):   # 10-15：底栏「阵容」打开的是整页推荐阵容，左上返回
            tap(93, 67, 3)
        elif has(it, "推荐阵容"):                               # 选人页「阵容攻略」弹的推荐阵容小窗，右上 X
            tap(2084, 234, 2)
        else:
            tap(93, 67, 3)
    raise NavError(f"没回到记忆战场页：{[t for t, _, _ in it][:25]}")


def to_detail(idx, log=print):
    to_ma_page(log)
    tap(190, BOSS_Y[idx], 2)
    tap(2109, 1430, 4)
    it = ocr()
    if not has(it, "训练模式", (1400, 1300, 1800, 1460)):
        raise NavError(f"没进 BOSS 详情页：{[t for t, _, _ in it][:25]}")
    name = [t for t, x, y in it if 100 <= x <= 600 and 150 <= y <= 260]
    diff = detail_difficulty()
    log(f"  BOSS 详情页：{name}，难度（详情页读）{diff}")
    return name


def detail_difficulty(png=str(tmp_dir() / "ma_nav.png")):
    """详情页名字后面的花体难度字（SS / SSS）。单次 OCR 不稳（SSS 读成 S55、S85、55），
    裁标题区按 1/1.5/2/2.5/3/4 倍各读一次，5、8 当 S，只认 SS / SSS，投票取多数；平票或读不出 None。
    10-10：只用 1/2/3 倍时奈埃尔实机读成「555」「55」平票 → None；改 6 档后 4 张实拍都投对。"""
    import cv2
    im = cv2.imread(str(png))
    if im is None:
        return None
    crop = im[150:260, 20:900]
    votes = []
    for k in (1, 1.5, 2, 2.5, 3, 4):
        c = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        p = tmp_dir() / "ma_diff.png"
        cv2.imwrite(str(p), c)
        for line in ocr_lines(p, timeout=30).splitlines():
            try:
                t = json.loads(line)["text"]
            except (json.JSONDecodeError, KeyError):
                continue
            for tok in t.replace(" ", "").split("•"):
                tok = "".join(ch for ch in tok if ch in "S58")
                tok = tok.replace("5", "S").replace("8", "S")
                if tok in ("SS", "SSS"):
                    votes.append(tok)
    if not votes:
        return None
    return max(("SSS", "SS"), key=votes.count) if votes.count("SSS") != votes.count("SS") else None


def team_ok(it) -> bool:
    """选人页特殊规则两行都打勾（OCR 读成 V/✓）才算配好；没配是 X。
    10-23：有的 BOSS（奔狼的领主）训练选人页没有「特殊规则」区，改看三个格子底下是不是都有「80级」这种等级字。"""
    rows = [t for t, x, y in it if ("最少上场人数" in t or "评阶" in t) and 1300 <= x <= 1800]
    if not rows and not has(it, "特殊规则"):
        lv = [t for t, x, y in it if t.strip().endswith("级") and t.strip()[:-1].isdigit() and 100 <= x <= 1000 and 750 <= y <= 900]
        return len(lv) >= 3
    return bool(rows) and not any(t.lstrip().startswith(("X", "x", "×")) for t in rows) and \
        not has(it, "X", (1180, 990, 1260, 1140))


OFFICIAL_STEP = 244          # 官方推荐行距；一键编队按钮在行号下方约 72


def official_row_y(it, row, scrolled=False):
    """官方推荐小窗里第 row 行「一键编队」的 y；这一屏没有返回 None。
    行号是斜体：1 读成 7、3 常读不出（10-15 实拍），读得到就直接用，读不到用相邻行号 ±244 推。
    没滑过时第 1 行固定在 648。推出来的位置旁边必须真有「一键编队」字。"""
    nums = {}
    for t, x, y in it:
        t = t.strip()
        if 300 <= x <= 400 and 450 <= y <= 1250 and t.isdigit() and len(t) == 1:
            nums.setdefault(int(t), y)
    cand = []
    if row == 1 and not scrolled:
        cand.append(648)
    if row in nums and not (row == 7 and 1 not in nums):
        cand.append(nums[row] + 72)
    if row - 1 in nums and row - 1 != 7:
        cand.append(nums[row - 1] + 72 + OFFICIAL_STEP)
    if row + 1 in nums:
        cand.append(nums[row + 1] + 72 - OFFICIAL_STEP)
    for y in cand:
        if 560 <= y <= 1200 and has(it, "一键编队", (1750, y - 70, 2050, y + 70)):
            return y
    return None


def one_key_team(row, log=print, tab="selected"):
    """选人页 → 阵容攻略 → 第 row 套「一键编队」。
    tab="selected"：精选推荐 + 完整度优先（10-10 学法，goto_row 翻表）；
    tab="official"：官方推荐第 row 行（10-15 学法：推荐阵容「全部应用」的队在官方推荐里能找到，训练页要单独配）。"""
    if tab == "official":
        tap(1649, 222, 3); tap(613, 383, 2.5)
        for k in range(4):
            it = ocr()
            if not has(it, "官方推荐"):
                raise NavError(f"没打开官方推荐：{[t for t, _, _ in it][:25]}")
            y = official_row_y(it, row, scrolled=k > 0)
            if y:
                break
            subprocess.run(ADB + ["shell", "input swipe 1200 1050 1200 560 600"], timeout=10)   # 往下翻约 2 行
            time.sleep(1.5)
        else:
            raise NavError(f"官方推荐翻了 3 次没找到第 {row} 行")
        tap(1890, y, 3)
        log(f"  官方推荐第 {row} 行一键编队")
        it = ocr()
        if has(it, "推荐阵容"):
            tap(2084, 234, 2); it = ocr()
        return it
    tap(1649, 222, 3); tap(1286, 381, 2.5)
    it = ocr()
    if not has(it, "ON", (1900, 340, 2100, 420)):
        tap(2053, 381, 2.5)
    r = subprocess.run([str(HERE.parent.parent / "bh3_auto/.venv/bin/python"), str(HERE / "goto_row.py"), str(row), "team"],
                       capture_output=True, text=True, timeout=200)
    log("  " + r.stdout.strip().replace("\n", " / "))
    time.sleep(2)
    it = ocr()
    if has(it, "推荐阵容"):
        tap(2084, 234, 2); it = ocr()
    return it


def to_train_team(idx, row, log=print, tab="selected"):
    to_detail(idx, log)
    tap(1598, 1383, 4)
    it = ocr()
    if not has(it, "选择难度"):
        raise NavError(f"没进训练选人页：{[t for t, _, _ in it][:25]}")
    if not team_ok(it):
        log(f"  训练队伍没配好，一键编队第 {row} 套（{tab}）")
        it = one_key_team(row, log, tab)
        if not team_ok(it):
            raise NavError(f"一键编队后特殊规则仍没满足：{[t for t, _, _ in it if '规则' in t or '人数' in t or '评阶' in t]}")
    log("  训练选人页就绪")


# ---------- 正式战斗 / 整跑 ----------
def to_ma_from_anywhere(log=print):
    """游戏里任意页 → 主界面 → 出击 →「挑战」页签 → 记忆战场。已经在记忆战场里就直接退回记忆战场页。"""
    for _ in range(8):
        it = ocr()
        if has(it, "战场减负", (1500, 1350, 1950, 1500)) or has(it, "训练模式") or has(it, "推荐阵容") or has(it, "开战"):
            return to_ma_page(log)
        pos = has(it, "记忆战场", (0, 1100, 2400, 1350))
        if pos:                                                  # 出击页卡片：「挑战」页签在 (721,1215)，冷启动后的「推荐」页签在 (941,1271)
            if has(it, "结算中", (pos[0] - 100, pos[1] + 20, pos[0] + 400, pos[1] + 120)):   # 卡名下面一条（10-20：卡名 (721,1216)、「⚠结算中」(838,1271)）
                raise Settling("卡片底下是「结算中」，上期完了、下期还没开")
            tap(*pos, 4); continue                               # 10-10 冷启动时按写死的 (721,1215) 点进了超弦空间
        if has(it, "挑战", (1250, 220, 1480, 330)) and has(it, "出击", (900, 220, 1200, 330)):
            tap(1363, 276, 3); continue
        if has(it, "出击", (2050, 650, 2350, 850)):               # 主界面右侧大「出击」
            tap(2200, 750, 4); continue
        if has(it, "主菜单", (380, 30, 560, 110)):
            tap(469, 65, 4); continue
        tap(93, 67, 3)
    raise NavError(f"没进到记忆战场：{[t for t, _, _ in it][:25]}")


def ma_status(it=None):
    """记忆战场页：左栏三个 BOSS 的（名字 OCR, 进度 n, 总 m）和今日剩余挑战次数。"""
    it = it or ocr()
    bosses = []
    for k, y in enumerate(BOSS_Y):
        name = [t for t, x, y2 in it if 60 <= x <= 330 and abs(y2 - (y + 35)) <= 60 and len(t) >= 3]
        prog = [t for t, x, y2 in it if 380 <= x <= 600 and abs(y2 - y) <= 60 and "/" in t]
        n = m = None
        if prog:
            a, _, b = prog[0].replace("l", "/").replace("U", "0").partition("/")
            n = int(a) if a.strip().isdigit() else None
            m = int(b) if b.strip().isdigit() else None
        if n is not None and m and n > m:         # 10-16：减负刚结束把 4/5 读成「41/5」→ 判成打完跳过；打过的比总关多 = 没读出（总关数留着，难度靠它）
            n = None
        bosses.append({"idx": k, "name": name[0] if name else None, "done": n, "total": m})
    left = None
    for t, x, y in it:
        if "剩余挑战次数" in t or (1900 <= x <= 2400 and 1290 <= y <= 1350):
            d = "".join(ch for ch in t if ch.isdigit())
            if d and len(d) <= 2:
                left = int(d)
    if left is None:
        left = left_from_png()
    return bosses, left


def left_from_png(png=str(tmp_dir() / "ma_nav.png")):
    """今日剩余挑战次数：整屏识字常漏掉单个数字（10-15 一晚 4 次都是 None，屏上是 0）。
    裁「今日剩余挑战次数」右边的数字，放大 2/3/4 倍各读一次；U、O、D 当 0，l、I 当 1；投票取多数，读不出 None。"""
    import cv2
    im = cv2.imread(str(png))
    if im is None:
        return None
    if im.shape[1] != 2400:
        im = cv2.resize(im, (2400, 1504))
    crop = im[1280:1360, 2230:2400]            # 会带进半个「数」字，不含数字，丢掉就行
    votes = []
    for k in (2, 3, 4):
        c = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        p = tmp_dir() / "ma_left.png"
        cv2.imwrite(str(p), c)
        for line in ocr_lines(p, timeout=30).splitlines():
            try:
                t = json.loads(line)["text"]
            except (json.JSONDecodeError, KeyError):
                continue
            t = t.translate(str.maketrans("UODoQlI|", "00000111"))
            d = "".join(ch for ch in t if ch.isdigit())
            if d and len(d) <= 2:
                votes.append(int(d))
    if not votes and ocr_engine() == "rapid":     # 11-24：RapidOCR 框不出单独一个「0」，整块当一行字认（读成「数0」）
        for k in (2, 3, 4):
            t = rapid_text_line(cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC))
            d = "".join(ch for ch in t.translate(str.maketrans("UODoQlI|", "00000111")) if ch.isdigit())
            if d and len(d) <= 2:
                votes.append(int(d))
    return max(set(votes), key=votes.count) if votes else None


def sweep_all(log=print):
    """三个 BOSS 依次「战场减负」到不再弹结果框（点了没确认框，直接减负；减负占挑战次数）。返回减负次数。"""
    total = 0
    for k, y in enumerate(BOSS_Y):
        to_ma_page(log)
        tap(190, y, 2)
        for _ in range(8):
            tap(1726, 1428, 3)
            it = ocr()
            if not has(it, "挑战进度"):
                break
            prog = [t for t, x, yy in it if 1700 <= x <= 2000 and 720 <= yy <= 800]
            total += 1
            log(f"  减负 BOSS{k}：进度 {prog}")
            tap(1198, 1067, 2.5)
    return total


def to_real_team(idx, row, log=print, tab="selected", force=False):
    """BOSS 详情页 →「开始战斗」→ 正式选人页；没配好就一键编队第 row 套。
    force=True（档案 lineup.force）：已经配好也照样一键编队，换成档案那一队（10-15 托纳提乌换成精选第 1 行花火队用）。"""
    to_detail(idx, log)
    tap(2115, 1393, 4)
    it = ocr()
    if has(it, "挑战次数不足"):
        tap(1198, 1028, 2)                         # 关掉提示框
        raise NoTries("今日挑战次数用完")
    if not has(it, "开战", (1900, 1330, 2300, 1460)):
        raise NavError(f"没进正式选人页：{[t for t, _, _ in it][:25]}")
    if force or not team_ok(it):
        log(f"  {'按档案换队' if force else '队伍没配好'}，一键编队第 {row} 套（{tab}）")
        it = one_key_team(row, log, tab)
        if not team_ok(it):
            raise NavError(f"一键编队后特殊规则仍没满足：{[t for t, _, _ in it if '规则' in t or '人数' in t or '评阶' in t]}")
    return it


def set_buff(on: bool, log=print):
    """选人页左下「增益模式」开关 (462,1402)：读 ON/OFF，不对就点一下。"""
    it = ocr()
    cur = "ON" if has(it, "ON", (380, 1360, 560, 1440)) else ("OFF" if has(it, "OFF", (380, 1360, 560, 1440)) else None)
    if cur is None:
        if on:
            raise NavError("要开增益模式，但选人页没有增益模式开关")
        log("  选人页没有增益模式开关（SS 难度的月轮就没有，10-10 实测），按不开处理")
        return False
    if (cur == "ON") != on:
        tap(462, 1402, 2)
        it = ocr()
        cur = "ON" if has(it, "ON", (380, 1360, 560, 1440)) else "OFF"
    log(f"  增益模式 {cur}")
    return cur == "ON"


def press_fight(log=print):
    """正式选人页点「开战」。弹框里带「锁定」就确认（女武神锁定提示，推测）；别的框不认识就报错不点。"""
    tap(2098, 1396, 3)
    it = ocr()
    words = [t for t, _, _ in it]
    if has(it, "确定") or has(it, "确认"):
        if any("锁定" in w for w in words):
            pos = has(it, "确定") or has(it, "确认")
            log(f"  开战确认框（锁定）：{words[:15]} → 点 {pos}")
            tap(*pos, 3)
        else:
            raise NavError(f"开战后弹了不认识的框：{words[:25]}")


def settle(log=print):
    """正式结算页：有「确认结算」就点（不点「重新挑战」）；之后回记忆战场页。"""
    for _ in range(4):
        it = ocr()
        pos = has(it, "确认结算")
        if pos:
            log(f"  点「确认结算」@{pos}")
            tap(*pos, 4)
            break
        time.sleep(2)
    return to_ma_page(log)
