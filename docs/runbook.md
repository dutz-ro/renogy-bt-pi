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
