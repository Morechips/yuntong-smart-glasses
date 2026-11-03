"""Cached JPEG comparison: two resident BINs, no ESP input or actuators."""
import argparse,hashlib,json,threading,time,uuid,shutil
from collections import Counter
from pathlib import Path
import cv2,numpy as np
import paths
from board_connection import FloorBackend
from corridor_decision import CorridorDecision
from tactile_backend import TactileBackend
from path_policy import PathPolicy
from verify_board import verify_backend


HEALTH="""import pathlib,json,shutil,subprocess,re
mounts={r.split()[1]:r.split()[3] for r in pathlib.Path('/proc/mounts').read_text().splitlines() if r.split()[1] in ['/','/dev/shm']}
temperature={p.parent.name:p.read_text().strip() for p in pathlib.Path('/sys/class/thermal').glob('thermal_zone*/temp')}
kernel=subprocess.run(['sudo','-n','dmesg','--color=never'],capture_output=True,text=True,timeout=5)
errors=[line for line in kernel.stdout.splitlines() if re.search(r'mmcblk[0-9]+:.*(recovery failed|error)|I/O error, dev mmc|Buffer I/O error.*mmc|EXT4-fs.*(error|aborted)|mmc[0-9]+:.*(timeout|timed out|error -)',line,re.I)]
print(json.dumps(dict(boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),mounts=mounts,ram_free=shutil.disk_usage('/dev/shm').free,ext4={p.parent.name:p.read_text().strip() for p in pathlib.Path('/sys/fs/ext4').glob('*/errors_count')},temperature=temperature,meminfo=pathlib.Path('/proc/meminfo').read_text(),kernel_query_code=kernel.returncode,storage_errors=errors)))
"""


def health(backend):
    import shlex
    result=backend.run(backend.transport,'python3 -c '+shlex.quote(HEALTH),timeout=5)
    if result['code']!=0:raise RuntimeError(result)
    value=json.loads(result['stdout'])
    if set(value['mounts'])!={'/','/dev/shm'} or not value['ext4'] or any('rw' not in flags.split(',') for flags in value['mounts'].values()) or any(v!='0' for v in value['ext4'].values()) or value['kernel_query_code']!=0 or value['storage_errors']:
        raise RuntimeError('X5 storage health changed: '+json.dumps(value))
    return value


def fixtures(out):
    rows=json.loads((paths.BASE/'runs/compile_01/inputs.json').read_text(encoding='utf-8'))['samples']
    selected=[paths.ROOT/next(r['source'] for r in rows if r['sample_id']==sid) for sid in ['A01_02','A02_02','A03_02','A04_02']]
    selected += sorted((paths.HERE.parent/'blindpath-guideTWSI-x5-20260911/calibration').glob('*.jpg'))[::8][:4]
    (out/'inputs').mkdir();items=[]
    for index,source in enumerate(selected):
        image=cv2.imdecode(np.frombuffer(source.read_bytes(),np.uint8),1)
        h,w=image.shape[:2];scale=min(640/w,480/h);resized=cv2.resize(image,(round(w*scale),round(h*scale)))
        canvas=np.full((480,640,3),127,np.uint8);y=(480-resized.shape[0])//2;x=(640-resized.shape[1])//2
        canvas[y:y+resized.shape[0],x:x+resized.shape[1]]=resized
        data=cv2.imencode('.jpg',canvas)[1].tobytes();(out/'inputs'/('%02d.jpg'%index)).write_bytes(data)
        items.append(dict(id=index,jpeg=data,source=str(source.relative_to(paths.ROOT)),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),jpeg_sha256=hashlib.sha256(data).hexdigest()))
    (out/'inputs.json').write_text(json.dumps([{k:v for k,v in row.items() if k!='jpeg'} for row in items],indent=2),encoding='utf-8')
    return items


