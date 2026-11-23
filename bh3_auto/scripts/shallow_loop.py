"""乐土浅层 · 自动刷星主控

流程（实测）：
  备战页(选档/看星) --开始战斗--> 队伍配置页(出战位/支援位) --开始战斗--> 战斗
  → 结算页(待补) → 回备战页 → 重读星级 → 循环
规则：
  目标档 = 第一个「未三星且未锁定」的档（安全→普通→危险）
  该档三星 → 下一档；未三星 → 重打（上限 N 次）；危险档上锁 → 跳过
用法: .venv/bin/python scripts/shallow_loop.py [最大循环]
"""
from __future__ import annotations
import sys, time, re, json, subprocess, tempfile, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

from lib.adb import Adb
from lib.vision import imdecode
from lib.shallow_detect import scan, is_locked
from scripts.shallow_battle import fight, _in_battle, battle_mode
from rotations import get_rotation
try:        # 11-02 暂停协议（幻想引擎的 lib/play_ctl.py，经 PYTHONPATH 传进来）；单独运行时 import 不到，用什么都不做的替身
    from play_ctl import pause_point as xa_pause, mark as xa_mark
except ImportError:
    def xa_pause():
        return False

    def xa_mark(*a, **k):
        pass

PROJECT = __import__('pathlib').Path(__file__).resolve().parent.parent
a = Adb()

BTN_FIGHT = (2098, 1369)          # 「开始战斗」（备战页 / 队伍配置页 同坐标）
RESULT_BLANKS = [(1200, 220), (200, 1300), (2200, 1300), (1200, 1460)]  # 结算页空白候选点
TIER_CLICK = {'safe': (220, 890), 'normal': (220, 1048), 'danger': (220, 1205)}
TIER_NAME = {'safe': '安全', 'normal': '普通', 'danger': '危险'}


def ocr_text(png: bytes):
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png); p = f.name
    try:
        out = subprocess.run([str(PROJECT / 'ocr/ocr'), p], capture_output=True, text=True, timeout=25).stdout
    finally:
        os.unlink(p)
    items = []
    for line in out.splitlines():
        if line.startswith('{'):
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items


def page_of(items) -> str:
    t = ' '.join(i.get('text', '') for i in items)
    if '出战位' in t and '支援位' in t:
        return 'team'          # 队伍配置页
    if '当前挑战角色' in t and '难度选择' in t:
        return 'prep'          # 备战页
    if '挑战结束' in t or '点击空白处关闭' in t:
        return 'result'        # 结算弹窗
    if '挑战目标' in t or '最高连击数' in t or '剩余时间' in t:
        return 'result'        # 结算统计页（点任意处退出）
    return 'unknown'


def read_role(items) -> str:
    """备战页角色名：在「当前挑战角色」下方。"""
    anchor = next((i for i in items if '当前挑战角色' in i.get('text', '')), None)
    if not anchor:
        return ''
    y0 = anchor['y']
    cands = [i for i in items if y0 < i['y'] < y0 + 500 and i['x'] < 600 and i.get('conf', 0) >= 0.5
             and len(i.get('text', '')) >= 2 and '装甲' not in i['text'] and '序列' not in i['text']]
    cands.sort(key=lambda i: i['y'])
    return cands[0]['text'] if cands else ''


def _still_result(adb: Adb) -> bool:
    """OCR 检查是否仍在结算页/弹窗。"""
    items = ocr_text(adb.screencap())
    t = ' '.join(i.get('text', '') for i in items)
    return ('挑战结束' in t) or ('点击空白处关闭' in t) or ('挑战目标' in t) or ('最高连击数' in t)


