"""screenrecord→FFmpeg持续采集；仅保留最新帧，带序号/接收时间和断流重启。

接收时间不是设备曝光时间。输入后等待180ms的新帧，实机还须核验视觉变化延迟。
"""
from __future__ import annotations
import shutil, subprocess, threading, time
from pathlib import Path
import numpy as np


class FrameStream:
    WIDTH,HEIGHT=2400,1504
    def __init__(self,adb,log_dir,time_limit=180):
        self.adb=adb
        self.log_dir=Path(log_dir)
        self.time_limit=time_limit
        self.condition=threading.Condition()
        self.latest=None
        self.sequence=0
        self.stopping=threading.Event()
        self.proc=None;self.decoder=None;self.error=None
        self.worker=None;self.restarts=0
        self.started_at=None
        self.restart_requested=threading.Event()
    def age(self):
        return time.monotonic()-self.started_at if self.started_at is not None else 0
    def request_restart(self):
        self.restart_requested.set()
    def wait_restarted(self,previous_start,timeout=6):
        # 调用时已确认游戏暂停；先等新采集器与新帧就绪，再恢复游戏。
        deadline=time.monotonic()+timeout
        while self.started_at==previous_start:
            if time.monotonic()>=deadline:raise TimeoutError('暂停续流6秒未重启')
            if self.error:raise RuntimeError(self.error)
            time.sleep(.03)
        return self.read(after=self.started_at,timeout=max(.01,deadline-time.monotonic()))
    def start(self):
        if not shutil.which('ffmpeg'):
            raise RuntimeError('持续采集需要本机已有的FFmpeg')
        self.worker=threading.Thread(target=self._run,name='elysium-capture',daemon=True)
        self.worker.start()
        return self
    def _run(self):
        failures=0
        while not self.stopping.is_set() and failures<3:
            count=0
            try:
                self.restart_requested.clear()
                with (self.log_dir/'stream-adb.err').open('ab') as ae, (self.log_dir/'stream-ffmpeg.err').open('ab') as fe:
                    self.proc=subprocess.Popen([self.adb.adb,'-s',self.adb.serial,'exec-out','screenrecord',
                        '--output-format=h264',f'--size={self.WIDTH}x{self.HEIGHT}','--bit-rate=12000000',
                        f'--time-limit={self.time_limit}','-'],stdout=subprocess.PIPE,stderr=ae)
                    self.started_at=time.monotonic()
                    self.decoder=subprocess.Popen([shutil.which('ffmpeg'),'-hide_banner','-loglevel','warning',
                        '-probesize','32','-analyzeduration','0','-flags','low_delay','-f','h264','-i','pipe:0',
                        '-vf','fps=15','-pix_fmt','bgr24','-f','rawvideo','pipe:1'],
                        stdin=self.proc.stdout,stdout=subprocess.PIPE,stderr=fe)
                    self.proc.stdout.close()
                    size=self.WIDTH*self.HEIGHT*3
                    while not self.stopping.is_set() and not self.restart_requested.is_set():
                        data=self.decoder.stdout.read(size)
                        if len(data)!=size:
                            break
                        stamp=time.monotonic()
                        image=np.frombuffer(data,np.uint8).reshape(self.HEIGHT,self.WIDTH,3).copy()
                        with self.condition:
                            self.sequence+=1
                            self.latest=(self.sequence,stamp,image)
                            self.condition.notify_all()
                        count+=1
            except Exception as e:
                self.error=str(e)
            finally:
                self._terminate()
            failures=0 if count else failures+1
            self.restarts+=1
        if not self.stopping.is_set():
            self.error=self.error or '视频流连续三次启动失败'
            with self.condition:
                self.condition.notify_all()
    def _terminate(self):
        for proc in (self.proc,self.decoder):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=1)
    def read(self,after=0,sequence=0,timeout=1.2):
        deadline=time.monotonic()+timeout
        with self.condition:
            while True:
                now=time.monotonic()
                if self.latest:
                    seq,stamp,im=self.latest
                    if seq>sequence and stamp>=after and now-stamp<.3:
                        return seq,stamp,im.copy()
                if self.error:
                    raise RuntimeError(self.error)
                remaining=deadline-now
                if remaining<=0:
                    raise TimeoutError('没有新鲜视频帧，拒绝读取缓存旧图')
                self.condition.wait(min(.1,remaining))
    def stop(self):
        self.stopping.set()
        self._terminate()
        if self.worker:
            self.worker.join(timeout=2)