def phase(name,seconds,floor,tactile,items,out,reference):
    started=time.monotonic();next_floor=started;next_path=started;last_result=None;rows=[];paths_rows=[];resources=[]
    hashes={};gate=CorridorDecision();path_gate=PathPolicy();last_health=-1e9
    with (out/(name+'.jsonl')).open('w',encoding='utf-8') as log:
        while time.monotonic()-started<seconds:
            time.sleep(max(0,next_floor-time.monotonic()));received=time.monotonic();item=items[len(rows)%len(items)]
            # Latest-frame cadence: missed releases are dropped, never queued.
            next_floor=max(next_floor+1/15,received)
            before=time.perf_counter();category,timing=floor.infer(item['jpeg'],len(rows))
            result=gate.update(category,received,time.monotonic(),name);completed=time.monotonic()
            digest=hashlib.sha256(category.tobytes()).hexdigest();hashes.setdefault(item['id'],digest)
            row=dict(type='floor',fixture_id=item['id'],floor_rpc_and_decision_ms=(time.perf_counter()-before)*1000,
                     update_gap_ms=None if last_result is None else (completed-last_result)*1000,result=result,timing=timing,
                     category_sha256=digest,baseline_equal=reference.get(item['id'],digest)==digest)
            last_result=completed;rows.append(row);log.write(json.dumps(row)+'\n');log.flush()
            if tactile is not None and completed>=next_path:
                next_path=completed+1/3
                mask,path_timing=tactile.infer(item['jpeg'],len(paths_rows))
                path=path_gate.update(mask,received,time.monotonic(),name)
                path_row=dict(type='tactile',fixture_id=item['id'],age_ms=(time.monotonic()-received)*1000,
                              path=path,timing=path_timing,pixels=int(mask.sum()))
                paths_rows.append(path_row);log.write(json.dumps(path_row)+'\n');log.flush()
                if len(paths_rows)<=len(items):
                    image=cv2.imdecode(np.frombuffer(item['jpeg'],np.uint8),1)
                    full=cv2.resize(mask,(640,480),interpolation=cv2.INTER_NEAREST)>0
                    image[full]=(image[full]*.45+np.array([0,200,220])*.55).astype(np.uint8)
                    cv2.imencode('.jpg',image)[1].tofile(out/('tactile_%02d.jpg'%len(paths_rows)))
            if completed-last_health>=10:resources.append(dict(elapsed_s=completed-started,health=health(floor)));last_health=completed
    values=lambda key:[r[key] for r in rows if r[key] is not None]
    return dict(seconds=time.monotonic()-started,floor_results=len(rows),path_results=len(paths_rows),
        floor_rpc_decision_p95_ms=float(np.percentile(values('floor_rpc_and_decision_ms'),95)),
        floor_update_gap_p95_ms=float(np.percentile(values('update_gap_ms'),95)),
        floor_update_gap_max_ms=max(values('update_gap_ms')),floor_hashes_all_equal=all(r['baseline_equal'] for r in rows),
        floor_states=dict(Counter(r['result']['status'] for r in rows)),
        path_positive_results=sum(r['pixels']>0 for r in paths_rows),
        path_predict_prepost_p95_ms=float(np.percentile([r['timing']['predict_including_prepost_ms'] for r in paths_rows],95)) if paths_rows else None,
        resource_samples=resources),hashes


def main():
    import shlex
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=90);parser.add_argument('--baseline-seconds',type=int,default=30);args=parser.parse_args()
    if not 10<=args.seconds<=600 or not 10<=args.baseline_seconds<=120:parser.error('bounded seconds only')
    password=input();cv2.setNumThreads(1)
    out=paths.HERE/'runs'/('board_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    snapshot=out/'source_snapshot';snapshot.mkdir()
    for source in paths.HERE.glob('*.py'):shutil.copyfile(source,snapshot/source.name)
    (snapshot/'runtime_utils').mkdir()
    for source in (paths.HERE/'runtime_utils').glob('*.py'):shutil.copyfile(source,snapshot/'runtime_utils'/source.name)
    report=dict(scope='Cached original-derived JPEGs; not ESP transport, not sensor latency, no voice or motor commands',baseline_guard=paths.verify_baseline(),output=str(out))
    floor=None;tactile=None
    try:
        items=fixtures(out);floor=FloorBackend(password);report['floor_backend']=floor.metadata;report['health_before']=health(floor)
        report['fixed32']=verify_backend(floor,out/'fixed32')
        baseline,hashes=phase('floor_only',args.baseline_seconds,floor,None,items,out,{})
        report['floor_only']=baseline
        tactile=TactileBackend(password);password=None;report['tactile_backend']=tactile.metadata
        # Real positive images warm the full mask decoder, not only empty outputs.
        probes=[]
        for index,item in enumerate(items):
            mask,timing=tactile.infer(item['jpeg'],index);probes.append(dict(fixture_id=item['id'],pixels=int(mask.sum()),timing=timing))
        report['tactile_probe']=probes
        if not any(r['pixels'] for r in probes[4:]):raise RuntimeError('No positive tactile output on saved positive probe images')
        report['joint'],_=phase('joint',args.seconds,floor,tactile,items,out,hashes)
        report['health_after']=health(floor)
        joint=report['joint'];report['cached_timing_gate']=dict(
            same_floor_outputs=joint['floor_hashes_all_equal'],
            floor_compute_p95_le120=joint['floor_rpc_decision_p95_ms']<=120,
            floor_compute_p95_increase_le10=joint['floor_rpc_decision_p95_ms']-baseline['floor_rpc_decision_p95_ms']<=10,
            floor_update_gap_p95_le200=joint['floor_update_gap_p95_ms']<=200,
            no_gap_over500=joint['floor_update_gap_max_ms']<=500)
        report['error']=None
    except Exception as exc:report['error']=repr(exc)
    finally:
        if tactile:tactile.close();report['tactile_cleanup']=getattr(tactile,'cleanup',None)
        if floor:floor.close();report['floor_cleanup']=getattr(floor,'cleanup',None)
        report['baseline_guard_after']=paths.verify_baseline()
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),error=report.get('error'),gate=report.get('cached_timing_gate')),ensure_ascii=True))
    if report.get('error'):raise SystemExit(1)


if __name__=='__main__':main()
