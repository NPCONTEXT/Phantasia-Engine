"""超弦空间 菜单导航（2400×1504）：回主页、读主页状态（剩余时间 / 小组积分表）、关卡页逐区读（区名 / 剩余积分 / 环境 / 能不能打）、
进选人页、读预设队伍。坐标和字样都是 10-16 侦察实测。每步认字确认，认不出抛 NavError，不盲点。"""
from __future__ import annotations

import json, re, subprocess, time
from pathlib import Path

import cv2
import numpy as np

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
OCR = HERE.parent.parent / "bh3_auto/ocr/ocr"
import os  # noqa: E402  11-17 无线调试：用幻想引擎经 ANDROID_SERIAL 指定的设备；单独运行时才用下面这个序列号
ADB = ["/opt/homebrew/bin/adb", "-s", os.environ.get("ANDROID_SERIAL") or "YOUR_DEVICE_SERIAL"]
PNG = Path("/tmp/ss_nav.png")
CROP = Path("/tmp/ss_crop.png")

# 关卡页左边四个节点（10-16：左 = 开局唯一能打的；上 / 右 / 下 带锁；作者：上左右同一环境、下单独一个）
NODES = {"左": (230, 796), "上": (464, 560), "右": (700, 800), "下": (464, 1034)}
PREP = (2071, 1420)               # 关卡页「战斗准备」（锁着的区是灰的）
BACK = (93, 67)                   # 左上「返回」
MAIN_START = (2118, 1417)         # 主页「开始战斗」= 进关卡页
TEAM_START = (2097, 1415)         # 选人页「开始战斗」= 真开打（只在 start_fight 里点）
ROW_Y = (339, 428, 517, 608, 698, 789, 876, 971)   # 主页小组表 8 行
KINDS = ("边缘区", "密集区", "高危区", "特异区")


class NavError(RuntimeError):
    pass


def tap(x, y, w=2.5):
    subprocess.run(ADB + ["shell", f"input tap {x} {y}"], timeout=10)
    time.sleep(w)


