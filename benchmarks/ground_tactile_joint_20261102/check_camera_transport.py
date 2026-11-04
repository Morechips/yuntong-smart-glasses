"""Read-only, no-model camera diagnostics on RDK and through the PC relay."""
import argparse
import json
import shlex
import time
import sys
from types import SimpleNamespace
import urllib.request
import uuid
import numpy as np
import paths
from board_connection import connect_checked
from rdk_io import run
from check_joint_board import health
from camera_link import X5CameraLink
sys.path.insert(0, str(paths.ROOT / 'hardware_test/esp_dataset_capture'))
from resilient_stream import ResilientStream

REMOTE = r'''
import json,time,urllib.request
host=HOST
seconds=SECONDS
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def clock():
 t0=time.monotonic()
 with opener.open('http://'+host+':8081/state',timeout=2) as r:s=json.load(r)
 t1=time.monotonic()
 return dict(t0=t0,t1=t1,clock_resolution_s=time.get_clock_info('monotonic').resolution,esp_uptime_s=s['uptime_ms']/1000,boot=s['imu_stream']['boot_id'],speaker_pa=s['speaker_pa'],motor_cycle_enabled=s['motor_cycle_enabled'])
before=clock();rows=[];started=time.monotonic();error=None
try:
 with opener.open('http://'+host+'/stream',timeout=2) as r:
  while time.monotonic()-started<seconds:
   line=r.readline()
   if not line:raise RuntimeError('camera EOF')
   if not line.startswith(b'--'):continue
   headers={}
   while True:
    line=r.readline()
    if line in (b'\r\n',b'\n'):break
    if not line:raise RuntimeError('header EOF')
    k,v=line.decode('ascii').split(':',1);headers[k.lower()]=v.strip()
   size=int(headers['content-length'])
   if not 0<size<2000000:raise ValueError('JPEG size invalid')
   data=r.read(size)
   if len(data)!=size:raise RuntimeError('JPEG truncated')
   rows.append(dict(received=time.monotonic(),headers=headers))
except Exception as exc:error=repr(exc)
print(json.dumps(dict(before=before,after=clock(),rows=rows,error=error,elapsed_s=time.monotonic()-started)))
'''


def clock(opener, url):
    t0 = time.monotonic()
    with opener.open(url + '/state', timeout=2) as response:
        state = json.load(response)
    return dict(t0=t0, t1=time.monotonic(), esp_uptime_s=state['uptime_ms'] / 1000,
                clock_resolution_s=time.get_clock_info('monotonic').resolution,
                boot=state['imu_stream']['boot_id'], speaker_pa=state['speaker_pa'], motor_cycle_enabled=state['motor_cycle_enabled'])


def statistics(data):
    rows = data['rows']
    gaps = [(b['received'] - a['received']) * 1000 for a, b in zip(rows, rows[1:])]
    before = data['before']
    # /state uptime is floored to ms; use a conservative interval rather than
    # pretending the server and receiver clocks are exactly synchronized.
    quantum=before.get('clock_resolution_s',0)
    offset_min = before['t0'] - quantum - before['esp_uptime_s'] - .001
    offset_max = before['t1'] + quantum - before['esp_uptime_s']
    lower_ages = [];upper_ages=[];invalid_stamps=0
    for row in rows:
        if row['headers'].get('x-device-boot-id') != before['boot']:
            raise RuntimeError('ESP boot changed')
        if row['headers'].get('x-frame-time-status') != 'ok':
            invalid_stamps+=1
            continue
        source = int(row['headers']['x-frame-source-us']) / 1000000
        row['source_stamp_age_lower_ms'] = (row['received'] - quantum - source - offset_max) * 1000
        row['source_stamp_age_upper_ms'] = (row['received'] + quantum - source - offset_min) * 1000
        lower_ages.append(row['source_stamp_age_lower_ms'])
        upper_ages.append(row['source_stamp_age_upper_ms'])
    return dict(frames=len(rows), receive_gap_p95_ms=float(np.percentile(gaps, 95)) if gaps else None,
                receive_gap_max_ms=max(gaps) if gaps else None, receive_gaps_over500=sum(v > 500 for v in gaps),
                source_stamp_age_lower_p95_ms=float(np.percentile(lower_ages, 95)) if lower_ages else None,
                source_stamp_age_lower_max_ms=max(lower_ages) if lower_ages else None,
                source_stamp_age_definitely_over500=sum(v > 500 for v in lower_ages),
                source_stamp_age_upper_p95_ms=float(np.percentile(upper_ages,95)) if upper_ages else None,
                source_stamp_age_upper_max_ms=max(upper_ages) if upper_ages else None,
                source_stamp_age_possibly_over500=sum(v > 500 for v in upper_ages),source_stamp_invalid=invalid_stamps,
                clock_resolution_s=quantum,clock_model='Initial offset interval, millisecond server rounding and local clock quantization; constant offset assumed',
                clock_probe_rtt_ms=(before['t1'] - before['t0']) * 1000)


