"""Bounded experimental live joint run; silent unless explicitly requested."""
import argparse,json,threading,time,uuid
import urllib.request
from collections import deque,Counter
from urllib.parse import urlsplit
import cv2,numpy as np
import paths
from board_connection import FloorBackend
from tactile_backend import TactileBackend
from corridor_decision import CorridorDecision
from demo_core import render
from speech_output import SpeechOutput
from run_storage import RunStorage
from verify_board import verify_backend
from x5_camera_link import X5CameraLink
import sys
sys.path.insert(0,str(paths.ROOT/'hardware_test/esp_dataset_capture'))
from resilient_stream import ResilientStream
from path_policy import PathPolicy,correction
from guidance_output import GuidanceOutput
from check_joint_board import health


class StopRequested(Exception):pass


def parse_args(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=60);parser.add_argument('--url',help='ESP on PC hotspot; omit to find ESP on RDK WLAN')
    parser.add_argument('--speech',action='store_true');parser.add_argument('--haptics',action='store_true')
    parser.add_argument('--camera-fixed',action='store_true');parser.add_argument('--mapping-confirmed',action='store_true')
    parser.add_argument('--max-pulses',type=int,default=3);parser.add_argument('--max-utterances',type=int,default=3)
    parser.add_argument('--tactile-off',action='store_true');args=parser.parse_args(argv)
    if not 10<=args.seconds<=120:parser.error('First-stage live checks are bounded to 10..120 seconds')
    if args.haptics and (not args.camera_fixed or not args.mapping_confirmed):parser.error('Guidance needs camera and wearing-side confirmation')
    if not 1<=args.max_pulses<=3 or not 1<=args.max_utterances<=3:parser.error('Physical feedback checks limited to 1..3')
    return args


