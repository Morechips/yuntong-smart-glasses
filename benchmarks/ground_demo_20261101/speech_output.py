"""Cached short Chinese prompts on ESP; latest state only, no speech queue."""
from pathlib import Path
import hashlib,json,threading,time,urllib.request,wave
from demo_core import MAX_AGE_S

TEXTS={'LEFT':'左侧有障碍','CENTER':'前方有障碍','RIGHT':'右侧有障碍'}
TEXTS.update(LEFT_CENTER='前方和左侧有障碍',CENTER_RIGHT='前方和右侧有障碍',
             LEFT_CENTER_RIGHT='前方和两侧有障碍',LEFT_RIGHT='左右两侧有障碍')
REGIONS=('LEFT','CENTER','RIGHT')
AUDIO=Path(__file__).resolve().parent/'audio_corridor'


def phrase_key(result):
    regions=[r for r in REGIONS if r in result.get('confirmed_regions',[])]
    return '_'.join(regions) if regions else result['direction']


def load_clips(folder=AUDIO):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    expected={r['direction']:r['sha256'] for r in manifest['clips']}
    clips={}
    for direction in TEXTS:
        path=folder/(direction+'.wav')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected[direction]:raise ValueError('Speech asset changed')
        with wave.open(str(path),'rb') as source:
            if (source.getframerate(),source.getsampwidth(),source.getnchannels(),source.getcomptype())!=(24000,2,1,'NONE'):
                raise ValueError('Speech must be 24kHz LE16 mono PCM')
            count=source.getnframes()
            if not 0<count<=24000*3.5:raise ValueError('Speech clip duration outside demo contract')
            body=source.readframes(count)
            if len(body)!=count*2:raise ValueError('Speech WAV truncated')
        clips[direction]=dict(pcm=body,duration_s=count/24000,text=TEXTS[direction])
    return clips


def play_clip(opener,url,clip,volume=25,can_start=None):
    if type(volume) is not int or not 0<=volume<=70:raise ValueError('Speech volume must be an integer 0..70')
    # Old firmware silently ignores URL queries. Check support before sending
    # any loudness-adjusted audio rather than reporting a false adjustment.
    if volume!=25:
        with opener.open(url.rstrip('/')+'/state',timeout=2) as response:state=json.load(response)
        if state.get('speaker_volume_query') is not True or state.get('speaker_volume_max',-1)<volume:
            raise ValueError('ESP固件尚不支持可调音量，请先安装音量更新版')
    if can_start is not None and not can_start():return None
    request=urllib.request.Request(url.rstrip('/')+'/pcm?volume='+str(volume),data=clip['pcm'],headers={'Content-Type':'application/octet-stream'},method='POST')
    with opener.open(request,timeout=clip['duration_s']+2) as response:reply=json.load(response)
    if reply.get('ok') is not True or reply.get('codec_result')!=0 or reply.get('pcm_bytes')!=len(clip['pcm']):
        raise ValueError('ESP did not acknowledge complete speech playback')
    if reply.get('volume',25)!=volume:raise ValueError('ESP volume acknowledgment mismatch')
    return reply


class SpeechPolicy:
    def __init__(self):self.last_started=-1e9;self.last_direction=None
    def due(self,result,received,now):
        if result['status']!='OBSTACLE' or result['direction'] not in REGIONS or not 0<=now-received<=MAX_AGE_S:return False
        if now-self.last_started<2:return False
        return result['direction']!=self.last_direction or now-self.last_started>=6
    def started(self,direction,now):self.last_direction=direction;self.last_started=now
    def absent(self):self.last_direction=None


class SpeechOutput:
    def __init__(self,url,enabled,stop,log,output_lock=None,clips=None,max_utterances=0,volume=25):
        if type(volume) is not int or not 0<=volume<=70:raise ValueError('Speech volume must be an integer 0..70')
        self.volume=volume
        self.url=url;self.enabled=enabled;self.stop=stop;self.log=log
        self.output_lock=output_lock if output_lock is not None else threading.Lock()
        self.clips=(load_clips() if clips is None else clips) if enabled else {}
        self.lock=threading.Lock();self.latest=None;self.policy=SpeechPolicy();self.sent=0;self.max_utterances=max_utterances
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def update(self,result,received):
        with self.lock:
            self.latest=(dict(result),received)
            if result['status'] in ['UNKNOWN','NO_CANDIDATE']:self.policy.absent()
    def clear(self):
        with self.lock:self.latest=None;self.policy.absent()
    def _begin(self,direction,phrase):
        # Capability GET may take time. Recheck freshness immediately before PCM.
        with self.lock:
            now=time.monotonic()
            if self.stop.is_set() or not self.latest or self.latest[0]['direction']!=direction or not self.policy.due(*self.latest,now):return False
            if phrase_key(self.latest[0])!=phrase:return False
            self.policy.started(direction,now)
            self.started_context=dict(result_request_id=self.latest[0].get('request_id'),cue_started_age_ms=(now-self.latest[1])*1000)
            return True
    def _run(self):
        if not self.enabled:return
        while not self.stop.wait(.04):
            with self.lock:
                if not self.latest or not self.policy.due(*self.latest,time.monotonic()):continue
            # The ESP serves PCM and motor requests on one blocking server.
            # Never queue behind the other output; re-evaluate the freshest state.
            if not self.output_lock.acquire(blocking=False):continue
            try:
                with self.lock:
                    if self.stop.is_set() or not self.latest or not self.policy.due(*self.latest,time.monotonic()):continue
                    result,received=self.latest;direction=result['direction'];phrase=phrase_key(result)
                started=time.monotonic();reply=play_clip(self.opener,self.url,self.clips[phrase],self.volume,lambda:self._begin(direction,phrase))
                if reply is None:continue
                self.sent+=1
                self.log(dict(type='speech',direction=direction,phrase_key=phrase,text=TEXTS[phrase],audio_duration_s=self.clips[phrase]['duration_s'],volume=self.volume,
                              reply=reply,request_to_ack_ms=(time.monotonic()-started)*1000,**self.started_context))
                if self.max_utterances and self.sent>=self.max_utterances:
                    self.enabled=False;self.log(dict(type='speech_limit_reached',limit=self.max_utterances));return
            except Exception as exc:
                self.log(dict(type='speech_error',error=str(exc)));self.enabled=False;return
            finally:self.output_lock.release()
