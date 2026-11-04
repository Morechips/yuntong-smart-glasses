"""Hidden widgets with a fake worker; no desktop automation or hardware."""
import threading,time,unittest
from unittest.mock import patch
import joint_app
from haptic_profiles import PROFILE_LABELS


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.root=joint_app.tk.Tk();self.root.withdraw();self.app=joint_app.JointApp(self.root,password='test-only')
    def tearDown(self):
        self.app.stop.set()
        if self.app.worker:self.app.worker.join(1)
        self.root.destroy()
    def pump(self,condition):
        end=time.monotonic()+1
        while time.monotonic()<end:
            self.root.update()
            if condition():return
            time.sleep(.01)
        raise AssertionError('Viewer did not reach expected state')

    def test_default_a_continuous_idle_and_outputs_off(self):
        self.assertEqual(self.app.selected_profile(),'cadence');self.assertEqual(self.app.duration.get(),'持续')
        self.assertIsNone(self.app.worker);self.assertFalse(self.app.speech.get());self.assertFalse(self.app.haptics.get())

    def test_b_a_and_legacy_locked_during_run_and_switchable_after_stop(self):
        captured=[]
        def fake_run(args,password,stop,on_frame):
            captured.append(args);stop.wait(2);return {'error':None},'TEST_ONLY'
        with patch.object(joint_app.live_joint,'run_joint',fake_run):
            for key in ('cadence_duration','cadence','legacy'):
                self.app.haptic_profile.set(PROFILE_LABELS[key]);self.app.start()
                self.pump(lambda:captured and captured[-1].haptic_profile==key)
                self.assertTrue(all(str(c['state'])=='disabled' for c in self.app.controls))
                self.assertEqual((captured[-1].seconds,captured[-1].max_pulses,captured[-1].max_utterances),(0,0,0))
                self.app.stop_run()
                self.pump(lambda:str(self.app.start_button['state'])=='normal' and not self.app.worker.is_alive())
                self.assertTrue(all(str(c['state'])!='disabled' for c in self.app.controls))
        self.assertEqual([a.haptic_profile for a in captured],['cadence_duration','cadence','legacy'])

    def test_labels_distinguish_center_pending_unknown_and_three_levels(self):
        self.assertEqual(joint_app.path_level_text(dict(status='CONFIRMED',direction='CENTER',deviation_level='NONE')),'居中')
        self.assertEqual(joint_app.path_level_text(dict(status='PENDING',direction='RIGHT',deviation_level='NONE')),'待确认')
        self.assertEqual(joint_app.path_level_text(dict(status='UNKNOWN')),'未知')
        for level,label in [('LOW','轻微'),('MEDIUM','中等'),('HIGH','明显')]:
            self.assertEqual(joint_app.path_level_text(dict(status='CONFIRMED',direction='LEFT',deviation_level=level)),label)


if __name__=='__main__':unittest.main()
