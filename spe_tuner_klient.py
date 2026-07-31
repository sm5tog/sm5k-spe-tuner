## ==========================================================
## SM5K SPE Tuner Klient v1.0.0
## SPE Expert 1K-FA controller – nätverksklient mot spe_ampd
## (serieporten ägs av en spe_ampd-daemon på en Raspberry Pi,
##  se spe_ampd.py). GUI:t är annars identiskt med spe_tuner.py.
## Author: SM5K (SM5TOG)
## ==========================================================

import tkinter as tk
import customtkinter as ctk
import ee_theme as th
import threading
import queue
import time
import logging
from collections import deque
import json
import os
import socket
import websocket

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────

DEFAULT_RADIO = {"name": "", "input": 1, "tci_host": "127.0.0.1", "tci_port": 50001}

DEFAULT_CONFIG = {
    "ampd_host": "192.168.68.107",
    "ampd_port": 5560,
    "target_power": {"CW": 300, "DIGU": 300},
    "drive_curve":  {},   # {band: {drive: power}} — gemensam för alla modes
    "radios": [
        {"name": "Radio 1", "input": 1, "tci_host": "127.0.0.1", "tci_port": 50001},
        {"name": "Radio 2", "input": 2, "tci_host": "127.0.0.1", "tci_port": 40001},
    ],
}

# Hårdkodade konstanter för SPE Expert-protokollet
SWEEP_STEP  = 20000   # Hz per sweep-steg
FREQ_SETTLE = 1.0     # Sekunder att vänta efter VFO-byte
PAUSE_STEP  = 1.0     # Sekunder mellan sweep-steg

BANDS = {
    "160l": (1810000,  1838000),  "160h": (1840000,  2000000),
    "80l":  (3500000,  3599000),  "80h":  (3600000,  3800000),
    "40l":  (7000000,  7099000),  "40h":  (7100000,  7200000),
    "30l":  (10100000, 10150000),
    "20l":  (14000000, 14099000), "20h":  (14100000, 14350000),
    "17l":  (18068000, 18110000), "17h":  (18110000, 18168000),
    "15l":  (21000000, 21099000), "15h":  (21100000, 21450000),
    "12l":  (24890000, 24920000), "12h":  (24920000, 24990000),
    "10l":  (28000000, 28099000), "10h":  (28100000, 29700000),
    "6l":   (50000000, 50130000), "6h":   (50131000, 52000000),
}

def freq_to_band(freq):
    """Returnerar bandnyckel (t.ex. '15l') för given frekvens, eller None."""
    for key, (start, end) in BANDS.items():
        if start <= freq <= end:
            return key
    return None

import sys as _sys
# Filen sparas bredvid EXE:n (frozen) eller bredvid .py-filen (script).
# Med PyInstaller --onefile pekar __file__ på en temporär mapp som
# försvinner vid stängning – sys.executable pekar på EXE:n istället.
if getattr(_sys, "frozen", False):
    _cfg_path = os.path.join(os.path.dirname(_sys.executable), "settings_klient.json")
else:
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_klient.json")

def load_config():
    try:
        with open(_cfg_path) as f:
            saved = json.load(f)
            result = dict(DEFAULT_CONFIG)
            if "ampd_host" in saved:
                result["ampd_host"] = saved["ampd_host"]
            if "ampd_port" in saved:
                result["ampd_port"] = saved["ampd_port"]
            if "radios" in saved:
                result["radios"] = saved["radios"]
            if "target_power" in saved:
                tp = saved["target_power"]
                if isinstance(tp, dict):
                    result["target_power"] = {
                        "CW":   int(tp.get("CW",   300)),
                        "DIGU": int(tp.get("DIGU", 300)),
                    }
                else:
                    # Migrering från gammalt format (ett int-värde)
                    result["target_power"] = {"CW": int(tp), "DIGU": int(tp)}
            if "drive_curve" in saved:
                result["drive_curve"] = {
                    band: {int(k): float(v) for k, v in pts.items()}
                    for band, pts in saved["drive_curve"].items()
                }
            return result
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(c):
    with open(_cfg_path, "w") as f:
        json.dump(c, f, indent=2)

cfg = load_config()

# Reglerings-logg
_log_path = os.path.join(os.path.dirname(_cfg_path), "reg_log_klient.csv")
_reg_logger = logging.getLogger("reg")
_reg_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_log_path, mode="w", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(message)s"))
_reg_logger.addHandler(_fh)
_reg_logger.info("time,phase,band,mode,target,measured,avg,drive,new_drive,action")

def find_radio(input_nr):
    """Returnerar radio-dict för given ingång, eller None om okänd."""
    for r in cfg.get("radios", []):
        if r.get("input") == input_nr and r.get("name", "").strip():
            return r
    return None

# ──────────────────────────────────────────────────────────
# PALETTE  (ee_theme, ExpertSDR3-look — samma profil som SunSync)
# De gamla konstantnamnen behålls som alias mot ee_theme-tokens så att
# kärnlogiken och process_queue kan använda dem oförändrat.
# ──────────────────────────────────────────────────────────

BG      = th.color("bg_app")          # fönsterbakgrund
PANEL   = th.color("bg_surface")      # panel-/kortyta
CTRL    = th.color("bg_control")      # kontrollyta/cell
BORDER  = th.color("border")
TEXT    = th.color("text_primary")
MUTED   = th.color("text_secondary")
GREEN   = th.color("accent_green")
GREEN_B = th.color("accent_green_bright")
RED     = th.color("accent_red")
AMBER   = th.color("accent_orange")
AMBER_B = th.color("accent_orange_bright")
BLUE    = th.color("accent_blue")
BLUE_B  = th.color("accent_blue_bright")
DISC    = th.color("status_disconnected")
WHITE   = th.color("btn_text_on_accent")
BTNBG   = th.color("btn_idle_bg")
BTNHOV  = th.color("btn_hover_bg")
BTNFG   = th.color("btn_text")
TUNEBG  = th.color("accent_green")    # tune-knapparnas grön
TUNEFG  = th.color("btn_text_on_accent")
STOPBG  = th.color("accent_red")
STOPFG  = th.color("btn_text_on_accent")
TXTINT  = "#2a1818"                    # subtil röd bakgrund vid TX-highlight
RADIUS  = th.METRICS["radius"]

# ──────────────────────────────────────────────────────────
# CORE – STATE
# ──────────────────────────────────────────────────────────

latest = {
    "flags":        None,
    "power":        None,
    "freq":         None,   # aktiv TX-frekvens
    "freq_rx0":     None,   # VFO A på TRX 0
    "freq_rx1":     None,   # VFO A på TRX 1
    "tx_trx":       0,      # aktivt TX-TRX (0=RX1, 1=RX2)
    "trx_active":   {0: False, 1: False},
    "modulation":   {0: None, 1: None},
    "drive":        {0: 100, 1: 100},
    "tune_drive":   {0: 100, 1: 100},
    "tune_active":  {0: False, 1: False},
    "timestamp":    0,
    "ampd_connected": False,   # TCP-länk till spe_ampd upprättad
    "amp_on":       False,     # daemonens serieport öppen (DTR hög, slutsteget på)
    "active_radio": None,
}

tci_cmd_queue  = queue.Queue()  # kommandon att skicka via TCI-anslutningen
ampd_cmd_queue = queue.Queue()  # kommandon att skicka till spe_ampd (amp_on/amp_off/key)

def send_tci_cmd(cmd):
    tci_cmd_queue.put(cmd)

stop_requested = threading.Event()

def request_stop():
    stop_requested.set()

# ──────────────────────────────────────────────────────────
# CORE – AMPD COMMANDS (skickas över nätverk till spe_ampd,
# som äger den fysiska serieporten mot slutsteget)
# ──────────────────────────────────────────────────────────

def send_key(keycode):
    ampd_cmd_queue.put({"cmd": "key", "code": keycode})

def amp_on_cmd():  ampd_cmd_queue.put({"cmd": "amp_on"})
def amp_off_cmd(): ampd_cmd_queue.put({"cmd": "amp_off"})

