# Botul de Telegram

Rulează pe **VPS**, în containerul `telegram`, și citește direct din InfluxDB.
Pi-ul nu are nicio legătură cu botul — dacă Pi-ul cade, botul răspunde în
continuare cu ultimele date primite.

## 1. Creare bot

1. Telegram → **@BotFather** → `/newbot`
2. nume afișat (ex. `Rulota Monitor`) și username (ex. `Rulota_Bot`)
3. primești un token de forma `1234567890:AA...`

Tokenul se pune **doar** în `/opt/renogy/.env` pe VPS:

```bash
TELEGRAM_BOT_TOKEN=1234567890:AA...
```

```bash
cd /opt/renogy && docker compose up -d telegram
docker compose logs -f telegram      # "bot started, bound chats: []"
```

## 2. Autorizare

Deschide botul în Telegram și apasă **Start** (sau trimite `/start`).

Primul chat care scrie este **singurul** care primește răspuns; se salvează în
`/data/state.json` (volumul `telegram_state`). Orice alt chat este ignorat și
doar logat:

```text
bot started, bound chats: [123456789]
ignored unknown chat 987654321
```

Ca să schimbi chatul autorizat:

```bash
ssh <vps> 'cd /opt/renogy && docker compose exec telegram rm -f /data/state.json && \
           docker compose restart telegram'
```

## 3. Comenzi

| Comandă | Ce returnează |
|---|---|
| `/status` | rezumat: SOC, tensiune, curent per baterie + surse active |
| `/baterii` | detalii per baterie: SOC, V, A, W, Ah, temperaturi, celule, încălzire |
| `/incarcator` | DC-DC + MPPT: PV, alternator/starter, temperaturi, mod, azi, total |
| `/energie` | surse de încărcare și consum, în amperi |
| `/azi` | raport de la miezul nopții |
| `/raport` | raport complet ultimele 24 h |
| `/ajutor` | lista comenzilor |

Merge și fără slash: `status`, `baterie`, `consum`, `raport`.

### Exemplu `/status`

```text
⚡ Status acum

Baterie 1 (BT-2): 99.9% · 14.40 V · 0.00 A (repaus) · 11.9 °C
Baterie 2 (nativă): 100.0% · 14.50 V · 0.07 A (încarcă) · 11.8 °C

Surse: priză
Curent net în baterii: 0.07 A
Consum estimat: 0.00 A
PV: 0 W · Alternator: 0 W · Temp controller: 9 °C

Ultima măsurătoare: acum 2 min
```

### Exemplu `/raport`

```text
📊 Raport ultimele 24h

Baterie 1 (BT-2)
  SOC: min 94.6% · max 100.0% · mediu 99.4%
  Tensiune: min 13.98 V · max 14.40 V
Baterie 2 (nativă)
  SOC: min 98.8% · max 100.0% · mediu 99.8%
  Tensiune: min 14.02 V · max 14.42 V

Energie
  Panouri: 0.00 Ah (0 Wh)
  Alternator: 0.00 Ah (0 Wh)
  Priză: 0.03 Ah
  Net în baterii: +0.03 Ah
  Consumat: 0.00 Ah
```

## 4. Mesaje automate

### Raport zilnic

Se trimite la ora din `.env`:

```bash
REPORT_HOUR=9
REPORT_MINUTE=0
TZ_NAME=Europe/Bucharest
```

Se trimite o singură dată pe zi; ora se verifică la fiecare minut.

### Alarmă SOC

```bash
SOC_ALERT_BELOW=20      # pragul, în procente
ALERT_COOLDOWN_H=3      # cât să tacă între alerte repetate
```

- când oricare baterie scade sub prag → mesaj cu SOC și tensiune;
- la revenire peste prag → mesaj de confirmare;
- alerta se repetă cel mult o dată la `ALERT_COOLDOWN_H` ore.

Alertele din Grafana (SOC și telemetrie lipsă) ajung separat, prin **ntfy**, pe
telefon — vezi `docs/DEPLOY.md`, secțiunea 5.5.

## 5. Variabile de configurare

| Variabilă | Default | Rol |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | tokenul de la BotFather |
| `INFLUX_URL` | `http://influxdb:8086` | sursa de date |
| `INFLUX_TOKEN` / `INFLUX_ORG` / `INFLUX_BUCKET` | din `.env` | acces InfluxDB |
| `TZ_NAME` | `Europe/Bucharest` | fusul pentru raport |
| `SOC_ALERT_BELOW` | `20` | prag alarmă |
| `ALERT_COOLDOWN_H` | `3` | pauză între alarme |
| `REPORT_HOUR` / `REPORT_MINUTE` | `9` / `0` | ora raportului |
| `STATE_DIR` | `/data` | unde se salvează chatul autorizat |

## 6. Depanare

| Simptom | Verifică |
|---|---|
| niciun răspuns | `docker compose logs telegram`; token greșit → `getMe` |
| „ignored unknown chat" | alt cont încearcă; șterge `/data/state.json` |
| răspunde dar fără date | InfluxDB gol → verifică `telegraf` |
| raportul nu vine | ora din `.env`, fusul `TZ_NAME`, containerul pornit |
| alarma nu vine | `SOC_ALERT_BELOW`, cooldown, bateria chiar sub prag |

Test rapid al tokenului:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getMe"
```
