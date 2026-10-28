# Renogy Monitor — Plan de proiect

Monitorizare Renogy DC-DC MPPT charger + 2× baterii 12V LiFePO4 prin Bluetooth LE,
de pe un Raspberry Pi Zero W, cu trimitere la un server propriu, **refresh 60s**.

---

## 0. Realitate hardware — de citit înainte de orice

### 0.1 Configurația ta

| Element | Ce ai | Implicație |
|---|---|---|
| Pi | **Zero W** (BCM43438, BT 4.1 BLE, single-core ARMv6 1GHz, 512MB) | La limită. Funcționează, dar fiecare decizie trebuie să economisească CPU |
| MPPT DC-DC | modul **BT-2** | periferic BLE separat |
| Baterie #1 | modul **BT-2** | periferic BLE separat |
| Baterie #2 | **BLE integrat** | periferic BLE separat, protocol ușor diferit |

Trei periferice BLE separate, fără daisy-chain posibil.

### 0.2 Decizia care definește proiectul: polling secvențial

La 60 de secunde **nu ai nevoie de conexiuni persistente și nici de concurență**.
Asta elimină partea grea a proiectului.

```
t=0s    conectare MPPT ──> citire ──> deconectare        (~8s)
t=10s   conectare Baterie #1 ──> citire ──> deconectare  (~8s)
t=20s   conectare Baterie #2 ──> citire ──> deconectare  (~8s)
t=30s   radio liber ──> uplink MQTT ──> idle             (~30s rezervă)
t=60s   ciclul se reia
```

**O singură conexiune BLE activă la orice moment.** Consecințe:

| Problemă din varianta la 5s | Cu polling secvențial la 60s |
|---|---|
| 3 conexiuni concurente pe BlueZ | Dispare — una singură |
| Coexistență WiFi ↔ BLE | Dispare — uplink în fereastra liberă de 30s |
| Reconnect cu backoff, keepalive, mutex pe adaptor | Nu mai sunt necesare |
| BlueZ îngheață după ore | Mult mai rar; și poți face `hci0 reset` între cicluri |
| Tuning de connection interval | Irelevant |
| Leak-uri de memorie în sesiuni lungi | Fiecare ciclu pornește curat |

Ai **30 de secunde de rezervă** în fiecare ciclu. Le folosești pentru:
- 2 încercări per device (retry imediat dacă prima eșuează)
- reset de adaptor între cicluri, dacă apar probleme
- uplink MQTT fără să concureze cu BLE

Bonus: cu radio-ul liber jumătate din timp, aplicația Renogy DC Home de pe telefon
reușește de multe ori să prindă device-urile fără să oprești serviciul.

### 0.3 Implicația asupra restului stack-ului

| | La 5s | **La 60s** |
|---|---|---|
| Sample-uri / zi / device | 17.280 | **1.440** |
| Trafic lunar (3 device-uri) | ~50 MB | **~5 MB** |
| Scrieri pe cardul SD | agresiv | neglijabil |
| Retenție raw în InfluxDB | 30 zile | **1 an, fără downsampling** |
| Downsample tasks în Flux | obligatoriu | opțional |

Practic dispare și nevoia de retention policy complicat. Un singur bucket, raw, 1 an.

### 0.4 Capcana ARMv6 — citește înainte de `pip install`

Pi Zero W e ARMv6. PyPI nu are wheels pentru arhitectura asta la pachetele moderne.
Orice dependență cu extensii Rust sau C se va **compila de la sursă, ore întregi**,
și uneori eșuează din lipsă de RAM.

**Interzis în acest proiect:**
- `pydantic` v2 (Rust) → folosește `dataclasses` + `PyYAML`
- `orjson`, `msgspec` (Rust/C) → `json` din stdlib e suficient
- `uvloop` → asyncio standard
- `polars`, `numpy` (dacă nu e absolut necesar)
- `cryptography` versiuni noi → folosește `ssl` din stdlib pentru TLS MQTT

**Permis / testat pe armv6:**
- `bleak` (pure Python + dbus-fast)
- `paho-mqtt` (pure Python)
- `PyYAML` (are wheel sau compilează rapid)
- `structlog` (pure Python)
- `sqlite3` (stdlib)

Regulă: dacă un pachet nu e pure-Python, justifică-l în PR sau taie-l.
Rulează `pip install --only-binary=:all:` ca să vezi imediat ce ar compila.

### 0.5 Aplicația Renogy DC Home

BLE permite un singur *central* conectat la un periferic. Cât timp Pi-ul e conectat,
telefonul nu vede device-ul. La 60s ești conectat doar ~8s per device, deci de cele
mai multe ori aplicația prinde o fereastră liberă. Dacă vrei garanție:
`renogyctl pause 5m` oprește ciclul complet.

