"""乐土浅层 · 备战页识别（星级/锁定/角色）
实测基准 2400x1504：
  徽章三章中心 x ≈ 482 / 545 / 610（安全档）
  档位 y：safe 895 / normal 1048 / danger 1205
  黄章(得星): H 25±5, S≈157, V≈205  -> 判定: H18-35 & S>120 & V>150 占比>30%
  灰章(未得): S≈0, V≈183            -> 判定: 黄占比<10%
  危险档锁: 浅灰白挂锁 ROI (535-582, 1153-1215)
"""
import cv2
import numpy as np

# 每档徽章中心 x 各不相同（实测：安全 482/545/610、普通 475/538/601、危险 458/522/587）
BADGE_XS = {
    "safe": (482, 545, 610),
    "normal": (475, 538, 601),
    "danger": (458, 522, 587),
}
TIER_Y = {"safe": 895, "normal": 1048, "danger": 1192}
LOCK_ROI = (535, 1153, 582, 1215)      # x0,y0,x1,y1
# 兼容两种黄：饱和金黄(安全/普通 S≈157) 与 淡金/米黄(危险档选中态 S≈30-38)
# 只取徽章内圆（避开选中态金色边框干扰）：S>20 & V>140 占比 > 35%
YELLOW_LO = (5, 20, 140)
YELLOW_HI = (45, 255, 255)
BADGE_HALF = 15


def _is_star(hsv_roi) -> bool:
    """徽章是否得星：内圆区 S 最大值 > 150。
    实测：黄章(饱和金黄 S_max 235 / 淡金 194-207) vs 灰章(S_max 0-1) vs 紫背景(≈100)。"""
    return int(hsv_roi[:, :, 1].max()) > 150


def stars_of(img_bgr, tier: str) -> int:
    """返回该档已得星数（0-3）：徽章内圆有色彩(S>20)占比>35% 记 1 星。"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    y = TIER_Y[tier]
    n = 0
    for cx in BADGE_XS[tier]:
        roi = hsv[max(0, y - BADGE_HALF):y + BADGE_HALF, cx - BADGE_HALF:cx + BADGE_HALF]
        if _is_star(roi):
            n += 1
    return n


LOCK_TPL = str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'assets/seals2/ui_lock.png')


def is_locked(img_bgr) -> bool:
    """危险档是否上锁：锁模板匹配（实测有锁 0.9999 / 解锁后 0.72，阈值 0.85）。"""
    from lib.vision import match_template
    t = cv2.imread(LOCK_TPL, cv2.IMREAD_GRAYSCALE)
    if t is None:
        return False
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return bool(match_template(g, t, scales=(0.9, 1.0, 1.1), conf=0.85))


def scan(img_bgr) -> dict:
    return {
        "safe": stars_of(img_bgr, "safe"),
        "normal": stars_of(img_bgr, "normal"),
        "danger": stars_of(img_bgr, "danger"),
        "danger_locked": is_locked(img_bgr),
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "assets/full/shallow1.png"
    img = cv2.imread(path)
    print("识别结果:", scan(img))
