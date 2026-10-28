"""BleSession: connect → use → always disconnect (async context manager)."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

from renogy_monitor.ble.constants import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_READ_TIMEOUT_S,
    NOTIFY_CHAR_UUID,
    WRITE_CHAR_UUID,
)
from renogy_monitor.ble.transport import (
    BleConnectError,
    BleError,
    BleTimeoutError,
    BleTransport,
)
from renogy_monitor.logging_setup import get_logger
from renogy_monitor.modbus.frames import build_read_request

log = get_logger(__name__)


class BleSession:
    """Guarantees disconnect() even on timeout/exception.

    One session = one connection. Sequential polling only — never hold two.
    """

    def __init__(
        self,
        transport: BleTransport,
        address: str,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_S,
        read_timeout: float = DEFAULT_READ_TIMEOUT_S,
        write_char: str = WRITE_CHAR_UUID,
        notify_char: str = NOTIFY_CHAR_UUID,
    ) -> None:
        self._transport = transport
        self.address = address
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_char = write_char
        self.notify_char = notify_char
        self._notify_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._notify_started = False

    async def __aenter__(self) -> Self:
        try:
            await asyncio.wait_for(
                self._transport.connect(self.address, self.connect_timeout),
                timeout=self.connect_timeout,
            )
        except TimeoutError as exc:
            await self._safe_disconnect()
            raise BleTimeoutError(
                f"connect timeout after {self.connect_timeout}s to {self.address}"
            ) from exc
        except BleError:
            await self._safe_disconnect()
            raise
        except Exception as exc:
            await self._safe_disconnect()
            raise BleConnectError(f"connect failed: {exc}") from exc

        try:
            await asyncio.wait_for(
                self._transport.start_notify(self.notify_char, self._on_notify),
                timeout=self.read_timeout,
            )
            self._notify_started = True
        except Exception as exc:
            await self._safe_disconnect()
            raise BleError(f"start_notify failed: {exc}") from exc

        log.info("ble_session_open", address=self.address)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._safe_disconnect()
        log.info("ble_session_closed", address=self.address, error=str(exc) if exc else None)

    async def _on_notify(self, data: bytes) -> None:
        await self._notify_queue.put(bytes(data))

    async def _safe_disconnect(self) -> None:
        if self._notify_started:
            try:
                await asyncio.wait_for(
                    self._transport.stop_notify(self.notify_char),
                    timeout=2.0,
                )
            except Exception as exc:
                log.debug("stop_notify_failed", error=str(exc))
            self._notify_started = False
        try:
            await asyncio.wait_for(self._transport.disconnect(), timeout=5.0)
        except Exception as exc:
            log.warning("disconnect_failed", error=str(exc))

    def _drain_queue(self) -> None:
        while not self._notify_queue.empty():
            try:
                self._notify_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def modbus_read(
        self,
        device_id: int,
        register: int,
        word_count: int,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """Write Modbus read request; return first notify payload (raw bytes).

        Does not parse — recon tools need raw frames even if protocol differs.
        """
        wait = self.read_timeout if timeout is None else timeout
        request = build_read_request(device_id, register, word_count)
        self._drain_queue()

        try:
            await asyncio.wait_for(
                self._transport.write(self.write_char, request, response=False),
                timeout=wait,
            )
        except TimeoutError as exc:
            raise BleTimeoutError(f"write timeout after {wait}s") from exc

        try:
            payload = await asyncio.wait_for(self._notify_queue.get(), timeout=wait)
        except TimeoutError as exc:
            raise BleTimeoutError(
                f"no notify response within {wait}s "
                f"(device_id={device_id} reg={register})"
            ) from exc

        return payload