def toggle_operate():    send_key(0x1C)
def next_antenna():      send_key(0x2B)
def toggle_power_mode(): send_key(0x1A)
def send_tune():         send_key(0x34)

# ──────────────────────────────────────────────────────────
# CORE – TCI
# Läser alltid från latest["active_radio"] så att URL:en
# uppdateras när aktiv ingång byter radio.
# ──────────────────────────────────────────────────────────

def _tci_url():
    radio = latest.get("active_radio")
    if not radio:
        radios = cfg.get("radios", [])
        radio  = radios[0] if radios else {}
    return f"ws://{radio.get('tci_host','127.0.0.1')}:{radio.get('tci_port', 50001)}"

def set_freq(ws, freq, trx):       ws.send(f"vfo:{trx},0,{freq};")
def set_tx(ws, on, trx):           ws.send(f"tune:{trx},true;" if on else f"tune:{trx},false;")
def ensure_tx_ready(ws, trx):      ws.send(f"modulation:{trx},CW;")

# ──────────────────────────────────────────────────────────
# CORE – BACKGROUND LOOPS
# ──────────────────────────────────────────────────────────

def ampd_listener_loop(callback, running):
    """Beständig TCP/JSON-anslutning mot spe_ampd (Pi:n som äger serieporten
    mot slutsteget). Mirrorspegel av tci_listener_loop: reconnect var 3:e s,
    heartbeat var 1:a s (håller daemonens failsafe-vakthund nöjd), och
    dränerar ampd_cmd_queue för utgående kommandon (amp_on/amp_off/key)."""
    buf = b""
    while running["run"]:
        sock = None
        try:
            sock = socket.create_connection((cfg["ampd_host"], cfg["ampd_port"]), timeout=5)
            sock.settimeout(1.0)
            latest["ampd_connected"] = True
            callback("ampd_status", "connected")
            last_heartbeat = 0.0

            while running["run"]:
                now = time.time()
                if now - last_heartbeat >= 1.0:
                    try:
                        sock.sendall((json.dumps({"cmd": "heartbeat"}) + "\n").encode("utf-8"))
                    except OSError:
                        break
                    last_heartbeat = now

                while not ampd_cmd_queue.empty():
                    try:
                        cmd = ampd_cmd_queue.get_nowait()
                        sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
                    except OSError:
                        break
                    except queue.Empty:
                        break

                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                except socket.timeout:
                    continue

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    _handle_ampd_message(msg, callback)

        except Exception as e:
            callback("log", f"AMPD error: {type(e).__name__}: {e}")
        finally:
            if sock:
                try: sock.close()
                except: pass
            buf = b""
            latest["ampd_connected"] = False
            latest["amp_on"]         = False
            latest["flags"]          = None

        callback("ampd_status", "disconnected")
        callback("serial_status", "disconnected")
        if running["run"]:
            time.sleep(3)

def _handle_ampd_message(msg, callback):
    t = msg.get("type")
    if t == "telemetry":
        flags = {"tx": msg["tx"], "op": msg["op"], "tune": msg["tune"], "alarm": msg["alarm"]}
        latest["flags"]     = flags
        latest["power"]     = msg.get("power")
        latest["timestamp"] = time.time()

        inp   = msg.get("input")
        radio = find_radio(inp) if inp is not None else None
        latest["active_radio"] = radio

        data = dict(msg)
        data.pop("type", None)
        data["radio"] = radio
        callback("telemetry", data)

    elif t == "serial_status":
        callback("serial_status", msg.get("value"))

    elif t == "amp_status":
        latest["amp_on"] = (msg.get("value") == "on")

    elif t == "log":
        callback("log", msg.get("msg", ""))

def _active_tx_freq():
    """Returnerar frekvensen för det TRX som för tillfället sänder."""
    trx = latest["tx_trx"]
    if trx == 1:
        return latest["freq_rx1"] or latest["freq_rx0"]
    return latest["freq_rx0"] or latest["freq_rx1"]

def tci_listener_loop(callback, running):
    while running["run"]:
        url = _tci_url()
        try:
            ws = websocket.create_connection(url, timeout=5)
            ws.settimeout(0.15)
            callback("tci_status", "connected")
            ws.send("vfo:0,0;")
            ws.send("vfo:1,0;")
            ws.send("modulation:0;")
            ws.send("modulation:1;")
            ws.send("drive:0;")
            ws.send("drive:1;")
            ws.send("tune_drive:0;")
            ws.send("tune_drive:1;")
            ws.send("tx_sensors_enable:true,100;")

            _keepalive_ticks = 0
            while running["run"]:
                # Byt anslutning om aktiv radio ändrats
                if _tci_url() != url:
                    break

                try:
                    msg = ws.recv()
                    _keepalive_ticks = 0
                except (websocket.WebSocketTimeoutException, socket.timeout):
                    # Töm utgående kö även om inga inkommande meddelanden
                    while not tci_cmd_queue.empty():
                        try:
                            ws.send(tci_cmd_queue.get_nowait())
                        except Exception:
                            pass
                    _keepalive_ticks += 1
                    if _keepalive_ticks >= 200:   # ~30 s
                        ws.send("vfo:0,0;")
                        ws.send("vfo:1,0;")
                        _keepalive_ticks = 0
                    continue

                if msg.startswith("vfo:0,0,"):
                    try:
                        latest["freq_rx0"] = int(msg.rstrip(";").split(",")[2])
                        latest["freq"] = _active_tx_freq()
                        callback("radio_freq", latest["freq"])
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("vfo:1,0,"):
                    try:
                        latest["freq_rx1"] = int(msg.rstrip(";").split(",")[2])
                        latest["freq"] = _active_tx_freq()
                        callback("radio_freq", latest["freq"])
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("trx:"):
                    # trx:N,true/false — faktisk TX-status per TRX
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx = int(parts[0].split(":")[1])
                        active = parts[1].strip().lower() == "true"
                        latest["trx_active"][trx] = active
                        if active:
                            latest["tx_trx"] = trx
                            latest["freq"] = _active_tx_freq()
                            callback("radio_freq", latest["freq"])
                        callback("log", f"trx:{trx},{str(active).lower()} → TX RX{latest['tx_trx']+1}")
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("tx_frequency:"):
                    try:
                        freq = int(msg.rstrip(";").split(":")[1])
                        latest["freq"] = freq
                        callback("radio_freq", freq)
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("modulation:"):
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx  = int(parts[0].split(":")[1])
                        mode = parts[1].strip().upper()
                        latest["modulation"][trx] = mode
                        callback("modulation", (trx, mode))
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("drive:"):
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx  = int(parts[0].split(":")[1])
                        val  = int(parts[1])
                        latest["drive"][trx] = val
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("tune_drive:"):
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx  = int(parts[0].split(":")[1])
                        val  = int(parts[1])
                        latest["tune_drive"][trx] = val
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("tx_sensors:"):
                    # tx_sensors:N,mic,power_rms,power_peak,swr
                    # Pålitlig detektering av aktivt TX-TRX — fungerar för både macro och keyer
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx   = int(parts[0].split(":")[1])
                        power = float(parts[3])   # power_peak W
                        if power > 1.0 and trx != latest["tx_trx"]:
                            latest["tx_trx"] = trx
                            latest["freq"] = _active_tx_freq()
                            callback("radio_freq", latest["freq"])
                            callback("log", f"tx_sensors:{trx} power={power:.0f}W → TX RX{trx+1}")
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("tune:"):
                    try:
                        parts = msg.rstrip(";").split(",")
                        trx    = int(parts[0].split(":")[1])
                        active = parts[1].strip().lower() == "true"
                        latest["tune_active"][trx] = active
                        if active and trx != latest["tx_trx"]:
                            latest["tx_trx"] = trx
                            latest["freq"] = _active_tx_freq()
                            callback("radio_freq", latest["freq"])
                            callback("log", f"tune:{trx},true → TX RX{trx+1}")
                    except (ValueError, IndexError):
                        pass

                # Skicka väntande utgående kommandon
                while not tci_cmd_queue.empty():
                    try:
                        ws.send(tci_cmd_queue.get_nowait())
                    except Exception:
                        pass

        except Exception as e:
            callback("log", f"TCI error: {type(e).__name__}: {e}")

        latest["freq"] = None
        latest["freq_rx0"] = None
        latest["freq_rx1"] = None
        latest["trx_active"] = {0: False, 1: False}
        callback("tci_status", "disconnected")
        callback("radio_freq", 0)
        if running["run"]:
            time.sleep(3)

