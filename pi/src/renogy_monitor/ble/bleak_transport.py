"""Bleak-backed BleTransport. Only module that may import bleak."""

from __future__ import annotations

from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from renogy_monitor.ble.constants import ALIAS_PREFIXES
from renogy_monitor.ble.transport import (
    BleAdvertisement,
    BleConnectError,
    BleError,
    BleTransport,
    GattCharacteristic,
    GattService,
    NotifyCallback,
)
from renogy_monitor.logging_setup import get_logger

log = get_logger(__name__)


def _name_matches(name: str | None, prefixes: tuple[str, ...]) -> bool:
    if not name:
        return False
    stripped = name.strip()
    return any(stripped.startswith(p) for p in prefixes)


class BleakTransport(BleTransport):
    """Real BLE I/O via BlueZ (Linux) / CoreBluetooth."""

    def __init__(self) -> None:
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._notify_cb: NotifyCallback | None = None

    async def scan(
        self,
        timeout: float,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> list[BleAdvertisement]:
        prefixes = name_prefixes if name_prefixes is not None else ALIAS_PREFIXES
        log.info("ble_scan_start", timeout=timeout, prefixes=list(prefixes))
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

        results: list[BleAdvertisement] = []
        # devices: dict[str, tuple[BLEDevice, AdvertisementData]]
        for _addr, (dev, adv) in devices.items():
            name = dev.name or adv.local_name
            if prefixes and not _name_matches(name, prefixes):
                continue
            rssi = adv.rssi
            results.append(
                BleAdvertisement(
                    address=dev.address,
                    name=name,
                    rssi=rssi,
                    metadata={
                        "uuids": list(adv.service_uuids) if adv.service_uuids else [],
                        "manufacturer_data": {
                            str(k): v.hex() for k, v in (adv.manufacturer_data or {}).items()
                        },
                    },
                )
            )
        results.sort(key=lambda a: a.rssi if a.rssi is not None else -999, reverse=True)
        log.info("ble_scan_done", count=len(results))
        return results

    async def connect(self, address: str, timeout: float) -> None:
        if self._client is not None and self._client.is_connected:
            raise BleError("already connected — disconnect first (sequential only)")

        log.info("ble_connect", address=address, timeout=timeout)
        self._client = BleakClient(address, timeout=timeout)
        try:
            await self._client.connect()
        except Exception as exc:
            self._client = None
            raise BleConnectError(f"failed to connect to {address}: {exc}") from exc

        if not self._client.is_connected:
            self._client = None
            raise BleConnectError(f"not connected after connect() to {address}")

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self._notify_cb = None
        if client is None:
            return
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception as exc:
            log.warning("ble_disconnect_error", error=str(exc))

    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _require_client(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise BleError("not connected")
        return self._client

    async def get_services(self) -> tuple[GattService, ...]:
        client = self._require_client()
        services: list[GattService] = []
        for service in client.services:
            chars: list[GattCharacteristic] = []
            for char in service.characteristics:
                props = tuple(sorted(char.properties))
                desc = ""
                if char.description:
                    desc = str(char.description)
                chars.append(
                    GattCharacteristic(
                        uuid=str(char.uuid),
                        handle=int(char.handle),
                        properties=props,
                        description=desc,
                    )
                )
            services.append(
                GattService(
                    uuid=str(service.uuid),
                    handle=int(service.handle),
                    characteristics=tuple(chars),
                )
            )
        return tuple(services)

    async def start_notify(self, char_uuid: str, callback: NotifyCallback) -> None:
        client = self._require_client()
        self._notify_cb = callback

        async def _handler(_sender: Any, data: bytearray) -> None:
            cb = self._notify_cb
            if cb is None:
                return
            result = cb(bytes(data))
            if result is not None:
                await result

        # Some native Renogy batteries expose the same UUID more than once.
        # Bleak cannot resolve a duplicated UUID string, so select the
        # characteristic object by its notify property (and therefore handle).
        await client.start_notify(self._resolve_characteristic(char_uuid, "notify"), _handler)

    async def stop_notify(self, char_uuid: str) -> None:
        client = self._client
        self._notify_cb = None
        if client is None or not client.is_connected:
            return
        try:
            await client.stop_notify(self._resolve_characteristic(char_uuid, "notify"))
        except Exception as exc:
            log.debug("stop_notify_error", error=str(exc))

    async def write(
        self,
        char_uuid: str,
        data: bytes,
        *,
        response: bool = False,
    ) -> None:
        client = self._require_client()
        await client.write_gatt_char(
            self._resolve_characteristic(char_uuid, "write"), bytearray(data), response=response
        )

    async def read(self, char_uuid: str) -> bytes:
        client = self._require_client()
        data = await client.read_gatt_char(self._resolve_characteristic(char_uuid, "read"))
        return bytes(data)

    def _resolve_characteristic(self, uuid: str, preferred_property: str) -> Any:
        """Resolve a UUID to a unique characteristic object.

        Bleak's UUID lookup raises when a peripheral has duplicate UUIDs.
        Renogy's native battery firmware does this, so prefer a characteristic
        that supports the operation and fall back to the first UUID match.
        """
        client = self._require_client()
        wanted = uuid.lower()
        matches = [
            char
            for service in client.services
            for char in service.characteristics
            if str(char.uuid).lower() == wanted
        ]
        if not matches:
            return uuid
        if len(matches) == 1:
            return matches[0]
        for char in matches:
            if preferred_property in set(char.properties):
                return char
        return matches[0]
