"""乐土浅层 · 角色打法表（单文件集中管理）

mode 说明：
  ring_normal   开启星之环 → 无限连点普攻（愈生佑翎等，另叠加大招/支援）
  4atk_weapon   4 次普攻 + 1 次武器技循环（窈窕谍影：4 普攻触发 1 秒武器技窗口）
  normal_only   仅无限连点普攻
  weapon_normal 武器技 + 普攻交替
  ult_normal    有大招点大招 + 普攻
未收录角色 → DEFAULT（仅普攻），并打日志便于后续补表。
"""
ROTATIONS = {
    # ── 已实测 ──
    "愈生佑翎": {"mode": "ring_normal", "ring_first": True, "ult": True, "support": True},
    "窈窕谍影": {"mode": "4atk_weapon", "ring_first": True, "ult": False, "support": True},

    # ── 待补充（按角色名 → 打法，几十个角色同格式）──
    # "角色名": {"mode": "normal_only"},
}

DEFAULT = {"mode": "normal_only", "ring_first": False, "ult": False, "support": False}


def get_rotation(role_name: str) -> dict:
    """按角色名取打法；支持别名/包含匹配。"""
    if not role_name:
        return dict(DEFAULT)
    if role_name in ROTATIONS:
        return dict(ROTATIONS[role_name])
    for k, v in ROTATIONS.items():
        if k in role_name or role_name in k:
            return dict(v)
    print(f'   ⚠️ 未收录角色「{role_name}」，使用默认打法(仅普攻)', flush=True)
    return dict(DEFAULT)
