"""Configuration via dataclasses + PyYAML (no pydantic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DeviceConfig:
    alias: str
    mac: str
    kind: str  # dc_charger | battery_bt2 | battery_native
    device_id: int | None = None
    write_char: str | None = None
    notify_char: str | None = None


@dataclass
class AppConfig:
    site_id: str = "default"
    cycle_budget_s: float = 60.0
    devices: list[DeviceConfig] = field(default_factory=list)
    log_level: str = "INFO"
    mqtt: "MqttConfig | None" = None


@dataclass
class MqttConfig:
    host: str
    port: int = 8883
    username: str | None = None
    password: str | None = None
    ca_file: str | None = None
    client_id: str = "renogy-pi"


def load_config(path: Path | str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    devices_raw = raw.get("devices") or []
    devices: list[DeviceConfig] = []
    for item in devices_raw:
        if not isinstance(item, dict):
            continue
        devices.append(
            DeviceConfig(
                alias=str(item["alias"]),
                mac=str(item["mac"]),
                kind=str(item.get("kind", "unknown")),
                device_id=_opt_int(item.get("device_id")),
                write_char=_opt_str(item.get("write_char")),
                notify_char=_opt_str(item.get("notify_char")),
            )
        )
    return AppConfig(
        site_id=str(raw.get("site_id", "default")),
        cycle_budget_s=float(raw.get("cycle_budget_s", 60.0)),
        devices=devices,
        log_level=str(raw.get("log_level", "INFO")),
        mqtt=_mqtt_config(raw.get("mqtt")),
    )


def _mqtt_config(value: Any) -> MqttConfig | None:
    if not isinstance(value, dict) or not value.get("host"):
        return None
    return MqttConfig(
        host=str(value["host"]),
        port=int(value.get("port", 8883)),
        username=_opt_str(value.get("username")),
        password=_opt_str(value.get("password")),
        ca_file=_opt_str(value.get("ca_file")),
        client_id=str(value.get("client_id", "renogy-pi")),
    )


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
