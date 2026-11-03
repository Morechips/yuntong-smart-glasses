from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import json,threading,time,unittest
from live_demo import Demo,HERE
from resilient_stream import ResilientStream
from run_storage import RunStorage


class ContinuousTests(unittest.TestCase):
    def test_storage_is_bounded_and_counts_survive_rotation(self):
        with TemporaryDirectory(dir=HERE/'runs') as directory:
            path=Path(directory);self.assertEqual(path.resolve().parent,(HERE/'runs').resolve())
            storage=RunStorage(path,max_bytes=600,backups=2,window=8)
            for i in range(100):storage.add(dict(type='result',result=dict(age_ms=i,postprocess_ms=1),timing={}))
            self.assertEqual(storage.statistics()['results'],100);self.assertEqual(len(storage.age),8)
            storage.checkpoint(active=True);storage.close()
            self.assertLessEqual(len(list(path.glob('events*.jsonl'))),3)
            self.assertTrue(all(p.stat().st_size<=600 for p in path.glob('events*.jsonl')))
            self.assertEqual(json.loads((path/'current_status.json').read_text())['results'],100)
    def test_reconnect_rejects_old_generation_and_stop_interrupts_wait(self):
        created=[];frames=[];states=[];drop=threading.Event()
        class Source:
            def __init__(self,url):
                self.done=threading.Event();self.error='test disconnect';self.thread=None;self.stop_event=threading.Event();created.append(self)
            def subscribe(self,callback):self.callback=callback
            def start(self):
                def work():
                    self.callback(b'fake',{},time.monotonic())
                    while not self.stop_event.wait(.01):
                        if len(created)==1 and drop.is_set():break
                    self.done.set()
                self.thread=threading.Thread(target=work);self.thread.start()
            def stop(self):self.stop_event.set()
        stream=ResilientStream('fake',lambda state,details:states.append(state),delays=(.03,),source_factory=Source)
        stream.subscribe(lambda jpeg,headers,received:frames.append(headers['x-demo-connection']));stream.start()
        try:
            deadline=time.monotonic()+1
            while not frames and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(stream.is_current('1'));drop.set()
            while len(frames)<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(frames,['1','2']);self.assertFalse(stream.is_current('1'));self.assertTrue(stream.is_current('2'))
        finally:stream.stop();stream.thread.join(2)
        self.assertTrue(stream.done.is_set());self.assertFalse(stream.is_current('2'))
    def test_continuous_real_jpeg_recovers_and_runs_until_manual_stop(self):
        manifest=HERE.parent/'ground_route_screen_20261031/runs/round01/data/manifest.json'
        row=next(r for r in json.loads(manifest.read_text(encoding='utf-8'))['samples'] if r['sample_id']=='A02_02')
        jpeg=(manifest.parent/row['file']).read_bytes();connections=[];server_stop=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                connections.append(1);first=len(connections)==1
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=demo');self.end_headers()
                try:
                    for index in range(4 if first else 80):
                        if server_stop.is_set():return
                        header=(f'--demo\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Device-Boot-Id: same-boot\r\nX-Stream-Id: same-stream\r\n\r\n').encode()
                        self.wfile.write(header+jpeg+b'\r\n');self.wfile.flush();time.sleep(.08)
                    self.wfile.write(b'--demo--\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        demo=Demo.__new__(Demo);demo.seconds=0;demo.run_decision_mode='adaptive';demo.display_lock=threading.Lock();demo.current=None;demo.stop=threading.Event()
        before=set((HERE/'runs').glob('live_*'))
        thread=threading.Thread(target=demo.run,args=('http://127.0.0.1:'+str(server.server_port),'PC ONNX',False,None,False));thread.start()
        try:
            deadline=time.monotonic()+7
            while time.monotonic()<deadline:
                with demo.display_lock:current=demo.current
                if len(connections)>=2 and current and current.get('result',{}).get('status')=='OBSTACLE':break
                time.sleep(.03)
            self.assertTrue(thread.is_alive());self.assertGreaterEqual(len(connections),2)
        finally:
            demo.stop.set();server_stop.set();thread.join(6);server.shutdown();server.server_close();server_thread.join(2)
        self.assertFalse(thread.is_alive())
        out=(set((HERE/'runs').glob('live_*'))-before).pop()
        (out/'TEST_ONLY.txt').write_text('Continuous LOOPBACK real PC model test, transient source loss and manual stop; no physical outputs.\n',encoding='utf-8')
        summary=json.loads((out/'summary.json').read_text(encoding='utf-8'));self.assertIsNone(summary['error'])
        self.assertTrue(summary['continuous']);self.assertGreaterEqual(summary['camera_reconnects'],1)
        rows=[json.loads(s) for s in (out/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        first_new=next(r for r in rows if r['type']=='result' and r['headers']['x-demo-connection']=='2')
        self.assertEqual(first_new['result']['status'],'PENDING')
        self.assertEqual(summary['haptic_acknowledgments'],0);self.assertEqual(summary['speech_acknowledgments'],0)

if __name__=='__main__':unittest.main()
