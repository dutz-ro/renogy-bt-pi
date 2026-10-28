"""Register maps for the Renogy devices found during hardware recon."""

from __future__ import annotations

from typing import Any


def parse_dc_charger(registers: tuple[int, ...]) -> dict[str, Any]:
    """Parse register 256, 30 words, from a Renogy DC-DC charger.

    The response layout follows the public Renogy BT DCChargerClient map.
    Temperature bytes are packed into register 3 (controller high byte,
    battery low byte).
    """
    if len(registers) < 22:
        raise ValueError("DC charger response needs at least 22 registers")
    temp = registers[3]
    status_names = {
        0: "deactivated", 1: "activated", 2: "mppt", 3: "equalizing",
        4: "boost", 5: "floating", 6: "current limiting", 8: "alternator direct",
    }
    return {
        "battery_soc_pct": float(registers[0]),
        "battery_voltage_v": registers[1] / 10,
        "charge_current_a": registers[2] / 100,
        "controller_temperature_c": float((temp >> 8) & 0xFF),
        "battery_temperature_c": float(temp & 0xFF),
        "alternator_voltage_v": registers[4] / 10,
        "alternator_current_a": registers[5] / 100,
        "alternator_power_w": float(registers[6]),
        "pv_voltage_v": registers[7] / 10,
        "pv_current_a": registers[8] / 100,
        "pv_power_w": float(registers[9]),
        "charging_amp_hours_today": float(registers[17]),
        "power_generation_today_wh": float(registers[19]),
        "charging_status": status_names.get(registers[28], "unknown") if len(registers) > 28 else None,
    }


def parse_battery_info(registers: tuple[int, ...]) -> dict[str, Any]:
    """Parse register 5042, 6 words, from the native Renogy battery."""
    if len(registers) < 6:
        raise ValueError("battery response needs 6 registers")
    raw_current = registers[0]
    current = raw_current - 65536 if raw_current & 0x8000 else raw_current
    remaining_ah = ((registers[2] << 16) | registers[3]) / 1000
    capacity_ah = ((registers[4] << 16) | registers[5]) / 1000
    soc = (remaining_ah / capacity_ah * 100) if capacity_ah else None
    return {
        "battery_current_a": current / 100,
        "battery_voltage_v": registers[1] / 10,
        "remaining_charge_ah": remaining_ah,
        "capacity_ah": capacity_ah,
        "battery_soc_pct": soc,
    }
