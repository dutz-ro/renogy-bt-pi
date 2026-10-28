"""BLE GATT UUIDs and Renogy alias prefixes (recon defaults)."""

from __future__ import annotations

# Standard Renogy BT-1 / BT-2 tunnel (from renogy-bt reference)
WRITE_SERVICE_UUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000ffd1-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
NOTIFY_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"

# Name prefixes seen on Renogy BLE modules / native BMS
ALIAS_PREFIXES: tuple[str, ...] = (
    "BT-TH",
    "RNG",
    "RTM",
    "RBT",
    "RNGRBP",
    "BTRIC",
    "RTMShunt",
    "RNGRIU",
)

# Device IDs to try during sweep (broadcast + known charger/battery ranges)
SWEEP_DEVICE_IDS: tuple[int, ...] = (
    255,  # broadcast
    16,
    17,
    96,
    97,  # chargers / controllers
    33,
    34,
    35,  # batteries (common)
    48,
    49,
    50,  # batteries (alt)
)

DEFAULT_SCAN_TIMEOUT_S = 10.0
DEFAULT_CONNECT_TIMEOUT_S = 12.0
DEFAULT_READ_TIMEOUT_S = 5.0
DEFAULT_PROBE_WORDS = 10
DEFAULT_SWEEP_REGISTER = 256  # 0x0100
