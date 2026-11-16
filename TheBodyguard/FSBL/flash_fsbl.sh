
#!/bin/bash
set -euo pipefail

# Argomenti ricevuti da STM32CubeIDE
PROJECT_DIR="$1"
BUILD_DIR="$2"
PROJECT_NAME="$3"

BIN="$BUILD_DIR/$PROJECT_NAME.bin"
SIGNED="$BUILD_DIR/$PROJECT_NAME-trusted.bin"

# Percorsi standard di STM32CubeProgrammer su macOS
CUBE_APP="/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/STM32CubeProgrammer.app"

# Ricerca delle utility per diverse versioni macOS
TOOLS_DIR=""
for DIR in \
  "$CUBE_APP/Contents/Resources/bin" \
  "$CUBE_APP/Contents/MacOs/bin"; do
    if [ -x "$DIR/STM32_Programmer_CLI" ] && \
       [ -x "$DIR/STM32_SigningTool_CLI" ]; then
      TOOLS_DIR="$DIR"
      break
    fi
done

if [ -z "$TOOLS_DIR" ]; then
  echo "ERRORE: utility STM32CubeProgrammer non trovate"
  exit 1
fi

SIGNER="$TOOLS_DIR/STM32_SigningTool_CLI"
PROGRAMMER="$TOOLS_DIR/STM32_Programmer_CLI"
LOADER="$TOOLS_DIR/ExternalLoader/MX25UM51245G_STM32N6570-NUCLEO.stldr"

# Il loader può trovarsi nella directory bin
# oppure nella directory madre dell'installazione.
if [ ! -f "$LOADER" ]; then
  LOADER="$(find "$CUBE_APP/Contents" \
    -name 'MX25UM51245G_STM32N6570-NUCLEO.stldr' \
    -print -quit)"
fi

if [ ! -f "$BIN" ] || [ ! -f "$LOADER" ]; then
  echo "ERRORE: firmware BIN o external loader mancante"
  exit 1
fi

echo "=== STM32N657 FSBL ==="
echo "Firmware: $BIN"

# Genera l'immagine con header di boot
"$SIGNER" \
  -bin "$BIN" \
  -nk \
  -of 0x80000000 \
  -t fsbl \
  -hv 2.3 \
  -align \
  -o "$SIGNED"

echo "=== Flash XSPI ==="

# Programma e verifica il firmware
"$PROGRAMMER" \
  -c port=SWD mode=HOTPLUG \
  -el "$LOADER" \
  -w "$SIGNED" 0x70000000 \
  -v

echo "=== Programmazione completata ==="
