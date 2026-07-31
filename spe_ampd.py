## ==========================================================
## spe_ampd — SPE Expert 1K-FA serial daemon
## Runs on the Pi physically wired to the amplifier (RS232/USB).
## Owns the serial port; GUI clients (spe_tuner.py) connect over
## TCP/JSON-lines to send commands and receive telemetry.
## ==========================================================

import json
import logging
import os
import queue
import socket
import socketserver
import sys
import threading
import time

import serial

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ampd_config.json")

DEFAULT_CONFIG = {
    "serial_port": "/dev/serial/by-id/usb-Prolific_Technology_Inc._ATEN_USB_to_Serial_Bridge_DKCDb121474-if00-port0",
    "listen_host": "0.0.0.0",
    "listen_port": 5560,
    "heartbeat_timeout": 5.0,   # sekunder utan livstecken från klient innan failsafe amp_off
}

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(saved)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)

cfg = load_config()

BAUDRATE = 9600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("spe_ampd")

# ──────────────────────────────────────────────────────────
# PACKET PARSING (identisk med spe_tuner.py:s telemetri-del)
# ──────────────────────────────────────────────────────────

WARNINGS = {
    0x11: "VOLT LOW (HALF)",   0x12: "VOLT LOW (FULL)",
    0x13: "VOLT HIGH (HALF)",  0x14: "VOLT HIGH (FULL)",
    0x15: "CURRENT HIGH (HALF)", 0x16: "CURRENT HIGH (FULL)",
    0x17: "OVERTEMP >90°C",    0x18: "DRIVE TOO HIGH",
    0x1B: "HIGH REVERSE POWER", 0x1C: "PA PROTECTION",
    0x1E: "SHUTDOWN",
}

def get_flags(pkt):
    f = pkt[5]
    return {"tx": bool(f & 4), "op": bool(f & 2), "tune": bool(f & 1), "alarm": bool(f & 8)}

def get_power(pkt):      return ((pkt[27] << 8) | pkt[26]) / 10.0
def get_temp(pkt):       return pkt[25]
def get_antenna(pkt):    return (pkt[22] & 0x0F) + 1
def get_input(pkt):      return (pkt[18] & 0x0F) + 1
def get_power_mode(pkt): return "HIGH" if (pkt[5] & 0b10000) else "LOW"
def get_warning(pkt):    return WARNINGS.get(pkt[6], None)

def get_band(pkt):
    bands = ["160", "80", "40", "30", "20", "17", "15", "12", "10", "6"]
    return bands[pkt[18] >> 4]

def get_swr(pkt):
    if get_flags(pkt)["op"]:
        return None
    val = (pkt[24] << 8) | pkt[23]
    return None if (val == 0 or val == 9999) else val / 100.0

def get_reflected_power(pkt):
    val = (pkt[29] << 8) | pkt[28]
    return val / 10.0

def get_temp_unit(pkt):
    return "°C" if (pkt[5] & 0b10000000) else "°F"

def read_packet(ser, running, timeout=10.0):
    buffer = bytearray()
    deadline = time.time() + timeout
    while running["run"]:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        ser.timeout = min(remaining, 1.0)
        b = ser.read(1)
        if not b:
            continue
        buffer.append(b[0])
        deadline = time.time() + timeout
        if ser.in_waiting:
            buffer.extend(ser.read(ser.in_waiting))
        while len(buffer) >= 35:
            if buffer[:3] == b"\xAA\xAA\xAA":
                pkt = buffer[:35]
                buffer = buffer[35:]
                return pkt
            buffer.pop(0)
    return None

# ──────────────────────────────────────────────────────────
# AMP STATE — en instans delad av alla klientanslutningar
# ──────────────────────────────────────────────────────────

