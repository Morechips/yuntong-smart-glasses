"""Verify stored JPEG bytes, decoding and receive-time index without re-encoding."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

def verify(folder):
    folder=Path(folder).resolve(); errors=[]; last_index=-1; last_time=None; checked=0
    rows=[]
    for line in (folder/'frames.jsonl').read_text(encoding='utf-8').splitlines():
        try:rows.append(json.loads(line))
        except Exception as exc:errors.append('Invalid JSON row: '+str(exc))
    for row in rows:
        try:
            target=(folder/row['file']).resolve()
            if folder not in target.parents:raise ValueError('Frame path escapes dataset')
            data=target.read_bytes()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Frame SHA mismatch')
            if len(data)!=row['jpeg_bytes']:raise ValueError('Frame length mismatch')
            with Image.open(target) as im:
                if im.format!='JPEG':raise ValueError('Stored frame is not JPEG')
                im.load()
            if row['index']<=last_index:raise ValueError('Duplicate/reversed frame index')
            if last_time is not None and row['received_pc_monotonic']<last_time:raise ValueError('Reversed receive time')
            last_index=row['index'];last_time=row['received_pc_monotonic'];checked+=1
        except Exception as exc:errors.append(f"{row.get('index')}: {exc}")
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    if len(rows)!=report['saved_frames']:errors.append('Index count disagrees with saved frame count')
    if report['received_jpeg_messages']!=report['saved_frames']+report['save_queue_drops']+report['unpersisted_frames']:errors.append('Receive/save accounting mismatch')
    if not rows:errors.append('No saved frames')
    return dict(status='passed' if not errors else 'failed',frames_checked=checked,errors=errors,
                acquisition_status=report['status'],save_queue_drops=report['save_queue_drops'],
                max_receive_gap_s=report['max_receive_gap_s'],capture_time_verified=False,
                scope='Byte integrity, JPEG decoding and receive-time index checks; not synchronized capture timing or algorithm accuracy')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();result=verify(a.folder)
    (a.folder/'quality_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='passed' else 1)
