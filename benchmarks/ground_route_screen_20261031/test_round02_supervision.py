import unittest
import numpy as np
from round02_supervision import coarse_mask, binary_target


class CoarseSupervisionTests(unittest.TestCase):
    def test_uncertain_boundaries_are_ignored(self):
        mask=np.zeros((90,100),np.uint8);mask[30:60,30:70]=1
        result=coarse_mask(mask)
        self.assertEqual(result[45,45],1)
        self.assertEqual(result[30,45],255)
        self.assertEqual(result[15,45],0)

    def test_visible_ground_in_chair_gap_remains_ground(self):
        mask=np.ones((70,70),np.uint8)
        mask[10:18,12:58]=2;mask[18:60,12:18]=2;mask[18:60,52:58]=2
        result=coarse_mask(mask)
        self.assertEqual(result[40,35],1)
        self.assertEqual(result[35,15],2)

    def test_binary_mapping_preserves_ignore_and_obstacle_negative(self):
        mask=np.array([[0,1,2,255]],np.uint8)
        np.testing.assert_array_equal(binary_target(mask),np.array([[0,1,0,255]],np.uint8))


if __name__=='__main__':unittest.main()
