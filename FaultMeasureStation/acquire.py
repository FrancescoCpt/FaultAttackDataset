#!/usr/bin/env python3
"""
Unified STM32 acquisition controller (+ oscilloscope capture)
==============================================================

Merges two acquisition protocols into a single application. At startup the
program shows a menu:

    1. Auto-Induced — single STM32N6 dual-ADC recorder (4-step flow)
       STM32N6 (LPUART1 @ 209700). Default ADC sample rate: 84 MHz.
       Traces saved to <out-dir>/Autoinduced_Traces/.
       Steps: 0) arm scope   1) start dual-ADC acquisition
              2) wait for 'E'   2b) scope readout
              3) fetch ADC2 ('X')   4) fetch ADC1 ('Y')

    2. Induced      — 3-board glitch-attack chain (6-step flow)
       Bodyguard (LPUART1 @ 209700) + Victim (USART2 @ 115200)
       + Attacker (USART2 @ 115200). Default ADC sample rate: 84 MHz.
       Traces saved to <out-dir>/Induced_Traces/.
       Steps: 0) arm scope   1) arm Attacker   2) start Bodyguard
              3) wait for 'E'   3b) scope readout
              4) snapshot Victim   5) query Attacker CCR1
              6) fetch Bodyguard buffers

`--protocol {1,2}` skips the menu for scripting.

Regardless of the selected mode, the rest of the pipeline is shared:
    * optional Rohde & Schwarz RTB2000 oscilloscope capture (VISA)
    * accumulating CSV files for the dual-ADC traces and each scope channel
    * per-run interactive Y/n prompt to save or discard the trace
    * per-run matplotlib plot (displayed only — no PNG is written)

Save policy (strict):
    * A run is saved ONLY if the scope capture succeeded. If the scope
      capture failed (or the scope is disabled), the run is fully
      discarded — nothing is written for the Bodyguard or the Scope.
    * When saved, every CSV file gets the SAME Run ID as the first column
      of the row, so a trace in bodyguard_adc1.csv corresponds exactly to
      the trace with the same Run ID in each scope_chN.csv. The Run ID
      has the form <session-timestamp>_run<NNN>, e.g. 20260824_153000_run007
      (session-timestamp is fixed once at program startup; NNN is the
      3-digit run number from the current loop).

Output CSV files (written to <--out-dir>/<mode subfolder>/, appended run-after-run):
    bodyguard_adc1.csv   dual-ADC ADC1 traces (raw 8-bit codes)
    bodyguard_adc2.csv   dual-ADC ADC2 traces (raw 8-bit codes)
    scope_ch1.csv        oscilloscope CH1 traces (volts)
    scope_ch2.csv        oscilloscope CH2 traces (volts)
    scope_ch3.csv        oscilloscope CH3 traces (volts)

Every row in every CSV starts with the Run ID (an integer). Scope CSVs
also carry a "TIME" row on top (written on the first saved run only)
holding the time axis in seconds.
"""

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any, Iterable

import numpy as np
import serial
import matplotlib.pyplot as plt

# pyvisa is only needed when a scope is used; tolerate its absence so the
# script still runs (serial-only) on a machine without pyvisa installed.
try:
    import pyvisa
except ImportError:
    pyvisa = None


# --------------------------------------------------------------------------- #
# Protocol constants                                                          #
# --------------------------------------------------------------------------- #
NUM_SAMPLES = 6000

BODYGUARD_BAUD = 209700
VICTIM_BAUD    = 115200
ATTACKER_BAUD  = 115200

# Sample-rate defaults (Hz), chosen per protocol at runtime unless the user
# overrides them via --sample-rate.
SAMPLE_RATE_P1_HZ = 84_000_000        # Induced      (Bodyguard on F4 @ 84 MHz)
SAMPLE_RATE_P2_HZ = 84_000_000        # Auto-Induced (STM32N6)

CMD_START      = b'A'    # -> board(s): start acquisition (and, in Induced, arm TIM1)
CMD_FETCH_1    = b'X'    # -> board(s): transmit buffer #1 (ADC2)
CMD_FETCH_2    = b'Y'    # -> board(s): transmit buffer #2 (ADC1)
CMD_INFO       = b'I'    # -> Attacker (Induced only): report last CCR1
ACK_ACQ_DONE   = b'E'    # <- board: acquisition complete
BUF_TERMINATOR = b':'    # <- board: end-of-buffer line

# Induced: Attacker TIM1 clock (used to convert CCR1 ticks -> µs)
ATTACKER_TIM1_HZ = 84_000_000

# Default VISA resource for the RTB2000. `--scope` (bare) uses this; pass a
# string to override.
DEFAULT_SCOPE_RESOURCE = 'USB0::0x0AAD::0x01D6::302177::INSTR'

# --- Default scope setup (overridable via CLI) ---
DEF_TIME_DIV    = 20e-6      # 20 µs/div
DEF_H_POS       = 80e-6      # 80 µs horizontal position
DEF_VOLTS_DIV   = 1.0        # 1 V/div
DEF_TRIG_LEVEL  = 1.0        # 1 V
DEF_TRIG_SOURCE = 2
DEF_TRIG_SLOPE  = 'rising'
DEF_TRIG_MODE   = 'normal'

# Sentinel Run ID used as the first-column marker on the time-axis row of
# the scope CSVs. A real Run ID always has the form
# "<timestamp>_run<NNN>" (e.g. "20260824_153000_run007"), so the bare
# string "TIME" can never collide with one.
TIME_AXIS_ID = "TIME"

# Menu ordering is:
#   1 -> Auto-Induced (single STM32N6, 4 steps)
#   2 -> Induced      (3-board glitch chain, 6 steps)
PROTO_AUTO_INDUCED = 1
PROTO_INDUCED      = 2
PROTO_NAMES = {
    PROTO_AUTO_INDUCED: "Auto-Induced (STM32N6, 4 steps)",
    PROTO_INDUCED:      "Induced (3-board glitch chain, 6 steps)",
}


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    protocol:       int
    bodyguard_port: str
    victim_port:    Optional[str]   = None        # Induced only
    attacker_port:  Optional[str]   = None        # Induced only
    sample_rate:    Optional[float] = None
    show_plot:      bool            = True
    out_dir:        str             = "."
    # Oscilloscope
    scope_resource: Optional[str]   = None        # None => scope disabled
    scope_ch_adc1:  int             = 1
    scope_ch_adc2:  int             = 2
    scope_ch_adc3:  int             = 3
    scope_timeout_s: float          = 10.0
    scope_time_div: float           = DEF_TIME_DIV
    scope_h_pos:    float           = DEF_H_POS
    scope_volts_div: float          = DEF_VOLTS_DIV
    scope_trig_source: int          = DEF_TRIG_SOURCE
    scope_trig_slope:  str          = DEF_TRIG_SLOPE
    scope_trig_level:  Optional[float] = DEF_TRIG_LEVEL
    scope_trig_mode:   str          = DEF_TRIG_MODE
    # Save-prompt behavior
    auto_save:      bool            = False


