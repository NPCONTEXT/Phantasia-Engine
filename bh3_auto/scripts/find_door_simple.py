"""找门阶段1：先识别页面，再短步移动；全屏OCR不受帧差预检屏蔽。

每层120秒累计预算，点击后必须有换层/新页面证据，不把点击算成功。
--observe只识别；--approach走到允许的门前后暂停，不进门。
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.er_runtime import (Controller, RunMemory, RunStopped, button,
                               door_policy, floor_of, score_of)
from scripts import er_role
from lib.door_model import DoorTracker, NAV_LABELS, DOOR_LABEL

SPIRAL = (0,0,15,30,50,75,100,130,160,195,230,265,300,335)
# 第16层（菲莉丝商店休整层）固定路线的步数上限；进店前、出店后各算一次。
# 作者实测（2026-10-06）：出生正对商店，逐步直走 + 认「商店」交互就到；买完首领门在商店旁正前方几步。
ROUTE16_STEPS = 16
# 点了「前往」这么久还没黑屏 / 加载、一直是旧层画面 = 传送没开始（10-20：历次 26 次成功进门最晚 3.2 秒就开始黑屏；
# 愈生佑翎两次站在门侧面点，提示消失、人不动，9 秒多都没黑屏）。这时往后退一段，交回找门重新对准再走过去。
# 11-03 改成每层最多 3 次、都往正后方退：原来第 2 次往左后方斜退（225），那天第 15 层退完门就跟丢了。
ENTER_STUCK_S = 4.5
BACK_OFF = (180, 180, 180)
# 11-03 点「前往」前离门太近（门框高 ≥ TOO_CLOSE_H，多半贴在门侧面）：先往正后方退一点，交回找门重新对正、走近再点；
# 每段找门（每层 / 出店后 / 重打本层都清零）最多先退 TOO_CLOSE_MAX 次，之后照点。
# 依据：10-07 起量得到门框的 8 次「点了没进去」有 6 次 ≥ 400，
# 26 次量得到普通门的成功只有 1 次 ≥ 400；「侧门」ce_door 框天生高（641、849 都进去了），不算。
TOO_CLOSE_H = 400
TOO_CLOSE_BACK = (180, 500)
TOO_CLOSE_MAX = 2


def lock_door(c, memory, name):
    """11-11：进门没成 / 贴太近退开后，这段找门锁定这扇门（DoorTracker.lock：看得见它就只追它，附近别的门不跟）。
    第 14 层：点「真我」没进去、退开后被旁边的「空梦」带走，在六扇门之间来回追到 120 秒。
    门提示里的名字换成模型标签；名字不在表里（商店等）就锁正在追的目标。"""
    t = memory.tracker
    if t is None:
        return
    label = DOOR_LABEL.get(name) or (t.target.label if t.target else None)
    if label and t.lock != label:
        t.lock = label
        c.log('door_lock', door=name, label=label, floor=memory.floor)
# 11-12 第 8 层：四扇门挤在一起，追左边的「天慧」时先碰到紧挨着的「无限」提示，拒绝后一律往右后方（130°）退，
# 又碰「螺旋」再往右后方退，人退到门群另一侧、天慧被大门挡住跟丢，脱困 3 次停（logs/elysium_20261112-012626）。
# 现在正在追的门在画面一侧时往那一侧的斜后方退；没在追门、或追的门就在正中（多半就是被拒的那扇、模型认错）照旧 130°。
REJECT_BACK = 130
REJECT_SIDE_DX = 200      # 追的门中心离画面正中（x=1200）超过这么多才算在一侧


def reject_back(tracker):
    t = tracker.target if tracker is not None else None
    if t is None:
        return REJECT_BACK
    dx = t.center[0]-1200
    if abs(dx) < REJECT_SIDE_DX:
        return REJECT_BACK
    return 225 if dx < 0 else 135


INTERRUPTS = ('battle','select','core','anchor','shop','skip','skip_confirm',
              'pause','settle_confirm','shop_confirm','result','failure','retry','weekly','lobby','reward')


def door_height(detections) -> float:
    """这一帧最高的门框（不算侧门 ce_door）的高度，2400×1504 基准；没认出门是 0。"""
    return max((d.box[3] for d in detections if d.label in NAV_LABELS and d.label != 'ce_door'), default=0)


def transition_evidence(before, after, shop=False):
    bf,af = floor_of(before.items),floor_of(after.items)
    if bf and af and af == bf+1:
        return True
    if af==8 and any('减负层' in i.get('text','') for i in before.items):
        return True
    if shop:
        return after.state in ('shop','skip','skip_confirm')
    return after.state in ('battle','select','core','anchor','result')


def enter_door(c, memory, frame, transition_seconds=10):
    # 第一张可能是主控传入，按当前帧定位再次核对类型；所有入口共用此函数。
    policy,name = door_policy(frame.items,memory.floor,memory.after_shop)
    if policy not in ('accept','accept_shop'):
        return policy
    c.move(0,60,frame)
    fresh = c.observe(active_world=True)
    memory.update(fresh)
    if fresh.state != 'door':
        return fresh.state
    p2,n2 = door_policy(fresh.items,memory.floor,memory.after_shop)
    if (p2,n2) != (policy,name):
        return 'changed'
    if memory.floor is None and not any('减负层' in i.get('text','') for i in fresh.items):
        c.stop('起始层数尚未被两张新帧确认，保留门前现场',pause=True)
    frame = fresh
    go = button(frame.items,'前往',roi=(1100,400,1650,900))
    shop = policy == 'accept_shop'
    deadline = time.monotonic()+transition_seconds
    clicked_at=time.monotonic()
    c.log('enter_attempt',door=name,policy=policy,floor=memory.floor,
          nav_elapsed_s=round(time.monotonic()-memory.nav_started,3) if memory.nav_started else 0)
    memory.expected_floor=memory.floor+1 if memory.floor else 8
    c.tap_item(go,frame)
    c.pending_until=deadline
    retries=0
    pending_animation=False
    faded=False                      # 点完以后见过黑屏 / 加载 / 读帧失败（传送开始的样子）
    while time.monotonic() < deadline:
        next_frame = c.observe(active_world=not pending_animation and c.frame.state in ('world','door','merchant'),
                               transition=True)
        if next_frame is None:
            # 已点「前往」后读帧失败：多半是传送淡出/加载期没有新画面，接着等，不停机。
            # 仍受本次换层10秒期限约束；读不到画面本身不算换层成功。
            pending_animation=True
            faded=True
            c.world_since=None
            continue
        if transition_evidence(frame,next_frame,shop):
            memory.expected_floor=memory.floor+1 if memory.floor else 8
            memory.update(next_frame)
            memory.reset_navigation()
            memory.transitions+=1
            c.pending_until=0
            c.log('enter_verified',door=name,state=next_frame.state,floor=memory.floor)
            return next_frame.state if next_frame.state != 'world' else 'entered'
        if next_frame.state in INTERRUPTS:
            if shop and next_frame.state=='battle':
                # 16层休整场景淡入时旧攻击按钮仍在；等待真实楼层/商店证据。
                pending_animation=True
                c.world_since=None
                continue
            memory.update(next_frame)
            return next_frame.state
        if next_frame.state == 'door':
            p,n = door_policy(next_frame.items,memory.floor,memory.after_shop)
            current_go=button(next_frame.items,'前往',roi=(1100,400,1650,900))
            if not current_go:
                # 输入已锁定/按钮消失，可能正在播放进门动画；不是点击失败，也不是成功。
                c.world_since=None
                pending_animation=True
                continue
            if time.monotonic()-clicked_at<1.2:
                continue
            if (p,n) == (policy,name) and retries < 1:
                c.tap_item(button(next_frame.items,'前往',roi=(1100,400,1650,900)),next_frame)
                retries+=1
                clicked_at=time.monotonic()
            else:
                c.stop('点门后仍在同一交互页；仅重试一次',pause=True)
        elif next_frame.state == 'world':
            if not faded and time.monotonic()-clicked_at >= ENTER_STUCK_S and memory.enter_retries < len(BACK_OFF):
                back=BACK_OFF[memory.enter_retries]
                memory.enter_retries+=1
                memory.expected_floor=None
                c.pending_until=0
                c.log('enter_not_started',door=name,waited_s=round(time.monotonic()-clicked_at,2),
                      retry=memory.enter_retries,back=back)
                c.move(back,900,next_frame)
                return 'not_started'
            # 门文案/GO先消失，角色仍会在旧层播放输入锁定的动画。
            # 保留有限等待，不把旧层HUD当点击失败，也不走位干扰传送。
            pending_animation=True
            c.world_since=None
            c.log('enter_pending',reason='GO消失，等待新层/新战斗证据')
        elif next_frame.state in ('unknown','loading'):
            faded=True
            # 已核对并点门后的淡出，输入被锁定；不能继续累计世界待机。
            # 仍受本次换层10秒期限约束，黑屏本身不算换层成功。
            pending_animation=True
            c.world_since=None
            c.log('enter_pending',reason='门交互后淡出/加载，等待换层证据')
    c.stop('换层等待超时',pause=c.frame.state in ('world','door','merchant'))


def find_proc(adb=None, brk=None, max_cycles=3, battle_check=None, direct=False,
              controller=None, memory=None, initial=None, mode='enter', nav_seconds=120):
    own = controller is None
    c = controller or Controller(adb)
    memory = memory or RunMemory(after_shop=direct)
    frame = initial or c.observe()
    step,recoveries = memory.nav_step,memory.nav_recoveries
    last_recovery = memory.nav_recovery_since or time.monotonic()
    memory.nav_recovery_since=last_recovery
    try:
        while memory.nav_remaining(nav_seconds) > 0:
            if getattr(c,'maintenance_handler',None):frame=c.maintenance_handler(frame)
            before_progress=(memory.floor,memory.score)
            memory.update(frame)
            if (memory.floor,memory.score)!=before_progress:
                c.log('progress',floor=memory.floor,score=memory.score)
            if memory.floor==17 and frame.state in ('world','door','merchant') and (score_of(frame.items) or 0)>=20000:
                return 'terminal_clear'
            if frame.state in INTERRUPTS:
                return frame.state
            if mode == 'observe':
                c.save('observe',frame)
                return frame.state
            if frame.state in ('unknown','loading'):
                # 战后过渡、未知页交回主控观察，决不作为允许移动的证据。
                return frame.state
            if frame.state == 'merchant':
                if memory.after_shop:
                    c.move(180,250,frame)
                else:
                    tag=button(frame.items,'商店',roi=(1100,400,1650,900))
                    c.tap_item(tag,frame)
                    deadline=time.monotonic()+20
                    c.pending_until=deadline
                    c.world_since=None
                    retry=False;clicked=time.monotonic()
                    while time.monotonic()<deadline:
                        frame=c.observe(transition=True)
                        if frame is None:   # 点商店后NPC镜头/对话加载，读帧失败接着等
                            c.world_since=None
                            continue
                        memory.update(frame)
                        if frame.state in ('shop','skip','skip_confirm','pause'):
                            c.pending_until=0
                            return frame.state
                        if frame.state=='merchant' and not retry and time.monotonic()-clicked>1.2:
                            c.tap_item(button(frame.items,'商店',roi=(1100,400,1650,900)),frame)
                            retry=True
                        # NPC镜头动画锁输入，会有较长无文字段；不盲点。
                        c.world_since=None
                    c.stop('商店交互20秒未出现菜单/剧情',pause=True)
            elif frame.state == 'door':
                policy,name=door_policy(frame.items,memory.floor,memory.after_shop)
                if policy.startswith('accept'):
                    if mode == 'approach':
                        c.save('approached',frame)
                        if not c.pause():
                            c.stop('已到门前，但暂停未确认')
                        return 'approached'
                    if memory.tracker is None:
                        memory.tracker=DoorTracker(prefer=er_role.cur().DOOR_PREFER)
                    h=door_height(frame.detections)
                    backs=getattr(memory.tracker,'too_close_backs',0)
                    if h>=TOO_CLOSE_H and backs<TOO_CLOSE_MAX:
                        # 11-03：离门太近多半贴在门侧面，点「前往」不走；先退一点，交回找门重新对正走近
                        memory.tracker.too_close_backs=backs+1
                        c.log('door_too_close',door=name,floor=memory.floor,h=round(h),times=backs+1)
                        lock_door(c,memory,name)                 # 11-11：退开后接着追这扇门
                        c.move(*TOO_CLOSE_BACK,frame)
                        last_recovery=time.monotonic()          # 退完「多久没进展」重新计，别刚转身就脱困
                        memory.nav_recovery_since=last_recovery
                        frame=c.observe(active_world=True)
                        continue
                    res=enter_door(c,memory,frame)
                    if res=='not_started':      # 传送没开始：已经往后退了，接着找门、重新对准走过去
                        lock_door(c,memory,name)                 # 11-11：重新对准的还是这扇门，别被旁边的门带走
                        # 11-03：点门、退后这几秒不算「没进展」，计时清零（那天退完 6–12 秒就触发脱困，把人带离了门）
                        last_recovery=time.monotonic()
                        memory.nav_recovery_since=last_recovery
                        frame=c.observe(active_world=True)
                        continue
                    return res
                if policy == 'reject':
                    back=reject_back(memory.tracker)
                    c.log('reject_door',door=name,floor=memory.floor,back=back)
                    c.move(back,350,frame)
                else:
                    # 缺文案/门名/按钮时只重读一次，不猜坐标。
                    c.move(0,60,frame)
                    refreshed=c.observe(active_world=True)
                    memory.update(refreshed)
                    if memory.floor==17 and refreshed.state in ('world','door','merchant') and (score_of(refreshed.items) or 0)>=20000:
                        return 'terminal_clear'
                    if refreshed.state in INTERRUPTS:
                        return refreshed.state
                    if refreshed.state != 'door':
                        frame=refreshed
                        continue
                    p,_=door_policy(refreshed.items,memory.floor,memory.after_shop)
                    if p == 'incomplete':
                        c.stop('门提示不完整，无法确认门类型',pause=True)
                    frame=refreshed
                    continue
            elif frame.state == 'world':
                if memory.tracker is None:
                    memory.tracker=DoorTracker(prefer=er_role.cur().DOOR_PREFER)
                tracker=memory.tracker
                previous=tracker.target
                target=tracker.choose(frame.detections,memory.floor,memory.after_shop)
                if memory.floor==16 and memory.route16<ROUTE16_STEPS and memory.route16_seen and not target:
                    # 10-07 第3轮出店后：首领门在左前方，越走越偏，门出了画面后又直走了12步。
                    # 认出过目标又丢了就不再直走，交给下面的通用找法（朝丢失的一侧找、转镜头）。
                    c.log('route16_handover',step=memory.route16,after_shop=memory.after_shop)
                    memory.route16=ROUTE16_STEPS
                if memory.floor==16 and memory.route16<ROUTE16_STEPS:
                    # 不转镜头，只往前走：认出商店/首领门就按左右偏差斜着走过去，认不出就直走。
                    # 碰到「商店」或门的交互提示，会在下一帧走上面的 merchant / door 分支。
                    # 走满步数还没碰到，再交给下面的通用找法（转镜头 + 跟踪 + 脱困）。
                    memory.route16+=1
                    if target:
                        memory.route16_seen=True
                        dx=target.center[0]-1200
                        deg=round(max(-55.0,min(55.0,dx/1200*90)))
                        c.log('route16',step=memory.route16,after_shop=memory.after_shop,
                              label=target.label,dx=round(dx),deg=deg)
                        c.move(deg,300 if target.box[3]>250 else 450,frame)
                    else:
                        c.log('route16',step=memory.route16,after_shop=memory.after_shop,label=None)
                        c.move(0,450,frame)
                    last_recovery=time.monotonic()
                    memory.nav_recovery_since=last_recovery
                    step+=1
                    memory.nav_step=step
                    frame=c.observe(active_world=True)
                    continue
                if target and previous:
                    progress=target.box[3]>previous.box[3]+5 or abs(target.center[0]-1200)<abs(previous.center[0]-1200)-60
                    if progress:
                        last_recovery=time.monotonic()
                        memory.nav_recovery_since=last_recovery
                elapsed=time.monotonic()-last_recovery
                if elapsed > 22:
                    if recoveries >= 3:
                        c.stop('三次脱困仍未找到门',pause=True)
                    # 半秒退步常仍留在墙角；分别后退、横移、斜退，观察后再搜。
                    c.move((180,90,225)[recoveries],(1200,900,1200)[recoveries],frame)
                    recoveries+=1
                    last_recovery=time.monotonic()
                    memory.nav_recoveries=recoveries
                    memory.nav_recovery_since=last_recovery
                    c.log('recover',count=recoveries,remaining=round(memory.nav_remaining(nav_seconds),1))
                else:
                    if target:
                        dx=target.center[0]-1200
                        c.log('track_door',label=target.label,confidence=round(target.confidence,3),
                              box=[round(v) for v in target.box],camera_sign=tracker.camera_sign)
                        c.save('nav_last',frame)
                        if abs(dx)>160:
                            tracker.last_dx=dx
                            c.camera(tracker.camera_sign*(1 if dx>0 else -1)*min(80,max(20,abs(dx)/8)),frame)
                        else:
                            tracker.last_dx=None
                            c.move(0,200 if target.box[3]>180 else 350,frame)
                    elif previous and tracker.missed<3:
                        c.move(90 if previous.center[0]>=1200 else 270,100,frame)
                    elif step%12<8:
                        tracker.last_dx=None
                        c.camera(180,frame)
                    else:
                        # 完整扫视后再换观察点；不在边缘一边小绕圈一边撞墙。
                        phase=step%12
                        c.move(180 if phase==8 else 90 if phase==9 else 0,
                               1000 if phase!=9 else 600,frame)
                step+=1
                memory.nav_step=step
            else:
                return frame.state
            frame=c.observe(active_world=True)
        c.stop(f'本层累计找门超过{nav_seconds}秒',pause=frame.state in ('world','door','merchant'))
    finally:
        if own:
            c.close()


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--observe',action='store_true')
    p.add_argument('--approach',action='store_true')
    p.add_argument('--after-shop',action='store_true')
    p.add_argument('--nav-seconds',type=float,default=120)
    p.add_argument('max_cycles',nargs='?',type=int,default=3)
    args=p.parse_args()
    try:
        result=find_proc(direct=args.after_shop,max_cycles=args.max_cycles,
                         mode='observe' if args.observe else 'approach' if args.approach else 'enter',
                         nav_seconds=args.nav_seconds)
        print('找门结果:',result,flush=True)
    except RunStopped as exc:
        print('安全停止:',exc,flush=True)
        raise SystemExit(2)


if __name__=='__main__':
    main()
