"""Whole demo: original JPEG -> model -> decision -> two LOOPBACK feedback APIs."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import hashlib,json,threading,time,unittest,urllib.request
from live_demo import Demo,HERE
from speech_output import load_clips


class ClosedLoopTests(unittest.TestCase):
    def test_model_decision_speech_and_motor_are_causally_connected(self):
        manifest_path=HERE.parent/'ground_route_screen_20261031/runs/round01/data/manifest.json'
        sample=next(r for r in json.loads(manifest_path.read_text(encoding='utf-8'))['samples'] if r['sample_id']=='A02_02')
        jpeg=(manifest_path.parent/sample['file']).read_bytes()
        calls=[];state_lock=threading.Lock();active=0;peak=0
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,data):
                body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if self.path=='/state':
                    self.reply(dict(motor_enabled=True,motor_cycle_enabled=False,speaker_volume_query=True,speaker_volume_max=70));return
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=demo');self.end_headers()
                try:
                    for i in range(15):
                        header=(f'--demo\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Device-Boot-Id: loopback-closed-loop\r\nX-Stream-Id: 1\r\nX-Frame-Seq: {i+1}\r\n\r\n').encode()
                        self.wfile.write(header+jpeg+b'\r\n');self.wfile.flush();time.sleep(.1)
                    self.wfile.write(b'--demo--\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            def do_POST(self):
                nonlocal active,peak
                data=self.rfile.read(int(self.headers.get('Content-Length',0)));parsed=urlsplit(self.path);query=parse_qs(parsed.query)
                with state_lock:
                    active+=1;peak=max(peak,active);calls.append(dict(path=parsed.path,query=query,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
                time.sleep(.04)
                if parsed.path=='/pcm':reply=dict(ok=True,codec_result=0,pcm_bytes=len(data),volume=int(query['volume'][0]))
                else:reply=dict(ok=True,final_level=0,channel=query['channel'][0],pulse_ms=int(query['ms'][0]))
                with state_lock:active-=1
                self.reply(reply)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        real_factory=urllib.request.build_opener
        # Every peripheral URL stays local, redirected to this test's random port.
        class Router:
            def __init__(self,*args):self.actual=real_factory(*args)
            def open(self,request,timeout):
                url=request if isinstance(request,str) else request.full_url
                if urlsplit(url).port==8081:
                    routed=url.replace('127.0.0.1:8081','127.0.0.1:'+str(server.server_port))
                    if isinstance(request,str):request=routed
                    else:request=urllib.request.Request(routed,data=request.data,headers=dict(request.header_items()),method=request.get_method())
                return self.actual.open(request,timeout=timeout)
        before=set((HERE/'runs').glob('live_*'))
        demo=Demo.__new__(Demo);demo.display_lock=threading.Lock();demo.current=None;demo.stop=threading.Event()
        demo.run_decision_mode='adaptive';demo.run_speech_volume=55;demo.run_pulse_ms=500;demo.seconds=3
        demo.max_pulses=1;demo.max_utterances=1
        try:
            with patch('urllib.request.build_opener',side_effect=lambda *args:Router(*args)):
                demo.run('http://127.0.0.1:'+str(server.server_port),'PC ONNX',True,None,True)
        finally:server.shutdown();server.server_close();thread.join(2)
        out=(set((HERE/'runs').glob('live_*'))-before).pop()
        (out/'TEST_ONLY.txt').write_text('Whole-loop LOCAL fake ESP transport/actuators with real PC ONNX JPEG inference; no hardware commands.\n',encoding='utf-8')
        report=json.loads((out/'summary.json').read_text(encoding='utf-8'))
        events=[json.loads(s) for s in (out/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        results={e['result']['request_id']:e['result'] for e in events if e['type']=='result'}
        self.assertTrue(report['both_feedback_acknowledged']);self.assertEqual(report['haptic_acknowledgments'],1);self.assertEqual(report['speech_acknowledgments'],1)
        self.assertEqual(peak,1);self.assertEqual(len(calls),2)
        pcm=next(c for c in calls if c['path']=='/pcm');motor=next(c for c in calls if c['path']=='/motor')
        self.assertEqual(pcm['query'],dict(volume=['55']));self.assertEqual(pcm['sha256'],hashlib.sha256(load_clips()['CENTER']['pcm']).hexdigest())
        self.assertEqual(motor['query'],dict(channel=['both'],ms=['500']))
        for event in events:
            if event['type'] in ['speech','haptic']:
                self.assertEqual(results[event['result_request_id']]['status'],'OBSTACLE')
                self.assertEqual(results[event['result_request_id']]['direction'],'CENTER')
                self.assertLessEqual(event['cue_started_age_ms'],500)
        self.assertIsNotNone(report['error']) # deliberate multipart end -> stop, not fallback
        self.assertTrue(demo.stop.is_set());self.assertIsNone(demo.current['image'])

if __name__=='__main__':unittest.main()
