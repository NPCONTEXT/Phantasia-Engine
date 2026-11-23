"""乐土浅层 · 战斗执行（愈生佑翎等 ring_normal 打法）

实测坐标（2400x1504）：
  星之环爆发  (1700, 1200)  蓝紫色 + 150/150 能量；开启后环形倒计时，能量耗尽归零，充满可再点
  必杀技/大招 (2205, 1005)  橙金色 + ⚡150（能量不足=无效点击，多按无妨）
  普攻键      (2210, 1305)  右下最大圆
  支援①/②    (2072, 341) / (2072, 624)
战斗结束判定：浅层普攻键模板连续两帧消失
"""
import sys, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

import cv2
from lib.adb import Adb
from lib.vision import imdecode, gray, match_template
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
RING = (1700, 1200)
WEAPON = (1950, 1090)     # 武器技（4 普攻触发 1s 可用窗口；实测坐标）
ULT = (2205, 1005)
ATK = (2210, 1305)
SUP1 = (2245, 405)   # 支援①头像中心（实测：点文字无效）
SUP2 = (2250, 670)   # 支援②头像中心
# 多角色普攻键模板（图标随角色不同，位置固定 (2210,1305)）
RING_TPLS = [
    str(PROJECT / 'assets/seals2/ui_attack_ring_shallow.png'),   # 愈生佑翎（白莲）
    str(PROJECT / 'assets/seals2/ui_attack_ring_yt.png'),        # 窈窕谍影（人形）
]
RING_INTERVAL = 12      # 每 N 轮再点一次星之环（≈30s，充满才生效）


TPL_MODE = {
    'ui_attack_ring_shallow.png': 'ring_normal',    # 愈生佑翎（白莲）
    'ui_attack_ring_yt.png': '4atk_weapon',         # 窈窕谍影（人形）
}


def battle_mode(src):
    """战斗中识别角色打法（普攻键图标=角色身份）。"""
    png = src.screencap() if hasattr(src, 'screencap') else src
    g = gray(imdecode(png))
    for tpl in RING_TPLS:
        t = cv2.imread(tpl, cv2.IMREAD_GRAYSCALE)
        if t is None:
            continue
        if match_template(g, t, scales=(0.9, 1.0, 1.1), conf=0.60):
            return TPL_MODE.get(Path(tpl).name)
    return None


def _in_battle(src) -> bool:
    """src: Adb 对象 或 png bytes。多模板匹配（适配不同角色普攻键图标）。"""
    png = src.screencap() if hasattr(src, 'screencap') else src
    g = gray(imdecode(png))
    for tpl in RING_TPLS:
        t = cv2.imread(tpl, cv2.IMREAD_GRAYSCALE)
        if t is None:
            continue
        if match_template(g, t, scales=(0.9, 1.0, 1.1), conf=0.60):
            return True
    return False


def _is_prep(adb: Adb) -> bool:
    """是否已回到备战页（OCR 特征）。"""
    import subprocess, tempfile, os
    png = adb.screencap()
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png); p = f.name
    try:
        out = subprocess.run([str(PROJECT / 'ocr/ocr'), p], capture_output=True, text=True, timeout=20).stdout
    finally:
        os.unlink(p)
    return '当前挑战角色' in out


def _has_result_popup(png: bytes) -> bool:
    """结算弹窗检测：OCR 关键词（只在战斗按钮消失后调用，频率低）。"""
    import subprocess, tempfile, os, json
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png); p = f.name
    try:
        out = subprocess.run([str(PROJECT / 'ocr/ocr'), p], capture_output=True, text=True, timeout=20).stdout
    finally:
        os.unlink(p)
    return ('挑战结束' in out) or ('点击空白处关闭' in out)


