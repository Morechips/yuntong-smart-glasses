"""Reuse framed IO and exact RAM cleanup; distinct model/worker lifetime."""
import paths
from pathlib import Path
import hashlib,json,shlex,time,uuid
import cv2,numpy as np
from backends import X5Backend
from board_connection import connect_checked,run,sftp


class TactileBackend(X5Backend):
    name='X5 GuideTWSI BIN'
    def __init__(self,password):
        self.run=run;self.ctx=connect_checked(password);self.transport=self.ctx.__enter__()
        self.channel=None;self.reader=None;self.writer=None
        self.remote='/dev/shm/yuntong_floor_demo_tactile_'+uuid.uuid4().hex[:12]
        try:
            result=run(self.transport,'cat /proc/sys/kernel/random/boot_id')
            if result['code']!=0:raise RuntimeError('Cannot read X5 identity')
            self.boot=result['stdout'].strip()
            if run(self.transport,'mkdir '+shlex.quote(self.remote))['code']!=0:raise RuntimeError('Cannot create RAM folder')
            files=[(paths.TACTILE,'model.bin'),(paths.HERE/'tactile_worker.py','tactile_worker.py'),(paths.HERE/'pyeasy_yolo_seg.py','pyeasy_yolo_seg.py')]
            files += [(p,'runtime_utils/'+p.name) for p in (paths.HERE/'runtime_utils').glob('*.py')]
            with sftp(self.transport) as client:
                client.mkdir(self.remote+'/runtime_utils')
                for source,name in files:
                    client.put(str(source),self.remote+'/'+name)
                    with client.open(self.remote+'/'+name,'rb') as f:
                        if hashlib.sha256(f.read()).digest()!=hashlib.sha256(source.read_bytes()).digest():raise ValueError('Upload SHA mismatch')
            self.channel=self.transport.open_session();self.channel.settimeout(12)
            self.channel.exec_command('env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -u '+shlex.quote(self.remote+'/tactile_worker.py')+' '+shlex.quote(self.remote+'/model.bin'))
            self.reader=self.channel.makefile('rb');self.writer=self.channel.makefile('wb')
            ready=self._read()
            if not ready.get('ready') or ready['outputs']!=10 or ready['input_size']!=[640,640]:raise ValueError('Wrong tactile contract')
            self.channel.settimeout(3)
            self.metadata=dict(backend=self.name,boot=self.boot,remote=self.remote,worker=ready,model_sha256=hashlib.sha256(paths.TACTILE.read_bytes()).hexdigest())
        except Exception:self.close();raise


class TactileOnnx:
    name='PC GuideTWSI FP32; NOT BPU'
    def __init__(self):
        import onnxruntime as ort
        from pyeasy_yolo_seg import YOLOSegDecoder,SegConfig
        options=ort.SessionOptions();options.intra_op_num_threads=2
        self.session=ort.InferenceSession(str(paths.ONNX),sess_options=options,providers=['CPUExecutionProvider'])
        self.decoder=YOLOSegDecoder(SegConfig(score_thres=.50))
        self.metadata=dict(backend=self.name,model_sha256=hashlib.sha256(paths.ONNX.read_bytes()).hexdigest())
    def infer(self,jpeg,request_id):
        from pyeasy_yolo_seg import masks_to_full
        from runtime_utils.preprocess import resized_image
        started=time.perf_counter();image=cv2.imdecode(np.frombuffer(jpeg,np.uint8),1)
        if image is None:raise ValueError('Bad JPEG')
        resized=resized_image(image,640,640,1)
        x=np.ascontiguousarray(cv2.cvtColor(resized,cv2.COLOR_BGR2RGB).transpose(2,0,1)[None],dtype=np.float32)/255
        boxes,scores,_,masks=self.decoder.decode(self.session.run(None,{self.session.get_inputs()[0].name:x}),image.shape[1],image.shape[0])
        full=masks_to_full(masks,boxes,image.shape[1],image.shape[0])
        return cv2.resize(full,(320,240),interpolation=cv2.INTER_NEAREST),dict(id=request_id,detections=len(boxes),scores=scores.tolist(),pc_total_ms=(time.perf_counter()-started)*1000)
    def close(self):pass
