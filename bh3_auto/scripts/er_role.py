"""乐土角色档案的加载：master.py 按 --role 选一份 elysium_roles/<id>.py，其余模块从 cur() 取角色专属的那几处
（增益关键词、备战页认人、普攻键模板、刻印 / 核心 / 锚点 / 门 / 商店口味、战斗手法）。
不传 --role = 符华，行为和 2026-10-20 改档案以前逐字一样。
"""
from __future__ import annotations
import importlib, re
from pathlib import Path

DEFAULT = 'fuhua'
ROLES_DIR = Path(__file__).resolve().parent.parent / 'elysium_roles'
REQUIRED = ('ID', 'LABEL', 'DESC', 'BUFF_WORDS', 'PREP_WORDS', 'ATTACK_TEMPLATE', 'seal_score', 'pick_core',
            'ANCHOR_ORDER', 'BAD_DOORS', 'DOOR_PREFER', 'shop_score', 'SHOP_MIN_SCORE', 'fight')
_cur = None


def load(role_id: str | None = None):
    rid = role_id or DEFAULT
    if not re.fullmatch(r'[a-z0-9_]+', rid):
        raise ValueError(f'角色 id 只能用小写字母、数字、下划线：{rid!r}')
    if not (ROLES_DIR / f'{rid}.py').is_file():
        raise ValueError(f'没有这个乐土角色档案：{rid}（elysium_roles/{rid}.py 不存在）')
    mod = importlib.import_module(f'elysium_roles.{rid}')
    missing = [k for k in REQUIRED if not hasattr(mod, k)]
    if missing:
        raise ValueError(f'角色档案 {rid} 缺：{"、".join(missing)}')
    if mod.ID != rid:
        raise ValueError(f'角色档案 {rid}.py 里写的 ID 是 {mod.ID!r}，和文件名对不上')
    return mod


def use(role_id: str | None = None):
    """选定这次运行用的角色（master.main 开头调一次）。"""
    global _cur
    _cur = load(role_id)
    return _cur


def cur():
    """当前角色；没选过就是符华（离线测试、单独跑 find_door_simple / shop_buy 时也一样）。"""
    global _cur
    if _cur is None:
        _cur = load(DEFAULT)
    return _cur


def listing() -> list[dict]:
    """所有角色档案（给 tools.json 的 roles 核对用）：[{id, label, desc}]，按 id 排。"""
    out = []
    for p in sorted(ROLES_DIR.glob('*.py')):
        if p.stem.startswith('_'):
            continue
        m = load(p.stem)
        out.append(dict(id=m.ID, label=m.LABEL, desc=m.DESC))
    return out
