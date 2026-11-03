"""Reconnect the SAME camera address, never hold or replay old images."""
import threading,time
from live_stream import LiveStream


class ResilientStream:
    def __init__(self,url,on_state,delays=(1,2,4,5),source_factory=LiveStream):
        self.url=url;self.on_state=on_state;self.delays=delays;self.source_factory=source_factory
        self.stop_event=threading.Event();self.done=threading.Event();self.thread=None
        self.callback=None;self.received=0;self.error='';self.generation=0;self.active_generation=None
        self.current=None;self.reconnects=0;self.lock=threading.Lock()
    def subscribe(self,callback):self.callback=callback
    def start(self):
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set();self.active_generation=None
        if self.current:self.current.stop()
    def is_current(self,generation):return not self.stop_event.is_set() and self.active_generation==generation
    def _run(self):
        attempt=0
        try:
            while not self.stop_event.is_set():
                self.generation+=1;generation=str(self.generation);source=self.source_factory(self.url)
                self.current=source;last=[time.monotonic()];connected=[False]
                self.on_state('connecting',dict(generation=generation,reconnects=self.reconnects))
                def arrived(jpeg,headers,received):
                    if self.stop_event.is_set():return
                    last[0]=time.monotonic();self.received+=1
                    if not connected[0]:
                        connected[0]=True;self.active_generation=generation
                        self.on_state('connected',dict(generation=generation,reconnects=self.reconnects))
                    headers=dict(headers);headers['x-demo-connection']=generation
                    if self.callback:self.callback(jpeg,headers,received)
                source.subscribe(arrived);source.start()
                while not self.stop_event.wait(.05):
                    if source.done.is_set() or time.monotonic()-last[0]>3:break
                self.active_generation=None;source.stop()
                if source.thread:source.thread.join(3)
                if self.stop_event.is_set():break
                # Do not start another source if the previous socket thread did
                # not exit: no orphan readers or overlapping callbacks.
                if source.thread and source.thread.is_alive():raise RuntimeError('摄像头读取线程未退出，请停止后重开')
                self.error=source.error or '3秒未收到完整图像'
                self.reconnects+=1
                self.on_state('disconnected',dict(reason=self.error,reconnects=self.reconnects))
                attempt=0 if connected[0] else attempt
                if self.stop_event.wait(self.delays[min(attempt,len(self.delays)-1)]):break
                attempt+=1
        except Exception as exc:self.error=str(exc)
        finally:
            self.active_generation=None
            if self.current:
                self.current.stop()
                if self.current.thread:self.current.thread.join(3)
            self.done.set()
