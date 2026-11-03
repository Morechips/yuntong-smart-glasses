"""Record native ESP MJPEG frames without inference or JPEG re-encoding.

Capture timestamps are optional source metadata. Receive timestamps are never
represented as sensor timestamps. The recorder does not change ESP firmware.
"""
from __future__ import annotations
import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import threading
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]

def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def stream_url(value):
    value = value.strip()
    if '://' not in value:
        value = 'http://' + value
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('请输入微雪板HTTP地址，例如 http://192.168.137.189/stream')
    if parsed.query or parsed.fragment:
        raise ValueError('地址不能带查询参数或片段')
    return urlunsplit(('http', parsed.netloc, parsed.path or '/stream', '', ''))

def safe_name(value):
    result = re.sub(r'[^\w\-]+', '_', value.strip(), flags=re.UNICODE).strip('_. ')
    if not result or result.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('地点和拍摄批次不能为空，也不能使用系统保留名称')
    return result[:80]

class MultipartJpeg:
    """Bounded content-length parser; partial network reads are supported."""
    def __init__(self, content_type, max_frame=2*1024*1024):
        from email.message import Message
        message=Message(); message['content-type']=content_type
        boundary=message.get_param('boundary')
        if message.get_content_type() != 'multipart/x-mixed-replace' or not isinstance(boundary,str):
            raise ValueError('设备未返回MJPEG流；请检查/stream及当前固件')
        if not 1<=len(boundary)<=70 or any(ord(c)<33 or ord(c)>126 for c in boundary):
            raise ValueError('Invalid MJPEG boundary')
        self.marker=b'--'+boundary.encode('ascii'); self.buffer=bytearray(); self.expected=None
        self.headers={}; self.max_frame=max_frame; self.terminated=False

    def feed(self, chunk):
        self.buffer.extend(chunk)
        if len(self.buffer)>self.max_frame+8192+65536: raise ValueError('MJPEG buffer limit exceeded')
        while not self.terminated:
            if self.expected is None:
                start=self.buffer.find(self.marker)
                if start<0:
                    if len(self.buffer)>len(self.marker)+4: del self.buffer[:-(len(self.marker)+4)]
                    return
                if start: del self.buffer[:start]
                n=len(self.marker)
                if len(self.buffer)<n+2: return
                if self.buffer[n:n+2]==b'--': self.terminated=True; return
                if self.buffer[n:n+2]!=b'\r\n': raise ValueError('Malformed multipart boundary')
                end=self.buffer.find(b'\r\n\r\n', n+2)
                if end<0:
                    if len(self.buffer)>8192: raise ValueError('MJPEG header too large')
                    return
                if end>8192: raise ValueError('MJPEG header too large')
                headers={}
                for line in bytes(self.buffer[n+2:end]).split(b'\r\n'):
                    key,sep,val=line.partition(b':')
                    if not sep: raise ValueError('Malformed MJPEG header')
                    key=key.strip().decode('ascii').lower(); val=val.strip().decode('ascii')
                    if key in headers: raise ValueError('Duplicate MJPEG header')
                    headers[key]=val
                length=headers.get('content-length','')
                if not length.isascii() or not length.isdecimal(): raise ValueError('Missing JPEG Content-Length')
                self.expected=int(length)
                if not 0<self.expected<=self.max_frame: raise ValueError('JPEG size limit exceeded')
                if headers.get('content-type','').lower().split(';')[0]!='image/jpeg': raise ValueError('Non-JPEG part')
                self.headers=headers; del self.buffer[:end+4]
            if len(self.buffer)<self.expected: return
            data=bytes(self.buffer[:self.expected]); del self.buffer[:self.expected]
            if not (data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')): raise ValueError('Invalid JPEG markers')
            meta=self.headers; self.expected=None; self.headers={}
            yield data,meta

class Recorder:
    def __init__(self, url, output_root, site, batch, scene, seconds=45, notes='', queue_size=96, timeout=2.0,
                 timestamp_header='', sequence_header='', stream=None):
        if not 1<=float(seconds)<=600: raise ValueError('每段时长应在1至600秒之间')
        if not 1<=queue_size<=1024 or not 0.1<=timeout<=10: raise ValueError('Invalid queue/timeout')
        self.url=stream_url(url); self.seconds=float(seconds); self.timeout=timeout
        self.site=safe_name(site); self.batch=safe_name(batch); self.scene=safe_name(scene)
        self.output_root=Path(output_root).resolve(); self.notes=notes
        self.timestamp_header=timestamp_header.strip().lower(); self.sequence_header=sequence_header.strip().lower()
        self.stream=stream
        self.pending=queue.Queue(queue_size); self.stop_event=threading.Event(); self.reader_done=threading.Event()
        self.done=threading.Event(); self.lock=threading.Lock(); self.thread=None; self.directory=None
        self.latest=None; self.received=0; self.saved=0; self.dropped=0; self.transport_bytes=0
        self.gap_count=0; self.max_gap=0.; self.last_received=None; self.first_received=None
        self.error=''; self.reason=''; self.receipt_times=deque(maxlen=2400); self.drop_ranges=[]
        self.started=None; self.ended=None; self.stop_requested_at=None

    def start(self):
        if self.thread is not None: raise RuntimeError('Recorder already started')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        self.directory=self.output_root/self.site/self.batch/(stamp+'_'+self.scene)
        self.directory.mkdir(parents=True,exist_ok=False); (self.directory/'frames').mkdir()
        self.started=time.monotonic(); self.started_wall=datetime.now(timezone.utc).isoformat()
        dump(self.directory/'session.json',dict(schema='esp-native-jpeg-v1',url=self.url,site_id=self.site,
             batch_id=self.batch,scene=self.scene,notes=self.notes,requested_seconds=self.seconds,started_utc=self.started_wall,
             max_pending_frames=self.pending.maxsize,source_timestamp_header=self.timestamp_header or None,
             source_sequence_header=self.sequence_header or None,
             timing_scope='PC monotonic receive times; no calibrated sensor capture time',
             jpeg_reencoded=False,output_cache_used=False,shared_preview_stream=self.stream is not None,
             split='unassigned',label_source='operator scene label; not pixel truth'))
        shutil.copy2(Path(__file__),self.directory/'recorder_source.py')
        if self.stream is not None:shutil.copy2(Path(__file__).with_name('live_stream.py'),self.directory/'live_stream_source.py')
        self.thread=threading.Thread(target=self._run,name='esp-dataset-recorder',daemon=False); self.thread.start()
        return self.directory

    def stop(self):
        self.stop_requested_at=self.stop_requested_at or time.monotonic(); self.stop_event.set()

    def mark(self,label):
        if self.directory and not self.done.is_set():
            with (self.directory/'operator_events.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(label=label,pc_monotonic=time.monotonic(),scope='operator marker, not ground truth'),ensure_ascii=False)+'\n')

    def snapshot(self):
        with self.lock:
            now=time.monotonic(); elapsed=(self.ended or now)-(self.started or now)
            recent=sum(t>=now-10 for t in self.receipt_times)
            recent_window=min(10,max(.001,now-(self.first_received or now)))
            return dict(received=self.received,saved=self.saved,save_queue_drops=self.dropped,pending=self.pending.qsize(),
                        elapsed_s=elapsed,remaining_s=max(0,self.seconds-elapsed),received_fps=self.received/max(elapsed,.001),
                        fps_10s=recent/recent_window if self.received else 0.,max_receive_gap_s=self.max_gap,
                        gaps_over_500ms=self.gap_count,error=self.error,reason=self.reason,done=self.done.is_set(),
                        directory=str(self.directory) if self.directory else None)

    def preview(self):
        with self.lock: return self.latest

    def _accept(self,jpeg,headers,now):
        if self.stop_event.is_set() or now<self.started or now>=self.started+self.seconds:return
        with self.lock:
            idx=self.received;self.received+=1;self.receipt_times.append(now)
            if self.last_received is not None:
                gap=now-self.last_received;self.max_gap=max(self.max_gap,gap);self.gap_count+=int(gap>.5)
            self.last_received=now
            if self.first_received is None:self.first_received=now
            self.latest=(idx,jpeg)
        meta=dict(index=idx,file=f'frames/{idx:08d}.jpg',jpeg_bytes=len(jpeg),received_pc_monotonic=now,
                  received_session_seconds=now-self.started,received_unix_ns=time.time_ns(),
                  source_timestamp_raw=headers.get(self.timestamp_header) if self.timestamp_header else None,
                  source_sequence_raw=headers.get(self.sequence_header) if self.sequence_header else None,
                  source_clock_mapping=None,part_headers=headers)
        try:self.pending.put_nowait((jpeg,meta))
        except queue.Full:
            with self.lock:
                self.dropped+=1
                if self.drop_ranges and self.drop_ranges[-1][1]==idx-1:self.drop_ranges[-1][1]=idx
                else:self.drop_ranges.append([idx,idx])

    def _read_shared(self):
        subscribed=False
        try:
            dump(self.directory/'device_stats_before.json',dict(unavailable='Preview already owns the HTTP stream; no competing diagnostics request was issued'))
            dump(self.directory/'http_headers.json',self.stream.http_headers)
            self.stream.subscribe(self._accept);subscribed=True
            deadline=self.started+self.seconds
            while not self.stop_event.is_set() and time.monotonic()<deadline:
                if self.stream.done.wait(.02):
                    if self.stop_event.is_set():break
                    raise ConnectionError(self.stream.error or 'Preview stream ended')
            if not self.error:self.reason='operator_stop' if self.stop_event.is_set() else 'duration_reached'
        except Exception as exc:
            if not self.error:self.error=str(exc);self.reason='stream_error'
            self.stop_event.set()
        finally:
            if subscribed:self.stream.unsubscribe(self._accept)
            try:dump(self.directory/'http_headers.json',self.stream.http_headers)
            except Exception as exc:
                if not self.error:self.error=str(exc);self.reason='storage_error'
            self.ended=time.monotonic();self.reader_done.set()

    def _read(self):
        if self.stream is not None:return self._read_shared()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base=urlsplit(self.url)
            stats_url=urlunsplit((base.scheme,base.netloc,'/stats','',''))
            try:
                with opener.open(stats_url,timeout=min(1.,self.timeout)) as diagnostic:
                    payload=bytearray(); deadline=time.monotonic()+1.; stats=None
                    while not self.stop_event.is_set() and time.monotonic()<deadline:
                        chunk=diagnostic.read1(min(4096,65537-len(payload)))
                        if not chunk: break
                        payload.extend(chunk)
                        if len(payload)>65536: raise ValueError('Stats response limit exceeded')
                        try: stats=json.loads(payload.decode('utf-8')); break
                        except (UnicodeDecodeError,json.JSONDecodeError): pass
                    if stats is None: raise ValueError('Stats deadline reached or incomplete JSON')
                    dump(self.directory/'device_stats_before.json',dict(url=stats_url,data=stats,scope='device self-report; not independently verified firmware identity'))
            except Exception as exc:
                dump(self.directory/'device_stats_before.json',dict(url=stats_url,unavailable=str(exc)))
            if self.stop_event.is_set(): self.reason='operator_stop'; return
            with opener.open(urllib.request.Request(self.url,headers={'User-Agent':'Yuntong-Dataset-Capture/1'}),timeout=self.timeout) as response:
                if response.status!=200: raise ValueError(f'HTTP {response.status}')
                dump(self.directory/'http_headers.json',dict(response.headers.items()))
                parser=MultipartJpeg(response.headers.get('Content-Type',''))
                deadline=self.started+self.seconds
                while not self.stop_event.is_set() and time.monotonic()<deadline:
                    chunk=response.read1(65536)
                    if not chunk:
                        if self.stop_event.is_set(): break
                        raise ConnectionError('设备断流：本段已保留，请重新开始下一段')
                    self.transport_bytes+=len(chunk)
                    for jpeg,headers in parser.feed(chunk):
                        now=time.monotonic()
                        if self.stop_event.is_set() or now>=deadline: break
                        self._accept(jpeg,headers,now)
                    if parser.terminated: raise ConnectionError('设备结束MJPEG流')
                if not self.error: self.reason='operator_stop' if self.stop_event.is_set() else 'duration_reached'
        except (Exception,) as exc:
            if self.error: pass
            elif self.stop_event.is_set(): self.reason='operator_stop'
            else: self.error=str(exc); self.reason='stream_error'; self.stop_event.set()
        finally:
            self.ended=time.monotonic(); self.reader_done.set()

    def _run(self):
        reader=threading.Thread(target=self._read,name='esp-jpeg-receive',daemon=True); reader.start()
        try:
            with (self.directory/'frames.jsonl').open('x',encoding='utf-8') as index:
                while not self.reader_done.is_set() or not self.pending.empty():
                    try: jpeg,meta=self.pending.get(timeout=.05)
                    except queue.Empty: continue
                    try:
                        before=time.monotonic(); path=self.directory/meta['file']
                        with path.open('xb') as f: f.write(jpeg)
                        meta['sha256']=hashlib.sha256(jpeg).hexdigest(); meta['write_finished_pc_monotonic']=time.monotonic()
                        meta['write_ms']=(meta['write_finished_pc_monotonic']-before)*1000
                        index.write(json.dumps(meta,ensure_ascii=False)+'\n'); index.flush()
                        with self.lock: self.saved+=1
                    finally: self.pending.task_done()
        except Exception as exc:
            self.error='保存失败：'+str(exc); self.reason='storage_error'; self.stop_event.set()
        finally:
            reader.join(self.timeout+2)
            if reader.is_alive(): self.error=self.error or '接收线程未及时结束'; self.reason='reader_shutdown_error'
            self.ended=self.ended or time.monotonic()
            elapsed=max(.001,self.ended-self.started)
            trailing=max(0,self.ended-(self.last_received or self.started))
            status='failed' if self.error or not self.saved else 'partial' if self.dropped else 'recorded'
            report=dict(schema='esp-native-jpeg-v1',status=status,reason=self.reason,error=self.error or None,
                        started_utc=self.started_wall,elapsed_s=elapsed,requested_seconds=self.seconds,
                        received_jpeg_messages=self.received,saved_frames=self.saved,save_queue_drops=self.dropped,
                        unpersisted_frames=self.received-self.saved-self.dropped,
                        dropped_index_ranges=self.drop_ranges,received_fps=self.received/elapsed,saved_fps=self.saved/elapsed,
                        max_receive_gap_s=self.max_gap,gaps_over_500ms=self.gap_count,
                        first_frame_delay_s=None if self.first_received is None else self.first_received-self.started,
                        trailing_no_frame_seconds=trailing,transport_bytes=None if self.stream is not None else self.transport_bytes,
                        native_jpeg_preserved=True,capture_time_verified=False,
                        shared_preview_stream=self.stream is not None,
                        transport_bytes_scope='not counted for shared preview connection' if self.stream else 'bytes read by standalone recording connection',
                        timestamp_basis='PC receive monotonic clock; optional uncalibrated device metadata retained verbatim',
                        automatic_reconnect=False,scope='Native ESP camera dataset capture, not model FPS or obstacle validation')
            try: dump(self.directory/'report.json',report)
            except Exception as exc:
                self.error=(self.error+'；' if self.error else '')+'最终报告保存失败：'+str(exc)
                self.reason='report_write_error'
            finally:
                with self.lock: self.done.set()

def record_headless(args):
    session=Recorder(args.url,args.output,args.site,args.batch,args.scene,args.seconds,args.notes,
                     timestamp_header=args.timestamp_header,sequence_header=args.sequence_header)
    directory=session.start(); print('Saving to '+str(directory),flush=True)
    try:
        while not session.done.wait(.5): pass
    except KeyboardInterrupt:
        session.stop()
        while not session.done.wait(.2): pass
    print(json.dumps(session.snapshot(),ensure_ascii=False,indent=2))
    return 1 if session.error or not session.saved else 0

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--url',required=True)
    p.add_argument('--output',type=Path,default=ROOT/'datasets/esp_capture')
    p.add_argument('--site',required=True); p.add_argument('--batch',required=True); p.add_argument('--scene',required=True)
    p.add_argument('--seconds',type=float,default=45); p.add_argument('--notes',default='')
    p.add_argument('--timestamp-header',default=''); p.add_argument('--sequence-header',default='')
    return record_headless(p.parse_args())

if __name__=='__main__': raise SystemExit(main())
