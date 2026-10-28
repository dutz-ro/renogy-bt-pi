"""BlueZ helpers via D-Bus (no bluetoothctl, no root required).

The native Renogy battery stops accepting new connections when a previous
session is left dangling in BlueZ. Disconnecting the device object first makes
the next connect reliable, which is what the phone app effectively does.
"""

from __future__ import annotations

from dbus_fast import Message
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType

from renogy_monitor.logging_setup import get_logger

log = get_logger(__name__)

_HCI = "hci0"


def device_path(mac: str) -> str:
    return f"/org/bluez/{_HCI}/dev_{mac.replace(':', '_').upper()}"


async def _call(mac: str, member: str) -> bool:
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=device_path(mac),
                interface="org.bluez.Device1",
                member=member,
            )
        )
        if reply.message_type.name == "ERROR":
            log.debug("bluez_call_error", mac=mac, member=member, error=reply.error_name)
            return False
        return True
    except Exception as exc:
        log.debug("bluez_call_failed", mac=mac, member=member, error=str(exc))
        return False
    finally:
        if bus is not None:
            try:
                bus.disconnect()
            except Exception:
                pass


async def disconnect_device(mac: str) -> bool:
    """Force-disconnect so a new BLE session can start cleanly."""
    ok = await _call(mac, "Disconnect")
    if ok:
        log.debug("bluez_disconnected", mac=mac)
    return ok


async def connect_device(mac: str) -> bool:
    """Ask BlueZ to connect; useful as a wake-up for non-advertising devices."""
    ok = await _call(mac, "Connect")
    if ok:
        log.info("bluez_connected", mac=mac)
    return ok
