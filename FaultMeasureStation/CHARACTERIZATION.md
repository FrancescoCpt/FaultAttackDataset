# Caratterizzazione del reset e confronto dei minimi

`characterize.py` riutilizza `acquire.py` senza modificarlo. Stessi protocolli seriali e DSO R&S RTB2000: CH1 segnale misurato, CH2 trigger dell'esperimento (default), CH3 impulso dell'attaccante. Installazione: `python3 -m pip install -r requirements.txt`; serve anche il backend VISA già usato per `acquire.py`.

Avviare dalla cartella `FaultMeasureStation`. Omettere `--action` per il menu. Le modalità 1 e 2 effettuano **30 esecuzioni**, non 30 misure necessariamente valide; `--runs` cambia il numero. Le misure scartate non sono rimpiazzate automaticamente e sono escluse dalle statistiche. Controllare il numero di misure valide prima di usare una caratterizzazione (il programma ne richiede almeno due).

## 1. Tempo di reset con glitch attivo

```sh
python3 characterize.py --action 1 -b PORTA_BODYGUARD -v PORTA_VITTIMA -a PORTA_ATTACCANTE
```

Per ogni esecuzione: arma DSO, arma attaccante, avvia Bodyguard, acquisisce DSO e buffer con il protocollo indotto esistente. Cerca un solo impulso CH3 completo con soglia centrale 0.5 V e isteresi 0.2 V (salita a 0.6 V, discesa a 0.4 V), nella finestra temporale configurata. Misura il minimo CH1 nella porzione **20–90%** dell'impulso CH3, come la finestra interna dello screenshot. Cerca poi la prima salita CH1 sopra **3 V** che rimanga sopra soglia per almeno **2 µs**, dopo la fine dell'impulso CH3. Il tempo di ripresa è quello del primo campione sopra soglia, non la fine dei 2 µs di conferma.

`dt = t_ripresa - t_minimo`. Nel tuo screenshot sarebbe circa `50.669 - 44.730 = 5.939 µs`. Si salvano media, mediana, deviazione standard campionaria e intervallo di dt e Vmin. La mediana di dt è il valore usato dalla modalità 2.

## 2. Autoindotto

```sh
python3 characterize.py --action 2 -b PORTA_BODYGUARD --calibration Characterization/SESSIONE_action1/summary.json
```

Usa il protocollo **Auto-Induced** di `acquire.py` (STM32N6: A, attesa E, X, Y). Non apre la seriale dell'attaccante e non lo arma. L'attaccante deve essere già disarmato: non esiste un comando di disarmo nel protocollo attuale. Se CH3 supera la soglia alta (default 0.6 V), la misura viene invalidata.

Trova la ripresa sostenuta CH1, calcola `t_atteso = t_ripresa - mediana(dt)` e cerca il minimo in una finestra **±1 µs** intorno a t_atteso. Regolare `--backtrack-half-us` in base alla dispersione osservata nella caratterizzazione; non allargarla tanto da includere eventi estranei. La finestra non può includere la ripresa. Soglia e durata di conferma del reset devono corrispondere alla caratterizzazione.

Questa procedura assume che il tempo minimo→ripresa sia trasferibile dall'indotto all'autoindotto. È una localizzazione basata sulla caratterizzazione, non una verifica indipendente dell'identità del glitch. Il criterio CH1 identifica una ripresa elettrica compatibile con reset, non conferma da solo un reboot del firmware.

## 3. Regolazione osservazionale del resistore

```sh
python3 characterize.py --action 3 -b PORTA_BODYGUARD -v PORTA_VITTIMA -a PORTA_ATTACCANTE --reference Characterization/SESSIONE_action2/summary.json
```

Prima di ciascuna acquisizione, regolare il resistore e inserire un valore/nota (es. `220 ohm`), oppure Invio. `q` termina. Ogni acquisizione è indotta. Il grafico mostra CH1, CH3, finestra del minimo, minimo e ripresa; una linea indica la mediana dei minimi autoindotti e una fascia il loro intervallo min–max. Il terminale riporta la differenza in volt rispetto alla mediana; non applica una soglia automatica di equivalenza. I valori del resistore sono annotazioni manuali, non misure dello strumento.

## Parametri e risultati

`--help` elenca le opzioni. Principali controlli:

- `--reset-level 3 --reset-hold-us 2`: soglia e persistenza della ripresa.
- `--ch3-level 0.5 --ch3-hysteresis 0.2 --window-start 0.2 --window-end 0.9`: finestra del glitch indotto.
- `--search-start-us 0 --search-end-us 160`: intervallo di ricerca relativo all'asse DSO. Se vi sono riprese precedenti estranee, restringerlo.
- `--backtrack-half-us 1`: semiampiezza ricerca autoindotta.
- `--scope-time-div 0.00002 --scope-h-pos 0.00008`: base tempi e posizione.
- `--scope-trig-source 2 --scope-trig-level 1`: trigger DSO in modalità normal.
- `--scope VISA_RESOURCE`: indirizzo diverso dal default di `acquire.py`.
- `--no-plot`: esecuzione senza finestre grafiche.

Ogni avvio crea una nuova sottocartella in `Characterization/` (o `--out-dir`):

- `config.json`: parametri e copia dei riepiloghi di riferimento.
- `measurements.csv`: una riga per esecuzione, stato, motivo di esclusione, tempi in µs e tensioni in V.
- `summary.json`: statistiche delle sole misure valide e conteggi di completamento.
- `runNNN.npz`: tracce grezze DSO con **asse temporale per ogni canale e ogni esecuzione**, in secondi/volt, più buffer ADC. Lettura con `numpy.load(...)`; ad esempio `scope_adc1_t`, `scope_adc1_v`.
- `runNNN.json`: risultato e risposte seriali di quell'esecuzione.

Si conservano anche le tracce delle misure invalidate. Impulsi mancanti/multipli, finestre insufficienti, minimo sul bordo e ripresa non trovata non producono statistiche valide. Errori di comunicazione che interrompono il protocollo fermano la sessione: le esecuzioni precedenti restano salvate. Nessuna acquisizione è stata verificata sul banco da questo ambiente.

Test senza hardware, dalla radice del repository:

```sh
MPLBACKEND=Agg python3 -m unittest discover -s FaultMeasureStation -p test_characterize.py -v
```

CH3 può avere un livello alto di circa 1 V: la soglia va posta fra il livello basso e quello alto, non sul plateau. `--ch3-hysteresis` è la larghezza totale della banda in volt; zero ripristina il rilevamento a soglia singola. Le soglie CH3 sono indipendenti dalla soglia di ripresa CH1 (3 V) e dal trigger DSO (CH2 di default). I tempi dei fronti CH3 sono interpolati alle rispettive soglie di salita/discesa.
