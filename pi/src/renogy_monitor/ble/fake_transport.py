"""FakeTransport — replay captured frames for tests / offline recon."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from renogy_monitor.ble.transport import (
    BleAdvertisement,
    BleConnectError,
    BleError,
    BleTransport,
    GattCharacteristic,
    GattService,
    NotifyCallback,
)


@dataclass
class FakePeripheral:
    address: str
    name: str
    rssi: int = -60
    services: tuple[GattService, ...] = ()
    # map request hex -> response bytes
    responses: dict[str, bytes] = field(default_factory=dict)
    # fallback: any write gets this notify (or None = silence)
    default_response: bytes | None = None
    # device_id that answers (for auto modbus replies built from register map)
    auto_device_id: int | None = None
    auto_payload: bytes | None = None  # register data bytes for FC03


def _default_renogy_services() -> tuple[GattService, ...]:
    from renogy_monitor.ble.constants import (
        NOTIFY_CHAR_UUID,
        NOTIFY_SERVICE_UUID,
        WRITE_CHAR_UUID,
        WRITE_SERVICE_UUID,
    )

    return (
        GattService(
            uuid=WRITE_SERVICE_UUID,
            handle=1,
            characteristics=(
                GattCharacteristic(
                    uuid=WRITE_CHAR_UUID,
                    handle=2,
                    properties=("write-without-response", "write"),
                ),
            ),
        ),
        GattService(
            uuid=NOTIFY_SERVICE_UUID,
            handle=3,
            characteristics=(
                GattCharacteristic(
                    uuid=NOTIFY_CHAR_UUID,
                    handle=4,
                    properties=("notify",),
                ),
            ),
        ),
    )


class FakeTransport(BleTransport):
    """In-memory BLE for unit tests. No hardware."""

    def __init__(
        self,
        peripherals: list[FakePeripheral] | None = None,
        *,
        connect_delay: float = 0.0,
        fail_connect: bool = False,
    ) -> None:
        self._peripherals = {p.address.upper(): p for p in (peripherals or [])}
        self._connected: FakePeripheral | None = None
        self._notify_cb: NotifyCallback | None = None
        self.connect_delay = connect_delay
        self.fail_connect = fail_connect
        self.writes: list[tuple[str, bytes]] = []

    def add_peripheral(self, peripheral: FakePeripheral) -> None:
        if not peripheral.services:
            peripheral.services = _default_renogy_services()
        self._peripherals[peripheral.address.upper()] = peripheral

    async def scan(
        self,
        timeout: float,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> list[BleAdvertisement]:
        await asyncio.sleep(0)  # yield
        out: list[BleAdvertisement] = []
        for p in self._peripherals.values():
            if name_prefixes and (
                not p.name or not any(p.name.startswith(x) for x in name_prefixes)
            ):
                continue
            out.append(
                BleAdvertisement(address=p.address, name=p.name, rssi=p.rssi)
            )
        return out

    async def connect(self, address: str, timeout: float) -> None:
        if self.fail_connect:
            raise BleConnectError("simulated connect failure")
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        key = address.upper()
        p = self._peripherals.get(key)
        if p is None:
            raise BleConnectError(f"unknown address {address}")
        self._connected = p

    async def disconnect(self) -> None:
        self._connected = None
        self._notify_cb = None

    def is_connected(self) -> bool:
        return self._connected is not None

    async def get_services(self) -> tuple[GattService, ...]:
        if self._connected is None:
            raise BleError("not connected")
        if not self._connected.services:
            return _default_renogy_services()
        return self._connected.services

    async def start_notify(self, char_uuid: str, callback: NotifyCallback) -> None:
        if self._connected is None:
            raise BleError("not connected")
        self._notify_cb = callback

    async def stop_notify(self, char_uuid: str) -> None:
        self._notify_cb = None

    async def write(
        self,
        char_uuid: str,
        data: bytes,
        *,
        response: bool = False,
    ) -> None:
        if self._connected is None:
            raise BleError("not connected")
        self.writes.append((char_uuid, data))
        p = self._connected
        reply: bytes | None = p.responses.get(data.hex())
        if reply is None and p.auto_device_id is not None and p.auto_payload is not None:
            reply = self._auto_modbus_reply(data, p)
        if reply is None:
            reply = p.default_response
        if reply is not None and self._notify_cb is not None:
            result = self._notify_cb(reply)
            if result is not None:
                await result

    def _auto_modbus_reply(self, request: bytes, p: FakePeripheral) -> bytes | None:
        """If request is FC03 for auto_device_id, synthesize a valid response."""
        if p.auto_device_id is None or p.auto_payload is None:
            return None
        if len(request) < 8:
            return None
        try:
            # validate request CRC by round-trip expectations
            device_id = request[0]
            function = request[1]
            if function != 0x03:
                return None
            if device_id not in (p.auto_device_id, 255):
                return None
            word_count = int.from_bytes(request[4:6], "big")
            payload = p.auto_payload
            need = word_count * 2
            if len(payload) < need:
                payload = payload + bytes(need - len(payload))
            payload = payload[:need]
            import struct

            from renogy_monitor.modbus.frames import crc16_modbus

            body = struct.pack(">BBB", p.auto_device_id, 0x03, len(payload)) + payload
            return body + crc16_modbus(body)
        except Exception:
            return None

    async def read(self, char_uuid: str) -> bytes:
        if self._connected is None:
            raise BleError("not connected")
        return b""


def make_modbus_success_response(device_id: int, register_values: list[int]) -> bytes:
    """Helper for tests: FC03 response with given uint16 values."""
    import struct

    from renogy_monitor.modbus.frames import crc16_modbus

    payload = struct.pack(f">{len(register_values)}H", *register_values)
    body = struct.pack(">BBB", device_id, 0x03, len(payload)) + payload
    return body + crc16_modbus(body)
