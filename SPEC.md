# SM5K SPE Tuner — Komplett programspecifikation
*Används för att återskapa programmet från grunden.*

---

## ⚠ VIKTIGA FALLGROPAR — läs innan du ändrar något

### AMP ON/OFF
- Styrs via **DTR** (porten öppen/stängd), INTE via RTS eller keycode
- Arkitektur: `start_serial()` / `stop_serial()` — två separata funktioner
- TCI och serial är **separata trådar med separata `running`-flaggor**
- `_amp_connect()` startar serial, `_amp_disconnect()` stoppar den
- **Ändra inte detta till RTS-styrning** — det fungerar inte (steget startar ändå vid portöppning)

### OP/STANDBY-debounce — får INTE förenklas bort
- Steget skickar spuriösa paket vid reläbyte → utan debounce flimrar knappen
- Tre lager krävs alla tre:
  1. **Optimistisk uppdatering** i `toggle_mode()` — GUI svarar omedelbart
  2. **Settling-fönster** (`_relay_settling_until`) — ignorerar paket 500ms efter toggle/ant
  3. **2-pakets-konsensus** — accepterar bara ändring om två paket i rad är lika
- `_apply_op_state(op)` uppdaterar mode_btn + SWR-rubrik atomärt — använd alltid denna

### HIGH/LOW power-bit
- `pkt[5] & 0b10000` satt = **HIGH** (inte LOW som man kan tro)
- Verifierat mot hårdvara — ändra inte utan test

### tx_enable vs trx:
- `tx_enable:N,true/false` i TCI är **opålitlig** — ExpertSDR skickar den konstant för intern routing
- Använd **alltid `trx:N,true/false`** för TX-detektering
- Både true och false måste hanteras — annars fastnar `tx_trx` på fel RX

### PyInstaller cache
- `pyinstaller spec` utan `--clean` kan använda cachad EXE och INTE bygga om
- Använd alltid `--clean` när du behöver verifiera en kodändring

### Fönsterstorlek
- Använd `resizable(False, False)` — men sätt INTE `geometry()` explicit (blockerar log-expansion)
- Dynamiska widgets (status-text, radio-label) måste ha fast `width=` för att inte tvinga resize
- Meter-labels ska ha `width=6` för att hålla fast bredd vid olika värden

---

## Syfte
Windows-applikation (Python/tkinter, single-file EXE via PyInstaller) för daglig drift av
SPE Expert 1K-FA slutsteg tillsammans med ExpertSDR3. Ersätter SPE:s egna program för
all löpande körning — inget behov av att röra frontpanelen eller originalprogram under QSO.

---

## Kommunikation

### RS232 — SPE Expert 1K-FA
- Port: konfigureras i settings (standard COM3)
- Baudrate: 9600, timeout=1.0 s
- Porten hålls **alltid öppen när AMP är ON** — stängs aldrig pga timeout. DTR förblir hög.
- **AMP ON/OFF styrs via DTR**: porten öppen = DTR hög = steget på; porten stängd = DTR låg = steget av
- Init-paket vid öppning och re-init vid timeout: `[0x55, 0x55, 0x55, 0x01, 0x80, 0x80]`
- Telemetripaket: 35 bytes, börjar med `0xAA 0xAA 0xAA`
- Kommandon skickas som RS232-keycode-paket: `[0x55, 0x55, 0x55, 0x02, 0x10, keycode, checksum]`
  - checksum = `(0x10 + keycode) & 0xFF`
- Kommandon (keycode):
  - `toggle_operate()`: `0x1C`
  - `next_antenna()`: `0x2B`
  - `toggle_power_mode()`: `0x1A`
  - `send_tune()`: `0x34`

### TCI — ExpertSDR3
- WebSocket, port konfigureras i settings (standard 50001)
- Protokoll: text, gemener, semikolon-separerade meddelanden t.ex. `vfo:0,0,14000000;`
- Prenumererar på: `vfo:0,0`, `vfo:1,0`, `trx:`, `tx_frequency:`
- Skickar: `vfo:N,0,FREQ;`, `tune:N,true/false;`, `modulation:N,CW;`
- `trx:N,true/false` = faktisk TX-status per TRX (pålitlig — använd denna, INTE tx_enable)
- TCI startar alltid vid appstart (oberoende av AMP ON/OFF)

---

## Delad state (`latest`-dict, trådsäker via GIL)

```python
latest = {
    "flags":        None,       # dict: op, tx, tune, alarm
    "power":        None,       # float W
    "freq":         None,       # aktiv TX-frekvens (Hz)
    "freq_rx0":     None,       # VFO A på TRX 0 (RX1/vänster)
    "freq_rx1":     None,       # VFO A på TRX 1 (RX2/höger)
    "tx_trx":       0,          # aktivt TX-TRX (0=RX1, 1=RX2)
    "trx_active":   {0: False, 1: False},
    "timestamp":    0,
    "ser":          None,       # aktiv serial.Serial-instans
    "active_radio": None,
}
```

---

