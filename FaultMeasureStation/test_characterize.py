import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import characterize as c


def signals():
    t = np.arange(-10, 100, .01)*1e-6
    y = np.full(len(t), 2.5)
    # Isolated pre-reset spike must not count as sustained recovery.
    y[(t>10e-6)&(t<10.1e-6)] = 3.5
    y -= .8*np.exp(-((t-44.7e-6)/.08e-6)**2)
    y[t>=50.7e-6] = 3.5
    ch3 = np.where((t>=44.4e-6)&(t<45.4e-6), 3.3, 0.)
    return t, y, ch3


class AnalysisTests(unittest.TestCase):
    def test_induced_and_backtracked_minimum(self):
        t, y, ch3 = signals()
        r = c.analyze(t,y,t,ch3,c.Analysis())
        self.assertAlmostEqual(r['t_min_us'],44.7, places=2)
        self.assertAlmostEqual(r['dt_us'],6., delta=.011)  # One sample of timing quantization.
        auto = c.analyze(t,y,t,np.zeros_like(ch3),c.Analysis(),r['dt_us'])
        self.assertAlmostEqual(auto['v_min'],r['v_min'])

    def test_one_volt_noisy_plateau(self):
        t,y,ch3 = signals()
        active = ch3 > 0
        ch3[active] = 1.0 + .08*np.sin(np.arange(np.count_nonzero(active))*2.0)
        self.assertGreater(len(c.pulses(t,ch3,1.0)), 10)
        r = c.analyze(t,y,t,ch3,c.Analysis())
        self.assertAlmostEqual(r['t_min_us'],44.7,places=2)

    def test_hysteresis_ignores_threshold_chatter(self):
        t = np.arange(10, dtype=float)
        y = np.array([0.,.7,.49,.51,.48,.52,.7,.3,0.,0.])
        self.assertEqual(len(c.pulses(t,y,.5,.2)),1)
        self.assertGreater(len(c.pulses(t,y,.5,0.)),1)

    def test_truncated_pulses_are_excluded(self):
        t = np.arange(7, dtype=float)
        y = np.array([1.,1.,0.,0.,1.,1.,1.])
        self.assertEqual(c.pulses(t,y,.5,.2),[])

    def test_missing_reset(self):
        t,y,ch3 = signals()
        y[t>=50e-6] = 2.5
        with self.assertRaisesRegex(ValueError,'Ripresa'):
            c.analyze(t,y,t,ch3,c.Analysis())

    def test_missing_or_ambiguous_pulse(self):
        t,y,ch3 = signals()
        with self.assertRaisesRegex(ValueError,'trovati 0'):
            c.analyze(t,y,t,np.zeros_like(ch3),c.Analysis())
        ch3[(t>20e-6)&(t<21e-6)] = 3.3
        with self.assertRaisesRegex(ValueError,'trovati 2'):
            c.analyze(t,y,t,ch3,c.Analysis())

    def test_auto_rejects_active_attacker(self):
        t,y,ch3 = signals()
        with self.assertRaisesRegex(ValueError,'CH3 attivo'):
            c.analyze(t,y,t,ch3,c.Analysis(),6.)

    def test_bad_trace_and_boundary_minimum(self):
        t,y,ch3 = signals()
        with self.assertRaises(ValueError):
            c.analyze(t[::-1],y,t,ch3,c.Analysis())
        with self.assertRaisesRegex(ValueError,'bordo'):
            c.minimum(t,np.arange(len(t)),44e-6,45e-6)

    def test_reset_hold_and_record_boundary(self):
        t,y,ch3 = signals()
        y[t>=50e-6] = 2.5
        y[t>99e-6] = 3.5
        with self.assertRaisesRegex(ValueError,'Ripresa'):
            c.analyze(t,y,t,ch3,c.Analysis())

    def test_thirty_auto_runs_without_attacker_port(self):
        t,y,ch3 = signals()
        class Port:
            def close(self): pass
        class Scope:
            def arm(self): pass
            def close(self): pass
        calls = []
        def experiment(cfg, ports, scope):
            self.assertEqual(cfg.protocol,c.acquire.PROTO_AUTO_INDUCED)
            self.assertEqual(set(ports),{'bodyguard'})
            calls.append(1)
            return np.zeros(3),np.zeros(3),{'scope_adc1':(t,y),'scope_adc3':(t,np.zeros_like(ch3))}
        with tempfile.TemporaryDirectory() as tmp:
            calibration=Path(tmp)/'cal.json'
            c.write_json(calibration,dict(action='1',valid_runs=30,analysis=c.asdict(c.Analysis()),dt_us=c.stats([6.,6.])))
            with patch.object(c.acquire,'setup_scope',return_value=Scope()), patch.object(c.acquire,'_open',return_value=Port()) as op, patch.object(c.acquire,'run_experiment',side_effect=experiment), patch.object(c.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
                c.main(['--action','2','-b','mock','--calibration',str(calibration),'--out-dir',tmp,'--no-plot'])
            self.assertEqual(len(calls),30)
            op.assert_called_once_with('mock',c.acquire.BODYGUARD_BAUD,'bodyguard')
            summary=json.loads(next(Path(tmp).glob('*/summary.json')).read_text())
            self.assertEqual(summary['valid_runs'],30)
            self.assertTrue(summary['complete'])
            self.assertEqual(len(list(Path(tmp).glob('*/*.npz'))),30)


if __name__=='__main__':
    unittest.main()
