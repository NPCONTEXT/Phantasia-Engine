"""乐土前后两段：从主界面进到挑战里，收尾回主界面。坐标统一为2400×1504。

作者实测路线（2026-10-06）：主界面「出击」→「挑战」页签 → 往世乐土 →「快速出击」→「深层序列」
→ 周常页（侵蚀：积分×2.5；按所选角色档案的关键词补勾增益）→「减负战斗」→ 备战页（核对出战角色）→「开始战斗」。
回主界面：逐级「返回」，到出击页点左上房子图标。

本周九档已领时默认不开新一局（作者定：直接回主界面算完成）；--force 才照样开局。
"""
from __future__ import annotations
import itertools, re, time
import cv2
from lib.vision import imdecode
from scripts.er_runtime import RunStopped, button, reward_marks
from scripts import er_role

# ---- 增益词条：按当前角色档案的关键词打分（elysium_roles/<角色>.py 的 BUFF_WORDS），在点数内挑总分最高的组合
CHECK_X = 2224          # 增益行右端勾选框中心
LIST_TOP, LIST_BOTTOM = 640, 1140


def nav_page(items):
    """菜单页判定；挑战里的页面一律返回 None（交给 state_of）。"""
    t = ' '.join(i.get('text', '') for i in items)
    if '减负战斗说明' in t or ('出战位' in t and '开始战斗' in t and '支援位' in t):
        return 'prep'
    if '本周最高分' in t and '开始战斗' in t:
        return 'weekly'
    if '浅层序列' in t and '深层序列' in t and ('英桀试炼' in t or '本周进度' in t):
        return 'mode'
    if '快速出击' in t and '深层序列' in t:
        return 'lobby'
    if '推荐' in t and button(items, '挑战', roi=(1200, 200, 1550, 340)) and button(items, '出击', roi=(850, 200, 1200, 340)):
        return 'chuji'
    if (button(items, '任务', roi=(0, 150, 450, 300)) and button(items, '活动', roi=(0, 280, 500, 420))
            and button(items, '出击', roi=(1900, 650, 2400, 850))):
        return 'main'
    return None


def weekly_progress(items):
    """出击→挑战页签上往世乐土卡片的「本周挑战：n/9」，读不到返回 None。"""
    card = next((i for i in items if '往世乐' in i.get('text', '')), None)
    if not card:
        return None
    for i in items:
        m = re.fullmatch(r'\s*(\d)\s*/\s*9\s*', i.get('text', ''))
        if m and card['x'] - 100 < i['x'] < card['x'] + 500 and abs(i['y'] - card['y']) < 120:
            return int(m[1])
    return None


def unfinished_page(items):
    """10-28：上一局没打完（暂停页「放弃战斗」只放弃本层，或脚本中途停下），再进深层序列先弹「当前挑战进度」
    （哪一层、哪个角色、HP，「直接结算」/「继续挑战」）。旧代码认不出，空等 16 步停下。局内失败页没有这两个词，归主控。"""
    t = ' '.join(i.get('text', '') for i in items)
    return '当前挑战进度' in t and bool(button(items, '直接结算')) and bool(button(items, '继续挑战'))


def _png_2400(png):
    im = imdecode(png)
    h, w = im.shape[:2]
    return im if (w, h) == (2400, 1504) else cv2.resize(im, (2400, 1504))


def checked(im, y):
    """勾选框里有亮色对勾 = 已勾。"""
    box = cv2.cvtColor(im[max(0, int(y) - 26):int(y) + 26, CHECK_X - 32:CHECK_X + 32], cv2.COLOR_BGR2GRAY)
    return float((box > 170).mean()) > 0.04


