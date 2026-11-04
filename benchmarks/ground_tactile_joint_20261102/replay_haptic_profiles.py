"""Recompute path states from recorded geometry, never actuate or infer."""
import argparse
from collections import Counter
import hashlib,json,math
from pathlib import Path
from unittest.mock import patch
import path_policy
from haptic_profiles import PROFILE_LABELS,pulse_parameters


def replay(source,output):
    raw=source.read_bytes();rows=[]
    for line in raw.decode('utf-8').splitlines():
        try:row=json.loads(line)
        except json.JSONDecodeError:continue
        if row.get('type')=='path':rows.append(row)
    gate=path_policy.PathPolicy();results=[]
    for row in rows:
        old=row['path'];offset=old.get('offset')
        visible=isinstance(offset,(int,float)) and math.isfinite(offset)
        geometry=dict(old,status='VISIBLE') if visible else dict(status='UNKNOWN',reason=old.get('reason','no_geometry'),direction=None)
        headers=row.get('headers',{});key=tuple(headers.get(k) for k in ('x-device-boot-id','x-stream-id','x-demo-connection'))
        with patch.object(path_policy,'geometry',return_value=geometry):
            result=gate.update(None,row['received'],row['pc_monotonic'],key)
        results.append(dict(received=row['received'],key=key,previous_status=old['status'],new_path=result))
    output.mkdir(parents=True,exist_ok=True)
    (output/'geometry_replay.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in results),encoding='utf-8')
    report=dict(scope='Recorded model geometry replay only; no model rerun, actuation, output-scheduler replay or accuracy claim',
        source=str(source),source_sha256=hashlib.sha256(raw).hexdigest(),recorded_path_observations=len(rows),
        recorded_observations_with_offset=sum('offset' in r['path'] for r in rows),
        new_states=dict(Counter(r['new_path']['status'] for r in results)),
        new_directions=dict(Counter(r['new_path']['direction'] for r in results)),
        new_deviation_levels=dict(Counter(r['new_path']['deviation_level'] for r in results)),
        profiles={profile:{level:pulse_parameters(level,profile) for level in ('NONE','LOW','MEDIUM','HIGH')} for profile in PROFILE_LABELS},
        real_output_requests=0)
    (output/'replay_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--events',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(replay(args.events,args.output),ensure_ascii=True))


if __name__=='__main__':main()
