"""Import the frozen obstacle runtime without editing any baseline file."""
from pathlib import Path
import hashlib,json,sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BASE=HERE.parent/'ground_demo_20261101'
sys.path.insert(0,str(BASE))
sys.path.insert(0,str(HERE.parent/'ground_route_screen_20261031'))
TACTILE=HERE.parent/'blindpath-guideTWSI-x5-20260911/model/yolo11n_tactile_bayese_640x640_nv12.bin'
ONNX=TACTILE.with_name('yolo11n_tactile.onnx')


def verify_baseline():
    manifest=json.loads((ROOT/'submission/baseline.json').read_text(encoding='utf-8'))
    changed=[r['path'] for r in manifest['files'] if not (ROOT/r['path']).is_file() or hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['sha256']]
    if changed:raise RuntimeError('Submission baseline changed: '+repr(changed))
    if not TACTILE.is_file():raise FileNotFoundError('External tactile BIN is not bundled; see docs/STATUS.md')
    if hashlib.sha256(TACTILE.read_bytes()).hexdigest()!='4c5150ce17c1e09264ffbadc20a0af84733be8c5edfbc579aded69f4f47b7439':raise ValueError('Tactile BIN hash changed')
    return dict(frozen_files=len(manifest['files']),changed=changed)
