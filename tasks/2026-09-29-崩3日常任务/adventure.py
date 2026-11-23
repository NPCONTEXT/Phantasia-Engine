"""冒险委托调试入口（逻辑都在 bh3_daily.Bot 里，日常脚本用 --only adventure 跑完整流程）。

用法：adventure.py go          进开放世界并打开委托界面（不接取）
      adventure.py exit        委托列表/委托板/开放世界 → 主界面
      adventure.py cycle N     go+exit 循环 N 次（验证走路可重复）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cv2  # noqa: E402
from bh3_daily import Bot, log  # noqa: E402


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "go"
    bot = Bot()
    if cmd == "exit":
        return 0 if bot.adv_exit() else 1
    rounds = int(sys.argv[2]) if cmd == "cycle" and len(sys.argv) > 2 else 1
    ok_n = 0
    for r in range(1, rounds + 1):
        log(f"== 第 {r} 轮：进开放世界")
        t0 = time.time()
        ok = bot.adv_enter_world() and bot.adv_go_board()
        cv2.imwrite(str(HERE / "shots" / f"adv_r{r}_{'ok' if ok else 'fail'}.png"), bot.grab())
        log(f"  结果：{'ok' if ok else 'fail'}，用时 {time.time() - t0:.0f}s")
        ok_n += ok
        if cmd == "cycle":
            bot.adv_exit()
    log(f"完成：{ok_n}/{rounds}")
    return 0 if ok_n == rounds else 1


if __name__ == "__main__":
    sys.exit(main())
