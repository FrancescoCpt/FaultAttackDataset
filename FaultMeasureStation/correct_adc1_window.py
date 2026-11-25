#!/usr/bin/env python3
"""Correzione ADC raccordata nella finestra del minimo, con ampiezza per classe.

Eseguire una volta per board/sottogruppo usando --root. Solo bodyguard_adc1.csv.
Esempio (finestra inclusiva, riferita al minimo originale):
  python correct_adc1_window.py --root Dataset/V1/B1 --output /tmp/B1_corrected \
      --window -80 20 --induced-offset 10 --autoinduced-offset 0 --keep-unprocessable

Per default produce anteprima e report. --write-csv salva anche i CSV corretti.
Gli originali non vengono modificati. Output nuovo e fuori dall'albero di input.
Profilo smooth predefinito: correzione nulla ai bordi, massima al centro,
con raccordi a coseno. --ramp-samples regola la lunghezza dei raccordi.
--profile block ripristina la traslazione uniforme. Offset applicati arrotondati
a interi ADC; nessun clipping, allineamento dei CSV o modifica fuori finestra.
La correzione condizionata alla classe modifica il dataset: conservarne la
provenienza e valutare le prestazioni del classificatore anche su dati originali.
"""
import argparse
import csv
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

from plot_adc1_variability import CLASSES, COLORS, reset_index


def correction_profile(length, profile='smooth', ramp_samples=None):
    """Raised-cosine ramps; optionally retain a flat central correction."""
    if profile == 'block':
        return np.ones(length)
    if profile != 'smooth' or length < 3:
        raise ValueError('Il profilo smooth richiede almeno 3 campioni')
    ramp = (length - 1) // 2 if ramp_samples is None else ramp_samples
    if not 1 <= ramp <= (length - 1) // 2:
        raise ValueError('ramp-samples deve essere tra 1 e (lunghezza finestra - 1) // 2')
    positions = np.arange(length)
    phase = np.minimum(np.minimum(positions, length - 1 - positions) / ramp, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * phase)