def fight(adb: Adb = None, mode: str = 'ring_normal', max_seconds: int = 400,
          ring_first: bool = True) -> bool:
    """执行一场战斗。返回 True=正常结束（按钮消失），False=超时/异常。"""
    adb = adb or Adb()
    if mode == '4atk_weapon':
        return fight_yt_chain(adb, max_seconds)
    t0 = time.time()
    # 入战：支援各点一次
    adb.tap(SUP1[0], SUP1[1], delay_after=0.05)
    adb.tap(SUP2[0], SUP2[1], delay_after=0.05)
    if ring_first or mode == 'ring_normal':
        adb.tap(RING[0], RING[1], delay_after=0.05)
        print('   [战斗] 开启星之环', flush=True)
    import threading
    stop = threading.Event()

    def atk_thread():
        """普攻线程（统一节奏，不识别角色）：
        4 普攻 + 1 武器技循环（窈窕谍影：4 普攻触发 1s 武器技窗口；
        其他角色武器技点了无害/CD 中无效）。异常自愈不退出。"""
        while not stop.is_set():
            try:
                adb._run(["shell",
                          f"for i in $(seq 1 4); do input tap {ATK[0]} {ATK[1]}; done; "
                          f"input tap {WEAPON[0]} {WEAPON[1]}"], timeout=10)
                time.sleep(0.15)
            except Exception as e:
                print(f'   [普攻线程] 异常自愈: {e}', flush=True)
                time.sleep(0.6)

    def skill_thread():
        """技能：独立线程并行点（大招/支援交替 ~1.2s；星环每 ~25s）。"""
        k = 0
        while not stop.is_set():
            time.sleep(0.8)
            if stop.is_set():
                break
            try:
                # 只点支援（大招会吃掉星之环加成，禁用）
                adb.tap(SUP1[0] if k % 2 == 0 else SUP2[0],
                        SUP1[1] if k % 2 == 0 else SUP2[1], delay_after=0)
                k += 1
                if k % 20 == 0:                 # ~20s，星环充满才生效
                    adb.tap(RING[0], RING[1], delay_after=0)
                    print('   [战斗] 技能线程 · 再点星之环', flush=True)
            except Exception as e:
                print(f'   [技能线程] 异常自愈: {e}', flush=True)

    t_atk = threading.Thread(target=atk_thread, daemon=True)
    t_skl = threading.Thread(target=skill_thread, daemon=True)
    t_atk.start(); t_skl.start()
    try:
        while time.time() - t0 < max_seconds:
            time.sleep(4)
            try:
                alive = _in_battle(adb)
            except Exception as e:
                print(f'   [战斗] 状态查询异常自愈: {e}', flush=True)
                continue
            if not alive:
                time.sleep(1.0)
                if not _in_battle(adb):
                    stop.set()
                    time.sleep(0.2)
                    adb.tap(1200, 750, delay_after=0.3)     # 点任意处
                    time.sleep(1.5)
                    if _is_prep(adb):
                        print(f'   [战斗] 挑战结束，已回备战页（{int(time.time()-t0)}s）', flush=True)
                        return True
                    print('   [战斗] 波次切换 → 继续打', flush=True)
                    adb.tap(RING[0], RING[1], delay_after=0)
                    stop.clear()
                    t_atk = threading.Thread(target=atk_thread, daemon=True)
                    t_skl = threading.Thread(target=skill_thread, daemon=True)
                    t_atk.start(); t_skl.start()
    finally:
        stop.set()
    print('   [战斗] 超时退出', flush=True)
    return False


if __name__ == '__main__':
    fight()


def fight_yt_chain(adb: Adb = None, max_seconds: int = 400) -> bool:
    """窈窕谍影：武器技独立线程狂点 + 主链控节奏。
    主链: 开局星之环 -> 循环[ 支援×1 -> 普攻×4 -> 普攻×4 -> 星之环×1 ]
    武器技线程: 持续高频点击（不亮=无效无害；危险档乐土特性满，支援后武器技无限用）
    """
    import threading
    adb = adb or Adb()
    t0 = time.time()
    stop = threading.Event()
    adb.tap(RING[0], RING[1], delay_after=0.2)
    print('   [战斗] 窈窕谍影：开局星之环 + 武器技线程启动', flush=True)

    def weapon_thread():
        while not stop.is_set():
            try:
                adb._run(["shell",
                          f"for i in 1 2 3 4 5 6 7 8; do input tap {WEAPON[0]} {WEAPON[1]}; done"],
                         timeout=10)
            except Exception as e:
                print(f'   [武器技线程] 异常自愈: {e}', flush=True)
                time.sleep(0.5)
            time.sleep(0.2)

    t_w = threading.Thread(target=weapon_thread, daemon=True)
    t_w.start()

    sup_i = 0
    round_i = 0
    try:
        while time.time() - t0 < max_seconds:
            sup = SUP1 if sup_i % 2 == 0 else SUP2
            sup_i += 1
            # 主链：支援 -> 普攻×4 -> 普攻×4 -> 星之环（武器技交给独立线程）
            cmd = (f"input tap {sup[0]} {sup[1]}; "
                   f"for i in 1 2 3 4; do input tap {ATK[0]} {ATK[1]}; done; "
                   f"for i in 1 2 3 4; do input tap {ATK[0]} {ATK[1]}; done; "
                   f"input tap {RING[0]} {RING[1]}")
            try:
                adb._run(["shell", cmd], timeout=12)
            except Exception as e:
                print(f'   [主链] 异常自愈: {e}', flush=True)
                time.sleep(0.5)
            round_i += 1
            if round_i % 3 == 0:
                if not _in_battle(adb):
                    time.sleep(0.8)
                    if not _in_battle(adb):
                        stop.set()
                        time.sleep(0.2)
                        adb.tap(1200, 750, delay_after=0.3)
                        time.sleep(1.5)
                        if _is_prep(adb):
                            print(f'   [战斗] 挑战结束，已回备战页（{int(time.time()-t0)}s）', flush=True)
                            return True
                        print('   [战斗] 波次切换 → 继续', flush=True)
                        adb.tap(RING[0], RING[1], delay_after=0.2)
                        stop.clear()
                        t_w = threading.Thread(target=weapon_thread, daemon=True)
                        t_w.start()
            time.sleep(0.05)
    finally:
        stop.set()
    print('   [战斗] 超时退出', flush=True)
    return False
