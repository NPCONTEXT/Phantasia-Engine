"""乐土共用页面识别、唯一动作控制器和运行预算。坐标统一为2400×1504。

识别任务只读，主线程独占输入。慢帧触发暂停并终止，旧图不再用于点击。
"""
from __future__ import annotations
import concurrent.futures as futures
from dataclasses import dataclass, field
from pathlib import Path
import json, math, os, re, subprocess, tempfile, time, fcntl
import cv2
import numpy as np
from lib.adb import Adb
from lib.vision import imdecode, gray, match_template
from lib.frame_stream import FrameStream
from lib.maatouch import MaaTouch
from lib.door_model import DoorModel
from scripts import er_role

PROJECT = Path(__file__).resolve().parent.parent


def __getattr__(name):
    # 不进的门跟着角色档案走（elysium_roles/<角色>.py）；老代码和测试照旧用 er_runtime.BAD_DOORS 取
    if name == 'BAD_DOORS':
        return er_role.cur().BAD_DOORS
    raise AttributeError(name)


class RunStopped(RuntimeError):
    pass


def ocr(png: bytes, timeout=8) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png)
        path = f.name
    try:
        p = subprocess.run([str(PROJECT / 'ocr/ocr'), path], capture_output=True,
                           text=True, timeout=timeout)
        if p.returncode:
            raise RunStopped(f'OCR失败({p.returncode}): {p.stderr[-200:]}')
        items = []
        for line in p.stdout.splitlines():
            if line.lstrip().startswith('{'):
                try:
                    item = json.loads(line)
                    if 'text' in item:
                        items.append(item)
                except ValueError:
                    continue
        return items
    finally:
        os.unlink(path)


def button(items, text, exact=False, roi=None):
    for item in items:
        t = item.get('text', '').strip()
        if (t == text if exact else text in t):
            x, y = item['x'] + item['w']/2, item['y'] + item['h']/2
            if roi is None or roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]:
                return item
    return None


def floor_of(items):
    text = ''.join(i.get('text', '') for i in items if i.get('y', 999) < 140)
    # 字间空格/「数」的形近字不应丢掉整个HUD；完整 /17 分母优先。
    compact=re.sub(r'\s+','',text)
    m=re.search(r'抵.{0,3}?层.{0,3}?[^0-9]*([0-9]{1,2})[/／]17(?![0-9])',compact)
    if not m:m = re.search(r'抵[达迭]?层数\s*[-－]?\s*(\d{1,2})', text)
    return int(m[1]) if m and 1 <= int(m[1]) <= 17 else None


def score_of(items):
    text = ''.join(i.get('text', '') for i in sorted(items, key=lambda x:x.get('x',0))
                   if i.get('x', 999) < 500 and 170 < i.get('y', 0) < 260)
    m = re.search(r'得分\s*(\d{1,5})(?!\d)', text)
    return int(m[1]) if m else None


def world_hud(items):
    hp=any(450<i.get('x',0)<950 and 1300<i.get('y',0)<1450 and re.search(r'\d+\s*/\s*\d+',i.get('text','')) for i in items)
    support=any(i.get('x',0)>1900 and '支援' in i.get('text','') for i in items)
    return hp and (support or score_of(items) is not None)


_ATTACK = {}   # 普攻键模板，按角色档案的 ATTACK_TEMPLATE 路径缓存
def battle_by_template(png):
    # ATTACK_TEMPLATE 可以是一张图的路径，也可以是几张（任意一张认出来就算）
    paths = er_role.cur().ATTACK_TEMPLATE
    paths = (paths,) if isinstance(paths, str) else tuple(paths)
    tpls = []
    for rel in paths:
        path = PROJECT/rel
        tpl = _ATTACK.get(path)
        if tpl is None:
            tpl = cv2.imread(str(path), 0)
            if tpl is None:
                raise RunStopped('缺少战斗按钮模板')
            _ATTACK[path] = tpl
        tpls.append(tpl)
    im = imdecode(png)
    h,w = im.shape[:2]
    im = cv2.resize(im, (2400,1504)) if (w,h) != (2400,1504) else im
    g = gray(im)
    return any(match_template(g, tpl, scales=(.9,1.,1.1), roi=(1900,1050,490,450), conf=.55) for tpl in tpls)


