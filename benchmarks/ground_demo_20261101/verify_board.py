"""Same-JPEG quantized-emulator vs actual BIN consistency; no accuracy claim."""
from pathlib import Path
import argparse,getpass,json
import numpy as np
from backends import X5Backend
from demo_core import categories,candidates

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];RUN=HERE/'runs/compile_01'

def verify_backend(backend,out,sample_ids=None):
    out.mkdir(exist_ok=False);rows=[]
    for index,sample in enumerate(json.loads((RUN/'inputs.json').read_text(encoding='utf-8'))['samples']):
        sid=sample['sample_id']
        if sample_ids and sid not in sample_ids:continue
        category,timing=backend.infer((ROOT/sample['source']).read_bytes(),index)
        expected=categories(np.load(RUN/'emulator'/(sid+'.npy'),allow_pickle=False))
        a,_=candidates(expected);b,_=candidates(category)
        np.save(out/(sid+'.npy'),category)
        rows.append(dict(sample_id=sid,pixel_agreement=float((category==expected).mean()),
                         candidate_equal=(a['status'],a['direction'])==(b['status'],b['direction']),timing=timing))
    report=dict(rows=rows,min_pixel_agreement=min(r['pixel_agreement'] for r in rows),
                candidate_contract_equal=all(r['candidate_equal'] for r in rows),scope='Quantized emulator vs actual BIN; development inputs only')
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--password-stdin',action='store_true')
    parser.add_argument('--output',type=Path,default=HERE/'runs/board_verify_01');args=parser.parse_args()
    password=input() if args.password_stdin else getpass.getpass('X5 SSH password: ')
    backend=X5Backend(password)
    try:
        report=verify_backend(backend,args.output)
        (args.output/'backend.json').write_text(json.dumps(backend.metadata,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
    finally:
        backend.close()
        if args.output.exists():(args.output/'cleanup.json').write_text(json.dumps(getattr(backend,'cleanup',None)),encoding='utf-8')

if __name__=='__main__':main()