def translate(y, event, start, stop, offset, profile='smooth', ramp_samples=None):
    """Return a copy; inclusive window, rejecting incomplete windows/overflow."""
    left, right = event + start, event + stop + 1
    if left < 0 or right > len(y):
        raise ValueError('finestra di correzione fuori traccia')
    result = y.astype(np.int16, copy=True)
    weights = correction_profile(right-left, profile, ramp_samples)
    # Round the correction itself: every trace receives the same integer delta.
    delta = np.rint(offset * weights).astype(np.int64)
    window = result[left:right].astype(np.int64) + delta
    if np.any((window < 0) | (window > 255)):
        raise ValueError('offset porterebbe campioni fuori [0, 255]; nessun clipping applicato')
    result[left:right] = window
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, required=True, help='Board o sottogruppo da correggere ricorsivamente')
    parser.add_argument('--output', type=Path, required=True, help='Nuova cartella esterna a root')
    parser.add_argument('--window', nargs=2, type=int, required=True, metavar=('START', 'STOP'), help='Estremi inclusivi relativi al minimo originale')
    parser.add_argument('--profile', choices=('smooth', 'block'), default='smooth',
                        help='Smooth: raccordo a coseno; block: traslazione uniforme')
    parser.add_argument('--ramp-samples', type=int,
                        help='Lunghezza di ciascun raccordo in intervalli di campioni; default: metà finestra')
    parser.add_argument('--induced-offset', type=int, required=True)
    parser.add_argument('--autoinduced-offset', type=int, required=True)
    parser.add_argument('--write-csv', action='store_true', help='Scrive anche i CSV corretti; altrimenti solo anteprima/report')
    parser.add_argument('--keep-unprocessable', action='store_true', help='Mantiene inalterate e segnala le righe non correggibili; altrimenti interrompe')
    parser.add_argument('--plot-margin', type=int, default=100, help='Campioni di contesto aggiuntivi ai lati della finestra')
    parser.add_argument('--reset-search', type=int, default=250)
    parser.add_argument('--plateau-hold', type=int, default=40)
    parser.add_argument('--plateau-threshold', type=float, default=245)
    parser.add_argument('--min-depth', type=float, default=8)
    args = parser.parse_args()
    root, output = args.root.expanduser().resolve(), args.output.expanduser().resolve()
    start, stop = args.window
    if start > stop or args.plot_margin < 0:
        parser.error('Finestra o margine non validi')
    if args.profile == 'block' and args.ramp_samples is not None:
        parser.error('--ramp-samples si usa solo con --profile smooth')
    try:
        weights = correction_profile(stop-start+1, args.profile, args.ramp_samples)
    except ValueError as error:
        parser.error(str(error))
    if (args.reset_search < 2 or args.plateau_hold < 1 or
            not 0 < args.plateau_threshold <= 255 or not 0 < args.min_depth <= 255):
        parser.error('Parametri di rilevamento non validi')
    if any(abs(value) > 255 for value in (args.induced_offset, args.autoinduced_offset)):
        parser.error('Offset ammessi: interi in [-255, 255]')
    if not root.is_dir():
        parser.error(f'Cartella inesistente: {root}')
    if output.exists() or output == root or root in output.parents:
        parser.error('Output deve essere nuovo ed esterno alla cartella di input')
    paths = sorted(p for p in root.rglob('bodyguard_adc1.csv')
                   if p.parent.name in CLASSES and 'filter_reports' not in p.parts)
    if not paths:
        parser.error('Nessun bodyguard_adc1.csv riconosciuto')
    offsets = {'indotto': args.induced_offset, 'autoindotto': args.autoinduced_offset}
    view_start, view_stop = min(start, 0)-args.plot_margin, max(stop, 0)+args.plot_margin
    x = np.arange(view_start, view_stop+1)
    # Each curve uses precisely the same traces and original event indices.
    previews = {label: [[], []] for label in offsets}
    report = []
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.adc1-correction-', dir=output.parent))
    try:
        for path in paths:
            label = CLASSES[path.parent.name]
            relative = path.relative_to(root)
            destination = staging / 'csv' / relative
            rows_out = []
            seen = set()
            with path.open(newline='') as stream:
                for line, row in enumerate(csv.reader(stream), 1):
                    if not row or row[0] == 'TIME':
                        rows_out.append(row)
                        continue
                    if not row[0] or row[0] in seen:
                        raise ValueError(f'{path}:{line}: ID vuoto o duplicato')
                    seen.add(row[0])
                    event, status, reason, plotted = '', 'corretta', '', False
                    try:
                        y = np.asarray(row[1:], dtype=float)
                        if not y.size or not np.isfinite(y).all() or np.any((y < 0) | (y > 255) | (y != np.floor(y))):
                            raise ValueError('campioni ADC non validi')
                        event, _ = reset_index(y, args.reset_search, args.plateau_hold,
                                               args.plateau_threshold, args.min_depth)
                        corrected = translate(y, event, start, stop, offsets[label], args.profile, args.ramp_samples)
                        if offsets[label] == 0:
                            status = 'offset_zero'
                        rows_out.append([row[0], *corrected.tolist()])
                        if event+view_start >= 0 and event+view_stop < len(y):
                            selection = slice(event+view_start, event+view_stop+1)
                            previews[label][0].append(y[selection])
                            previews[label][1].append(corrected[selection])
                            plotted = True
                    except ValueError as error:
                        if not args.keep_unprocessable:
                            raise ValueError(f'{path}:{line}: {error}') from error
                        status, reason = 'inalterata_non_elaborabile', str(error)
                        rows_out.append(row)
                    report.append([str(relative), row[0], label, event, offsets[label], status, reason, plotted])
            if args.write_csv:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('w', newline='') as stream:
                    csv.writer(stream).writerows(rows_out)
            print(relative)
        np.savetxt(staging / 'profilo_correzione.csv',
                   np.column_stack((np.arange(start, stop+1), weights,
                                    np.rint(args.induced_offset*weights),
                                    np.rint(args.autoinduced_offset*weights))),
                   delimiter=',', header='campione_relativo,peso,delta_induced,delta_autoinduced', comments='')
        with (staging / 'correzioni.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['file', 'run_id', 'classe', 'minimo_originale', 'offset_richiesto', 'stato', 'motivo', 'inclusa_plot'])
            writer.writerows(report)
        settings = vars(args).copy()
        settings.update(root=str(root), output=str(output), files=[str(p) for p in paths])
        (staging / 'parametri.json').write_text(json.dumps(settings, indent=2) + '\n')
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharex=True, sharey=True, constrained_layout=True)
        for ax, stage, title in zip(axes, (0, 1), ('Prima', 'Dopo')):
            for label, values in previews.items():
                if not values[stage]:
                    continue
                y = np.asarray(values[stage], dtype=float)
                mean = y.mean(axis=0)
                ax.plot(x, mean, color=COLORS[label], label=f'{label}: media (n={len(y)})')
                ax.plot(x, np.median(y, axis=0), color=COLORS[label], linestyle='--', label=f'{label}: mediana')
                if len(y) > 1:
                    sd = y.std(axis=0, ddof=1)
                    ax.fill_between(x, mean-sd, mean+sd, color=COLORS[label], alpha=.15)
            ax.axvspan(start, stop, color='gray', alpha=.1)
            ax.axvline(0, color='gray', linestyle=':')
            ax.set_title(title)
            ax.set_xlabel('Campioni dal minimo originale')
            ax.grid(alpha=.2)
            if ax.lines:
                ax.legend(fontsize=8)
        axes[0].set_ylabel('Codici ADC')
        fig.suptitle(f'{root.name}: offset Induced {args.induced_offset:+d}, Autoinduced {args.autoinduced_offset:+d}\nProfilo {args.profile}, finestra [{start}, {stop}] inclusiva; bande ±1 SD; solo tracce elaborabili con contesto completo')
        fig.savefig(staging / 'prima_dopo.png', dpi=160)
        plt.close(fig)
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    unchanged = sum(r[5] == 'inalterata_non_elaborabile' for r in report)
    print(f'Risultati: {output}\nTracce: {len(report)}; non elaborabili lasciate inalterate: {unchanged}')
    print('CSV corretti salvati in csv/.' if args.write_csv else 'Solo anteprima: usare --write-csv per salvare i CSV in una nuova cartella output.')


if __name__ == '__main__':
    main()
