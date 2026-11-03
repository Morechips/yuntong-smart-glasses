"""Small desktop controller for native ESP camera dataset collection."""
import io
import os
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from PIL import Image, ImageTk
from recorder import Recorder, ROOT, stream_url
from live_stream import LiveStream

SCENES=[('empty_static','空地静止'),('obstacle_static','障碍静止'),('small_yaw','轻微左右转头'),
        ('small_pitch','轻微抬头低头'),('turn_away_return','转开后回到障碍'),
        ('slow_walk','慢走经过静物'),('object_enter_remove','物体进入与移走')]

class App:
    def __init__(self, root):
        self.root=root; self.session=None; self.live=None; self.last_preview=None; self.photo=None; self.closing=False
        self.session_handled=True;self.saved_message=''
        root.title('云瞳 · 实时预览与场景采集'); root.minsize(850,660)
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.url=tk.StringVar(value=''); self.site=tk.StringVar(value=''); self.batch=tk.StringVar(value=datetime.now().strftime('%Y%m%d_批次1'))
        self.scene=tk.StringVar(value=SCENES[0][1]); self.seconds=tk.StringVar(value='45'); self.notes=tk.StringVar()
        self.output=tk.StringVar(value=str(ROOT/'datasets/esp_capture')); self.status=tk.StringVar(value='填入当前微雪板IP，点击连接预览。填写地点后可开始保存。')
        self.info=tk.StringVar(value='原始JPEG直接保存；预览与保存独立。时间默认是电脑接收时间。')
        box=ttk.Frame(root,padding=12); box.pack(fill='both',expand=True)
        self.fields=[]
        def field(row,label,var,width=50):
            ttk.Label(box,text=label).grid(row=row,column=0,sticky='w',pady=3)
            entry=ttk.Entry(box,textvariable=var,width=width); entry.grid(row=row,column=1,columnspan=2,sticky='ew',padx=8); self.fields.append(entry)
        field(0,'微雪板IP / 流地址',self.url)
        field(1,'地点（例如实验室走廊）',self.site)
        field(2,'拍摄批次',self.batch)
        ttk.Label(box,text='场景').grid(row=3,column=0,sticky='w')
        self.scene_box=ttk.Combobox(box,textvariable=self.scene,values=[x[1] for x in SCENES],state='readonly')
        self.scene_box.grid(row=3,column=1,sticky='ew',padx=8); self.fields.append(self.scene_box)
        self.duration=ttk.Combobox(box,textvariable=self.seconds,values=['30','45','60'],width=5,state='readonly')
        self.duration.grid(row=3,column=2,sticky='w'); self.fields.append(self.duration)
        field(4,'备注（高度/方向/光照）',self.notes)
        field(5,'保存目录',self.output)
        ttk.Button(box,text='选择目录',command=self.choose_directory).grid(row=5,column=3,padx=4)
        buttons=ttk.Frame(box); buttons.grid(row=6,column=0,columnspan=4,sticky='w',pady=10)
        self.preview_button=ttk.Button(buttons,text='连接预览',command=self.connect_preview); self.preview_button.pack(side='left',padx=4)
        self.start_button=ttk.Button(buttons,text='开始采集',command=self.start); self.start_button.pack(side='left',padx=4)
        self.stop_button=ttk.Button(buttons,text='停止并保存',command=self.stop,state='disabled'); self.stop_button.pack(side='left',padx=4)
        ttk.Button(buttons,text='标记：障碍进入',command=lambda:self.mark('operator_obstacle_enter')).pack(side='left',padx=4)
        ttk.Button(buttons,text='标记：障碍移走',command=lambda:self.mark('operator_obstacle_removed')).pack(side='left',padx=4)
        ttk.Button(buttons,text='打开保存目录',command=self.open_folder).pack(side='left',padx=4)
        ttk.Label(box,textvariable=self.status,wraplength=840).grid(row=7,column=0,columnspan=4,sticky='w',pady=3)
        ttk.Label(box,textvariable=self.info,wraplength=840).grid(row=8,column=0,columnspan=4,sticky='w',pady=3)
        self.preview=tk.Label(box,text='点击“连接预览”先查看画面；点击“开始采集”才保存',background='#151515',foreground='white',width=80,height=24)
        self.preview.grid(row=9,column=0,columnspan=4,sticky='nsew',pady=6)
        ttk.Label(box,text='建议依次录制空地、静物、左右转头、抬头低头。自动到时保存；断流会结束本段，已收数据保留。',wraplength=840).grid(row=10,column=0,columnspan=4,sticky='w')
        box.columnconfigure(1,weight=1); box.rowconfigure(9,weight=1)
        root.after(100,self.poll)

    def choose_directory(self):
        if self.session and not self.session.done.is_set():return
        value=filedialog.askdirectory(initialdir=self.output.get() if Path(self.output.get()).exists() else str(ROOT))
        if value:self.output.set(value)

    def controls(self, recording):
        for f in self.fields:
            f.configure(state='disabled' if recording else 'readonly' if isinstance(f,ttk.Combobox) else 'normal')
        self.start_button.configure(state='disabled' if recording else 'normal'); self.stop_button.configure(state='normal' if recording else 'disabled')
        self.preview_button.configure(state='disabled' if recording else 'normal')
        if self.live and not self.live.done.is_set():self.fields[0].configure(state='disabled')

    def connect_preview(self):
        if self.session and not self.session.done.is_set():return
        if self.live and not self.live.done.is_set():
            self.live.stop();self.preview_button.configure(state='disabled');self.status.set('正在断开预览…');return
        try:
            self.live=LiveStream(self.url.get());self.live.start();self.last_preview=None
            self.preview_button.configure(text='断开预览');self.fields[0].configure(state='disabled')
            self.status.set('正在连接预览；当前没有保存数据…');self.saved_message=''
        except Exception as exc: self.live=None;messagebox.showerror('无法连接预览',str(exc))

    def start(self):
        if self.session and not self.session.done.is_set():return
        try:
            scene=next(code for code,label in SCENES if label==self.scene.get())
            # Validate labels before touching a running preview connection.
            candidate=Recorder(self.url.get(),self.output.get(),self.site.get(),self.batch.get(),scene,float(self.seconds.get()),self.notes.get())
            if self.live and not self.live.done.is_set() and self.live.url!=candidate.url:raise ValueError('请先断开预览，再连接新地址')
            if self.live is None or self.live.done.is_set():
                self.live=LiveStream(candidate.url);self.live.start();self.last_preview=None
                self.preview_button.configure(text='断开预览')
            candidate.stream=self.live;self.session=candidate
            directory=self.session.start();self.session_handled=False;self.controls(True)
            self.status.set('正在连接并采集：'+str(directory)); self.info.set('等待第一张图像…')
        except Exception as exc: self.session=None; messagebox.showerror('无法开始采集',str(exc))

    def stop(self):
        if self.session and not self.session.done.is_set(): self.session.stop(); self.status.set('正在停止接收并保存剩余帧…')

    def mark(self,label):
        if self.session:self.session.mark(label)

    def open_folder(self):
        folder=self.session.directory if self.session and self.session.directory else Path(self.output.get())
        if folder.exists():os.startfile(str(folder))

    def poll(self):
        recording=self.session is not None and not self.session.done.is_set()
        if self.session:
            s=self.session.snapshot()
            if recording:self.info.set(f"正在保存：接收 {s['received']} 帧 / 已保存 {s['saved']} 帧   接收 {s['fps_10s']:.1f} FPS   丢帧 {s['save_queue_drops']}   剩余 {s['remaining_s']:.0f} 秒")
            if s['done'] and not self.session_handled:
                self.session_handled=True
                self.controls(False)
                message='采集失败/中断：'+s['error'] if s['error'] else '未收到可保存的图像，请检查地址和设备。' if not s['saved'] else '本段已保存，存在保存丢帧，请查看报告。' if s['save_queue_drops'] else '本段已保存。'
                self.saved_message=message+'  目录：'+str(s['directory']);self.status.set(self.saved_message)
        if self.live:
            live=self.live.snapshot();p=self.live.preview()
            if p and p[0]!=self.last_preview:
                try:
                    image=Image.open(io.BytesIO(p[1]));image.load();image.thumbnail((800,450))
                    self.photo=ImageTk.PhotoImage(image);self.preview.configure(image=self.photo,text='',width=0,height=0);self.last_preview=p[0]
                except Exception as exc:self.status.set('预览解码失败：'+str(exc));self.last_preview=p[0]
            if not recording:
                self.info.set(f"仅预览，不保存   接收 {live['fps']:.1f} FPS   已接收 {live['received']} 帧")
                if live['done']:
                    self.status.set(('预览中断：'+live['error']) if live['error'] else '预览已断开。')
                    self.preview_button.configure(text='连接预览',state='normal');self.fields[0].configure(state='normal')
                    self.preview.configure(image='',text='预览已断开，点击“连接预览”重试',width=80,height=24);self.photo=None;self.live=None
                elif live['received']:
                    self.status.set((self.saved_message+'  ' if self.saved_message else '')+'正在预览，点击“开始采集”才会保存。')
        if self.closing:
            if not recording and (self.live is None or self.live.done.is_set()): self.root.destroy();return
        self.root.after(100,self.poll)

    def close(self):
        self.closing=True; self.stop()
        if self.live:self.live.stop()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='ESP native-JPEG capture window')
    parser.add_argument('--url',default='',help='Currently verified camera IP or MJPEG URL')
    args=parser.parse_args()
    root=tk.Tk(); app=App(root)
    if args.url:app.url.set(args.url);root.after(250,app.connect_preview)
    root.mainloop()
