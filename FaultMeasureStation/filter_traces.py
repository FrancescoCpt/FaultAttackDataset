#!/usr/bin/env python3
"""Offline, non-destructive CH2 reset / CH3 glitch screening of acquisition CSVs."""
import argparse
import csv
import html
import json
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from characterize import pulses, trace


@dataclass
class Settings:
    smooth_us: float = 0.1
    low_hold_us: float = 2.0
    square_max_us: float = 0.5
    reset_min_us: float = 2.0
    timing_margin_us: float = 0.2
    min_amplitude_v: float = 0.5
    ch3_level: float = 0.5
    ch3_hysteresis: float = 0.2
    ch3_min_width_us: float = 0.5


class CsvTraces:
    """Index row offsets by Run ID, without loading the dataset into memory."""
    def __init__(self, path):
        self.path = Path(path)
        self.offsets = {}
        with self.path.open() as f:
            head = next(csv.reader([f.readline()]))
            if head[0] != 'TIME':
                raise ValueError(f'{path}: prima riga TIME mancante')
            self.t = np.asarray(head[1:], float)
            trace(self.t, np.zeros_like(self.t))
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                run_id = next(csv.reader([line]))[0]
                if run_id in self.offsets:
                    raise ValueError(f'{path}: Run ID duplicato: {run_id}')
                self.offsets[run_id] = offset

    def read(self, run_id):
        with self.path.open() as f:
            f.seek(self.offsets[run_id])
            row = next(csv.reader([f.readline()]))
        return trace(self.t, np.asarray(row[1:], float))


