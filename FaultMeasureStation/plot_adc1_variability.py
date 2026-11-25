#!/usr/bin/env python3
"""Confronta solo bodyguard_adc1.csv (numpy e matplotlib).

Esempi:
  python plot_adc1_variability.py
  python plot_adc1_variability.py --root Dataset/B1 --no-show
  python plot_adc1_variability.py --root Dataset/B1/K1V1 --start 300 --stop 900

Allineamento opzionale (--align-reset) sul minimo precedente il plateau alto.
Nessuna normalizzazione o interpolazione. Ogni traccia pesa allo stesso modo.
Le bande percentile descrivono le tracce, non l'incertezza della media.
"""
import argparse
import csv
from pathlib import Path

import numpy as np

CLASSES = {'Induced': 'indotto', 'Induced_Traces': 'indotto', 'Induced_Taces': 'indotto',
           'Autoinduced': 'autoindotto', 'Autoinduced_Traces': 'autoindotto'}
COLORS = {'indotto': 'tab:blue', 'autoindotto': 'tab:orange'}


def load(root, skip_invalid=False, identities=None, paths=None):
    groups = {}
    length = None
    for path in sorted(root.rglob('bodyguard_adc1.csv') if paths is None else paths):
        if 'filter_reports' in path.parts or path.parent.name not in CLASSES:
            continue
        label = CLASSES[path.parent.name]
        group = path.parent.parent.relative_to(root).as_posix()
        rows, seen = [], set()
        rejected = 0
        with path.open(newline='') as stream:
            for line, row in enumerate(csv.reader(stream), 1):
                if not row:
                    continue
                if row[0] == 'TIME':
                    continue
                if not row[0] or row[0] in seen:
                    raise ValueError(f'{path}:{line}: ID vuoto o duplicato')
                seen.add(row[0])
                y = np.asarray(row[1:], dtype=float)
                if (not y.size or not np.isfinite(y).all() or
                        np.any((y < 0) | (y > 255) | (y != np.floor(y)))):
                    if not skip_invalid:
                        raise ValueError(f'{path}:{line}: campioni ADC non validi; usare --skip-invalid per escluderli')
                    rejected += 1
                    continue
                if length is None:
                    length = y.size
                if y.size != length:
                    raise ValueError(f'{path}:{line}: {y.size} campioni, attesi {length}; '
                                     'selezionare acquisizioni con lunghezze omogenee')
                rows.append(y.astype(np.uint8))
                if identities is not None:
                    identities.setdefault((group, label), []).append((str(path), row[0]))
        if rejected:
            print(f'ATTENZIONE: {path}: escluse {rejected} tracce con campioni ADC non validi')
        if rows:
            groups.setdefault(group, {}).setdefault(label, []).append(np.stack(rows))
            print(f'{group:20s} {label:12s} {len(rows):5d} tracce')
    if not groups:
        raise ValueError(f'Nessun bodyguard_adc1.csv di classe riconosciuta in {root}')
    return {g: {c: np.concatenate(arrays) for c, arrays in classes.items()}
            for g, classes in groups.items()}


def reset_index(y, search=250, hold=40, threshold=245, min_depth=8):
    """Find a persistent high plateau, then its preceding local minimum.

    A plateau starts with `hold` high samples and has >=98% high samples
    through the end of the trace. Search only `search` samples before it,
    so earlier unrelated minima cannot anchor the alignment. On tied minima,
    use the last sample (closest to the rising edge). No smoothing is applied.
    """
    high = y >= threshold
    if len(y) < hold or np.median(y[-hold:]) < threshold:
        raise ValueError('plateau finale alto assente')
    sums = np.concatenate(([0], np.cumsum(high)))
    starts = np.flatnonzero(sums[hold:] - sums[:-hold] == hold)
    starts = starts[starts > 0]
    starts = starts[(sums[-1] - sums[starts]) / (len(y) - starts) >= .98]
    if not len(starts):
        raise ValueError('plateau persistente assente')
    plateau = int(starts[0])
    left = max(0, plateau - search)
    segment = y[left:plateau]
    index = left + int(np.flatnonzero(segment == segment.min())[-1])
    if index == left:
        raise ValueError('minimo sul bordo della finestra di ricerca')
    if float(np.median(y[plateau:])) - float(y[index]) < min_depth:
        raise ValueError('minimo non sufficientemente profondo rispetto al plateau')
    return index, plateau


