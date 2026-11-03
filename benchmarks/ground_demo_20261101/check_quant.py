from pathlib import Path
import json
import cv2,numpy as np,onnxruntime as ort
from demo_core import categories,candidates,render

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
RUN=HERE/'runs/compile_01'
MODEL=HERE.parent/'ground_route_screen_20261031/runs/round02_20261101/floor_only/model.onnx'

def main():
    options=ort.SessionOptions();options.intra_op_num_threads=2
    session=ort.InferenceSession(str(MODEL),sess_options=options,providers=['CPUExecutionProvider'])
    rows=[];views=RUN/'comparison';views.mkdir(exist_ok=True)
    for sample in json.loads((RUN/'inputs.json').read_text(encoding='utf-8'))['samples']:
        sid=sample['sample_id'];x=np.load(RUN/sample['input_file'],allow_pickle=False)
        fp=categories(session.run(None,{'image':x})[0]);quant=categories(np.load(RUN/'emulator'/(sid+'.npy'),allow_pickle=False))
        a,_=candidates(fp);b,_=candidates(quant)
        row=dict(sample_id=sid,different_pixels=int((fp!=quant).sum()),pixel_agreement=float((fp==quant).mean()),
                 fp32_status=a['status'],fp32_direction=a['direction'],quant_status=b['status'],quant_direction=b['direction'])
        rows.append(row)
        if sid.endswith('_02'):
            rgb=cv2.imdecode(np.fromfile(ROOT/sample['source'],np.uint8),1)
            ok,encoded=cv2.imencode('.jpg',np.hstack([render(rgb,fp,a,'FP32'),render(rgb,quant,b,'QUANT EMULATOR')]))
            if not ok:raise RuntimeError('JPEG encoding failed')
            encoded.tofile(views/(sid+'.jpg'))
    report=dict(scope='Same model and same tensors: FP32 vs quantized emulator, development images only',rows=rows,
                min_pixel_agreement=min(r['pixel_agreement'] for r in rows),
                candidate_contract_equal=sum((r['fp32_status'],r['fp32_direction'])==(r['quant_status'],r['quant_direction']) for r in rows),
                count=len(rows),not_actual_board=True)
    (RUN/'quant_comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
