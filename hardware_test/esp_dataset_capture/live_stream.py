"""One MJPEG connection shared by unsaved preview and an optional recorder."""
from collections import deque
import threading
import time
import urllib.request
from recorder import MultipartJpeg, stream_url

class LiveStream:
    def __init__(self,url,timeout=2.):
        self.url=stream_url(url); self.timeout=timeout; self.lock=threading.Lock()
        self.stop_event=threading.Event(); self.done=threading.Event(); self.thread=None
        self.latest=None; self.received=0; self.error=''; self.times=deque(maxlen=2400)
        self.started=None; self.callback=None; self.http_headers={}

    def start(self):
        if self.thread is not None:raise RuntimeError('Stream already started')
        self.started=time.monotonic()
        self.thread=threading.Thread(target=self._run,name='esp-preview-stream',daemon=True);self.thread.start()

    def stop(self):self.stop_event.set()

    def subscribe(self,callback):
        with self.lock:
            if self.callback is not None:raise RuntimeError('Another recorder already uses this stream')
            if self.done.is_set():raise RuntimeError(self.error or 'Stream has ended')
            self.callback=callback

    def unsubscribe(self,callback):
        with self.lock:
            if self.callback==callback:self.callback=None

    def snapshot(self):
        with self.lock:
            now=time.monotonic(); recent=sum(t>=now-10 for t in self.times)
            return dict(received=self.received,fps=recent/max(.001,min(10,now-(self.started or now))),
                        error=self.error,done=self.done.is_set())

    def preview(self):
        with self.lock:return self.latest

    def _run(self):
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.url,timeout=self.timeout) as response:
                if response.status!=200:raise ValueError(f'HTTP {response.status}')
                self.http_headers=dict(response.headers.items())
                parser=MultipartJpeg(response.headers.get('Content-Type',''))
                while not self.stop_event.is_set():
                    chunk=response.read1(65536)
                    if not chunk:raise ConnectionError('摄像头断流，请重新连接预览')
                    for jpeg,headers in parser.feed(chunk):
                        if self.stop_event.is_set():break
                        now=time.monotonic()
                        # Holding the source lock makes detach a barrier: no late frame
                        # can enter a recorder after its index writer is finished.
                        with self.lock:
                            self.latest=(self.received,jpeg);self.received+=1;self.times.append(now)
                            if self.callback:self.callback(jpeg,headers,now)
                    if parser.terminated:raise ConnectionError('摄像头结束图像流')
        except Exception as exc:
            if not self.stop_event.is_set():self.error=str(exc)
        finally:self.done.set()
