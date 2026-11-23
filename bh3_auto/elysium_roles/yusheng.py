"""乐土角色档案：愈生佑翎（雷电 · 普攻 · 天慧路线）。2026-10-20 按游戏里备战页「角色教学」通关推荐一写，当晚 17 层 20000 分实测。

推荐一原文：不开启星环爆发，长按攻击键连续使用普攻第三击，并自动衔接普攻第四击。
刻印推荐：专属「澈愿」的祝福（首选）、「静抚」的祝福、「沁灵」的祝福；通用 天慧Ⅱ、黄金Ⅰ、戒律Ⅱ、空梦Ⅰ
（Ⅰ / Ⅱ = 核心刻印页上面 / 下面那张，位置固定，作者说的）。
助战推荐：深空定锚·曙光、失落迷迭；追忆之证（开局）：轻，如一叶菩提（通用）、空，如一树娑罗（助战）——这三样在游戏里配好，脚本不改。

乐土图鉴原文（10-20 22:0x 截图 tasks/2026-10-20-乐土多角色/侦察截图/*-图鉴-*）：
- 「澈愿」：星环蓄能期间，持续按住攻击键会进入风汲形态：留下溯海之羽，并持续释放普通攻击第三击（期间可以移动）；
  第三击攻击间隔大幅降低，期间以固定间隔消耗简并动能触发溯海之羽；没有简并动能就发动第四击、回复 4 点；
  敌人受到来自她的全伤害提高 10%（独立加成），每持有一枚核心刻印额外获得一次该效果。
- 「静抚」：溯海之羽每触发 1 次共鸣技能，敌人受到的全伤害提高 8%（＋：10%），最多 32%（＋：40%），持续 10 秒。
- 「沁灵」：溯海之羽每触发 1 次共鸣技能，必杀技额外造成 400%（＋：500%）攻击力的雷电元素伤害，放必杀后重置计数。
- 「挚言」「笃煦」是星环爆发流的（推荐二、三），这里不要。
作者：「操作简单」是指拿到改普攻形态的核心刻印（澈愿）后，可以一直长按普攻不松手。
"""
import time

ID = 'yusheng'
LABEL = '愈生佑翎'
DESC = '雷电普攻 · 天慧路线（角色教学推荐一）'

# ① 周常页补勾增益：元素穿透那条 + 她用得上的刻印组（天慧、黄金、戒律）；空梦那条要极限闪避才生效，脚本不闪避，不要
BUFF_WORDS = (('元素穿透', 4), ('天慧', 3), ('黄金', 3), ('戒律', 3))

# ② 备战页认人：右边「角色调整」里有她的名字（10-20 备战页识字：「愈生佑翎的溯海之羽…」，溯常被读成湖，所以只取「海之羽」）
PREP_WORDS = ('愈生佑翎', '世界之星', '海之羽')

# ③ 认「战斗中」：她的普攻键图标（浅层乐土截的白莲图标；深层第一场战斗时核对，必要时换成深层截图）
ATTACK_TEMPLATE = ('assets/seals2/ui_attack_ring_shallow.png',)

# ③b 第 1 层第一次选专属刻印：三张里没有它就点「重置刻印」一直刷（作者：「第一轮的重置是可以无限的」）。
#     10-27 02:52 那局开局给的是 挚言 / 笃煦 / 静抚，脚本拿了静抚，一路没出澈愿，第 8 层首领连输两次。
FIRST_SEAL = '澈愿'


# ④ 刻印打分：专属三个最优先；通用要天慧 / 黄金 / 戒律 / 空梦；雷电、元素、普攻、连击加分；武器技、闪避用不上
def seal_score(text):
    good = ('雷电', '元素伤害', '元素穿透', '普通攻击', '普攻', '连击', '全伤害', '天慧', '黄金', '戒律', '空梦')
    bad = ('武器技', '闪避')
    score = sum(2 for k in good if k in text) - sum(2 for k in bad if k in text)
    if '澈愿' in text:
        score += 20
    if '静抚' in text or '沁灵' in text:
        score += 10
    if '物理伤害' in text and '元素' not in text:
        score -= 4
    if ('怪物受到' in text or '敌人受到' in text) and ('提高' in text or '增加' in text):
        score += 3
    return score


# ⑤ 核心刻印（每组二选一）：照攻略页推荐一 天慧Ⅱ、黄金Ⅰ、戒律Ⅱ、空梦Ⅰ，Ⅰ = 上面那张、Ⅱ = 下面那张；别的组选上面那张。
#    10-20 实跑三次核心页都选了上面那张，天慧（真言 / 秘言）、戒律（万般戒律 / 为戒律所佑）选反了。
#    专属刻印（澈愿等）不在这一页，在普通选刻印页，靠 ④ 的打分选。
CORE_PICK = {'天慧': 2, '黄金': 1, '戒律': 2, '空梦': 1}


def pick_core(groups, group=''):
    n = CORE_PICK.get(group, 1)
    return groups[n - 1] if len(groups) >= n else groups[0]


# ⑥ 锚点偏好：推荐的四组在前
ANCHOR_ORDER = ('天慧', '黄金', '戒律', '空梦', '真我', '繁星', '救世', '旭光', '浮生', '鏖灭')

# ⑦ 门：不进的刻印门（同符华）；找门模型优先走的门只认得鏖灭 / 浮生 / 天慧 / 戒律四种，选她用得上的两种
BAD_DOORS = ('刹那', '螺旋', '无限')
DOOR_PREFER = ('tianhui', 'jielv')


