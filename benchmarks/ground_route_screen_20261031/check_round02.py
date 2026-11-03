"""Bounded checks with the installed X5 compiler, without toolchain patches."""
from pathlib import Path
import hashlib
import json
import subprocess
import time

OUT=Path(__file__).resolve().parent/'runs/round02_20261101'


def linux(path):
    value=str(path.resolve()).replace('\\','/')
    return '/mnt/'+value[0].lower()+value[2:]


def main():
    root=OUT/'compiler_check';root.mkdir(exist_ok=False)
    rows=[]
    for kind in ['floor_only','floor_obstacle_context']:
        folder=root/kind;folder.mkdir()
        model=OUT/kind/'model.onnx'
        digest=hashlib.sha256(model.read_bytes()).hexdigest()
        command=['wsl.exe','-d','Ubuntu-22.04','--cd',linux(folder),'--exec','/usr/bin/env',
                 'PATH=/root/blindpath_compile/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                 'VIRTUAL_ENV=/root/blindpath_compile/.venv',
                 '/root/blindpath_compile/.venv/bin/hb_mapper','checker','--model-type','onnx',
                 '--march','bayes-e','--model',linux(model),'--input-shape','image','1x3x240x320']
        row=dict(kind=kind,model_sha256=digest,command=command,
                 scope='Compiler compatibility only; no quantized BIN or actual BPU inference')
        (folder/'command.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        started=time.perf_counter()
        with (folder/'console.log').open('wb') as log:
            try:
                process=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180)
                row['exit_code']=process.returncode
            except subprocess.TimeoutExpired:
                row['timeout_s']=180
        row['elapsed_s']=time.perf_counter()-started
        row['source_model_unchanged']=hashlib.sha256(model.read_bytes()).hexdigest()==digest
        rows.append(row)
        (root/'report.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in row.items() if k!='command'}),flush=True)


if __name__=='__main__':main()
