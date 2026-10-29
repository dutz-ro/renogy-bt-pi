"""Normalized telemetry and JSON serialization for the server uplink."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Telemetry:
    site_id: str
    device_id: str
    device_kind: str
    ts: str
    stale: bool = False
    battery_soc_pct: float | None = None
    battery_voltage_v: float | None = None
    battery_current_a: float | None = None
    battery_power_w: float | None = None
    charge_current_a: float | None = None
    charge_power_w: float | None = None
    alternator_voltage_v: float | None = None
    alternator_current_a: float | None = None
    alternator_power_w: float | None = None
    pv_voltage_v: float | None = None
    pv_current_a: float | None = None
    pv_power_w: float | None = None
    # Derived system view (no shunt: attribution is inferred)
    battery_net_current_a: float | None = None
    solar_current_a: float | None = None
    alternator_current_a: float | None = None
    ac_charger_current_a: float | None = None
    charge_total_current_a: float | None = None
    consumption_current_a: float | None = None
    charge_sources: str | None = None
    load_voltage_v: float | None = None
    load_current_a: float | None = None
    load_power_w: float | None = None
    # Battery detail (native + BT-2)
    battery_temperature_c: float | None = None
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    temperature_avg_c: float | None = None
    temperature_sensor_count: float | None = None
    cell_count: float | None = None
    cell_voltage_min_v: float | None = None
    cell_voltage_max_v: float | None = None
    cell_voltage_avg_v: float | None = None
    cell_voltage_delta_v: float | None = None
    remaining_charge_ah: float | None = None
    capacity_ah: float | None = None
    battery_heating: str | None = None
    # Charger detail
    charging_mode: str | None = None
    battery_min_voltage_today_v: float | None = None
    battery_max_voltage_today_v: float | None = None
    battery_max_current_today_a: float | None = None
    max_charging_power_today_w: float | None = None
    charging_amp_hours_today: float | None = None
    power_generation_today_wh: float | None = None
    power_generation_total_wh: float | None = None
    total_working_days: float | None = None
    count_battery_overdischarged: float | None = None
    count_battery_fully_charged: float | None = None
    battery_ah_total_accumulated: float | None = None
    controller_temperature_c: float | None = None
    raw_registers: tuple[int, ...] = ()

    @classmethod
    def now(cls, site_id: str, device_id: str, device_kind: str, **values: Any) -> "Telemetry":
        return cls(
            site_id=site_id,
            device_id=device_id,
            device_kind=device_kind,
            ts=datetime.now(UTC).isoformat(),
            **values,
        )

    def to_json(self) -> str:
        """Serialize fields only. Raw registers stay local (arrays are not
        valid Telegraf/MQTT numeric fields)."""
        data = asdict(self)
        data.pop("raw_registers", None)
        return json.dumps(data, separators=(",", ":"), sort_keys=True)
