# Runbook

## Recon (Phase 0)

```bash
renogyctl scan
renogyctl sweep --mac AA:BB:CC:DD:EE:FF
renogyctl probe --mac AA:BB:CC:DD:EE:FF --device-id 16 --register 256
```

Fixtures land in `pi/tests/fixtures/`. GATT summary in `docs/discovered.md`.

## BlueZ stuck

```bash
sudo hciconfig hci0 down
sudo hciconfig hci0 up
# or
sudo systemctl restart bluetooth
```

## DC Home app needs the devices

```bash
# Phase 6: renogyctl pause 5m
sudo systemctl stop renogy-monitor   # until pause exists
```

## Logs

```bash
journalctl -u renogy-monitor -f
```

## Server stack on the VPS (Tailscale-only)

Everything is private: MQTT binds to the tailnet IP, Grafana binds to loopback
and is published with `tailscale serve` (valid TLS, tailnet only).

| Service | Address |
|---|---|
| Grafana | `https://blow-global.tail3d9980.ts.net:10451` |
| ntfy (push) | `https://blow-global.tail3d9980.ts.net:10452/<NTFY_TOPIC>` |
| MQTT | `100.83.4.23:1883` (user/password from `/opt/renogy/.env`) |

Deploy:

```bash
rsync -az server/ claude-server:/opt/renogy/
ssh claude-server 'cd /opt/renogy && docker compose up -d'
```

Tailscale Serve mapping:

```bash
ssh claude-server 'tailscale serve --bg --https=10451 http://127.0.0.1:3300'
ssh claude-server 'tailscale serve --bg --https=10452 http://127.0.0.1:2586'
```

Phone push: install the ntfy app, subscribe to the ntfy URL above.

## Pi: collector service

```bash
systemctl status renogy-monitor
journalctl -u renogy-monitor -f
renogyctl run --config /home/pi/renogy-monitor/config.yaml --once
```

Tailscale on the Pi is required only for reaching the private MQTT listener.

## Telegram bot (VPS)

Runs on the VPS in the `telegram` container, reads InfluxDB directly.
It does not depend on the Pi except through the telemetry it already sends.

```bash
ssh claude-server 'cd /opt/renogy && docker compose logs -f telegram'
ssh claude-server 'cd /opt/renogy && docker compose restart telegram'
```

Config lives in `/opt/renogy/.env`:

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot token from BotFather |
| `SOC_ALERT_BELOW` | SOC alarm threshold (default 20) |
| `ALERT_COOLDOWN_H` | hours between repeated alarms |
| `REPORT_HOUR` / `REPORT_MINUTE` | daily report time, Europe/Bucharest |

The first chat that sends `/start` is bound and persisted in
`/data/state.json`; other chats are ignored.

Commands: `/status`, `/baterii`, `/incarcator`, `/energie`, `/azi`,
`/raport`, `/ajutor`.
