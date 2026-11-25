# Inferenza CPU / NPU

La configurazione si trova in `FSBL/Core/Inc/bodyguard_ml_config.h`:

```c
#define BODYGUARD_ML_BACKEND BODYGUARD_ML_NPU  /* Neural-ART, default */
/* oppure */
#define BODYGUARD_ML_BACKEND BODYGUARD_ML_CPU  /* solo Cortex-M55 */
```

Modificare la define esistente, poi **Project → Clean → Build Project** sul
progetto `AdvancedFaultBodyguard_FSBL`. Fare Refresh se CubeIDE non mostra i nuovi
file. È possibile anche impostare `BODYGUARD_ML_BACKEND=BODYGUARD_ML_CPU` nei simboli
del compilatore per la configurazione scelta: il valore da build prevale sul default.
Il codice del modello non selezionato viene escluso dal preprocessore.

La NPU usa il modello già presente in `network.c` e il relativo blob in AXISRAM5.
Questo modello ha anche epoche software eseguite dalla CPU, come previsto dalla
conversione originale. La modalità CPU usa `network_cpu*.c`, generati dal medesimo
`NetworkTraining/training_outputs/final_model_int8.tflite` con ST Edge AI 2.2.0 /
X-CUBE-AI 10.2.0, senza `--st-neural-art`. Non è una simulazione della NPU.
La libreria `Middlewares/ST/AI/Lib/NetworkRuntime1020_CM55_GCC.a` deve restare presente.

## Acquisizione e risultati

`BODYGUARD_ML_ADC_CHANNEL` sceglie **1 = ADC1** (default) oppure **2 = ADC2**.
Selezionare il canale fisico corrispondente al segnale usato nel training.
Entrambi i backend ricevono lo stesso preprocessing e lo stesso tensore int8
di 3199 elementi. La lunghezza dell'acquisizione e quella dichiarata dal
preprocessing sono ora entrambe `BODYGUARD_ADC_SAMPLES = 6000`.

Ogni comando `A` o pressione del pulsante avvia l'acquisizione esistente;
dopo la separazione dei due canali viene eseguita una classificazione. Il firmware
invia `E` dopo l'inferenza e mantiene il protocollo di trasferimento delle tracce.
Il software sul PC deve quindi tollerare anche il tempo di inferenza prima di `E`.
Non sono aggiunte stampe sulla UART che possano contaminare le tracce.

Nel debugger / Live Expressions osservare:

- `g_ml_status`: 0 = successo; valori negativi definiti in `bodyguard_ml.h`.
- `g_ml_count`: numero di inferenze completate con successo.
- `g_ml_result.predicted_class`: 0 = autoindotto, 1 = indotto; `UINT32_MAX` se errore.
- `g_ml_result.probability[0..1]` e `quantized_output[0..1]`.
- `g_ml_result.preprocess_ms`, `inference_ms`: tempi separati, risoluzione 1 ms.
- `g_ml_result.preprocessing`: indice evento, IQR e conteggio delle feature saturate.

Un errore di inizializzazione porta a `Error_Handler()`. Un timeout NPU di 5 s
invalida il runtime e resta visibile nel risultato; riavviare la scheda prima
di continuare. Il trasferimento delle tracce rimane disponibile dopo un errore
di inferenza. Non usare classe/probabilità quando lo status è diverso da zero.

Per confrontare i backend sullo stesso identico input, usare
`BodyguardML_RunInt8()` con un tensore int8 fisso nelle due build, evitando una
nuova acquisizione analogica. Non assumere uguaglianza bit per bit: le due
conversioni possono avere differenze di arrotondamento.

## Aggiornare il modello

Dalla cartella `AdvancedFaultBodyguard`:

```sh
python3 NetworkTraining/generate_cpu_model.py --stedgeai /percorso/al/convertitore/stedgeai
```

Lo script rigenera i file CPU e ripristina le guardie della define. Per un nuovo
modello bisogna aggiornare **anche** la conversione NPU, il blob dei pesi e il
preprocessing. Dopo la rigenerazione NPU preservare le guardie
`BODYGUARD_ML_BACKEND == BODYGUARD_ML_NPU` di `network.c` e `network_atonbuf.c`.
La rigenerazione CubeMX può inoltre sovrascrivere impostazioni del progetto:
verificare che `X-CUBE-AI` resti una source directory in tutte le configurazioni.

Il contratto attuale è: input int8 `[1,1,3199,1]`, scala 0.255109578371048,
zero-point -1; output int8 di 2 classi, scala 1/256, zero-point -128.
Le dimensioni vengono controllate a compilazione; quando cambia il modello
verificare anche scale, zero-point, ordine delle classi e calibrazione.
Il preprocessing ereditato usa statistiche fisse in sostituzione della
standardizzazione per dataset del training: la qualità della classificazione
su nuove tracce richiede una validazione sperimentale.

## Verifiche eseguite

Compilazione e link di entrambi i backend con GCC ARM 14.3, Cortex-M55 hard-float
e linker script del progetto. Verificato nei simboli ELF che la build CPU non
contiene il modello NPU né la sua inizializzazione, e viceversa per il modello CPU.
Preprocessing verificato su tracce sintetiche con AddressSanitizer/UBSan.
Resta da eseguire la verifica sulla scheda e il confronto numerico CPU/NPU:
la validazione host ST non supporta il target Cortex-M55.

Le modifiche sono limitate ad AdvancedFaultBodyguard; TheBodyguard non è stato modificato.
