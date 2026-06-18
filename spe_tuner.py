## ==========================================================
## SM5K SPE Tuner v1.0.4
## SPE Expert 1K-FA controller – Single file | In-app settings
## Author: SM5K (SM5TOG)
## ==========================================================

import tkinter as tk
import threading
import queue
import time
import json
import os
import socket
import websocket
import serial

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────

DEFAULT_RADIO = {"name": "", "input": 1, "tci_host": "127.0.0.1", "tci_port": 50001}

DEFAULT_CONFIG = {
    "serial_port": "COM6",
    "radios": [
        {"name": "Radio 1", "input": 1, "tci_host": "127.0.0.1", "tci_port": 50001},
        {"name": "Radio 2", "input": 2, "tci_host": "127.0.0.1", "tci_port": 40001},
    ],
}

# Hårdkodade konstanter för SPE Expert-protokollet
BAUDRATE    = 9600    # Fast i slutsteget, ej valbar
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
    _cfg_path = os.path.join(os.path.dirname(_sys.executable), "settings.json")
else:
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

def load_config():
    try:
        with open(_cfg_path) as f:
            saved = json.load(f)
            result = dict(DEFAULT_CONFIG)
            if "serial_port" in saved:
                result["serial_port"] = saved["serial_port"]
            if "radios" in saved:
                result["radios"] = saved["radios"]
            return result
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(c):
    with open(_cfg_path, "w") as f:
        json.dump(c, f, indent=2)

cfg = load_config()

def find_radio(input_nr):
    """Returnerar radio-dict för given ingång, eller None om okänd."""
    for r in cfg.get("radios", []):
        if r.get("input") == input_nr and r.get("name", "").strip():
            return r
    return None

# ──────────────────────────────────────────────────────────
# PALETTE
# ──────────────────────────────────────────────────────────

BG      = "#16181d"
PANEL   = "#1e2028"
BORDER  = "#2a2d3a"
TEXT    = "#d4d8e8"
MUTED   = "#555a6e"
GREEN   = "#48c774"
RED     = "#e05252"
AMBER   = "#e8a030"
BTNBG   = "#2a2d3a"
BTNFG   = "#d4d8e8"
TUNEBG  = "#1a3d1a"
TUNEFG  = "#48c774"
STOPBG  = "#3d1a1a"
STOPFG  = "#e05252"

# ──────────────────────────────────────────────────────────
# CORE – STATE
# ──────────────────────────────────────────────────────────

WARNINGS = {
    0x11: "VOLT LOW (HALF)",   0x12: "VOLT LOW (FULL)",
    0x13: "VOLT HIGH (HALF)",  0x14: "VOLT HIGH (FULL)",
    0x15: "CURRENT HIGH (HALF)", 0x16: "CURRENT HIGH (FULL)",
    0x17: "OVERTEMP >90°C",    0x18: "DRIVE TOO HIGH",
    0x1B: "HIGH REVERSE POWER", 0x1C: "PA PROTECTION",
    0x1E: "SHUTDOWN",
}

latest = {
    "flags":        None,
    "power":        None,
    "freq":         None,   # aktiv TX-frekvens
    "freq_rx0":     None,   # VFO A på TRX 0
    "freq_rx1":     None,   # VFO A på TRX 1
    "tx_trx":       0,      # aktivt TX-TRX (0=RX1, 1=RX2)
    "trx_active":   {0: False, 1: False},  # vilka TRX som för tillfället sänder
    "timestamp":    0,
    "ser":          None,
    "active_radio": None,   # radio-dict för aktiv ingång
}

stop_requested = threading.Event()

def request_stop():
    stop_requested.set()

# ──────────────────────────────────────────────────────────
# CORE – SERIAL COMMANDS
# ──────────────────────────────────────────────────────────

def send_key(keycode):
    ser = latest["ser"]
    if ser is None:
        return
    pkt = [0x55, 0x55, 0x55, 0x02, 0x10, keycode, (0x10 + keycode) & 0xFF]
    ser.write(bytes(pkt))

def toggle_operate():    send_key(0x1C)
def next_antenna():      send_key(0x2B)
def toggle_power_mode(): send_key(0x1A)
def send_tune():         send_key(0x34)

# ──────────────────────────────────────────────────────────
# CORE – PACKET PARSING
# ──────────────────────────────────────────────────────────

