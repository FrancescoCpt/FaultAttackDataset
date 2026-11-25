"""Regression checks for reset anchoring (no graphical backend needed)."""
import unittest

import numpy as np

from plot_adc1_variability import reset_index


class ResetDetectionTests(unittest.TestCase):
    def waveform(self):
        y = np.full(1000, 220.0)
        y[10] = 0  # Unrelated earlier minimum must not become the anchor.
        y[540:600] = np.linspace(219, 180, 60)
        y[600:620] = np.linspace(180, 254, 20)
        y[620:] = 255
        return y

    def test_local_minimum_before_plateau_and_last_tie(self):
        event, plateau = reset_index(self.waveform())
        self.assertEqual(event, 600)
        self.assertGreater(plateau, event)

    def test_shift_invariance_and_cropped_alignment(self):
        y = self.waveform()
        shifted = np.concatenate((np.full(73, 220), y[:-73]))
        a, _ = reset_index(y)
        b, _ = reset_index(shifted)
        self.assertEqual(b-a, 73)
        np.testing.assert_array_equal(y[a-100:a+101], shifted[b-100:b+101])

    def test_temporary_high_pulse_is_not_final_plateau(self):
        y = self.waveform()
        y[100:150] = 255
        self.assertEqual(reset_index(y)[0], 600)

    def test_missing_plateau_and_flat_plateau_rejected(self):
        for y in (np.full(1000, 220), np.full(1000, 255)):
            with self.assertRaises(ValueError):
                reset_index(y)

    def test_minimum_at_search_boundary_rejected(self):
        y = np.full(1000, 255.0)
        y[:620] = np.linspace(100, 240, 620)
        with self.assertRaisesRegex(ValueError, 'bordo'):
            reset_index(y)


if __name__ == '__main__':
    unittest.main()
