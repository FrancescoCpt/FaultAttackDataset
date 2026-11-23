from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np

from h5_viewer import describe, read_slice, preview, main


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.h5'
        self.f = h5py.File(self.path, 'w')
        self.addCleanup(self.f.close)

    def test_multidimensional_selection_and_limit(self):
        values = np.arange(2 * 3 * 100).reshape(2, 3, 100)
        d = self.f.create_dataset('cube', data=values)
        x, y, _ = read_slice(d, indices='1,2', start=3, stop=94, step=2, max_points=7)
        self.assertLessEqual(len(x), 7)
        np.testing.assert_array_equal(y, values[1, 2, x])
        x, y, _ = read_slice(d, axis=1, indices='1,4')
        np.testing.assert_array_equal(y, values[1, :, 4])

    def test_scalars_strings_compound_and_empty(self):
        d = self.f.create_dataset('text', data='ciao', dtype=h5py.string_dtype())
        x, y, _ = read_slice(d)
        self.assertIn('ciao', preview(x, y))
        d = self.f.create_dataset('table', data=np.array([(1, 2.5), (2, 4.5)], dtype=[('id', 'i4'), ('v', 'f8')]))
        _, y, _ = read_slice(d, field='v')
        np.testing.assert_array_equal(y, [2.5, 4.5])
        d = self.f.create_dataset('empty', shape=(0,), dtype='f8')
        self.assertEqual(len(read_slice(d)[0]), 0)
        d = self.f.create_dataset('null', dtype='f8')
        with self.assertRaises(ValueError):
            read_slice(d)

    def test_invalid_selection_and_attributes(self):
        d = self.f.create_dataset('matrix', shape=(3, 10))
        d.attrs['unit'] = 'V'
        self.assertIn('unit: V', describe(d))
        for kwargs in (dict(indices='4'), dict(axis=2), dict(step=0), dict(stop=11), dict(indices='0,1')):
            with self.assertRaises(ValueError):
                read_slice(d, **kwargs)

    def test_large_dataset_read_is_bounded(self):
        d = self.f.create_dataset('large', shape=(1000, 1000000), chunks=(1, 1000), dtype='u1', fillvalue=7)
        x, y, _ = read_slice(d, max_points=123)
        self.assertLessEqual(len(y), 123)
        self.assertTrue(np.all(y == 7))

    def test_cli_inspection_is_read_only(self):
        self.f.create_dataset('data', data=[1, 2, 3])
        self.f.close()
        before = self.path.read_bytes()
        self.assertEqual(main([str(self.path), '--inspect', '/data']), 0)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
