import unittest
import numpy as np
from correct_adc1_window import translate, correction_profile


class TranslationTests(unittest.TestCase):
    def test_inclusive_window_and_original_unchanged(self):
        y = np.full(20, 200, dtype=np.uint8)
        changed = translate(y, 10, -2, 1, 10, profile='block')
        np.testing.assert_array_equal(changed[8:12], 210)
        np.testing.assert_array_equal(changed[:8], y[:8])
        np.testing.assert_array_equal(changed[12:], y[12:])
        np.testing.assert_array_equal(y, 200)

    def test_mean_shift_preserves_std(self):
        before = np.array([[200]*20, [210]*20, [220]*20])
        after = np.stack([translate(y, 10, -2, 2, -10, profile='block') for y in before])
        np.testing.assert_allclose(after.mean(0)[8:13], before.mean(0)[8:13]-10)
        np.testing.assert_allclose(after.std(0), before.std(0))

    def test_overflow_and_underflow_rejected(self):
        for value, offset in ((250, 10), (5, -10)):
            with self.assertRaises(ValueError):
                translate(np.full(20, value), 10, -2, 2, offset)

    def test_smooth_edges_center_and_symmetry(self):
        y = np.full(101, 200)
        delta = translate(y, 50, -20, 20, -20) - y
        self.assertEqual(delta[30], 0)
        self.assertEqual(delta[70], 0)
        self.assertEqual(delta[50], -20)
        np.testing.assert_array_equal(delta, delta[::-1])
        np.testing.assert_array_equal(delta[:30], 0)
        np.testing.assert_array_equal(delta[71:], 0)

    def test_short_ramps_preserve_flat_center(self):
        weights = correction_profile(21, ramp_samples=4)
        np.testing.assert_allclose(weights[4:17], 1)
        self.assertEqual(weights[0], 0)
        self.assertEqual(weights[-1], 0)

    def test_invalid_ramp_or_window(self):
        for length, ramp in ((2, None), (11, 0), (11, 6)):
            with self.assertRaises(ValueError):
                correction_profile(length, ramp_samples=ramp)

    def test_incomplete_window_rejected(self):
        with self.assertRaises(ValueError):
            translate(np.full(20, 200), 1, -2, 2, 1)


if __name__ == '__main__':
    unittest.main()
