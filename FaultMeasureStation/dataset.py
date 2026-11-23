#!/usr/bin/env python3
"""Build/update one HDF5 archive from the station's CSV/NPZ output.

Examples:
  python dataset.py --dry-run
  python dataset.py --output Dataset/dataset.h5
  python dataset.py --allow-missing-scope
  python dataset.py --source Induced_Traces --board B6 --experiment K1V3 --mode Induced

Default source: Dataset beside this script. Run while acquisition/filtering is
stopped. Existing traces are immutable; IDs identify acquisitions within a
board/experiment/mode. Repeated imports verify content and add only new IDs.
Missing timing/calibration information is not inferred from current firmware.

Layout:
  /boards/<board>/characterization/<source-folder>/<content-hash>/
  /boards/<board>/experiments/<experiment>/<mode>/sessions/<batch-hash>/
  /boards/<board>/experiments/<experiment>/<mode>/filtering/<content-hash>/

Sessions are immutable IMPORT BATCHES, not inferred hardware sessions. ADCs
use uint8; scope voltages and original TIME axes use float64. A new import
may have different trace lengths; within one source folder lengths must match.
Filtering snapshots preserve reports plus selected_run_id arrays: join these
to session run_id, never assume report row positions match waveform positions.
Only the five CSVs directly in a mode folder are waveform sources; filtered/
and original_prefilter/ copies are intentionally not imported recursively.
Changing source traces or adding missing channels to an already imported ID
is rejected, rather than silently replacing archived data. Import raw complete
sources initially whenever available. Removed source IDs remain in the archive.
Characterization references and Bodyguard timing are marked unknown when not
available; original JSON configurations are preserved in metadata snapshots.

Run on a quiescent source. One writer is enforced on macOS/Linux with flock.
Staging and committed_count detect incomplete logical batches, but HDF5 is
not transactional: retain sources and backups against power/filesystem failure.
Exit code 1 means some source groups failed; other valid groups may have been
imported. No source file is deleted. --dry-run does not create or modify HDF5.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid

import numpy as np
import h5py

SCHEMA = '1'
CHANNELS = ('bodyguard_adc1', 'bodyguard_adc2', 'scope_ch1', 'scope_ch2', 'scope_ch3')
STR = h5py.string_dtype('utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(len(part).to_bytes(8, 'little'))
        h.update(part)
    return h.hexdigest()


def numeric(group, name, values):
    a = np.asarray(values)
    options = dict(compression='gzip', compression_opts=4, shuffle=True, fletcher32=True) if a.size and a.ndim else {}
    return group.create_dataset(name, data=a, **options)


def strings(group, name, values):
    return group.create_dataset(name, data=values, dtype=STR)


class TraceFile:
    """Keep only offsets in memory; read at most one waveform at a time."""
    def __init__(self, path, adc):
        self.path, self.adc = path, adc
        self.offsets, self.time = {}, None
        self.stat = self.signature()
        with path.open() as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                row = next(csv.reader([line]))
                if not row:
                    continue
                rid = row[0]
                if rid == 'TIME':
                    if self.time is not None or self.offsets:
                        raise ValueError(f'{path}: TIME duplicato o fuori posizione')
                    self.time = np.asarray(row[1:], dtype='<f8')
                    if not self.time.size or not np.isfinite(self.time).all() or np.any(np.diff(self.time) <= 0):
                        raise ValueError(f'{path}: asse temporale non valido')
                else:
                    if not rid or rid in self.offsets:
                        raise ValueError(f'{path}: ID vuoto o duplicato {rid!r}')
                    self.offsets[rid] = offset
        if not adc and self.time is None:
            raise ValueError(f'{path}: riga TIME mancante')

    def signature(self):
        st = self.path.stat()
        return st.st_size, st.st_mtime_ns

    def read(self, rid):
        with self.path.open() as f:
            f.seek(self.offsets[rid])
            a = np.asarray(next(csv.reader([f.readline()]))[1:], dtype='<f8')
        if not a.size or not np.isfinite(a).all():
            raise ValueError(f'{self.path}: campioni vuoti/non finiti in {rid}')
        if self.adc:
            if np.any((a < 0) | (a > 255) | (a != np.floor(a))):
                raise ValueError(f'{self.path}: ADC fuori range uint8 in {rid}')
            return a.astype('u1')
        if len(a) != len(self.time):
            raise ValueError(f'{self.path}: lunghezza TIME diversa in {rid}')
        return a


def row_hash(files, rid):
    parts = [rid.encode()]
    for name, src in files.items():
        parts.extend((name.encode(), src.read(rid).tobytes()))
        if src.time is not None:
            parts.append(src.time.tobytes())
    return digest(parts)


@contextmanager
def locked(path):
    # Advisory OS lock: automatically released even if the process terminates.
    import fcntl
    with Path(str(path) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'Un altro importatore sta aggiornando {path}') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def commit(h5, target, writer):
    """Stage a complete immutable group, then publish it. Not a DB transaction."""
    staging = h5.require_group('_staging')
    key = uuid.uuid4().hex
    group = staging.create_group(key)
    try:
        writer(group)
        group.attrs['created_at'] = now()
        group.attrs['complete'] = True
        h5.flush()
        h5.require_group(target.rsplit('/', 1)[0])
        h5.move(group.name, target)
        h5.flush()
    except Exception:
        if key in staging:
            del staging[key]
        raise


def archive_snapshot(h5, target, paths, base, npz=False):
    """Content-addressed versions; JSON/CSV text is retained verbatim."""
    if not paths:
        return
    # Characterization contains only a few runs. NPZ arrays are read individually.
    fingerprints = []
    for p in paths:
        h = hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        fingerprints.append((p.relative_to(base).as_posix(), h.hexdigest()))
    version = digest([json.dumps(fingerprints).encode()])
    destination = f'{target}/{version}'
    if destination in h5:
        return
    def write(g):
        g.attrs['source'] = str(base.resolve())
        g.attrs['sha256_manifest'] = json.dumps(fingerprints)
        for p, (rel, expected) in zip(paths, fingerprints):
            content = p.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f'Sorgente modificata durante importazione: {p}')
            if npz and p.suffix == '.npz':
                dest = g.require_group('traces/' + p.stem)
                with np.load(io.BytesIO(content), allow_pickle=False) as data:
                    for name in data.files:
                        numeric(dest, name, data[name])
            else:
                dest = g.require_group('files')
                if '/' in rel:
                    parent, name = rel.rsplit('/', 1)
                    dest = dest.require_group(parent)
                else:
                    name = rel
                strings(dest, name, content.decode('utf-8'))
                if p.name == 'selection.csv':
                    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8'))))
                    if rows and 'run_id' in rows[0] and 'export_decision' in rows[0]:
                        selection = g.require_group('selections/' + digest([rel.encode()]))
                        selection.attrs['source_file'] = rel
                        for field in ('run_id', 'status', 'export_decision'):
                            strings(selection, field, [r.get(field, '') for r in rows])
                        strings(selection, 'selected_run_id', [r['run_id'] for r in rows
                                                               if r['export_decision'] == 'keep'])
    commit(h5, destination, write)


def import_traces(h5, folder, board, experiment, mode, allow_missing, dry_run):
    files = {n: TraceFile(folder / (n + '.csv'), n.startswith('bodyguard'))
             for n in CHANNELS if (folder / (n + '.csv')).exists()}
    missing = sorted(set(CHANNELS) - files.keys())
    if any(n.startswith('bodyguard') for n in missing) or (missing and not allow_missing):
        raise ValueError(f'{folder}: canali mancanti {missing}; per scope storico usare --allow-missing-scope')
    ids = list(files['bodyguard_adc1'].offsets)
    if any(set(f.offsets) != set(ids) for f in files.values()):
        raise ValueError(f'{folder}: Run ID non corrispondenti tra canali; nessun abbinamento per posizione')
    root = f'/boards/{board}/experiments/{experiment}/{mode}'
    known = {}
    if h5 is not None and root + '/sessions' in h5:
        for session in h5[root + '/sessions'].values():
            for rid, sha in zip(session['run_id'].asstr()[:], session['trace_sha256'].asstr()[:]):
                known[rid] = sha
    hashes, lengths = {}, {}
    for rid in ids:
        hashes[rid] = row_hash(files, rid)
        if rid in known and known[rid] != hashes[rid]:
            raise ValueError(f'{folder}: contenuto modificato per Run ID già archiviato {rid}')
        for name, src in files.items():
            length = len(src.read(rid))
            if name in lengths and lengths[name] != length:
                raise ValueError(f'{folder}: lunghezze variabili in {name}; separare le sessioni')
            lengths[name] = length
    new = [rid for rid in ids if rid not in known]
    print(f'{board}/{experiment}/{mode}: {len(ids)} tracce, {len(new)} nuove; mancanti: {missing}')
    if dry_run:
        return
    if new:
        batch = digest([hashes[r].encode() for r in new])
        def write(g):
            g.attrs.update(source=str(folder.resolve()), missing_channels=json.dumps(missing),
                           characterization_ref='unknown', bodyguard_timing='unknown',
                           description='Immutable import batch; acquisition run IDs preserved')
            strings(g, 'run_id', new)
            strings(g, 'trace_sha256', [hashes[r] for r in new])
            for name, src in files.items():
                device, channel = name.split('_', 1)
                dest = g.require_group(device)
                length = lengths[name]
                d = dest.create_dataset(channel, shape=(len(new), length),
                    dtype='u1' if src.adc else '<f8', chunks=(1, min(length, 65536)),
                    compression='gzip', compression_opts=4, shuffle=True, fletcher32=True)
                d.attrs['unit'] = 'ADC code' if src.adc else 'V'
                for i, rid in enumerate(new):
                    d[i] = src.read(rid)
                if src.time is not None:
                    numeric(g.require_group('timing'), name + '_seconds', src.time).attrs['unit'] = 's'
            # Verify actual stored values against the hashes computed before writing.
            for i, rid in enumerate(new):
                parts = [rid.encode()]
                for name, src in files.items():
                    device, channel = name.split('_', 1)
                    parts.extend((name.encode(), g[device][channel][i].tobytes()))
                    if src.time is not None:
                        parts.append(g['timing'][name + '_seconds'][:].tobytes())
                if digest(parts) != hashes[rid]:
                    raise ValueError(f'Verifica scrittura fallita: {rid}')
            if any(src.signature() != src.stat for src in files.values()):
                raise ValueError('CSV modificati durante importazione: fermare acquisizione e filtri')
            g.attrs['committed_count'] = len(new)
        commit(h5, root + '/sessions/' + batch, write)
    reports = folder / 'filter_reports'
    if reports.exists():
        paths = sorted(p for p in reports.rglob('*') if p.is_file() and p.suffix in ('.csv', '.json')
                       and not {'filtered', 'original_prefilter'}.intersection(p.relative_to(reports).parts))
        archive_snapshot(h5, root + '/filtering', paths, reports)
    configs = sorted(folder.glob('*.json'))
    archive_snapshot(h5, root + '/metadata', configs, folder)


def component(value):
    if not value or value in ('.', '..') or '/' in value:
        raise ValueError(f'Identificativo non valido: {value!r}')
    return value


def main(argv=None):
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, default=here / 'Dataset')
    parser.add_argument('--output', type=Path, default=here / 'Dataset' / 'dataset.h5')
    parser.add_argument('--board', help='Board da importare; obbligatoria per una cartella di acquisizione')
    parser.add_argument('--experiment', help='Es. K1V3, per importare una singola cartella di acquisizione')
    parser.add_argument('--mode', choices=('Induced', 'Autoinduced'))
    parser.add_argument('--allow-missing-scope', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Valida sorgenti e confronta archivio senza modificarlo')
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error(f'Cartella inesistente: {args.source}')
    direct = args.experiment is not None or args.mode is not None
    if direct and not all((args.board, args.experiment, args.mode)):
        parser.error('--board, --experiment e --mode sono necessari insieme')
    jobs, boards = [], []
    if direct:
        jobs.append((args.source, component(args.board), component(args.experiment), args.mode))
    else:
        boards = sorted(p for p in args.source.iterdir() if p.is_dir() and (not args.board or p.name == args.board))
        for board in boards:
            component(board.name)
            for exp in sorted(board.iterdir()):
                if exp.is_dir() and exp.name != 'Characterization':
                    for mode in ('Induced', 'Autoinduced'):
                        if (exp / mode).is_dir():
                            jobs.append((exp / mode, board.name, component(exp.name), mode))
    if not jobs and not any((b / 'Characterization').is_dir() for b in boards):
        parser.error('Nessuna acquisizione o caratterizzazione trovata')

    def run(h5):
        errors = 0
        if h5 is not None:
            if 'schema_version' in h5.attrs:
                if h5.attrs['schema_version'] != SCHEMA:
                    raise ValueError('Versione schema HDF5 incompatibile')
            elif len(h5) or len(h5.attrs):
                raise ValueError('Il file esistente non è un dataset gestito da questo script')
            elif not args.dry_run:
                h5.attrs.update(schema_version=SCHEMA, dataset_uuid=str(uuid.uuid4()), created_at=now())
            if not args.dry_run:
                # Leftovers from interrupted imports are never published to readers.
                if '_staging' in h5:
                    del h5['_staging']
                h5.require_group('boards')
        for board in boards:
            if h5 is not None and not args.dry_run:
                h5.require_group('/boards/' + board.name).attrs['board_id'] = board.name
                archive_snapshot(h5, f'/boards/{board.name}/metadata',
                                 sorted(board.glob('*.json')), board)
            char = board / 'Characterization'
            if char.exists():
                for folder in sorted(p for p in char.iterdir() if p.is_dir()):
                    paths = sorted(p for p in folder.iterdir() if p.suffix in ('.json', '.csv', '.npz') and p.is_file())
                    try:
                        if args.dry_run:
                            for p in paths:
                                if p.suffix == '.npz':
                                    with np.load(p, allow_pickle=False) as data:
                                        for key in data.files:
                                            _ = data[key]
                            print(f'Caratterizzazione {board.name}/{folder.name}: {len(paths)} file')
                        else:
                            archive_snapshot(h5, f'/boards/{board.name}/characterization/{component(folder.name)}', paths, folder, npz=True)
                    except (ValueError, OSError) as e:
                        print(f'ERRORE: {e}')
                        errors += 1
        for job in jobs:
            try:
                import_traces(h5, *job, args.allow_missing_scope, args.dry_run)
            except (ValueError, OSError) as e:
                print(f'ERRORE: {e}')
                errors += 1
        if h5 is not None and not args.dry_run:
            h5.attrs['updated_at'] = now()
            h5.flush()
        print(f'Completato: {len(jobs)} gruppi esaminati, {errors} errori. Sorgenti conservate.')
        return 1 if errors else 0

    if args.dry_run:
        if args.output.exists():
            with h5py.File(args.output, 'r') as h5:
                return run(h5)
        return run(None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with locked(args.output), h5py.File(args.output, 'a') as h5:
        return run(h5)


if __name__ == '__main__':
    raise SystemExit(main())
