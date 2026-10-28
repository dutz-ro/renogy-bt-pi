"""Sequential BLE collection cycle plus charging-source inference.

The hardware has no shunt, so the source split is derived:

* solar and alternator current are reported by the DC-DC charger itself;
* the batteries report the net current flowing into (positive) or out of
  (negative) the bank, measured by each BMS;
* whatever net battery charge is not explained by the DC-DC must come from
  another charger, in practice the mains/AC charger.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from renogy_monitor.ble.bluez import disconnect_device
from renogy_monitor.ble.session import BleSession
from renogy_monitor.config import AppConfig, DeviceConfig
from renogy_monitor.modbus.frames import parse_response
from renogy_monitor.modbus.registers import parse_battery_info, parse_dc_charger
from renogy_monitor.telemetry import Telemetry

BATTERY_KINDS = {"battery_bt2", "battery_native"}
SOURCE_EPSILON_A = 0.05

# Last good sample per device, used to publish a `stale` value instead of a
# hole when a BLE module sleeps or temporarily stops advertising.
_last_good: dict[str, Telemetry] = {}
_LAST_GOOD_PATH = Path("/var/lib/renogy-monitor/last_good.json")


def _load_last_good() -> None:
    try:
        raw = json.loads(_LAST_GOOD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for alias, payload in raw.items():
        try:
            _last_good[alias] = Telemetry(**payload)
        except TypeError:
            continue


def _save_last_good() -> None:
    try:
        _LAST_GOOD_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_GOOD_PATH.write_text(
            json.dumps({a: asdict(t) for a, t in _last_good.items()}, default=list),
            encoding="utf-8",
        )
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class CycleResult:
    telemetry: list[Telemetry]
    errors: list[str]


async def collect_device(transport: object, app: AppConfig, device: DeviceConfig) -> Telemetry:
    if device.device_id is None:
        raise ValueError(f"device {device.alias} has no device_id")
    if device.kind == "dc_charger":
        register, words = 256, 30
    elif device.kind in BATTERY_KINDS:
        register, words = 5042, 6
    else:
        raise ValueError(f"unsupported device kind: {device.kind}")
    # A dangling BlueZ session makes the next connect time out silently.
    await disconnect_device(device.mac)
    async with BleSession(
        transport,
        device.mac,
        write_char=device.write_char or "0000ffd1-0000-1000-8000-00805f9b34fb",
        notify_char=device.notify_char or "0000fff1-0000-1000-8000-00805f9b34fb",
    ) as session:
        raw = await session.modbus_read(device.device_id, register, words, timeout=8)
    response = parse_response(raw)
    if response.is_exception:
        raise ValueError(f"{device.alias}: Modbus exception {response.exception_code}")
    values = (
        parse_dc_charger(response.registers)
        if device.kind == "dc_charger"
        else parse_battery_info(response.registers)
    )
    if device.kind == "dc_charger":
        values["charge_power_w"] = (values.get("alternator_power_w") or 0) + (
            values.get("pv_power_w") or 0
        )
    else:
        values["battery_power_w"] = values["battery_voltage_v"] * values["battery_current_a"]
    return Telemetry.now(
        app.site_id,
        device.alias,
        device.kind,
        raw_registers=response.registers,
        **{k: v for k, v in values.items() if k in Telemetry.__dataclass_fields__},
    )


def derive_system_telemetry(site_id: str, items: list[Telemetry]) -> Telemetry:
    """Combine device samples into one system view with source attribution."""
    batteries = [item for item in items if item.device_kind in BATTERY_KINDS]
    chargers = [item for item in items if item.device_kind == "dc_charger"]

    battery_net_a = sum((b.battery_current_a or 0.0) for b in batteries)
    solar_a = sum((c.pv_current_a or 0.0) for c in chargers)
    alternator_a = sum((c.alternator_current_a or 0.0) for c in chargers)
    dc_charger_out_a = sum((c.charge_current_a or 0.0) for c in chargers)

    # What the DC-DC delivers beyond solar/alternator is not possible: it is
    # their sum. Any surplus net battery charge beyond the DC-DC output can
    # only come from another charger on the bank (mains charger).
    ac_charger_a = max(0.0, battery_net_a - dc_charger_out_a)

    charge_total_a = max(0.0, battery_net_a)
    discharge_a = max(0.0, -battery_net_a)

    active: list[str] = []
    if solar_a > SOURCE_EPSILON_A:
        active.append("solar")
    if alternator_a > SOURCE_EPSILON_A:
        active.append("alternator")
    if ac_charger_a > SOURCE_EPSILON_A:
        active.append("priza")
    sources = "+".join(active) if active else ("descarcare" if discharge_a > 0 else "idle")

    return Telemetry.now(
        site_id,
        "system",
        "system",
        battery_net_current_a=round(battery_net_a, 2),
        solar_current_a=round(solar_a, 2),
        alternator_current_a=round(alternator_a, 2),
        ac_charger_current_a=round(ac_charger_a, 2),
        charge_total_current_a=round(charge_total_a, 2),
        consumption_current_a=round(discharge_a, 2),
        charge_sources=sources,
    )


async def collect_cycle(transport: object, app: AppConfig) -> CycleResult:
    if not _last_good:
        _load_last_good()
    values: list[Telemetry] = []
    errors: list[str] = []
    for device in app.devices:
        sample: Telemetry | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                sample = await collect_device(transport, app, device)
                break
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(2)
        if sample is not None:
            _last_good[device.alias] = sample
            _save_last_good()
            values.append(sample)
        else:
            errors.append(f"{device.alias}: {last_error}")
            cached = _last_good.get(device.alias)
            if cached is not None:
                values.append(replace(cached, ts=Telemetry.now("", "", "").ts, stale=True))
        await asyncio.sleep(0)
    if values and app.devices:
        values.append(derive_system_telemetry(app.site_id, values))
    return CycleResult(values, errors)