def start_ampd(callback):
    running = {"run": True}
    threading.Thread(target=ampd_listener_loop, args=(callback, running), daemon=True).start()
    return running

def stop_ampd(running):
    running["run"] = False

def start_tci(callback):
    running = {"run": True}
    threading.Thread(target=tci_listener_loop, args=(callback, running), daemon=True).start()
    return running

# ──────────────────────────────────────────────────────────
# CORE – TUNE HELPERS
# ──────────────────────────────────────────────────────────

def wait_for_tune(callback, timeout=10):
    start = time.time(); started = False
    while time.time() - start < timeout:
        if stop_requested.is_set():
            return False
        f = latest["flags"]
        if f:
            if f["tune"]:
                started = True
                callback("status", "TUNE START")
            if started and not f["tune"]:
                callback("status", "TUNE DONE")
                return True
        time.sleep(0.02)
    return False

def panic(ws, cb, msg, trx=0):
    cb("error", msg)
    try: set_tx(ws, False, trx)
    except: pass
    stop_requested.clear()
    cb("done", None)
    raise RuntimeError(msg)

def ensure_standby(ws, cb):
    for attempt in range(3):
        f = latest["flags"]
        if f and not f["op"]:
            return
        cb("status", f"STANDBY: toggle attempt {attempt+1}/3")
        toggle_operate()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            f = latest["flags"]
            if f and not f["op"]:
                cb("status", "STANDBY: OK")
                return
            time.sleep(0.05)
    panic(ws, cb, "STANDBY FAIL: amplifier did not enter standby after 3 attempts")

def validate_rf(ws, cb):
    deadline = time.time() + 3.0
    while time.time() < deadline:
        p = latest["power"]
        if p is not None and p > 0.0:
            if p > 15.0: panic(ws, cb, f"RF FAIL: power too high ({p:.1f} W, max 15 W)")
            if p < 2.0:  panic(ws, cb, f"RF FAIL: power too low ({p:.1f} W, min 2 W)")
            cb("status", f"RF OK: {p:.1f} W")
            return
        time.sleep(0.02)
    panic(ws, cb, "RF FAIL: no power reading within 3 seconds")

# ──────────────────────────────────────────────────────────
# CORE – TUNE OPERATIONS
# ──────────────────────────────────────────────────────────

def manual_tune(callback):
    stop_requested.clear()
    if not latest["amp_on"]:
        callback("error", "MANUAL TUNE FAIL: no serial connection")
        callback("done", None)
        return
    trx  = latest["tx_trx"]
    freq = latest["freq_rx1"] if trx == 1 else latest["freq_rx0"]
    callback("log", f"TUNE START: tx_trx={trx} freq_rx0={latest['freq_rx0']} freq_rx1={latest['freq_rx1']} → använder RX{trx+1} {freq}")
    if not freq:
        callback("error", "MANUAL TUNE FAIL: no frequency from radio")
        callback("done", None)
        return

    ws = websocket.create_connection(_tci_url(), timeout=5)
    try:
        callback("status", f"MANUAL TUNE RX{trx+1}: {freq/1_000_000:.3f} MHz")
        set_freq(ws, freq, trx)
        time.sleep(FREQ_SETTLE)
        ensure_standby(ws, callback)
        if not stop_requested.is_set():
            ensure_tx_ready(ws, trx)
            set_tx(ws, True, trx)
            validate_rf(ws, callback)
            send_tune()
            if not wait_for_tune(callback):
                if not stop_requested.is_set():
                    panic(ws, callback, "TUNE FAIL: no tune cycle within timeout", trx)
            set_tx(ws, False, trx)
        else:
            callback("log", "Manual tune stopped")
    finally:
        try: set_tx(ws, False, trx)
        except: pass
        ws.close()

    stop_requested.clear()
    callback("done", None)

def run_tune(band, callback):
    stop_requested.clear()
    if not latest["amp_on"]:
        callback("error", "SWEEP FAIL: no serial connection")
        callback("done", None)
        return
    if band not in BANDS:
        callback("error", "Bad band")
        callback("done", None)
        return

    trx = latest["tx_trx"]
    restore_freq = latest["freq_rx1"] if trx == 1 else latest["freq_rx0"]
    start_f, end_f = BANDS[band]
    ws = websocket.create_connection(_tci_url(), timeout=5)
    try:
        f = start_f + 1000   # börja 1 kHz in i bandet
        while f <= end_f:
            if stop_requested.is_set():
                callback("log", "Sweep stopped"); break
            callback("status", f"Sweep RX{trx+1} {f/1_000_000:.3f} MHz")
            set_freq(ws, f, trx)
            time.sleep(FREQ_SETTLE)
            ensure_standby(ws, callback)
            if stop_requested.is_set():
                callback("log", "Sweep stopped"); break
            ensure_tx_ready(ws, trx)
            set_tx(ws, True, trx)
            validate_rf(ws, callback)
            send_tune()
            if not wait_for_tune(callback):
                if stop_requested.is_set():
                    set_tx(ws, False, trx)
                    callback("log", "Sweep stopped"); break
                panic(ws, callback, "TUNE FAIL: no tune cycle within timeout", trx)
            set_tx(ws, False, trx)
            time.sleep(PAUSE_STEP)
            f += SWEEP_STEP
        if not stop_requested.is_set():
            callback("status", "DONE")
    finally:
        try: set_tx(ws, False, trx)
        except: pass
        if restore_freq:
            try: set_freq(ws, restore_freq, trx)
            except: pass
        ws.close()

    stop_requested.clear()
    callback("done", None)

# ──────────────────────────────────────────────────────────
# WIDGET – METER  (CTk-kort med rundad progressbar)
# ──────────────────────────────────────────────────────────

class Meter(ctk.CTkFrame):
    BAR_W = 64

    def __init__(self, parent, label, unit, max_val, thresholds, fonts):
        super().__init__(parent, fg_color=PANEL, corner_radius=RADIUS,
                         border_width=1, border_color=BORDER)
        self._max = max_val; self._thresholds = thresholds; self._unit = unit
        self._hdr = ctk.CTkLabel(self, text=label, text_color=MUTED,
                                 font=fonts["small"])
        self._hdr.pack(anchor="w", padx=12, pady=(6, 0))
        self.val_lbl = ctk.CTkLabel(self, text=f"--- {unit}", text_color=TEXT,
                                    font=fonts["val"], anchor="w")
        self.val_lbl.pack(anchor="w", padx=12)
        self.bar = ctk.CTkProgressBar(self, width=self.BAR_W, height=6,
                                      corner_radius=RADIUS, progress_color=GREEN,
                                      fg_color=CTRL)
        self.bar.set(0)
        self.bar.pack(anchor="w", padx=12, pady=(4, 10))

    def set(self, value, text):
        pct = min(max(value/self._max, 0.0), 1.0)
        col = GREEN
        for thresh, c in self._thresholds:
            if value >= thresh: col = c
        self.val_lbl.configure(text=text, text_color=col)
        self.bar.configure(progress_color=col)
        self.bar.set(pct)

    def clear(self):
        self.val_lbl.configure(text="---", text_color=TEXT)
        self.bar.set(0)

    def highlight(self, on):
        self.configure(fg_color=TXTINT if on else PANEL,
                       border_color=RED if on else BORDER)
        self._hdr.configure(fg_color="transparent")
        self.val_lbl.configure(text_color=WHITE if on else TEXT)


# ──────────────────────────────────────────────────────────
# WIDGET – COLLAPSIBLE SECTION  (för loggen)
# ──────────────────────────────────────────────────────────

