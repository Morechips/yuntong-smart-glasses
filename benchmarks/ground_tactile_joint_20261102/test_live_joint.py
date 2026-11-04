"""Full candidate controller with loopback JPEG and stub models, no hardware."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path
import json,threading,time,unittest
import numpy as np
import paths
import live_joint


class LiveTests(unittest.TestCase):
    def test_shared_stream_reconnect_and_cleanup_never_actuate(self):
        manifest=json.loads((paths.BASE/'runs/compile_01/inputs.json').read_text(encoding='utf-8'))
        jpeg=(paths.ROOT/manifest['samples'][0]['source']).read_bytes();connections=[];done=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                connections.append(1);self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=demo');self.end_headers()
                try:
                    for index in range(25):
                        if done.is_set():return
                        self.wfile.write((f'--demo\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\nX-Device-Boot-Id: test-only\r\nX-Stream-Id: same\r\nX-Frame-Source-Us: {int(time.monotonic()*1000000)}\r\nX-Frame-Time-Status: ok\r\n\r\n').encode()+jpeg+b'\r\n');self.wfile.flush();time.sleep(.05)
                    self.wfile.write(b'--demo--\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        class Floor:
            name='TEST ONLY floor stub'
            def __init__(self,*a):self.metadata={'test_only':True};self.cleanup=None
            def infer(self,jpeg,index):return np.ones((240,320),np.uint8),{'model_readback_ms':0}
            def close(self):self.cleanup={'code':0}
        class Tactile(Floor):
            def infer(self,jpeg,index):
                mask=np.zeros((240,320),np.uint8);mask[125:235,60:80]=1
                return mask,dict(test_only=True)
        before=set((paths.HERE/'runs').glob('live_*'));views=[];run_stop=threading.Event();timer=threading.Timer(10,run_stop.set)
        def clock(*args):
            now=time.monotonic()
            return dict(t0=now,t1=now+.001,esp_uptime_s=now,boot='test-only')
        try:
            timer.start()
            with patch('sys.argv',['live_joint','--seconds','0','--url','http://127.0.0.1:'+str(server.server_port),'--trace-transport']),patch('builtins.input',return_value='test'),patch.object(live_joint,'trace_clock',clock),patch.object(live_joint,'FloorBackend',Floor),patch.object(live_joint,'TactileBackend',Tactile),patch.object(live_joint,'verify_backend',return_value={'min_pixel_agreement':1,'candidate_contract_equal':True}):
                live_joint.run_joint(live_joint.parse_args(),'test',stop=run_stop,on_frame=lambda image,meta:views.append((image.shape,meta)))
        finally:timer.cancel();done.set();server.shutdown();server.server_close();thread.join(2)
        out=(set((paths.HERE/'runs').glob('live_*'))-before).pop()
        (out/'TEST_ONLY.txt').write_text('Loopback JPEG + stub models; not physical board or perception evidence.',encoding='utf-8')
        summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
        self.assertIsNone(summary['error']);self.assertGreater(summary['results'],40);self.assertGreaterEqual(summary['camera_reconnects'],1)
        self.assertTrue(summary['continuous']);self.assertEqual(summary['seconds'],0);self.assertEqual(summary['max_pulses'],0);self.assertEqual(summary['max_utterances'],0)
        self.assertEqual(summary['speech_acknowledgments'],0);self.assertEqual(summary['haptic_acknowledgments'],0)
        self.assertEqual(summary['floor_cleanup']['code'],0);self.assertEqual(summary['tactile_cleanup']['code'],0)
        rows=[json.loads(s) for s in (out/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        paths_seen=[r for r in rows if r['type']=='path']
        first_new=next(r for r in paths_seen if r['headers']['x-demo-connection']=='2')
        self.assertEqual(first_new['path']['status'],'PENDING')
        self.assertTrue(summary['path_states'].get('CONFIRMED',0)>0)
        self.assertTrue(summary['viewer_enabled']);self.assertGreater(len(views),10)
        self.assertIsNone(summary['transport_trace_error']);self.assertEqual(summary['transport_trace']['frames_total'],summary['camera_received'])
        self.assertTrue((out/'transport_trace.json').is_file());self.assertGreater(summary['path_positive_results'],0)
        self.assertTrue(all(meta['floor_received'] is not None and shape[1]==1280 for shape,meta in views))

    def test_stop_before_start_loads_no_model(self):
        stop=threading.Event();stop.set()
        with patch.object(live_joint,'FloorBackend') as load:
            summary,out=live_joint.run_joint(live_joint.parse_args(['--seconds','10']),'test',stop)
        (out/'TEST_ONLY.txt').write_text('Stop-before-start software check; no hardware.',encoding='utf-8')
        load.assert_not_called();self.assertIsNone(summary['error']);self.assertEqual(summary['results'],0)


if __name__=='__main__':unittest.main()
