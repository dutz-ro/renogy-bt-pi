# Renogy Monitor

## Context
Colectare date BLE de la un Renogy DC-DC MPPT charger (modul BT-2), o baterie
LiFePO4 cu modul BT-2, și o a doua baterie cu BLE integrat.
Target: **Raspberry Pi Zero W** — ARMv6, single-core, 512MB, BT 4.1.
Uplink MQTT către VPS (InfluxDB + Grafana).

## Constrângeri de platformă (NEGOCIABILE NICIODATĂ)
- **ARMv6: doar dependențe pure-Python.** Fără pydantic v2, orjson, uvloop,
  msgspec, numpy. Orice pachet cu Rust sau C se compilează ore întregi sau
  eșuează. Verifică cu `pip install --only-binary=:all:`.
- **Single-core 1GHz.** Cod hot-path scris cu `struct.unpack`, nu cu abstracții
  în straturi. Fără comprehension-uri imbricate peste mii de elemente.
- **Polling secvențial, o singură conexiune BLE la un moment dat.** Ciclu de 60s:
  connect → read → disconnect, pentru fiecare din cele 3 device-uri, pe rând.
  NU implementa conexiuni persistente, keepalive, pooling sau citiri paralele —
  au fost evaluate și respinse. Simplitatea e feature-ul principal aici.
- **Orice operație BLE are timeout.** `asyncio.wait_for` obligatoriu.
  O conexiune nedeconectată blochează adaptorul până la reboot.

## Reguli
- Python 3.11+, asyncio peste tot. Fără thread-uri pentru BLE.
- Type hints obligatorii. `mypy --strict` trebuie să treacă (rulează pe laptop, nu pe Pi).
- BLE I/O e izolat în `ble/transport.py`. Logica de protocol NU importă bleak.
- Orice test nou rulează cu `FakeTransport` — CI nu are hardware.
- Hărțile de registre sunt DATE (dict/YAML), nu cod. Ușor de corectat.
- **Cele două baterii folosesc protocoale diferite.** `battery_bt2.py` și
  `battery_native.py` sunt clase separate, cu hărți de registre separate.
  Nu încerca să le unifici înainte de a avea capturi de la ambele.
- Nu folosi `time.sleep`. Nu folosi `print`. Logging structurat (structlog),
  nivel INFO în producție — DEBUG costă CPU real pe ARMv6.
- Toate valorile numerice trec prin validare de domeniu (o tensiune de
  baterie de 400V e un frame corupt, nu o măsurătoare).

## Comenzi
- `just test` — pytest, fără hardware
- `just lint` — ruff + mypy
- `just deploy` — rsync + restart pe Pi prin Tailscale
- `renogyctl scan` / `probe` / `sweep` / `read-once` / `run` / `pause`

## Referințe
- github.com/cyrils/renogy-bt — protocol de referință (BT-1/BT-2, Modbus RTU
  peste GATT). Modelul lui connect-read-disconnect e **exact** ce ne trebuie la 60s.
  Împrumută hărțile de registre și logica de parsare. Nu-l lua ca dependență —
  vrem control pe timeouts, buffering și schema de date.
- pypi.org/project/renogy-ble — parser alternativ, util pentru cross-check
- Documentație Modbus Renogy: discussions #94 în repo-ul cyrils/renogy-bt

## Anti-obiective
- Nu scriem în device-uri. Read-only. O comandă greșită poate strica un BMS.
- Nu implementăm control (on/off încărcare) în v1.
- Nu facem UI propriu — Grafana e suficient.
