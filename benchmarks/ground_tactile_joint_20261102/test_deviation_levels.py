"""Boundary/state tests use exact offsets; geometry tests retain real masks."""
import unittest
from unittest.mock import patch
import path_policy


class DeviationTests(unittest.TestCase):
    def setUp(self):self.policy=path_policy.PathPolicy();self.t=0.
    def feed(self,offset,key='a',dt=.1):
        self.t+=dt
        geometry=dict(status='VISIBLE',direction=None,reason='image_space_only',offset=offset)
        with patch.object(path_policy,'geometry',return_value=geometry):
            return self.policy.update(None,self.t,self.t,key)
    def confirmed(self,offset):
        for _ in range(3):result=self.feed(offset)
        self.assertEqual(result['status'],'CONFIRMED');return result

    def test_both_sides_and_initial_levels(self):
        for sign,direction in [(-1,'LEFT'),(1,'RIGHT')]:
            for magnitude,level in [(.2,'LOW'),(.30,'MEDIUM'),(.50,'HIGH'),(.9,'HIGH')]:
                self.policy.reset();result=self.confirmed(sign*magnitude)
                self.assertEqual((result['direction'],result['deviation_level']),(direction,level))

    def test_center_and_exact_start_boundary_stay_silent(self):
        for offset in [0,.1,-.1,.16,-.16,.12,-.12]:
            self.policy.reset();result=self.confirmed(offset)
            self.assertEqual((result['direction'],result['deviation_level']),('CENTER','NONE'))

    def test_pending_side_cannot_confirm_inside_hysteresis_band(self):
        self.feed(.2)
        for _ in range(4):result=self.feed(.12)
        self.assertEqual((result['direction'],result['deviation_level']),('CENTER','NONE'))

    def test_confirmed_band_is_low_and_center_stops_immediately(self):
        self.confirmed(.7)
        result=self.feed(.12)
        self.assertEqual((result['status'],result['deviation_level']),('CONFIRMED','LOW'))
        result=self.feed(.10)
        self.assertEqual((result['direction'],result['deviation_level']),('CENTER','NONE'))

    def test_reversal_requires_opposite_start_threshold_and_three_frames(self):
        self.confirmed(.6)
        self.assertEqual(self.feed(-.12)['direction'],'CENTER')
        for expected in ['PENDING','PENDING','CONFIRMED']:
            result=self.feed(-.2);self.assertEqual(result['status'],expected)
        self.assertEqual((result['direction'],result['deviation_level']),('LEFT','LOW'))

    def test_reversal_while_far_stops_old_direction_before_confirmation(self):
        self.confirmed(.6);result=self.feed(-.6)
        self.assertEqual((result['status'],result['direction'],result['deviation_level']),('PENDING','LEFT','NONE'))

    def test_level_hysteresis_and_two_distinct_observations(self):
        self.confirmed(.2)
        for value in [.29,.31,.32,.29,.31]:self.assertEqual(self.feed(value)['deviation_level'],'LOW')
        self.assertEqual(self.feed(.34)['deviation_level'],'LOW')
        self.assertEqual(self.feed(.34)['deviation_level'],'MEDIUM')
        for value in [.29,.28,.31]:self.assertEqual(self.feed(value)['deviation_level'],'MEDIUM')
        self.assertEqual(self.feed(.26)['deviation_level'],'MEDIUM')
        self.assertEqual(self.feed(.26)['deviation_level'],'LOW')

    def test_high_boundary_and_direct_level_jumps(self):
        self.confirmed(.2);self.feed(.8)
        self.assertEqual(self.feed(.8)['deviation_level'],'HIGH')
        for value in [.49,.51,.48]:self.assertEqual(self.feed(value)['deviation_level'],'HIGH')
        self.feed(.46);self.assertEqual(self.feed(.46)['deviation_level'],'MEDIUM')
        self.feed(.8);self.feed(.8);self.feed(.2)
        self.assertEqual(self.feed(.2)['deviation_level'],'LOW')

    def test_source_change_gap_duplicate_and_stale_clear_levels(self):
        self.confirmed(.7)
        self.assertEqual(self.feed(.7,key='b')['deviation_level'],'NONE')
        self.confirmed(.7)
        self.assertEqual(self.feed(.7,dt=.6)['status'],'PENDING')
        self.confirmed(.7)
        self.assertEqual(self.policy.update(None,self.t,self.t,'a')['deviation_level'],'NONE')
        self.confirmed(.7)
        result=self.policy.update(None,self.t+1,self.t+2,'a')
        self.assertEqual((result['status'],result['deviation_level']),('UNKNOWN','NONE'))

    def test_missing_geometry_and_invalid_offset_reset(self):
        self.confirmed(.7)
        with patch.object(path_policy,'geometry',return_value=dict(status='UNKNOWN',reason='no_near_support',direction=None)):
            self.t+=.1;result=self.policy.update(None,self.t,self.t,'a')
        self.assertEqual(result['deviation_level'],'NONE')
        self.assertEqual(self.feed(.7)['status'],'PENDING')
        result=self.feed(float('nan'))
        self.assertEqual((result['status'],result['deviation_level']),('UNKNOWN','NONE'))


if __name__=='__main__':unittest.main()