def parse(out: str):
    res = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        res.append((d["text"].strip(), d["x"] + d["w"] // 2, d["y"] + d["h"] // 2))
    return res


def ocr_path(p: Path):
    return parse(subprocess.run([str(OCR), str(p)], capture_output=True, text=True, timeout=30).stdout)


def grab():
    pause_point()        # 11-02 幻想引擎要暂停就停在截图前（战斗里不停），继续后重新截图
    PNG.write_bytes(subprocess.run(ADB + ["exec-out", "screencap", "-p"], capture_output=True, timeout=20).stdout)
    return cv2.imread(str(PNG))


def ocr():
    """截屏 + 识字；返回 (文字列表, 图)。"""
    img = grab()
    return ocr_path(PNG), img


def has(items, key, region=None):
    for t, x, y in items:
        if key in t and (region is None or (region[0] <= x <= region[2] and region[1] <= y <= region[3])):
            return x, y
    return None


# ---------- 认页面 ----------
def page(it) -> str | None:
    """main 主页 / stage 关卡页 / team 选人页 / preset 预设队伍 / rules 玩法说明 / tasks 深渊任务 / recommend 本期深渊推荐 /
    sortie 出击页（有超弦空间卡）/ sortie_tab 出击页、这个页签没有超弦空间卡 / home 主界面 / None 认不出。
    叠层的先认（说明、任务、推荐都盖在主页上）。"""
    if has(it, "使用方案", (1900, 1350, 2400, 1504)):
        return "preset"
    if has(it, "本期深渊推荐") or has(it, "全部关卡", (100, 1150, 600, 1350)) or has(it, "锁定中"):
        return "recommend"
    if has(it, "规则说明") or (has(it, "攻略站点") and has(it, "深渊周报")):
        return "rules"
    if has(it, "深渊终极区") or has(it, "本期总积分达到"):
        return "tasks"
    if has(it, "预设队伍", (700, 150, 1100, 290)) and has(it, "开始战斗", (1850, 1350, 2350, 1480)):
        return "team"
    if has(it, "战斗准备", (1850, 1350, 2300, 1480)) and (has(it, "积分", (60, 250, 480, 360)) or has(it, "通关条件")):
        return "stage"                                  # 10-17：「剩余积分」读成过「週余积分」，只认「积分」二字
    if has(it, "开始战斗", (1850, 1350, 2350, 1480)) and (has(it, "深渊", (60, 940, 300, 1040)) or has(it, "无限榜单")
                                                           or has(it, "兑换商店")):
        return "main"                                   # 10-17：「深渊任务」读成过「深渊信务」
    if has(it, "超弦空间", (0, 1100, 2400, 1350)):
        return "sortie"
    if has(it, "挑战", (1250, 220, 1480, 330)) and has(it, "出击", (900, 220, 1200, 330)):
        return "sortie_tab"                             # 10-22：「推荐」页签换成新活动，下排没有超弦卡（卡在「挑战」页签）
    if has(it, "出击", (2050, 650, 2350, 850)):
        return "home"
    return None


# ---------- 主页 ----------
def settling(it) -> bool:
    """结算期：主页右上「剩余时间」变成「结算中」、开始战斗变灰（10-18 22:17 实拍；作者：每周三、周日 22:00–22:30 结算期，之后才是未开放）。"""
    return has(it, "结算中", (1500, 0, 2400, 160)) is not None


def remain_minutes(it):
    """右上剩余时间 → 分钟：「01天22小时」→ 2760、「21小时」→ 1260、「00分58秒」→ 0（10-18 21:59 实拍：不到 1 小时显示分秒）。读不出 None。"""
    for t, x, y in it:
        if "余时间" not in t:
            continue
        s = t.split("余时间", 1)[1].replace("O", "0").replace("o", "0")
        m = re.search(r"(\d+)\s*天\s*(\d+)\s*小时", s)
        if m:
            return int(m.group(1)) * 1440 + int(m.group(2)) * 60
        m = re.search(r"(\d+)\s*小时(?:\s*(\d+)\s*分)?", s)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2) or 0)
        m = re.search(r"(\d+)\s*分", s)
        if m:
            return int(m.group(1))
    return None


def remain_hours(it):
    """右上「剩余时间01天22小时」→ 46；不到一天时可能是「xx小时yy分」（没见过，按推断写）。读不出 None。"""
    for t, x, y in it:
        if "余时间" not in t:                            # 10-17：「剩余时间」读成过「剥余时间」
            continue
        s = t.split("余时间", 1)[1].replace("O", "0").replace("o", "0")
        m = re.search(r"(\d+)\s*天\s*(\d+)\s*小时", s)
        if m:
            return int(m.group(1)) * 24 + int(m.group(2))
        m = re.search(r"(\d+)\s*小时", s)
        if m:
            return int(m.group(1))
        if re.search(r"\d+\s*分", s):
            return 0
    return None


def my_row(img):
    """小组表里自己那行有亮青色边框（BGR≈255,215,0，10-16 r02 实测在 x≈904 竖边），返回行号 0–7；找不到 None。"""
    if img is None:
        return None
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    best, best_n = None, 0
    for k, y in enumerate(ROW_Y):
        roi = img[y - 30:y + 30, 896:914].astype(int)
        n = int(((roi[..., 0] > 230) & (roi[..., 1] > 180) & (roi[..., 2] < 60)).sum())
        if n > best_n:
            best, best_n = k, n
    return best if best_n >= 20 else None


def digits(t: str):
    d = "".join(ch for ch in t if ch.isdigit())
    return int(d) if d else None


