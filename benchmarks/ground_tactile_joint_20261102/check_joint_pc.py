"""Actual two-model FP32 offline smoke; explicitly not X5 deployment proof."""
import json,time,uuid
from pathlib import Path
import cv2,numpy as np
import paths
from backends import OnnxBackend
from tactile_backend import TactileOnnx
from corridor_decision import CorridorDecision
from path_policy import geometry
from check_joint_board import fixtures


def main():
    cv2.setNumThreads(1);out=paths.HERE/'runs'/('pc_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    guard=paths.verify_baseline();items=fixtures(out);floor=OnnxBackend();tactile=TactileOnnx();rows=[]
    try:
        for item in items:
            received=time.monotonic();category,ft=floor.infer(item['jpeg'],item['id']);mask,tt=tactile.infer(item['jpeg'],item['id'])
            # Geometry only: independent still images are not a temporal drive.
            path=geometry(mask);image=cv2.imdecode(np.frombuffer(item['jpeg'],np.uint8),1)
            left=image.copy();right=image.copy()
            fmask=cv2.resize(category,(640,480),interpolation=cv2.INTER_NEAREST)>0
            tmask=cv2.resize(mask,(640,480),interpolation=cv2.INTER_NEAREST)>0
            left[fmask]=(left[fmask]*.45+np.array([180,160,0])*.55).astype(np.uint8)
            right[tmask]=(right[tmask]*.45+np.array([0,200,220])*.55).astype(np.uint8)
            view=np.vstack([np.hstack([left,right]),np.zeros((70,1280,3),np.uint8)])
            cv2.putText(view,'FLOOR FP32                  TACTILE FP32 - offline, no actuator',(12,515),cv2.FONT_HERSHEY_SIMPLEX,.65,(240,240,240),1)
            cv2.imencode('.jpg',view)[1].tofile(out/('pair_%02d.jpg'%item['id']))
            rows.append(dict(id=item['id'],floor_timing=ft,tactile_timing=tt,path_geometry=path,tactile_pixels=int(mask.sum())))
    finally:floor.close();tactile.close()
    report=dict(scope='Actual PC ONNX FP32 on eight independent prepared images; not BIN or X5, no scene accuracy claim or actuator',
                baseline_guard=guard,baseline_guard_after=paths.verify_baseline(),floor_backend=floor.metadata,tactile_backend=tactile.metadata,
                rows=rows,positive_saved_probe_images=sum(r['tactile_pixels']>0 for r in rows[4:]))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),images=len(rows),positive_saved_probe_images=report['positive_saved_probe_images'])))
    if not report['positive_saved_probe_images']:raise RuntimeError('No saved positive probe detected')


if __name__=='__main__':main()