def run_joint(args,password,stop=None,on_frame=None):
    cv2.setNumThreads(1);guard=paths.verify_baseline()
    out=paths.HERE/'runs'/('live_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    storage=RunStorage(out);log_lock=threading.Lock();stop=stop if stop is not None else threading.Event();latest_lock=threading.Lock();latest=[None]
    floor=tactile=link=stream=speech=guidance=None;error=None;tactile_error=None
    gaps=deque(maxlen=2048);path_times=deque(maxlen=2048);path_states=Counter();last_complete=None;last_frame=None;last_path=None
    path_received=None;path_key=None;last_path_picture=None;snapshots=0;last_snapshot=-1e9;last_checkpoint=-1e9
    last_view=-1e9;last_health=-1e9;health_error=None;last_health_result=None
    def check_stop():
        if stop.is_set():raise StopRequested()
    def log(row):
        with log_lock:storage.add(dict(row,pc_monotonic=time.monotonic()))
    try:
        check_stop()
        log(dict(type='configuration',speech=args.speech,haptics=args.haptics,obstacle_motor=False,speech_volume=55,pulse_ms=500,
                 camera_fixed=args.camera_fixed,mapping_confirmed=args.mapping_confirmed,seconds=args.seconds))
        floor=FloorBackend(password);log(dict(type='floor_backend',metadata=floor.metadata))
        check_stop()
        report=verify_backend(floor,out/'floor_startup',sample_ids=['A01_02','A02_02'])
        if report['min_pixel_agreement']!=1 or not report['candidate_contract_equal']:raise RuntimeError('Floor BIN output changed')
        check_stop()
        if not args.tactile_off:
            tactile=TactileBackend(password);log(dict(type='tactile_backend',metadata=tactile.metadata))
            check_stop()
            probe=(paths.HERE.parent/'blindpath-guideTWSI-x5-20260911/calibration/teaser_0206.jpg').read_bytes()
            for i in range(2):tactile.infer(probe,-10-i)
        password=None
        if args.url:camera_url=args.url;peripheral_url='http://'+urlsplit(args.url).hostname+':8081'
        else:link=X5CameraLink(floor);camera_url=link.camera_url;peripheral_url=link.peripheral_url;log(dict(type='camera_route',esp_host=link.host))
        check_stop()
        if args.speech or args.haptics:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(peripheral_url+'/state',timeout=2) as response:esp_state=json.load(response)
            if args.haptics and (esp_state.get('motor_enabled') is not True or esp_state.get('motor_cycle_enabled') is not False):raise RuntimeError('ESP motor capability or cycle state invalid')
            if args.speech and (esp_state.get('speaker_volume_query') is not True or esp_state.get('speaker_volume_max',-1)<55):raise RuntimeError('ESP does not confirm volume55 support')
            log(dict(type='esp_capability',state=esp_state))
        output_lock=threading.Lock()
        speech=SpeechOutput(peripheral_url,args.speech,stop,log,output_lock,max_utterances=args.max_utterances,volume=55)
        guidance=GuidanceOutput(peripheral_url,stop,log,output_lock,enabled=args.haptics,camera_fixed=args.camera_fixed,
                                mapping_confirmed=args.mapping_confirmed,max_pulses=args.max_pulses,pulse_ms=500)
        def arrived(jpeg,headers,received):
            with latest_lock:latest[0]=(jpeg,headers,received)
        stream=ResilientStream(camera_url,lambda state,details:log(dict(type='camera_state',state=state,**details)))
        stream.subscribe(arrived);stream.start();started=time.monotonic();next_path=started;request_id=0;floor_gate=CorridorDecision();path_gate=PathPolicy()
        while not stop.is_set() and time.monotonic()-started<args.seconds:
            if time.monotonic()-last_health>=10:
                last_health=time.monotonic()
                if hasattr(floor,'transport'):
                    try:last_health_result=health(floor);log(dict(type='board_health',health=last_health_result))
                    except Exception as exc:health_error=repr(exc);raise
            with latest_lock:item=latest[0]
            if item is None or item[2]==last_frame:
                if stream.done.is_set():raise RuntimeError(stream.error or 'camera ended')
                if not stream.active_generation or last_frame is None or time.monotonic()-last_frame>.5:
                    speech.clear();guidance.clear();floor_gate.reset();path_gate.reset();last_path=None
                stop.wait(.01);continue
            jpeg,headers,received=item;last_frame=received
            if time.monotonic()-received>.5:speech.clear();guidance.clear();continue
            request_id+=1;category,timing=floor.infer(jpeg,request_id)
            if not stream.is_current(headers.get('x-demo-connection')):speech.clear();guidance.clear();floor_gate.reset();path_gate.reset();continue
            key=(headers.get('x-device-boot-id'),headers.get('x-stream-id'),headers.get('x-demo-connection'))
            result=floor_gate.update(category,received,time.monotonic(),key);result['request_id']=request_id
            completed=time.monotonic()
            if last_complete is not None:gaps.append((completed-last_complete)*1000)
            last_complete=completed
            # Block correction on the new floor state before publishing speech;
            # both happen BEFORE optional tactile work.
            guidance.update_floor(result,received,key);speech.update(result,received)
            log(dict(type='result',result=result,timing=timing,headers=headers))
            if tactile is not None and completed>=next_path:
                next_path=completed+1/3
                try:
                    before=time.perf_counter();mask,pt=tactile.infer(jpeg,request_id);path_times.append((time.perf_counter()-before)*1000)
                    if not stream.is_current(headers.get('x-demo-connection')):guidance.clear();path_gate.reset();continue
                    last_path=path_gate.update(mask,received,time.monotonic(),key);last_path['request_id']=request_id
                    path_received=received;path_key=key;guidance.update_path(last_path,received,key);path_states[last_path['status']]+=1
                    log(dict(type='path',path=last_path,timing=pt,headers=headers,received=received))
                    picture=cv2.imdecode(np.frombuffer(jpeg,np.uint8),1);picture=cv2.resize(picture,(640,480))
                    mask_full=cv2.resize(mask,(640,480),interpolation=cv2.INTER_NEAREST)>0
                    picture[mask_full]=(picture[mask_full]*.4+np.array([0,200,220])*.6).astype(np.uint8);last_path_picture=picture
                    if path_times[-1]>200:
                        tactile_error='Tactile exceeded 200ms experimental task budget; branch disabled'
                        log(dict(type='tactile_disabled',reason=tactile_error));tactile.close();tactile=None;guidance.clear()
                except Exception as exc:
                    tactile_error=repr(exc);log(dict(type='tactile_error',error=tactile_error));guidance.clear();path_gate.reset()
                    if tactile:tactile.close();tactile=None
            if completed-last_snapshot>=5 or (on_frame is not None and completed-last_view>=.1):
                last_view=completed
                left=render(cv2.imdecode(np.frombuffer(jpeg,np.uint8),1),category,result,floor.name)
                right=np.zeros_like(left)
                if last_path_picture is not None and path_received is not None and time.monotonic()-path_received<=.5 and path_key==key:
                    right[:480]=last_path_picture
                    label='TACTILE source frame '+str(last_path['request_id'])+' '+last_path['status']+' '+str(last_path.get('direction'))
                else:label='TACTILE UNKNOWN / expired'
                cv2.putText(right,label,(10,515),cv2.FONT_HERSHEY_SIMPLEX,.5,(230,230,230),1)
                combined=np.hstack([left,right])
                if completed-last_snapshot>=5:
                    snapshots=snapshots%12+1;last_snapshot=completed
                    cv2.imencode('.jpg',combined)[1].tofile(out/('preview_%02d.jpg'%snapshots))
                if on_frame is not None:on_frame(combined,dict(floor=result,path=last_path,floor_received=received,path_received=path_received,elapsed_s=completed-started,output=str(out)))
            if completed-last_checkpoint>=5:
                last_checkpoint=completed
                with log_lock:storage.checkpoint(active=True,elapsed_s=completed-started,path_states=dict(path_states),tactile_error=tactile_error,camera_reconnects=stream.reconnects)
    except StopRequested:log(dict(type='user_stop'))
    except Exception as exc:error=repr(exc);log(dict(type='error',error=error))
    finally:
        stop.set()
        if speech:speech.clear();speech.thread.join(8)
        if guidance:guidance.clear();guidance.thread.join(2)
        if stream:stream.stop();stream.thread.join(7)
        if floor is not None and hasattr(floor,'transport'):
            try:last_health_result=health(floor)
            except Exception as exc:health_error=repr(exc);error=error or health_error
        cleanup_errors=[]
        for label,resource in [('camera_link',link),('tactile',tactile),('floor',floor)]:
            if resource:
                try:resource.close()
                except Exception as exc:cleanup_errors.append(dict(resource=label,error=repr(exc)));error=error or repr(exc)
        summary=dict(error=error,tactile_error=tactile_error,seconds=args.seconds,scope='Development live check, not wearable navigation acceptance',
                     health_error=health_error,health_after=last_health_result,viewer_enabled=on_frame is not None,
                     camera_received=stream.received if stream else 0,camera_reconnects=stream.reconnects if stream else 0,
                     floor_cleanup=getattr(floor,'cleanup',None),tactile_cleanup=getattr(tactile,'cleanup',None),
                     speech_acknowledgments=storage.counts['speech'],cleanup_errors=cleanup_errors,
                     path_states=dict(path_states),floor_update_gap_p95_ms=float(np.percentile(gaps,95)) if gaps else None,
                     floor_update_gap_max_ms=max(gaps) if gaps else None,tactile_rpc_p95_ms=float(np.percentile(path_times,95)) if path_times else None,
                     baseline_guard=guard,baseline_guard_after=paths.verify_baseline(),**storage.statistics())
        with log_lock:storage.checkpoint(active=False,error=error)
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');storage.close()
    print(json.dumps(dict(output=str(out),error=error,results=summary['results'],path_states=dict(path_states))))
    return summary,out


def main():
    summary,_=run_joint(parse_args(),input())
    if summary['error']:raise SystemExit(1)


if __name__=='__main__':main()
