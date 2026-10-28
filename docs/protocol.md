# Protocol notes

Reference: [cyrils/renogy-bt](https://github.com/cyrils/renogy-bt) — Modbus RTU over GATT.

## GATT (BT-1 / BT-2)

| Role | UUID |
|---|---|
| Write service | `0000ffd0-0000-1000-8000-00805f9b34fb` |
| Write char | `0000ffd1-0000-1000-8000-00805f9b34fb` |
| Notify char | `0000fff1-0000-1000-8000-00805f9b34fb` |

## Modbus

- Function `0x03` read holding registers
- Request: `device_id, 0x03, reg_hi, reg_lo, words_hi, words_lo, crc_hi, crc_lo`
- Default recon register: **256** (`0x0100`)
- Device IDs to try: 255, 16, 17, 96, 97, 33–35, 48–50

## Battery native BLE

May differ from BT-2. Capture raw with `sweep` / `probe`; do not assume register maps
until fixtures exist for both batteries.

## Register maps

Filled in Phase 1 from fixtures + DC Home cross-check. Maps live as data under
`renogy_monitor/modbus/` (not hard-coded parsers only).
