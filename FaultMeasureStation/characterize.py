#!/usr/bin/env python3
"""Characterize reset latency and compare induced/self-induced CH1 minima."""
import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import acquire


@dataclass
class Analysis:
    ch3_level: float = 0.5
    ch3_hysteresis: float = 0.2
    window_start: float = 0.2
    window_end: float = 0.9
    reset_level: float = 3.0
    reset_hold_us: float = 2.0
    search_start_us: float = 0.0
    search_end_us: float = 160.0
    backtrack_half_us: float = 1.0


def trace(t, y):
    t, y = np.asarray(t, float), np.asarray(y, float)
    if (t.ndim != 1 or y.shape != t.shape or len(t) < 3
            or not np.all(np.isfinite(t)) or not np.all(np.isfinite(y))
            or np.any(np.diff(t) <= 0)):
        raise ValueError('Traccia vuota, non finita o asse temporale non crescente')
    return t, y


def pulses(t, y, level, hysteresis=0.0):
    """Complete Schmitt-trigger pulses; hysteresis is the full band in volts.

    Rise is interpolated at level + hysteresis/2; fall at level -
    hysteresis/2. A record starting above the lower threshold must first
    return low, so a truncated initial pulse is never counted.
    """
    upper, lower = level + hysteresis/2, level - hysteresis/2
    high = bool(y[0] > lower)
    start = None
    result = []
    edges = np.flatnonzero(
        ((y[:-1] <= upper) & (y[1:] > upper)) |
        ((y[:-1] > lower) & (y[1:] <= lower))) + 1
    for i in edges:
        if not high and y[i] > upper:
            start = float(t[i-1] + (upper-y[i-1]) * (t[i]-t[i-1]) / (y[i]-y[i-1]))
            high = True
        elif high and y[i] <= lower:
            if start is not None:
                end = float(t[i-1] + (lower-y[i-1]) * (t[i]-t[i-1]) / (y[i]-y[i-1]))
                result.append((start, end))
            start = None
            high = False
    return result


def recovery(t, y, cfg, after):
    """First rising crossing sustained above threshold for the entire hold."""
    candidates = np.flatnonzero((y[:-1] <= cfg.reset_level) & (y[1:] > cfg.reset_level)) + 1
    for i in candidates:
        if t[i] < max(after, cfg.search_start_us * 1e-6) or t[i] > cfg.search_end_us * 1e-6:
            continue
        end = np.searchsorted(t, t[i] + cfg.reset_hold_us * 1e-6)
        if end < len(t) and np.all(y[i:end+1] > cfg.reset_level):
            return int(i)
    raise ValueError('Ripresa CH1 non trovata (soglia/durata/finestra)')


def minimum(t, y, lo, hi):
    if lo < t[0] or hi > t[-1] or hi <= lo:
        raise ValueError('Finestra minimo fuori dalla traccia')
    ix = np.flatnonzero((t >= lo) & (t <= hi))
    if len(ix) < 3:
        raise ValueError('Campioni insufficienti nella finestra minimo')
    j = int(np.argmin(y[ix]))
    if j == 0 or j == len(ix)-1:
        raise ValueError('Minimo sul bordo: ampliare/verificare la finestra')
    return int(ix[j])


