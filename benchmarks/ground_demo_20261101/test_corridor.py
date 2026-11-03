import unittest
import numpy as np
from corridor_decision import CorridorDecision,LegacyDecision,estimate_axis


def floor(left=24,right=296):
    m=np.zeros((240,320),np.uint8);m[100:,left:right]=1
    return m


def run_frames(engine,mask,count=4,start=1.,key='a'):
    return [engine.update(mask,start+i*.1,start+i*.1+.001,key) for i in range(count)]


class CorridorTests(unittest.TestCase):
    def test_front_small_not_suppressed_by_large_side(self):
        m=floor();m[145:220,35:88]=0;m[165:185,150:165]=0
        old=run_frames(LegacyDecision(),m)[-1];new=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(old['direction'],'LEFT');self.assertEqual(new['direction'],'CENTER')
        self.assertEqual(set(new['confirmed_regions']),{'LEFT','CENTER'})
    def test_independent_regions_survive_alternating_largest_candidate(self):
        engine=CorridorDecision()
        for i in range(4):
            m=floor();m[160:190,145:170]=0
            if i%2:m[150:210,225:275]=0
            else:m[150:210,35:85]=0
            result=engine.update(m,1+i*.1,1.01+i*.1,'a')
            if i>=1:self.assertIn('CENTER',result['confirmed_regions'])
    def test_both_sides_without_front(self):
        m=floor();m[160:185,35:70]=0;m[175:210,250:280]=0
        result=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(set(result['confirmed_regions']),{'LEFT','RIGHT'})
        self.assertEqual(result['direction'],'RIGHT')
    def test_one_wide_candidate_can_occupy_multiple_regions(self):
        m=floor();m[170:195,60:260]=0
        result=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(len(result['boxes']),1);self.assertEqual(len(result['confirmed_regions']),3)
        self.assertEqual(result['direction'],'CENTER')
    def test_removed_region_clears_immediately(self):
        engine=CorridorDecision();m=floor();m[160:180,150:170]=0
        run_frames(engine,m);result=engine.update(floor(),1.4,1.41,'a')
        self.assertEqual(result['status'],'NO_CANDIDATE');self.assertEqual(result['confirmed_regions'],[])
    def test_axis_follows_outer_floor_not_biggest_free_gap(self):
        m=floor(70,300);m[160:190,170:205]=0
        result=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(result['corridor']['source'],'ADAPTIVE')
        self.assertAlmostEqual(result['corridor']['center_x'],184.5,delta=1.)
        self.assertEqual(result['direction'],'CENTER')
    def test_axis_warmup_then_time_smoothing(self):
        engine=CorridorDecision();results=run_frames(engine,floor(24,280),3)
        self.assertEqual(results[1]['corridor']['source'],'FIXED_FALLBACK')
        self.assertEqual(results[2]['corridor']['source'],'ADAPTIVE')
        old=results[-1]['corridor']['center_x']
        new=engine.update(floor(44,300),1.3,1.31,'a')['corridor']['center_x']
        self.assertGreater(new,old);self.assertLess(new,old+20)
    def test_tall_obstacle_rows_trigger_visible_fallback(self):
        m=floor();m[130:225,60:260]=0
        result=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(result['corridor']['source'],'FIXED_FALLBACK')
        self.assertEqual(result['corridor']['reason'],'too_few_clean_rows')
        self.assertEqual(result['direction'],'CENTER')
    def test_internal_hole_with_two_supported_flanks_keeps_outer_axis(self):
        m=floor();m[130:225,140:180]=0
        result=run_frames(CorridorDecision(),m)[-1]
        self.assertEqual(result['corridor']['source'],'ADAPTIVE')
        self.assertAlmostEqual(result['corridor']['center_x'],159.5,delta=1)
        self.assertEqual(result['direction'],'CENTER')
    def test_insufficient_longitudinal_support_rejects_axis(self):
        m=floor();m[100:200]=0
        axis,_,_=estimate_axis(m,[]);self.assertIsNone(axis)
    def test_stale_and_source_change_reset_every_region_and_axis(self):
        engine=CorridorDecision();m=floor();m[160:180,150:170]=0;run_frames(engine,m)
        stale=engine.update(m,1.4,2.,'a')
        self.assertEqual(stale['status'],'UNKNOWN');self.assertEqual(stale['confirmed_regions'],[])
        self.assertEqual(stale['corridor']['points'],[])
        new=engine.update(m,2.1,2.11,'b');self.assertEqual(new['status'],'PENDING')
        self.assertEqual(new['corridor']['source'],'FIXED_FALLBACK')
    def test_merge_split_does_not_require_instance_identity(self):
        engine=CorridorDecision();a=floor();a[170:190,130:155]=0;a[170:190,160:185]=0
        first=engine.update(a,1,1.01,'a');a[170:190,155:160]=0
        merged=engine.update(a,1.1,1.11,'a')
        self.assertEqual(len(first['boxes']),2);self.assertEqual(len(merged['boxes']),1)
        self.assertEqual(merged['status'],'OBSTACLE');self.assertEqual(merged['direction'],'CENTER')
    def test_no_floor_is_unknown_not_fixed_safe_corridor(self):
        result=run_frames(CorridorDecision(),np.zeros((240,320),np.uint8))[-1]
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['corridor']['source'],'UNKNOWN')

if __name__=='__main__':unittest.main()