---

## 1. Arhitectura

```
┌─ Pi Zero W (în mașină/rulotă/barcă) ────────────────────┐
│                                                          │
│   ciclu de 60s, o singură conexiune BLE la un moment dat │
│                                                          │
│   [BLE] ──> collector (asyncio + bleak)                  │
│     seq.      │  connect → modbus read → disconnect      │
│               ▼                                          │
│           normalizer  ── schema JSON unificată           │
│               ▼                                          │
│           SQLite WAL (buffer offline, ring ~30 zile)     │
│               ▼                                          │
│           uplink ── MQTT/TLS (QoS1) ──────┐              │
│           (în fereastra liberă de ~30s)   │              │
└───────────────────────────────────────────┼──────────────┘
                                            │ LTE / hotspot
┌─ VPS (Hetzner / Contabo / orice ~4€/lună)─▼──────────────┐
│  Mosquitto (TLS, user/pass)                              │
│        ▼                                                 │
│  Telegraf  ──>  InfluxDB 2.x  ──>  Grafana               │
│                                      ▲                   │
│  Caddy (HTTPS automat) ──────────────┘                   │
│  Tailscale (SSH la Pi de oriunde, fără port forwarding)  │
└──────────────────────────────────────────────────────────┘
```

### De ce MQTT și nu HTTP POST
La 60s volumul e mic (4.320 mesaje/zi total), deci HTTP POST ar fi și el viabil.
MQTT rămâne alegerea bună pentru: QoS1 (livrare garantată la backlog),
LWT (serverul știe imediat când Pi-ul cade), `retain` pentru ultima valoare,
și o singură conexiune TLS ținută deschisă în loc de handshake la fiecare ciclu.

Dar dacă vrei să scurtezi proiectul: `uplink/http.py` cu POST batch la 60s e
perfect acceptabil și elimină Mosquitto din stack. Decide în Faza 3.

### Alternative dacă nu vrei VPS
- **Home Assistant** acasă + Nabu Casa / Tailscale → dashboard gata făcut, zero cod server
- **InfluxDB Cloud Serverless** free tier + **Grafana Cloud** free tier → zero infra
- Recomandare: începe cu VPS propriu, ai control total și costă cât o cafea

---

## 2. Structura repo-ului

```
renogy-monitor/
├── CLAUDE.md
├── README.md
├── PLAN.md
├── pi/
│   ├── pyproject.toml
│   ├── src/renogy_monitor/
│   │   ├── __main__.py
│   │   ├── config.py
│   │   ├── ble/
│   │   │   ├── transport.py
│   │   │   ├── bleak_transport.py
│   │   │   ├── fake_transport.py
│   │   │   └── session.py
│   │   ├── modbus/
│   │   │   ├── frames.py
│   │   │   └── registers.py
│   │   ├── devices/
│   │   ├── cycle.py
│   │   ├── buffer.py
│   │   ├── uplink/
│   │   ├── health.py
│   │   └── cli.py
│   ├── tests/
│   └── deploy/
├── server/
└── docs/
```

---

## 3. Fazele proiectului

### Faza 0 — Recon ✅ (implemented)
- [x] `renogyctl scan`
- [x] `renogyctl probe`
- [x] `renogyctl sweep`
- [ ] Run on hardware → fill `docs/discovered.md` + fixtures

### Faza 1 — Citire single-device
- [ ] CRC16 + register maps + `read-once` + unit tests on fixtures

### Faza 2 — Ciclul secvențial de 60s
- [ ] BleSession budget, retry, hci reset, metrics

### Faza 3 — Buffer + uplink
- [ ] SQLite WAL + MQTT QoS1 + drain

### Faza 4 — Server stack
- [ ] docker-compose mosquitto/telegraf/influx/grafana/caddy

### Faza 5 — Dashboard + alerte

### Faza 6 — Hardening
- [ ] systemd watchdog, pause, SD protection

---

## 4. Schema de date

```
renogy/<site_id>/<device_id>/state
renogy/<site_id>/<device_id>/status
```

`ts` pe Pi la citire. Unități SI. `stale` pe reuse.

---

## 5. Riscuri

| Risc | Mitigare |
|---|---|
| Battery native ≠ BT-2 | clase separate + capturi Faza 0 |
| ARMv6 compile | pure-Python only |
| BLE hang | timeouts + session disconnect |
| BlueZ freeze | hci reset + watchdog |

---

## 6–8

Vezi CLAUDE.md. Prima comandă pe hardware: `renogyctl scan`.
