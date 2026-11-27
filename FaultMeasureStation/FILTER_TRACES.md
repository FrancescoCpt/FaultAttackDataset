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

## Controllo dei campioni ADC a 8 bit

Per ogni trial non già scartato dai criteri CH2/CH3, vengono verificati i campioni in `bodyguard_adc1.csv` e `bodyguard_adc2.csv`, abbinati tramite Run ID. Ogni campione deve essere un intero compreso tra **0 e 255 inclusi**. Basta un campione fuori intervallo, non numerico, non finito (`NaN`, infinito) o frazionario, oppure una traccia vuota, per classificare l'intero trial come `discard`, anche se prima era `review`.

Il motivo dello scarto riporta il file ADC e il primo campione non valido (posizione a partire da 1, escluso il Run ID) per ciascun ADC interessato. Lo scarto compare nei report e nelle statistiche ed esclude il Run ID da tutti i CSV esportati, senza chiedere conferma. Il controllo viene applicato ai due file ADC quando presenti; l'esclusione degli ID mancanti dai canali disponibili resta gestita dall'esportazione.

## Risultati

Ogni esecuzione crea una nuova cartella `filter_reports/DATA_ORA` sotto il dataset. `--out-dir CARTELLA_NUOVA` cambia destinazione.

- `report.html`: statistiche, lista completa dei Run ID con esito e motivo, grafici degli scarti e dei casi dubbi.
- `results.csv`: tutte le misure e classificazioni.
- `discard.csv`: tracce candidate allo scarto.
- `review.csv`: tracce da verificare manualmente.
- `summary.json`: conteggi, percentuali, motivi e parametri per riprodurre l'analisi.
- `trace_*.png`: grafici CH2 grezzo/filtrato e CH3 con riferimenti temporali.

`--plots 20` è il default; `--plots -1` disegna tutti gli scarti/casi dubbi e `--plots 0` solo statistiche. Tutti gli ID restano nei CSV anche quando i grafici sono limitati. I CSV ADC sono sottoposti al controllo di validità a 8 bit descritto sotto; i CSV filtrati sono copie separate e gli originali restano intatti.

Test dalla radice del repository:

```sh
MPLBACKEND=Agg python3 -m unittest discover -s FaultMeasureStation -p test_filter_traces.py -v
```

## Esportazione dei CSV filtrati

```sh
python3 filter_traces.py Dataset/Victim_7/Induced --mode induced --max-good 50 --plots -1
```

La sottocartella `filtered/` del report contiene copie di tutti i `scope_ch*.csv` e `bodyguard_adc*.csv` presenti nella cartella sorgente. Le righe `TIME` sono preservate e non contano nel limite. Nessun originale viene sovrascritto.

`--max-good N` (default **100**) esporta al massimo N tracce nell'ordine del CSV CH2, includendo nel conteggio le review accettate. Non è un campionamento casuale. Se ci sono meno tracce idonee vengono esportate tutte quelle disponibili. La statistica di classificazione continua a riguardare l'intero dataset.

Le `discard` sono sempre escluse. Per ogni `review` che può ancora rientrare nel limite viene chiesto `s/n`, mostrando il motivo e il percorso del grafico se generato. Non viene assunta una risposta in caso di EOF: l'esportazione si interrompe e i report rimangono disponibili. Una review oltre il limite non viene richiesta né esportata.

Si esportano solo Run ID presenti in **tutti** i CSV sorgente selezionati, per mantenere i canali e gli ADC allineati; gli ID mancanti sono marcati `missing_channel`. `selection.csv` registra per ogni trial la decisione (`keep`, `discard`, `review_accepted`, `review_rejected`, `limit`, `missing_channel`); `summary.json` contiene anche conteggio e Run ID esportati. Non si duplicano tracce per raggiungere N.

## Filtro opzionale della dispersione prima del recovery

```sh
python3 filter_traces.py Induced_Traces --mode induced --filter-recovery --recovery-max-rms 5
```

Disattivato per default, disponibile solo con `--mode induced`. Usa ADC1 a
5 MHz (`--adc-sample-rate`), con lo stesso rilevamento di `main.ipynb`:
mediana mobile di 3 campioni, livello basso al percentile 10 fra 845–875 µs,
livello alto mediano fra 895–900 µs, soglia al 90%, ricerca fra 850–900 µs,
permanenza di 5 campioni ed escursione minima di 10 counts. Queste finestre
assolute richiedono acquisizioni compatibili con quelle del notebook.

Allinea per interpolazione le tracce non già scartate (incluse le review),
calcola la mediana punto per punto e scarta quelle con distanza RMS dalla
mediana superiore a `--recovery-max-rms` (default 5 conteggi ADC) nella
finestra `--recovery-window-us -20 0`. Soglie più basse sono più selettive;
5 è una soglia iniziale configurabile, non una calibrazione automatica.
Il confronto usa solo la classe indotta, in un singolo passaggio, prima del
limite `--max-good` e delle decisioni manuali sulle review.

ADC1 mancante per un ID, recovery non rilevabile o finestra incompleta
comportano scarto con motivo esplicito. Con meno di tre recovery validi il
confronto RMS non viene applicato e le tracce valide richiedono review.
Il file ADC1 assente interrompe il comando prima della creazione dei report.
Le review che superano il filtro restano review.

`results.csv` riporta `adc_recovery_us`, `recovery_rms_counts`, soglia e
classificazione precedente; `summary.json` conserva impostazioni e numero
di tracce di riferimento. L'RMS è visibile anche nella tabella HTML.
I grafici CH2/CH3 esistenti descrivono il primo stadio, non il confronto ADC.
I campioni esportati non vengono smussati o alterati: lo scarto esclude
l'intero Run ID da tutti i canali. Nessun dataset originale viene modificato.

La banda ±2σ del notebook misura dispersione, non l'intervallo di confidenza
della media. La selezione modifica la popolazione osservata: confrontare
anche il risultato senza filtro e riportare soglia e numero di scarti.
