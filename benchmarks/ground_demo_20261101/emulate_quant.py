"""Run only within the installed Linux Horizon toolchain."""
from pathlib import Path
import json,time
import numpy as np
from horizon_tc_ui import HB_ONNXRuntime

HERE=Path(__file__).resolve().parent
RUN=HERE/'runs/compile_01'

def main():
    out=RUN/'emulator';out.mkdir(exist_ok=False)
    model=RUN/'work/floor_demo_quantized_model.onnx'
    session=HB_ONNXRuntime(model_file=str(model))
    manifest=json.loads((RUN/'inputs.json').read_text(encoding='utf-8'))
    rows=[]
    for row in manifest['samples']:
        x=np.load(RUN/row['input_file'],allow_pickle=False)
        started=time.perf_counter();value=session.run(input_info={session.input_names[0]:x})[0]
        assert np.isfinite(value).all()
        np.save(out/(row['sample_id']+'.npy'),value)
        rows.append(dict(sample_id=row['sample_id'],shape=list(value.shape),dtype=str(value.dtype),emulator_ms=(time.perf_counter()-started)*1000))
    report=dict(scope='Quantized ONNX emulation; not X5 speed',input_names=session.input_names,input_shapes=session.input_shapes,
                output_names=session.output_names,output_shapes=session.output_shapes,rows=rows)
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(dict(completed=len(rows))),flush=True)

if __name__=='__main__':main()
