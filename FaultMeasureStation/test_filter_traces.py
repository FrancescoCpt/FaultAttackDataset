import unittest
import numpy as np
from filter_traces import Settings, classify, pulses


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.t=np.arange(-10,100,.01)*1e-6
        self.cfg=Settings()

    def signal(self,fall):
        u=self.t*1e6
        return np.where(u<0,0,np.where(u<40,3.3,3.3*np.clip(1-(u-40)/fall,0,1)))

    def glitch(self,at):
        return self.t,np.where((self.t>=at*1e-6)&(self.t<(at+1)*1e-6),1.,0.)

    def test_square_discard(self):
        self.assertEqual(classify(self.t,self.signal(.05),self.cfg,'auto')['status'],'discard')

    def test_slow_keep(self):
        r=classify(self.t,self.signal(10),self.cfg,'auto')
        self.assertEqual(r['status'],'keep')
        self.assertAlmostEqual(r['fall_us'],8,delta=.1)
        self.assertAlmostEqual(r['reset_us'],41,delta=.1)

    def test_glitch_order(self):
        for when,expected in [(38,'keep'),(45,'discard'),(41,'review')]:
            with self.subTest(when=when):
                self.assertEqual(classify(self.t,self.signal(10),self.cfg,'induced',self.glitch(when))['status'],expected)

    def test_short_ch3_ring_is_ignored(self):
        t,y=self.glitch(38)
        y[(t>=39.02e-6)&(t<39.26e-6)]=.8
        self.assertEqual(classify(self.t,self.signal(10),self.cfg,'induced',(t,y))['status'],'keep')

    def test_missing_glitch_review(self):
        self.assertEqual(classify(self.t,self.signal(10),self.cfg,'induced')['status'],'review')

    def test_flat_and_truncated_review(self):
        for y in [np.zeros(len(self.t)),np.where(self.t>=0,3.3,0.)]:
            self.assertEqual(classify(self.t,y,self.cfg,'auto')['status'],'review')

    def test_rebounding_dip_is_not_final_fall(self):
        y=self.signal(10)
        y[(self.t>20e-6)&(self.t<20.5e-6)]=0
        r=classify(self.t,y,self.cfg,'auto')
        self.assertEqual(r['status'],'keep')
        self.assertAlmostEqual(r['reset_us'],41,delta=.1)

    def test_intermediate_duration_review(self):
        self.assertEqual(classify(self.t,self.signal(1.5),self.cfg,'auto')['status'],'review')


if __name__=='__main__':
    unittest.main()
