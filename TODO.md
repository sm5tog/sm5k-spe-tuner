# SM5K SPE Tuner — Backlog

## Effektreglering via TCI
- Sätt TX-effekt via TCI inför tune (lägre effekt för att skydda tunern)
- Återställ effekt efter tune till tidigare värde
- Separat effektinställning för tune vs normal sändning (t.ex. 10W tune / full power TX)
- Gäller både Tune, single och Tune, sweep

## Fjärrstyrning / server-klient — KLART 2026-07-31
- `spe_ampd.py` (daemon, äger RS232) + `spe_tuner_klient.py` (GUI, TCP/JSON mot daemonen)
- `spe_tuner.py` (original, allt lokalt) rörd inte, förblir tillgänglig separat
- TCI konfigureras med fjärrmaskinens riktiga LAN-IP i Settings, inte 127.0.0.1
- Verifierat fungerande Ramnäs↔Blecket, se `SPEC.md` och `radioutrustning/kunskapsbank_fjarrstyrning.md`
- Kvarstår: arbitrering mellan flera samtidiga klienter (idag ingen låsning)
