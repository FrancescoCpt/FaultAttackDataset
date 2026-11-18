# Rilevamento di fault injection con reti neurali su STM32

Piattaforma di ricerca per acquisire e analizzare le tracce elettriche di un microcontrollore sottoposto a glitch di tensione, con l'obiettivo di sviluppare una protezione embedded basata su rete neurale. Il dataset distingue eventi **indotti** dal circuito di attacco ed eventi **autoindotti**, acquisiti senza armare l'attaccante.

Il sistema comprende tre progetti firmware STM32, una stazione di misura Python e gli strumenti per preparare il dataset e addestrare la rete. La STM32N6 viene inizialmente utilizzata per l'acquisizione; il dataset serve poi allo sviluppo di un modello da integrare sulla stessa piattaforma per il rilevamento degli attacchi.

## Architettura

| Progetto | Board | Ruolo |
| --- | --- | --- |
| [`AdvancedFaultVictim`](AdvancedFaultVictim/) | NUCLEO-F401RE, STM32F4 | Vittima: esegue il workload e genera i segnali di sincronizzazione. |
| [`AdvancedFaultAttacker`](AdvancedFaultAttacker/) | NUCLEO-F401RE, STM32F4 | Attaccante: riceve un trigger e pilota il circuito esterno di glitch. |
| [`TheBodyguard`](TheBodyguard/) | NUCLEO-N657X0-Q, STM32N6 | Acquisizione delle tracce; piattaforma destinata all'integrazione della protezione neurale. |
| [`FaultMeasureStation`](FaultMeasureStation/) | PC e oscilloscopio R&S RTB2000 | Orchestrazione delle board, acquisizione, caratterizzazione e filtraggio. |

La stazione arma l'oscilloscopio e, nelle misure indotte, l'attaccante; avvia quindi l'acquisizione della Bodyguard. La vittima esegue il workload e il suo trigger sincronizza il timer dell'attaccante. Il circuito esterno applica il glitch, mentre Bodyguard e oscilloscopio registrano le tracce da associare allo stesso trial.

### Vittima: workload AES

La F4 vittima può eseguire un workload arbitrario. In questo progetto il workload è una cifratura **AES-128-CBC**, senza padding, su un plaintext di 64 byte.

Il [firmware della vittima](AdvancedFaultVictim/Core/Src/main.c) contiene tre tuple chiave–plaintext, generate casualmente e conservate come costanti. Ogni set include anche IV e ciphertext atteso coerenti:

- `key1`, `plaintext1`, `IV1`, `expectedCiphertext1`;
- `key2`, `plaintext2`, `IV2`, `expectedCiphertext2`;
- `key3`, `plaintext3`, `IV3`, `expectedCiphertext3`.

Il set si seleziona prima della compilazione:

```c
#define AES_TEST_SET 1  /* 1, 2 oppure 3 */
```

I valori rimangono fissi fra le esecuzioni, così la condizione sperimentale è riproducibile. Il segnale di attività della vittima viene portato alto all'inizio dell'operazione e basso al completamento; la sua evoluzione viene usata anche per distinguere completamento normale e reset.

### Attaccante: generazione del glitch

