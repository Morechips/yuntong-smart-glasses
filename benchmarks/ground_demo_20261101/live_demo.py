"""Latest-frame desktop demo. X5 inference is explicit; PC fallback is labeled."""
from pathlib import Path
import argparse,json,math,threading,time,urllib.request,sys,uuid
from urllib.parse import urlsplit
import tkinter as tk
from tkinter import ttk,messagebox,simpledialog
import cv2,numpy as np
from PIL import Image,ImageTk
from backends import OnnxBackend,X5Backend
from demo_core import render,MAX_AGE_S
from speech_output import SpeechOutput,load_clips,play_clip
from corridor_decision import decision_engine,DEFAULT_DECISION_MODE
from run_storage import RunStorage

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'hardware_test/esp_dataset_capture'))
from live_stream import LiveStream
from resilient_stream import ResilientStream


class PulseOutput:
    def __init__(self,url,enabled,stop,log,max_pulses=0,pulse_ms=100,output_lock=None):
        if not 20<=pulse_ms<=500:raise ValueError('Pulse duration must be 20..500ms')
        self.url=url.rstrip('/');self.enabled=enabled;self.stop=stop;self.log=log
        self.lock=threading.Lock();self.latest=None;self.last_sent=-1e9
        self.max_pulses=max_pulses;self.sent=0
        self.pulse_ms=int(pulse_ms)
        self.output_lock=output_lock if output_lock is not None else threading.Lock()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def update(self,result,received):
        with self.lock:self.latest=(dict(result),received)
    def clear(self):
        with self.lock:self.latest=None
    def _run(self):
        if not self.enabled:return
        while not self.stop.wait(.04):
            if not self.output_lock.acquire(blocking=False):continue
            try:
                with self.lock:item=self.latest
                if not item or self.stop.is_set():continue
                result,received=item;now=time.monotonic()
                if result['status']!='OBSTACLE' or not 0<=now-received<=MAX_AGE_S or now-self.last_sent<1.5:continue
                channel={'LEFT':'left','CENTER':'both','RIGHT':'right'}[result['direction']]
                self.last_sent=now
                request=urllib.request.Request(self.url+'/motor?channel='+channel+'&ms='+str(self.pulse_ms),data=b'',method='POST')
                with self.opener.open(request,timeout=1.5) as response:reply=json.load(response)
                if reply.get('ok') is not True or reply.get('final_level')!=0:raise ValueError('Motor did not acknowledge bounded pulse')
                self.log(dict(type='haptic',channel=channel,pulse_ms=self.pulse_ms,reply=reply,
                              result_request_id=result.get('request_id'),cue_started_age_ms=(now-received)*1000))
                self.sent+=1
                if self.max_pulses and self.sent>=self.max_pulses:
                    self.enabled=False;self.log(dict(type='haptic_limit_reached',limit=self.max_pulses));return
            except Exception as exc:
                # A failed pulse is not retried; disable until the next explicit start.
                self.log(dict(type='haptic_error',error=str(exc)));self.enabled=False;return
            finally:self.output_lock.release()