def read_packet(ser, running, timeout=10.0):
    """Blockerande läsning – OS väcker tråden direkt när bytes anländer.
    Returnerar ett 35-byte paket eller None vid timeout/stopp."""
    buffer   = bytearray()
    deadline = time.time() + timeout

    while running["run"]:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None

        # Blockerande läsning av 1 byte, max 1 s per anrop
        # → reagerar omedelbart när data börjar strömma in
        ser.timeout = min(remaining, 1.0)
        b = ser.read(1)
        if not b:
            continue          # 1-sekunders chunk-timeout, kolla deadline

        buffer.append(b[0])
        deadline = time.time() + timeout   # återställ vid inkommande data

        # Töm resten av bufferten utan att vänta
        if ser.in_waiting:
            buffer.extend(ser.read(ser.in_waiting))

        # Extrahera giltigt paket
        while len(buffer) >= 35:
            if buffer[:3] == b"\xAA\xAA\xAA":
                pkt    = buffer[:35]
                buffer = buffer[35:]
                return pkt
            buffer.pop(0)

    return None

def get_flags(pkt):
    f = pkt[5]
    return {"tx": bool(f&4), "op": bool(f&2), "tune": bool(f&1), "alarm": bool(f&8)}

def get_power(pkt):      return ((pkt[27]<<8)|pkt[26]) / 10.0
def get_temp(pkt):       return pkt[25]
def get_antenna(pkt):    return (pkt[22] & 0x0F) + 1
def get_input(pkt):      return (pkt[18] & 0x0F) + 1
def get_power_mode(pkt): return "HIGH" if (pkt[5] & 0b10000) else "LOW"
def get_warning(pkt):    return WARNINGS.get(pkt[6], None)

def get_band(pkt):
    bands = ["160","80","40","30","20","17","15","12","10","6"]
    return bands[pkt[18] >> 4]

def get_swr(pkt):
    if get_flags(pkt)["op"]:
        return None
    val = (pkt[24]<<8) | pkt[23]
    return None if (val == 0 or val == 9999) else val / 100.0

def get_reflected_power(pkt):
    val = (pkt[29]<<8) | pkt[28]
    return val / 10.0   # Wpep; 0 when not transmitting

def get_temp_unit(pkt):
    """Returnerar '°C' eller '°F' beroende på FLAGS bit 7 (T_SCALE)."""
    return "°C" if (pkt[5] & 0b10000000) else "°F"

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

def telemetry_loop(callback, running):
    while running["run"]:
        ser = None
        try:
            ser = serial.Serial(cfg["serial_port"], BAUDRATE, timeout=1.0)
            latest["ser"] = ser
            ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x80, 0x80]))

            serial_ok = None  # None=okänd, True=OK, False=lost

            while running["run"]:
                pkt = read_packet(ser, running, timeout=10.0)

                if pkt is None:
                    # Timeout – porten hålls ÖPPEN (DTR förblir hög, slutsteget stannar på).
                    # Visa som lost och skicka om init, men stäng INTE porten.
                    if serial_ok is not False:
                        serial_ok = False
                        callback("serial_status", "disconnected")
                    try:
                        ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x80, 0x80]))
                    except Exception:
                        break  # Verkligt serial-fel – bryt och öppna om
                    continue

                if serial_ok is not True:
                    serial_ok = True
                    callback("serial_status", "connected")

                flags = get_flags(pkt)
                latest["flags"]     = flags
                latest["power"]     = get_power(pkt)
                latest["timestamp"] = time.time()

                inp   = get_input(pkt)
                radio = find_radio(inp)
                latest["active_radio"] = radio

                callback("telemetry", {
                    "power":      round(latest["power"], 1),
                    "tx":         flags["tx"],
                    "op":         flags["op"],
                    "tune":       flags["tune"],
                    "alarm":      flags["alarm"],
                    "warning":    get_warning(pkt),
                    "band":       get_band(pkt),
                    "ant":        get_antenna(pkt),
                    "power_mode": get_power_mode(pkt),
                    "temp":       get_temp(pkt),
                    "temp_unit":  get_temp_unit(pkt),
                    "swr":        get_swr(pkt),
                    "reflected":  get_reflected_power(pkt),
                    "input":      inp,
                    "radio":      radio,
                })

        except Exception as e:
            callback("log", f"Serial error: {type(e).__name__}: {e}")
        finally:
            latest["ser"]   = None
            latest["flags"] = None
            if ser:
                try: ser.close()
                except: pass

        callback("serial_status", "disconnected")
        if running["run"]:
            time.sleep(3)

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
            ws.settimeout(30)
            callback("tci_status", "connected")
            # Begär startfrekvens för båda TRX
            ws.send("vfo:0,0;")
            ws.send("vfo:1,0;")

            while running["run"]:
                # Byt anslutning om aktiv radio ändrats
                if _tci_url() != url:
                    break

                try:
                    msg = ws.recv()
                except (websocket.WebSocketTimeoutException, socket.timeout):
                    ws.send("vfo:0,0;")
                    ws.send("vfo:1,0;")
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
                        # Välj aktivt TX-TRX: lägst index som sänder, annars behåll
                        if latest["trx_active"][0]:
                            new_trx = 0
                        elif latest["trx_active"][1]:
                            new_trx = 1
                        else:
                            new_trx = latest["tx_trx"]  # ingen sänder — behåll
                        if new_trx != latest["tx_trx"]:
                            latest["tx_trx"] = new_trx
                            latest["freq"] = _active_tx_freq()
                            callback("radio_freq", latest["freq"])
                        callback("log", f"trx:{trx},{str(active).lower()} → TX RX{latest['tx_trx']+1}")
                    except (ValueError, IndexError):
                        pass

                elif msg.startswith("tx_frequency:"):
                    # Exakt TX-frekvens från ExpertSDR
                    try:
                        freq = int(msg.rstrip(";").split(":")[1])
                        latest["freq"] = freq
                        callback("radio_freq", freq)
                    except (ValueError, IndexError):
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