class CollapsibleSection(ctk.CTkFrame):
    def __init__(self, parent, title, font, refit=None):
        super().__init__(parent, fg_color="transparent")
        self._title = title; self._open = False; self._refit = refit
        self._btn = ctk.CTkButton(self, text=f"▶  {title}", anchor="w",
                                  fg_color="transparent", hover_color=BTNHOV,
                                  text_color=MUTED, font=font,
                                  height=20, corner_radius=RADIUS,
                                  command=self._toggle)
        self._btn.pack(fill="x", padx=6, pady=(2, 2))
        self.body = ctk.CTkFrame(self, fg_color="transparent")

    def _toggle(self):
        if self._open:
            self.body.pack_forget(); self._open = False
            self._btn.configure(text=f"▶  {self._title}", text_color=MUTED)
        else:
            self.body.pack(fill="both", expand=True); self._open = True
            self._btn.configure(text=f"▼  {self._title}", text_color=TEXT)
        if self._refit:
            self.after(10, self._refit)

# ──────────────────────────────────────────────────────────
# SETTINGS DIALOG
# ──────────────────────────────────────────────────────────

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Settings")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.after(200, self.grab_set)   # CTkToplevel: vänta tills fönstret ritats

        self._f_lbl = th.font("label")
        self._f_hdr = th.font("small", bold=True)
        self._f_ent = th.font("body")

        self._vars   = {}
        self._rvars  = [[], []]   # radio vars per index

        # ── SPE Expert – ampd-daemon (Pi:n som äger serieporten) ──
        self._section_label("SPE EXPERT AMPLIFIER (spe_ampd)")
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=18, pady=4)
        ampd_fields = [("Host", "ampd_host", str), ("Port", "ampd_port", int)]
        for row, (label, key, typ) in enumerate(ampd_fields):
            ctk.CTkLabel(form, text=label, text_color=MUTED, font=self._f_lbl,
                         anchor="w").grid(row=row, column=0, sticky="w", pady=3, padx=(0, 14))
            var = tk.StringVar(value=str(cfg[key]))
            self._vars[key] = (var, typ)
            ctk.CTkEntry(form, textvariable=var, font=self._f_ent, width=180,
                         fg_color=CTRL, border_color=BORDER, text_color=TEXT,
                         corner_radius=RADIUS).grid(row=row, column=1, sticky="we", pady=3)

        # ── Radio 1 och 2 ────────────────────────────────────
        radios = cfg.get("radios", DEFAULT_CONFIG["radios"])
        while len(radios) < 2:
            radios.append(dict(DEFAULT_RADIO))

        radio_fields = [
            ("Name",     "name",     str),
            ("Input",    "input",    int),
            ("TCI Host", "tci_host", str),
            ("TCI Port", "tci_port", int),
        ]

        for ri in range(2):
            self._section_label(f"RADIO {ri+1}")
            rf = ctk.CTkFrame(self, fg_color="transparent")
            rf.pack(fill="x", padx=18, pady=4)
            rvars = {}
            for row, (label, key, typ) in enumerate(radio_fields):
                ctk.CTkLabel(rf, text=label, text_color=MUTED, font=self._f_lbl,
                             anchor="w").grid(row=row, column=0, sticky="w",
                                              pady=3, padx=(0, 14))
                val = radios[ri].get(key, DEFAULT_RADIO.get(key, ""))
                var = tk.StringVar(value=str(val))
                rvars[key] = (var, typ)
                ctk.CTkEntry(rf, textvariable=var, font=self._f_ent, width=180,
                             fg_color=CTRL, border_color=BORDER, text_color=TEXT,
                             corner_radius=RADIUS).grid(row=row, column=1,
                                                        sticky="we", pady=3)
            self._rvars[ri] = rvars

        # ── Knappar ──────────────────────────────────────────
        ctk.CTkLabel(self, text="AMPD changes apply on next reconnect.",
                     text_color=MUTED, font=self._f_hdr).pack(pady=(8, 0))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=10)
        ctk.CTkButton(btn_row, text="Cancel", command=self.destroy,
                      fg_color=BTNBG, hover_color=BTNHOV, text_color=BTNFG,
                      font=self._f_ent, corner_radius=RADIUS, width=90,
                      height=th.METRICS["btn_height"]).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_row, text="Save", command=self._save,
                      fg_color=BLUE, hover_color=BLUE_B, text_color=WHITE,
                      font=self._f_ent, corner_radius=RADIUS, width=90,
                      height=th.METRICS["btn_height"]).pack(side="right")

    def _section_label(self, text):
        ctk.CTkLabel(self, text=text, text_color=MUTED, font=th.font("small", bold=True),
                     anchor="w").pack(anchor="w", padx=18, pady=(10, 0))

    def _save(self):
        # AMPD host/port
        for key in ("ampd_host", "ampd_port"):
            var, typ = self._vars[key]
            try:
                cfg[key] = typ(var.get().strip())
            except (KeyError, ValueError):
                pass

        # Radios
        radios = []
        for rvars in self._rvars:
            r = {}
            for key, (var, typ) in rvars.items():
                try: r[key] = typ(var.get().strip())
                except ValueError: r[key] = var.get().strip()
            radios.append(r)
        cfg["radios"] = radios

        save_config(cfg)
        self.destroy()

# ──────────────────────────────────────────────────────────
# STÄNGNINGSDIALOG
# ──────────────────────────────────────────────────────────

class _CloseDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = "cancel"
        self.title("Avsluta")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.after(200, self.grab_set)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        ctk.CTkLabel(self, text="Stäng av slutsteget?", text_color=TEXT,
                     font=th.font("body", bold=True)).pack(padx=28, pady=(20, 8))

        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=20)

        ctk.CTkButton(f, text="Stäng steget och avsluta",
                      fg_color=STOPBG, hover_color=STOPBG, text_color=STOPFG,
                      font=th.font("body"), corner_radius=RADIUS, height=34,
                      command=lambda: self._set("yes")).pack(fill="x", pady=3)
        ctk.CTkButton(f, text="Avbryt — stanna kvar",
                      fg_color="transparent", hover_color=BORDER, text_color=MUTED,
                      font=th.font("body"), corner_radius=RADIUS, height=34,
                      command=self.destroy).pack(fill="x", pady=(3, 16))

        self.wait_window()

    def _set(self, result):
        self.result = result
        self.destroy()

# ──────────────────────────────────────────────────────────
# MAIN GUI
# ──────────────────────────────────────────────────────────

