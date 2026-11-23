import serial
import time
import os
import csv
import matplotlib.pyplot as plt
import sys
#from VictimHandler import VictimHandler


# ===============================
# CONFIGURAZIONE
# ===============================
CAMPIONATORE_PORT = "/dev/tty.usbmodem21202"
#ATTACKER_PORT = "/dev/tty.usbmodem11302"
#VICTIM_PORT = "/dev/tty.usbmodem11302"

CAMPIONATORE_BAUD = 209700
#ATTACKER_BAUD = 115200
#VICTIM_BAUD = 115200

DATASET_1_PATH = r"C:\Users\Pasquale\OneDrive\Documenti\VScode\comunicazione_N6\costruzione dataset attacco indotto\dataset_ALIM_VITT_INDOTTO.csv"
DATASET_2_PATH = r"C:\Users\Pasquale\OneDrive\Documenti\VScode\comunicazione_N6\costruzione dataset attacco indotto\dataset_TRIG_VITT_INDOTTO.csv"

MAX_ACQUISIZIONI = 1000

# Ritardo dopo armamento attaccante.
# Serve per essere sicuro che TIM1 sia già armato prima di avviare campionatore/vittima.
ARMING_DELAY_S = 0.005   # 5 ms


def setup_serial(port, baudrate, timeout=1):
    ser = serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout
    )

    time.sleep(2)

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    return ser


def serial_write(ser, msg):
    if isinstance(msg, str):
        ser.write(msg.encode())
    elif isinstance(msg, int):
        ser.write(str(msg).encode())
    elif isinstance(msg, bytes):
        ser.write(msg)
    else:
        raise TypeError("Tipo non supportato per serial_write")

    ser.flush()


def wait_for_E(ser):
    print("Attendo fine acquisizione")

    while True:
        data = ser.read(1)

        if data == b'E':
            print("Ricevuta 'E' -> acquisizione completata")
            return


def read_vector_until_colon(ser):
    """
    Legge un buffer numerico fino al carattere ':'.
    Ogni campione arriva come riga testuale: valore\r\n
    """
    vector = []

    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()

        if line == "":
            continue

        if line == ":":
            break

        try:
            vector.append(float(line))
        except ValueError:
            print(f"Valore non numerico ricevuto: {line}")

    return vector


def read_two_vectors(ser):
    """
    Il campionatore invia:
    buffer1
    :
    buffer2
    :
    """
    serial_write(ser, "F1\r")
    print("Lettura primo buffer...")
    vector1 = read_vector_until_colon(ser)
    print(f"Primo buffer ricevuto: {len(vector1)} campioni")

    time.sleep(1)

    serial_write(ser, "F2\r")
    print("Lettura secondo buffer...")
    vector2 = read_vector_until_colon(ser)
    print(f"Secondo buffer ricevuto: {len(vector2)} campioni")

    return vector1, vector2

def read_prediction_vector(ser):
    serial_write(ser, "FP\r")
    print("Lettura vettore predizione...")
    prediction_vector = read_vector_until_colon(ser)
    print(f"Vettore predizione ricevuto: {len(prediction_vector)} campioni")
    return prediction_vector


def request_last_ccr1(ser_attacker):
    """
    Chiede all'attaccante il CCR1 dell'ultimo PWM.

    Firmware atteso:
        I -> CCR1=xxxx\r\n

    oppure:
        I -> NO_PWM\r\n
    """

    ser_attacker.reset_input_buffer()

    serial_write(ser_attacker, "I")

    t0 = time.time()
    timeout_s = 2.0

    while time.time() - t0 < timeout_s:
        line = ser_attacker.readline().decode("utf-8", errors="ignore").strip()

        if line == "":
            continue

        print(f"Risposta attaccante: {line}")

        if line.startswith("CCR1="):
            value_str = line.split("=", 1)[1]
            return int(value_str)

        if line == "NO_PWM":
            raise RuntimeError("L'attaccante ha risposto NO_PWM: nessun PWM completato.")

    raise TimeoutError("Timeout: nessuna risposta CCR1 ricevuta dall'attaccante.")


def append_trace_to_csv(file_path, ccr1, vector):
    """
    Salva una riga CSV fatta così:

        CCR1 ; sample0 ; sample1 ; sample2 ; ...

    Quindi la prima colonna è il CCR1 usato per quella traccia.
    """
    row = [ccr1] + vector

    with open(file_path, "a", newline="") as csvfile:
        writer = csv.writer(csvfile, delimiter=";")
        writer.writerow(row)


def main():
    ser = setup_serial(CAMPIONATORE_PORT, CAMPIONATORE_BAUD)
    #ser_attacker = setup_serial(ATTACKER_PORT, ATTACKER_BAUD)
    # victim = VictimHandler(VICTIM_PORT, VICTIM_BAUD)

    state = "INIT"
    cnt = 0

    try:
        while True:
            if state == "INIT":
                cnt += 1

                if cnt > MAX_ACQUISIZIONI:
                    state = "FINISH"
                    continue

                print(f"\nIterazione {cnt}/{MAX_ACQUISIZIONI}")

                time.sleep(2)

                # 1) Armo l'attaccante.
                #    Il CCR1 è già stato preparato dal firmware.
                #serial_write(ser_attacker, "A")
                #print("Attaccante armato")

                # 2) Aspetto qualche ms per essere sicuro che TIM1 sia armato.
                time.sleep(ARMING_DELAY_S)

                # 3) Avvio il campionatore.
                serial_write(ser, "S\r")
                print("Campionatore avviato")

                # 4) Avvio encrypt / trigger vittima.
                # victim.start_encrypt()
                # print("Encrypt avviato")

                state = "ATTESA_E"

            elif state == "ATTESA_E":
                wait_for_E(ser)
                state = "ACQUISIZIONE"

            elif state == "ACQUISIZIONE":
                # 5) Chiedo all'attaccante il CCR1 usato per l'ultimo PWM.
                #ccr1 = request_last_ccr1(ser_attacker)
                #print(f"CCR1 usato per questa acquisizione: {ccr1}")

                # 6) Chiedo al campionatore di inviare i due buffer.
                print("Richiesta invio dei due buffer")

                vettore1, vettore2 = read_two_vectors(ser)

                # 7) Salvo i due dataset.
                #    Prima colonna = CCR1
                #    Colonne successive = campioni della traccia.
                #append_trace_to_csv(DATASET_1_PATH, ccr1, vettore1)
                #append_trace_to_csv(DATASET_2_PATH, ccr1, vettore2)

                plt.plot(vettore1, label="Buffer 1")
                plt.plot(vettore2, label="Buffer 2")
                plt.legend()
                plt.show()

                print(f"Salvato buffer 1 in: {DATASET_1_PATH}")
                print(f"Salvato buffer 2 in: {DATASET_2_PATH}")

                prediction_vector = read_prediction_vector(ser)
                print(f"Vettore predizione: {prediction_vector}")

                k = input("Premi invio per continuare...")
                if k.lower() == "q":
                    sys.exit(0)

                state = "INIT"

            elif state == "FINISH":
                print("Comunicazione terminata.")
                break

    finally:
        ser.close()
        #ser_attacker.close()
        # victim.close()  # solo se la tua classe VictimHandler lo prevede


if __name__ == "__main__":
    main()