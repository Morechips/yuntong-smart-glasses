"""One framed request at a time over an existing SSH channel; no listener."""
import base64,json,sys,time,zlib
from pathlib import Path
import cv2,numpy as np
from hobot_dnn import pyeasy_dnn
from demo_core import preprocess,categories,featuremap_bytes

def emit(value):print('DEMO_JSON:'+json.dumps(value),flush=True)

def main():
    cv2.setNumThreads(1)
    started=time.perf_counter();model=pyeasy_dnn.load(sys.argv[1])[0]
    if str(model.inputs[0].properties.dtype)!='float32' or tuple(model.inputs[0].properties.shape)!=(1,3,240,320):
        raise ValueError('Unexpected compiled input contract')
    emit(dict(ready=True,load_ms=(time.perf_counter()-started)*1000,
              inputs=[dict(shape=list(i.properties.shape),dtype=str(i.properties.dtype)) for i in model.inputs],
              outputs=[dict(shape=list(i.properties.shape),dtype=str(i.properties.dtype)) for i in model.outputs]))
    while True:
        line=sys.stdin.buffer.readline(3*1024*1024)
        if not line:break
        if not line.endswith(b'\n'):raise ValueError('Request limit exceeded')
        request=json.loads(line)
        if request.get('stop'):break
        started=time.perf_counter();data=base64.b64decode(request['jpeg'],validate=True)
        if len(data)>2*1024*1024:raise ValueError('JPEG limit')
        bgr=cv2.imdecode(np.frombuffer(data,np.uint8),1)
        if bgr is None:raise ValueError('Bad JPEG')
        x=preprocess(bgr);preprocessed=time.perf_counter()
        tensor=model.forward(featuremap_bytes(x))[0];raw=np.asarray(tensor.buffer).copy();forwarded=time.perf_counter()
        if request.get('diagnostic'):
            np.save(Path(__file__).parent/'diagnostic_input.npy',x)
            np.save(Path(__file__).parent/'diagnostic_logits.npy',raw)
        category=categories(raw)
        emit(dict(id=request['id'],category=base64.b64encode(zlib.compress(category.tobytes())).decode('ascii'),
                  model_readback_ms=(forwarded-preprocessed)*1000,total_board_ms=(time.perf_counter()-started)*1000,
                  output_shape=list(raw.shape),output_dtype=str(raw.dtype)))

if __name__=='__main__':main()
