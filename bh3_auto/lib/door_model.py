"""本项目25类YOLO11 ONNX解析：RGB留边、[1,29,8400]转置、按类NMS。"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from pathlib import Path
import cv2,numpy as np,onnxruntime as ort

NAV_LABELS={'aomie','zhenwo','kongmeng','shangdian','shangdian_open','jielv','tianhui',
            'fanxing','huangjin','jiushi','xvguang','fusheng','BOSS','ce_door','keyin','keyin_open'}

@dataclass
class Detection:
    label:str
    confidence:float
    box:tuple
    @property
    def center(self):
        x,y,w,h=self.box
        return x+w/2,y+h/2


class DoorModel:
    def __init__(self,path):
        options=ort.SessionOptions();options.intra_op_num_threads=2
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
        self.names=ast.literal_eval(self.session.get_modelmeta().custom_metadata_map['names'])
        self.input_name=self.session.get_inputs()[0].name
        if len(self.names)!=25 or self.session.get_outputs()[0].shape!=[1,29,8400]:
            raise ValueError('门模型元数据与25类输出不符')
    def detect(self,im,conf=.35):
        h,w=im.shape[:2];scale=min(640/w,640/h)
        nw,nh=round(w*scale),round(h*scale);px,py=(640-nw)//2,(640-nh)//2
        canvas=np.full((640,640,3),114,np.uint8)
        canvas[py:py+nh,px:px+nw]=cv2.resize(im,(nw,nh))
        tensor=np.ascontiguousarray(canvas[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255
        rows=self.session.run(None,{self.input_name:tensor})[0][0].T
        scores=rows[:,4:].max(1);classes=rows[:,4:].argmax(1)
        boxes=rows[:,:4].copy();boxes[:,:2]-=boxes[:,2:]/2
        detections=[]
        for cls in np.unique(classes[scores>=conf]):
            indices=np.where((classes==cls)&(scores>=conf))[0]
            keep=cv2.dnn.NMSBoxes(boxes[indices].tolist(),scores[indices].tolist(),conf,.45)
            for local in np.asarray(keep).reshape(-1):
                i=indices[int(local)];x,y,bw,bh=boxes[i]
                # 所有输出均换回2400×1504基准；输入分辨率不改变注入坐标。
                box=((x-px)/scale*2400/w,(y-py)/scale*1504/h,bw/scale*2400/w,bh/scale*1504/h)
                detections.append(Detection(self.names[int(cls)],float(scores[i]),tuple(float(v) for v in box)))
        return detections


def nav_candidates(detections,layer,after_shop=False):
    allowed=NAV_LABELS.copy()
    if layer is None or layer<15 or after_shop:
        allowed-={'shangdian','shangdian_open'}
    return [d for d in detections if d.label in allowed and d.confidence>=.4
            and 120<d.center[0]<2220 and 180<d.center[1]<1130
            and d.box[2]>20 and d.box[3]>20
            and (d.label!='ce_door' or d.box[3]/d.box[2]<4)]


def overlap(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    inter=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    return inter/max(1,aw*ah+bw*bh-inter)


# 11-11：门提示里的名字 → 模型标签（进门没成、贴太近退开后按名字锁定那扇门）
DOOR_LABEL={'鏖灭':'aomie','浮生':'fusheng','天慧':'tianhui','戒律':'jielv','旭光':'xvguang','繁星':'fanxing',
            '黄金':'huangjin','救世':'jiushi','真我':'zhenwo','空梦':'kongmeng'}

class DoorTracker:
    def __init__(self,prefer=None):
        # prefer：非商店层优先走过去的门（模型标签），来自角色档案 DOOR_PREFER；不给 = 符华的
        self.prefer=tuple(prefer) if prefer is not None else ('aomie','fusheng','tianhui','jielv')
        self.target=None;self.missed=0;self.step=0;self.last_dx=None
        self.camera_sign=1;self.wrong_turns=0
        self.lock=None   # 11-11：锁定的门标签（进门没成 / 贴太近退开后由 find_door_simple 设），这段找门看得见它就只追它
    def _near(self,d):
        cx,cy=self.target.center
        return overlap(d.box,self.target.box)>.1 or abs(d.center[0]-cx)<180 and abs(d.center[1]-cy)<150
    def choose(self,detections,layer,after_shop=False):
        candidates=nav_candidates(detections,layer,after_shop)
        if self.lock:
            locked=[d for d in candidates if d.label==self.lock]
            on_lock=self.target is not None and self.target.label==self.lock
            if locked and not (on_lock and any(self._near(d) for d in locked)):
                # 锁定的门在画面里、又不是原位附近那个：直接认回它（11-11 第 14 层退开后被旁边的门带走，在几扇门之间来回追到 120 秒）
                self.target=max(locked,key=lambda d:d.confidence-abs(d.center[0]-1200)/6000)
                self.missed=0;self.wrong_turns=0;self.last_dx=None
                return self.target
        if self.target and candidates:
            near=[d for d in candidates if self._near(d)]
            if self.lock and self.target.label==self.lock:
                near=[d for d in near if d.label==self.lock]   # 追着锁定的门时，附近冒出别的门不跟过去
            if near:
                target=max(near,key=lambda d:overlap(d.box,self.target.box))
                dx=target.center[0]-1200
                if self.last_dx is not None and abs(dx)>abs(self.last_dx)+80:
                    self.wrong_turns+=1
                    if self.wrong_turns>=2:
                        self.camera_sign*=-1;self.wrong_turns=0
                else:
                    self.wrong_turns=0
                self.target=target;self.missed=0
                return target
        self.missed+=1
        if self.target and self.missed<3:
            return None  # 短暂遮挡不立刻换目标。
        preferred=('shangdian','shangdian_open','keyin','keyin_open') if layer and layer>=15 and not after_shop else self.prefer+('keyin','keyin_open')
        if candidates:
            self.target=max(candidates,key=lambda d:(int(d.label in preferred),int(d.label!='ce_door'),d.confidence-abs(d.center[0]-1200)/6000))
            self.missed=0
            return self.target
        self.target=None
        return None