def smooth(y, count):
    count = max(1, int(count) | 1)
    if count > len(y):
        raise ValueError('Finestra smoothing maggiore della traccia')
    padded = np.pad(y, (count//2, count//2), mode='edge')
    sums = np.concatenate(([0.], np.cumsum(padded)))
    return (sums[count:]-sums[:-count])/count


def crossing(t, y, i, level):
    return float(t[i-1] + (level-y[i-1])*(t[i]-t[i-1])/(y[i]-y[i-1]))


def reset_features(t, y, cfg):
    t, y = trace(t, y)
    dt = np.median(np.diff(t))
    z = smooth(y, round(cfg.smooth_us*1e-6/dt))
    baseline = t < 0
    if np.count_nonzero(baseline) < 5:
        raise ValueError('Pretrigger CH2 insufficiente per il livello basso')
    low = float(np.median(z[baseline]))
    high = float(np.percentile(z[t>=0], 95))
    amplitude = high-low
    if amplitude < cfg.min_amplitude_v:
        raise ValueError('Ampiezza CH2 insufficiente')
    upper, lower = low+.9*amplitude, low+.1*amplitude
    # Require a complete AES marker rise, then seek its final sustained fall.
    rises = np.flatnonzero((z[:-1]<=upper)&(z[1:]>upper))+1
    rises = rises[t[rises]>=-cfg.smooth_us*1e-6]
    if not len(rises):
        raise ValueError('Salita CH2 non trovata')
    start = rises[0]
    falls = np.flatnonzero((z[:-1]>lower)&(z[1:]<=lower))+1
    candidates = []
    for j in falls:
        if j <= start:
            continue
        end = np.searchsorted(t, t[j]+cfg.low_hold_us*1e-6)
        if end < len(t) and np.all(z[j:end+1]<=lower):
            candidates.append(j)
    if len(candidates) != 1:
        raise ValueError(f'Discesa CH2 sostenuta assente o ambigua ({len(candidates)})')
    j = candidates[0]
    upper_falls = np.flatnonzero((z[:-1]>upper)&(z[1:]<=upper))+1
    upper_falls = upper_falls[(upper_falls>start)&(upper_falls<=j)]
    if not len(upper_falls):
        raise ValueError('Inizio discesa CH2 non trovato')
    i = upper_falls[-1]
    t90, t10 = crossing(t,z,i,upper), crossing(t,z,j,lower)
    return dict(reset_us=t90*1e6, fall_end_us=t10*1e6, fall_us=(t10-t90)*1e6,
                low_v=low, high_v=high, threshold90_v=upper, threshold10_v=lower,
                sample_us=float(dt*1e6))


def classify(t, y, cfg, mode, ch3=None):
    r = dict(status='review', reason='')
    try:
        r.update(reset_features(t,y,cfg))
        if r['sample_us']*3 > cfg.square_max_us:
            raise ValueError('Risoluzione temporale insufficiente per distinguere la discesa rapida')
        if r['fall_us'] <= cfg.square_max_us:
            r.update(status='discard', reason='CH2 a onda quadra: reset non confermato')
            return r
        if r['fall_us'] < cfg.reset_min_us:
            raise ValueError('Durata discesa CH2 intermedia: reset incerto')
        if mode == 'induced':
            if ch3 is None:
                raise ValueError('CH3 mancante per questo Run ID')
            t3,y3 = trace(*ch3)
            pp = [(a,b) for a,b in pulses(t3,y3,cfg.ch3_level,cfg.ch3_hysteresis) if a>=0 and (b-a)*1e6>=cfg.ch3_min_width_us]
            if len(pp)!=1:
                raise ValueError(f'Impulso CH3 assente o ambiguo ({len(pp)})')
            r['glitch_us'] = pp[0][0]*1e6
            r['reset_minus_glitch_us'] = r['reset_us']-r['glitch_us']
            if r['reset_minus_glitch_us'] < -cfg.timing_margin_us:
                r.update(status='discard',reason='Glitch successivo al reset stimato')
                return r
            if r['reset_minus_glitch_us'] <= cfg.timing_margin_us:
                raise ValueError('Glitch e reset troppo vicini: ordine temporale incerto')
        r.update(status='keep',reason='Compatibile con i criteri richiesti')
    except ValueError as e:
        r.update(status='review',reason=str(e))
    return r


def plot(path, t, y, ch3, r, cfg):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(11,4))
    ax.plot(t*1e6,y,lw=.5,label='CH2 raw')
    ax.plot(t*1e6,smooth(y,round(cfg.smooth_us*1e-6/np.median(np.diff(t)))),lw=1,label='CH2 filtrato')
    if ch3 is not None:
        ax.plot(ch3[0]*1e6,ch3[1],lw=.6,label='CH3')
    if 'reset_us' in r:
        ax.axvspan(r['reset_us'],r['fall_end_us'],alpha=.15,color='green',label='Discesa 90–10%')
        ax.axvline(r['reset_us'],color='green',ls='--',label='Reset stimato (90%)')
    if 'glitch_us' in r:
        ax.axvline(r['glitch_us'],color='magenta',ls='--',label='Glitch')
    ax.set(xlabel='Tempo [µs]',ylabel='Tensione [V]',title=r['run_id']+' — '+r['reason'])
    ax.legend(fontsize=8)
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(path,dpi=110)
    plt.close(fig)


def export_filtered(folder, out, rows, max_good):
    """Copy selected raw CSV rows, aligned by ID; never modify source files."""
    paths = sorted(folder.glob('scope_ch*.csv')) + sorted(folder.glob('bodyguard_adc*.csv'))
    indexes = {}
    headers = {}
    for path in paths:
        offsets = {}
        with path.open(newline='') as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                run_id = next(csv.reader([line]))[0]
                if run_id == 'TIME':
                    if path.name in headers or offsets:
                        raise ValueError(f'{path}: riga TIME duplicata o non iniziale')
                    headers[path.name] = line
                elif run_id in offsets:
                    raise ValueError(f'{path}: Run ID duplicato {run_id}')
                else:
                    offsets[run_id] = offset
        if path.name.startswith('scope_') and path.name not in headers:
            raise ValueError(f'{path}: riga TIME mancante')
        indexes[path.name] = offsets
    selected = []
    decisions = []
    print(f'Report per verificare le review: {out / "report.html"}')
    for r in rows:
        rid = r['run_id']
        decision = 'discard'
        if r['status'] != 'discard':
            if any(rid not in offsets for offsets in indexes.values()):
                decision = 'missing_channel'
            elif len(selected) >= max_good:
                decision = 'limit'
            elif r['status'] == 'review':
                print(f"REVIEW {rid}: {r['reason']}")
                if r.get('plot'):
                    print(f"Grafico: {out / r['plot']}")
                while True:
                    try:
                        answer = input('Includere questa traccia nei CSV filtrati? [s/n]: ').strip().lower()
                    except EOFError as e:
                        raise ValueError('Decisione review mancante: esportazione annullata; report conservati.') from e
                    if answer in ('s', 'si', 'sì', 'y', 'yes', 'n', 'no'):
                        break
                decision = 'review_accepted' if answer in ('s','si','sì','y','yes') else 'review_rejected'
                if decision == 'review_accepted':
                    selected.append(rid)
            else:
                selected.append(rid)
                decision = 'keep'
        decisions.append(dict(run_id=rid, status=r['status'], export_decision=decision))
    # Decide first; no partial export is created while waiting for review answers.
    target = out / 'filtered'
    target.mkdir()
    for path in paths:
        with path.open(newline='') as src, (target/path.name).open('w', newline='') as dest:
            if path.name in headers:
                dest.write(headers[path.name])
            for rid in selected:
                src.seek(indexes[path.name][rid])
                line = src.readline()
                dest.write(line if line.endswith('\n') else line+'\n')
    with (out/'selection.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['run_id','status','export_decision'])
        w.writeheader()
        w.writerows(decisions)
    result=dict(max_good=max_good, exported=len(selected), run_ids=selected,
                decisions=dict(Counter(r['export_decision'] for r in decisions)),
                files=[p.name for p in paths])
    print(f'Esportate {len(selected)}/{max_good} tracce in {target}')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('folder',type=Path,help='Cartella contenente scope_ch2.csv e, per indotto, scope_ch3.csv')
    p.add_argument('--mode',choices=['auto','induced'],required=True)
    p.add_argument('--out-dir',type=Path)
    p.add_argument('--max-good',type=int,default=100,help='Massimo numero di tracce esportate, comprese le review accettate')
    p.add_argument('--plots',type=int,default=20,help='Massimo grafici scarto/revisione; -1 per tutti, 0 nessuno')
    for k,v in asdict(Settings()).items():
        p.add_argument('--'+k.replace('_','-'),type=float,default=v)
    a=p.parse_args(argv)
    cfg=Settings(**{k:getattr(a,k) for k in asdict(Settings())})
    if (not all(np.isfinite(v) and v>=0 for v in asdict(cfg).values())
            or not 0<cfg.square_max_us<cfg.reset_min_us or cfg.low_hold_us<=0
            or cfg.min_amplitude_v<=0 or cfg.ch3_level<=cfg.ch3_hysteresis/2 or a.plots < -1 or a.max_good < 1):
        p.error('Soglie non valide')
    ch2=CsvTraces(a.folder/'scope_ch2.csv')
    ch3=CsvTraces(a.folder/'scope_ch3.csv') if (a.folder/'scope_ch3.csv').exists() and a.mode=='induced' else None
    out=a.out_dir or a.folder/'filter_reports'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True,exist_ok=False)
    rows=[]
    images=0
    for n,run_id in enumerate(ch2.offsets,1):
        extra=None
        r=dict(run_id=run_id,source_row=n+1)
        try:
            t,y=ch2.read(run_id)
            if ch3 is not None and run_id in ch3.offsets:
                extra=ch3.read(run_id)
            r.update(classify(t,y,cfg,a.mode,extra))
            if r['status']!='keep' and (a.plots==-1 or images<a.plots):
                r['plot']=f'trace_{n:05d}.png'
                plot(out/r['plot'],t,y,extra,r,cfg)
                images+=1
        except ValueError as e:
            r.update(status='review',reason=str(e))
        rows.append(r)
        if r['status']!='keep':
            print(f"{r['status'].upper():7} {run_id}: {r['reason']}")
    counts=Counter(r['status'] for r in rows)
    summary=dict(mode=a.mode,source=str(a.folder.resolve()),settings=asdict(cfg),total=len(rows),
                 counts={k:counts[k] for k in ('keep','discard','review')},
                 percentages={k:100*counts[k]/len(rows) if rows else 0 for k in ('keep','discard','review')},
                 reasons=dict(Counter(r['reason'] for r in rows)),
                 ch3_only_ids=sorted(set(ch3.offsets)-set(ch2.offsets)) if ch3 else [])
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    fields=list(dict.fromkeys(k for r in rows for k in r)) or ['run_id','status','reason']
    for filename,subset in [('results.csv',rows),('discard.csv',[r for r in rows if r['status']=='discard']),('review.csv',[r for r in rows if r['status']=='review'])]:
        with (out/filename).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(subset)
    content=['<!doctype html><meta charset="utf-8"><title>Filtro tracce</title>',
             '<style>body{font:16px sans-serif;max-width:1200px;margin:30px auto}td,th{padding:8px;border-bottom:1px solid #ddd}img{max-width:100%}</style>',
             '<h1>Filtro tracce — '+html.escape(a.mode)+'</h1>',
             '<p>'+html.escape(str(summary['counts']))+f" — Totale: {len(rows)}</p>",
             '<p>Analisi non distruttiva. KEEP significa compatibilità con i criteri; la precedenza del glitch non dimostra causalità. REVIEW richiede verifica manuale.</p>',
             '<p>Soglie provvisorie configurabili; reset stimato all’inizio della discesa finale CH2 (90%).</p>',
             '<p><a href="results.csv">Tutti i risultati</a> · <a href="discard.csv">Scarti</a> · <a href="review.csv">Da verificare</a></p>',
             '<table><tr><th>Run ID</th><th>Esito</th><th>Motivo</th><th>Discesa µs</th><th>Reset − glitch µs</th></tr>']
    for r in rows:
        content.append('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in ('run_id','status','reason','fall_us','reset_minus_glitch_us'))+'</tr>')
    content.append('</table><h2>Grafici degli scarti e dei casi da verificare</h2>')
    for r in rows:
        if 'plot' in r:
            content.append('<img loading="lazy" src="'+r['plot']+'">')
    (out/'report.html').write_text('\n'.join(content))
    print(json.dumps(summary['counts']),f'\nReport: {out / "report.html"}')
    summary['export'] = export_filtered(a.folder, out, rows, a.max_good)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (out/'report.html').open('a') as f:
        f.write(f"<p>Tracce esportate: {summary['export']['exported']} / {a.max_good}. "
                '<a href="selection.csv">Decisioni di esportazione</a>; CSV nella sottocartella filtered.</p>')
    return summary


if __name__=='__main__':
    main()
