"""Ren logik för SPE Tuner (inga GUI- eller nätverksimporter): bandtabell och drivkurvan."""

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


def curve_interpolate(pts, target_w):
    """Returnerar interpolerat drive-värde för target_w ur kurvan {drive: effekt}, eller None."""
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


def curve_update(curve, drive, power_w):
    """Lägger till eller uppdaterar en punkt i kurvan (på plats). Samma drive slås ihop trögt."""
    existing = curve.get(drive)
    if existing is not None:
        curve[drive] = round(0.7 * existing + 0.3 * power_w, 1)
    else:
        curve[drive] = round(power_w, 1)


def last_drive_key(band, mode, pmode, trx):
    """Nyckel för minnet: band, mode (CW/DIGU), slutstegets effektläge (HIGH/LOW) och radiokanal
    (trx 0 = RX1, 1 = RX2)."""
    return f"{band}|{mode}|{pmode}|RX{trx + 1}"


def curve_key(band, pmode, trx):
    """Nyckel för drivkurvan: band, effektläge och radiokanal."""
    return f"{band}|{pmode}|RX{trx + 1}"


def choose_ff_drive(last_drive, curve, band, mode, target_w, pmode, trx):
    """Drive att starta med: senast stabila drive för (band, mode, effektläge, RX, börvärde),
    annars interpolerat ur kurvan, annars None. last_drive: {"band|mode|pmode|RXn": {target: drive}}."""
    mem = ((last_drive or {}).get(last_drive_key(band, mode, pmode, trx)) or {}).get(int(target_w))
    if mem is not None:
        return int(mem)
    return curve_interpolate(curve, target_w)


def amp_band_from_freq(freq):
    """Slutstegets bandnamn ('40', '30', '6' ...) ur en frekvens, eller None.
    Samma nycklar som drivkurvan och last_drive använder (BANDS utan l/h-suffix)."""
    if not freq:
        return None
    b = freq_to_band(freq)
    return b[:-1] if b else None
