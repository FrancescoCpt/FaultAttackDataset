import unittest
import tempfile
import csv
import io
import contextlib
from pathlib import Path
from unittest.mock import patch
from filter_traces import export_filtered
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


class ExportTests(unittest.TestCase):
    def test_alignment_review_discard_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); out=folder/'report'; out.mkdir()
            (folder/'scope_ch2.csv').write_text('TIME,0,1\na,1,2\nb,2,3\nc,3,4\nd,4,5\ne,5,6\n')
            # Different row order must not change alignment.
            (folder/'bodyguard_adc1.csv').write_text('e,5,6\nd,4,5\nc,3,4\nb,2,3\na,1,2\n')
            rows=[dict(run_id=k,status=v,reason='test') for k,v in
                  [('a','discard'),('b','review'),('c','review'),('d','keep'),('e','keep')]]
            with patch('builtins.input',side_effect=['n','s']) as ask, contextlib.redirect_stdout(io.StringIO()):
                result=export_filtered(folder,out,rows,2)
            self.assertEqual(result['run_ids'],['c','d'])
            self.assertEqual(ask.call_count,2)
            self.assertEqual((out/'filtered/scope_ch2.csv').read_text(),'TIME,0,1\nc,3,4\nd,4,5\n')
            self.assertEqual((out/'filtered/bodyguard_adc1.csv').read_text(),'c,3,4\nd,4,5\n')
            self.assertIn('a,1,2',(folder/'scope_ch2.csv').read_text())

    def test_missing_channel_excluded_and_shortfall_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); out=folder/'report'; out.mkdir()
            (folder/'scope_ch2.csv').write_text('TIME,0,1\na,1,2\nb,2,3\n')
            (folder/'bodyguard_adc1.csv').write_text('b,2,3\n')
            rows=[dict(run_id=k,status='keep') for k in ['a','b']]
            with contextlib.redirect_stdout(io.StringIO()):
                result=export_filtered(folder,out,rows,10)
            self.assertEqual(result['run_ids'],['b'])
            self.assertEqual(result['decisions']['missing_channel'],1)

    def test_unanswered_review_does_not_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); out=folder/'report'; out.mkdir()
            (folder/'scope_ch2.csv').write_text('TIME,0,1\na,1,2\n')
            with patch('builtins.input',side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    export_filtered(folder,out,[dict(run_id='a',status='review',reason='test')],1)
            self.assertFalse((out/'filtered').exists())


if __name__=='__main__':
    unittest.main()
