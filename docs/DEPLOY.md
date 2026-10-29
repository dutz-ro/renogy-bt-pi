# Deploy cap-coadă

Ghid pentru cine pornește de la zero: card SD gol → dashboard în Grafana + bot
în Telegram. Timp estimat: **90–120 minute**, din care ~30 minute așteptare.

Înlocuiește valorile din `<paranteze>` cu ale tale. Nu pune niciodată parole în
git.

---

## 0. Ce îți trebuie

**Hardware**

- Raspberry Pi Zero W + card microSD 16 GB (clasa A1/A2)
- Alimentator 5 V / 2 A pe portul **PWR** (nu prin portul USB de date)
- Cablu micro-USB **de date** pentru consolă (opțional, dar util la început)
- Încărcător Renogy și bateriile, cu modulele BLE montate

**Conturi și acces**

- VPS cu Docker (1 vCPU / 1 GB RAM e suficient)
- cont Tailscale (gratuit) pe Pi, VPS și telefon
- bot Telegram creat cu **@BotFather**

**Pe laptop**

- `xz`, `dd`, acces la cardul SD
- `ssh`, `git`

---

## 1. Pregătește cardul SD

### 1.1 Scrie imaginea

Descarcă **Raspberry Pi OS Lite 32-bit (armhf)** — pe Pi Zero W trebuie neapărat
32-bit. Verifică suma SHA256, apoi scrie:

```bash
# macOS: găsește discul cardului, ex. /dev/disk4 (NU disk0!)
diskutil list

diskutil unmountDisk /dev/diskN
xz -dc 2026-10-06-raspios-trixie-armhf-lite.img.xz | sudo dd of=/dev/rdiskN bs=4m
sync && diskutil eject /dev/diskN
```

> `dd` distruge tot de pe discul indicat. Verifică de două ori numărul.

### 1.2 Pregătește partiția de boot

Remontează `bootfs` și creează:

```bash
BOOT=/Volumes/bootfs

# SSH activat
touch "$BOOT/ssh"

# consolă pe USB serial + gadget serial (debug fără rețea)
echo 'dtoverlay=dwc2,dr_mode=peripheral' >> "$BOOT/config.txt"
# adaugă la finalul liniei din cmdline.txt:
#   console=ttyGS0,115200 modules-load=dwc2,g_serial cfg80211.ieee80211_regdom=RO
#   systemd.run=/boot/firmware/firstrun.sh
```

### 1.3 Scriptul de prim boot

Copiază `pi/deploy/firstrun.example.sh` ca `firstrun.sh` pe partiția de boot,
completează userul, parola, hostname-ul și rețelele WiFi, apoi:

```bash
cp pi/deploy/firstrun.example.sh "$BOOT/firstrun.sh"
chmod +x "$BOOT/firstrun.sh"
```

Scriptul rulează o singură dată, la primul boot: creează utilizatorul, scrie
profilurile WiFi, activează SSH, apoi **șterge `systemd.run` din `cmdline.txt`**
ca sistemul să pornească normal la boot-urile următoare.

> Dacă preferi să configurezi manual, sari peste 1.3: pornește Pi-ul, conectează
> un monitor HDMI+USB OTG sau folosește `raspi-config`.

---

## 2. Primul boot

1. Cardul în Pi.
2. Alimentatorul în **PWR**.
3. Așteaptă 2–3 minute.

Verificări:

```bash
# consolă USB (dacă ai cablul de date în portul din mijloc)
screen /dev/cu.usbmodem* 115200      # ieși cu Ctrl-A, K

# sau, dacă WiFi-ul s-a conectat
ssh pi@renogy-pi.local
```

Pe Pi:

```bash
hostname                       # renogy-pi
ip -4 addr show wlan0 | grep inet
ping -c1 1.1.1.1
```

---

## 3. Instalează aplicația pe Pi

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git

mkdir -p ~/renogy-monitor
# copiază conținutul directorului pi/ din repo în ~/renogy-monitor
```

Apoi:

```bash
cd ~/renogy-monitor
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

Creează `config.yaml` pornind de la `deploy/config.example.yaml` și pune MAC-urile
reale (le afli cu `renogyctl scan`):

```yaml
site_id: rulota
cycle_budget_s: 60

devices:
  - alias: dcc50s
    mac: "60:98:66:FA:EE:CA"
    kind: dc_charger
    device_id: 96
  - alias: battery_bt2
    mac: "CC:45:A5:83:BD:F3"
    kind: battery_bt2
    device_id: 48
  - alias: battery_native
    mac: "4C:E1:74:52:1C:0E"
    kind: battery_native
    device_id: 255

mqtt:
  host: "<IP_TAILSCALE_VPS>"
  port: 1883
  username: "renogy"
  password: "<PAROLA_MQTT>"
  client_id: "renogy-pi"
```

