import unittest
import numpy as np
from demo_core import candidates,Confirmation,featuremap_bytes

class DemoTests(unittest.TestCase):
    def test_runtime_bytes_preserve_all_float_payload(self):
        x=np.linspace(-1,1,3*240*320,dtype=np.float32).reshape(1,3,240,320)
        raw=featuremap_bytes(x)
        self.assertEqual(raw.dtype,np.uint8);self.assertEqual(raw.size,921600)
        self.assertEqual(raw.tobytes(),x.tobytes())
        self.assertTrue(np.array_equal(raw.view(np.float32).reshape(x.shape),x))
    def floor(self):
        m=np.zeros((240,320),np.uint8);m[100:,40:280]=1;return m
    def test_no_candidate_on_solid_floor(self):
        result,_=candidates(self.floor());self.assertEqual(result['status'],'NO_CANDIDATE')
    def test_three_obstacle_positions(self):
        for x,d in [(80,'LEFT'),(150,'CENTER'),(220,'RIGHT')]:
            m=self.floor();m[160:190,x:x+15]=0
            result,_=candidates(m);self.assertEqual(result['direction'],d)
    def test_no_floor_is_unknown(self):
        result,_=candidates(np.zeros((240,320),np.uint8));self.assertEqual(result['status'],'UNKNOWN')
    def test_fresh_confirmation_and_stale_clear(self):
        gate=Confirmation();raw=dict(status='CANDIDATE',direction='CENTER',boxes=[],reason='test')
        self.assertEqual(gate.update(raw,1,1.01,'a')['status'],'PENDING')
        self.assertEqual(gate.update(raw,1.1,1.11,'a')['status'],'OBSTACLE')
        self.assertEqual(gate.update(raw,1.2,2,'a')['status'],'UNKNOWN')
        self.assertEqual(gate.update(raw,2.1,2.11,'a')['status'],'PENDING')
    def test_source_change_and_empty_clear(self):
        gate=Confirmation();raw=dict(status='CANDIDATE',direction='LEFT',boxes=[],reason='test')
        gate.update(raw,1,1.01,'a');gate.update(raw,1.1,1.11,'a')
        self.assertEqual(gate.update(raw,1.2,1.21,'b')['status'],'PENDING')
        empty=dict(status='NO_CANDIDATE',direction=None,boxes=[],reason='test')
        self.assertEqual(gate.update(empty,1.3,1.31,'b')['status'],'NO_CANDIDATE')
        self.assertEqual(gate.update(raw,1.4,1.41,'b')['status'],'PENDING')

if __name__=='__main__':unittest.main()
