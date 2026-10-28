# Renogy Monitor

BLE monitor for a Renogy DC-DC MPPT (BT-2) + 2× LiFePO4 batteries on a
**Raspberry Pi Zero W**, 60s sequential poll, MQTT → InfluxDB/Grafana.

See [PLAN.md](PLAN.md) and [CLAUDE.md](CLAUDE.md).

## Status

**Phase 0 — Recon** implemented:

| Command | Purpose |
|---|---|
| `renogyctl scan` | Discover Renogy BLE, GATT dump → `docs/discovered.md` |
| `renogyctl probe` | One Modbus read → `pi/tests/fixtures/*.bin` + `.json` |
| `renogyctl sweep` | Try device_ids on register 256; save raw even if non-Modbus |

No polling loop, no MQTT yet.

## Constraints (Pi Zero W / ARMv6)

- Pure-Python deps only (`bleak`, `PyYAML`, `structlog`)
- One BLE connection at a time
- Every BLE op wrapped in `asyncio.wait_for`

## Dev (laptop)

```bash
cd pi
pip install -e ".[dev]"
just test    # from repo root
just lint
```

## On the Pi

```bash
cd pi && ./deploy/install.sh
renogyctl scan
renogyctl sweep --mac <MAC>
```

## Layout

```
pi/src/renogy_monitor/   # application
pi/tests/                # FakeTransport tests, fixtures
docs/                    # discovered.md, protocol, runbook
server/                  # Phase 4 docker stack (stub)
```
