"""Sequential BLE collection cycle for the three discovered devices."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from renogy_monitor.ble.session import BleSession
from renogy_monitor.config import AppConfig, DeviceConfig
from renogy_monitor.modbus.frames import parse_response
from renogy_monitor.modbus.registers import parse_battery_info, parse_dc_charger
from renogy_monitor.telemetry import Telemetry


@dataclass(frozen=True, slots=True)
class CycleResult:
    telemetry: list[Telemetry]
    errors: list[str]


async def collect_device(transport: object, app: AppConfig, device: DeviceConfig) -> Telemetry:
    if device.device_id is None:
        raise ValueError(f"device {device.alias} has no device_id")
    if device.kind == "dc_charger":
        register, words = 256, 30
    elif device.kind in {"battery_bt2", "battery_native"}:
        register, words = 5042, 6
    else:
        raise ValueError(f"unsupported device kind: {device.kind}")
    async with BleSession(
        transport, device.mac, write_char=device.write_char or "0000ffd1-0000-1000-8000-00805f9b34fb",
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
        values["charge_power_w"] = values.get("alternator_power_w", 0) + values.get("pv_power_w", 0)
    else:
        values["battery_power_w"] = values["battery_voltage_v"] * values["battery_current_a"]
    return Telemetry.now(
        app.site_id, device.alias, device.kind,
        raw_registers=response.registers, **{k: v for k, v in values.items() if k in Telemetry.__dataclass_fields__}
    )


async def collect_cycle(transport: object, app: AppConfig) -> CycleResult:
    values: list[Telemetry] = []
    errors: list[str] = []
    for device in app.devices:
        try:
            values.append(await collect_device(transport, app, device))
        except Exception as exc:
            errors.append(f"{device.alias}: {exc}")
        await asyncio.sleep(0)
    return CycleResult(values, errors)