def start_serial(callback):
    running = {"run": True}
    threading.Thread(target=telemetry_loop, args=(callback, running), daemon=True).start()
    return running

def stop_serial(running):
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
    if latest["ser"] is None:
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
    if latest["ser"] is None:
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
# WIDGET – METER
# ──────────────────────────────────────────────────────────

class Meter(tk.Frame):
    BAR_W = 170
    BAR_H = 8

    def __init__(self, parent, label, unit, max_val, thresholds):
        super().__init__(parent, bg=PANEL, padx=16, pady=12)
        self._max = max_val; self._thresholds = thresholds
        self._hdr = tk.Label(self, text=label, bg=PANEL, fg=MUTED, font=("Consolas", 8))
        self._hdr.pack(anchor="w")
        self.val_lbl = tk.Label(self, text=f"--- {unit}", bg=PANEL, fg=TEXT,
                                 font=("Consolas", 28, "bold"), width=6, anchor="w")
        self.val_lbl.pack(anchor="w")
        self.cv   = tk.Canvas(self, bg=BORDER, width=self.BAR_W, height=self.BAR_H,
                               bd=0, highlightthickness=0)
        self.cv.pack(anchor="w", pady=(6, 0))
        self._bar = self.cv.create_rectangle(0, 0, 0, self.BAR_H, fill=GREEN, outline="")

    def set(self, value, text):
        pct = min(max(value/self._max, 0.0), 1.0)
        col = GREEN
        for thresh, c in self._thresholds:
            if value >= thresh: col = c
        self.val_lbl.config(text=text, fg=col)
        self.cv.itemconfig(self._bar, fill=col)
        self.cv.coords(self._bar, 0, 0, int(self.BAR_W*pct), self.BAR_H)

    def clear(self):
        self.val_lbl.config(text="---", fg=TEXT)
        self.cv.coords(self._bar, 0, 0, 0, self.BAR_H)

    def highlight(self, on):
        bg = "#2a1818" if on else PANEL
        self.config(bg=bg)
        for w in self.winfo_children():
            try: w.config(bg=bg)
            except tk.TclError: pass
        self.val_lbl.config(fg="white" if on else TEXT)

# ──────────────────────────────────────────────────────────
# WIDGET – COLLAPSIBLE SECTION
# ──────────────────────────────────────────────────────────

class CollapsibleSection(tk.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, bg=BG)
        self._title = title; self._open = False
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self._btn = tk.Button(self, text=f"▶  {title}", anchor="w",
                               bg=BG, fg=MUTED, activebackground=BORDER,
                               activeforeground=TEXT, relief="flat",
                               font=("Consolas", 9), cursor="hand2",
                               bd=0, command=self._toggle)
        self._btn.pack(fill="x", padx=10, pady=4)
        self.body = tk.Frame(self, bg=BG)

    def _toggle(self):
        if self._open:
            self.body.pack_forget(); self._open = False
            self._btn.config(text=f"▶  {self._title}", fg=MUTED)
        else:
            self.body.pack(fill="x"); self._open = True
            self._btn.config(text=f"▼  {self._title}", fg=TEXT)

