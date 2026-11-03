import unittest
import numpy as np
from path_policy import PathPolicy,geometry,correction


def lane(x=160):
    value=np.zeros((240,320),np.uint8);value[125:235,x-10:x+10]=1;return value


class PolicyTests(unittest.TestCase):
    def test_three_distinct_observations_confirm_correction_toward_path(self):
        p=PathPolicy()
        for t in [0,.2,.4]:result=p.update(lane(90),t,t+.01,'source')
        self.assertEqual(result['status'],'CONFIRMED');self.assertEqual(result['direction'],'LEFT')
        self.assertEqual(correction(result,.4,{'status':'NO_CANDIDATE'},.4,.45,True,True)[0],'left')
    def test_aligned_does_not_mean_double_vibration(self):
        path=dict(status='CONFIRMED',direction='CENTER')
        self.assertEqual(correction(path,0,{'status':'NO_CANDIDATE'},0,.1,True,True),(None,'aligned_no_pulse'))
    def test_all_obstacle_unknown_and_pending_states_block_guidance(self):
        for status in ['OBSTACLE','UNKNOWN','PENDING']:
            self.assertIsNone(correction(dict(status='CONFIRMED',direction='RIGHT'),0,dict(status=status),0,.1,True,True)[0])
    def test_mapping_and_camera_confirmation_are_required(self):
        path=dict(status='CONFIRMED',direction='RIGHT');floor=dict(status='NO_CANDIDATE')
        for camera,mapping in [(False,False),(True,False),(False,True)]:self.assertIsNone(correction(path,0,floor,0,.1,camera,mapping)[0])
    def test_stale_future_and_source_mismatch_block_guidance(self):
        path=dict(status='CONFIRMED',direction='RIGHT');floor=dict(status='NO_CANDIDATE')
        for args in [(0,0,.6,True),(1,0,.1,True),(0,1,.1,True),(0,0,.1,False)]:
            self.assertIsNone(correction(path,args[0],floor,args[1],args[2],True,True,args[3])[0])
    def test_same_cached_frame_is_not_three_confirmations(self):
        p=PathPolicy();p.update(lane(),0,0,'a');p.update(lane(),0,.01,'a')
        self.assertNotEqual(p.update(lane(),.1,.11,'a')['status'],'CONFIRMED')
    def test_source_change_and_long_gap_reset_confirmation(self):
        for key,t in [('b',.4),('a',1.0)]:
            p=PathPolicy();p.update(lane(),0,0,'a');p.update(lane(),.2,.2,'a')
            self.assertEqual(p.update(lane(),t,t,key)['status'],'PENDING')
    def test_no_near_support_and_two_disjoint_paths_are_unknown(self):
        mask=lane();mask[132:]=0
        self.assertEqual(geometry(mask)['status'],'UNKNOWN')
        self.assertEqual(geometry(lane(90)|lane(230))['reason'],'multiple_near_paths')
    def test_hysteresis_and_clear_on_empty(self):
        p=PathPolicy()
        for t in [0,.2,.4]:p.update(lane(205),t,t,'a')
        self.assertEqual(p.update(lane(180),.6,.6,'a')['direction'],'RIGHT')
        self.assertEqual(p.update(np.zeros((240,320),np.uint8),.8,.8,'a')['status'],'UNKNOWN')
        self.assertEqual(p.update(lane(180),1,1,'a')['status'],'PENDING')


if __name__=='__main__':unittest.main()
