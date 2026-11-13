import contextlib
import io
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import acquire


class RetryTests(unittest.TestCase):
    def test_failed_fit_raises_specific_error(self):
        with patch.object(acquire, '_fit', side_effect=[None, np.zeros(2)]):
            with self.assertRaises(acquire.BufferFitError):
                acquire._fit_buffers([1], [2], {})

    def test_successful_fit_is_not_repeated(self):
        first, second = np.zeros(2), np.ones(2)
        with patch.object(acquire, '_fit', side_effect=[first, second]) as fit:
            result = acquire._fit_buffers([1], [2], {})
        self.assertIs(result[0], first)
        self.assertIs(result[1], second)
        self.assertEqual(fit.call_count, 2)

    def test_retry_does_not_save_or_advance_run_id(self):
        samples = np.ones(3)
        meta = {key: (samples, samples) for key in ('scope_adc1', 'scope_adc2', 'scope_adc3')}
        good = (samples, samples, meta)
        scope = Mock()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
                patch('sys.argv', ['acquire.py', '--protocol', '1', '-b', 'mock', '--repeat', '2', '--no-plot', '--auto-save', '--out-dir', tmp]), \
                patch.object(acquire, 'setup_scope', return_value=scope), \
                patch.object(acquire, 'open_ports', return_value={}), \
                patch.object(acquire.time, 'sleep'), \
                patch.object(acquire, 'run_experiment', side_effect=[acquire.BufferFitError('bad'), good, acquire.BufferFitError('bad'), good]) as run, \
                patch.object(acquire, 'append_bodyguard_traces') as save_adc, \
                patch.object(acquire, 'append_scope_traces') as save_scope:
            acquire.main()
        self.assertEqual(run.call_count, 4)
        self.assertEqual(save_adc.call_count, 2)
        self.assertEqual(save_scope.call_count, 2)
        self.assertTrue(save_adc.call_args_list[0].args[2].endswith('_run001'))
        self.assertTrue(save_adc.call_args_list[1].args[2].endswith('_run002'))
        scope.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