def analyze(t, y, t3, y3, cfg, dt_us=None):
    t, y = trace(t, y)
    t3, y3 = trace(t3, y3)
    pp = [(a, b) for a, b in pulses(t3, y3, cfg.ch3_level, cfg.ch3_hysteresis)
          if cfg.search_start_us * 1e-6 <= a <= cfg.search_end_us * 1e-6]
    if dt_us is None:
        if len(pp) != 1:
            raise ValueError(f'Atteso un impulso CH3 completo, trovati {len(pp)} '
                             f'(soglie salita/discesa: '
                             f'{cfg.ch3_level + cfg.ch3_hysteresis/2:g}/'
                             f'{cfg.ch3_level - cfg.ch3_hysteresis/2:g} V)')
        rise, fall = pp[0]
        lo = rise + cfg.window_start * (fall-rise)
        hi = rise + cfg.window_end * (fall-rise)
        m = minimum(t, y, lo, hi)
        r = recovery(t, y, cfg, fall)
    else:
        if np.any(y3 > cfg.ch3_level + cfg.ch3_hysteresis/2):
            raise ValueError('CH3 attivo in misura autoindotta: verificare attaccante')
        r = recovery(t, y, cfg, cfg.search_start_us * 1e-6)
        expected = t[r] - dt_us * 1e-6
        lo, hi = expected-cfg.backtrack_half_us*1e-6, expected+cfg.backtrack_half_us*1e-6
        if hi >= t[r]:
            raise ValueError('Finestra minimo sovrapposta alla ripresa')
        m = minimum(t, y, lo, hi)
        rise = fall = None
    return dict(t_min_us=float(t[m]*1e6), v_min=float(y[m]),
                t_reset_us=float(t[r]*1e6), v_reset=float(y[r]),
                dt_us=float((t[r]-t[m])*1e6),
                window_start_us=float(lo*1e6), window_end_us=float(hi*1e6),
                ch3_rise_us=None if rise is None else rise*1e6,
                ch3_fall_us=None if fall is None else fall*1e6)


def stats(values):
    x = np.asarray(values, float)
    return dict(n=len(x), mean=float(np.mean(x)), median=float(np.median(x)),
                std=float(np.std(x, ddof=1)) if len(x)>1 else 0.0,
                min=float(np.min(x)), max=float(np.max(x)))


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')


def load_summary(path, action):
    data = json.loads(Path(path).read_text())
    if data.get('action') != action or data.get('valid_runs', 0) < 2:
        raise ValueError(f'{path}: servono almeno due misure valide della modalità {action}')
    if not np.isfinite(data['dt_us']['median']) or data['dt_us']['median'] <= 0:
        raise ValueError(f'{path}: dt non valido')
    return data


def plot_run(meta, result, reference, title):
    plt.close('all')
    fig, ax = plt.subplots(figsize=(12, 6))
    t, y = meta['scope_adc1']
    t3, y3 = meta['scope_adc3']
    ax.plot(t*1e6, y, lw=.7, label='CH1')
    ax.plot(t3*1e6, y3, lw=.7, label='CH3')
    if result.get('status') == 'ok':
        ax.axvspan(result['window_start_us'], result['window_end_us'], alpha=.2)
        ax.plot(result['t_min_us'], result['v_min'], 'ro', fillstyle='none', ms=10)
        ax.plot(result['t_reset_us'], result['v_reset'], 'gs', fillstyle='none', ms=10)
        title += f"\nVmin={result['v_min']:.4f} V; dt={result['dt_us']:.3f} µs"
    else:
        title += '\nNON VALIDA: ' + result.get('error', '')
    if reference:
        s = reference['v_min']
        ax.axhline(s['median'], color='orange', label='Mediana minimi autoindotti')
        ax.axhspan(s['min'], s['max'], color='orange', alpha=.15, label='Intervallo autoindotto')
    ax.set(xlabel='Tempo [µs]', ylabel='Tensione [V]', title=title)
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    plt.show(block=False)
    plt.pause(.1)