## Dual-RX TX-tracking
- `trx_active[N]` uppdateras för varje `trx:N,true/false`
- `tx_trx` = lägst index i `trx_active` som är True; om ingen sänder behålls senaste
- `_active_tx_freq()` returnerar rätt frekvens baserat på `tx_trx`
- **OBS**: ExpertSDR:s interna tuner på RX2 skickar `trx:1,true` — utan false-hantering
  fastnar tunern på RX2 även vid sändning på RX1. Bägge true/false måste hanteras.
- Vid TCI-disconnect: `trx_active` nollställs

---

## Trådar
- `telemetry_loop(callback, running)` — RS232, startas av `start_serial()`, daemon
- `tci_listener_loop(callback, running)` — WebSocket, startas av `start_tci()`, daemon
- `start_serial()` / `stop_serial(running)` — separata från TCI
- TCI startas vid app-init; serial startas bara när AMP: ON klickas
- `running = {"run": True}` — sätts till False för att stoppa loop

---

## AMP ON/OFF-arkitektur
- **AMP: OFF** (starttillstånd): `_serial_running = None`, serieporten stängd, DTR låg → steget av
- **AMP: ON**: `_amp_connect()` anropas → `start_serial()` → porten öppnas, DTR hög → steget på
- **AMP: OFF** (klick): `_amp_disconnect()` → `stop_serial()` → loopen avslutas → porten stängs, DTR låg
- Kontrollknappar (Mode, ANT, Power, Tune) är disabled tills AMP: ON
- Vid stängning: `_CloseDialog` frågar "Stäng av slutsteget?" om AMP är ON

---

## Tune-flöde

### Manual Tune (Tune, single)
1. Snapshotta `tx_trx` och frekvens (`freq_rx0`/`freq_rx1`) vid start
2. `modulation:TRX,CW;`
3. Vänta på STANDBY (max 3 toggle, 0.05s poll)
4. TX ON: `tune:TRX,true;`
5. Validera RF: 2–15W inom 3 sekunder
6. `send_tune()` via RS232
7. Poll `flags["tune"]` true → false (max 10s)
8. TX OFF: `tune:TRX,false;`
9. Återställ frekvens i `finally`

### Tune, sweep
- Bestäm segment från VFO via `freq_to_band(freq)`
- Spara `restore_freq` innan sweep
- Stega 20 kHz per steg, startar 1 kHz in i bandet (`f = start_f + 1000`)
- Kör manual tune-flöde på varje frekvens
- Återställ alltid `restore_freq` i `finally`
- `stop_requested` (threading.Event) — STOP avbryter

### Bandplan
```python
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
```

---

## Telemetri — paketparsning (RS232, 35 bytes, header `0xAA 0xAA 0xAA`)
- `get_flags(pkt)`: `pkt[5]` → `op=bit1, tx=bit2, tune=bit0, alarm=bit3`
- `get_power(pkt)`: `((pkt[27]<<8)|pkt[26]) / 10.0` W
- `get_swr(pkt)`: `(pkt[24]<<8)|pkt[23]` / 100.0 (None i OPERATE eller vid 0/9999)
- `get_reflected_power(pkt)`: `((pkt[29]<<8)|pkt[28]) / 10.0` W
- `get_temp(pkt)`: `pkt[25]`; enhet: bit 7 av pkt[5] → °C (1) eller °F (0)
- `get_band(pkt)`: `pkt[18] >> 4` → index i `["160","80","40","30","20","17","15","12","10","6"]`
- `get_antenna(pkt)`: `(pkt[22] & 0x0F) + 1`
- `get_input(pkt)`: `(pkt[18] & 0x0F) + 1`
- `get_power_mode(pkt)`: `"HIGH" if (pkt[5] & 0b10000) else "LOW"` ← **bit satt = HIGH** (omvänt mot vad man förväntar — verifierat mot hårdvara)
- `get_warning(pkt)`: `pkt[6]` → WARNINGS-dict

---

## Debounce-logik (GUI, viktigt)

### OP/STANDBY — 2-pakets-konsensus + settling-fönster
- `toggle_mode()`: optimistisk GUI-uppdatering direkt + `_relay_settling_until = now + 0.5s`
- `next_ant()`: sätter bara settling-fönstret (skyddar mot OP-flimmer vid reläbyte)
- `process_queue`: accepterar OP-ändring bara om två paket i rad är lika OCH utanför settling
- Första paketet efter ny anslutning accepteras direkt (`_op_raw_prev is None`)
- `_apply_op_state(op)`: uppdaterar mode_btn + SWR-meter-rubrik atomärt

### TX — 400 ms debounce
- `_tx_pending_since`: sätts när TX=True, nollställs vid False
- TX visas som ON först efter 400ms kontinuerlig True

### Band — 500 ms debounce
- `_band_pending = (band_str, timestamp)`: uppdateras vid nytt band
- Band visas först efter 500ms stabilt värde

### Alarm — 400 ms debounce
- `_alarm_pending_since`: samma mönster som TX

---

## GUI

