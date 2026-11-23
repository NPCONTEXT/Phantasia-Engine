"""视觉识别：多尺度模板匹配 + 区域分析。

模板库位于 assets/seals2/（刻印图标），后续可扩展 UI 模板（三选一界面、商店、战斗中图标等）。
匹配结果以「屏幕实际像素」坐标返回；人工标注坐标统一用 2400x1504 基准，见 adb.BASE。
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
SEALS_DIR = PROJECT / "assets" / "seals2"


def imdecode(png_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")
    return img


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def load_templates(directory: Path = SEALS_DIR) -> dict[str, np.ndarray]:
    """加载 <name>.png -> 灰度模板。"""
    out = {}
    for f in sorted(directory.glob("*.png")):
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        out[f.stem] = img
    return out


def match_template(
    screen_gray: np.ndarray,
    tmpl: np.ndarray,
    scales: tuple[float, ...] = (1.0,),
    roi: tuple[int, int, int, int] | None = None,
    conf: float = 0.75,
) -> tuple[float, int, int] | None:
    """在 screen 中找 tmpl（支持多尺度），返回 (置信度, x, y) 或 None。

    roi: (x, y, w, h) 可选，限制搜索区域（屏幕像素坐标）。默认全屏。
    """
    sh, sw = screen_gray.shape[:2]
    if roi:
        rx, ry, rw, rh = roi
        search = screen_gray[ry : ry + rh, rx : rx + rw]
        ox, oy = rx, ry
    else:
        search = screen_gray
        ox, oy = 0, 0

    best: tuple[float, int, int] = (0.0, 0, 0)
    for sc in scales:
        th, tw = int(tmpl.shape[0] * sc), int(tmpl.shape[1] * sc)
        if th < 8 or tw < 8 or th >= search.shape[0] or tw >= search.shape[1]:
            continue
        resized = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        if maxv > best[0] and maxv >= conf:
            best = (float(maxv), int(maxloc[0]) + ox, int(maxloc[1]) + oy)
    return best if best[0] >= conf else None


def find_seal(
    screen_gray: np.ndarray,
    templates: dict[str, np.ndarray],
    roi: tuple[int, int, int, int] | None = None,
    conf: float = 0.72,
    scales: tuple[float, ...] = (0.6, 0.75, 0.9, 1.0, 1.15, 1.3, 1.5),
) -> tuple[str, float, int, int] | None:
    """在画面中找任意已知刻印，返回 (名称, 置信度, x, y) 或 None。"""
    best = None
    for name, tmpl in templates.items():
        hit = match_template(screen_gray, tmpl, scales=scales, roi=roi, conf=conf)
        if hit and (best is None or hit[0] > best[1]):
            best = (name, hit[0], hit[1], hit[2])
    return best


def find_all_seals(
    screen_gray: np.ndarray,
    templates: dict[str, np.ndarray],
    roi: tuple[int, int, int, int] | None = None,
    conf: float = 0.72,
    scales: tuple[float, ...] = (0.6, 0.75, 0.9, 1.0, 1.15, 1.3, 1.5),
) -> list[dict]:
    """找出画面里所有已知刻印的位置（每个模板取全图最佳匹配）。"""
    hits = []
    for name, tmpl in templates.items():
        hit = match_template(screen_gray, tmpl, scales=scales, roi=roi, conf=conf)
        if hit:
            hits.append({"name": name, "conf": round(hit[0], 3), "x": hit[1], "y": hit[2]})
    return hits


def annotate(img: np.ndarray, hits: list[dict], th: int, tw: int, color=(0, 255, 0)) -> np.ndarray:
    out = img.copy()
    for h in hits:
        x, y = h["x"], h["y"]
        cv2.rectangle(out, (x - tw // 2, y - th // 2), (x + tw // 2, y + th // 2), color, 3)
        cv2.putText(out, h["name"], (x - tw // 2, y - th // 2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return out


if __name__ == "__main__":
    from lib.adb import Adb

    a = Adb()
    img = imdecode(a.screencap())
    g = gray(img)
    tmpls = load_templates()
    print("templates:", list(tmpls.keys()))
    hits = find_all_seals(g, tmpls)
    print("hits:", hits)