class TunerGUI:
    def __init__(self, root):
        self.root            = root
        self.queue           = queue.Queue()
        self.running         = False
        self._last_input     = None
        self._locked         = False
        self._swr_in_op      = False
        self._amp_connected  = False

        root.title("SM5K SPE Tuner Klient v1.0.0")
        root.configure(fg_color=BG)
        root.resizable(False, False)
        root.overrideredirect(True)     # frameless, som rotorn — dra var som helst, avsluta via högerklick

        # Typsnitt (ee_theme) — byggs efter att root finns
        self.fonts = {
            "freq":   th.font("freq_secondary", bold=True),
            "val":    th.font("freq_main", scale=0.72, bold=True),
            "head":   th.font("body", bold=True),
            "body":   th.font("body"),
            "label":  th.font("label"),
            "small":  th.font("small"),
            "btn":    th.font("small", bold=True),
        }

        self._tx_pending_since     = None
        self._tx_on                = False
        self._op_on                = False
        self._op_raw_prev          = None
        self._relay_settling_until = 0.0
        self._band_pending         = None
        self._alarm_pending_since  = None
        self._current_band         = None
        self._reg_last_adjust      = 0.0
        self._reg_stable_since     = None
        self._reg_ff_applied       = False
        self._reg_last_target      = None
        self._reg_phase2           = False   # True = underhållsfas (lång mätning)
        self._power_history        = deque(maxlen=10)   # fas 1: ~2s
        self._power_history_slow   = deque(maxlen=50)   # fas 2: ~10s
        self._power_display        = deque(maxlen=3)    # utjämning för display

        self._tci_running  = start_tci(self.callback)
        self._ampd_running = start_ampd(self.callback)
        self._build_ui()
        self._bind_drag_recursive(self.root)
        self._set_controls("disabled")
        root.update_idletasks()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self.process_queue)

    # ── Layout ──────────────────────────────────────────────

    def _build_ui(self):
        r  = self.root
        M  = th.METRICS
        F  = self.fonts
        GP = M["gap"] + 2

        def card(parent):
            return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS,
                                border_width=1, border_color=BORDER)

        # ── Huvudrad: liggande kort ──────────────────────────────
        main = ctk.CTkFrame(r, fg_color="transparent")
        main.pack(fill="x", padx=M["panel_pad"], pady=(M["panel_pad"], 0))
        self._meters_frame = main       # ankare för larm-/okänd-banner (pack after=)

        # Kort A — status / AMP -----------------------------------
        cA = card(main)
        cA.pack(side="left", fill="y", padx=(0, GP))
        topA = ctk.CTkFrame(cA, fg_color="transparent")
        topA.pack(fill="x", padx=8, pady=(8, 2))
        self.amp_btn = ctk.CTkButton(topA, text="AMP: OFF", width=76,
                                     height=M["btn_height"], corner_radius=RADIUS,
                                     fg_color=BTNBG, hover_color=BTNHOV, text_color=RED,
                                     font=F["btn"], command=self._toggle_amp)
        self.amp_btn.pack(side="left")
        cfg_btn = ctk.CTkButton(topA, text="⚙", width=28,
                                height=M["btn_height"], corner_radius=RADIUS,
                                fg_color=BTNBG, hover_color=BTNHOV, text_color=MUTED,
                                font=F["head"], command=lambda: SettingsDialog(self.root))
        cfg_btn.pack(side="left", padx=(4, 0))
        self.tci_lbl    = tk.Label(cA, text="TCI: ---",    bg=PANEL, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w")
        self.serial_lbl = tk.Label(cA, text="Serial: ---", bg=PANEL, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w")
        self.tci_lbl.pack(fill="x", padx=10, pady=(2, 0))
        self.serial_lbl.pack(fill="x", padx=10, pady=(0, 8))

        # Kort B/C — mätare ---------------------------------------
        self.power_meter = Meter(main, "POWER", "W", 150, [(60, AMBER), (100, RED)], F)
        self.power_meter.pack(side="left", fill="y", padx=(0, GP))
        self.swr_meter = Meter(main, "SWR", "", 3.0, [(1.5, AMBER), (2.5, RED)], F)
        self.swr_meter.pack(side="left", fill="y", padx=(0, GP))

        # Kort D — mode/ant/pwr + effektbörvärde ------------------
        cD = card(main)
        cD.pack(side="left", fill="y", padx=(0, GP))
        dbtn = dict(height=M["btn_height"], corner_radius=RADIUS, fg_color=BTNBG,
                    hover_color=BTNHOV, text_color=BTNFG, font=F["btn"])
        rowD1 = ctk.CTkFrame(cD, fg_color="transparent")
        rowD1.pack(fill="x", padx=8, pady=(8, 2))
        self.mode_btn = ctk.CTkButton(rowD1, text="Mode: ---", width=118,
                                      command=self.toggle_mode, **dbtn)
        self.mode_btn.pack(side="left")
        rowD2 = ctk.CTkFrame(cD, fg_color="transparent")
        rowD2.pack(fill="x", padx=8, pady=(0, 2))
        self.ant_btn = ctk.CTkButton(rowD2, text="ANT: ---", width=57,
                                     command=self.next_ant, **dbtn)
        self.ant_btn.pack(side="left")
        self.power_btn = ctk.CTkButton(rowD2, text="Pwr: ---", width=57,
                                       command=self.toggle_power, **dbtn)
        self.power_btn.pack(side="left", padx=(4, 0))
        rowD3 = ctk.CTkFrame(cD, fg_color="transparent")
        rowD3.pack(fill="x", padx=8, pady=(0, 8))
        self._reg_minus = ctk.CTkButton(rowD3, text="−", width=26,
                                        command=self._reg_decrease, **dbtn)
        self._reg_minus.pack(side="left")
        self._reg_lbl = tk.Label(rowD3, text="300W", bg=PANEL, fg=TEXT,
                                 font=("Segoe UI", 10, "bold"), width=6)
        self._reg_lbl.pack(side="left", padx=2)
        self._reg_plus = ctk.CTkButton(rowD3, text="+", width=26,
                                       command=self._reg_increase, **dbtn)
        self._reg_plus.pack(side="left")
        self._reg_btn = ctk.CTkButton(rowD3, text="●", width=26, height=M["btn_height"],
                                      corner_radius=RADIUS, fg_color=BTNBG,
                                      hover_color=BTNHOV, text_color=GREEN, font=F["btn"],
                                      command=self._reg_toggle)
        self._reg_btn.pack(side="left", padx=(6, 0))
        self._reg_active = True
        self._reg_update_label()

        # Kort E — tune + STOP ------------------------------------
        cE = card(main)
        cE.pack(side="left", fill="both", expand=True)
        rowE1 = ctk.CTkFrame(cE, fg_color="transparent")
        rowE1.pack(fill="x", padx=8, pady=(8, 2))
        self.tune_single_btn = ctk.CTkButton(rowE1, text="Tune, single", font=F["head"],
                                             width=108, fg_color=TUNEBG, hover_color=GREEN_B,
                                             text_color=TUNEFG, corner_radius=RADIUS,
                                             height=32, command=self._on_tune_single)
        self.tune_single_btn.pack(side="left", fill="x", expand=True)
        self.tune_sweep_btn = ctk.CTkButton(rowE1, text="Tune, sweep", font=F["head"],
                                            width=128, fg_color=TUNEBG, hover_color=GREEN_B,
                                            text_color=TUNEFG, corner_radius=RADIUS,
                                            height=32, command=self._on_tune_sweep)
        self.tune_sweep_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.stop_btn = ctk.CTkButton(cE, text="STOP", font=F["head"],
                                      fg_color=STOPBG, hover_color=RED, text_color=STOPFG,
                                      corner_radius=RADIUS, height=28, command=self.stop)
        self.stop_btn.pack(fill="x", padx=8, pady=(0, 8))

        self._ctrl_widgets = [self.mode_btn, self.ant_btn, self.power_btn,
                               self.tune_single_btn, self.tune_sweep_btn]

        # ── Larmbanners (dolda tills de behövs, packas after=main) ─
        self.alarm_lbl = tk.Label(r, text="", bg=RED, fg=WHITE,
                                   font=("Segoe UI", 11, "bold"), pady=6)
        self.other_radio_lbl = tk.Label(r, text="", bg=BORDER, fg=MUTED,
                                         font=("Segoe UI", 10), pady=5)

        # ── Inforad: TX / band / temp / radio / frekvens ─────────
        info = ctk.CTkFrame(r, fg_color=BG, corner_radius=0)
        info.pack(fill="x", padx=M["panel_pad"], pady=(4, 0))
        self.tx_lbl   = tk.Label(info, text="TX: OFF",   bg=BG, fg=MUTED, font=("Segoe UI", 10), width=8, anchor="center")
        self.band_lbl = tk.Label(info, text="Band: ---", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.temp_lbl = tk.Label(info, text="Temp: ---", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.radio_lbl = tk.Label(info, text="",         bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="e")
        self.freq_lbl = tk.Label(info, text="",          bg=BG, fg=TEXT,  font=("Consolas", 10), cursor="hand2")
        self.tx_lbl.pack(side="left", padx=(2, 14))
        self.band_lbl.pack(side="left", padx=(0, 14))
        self.temp_lbl.pack(side="left")
        self.freq_lbl.pack(side="right", padx=(0, 4))
        self.radio_lbl.pack(side="right", padx=(0, 14))
        self.freq_lbl.bind("<Button-1>", lambda e: self._toggle_rx())

        # ── Fällbar logg ─────────────────────────────────────────
        self._log_sect = CollapsibleSection(r, "LOG", F["btn"], refit=self._fit_window)
        self._log_sect.pack(fill="both", expand=True, padx=M["panel_pad"],
                            pady=(2, M["panel_pad"]))
        self.log = tk.Text(self._log_sect.body, height=8, bg=PANEL, fg=TEXT,
                           insertbackground=TEXT, font=("Consolas", 9), relief="flat",
                           padx=8, pady=6, borderwidth=0, highlightthickness=0)
        self.log.pack(fill="both", expand=True, padx=2, pady=(0, 2))

    def _fit_window(self):
        # Låt fönstret krympa/växa till innehållets begärda storlek (loggen fälls ut/in).
        self.root.geometry("")

    # ── Frameless: dra + högerklicksmeny ─────────────────────
    def _drag_start(self, e):
        self._drag_off = (e.x_root - self.root.winfo_x(),
                          e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        ox, oy = self._drag_off
        self.root.geometry(f"+{e.x_root - ox}+{e.y_root - oy}")

    def _show_menu(self, e):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT,
                    activebackground=BORDER, activeforeground=TEXT, bd=0)
        m.add_command(label="Settings…", command=lambda: SettingsDialog(self.root))
        m.add_separator()
        m.add_command(label="Avsluta", command=self._on_close)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _bind_drag_recursive(self, w):
        # Gör hela stripan flyttbar och högerklickbar. Interaktiva widgetar (knappar,
        # entries, loggen, klickbar frekvens) hoppas över så de behåller sin funktion.
        skip = (ctk.CTkButton, ctk.CTkEntry, ctk.CTkTextbox, ctk.CTkSlider, ctk.CTkCheckBox)
        if isinstance(w, skip) or w in (self.freq_lbl, self.log):
            return
        try:
            w.bind("<ButtonPress-1>", self._drag_start, add="+")
            w.bind("<B1-Motion>", self._drag_move, add="+")
            w.bind("<Button-3>", self._show_menu, add="+")
        except tk.TclError:
            pass
        for c in w.winfo_children():
            self._bind_drag_recursive(c)

    # ── Kontrollhanterare ────────────────────────────────────

    def _on_sweep_toggle(self):
        if self._sweep_var.get():
            self._sweep_chk.config(text="Sweep ON",  fg=TUNEFG)
        else:
            self._sweep_chk.config(text="Sweep OFF", fg=MUTED)

    def _toggle_rx(self):
        latest["tx_trx"] = 1 - latest["tx_trx"]
        latest["freq"] = _active_tx_freq()
        self._log(f"TX RX manuellt → RX{latest['tx_trx']+1}")
        self.queue.put(("radio_freq", latest["freq"]))

    # ── Effektreglering ──────────────────────────────────────

    def _curve_interpolate(self, band, target_w):
        """Returnerar interpolerat drive-värde för target_w från kurvan, eller None."""
        pts = cfg["drive_curve"].get(band)
        if not pts or len(pts) < 2:
            return None
        pairs = sorted(pts.items())   # [(drive, power), ...]
        # Hitta omgivande punkter
        for i in range(len(pairs) - 1):
            d0, p0 = pairs[i]
            d1, p1 = pairs[i + 1]
            if p0 <= target_w <= p1 or p1 <= target_w <= p0:
                if p1 == p0:
                    return d0
                t = (target_w - p0) / (p1 - p0)
                return int(round(d0 + t * (d1 - d0)))
        # Utanför kurvan — extrapolera från närmaste ände
        d0, p0 = pairs[0];  d1, p1 = pairs[-1]
        if target_w < min(p0, p1):
            d, p = (d0, p0) if p0 < p1 else (d1, p1)
            return max(0, min(100, d - 2))
        else:
            d, p = (d1, p1) if p1 > p0 else (d0, p0)
            return max(0, min(100, d + 2))

    def _curve_add_point(self, band, drive, power_w):
        """Lägger till eller uppdaterar en kurv-punkt och sparar."""
        curve = cfg["drive_curve"].setdefault(band, {})
        # Slå ihop med befintlig punkt om drive är samma
        existing = curve.get(drive)
        if existing is not None:
            curve[drive] = round(0.7 * existing + 0.3 * power_w, 1)  # trög uppdatering
        else:
            curve[drive] = round(power_w, 1)
        save_config(cfg)

    def _reg_mode(self):
        """Returnerar aktuellt mode (CW/DIGU) eller None."""
        trx = latest["tx_trx"]
        m = latest["modulation"].get(trx)
        return m if m in ("CW", "DIGU") else None

    def _reg_update_label(self):
        mode = self._reg_mode()
        tp   = cfg["target_power"].get(mode, 300) if mode else "---"
        lbl  = f"{tp}W" if isinstance(tp, int) else tp
        self._reg_lbl.config(text=lbl)
        col = GREEN if (self._reg_active and mode) else MUTED
        self._reg_btn.configure(text_color=col)

    def _reg_apply_ff(self):
        """Skicka FF-drive direkt via TCI om kurvan har ett lärt värde för nuvarande band/target."""
        if not self._current_band:
            return
        mode = self._reg_mode()
        if not mode:
            return
        target = cfg["target_power"].get(mode)
        if not target:
            return
        ff_drive = self._curve_interpolate(self._current_band, target)
        if ff_drive is None:
            return
        trx = latest["tx_trx"]
        in_tune = latest["tune_active"].get(trx, False)
        if in_tune:
            current = latest["tune_drive"].get(trx, 100)
            if ff_drive != current:
                latest["tune_drive"][trx] = ff_drive
                send_tci_cmd(f"tune_drive:{trx},{ff_drive};")
        else:
            current = latest["drive"].get(trx, 100)
            if ff_drive != current:
                latest["drive"][trx] = ff_drive
                send_tci_cmd(f"drive:{trx},{ff_drive};")
        self._reg_ff_applied = False   # nollställ så regleringen börjar om vid nästa TX

    def _reg_increase(self):
        mode = self._reg_mode()
        if not mode:
            return
        tp   = cfg["target_power"][mode]
        step = 10 if tp < 100 else 100
        cfg["target_power"][mode] = min(tp + step, 1500)
        save_config(cfg)
        self._reg_update_label()
        self._reg_apply_ff()

    def _reg_decrease(self):
        mode = self._reg_mode()
        if not mode:
            return
        tp   = cfg["target_power"][mode]
        step = 10 if tp <= 100 else 100
        cfg["target_power"][mode] = max(tp - step, 10)
        save_config(cfg)
        self._reg_update_label()
        self._reg_apply_ff()

    def _reg_toggle(self):
        self._reg_active = not self._reg_active
        self._reg_update_label()
        state = "ON" if self._reg_active else "OFF"
        self._log(f"Effektreglering {state}")

    def _regulation_step(self, measured_w):
        """Justera drive/tune_drive en tick om reglering är aktiv och villkor uppfyllda."""
        trx   = latest["tx_trx"]
        mode  = latest["modulation"].get(trx)
        tx_on = self._tx_on

        if mode not in ("CW", "DIGU") or not self._reg_active or not self._op_on:
            return

        if not tx_on:
            self._power_history.clear()
            self._power_history_slow.clear()
            self._reg_ff_applied    = False
            self._reg_stable_since  = None
            self._reg_phase2        = False
            self._consec_transients = 0
            return

        if self._current_band is None:
            return

        target = cfg["target_power"].get(mode, 300)
        band   = self._current_band
        now    = time.monotonic()
        ts     = time.strftime("%H:%M:%S")
        rx_tag = f"RX{trx+1}"

        in_tune = latest["tune_active"].get(trx, False)
        if in_tune:
            current_drive = latest["tune_drive"].get(trx, 100)
            cmd_prefix    = f"tune_drive:{trx}"
        else:
            current_drive = latest["drive"].get(trx, 100)
            cmd_prefix    = f"drive:{trx}"

        def set_drive(new_drive):
            if in_tune: latest["tune_drive"][trx] = new_drive
            else:       latest["drive"][trx]      = new_drive
            send_tci_cmd(f"{cmd_prefix},{new_drive};")
            self._reg_last_adjust = now
            self._power_history.clear()
            self._power_history_slow.clear()

        # Nollställ om börvärdet ändrats under sändning
        if target != self._reg_last_target:
            self._reg_last_target   = target
            self._reg_ff_applied    = False
            self._reg_phase2        = False
            self._reg_stable_since  = None
            self._consec_transients = 0
            self._power_history.clear()
            self._power_history_slow.clear()

        # ── Feed-forward: hoppa till lärt drive-värde vid TX-start ──
        if not self._reg_ff_applied:
            self._reg_ff_applied = True
            ff_drive = self._curve_interpolate(band, target)
            if ff_drive is not None and ff_drive != current_drive:
                set_drive(ff_drive)
                _reg_logger.info(f"{ts},FF,{band},{mode},{target},{measured_w:.0f},,{current_drive},{ff_drive},feed-forward {rx_tag}")
            return  # vänta en cykel på att drive ska sätta sig

        # ── Klassificera mätpunkten ──────────────────────────────
        # min_w: dynamisk undregräns — filtrerar CW-element-glapp och kantprover.
        # target - 100W ger tillräcklig marginal utan att filtrera bort legitima mätvärden.
        min_w        = max(50, target - 100)
        is_transient = measured_w > target + 100
        is_valid     = min_w <= measured_w <= target + 100

        # Räkna på varandra följande transienter (utan giltigt värde emellan)
        if is_transient:
            self._consec_transients = getattr(self, "_consec_transients", 0) + 1
        else:
            self._consec_transients = 0

        # ── Överskyddsskydd: transient när TX är etablerad → minska direkt ──
        history_len = len(self._power_history) + len(self._power_history_slow)
        if is_transient and (history_len >= 3 or self._consec_transients >= 3):
            self._reg_phase2       = False
            self._reg_stable_since = None
            if now - self._reg_last_adjust >= 0.15:
                err  = measured_w - target
                step = 4 if err > 80 else 3 if err > 40 else 2
                new_drive = max(current_drive - step, 0)
                set_drive(new_drive)
                _reg_logger.info(f"{ts},SKYDD,{band},{mode},{target},{measured_w:.0f},,{current_drive},{new_drive},step={step} {rx_tag}")
            return

        if is_transient:
            # TX-start-transient — TX ännu inte stabiliserat, ignorera
            _reg_logger.info(f"{ts},TRANSIENT,{band},{mode},{target},{measured_w:.0f},,{current_drive},,TX-start")
            return

        # ── Fas 2: underhåll med lång mätning (~10s) ────────────
        if self._reg_phase2:
            if is_valid:
                self._power_history_slow.append(measured_w)
            n = len(self._power_history_slow)
            if n < 50:
                _reg_logger.info(f"{ts},FAS2,{band},{mode},{target},{measured_w:.0f},,{current_drive},,n={n}/50")
                return
            avg   = sum(self._power_history_slow) / n
            error = avg - target
            self._power_history_slow.clear()
            if abs(error) < 10:
                _reg_logger.info(f"{ts},FAS2-OK,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},,stabil")
                return
            # Drift utanför tolerans → 1-stegs korrigering, tillbaka till fas 1
            self._curve_add_point(band, current_drive, avg)
            self._reg_phase2       = False
            self._reg_stable_since = None
            step      = 1
            new_drive = max(current_drive - step, 0) if error > 0 else min(current_drive + step, 100)
            set_drive(new_drive)
            _reg_logger.info(f"{ts},FAS2-drift,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},{new_drive},→fas1")
            return

        # ── Fas 1: aktiv sökning med rullande medelvärde (~2s) ──
        if is_valid:
            self._power_history.append(measured_w)
        n = len(self._power_history)
        if n < 4:
            _reg_logger.info(f"{ts},FAS1,{band},{mode},{target},{measured_w:.0f},,{current_drive},,n={n}/4")
            return

        avg     = sum(self._power_history) / n
        error   = avg - target
        abs_err = abs(error)

        if abs_err < 10:
            if self._reg_stable_since is None:
                self._reg_stable_since = now
            if now - self._reg_stable_since >= 2.0:
                # Stabil i 2s → spara kurv-punkt och gå till fas 2
                self._curve_add_point(band, current_drive, avg)
                self._reg_phase2       = True
                self._reg_stable_since = None
                self._power_history_slow.clear()
                _reg_logger.info(f"{ts},FAS1→FAS2,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},,stabil 2s {rx_tag}")
            else:
                _reg_logger.info(f"{ts},FAS1-stabil,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},,inom 10W")
            return

        # Utanför tolerans → proportionell justering
        self._reg_stable_since = None
        if abs_err > 80:   step, interval = 4, 0.15
        elif abs_err > 40: step, interval = 3, 0.20
        elif abs_err > 20: step, interval = 2, 0.30
        else:              step, interval = 1, 0.50
        if now - self._reg_last_adjust < interval:
            _reg_logger.info(f"{ts},FAS1-väntar,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},,cooldown")
            return
        new_drive = max(current_drive - step, 0) if error > 0 else min(current_drive + step, 100)
        set_drive(new_drive)
        _reg_logger.info(f"{ts},FAS1-just,{band},{mode},{target},{measured_w:.0f},{avg:.0f},{current_drive},{new_drive},step={step} {rx_tag}")

    def _apply_op_state(self, op):
        if op != self._swr_in_op:
            self._swr_in_op = op
            if op:
                self.swr_meter._hdr.configure(text="REF POWER")
                self.swr_meter._max        = 200
                self.swr_meter._thresholds = [(60, AMBER), (150, RED)]
            else:
                self.swr_meter._hdr.configure(text="SWR")
                self.swr_meter._max        = 3.0
                self.swr_meter._thresholds = [(1.5, AMBER), (2.5, RED)]
            self.swr_meter.clear()
        self.mode_btn.configure(
            text="Mode: OPERATE" if op else "Mode: STANDBY",
            fg_color=AMBER if op else BTNBG,
            text_color=BG  if op else BTNFG,
            hover_color=AMBER_B if op else BTNHOV)

    def toggle_mode(self):
        toggle_operate()
        self._log("Toggle Mode")
        expected = not self._op_on
        self._op_on       = expected
        self._op_raw_prev = expected
        self._relay_settling_until = time.monotonic() + 0.5
        self._apply_op_state(expected)

    def next_ant(self):
        next_antenna()
        self._log("Next ANT")
        self._relay_settling_until = time.monotonic() + 0.5

    def toggle_power(self):  toggle_power_mode(); self._log("Toggle Power Mode")

    def _amp_connect(self):
        self._amp_connected = True
        self._last_input    = None
        amp_on_cmd()
        self.amp_btn.configure(text="AMP: ON", text_color=GREEN)
        if not self._locked:
            self._set_controls("normal")
        self._log("AMP ON")

    def _amp_disconnect(self):
        self._amp_connected = False
        amp_off_cmd()
        latest["flags"] = None
        self._op_on       = False
        self._op_raw_prev = None
        self.serial_lbl.config(text="Serial: ---", fg=MUTED)
        self.amp_btn.configure(text="AMP: OFF", text_color=RED)
        self.mode_btn.configure(text="Mode: ---", fg_color=BTNBG, text_color=BTNFG,
                                hover_color=BTNHOV)
        self._set_controls("disabled")
        self._log("AMP OFF")

    def _toggle_amp(self):
        if self._amp_connected:
            self._amp_disconnect()
        else:
            self._amp_connect()

    def _on_close(self):
        if self._amp_connected:
            dlg = _CloseDialog(self.root)
            if dlg.result == "cancel":
                return
            self._amp_disconnect()
            time.sleep(1.0)
        self._tci_running["run"]  = False
        self._ampd_running["run"] = False
        self.root.destroy()

    def _on_tune_single(self):
        if self.running: return
        self.running = True
        self._log("Manual Tune")
        threading.Thread(target=self._run_manual, daemon=True).start()

    def _on_tune_sweep(self):
        if self.running: return
        freq = latest.get("freq")
        band = freq_to_band(freq) if freq else None
        if not band:
            self._log("SWEEP FAIL: frekvens matchar inget band")
            return
        self.running = True
        self._log(f"Sweep {band.upper()}")
        threading.Thread(target=self._run_core, args=(band,), daemon=True).start()

    def _run_manual(self):
        try: manual_tune(self.callback)
        except Exception as e: self.queue.put(("error", str(e)))

    def _run_core(self, band):
        try: run_tune(band, self.callback)
        except Exception as e: self.queue.put(("error", str(e)))

    def stop(self):
        request_stop()
        self.queue.put(("log", "STOP requested"))

    # ── Ingångshantering ─────────────────────────────────────

    def _handle_input_change(self, inp, radio):
        if inp == self._last_input:
            return
        self._last_input = inp

        if radio:
            # Känd radio på den här ingången
            self.other_radio_lbl.pack_forget()
            self.radio_lbl.config(text=f"● {radio['name']}", fg=GREEN)
            self._set_controls("normal")
            self._locked = False
            self._log(f"Active: {radio['name']} (IN{inp})")
        else:
            # Okänd ingång — gråa ut
            self.other_radio_lbl.config(
                text=f"  Unknown source on IN{inp} — controls disabled  ")
            self.other_radio_lbl.pack(fill="x", after=self._meters_frame)
            self.radio_lbl.config(text=f"IN{inp}: unknown", fg=MUTED)
            self._set_controls("disabled")
            self._locked = True
            self._log(f"Unknown source on IN{inp} — controls locked")

    def _set_controls(self, state):
        for w in self._ctrl_widgets:
            w.configure(state=state)

    # ── Callback / kö ────────────────────────────────────────

    def callback(self, event, data): self.queue.put((event, data))
    def _log(self, msg): self.queue.put(("log", msg))

    def process_queue(self):
        try:
            while True:
                event, data = self.queue.get_nowait()

                if event == "status":
                    self.radio_lbl.config(text=data, fg=TEXT)
                    self.log.insert(tk.END, f"{data}\n")
                    self.log.see(tk.END)

                elif event == "error":
                    self.radio_lbl.config(text=f"ERR: {data}", fg=RED)
                    self.log.insert(tk.END, f"ERROR: {data}\n")
                    self.log.see(tk.END)

                elif event == "done":
                    self.running = False
                    if not self._locked:
                        self._set_controls("normal")
                    self.radio_lbl.config(
                        text=f"● {self._last_radio_name()}", fg=GREEN)

                elif event == "log":
                    ts = time.strftime("%H:%M:%S")
                    self.log.insert(tk.END, f"{ts} {data}\n")
                    self.log.see(tk.END)

                elif event == "radio_freq":
                    trx = latest["tx_trx"]
                    rx_label = f"RX{trx+1}"
                    display = f"{rx_label}  {data/1_000_000:.6f} MHz" if data else ""
                    self.freq_lbl.config(text=display)

                elif event == "modulation":
                    trx, mode = data
                    if trx == latest["tx_trx"]:
                        self._reg_update_label()

                elif event == "tci_status":
                    ok = data == "connected"
                    self.tci_lbl.config(text=f"TCI: {'OK' if ok else 'LOST'}",
                                        fg=GREEN if ok else RED)
                    self.log.insert(tk.END,
                        f"TCI: {'connected' if ok else 'lost'}\n")
                    self.log.see(tk.END)

                elif event == "serial_status":
                    ok = data == "connected"
                    self.serial_lbl.config(text=f"Serial: {'OK' if ok else 'LOST'}",
                                           fg=GREEN if ok else RED)
                    self.log.insert(tk.END,
                        f"Serial: {'connected' if ok else 'lost'}\n")
                    self.log.see(tk.END)

                elif event == "telemetry":
                    p = data.get("power")
                    if p is not None:
                        self._power_display.append(p)
                        p_avg = sum(self._power_display) / len(self._power_display)
                        self.power_meter.set(p_avg, f"{p_avg:.0f} W")
                        self._regulation_step(p)
                    else:
                        self._power_display.clear()
                        self.power_meter.clear()

                    # TX — 400 ms debounce mot falskt TX-flimmer
                    if "tx" in data:
                        raw_tx = data["tx"]
                        if raw_tx:
                            if self._tx_pending_since is None:
                                self._tx_pending_since = time.monotonic()
                            on = (time.monotonic() - self._tx_pending_since) >= 0.40
                        else:
                            self._tx_pending_since = None
                            on = False
                        self._tx_on = on
                        self.tx_lbl.config(
                            text="● TX ON" if on else "TX: OFF",
                            bg=RED if on else BG,
                            fg="white" if on else MUTED)
                        self.power_meter.highlight(on)
                        self.swr_meter.highlight(on)

                    # Band — 500 ms debounce
                    if "band" in data and data["band"]:
                        b = data["band"]
                        if self._band_pending is None or self._band_pending[0] != b:
                            self._band_pending = (b, time.monotonic())
                        elif time.monotonic() - self._band_pending[1] >= 0.50:
                            self._current_band = b
                            self.band_lbl.config(text=f"Band: {b}", fg=TEXT)

                    if "temp" in data and data["temp"] is not None:
                        t    = data["temp"]
                        unit = data.get("temp_unit", "°C")
                        t_c  = (t - 32) / 1.8 if unit == "°F" else t
                        tc   = RED if t_c >= 83 else (AMBER if t_c >= 70 else TEXT)
                        self.temp_lbl.config(text=f"Temp: {t}{unit}", fg=tc)

                    # OP — 2-pakets-konsensus + settling-fönster
                    if "op" in data:
                        raw_op = data["op"]
                        if time.monotonic() >= self._relay_settling_until:
                            if self._op_raw_prev is None:
                                if raw_op != self._op_on:
                                    self._op_on = raw_op
                                    self._apply_op_state(raw_op)
                            elif raw_op == self._op_raw_prev and raw_op != self._op_on:
                                self._op_on = raw_op
                                self._apply_op_state(raw_op)
                        self._op_raw_prev = raw_op
                    op = self._op_on

                    # SWR/REF POWER
                    if op:
                        rev = data.get("reflected", 0.0)
                        if self._tx_on and rev and rev > 0.0:
                            self.swr_meter.set(rev, f"{rev:.1f} W")
                        else:
                            self.swr_meter.clear()
                    else:
                        swr = data.get("swr")
                        if swr is not None:
                            self.swr_meter.set(swr, f"{swr:.2f}")
                        else:
                            self.swr_meter.clear()

                    # Alarm — 400 ms debounce
                    raw_alarm = data.get("alarm", False)
                    if raw_alarm:
                        if self._alarm_pending_since is None:
                            self._alarm_pending_since = time.monotonic()
                        alarm = (time.monotonic() - self._alarm_pending_since) >= 0.40
                    else:
                        self._alarm_pending_since = None
                        alarm = False
                    warning = data.get("warning")
                    if alarm:
                        msg = warning if warning else "ALARM"
                        self.alarm_lbl.config(text=f"  ⚠  {msg}  ⚠  ")
                        self.alarm_lbl.pack(fill="x", after=self._meters_frame)
                        self.log.insert(tk.END, f"ALARM: {msg}\n")
                        self.log.see(tk.END)
                    else:
                        self.alarm_lbl.pack_forget()

                    if "ant" in data and data["ant"] is not None:
                        self.ant_btn.configure(text=f"ANT: {data['ant']}")

                    if "power_mode" in data and data["power_mode"]:
                        self.power_btn.configure(text=f"Pwr: {data['power_mode']}")

                    if "input" in data:
                        self._handle_input_change(data["input"], data.get("radio"))

        except queue.Empty:
            pass

        self.root.after(100, self.process_queue)

    def _last_radio_name(self):
        radio = find_radio(self._last_input) if self._last_input else None
        return radio["name"] if radio else "---"


# ──────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    th.setup()
    root = ctk.CTk()
    app  = TunerGUI(root)
    root.mainloop()

# ===== END OF SCRIPT =====