class Demo:
    def __init__(self,root,url='http://192.168.137.44',mode='X5 BPU',seconds=0,pulse_ms=500):
        self.root=root;root.title('云瞳：阶段版 v0.1 持续障碍提示')
        self.seconds=seconds
        self.run_deadline=None;self.run_started=None
        self.ssh_password=None
        root.minsize(820,750)
        self.url=tk.StringVar(value=url)
        self.mode=tk.StringVar(value=mode);self.motor=tk.BooleanVar(value=False)
        self.pulse_ms=tk.StringVar(value=str(pulse_ms))
        self.voice=tk.BooleanVar(value=True)
        self.speech_volume=tk.StringVar(value='55')
        self.decision=tk.StringVar(value='旧版对照' if DEFAULT_DECISION_MODE=='legacy' else '通道优先')
        self.voice_status=tk.StringVar(value='语音：开始后遇到障碍才播报；可先试听。')
        self.voice_note=self.voice_status.get();self.audition_worker=None
        self.status=tk.StringVar(value='尚未运行。先上电并连接 ESP；X5 模式还需要网线连接。')
        self.haptic_status=tk.StringVar(value='触觉：未运行，请在开始前勾选；接口回执不代表实际触感。')
        self.haptic_note=self.haptic_status.get()
        self.display_lock=threading.Lock();self.current=None;self.stop=threading.Event();self.worker=None
        panel=ttk.Frame(root,padding=10);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='ESP地址').grid(row=0,column=0)
        ttk.Entry(panel,textvariable=self.url,width=29).grid(row=0,column=1)
        ttk.Combobox(panel,textvariable=self.mode,values=['PC ONNX','X5 BPU'],state='readonly',width=12).grid(row=0,column=2)
        self.start_button=ttk.Button(panel,text='开始持续运行' if seconds==0 else '开始%d秒演示'%seconds,command=self.start);self.start_button.grid(row=0,column=3,padx=8)
        ttk.Button(panel,text='停止',command=self.request_stop).grid(row=0,column=4)
        self.haptic_checkbox=ttk.Checkbutton(panel,text='开始前启用触觉提示（左/双/右表示障碍方向）',variable=self.motor)
        self.haptic_checkbox.grid(row=1,column=0,columnspan=4,sticky='w',pady=6)
        duration=ttk.Frame(panel);duration.grid(row=1,column=4)
        self.duration_control=ttk.Combobox(duration,textvariable=self.pulse_ms,values=['100','300','500'],state='readonly',width=5)
        self.duration_control.pack(side='left');ttk.Label(duration,text='毫秒').pack(side='left')
        self.voice_checkbox=ttk.Checkbutton(panel,text='启用语音：左侧／前方／右侧有障碍',variable=self.voice)
        self.voice_checkbox.grid(row=2,column=0,columnspan=3,sticky='w')
        volume=ttk.Frame(panel);volume.grid(row=2,column=3)
        ttk.Label(volume,text='音量').pack(side='left')
        self.volume_control=ttk.Combobox(volume,textvariable=self.speech_volume,values=['25','40','55','70'],state='readonly',width=4)
        self.volume_control.pack(side='left')
        self.audition_button=ttk.Button(panel,text='试听前方提示',command=self.audition);self.audition_button.grid(row=2,column=4)
        ttk.Label(panel,textvariable=self.voice_status,wraplength=760).grid(row=3,column=0,columnspan=5,sticky='w')
        ttk.Label(panel,textvariable=self.haptic_status,wraplength=760).grid(row=4,column=0,columnspan=5,sticky='w')
        self.image_label=ttk.Label(panel);self.image_label.grid(row=5,column=0,columnspan=5)
        ttk.Label(panel,textvariable=self.status,wraplength=760).grid(row=6,column=0,columnspan=5,sticky='w',pady=8)
        ttk.Label(panel,text='演示限定：原走廊、近水平视角、白箱。蓝色物体会漏检；无候选不代表通道安全。').grid(row=7,column=0,columnspan=5,sticky='w')
        decision=ttk.Frame(panel);decision.grid(row=8,column=0,columnspan=5,sticky='w',pady=6)
        ttk.Label(decision,text='决策模式（开始前选择）').pack(side='left')
        self.decision_control=ttk.Combobox(decision,textvariable=self.decision,values=['旧版对照','通道优先'],state='readonly',width=14)
        self.decision_control.pack(side='left')
        ttk.Label(decision,text='  通道内优先；紫色为估计区域，灰色表示降级').pack(side='left')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(40,self.refresh)
    def request_stop(self):
        self.stop.set()
        if self.worker and self.worker.is_alive():self.status.set('正在停止；已开始的一句语音会播完，不再播下一句。')
    def audition(self):
        if (self.worker and self.worker.is_alive()) or (self.audition_worker and self.audition_worker.is_alive()):return
        url=self.url.get();volume=int(self.speech_volume.get())
        password=self.ssh_password
        if getattr(self,'esp_via_x5',False) and password is None:
            password=simpledialog.askstring('X5 SSH','输入 sunrise 用户的SSH密码（只在内存中使用）',show='*',parent=self.root)
            if password is None:return
            self.ssh_password=password
        def play():
            backend=None;link=None
            try:
                parsed=urlsplit(url)
                if parsed.scheme!='http' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('ESP地址无效')
                with self.display_lock:self.voice_note='语音：正在试听前方提示……'
                peripheral_url='http://'+parsed.hostname+':8081'
                if getattr(self,'esp_via_x5',False):
                    from x5_camera_link import X5CameraLink
                    backend=X5Backend(password);link=X5CameraLink(backend);peripheral_url=link.peripheral_url
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                play_clip(opener,peripheral_url,load_clips()['CENTER'],volume)
                with self.display_lock:self.voice_note='语音：音量%d试听回执成功，请以实际听音判断。'%volume
            except Exception as exc:
                with self.display_lock:self.voice_note='语音：试听失败：'+str(exc)
            finally:
                if link:link.close()
                if backend:backend.close()
        self.audition_worker=threading.Thread(target=play,daemon=False);self.audition_worker.start()
    def close(self):
        self.request_stop()
        if (self.worker and self.worker.is_alive()) or (self.audition_worker and self.audition_worker.is_alive()):
            self.status.set('正在停止输入、提示和X5进程……');self.root.after(150,self.close)
        else:self.root.destroy()
    def start(self,password=None):
        if (self.worker and self.worker.is_alive()) or (self.audition_worker and self.audition_worker.is_alive()):return
        url=self.url.get().rstrip('/');mode=self.mode.get();motor=self.motor.get()
        try:
            parsed=urlsplit(url)
            if parsed.scheme!='http' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ['', '/']:raise ValueError('请输入 http://ESP地址，不带路径')
        except Exception as exc:messagebox.showerror('地址有误',str(exc));return
        if password is None:password=self.ssh_password
        if mode=='X5 BPU' and password is None:
            password=simpledialog.askstring('X5 SSH','输入 sunrise 用户的SSH密码（只在内存中使用）',show='*',parent=self.root)
            if password is None:return
        if mode=='X5 BPU':self.ssh_password=password
        self.run_pulse_ms=int(self.pulse_ms.get())
        self.run_speech_volume=int(self.speech_volume.get())
        self.run_decision_mode='adaptive' if self.decision.get()=='通道优先' else 'legacy'
        self.run_deadline=None;self.run_started=None
        self.stop=threading.Event();self.start_button.configure(state='disabled')
        self.haptic_checkbox.configure(state='disabled')
        self.duration_control.configure(state='disabled')
        self.voice_checkbox.configure(state='disabled');self.audition_button.configure(state='disabled')
        self.volume_control.configure(state='disabled');self.decision_control.configure(state='disabled')
        self.worker=threading.Thread(target=self.run,args=(url,mode,motor,password,self.voice.get()),daemon=False);self.worker.start()
    def run(self,url,mode,motor,password,voice=False):
        cv2.setNumThreads(1)
        stamp=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6];out=HERE/'runs'/('live_'+stamp);out.mkdir(parents=True,exist_ok=False)
        log_lock=threading.Lock();storage=RunStorage(out);haptic_count=0;haptic_error=None;speech_count=0;speech_error=None
        self.feedback_failure='';self.connection_note='连接图传中'
        with self.display_lock:self.haptic_note='触觉：本轮等待障碍，回执0次' if motor else '触觉：本轮关闭'
        with self.display_lock:self.voice_note='语音：本轮已启用，等待障碍' if voice else '语音：本轮关闭'
        def log(row):
            nonlocal haptic_count,haptic_error,speech_count,speech_error
            row=dict(row,pc_monotonic=time.monotonic())
            with log_lock:
                storage.add(row)
                if row['type']=='haptic':
                    haptic_count+=1
                    with self.display_lock:self.haptic_note='触觉：ESP已回执%d次（本轮）；是否感觉到仍需实际确认。'%haptic_count
                elif row['type']=='haptic_error':
                    haptic_error=row['error']
                    self.feedback_failure='触觉故障，请停止并重新开始：'+haptic_error
                    with self.display_lock:self.haptic_note='触觉：请求失败，已停用：'+haptic_error
                elif row['type']=='haptic_limit_reached':
                    with self.display_lock:self.haptic_note='触觉：已达%d次测试上限，已停止'%row['limit']
                elif row['type']=='speech':
                    speech_count+=1
                    with self.display_lock:self.voice_note='语音：已回执%d句，最近：%s'%(speech_count,row['text'])
                elif row['type']=='speech_error':
                    speech_error=row['error']
                    self.feedback_failure='语音故障，请停止并重新开始：'+speech_error
                    with self.display_lock:self.voice_note='语音：请求失败，已停用：'+speech_error
                elif row['type']=='speech_limit_reached':
                    with self.display_lock:self.voice_note='语音：已达%d句检查上限，已停止'%row['limit']
        stream=None;backend=None;pulse=None;speech=None;camera_link=None;latest_lock=threading.Lock();latest=[None];gate=decision_engine(getattr(self,'run_decision_mode',DEFAULT_DECISION_MODE));error=None
        try:
            log(dict(type='configuration',decision_mode=getattr(self,'run_decision_mode',DEFAULT_DECISION_MODE),
                     speech_enabled=voice,haptic_enabled=motor,speech_volume=getattr(self,'run_speech_volume',25),
                     pulse_ms=getattr(self,'run_pulse_ms',100),max_pulses=getattr(self,'max_pulses',0),
                     max_utterances=getattr(self,'max_utterances',0),closed_loop_requested=bool(voice and motor),continuous=getattr(self,'seconds',120)==0))
            with self.display_lock:self.current=dict(text='正在加载 '+mode+'，等待ESP图像……',image=None)
            backend=X5Backend(password) if mode=='X5 BPU' else OnnxBackend();password=None
            log(dict(type='backend',metadata=backend.metadata))
            # An actual BIN is checked against two quantized-emulator outputs before actuators.
            if mode=='X5 BPU':
                from verify_board import verify_backend
                verification=verify_backend(backend,out/'board_check',sample_ids=['A01_02','A02_02'])
                if not verification['candidate_contract_equal'] or verification['min_pixel_agreement']!=1.0:raise RuntimeError('X5输出核对未通过，停止实流')
            peripheral_url='http://'+urlsplit(url).hostname+':8081'
            camera_url=url
            if getattr(self,'esp_via_x5',False):
                if mode!='X5 BPU':raise ValueError('经RDK访问ESP需要选择X5 BPU后端')
                from x5_camera_link import X5CameraLink
                camera_link=X5CameraLink(backend)
                camera_url=camera_link.camera_url;peripheral_url=camera_link.peripheral_url
                log(dict(type='camera_route',route='ESP via X5 SSH loopback',esp_host=camera_link.host,mac='90:70:69:fe:56:0c'))
            output_lock=threading.Lock()
            pulse=PulseOutput(peripheral_url,motor,self.stop,log,getattr(self,'max_pulses',0),getattr(self,'run_pulse_ms',100),output_lock)
            speech=SpeechOutput(peripheral_url,voice,self.stop,log,output_lock,max_utterances=getattr(self,'max_utterances',0),volume=getattr(self,'run_speech_volume',25))
            if motor:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(peripheral_url+'/state',timeout=2) as response:state=json.load(response)
                if not state.get('motor_enabled') or state.get('motor_cycle_enabled'):raise RuntimeError('ESP当前马达接口不可用或循环测试仍开启')
                log(dict(type='esp_state',state=state))
            def arrived(jpeg,headers,received):
                with latest_lock:latest[0]=(jpeg,headers,received)
            continuous=getattr(self,'seconds',120)==0
            def camera_state(state,details):
                self.connection_note={'connecting':'连接图传中','connected':'图传已连接','disconnected':'图传断开，等待重连'}.get(state,state)
                if details.get('reconnects'):self.connection_note+='（第%d次）'%details['reconnects']
                log(dict(type='camera_connection',state=state,**details))
            stream=ResilientStream(camera_url,camera_state) if continuous else LiveStream(camera_url)
            stream.subscribe(arrived);stream.start()
            stream_started=time.monotonic();deadline=None if continuous else stream_started+getattr(self,'seconds',120)
            last_received=None;request_id=0;waiting_marked=False
            self.run_deadline=deadline;self.run_started=stream_started
            last_preview=-1e9;preview_index=0;last_checkpoint=-1e9
            while not self.stop.is_set() and (deadline is None or time.monotonic()<deadline):
                if time.monotonic()-last_checkpoint>=5:
                    last_checkpoint=time.monotonic()
                    with log_lock:storage.checkpoint(active=True,elapsed_s=last_checkpoint-stream_started,
                        camera_reconnects=getattr(stream,'reconnects',0),connection=self.connection_note,
                        speech_error=speech_error,haptic_error=haptic_error)
                with latest_lock:item=latest[0]
                if item is None or item[2]==last_received:
                    if stream.done.is_set():raise RuntimeError(stream.error or '摄像头断流')
                    if continuous and (not stream.active_generation or last_received is None or time.monotonic()-last_received>MAX_AGE_S):
                        pulse.clear();speech.clear();gate.reset()
                        if not waiting_marked:
                            with self.display_lock:self.current=dict(image=None,text='UNKNOWN：'+self.connection_note+'；不发送旧提示')
                            waiting_marked=True
                    self.stop.wait(.01);continue
                jpeg,headers,received=item;last_received=received
                if time.monotonic()-received>MAX_AGE_S:
                    pulse.clear();speech.clear();gate.reset();self.stop.wait(.02);continue
                request_id+=1;category,timing=backend.infer(jpeg,request_id)
                if continuous and not stream.is_current(headers.get('x-demo-connection')):
                    pulse.clear();speech.clear();gate.reset();continue
                waiting_marked=False
                key=(headers.get('x-device-boot-id'),headers.get('x-stream-id'),headers.get('x-demo-connection'))
                result=gate.update(category,received,time.monotonic(),key)
                result['request_id']=request_id
                if result['status']=='UNKNOWN':pulse.clear();speech.clear()
                else:pulse.update(result,received);speech.update(result,received)
                bgr=cv2.imdecode(np.frombuffer(jpeg,np.uint8),1)
                display=render(bgr,category,result,backend.name)
                if time.monotonic()-last_preview>=5:
                    preview_index=(preview_index%12)+1 if continuous else preview_index+1;last_preview=time.monotonic()
                    ok,encoded=cv2.imencode('.jpg',display)
                    if not ok:raise RuntimeError('Preview encoding failed')
                    encoded.tofile(out/('preview_%03d.jpg'%preview_index))
                    (out/('source_%03d.jpg'%preview_index)).write_bytes(jpeg)
                    ok,encoded=cv2.imencode('.png',category)
                    if not ok:raise RuntimeError('Category encoding failed')
                    encoded.tofile(out/('category_%03d.png'%preview_index))
                log(dict(type='result',request_id=request_id,headers=headers,result=result,timing=timing,
                         timing_scope='PC full-JPEG receive to result only; not sensor exposure'))
                with self.display_lock:self.current=dict(image=display,result=result,received=received,text=backend.name)
        except Exception as exc:
            error=str(exc)
            try:log(dict(type='error',error=error))
            except Exception:pass
        finally:
            self.stop.set()
            if speech:speech.clear();speech.thread.join(8)
            if pulse:pulse.clear();pulse.thread.join(2)
            if stream:
                stream.stop()
                if stream.thread:stream.thread.join(7 if getattr(self,'seconds',120)==0 else 3)
            if camera_link:
                try:camera_link.close()
                except Exception as exc:
                    error=error or str(exc)
            if backend:backend.close()
            stats=storage.statistics()
            summary=dict(backend=mode,error=error,haptic_enabled=motor,
                         decision_mode=getattr(self,'run_decision_mode',DEFAULT_DECISION_MODE),
                         physical_haptic_confirmation=False,x5_cleanup=getattr(backend,'cleanup',None),
                         configured_pulse_ms=getattr(self,'run_pulse_ms',100),
                         speech_enabled=voice,speech_acknowledgments=speech_count,speech_error=speech_error,
                         closed_loop_requested=bool(voice and motor),
                         both_feedback_acknowledged=bool(speech_count and haptic_count and not speech_error and not haptic_error),
                         haptic_error=haptic_error,speech_volume=getattr(self,'run_speech_volume',25),
                         exposure_to_cue_measured=False,continuous=getattr(self,'seconds',120)==0,
                         requested_seconds=getattr(self,'seconds',120),camera_received=stream.received if stream else 0,
                         camera_reconnects=getattr(stream,'reconnects',0),**stats)
            try:
                storage.checkpoint(active=False,error=error)
                (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception as exc:error=error or ('保存记录失败：'+str(exc))
            finally:storage.close()
            with self.display_lock:
                self.current=dict(image=None,text=('已停止：'+error if error else '本轮演示已停止')+'。记录：'+str(out))
                self.haptic_note='触觉：本轮已停止；ESP回执%d次'%haptic_count+('；错误：'+haptic_error if haptic_error else '')
                self.voice_note='语音：本轮已停止；ESP回执%d句'%speech_count+('；错误：'+speech_error if speech_error else '')
    def refresh(self):
        with self.display_lock:
            item=self.current.copy() if self.current else None
            haptic_note=self.haptic_note
            voice_note=self.voice_note
        self.haptic_status.set(haptic_note)
        self.voice_status.set(voice_note)
        if item:
            image=item.get('image');result=item.get('result');text=item['text']
            if not result and self.worker and self.worker.is_alive() and self.seconds==0 and self.run_started is not None:
                text='UNKNOWN：'+getattr(self,'connection_note','等待图像')+'；不发送旧提示'
            if result:
                age=time.monotonic()-item['received']
                if age>MAX_AGE_S:
                    text+=' | UNKNOWN：结果过期，提示已停止'
                    if image is not None:
                        image=image.copy();image[480:]=0
                        cv2.putText(image,'UNKNOWN - STALE',(12,520),cv2.FONT_HERSHEY_SIMPLEX,.8,(230,230,230),2)
                else:
                    names={'LEFT':'左侧障碍','CENTER':'前方障碍','RIGHT':'右侧障碍',None:''}
                    text+=' | '+result['status']+' '+names[result['direction']]+' | 接收后年龄 %.0fms'%(age*1000)
                    corridor=result.get('corridor',{})
                    if corridor:text+=' | '+{'ADAPTIVE':'地面通道估计','FIXED_FALLBACK':'固定区域降级','UNKNOWN':'通道未知'}.get(corridor.get('source'),'')
                    if result.get('confirmed_regions'):text+=' | 已确认区域：'+','.join(result['confirmed_regions'])
                    if result['status']=='OBSTACLE':
                        text+=' | 当前输出：'+names[result['direction']]
                        if result['direction']!='CENTER':text+='（通道内暂无确认候选）'
            if getattr(self,'feedback_failure',''):text+=' | '+self.feedback_failure
            self.status.set(text)
            if image is not None:
                self.photo=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)))
                self.image_label.configure(image=self.photo)
            else:
                self.image_label.configure(image='');self.photo=None
        idle=(not self.worker or not self.worker.is_alive()) and (not self.audition_worker or not self.audition_worker.is_alive())
        if idle:
            self.start_button.configure(text=('开始持续' if self.seconds==0 else '开始%d秒'%self.seconds)+('闭环' if self.voice.get() and self.motor.get() else '预览'))
            self.start_button.configure(state='normal');self.haptic_checkbox.configure(state='normal')
            self.duration_control.configure(state='readonly')
            self.voice_checkbox.configure(state='normal');self.audition_button.configure(state='normal')
            self.volume_control.configure(state='readonly');self.decision_control.configure(state='readonly')
        else:
            if self.worker and self.worker.is_alive():
                if self.run_started is None:caption='正在加载……'
                elif self.seconds==0:
                    elapsed=max(0,int(time.monotonic()-self.run_started));caption='持续运行 %d分%02d秒'%(elapsed//60,elapsed%60)
                else:caption='运行中 还剩%d秒'%max(0,math.ceil(self.run_deadline-time.monotonic()))
                self.start_button.configure(text=caption)
            self.start_button.configure(state='disabled');self.audition_button.configure(state='disabled');self.volume_control.configure(state='disabled');self.decision_control.configure(state='disabled')
        self.root.after(40,self.refresh)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://192.168.137.44')
    parser.add_argument('--backend',choices=['x5','pc'],default='x5')
    parser.add_argument('--seconds',type=int,default=0,help='0: continuous until Stop; 1..600: bounded check');parser.add_argument('--start',action='store_true')
    parser.add_argument('--haptics',action='store_true');parser.add_argument('--max-pulses',type=int,default=0)
    parser.add_argument('--max-utterances',type=int,default=0)
    parser.add_argument('--pulse-ms',type=int,choices=[100,300,500],default=500)
    parser.add_argument('--no-speech',action='store_true')
    parser.add_argument('--speech-volume',type=int,choices=[25,40,55,70],default=55)
    parser.add_argument('--decision',choices=['legacy','adaptive'],default=DEFAULT_DECISION_MODE)
    parser.add_argument('--esp-via-x5',action='store_true',help='Discover ESP by MAC on X5 WLAN; use loopback-only SSH links')
    parser.add_argument('--password-stdin',action='store_true');args=parser.parse_args()
    if not 0<=args.seconds<=600:parser.error('seconds must be 0 (continuous) or 1..600')
    if not 0<=args.max_pulses<=100:parser.error('max-pulses must be 0..100; 0 means no count limit')
    if not 0<=args.max_utterances<=100:parser.error('max-utterances must be 0..100; 0 means no count limit')
    password=input() if args.password_stdin else None
    root=tk.Tk();app=Demo(root,args.url,'X5 BPU' if args.backend=='x5' else 'PC ONNX',args.seconds,args.pulse_ms)
    app.motor.set(args.haptics);app.max_pulses=args.max_pulses
    app.max_utterances=args.max_utterances
    app.voice.set(not args.no_speech)
    app.speech_volume.set(str(args.speech_volume))
    app.decision.set('通道优先' if args.decision=='adaptive' else '旧版对照')
    app.ssh_password=password
    app.esp_via_x5=args.esp_via_x5
    if args.haptics and not args.no_speech:
        app.status.set('闭环已就绪：点击开始后才运行识别、语音和短振；可随时停止。')
    if args.esp_via_x5:app.status.set('等待开始：将经RDK按MAC查找ESP，无需切换热点；请选择X5 BPU。')
    if args.start:root.after(300,lambda:app.start(password))
    root.mainloop()

if __name__=='__main__':main()