# --------------------------------------------------------------------------- #
# Oscilloscope wrapper (R&S RTB2000 / RTB2002 / RTB2004)                       #
# --------------------------------------------------------------------------- #
class Scope:
    """
    Thin PyVISA wrapper for a Rohde & Schwarz RTB2000-series oscilloscope.
    """

    _SLOPE_MAP = {
        'rising':  'POSitive', 'pos': 'POSitive', 'positive': 'POSitive',
        'falling': 'NEGative', 'neg': 'NEGative', 'negative': 'NEGative',
        'either':  'EITHer',   'both': 'EITHer',  'any': 'EITHer',
    }

    def __init__(self, resource: str, timeout_ms: int = 20000):
        if pyvisa is None:
            raise RuntimeError(
                "pyvisa is not installed. Install it with:\n"
                "    pip install pyvisa\n"
                "and a VISA backend (the NI-VISA / R&S VISA runtime, or "
                "'pip install pyvisa-py')."
            )
        self.rm = pyvisa.ResourceManager()
        self.instr = self.rm.open_resource(resource)
        self.instr.timeout = timeout_ms
        self.resource = resource
        # ASCII transfer: a few thousand samples move in a fraction of a second
        # and parse trivially. Header/data are always in seconds/volts.
        self.instr.write('FORMat:DATA ASC')

    def idn(self) -> str:
        return self.instr.query('*IDN?').strip()

    # ---- setup ---------------------------------------------------------- #
    def set_vertical(self, channels: Iterable[int],
                     scale_v_per_div: float) -> None:
        """Enable each channel and set its vertical scale (V/div)."""
        for ch in channels:
            self.instr.write(f'CHANnel{ch}:STATe ON')
            self.instr.write(f'CHANnel{ch}:SCALe {scale_v_per_div}')

    def set_timebase(self, scale_s_per_div: float,
                     position_s: float = 0.0) -> Tuple[float, float]:
        """Set horizontal scale (s/div) and horizontal position (s).

        Returns the (scale, position) actually in effect after the scope
        rounds them to its available settings.
        """
        self.instr.write(f'TIMebase:SCALe {scale_s_per_div}')
        self.instr.write(f'TIMebase:POSition {position_s}')
        try:
            sc = float(self.instr.query('TIMebase:SCALe?').strip())
            po = float(self.instr.query('TIMebase:POSition?').strip())
        except Exception:
            sc, po = scale_s_per_div, position_s
        return sc, po

    def configure_trigger(self, source_ch: int = 2, slope: str = 'rising',
                          level: Optional[float] = None,
                          mode: str = 'normal') -> float:
        """
        Set up an edge trigger on an *input channel* (no external trigger wire
        needed) and return the trigger level actually in effect (volts).
        """
        slp = self._SLOPE_MAP.get(str(slope).lower(), 'POSitive')
        mde = 'AUTO' if str(mode).lower().startswith('a') else 'NORMal'

        self.instr.write('TRIGger:A:TYPE EDGE')
        self.instr.write(f'TRIGger:A:SOURce CH{source_ch}')
        self.instr.write(f'TRIGger:A:EDGE:SLOPe {slp}')
        self.instr.write(f'TRIGger:A:MODE {mde}')

        if level is None:
            # FINDlevel needs a live signal, so run briefly, find, then stop.
            self.instr.write('RUN')
            time.sleep(0.3)
            self.instr.write('TRIGger:A:FINDlevel')
            time.sleep(0.2)
            self.instr.write('STOP')
        else:
            self.instr.write(f'TRIGger:A:LEVel{source_ch}:VALue {level}')

        try:
            return float(self.instr.query(
                f'TRIGger:A:LEVel{source_ch}:VALue?').strip())
        except Exception:
            return float('nan')

    # ---- per-run acquisition ------------------------------------------- #
    def arm(self) -> None:
        """Stop any current acquisition, arm single-shot, and wait until the
        scope's trigger front-end is actually in 'wait-for-trigger' state.

        RTB2000 quirk: SINGle returns immediately, but the trigger circuitry
        needs a few tens of ms to be fully armed after a previous STOP. If
        the ADC signal appears in that gap, the trigger is missed. Polling
        ACQuire:STATe? until it leaves 'STOP' guarantees we don't proceed
        before the scope is truly listening.
        """
        self.instr.write('STOP')
        try:
            self.instr.query('*OPC?')          # force STOP fully applied
        except Exception:
            pass

        self.instr.write('SINGle')

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                state = self.instr.query('ACQuire:STATe?').strip().upper()
            except Exception:
                state = ''
            # RTB2000 typically reports 'RUN' (armed & waiting) after SINGle;
            # 'COMP' after the frame is done; 'STOP' if it never armed.
            if state and not state.startswith('STOP'):
                return
            time.sleep(0.01)

        # Fallback safety delay if polling didn't confirm the state.
        time.sleep(0.15)

    def wait_complete(self, timeout_s: float = 10.0) -> bool:
        """Block until the single acquisition has triggered and finished."""
        old = self.instr.timeout
        self.instr.timeout = int(timeout_s * 1000)
        try:
            self.instr.query('*OPC?')          # completes when SINGle done
            return True
        except Exception:
            return False                       # VISA timeout -> never triggered
        finally:
            self.instr.timeout = old

    def read_channel(self, ch: int) -> Tuple[np.ndarray, np.ndarray]:
        """Return (time_seconds, voltage_volts) for oscilloscope channel `ch`."""
        # Header: "XStart,XStop,RecordLength,ValuesPerSample"
        header = self.instr.query(f'CHANnel{ch}:DATA:HEADer?').strip()
        parts = header.split(',')
        x_start = float(parts[0])
        x_stop  = float(parts[1])

        y = self.instr.query_ascii_values(
            f'CHANnel{ch}:DATA?', container=np.array, separator=',')
        n = len(y)
        if n == 0:
            return np.array([]), np.array([])
        t = np.linspace(x_start, x_stop, n)
        return t, np.asarray(y, dtype=float)

    def close(self) -> None:
        try:
            self.instr.write('RUN')            # leave the screen live again
        except Exception:
            pass
        for obj in (getattr(self, 'instr', None), getattr(self, 'rm', None)):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Serial helpers                                                              #