```bash
chmod 600 config.yaml
```

Test rapid, fără serviciu:

```bash
.venv/bin/renogyctl run --config config.yaml --once
```

Trebui să vezi patru linii JSON: `dcc50s`, `battery_bt2`, `battery_native`,
`system`.

Instalează serviciul:

```bash
sudo install -d -o pi -g pi /var/lib/renogy-monitor
sudo cp deploy/renogy-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now renogy-monitor
systemctl status renogy-monitor
```

---

## 4. Tailscale pe Pi

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --accept-dns=false --hostname renogy-pi
```

Deschide linkul afișat și aprobă dispozitivul. Apoi, **important**:

```text
Tailscale admin → Machines → renogy-pi → ⋯ → Disable key expiry
```

Altfel nodul cere reautentificare după ~6 luni.

Acces de la distanță:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@renogy-pi    # o singură dată
ssh pi@renogy-pi
```

Adaugă în `~/.ssh/config` pe laptop:

```text
Host renogy-pi
    HostName renogy-pi
    User pi
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30
```

---

## 5. VPS: stack-ul de servere

### 5.1 Pregătire

```bash
ssh root@<VPS>
apt-get update && apt-get install -y docker.io docker-compose-v2
usermod -aG docker <user>
```

### 5.2 Fișiere

```bash
sudo mkdir -p /opt/renogy && sudo chown $USER:$USER /opt/renogy
# copiază conținutul directorului server/ în /opt/renogy
cd /opt/renogy
```

### 5.3 Secrete

```bash
cat > .env <<EOF
TAILSCALE_IP=$(tailscale ip -4 | head -1)
GRAFANA_PORT=3300
INFLUX_USERNAME=admin
INFLUX_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
INFLUX_ORG=renogy
INFLUX_BUCKET=telemetry
INFLUX_TOKEN=$(openssl rand -hex 32)
INFLUX_RETENTION=52w
MQTT_USER=renogy
MQTT_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
GRAFANA_ROOT_URL=https://<NUME>.ts.net:10451
NTFY_BASE_URL=https://<NUME>.ts.net:10452
NTFY_PORT=2586
NTFY_TOPIC=renogy-$(openssl rand -hex 12)
ALERT_WEBHOOK_URL=http://alertbridge:8080/hook
TELEGRAM_BOT_TOKEN=<TOKEN_BOTFATHER>
TZ_NAME=Europe/Bucharest
SOC_ALERT_BELOW=20
REPORT_HOUR=9
REPORT_MINUTE=0
EOF
chmod 600 .env
```

Parola MQTT trebuie scrisă și în `config.yaml` pe Pi.

```bash
set -a; . ./.env; set +a
docker run --rm -v /opt/renogy/mosquitto:/out eclipse-mosquitto:2 \
  sh -c "mosquitto_passwd -c -b /out/passwd '$MQTT_USER' '$MQTT_PASSWORD'"
sudo chmod 644 mosquitto/passwd
```

### 5.4 Pornire

```bash
docker compose up -d
docker compose ps
```

Containere așteptate: `mosquitto`, `influxdb`, `telegraf`, `grafana`, `ntfy`,
`alertbridge`, `telegram`.

### 5.5 Publicare prin Tailscale (fără porturi publice)

```bash
tailscale serve --bg --https=10451 http://127.0.0.1:3300   # Grafana
tailscale serve --bg --https=10452 http://127.0.0.1:2586   # ntfy
tailscale serve status
```

Rezultat:

```text
Grafana: https://<NUME>.ts.net:10451
ntfy:    https://<NUME>.ts.net:10452/<NTFY_TOPIC>
```

MQTT ascultă doar pe IP-ul Tailscale al VPS-ului, port 1883.

---

## 6. Grafana

Login cu `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` din `.env`.

Totul e provisionat automat la pornire:

- datasource `Renogy InfluxDB`;
- dashboard **Renogy — Energie** (SOC, tensiuni, curenți, temperaturi, celule,
  surse de încărcare, consum, producție azi/total);
- reguli de alertă: SOC sub 20% și telemetrie lipsă 15 minute.

Primul punct apare în grafice la ~1 minut după ce Pi-ul publică.

---

## 7. Telegram

1. În Telegram: `@BotFather` → `/newbot` → nume + username → primești tokenul.
2. Pune tokenul în `.env` (`TELEGRAM_BOT_TOKEN`) și repornește:
   ```bash
   docker compose up -d telegram
   ```