class ArmedScope:
    """The real scope was armed before entering the existing protocol."""
    def __init__(self, scope):
        self.scope = scope
    def arm(self):
        pass
    def __getattr__(self, key):
        return getattr(self.scope, key)


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--action', choices=['1', '2', '3'])
    p.add_argument('-b', '--bodyguard', required=True)
    p.add_argument('-v', '--victim')
    p.add_argument('-a', '--attacker')
    p.add_argument('--runs', type=int, default=5, help='Esecuzioni per modalità 1/2; modalità 3 continua fino a q')
    p.add_argument('--calibration', help='summary.json della modalità 1')
    p.add_argument('--reference', help='summary.json della modalità 2, necessario per modalità 3')
    p.add_argument('--out-dir', type=Path, default=Path(__file__).resolve().parent/'Characterization')
    p.add_argument('--no-plot', action='store_true')
    p.add_argument('--scope', nargs='?', const=acquire.DEFAULT_SCOPE_RESOURCE, default=acquire.DEFAULT_SCOPE_RESOURCE)
    p.add_argument('--scope-time-div', type=float, default=acquire.DEF_TIME_DIV)
    p.add_argument('--scope-h-pos', type=float, default=acquire.DEF_H_POS)
    p.add_argument('--scope-volts-div', type=float, default=acquire.DEF_VOLTS_DIV)
    p.add_argument('--scope-trig-source', type=int, choices=[1,2,3,4], default=2)
    p.add_argument('--scope-trig-level', type=float, default=1.0)
    p.add_argument('--scope-timeout', type=float, default=10.0)
    for name, default in asdict(Analysis()).items():
        p.add_argument('--'+name.replace('_','-'), type=float, default=default)
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if a.action is None:
        print('1. Caratterizza dt con glitch (30 esecuzioni)\n2. Misura autoindotto senza armare\n3. Confronto osservazionale indotto / autoindotto')
        a.action = input('Azione [1/2/3]: ').strip()
    if a.action not in ('1','2','3'):
        p.error('Azione non valida')
    c = Analysis(**{k:getattr(a,k) for k in asdict(Analysis())})
    if (not all(np.isfinite(v) for v in asdict(c).values())
            or not 0 <= c.window_start < c.window_end <= 1
            or c.ch3_hysteresis < 0 or c.ch3_level <= c.ch3_hysteresis/2
            or c.reset_hold_us < 0 or c.backtrack_half_us <= 0
            or c.search_start_us >= c.search_end_us or a.runs < 2):
        p.error('Parametri analisi non validi; --runs deve essere >= 2')
    induced = a.action != '2'
    if induced and (not a.victim or not a.attacker):
        p.error('Modalità 1/3 richiedono --victim e --attacker')
    reference = calibration = None
    try:
        if a.action == '2':
            if not a.calibration:
                p.error('Modalità 2 richiede --calibration')
            calibration = load_summary(a.calibration, '1')
            # The definition of recovery must match the dt characterization.
            for k in ('reset_level','reset_hold_us'):
                if getattr(c,k) != calibration['analysis'][k]:
                    p.error(f'{k}: usare lo stesso valore della caratterizzazione')
        if a.action == '3':
            if not a.reference:
                p.error('Modalità 3 richiede --reference')
            reference = load_summary(a.reference, '2')
            if not sys.stdin.isatty():
                p.error('Modalità osservazionale richiede un terminale interattivo')
    except (OSError, ValueError, KeyError) as e:
        p.error(str(e))
    cfg = acquire.Config(protocol=acquire.PROTO_INDUCED if induced else acquire.PROTO_AUTO_INDUCED,
                         bodyguard_port=a.bodyguard, victim_port=a.victim, attacker_port=a.attacker,
                         scope_resource=a.scope, scope_time_div=a.scope_time_div,
                         scope_h_pos=a.scope_h_pos, scope_volts_div=a.scope_volts_div,
                         scope_trig_source=a.scope_trig_source, scope_trig_level=a.scope_trig_level,
                         scope_timeout_s=a.scope_timeout, show_plot=False)
    folder = a.out_dir / (datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_action'+a.action)
    folder.mkdir(parents=True)
    write_json(folder/'config.json', dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}, analysis=asdict(c), calibration=calibration, reference=reference))
    print(f'Risultati: {folder}')
    if not induced:
        print('Autoindotto: nessuna porta attaccante aperta, nessun comando di armamento inviato.\nL’attaccante deve essere già disarmato; CH3 attivo invalida la misura.')
    scope = None
    ports = {}
    results = []
    try:
        scope = acquire.setup_scope(cfg)
        if scope is None:
            raise RuntimeError('DSO obbligatorio: inizializzazione fallita')
        # Open incrementally so a failed later port does not leak earlier handles.
        for name, port, baud in [('bodyguard',a.bodyguard,acquire.BODYGUARD_BAUD)] + (
                [('victim',a.victim,acquire.VICTIM_BAUD),('attacker',a.attacker,acquire.ATTACKER_BAUD)] if induced else []):
            ports[name] = acquire._open(port, baud, name)
        time.sleep(.2)
        run = 0
        while a.action == '3' or run < a.runs:
            resistor = ''
            if a.action == '3':
                resistor = input('Regola il resistore; inserisci valore/nota e Invio per acquisire (q termina): ').strip()
                if resistor.lower() == 'q':
                    break
            run += 1
            run_id = f'run{run:03d}'
            print(f'\n{run_id}' + (f'/{a.runs}' if a.action != '3' else ''))
            # Fail before any serial start if scope.arm itself raises.
            scope.arm()
            adc1, adc2, meta = acquire.run_experiment(cfg, ports, ArmedScope(scope))
            row = dict(run_id=run_id, resistor=resistor, status='invalid', error='')
            arrays = dict(adc1=adc1, adc2=adc2)
            for key in ('scope_adc1','scope_adc2','scope_adc3'):
                if key in meta:
                    arrays[key+'_t'], arrays[key+'_v'] = meta[key]
            np.savez_compressed(folder/(run_id+'.npz'), **arrays)
            try:
                if not all(k in meta for k in ('scope_adc1','scope_adc3')):
                    raise ValueError('Acquisizione DSO incompleta')
                row.update(analyze(*meta['scope_adc1'], *meta['scope_adc3'], c,
                                   None if calibration is None else calibration['dt_us']['median']))
                row['status'] = 'ok'
                if reference:
                    row['difference_v'] = row['v_min']-reference['v_min']['median']
                print(f"Vmin={row['v_min']:.4f} V, dt={row['dt_us']:.3f} µs" +
                      (f", differenza da autoindotto={row['difference_v']:+.4f} V" if reference else ''))
            except ValueError as e:
                row['error'] = str(e)
                print('NON VALIDA:', e)
            results.append(row)
            write_json(folder/(run_id+'.json'), dict(result=row, serial={k:v for k,v in meta.items() if isinstance(v,(str,int,float,bool))}))
            fields = ['run_id','resistor','status','error','t_min_us','v_min','t_reset_us','v_reset','dt_us','window_start_us','window_end_us','ch3_rise_us','ch3_fall_us','difference_v']
            with (folder/'measurements.csv').open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(results)
            valid = [r for r in results if r['status']=='ok']
            summary = dict(action=a.action, analysis=asdict(c), attempted_runs=run, valid_runs=len(valid),
                           requested_runs=None if a.action=='3' else a.runs,
                           complete=a.action!='3' and run==a.runs,
                           calibration_source=a.calibration, reference_source=a.reference)
            if valid:
                summary.update({k:stats([r[k] for r in valid]) for k in ('dt_us','v_min')})
            write_json(folder/'summary.json', summary)
            if not a.no_plot and 'scope_adc1' in meta and 'scope_adc3' in meta:
                plot_run(meta, row, reference, run_id + (f' — R: {resistor}' if resistor else ''))
        print(f"\nMisure valide: {sum(r['status']=='ok' for r in results)}/{len(results)}. Risultati: {folder}")
        if not a.no_plot and results:
            plt.show()
    finally:
        acquire.close_ports(ports)
        if scope is not None:
            scope.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrotto; misure già salvate conservate.')
        sys.exit(130)
    except (RuntimeError, OSError, ValueError) as e:
        print(f'Errore: {e}', file=sys.stderr)
        sys.exit(1)
