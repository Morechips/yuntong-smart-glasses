import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from recorder import Recorder, MultipartJpeg, safe_name, stream_url

def jpeg_bytes():
    out=io.BytesIO(); Image.new('RGB',(64,48),(40,90,120)).save(out,format='JPEG'); return out.getvalue()

def part(jpeg,headers=''):
    return b'--test\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n'+headers.encode()+b'\r\n'+jpeg+b'\r\n'

class Server:
    def __init__(self,mode='continuous'):
        self.mode=mode; self.jpeg=jpeg_bytes(); self.requests=[]; owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.requests.append(self.path)
                if self.path=='/stats':
                    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"synthetic":true}');return
                self.send_response(200); self.send_header('Content-Type','multipart/x-mixed-replace; boundary=test'); self.end_headers()
                try:
                    if owner.mode=='malformed':self.wfile.write(b'--test\r\nContent-Type: image/jpeg\r\nContent-Length: 999999999\r\n\r\n');return
                    if owner.mode=='stall':time.sleep(.7);return
                    if owner.mode=='burst':self.wfile.write(b''.join(part(owner.jpeg) for _ in range(180))); self.wfile.flush();return
                    for i in range(80):
                        data=part(owner.jpeg,'X-Frame-Seq: '+str(i)+'\r\n')
                        for start in range(0,len(data),81):self.wfile.write(data[start:start+81])
                        self.wfile.flush();time.sleep(.025)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(2)
    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}/stream'

class Tests(unittest.TestCase):
    def test_parser_fragments_preserve_original(self):
        jpg=jpeg_bytes(); parser=MultipartJpeg('multipart/x-mixed-replace; boundary="test"')
        data=part(jpg); results=[]
        for b in data:results.extend(parser.feed(bytes([b])))
        self.assertEqual(results[0][0],jpg)
    def test_invalid_header_size_and_duplicate(self):
        for data in [b'--test\r\nContent-Length: 99999999\r\nContent-Type: image/jpeg\r\n\r\n',
                     b'--test\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\n']:
            with self.assertRaises(ValueError):list(MultipartJpeg('multipart/x-mixed-replace; boundary=test').feed(data))
    def test_bad_url_and_path_names(self):
        self.assertEqual(stream_url('192.168.1.3'),'http://192.168.1.3/stream')
        for value in ['http://user:pass@host/stream','file:///tmp/image','http://host/?secret=x']:
            with self.assertRaises(ValueError):stream_url(value)
        for value in ['..','CON','COM9','']:
            with self.assertRaises(ValueError):safe_name(value)
    def test_record_native_jpeg_and_time_contract(self):
        with tempfile.TemporaryDirectory() as tmp, Server() as server:
            rec=Recorder(server.url,tmp,'地点一','批次一','empty_static',seconds=1,sequence_header='X-Frame-Seq')
            folder=rec.start();self.assertTrue(rec.done.wait(5));self.assertFalse(rec.error)
            report=json.loads((folder/'report.json').read_text('utf-8')); self.assertEqual(report['reason'],'duration_reached')
            rows=[json.loads(x) for x in (folder/'frames.jsonl').read_text('utf-8').splitlines()]
            self.assertGreater(len(rows),5);self.assertEqual(report['save_queue_drops'],0)
            for row in rows:
                self.assertEqual((folder/row['file']).read_bytes(),server.jpeg)
                self.assertEqual(row['sha256'],hashlib.sha256(server.jpeg).hexdigest())
                self.assertIsNone(row['source_timestamp_raw']);self.assertIsNone(row['source_clock_mapping'])
                self.assertIsNotNone(row['source_sequence_raw'])
            self.assertTrue(all(a['received_pc_monotonic']<=b['received_pc_monotonic'] for a,b in zip(rows,rows[1:])))
            self.assertFalse(report['capture_time_verified']);self.assertEqual(report['received_jpeg_messages'],len(rows))
    def test_slow_disk_records_drops(self):
        original=Path.open
        def slow_open(p,*args,**kwargs):
            if p.suffix=='.jpg' and args and args[0]=='xb':time.sleep(.02)
            return original(p,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp, Server('burst') as server, patch.object(Path,'open',slow_open):
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1,queue_size=2)
            folder=rec.start();self.assertTrue(rec.done.wait(5))
            report=json.loads((folder/'report.json').read_text('utf-8'))
            self.assertGreater(report['save_queue_drops'],0)
            self.assertEqual(report['received_jpeg_messages'],report['saved_frames']+report['save_queue_drops'])
            self.assertEqual(report['status'],'failed') # stream EOF is a recorded failure, not success
    def test_timeout_persists_failure(self):
        with tempfile.TemporaryDirectory() as tmp, Server('stall') as server:
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1,timeout=.15)
            folder=rec.start();self.assertTrue(rec.done.wait(3))
            report=json.loads((folder/'report.json').read_text('utf-8'));self.assertEqual(report['status'],'failed');self.assertEqual(report['saved_frames'],0)
    def test_stop_is_bounded_and_output_unique(self):
        with tempfile.TemporaryDirectory() as tmp, Server() as server:
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=5)
            folder=rec.start();time.sleep(.15);rec.stop();self.assertTrue(rec.done.wait(3));self.assertEqual(rec.reason,'operator_stop')
            rec2=Recorder(server.url,tmp,'site','batch','scene',seconds=1)
            folder2=rec2.start();self.assertNotEqual(folder,folder2);self.assertTrue(rec2.done.wait(4));self.assertTrue(folder.exists())
    def test_malformed_stream_persists_error(self):
        with tempfile.TemporaryDirectory() as tmp, Server('malformed') as server:
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1)
            folder=rec.start();self.assertTrue(rec.done.wait(3));self.assertTrue(rec.error);self.assertTrue((folder/'report.json').exists())
    def test_report_write_failure_still_notifies_completion(self):
        from recorder import dump as real_dump
        def failing_dump(path,value):
            if Path(path).name=='report.json':raise OSError('simulated disk full')
            return real_dump(path,value)
        with tempfile.TemporaryDirectory() as tmp,Server() as server,patch('recorder.dump',failing_dump):
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1)
            rec.start();self.assertTrue(rec.done.wait(4));self.assertIn('disk full',rec.error);self.assertEqual(rec.reason,'report_write_error')
    def test_corrupt_saved_jpeg_is_detected(self):
        from verify_dataset import verify
        with tempfile.TemporaryDirectory() as tmp,Server() as server:
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1)
            folder=rec.start();self.assertTrue(rec.done.wait(4));self.assertEqual(verify(folder)['status'],'passed')
            first=next((folder/'frames').glob('*.jpg'));first.write_bytes(b'corrupt')
            checked=verify(folder);self.assertEqual(checked['status'],'failed');self.assertTrue(checked['errors'])

if __name__=='__main__':unittest.main(verbosity=2)