# --------------------------------------------------------------------------- #
def _open(port: str, baud: int, name: str) -> serial.Serial:
    try:
        ser = serial.Serial(port, baud, timeout=0.2,
                            bytesize=8, parity='N', stopbits=1)
    except serial.SerialException as e:
        print(f"[!] Cannot open {name} on {port} @ {baud} baud: {e}",
              file=sys.stderr)
        sys.exit(1)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    print(f"[+] {name:<9} opened on {port} @ {baud} baud")
    return ser


def _wait_for(ser: serial.Serial, target: bytes, timeout: float) -> bool:
    """Block until `target` byte is read from `ser`, or timeout expires."""
    ser.timeout = 0.2
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        b = ser.read(1)
        if b == target:
            return True
    return False


def _read_ascii_block(ser: serial.Serial,
                     expected: int,
                     leftover: bytes = b"",
                     timeout: float = 60.0) -> Tuple[List[int], bytes]:
    """
    Read decimal ASCII lines terminated by "\r\n" until a line == ":".
    Returns (samples, leftover). 'leftover' is any bytes read past the ":"
    that belong to the NEXT block — feed them into the next call.
    """
    ser.timeout = 0.2
    samples: List[int] = []
    acc = leftover
    deadline = time.monotonic() + timeout
    last_progress = 0

    while time.monotonic() < deadline:
        chunk = ser.read(8192)
        if chunk:
            acc += chunk

        while b"\r\n" in acc:
            line, acc = acc.split(b"\r\n", 1)
            line = line.strip()
            if line == BUF_TERMINATOR:
                return samples, acc
            if not line:
                continue
            try:
                samples.append(int(line))
            except ValueError:
                pass

        if len(samples) - last_progress >= 1000:
            print(f"      ...{len(samples):>5}/{expected} samples",
                  end="\r", flush=True)
            last_progress = len(samples)

        if not chunk:
            time.sleep(0.005)

    raise TimeoutError(
        f"Timed out reading ASCII block "
        f"({len(samples)}/{expected} samples before the ':' terminator)"
    )


def _drain(ser: serial.Serial, quiet_seconds: float = 1.0,
           hard_limit: float = 15.0) -> int:
    """Read and discard bytes until `quiet_seconds` idle, or `hard_limit`."""
    ser.timeout = 0.2
    n = 0
    started = time.monotonic()
    last_data = time.monotonic()
    while True:
        chunk = ser.read(8192)
        if chunk:
            n += len(chunk)
            last_data = time.monotonic()
        else:
            if time.monotonic() - last_data > quiet_seconds:
                break
        if time.monotonic() - started > hard_limit:
            break
    return n


def _fit(x: List[int]) -> np.ndarray:
    """Trim/pad a raw sample list to exactly NUM_SAMPLES uint16 values."""
    arr = np.array(x, dtype=np.uint16)
    if arr.size >= NUM_SAMPLES:
        return arr[:NUM_SAMPLES]
    pad = np.full(NUM_SAMPLES - arr.size,
                  arr[-1] if arr.size else 0,
                  dtype=np.uint16)
    return np.concatenate([arr, pad])


# --------------------------------------------------------------------------- #
# Scope setup + per-run readout (shared by both protocols)                    #
# --------------------------------------------------------------------------- #
def setup_scope(cfg: Config) -> Optional[Scope]:
    """Open the scope and apply horizontal / vertical / trigger settings.

    Returns a ready Scope, or None if scope capture is disabled or setup
    failed (in which case the experiment still runs serial-only, but every
    run will end up being discarded — see the save policy in main()).
    """
    if not cfg.scope_resource:
        return None

    print(f"  Scope     : {cfg.scope_resource}")
    scope: Optional[Scope] = None
    try:
        scope = Scope(cfg.scope_resource)
        print(f"  Scope IDN : {scope.idn()}")

        scope.set_vertical({cfg.scope_ch_adc1, cfg.scope_ch_adc2,
                            cfg.scope_ch_adc3},
                           cfg.scope_volts_div)
        sc, po = scope.set_timebase(cfg.scope_time_div, cfg.scope_h_pos)
        lvl = scope.configure_trigger(
            source_ch=cfg.scope_trig_source,
            slope=cfg.scope_trig_slope,
            level=cfg.scope_trig_level,
            mode=cfg.scope_trig_mode,
        )

        lsrc = "find" if cfg.scope_trig_level is None else "manual"
        print(f"  Scope CHs : ADC1=CH{cfg.scope_ch_adc1}, "
              f"ADC2=CH{cfg.scope_ch_adc2}, "
              f"ADC3=CH{cfg.scope_ch_adc3}")
        print(f"  Scope vert: {cfg.scope_volts_div:g} V/div")
        print(f"  Scope horz: {sc*1e6:.3g} µs/div, position {po*1e6:.3g} µs")
        print(f"  Scope trig: CH{cfg.scope_trig_source} {cfg.scope_trig_slope} "
              f"edge, {cfg.scope_trig_mode} mode, level={lvl:.3f} V ({lsrc})")
        return scope
    except Exception as e:
        print(f"  [!] Could not open/configure scope: {e}")
        print("  [!] Continuing WITHOUT oscilloscope capture "
              "(every run will be discarded).")
        if scope is not None:
            scope.close()
        return None