def buff_rows(items, png):
    """读当前可见的增益行：[{key, text, cost, y, on}]。"""
    im = _png_2400(png)
    costs = []
    for i in items:
        # 11-24：点数前的闪电图标多数帧读成「$」，有时读成「4」（「$15」读成「415」整行丢了，勾完核对不上停机）→ 「4」也当前缀；
        # 点数只有 5 的倍数（0/5/10/15…），读出别的数不认
        m = re.fullmatch(r'\s*[$¥＄4]?\s*(\d{1,2})\s*', i.get('text', ''))
        cy = i['y'] + i['h'] / 2
        if m and int(m[1]) % 5 == 0 and i['x'] < 330 and LIST_TOP < cy < LIST_BOTTOM:
            costs.append((cy, int(m[1])))
    rows = []
    for cy, cost in sorted(costs):
        words = sorted((i for i in items if 270 < i['x'] < 2150 and abs(i['y'] + i['h'] / 2 - cy) < 45),
                       key=lambda i: (i['y'], i['x']))
        text = ''.join(w['text'] for w in words)
        if not text:
            continue
        rows.append(dict(key=re.sub(r'\W', '', text)[:8], text=text, cost=cost, y=cy, on=checked(im, cy)))
    return rows


def buff_points(items):
    for i in items:
        m = re.search(r'(\d{1,2})\s*/\s*(\d{1,2})', i.get('text', ''))
        if m and i['x'] > 1950 and 180 < i['y'] < 1200:
            return int(m[1]), int(m[2])
    return None


def buff_score(text):
    return sum(w for k, w in er_role.cur().BUFF_WORDS if k in text)


def choose_adds(rows, remaining):
    """只补不删：在剩余点数内，从没勾的行里挑得分最高的组合（同分取多用点数的）。
    用户已勾的一律保留——10-07 两次实跑里「按计划取消再重选」都因读列表出错改坏了用户的勾选。"""
    cand = [r for r in rows if not r['on'] and r['cost'] > 0 and buff_score(r['text']) > 0]
    best, best_key = [], (0, 0)
    for n in range(1, len(cand) + 1):
        for combo in itertools.combinations(cand, n):
            cost = sum(r['cost'] for r in combo)
            if cost > remaining:
                continue
            key = (sum(buff_score(r['text']) for r in combo), cost)
            if key > best_key:
                best, best_key = list(combo), key
    return {r['key'] for r in best}


