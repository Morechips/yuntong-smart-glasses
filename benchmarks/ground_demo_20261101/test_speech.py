import io,json,threading,time,unittest
from speech_output import SpeechOutput,SpeechPolicy,load_clips,play_clip,phrase_key
from live_demo import PulseOutput

def obstacle(direction):return dict(status='OBSTACLE',direction=direction)

class SpeechTests(unittest.TestCase):
    def test_adjusted_volume_requires_support_before_any_pcm(self):
        calls=[]
        class OldFirmware:
            def open(self,request,timeout):
                calls.append(request);return io.BytesIO(b'{"speaker_pa":false}')
        with self.assertRaisesRegex(ValueError,'ESP'):play_clip(OldFirmware(),'http://esp',dict(pcm=b'\0\0',duration_s=.01),40)
        self.assertEqual(calls,['http://esp/state'])
    def test_adjusted_volume_sent_and_acknowledged(self):
        calls=[]
        class NewFirmware:
            def open(self,request,timeout):
                calls.append(request)
                if isinstance(request,str):return io.BytesIO(b'{"speaker_volume_query":true,"speaker_volume_max":70}')
                return io.BytesIO(b'{"ok":true,"codec_result":0,"pcm_bytes":2,"volume":55}')
        reply=play_clip(NewFirmware(),'http://esp',dict(pcm=b'\0\0',duration_s=.01),55)
        self.assertEqual(reply['volume'],55);self.assertEqual(calls[1].full_url,'http://esp/pcm?volume=55')
    def test_mismatched_volume_ack_is_not_success(self):
        class WrongAck:
            def open(self,request,timeout):
                if isinstance(request,str):return io.BytesIO(b'{"speaker_volume_query":true,"speaker_volume_max":70}')
                return io.BytesIO(b'{"ok":true,"codec_result":0,"pcm_bytes":2,"volume":25}')
        with self.assertRaisesRegex(ValueError,'volume acknowledgment'):play_clip(WrongAck(),'http://esp',dict(pcm=b'\0\0',duration_s=.01),40)
    def test_volume_preflight_does_not_allow_expired_or_cleared_prompt(self):
        calls=[];allowed=[True]
        class SlowState:
            def open(self,request,timeout):
                calls.append(request);allowed[0]=False
                return io.BytesIO(b'{"speaker_volume_query":true,"speaker_volume_max":70}')
        self.assertIsNone(play_clip(SlowState(),'http://esp',dict(pcm=b'\0\0',duration_s=.01),40,lambda:allowed[0]))
        self.assertEqual(calls,['http://esp/state'])
    def test_invalid_volume_sends_no_network_request(self):
        class NoNetwork:
            def open(self,*args,**kwargs):raise AssertionError('Unexpected network')
        for volume in [-1,71,100,40.5,True]:
            with self.assertRaises(ValueError):play_clip(NoNetwork(),'http://esp',dict(pcm=b'\0\0',duration_s=.01),volume)
    def test_cached_prompts_are_short_pcm(self):
        clips=load_clips();self.assertEqual(len(clips),7)
        for clip in clips.values():
            self.assertLessEqual(clip['duration_s'],3.5);self.assertGreater(clip['duration_s'],0)
            self.assertEqual(len(clip['pcm'])%2,0);self.assertFalse(clip['pcm'].startswith(b'RIFF'))
    def test_combination_uses_only_confirmed_regions(self):
        result=dict(status='OBSTACLE',direction='CENTER',confirmed_regions=['LEFT','CENTER'],regions=['LEFT','CENTER','RIGHT'])
        self.assertEqual(phrase_key(result),'LEFT_CENTER')
        policy=SpeechPolicy();policy.started('CENTER',1)
        result['confirmed_regions']=['CENTER','RIGHT']
        self.assertFalse(policy.due(result,3,3));self.assertTrue(policy.due(result,7,7))
    def test_secondary_region_change_during_capability_get_cancels_old_phrase(self):
        stop=threading.Event();calls=[];lock=threading.Lock();lock.acquire()
        speech=SpeechOutput('http://esp',True,stop,lambda row:None,output_lock=lock,volume=55)
        initial=dict(status='OBSTACLE',direction='CENTER',confirmed_regions=['LEFT','CENTER'])
        latest=dict(initial,confirmed_regions=['CENTER','RIGHT'])
        class ChangedState:
            def open(self,request,timeout):
                calls.append(request);speech.update(latest,time.monotonic())
                return io.BytesIO(b'{"speaker_volume_query":true,"speaker_volume_max":70}')
        try:
            speech.update(initial,time.monotonic())
            reply=play_clip(ChangedState(),'http://esp',speech.clips['LEFT_CENTER'],55,lambda:speech._begin('CENTER','LEFT_CENTER'))
            self.assertIsNone(reply);self.assertEqual(calls,['http://esp/state'])
        finally:stop.set();lock.release();speech.thread.join(1)
    def test_no_speech_on_unknown_pending_or_stale(self):
        policy=SpeechPolicy()
        for status in ['UNKNOWN','PENDING','NO_CANDIDATE']:
            self.assertFalse(policy.due(dict(status=status,direction=None),1,1.1))
        self.assertFalse(policy.due(obstacle('LEFT'),1,1.6));self.assertFalse(policy.due(obstacle('LEFT'),2,1.9))
    def test_repeat_and_direction_change_intervals(self):
        policy=SpeechPolicy();self.assertTrue(policy.due(obstacle('LEFT'),1,1));policy.started('LEFT',1)
        self.assertFalse(policy.due(obstacle('RIGHT'),2.9,2.9));self.assertTrue(policy.due(obstacle('RIGHT'),3,3))
        self.assertFalse(policy.due(obstacle('LEFT'),6.9,6.9));self.assertTrue(policy.due(obstacle('LEFT'),7,7))
        policy.absent();self.assertTrue(policy.due(obstacle('LEFT'),3,3))
    def test_busy_output_keeps_only_latest_direction(self):
        stop=threading.Event();lock=threading.Lock();lock.acquire();calls=[]
        clips={d:dict(pcm=d.encode(),duration_s=.01) for d in ['LEFT','CENTER','RIGHT']}
        speech=SpeechOutput('http://127.0.0.1:1',True,stop,lambda r:None,lock,clips,max_utterances=1)
        class Fake:
            def open(self,request,timeout):
                calls.append(request.data)
                return io.BytesIO(json.dumps(dict(ok=True,codec_result=0,pcm_bytes=len(request.data))).encode())
        speech.opener=Fake();speech.update(obstacle('LEFT'),time.monotonic());time.sleep(.06)
        speech.update(obstacle('RIGHT'),time.monotonic());lock.release();speech.thread.join(.5);stop.set()
        self.assertEqual(calls,[b'RIGHT'])
    def test_motor_does_not_queue_after_clear_while_speech_busy(self):
        stop=threading.Event();lock=threading.Lock();lock.acquire();calls=[]
        pulse=PulseOutput('http://127.0.0.1:1',True,stop,lambda r:None,max_pulses=1,output_lock=lock)
        class Fake:
            def open(self,*args,**kwargs):calls.append(1);return io.BytesIO(b'{"ok":true,"final_level":0}')
        pulse.opener=Fake();pulse.update(obstacle('CENTER'),time.monotonic());time.sleep(.06)
        pulse.clear();lock.release();time.sleep(.08);stop.set();pulse.thread.join(.5)
        self.assertEqual(calls,[])
    def test_motor_and_speech_never_post_concurrently(self):
        stop=threading.Event();lock=threading.Lock();state_lock=threading.Lock();active=0;peak=0;calls=[]
        class Fake:
            def open(self,request,timeout):
                nonlocal active,peak
                with state_lock:active+=1;peak=max(peak,active);calls.append(request.full_url)
                time.sleep(.1)
                with state_lock:active-=1
                reply=dict(ok=True,final_level=0,codec_result=0,pcm_bytes=len(request.data))
                return io.BytesIO(json.dumps(reply).encode())
        pulse=PulseOutput('http://127.0.0.1:1',True,stop,lambda r:None,max_pulses=1,output_lock=lock)
        clips={d:dict(pcm=b'\0\0',duration_s=.01) for d in ['LEFT','CENTER','RIGHT']}
        speech=SpeechOutput('http://127.0.0.1:1',True,stop,lambda r:None,lock,clips,max_utterances=1)
        pulse.opener=Fake();speech.opener=Fake()
        now=time.monotonic();pulse.update(obstacle('CENTER'),now);speech.update(obstacle('CENTER'),now)
        pulse.thread.join(.8);speech.thread.join(.8);stop.set()
        self.assertEqual(len(calls),2);self.assertEqual(peak,1)

if __name__=='__main__':unittest.main()