def _capture_scope_traces(scope: Optional[Scope], cfg: Config,
                          meta: Dict[str, Any]) -> None:
    """Wait for the scope's single-shot, read all three channels into meta.

    Populates meta['scope_adc1/2/3'] atomically: either all three keys are
    set (successful capture), or none of them are (any failure — VISA
    timeout, no valid trigger edge, read error). Downstream code uses the
    presence/absence of these keys to decide whether the run counts as a
    successful scope capture.
    """
    if scope is None:
        return
    print("      waiting for scope single-shot to finish...")
    if scope.wait_complete(cfg.scope_timeout_s):
        try:
            t1, y1 = scope.read_channel(cfg.scope_ch_adc1)
            t2, y2 = scope.read_channel(cfg.scope_ch_adc2)
            t3, y3 = scope.read_channel(cfg.scope_ch_adc3)
            meta['scope_adc1'] = (t1, y1)
            meta['scope_adc2'] = (t2, y2)
            meta['scope_adc3'] = (t3, y3)
            print(f"      -> scope: CH{cfg.scope_ch_adc1}={len(y1)} pts, "
                  f"CH{cfg.scope_ch_adc2}={len(y2)} pts, "
                  f"CH{cfg.scope_ch_adc3}={len(y3)} pts")
        except Exception as e:
            print(f"      [!] scope readout failed: {e}")
    else:
        print(f"      [!] scope did not trigger within "
              f"{cfg.scope_timeout_s:.1f}s. No valid "
              f"{cfg.scope_trig_slope} edge on CH{cfg.scope_trig_source} "
              f"at {cfg.scope_trig_level} V. Adjust --scope-trig-level / "
              f"--scope-trig-slope, or use --scope-trig-mode auto.")


# --------------------------------------------------------------------------- #
# Persistent serial ports (opened once, reused across every run)              #
# --------------------------------------------------------------------------- #
def open_ports(cfg: Config) -> Dict[str, serial.Serial]:
    """Open every serial port the selected protocol needs, one time.

    The returned dict is kept alive by main() for the whole session and
    passed into every run. Opening a serial port on Windows costs ~100–
    500 ms per call, and — worse — a fresh open() on an STM32's USB-CDC
    virtual COM often triggers a full USB re-enumeration on the device,
    which can silently reset the MCU. Doing it once, up-front, removes
    both the latency and the re-enumeration risk between successive runs.
    """
    if cfg.protocol == PROTO_AUTO_INDUCED:
        return {
            'bodyguard': _open(cfg.bodyguard_port, BODYGUARD_BAUD, "STM32N6"),
        }
    return {
        'bodyguard': _open(cfg.bodyguard_port, BODYGUARD_BAUD, "Bodyguard"),
        'victim':    _open(cfg.victim_port,    VICTIM_BAUD,    "Victim"),
        'attacker':  _open(cfg.attacker_port,  ATTACKER_BAUD,  "Attacker"),
    }


def close_ports(ports: Dict[str, serial.Serial]) -> None:
    for s in ports.values():
        try:
            s.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Induced protocol — Bodyguard + Victim + Attacker glitch chain (6 steps)     #
