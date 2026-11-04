"""Real scheduler threads, fake motor HTTP, deterministic monotonic time."""
import io,json,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import guidance_output
from haptic_profiles import pulse_parameters,cue_parameters


class Harness:
    def __init__(self,profile='cadence',reply_patch=None,failure=None):
        self.now=1000.;self.calls=[];self.events=[];self.stop=threading.Event();self.lock=threading.Lock()
        self.reply_patch=reply_patch or {};self.failure=failure
        self.patches=[patch.object(guidance_output,'time',SimpleNamespace(monotonic=lambda:self.now)),
                      patch('urllib.request.build_opener',return_value=self)]
        for p in self.patches:p.start()
        self.output=guidance_output.GuidanceOutput('http://test-only.invalid',self.stop,self.events.append,self.lock,True,True,True,haptic_profile=profile)
    def __enter__(self):return self
    def __exit__(self,*args):
        self.stop.set()
        if self.lock.locked():self.lock.release()
        self.output.thread.join(1)
        for p in reversed(self.patches):p.stop()
        assert not self.output.thread.is_alive()
    def open(self,request,timeout):
        query=parse_qs(urlsplit(request.full_url).query);channel=query['channel'][0];pulse=int(query['ms'][0])
        assert request.get_method()=='POST' and request.data==b''
        self.calls.append(dict(started=self.now,channel=channel,pulse_ms=pulse))
        if self.failure:raise self.failure
        self.now+=pulse/1000  # The ACK follows the motor pulse, not its start.
        return io.StringIO(json.dumps(dict(dict(ok=True,channel=channel,pulse_ms=pulse,final_level=0),**self.reply_patch)))
    def publish(self,level='HIGH',side='RIGHT',floor_status='NO_CANDIDATE',path_status='CONFIRMED',path_age=0,path_key='same'):
        magnitude={'LOW':.2,'MEDIUM':.4,'HIGH':.7,'NONE':0}[level]
        self.output.update_floor(dict(status=floor_status,request_id=1),self.now,'same')
        self.output.update_path(dict(status=path_status,direction=side,deviation_level=level,offset=magnitude if side=='RIGHT' else -magnitude,request_id=2),self.now-path_age,path_key)
    def wait_sent(self,count):
        end=time.monotonic()+.7
        while time.monotonic()<end:
            if len([r for r in self.events if r['type']=='haptic'])>=count:return
            time.sleep(.005)
        raise AssertionError(('Expected haptic ACK count',count,self.calls,self.events))
    def quiet(self):
        before=len(self.calls);time.sleep(.10);assert len(self.calls)==before,(before,self.calls)


class ProfileTests(unittest.TestCase):
    def test_exact_six_patterns_and_legacy_fallback(self):
        for profile,durations in [('cadence',[300,300,300]),('cadence_duration',[200,350,500])]:
            for level,duration,period in zip(['LOW','MEDIUM','HIGH'],durations,[3.,1.5,1.]):
                with self.subTest(profile=profile,level=level):
                    self.assertEqual(pulse_parameters(level,profile),dict(pulse_ms=duration,period_s=period))
                    with Harness(profile) as h:
                        h.publish(level);h.wait_sent(1)
                        self.assertEqual(h.calls[0]['pulse_ms'],duration)
                        event=h.events[0];self.assertEqual((event['haptic_profile'],event['deviation_level'],event['period_s']),(profile,level,period))
                        h.now=1000.+period-.01;h.publish(level);h.quiet()
                        h.now=1000.+period;h.publish(level);h.wait_sent(2)
        for level in ['LOW','MEDIUM','HIGH']:
            self.assertEqual(pulse_parameters(level,'legacy'),dict(pulse_ms=500,period_s=1.5))
        with Harness('legacy') as h:
            h.publish('LOW');h.wait_sent(1);self.assertEqual(h.calls[0]['pulse_ms'],500)

    def test_period_is_start_to_start_not_after_ack(self):
        with Harness('cadence_duration') as h:
            h.publish();h.wait_sent(1);self.assertEqual(h.now,1000.5)
            h.now=1000.99;h.publish();h.quiet()
            h.now=1001.;h.publish();h.wait_sent(2)
            self.assertEqual([c['started'] for c in h.calls],[1000.,1001.])
            self.assertEqual(h.events[1]['cue_started_pc_monotonic'],1001.)

    def test_latest_level_changes_due_time_without_queued_old_pattern(self):
        with Harness() as h:
            h.publish('LOW');h.wait_sent(1)
            h.now=1001.;h.publish('HIGH');h.wait_sent(2)
            h.now=1002.;h.publish('LOW');h.quiet()
            h.now=1004.;h.publish('LOW');h.wait_sent(3)
            self.assertEqual([e['deviation_level'] for e in h.events],['LOW','HIGH','LOW'])

    def test_left_and_right_channel_match_correction_direction(self):
        for side,channel in [('LEFT','left'),('RIGHT','right')]:
            with Harness() as h:
                h.publish(side=side);h.wait_sent(1)
                self.assertEqual(h.calls[0]['channel'],channel)
                self.assertEqual(h.events[0]['abs_offset'],.7)

    def test_center_pending_unknown_stale_mismatch_and_obstacle_are_silent(self):
        cases=[dict(level='NONE',side='CENTER'),dict(path_status='PENDING'),dict(path_status='UNKNOWN'),
               dict(path_age=.6),dict(path_key='other'),dict(floor_status='OBSTACLE'),dict(floor_status='UNKNOWN'),dict(floor_status='PENDING')]
        with Harness() as h:
            for kwargs in cases:h.publish(**kwargs);h.quiet()
            self.assertEqual(h.calls,[])

    def test_speech_lock_release_uses_latest_state_and_never_catches_up(self):
        with Harness() as h:
            h.lock.acquire();h.publish('HIGH');h.now+=30;h.publish(level='NONE',side='CENTER');h.lock.release();h.quiet()
            self.assertEqual(h.calls,[])
            h.publish('LOW');h.wait_sent(1);h.quiet()
            self.assertEqual(len(h.calls),1)

    def test_queued_state_expiring_behind_speech_is_not_sent(self):
        with Harness() as h:
            h.lock.acquire();h.publish();h.now+=1;h.lock.release();h.quiet();self.assertEqual(h.calls,[])

    def test_wrong_channel_duration_or_final_level_disables_without_retry(self):
        for reply in [dict(channel='left'),dict(pulse_ms=500),dict(final_level=1),dict(ok=False)]:
            with Harness(reply_patch=reply) as h:
                h.publish();h.output.thread.join(.7);self.assertFalse(h.output.enabled)
                self.assertEqual(len(h.calls),1);self.assertEqual(h.events[0]['type'],'haptic_error')

    def test_ambiguous_timeout_and_stop_never_add_another_request(self):
        with Harness(failure=TimeoutError('ambiguous ACK')) as h:
            h.publish();h.output.thread.join(.7);self.assertFalse(h.output.enabled);self.assertEqual(len(h.calls),1)
        with Harness() as h:
            h.publish();h.wait_sent(1);h.stop.set();h.output.thread.join(.7)
            h.now+=20;h.publish();h.quiet();self.assertEqual(len(h.calls),1)

    def test_missing_or_nonfinite_offset_and_none_level_have_no_pattern(self):
        for path in [{},dict(offset=float('nan'),deviation_level='HIGH'),dict(offset=.2,deviation_level='NONE')]:
            self.assertIsNone(cue_parameters(path))
        with self.assertRaises(ValueError):pulse_parameters('LOW','unknown')


if __name__=='__main__':unittest.main()