def state_of(items, png=b'',detections=()):
    text = ' '.join(i.get('text', '') for i in items)
    # 菜单/弹窗优先；世界HUD可以透过弹窗被OCR读到。
    if '是否放弃并结算' in text and '获取对应奖励' in text:
        return 'settle_confirm'
    if '是否刷新商店' in text:
        return 'shop_confirm'
    if '真的要跳过剧情吗' in text:
        return 'skip_confirm'
    if '获得奖励' in text and button(items,'确定'):
        return 'reward'
    # 结果页标题有「挑战结束」(减负放弃结算)和「挑战成功」(普通模式)两种；它叠在大厅上，
    # 背后的「快速出击/深层序列」也读得到，所以必须排在lobby之前（10-05 full-run1-part9 被认成大厅）。
    if ('挑战结束' in text or '挑战成功' in text) and '挑战积分' in text and '到达层数' in text:
        return 'result'
    if button(items,'战斗失败',exact=True):
        return 'failure'
    if button(items,'重新挑战本层') and button(items,'直接结算'):
        return 'retry'
    if '继续战斗' in text and ('战况报告' in text or '系统设置' in text):
        return 'pause'
    if '购买刻印' in text and ('升级刻印' in text or '更换配置' in text or '返回' in text):
        return 'shop'
    if '请选择下一层' in text:
        return 'anchor'
    if '本周最高分' in text and '开始战斗' in text:
        return 'weekly'
    if '快速出击' in text and '深层序列' in text:
        return 'lobby'
    badges=[i['text'].strip() for i in items if i.get('x',0)>2100 and 300<i.get('y',0)<1300
            and i.get('text','').strip() in ('核心','普通','增幅','专属')]
    if '解锁核心刻印' in text or badges and all(t=='核心' for t in badges):
        return 'core'
    if '请选择英桀' in text and '刻印' in text or '选择刻印' in text and button(items,'确认'):
        return 'select'
    if button(items,'跳过',roi=(1600,0,2400,250)):
        return 'skip'
    if button(items,'商店',roi=(1100,400,1650,900)) and floor_of(items):
        return 'merchant'
    if '启动传送锚点' in text or '下一层' in text and button(items,'前往',roi=(1100,400,1650,900)):
        return 'door'
    if floor_of(items)==16 and world_hud(items):
        return 'world'  # 当前17层序列第16层为商店休整，按钮淡入不能当战斗。
    if png and battle_by_template(png):
        return 'battle'
    if any(d.label=='guaiwu_xueliang_UI' and d.confidence>=.5 and d.center[1]<700 for d in detections):
        return 'battle'
    if floor_of(items) or world_hud(items) or '抵达层数' in text and '减负层' in text:
        return 'world'
    if png:
        im = imdecode(png)
        if float(im.mean()) < 12 or 'LOADING' in text.upper() or '加载' in text:
            return 'loading'
    return 'unknown'


def door_policy(items, layer=None, after_shop=False):
    """唯一选门策略；不完整门名不点击，商店只在15层起且尚未采购时进入。"""
    info = ' '.join(i.get('text','') for i in sorted(items,key=lambda i:(i.get('y',0),i.get('x',0)))
                    if i.get('x',9999) < 900 and 420 < i.get('y',0) < 850)
    go = button(items,'前往',roi=(1100,400,1650,900))
    if not go or '下一层' not in info:
        return 'incomplete', ''
    if '商店' in info or '菲莉丝' in info:
        return ('accept_shop' if layer and layer >= 15 and not after_shop else 'reject'), '商店'
    for bad in er_role.cur().BAD_DOORS:
        if bad in info:
            return 'reject', bad
    # 门名可能换行或少一个引号；用整块文案兜底，仍要求能读出类型。
    seals = ('鏖灭','浮生','天慧','戒律','旭光','繁星','黄金','救世','真我','空梦')
    seal = next((s for s in seals if s in info), '')
    if seal or '首领' in info or '决战' in info or '最终' in info:
        return 'accept', seal or '首领'
    return 'incomplete', ''


@dataclass
class Frame:
    png: bytes
    items: list
    started: float
    captured: float
    finished: float
    generation: int
    state: str = ''
    width: int = 2400
    height: int = 1504
    detections: list = field(default_factory=list)


