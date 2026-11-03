from pathlib import Path
import base64,hashlib,json,shlex,sys,time,uuid,zlib
import cv2,numpy as np
from demo_core import preprocess,categories

HERE=Path(__file__).resolve().parent
MODEL=HERE.parent/'ground_route_screen_20261031/runs/round02_20261101/floor_only/model.onnx'
BIN=HERE/'runs/compile_01/work/floor_demo.bin'

class OnnxBackend:
    name='PC ONNX FP32'
    def __init__(self):
        import onnxruntime as ort
        options=ort.SessionOptions();options.intra_op_num_threads=2
        self.session=ort.InferenceSession(str(MODEL),sess_options=options,providers=['CPUExecutionProvider'])
        self.metadata=dict(backend=self.name,model_sha256=hashlib.sha256(MODEL.read_bytes()).hexdigest())
    def infer(self,jpeg,request_id,diagnostic=False):
        started=time.perf_counter();bgr=cv2.imdecode(np.frombuffer(jpeg,np.uint8),1)
        if bgr is None:raise ValueError('JPEG decode failed')
        result=categories(self.session.run(None,{'image':preprocess(bgr)})[0])
        return result,dict(pc_compute_ms=(time.perf_counter()-started)*1000)
    def close(self):pass

class X5Backend:
    name='X5 BPU BIN via PC relay'
    def __init__(self,password):
        sys.path.insert(0,str(HERE.parent/'ground_route_screen_20261031'))
        from rdk_io import connect,run,sftp
        self.run=run;self.ctx=connect(password);self.transport=self.ctx.__enter__()
        self.channel=None;self.reader=None;self.writer=None
        self.remote='/dev/shm/yuntong_floor_demo_'+uuid.uuid4().hex[:12]
        try:
            pre=run(self.transport,"python3 -c \"import json,pathlib,shutil;print(json.dumps(dict(boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),ram_free=shutil.disk_usage('/dev/shm').free)))\"")
            if pre['code']!=0:raise RuntimeError('X5 preflight failed: '+pre['stderr'])
            health=json.loads(pre['stdout']);assert health['ram_free']>64*1024**2
            self.boot=health['boot']
            assert run(self.transport,'mkdir '+shlex.quote(self.remote))['code']==0
            files=[(BIN,'model.bin'),(HERE/'board_worker.py','board_worker.py'),(HERE/'demo_core.py','demo_core.py')]
            with sftp(self.transport) as client:
                for source,name in files:
                    client.put(str(source),self.remote+'/'+name)
                    with client.open(self.remote+'/'+name,'rb') as stream:
                        if hashlib.sha256(stream.read()).hexdigest()!=hashlib.sha256(source.read_bytes()).hexdigest():raise ValueError('Upload SHA mismatch')
            self.channel=self.transport.open_session();self.channel.settimeout(10)
            self.channel.exec_command('PYTHONDONTWRITEBYTECODE=1 python3 -u '+shlex.quote(self.remote+'/board_worker.py')+' '+shlex.quote(self.remote+'/model.bin'))
            self.reader=self.channel.makefile('rb');self.writer=self.channel.makefile('wb')
            ready=self._read();assert ready['ready']
            self.channel.settimeout(3)
            self.metadata=dict(backend=self.name,remote=self.remote,boot=self.boot,preflight=health,worker=ready,
                               model_sha256=hashlib.sha256(BIN.read_bytes()).hexdigest())
        except Exception:
            self.close();raise
    def _read(self):
        # Native DNN initialization may print informational lines before ready.
        for _ in range(100):
            line=self.reader.readline(2*1024*1024)
            if not line:raise RuntimeError('X5 worker exited')
            if line.startswith(b'DEMO_JSON:'):return json.loads(line[len(b'DEMO_JSON:'):])
        raise RuntimeError('Unexpected X5 output')
    def infer(self,jpeg,request_id,diagnostic=False):
        started=time.perf_counter()
        self.writer.write((json.dumps(dict(id=request_id,jpeg=base64.b64encode(jpeg).decode('ascii'),diagnostic=diagnostic))+'\n').encode());self.writer.flush()
        result=self._read()
        if result['id']!=request_id:raise ValueError('X5 response id mismatch')
        data=zlib.decompress(base64.b64decode(result.pop('category')))
        if len(data)!=240*320:raise ValueError('X5 category size')
        category=np.frombuffer(data,np.uint8).reshape(240,320).copy()
        if not np.isin(category,[0,1]).all():raise ValueError('X5 category contract')
        result['pc_ssh_roundtrip_ms']=(time.perf_counter()-started)*1000
        return category,result
    def close(self):
        if self.writer:
            try:self.writer.write(b'{"stop":true}\n');self.writer.flush()
            except Exception:pass
        if self.channel:
            # Allow normal worker/model teardown before closing its channel.
            deadline=time.monotonic()+2
            while not self.channel.exit_status_ready() and time.monotonic()<deadline:time.sleep(.02)
            self.channel.close()
        if getattr(self,'transport',None):
            try:
                health=self.run(self.transport,"cat /proc/sys/kernel/random/boot_id",timeout=5)
                if health['stdout'].strip()==getattr(self,'boot',None):
                    # Exact freshly-created directory only; no shell glob or SD path.
                    command="python3 -c "+shlex.quote("import pathlib,shutil;p=pathlib.Path("+repr(self.remote)+");assert p.parent==pathlib.Path('/dev/shm') and p.name.startswith('yuntong_floor_demo_');shutil.rmtree(p)")
                    self.cleanup=self.run(self.transport,command,timeout=5)
            except Exception as exc:self.cleanup=dict(error=str(exc))
            self.ctx.__exit__(None,None,None);self.transport=None
