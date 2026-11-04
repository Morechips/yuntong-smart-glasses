"""No physical outputs: prove both schedulers continue beyond three cues."""
import io,json,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
import paths
import live_joint
import guidance_output
import speech_output


class ContinuousTests(unittest.TestCase):
    def test_runtime_defaults_have_no_duration_or_count_caps(self):
        args=live_joint.parse_args([])
        self.assertEqual((args.seconds,args.max_pulses,args.max_utterances),(0,0,0))
        self.assertFalse(args.speech);self.assertFalse(args.haptics)
        timed=live_joint.parse_args(['--seconds','120','--max-pulses','1'])
        self.assertEqual((timed.seconds,timed.max_pulses),(120,1))

    def test_guidance_repeats_past_three_until_stop(self):
        stop=threading.Event();events=[];clock=[1000.];holder=[]
        class Opener:
            def open(self,*args,**kwargs):
                clock[0]+=2
                return io.StringIO(json.dumps(dict(ok=True,final_level=0,channel='left',pulse_ms=300)))
        def update():
            output=holder[0];now=clock[0]
            output.update_floor(dict(status='NO_CANDIDATE'),now,'same')
            output.update_path(dict(status='CONFIRMED',direction='LEFT',deviation_level='MEDIUM',offset=-.4),now,'same')
        def log(row):
            events.append(row)
            if len(events)>=5:stop.set()
            else:update()
        with patch.object(guidance_output,'time',SimpleNamespace(monotonic=lambda:clock[0])),patch('urllib.request.build_opener',return_value=Opener()):
            output=guidance_output.GuidanceOutput('http://test-only.invalid',stop,log,threading.Lock(),True,True,True)
            holder.append(output);update();output.thread.join(2)
            stop.set();output.thread.join(1)
        self.assertEqual(len(events),5);self.assertTrue(output.enabled);self.assertFalse(output.thread.is_alive())

    def test_speech_repeats_past_three_until_stop(self):
        stop=threading.Event();events=[];clock=[1000.];holder=[]
        class Opener:
            def open(self,request,timeout):
                clock[0]+=7
                return io.StringIO(json.dumps(dict(ok=True,codec_result=0,pcm_bytes=2,volume=25)))
        def log(row):
            events.append(row)
            if len(events)>=5:stop.set()
            else:holder[0].update(dict(status='OBSTACLE',direction='CENTER'),clock[0])
        clips={'CENTER':dict(pcm=b'\x00\x00',duration_s=.001,text='test-only')}
        with patch.object(speech_output,'time',SimpleNamespace(monotonic=lambda:clock[0])),patch('urllib.request.build_opener',return_value=Opener()):
            output=speech_output.SpeechOutput('http://test-only.invalid',True,stop,log,clips=clips)
            holder.append(output);output.update(dict(status='OBSTACLE',direction='CENTER'),clock[0]);output.thread.join(2)
            stop.set();output.thread.join(1)
        self.assertEqual(len(events),5);self.assertTrue(output.enabled);self.assertFalse(output.thread.is_alive())


if __name__=='__main__':unittest.main()
