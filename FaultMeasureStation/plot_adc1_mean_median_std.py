#!/usr/bin/env python3
"""Unico plot ADC1: media, mediana e ±1 SD per Autoinduced e Induced.

Richiede numpy, matplotlib e plot_adc1_variability.py nella stessa cartella.
Con --root cerca ricorsivamente i bodyguard_adc1.csv nella cartella scelta.
Senza --root legge solo le cartelle di classe direttamente in FaultMeasureStation.
Esclude filter_reports; riconosce Induced, Autoinduced e le varianti _Traces.
Allinea al minimo di reset per default; --no-align mostra i campioni originali.
--samples-around N mostra N campioni per lato di t_recovery (minimo di reset),
includendo il campione centrale: 2*N+1 campioni totali.
Le bande usano la SD campionaria delle tracce a ogni campione (ddof=1),
non l'errore standard né un intervallo di confidenza della media/mediana.
"""
import argparse
from pathlib import Path

import numpy as np

from plot_adc1_variability import CLASSES, align_groups, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument('--root', type=Path,
                        help='Percorso da analizzare ricorsivamente: board, sottogruppo o dataset. Se omesso: solo classi direttamente in FaultMeasureStation')
    parser.add_argument('--output', type=Path, default=base / 'adc1_mean_median_std')
    parser.add_argument('--skip-invalid', action='store_true', help='Esclude e segnala tracce ADC non valide')
    parser.add_argument('--no-align', action='store_true', help='Disabilita allineamento al reset')
    parser.add_argument('--samples-around', type=int, metavar='N',
                        help='N campioni prima e dopo t_recovery (minimo di reset): 2*N+1 totali')
    parser.add_argument('--pre', type=int, help='Campioni prima del minimo (default: 500)')
    parser.add_argument('--post', type=int, help='Campioni dopo il minimo (default: 500)')
    parser.add_argument('--reset-search', type=int, default=250)
    parser.add_argument('--plateau-hold', type=int, default=40)
    parser.add_argument('--plateau-threshold', type=float, default=245)
    parser.add_argument('--min-depth', type=float, default=8)
    parser.add_argument('--no-show', action='store_true', help='Salva senza aprire la finestra')
    args = parser.parse_args()
    if args.samples_around is not None:
        if args.no_align:
            parser.error('--samples-around richiede allineamento: rimuovere --no-align')
        if args.pre is not None or args.post is not None:
            parser.error('Usare --samples-around oppure --pre/--post, non entrambi')
        if args.samples_around < 1:
            parser.error('--samples-around deve essere positivo')
        args.pre = args.post = args.samples_around
    args.pre = 500 if args.pre is None else args.pre
    args.post = 500 if args.post is None else args.post
    if (args.pre < 0 or args.post < 0 or args.pre + args.post < 1 or
            args.reset_search < 2 or args.plateau_hold < 1 or
            not 0 < args.plateau_threshold <= 255 or not 0 < args.min_depth <= 255):
        parser.error('Parametri di allineamento non validi')
    identities = {}
    try:
        root = (args.root if args.root is not None else base).expanduser().resolve()
        if not root.is_dir():
            parser.error(f'Cartella inesistente: {root}')
        candidates = root.rglob('bodyguard_adc1.csv') if args.root is not None else root.glob('*/bodyguard_adc1.csv')
        paths = sorted(path for path in candidates
                       if path.parent.name in CLASSES and
                       'filter_reports' not in path.relative_to(root).parts)
        if not paths:
            parser.error(f'Nessun bodyguard_adc1.csv di classe riconosciuta in {root}')
        print(f'Percorso selezionato: {root}')
        print(f'CSV inclusi ({len(paths)}):')
        for path in paths:
            print(f'  {path.relative_to(root)}')
        groups = load(root, args.skip_invalid, identities, paths=paths)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'sorgenti.txt').write_text(
        f'Percorso selezionato: {root}\n' + '\n'.join(str(path) for path in paths) + '\n')
    if not args.no_align:
        groups = align_groups(groups, identities, args)
    data = {}
    for label in ('autoindotto', 'indotto'):
        arrays = [classes[label] for classes in groups.values() if label in classes]
        if not arrays or sum(len(y) for y in arrays) < 2:
            parser.error(f'Servono almeno due tracce utilizzabili per {label}')
        data[label] = np.concatenate(arrays).astype(float)

    import matplotlib
    if args.no_show:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    n = next(iter(data.values())).shape[1]
    x = np.arange(n) if args.no_align else np.arange(-args.pre, args.post + 1)
    fig, ax = plt.subplots(figsize=(14, 7), constrained_layout=True)
    exported = [x]
    columns = ['campione']
    for label, english, color in (('autoindotto', 'Autoinduced', 'tab:orange'),
                                   ('indotto', 'Induced', 'tab:blue')):
        y = data[label]
        mean, median, sd = y.mean(axis=0), np.median(y, axis=0), y.std(axis=0, ddof=1)
        ax.fill_between(x, mean-sd, mean+sd, color=color, alpha=.15,
                        label=f'{english}: media ±1 SD')
        ax.fill_between(x, median-sd, median+sd, facecolor='none',
                        edgecolor=color, hatch='///', linewidth=0, alpha=.22,
                        label=f'{english}: mediana ±1 SD')
        ax.plot(x, mean, color=color, linewidth=2,
                label=f'{english}: media (n={len(y)})')
        ax.plot(x, median, color=color, linewidth=2, linestyle='--',
                label=f'{english}: mediana')
        exported.extend((mean, median, sd))
        columns.extend(f'{english}_{stat}' for stat in ('media', 'mediana', 'std'))
    if not args.no_align:
        ax.axvline(0, color='gray', linestyle=':', linewidth=1)
    ax.set_xlabel('Indice campione' if args.no_align else 'Campioni rispetto al minimo di reset (0)')
    ax.set_ylabel('Codici ADC')
    mode = 'dati non allineati' if args.no_align else 'allineamento al minimo di reset'
    ax.set_title(f'bodyguard_adc1 — {root.name} — {mode}\nMedia, mediana e ±1 deviazione standard di tutte le tracce incluse')
    ax.grid(alpha=.2)
    ax.legend(ncol=2, fontsize=9, loc='best')
    fig.supxlabel('Bande: dispersione delle tracce; non intervalli di confidenza. Ogni traccia ha lo stesso peso.', fontsize=9)
    stem = 'media_mediana_std' + ('_grezzo' if args.no_align else '_allineato')
    fig.savefig(args.output / f'{stem}.png', dpi=180)
    np.savetxt(args.output / f'{stem}.csv', np.column_stack(exported),
               delimiter=',', header=','.join(columns), comments='')
    print(f'Grafico: {(args.output / (stem + ".png")).resolve()}')
    if not args.no_show:
        plt.show()
    plt.close(fig)


if __name__ == '__main__':
    main()
