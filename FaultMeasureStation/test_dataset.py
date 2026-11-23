import csv
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np

import dataset


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.folder = self.source / 'B1' / 'K1V1' / 'Induced'
        self.folder.mkdir(parents=True)
        self.output = self.base / 'dataset.h5'
        self.args = ['--source', str(self.source), '--output', str(self.output)]
        self.write(['r1', 'r2'])

    def write(self, ids):
        for channel in dataset.CHANNELS:
            with (self.folder / (channel + '.csv')).open('w', newline='') as f:
                w = csv.writer(f)
                if channel.startswith('scope'):
                    w.writerow(['TIME', -0.1, 0, 0.1])
                for rid in ids:
                    w.writerow([rid, 0, 12, 255])

    def sessions(self, f):
        return f['boards/B1/experiments/K1V1/Induced/sessions']

    def test_roundtrip_idempotence_append_and_filter_version(self):
        reports = self.folder / 'filter_reports'
        reports.mkdir()
        (reports / 'selection.csv').write_text('run_id,status,export_decision\nr1,keep,keep\nr2,discard,discard\n')
        self.assertEqual(dataset.main(self.args), 0)
        self.assertEqual(dataset.main(self.args), 0)
        with h5py.File(self.output) as f:
            self.assertEqual(len(self.sessions(f)), 1)
            session = next(iter(self.sessions(f).values()))
            np.testing.assert_array_equal(session['bodyguard/adc1'][:], [[0, 12, 255]] * 2)
            self.assertEqual(session['bodyguard/adc1'].dtype, np.dtype('uint8'))
            np.testing.assert_array_equal(session['timing/scope_ch1_seconds'][:], [-0.1, 0, 0.1])
        self.write(['r1', 'r2', 'r3'])
        (reports / 'selection.csv').write_text('run_id,status,export_decision\nr3,keep,keep\n')
        self.assertEqual(dataset.main(self.args), 0)
        with h5py.File(self.output) as f:
            self.assertEqual(sum(len(s['run_id']) for s in self.sessions(f).values()), 3)
            self.assertEqual(len(f['boards/B1/experiments/K1V1/Induced/filtering']), 2)

    def test_changed_existing_trace_rejected(self):
        self.assertEqual(dataset.main(self.args), 0)
        p = self.folder / 'bodyguard_adc1.csv'
        p.write_text(p.read_text().replace('12', '13'))
        self.assertEqual(dataset.main(self.args), 1)
        with h5py.File(self.output) as f:
            self.assertEqual(len(self.sessions(f)), 1)

    def test_ids_not_position_and_invalid_adc(self):
        p = self.folder / 'bodyguard_adc2.csv'
        p.write_text('r2,0,12,255\nr1,0,12,255\n')
        self.assertEqual(dataset.main(self.args), 0)
        p.write_text('r1,0,12,256\nr2,0,12,255\n')
        self.assertEqual(dataset.main(self.args), 1)
        p.write_text('r1,0,12,255\n')
        self.assertEqual(dataset.main(self.args), 1)

    def test_missing_scope_requires_option_and_dry_run_no_write(self):
        (self.folder / 'scope_ch3.csv').unlink()
        self.assertEqual(dataset.main(self.args + ['--dry-run']), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(dataset.main(self.args + ['--allow-missing-scope']), 0)
        with h5py.File(self.output) as f:
            s = next(iter(self.sessions(f).values()))
            self.assertIn('scope_ch3', s.attrs['missing_channels'])
            self.assertNotIn('ch3', s['scope'])

    def test_characterization_versions_and_staging_recovery(self):
        char = self.source / 'B1' / 'Characterization' / 'Victim1_action1'
        char.mkdir(parents=True)
        (char / 'config.json').write_text('{"action": 1}')
        np.savez_compressed(char / 'run001.npz', adc1=np.array([1, 2, 3]))
        self.assertEqual(dataset.main(self.args), 0)
        with h5py.File(self.output, 'a') as f:
            f.require_group('_staging/abandoned')
        (char / 'config.json').write_text('{"action": 2}')
        self.assertEqual(dataset.main(self.args), 0)
        with h5py.File(self.output) as f:
            self.assertEqual(len(f['boards/B1/characterization/Victim1_action1']), 2)
            self.assertNotIn('_staging/abandoned', f)


if __name__ == '__main__':
    unittest.main()