### Fönster
- Titel: `"SM5K SPE Tuner vX.Y.Z"`
- `resizable(False, False)` — aldrig resize pga innehåll
- Mörkt tema:
  ```
  BG="#16181d", PANEL="#1e2028", BORDER="#2a2d3a", TEXT="#d4d8e8"
  MUTED="#555a6e", GREEN="#48c774", RED="#e05252", AMBER="#e8a030"
  BTNBG="#2a2d3a", BTNFG="#d4d8e8", TUNEBG="#1a3d1a", TUNEFG="#48c774"
  STOPBG="#3d1a1a", STOPFG="#e05252"
  ```

### Topbar (vänster → höger)
- `TCI: OK/LOST` (grön/röd, width=14)
- `Serial: OK/LOST` (grön/röd, width=14)
- `AMP: ON/AMP: OFF` (grön/röd, klickbar, width=8) — startar/stoppar serieporten
- [höger] ⚙ → SettingsDialog
- [höger] radionamn / frekvens (width=14, anchor=e)

### Mätare (POWER + REF POWER/SWR)
- Två Meter-widgets sida vid sida, lika stora (expand=True, fill=both)
- POWER: 0–150W, gul >60W, röd >100W; font Consolas 28 bold, width=6
- REF POWER (i OPERATE): 0–200W, gul >60W, röd >150W
- SWR (i STANDBY): 0–3.0, gul >1.5, röd >2.5
- Rubrik växlar mellan "REF POWER" och "SWR" beroende på OP-läge

### Statusrad
- `TX: OFF` / `● TX ON` (röd bg, fast width=8) — 400ms debounce
- `Band: 20` (500ms debounce)
- `Temp: 29°C` (röd ≥83°C, gul ≥70°C, enhetssäker jämförelse)
- `RX1/RX2 14.074000 MHz` — klick på label växlar manuellt aktiv TX-TRX

### Kontrollknappar (tre i rad, lika breda via columnconfigure weight=1)
- `Mode: OPERATE` (gul bg i OPERATE) / `Mode: STANDBY` — toggle_operate()
- `ANT: 2` — next_antenna()
- `Power: HIGH` / `Power: LOW` — toggle_power_mode()
- Alla disabled när AMP: OFF

### Tune-knappar (två i rad, exakt lika stora via grid+columnconfigure weight=1)
- `Tune, single` — tunar på aktuell VFO-frekvens
- `Tune, sweep` — kör bandsweep för aktuellt segment
- Disabled när AMP: OFF

### STOP-knapp
- Röd, full bredd, sätter stop_requested

### LOG (CollapsibleSection)
- Expanderbar sektion längst ned
- Tidsstämpel (HH:MM:SS) framför varje rad

### Larmbanners
- `alarm_lbl`: röd banner med `⚠ MSG ⚠`, visas med pack() efter meters_frame
- `other_radio_lbl`: grå banner vid okänd ingång, låser kontroller

---

## Ingångshantering
- RS232-telemetri rapporterar aktiv ingång (`get_input(pkt)`)
- `find_radio(inp)` matchar mot konfigurerade radios
- Känd ingång: kontroller aktiverade, topbar grön
- Okänd ingång: kontroller låsta (disabled), varningsbanner visas

---

## Inställningar (settings.json)
- Sparas bredvid EXE (sys.executable i frozen-läge)
- Fält: `serial_port`, `radios` (lista med `name`, `input`, `tci_host`, `tci_port`)
- SettingsDialog: modal Toplevel med grab_set()

---

## Stängning
- `WM_DELETE_WINDOW` → `_on_close()`
- Om AMP ON: visa `_CloseDialog` ("Stäng av slutsteget?" / "Avbryt")
- Vid bekräftelse: `_amp_disconnect()` + `time.sleep(1.0)` → `_tci_running["run"]=False` → `destroy()`

---

## Bygge
```
cd "C:\claude\Tune\sm5k-spe-tuner"
python -m PyInstaller SM5K_SPE_Tuner.spec
```
- Python 3.14, PyInstaller 6.20
- Spec-filen kopierar automatiskt EXE till `C:\claude\aktiv\tune\SM5K_SPE_Tuner.exe`
- GitHub: https://github.com/sm5tog/sm5k-spe-tuner

---

## Versionshistorik
- v1.0.0: Första release
- v1.0.1: Sweep integrerad, 20 kHz steg, fix serial-looping/DTR-cykling
- v1.0.2: AMP ON/OFF via DTR (start_serial/stop_serial), OP-debounce (2-pakets-konsensus + settling), TX/band/alarm-debounce, stängdialog, optimistisk GUI-uppdatering
- v1.0.3: Lika stora tune-knappar, fast fönsterstorlek, frekvensåterställning efter sweep, dual-RX frekvensvisning med RX-prefix
- v1.0.4: Dual-RX TX-tracking via `trx:N,true/false`, `trx_active`-dict, AMP ON/OFF återinförd korrekt, OP-debounce återinförd, HIGH/LOW power-fix