class Entry:
    """主控之外的菜单导航。只在菜单页动作，挑战里的事交还主控。"""

    PREP_X = 1100       # 备战页右半边（减负战斗说明 / 特殊调整 / 角色调整那一栏）从这里往右；左半边只有出战位、支援位这些通用字

    def __init__(self, c, force=False):
        self.c, self.force = c, force
        self.role_checked = False      # 这次经过备战页核对过（或记过「没核对」）出战角色

    # -------- 基础动作
    def look(self):
        self.c.world_since = None          # 菜单页不受世界待机预算约束
        return self.c.observe()

    def tap(self, item, frame, wait=1.2):
        self.c.tap(item['x'] + item['w'] / 2, item['y'] + item['h'] / 2, frame)
        time.sleep(wait)

    def tap_xy(self, x, y, frame, wait=1.2):
        self.c.tap(x, y, frame)
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, ms):
        self.c._input(lambda: self.c.adb.swipe(x1, y1, x2, y2, ms), f'swipe({x1},{y1}->{x2},{y2})')
        time.sleep(ms / 1000 + 0.9)

    def wait_page(self, pages, seconds=12):
        deadline = time.monotonic() + seconds
        frame = None
        while time.monotonic() < deadline:
            frame = self.look()
            if nav_page(frame.items) in pages or frame.state in pages:
                return frame
            time.sleep(0.3)
        self.c.save('entry_wait_timeout', frame)
        raise RunStopped(f'等待页面 {"/".join(pages)} 超时，停在当前页面')

    def close_popups(self, frame):
        """10-27：新一期周常页一进来就弹「关卡效果变化」（已失效 / 新生效关卡效果，底部「确认」），挡住增益列表和「减负战斗」，
        底下的字照样识得出、页面仍认成周常页，旧代码白读一遍增益、点减负没反应，等备战页超时停下 → 先点「确认」关掉。
        刚弹出时按钮位置是「01秒后可关闭界面」，倒计时完才变「确认」：倒计时就等，最多点 3 次确认。"""
        taps = 0
        for _ in range(10):
            if not any('关卡效果变化' in i.get('text', '') for i in frame.items):
                return frame
            roi = (900, 1280, 1500, 1480)
            ok = button(frame.items, '确认', roi=roi)
            if ok and taps < 3:
                self.c.log('entry_popup', title='关卡效果变化')
                self.tap(ok, frame, 1.5)
                taps += 1
            elif not ok and button(frame.items, '秒后可关闭', roi=roi):
                time.sleep(1.0)
            else:
                break
            frame = self.look()
        self.c.save('popup_stuck', frame)
        raise RunStopped('「关卡效果变化」弹窗关不掉（倒计时没走完 / 找不到或点了 3 次「确认」还在），停在当前页面')

    def settle_unfinished(self, frame):
        """10-28 作者定：遇到上一局没打完的「当前挑战进度」就「直接结算」，再接着开新局（脚本接不上那局已拿到的刻印；
        手打到一半的局也会被结掉）。路线照手动做法：「直接结算」→ 确认框「确定」→ 结算页点空白 → 回到菜单页。
        确认框和这种结算页还没截过图（只离线）：确认框弹出时背后的「直接结算」可能还读得到，所以先找「确定」再考虑重点；
        结算页叠在大厅上、背后的菜单字也读得到，所以先认结算页再认菜单页。"""
        c = self.c
        c.log('entry_unfinished', page=[i.get('text', '').strip() for i in frame.items if 300 < i.get('y', 0) < 900])
        c.save('entry_unfinished', frame)
        self.tap(button(frame.items, '直接结算'), frame, 1.5)
        settle, ok, close = 1, 0, 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            frame = self.look()
            hint = button(frame.items, '点击空白处关闭界面')
            confirm = button(frame.items, '确定', exact=True) or button(frame.items, '确认', exact=True)
            if frame.state == 'result' or hint:
                if not close:
                    c.save('entry_unfinished_result', frame)
                target = confirm or hint          # 结算页上可能叠着「确定」提示框，先关它
                if target and close < 4:
                    self.tap(target, frame, 1.5)
                    close += 1
                    continue
            elif confirm or (frame.state == 'settle_confirm' and button(frame.items, '放弃', exact=True)):
                if ok < 3:
                    c.save('entry_unfinished_confirm', frame)
                    self.tap(confirm or button(frame.items, '放弃', exact=True), frame, 1.5)
                    ok += 1
                    continue
            elif unfinished_page(frame.items):
                if settle < 2:
                    self.tap(button(frame.items, '直接结算'), frame, 1.5)
                    settle += 1
                    continue
            elif nav_page(frame.items):
                c.log('entry_unfinished_done', page=nav_page(frame.items), settle=settle, ok=ok, close=close)
                return frame
            time.sleep(0.5)
        c.save('entry_unfinished_stuck', frame)
        raise RunStopped('「当前挑战进度」点了直接结算后 30 秒没回到菜单页，停在当前页面')

    # -------- 进去
    def enter(self, frame):
        """从任意菜单页进到挑战里。返回 ('started', 挑战里的帧) / ('weekly_done', 帧) / ('claim', 周常页帧)。"""
        c = self.c
        for _ in range(16):
            frame = self.close_popups(frame)
            if unfinished_page(frame.items):
                frame = self.settle_unfinished(frame)
                continue
            page = nav_page(frame.items)
            if page == 'main':
                self.tap(button(frame.items, '出击', roi=(1900, 650, 2400, 850)), frame, 2.0)
            elif page == 'chuji':
                tab = button(frame.items, '挑战', roi=(1200, 200, 1550, 340))
                card = next((i for i in frame.items if '往世乐' in i.get('text', '')), None)
                if not card:
                    self.tap(tab, frame, 1.8)
                else:
                    done = weekly_progress(frame.items)
                    c.log('entry_progress', page='chuji', weekly=done)
                    self.tap_xy(card['x'] + card['w'] / 2, card['y'] - 60, frame, 2.5)
            elif page == 'lobby':
                self.tap(button(frame.items, '快速出击', roi=(1900, 1300, 2400, 1504)), frame, 2.0)
            elif page == 'mode':
                deep = next((i for i in frame.items if '深层序列' in i['text'] and i['y'] < 600), None)
                if not deep:
                    raise RunStopped('模式选择页找不到深层序列')
                self.tap(deep, frame, 2.5)
            elif page == 'weekly':
                return self.on_weekly(frame)
            elif page == 'prep':
                return self.start(frame)
            elif frame.state in ('pause', 'world', 'battle', 'door', 'merchant', 'select', 'core', 'anchor',
                                 'shop', 'skip', 'skip_confirm', 'settle_confirm', 'result', 'retry', 'failure'):
                return 'started', frame        # 已经在挑战里（或其结算页），交还主控
            else:
                c.save('entry_unknown', frame)
                time.sleep(1.0)
            frame = self.look()
        raise RunStopped('从主界面进乐土走了16步还没到备战页，停在当前页面')

    def on_weekly(self, frame):
        c = self.c
        marks = reward_marks(frame.png)
        c.log('entry_weekly', claimed=sum(marks), difficulty='侵蚀' if '侵蚀' in ' '.join(i['text'] for i in frame.items) else None)
        if all(marks) and not self.force:
            c.save('weekly_done', frame)
            return 'weekly_done', frame
        # 10-28：前后只要不是数字就行（10-27 读成「20000一」，旧的 \b 把「一」当字母、匹配不上 → 当没打过误开一局）
        best = max((int(m[1]) for i in frame.items if i.get('x', 999) < 250 and 1300 < i.get('y', 0) < 1440
                    for m in [re.search(r'(?<!\d)(\d{4,5})(?!\d)', i['text'])] if m), default=0)
        if not all(marks) and best >= 20000 and not self.force:
            return 'claim', frame          # 本周已打到20000分但还有档没领：只补领，不再开局
        diff = next((i for i in frame.items if '积分' in i['text'] and 1150 < i['x'] + i['w'] / 2 < 1550
                     and 1150 < i['y'] < 1290), None)
        if not diff or '侵蚀' not in diff['text']:
            c.save('entry_difficulty', frame)
            raise RunStopped('周常页难度不是「侵蚀：积分×2.5」，没有自动改，保留现场')
        frame = self.set_buffs(frame)
        go = button(frame.items, '减负战斗', roi=(1550, 1150, 1950, 1290))
        if not go:
            raise RunStopped('周常页找不到「减负战斗」')
        self.tap(go, frame, 2.5)
        return self.start(self.wait_page(('prep',)))

    # 增益列表的翻页：先翻回最顶上，再一屏一屏往下（游戏会记住上次翻到哪，不能假设在顶部）
    VIEW_STEPS = ((900, 700, 900, 260, 1500), (900, 1000, 900, 600, 1200), (900, 1000, 900, 600, 1200))

    def settled_rows(self):
        """读一屏增益行：列表有惯性，连续两帧行位置一致才算停稳。返回 (帧, 行, 是否停稳)。"""
        prev, f, rows = None, None, []
        for _ in range(6):
            f = self.look()
            rows = buff_rows(f.items, f.png)
            sig = [(r['key'], round(r['y'] / 8), r['on']) for r in rows]
            if prev is not None and sig == prev:
                return f, rows, True
            prev = sig
            time.sleep(0.4)
        return f, rows, False

    def views(self, max_swipes=5):
        """先翻回最顶上，再一屏一屏往下；连续两屏行一样 = 到底了。产出 (帧, 行, 是否停稳, 是否到底)。"""
        for _ in range(3):
            self.swipe(900, 300, 900, 1100, 600)
        prev = None
        for n in range(max_swipes):
            self.swipe(*self.VIEW_STEPS[0 if n == 0 else 1])
            f, rows, stable = self.settled_rows()
            keys = [r['key'] for r in rows]
            bottom = prev is not None and keys == prev
            yield f, rows, stable, bottom
            if bottom:
                return
            prev = keys

    def read_buffs(self):
        """从顶上翻到底读一遍增益列表。返回 (行, 点数 (已用, 总数) 或 None, 读全没有)。"""
        rows, pts, complete, steady = {}, None, False, True
        for n, (f, vis, stable, bottom) in enumerate(self.views()):
            # 11-24：每屏记一行，再读不全时能看出卡在哪（行没认出来 / 没停稳 / 没凑出连着两屏一样）
            self.c.log('buffs_view', n=n + 1, keys=[r['key'] for r in vis], costs=[r['cost'] for r in vis],
                       stable=stable, bottom=bottom)
            steady = steady and stable
            pts = pts or buff_points(f.items)
            for r in vis:
                old = rows.get(r['key'])
                if old and old['cost'] == 0:
                    r = dict(r, cost=0)
                rows[r['key']] = r
            complete = complete or bottom
        return rows, pts, complete and steady

    def set_buffs(self, frame):
        """读、勾增益这一段让控制器改用 adb 整屏截图（c.menu_screencap），出这一段（含停机）一定切回录屏。
        11-24：新一周增益全空（0/30），录屏帧上两遍各翻 5 屏都没读全、停在周常页；10-20 两次也这样，
        当时 adb 整屏截图一遍就读全。菜单页画面不动，慢一点（每帧多 1–2 秒）没关系。"""
        c = self.c
        c.menu_screencap = True
        try:
            return self._set_buffs(frame)
        finally:
            c.menu_screencap = False

    def _set_buffs(self, frame):
        """按关键词补勾增益：确认读到列表底部、每屏都停稳才动；只在剩余点数内补勾，绝不取消已勾的。
        每点一下都核对只有目标那一行变了；对不上就停在周常页，不开局。
        没读全就从头再读一遍（10-20 01:25 偶发没读全，19:42 侦察同一张列表 3 屏就读全了）；
        两次都没读全、而且一个都没勾（游戏不让开局），就停在周常页说清楚，不去点开战白等。"""
        c = self.c
        for n in range(2):
            rows, pts, ok = self.read_buffs()
            if ok:
                break
            c.log('buffs_reread', attempt=n + 1, rows=len(rows), points=pts)
        now = [r['text'][:24] for r in rows.values() if r['on']]
        if not ok:
            if not now and (pts is None or pts[0] == 0):
                f = self.look()
                c.save('buffs_unread', f)
                raise RunStopped('增益列表两次都没读全，一个增益也没勾，游戏不让开局：停在周常页，请手动勾好增益再播放')
            c.log('buffs_skip', reason='没确认读全增益列表，不改勾选', rows=len(rows), now=now)
            return self.look()
        total = pts[1] if pts else 30
        used = sum(r['cost'] for r in rows.values() if r['on'])
        adds = choose_adds(list(rows.values()), total - used)
        c.log('buffs_plan', total=total, used=used, rows=len(rows), add=[rows[k]['text'][:24] for k in adds], now=now)
        if not adds:
            c.log('buffs_ok', changed=0)
            return self.look()
        done = set()
        for f, vis, stable, bottom in self.views():
            for r in list(vis):
                if r['key'] not in adds or r['key'] in done or r['on']:
                    continue
                if not stable:
                    c.save('buff_unstable', f)
                    raise RunStopped('增益列表一直没停稳，没敢勾选，停在周常页')
                before = {x['key']: x['on'] for x in vis}
                c.log('buff_toggle', text=r['text'][:24], on=True)
                self.tap_xy(CHECK_X, r['y'], f, 0.8)
                f, vis, stable = self.settled_rows()
                after = {x['key']: x['on'] for x in vis}
                expect = dict(before, **{r['key']: True})
                if not stable or after.get(r['key']) is not True or any(after[k] != expect[k] for k in expect if k in after):
                    c.save('buff_mismatch', f)
                    raise RunStopped(f'勾选「{r["text"][:12]}」后核对不一致，停在周常页，请手动检查增益')
                done.add(r['key'])
        if done != adds:
            raise RunStopped('要补勾的增益没全部找到，停在周常页，请手动检查')
        c.log('buffs_ok', changed=len(done), added=[rows[k]['text'][:24] for k in done])
        return self.look()

    def check_role(self, frame):
        """开战前核对出战的是不是选的角色：备战页上没有角色名字，只看右半边「角色调整」那一栏，读到档案 PREP_WORDS 任意一个才算。
        第一帧的字可能还没出全：读不到就隔半秒再读，最多再读 2 帧（10-20 复查）。
        档案没写特征词就只记一笔不核对（新角色还没侦察时）。返回最后读的那一帧（开战按它点）。"""
        c, role = self.c, er_role.cur()
        self.role_checked = True
        if not role.PREP_WORDS:
            c.log('role_unchecked', role=role.ID, reason='角色档案没写备战页特征词')
            return frame
        for n in range(3):
            text = ' '.join(i.get('text', '') for i in frame.items if i.get('x', 0) >= self.PREP_X)
            hit = [w for w in role.PREP_WORDS if w in text]
            if hit:
                c.log('role_verified', role=role.ID, hit=hit, reads=n + 1)
                return frame
            if n < 2:
                time.sleep(0.5)
                frame = self.look()
                if nav_page(frame.items) != 'prep':
                    c.save('role_left_prep', frame)
                    raise RunStopped('核对出战角色时离开了备战页，停在当前页面')
        c.save('role_mismatch', frame)
        raise RunStopped(f'备战页出战的不是「{role.LABEL}」（读了 3 次都没读到它的特征词），停在备战页：'
                         f'请在游戏里换好出战角色，或在幻想引擎里换成现在出战的角色')

    def start(self, frame):
        c = self.c
        frame = self.check_role(frame)
        go = button(frame.items, '开始战斗', roi=(1800, 1300, 2400, 1504))
        if not go:
            raise RunStopped('备战页找不到「开始战斗」')
        c.save('entry_prep', frame)
        c.tap_item(go, frame)
        c.pending_until = time.monotonic() + 25
        c.world_since = None
        deadline = c.pending_until
        retried = False
        while time.monotonic() < deadline:
            f = c.observe(transition=True)
            if f is None:
                continue
            if f.state in ('select', 'core', 'anchor', 'world', 'battle', 'door', 'skip', 'skip_confirm', 'pause'):
                c.pending_until = 0
                c.log('entry_started', state=f.state)
                return 'started', f
            if not retried and nav_page(f.items) == 'prep' and time.monotonic() > deadline - 20:
                again = button(f.items, '开始战斗', roi=(1800, 1300, 2400, 1504))
                if again:          # 5秒后还在备战页：第一下被吞了，只补点一次
                    retried = True
                    c.tap_item(again, f)
                    deadline = time.monotonic() + 20
                    c.pending_until = deadline
        raise RunStopped('点「开始战斗」25秒内没进到挑战里')

    # -------- 回来
    def to_main(self):
        """从乐土菜单/结算页逐级退回主界面；不在挑战里动作。"""
        c = self.c
        seen_main = 0
        for _ in range(14):
            f = self.look()
            page = nav_page(f.items)
            if page == 'main':
                seen_main += 1
                if seen_main >= 2:
                    c.log('home_verified')
                    return True
                time.sleep(0.6)
                continue
            seen_main = 0
            menu = button(f.items, '主菜单', roi=(300, 0, 700, 140))
            back = button(f.items, '返回', roi=(0, 0, 420, 140))
            close = button(f.items, '点击空白处关闭界面')
            if page == 'chuji':
                self.tap_xy(379, 78, f, 2.0)              # 出击页左上房子图标（OCR 读不出字）
            elif menu:
                self.tap(menu, f, 2.0)
            elif f.state == 'reward' and button(f.items, '确定', exact=True):
                self.tap(button(f.items, '确定', exact=True), f, 1.5)
            elif close:
                self.tap(close, f, 1.5)
            elif back:
                self.tap(back, f, 1.8)
            elif page is None and f.state in ('unknown', 'loading'):
                time.sleep(1.0)
            else:
                c.save('home_blocked', f)
                raise RunStopped(f'回主界面时遇到不认识的页面({f.state})，停在原处')
        c.save('home_timeout')
        raise RunStopped('退了14步还没回到主界面')
