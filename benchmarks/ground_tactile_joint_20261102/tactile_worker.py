"""Separate X5 process: NV12 tactile model, never drives an actuator."""
import base64,json,sys,time,zlib
import cv2,numpy as np
from pyeasy_yolo_seg import PyeasyYOLOSeg,SegConfig,masks_to_full
from runtime_utils.postprocess import SOFTMAX_BACKEND


def emit(value):print('DEMO_JSON:'+json.dumps(value),flush=True)


def main():
    cv2.setNumThreads(1)
    model=PyeasyYOLOSeg(SegConfig(model_path=sys.argv[1],score_thres=.50))
    emit(dict(ready=True,kind='tactile',softmax_backend=SOFTMAX_BACKEND,
              input_size=[model.cfg.input_h,model.cfg.input_w],outputs=len(model.model.outputs)))
    while True:
        line=sys.stdin.buffer.readline(3*1024*1024)
        if not line:break
        if not line.endswith(b'\n'):raise ValueError('Request too large')
        request=json.loads(line)
        if request.get('stop'):break
        started=time.perf_counter();data=base64.b64decode(request['jpeg'],validate=True)
        if len(data)>2*1024*1024:raise ValueError('JPEG too large')
        image=cv2.imdecode(np.frombuffer(data,np.uint8),1)
        if image is None:raise ValueError('Bad JPEG')
        prepared=time.perf_counter();boxes,scores,classes,masks=model.predict(image);inferred=time.perf_counter()
        full=masks_to_full(masks,boxes,image.shape[1],image.shape[0])
        mask=cv2.resize(full,(320,240),interpolation=cv2.INTER_NEAREST)
        emit(dict(id=request['id'],category=base64.b64encode(zlib.compress(mask.tobytes())).decode(),
                  predict_including_prepost_ms=(inferred-prepared)*1000,total_board_ms=(time.perf_counter()-started)*1000,
                  detections=len(boxes),scores=scores.tolist(),boxes=boxes.tolist()))


if __name__=='__main__':main()