Il [firmware dell'attaccante](AdvancedFaultAttacker/Core/Src/main.c) configura **TIM1 in One Pulse Mode**:

- **PA9 / TIM1_CH2:** ingresso del trigger proveniente dalla vittima;
- **PA8 / TIM1_CH1:** uscita verso il circuito di attacco;
- **CCR1:** ritardo fra trigger e impulso;
- **ARR e CCR1:** durata dell'impulso di comando.

L'attaccante viene armato via UART dalla stazione di misura. Nella configurazione corrente l'impulso dura circa **1 µs** e il ritardo viene variato dal firmware. La durata e la temporizzazione sono controllate dal timer; la profondità del glitch di tensione dipende dal circuito esterno e dalla sua regolazione, incluso il resistore di controllo. Il livello effettivo viene verificato sulle acquisizioni del DSO.

### Bodyguard: dal dataset alla protezione embedded

La N6 acquisisce i segnali tramite due ADC e trasferisce i buffer alla stazione di misura. Questa prima fase permette di costruire un dataset etichettato per studiare la distinzione fra eventi indotti e autoindotti.

La directory [`TheBodyguard/NetworkTraining`](TheBodyguard/NetworkTraining/) raccoglie gli strumenti di elaborazione, analisi, training e conversione del modello. Il passo successivo del progetto è integrare la rete sulla N6 per realizzare la protezione basata su inferenza neurale. L'acquisizione del dataset e la validazione della protezione sono fasi distinte del lavoro.

## Stazione di misura

| Script | Funzione |
| --- | --- |
| [`acquire.py`](FaultMeasureStation/acquire.py) | Orchestra le board e il DSO; salva le tracce ADC e oscilloscopio con un Run ID comune. |
| [`characterize.py`](FaultMeasureStation/characterize.py) | Caratterizza il tempo fra minimo di tensione e ripresa post-reset, misura l'autoindotto e supporta la regolazione osservazionale dell'indotto. |
| [`filter_traces.py`](FaultMeasureStation/filter_traces.py) | Classifica i trial, produce statistiche e grafici ed esporta CSV filtrati mantenendo allineati ADC e canali DSO. |

### Segnali dell'oscilloscopio

| Canale | Segnale e utilizzo |
| --- | --- |
| **CH1** | Segnale di tensione osservato: minimo locale del glitch e ripresa post-reset. |
| **CH2** | Segnale di attività AES della vittima: discesa rapida al completamento normale, discesa lenta compatibile con il reset nel circuito di misura utilizzato. È anche la sorgente di trigger DSO predefinita. |
| **CH3** | Impulso del circuito di attacco: localizzazione temporale del glitch indotto. |

Il livello alto di CH3 può assestarsi intorno a **1 V** per effetto del sistema fisico. Gli script di analisi usano soglie configurabili e isteresi per evitare di frammentare l'impulso a causa del rumore.

### Installazione e acquisizione

Per i firmware utilizzare STM32CubeIDE e i tre progetti presenti nel repository. Per la stazione di misura servono Python, le dipendenze elencate nel repository e un backend VISA compatibile con il DSO.

Dalla radice del repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r FaultMeasureStation/requirements.txt
cd FaultMeasureStation
```

Acquisizione **autoindotta**, senza armare l'attaccante:

```bash
python3 acquire.py --protocol 1 \
  -b PORTA_BODYGUARD --scope \
  --repeat 100 --auto-save --no-plot \
  --out-dir acquisizioni/B1/K1V1
```

Acquisizione **indotta**, con le tre board:

```bash
python3 acquire.py --protocol 2 \
  -b PORTA_BODYGUARD -v PORTA_VITTIMA -a PORTA_ATTACCANTE \
  --scope --repeat 100 --auto-save --no-plot \
  --out-dir acquisizioni/B1/K1V1
```

Sostituire le porte con quelle del banco. `--scope` utilizza la risorsa VISA predefinita nello script; `--scope RISORSA_VISA` permette di specificarne un'altra. Le velocità seriali configurate sono **209700 baud** per Bodyguard e **115200 baud** per le due F4.

Lo script crea le sottocartelle `Autoinduced_Traces` e `Induced_Traces`. Nel dataset organizzato queste tipologie sono raccolte come `Autoinduced` e `Induced`.

I trial con errore di conversione dei buffer o timeout gestito sull'ACK di acquisizione vengono ripetuti senza avanzare il contatore. Il salvataggio richiede un'acquisizione DSO valida: `--repeat 100` non garantisce da solo 100 tracce utilizzabili dopo il filtraggio. Il numero finale va controllato nei report, acquisendo ulteriori trial quando necessario.

Per le modalità e i parametri di caratterizzazione consultare [CHARACTERIZATION.md](FaultMeasureStation/CHARACTERIZATION.md).

### Filtraggio ed esportazione

Per l'**autoindotto**, il filtro cerca una discesa lenta su CH2 compatibile con il reset. Una discesa rapida, tipica dell'onda quadra a fine AES, identifica un trial da scartare.

Per l'**indotto**, oltre al criterio di reset, viene verificato l'ordine temporale:

1. L'inizio della discesa finale CH2, al 90% dell'ampiezza, è usato come stima del reset.
2. Il fronte di salita CH3 identifica il glitch.
3. Un glitch successivo al reset stimato determina lo scarto; casi troppo ravvicinati o ambigui richiedono revisione.

La precedenza temporale è un criterio di compatibilità sperimentale, non una dimostrazione autonoma della causalità del reset.

```bash
python3 filter_traces.py Dataset/B1/K1V1/Autoinduced \
  --mode auto --max-good 100 --plots -1

python3 filter_traces.py Dataset/B1/K1V1/Induced \
  --mode induced --max-good 100 --plots -1
```

Il filtro produce `report.html`, statistiche JSON, risultati CSV e grafici. Esclude le righe `discard` e chiede conferma per le `review` candidate all'esportazione. La sottocartella `filtered/` contiene copie dei CSV con gli stessi Run ID su tutti i canali e buffer disponibili; gli originali restano intatti.

`--max-good 100` impone un massimo di 100 trial selezionati, comprese le review accettate. Se i trial idonei sono meno, vengono esportati solo quelli disponibili. I parametri e le regole complete sono descritti in [FILTER_TRACES.md](FaultMeasureStation/FILTER_TRACES.md).

## Dataset

Il dataset è organizzato per **board vittima**, **tupla chiave–plaintext**, **livello di tensione** e **tipologia di evento**. La numerosità prevista è di **100 tracce per tipologia** — indotto e autoindotto — per ciascuna condizione acquisita. Le acquisizioni grezze possono includere trial aggiuntivi da escludere durante il filtraggio.

Struttura di riferimento:

```text
FaultMeasureStation/Dataset/
└── B1/
    ├── Characterization/
    └── K1V1/
        ├── Autoinduced/
        └── Induced/
            ├── bodyguard_adc1.csv
            ├── bodyguard_adc2.csv
            ├── scope_ch1.csv
            ├── scope_ch2.csv
            ├── scope_ch3.csv
            └── filter_reports/
```

Ogni riga di acquisizione inizia con un **Run ID**, comune ai file dello stesso trial. Nei CSV DSO la prima riga, identificata da `TIME`, contiene l'asse temporale in secondi; i campioni sono espressi in volt. I CSV Bodyguard contengono i codici ADC grezzi.

I file `scope_ch1.csv`, `scope_ch2.csv` e `scope_ch3.csv` sono esclusi dal versionamento tramite `.gitignore`: possono essere presenti nella copia locale del dataset senza essere distribuiti da un clone Git. Il filtraggio basato sul DSO richiede anche questi file.

## Design degli esperimenti

Le acquisizioni seguono il piano riportato nella tabella seguente. Le sigle indicano:

- **B1–B6:** sei board vittima fisicamente distinte, tutte STM32F4.
- **K1–K3:** le tre tuple chiave–plaintext configurabili nel workload AES.
- **V1–V3:** tre livelli del minimo di tensione, riferiti al minimo autoindotto caratterizzato: **V1 = Vmin autoindotto**, **V2 = 0,90 × Vmin autoindotto**, **V3 = 0,80 × Vmin autoindotto**.
- **S1–S3:** sessioni di misura alle quali sono assegnate le combinazioni board–K–V.

Le percentuali si riferiscono al valore di **Vmin**, non alla tensione nominale di alimentazione. Il riferimento autoindotto viene caratterizzato sul banco e usato per regolare il circuito nelle misure indotte.

### Piano frazionato (L-18)

🟩 indica una sessione eseguita e presente nel dataset.

🟧 indica una sessione prossima ad essere eseguita.

⬜️ indica una sessione prevista in futuro per completezza del dataset.

| Board | K1 V1 | K1 V2 | K1 V3 | K2 V1 | K2 V2 | K2 V3 | K3 V1 | K3 V2 | K3 V3 |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| B1    | 🟩 | - | - | - | 🟩 | - | - | - | 🟩 |
| B2    | 🟩 | - | - | - | 🟩 | - | - | - | 🟩 |
| B3    | - | 🟩 | - | - | - | 🟩 | 🟩 | - | - |
| B4    | - | - | 🟩 | 🟩 | - | - | - | 🟩 | - |
| B5    | - | 🟩 | - | - | - | 🟩 | 🟩 | - | - |
| B6    | - | - | 🟩 | 🟩 | - | - | - | 🟩 | - |

Il piano comprende **54 combinazioni board–K–V**, assegnate a tre sessioni: ogni board contribuisce con tre combinazioni per sessione. La tabella descrive il disegno sperimentale; la presenza delle singole acquisizioni e la loro numerosità effettiva si verificano nelle cartelle e nei report del dataset.

Per riprodurre una misura, conservare insieme ai dati l'identificativo della board, il set AES, il livello Vmin di riferimento e quello misurato, la sessione, la regolazione del circuito e i parametri di acquisizione e filtraggio.

### Piano frazionato (L-54)

🟩 indica una sessione eseguita e presente nel dataset.

🟧 indica una sessione prossima ad essere eseguita.

⬜️ indica una sessione prevista in futuro per completezza del dataset.

| Board | K1 V1 | K1 V2 | K1 V3 | K2 V1 | K2 V2 | K2 V3 | K3 V1 | K3 V2 | K3 V3 |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| B1    | 🟩 S1  | ⬜️ S2 | ⬜️ S3 | ⬜️ S2  | 🟩 S3 | ⬜️ S1 | ⬜️ S3  | ⬜️ S1 | 🟩 S2 |
| B2    | 🟩 S2  | ⬜️ S3 | ⬜️ S1 | ⬜️ S3  | 🟩 S1 | ⬜️ S2 | ⬜️ S1  | ⬜️ S2 | 🟩 S3 |
| B3    | ⬜️ S3  | 🟩 S1 | ⬜️ S2 | ⬜️ S1  | ⬜️ S2 | 🟩 S3 | 🟩 S2  | ⬜️ S3 | ⬜️ S1 |
| B4    | ⬜️ S1  | ⬜️ S2 | 🟩 S3 | 🟩 S2  | ⬜️ S3 | ⬜️ S1 | ⬜️ S3  | 🟩 S1 | ⬜️ S2 |
| B5    | ⬜️ S2  | 🟩 S3 | ⬜️ S1 | ⬜️ S3  | ⬜️ S1 | 🟩 S2 | 🟩 S1  | ⬜️ S2 | ⬜️ S3 |
| B6    | ⬜️ S3  | ⬜️ S1 | 🟩 S2 | 🟩 S1  | ⬜️ S2 | ⬜️ S3 | ⬜️ S2  | 🟩 S3 | ⬜️ S1 |

Il piano comprende **54 combinazioni board–K–V**, assegnate a tre sessioni: ogni board contribuisce con tre combinazioni per sessione. La tabella descrive il disegno sperimentale; la presenza delle singole acquisizioni e la loro numerosità effettiva si verificano nelle cartelle e nei report del dataset.

Per riprodurre una misura, conservare insieme ai dati l'identificativo della board, il set AES, il livello Vmin di riferimento e quello misurato, la sessione, la regolazione del circuito e i parametri di acquisizione e filtraggio.

### Piano Completo

🟩 indica una sessione eseguita e presente nel dataset.

🟧 indica una sessione prossima ad essere eseguita.

⬜️ indica una sessione prevista in futuro per completezza del dataset.

#### Sessione 1
| Board | K1 V1 | K1 V2 | K1 V3 | K2 V1 | K2 V2 | K2 V3 | K3 V1 | K3 V2 | K3 V3 |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| B1    | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B2    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B3    | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B4    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ |
| B5    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ |
| B6    | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |

#### Sessione 2
| Board | K1 V1 | K1 V2 | K1 V3 | K2 V1 | K2 V2 | K2 V3 | K3 V1 | K3 V2 | K3 V3 |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| B1    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 |
| B2    | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B3    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ |
| B4    | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B5    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ |
| B6    | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |

#### Sessione 3
| Board | K1 V1 | K1 V2 | K1 V3 | K2 V1 | K2 V2 | K2 V3 | K3 V1 | K3 V2 | K3 V3 |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| B1    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B2    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 |
| B3    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ |
| B4    | ⬜️ | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B5    | ⬜️ | 🟩 | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ |
| B6    | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | ⬜️ | 🟩 | ⬜️ |