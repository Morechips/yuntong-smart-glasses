"""Loopback software integration, explicitly not an ESP/X5 physical test."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import io,json,threading,time,unittest
from live_demo import Demo,PulseOutput,HERE

class IntegrationTests(unittest.TestCase):
    def test_real_jpeg_model_direction_and_disconnect_without_haptics(self):
        self._check_stream('legacy')
    def test_adaptive_real_jpeg_model_direction_and_disconnect_without_haptics(self):
        self._check_stream('adaptive')
    def _check_stream(self,decision_mode):
        root=HERE.parents[1]
        manifest=json.loads((HERE.parent/'ground_route_screen_20261031/runs/round01/data/manifest.json').read_text(encoding='utf-8'))
        row=next(r for r in manifest['samples'] if r['sample_id']=='A02_02')
        jpeg=(HERE.parent/'ground_route_screen_20261031/runs/round01/data'/row['file']).read_bytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=demo');self.end_headers()
                for index in range(12):
                    headers=(f'--demo\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Device-Boot-Id: loopback-test\r\nX-Stream-Id: 1\r\nX-Frame-Seq: {index+1}\r\n\r\n').encode()
                    self.wfile.write(headers+jpeg+b'\r\n');self.wfile.flush();time.sleep(.1)
                self.wfile.write(b'--demo--\r\n');self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        before=set((HERE/'runs').glob('live_*'))
        demo=Demo.__new__(Demo);demo.run_decision_mode=decision_mode;demo.display_lock=threading.Lock();demo.current=None;demo.stop=threading.Event()
        try:demo.run('http://127.0.0.1:'+str(server.server_port),'PC ONNX',False,None)
        finally:server.shutdown();server.server_close();thread.join(2)
        out=(set((HERE/'runs').glob('live_*'))-before).pop()
        report=json.loads((out/'summary.json').read_text(encoding='utf-8'))
        events=[json.loads(s) for s in (out/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        results=[e['result'] for e in events if e['type']=='result']
        self.assertTrue(any(r['status']=='OBSTACLE' and r['direction']=='CENTER' for r in results))
        self.assertEqual(report['decision_mode'],decision_mode)
        self.assertEqual(report['haptic_acknowledgments'],0)
        self.assertIsNotNone(report['error'])
        self.assertTrue(demo.stop.is_set());self.assertIsNone(demo.current['image'])
        (out/'TEST_ONLY.txt').write_text('Loopback simulated ESP transport with repeated development JPEG. No physical ESP/X5 or motors.\n',encoding='utf-8')
    def test_expired_result_never_sends_pulse(self):
        calls=[];stop=threading.Event();pulse=PulseOutput('http://127.0.0.1:1',True,stop,calls.append)
        class NoNetwork:
            def open(self,*args,**kwargs):calls.append('unexpected request');raise AssertionError('Stale pulse')
        pulse.opener=NoNetwork()
        pulse.update(dict(status='OBSTACLE',direction='CENTER'),time.monotonic()-1)
        time.sleep(.15);stop.set();pulse.thread.join(1)
        self.assertEqual(calls,[])
    def test_pulse_budget_stops_after_one_ack(self):
        calls=[];events=[];stop=threading.Event()
        pulse=PulseOutput('http://127.0.0.1:1',True,stop,events.append,max_pulses=1)
        class FakeAck:
            def open(self,request,timeout):
                calls.append(request.full_url)
                return io.BytesIO(b'{"ok":true,"final_level":0}')
        pulse.opener=FakeAck();pulse.update(dict(status='OBSTACLE',direction='CENTER'),time.monotonic())
        pulse.thread.join(.5);stop.set()
        self.assertFalse(pulse.thread.is_alive());self.assertEqual(len(calls),1)
        self.assertFalse(pulse.enabled)
        self.assertTrue(any(e['type']=='haptic_limit_reached' for e in events))
    def test_500ms_pulse_uses_longer_timeout_and_selected_duration(self):
        calls=[];events=[];stop=threading.Event()
        pulse=PulseOutput('http://127.0.0.1:1',True,stop,events.append,max_pulses=1,pulse_ms=500)
        class FakeAck:
            def open(self,request,timeout):
                calls.append((request.full_url,timeout))
                return io.BytesIO(b'{"ok":true,"final_level":0}')
        pulse.opener=FakeAck();pulse.update(dict(status='OBSTACLE',direction='LEFT'),time.monotonic())
        pulse.thread.join(.5);stop.set()
        self.assertEqual(len(calls),1);self.assertIn('ms=500',calls[0][0]);self.assertGreater(calls[0][1],.5)
        self.assertEqual(events[0]['pulse_ms'],500)

if __name__=='__main__':unittest.main()