# ──────────────────────────────────────────────────────────
# SETTINGS DIALOG
# ──────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        lbl_kw = dict(bg=BG, fg=MUTED, font=("Consolas", 9), anchor="w")
        ent_kw = dict(bg=PANEL, fg=TEXT, font=("Consolas", 10),
                      insertbackground=TEXT, relief="flat", width=20,
                      highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=AMBER)

        self._vars   = {}
        self._rvars  = [[], []]   # radio vars per index

        # ── SPE Expert – COM-port ────────────────────────────
        self._section_label("SPE EXPERT AMPLIFIER")
        form = tk.Frame(self, bg=BG, padx=18, pady=4)
        form.pack(fill="x")
        tk.Label(form, text="COM Port", **lbl_kw).grid(
            row=0, column=0, sticky="w", pady=3, padx=(0, 14))
        var = tk.StringVar(value=cfg["serial_port"])
        self._vars["serial_port"] = (var, str)
        tk.Entry(form, textvariable=var, **ent_kw).grid(
            row=0, column=1, sticky="we", pady=3)

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
            rf = tk.Frame(self, bg=BG, padx=18, pady=4)
            rf.pack(fill="x")
            rvars = {}
            for row, (label, key, typ) in enumerate(radio_fields):
                tk.Label(rf, text=label, **lbl_kw).grid(
                    row=row, column=0, sticky="w", pady=3, padx=(0, 14))
                val = radios[ri].get(key, DEFAULT_RADIO.get(key, ""))
                var = tk.StringVar(value=str(val))
                rvars[key] = (var, typ)
                tk.Entry(rf, textvariable=var, **ent_kw).grid(
                    row=row, column=1, sticky="we", pady=3)
            self._rvars[ri] = rvars

        # ── Knappar ──────────────────────────────────────────
        tk.Label(self, text="Serial changes apply on next reconnect.",
                 bg=BG, fg=MUTED, font=("Consolas", 8)).pack(pady=(6, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(6, 0))

        btn_row = tk.Frame(self, bg=BG, padx=16, pady=10)
        btn_row.pack(fill="x")
        bkw = dict(bg=BTNBG, fg=BTNFG, relief="flat", font=("Consolas", 10),
                   activebackground=BORDER, activeforeground=TEXT, padx=14, pady=5)
        tk.Button(btn_row, text="Cancel", command=self.destroy, **bkw).pack(side="right", padx=(6,0))
        tk.Button(btn_row, text="Save",   command=self._save,   **bkw).pack(side="right")

    def _section_label(self, text):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(8, 0))
        tk.Label(self, text=text, bg=BG, fg=MUTED,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=18, pady=(4, 0))

    def _save(self):
        # COM-port
        try:
            cfg["serial_port"] = self._vars["serial_port"][0].get().strip()
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