def align_groups(groups, identities, args):
    """Crop integer-shifted traces to the same requested window; no padding."""
    aligned, report = {}, []
    for group, classes in groups.items():
        for label, traces in classes.items():
            kept = []
            for y, (path, run_id) in zip(traces, identities[group, label]):
                event = plateau = ''
                try:
                    event, plateau = reset_index(y, args.reset_search, args.plateau_hold,
                                                 args.plateau_threshold, args.min_depth)
                    if event < args.pre or event + args.post >= len(y):
                        raise ValueError('copertura insufficiente della finestra pre/post')
                    kept.append(y[event-args.pre:event+args.post+1].astype(float))
                    reason = ''
                except ValueError as error:
                    reason = str(error)
                report.append([group, label, path, run_id, event, plateau,
                               'esclusa' if reason else 'inclusa', reason])
            if kept:
                aligned.setdefault(group, {})[label] = np.stack(kept)
            print(f'Allineamento {group} {label}: {len(kept)}/{len(traces)} tracce incluse')
    with (args.output / 'allineamento_reset.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['configurazione', 'classe', 'file', 'run_id', 'indice_minimo',
                         'inizio_plateau', 'stato', 'motivo'])
        writer.writerows(report)
    return aligned


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent / 'Dataset')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'adc1_analysis')
    parser.add_argument('--max-traces', type=int, default=100, help='Tracce visualizzate per classe; statistiche su tutte')
    parser.add_argument('--skip-invalid', action='store_true', help='Esclude intere tracce con campioni fuori range ADC 8 bit, segnalando i conteggi')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--start', type=int, default=0, help='Primo campione incluso')
    parser.add_argument('--stop', type=int, help='Ultimo campione escluso')
    parser.add_argument('--align-reset', action='store_true', help='Allinea sul minimo precedente il plateau alto')
    parser.add_argument('--pre', type=int, default=500, help='Campioni prima del minimo (allineamento)')
    parser.add_argument('--post', type=int, default=500, help='Campioni dopo il minimo (allineamento)')
    parser.add_argument('--reset-search', type=int, default=250, help='Finestra di ricerca del minimo prima del plateau')
    parser.add_argument('--plateau-hold', type=int, default=40, help='Campioni consecutivi sopra soglia per confermare il plateau')
    parser.add_argument('--plateau-threshold', type=float, default=245, help='Soglia ADC del plateau alto')
    parser.add_argument('--min-depth', type=float, default=8, help='Profondità minima rispetto al plateau, in codici ADC')
    parser.add_argument('--no-show', action='store_true', help='Salva i PNG senza aprire finestre')
    args = parser.parse_args()
    if args.max_traces < 1:
        parser.error('--max-traces deve essere positivo')
    if (args.pre < 0 or args.post < 0 or args.pre + args.post < 1 or
            args.reset_search < 2 or args.plateau_hold < 1 or
            not 0 < args.plateau_threshold <= 255 or not 0 < args.min_depth <= 255):
        parser.error('Parametri di allineamento non validi')
    if args.align_reset and (args.start != 0 or args.stop is not None):
        parser.error('Con --align-reset usare --pre/--post al posto di --start/--stop')
    import matplotlib
    if args.no_show:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    try:
        identities = {}
        groups = load(args.root.resolve(), args.skip_invalid, identities)
    except ValueError as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    if args.align_reset:
        groups = align_groups(groups, identities, args)
        x = np.arange(-args.pre, args.post + 1)
        xlabel = 'Campioni rispetto al minimo di reset (0)'
        mode = 'allineamento sul minimo di reset'
        suffix = '_allineato'
    else:
        n = next(iter(next(iter(groups.values())).values())).shape[1]
        stop = n if args.stop is None else args.stop
        if not 0 <= args.start < stop <= n:
            parser.error(f'Intervallo non valido: richiesto 0 <= start < stop <= {n}')
        groups = {g: {c: y[:, args.start:stop].astype(float) for c, y in cs.items()}
                  for g, cs in groups.items()}
        x = np.arange(args.start, stop)
        xlabel, mode, suffix = 'Indice campione', 'senza allineamento', ''
    data = {c: np.concatenate([cs[c] for cs in groups.values() if c in cs])
            for c in COLORS if any(c in cs for cs in groups.values())}
    if len(data) != 2:
        parser.error('Servono entrambe le classi con tracce utilizzabili; consultare il report di allineamento se attivo')
    rng = np.random.default_rng(args.seed)
    fig, axes = plt.subplots(3, 2, figsize=(15, 12), sharex=True, constrained_layout=True)
    for ax, (label, y) in zip(axes[0], data.items()):
        indices = rng.choice(len(y), min(args.max_traces, len(y)), replace=False)
        ax.plot(x, y[indices].T, color=COLORS[label], alpha=.12, linewidth=.6)
        ax.plot(x, np.median(y, axis=0), color='black', linewidth=1, label='Mediana')
        ax.set_title(f'{label}: {len(y)} tracce ({len(indices)} mostrate)')
        ax.legend()
    limits = (min(y.min() for y in data.values()) - 3, max(y.max() for y in data.values()) + 3)
    for ax in axes[0]:
        ax.set_ylim(limits)
    stats = {}
    for label, y in data.items():
        color = COLORS[label]
        mean, sd = y.mean(axis=0), y.std(axis=0, ddof=1) if len(y) > 1 else np.full(y.shape[1], np.nan)
        stats[label] = (mean, sd)
        lo, med, hi = np.percentile(y, [5, 50, 95], axis=0)
        axes[1, 0].plot(x, med, color=color, label=label)
        axes[1, 0].fill_between(x, lo, hi, color=color, alpha=.2)
        axes[1, 1].plot(x, sd, color=color, label=label)
    a, sa = stats['indotto']
    b, sb = stats['autoindotto']
    delta = a - b
    pooled = np.sqrt(((len(data['indotto'])-1)*sa**2 + (len(data['autoindotto'])-1)*sb**2) /
                     max(1, len(data['indotto']) + len(data['autoindotto']) - 2))
    effect = np.divide(delta, pooled, out=np.full_like(delta, np.nan), where=pooled > 0)
    axes[2, 0].plot(x, delta, color='purple')
    axes[2, 0].axhline(0, color='gray', linewidth=.8)
    axes[2, 1].plot(x, effect, color='purple')
    axes[2, 1].axhline(0, color='gray', linewidth=.8)
    for ax, title in zip(axes.flat, ['', '', 'Mediana e intervallo 5–95% delle tracce',
                        'Deviazione standard per campione', 'Media indotto − media autoindotto',
                        'Differenza medie / deviazione standard pooled']):
        if title:
            ax.set_title(title)
        ax.grid(alpha=.2)
        if args.align_reset:
            ax.axvline(0, color='gray', linestyle='--', linewidth=.8)
        ax.set_ylabel('Codici ADC' if ax is not axes[2, 1] else 'Differenza standardizzata')
    for ax in axes[1]:
        ax.legend()
    for ax in axes[2]:
        ax.set_xlabel(xlabel)
    fig.suptitle(f'bodyguard_adc1 — {mode}; statistiche su tutte le tracce incluse')
    fig.savefig(args.output / f'confronto_classi{suffix}.png', dpi=160)

    fig_summary, summary_axes = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, sharey=True, constrained_layout=True)
    for label, y in data.items():
        legend = f'{label} (n={len(y)})'
        summary_axes[0].plot(x, stats[label][0], color=COLORS[label],
                             linewidth=1.8, label=legend)
        summary_axes[1].plot(x, np.median(y, axis=0), color=COLORS[label],
                             linewidth=1.8, label=legend)
    for ax, title in zip(summary_axes, ('Media', 'Mediana')):
        ax.set_title(title)
        ax.set_ylabel('Codici ADC')
        ax.grid(alpha=.2)
        ax.legend()
        if args.align_reset:
            ax.axvline(0, color='gray', linestyle='--', linewidth=.8)
    summary_axes[-1].set_xlabel(xlabel)
    fig_summary.suptitle(
        f'bodyguard_adc1 — {mode}\nMedia e mediana su tutte le tracce incluse per classe')
    fig_summary.savefig(args.output / f'media_mediana_classi{suffix}.png', dpi=160)

    count = len(groups)
    fig2, axs = plt.subplots((count+2)//3, 3, figsize=(16, 3.1*((count+2)//3)),
                             sharex=True, sharey=True, squeeze=False, constrained_layout=True)
    for ax, (group, classes) in zip(axs.flat, groups.items()):
        for label, y in classes.items():
            low, med, high = np.percentile(y, [5, 50, 95], axis=0)
            ax.plot(x, med, color=COLORS[label], label=f'{label} n={len(y)}')
            ax.fill_between(x, low, high, color=COLORS[label], alpha=.18)
        ax.set_title(group)
        ax.set_xlabel(xlabel)
        ax.set_ylabel('Codici ADC')
        ax.legend(fontsize=8)
        ax.grid(alpha=.2)
        if args.align_reset:
            ax.axvline(0, color='gray', linestyle='--', linewidth=.8)
    for ax in list(axs.flat)[count:]:
        ax.set_visible(False)
    fig2.suptitle(f'Confronto per configurazione — {mode} — mediana e percentili 5–95%')
    fig2.savefig(args.output / f'confronto_configurazioni{suffix}.png', dpi=140)
    with (args.output / f'riepilogo{suffix}.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['configurazione', 'classe', 'n_tracce', 'n_campioni',
                         'media_ADC', 'SD_intra_classe_RMS', 'picco_picco_mediano'])
        for group, classes in [('TOTALE', data), *groups.items()]:
            for label, y in classes.items():
                sd = np.sqrt(np.mean(np.var(y, axis=0, ddof=1))) if len(y)>1 else float('nan')
                writer.writerow([group, label, *y.shape, y.mean(), sd, np.median(np.ptp(y, axis=1))])
    print(f'\nRisultati: {args.output.resolve()}')
    print(f'Distanza RMS fra le medie: {np.sqrt(np.mean(delta**2)):.3f} codici ADC')
    print('Bande = dispersione delle tracce, non intervalli di confidenza. '
          'Il confronto globale pesa ogni traccia ugualmente e può risentire della composizione dei gruppi.')
    print('Differenza standardizzata non definita dove la variabilità pooled è zero o non stimabile.')
    if not args.no_show:
        plt.show()
    plt.close('all')


if __name__ == '__main__':
    main()
