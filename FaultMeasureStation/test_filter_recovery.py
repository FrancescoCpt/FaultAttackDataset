import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np
from filter_traces import adc_recovery, filter_recovery


class RecoveryFilterTests(unittest.TestCase):
    def waveform(self):
        t = np.arange(4601) * .2
        return np.rint(np.interp(t, [0, 870, 880, 890, 920], [220, 200, 190, 255, 255]))

    def apply(self, signals, threshold=5):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with (path/'bodyguard_adc1.csv').open('w') as f:
                csv.writer(f).writerows([str(i), *y] for i, y in enumerate(signals))
            rows = [dict(run_id=str(i), status='keep', reason='CH2 OK') for i in range(len(signals))]
            result = filter_recovery(path, rows, 5e6, (-20, 0), threshold)
            return result, rows

    def test_local_outlier_and_unmodified_inputs(self):
        base = self.waveform()
        outlier = base.copy()
        outlier[4350:4400] -= 25
        original = outlier.copy()
        result, rows = self.apply([base, base, base, outlier])
        self.assertTrue(result['applied'])
        self.assertEqual([r['status'] for r in rows], ['keep']*3+['discard'])
        np.testing.assert_array_equal(outlier, original)

    def test_outside_window_does_not_change_score(self):
        base = self.waveform()
        other = base.copy()
        other[100:200] -= 50
        _, rows = self.apply([base, base, other])
        self.assertEqual(rows[-1]['recovery_rms_counts'], 0)

    def test_insufficient_cohort_and_missing_recovery(self):
        result, rows = self.apply([self.waveform(), self.waveform(), np.full(4601, 220)])
        self.assertFalse(result['applied'])
        self.assertEqual([r['status'] for r in rows], ['review', 'review', 'discard'])

    def test_recovery_interpolation(self):
        _, recovery = adc_recovery(self.waveform(), 5e6)
        self.assertTrue(888 < recovery < 890)


if __name__ == '__main__':
    unittest.main()