class _CloseDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = "cancel"
        self.title("Avsluta")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        tk.Label(self, text="Stäng av slutsteget?",
                 bg=BG, fg=TEXT, font=("Consolas", 11, "bold"),
                 pady=6).pack(padx=28, pady=(20, 4))

        bkw = dict(relief="flat", font=("Consolas", 10), padx=14, pady=8, cursor="hand2")
        f = tk.Frame(self, bg=BG, padx=20, pady=0)
        f.pack(fill="x")

        tk.Button(f, text="Stäng steget och avsluta",
                  bg=STOPBG, fg=STOPFG,
                  activebackground=STOPBG, activeforeground=STOPFG,
                  command=lambda: self._set("yes"), **bkw).pack(fill="x", pady=3)
        tk.Button(f, text="Avbryt — stanna kvar",
                  bg=BG, fg=MUTED,
                  activebackground=BORDER, activeforeground=TEXT,
                  command=self.destroy, **bkw).pack(fill="x", pady=(3, 16))

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
        self._serial_running = None

        root.title("SM5K SPE Tuner v1.0.4")
        root.configure(bg=BG)
        root.resizable(False, False)

        self._tx_pending_since     = None
        self._tx_on                = False
        self._op_on                = False
        self._op_raw_prev          = None
        self._relay_settling_until = 0.0
        self._band_pending         = None
        self._alarm_pending_since  = None

        self._tci_running = start_tci(self.callback)
        self._build_ui()
        self._set_controls("disabled")
        root.update_idletasks()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self.process_queue)

    # ── Layout ──────────────────────────────────────────────

    def _build_ui(self):
        r = self.root

        # Topbar
        bar = tk.Frame(r, bg=BORDER)
        bar.pack(fill="x")

        self.tci_lbl    = tk.Label(bar, text="TCI: ---",    bg=BORDER, fg=MUTED, font=("Consolas", 9), width=14)
        self.serial_lbl = tk.Label(bar, text="Serial: ---", bg=BORDER, fg=MUTED, font=("Consolas", 9), width=14)
        self.radio_lbl  = tk.Label(bar, text="",            bg=BORDER, fg=MUTED, font=("Consolas", 9), anchor="e", width=14)
        self.amp_btn = tk.Button(bar, text="AMP: OFF", bg=BORDER, fg=RED,
                                  activebackground=BORDER, activeforeground=TEXT,
                                  relief="flat", font=("Consolas", 9),
                                  width=8, bd=0,
                                  command=self._toggle_amp)
        cfg_btn = tk.Button(bar, text="⚙", bg=BORDER, fg=MUTED,
                             activebackground=BORDER, activeforeground=TEXT,
                             relief="flat", font=("Consolas", 12),
                             cursor="hand2", bd=0,
                             command=lambda: SettingsDialog(self.root))
        self.tci_lbl.pack(side="left", padx=8, pady=3)
        self.serial_lbl.pack(side="left", padx=4, pady=3)
        self.amp_btn.pack(side="left", padx=4, pady=3)
        cfg_btn.pack(side="right", padx=8, pady=2)
        self.radio_lbl.pack(side="right", padx=4, pady=3, fill="x", expand=True)

        # Mätare
        self._meters_frame = tk.Frame(r, bg=PANEL)
        self._meters_frame.pack(fill="x")

        self.power_meter = Meter(self._meters_frame, "POWER", "W",  150, [(60, AMBER),(100, RED)])
        self.swr_meter   = Meter(self._meters_frame, "SWR",   "",   3.0, [(1.5, AMBER),(2.5, RED)])
        self.power_meter.pack(side="left", expand=True, fill="both")
        tk.Frame(self._meters_frame, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)
        self.swr_meter.pack(side="left", expand=True, fill="both")

        # Larmbanners (dolda till de behövs)
        self.alarm_lbl = tk.Label(r, text="", bg=RED, fg="white",
                                   font=("Consolas", 11, "bold"), pady=6)
        self.other_radio_lbl = tk.Label(r, text="", bg=BORDER, fg=MUTED,
                                         font=("Consolas", 10), pady=5)

        # Sekundär info
        info = tk.Frame(r, bg=BG, padx=10, pady=6)
        info.pack(fill="x")
        self.tx_lbl   = tk.Label(info, text="TX: OFF",   bg=BG, fg=MUTED, font=("Consolas", 10), width=8, anchor="center")
        self.band_lbl = tk.Label(info, text="Band: ---", bg=BG, fg=MUTED, font=("Consolas", 10))
        self.temp_lbl = tk.Label(info, text="Temp: ---", bg=BG, fg=MUTED, font=("Consolas", 10))
        self.freq_lbl = tk.Label(info, text="",          bg=BG, fg=TEXT,  font=("Consolas", 9), cursor="hand2")
        self.tx_lbl.pack(side="left", padx=(0,14))
        self.band_lbl.pack(side="left", padx=(0,14))
        self.temp_lbl.pack(side="left")
        self.freq_lbl.pack(side="right")
        self.freq_lbl.bind("<Button-1>", lambda e: self._toggle_rx())

        # Kontrollknappar
        tk.Frame(r, bg=BORDER, height=1).pack(fill="x")
        ctrl = tk.Frame(r, bg=BG, padx=10, pady=8)
        ctrl.pack(fill="x")

        bkw = dict(bg=BTNBG, fg=BTNFG, relief="flat", font=("Consolas", 10),
                   activebackground=BORDER, activeforeground=TEXT, padx=8, pady=5)

        self.mode_btn  = tk.Button(ctrl, text="Mode: ---", width=14, command=self.toggle_mode,  **bkw)
        self.ant_btn   = tk.Button(ctrl, text="ANT: ---",  width=10, command=self.next_ant,     **bkw)
        self.power_btn = tk.Button(ctrl, text="Pwr: ---",  width=10, command=self.toggle_power, **bkw)
        self.mode_btn.grid( row=0, column=0, padx=(0,4), sticky="we")
        self.ant_btn.grid(  row=0, column=1, padx=4,     sticky="we")
        self.power_btn.grid(row=0, column=2, padx=(4,0), sticky="we")
        ctrl.columnconfigure((0,1,2), weight=1)

        tune_row = tk.Frame(ctrl, bg=BG)
        tune_row.grid(row=1, column=0, columnspan=3, sticky="we", pady=(8,0))
        tune_row.columnconfigure((0,1), weight=1)

        self.tune_single_btn = tk.Button(tune_row, text="Tune, single", font=("Consolas", 11, "bold"),
                                          bg=TUNEBG, fg=TUNEFG,
                                          activebackground=TUNEBG, activeforeground=TUNEFG,
                                          relief="flat", pady=12, command=self._on_tune_single)
        self.tune_single_btn.grid(row=0, column=0, sticky="we")

        self.tune_sweep_btn = tk.Button(tune_row, text="Tune, sweep", font=("Consolas", 11, "bold"),
                                         bg=TUNEBG, fg=TUNEFG,
                                         activebackground=TUNEBG, activeforeground=TUNEFG,
                                         relief="flat", pady=12, padx=4, command=self._on_tune_sweep)
        self.tune_sweep_btn.grid(row=0, column=1, sticky="we", padx=(4,0))

        self._ctrl_widgets = [self.mode_btn, self.ant_btn, self.power_btn,
                               self.tune_single_btn, self.tune_sweep_btn]

        # STOP
        stop_row = tk.Frame(r, bg=BG, padx=10)
        stop_row.pack(fill="x", pady=(0,8))
        self.stop_btn = tk.Button(stop_row, text="STOP", font=("Consolas", 11, "bold"),
                                   bg=STOPBG, fg=STOPFG,
                                   activebackground=STOPBG, activeforeground=STOPFG,
                                   relief="flat", pady=8, command=self.stop)
        self.stop_btn.pack(fill="x")

        # Fällbar: Logg
        self._log_sect = CollapsibleSection(r, "LOG")
        self._log_sect.pack(fill="x")
        self.log = tk.Text(self._log_sect.body, height=8, width=52,
                            bg=PANEL, fg=TEXT, insertbackground=TEXT,
                            font=("Consolas", 9), relief="flat", padx=8, pady=6)
        self.log.pack(fill="x", padx=6, pady=(0,6))

        tk.Frame(r, bg=BORDER, height=1).pack(fill="x")

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

    def _apply_op_state(self, op):
        if op != self._swr_in_op:
            self._swr_in_op = op
            if op:
                self.swr_meter._hdr.config(text="REF POWER")
                self.swr_meter._max        = 200
                self.swr_meter._thresholds = [(60, AMBER), (150, RED)]
            else:
                self.swr_meter._hdr.config(text="SWR")
                self.swr_meter._max        = 3.0
                self.swr_meter._thresholds = [(1.5, AMBER), (2.5, RED)]
            self.swr_meter.clear()
        self.mode_btn.config(
            text="Mode: OPERATE" if op else "Mode: STANDBY",
            bg=AMBER if op else BTNBG,
            fg=BG    if op else BTNFG,
            activebackground=AMBER  if op else BORDER,
            activeforeground=BG     if op else TEXT)

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
        self._amp_connected  = True
        self._last_input     = None
        self._serial_running = start_serial(self.callback)
        self.amp_btn.config(text="AMP: ON", fg=GREEN)
        if not self._locked:
            self._set_controls("normal")
        self._log("AMP ON")

    def _amp_disconnect(self):
        self._amp_connected = False
        if self._serial_running:
            stop_serial(self._serial_running)
            self._serial_running = None
        latest["flags"] = None
        self._op_on       = False
        self._op_raw_prev = None
        self.serial_lbl.config(text="Serial: ---", fg=MUTED)
        self.amp_btn.config(text="AMP: OFF", fg=RED)
        self.mode_btn.config(text="Mode: ---", bg=BTNBG, fg=BTNFG,
                             activebackground=BORDER, activeforeground=TEXT)
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
        self._tci_running["run"] = False
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
            w.config(state=state)

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
                        self.power_meter.set(p, f"{p:.1f} W")
                    else:
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
                        self.ant_btn.config(text=f"ANT: {data['ant']}")

                    if "power_mode" in data and data["power_mode"]:
                        self.power_btn.config(text=f"Power: {data['power_mode']}")

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
    root = tk.Tk()
    app  = TunerGUI(root)
    root.mainloop()

# ===== END OF SCRIPT =====
