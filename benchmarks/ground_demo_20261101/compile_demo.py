"""Convert the frozen floor-only model using real development calibration."""
from pathlib import Path
import hashlib,json,subprocess,time
import cv2,numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
R1=HERE.parent/'ground_route_screen_20261031/runs/round01'
MODEL=HERE.parent/'ground_route_screen_20261031/runs/round02_20261101/floor_only/model.onnx'
OUT=HERE/'runs/compile_01'
VENV='/root/blindpath_compile/.venv'

def save(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def linux(path):
    s=str(path.resolve()).replace('\\','/');return '/mnt/'+s[0].lower()+s[2:]

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    (OUT/'calibration').mkdir();(OUT/'inputs').mkdir()
    manifest=json.loads((R1/'data/manifest.json').read_text(encoding='utf-8'))
    rows=[]
    for item in manifest['samples']:
        if item['split']!='development_scene_A':continue
        source=R1/'data'/item['file'];assert sha(source)==item['sha256']
        rgb=cv2.cvtColor(cv2.imdecode(np.fromfile(source,np.uint8),1),cv2.COLOR_BGR2RGB)
        x=np.ascontiguousarray((cv2.resize(rgb,(320,240)).astype(np.float32)/127.5-1).transpose(2,0,1)[None])
        name=item['sample_id'];np.save(OUT/'inputs'/(name+'.npy'),x)
        calibration=not name.startswith(('A05_','A06_'))
        if calibration:x.tofile(OUT/'calibration'/(name+'.f32'))
        rows.append(dict(sample_id=name,source=str(source.relative_to(ROOT)),source_sha256=item['sha256'],
                         input_file='inputs/'+name+'.npy',input_sha256=sha(OUT/'inputs'/(name+'.npy')),calibration=calibration))
    assert len(rows)==32 and sum(r['calibration'] for r in rows)==24
    save(OUT/'inputs.json',dict(scope='Development Scene A only; no independent accuracy',samples=rows))
    config=f'''model_parameters:
  onnx_model: '{linux(MODEL)}'
  march: 'bayes-e'
  layer_out_dump: false
  working_dir: '{linux(OUT)}/work'
  output_model_file_prefix: 'floor_demo'
input_parameters:
  input_name: 'image'
  input_type_train: 'featuremap'
  input_type_rt: 'featuremap'
  input_layout_train: 'NCHW'
  input_layout_rt: 'NCHW'
  input_shape: '1x3x240x320'
  norm_type: 'no_preprocess'
calibration_parameters:
  cal_data_dir: '{linux(OUT)}/calibration'
  cal_data_type: 'float32'
  preprocess_on: false
  calibration_type: 'max'
compiler_parameters:
  jobs: 1
  compile_mode: 'latency'
  debug: false
  optimize_level: 'O2'
'''
    (OUT/'config.yaml').write_text(config,encoding='utf-8')
    command=['wsl.exe','-d','Ubuntu-22.04','--cd',linux(OUT),'--exec','/usr/bin/timeout','-k','10s','180s',
             '/usr/bin/env','PATH='+VENV+'/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
             'VIRTUAL_ENV='+VENV,VENV+'/bin/hb_mapper','makertbin','--config',linux(OUT/'config.yaml'),'--model-type','onnx']
    before=sha(MODEL);save(OUT/'command.json',dict(command=command,model_sha256=before,diagnostics_bypassed=False))
    started=time.perf_counter()
    with (OUT/'console.log').open('wb') as f:
        result=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=210)
    report=dict(exit_code=result.returncode,elapsed_s=time.perf_counter()-started,source_model_unchanged=sha(MODEL)==before,
                calibration_frames=24,diagnostics_bypassed=False,actual_board_run=False,
                artifacts=[dict(file=str(p.relative_to(OUT)),sha256=sha(p),bytes=p.stat().st_size) for p in (OUT/'work').glob('*') if p.is_file() and p.suffix in ['.bin','.onnx']])
    save(OUT/'report.json',report);print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