3. Deschide botul și apasă **Start**. Primul chat care scrie devine singurul
   autorizat; restul sunt ignorate.

Comenzi: vezi [TELEGRAM.md](TELEGRAM.md).

---

## 8. Verificare cap-coadă

Rulează pe rând; toate trebuie să treacă.

```bash
# 1. Pi: serviciul rulează
ssh pi@renogy-pi 'systemctl is-active renogy-monitor'

# 2. Pi: ultimul ciclu a citit toate dispozitivele
ssh pi@renogy-pi 'journalctl -u renogy-monitor -n 30 | grep -E "session_open|mqtt_flush"'

# 3. VPS: containerele sunt sus
ssh <vps> 'cd /opt/renogy && docker compose ps'

# 4. InfluxDB primește date
ssh <vps> 'cd /opt/renogy && set -a && . ./.env && set +a && \
  docker compose exec -T influxdb influx query --org renogy --token "$INFLUX_TOKEN" \
  "from(bucket: \"telemetry\") |> range(start: -10m) |> filter(fn: (r) => r._field == \"battery_soc_pct\") |> last()"'

# 5. Grafana răspunde
curl -s -o /dev/null -w '%{http_code}\n' https://<NUME>.ts.net:10451/api/health

# 6. Botul răspunde
#    scrie /status în Telegram
```

Checklist final:

- [ ] `renogy-monitor` = active pe Pi
- [ ] 4 mesaje pe ciclu (`sent: 4`) în log
- [ ] SOC apare în Grafana pentru ambele baterii
- [ ] botul răspunde la `/status`
- [ ] ai primit mesajul de test al alertei (opțional, vezi mai jos)

Test alertă (fără să aștepți o baterie descărcată):

```bash
ssh <vps> 'cd /opt/renogy && set -a && . ./.env && set +a && \
  curl -s -u "$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD" \
  -X POST http://127.0.0.1:3300/api/alertmanager/grafana/config/api/v1/receivers/test \
  -H "Content-Type: application/json" \
  -d "{\"receivers\":[{\"name\":\"renogy-default\",\"grafana_managed_receiver_configs\":[{\"uid\":\"renogy-webhook\",\"name\":\"renogy-default\",\"type\":\"webhook\",\"settings\":{\"url\":\"http://alertbridge:8080/hook\"}}]}]}"'
```

---

## 9. Operare

```bash
# loguri Pi
ssh renogy-pi 'journalctl -u renogy-monitor -f'

# loguri bot / telegraf / grafana
ssh <vps> 'cd /opt/renogy && docker compose logs -f telegram'

# restart
ssh renogy-pi 'sudo systemctl restart renogy-monitor'
ssh <vps> 'cd /opt/renogy && docker compose restart telegram'

# update cod
git push            # de pe laptop
ssh renogy-pi 'cd ~/renogy-monitor && git pull && .venv/bin/pip install -e . && sudo systemctl restart renogy-monitor'
ssh <vps> 'cd /opt/renogy && git pull && docker compose up -d'

# backup
ssh <vps> 'cd /opt/renogy && tar czf /root/renogy-$(date +%F).tgz .env grafana'
```

---

## 10. Depanare

| Simptom | Cauză probabilă | Soluție |
|---|---|---|
| Grafana nu se deschide | Tailscale oprit pe laptop | `tailscale status`, repornește Tailscale |
| Panouri goale în Grafana | tab în fundal / cache | refresh forțat (Cmd+Shift+R) |
| `mqtt_unreachable` pe Pi | Pi nu e în tailnet | `tailscale status` pe Pi |
| Un dispozitiv lipsește din ciclu | sesiune BlueZ agățată | codul face `Disconnect` automat; verifică `bluetoothctl info <MAC>` |
| Bateria nu apare la scan | nu se anunță, dar acceptă conexiune | normal; conexiunea directă merge |
| WiFi nu se conectează | AP WPA2/WPA3 mixed | folosește un SSID WPA2 clasic |
| Botul nu răspunde | token greșit sau chat neînregistrat | `docker compose logs telegram`, trimite `/start` |
| Alerta nu vine | webhook URL lipsă | verifică `ALERT_WEBHOOK_URL` în `.env` |
| Discul VPS plin | retenție InfluxDB | `INFLUX_RETENTION`, șterge volume vechi |
| Pi se restartează aleator | alimentare slabă | alimentator 5 V/2 A pe PWR |

Loguri utile:

```bash
ssh renogy-pi 'journalctl -u renogy-monitor -n 100 --no-pager'
ssh renogy-pi 'bluetoothctl devices'
ssh <vps> 'cd /opt/renogy && docker compose logs --tail=100 telegraf'
```