# ⑧ 商店：专属三个最想要；推荐的四组 +3；雷电 / 元素 / 普攻 / 连击 +2；武器技、闪避、物理穿透、纯物理伤害不买
def shop_score(title, desc):
    blob = title + ' ' + desc
    if any(k in blob for k in ('武器技', '闪避', '物理穿透')):
        return -100
    if '物理伤害' in blob and '元素' not in blob:
        return -100
    score = 0
    if '澈愿' in blob:
        score += 20
    if '静抚' in blob or '沁灵' in blob:
        score += 10
    if any(k in blob for k in ('天慧', '黄金', '戒律', '空梦')):
        score += 3
    score += sum(2 for k in ('雷电', '元素伤害', '元素', '普攻', '全伤', '连击') if k in blob)
    return score


SHOP_MIN_SCORE = 4

# ⑨ 战斗手法（推荐一），按这次运行拿没拿到「澈愿」分两种；不按星环爆发：
#    拿到后：手指 0 一直按住攻击键（风汲形态，持续放普攻第三击），手指 1 依次点 必杀 → 支援 1 → 必杀 → 支援 2 → 助战饰品，
#            间隔 1.5 秒；一轮点完返回时手指 0 还按着，截图识字时也不松；离开战斗由主循环松手（Controller.release_hold）。
#            每轮开头松开一瞬间再按（作者选）：22:56 那局第 9 层一直按着 1 分钟没打完，重按后 14 秒打完，
#            怀疑按久了游戏不认这根手指（没截到图，推断）；这样最多卡一轮（约 7 秒）。
#            从暂停页接着打是新的一次运行，不知道前面拿没拿到澈愿，会用老打法；手动实测可加 --seals 告诉脚本。
#    拿到前：长按 2.4 秒松手，再单指点必杀、支援（10-20 21:38 打满 17 层那套）。没有澈愿时按住攻击键只放一次分支攻击「羽落」
#            就不动了：10-20 22:14 那局没出澈愿、一直按住，第 8 层首领 155 秒几乎没输出，打输一次。
#    10-20 实跑：第 12 层首领（MHT-3B 天堂使者，8 管血、限时）不放必杀会超时，所以要点必杀。
#    10-20 23:30–23:46 实测（每轮重按版）：第 10–17 层战斗合计 547 秒（21:02 那局老打法 694 秒，两局刻印不同），
#    第 12 层首领 111 秒一次过；第二根手指点必杀游戏认（截图能量 881 → 734）。日志 logs/elysium_20261020-233030。
ATTACK = (2175, 1278)
ULT = (2214, 1020)                         # 必杀技（能量满了才放得出，没满点了没反应）
SUPPORTS = ((2256, 420), (2256, 696))      # 右上两个支援头像（冷却中点了没反应）
TRINKET = (1746, 924)                      # 助战饰品技能（充能 600 才放得出来）
GAP_S = 1.5
REPRESS_S = 0.08                           # 松开到再按下隔一点，免得游戏同一帧里看不到松开
HOLD_MS = 2400
# 11-12 首领层边打边绕圈（作者：「还是被打太多次了，被中断了不少操作……动一下方向键，一直往左或往右，转圈攻打boss」；
# 03:2x：「只在首领层」）。澈愿原文「持续释放普通攻击第三击（期间可以移动）」。那晚第 17 层首领「殉死之兽」输两次（限时到还剩约 2 管血）、
# 第三次清场时只剩 1 秒。深层序列首领在第 8、12、17 层（周常页「首领预览」）；层数不知道（从暂停页接着打还没读到）就不绕。
BOSS_FLOORS = (8, 12, 17)
STRAFE = (80, 1207)                        # 摇杆往左推满（同 Controller.move 的 220 半径）
STRAFE_RIGHT = (520, 1207)                 # 往右推满
# 11-12 03:4x 实机：一直往左「绕了一半多，后面卡在地图那里了」→ 作者同意每隔几轮换方向：
# 每 STRAFE_ROUNDS 轮（一轮约 7.5 秒）左右换一次，换层从左重新数。
STRAFE_ROUNDS = 3


def strafe_dir(c):
    """这一轮往哪推：同一层里第 0–2 轮左、3–5 轮右、6–8 轮左……换层清零。"""
    floor = getattr(c, 'floor', None)
    if getattr(c, 'strafe_floor', None) != floor:
        c.strafe_floor, c.strafe_round = floor, 0
    n = getattr(c, 'strafe_round', 0)
    c.strafe_round = n + 1
    return STRAFE if (n // STRAFE_ROUNDS) % 2 == 0 else STRAFE_RIGHT


def has_cheyuan(c):
    return any('澈愿' in s for s in getattr(c, 'seals', ()))


def fight(c):
    if not has_cheyuan(c):
        return fight_tap(c)
    if getattr(c, 'holding', None):
        c.release_hold()
        time.sleep(REPRESS_S)
    c.hold(*ATTACK)
    if getattr(c, 'floor', None) in BOSS_FLOORS:
        c.stick_hold(*strafe_dir(c))
    for sx, sy in SUPPORTS:
        time.sleep(GAP_S)
        c.mtap(*ULT)
        time.sleep(GAP_S)
        c.mtap(sx, sy)
    c.mtap(*TRINKET)


def fight_tap(c):
    for sx, sy in SUPPORTS:
        c._input(lambda: c.adb.swipe(ATTACK[0], ATTACK[1], ATTACK[0], ATTACK[1], HOLD_MS),
                 f'hold_attack({HOLD_MS})', True)
        c.tap(*ULT, active=True)
        c.tap(sx, sy, active=True)
    c.tap(*TRINKET, active=True)