@dataclass
class RunMemory:
    floor: int | None = None
    score: int | None = None
    after_shop: bool = False
    nav_started: float | None = None
    last_frame: Frame | None = None
    transitions: int = 0
    expected_floor: int | None = None
    nav_step: int = 0
    nav_recoveries: int = 0
    nav_recovery_since: float | None = None
    tracker: object = None
    pending_floor: int | None = None
    pending_floor_reads: int = 0
    pending_score: int | None = None
    pending_score_reads: int = 0
    enter_retries: int = 0   # 这一层点了「前往」却没开始传送、退回去重来的次数（10-20）
    route16: int = 0   # 第16层固定路线已走的步数（每层、出店后各重新计）
    route16_seen: bool = False   # 这一段路线里认出过目标
    def reset_navigation(self):
        self.nav_started=None
        self.nav_step=0;self.nav_recoveries=0;self.nav_recovery_since=None
        self.tracker=None;self.enter_retries=0
        self.route16=0;self.route16_seen=False
    def update(self, frame):
        f = floor_of(frame.items)
        if f and self.floor is None and self.expected_floor is None and frame is not self.last_frame:
            self.pending_floor_reads=self.pending_floor_reads+1 if f==self.pending_floor else 1
            self.pending_floor=f
        initial_verified=self.floor is None and (f==self.expected_floor or self.expected_floor is None and self.pending_floor_reads>=2 and f==self.pending_floor)
        if f and (initial_verified or f==self.expected_floor):
            if f!=self.floor:
                self.floor=f
                self.reset_navigation()
            self.expected_floor=None
        score = score_of(frame.items)
        # 视频压缩偶尔把3000读成13000；一帧的高值不能永久抬高进度。
        if score is not None and frame is not self.last_frame:
            self.pending_score_reads=self.pending_score_reads+1 if score==self.pending_score else 1
            self.pending_score=score
        if score is not None and self.pending_score_reads>=2 and (self.score is None or score>self.score):
            self.score = score
        self.last_frame = frame
    def nav_remaining(self, limit, clock=time.monotonic):
        if self.nav_started is None:
            self.nav_started = clock()
        return limit - (clock() - self.nav_started)


