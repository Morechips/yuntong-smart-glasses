"""Obstacle speech owns priority; tactile vibration has no obstacle meaning."""
import threading,time,json,urllib.request
from path_policy import correction
from haptic_profiles import PROFILE_LABELS,cue_parameters


class GuidanceOutput:
    def __init__(self,url,stop,log,output_lock,enabled=False,camera_fixed=False,mapping_confirmed=False,max_pulses=0,haptic_profile='cadence'):
        self.url=url;self.stop=stop;self.log=log;self.output_lock=output_lock;self.enabled=enabled
        if haptic_profile not in PROFILE_LABELS:raise ValueError('Unknown haptic profile')
        self.haptic_profile=haptic_profile;self.camera_fixed=camera_fixed;self.mapping_confirmed=mapping_confirmed
        self.lock=threading.Lock();self.floor=None;self.path=None;self.sent=0;self.max_pulses=max_pulses;self.last_sent=-1e9
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def update_floor(self,result,received,key):
        with self.lock:self.floor=(dict(result),received,key)
    def update_path(self,result,received,key):
        with self.lock:self.path=(dict(result),received,key)
    def clear(self):
        with self.lock:self.path=None;self.floor=None
    def _run(self):
        if not self.enabled:return
        while not self.stop.wait(.04):
            if not self.output_lock.acquire(blocking=False):continue
            try:
                with self.lock:
                    if not self.floor or not self.path or self.stop.is_set():continue
                    floor,fr,fk=self.floor;path,pr,pk=self.path
                    now=time.monotonic()
                    channel,reason=correction(path,pr,floor,fr,now,self.camera_fixed,self.mapping_confirmed,fk==pk)
                    parameters=cue_parameters(path,self.haptic_profile) if channel is not None else None
                    if parameters is None or now-self.last_sent<parameters['period_s']:continue
                    self.last_sent=now
                    request=urllib.request.Request(self.url+'/motor?channel='+channel+'&ms='+str(parameters['pulse_ms']),data=b'',method='POST')
                with self.opener.open(request,timeout=1.5) as response:reply=json.load(response)
                if reply.get('ok') is not True or reply.get('final_level')!=0 or reply.get('channel')!=channel or reply.get('pulse_ms')!=parameters['pulse_ms']:
                    raise ValueError('Pulse channel/duration/off acknowledgment mismatch')
                self.sent+=1;self.log(dict(type='haptic',meaning='correct_toward_image_path',channel=channel,reason=reason,reply=reply,
                                          **parameters,cue_started_pc_monotonic=now,
                                          floor_request_id=floor.get('request_id'),path_request_id=path.get('request_id'),cue_started_path_age_ms=(now-pr)*1000))
                if self.max_pulses and self.sent>=self.max_pulses:self.enabled=False;return
            except Exception as exc:self.log(dict(type='haptic_error',error=str(exc)));self.enabled=False;return
            finally:self.output_lock.release()
