"""Candidate cue patterns. Period is measured from pulse start to pulse start."""
import math

PROFILE_LABELS={
    'cadence':'A：300ms定长，只变快慢',
    'cadence_duration':'B：长短和快慢一起变',
    'legacy':'回退：500ms，每1.5秒一次',
}
LEVEL_LABELS={'NONE':'居中','LOW':'轻微','MEDIUM':'中等','HIGH':'明显'}
PERIODS={'LOW':3.,'MEDIUM':1.5,'HIGH':1.}
DURATIONS={'cadence':{'LOW':300,'MEDIUM':300,'HIGH':300},
           'cadence_duration':{'LOW':200,'MEDIUM':350,'HIGH':500}}


def pulse_parameters(level,profile='cadence'):
    if profile not in PROFILE_LABELS:raise ValueError('Unknown haptic profile')
    if level not in PERIODS:return None
    if profile=='legacy':return dict(pulse_ms=500,period_s=1.5)
    return dict(pulse_ms=DURATIONS[profile][level],period_s=PERIODS[level])


def cue_parameters(path,profile='cadence'):
    offset=path.get('offset')
    if not isinstance(offset,(int,float)) or not math.isfinite(offset):return None
    parameters=pulse_parameters(path.get('deviation_level'),profile)
    return dict(parameters,abs_offset=abs(offset),deviation_level=path['deviation_level'],haptic_profile=profile) if parameters else None
