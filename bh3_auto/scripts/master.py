"""乐土总控：战斗、选择、有限找门、末层结算与周常领取。角色口味（增益、刻印、锚点、门、商店、战斗手法）在 elysium_roles/<角色>.py，按 --role 选。

从当前挑战内接管；不会在主界面自动开新挑战。日志记录每次观察/输入耗时。
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, unicodedata, re, signal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.er_runtime import (Controller,RunMemory,RunStopped,PROJECT,button,
                               state_of,battle_by_template,reward_marks,score_of)
from scripts.er_runtime import floor_of
from scripts.er_entry import Entry,nav_page
from scripts.find_door_simple import find_proc
from scripts import er_role
try:        # 11-02 暂停协议（幻想引擎的 lib/play_ctl.py，经 PYTHONPATH 传进来）；单独运行时 import 不到，用什么都不做的替身
    from play_ctl import pause_point as xa_pause, mark as xa_mark
except ImportError:
    def xa_pause():
        return False

    def xa_mark(*a, **k):
        pass


def card_groups(items):
    badges=[i for i in items if i.get('x',0)>2100 and 300<i.get('y',0)<1300
            and i.get('text','').strip() in ('普通','核心','增幅','专属')]
    titles=[]
    for badge in sorted(badges,key=lambda i:i['y']):
        near=[i for i in items if 1000<i.get('x',0)<2000 and i.get('w',9999)<950
              and abs(badge['y']-i.get('y',0))<85 and i.get('h',0)>40]
        if near:
            titles.append(min(near,key=lambda i:abs(i['y']-badge['y'])))
    if not titles:
        titles=[i for i in items if 1000<i.get('x',0)<2000 and 300<i.get('y',0)<1300
                and i.get('w',9999)<950 and i.get('h',0)>=58]
    titles=sorted({(i['x'],i['y']):i for i in titles}.values(),key=lambda i:i['y'])
    groups=[]
    for n,title in enumerate(titles):
        end=titles[n+1]['y']-15 if n+1<len(titles) else 1340
        text=' '.join(i.get('text','') for i in items if i.get('x',0)>1000 and title['y']-10<=i['y']<end)
        groups.append((title,text))
    return groups


def page_group(items):
    """刻印页标题「「天慧」的刻印」→ '天慧'（核心刻印页按组挑上 / 下哪张用）；读不到给空串。"""
    for i in items:
        if i.get('y',9999)<300:
            m=re.search(r'「\s*([^」\s]+)\s*」的刻印',i.get('text',''))
            if m:return m.group(1)
    return ''


def seal_score(text):
    # 10-20 起按角色档案打分（符华的原样搬进 elysium_roles/fuhua.py）
    return er_role.cur().seal_score(text)


def want_reset(frame,groups):
    """10-27 作者：第 1 层第一次选专属刻印的「重置刻印」可以无限点。档案写了 FIRST_SEAL（愈生佑翎 = 澈愿）、
    这页是第 1 层、三张都是专属、里面没有它 → 返回「重置刻印」按钮；别的情况 None（符华没写，照旧）。"""
    want=getattr(er_role.cur(),'FIRST_SEAL',None)
    if not want or not groups or any(want in g[1] for g in groups):
        return None
    if not any('第1层' in i.get('text','').replace(' ','') and i.get('y',9999)<160 for i in frame.items):
        return None
    badges=[i.get('text','').strip() for i in frame.items if i.get('x',0)>2100 and 300<i.get('y',0)<1300
            and i.get('text','').strip() in ('普通','核心','增幅','专属')]
    if not badges or any(b!='专属' for b in badges):
        return None
    return button(frame.items,'重置刻印',roi=(1300,1300,1950,1504))


def choose(c,frame,core=False):
    initial_kind='core' if core else 'select'
    groups=card_groups(frame.items)
    deadline=time.monotonic()+5
    def signature_of(gs):
        return tuple(unicodedata.normalize('NFKC',g[0]['text']).replace(' ','') for g in gs)
    previous=None
    while time.monotonic()<deadline:
        # 菜单标题在淡入/淡出中仍可见，等真实选项出现或页面切换。
        signature=signature_of(groups)
        if groups and signature==previous:
            break
        previous=signature
        frame=c.observe()
        if frame.state!=initial_kind:
            return frame
        groups=card_groups(frame.items)
    if not groups:
        c.stop('无法按卡片分组识别刻印选项')
    if not core:
        for n in range(1,61):
            rst=want_reset(frame,groups)
            if not rst:
                break
            c.log('seal_reset',n=n,want=er_role.cur().FIRST_SEAL,cards=[g[0]['text'] for g in groups])
            c.tap_item(rst,frame)
            time.sleep(1.0)
            previous=None
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:          # 新的三张淡入完、连续两帧一样才算
                frame=c.observe()
                if frame.state!=initial_kind:
                    return frame
                groups=card_groups(frame.items)
                signature=signature_of(groups)
                if groups and signature==previous:
                    break
                previous=signature
            if not groups:
                c.stop('点「重置刻印」后读不到刻印选项')
        else:
            c.log('seal_reset_gave_up',n=60)
    signature=signature_of(groups)
    group=page_group(frame.items) if core else ''
    title,text=er_role.cur().pick_core(groups,group) if core else max(groups,key=lambda g:seal_score(g[1]))
    chosen_name=unicodedata.normalize('NFKC',title['text']).replace(' ','')
    c.log('select_seal',title=title['text'],score=seal_score(text),cards=len(groups),core=core,group=group,desc=text[:120])
    getattr(c,'seals',[]).append(chosen_name)
    c.tap_item(title,frame)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        fresh=c.observe()
        if fresh.state not in ('select','core'):
            return fresh
        confirm=button(fresh.items,'选择刻印',roi=(1800,1300,2400,1504)) or button(fresh.items,'确认',roi=(1800,1300,2400,1504))
        if confirm:
            c.tap_item(confirm,fresh)
            break
    else:
        c.stop('选择后5秒内未出现确认按钮/新页面')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        fresh=c.observe()
        if fresh.state not in ('select','core'):
            return fresh
        new=card_groups(fresh.items)
        if new and chosen_name not in signature_of(new):
            return fresh
    c.stop('确认刻印后5秒仍停在同一组选择，停止重复点击')


def select_anchor(c,frame,refreshes):
    order=er_role.cur().ANCHOR_ORDER
    anchors=[i for i in frame.items if 380<i.get('y',0)<600 and i.get('x',0)<1950 and len(i.get('text',''))<12]
    for key in order:
        an=next((i for i in anchors if key in i['text']),None)
        if an:
            c.tap(an['x']+an['w']/2,650,frame)
            c.log('select_anchor',name=an['text'])
            return 0
    if refreshes>=2:
        c.stop('两次刷新后仍无允许的锚点，不进黑名单门')
    c.tap(2210,1340,frame)
    c.log('refresh_anchor',count=refreshes+1)
    return refreshes+1


def result_values(items):
    # 10-28：数字后面粘一两个杂字也认（10-27 03:37 打满 17 层，积分读成「20000：」，旧代码只认纯数字 → 停下没领九档）
    def value(label):
        tag=button(items,label)
        if not tag:
            return None
        cx=tag['x']+tag['w']/2
        vals=[m for i in items if abs(i['x']+i['w']/2-cx)<170 and tag['y']+35<i['y']<tag['y']+200
              for m in [re.fullmatch(r'\s*(\d+)\s*[^\d\s]{0,2}\s*',i['text'])] if m]
        return int(vals[0][1]) if vals else None
    return value('到达层数'),value('挑战积分')


def can_resume_settlement(frame):
    score=score_of(frame.items)
    return frame.state=='settle_confirm' and floor_of(frame.items)==17 and score is not None and score>=20000


def dismiss_failure(c,frame):
    # 游戏的「放弃并结算」先显示此信息页，需要一次空白点击才进统计。
    c.tap(1200,1300,frame)
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        fresh=c.observe()
        if fresh.state not in ('failure','unknown','loading'):return fresh
    c.stop('失败信息页点击后6秒未出现统计/重试页面')


def can_resume_weekly(frame):
    if frame.state!='weekly':return False
    for item in frame.items:
        if item.get('x',999)<250 and 1300<item.get('y',0)<1440:
            m=re.search(r'\b(\d{4,5})\b',item['text'])
            if m and int(m[1])>=20000:return True
    return False


def story_button(frame):
    return button(frame.items,'跳过',roi=(1600,0,2400,250)) if frame.state=='skip' else button(frame.items,'确认',exact=True)


def wait_story_button(c,frame,tries=6):
    """剧情页/跳过确认框的按钮可能还在淡入（10-07 第3轮第11层：那一帧只有标题，「取消/确认」还没出来）。
    最多再读 tries 次（约2.5秒）；页面变了就交回主循环。返回 (最新帧, 按钮或None)。"""
    item=story_button(frame)
    while not item and tries>0:
        time.sleep(.4);tries-=1
        frame=c.observe()
        if frame.state not in ('skip','skip_confirm'):
            return frame,None
        item=story_button(frame)
    return frame,item


def combat_animation(frame,last_combat,now):
    # 大招有一段连生命条也隐藏。最近明确在战斗，且没有菜单/清场证据时，
    # 延续战斗最多12秒；不能将无法读字的特写当成已退出战斗。
    # 11-03：last_combat = 最近一次在打（认出战斗画面，或「画面被挡」照打的那轮），不只是认出战斗画面那一刻。
    return frame.state in ('unknown','loading') and last_combat is not None and now-last_combat<12


def resume_challenge(c,frame):
    deadline=time.monotonic()+8
    c.tap_item(button(frame.items,'继续战斗',exact=True),frame)
    c.pending_until=deadline
    c.world_since=None
    clicked=time.monotonic();retried=False
    while time.monotonic()<deadline:
        frame=c.observe()
        if frame.state in ('world','battle','door','merchant','select','core','anchor','skip','skip_confirm'):
            c.pending_until=0
            c.log('resume_verified',state=frame.state)
            return frame
        if frame.state=='pause' and not retried and time.monotonic()-clicked>1.2:
            c.tap_item(button(frame.items,'继续战斗',exact=True),frame)
            retried=True
        elif frame.state not in ('pause','unknown','loading'):
            c.stop('恢复挑战后出现其他页面，保留现场')
    c.stop('恢复挑战8秒未出现场景/挑战菜单',pause=True)


def maintain_capture(c,frame):
    stream=c.stream
    if not stream or stream.restart_requested.is_set():return frame
    age=stream.age()
    safe=('pause','select','core','anchor','shop','skip','skip_confirm','result','weekly','lobby','reward')
    if frame.state in safe and age>=45:
        stream.request_restart()
        c.log('stream_restart_requested',state=frame.state,age_s=round(age,2))
    elif frame.state in ('world','door','merchant','battle') and age>=stream.time_limit-45:
        c.log('stream_maintenance_start',state=frame.state,age_s=round(age,2))
        if not c.pause():c.stop('视频续流前未确认暂停')
        previous_start=stream.started_at
        stream.request_restart()
        stream.wait_restarted(previous_start)
        frame=c.observe()
        if frame.state!='pause':c.stop('新视频流未确认仍在暂停页，保留现场')
        frame=resume_challenge(c,c.frame)
        c.log('stream_maintenance_done',state=frame.state)
    return frame


def claim_weekly(c,frame,settled=True):
    """settled=本次运行是否核到过结算结果页(17层/20000分)。没核到也照常领奖，
    但完成记录里如实写明，不当作完整验收（10-05 full-run1-part9 就是结果页被当成大厅点掉了）。"""
    for attempt in range(10):
        if frame.state=='reward':
            c.save('reward_received',frame)
            ok=button(frame.items,'确定',exact=True)
            c.tap_item(ok,frame)
        elif frame.state=='weekly':
            marks=reward_marks(frame.png)
            if all(marks):
                verified=c.observe()
                if verified.state=='weekly' and all(reward_marks(verified.png)):
                    c.save('weekly_complete',verified)
                    if not settled:
                        c.log('settlement_unverified',reason='本次运行没有核到结算结果页（可能已被点掉，或是从周常页续接）')
                    c.log('complete',claimed_tiers=9,settlement_verified=bool(settled),max_active_gap_s=round(c.max_gap,3))
                    return True
                frame=verified
                continue
            index=max(i for i,mark in enumerate(marks) if not mark)
            c.tap((309,509,709,909,1109,1309,1509,1709,1909)[index],1415,frame)
        else:
            c.stop('领奖时离开周常/奖励页面，停止点击')
        before=frame.state;left=False
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            frame=c.observe()
            if before=='reward' and frame.state=='weekly':break
            # 10-14：领完九档会接连弹两次「获得奖励」，中间隔一张文字过场；弹窗消失过又出现 = 新弹窗，回去再点确定
            if before=='reward' and frame.state!='reward':left=True
            if before=='reward' and frame.state=='reward' and left:break
            if before=='weekly' and (frame.state=='reward' or frame.state=='weekly' and reward_marks(frame.png)!=marks):break
            if frame.state not in ('weekly','reward','unknown','loading'):
                c.stop('领奖过渡出现其他页面，停止点击')
        else:c.stop('领奖操作后6秒没有页面或领取状态变化')
    c.stop('周常领取未出现九档已领取绿勾')


GAME_PKG='com.miHoYo.enterprise.NGHSoD'


def prelaunch(adb=None):
    """--from-main 的启动前准备：息屏就唤醒，锁屏就停（不碰解锁）；游戏没在前台，
    就用崩3日常脚本现成的启动流程拉起到主界面（公告/签到面板都由它处理）。
    游戏已在前台时什么都不做，交给主控按页面判断（在挑战里就接着打）。"""
    from lib.adb import Adb
    adb=adb or Adb()
    def sh(cmd):return adb.shell(cmd,timeout=20).decode(errors='ignore')
    if 'mWakefulness=Awake' not in sh('dumpsys power'):
        sh('input keyevent KEYCODE_WAKEUP');time.sleep(1.0)
    win=sh('dumpsys window')
    if 'isKeyguardShowing=true' in win:
        sh('wm dismiss-keyguard');time.sleep(1.5)
        win=sh('dumpsys window')
        if 'isKeyguardShowing=true' in win:
            raise RunStopped('设备锁屏了，需要先手动解锁')
    focus=next((l for l in win.splitlines() if 'mCurrentFocus' in l),'')
    if GAME_PKG in focus:
        return 'foreground'
    daily=PROJECT.parent/'tasks/2026-09-29-崩3日常任务'
    if str(daily) not in sys.path:sys.path.insert(0,str(daily))
    import bh3_daily
    bot=bh3_daily.Bot()
    bot.ensure_game()
    bot.ocr()
    if bot.is_sign_panel():bot.step_sign()
    if not bot.to_main():
        raise RunStopped('拉起游戏后没能回到主界面')
    return 'launched'


def main(argv=None,controller=None):
    p=argparse.ArgumentParser()
    p.add_argument('--after-shop',action='store_true')
    p.add_argument('--observe',action='store_true')
    p.add_argument('--approach',action='store_true')
    p.add_argument('--resume',action='store_true',help='仅启动时允许从已暂停的挑战继续')
    p.add_argument('--max-doors',type=int,help='实测：验证指定次数换层后，下一扇门前暂停')
    p.add_argument('--nav-seconds',type=float,default=120)
    p.add_argument('--max-seconds',type=float,default=1800)
    p.add_argument('--shop-rounds',type=int,default=10)
    p.add_argument('--log-dir')
    p.add_argument('--from-main',action='store_true',help='从主界面/乐土菜单进到挑战里再接管（顺序播放用）')
    p.add_argument('--home',action='store_true',help='领完周常或本周已完成后回主界面')
    p.add_argument('--force',action='store_true',help='本周九档已领也照样开一局（实测用）')
    p.add_argument('--role',help='乐土角色档案 id（elysium_roles/<id>.py），不给 = 符华')
    p.add_argument('--seals',help='接着打时这局已经拿到的刻印，逗号隔开（手动实测用；不给就当没有）')
    args=p.parse_args(argv)
    xa_mark('正在进乐土',say='正在进乐土，进去后到选刻印、商店、剧情页时才能停，请稍后再点')   # 11-02 暂停：进乐土这段不停
    try:
        role=er_role.use(args.role)
    except Exception as exc:
        print(json.dumps(dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),event='terminated',
                              reason=f'角色档案有问题：{exc}'),ensure_ascii=False),flush=True)
        return 2
    print(json.dumps(dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),event='role',id=role.ID,label=role.LABEL),
                     ensure_ascii=False),flush=True)
    if args.observe and args.resume:
        p.error('--observe只读取当前页面，不能同时--resume')
    if args.nav_seconds<=0 or args.max_seconds<=0 or args.shop_rounds<0 or args.max_doors is not None and args.max_doors<0:
        p.error('时限须为正数，商店轮数/允许换层数不能为负数')
    if args.from_main and controller is None:
        try:
            how=prelaunch()
            print(json.dumps(dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),event='prelaunch',how=how),ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps(dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),event='terminated',
                                  reason=f'启动前准备失败：{exc}'),ensure_ascii=False),flush=True)
            return 2
    c=controller or Controller(log_dir=args.log_dir)
    seals=[s.strip() for s in (args.seals or '').split(',') if s.strip()]
    if seals:   # 从暂停页接着打是新的一次运行，前面选到的刻印不知道；角色档案按它换打法（愈生佑翎有澈愿才一直按住）
        c.seals.extend(seals);c.log('seals_given',seals=seals)
    if not args.observe:c.maintenance_handler=lambda f:maintain_capture(c,f)
    previous_sigterm=signal.getsignal(signal.SIGTERM)
    def request_stop(_signum,_frame):raise RunStopped('收到停止指令')
    signal.signal(signal.SIGTERM,request_stop)
    m=RunMemory(after_shop=args.after_shop)
    start=time.monotonic()
    postbattle=0;clear_frames=0;anchor_refreshes=0;settling=False;settled=False;mode_taps=0
    last_kind='';same_since=start;same_count=0;retries=0;retry_at=0;last_battle=None
    last_combat=None   # 11-03 最近一次在打：认出战斗画面，或「画面被挡」照打的那轮；「动画延续」从它算 12 秒
    battle_floor=None;battle_start_score=None
    reported_progress=None
    try:
        if args.resume and not args.from_main:   # 从主界面开始时第一帧在菜单里，不按世界防待机预算取图
            c.world_since=time.monotonic()
        frame=c.observe()
        entry=Entry(c,force=args.force)
        if args.from_main and (nav_page(frame.items) or frame.state in ('lobby','weekly')):
            kind,frame=entry.enter(frame)
            if kind=='weekly_done':
                c.log('weekly_done',reason='本周九档已领，不开新一局')
                if args.home:entry.to_main()
                return 0
            if kind=='claim':
                claim_weekly(c,frame,settled=False)
                if args.home:entry.to_main()
                return 0
        if not entry.role_checked:   # 没经过备战页（游戏停在挑战里接着打）：挑战里认不了人，记一笔给幻想引擎显示（10-20 复查）
            c.log('role_unchecked',role=er_role.cur().ID,reason='接着打已经开始的挑战，没法核对出战角色')
        if args.resume and frame.state=='pause':
            frame=resume_challenge(c,frame)
        while time.monotonic()-start<args.max_seconds:
            if c.maintenance_handler:frame=c.maintenance_handler(frame)
            m.update(frame)
            progress=(m.floor,m.score)
            if progress!=reported_progress:
                c.log('progress',floor=m.floor,score=m.score)
                reported_progress=progress
            st=frame.state
            if st=='battle':
                if battle_floor!=m.floor or battle_start_score is None:
                    battle_floor=m.floor;battle_start_score=m.score
                last_battle=time.monotonic()
                last_combat=last_battle
            # 10-14：点了「放弃」后的收尾动画也是无字画面，结算中不再补战斗键
            elif not settling and not (m.floor==16 and not m.after_shop) and combat_animation(frame,last_combat,time.monotonic()):
                c.log('combat_animation',reason='近期已确认战斗，动画中暂无菜单/清场证据，有限延续战斗输入')
                st='battle'
            elif st=='world' and not (m.floor==16 and not m.after_shop) and last_battle is not None and time.monotonic()-last_battle<12 and m.floor==battle_floor and (
                m.score is None or battle_start_score is None or m.score<=battle_start_score):
                c.log('combat_ui_occluded',reason='同层且积分未增加，继续战斗等待明确清场/菜单')
                st='battle'
                # 11-03 第 14 层：这轮照打时放了必杀，紧接着的全屏动画离上次认出战斗已 14 秒，不算动画延续 → 不按键 → 1.8 秒待机保护停机。
                # 照打的这轮也算「最近在打」；本条仍从 last_battle 算，动画延续那几轮不刷新 last_combat，认不出画面时最多照打 12+12 秒
                last_combat=time.monotonic()
            if args.observe:
                c.save('observe',frame)
                c.log('observed_only',state=st,floor=m.floor)
                return 0
            if st != last_kind:
                last_kind=st;same_since=time.monotonic();same_count=0
            same_count+=1
            if st not in ('world','door','merchant','battle') and time.monotonic()-same_since>30:
                c.stop(f'{st}页面30秒没有切换')
            # 11-02 暂停：选刻印 / 锚点 / 剧情 / 商店页游戏在等人点，停在这里安全；战斗、找门时按着摇杆或攻击键，不能停
            if st in ('select','core','anchor','skip','skip_confirm','shop'):
                xa_mark(None)
                if xa_pause():
                    frame=c.observe();continue          # 停过：画面可能变了，重新看一眼再动作
            else:
                xa_mark('乐土正在战斗或找门',say='乐土正在战斗或找门，到选刻印、商店、剧情页时才能停，请稍后再点')
            if st in ('select','core'):
                last_battle=None;last_combat=None
                if same_count>4:
                    c.stop('刻印页连续四次操作后仍未退出')
                frame=choose(c,frame,st=='core')
                m.nav_started=None
                continue
            elif st=='anchor':
                anchor_refreshes=select_anchor(c,frame,anchor_refreshes)
                m.nav_started=None
            elif st in ('skip','skip_confirm'):
                frame,item=wait_story_button(c,frame)
                if frame.state not in ('skip','skip_confirm'):
                    continue
                if not item:
                    c.stop('剧情页等了约2.5秒仍缺少操作按钮')
                c.tap_item(item,frame)
            elif st=='battle':
                clear_frames=0
                # 保留原战斗配方，每3组检查整个页面；遮挡按钮不会直接触发找门。
                c.floor=m.floor          # 11-12：角色档案按层数认首领层（愈生佑翎首领层边打边绕圈）
                er_role.cur().fight(c)   # 角色档案的战斗手法（符华：普攻×10 + 三个键，共 3 组）
                frame=c.observe(active_world=True)
                if frame.state!='battle':
                    c.release_hold()   # 一直按住普攻的角色（愈生佑翎）：离开战斗马上松手
                    postbattle=time.monotonic()+2.5
                    m.nav_started=None
                continue
            elif st in ('world','door','merchant'):
                current_score=score_of(frame.items)
                if m.floor==17 and current_score is not None and current_score>=20000:
                    clear_frames+=1
                    if clear_frames>=2:
                        c.save('boss_cleared',frame)
                        if not c.pause():c.stop('末层清场后未确认暂停，停止结算')
                        frame=c.frame
                        settling=True
                        continue
                    else:
                        c.move(0,60,frame)
                elif st=='world' and time.monotonic()<postbattle:
                    # 已证实是世界画面的小动作维持2s预算；期间每帧优先处理刻印。
                    c.move(0,60,frame)
                else:
                    clear_frames=0
                    result=find_proc(controller=c,memory=m,initial=frame,direct=m.after_shop,
                                     mode='approach' if args.approach or args.max_doors is not None and m.transitions>=args.max_doors else 'enter',
                                     nav_seconds=args.nav_seconds)
                    if result=='approached':
                        return 0
                    frame=m.last_frame or c.frame
                    if result in ('unknown','loading'):
                        frame=c.frame
                    continue
            elif st=='shop':
                if args.shop_rounds>0:
                    c.save('shop_before',frame)
                    from scripts.shop_buy import run_shop
                    frame=run_shop(c,frame,args.shop_rounds)
                    if frame.state!='shop':
                        c.stop('采购后未停留在商店页面')
                    c.save('shop_after',frame)
                back=button(frame.items,'返回',roi=(0,0,500,200))
                if not back:
                    c.stop('商店返回按钮无法确认')
                c.tap_item(back,frame)
                deadline=time.monotonic()+8
                retried=False
                clicked=time.monotonic()
                while time.monotonic()<deadline:
                    frame=c.observe()
                    if frame.state in ('world','merchant','door'):
                        m.after_shop=True;m.reset_navigation()
                        c.log('shop_exited',floor=m.floor)
                        break
                    if frame.state=='shop' and not retried and time.monotonic()-clicked>1.2:
                        retry_back=button(frame.items,'返回',roi=(0,0,500,200))
                        if retry_back:c.tap_item(retry_back,frame);retried=True
                else:c.stop('商店返回后没有确认回到场景')
                continue
            elif st=='pause':
                if not settling:
                    c.save('manual_pause',frame)
                    c.pause_suspected=True
                    if not c.pause():c.stop('暂停页退出前未确认稳定暂停')
                    c.log('paused',reason='已核验稳定暂停，停止接管')
                    return 0
                settle=button(frame.items,'放弃并结算',exact=True)
                if settle:
                    c.tap_item(settle,frame)
                else:
                    report=button(frame.items,'战况报告',exact=True)
                    if not report:
                        c.stop('暂停页找不到战况报告/结算')
                    c.tap_item(report,frame)
            elif st=='settle_confirm':
                if not settling:
                    if args.resume and can_resume_settlement(frame):
                        settling=True
                        c.log('resume_settlement',reason='当前帧17层至少20000分，续接已发起的结算')
                    else:c.stop('无法核验本次结算资格，保留确认框')
                abandon=button(frame.items,'放弃',exact=True)
                deadline=time.monotonic()+5
                while not abandon and time.monotonic()<deadline:
                    frame=c.observe()
                    if frame.state not in ('settle_confirm','unknown'):
                        break
                    abandon=button(frame.items,'放弃',exact=True,roi=(500,900,1300,1150))
                if not abandon:c.stop('结算框5秒未出现可识别的放弃按钮')
                c.save('settle_confirm',frame)
                c.tap_item(abandon,frame)
            elif st=='result':
                floor,score=result_values(frame.items)
                c.save('settlement',frame)
                c.log('settlement_verified',floor=floor,score=score)
                if floor!=17 or score is None or score<20000:
                    c.stop('结算积分/层数未达到17层20000分，保存结果停止')
                c.log('progress',floor=floor,score=score)
                settling=True;settled=True
                # 10-07：结算页上会叠「锈蚀之徽掉落已达上限」之类的提示框，先点它的「确定」，再点空白关结算页
                notice=button(frame.items,'确定',exact=True)
                close=button(frame.items,'点击空白处关闭界面')
                if notice:
                    c.tap_item(notice,frame)
                elif close:
                    c.tap_item(close,frame)
                else:
                    c.stop('结果页关闭提示缺失')
            elif st=='lobby':
                if not settling:
                    c.log('outside_challenge',state=st)
                    return 0
                c.tap_item(button(frame.items,'深层序列',roi=(2000,0,2400,400)),frame)
            elif st=='weekly':
                if args.resume and can_resume_weekly(frame):settling=True
                if settling:
                    claim_weekly(c,frame,settled)
                    if args.home:entry.to_main()
                else:
                    c.log('outside_challenge',state=st)
                return 0
            elif st=='reward' and settling:
                c.save('reward_received',frame)
                c.tap_item(button(frame.items,'确定',exact=True),frame)
            elif st=='retry':
                c.save('layer_failed',frame)
                if retries and time.monotonic()-retry_at<8:
                    frame=c.observe()
                    continue
                if retries>=1:
                    c.stop('本次运行已重试一层，保留失败页，避免连续重复挑战')
                c.tap_item(button(frame.items,'重新挑战本层'),frame)
                c.log('retry_current_layer',count=retries+1)
                retries+=1;m.reset_navigation();m.score=None
                m.pending_score=None;m.pending_score_reads=0;retry_at=time.monotonic()
            elif st=='failure':
                frame=dismiss_failure(c,frame)
                continue
            elif st=='unknown' and settling and nav_page(frame.items)=='mode':
                # 10-14：放弃结算后可能放完收尾动画直接回模式选择页（不出结果页/快速出击页），点深层序列去领周常
                deep=next((i for i in frame.items if '深层序列' in i['text'] and i['y']<600),None)
                if not deep or mode_taps>=3:c.stop('结算后模式选择页点不进深层序列')
                c.log('settle_mode_page',reason='结算后回到模式选择页，点深层序列去领周常')
                mode_taps+=1
                c.tap_item(deep,frame)
            elif st in ('unknown','loading'):
                if st=='unknown' and time.monotonic()-same_since>12:
                    c.stop('未知页面持续12秒，不盲走')
                time.sleep(.12)
            else:
                c.stop(f'未处理页面{st}')
            frame=c.observe(active_world=st in ('world','door','merchant','battle'))
        c.stop('整次运行超过总时限',pause=frame.state in ('world','door','merchant','battle'))
    except (Exception,KeyboardInterrupt) as exc:
        if not c.guard_paused and (c.world_since is not None or c.pending_until>time.monotonic() or c.frame and c.frame.state in ('world','door','merchant','battle')):
            c.pause()
        reason=str(exc)
        if c.guard_paused and '；未确认暂停，查看pause_unconfirmed截图' in reason:
            reason=reason.replace('；未确认暂停，查看pause_unconfirmed截图','；后续重试已核验暂停/菜单')
        c.log('terminated',reason=reason,pause_verified=c.guard_paused,log_dir=str(c.log_dir))
        return 2
    finally:
        signal.signal(signal.SIGTERM,previous_sigterm)
        c.close()


if __name__=='__main__':
    raise SystemExit(main())