def pick_tier(img, farm: bool = False) -> str | None:
    """farm=False（默认·刷星）：第一个未三星且未锁的档；全三星 -> None（停止）
    farm=True（无限刷）：三档达成后 -> 永远返回 danger（反复刷危险档）"""
    s = scan(img)
    if farm and not s.get('danger_locked') and s.get('danger', 0) >= 3:
        return 'danger'          # 已达成仍继续打危险档（farm）
    for tier in ('safe', 'normal', 'danger'):
        if tier == 'danger' and s.get('danger_locked'):
            return None
        if s.get(tier, 0) < 3:
            return tier
    return None


def main():
    max_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    FARM = '--farm' in sys.argv          # 无限刷危险档模式
    cur_tier = None
    retry = 0
    cur_mode = ['ring_normal']      # 当前角色打法模式
    for rnd in range(max_rounds):
        png = a.screencap()
        img = imdecode(png)
        items = ocr_text(png)
        page = page_of(items)
        if _in_battle(png):
            page = 'battle'
        print(f'[{rnd}] 页面: {page}', flush=True)
        # 11-02 暂停：备战页 / 队伍页 / 结算页游戏在等人点，停在这里安全；战斗、加载时不能停
        if page in ('prep', 'team', 'result'):
            xa_mark(None)
            if xa_pause():
                continue                        # 停过：画面可能变了，下一轮重新截图再认
        else:
            xa_mark('乐土浅层正在战斗或加载', say='乐土浅层正在战斗或加载，回到备战页、结算页时才能停，请稍后再点')

        if page == 'battle':
            m = battle_mode(png)          # 战斗中按普攻键图标识别角色
            if m:
                cur_mode[0] = m
            fight(a, mode=cur_mode[0])
            continue
        if page == 'team':
            for _ in range(3):          # 连点3下（无延迟），防识别/加载慢
                a.tap(*BTN_FIGHT, delay_after=0)
            print('   -> 队伍配置页：开始战斗×3', flush=True)
            time.sleep(2)
            continue
        if page == 'prep':
            role = read_role(items)
            rot = get_rotation(role)
            cur_mode[0] = rot.get('mode', 'ring_normal')
            stars = scan(img)
            print(f'   角色={role} 打法={rot["mode"]} 星级={stars}', flush=True)
            tier = pick_tier(img, farm=FARM)
            if tier is None:
                if stars.get('danger', 0) >= 3:
                    print('   🎉 危险档三星达成 · 核心奖励已到手 → 停止循环', flush=True)
                else:
                    print('   ✅ 全部可达档位三星（危险未解锁）→ 停止循环', flush=True)
                return
            if FARM and tier == 'danger':
                print('   ♻️ FARM 模式：继续刷危险档', flush=True)
            if tier != cur_tier:
                cur_tier, retry = tier, 0
            if retry >= 5:
                print(f'   ⚠️ {TIER_NAME[tier]} 重打 {retry} 次仍未三星，结束', flush=True)
                return
            retry += 1
            a.tap(*TIER_CLICK[tier], delay_after=0.2)
            time.sleep(1.2)
            for _ in range(3):          # 连点3下（无延迟）
                a.tap(*BTN_FIGHT, delay_after=0)
            print(f'   -> 选档「{TIER_NAME[tier]}」(第{retry}次) → 开始战斗×3', flush=True)
            time.sleep(2.5)
            continue
        if page == 'result':
            # 统一：点空白处跳过（多点候选，逐个验证是否已离开结算页）
            for pt in RESULT_BLANKS:
                a.tap(pt[0], pt[1], delay_after=0.3)
                time.sleep(0.9)
                if not _still_result(a):
                    print(f'   -> 结算页：点空白 {pt} 跳过成功', flush=True)
                    break
            else:
                print('   -> 结算页：空白点均无效，等待', flush=True)
            time.sleep(0.8)
            continue
        # unknown = 加载/过渡页：点开始战斗加速（加载页点击无害），短轮询
        for _ in range(2):
            a.tap(*BTN_FIGHT, delay_after=0)
        print('   未知页面(加载中) → 点击开始战斗加速', flush=True)
        time.sleep(1.0)
    print('[结束] 循环上限')


if __name__ == '__main__':
    main()
