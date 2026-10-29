"""Register maps for the Renogy devices found during hardware recon."""

from __future__ import annotations

from typing import Any

# LiFePO4 self-heating in the Renogy Pro series is automatic and runs below
# roughly 5 C. The BMS does not expose a heating flag over Modbus, so the
# state is inferred from cell temperature and current.
HEATING_BELOW_C = 5.0


def parse_dc_charger(registers: tuple[int, ...]) -> dict[str, Any]:
    """Parse register 256 (30 words) from the RBC50D1S-G1 DC-DC charger."""
    if len(registers) < 22:
        raise ValueError("DC charger response needs at least 22 registers")
    temp = registers[3]
    total_ah = (registers[24] << 16) | registers[25] if len(registers) > 25 else None
    total_wh = (registers[28] << 16) | registers[29] if len(registers) > 29 else None
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
        "battery_min_voltage_today_v": registers[11] / 10,
        "battery_max_voltage_today_v": registers[12] / 10,
        "battery_max_current_today_a": registers[13] / 100,
        "max_charging_power_today_w": float(registers[15]),
        "charging_amp_hours_today": float(registers[17]),
        "power_generation_today_wh": float(registers[19]),
        "total_working_days": float(registers[21]),
        "count_battery_overdischarged": float(registers[22]),
        "count_battery_fully_charged": float(registers[23]),
        "battery_ah_total_accumulated": float(total_ah) if total_ah is not None else None,
        "power_generation_total_wh": float(total_wh) if total_wh is not None else None,
    }


def parse_battery_cells(registers: tuple[int, ...]) -> dict[str, Any]:
    """Parse register 5000: cell count followed by cell voltages (0.1 V)."""
    if not registers:
        raise ValueError("empty battery cell response")
    count = registers[0]
    cells = [registers[1 + i] / 10 for i in range(min(count, len(registers) - 1))]
    if not cells:
        raise ValueError("no cell voltages in response")
    return {
        "cell_count": float(count),
        "cell_voltage_min_v": min(cells),
        "cell_voltage_max_v": max(cells),
        "cell_voltage_delta_v": round(max(cells) - min(cells), 3),
        "cell_voltage_avg_v": round(sum(cells) / len(cells), 3),
    }


def parse_battery_temps(registers: tuple[int, ...]) -> dict[str, Any]:
    """Parse register 5017: sensor count followed by signed 0.1 C temps."""
    if not registers:
        raise ValueError("empty battery temperature response")
    count = registers[0]
    temps = []
    for i in range(min(count, len(registers) - 1)):
        raw = registers[1 + i]
        signed = raw - 65536 if raw & 0x8000 else raw
        temps.append(signed / 10)
    if not temps:
        raise ValueError("no temperatures in response")
    return {
        "temperature_sensor_count": float(count),
        "temperature_min_c": min(temps),
        "temperature_max_c": max(temps),
        "temperature_avg_c": round(sum(temps) / len(temps), 1),
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


def heating_state(min_temp_c: float | None, charge_current_a: float | None) -> str:
    """Infer self-heating state; the BMS has no explicit flag."""
    if min_temp_c is None:
        return "necunoscut"
    if min_temp_c >= HEATING_BELOW_C:
        return "inactivă"
    if (charge_current_a or 0) > 0.3:
        return "activă (dedus)"
    return "posibil activă"