# --------------------------------------------------------------------------- #
def run_experiment_p1(cfg: Config,
                      ports: Dict[str, serial.Serial],
                      scope: Optional[Scope] = None
                      ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    # Persistent handles — opened once by main(), reused every run.
    bodyguard = ports['bodyguard']
    victim    = ports['victim']
    attacker  = ports['attacker']

    meta: Dict[str, Any] = {}

    # Cheap per-run hygiene: drop anything the boards may have queued after
    # the previous run's drain. The one-time settle sleep + drain that used
    # to live here has moved to main(), right after open_ports().
    for s in (bodyguard, victim, attacker):
        s.reset_input_buffer()

    # 0. Arm the oscilloscope FIRST so it is waiting for its channel
    #    trigger before the glitch chain begins.
    if scope is not None:
        print("[0/6] Arming oscilloscope (STOP + SINGle)...")
        try:
            scope.arm()
        except Exception as e:
            print(f"      [!] scope arm failed: {e} — continuing without scope")
            scope = None

    # 1. Arm the Attacker
    print("[1/6] Arming Attacker (send 'A')...")
    attacker.write(CMD_START)
    attacker.flush()
    time.sleep(0.1)     # give TIM1 time to reconfigure with fresh CCR1

    # 2. Kick off Bodyguard acquisition
    print("[2/6] Starting Bodyguard acquisition (send 'A')...")
    bodyguard.write(CMD_START)
    bodyguard.flush()

    # 3. Wait for 'E' from Bodyguard
    print("[3/6] Waiting for Bodyguard 'E' (acquisition complete)...")
    if not _wait_for(bodyguard, ACK_ACQ_DONE, timeout=15.0):
        raise RuntimeError("Bodyguard never signalled 'E' — check wiring / power.")
    print("      -> acquisition complete")

    # 3b. Scope readout (if any)
    _capture_scope_traces(scope, cfg, meta)

    # 4. Snapshot the Victim's response
    victim.timeout = 0.4
    vraw = victim.read(128)
    vtext = vraw.decode(errors='replace').strip()
    meta['victim_response_raw'] = vraw
    meta['victim_response'] = vtext

    if 'Encrypt' in vtext:
        print(f"[4/6] Victim: {vtext!r}  -> AES completed (glitch missed / ineffective)")
        meta['glitch_effective'] = False
    elif vtext == "" or vtext == "Start":
        print(f"[4/6] Victim: {vtext!r}  -> possibly reset by glitch")
        meta['glitch_effective'] = True
    else:
        print(f"[4/6] Victim: {vtext!r}")
        meta['glitch_effective'] = None

    # 5. Ask Attacker for the CCR1 it used
    print("[5/6] Querying Attacker for glitch timing (send 'I')...")
    attacker.reset_input_buffer()
    attacker.write(CMD_INFO)
    attacker.flush()
    attacker.timeout = 1.0
    line = attacker.readline().decode(errors='replace').strip()
    print(f"      -> attacker replied: {line!r}")
    meta['attacker_response'] = line
    if line.startswith("CCR1="):
        try:
            ccr1 = int(line.split("=", 1)[1])
            meta['ccr1'] = ccr1
            meta['glitch_delay_us'] = ccr1 / (ATTACKER_TIM1_HZ / 1e6)
            print(f"      -> CCR1 = {ccr1} ticks "
                  f"({meta['glitch_delay_us']:.3f} µs after Victim's trigger edge)")
        except ValueError:
            pass
    elif line == "NO_PWM":
        print("      -> Attacker reports NO_PWM: trigger never reached PA9. "
              "Check the Victim->Attacker trigger wire.")

    # 6. Fetch the two Bodyguard acquisition buffers
    print("\n[6/6] Fetching traces...")
    bodyguard.reset_input_buffer()

    print("      sending 'X'; reading ADC2 block...")
    bodyguard.write(CMD_FETCH_1)
    bodyguard.flush()
    adc2, leftover = _read_ascii_block(bodyguard, NUM_SAMPLES, timeout=60.0)
    print(f"      -> ADC2: {len(adc2)} samples                    ")

    print("      reading ADC1 block...")
    adc1, _ = _read_ascii_block(bodyguard, NUM_SAMPLES, leftover, timeout=60.0)
    print(f"      -> ADC1: {len(adc1)} samples                    ")

    print("      sending 'Y' and draining retransmit...")
    bodyguard.write(CMD_FETCH_2)
    bodyguard.flush()
    drained = _drain(bodyguard, quiet_seconds=1.0, hard_limit=15.0)
    print(f"      -> drained {drained} bytes; Bodyguard now IDLE")

    return _fit(adc1), _fit(adc2), meta


# --------------------------------------------------------------------------- #
# Auto-Induced protocol — single STM32N6 dual-ADC recorder (4 steps)          #
# --------------------------------------------------------------------------- #
def run_experiment_p2(cfg: Config,
                      ports: Dict[str, serial.Serial],
                      scope: Optional[Scope] = None
                      ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    # Persistent handle — opened once by main(), reused every run.
    board = ports['bodyguard']

    meta: Dict[str, Any] = {}

    # Cheap per-run hygiene: drop anything the board may have queued after
    # the previous run. The one-time settle sleep + drain that used to
    # live here has moved to main(), right after open_ports().
    board.reset_input_buffer()

    # 0. Arm the oscilloscope FIRST so it is waiting for its channel
    #    trigger before the acquisition begins.
    if scope is not None:
        print("[0/4] Arming oscilloscope (STOP + SINGle)...")
        try:
            scope.arm()
        except Exception as e:
            print(f"      [!] scope arm failed: {e} — continuing without scope")
            scope = None

    # 1. Start the dual-ADC acquisition on the STM32N6
    print("[1/4] Starting acquisition (send 'A')...")
    board.write(CMD_START)
    board.flush()

    # 2. Wait for 'E' (both buffers full)
    print("[2/4] Waiting for 'E' (acquisition complete)...")
    if not _wait_for(board, ACK_ACQ_DONE, timeout=15.0):
        raise RuntimeError("STM32N6 never signalled 'E' — check wiring / power.")
    print("      -> acquisition complete")

    # 2b. Scope readout (if any)
    _capture_scope_traces(scope, cfg, meta)

    # 3. Fetch buffer #1 (ADC2) with 'X'
    print("[3/4] Fetching ADC2 buffer (send 'X')...")
    board.reset_input_buffer()
    board.write(CMD_FETCH_1)
    board.flush()
    adc2, leftover = _read_ascii_block(board, NUM_SAMPLES, timeout=60.0)
    print(f"      -> ADC2: {len(adc2)} samples                    ")

    # 4. Fetch buffer #2 (ADC1) with 'Y'
    print("[4/4] Fetching ADC1 buffer (send 'Y')...")
    board.write(CMD_FETCH_2)
    board.flush()
    adc1, _ = _read_ascii_block(board, NUM_SAMPLES, leftover, timeout=60.0)
    print(f"      -> ADC1: {len(adc1)} samples                    ")

    return _fit(adc1), _fit(adc2), meta


def run_experiment(cfg: Config,
                   ports: Dict[str, serial.Serial],
                   scope: Optional[Scope] = None
                   ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Dispatch to the appropriate protocol implementation.

    Auto-Induced (menu 1) -> 4-step STM32N6 flow  (run_experiment_p2)
    Induced      (menu 2) -> 6-step glitch chain  (run_experiment_p1)
    """
    if cfg.protocol == PROTO_AUTO_INDUCED:
        return run_experiment_p2(cfg, ports, scope=scope)
    if cfg.protocol == PROTO_INDUCED:
        return run_experiment_p1(cfg, ports, scope=scope)
    raise ValueError(f"Unknown protocol: {cfg.protocol}")


# --------------------------------------------------------------------------- #
# Presentation & CSV                                                          #
# --------------------------------------------------------------------------- #
def _time_axis(n: int, sample_rate: Optional[float]) -> Tuple[np.ndarray, str]:
    if sample_rate and sample_rate > 0:
        return np.arange(n) / sample_rate * 1e6, "Time (µs)"
    return np.arange(n).astype(float), "Sample index"


def plot_traces(adc1: np.ndarray, adc2: np.ndarray,
                meta: Dict[str, Any],
                protocol_name: str = "Acquisition",
                sample_rate: Optional[float] = None,
                show: bool = True) -> None:
    """Show (or silently close) the per-run matplotlib figure.

    No PNG is ever written — only raw CSV data is persisted.
    """
    t, xlabel = _time_axis(len(adc1), sample_rate)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle(f"{protocol_name} — dual-ADC capture", fontsize=13)

    # --- Track 1 : ADC1 alone ---
    ax1.plot(t, adc1, color='#1f6feb', linewidth=0.7)
    ax1.set_title("Track 1 — ADC1")
    ax1.set_ylabel("ADC1 code (8-bit)")
    ax1.set_ylim(-5, 260)
    ax1.grid(True, alpha=0.3)

    # --- Track 2 : ADC2 alone ---
    ax2.plot(t, adc2, color='#d1242f', linewidth=0.7)
    ax2.set_title("Track 2 — ADC2")
    ax2.set_ylabel("ADC2 code (8-bit)")
    ax2.set_ylim(-5, 260)
    ax2.grid(True, alpha=0.3)

    # --- Track 3 : ADC1 + ADC2 overlaid ---
    ax3.plot(t, adc1, color='#1f6feb', linewidth=0.7, label='ADC1', alpha=0.85)
    ax3.plot(t, adc2, color='#d1242f', linewidth=0.7, label='ADC2', alpha=0.85)
    ax3.set_title("Track 3 — ADC1 & ADC2 overlaid")
    ax3.set_ylabel("ADC code (8-bit)")
    ax3.set_ylim(-5, 260)
    ax3.set_xlabel(xlabel)
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='upper right', fontsize=9)

    info_lines = []
    # Induced-only annotations (silently skipped in Auto-Induced runs
    # because these keys are never populated there).
    if meta.get('victim_response'):
        info_lines.append(f"Victim: {meta['victim_response']!r}")
    if meta.get('glitch_effective') is True:
        info_lines.append("Outcome: likely reset by glitch")
    elif meta.get('glitch_effective') is False:
        info_lines.append("Outcome: AES completed (glitch ineffective)")
    if 'ccr1' in meta:
        info_lines.append(f"CCR1 = {meta['ccr1']} ticks")
        info_lines.append(f"Glitch delay = {meta['glitch_delay_us']:.2f} µs "
                          "(from Victim trigger)")
    elif meta.get('attacker_response') == "NO_PWM":
        info_lines.append("Attacker: NO_PWM (trigger not seen)")
    # Shared annotation
    if 'scope_adc1' in meta:
        info_lines.append("Scope: waveforms captured")

    if info_lines:
        fig.text(0.995, 0.965, "\n".join(info_lines),
                 ha='right', va='top', fontsize=9, family='monospace',
                 bbox=dict(boxstyle='round,pad=0.4',
                           facecolor='#fff8dc', edgecolor='#888', alpha=0.9))

    fig.tight_layout(rect=(0, 0, 1, 0.96))

    if show:
        plt.show()
    else:
        plt.close(fig)


def ask_save(run: int, total: int) -> bool:
    """Ask the user whether to save this run's traces. Default = yes."""
    if not sys.stdin or not sys.stdin.isatty():
        print("[?] Non-interactive stdin: saving by default.")
        return True
    prompt = f"[?] Save this run's traces (run {run}/{total})? [Y/n]: "
    while True:
        try:
            ans = input(prompt).strip().lower()
        except EOFError:
            print("  (EOF) -> saving by default")
            return True
        if ans in ("", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  please answer y or n")


def _append_sample_row(path: str, run_id, samples: list) -> None:
    """Append one trace as a single row: [run_id, *samples].

    `run_id` is either the timestamped Run ID string (e.g.
    "20260824_153000_run007", for a real trace row) or the sentinel
    TIME_AXIS_ID (for the time-axis row at the top of a scope CSV).
    """
    with open(path, 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([run_id, *samples])


def append_bodyguard_traces(adc1_path: str, adc2_path: str,
                            run_id: str,
                            adc1: np.ndarray, adc2: np.ndarray) -> None:
    """Append the dual-ADC ADC1/ADC2 traces, one raw-sample row per file,
    each row prefixed with the Run ID."""
    _append_sample_row(adc1_path, run_id, [int(v) for v in adc1.tolist()])
    _append_sample_row(adc2_path, run_id, [int(v) for v in adc2.tolist()])
    print(f"[+] ADC1 (run {run_id}) -> {adc1_path}")
    print(f"[+] ADC2 (run {run_id}) -> {adc2_path}")


def append_scope_traces(paths: Dict[str, str], run_id: str,
                        meta: Dict[str, Any]) -> None:
    """Append one raw-sample (volts) row to each captured scope-channel CSV,
    prefixed with the Run ID.

    The very first row of each scope file is the time axis (seconds),
    tagged with the sentinel Run ID `TIME` so it can be distinguished from
    real trace rows. It is written on the first saved run only.
    """
    for key, path in paths.items():
        if key in meta and len(meta[key][1]) > 0:
            t, y = meta[key]

            if not os.path.exists(path) or os.path.getsize(path) == 0:
                _append_sample_row(
                    path, TIME_AXIS_ID,
                    [f"{v:.6e}" for v in np.asarray(t, float).tolist()],
                )
                print(f"[+] Scope time axis written to {path} "
                      f"({len(t)} pts)")

            _append_sample_row(
                path, run_id,
                [f"{v:.6e}" for v in np.asarray(y, float).tolist()],
            )
            label = key.replace('scope_', '').upper()
            print(f"[+] Scope {label} (run {run_id}) -> {path}")


# --------------------------------------------------------------------------- #
# Startup menu                                                                #
# --------------------------------------------------------------------------- #
def choose_protocol(default: Optional[int] = None) -> int:
    """Show the acquisition-mode menu and return 1 or 2.

    If `default` is given (from --protocol), return it directly without
    asking. Under non-interactive stdin with no default, falls back to 1
    (Auto-Induced).
    """
    if default in (PROTO_AUTO_INDUCED, PROTO_INDUCED):
        return default
    if not sys.stdin or not sys.stdin.isatty():
        print("[!] Non-interactive stdin and no --protocol given; "
              "defaulting to 1 (Auto-Induced).")
        return PROTO_AUTO_INDUCED

    print()
    print("=" * 62)
    print("  Select acquisition mode")
    print("=" * 62)
    print("  1. Auto-Induced")
    print("     -> 1 board:  STM32N6 dual-ADC recorder (4-step flow)")
    print("     -> traces  : <out-dir>/Autoinduced_Traces/")
    print("  2. Induced")
    print("     -> 3 boards: Bodyguard + Victim + Attacker glitch chain")
    print("                  (6-step flow)")
    print("     -> traces  : <out-dir>/Induced_Traces/")
    print("=" * 62)
    while True:
        try:
            ans = input("Choice [1/2]: ").strip()
        except EOFError:
            print("  (EOF) -> defaulting to 1 (Auto-Induced)")
            return PROTO_AUTO_INDUCED
        if ans == "1":
            return PROTO_AUTO_INDUCED
        if ans == "2":
            return PROTO_INDUCED
        print("  please enter 1 or 2")


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Unified STM32 acquisition controller with two protocols: "
                    "(1) Auto-Induced single STM32N6 dual-ADC (4-step flow), "
                    "(2) Induced 3-board glitch-attack chain (6-step flow). "
                    "Optionally captures ADC1/ADC2 from an R&S RTB2000 scope, "
                    "and saves all traces to CSV in a mode-specific folder. "
                    "A run is only saved when the scope capture succeeded; "
                    "every CSV row is tagged with the same integer Run ID.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--protocol", type=int, choices=[1, 2], default=None,
                    help="Skip the startup menu (1=Auto-Induced, 2=Induced)")
    ap.add_argument("-b", "--bodyguard", required=True,
                    help="Serial port of the Bodyguard / STM32N6 board (LPUART1)")
    ap.add_argument("-v", "--victim", default=None,
                    help="Serial port of the Victim board (Induced mode only)")
    ap.add_argument("-a", "--attacker", default=None,
                    help="Serial port of the Attacker board (Induced mode only)")
    ap.add_argument("--sample-rate", type=float, default=None,
                    help="ADC sample rate in Hz. Defaults per protocol: "
                         f"{SAMPLE_RATE_P2_HZ:g} Hz for Auto-Induced, "
                         f"{SAMPLE_RATE_P1_HZ:g} Hz for Induced.")
    ap.add_argument("--out-dir", type=str, default=".",
                    help="Base directory for output. A mode-specific subfolder "
                         "(Autoinduced_Traces or Induced_Traces) is created "
                         "inside it.")
    ap.add_argument("--png", action="store_true",
                    help="[no-op, kept for backward compat] PNGs are no longer "
                         "written under any circumstances; only raw CSV is saved")
    ap.add_argument("--no-plot", action='store_true',
                    help="Do not open the interactive plot window")
    ap.add_argument("--repeat", type=int, default=1,
                    help="Number of experiment runs")
    ap.add_argument("--auto-save", action='store_true',
                    help="Skip the 'Save this run's traces? [Y/n]' prompt and "
                         "always save (but a run is still discarded if the "
                         "scope capture failed).")

    # --- oscilloscope options ---
    sg = ap.add_argument_group("oscilloscope (R&S RTB2000)")
    sg.add_argument("--scope", nargs="?", const=DEFAULT_SCOPE_RESOURCE,
                    default=None, metavar="VISA_RESOURCE",
                    help="Enable scope capture. Bare --scope uses the default "
                         f"resource ({DEFAULT_SCOPE_RESOURCE}); pass a string "
                         "to override.")
    sg.add_argument("--scope-ch-adc1", type=int, default=1,
                    help="Scope channel showing the ADC1 signal")
    sg.add_argument("--scope-ch-adc2", type=int, default=2,
                    help="Scope channel showing the ADC2 signal")
    sg.add_argument("--scope-ch-adc3", type=int, default=3,
                    help="Third scope channel to capture")
    sg.add_argument("--scope-timeout", type=float, default=10.0,
                    help="Max seconds to wait for the scope to trigger")
    sg.add_argument("--scope-time-div", type=float, default=DEF_TIME_DIV,
                    help="Horizontal scale in seconds/div")
    sg.add_argument("--scope-h-pos", type=float, default=DEF_H_POS,
                    help="Horizontal position in seconds")
    sg.add_argument("--scope-volts-div", type=float, default=DEF_VOLTS_DIV,
                    help="Vertical scale in volts/div for the ADC channels")
    sg.add_argument("--scope-trig-source", type=int, default=DEF_TRIG_SOURCE,
                    help="Channel to TRIGGER on (1=ADC1, 2=ADC2, ...)")
    sg.add_argument("--scope-trig-slope",
                    choices=['rising', 'falling', 'either'],
                    default=DEF_TRIG_SLOPE, help="Trigger edge slope")
    sg.add_argument("--scope-trig-level", default=str(DEF_TRIG_LEVEL),
                    help="Trigger level in volts, or 'find' to auto-set to "
                         "50%% of the signal amplitude")
    sg.add_argument("--scope-trig-mode", choices=['normal', 'auto'],
                    default=DEF_TRIG_MODE,
                    help="normal = wait for a real edge; auto = always produce "
                         "a frame even without a valid edge")

    args = ap.parse_args()

    # --------------------------------------------------------------------- #
    # Menu (unless overridden by --protocol)                                #
    # --------------------------------------------------------------------- #
    protocol = choose_protocol(args.protocol)

    # Default sample rate depends on the chosen protocol.
    #   Auto-Induced -> STM32N6 rate;  Induced -> Bodyguard F4 rate.
    if args.sample_rate is None:
        args.sample_rate = (SAMPLE_RATE_P2_HZ if protocol == PROTO_AUTO_INDUCED
                            else SAMPLE_RATE_P1_HZ)

    # Induced (glitch chain) needs -v and -a. Prompt interactively, else bail.
    if protocol == PROTO_INDUCED:
        if not args.victim or not args.attacker:
            if sys.stdin and sys.stdin.isatty():
                if not args.victim:
                    args.victim = input("Enter Victim serial port : ").strip()
                if not args.attacker:
                    args.attacker = input("Enter Attacker serial port: ").strip()
            if not args.victim or not args.attacker:
                print("[!] Induced mode (3-board glitch chain) requires both "
                      "--victim and --attacker.", file=sys.stderr)
                sys.exit(2)

    # Traces from the two modes never mix: each run drops its CSVs into a
    # protocol-specific subfolder inside --out-dir.
    #   Auto-Induced -> <out_dir>/Autoinduced_Traces/
    #   Induced      -> <out_dir>/Induced_Traces/
    trace_subfolder = ("Autoinduced_Traces" if protocol == PROTO_AUTO_INDUCED
                       else "Induced_Traces")
    args.out_dir = os.path.join(args.out_dir, trace_subfolder)
    os.makedirs(args.out_dir, exist_ok=True)

    # Parse trigger level: 'find' -> None (FINDlevel), else float volts
    if str(args.scope_trig_level).strip().lower() in ('find', ''):
        trig_level: Optional[float] = None
    else:
        try:
            trig_level = float(args.scope_trig_level)
        except ValueError:
            print(f"[!] Invalid --scope-trig-level {args.scope_trig_level!r}; "
                  "using 'find'.")
            trig_level = None

    cfg = Config(
        protocol=protocol,
        bodyguard_port=args.bodyguard,
        victim_port=args.victim,
        attacker_port=args.attacker,
        sample_rate=args.sample_rate,
        show_plot=not args.no_plot,
        out_dir=args.out_dir,
        scope_resource=args.scope,
        scope_ch_adc1=args.scope_ch_adc1,
        scope_ch_adc2=args.scope_ch_adc2,
        scope_ch_adc3=args.scope_ch_adc3,
        scope_timeout_s=args.scope_timeout,
        scope_time_div=args.scope_time_div,
        scope_h_pos=args.scope_h_pos,
        scope_volts_div=args.scope_volts_div,
        scope_trig_source=args.scope_trig_source,
        scope_trig_slope=args.scope_trig_slope,
        scope_trig_level=trig_level,
        scope_trig_mode=args.scope_trig_mode,
        auto_save=args.auto_save,
    )

    print("=" * 62)
    print(f"  Acquisition mode: {PROTO_NAMES[protocol]}")
    print("=" * 62)
    if protocol == PROTO_INDUCED:
        print(f"  Bodyguard : {cfg.bodyguard_port:<12} @ {BODYGUARD_BAUD} baud")
        print(f"  Victim    : {cfg.victim_port:<12} @ {VICTIM_BAUD} baud")
        print(f"  Attacker  : {cfg.attacker_port:<12} @ {ATTACKER_BAUD} baud")
    else:
        print(f"  STM32N6   : {cfg.bodyguard_port:<12} @ {BODYGUARD_BAUD} baud")
    if cfg.sample_rate:
        print(f"  Sample rate: {cfg.sample_rate:g} Hz "
              f"({NUM_SAMPLES / cfg.sample_rate * 1e6:.1f} µs window)")

    # Open + configure the scope once, at startup, before any acquisition.
    scope = setup_scope(cfg)

    # Accumulating raw-sample files (created/appended, not per-run).
    bg_adc1 = os.path.join(cfg.out_dir, "bodyguard_adc1.csv")
    bg_adc2 = os.path.join(cfg.out_dir, "bodyguard_adc2.csv")
    # Scope files are named after the actual CH number on the instrument, so
    # if you rewire ADC1 to CH4 later the file name tracks the wiring rather
    # than the "adc1" label.
    scope_paths = {
        'scope_adc1': os.path.join(cfg.out_dir,
                                   f"scope_ch{cfg.scope_ch_adc1}.csv"),
        'scope_adc2': os.path.join(cfg.out_dir,
                                   f"scope_ch{cfg.scope_ch_adc2}.csv"),
        'scope_adc3': os.path.join(cfg.out_dir,
                                   f"scope_ch{cfg.scope_ch_adc3}.csv"),
    }
    print(f"  ADC CSV     : {bg_adc1}, {bg_adc2}")
    if scope is not None:
        print(f"  Scope CSV   : " + ", ".join(scope_paths.values()))
    else:
        print("  Scope CSV   : (disabled — every run will be discarded)")
    if cfg.auto_save:
        print("  Save prompt : DISABLED (--auto-save: always saves when scope OK)")
    else:
        print("  Save prompt : ENABLED (asks after each run when scope OK)")
    print("  Save policy : run saved only if scope capture succeeded; "
          "same Run ID across all CSVs")
    print("=" * 62)

    # Fixed once per program launch; combined with the run number this
    # gives Run IDs like 20260824_153000_run007.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Open every serial port ONCE for the whole session — persistent
    # across runs, which removes both the per-run open() latency and the
    # USB re-enumeration risk on CDC devices.
    ports = open_ports(cfg)

    # First-open settle: give the boards a moment to finish any reset
    # chatter that opening the ports may have triggered, then drop it.
    # This used to run at the top of every experiment; subsequent runs
    # reuse the same open handles and no longer need it.
    time.sleep(0.2)
    for s in ports.values():
        s.reset_input_buffer()

    try:
        for run in range(1, args.repeat + 1):
            if args.repeat > 1:
                print(f"\n### Run {run}/{args.repeat} ###")

            adc1, adc2, meta = run_experiment(cfg, ports, scope=scope)

            print("\n--- Trace statistics ---")
            print(f"  ADC1: n={len(adc1):>4}  min={adc1.min():>3}  "
                  f"max={adc1.max():>3}  mean={adc1.mean():6.2f}  std={adc1.std():5.2f}")
            print(f"  ADC2: n={len(adc2):>4}  min={adc2.min():>3}  "
                  f"max={adc2.max():>3}  mean={adc2.mean():6.2f}  std={adc2.std():5.2f}")

            # ---- Save decision ---------------------------------------- #
            # Rule 1: a run is only ever saved if the scope capture
            #         succeeded AND actually triggered — no trigger, a
            #         VISA/read error, or a channel that came back with
            #         zero samples all count as failure. On any of these,
            #         the whole run is discarded (both Bodyguard and
            #         Scope) so the two families of CSV stay perfectly in
            #         sync — no row exists in one that is missing in the
            #         other.
            # Rule 2: when the scope IS OK, we still honour --auto-save vs
            #         the interactive ask_save() prompt.
            scope_ok = all(
                k in meta and len(meta[k][1]) > 0 for k in scope_paths
            )

            if not scope_ok:
                print("[-] Scope capture failed — no trigger, a read error, "
                      "or an empty channel (or scope disabled) — run "
                      "discarded. No Bodyguard or Scope data written.")
                save_this_run = False
            elif cfg.auto_save:
                save_this_run = True
            else:
                save_this_run = ask_save(run, args.repeat)

            if save_this_run:
                # Same Run ID everywhere: this trace in bodyguard_adc1.csv
                # is the same experiment as the trace with the same Run ID
                # in each scope_chN.csv, e.g. "20260824_153000_run007".
                run_id = f"{stamp}_run{run:03d}"
                append_bodyguard_traces(bg_adc1, bg_adc2, run_id, adc1, adc2)
                append_scope_traces(scope_paths, run_id, meta)
            elif scope_ok:
                # scope was fine, user just answered 'n' at the prompt
                print("[-] Run discarded by user. Nothing was written to disk.")

            # Per-run plot (display only — no PNG is ever saved).
            # Only build the figure on the final run and only if plots are
            # enabled; on intermediate runs the figure would immediately be
            # closed anyway, and matplotlib.figure() itself costs ~50-200 ms.
            if cfg.show_plot and run == args.repeat:
                plot_traces(adc1, adc2, meta,
                            protocol_name=PROTO_NAMES[cfg.protocol],
                            sample_rate=cfg.sample_rate,
                            show=True)
    finally:
        close_ports(ports)
        if scope is not None:
            scope.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
        sys.exit(130)


# python3 acquire.py --protocol 1 -b /dev/tty.usbmodem21202 --scope USB0::0x0AAD::0x01D6::302177::INSTR