def transport_gates(report):
    gates = {}
    for name in ('rdk_direct', 'pc_relay'):
        phase = report.get(name, {})
        gates[name + '_completed'] = phase.get('frames', 0) > 0 and not phase.get('error')
        gates[name + '_no_receive_gap_over500'] = phase.get('receive_gaps_over500') == 0
        gates[name + '_no_source_stamp_age_over500'] = phase.get('source_stamp_age_definitely_over500') == 0
    gates['pc_relay_no_reconnect'] = report.get('pc_relay', {}).get('reconnects') == 0
    return gates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 60:
        parser.error('10..60 seconds per phase')
    password = input()
    out = paths.HERE / 'runs' / ('camera_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    out.mkdir()
    report = dict(scope='No models, no actuator POST, no firmware/network configuration changes; two sequential camera paths',
                  baseline_guard=paths.verify_baseline())
    link = stream = None
    try:
        with connect_checked(password) as transport:
            password = None
            backend = SimpleNamespace(transport=transport, run=run)
            report['health_before'] = health(backend)
            link = X5CameraLink(backend)
            report['host'] = link.host
            code = REMOTE.replace('HOST', repr(link.host)).replace('SECONDS', str(args.seconds))
            reply = run(transport, 'python3 -c ' + shlex.quote(code), timeout=args.seconds + 12)
            if reply['code'] != 0:
                raise RuntimeError(reply)
            direct = json.loads(reply['stdout'])
            report['rdk_direct'] = dict(statistics(direct), error=direct.get('error'), elapsed_s=direct.get('elapsed_s'))
            (out / 'rdk_direct.json').write_text(json.dumps(direct), encoding='utf-8')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            relay = dict(before=clock(opener, link.peripheral_url), rows=[])
            events = []
            stream = ResilientStream(link.camera_url, lambda state, details: events.append(dict(state=state, **details)))
            stream.subscribe(lambda jpeg, headers, received: relay['rows'].append(dict(headers=headers, received=received)))
            stream.start()
            stream.done.wait(args.seconds)
            stream.stop()
            stream.thread.join(7)
            if stream.thread.is_alive():
                raise RuntimeError('camera reader did not stop')
            relay['after'] = clock(opener, link.peripheral_url)
            report['pc_relay'] = dict(statistics(relay), reconnects=stream.reconnects, events=events)
            (out / 'pc_relay.json').write_text(json.dumps(relay), encoding='utf-8')
            report['health_after'] = health(backend)
            report['transport_gates'] = transport_gates(report)
            link.close()
            link = None
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        if stream:
            stream.stop()
            stream.thread.join(7)
        if link:
            link.close()
        report['baseline_guard_after'] = paths.verify_baseline()
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(out), **{k: v for k, v in report.items() if not k.startswith('health')}), ensure_ascii=False))
    if report.get('error'):
        raise SystemExit(1)
    if not all(report['transport_gates'].values()):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
