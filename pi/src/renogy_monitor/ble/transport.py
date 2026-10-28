"""Abstract BLE transport — protocol code must not import bleak."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class BleError(Exception):
    """BLE I/O failure."""


class BleTimeoutError(BleError):
    """Operation timed out."""


class BleConnectError(BleError):
    """Connection failed."""


NotifyCallback = Callable[[bytes], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class GattCharacteristic:
    uuid: str
    handle: int
    properties: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class GattService:
    uuid: str
    handle: int
    characteristics: tuple[GattCharacteristic, ...]


@dataclass(frozen=True, slots=True)
class BleAdvertisement:
    address: str
    name: str | None
    rssi: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


class BleTransport(ABC):
    """Minimal transport surface used by session + recon tools."""

    @abstractmethod
    async def scan(
        self,
        timeout: float,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> list[BleAdvertisement]:
        """Scan for advertisements. If prefixes set, filter by name prefix."""

    @abstractmethod
    async def connect(self, address: str, timeout: float) -> None:
        """Connect to a peripheral by MAC/address."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect if connected. Always safe to call."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    async def get_services(self) -> tuple[GattService, ...]:
        """Return GATT tree for the connected peripheral."""

    @abstractmethod
    async def start_notify(self, char_uuid: str, callback: NotifyCallback) -> None:
        ...

    @abstractmethod
    async def stop_notify(self, char_uuid: str) -> None:
        ...

    @abstractmethod
    async def write(
        self,
        char_uuid: str,
        data: bytes,
        *,
        response: bool = False,
    ) -> None:
        ...

    @abstractmethod
    async def read(self, char_uuid: str) -> bytes:
        ...
