# SM5K SPE Tuner — Backlog

## Effektreglering via TCI
- Sätt TX-effekt via TCI inför tune (lägre effekt för att skydda tunern)
- Återställ effekt efter tune till tidigare värde
- Separat effektinställning för tune vs normal sändning (t.ex. 10W tune / full power TX)
- Gäller både Tune, single och Tune, sweep

## Fjärrstyrning / server-klient
- Dela appen i en daemon (kör RS232+TCI på fjärrsidan) och ett rent GUI hemma som pratar TCP
- TCI är redan nätverksklar (WebSocket, bara ändra host i settings)
- RS232-delen (steget) behöver Serial-over-IP eller klient-server-arkitektur
- Planerat för fjärrstation i Dalarna