class Controller:
    IDLE_LIMIT = 1.8  # 留0.2s给ADB输入；目标总待机<2s，异常要落盘。
    TRANSITION_READ = 3.0  # 换层等待窗口内单次读帧上限：够回退截图(实测约1.2s)+OCR
    def __init__(self, adb=None, log_dir=None, clock=time.monotonic, stream=True):
        self.adb = adb or Adb()
        self.clock = clock
        self.executor = futures.ThreadPoolExecutor(max_workers=1)
        self.generation = 0
        self.last_input = clock()
        self.frame = None
        self.log_dir = Path(log_dir or PROJECT/'logs'/time.strftime('elysium_%Y%m%d-%H%M%S'))
        self.log_dir.mkdir(parents=True,exist_ok=True)
        self.max_gap = 0
        self.guard_paused = False
        self.pause_suspected = False
        self.world_since=None
        self.stream_sequence=0
        self.pending_until=0
        self.observations=0
        self.previous_state=None
        self.stream=None;self.model=None;self.device_lock=None
        self.touch=None;self.holding=None   # 多指（MaaTouch）和正按住的点，见 hold()
        self.stick=None                      # 11-12 手指 2 推着摇杆的终点（边打边绕圈），见 stick_hold()
        self.floor=None                      # 主循环每轮战斗前写入当前层数（角色档案按它认首领层）
        self.seals=[]   # 这次运行选到 / 买到的刻印名（角色档案按它换打法；从暂停页接着打时前面的不知道）
        self.maintenance_handler=None
        if getattr(self.adb,'serial',None):
            name=re.sub(r'[^A-Za-z0-9_-]','_',self.adb.serial)
            self.device_lock=open(Path(tempfile.gettempdir())/f'bh3-elysium-{name}.lock','a+')
            try:fcntl.flock(self.device_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                self.device_lock.close()
                self.executor.shutdown(wait=False)
                raise RunStopped('已有乐土控制器占用此设备，拒绝并行输入')
        try:
            self.model=DoorModel(PROJECT/'models/best.onnx') if stream else None
            self.stream=FrameStream(self.adb,self.log_dir).start() if stream else None
        except Exception:
            self.close()
            raise
    def log(self, event, **fields):
        row = dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),mono=round(self.clock(),3),event=event,**fields)
        with (self.log_dir/'events.jsonl').open('a') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
        if event != 'input':
            print(json.dumps(row,ensure_ascii=False),flush=True)
    def _read(self, generation, deadline=None, pause_only=False):
        start = self.clock()
        deadline=deadline or start+10
        def remaining():
            if generation!=self.generation:raise RunStopped('已取消过期识别任务')
            seconds=deadline-self.clock()
            if seconds<=.02:raise RunStopped('本次识别预算已耗尽')
            return seconds
        def read_ocr(data):return ocr(data,timeout=min(8,remaining()))
        remaining()
        if self.stream:
            try:
                sequence,captured,im=self.stream.read(after=self.last_input+.18,sequence=self.stream_sequence,timeout=min(.45,remaining()))
                remaining()
                self.stream_sequence=sequence
            except TimeoutError:
                # 加载静帧可能不产生新视频包；回退一次真正的新截图，绝不沿用缓存。
                raw=self.adb._run(['exec-out','screencap'],timeout=min(1.5,max(.05,remaining()-.45)))
                if len(raw)<16:
                    raise RunStopped('视频断流且原生截图为空')
                rw,rh,pixel_format=np.frombuffer(raw[:12],'<u4')
                header=len(raw)-int(rw)*int(rh)*4
                if header not in (12,16) or pixel_format!=1:
                    raise RunStopped('视频断流且原生截图格式无效')
                rgba=np.frombuffer(raw[header:],np.uint8).reshape(int(rh),int(rw),4)
                im=cv2.cvtColor(rgba,cv2.COLOR_RGBA2BGR)
                captured=self.clock()
                self.log('capture_fallback',source='adb_raw',elapsed_s=round(captured-start,3))
            png=cv2.imencode('.png',im,[cv2.IMWRITE_PNG_COMPRESSION,1])[1].tobytes()
        else:
            png = self.adb.screencap()
            captured = self.clock()
            im = imdecode(png)
        h,w = im.shape[:2]
        if pause_only:
            # 暂停只需页签与继续按钮；不要再次读取整页密集战况说明。
            quick=[]
            for left,top,right,bottom in ((0,0,850,250),(1700,1250,2400,1504)):
                x0,y0=int(left*w/2400),int(top*h/1504)
                crop=im[y0:int(bottom*h/1504),x0:int(right*w/2400)]
                extra=read_ocr(cv2.imencode('.png',crop)[1].tobytes())
                quick += [dict(i,**{'x':(i['x']+x0)*2400/w,'y':(i['y']+y0)*1504/h,
                                     'w':i['w']*2400/w,'h':i['h']*1504/h}) for i in extra]
            quick_state=state_of(quick)
            if quick_state=='pause':
                return Frame(png,quick,start,captured,self.clock(),generation,'pause',2400 if self.stream else w,1504 if self.stream else h,[])
        items = read_ocr(png)
        items = [dict(i,**{k:i[k]*factor for k,factor in [('x',2400/w),('w',2400/w),('y',1504/h),('h',1504/h)]}) for i in items]
        remaining()
        detections=self.model.detect(im) if self.model and not pause_only else []
        st = state_of(items,png,detections)
        if pause_only:
            remaining()
            return Frame(png,items,start,captured,self.clock(),generation,st,2400 if self.stream else w,1504 if self.stream else h,[])
        score_hint=any(i.get('x',999)<500 and 170<i.get('y',0)<260 and '得分' in i.get('text','') for i in items)
        if st in ('world','door','merchant','battle','unknown') and (score_of(items) is None and score_hint or score_of(items) is not None and score_of(items)>20000):
            # 分数淡出/分字时单独放大核对，末层不能用历史分数代替当前画面。
            rx,ry=0,int(h*160/1504)
            rw,rh=int(w*550/2400),int(h*125/1504)
            crop=cv2.resize(im[ry:ry+rh,rx:rx+rw],None,fx=3,fy=3)
            extra=read_ocr(cv2.imencode('.png',crop)[1].tobytes())
            extra=[dict(i,**{'x':i['x']/3*2400/w,'y':(i['y']/3+ry)*1504/h,
                             'w':i['w']/3*2400/w,'h':i['h']/3*1504/h}) for i in extra]
            if score_of(extra) is not None:
                items=[i for i in items if not (i.get('x',999)<500 and 170<i.get('y',0)<260)]+extra
                st=state_of(items,png,detections)
        if st=='shop':
            label=button(items,'本次刷新消耗')
            if label and not any(re.fullmatch(r'\d{1,3}',i['text'].strip()) and
                                 label['x']+label['w']<i.get('x',0)<label['x']+label['w']+240 and
                                 abs(i.get('y',0)-label['y'])<50 for i in items):
                rx,ry=int(w*500/2400),int(h*1320/1504)
                crop=cv2.resize(im[ry:h,rx:int(w*1000/2400)],None,fx=3,fy=3)
                extra=read_ocr(cv2.imencode('.png',crop)[1].tobytes())
                items += [dict(i,**{'x':(i['x']/3+rx)*2400/w,'y':(i['y']/3+ry)*1504/h,
                                   'w':i['w']/3*2400/w,'h':i['h']/3*1504/h})
                          for i in extra if re.fullmatch(r'\d{1,3}',i.get('text','').strip())]
        if st=='door' and not button(items,'前往',roi=(1100,400,1650,900)):
            # 视频缩小后灰色交互按钮字号太小；只放大按钮所在区域补读。
            rx,ry=int(w*1100/2400),int(h*400/1504)
            rw,rh=int(w*550/2400),int(h*500/1504)
            crop=cv2.resize(im[ry:ry+rh,rx:rx+rw],None,fx=3,fy=3)
            extra=read_ocr(cv2.imencode('.png',crop)[1].tobytes())
            items += [dict(i,**{'x':(i['x']/3+rx)*2400/w,'y':(i['y']/3+ry)*1504/h,
                               'w':i['w']/3*2400/w,'h':i['h']/3*1504/h})
                      for i in extra if '前往' in i.get('text','')]
            st=state_of(items,png,detections)
        # 战斗特效和缩小帧会污染小字号层数；独立放大HUD核对，不让它受门预检影响。
        observed_floor=floor_of(items)
        hud_hint=any(i.get('y',999)<140 and 170<i.get('x',0)<650 and '抵' in i.get('text','') for i in items) or score_of(items) is not None
        if (st in ('world','battle') or hud_hint) and (observed_floor is None or observed_floor<8):
            rx,ry,rw,rh=int(w*170/2400),0,int(w*500/2400),int(h*135/1504)
            crop=cv2.resize(im[ry:ry+rh,rx:rx+rw],None,fx=2,fy=2)
            crop_png=cv2.imencode('.png',crop)[1].tobytes()
            header_items=read_ocr(crop_png)
            header_items=[dict(i,**{'x':(i['x']/2+rx)*2400/w,'y':(i['y']/2+ry)*1504/h,
                                   'w':i['w']/2*2400/w,'h':i['h']/2*1504/h}) for i in header_items]
            if floor_of(header_items):
                items=[i for i in items if not ('抵' in i.get('text','') and i.get('y',999)<140)]+header_items
                st=state_of(items,png,detections)
        # 视频按比例缩小用于识别；ADB仍按设备原生横屏2400×1504注入。
        return Frame(png,items,start,captured,self.clock(),generation,st,
                     2400 if self.stream else w,1504 if self.stream else h,detections)
    def observe(self, active_world=False, transition=False):
        # 工作线程绝不发送输入，所有动作包括应急暂停由此主线程串行执行。
        # transition=True：刚点了门/商店、仍在等换层的窗口内。加载时视频常无新包，
        # 回退的原生截图要1.2秒左右；这段时间输入本就被锁，读帧失败只算「还没加载完」，
        # 返回None由调用方继续等，不停机、不去点暂停（10-05 full-run2就停在这里）。
        in_transition = transition and self.clock() < self.pending_until
        # 慢帧保活（10-07 第2轮第10层首领战：电脑负载高时一帧三次OCR超过1.8秒预算被判停机）：
        # 世界/战斗里读帧超时，先补一下普攻或轻推摇杆让角色不进待机，换新识别线程再读；连续3次都慢才停。
        for attempt in range(self.SLOW_RETRIES + 1):
            if in_transition:
                active_world=False
                budget=min(self.TRANSITION_READ,max(.3,self.pending_until-self.clock()))
            else:
                active_world=active_world or self.world_since is not None
                idle_start=max(self.last_input,self.world_since or self.last_input)
                budget = self.IDLE_LIMIT - (self.clock()-idle_start) if active_world else 10
                if budget <= .05:
                    if attempt < self.SLOW_RETRIES and self._keepalive():
                        continue
                    self.stop('观察前已耗尽待机预算', pause=True)
            job = self.executor.submit(self._read,self.generation,deadline=self.clock()+max(.02,budget-.15))
            try:
                frame = job.result(timeout=max(.01,budget))
                break
            except Exception as exc:
                slow = isinstance(exc, futures.TimeoutError)
                reason = '截图/识别超过待机预算，丢弃未完成的帧' if slow else str(exc)
                self.generation+=1
                job.cancel()
                if in_transition:
                    self.log('transition_read_retry',reason='读帧超时' if slow else reason[:160],budget_s=round(budget,3))
                    return None
                if active_world and attempt < self.SLOW_RETRIES and self._keepalive():
                    self.log('slow_frame_keepalive',attempt=attempt+1,reason=reason[:160],budget_s=round(budget,3))
                    self._fresh_executor()
                    continue
                self.stop(reason,pause=active_world)
        if frame.generation != self.generation:
            self.stop('截图期间发生输入，图像已失效',pause=active_world)
        if frame.state in ('world','door','merchant','battle'):
            if self.world_since is None:
                self.world_since=frame.started
        elif frame.state!='unknown':
            self.world_since=None
        self.frame = frame
        self.log('observe',state=frame.state,capture_s=round(max(0,frame.captured-frame.started),3),
                 total_s=round(frame.finished-frame.started,3),source_age_s=round(frame.finished-frame.captured,3),
                 floor=floor_of(frame.items),score=score_of(frame.items))
        self.observations+=1
        if frame.state!=self.previous_state or frame.state=='world' and self.observations%8==0:
            self.save(f'frame_{self.observations:05d}_{frame.state}',frame)
            self.previous_state=frame.state
        return frame
    MENU_FRESH = 6.0  # 菜单页（选刻印/锚点/商店/剧情等）画面不会自己变，按它点击的有效期可以放宽
    SLOW_RETRIES = 3
    def _keepalive(self):
        """慢帧保活：最近确认的是战斗就补一下普攻，世界里就轻推一下摇杆；菜单页不需要，返回False。"""
        last = self.frame.state if self.frame else None
        if last == 'battle':
            self._input(lambda: self.adb.tap(2175,1278,delay_after=0),'keepalive_attack',True)
        elif last in ('world','door','merchant'):
            self._input(lambda: self.adb.swipe(300,1207,300,1147,60),'keepalive_nudge',True)
        else:
            return False
        return True
    def _fresh_executor(self):
        """丢弃卡住的识别线程：它跑完会自己结束（只读，不发输入），新任务不用排在它后面。"""
        old=self.executor
        self.executor=futures.ThreadPoolExecutor(max_workers=1)
        old.shutdown(wait=False,cancel_futures=True)
    def _fresh(self, frame):
        live = frame is not None and frame.state in ('world','door','merchant','battle')
        limit = self.IDLE_LIMIT if live or frame is None else self.MENU_FRESH
        # 10-07 第10层选刻印：录屏按计划重启时退回原生截图(1.2秒)，整帧1.83秒，被1.8秒的世界防待机门槛误判过期而停机
        if frame and (frame.generation != self.generation or self.clock()-frame.started > limit):
            self.stop('拒绝按过期截图操作',pause=live)
    def _input(self, fn, name, active=False, multi=False):
        if not multi:
            self.release_hold()   # 单指输入会打断正按着的手势：先松开按住的手指
        gap = max(0,self.clock()-max(self.last_input,self.world_since or self.clock()))
        if active:
            self.max_gap = max(self.max_gap,gap)
        start = self.clock()
        fn()
        self.last_input = self.clock()
        self.generation += 1
        self.log('input',action=name,gap_s=round(gap,3),duration_s=round(self.last_input-start,3),active=active)
    # 多指（10-20 愈生佑翎一直按住普攻）：手指 0 按住攻击键不松，手指 1 点必杀 / 支援。
    # 任何单指输入（点、走位、转镜头、暂停、保活）、离开战斗、停止、关闭之前都先松开。
    def hold(self, x, y):
        if self.holding==(x,y):return
        self.release_hold()
        def press():
            if self.touch is None:
                self.touch=MaaTouch(self.adb).start()
                self.log('maatouch_started',header=self.touch.header)
            self.touch.down(0,x,y)
        try:
            self._input(press,f'hold_down({x},{y})',True,multi=True)
        except Exception as e:
            self._drop_touch()
            self.stop(f'多指触控出错：{e}',pause=True)
        self.holding=(x,y)
    def mtap(self, x, y):
        if self.holding is None:
            return self.tap(x,y,active=True)   # 没按着就是普通单指点
        try:
            self._input(lambda:self.touch.tap(1,x,y),f'mtap({x},{y})',True,multi=True)
        except Exception as e:
            self.holding=None;self.stick=None;self._drop_touch()
            self.stop(f'多指触控出错：{e}',pause=True)
    # 11-12 首领层边打边绕圈（作者：「动一下方向键，一直往左或往右，转圈攻打boss」）：手指 0 按着攻击时，
    # 手指 2 按在摇杆中心（同 move() 的 300,1207）再分两段拖到 (x, y)，一直推着；松手跟着 release_hold 一起松。
    STICK_CENTER = (300, 1207)
    def stick_hold(self, x, y):
        if self.holding is None or self.touch is None:
            return False                       # 没按着攻击就不推（单指打法、刚松手）
        if self.stick==(x,y):
            return True
        cx,cy=self.STICK_CENTER
        def push():
            if self.stick is None:
                self.touch.down(2,cx,cy)
                self.touch.move(2,(cx+x)/2,(cy+y)/2)
            else:
                self.touch.move(2,cx,cy)        # 已经推着别的方向（换方向）：拖回中心再拖过去，不重按
            self.touch.move(2,x,y)
        try:
            self._input(push,f'stick_down({x},{y})',True,multi=True)
        except Exception as e:
            self.holding=None;self.stick=None;self._drop_touch()
            self.stop(f'多指触控出错：{e}',pause=True)
        self.stick=(x,y)
        return True
    def release_hold(self):
        # 松手不算新输入（不让手里的截图作废）：离开战斗松手后，还要按刚读的那帧去点
        if self.stick is not None:
            self.stick=None
            try:
                self.touch.up(2)
                self.log('input',action='stick_up',active=True)
            except Exception as e:
                self.log('stick_release_failed',reason=str(e))
        if self.holding is None:return
        self.holding=None
        try:
            self.touch.up(0)
            self.log('input',action='hold_up',active=True)
        except Exception as e:
            self.log('hold_release_failed',reason=str(e))
            self._drop_touch()
    def _drop_touch(self):
        t,self.touch=self.touch,None
        if t:t.close()
    def tap(self, x, y, frame=None, active=False):
        self._fresh(frame)
        w,h = (frame.width,frame.height) if frame else (2400,1504)
        self._input(lambda:self.adb.tap(round(x*w/2400),round(y*h/1504),delay_after=0),f'tap({x:.0f},{y:.0f})',active)
    def tap_item(self, item, frame):
        self.tap(item['x']+item['w']/2,item['y']+item['h']/2,frame,frame.state in ('door','merchant','world','battle'))
        if any(k in item['text'] for k in ('前往','继续战斗','重新挑战本层','开始战斗')):
            self.pending_until=self.clock()+5
            self.pause_suspected=False
            self.guard_paused=False
    def move(self, deg, ms, frame):
        if frame.state not in ('world','door','merchant'):
            self.stop(f'{frame.state}页面禁止走位')
        self._fresh(frame)
        rad = math.radians(deg)
        w,h = frame.width/2400,frame.height/1504
        self._input(lambda:self.adb.swipe(round(300*w),round(1207*h),round((300+math.sin(rad)*220)*w),
                     round((1207-math.cos(rad)*220)*h),ms),f'move({deg},{ms})',True)
    def camera(self, dx, frame):
        if frame.state != 'world':
            self.stop(f'{frame.state}页面禁止转镜头')
        self._fresh(frame)
        w,h = frame.width/2400,frame.height/1504
        def action():
            self.adb.swipe(round(1900*w),round(750*h),round((1900+dx)*w),round(750*h),180)
            # 转镜头不能假设能打断角色待机；紧接一个小走位，然后重新观察。
            self.adb.swipe(round(300*w),round(1207*h),round(300*w),round(987*h),60)
        self._input(action,f'camera_and_nudge({dx})',True)
    def pause(self):
        # 进门/加载动画可能吞输入；发送暂停并不等于已暂停。
        deadline=self.clock()+10
        # 刚点门/NPC后的未知画面可能已是剧情，左上会变成「隐藏」。
        # 先识别一次再决定；普通活动世界仍立即暂停。
        next_tap=self.clock()+.8 if self.pause_suspected or self.pending_until and self.world_since is None else 0
        pause_seen=None
        menu_seen=None;menu_kind=None
        active_seen=0
        while self.clock()<deadline:
            if self.clock()>=next_tap:
                self._input(lambda:self.adb.tap(82,70,delay_after=0),'emergency_pause',True)
                # 10-07 第3轮第17层：负载高时读一次暂停画面要1.5秒，旧的1.2秒补点会在已暂停后再点一下，
                # 而暂停页左上同一位置是「返回」=把游戏恢复，结果在暂停/继续之间来回切。补点前至少等2.8秒。
                next_tap=self.clock()+2.8
            try:
                began=self.clock()
                frame=self._read(self.generation,deadline=self.clock()+2.0,pause_only=True)   # 负载高时1.15秒不够读两块小图
                self.frame=frame
                self.log('pause_observe',state=frame.state,total_s=round(self.clock()-began,3))
                if frame.state=='pause':
                    self.pause_suspected=True
                    self.world_since=None
                    active_seen=0
                    pause_seen=self.clock() if pause_seen is None else pause_seen
                    next_tap=deadline  # 已看到暂停页，继续观察，不能再点左上把它恢复。
                else:
                    pause_seen=None
                    if self.pause_suspected and frame.state in ('world','door','merchant','battle'):
                        active_seen+=1
                        if active_seen>=2:
                            self.pause_suspected=False
                            self.world_since=frame.started
                            next_tap=0
                    else:active_seen=0
                blocked=frame.state in ('skip','skip_confirm','shop','shop_confirm','select','core','anchor','lobby','weekly','reward')
                if blocked:
                    menu_seen=self.clock() if menu_kind!=frame.state else menu_seen
                    menu_kind=frame.state
                    next_tap=deadline
                else:
                    menu_seen=None;menu_kind=None
                verified_pause=frame.state=='pause' and self.clock()-pause_seen>=.6
                verified_menu=blocked and self.clock()-menu_seen>=.6
                verified_terminal=frame.state in ('retry','result','failure') and self.clock()>=self.pending_until
                if verified_pause or verified_terminal or verified_menu:
                    self.guard_paused=True
                    self.world_since=None
                    self.pending_until=0
                    self.save('pause_verified',frame)
                    self.log('blocked_menu_verified' if verified_menu else 'pause_verified',state=frame.state)
                    return True
            except Exception as exc:
                self.log('pause_check_failed',reason=str(exc))
            time.sleep(.03)
        self.log('pause_unconfirmed')
        self.save('pause_unconfirmed')
        return False
    def save(self, name, frame=None):
        frame = frame or self.frame
        if frame:
            (self.log_dir/(name+'.png')).write_bytes(frame.png)
            (self.log_dir/(name+'.json')).write_text(json.dumps(frame.items,ensure_ascii=False,indent=2))
    def stop(self, reason, pause=False):
        self.log('stop',reason=reason,max_active_gap_s=round(self.max_gap,3))
        self.save('stopped')
        self.release_hold()
        if pause or self.world_since is not None:
            try:
                if not self.pause():
                    reason+='；未确认暂停，查看pause_unconfirmed截图'
            except Exception as e:
                self.log('pause_failed',reason=str(e))
        raise RunStopped(reason)
    def close(self):
        self.release_hold()
        self._drop_touch()
        self.executor.shutdown(wait=False,cancel_futures=True)
        if self.stream:
            self.stream.stop()
        if self.device_lock:
            self.device_lock.close()


def reward_marks(png):
    """只核验已领取绿勾；未领取按钮另做定位，避免把点击当领取成功。"""
    im = cv2.resize(imdecode(png),(2400,1504))
    hsv = cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv,(35,100,110),(85,255,255))
    return [int(cv2.countNonZero(mask[1400:1490,x-55:x+55])) > 160
            for x in (309,509,709,909,1109,1309,1509,1709,1909)]
