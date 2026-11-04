"""Independent joint viewer; continuous until manual Stop by default."""
import argparse
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from PIL import Image, ImageTk
import cv2
import live_joint
from haptic_profiles import PROFILE_LABELS,LEVEL_LABELS


def path_level_text(path):
    if not path or path.get('status')=='UNKNOWN':return '未知'
    if path.get('direction')=='CENTER':return '居中'
    if path.get('status')!='CONFIRMED':return '待确认'
    level=path.get('deviation_level')
    return LEVEL_LABELS[level] if level in ('LOW','MEDIUM','HIGH') else '未知'


class JointApp:
    def __init__(self, root, password=None, initial_options=None):
        self.root = root
        self.password = password
        self.worker = None
        self.stop = threading.Event()
        self.finished = queue.Queue()
        self.frame_lock = threading.Lock()
        self.frame = None
        self.closing = False
        self.photo = None
        root.title('云瞳 · 双模型持续联调（A/B震动）')
        root.geometry('1220x790')
        root.minsize(900, 640)
        initial_options=initial_options or {}
        self.speech = tk.BooleanVar(value=initial_options.get('speech',False))
        self.haptics = tk.BooleanVar(value=initial_options.get('haptics',False))
        self.fixed = tk.BooleanVar(value=initial_options.get('camera_fixed',False))
        self.mapping = tk.BooleanVar(value=initial_options.get('mapping_confirmed',False))
        self.duration = tk.StringVar(value='持续')
        self.run_seconds = 0
        self.haptic_profile=tk.StringVar(value=PROFILE_LABELS[initial_options.get('haptic_profile','cadence')])
        self.run_profile='cadence'
        self.url = tk.StringVar(value='')
        self.state = tk.StringVar(value='等待手动开始；当前无声振')
        self.floor_text = tk.StringVar(value='障碍：UNKNOWN')
        self.path_text = tk.StringVar(value='盲道：UNKNOWN')
        body = ttk.Frame(root, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='障碍语音＋盲道纠偏 · 持续运行至停止', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(body, text='左图：地面与障碍；右图：盲道自己的源帧。当前只估图像偏位，需固定朝前；不提供转弯指令。').pack(anchor='w', pady=(5, 8))
        settings = ttk.Frame(body)
        settings.pack(fill='x')
        self.controls = []
        for index, (text, var) in enumerate([('障碍语音 55', self.speech), ('盲道纠偏震动', self.haptics),
                                            ('镜头已固定朝前', self.fixed), ('佩戴左右已核对', self.mapping)]):
            control = ttk.Checkbutton(settings, text=text, variable=var)
            control.grid(row=index // 2, column=index % 2, sticky='w', padx=(0, 25), pady=2)
            self.controls.append(control)
        profiles=ttk.Frame(body);profiles.pack(fill='x',pady=(8,0))
        ttk.Label(profiles,text='震动模式').pack(side='left')
        profile_box=ttk.Combobox(profiles,textvariable=self.haptic_profile,values=tuple(PROFILE_LABELS.values()),width=32,state='readonly')
        profile_box.pack(side='left',padx=6);self.controls.append(profile_box)
        ttk.Label(profiles,text='开始前选择；停止后切换').pack(side='left')
        connection = ttk.Frame(body)
        connection.pack(fill='x', pady=8)
        self.stop_button = ttk.Button(connection, text='停止', command=self.stop_run, state='disabled')
        self.stop_button.pack(side='right')
        self.start_button = ttk.Button(connection, text='开始联调', command=self.start)
        self.start_button.pack(side='right', padx=(12, 5))
        ttk.Label(connection, text='运行时长').pack(side='left')
        duration = ttk.Combobox(connection, textvariable=self.duration, values=('持续', '60', '120'), width=5, state='readonly')
        duration.pack(side='left', padx=5)
        self.controls.append(duration)
        ttk.Label(connection, text='ESP地址（空白自动发现）').pack(side='left', padx=(14, 5))
        address = ttk.Entry(connection, textvariable=self.url, width=29)
        address.pack(side='left', fill='x', expand=True)
        self.controls.append(address)
        ttk.Label(body, textvariable=self.state, wraplength=1160).pack(anchor='w')
        ttk.Label(body, textvariable=self.floor_text).pack(anchor='w', pady=(5, 0))
        ttk.Label(body, textvariable=self.path_text).pack(anchor='w', pady=(2, 8))
        self.picture = tk.Label(body, background='#171d24', foreground='#cbd5df', text='开始后显示实时结果；不会自动播放或震动')
        self.picture.pack(fill='both', expand=True)
        ttk.Label(body, text='盲道居中不振；障碍／待确认／UNKNOWN时暂停纠偏。语音与震动串行；停止后已开始的一句会播完。').pack(anchor='w', pady=(8, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(80, self.refresh)

    def selected_profile(self):
        return next(key for key,label in PROFILE_LABELS.items() if label==self.haptic_profile.get())

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.run_seconds=0 if self.duration.get()=='持续' else int(self.duration.get())
        self.run_profile=self.selected_profile()
        argv = ['--seconds', str(self.run_seconds),'--max-pulses','0','--max-utterances','0','--haptic-profile',self.run_profile]
        for flag, var in [('--speech', self.speech), ('--haptics', self.haptics),
                          ('--camera-fixed', self.fixed), ('--mapping-confirmed', self.mapping)]:
            if var.get():
                argv.append(flag)
        if self.haptics.get() and (not self.fixed.get() or not self.mapping.get()):
            messagebox.showinfo('尚缺确认', '启用盲道震动前，请固定镜头朝前并实际核对佩戴左右。', parent=self.root)
            return
        if self.url.get().strip():
            argv += ['--url', self.url.get().strip()]
        try:
            args = live_joint.parse_args(argv)
        except SystemExit:
            messagebox.showerror('参数无效', '请核对时长和地址。', parent=self.root)
            return
        if self.password is None:
            self.password = simpledialog.askstring('X5连接', '输入X5 SSH密码（仅存内存）', show='*', parent=self.root)
        if not self.password:
            self.password = None
            return
        self.stop = threading.Event()
        with self.frame_lock:
            self.frame = None
        self.photo = None
        self.picture.configure(image='', text='正在核验系统、加载两份模型和查找ESP；此时不发声振')
        self.set_running(True)
        self.state.set('启动中；可点击停止。语音／震动不设次数上限。')
        self.floor_text.set('障碍：UNKNOWN')
        self.path_text.set('盲道：UNKNOWN')
        password = self.password

        def run():
            try:
                summary, out = live_joint.run_joint(args, password, self.stop, self.receive_frame)
                self.finished.put((summary, str(out)))
            except Exception as exc:
                self.finished.put(({'error': repr(exc)}, None))
        self.worker = threading.Thread(target=run, daemon=False)
        self.worker.start()

    def receive_frame(self, image, metadata):
        # The viewer retains one result, never a growing image queue.
        with self.frame_lock:
            self.frame = (image, metadata)

    def set_running(self, running):
        for control in self.controls:
            control.configure(state='disabled' if running else ('readonly' if isinstance(control, ttk.Combobox) else 'normal'))
        self.start_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')

    def stop_run(self):
        self.stop.set()
        self.stop_button.configure(state='disabled')
        self.state.set('正在停止并清理；已开始的语音会播完，不追加提示。')

    def close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.stop_run()
        else:
            self.password = None
            self.root.destroy()

    def refresh(self):
        active = self.worker is not None and self.worker.is_alive()
        try:
            summary, out = self.finished.get_nowait()
            self.set_running(False)
            with self.frame_lock:
                self.frame = None
            self.photo = None
            self.picture.configure(image='', text='检查已结束；预览已清除')
            self.floor_text.set('障碍：UNKNOWN（已停止）')
            self.path_text.set('盲道：UNKNOWN（已停止）')
            error = summary.get('error') or summary.get('tactile_error')
            self.state.set(('联调中断：' + str(error) if error else '已停止，无后续声振。') + (' 记录：' + out if out else ''))
            active = False
        except queue.Empty:
            pass
        if self.closing and not active:
            self.password = None
            self.root.destroy()
            return
        if active and not self.stop.is_set():
            with self.frame_lock:
                frame = self.frame
            if frame:
                image, metadata = frame
                now = time.monotonic()
                if now - metadata['floor_received'] > .5:
                    self.photo = None
                    self.picture.configure(image='', text='UNKNOWN · 图像／推理结果已过期，等待新结果')
                    self.floor_text.set('障碍：UNKNOWN（超过0.5秒）')
                    self.path_text.set('盲道：UNKNOWN（暂停纠偏）')
                else:
                    floor = metadata['floor']
                    self.floor_text.set('障碍：' + floor['status'] + '  主要方位：' + str(floor.get('direction')))
                    path = metadata.get('path')
                    path_fresh = metadata.get('path_received') is not None and now - metadata['path_received'] <= .5
                    self.path_text.set('盲道：' + ((path['status'] + '  图像偏位：' + str(path.get('direction'))+'  偏离：'+path_level_text(path)) if path and path_fresh else 'UNKNOWN（未识别／过期）'))
                    shown = image
                    if not path_fresh:
                        shown = image.copy()
                        shown[:, shown.shape[1] // 2:] = (23, 29, 36)
                        cv2.putText(shown, 'TACTILE UNKNOWN / expired', (shown.shape[1] // 2 + 10, 60), cv2.FONT_HERSHEY_SIMPLEX, .6, (230, 230, 230), 1)
                    available = (max(300, self.picture.winfo_width()), max(180, self.picture.winfo_height()))
                    pil = Image.fromarray(cv2.cvtColor(shown, cv2.COLOR_BGR2RGB))
                    pil.thumbnail(available)
                    self.photo = ImageTk.PhotoImage(pil)
                    self.picture.configure(image=self.photo, text='')
                    elapsed=int(metadata['elapsed_s'])
                    duration_text=('持续运行 · 已运行%02d:%02d'%(elapsed//60,elapsed%60)) if self.run_seconds==0 else '运行中 · 剩余约%d秒'%max(0,self.run_seconds-elapsed)
                    self.state.set(duration_text+' · 障碍语音%s／盲道震动%s · %s · 提示不设次数上限'%
                                   ('开' if self.speech.get() else '关', '开' if self.haptics.get() else '关',PROFILE_LABELS[self.run_profile]))
        self.root.after(80, self.refresh)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--speech',action='store_true')
    parser.add_argument('--haptics',action='store_true')
    parser.add_argument('--camera-fixed',action='store_true')
    parser.add_argument('--mapping-confirmed',action='store_true')
    parser.add_argument('--haptic-profile',choices=tuple(PROFILE_LABELS),default='cadence')
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else None
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    JointApp(root, password,vars(args))
    root.mainloop()


if __name__ == '__main__':
    main()
