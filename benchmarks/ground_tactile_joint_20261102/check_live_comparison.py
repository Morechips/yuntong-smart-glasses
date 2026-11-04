"""Bounded silent floor/joint/floor test; no firmware or network changes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
import live_joint
import paths


def gates(summary):
    trace=summary.get('transport_trace') or {}
    source_max=trace.get('source_stamp_age_upper_max_ms')
    return dict(runtime_no_error=summary.get('error') is None and summary.get('tactile_error') is None,
        results_present=summary.get('results',0)>0,
        receive_decision_p95_le120=summary.get('receive_to_result_p95_ms') is not None and summary['receive_to_result_p95_ms']<=120,
        result_update_no_gap_over500=summary.get('floor_update_gap_run_max_ms') is not None and summary['floor_update_gap_run_max_ms']<=500,
        transport_trace_available=bool(trace) and not summary.get('transport_trace_error'),
        source_headers_valid=bool(trace) and trace.get('source_stamp_invalid')==0,
        trace_covers_all_received=bool(trace) and trace.get('frames')==trace.get('frames_total') and trace.get('frames',0)>0,
        source_age_upper_max_le500=source_max is not None and source_max<=500,
        receive_no_gap_over500=bool(trace) and trace.get('receive_gaps_over500')==0,
        no_camera_reconnect=summary.get('camera_reconnects')==0,
        silent_no_motor=summary.get('speech_acknowledgments')==0 and summary.get('haptic_acknowledgments')==0)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-seconds',type=int,default=45)
    parser.add_argument('--seconds',type=int,default=120)
    args=parser.parse_args()
    if not 10<=args.baseline_seconds<=120 or not 10<=args.seconds<=120:
        parser.error('Each phase must be 10..120 seconds')
    password=input()
    out=paths.HERE/'runs'/('live_compare_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    out.mkdir();snapshot=out/'source_snapshot';snapshot.mkdir()
    for source in paths.HERE.glob('*.py'):shutil.copyfile(source,snapshot/source.name)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.iterdir()}
    report=dict(scope='Consecutive real-stream phases, not identical frames; no physical outputs, firmware or network changes',
                baseline_guard=paths.verify_baseline(),source_snapshot_sha256=hashes,phases=[])
    def save():
        temp=out/'report.json.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(out/'report.json')
    save()
    for name,seconds,tactile_off in [('floor_before',args.baseline_seconds,True),('joint',args.seconds,False),('floor_after',args.baseline_seconds,True)]:
        argv=['--seconds',str(seconds),'--trace-transport']+(['--tactile-off'] if tactile_off else [])
        summary,directory=live_joint.run_joint(live_joint.parse_args(argv),password)
        phase=dict(name=name,directory=str(directory.relative_to(paths.ROOT)),summary=summary,gates=gates(summary))
        report['phases'].append(phase);save()
        print(json.dumps(dict(phase=name,output=str(directory),gates=phase['gates']),ensure_ascii=False),flush=True)
        if summary.get('error') or summary.get('health_error') or summary.get('cleanup_errors'):
            report['stopped_reason']='Runtime, storage, or cleanup error; no additional phases';break
    password=None
    if len(report['phases'])==3:
        before,joint,after=[r['summary'].get('receive_to_result_p95_ms') for r in report['phases']]
        if None not in (before,joint,after):
            report['live_timing_comparison']=dict(floor_before_p95_ms=before,joint_p95_ms=joint,floor_after_p95_ms=after,
                joint_minus_baseline_mean_ms=joint-(before+after)/2,baseline_drift_ms=abs(after-before),
                scope='Different consecutive frames; descriptive, not same-input model-overhead proof')
    report['all_live_phase_gates_passed']=len(report['phases'])==3 and all(all(r['gates'].values()) for r in report['phases'])
    report['baseline_guard_after']=paths.verify_baseline();save()
    print(json.dumps(dict(output=str(out),all_live_phase_gates_passed=report['all_live_phase_gates_passed']),ensure_ascii=False),flush=True)
    if not report['all_live_phase_gates_passed']:raise SystemExit(2)


if __name__=='__main__':main()
