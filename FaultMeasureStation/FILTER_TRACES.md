# Filtro offline delle tracce

Lo script legge i CSV di `acquire.py`, non comunica con le schede e non modifica né cancella i dati. Il risultato è una proposta di selezione basata su CH2 e, per l'indotto, CH3.

Dalla cartella `FaultMeasureStation`:

```sh
python3 filter_traces.py Dataset/Victim_2/Autoinduced --mode auto --plots -1
python3 filter_traces.py Dataset/Victim_7/Induced --mode induced --plots -1
```

La cartella deve contenere `scope_ch2.csv` e, per indotto, `scope_ch3.csv`. Formato: prima riga `TIME` seguita dai tempi in secondi; righe successive con Run ID e tensioni in volt. I canali vengono abbinati per **Run ID**, non per numero di riga. Assi diversi fra i canali sono ammessi; ogni asse deve essere crescente e coerente con i propri campioni. Duplicati di ID interrompono l'analisi per evitare abbinamenti arbitrari. File CH3 mancante o traccia malformata produce casi da verificare, non tracce accettate. L'universo delle statistiche è costituito dalle righe CH2; eventuali ID presenti soltanto su CH3 sono elencati in `summary.json`.

## Criteri iniziali configurabili

CH2 viene smussato con media mobile di 0,1 µs. Il livello basso è la mediana pretrigger (t < 0); il livello alto è il percentile 95 dei campioni a t >= 0. Si richiedono una salita e una discesa completa al 10% dell'ampiezza che rimanga bassa per 2 µs. Per la discesa finale si prende l'ultimo attraversamento discendente del 90% prima del 10%. Il tempo **90–10%** distingue:

- <= 0,5 µs: `discard`, onda quadra / reset non confermato.
- >= 2 µs: compatibile con il reset.
- Fra i due limiti, eventi ambigui o segnali insufficienti: `review`.

Il passaggio al 90% è la **stima dell'inizio reset**, concordata per questo esperimento, non una misura diretta dell'istante interno di reset del microcontrollore.

Per indotto si cerca un impulso CH3 completo con isteresi (salita 0,6 V, discesa 0,4 V), di durata almeno 0,5 µs. Quest'ultimo filtro esclude il breve secondo attraversamento di circa 0,24 µs osservato dopo gli impulsi di circa 1 µs nei dati attuali. Più impulsi idonei richiedono revisione manuale.

- Glitch prima del reset stimato di oltre 0,2 µs: `keep`.
- Glitch dopo il reset stimato di oltre 0,2 µs: `discard`.
- Distanza entro ±0,2 µs: `review` per ordine temporale incerto.

La precedenza temporale è il criterio richiesto di compatibilità, **non prova da sola che il glitch abbia causato il reset**. Le soglie iniziali non sono una caratterizzazione universale: verificare i grafici e adattarle al circuito.

Opzioni principali (`--help` per tutte):

```text
--square-max-us 0.5
--reset-min-us 2
--low-hold-us 2
--smooth-us 0.1
--timing-margin-us 0.2
--ch3-level 0.5
--ch3-hysteresis 0.2
--ch3-min-width-us 0.5
```

## Risultati

Ogni esecuzione crea una nuova cartella `filter_reports/DATA_ORA` sotto il dataset. `--out-dir CARTELLA_NUOVA` cambia destinazione.

- `report.html`: statistiche, lista completa dei Run ID con esito e motivo, grafici degli scarti e dei casi dubbi.
- `results.csv`: tutte le misure e classificazioni.
- `discard.csv`: tracce candidate allo scarto.
- `review.csv`: tracce da verificare manualmente.
- `summary.json`: conteggi, percentuali, motivi e parametri per riprodurre l'analisi.
- `trace_*.png`: grafici CH2 grezzo/filtrato e CH3 con riferimenti temporali.

`--plots 20` è il default; `--plots -1` disegna tutti gli scarti/casi dubbi e `--plots 0` solo statistiche. Tutti gli ID restano nei CSV anche quando i grafici sono limitati. Nessuna soglia viene applicata ai CSV ADC e non vengono prodotti dataset filtrati in questa prima versione.

Test dalla radice del repository:

```sh
MPLBACKEND=Agg python3 -m unittest discover -s FaultMeasureStation -p test_filter_traces.py -v
```