def group_scores(img):
    """小组表「当前积分」列。10-16：整屏识字把火焰图标读成 4 / 0（「40」），单个「0」裁出来识字又认不出；
    办法：每行数字段（x 1748–1990）前面拼上表头「当前积分」，8 行竖着叠成一张图一次识字（离线 8/8 读出「当前积分0」）。
    积分上限 22000（三区 6000 + 高危 4000），读不出的行 None。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    head = img[220:266, 1715:1870]
    blocks = []
    for y in ROW_Y:
        blocks.append(np.hstack([head, img[y - 23:y + 23, 1748:1990]]))
        blocks.append(np.full((24, blocks[-1].shape[1], 3), 40, np.uint8))
    cv2.imwrite(str(CROP), cv2.resize(np.vstack(blocks), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    parts = {}
    for t, x, y in ocr_path(CROP):                     # 多位数时「当前积分」和「6000」会拆成两条（10-17 实测），按行拼回去
        parts.setdefault(int(y / 2 // 70), []).append((x, t))
    got = {}
    for k, ps in parts.items():
        line = "".join(t for _, t in sorted(ps))
        if "积分" not in line or not 0 <= k < len(ROW_Y):
            continue
        v = digits(line.split("积分", 1)[1])
        if v is not None and v <= 22000:
            got[k] = v
    return [got.get(k) for k in range(len(ROW_Y))]


def my_rank(it, me):
    """自己那行「排名」列（x≈975）：「2.」→ 2；没参战是「--」→ None。10-17：小组表能上下滑、不止 8 人，自己那行一直在可见区（高亮）。"""
    if me is None:
        return None
    y = ROW_Y[me]
    for t, x, y2 in it:
        if 900 <= x <= 1060 and abs(y2 - y) <= 35:
            d = digits(t)
            if d is not None and d < 100:
                return d
    return None


def main_status(it, img, scores=True):
    """主页：剩余小时、可见的小组 8 行（名字 / 积分 / 是不是自己）、自己第几行、自己排名。"""
    rows = []
    sc = group_scores(img) if scores else [None] * len(ROW_Y)
    me = my_row(img)
    for k, y in enumerate(ROW_Y):
        name = [t.lstrip("Qq，, ") for t, x, y2 in it if 1180 <= x <= 1650 and abs(y2 - y) <= 40 and len(t.lstrip("Qq，, ")) >= 2]
        rows.append({"name": name[0] if name else None, "score": sc[k], "me": k == me})
    return {"remain_h": remain_hours(it), "rows": rows, "me": me, "rank": my_rank(it, me)}


# ---------- 关卡页 ----------
def stage_info(it):
    """关卡页右边说明 + 左上区名 / 剩余积分（选中哪个节点就显示哪个）。"""
    name = next((t for t, x, y in it if x < 700 and y < 260 and any(k in t for k in KINDS)), None)
    kind = next((k for k in KINDS if name and k in name), None)
    pts = None
    for t, x, y in it:                                  # 打满的区读成一条「剩余积分0」(193,309)；没打满是「剩余积分」+「6000」两条
        if 60 <= x <= 480 and 270 <= y <= 350:
            v = digits(t.split("积分", 1)[1]) if "积分" in t else digits(t)
            if v is not None:
                pts = v
    env = next((t.lstrip("◎®© ") for t, x, y in it if t.endswith("环境") and 1000 <= x <= 1500 and 430 <= y <= 530), None)
    cond = next((t for t, x, y in it if t.startswith("限时") and 1000 <= x <= 2300 and 280 <= y <= 360), None)
    return {"name": name, "kind": kind, "left": pts, "env": env, "cond": cond}


def prep_enabled(img) -> bool:
    """「战斗准备」黄 = 能打，灰 = 锁着（10-16 r05 黄、r06 灰）。"""
    if img.shape[1] != 2400:
        img = cv2.resize(img, (2400, 1504))
    b, g, r = img[1390:1450, 1850:2290].reshape(-1, 3).mean(0)
    return r > 180 and g > 160 and b < 110


def zones(log=print):
    """在关卡页逐个点四个节点读一遍；返回 {位置: info+enabled}。点完停在最后一个节点。"""
    out = {}
    for pos, xy in NODES.items():
        tap(*xy, 2.0)
        it, img = ocr()
        if page(it) != "stage":
            raise NavError(f"点节点{pos}后不在关卡页：{[t for t, _, _ in it][:20]}")
        info = stage_info(it)
        info["enabled"] = prep_enabled(img)
        out[pos] = info
        log(f"  {pos}：{info['name']} 剩余积分 {info['left']} {info['env']} {'能打' if info['enabled'] else '锁着'}")
    return out


# ---------- 选人页 / 预设 ----------
def preset_names(it):
    """预设队伍页的队名（10-16 r16：地藏、B队…F队），按从上到下、从左到右。"""
    names = [(t, x, y) for t, x, y in it if (t.endswith("队") or (x in range(150, 600) and y < 260)) and len(t) <= 8
             and y < 1400 and t not in ("预设队伍",)]
    names = [(t, x, y) for t, x, y in names if not t.endswith("级")]
    return [t for t, x, y in sorted(names, key=lambda n: (round(n[2] / 100), n[1]))]


# ---------- 导航 ----------
def to_main(log=print):
    """游戏里任意页 → 超弦空间主页。主界面 → 出击 → 认「超弦空间」卡字就点（页签不同位置不同，别写死）。"""
    it = []
    for _ in range(12):
        it, img = ocr()
        pg = page(it)
        if pg == "main":
            return it, img
        if pg == "rules":
            pos = has(it, "关闭", (2000, 1250, 2300, 1380))
            tap(*(pos or (2210, 66)), 2)                 # 最后一页「关闭」，其他页右上 X
        elif pg == "tasks":
            tap(93, 69, 2)
        elif pg == "recommend":
            tap(2181, 166, 2)
        elif pg == "preset":
            tap(226, 76, 2.5)
        elif pg in ("stage", "team"):
            tap(*BACK, 3)
        elif pg == "sortie":
            tap(*has(it, "超弦空间", (0, 1100, 2400, 1350)), 4)
        elif pg == "home":
            tap(2200, 750, 4)
        elif has(it, "主菜单", (380, 30, 560, 110)):
            tap(469, 65, 4)
        else:
            tap(*BACK, 3)
    raise NavError(f"没回到超弦空间主页：{[t for t, _, _ in it][:25]}")


def to_stage(log=print):
    """主页 →「开始战斗」→ 关卡页。"""
    it, img = to_main(log)
    tap(*MAIN_START, 3)
    it, img = ocr()
    if page(it) != "stage":
        raise NavError(f"点开始战斗后没到关卡页：{[t for t, _, _ in it][:20]}")
    return it, img


def to_team(pos, log=print):
    """关卡页选节点 →「战斗准备」→ 选人页（不开打）。锁着的区抛 NavError。"""
    tap(*NODES[pos], 2.0)
    it, img = ocr()
    if page(it) != "stage":
        raise NavError(f"不在关卡页：{[t for t, _, _ in it][:20]}")
    if not prep_enabled(img):
        raise NavError(f"{pos} 区锁着（战斗准备是灰的）")
    tap(*PREP, 3.5)
    it, img = ocr()
    if page(it) != "team":
        raise NavError(f"点战斗准备后没到选人页：{[t for t, _, _ in it][:20]}")
    return it, img


# ---------- 配队：推荐阵容（10-17 作者定：官方推荐 + 完整度优先 + 第一行）----------
# 作者：「你配队用官方的，热门的好久没更新了，都是老角色」（热门第一行打特异区两次超时）
REC_PORTRAIT = (1310, 434)        # 选人页「女武神推荐」第一个头像 → 弹「推荐阵容」窗
REC_HOT_TAB = (611, 383)          # 「热门阵容」页签（打开时默认选中）
REC_OFFICIAL_TAB = (1286, 381)    # 「官方推荐」页签（这页没有「预计耗时」，只有完整度）
REC_TOGGLE = (2025, 381)          # 「完整度优先」开关（每次打开都重置成 OFF）
REC_ROW1_BTN = (1912, 576)        # 第一行「一键编队」黄色六边形
REC_ROW_DY = 243                  # 行距（10-17 高危区推荐窗：一键编队字 y 648 / 892 / 1135）；窗里一屏 3 行


def rec_row_xy(row):
    return REC_ROW1_BTN[0], REC_ROW1_BTN[1] + (row - 1) * REC_ROW_DY


def tab_selected(img, xy) -> bool:
    """推荐阵容窗页签：选中是白 / 灰底（10-17 均值 BGR≈165–172 三通道接近），没选中是蓝底（≈208,177,102）。"""
    x, y = xy
    b, g, r = img[y - 20:y + 20, x - 120:x + 120].reshape(-1, 3).mean(0)
    return abs(b - r) < 40 and r > 140


def hot_tab_selected(img) -> bool:
    return tab_selected(img, REC_HOT_TAB)


def rec_tab(it, name="官方推荐"):
    """推荐阵容窗里页签的位置（按字找）。10-17：前三区是「热门阵容 | 官方推荐」，高危区是「官方推荐 | 精选推荐」，位置不固定。"""
    return has(it, name, (300, 330, 1500, 430))


def rec_row_info(it, row):
    """推荐阵容窗第 row 行的（预计耗时, 完整度）文字；官方推荐 / 高危区没有预计耗时（None）。"""
    dy = (row - 1) * REC_ROW_DY
    t = next((t for t, x, y in it if 1520 <= x <= 1720 and 490 + dy <= y <= 570 + dy and ":" in t), None)
    c = next((t for t, x, y in it if 1560 <= x <= 1720 and 580 + dy <= y <= 650 + dy and "%" in t), None)
    return t, c


def apply_recommend(log=print, tab="官方推荐", row=1):
    """选人页 → 推荐阵容窗 → 官方推荐（按字找）+ 完整度优先 ON → 第 row 行（1–3）一键编队 → 回到选人页。返回该行（预计耗时, 完整度）。
    10-17 作者：同一阵容两次没有明显提升甚至下滑就换阵容 → 整跑脚本会换第 2、3 行。
    一键编队只换人和人偶，不动武器圣痕（窗口底注）。"""
    tap(*REC_PORTRAIT, 2.5)
    it, img = ocr()
    if not has(it, "推荐阵容"):
        raise NavError(f"点女武神推荐头像没弹推荐阵容窗：{[t for t, _, _ in it][:20]}")
    pos = rec_tab(it, tab)
    if not pos:
        raise NavError(f"推荐阵容窗里没找到「{tab}」页签：{[t for t, _, _ in it][:20]}")
    if not tab_selected(img, pos):
        tap(*pos, 2.0)
        it, img = ocr()
        if not tab_selected(img, pos):
            raise NavError(f"「{tab}」页签没切过去")
    if not has(it, "ON", (1940, 340, 2120, 420)):
        tap(*REC_TOGGLE, 2.0)
        it, img = ocr()
        if not has(it, "ON", (1940, 340, 2120, 420)):
            raise NavError("完整度优先没打开")
    if not 1 <= row <= 3:
        raise NavError(f"推荐阵容只认一屏的第 1–3 行，给的是第 {row} 行")
    t1, c1 = rec_row_info(it, row)
    if c1 is None:
        raise NavError(f"推荐阵容第 {row} 行没读到完整度（可能没有这一行）")
    log(f"  推荐阵容（{tab}、完整度优先）第 {row} 行：预计耗时 {t1}，完整度 {c1} → 一键编队")
    tap(*rec_row_xy(row), 3)
    for _ in range(3):                                  # 10-17：窗口关得慢，马上截图还看得到窗口
        it, img = ocr()
        if page(it) == "team" and not has(it, "推荐阵容"):
            return t1, c1
        time.sleep(1.5)
    raise NavError(f"一键编队后没回到选人页：{[t for t, _, _ in it][:20]}")


# ---------- 结算 ----------
def read_result(it):
    """战斗结算页（10-17：区名 / 最高连击数 / 战斗耗时 / 战斗得分，数值在右边同一行）→ {耗时秒, 得分}。"""
    def val(label):
        ly = next((y for t, x, y in it if label in t), None)
        if ly is None:
            return None
        cands = [t for t, x, y in it if x > 2000 and abs(y - ly) <= 30]
        return cands[0] if cands else None
    tm, sc = val("战斗耗时"), val("战斗得分")
    secs = None
    m = re.match(r"(\d+)[:：](\d+)", tm or "")
    if m:
        secs = int(m.group(1)) * 60 + int(m.group(2))
    return {"time": tm, "secs": secs, "score": digits(sc) if sc else None}


def settle(log=print):
    """结算页 → 点任意处 → 主页上的「战斗结算」层（当前积分 / 战斗时长）→ 点掉 → 主页。返回结算页读数 + 结算层读数。
    10-27：10 轮 → 40 轮（约 2 分钟）。战斗循环先到上限、游戏里这场还没打完时，前面几轮都在等收尾，10 轮不够用。"""
    res, layer = {}, {}
    for _ in range(40):
        it, img = ocr()
        if has(it, "战斗得分") and has(it, "战斗耗时"):
            res = read_result(it)
            log(f"  结算页：战斗耗时 {res['time']}，战斗得分 {res['score']}")
            tap(1200, 1300, 3.5)
        elif has(it, "战斗结算"):
            ly = {k: next((y for t, x, y in it if k in t), None) for k in ("当前积分", "战斗时长")}
            for k, y0 in ly.items():
                if y0 is not None:
                    v = [t for t, x, y in it if 1250 <= x <= 1700 and abs(y - y0) <= 40]
                    layer[k] = v[0] if v else None
            log(f"  结算层：{layer}")
            tap(1200, 1300, 3)
        elif has(it, "战斗失败"):
            # 10-25：放弃战斗（爬楼梯兜底）/ 打输后是「战斗失败」页（只有左下「攻略」按钮），点中间空白处直接回主页
            log("  战斗失败页，点空白处回主页")
            tap(1400, 1150, 3)
        elif page(it) == "main":
            return res, layer
        else:
            time.sleep(2)
    raise NavError(f"结算后没回到主页：{[t for t, _, _ in it][:20]}")


# ---------- 进超弦空间 + 判断本期开没开（10-17 作者：每周三、周日 22:00 结算，结算后到下期开放前进不去）----------
OTHER_CARDS = ("记忆战场", "万象虚境", "往世乐土", "角色挑战")


def card_texts(it, pos):
    """出击页「超弦空间」卡上的字。10-16 推荐页签：进行中1天 / 当前进度：0/4，在卡名右边同一张卡里；
    10-18 挑战页签卡窄：「结算中 14 分」在卡名上方一条，原来的框读到了右边记忆战场卡 → 加上方一条、截掉别的卡。"""
    right = min([x for t, x, y in it if t in OTHER_CARDS and abs(y - pos[1]) < 80] + [pos[0] + 800]) - 100
    top = [t for t, x, y in it if pos[0] - 120 <= x <= pos[0] + 340 and pos[1] - 110 <= y <= pos[1] - 20]
    return top + [t for t, x, y in it if pos[0] - 60 <= x <= min(pos[0] + 700, right) and 1170 <= y <= 1320
                  and t != "超弦空间" and t not in top]


def preparing(it):
    """准备中：上一期结算后到下一期开放前，主页照样能进，右下「开始战斗」灰、上面橙色标签「准备中 01天13小时」
    （11-05 01:02 实拍，识字中心约 2113,1287）。以前只认结算中，把准备中当开着、点开始战斗报 NavError。返回标签文字或 None。"""
    for t, x, y in it:
        if "准备中" in t and 1700 <= x <= 2400 and 1180 <= y <= 1360:
            return t
    return None


def main_state(it) -> str:
    if settling(it):
        return "settling"
    return "preparing" if preparing(it) else "open"


def enter(log=print):
    """任意页 → 超弦空间主页。返回 ("open" | "settling", 文字, 图) 或 ("closed", 卡上和点后画面的字, 图)。
    结算期（周三、周日 22:00–22:30）也能进主页，但开始战斗是灰的 → settling（10-18 实拍）。
    未开放时长什么样没见过：点卡后没到主页、还在出击页或认不出 = 当未开放，截图留证。"""
    it, img = [], None
    for _ in range(12):
        it, img = ocr()
        pg = page(it)
        if pg == "main":
            return main_state(it), it, img
        if pg == "sortie":
            pos = has(it, "超弦空间", (0, 1100, 2400, 1350))
            card = card_texts(it, pos)
            log(f"  出击页卡片：{card}")
            tap(*pos, 4)
            it2, img2 = ocr()
            if page(it2) == "main":
                return main_state(it2), it2, img2
            for _ in range(2):                         # 可能是加载慢：再等等
                time.sleep(2)
                it2, img2 = ocr()
                if page(it2) == "main":
                    return main_state(it2), it2, img2
            shot = HERE / "shots" / f"未开放_{time.strftime('%m%d-%H%M%S')}.png"
            cv2.imwrite(str(shot), img2)
            words = [t for t, _, _ in it2][:30]
            log(f"  点卡后没进主页（当本期未开放），画面文字：{words}；截图 {shot.name}")
            return "closed", card + words, img2
        if pg == "rules":
            pos = has(it, "关闭", (2000, 1250, 2300, 1380))
            tap(*(pos or (2210, 66)), 2)
        elif pg == "tasks":
            tap(93, 69, 2)
        elif pg == "recommend":
            tap(2181, 166, 2)
        elif pg == "preset":
            tap(226, 76, 2.5)
        elif pg in ("stage", "team"):
            tap(*BACK, 3)
        elif pg == "sortie_tab":
            tap(1363, 276, 3)                          # 10-22：点「挑战」页签再找（同记忆战场 ma_nav）；以前这里按返回，来回 12 轮报错
        elif pg == "home":
            tap(2200, 750, 4)
        elif has(it, "主菜单", (380, 30, 560, 110)):
            tap(469, 65, 4)
        else:
            tap(*BACK, 3)
    raise NavError(f"没进到超弦空间：{[t for t, _, _ in it][:25]}")


def trophy_change(it, me):
    """自己那行「奖杯」列（x≈2060–2160）：「+150」→ 150，「-100」→ -100；读不出 None。"""
    if me is None:
        return None
    y = ROW_Y[me]
    for t, x, y2 in it:
        if 1980 <= x <= 2200 and abs(y2 - y) <= 35:
            m = re.search(r"([+\-－])\s*(\d+)", t.replace("—", "-"))
            if m:
                return int(m.group(2)) * (-1 if m.group(1) in "-－" else 1)
    return None


def claim_tasks(log=print):
    """主页「深渊任务」→ 有「领取」就点、弹「获得奖励」点「确定」，领完的会排到下面（10-17 实测）→ 关面板。返回领了几项。
    10-25：整跑最后读区状态会停在关卡页，在关卡页点 (171,990) 打不开面板、记成「领过了」→ 先回主页；
    领奖弹框这次标题是「领取奖励」（以前是「获得奖励」），两种都认。"""
    to_main(log)
    tap(171, 990, 3)
    n = 0
    for _ in range(8):
        it, img = ocr()
        if has(it, "获得奖励") or has(it, "领取奖励"):
            pos = has(it, "确定", (1000, 1040, 1400, 1160)) or (1196, 1100)
            tap(*pos, 2.5)
            continue
        pos = has(it, "领取", (2000, 420, 2250, 1150))
        if not pos:
            break
        tap(*pos, 3)
        n += 1
        log(f"  深渊任务：点「领取」@{pos}")
    tap(93, 69, 2)
    return n
