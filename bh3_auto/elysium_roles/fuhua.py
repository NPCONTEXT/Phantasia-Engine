"""乐土角色档案：符华·渡尘之羽（元素普攻 · 低血流）。

2026-10-20 从 scripts/master.py、er_entry.py、er_runtime.py、shop_buy.py、lib/door_model.py 原样搬来，数值和判断一个没改
（tests/elysium_stage1/test_roles.py 拿改档案前的原函数逐项对照）。
加新角色：复制这份改名（文件名 = 角色 id，只用小写字母、数字、下划线），按下面 ①–⑨ 改内容；
ATTACK_TEMPLATE（普攻键截图）和 PREP_WORDS（备战页认人）必须换成新角色自己的，其余照抄也能跑，只是口味是符华的。
"""
ID = 'fuhua'
LABEL = '符华·渡尘之羽'
DESC = '元素普攻 · 低血流'

# ① 周常页补勾增益：关键词和分数（在剩余点数里挑总分最高的组合，只补不删）
BUFF_WORDS = (('元素', 4), ('臂铠', 4), ('拳套', 4), ('普攻', 4), ('普通攻击', 4),
              ('鏖灭', 3), ('浮生', 2), ('天慧', 2), ('戒律', 1), ('旭光', 1))

# ② 备战页认人：右边「角色调整」里本角色独有的词，读到任意一个就算出战的是它（10-07、10-14 三次备战页都读到这三个）
PREP_WORDS = ('鸢火之壁', '琉璃朱燧', '战士的回忆')

# ③ 认「战斗中」：本角色普攻键图标（相对 bh3_auto/）；换角色图标不同，认不出会把战斗当成大世界去找门
ATTACK_TEMPLATE = 'assets/seals2/ui_attack_ring.png'


# ④ 刻印打分：选普通刻印时取分最高的那张
def seal_score(text):
    good = ('元素伤害', '元素穿透', '普通攻击', '普攻', '连击', '撕裂', '全伤害', '生命越低', '低生命', '鏖灭', '浮生', '天慧', '戒律')
    bad = ('武器技', '闪避', '必杀技', '召唤物', '银币')
    score = sum(2 for k in good if k in text) - sum(2 for k in bad if k in text)
    if '玄迹' in text or '观化' in text:
        score += 20
    if '极诣' in text:
        score += 10
    if '物理伤害' in text and '元素' not in text:
        score -= 4
    if ('怪物受到' in text or '敌人受到' in text) and ('提高' in text or '增加' in text):
        score += 3
    if '受到的全伤害' in text and '怪物' not in text and '敌人' not in text and ('降低' in text or '减少' in text):
        score -= 3
    return score


# ⑤ 核心刻印：groups 是 [(标题项, 整卡文字)]，按页面从上到下；符华直接选第一张
def pick_core(groups, group=''):   # group = 页面标题里的刻印组名（10-20 加的参数，符华不用）
    return groups[0]


# ⑥ 锚点（下一层去哪）偏好顺序，两次刷新都没有就停
ANCHOR_ORDER = ('鏖灭', '浮生', '天慧', '戒律', '旭光', '真我', '繁星', '救世', '黄金', '空梦')

# ⑦ 门：不进的刻印门；找门模型优先走过去的门（模型标签名）
BAD_DOORS = ('刹那', '螺旋', '无限')
DOOR_PREFER = ('aomie', 'fusheng', 'tianhui', 'jielv')


# ⑧ 商店：商品打分（已售罄由引擎先排除），分数够 SHOP_MIN_SCORE 才买
def shop_score(title, desc):
    blob = title + ' ' + desc
    if any(k in blob for k in ('武器技', '闪避', '召唤物', '物理穿透')):
        return -100
    if '物理伤害' in blob and '元素' not in blob:
        return -100
    score = 3 if any(k in blob for k in ('鏖灭', '浮生', '天慧', '戒律')) else 0
    score += sum(2 for k in ('元素伤害', '元素', '普攻', '全伤', '连击', '生命越低', '撕裂') if k in blob)
    if '必杀技' in blob and '元素' not in blob and '全伤' not in blob:
        score -= 3
    return score


SHOP_MIN_SCORE = 4


# ⑨ 战斗手法：主控每轮调一次，打完再看一次画面。普攻连点 10 下 + 三个键，共 3 组
def fight(c):
    for _ in range(3):
        for _ in range(10):
            c.tap(2175, 1278, active=True)
        for x, y in ((2170, 960), (2180, 690), (2180, 830)):
            c.tap(x, y, active=True)