class AmpState:
    def __init__(self):
        self.lock = threading.Lock()
        self.ser = None
        self.running = {"run": False}
        self.clients = set()          # aktiva ClientHandler-instanser
        self.clients_lock = threading.Lock()

    def broadcast(self, obj):
        line = (json.dumps(obj) + "\n").encode("utf-8")
        with self.clients_lock:
            dead = []
            for c in self.clients:
                try:
                    c.send_raw(line)
                except Exception:
                    dead.append(c)
            for c in dead:
                self.clients.discard(c)

    def amp_on(self):
        with self.lock:
            if self.ser is not None:
                return
            try:
                self.ser = serial.Serial(cfg["serial_port"], BAUDRATE, timeout=1.0)
            except Exception as e:
                log.error("amp_on: kunde inte öppna %s: %s", cfg["serial_port"], e)
                self.broadcast({"type": "log", "msg": f"Serial open failed: {e}"})
                self.ser = None
                return
            self.running = {"run": True}
            self.ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x80, 0x80]))
            threading.Thread(target=self._telemetry_loop, daemon=True).start()
            log.info("AMP ON (serial opened, DTR high)")
            self.broadcast({"type": "amp_status", "value": "on"})

    def amp_off(self):
        with self.lock:
            self.running["run"] = False
            ser = self.ser
            self.ser = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        log.info("AMP OFF (serial closed, DTR low)")
        self.broadcast({"type": "amp_status", "value": "off"})
        self.broadcast({"type": "serial_status", "value": "disconnected"})

    def send_key(self, keycode):
        with self.lock:
            ser = self.ser
        if ser is None:
            return
        pkt = [0x55, 0x55, 0x55, 0x02, 0x10, keycode, (0x10 + keycode) & 0xFF]
        try:
            ser.write(bytes(pkt))
        except Exception as e:
            log.warning("send_key(%s) failed: %s", keycode, e)

    def _telemetry_loop(self):
        ser = self.ser
        serial_ok = None
        while self.running["run"] and self.ser is ser:
            try:
                pkt = read_packet(ser, self.running, timeout=10.0)
            except Exception:
                # Porten stängdes (amp_off) medan en blockerande läsning pågick
                # i den här tråden — normalt vid avstängning, inte ett fel.
                break

            if pkt is None:
                if not self.running["run"] or self.ser is not ser:
                    break
                if serial_ok is not False:
                    serial_ok = False
                    self.broadcast({"type": "serial_status", "value": "disconnected"})
                try:
                    ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x80, 0x80]))
                except Exception:
                    break
                continue

            if serial_ok is not True:
                serial_ok = True
                self.broadcast({"type": "serial_status", "value": "connected"})

            flags = get_flags(pkt)
            self.broadcast({
                "type": "telemetry",
                "power":      round(get_power(pkt), 1),
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
                "input":      get_input(pkt),
            })

        if self.ser is ser:
            # Loopen dog av eget fel (inte pga en ny amp_on/amp_off) — städa upp.
            self.amp_off()

amp = AmpState()

# ──────────────────────────────────────────────────────────
# HEARTBEAT-FAILSAFE
# Ett 1 kW-slutsteg på obemannad plats får inte lämnas i OPERATE
# (eller ens ON) om styrlänken dör. Varje mottaget klientmeddelande
# räknas som livstecken. Ingen anslutning alls = ingen kan skicka
# heartbeat = amp_off så fort sista klienten kopplar från.
# ──────────────────────────────────────────────────────────

_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()

def note_heartbeat():
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()

def _watchdog_loop():
    while True:
        time.sleep(1.0)
        with _heartbeat_lock:
            age = time.time() - _last_heartbeat
        with amp.clients_lock:
            has_client = len(amp.clients) > 0
        if amp.ser is not None and (not has_client or age > cfg["heartbeat_timeout"]):
            log.warning("Heartbeat timeout (age=%.1fs, clients=%d) — failsafe AMP OFF",
                        age, len(amp.clients))
            amp.amp_off()

# ──────────────────────────────────────────────────────────
# TCP SERVER — JSON-lines protokoll
# ──────────────────────────────────────────────────────────

class ClientHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self._send_lock = threading.Lock()
        with amp.clients_lock:
            amp.clients.add(self)
        note_heartbeat()
        log.info("Client connected: %s", self.client_address)

    def send_raw(self, line_bytes):
        with self._send_lock:
            self.request.sendall(line_bytes)

    def handle(self):
        buf = b""
        self.request.settimeout(1.0)
        while True:
            try:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    note_heartbeat()
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                break

    def _dispatch(self, msg):
        cmd = msg.get("cmd")
        if cmd == "amp_on":
            amp.amp_on()
        elif cmd == "amp_off":
            amp.amp_off()
        elif cmd == "key":
            code = msg.get("code")
            if isinstance(code, int):
                amp.send_key(code)
        elif cmd == "heartbeat":
            pass  # note_heartbeat() redan kört i handle()

    def finish(self):
        with amp.clients_lock:
            amp.clients.discard(self)
        log.info("Client disconnected: %s", self.client_address)
        # Failsafe: ingen klient kvar → slå av omedelbart, vänta inte på watchdog-pollen.
        with amp.clients_lock:
            has_client = len(amp.clients) > 0
        if not has_client:
            amp.amp_off()

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    threading.Thread(target=_watchdog_loop, daemon=True).start()
    server = ThreadingTCPServer((cfg["listen_host"], cfg["listen_port"]), ClientHandler)
    log.info("spe_ampd listening on %s:%d (serial_port=%s)",
             cfg["listen_host"], cfg["listen_port"], cfg["serial_port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        amp.amp_off()

if __name__ == "__main__":
    main()
