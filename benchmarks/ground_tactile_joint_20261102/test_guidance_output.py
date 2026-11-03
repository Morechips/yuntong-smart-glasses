import io,json,threading,time,unittest
from unittest.mock import patch
from guidance_output import GuidanceOutput


class OutputTests(unittest.TestCase):
    def test_confirmed_path_drives_only_correction_channel_with_budget(self):
        calls=[]
        class Opener:
            def open(self,request,timeout):
                calls.append(request.full_url);return io.StringIO(json.dumps(dict(ok=True,final_level=0)))
        stop=threading.Event();events=[]
        with patch('urllib.request.build_opener',return_value=Opener()):
            output=GuidanceOutput('http://test-only.invalid',stop,events.append,threading.Lock(),True,True,True,max_pulses=1)
            now=time.monotonic();output.update_floor(dict(status='NO_CANDIDATE',request_id=1),now,'a');output.update_path(dict(status='CONFIRMED',direction='RIGHT',request_id=2),now,'a')
            output.thread.join(1)
        stop.set();output.thread.join(1)
        self.assertEqual(calls,['http://test-only.invalid/motor?channel=right&ms=500'])
        self.assertEqual(events[0]['meaning'],'correct_toward_image_path');self.assertFalse(output.enabled)
    def test_no_old_correction_queues_behind_speech(self):
        calls=[]
        class Opener:
            def open(self,*a,**k):calls.append(1);raise AssertionError('must not post')
        stop=threading.Event();lock=threading.Lock();lock.acquire()
        with patch('urllib.request.build_opener',return_value=Opener()):
            output=GuidanceOutput('http://test-only.invalid',stop,lambda r:None,lock,True,True,True)
            now=time.monotonic();output.update_floor(dict(status='NO_CANDIDATE'),now,'a');output.update_path(dict(status='CONFIRMED',direction='LEFT'),now,'a')
            time.sleep(.08);output.update_floor(dict(status='OBSTACLE'),time.monotonic(),'a');lock.release();time.sleep(.1)
            stop.set();output.thread.join(1)
        self.assertFalse(calls)
    def test_ambiguous_failure_is_disabled_not_retried(self):
        calls=[];events=[]
        class Opener:
            def open(self,*a,**k):calls.append(1);raise TimeoutError('ambiguous ACK')
        stop=threading.Event()
        with patch('urllib.request.build_opener',return_value=Opener()):
            output=GuidanceOutput('http://test-only.invalid',stop,events.append,threading.Lock(),True,True,True)
            now=time.monotonic();output.update_floor(dict(status='NO_CANDIDATE'),now,'a');output.update_path(dict(status='CONFIRMED',direction='LEFT'),now,'a')
            output.thread.join(1)
        stop.set();output.thread.join(1)
        self.assertEqual(len(calls),1);self.assertFalse(output.enabled);self.assertEqual(events[0]['type'],'haptic_error')


if __name__=='__main__':unittest.main()
