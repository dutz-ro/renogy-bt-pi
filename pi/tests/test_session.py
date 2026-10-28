"""BleSession + FakeTransport tests — no hardware."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from renogy_monitor.ble.constants import NOTIFY_CHAR_UUID, WRITE_CHAR_UUID
from renogy_monitor.ble.fake_transport import (
    FakePeripheral,
    FakeTransport,
    make_modbus_success_response,
)
from renogy_monitor.ble.session import BleSession
from renogy_monitor.ble.transport import BleConnectError, BleTimeoutError
from renogy_monitor.cli import cmd_probe, cmd_scan, cmd_sweep
from renogy_monitor.modbus.frames import parse_response


@pytest.fixture
def charger_payload() -> bytes:
    # 10 registers of dummy telemetry
    import struct

    values = [1320, 1410, 124, 1830, 21, 38, 33, 25, 2, 87]  # scaled ints
    return struct.pack(f">{len(values)}H", *values)


@pytest.fixture
def transport(charger_payload: bytes) -> FakeTransport:
    t = FakeTransport()
    t.add_peripheral(
        FakePeripheral(
            address="AA:BB:CC:DD:EE:01",
            name="BT-TH-ABC123",
            rssi=-55,
            auto_device_id=16,
            auto_payload=charger_payload,
        )
    )
    t.add_peripheral(
        FakePeripheral(
            address="AA:BB:CC:DD:EE:02",
            name="RNG-BAT-01",
            rssi=-70,
            auto_device_id=33,
            auto_payload=charger_payload,
        )
    )
    return t


@pytest.mark.asyncio
async def test_session_modbus_read(transport: FakeTransport) -> None:
    async with BleSession(transport, "AA:BB:CC:DD:EE:01", read_timeout=2.0) as s:
        raw = await s.modbus_read(16, 256, 10)
    resp = parse_response(raw)
    assert not resp.is_exception
    assert resp.device_id == 16
    assert len(resp.registers) == 10
    assert not transport.is_connected()


@pytest.mark.asyncio
async def test_session_disconnects_on_error() -> None:
    t = FakeTransport(fail_connect=True)
    t.add_peripheral(FakePeripheral(address="AA:BB:CC:DD:EE:99", name="BT-TH-X"))
    with pytest.raises(BleConnectError):
        async with BleSession(t, "AA:BB:CC:DD:EE:99", connect_timeout=1.0):
            pass
    assert not t.is_connected()


@pytest.mark.asyncio
async def test_session_timeout_no_response() -> None:
    t = FakeTransport()
    t.add_peripheral(
        FakePeripheral(
            address="AA:BB:CC:DD:EE:03",
            name="BT-TH-SILENT",
            default_response=None,
            auto_device_id=None,
        )
    )
    async with BleSession(t, "AA:BB:CC:DD:EE:03", read_timeout=0.2) as s:
        with pytest.raises(BleTimeoutError):
            await s.modbus_read(1, 256, 2)
    assert not t.is_connected()


@pytest.mark.asyncio
async def test_scan_writes_discovered(transport: FakeTransport, tmp_path: Path) -> None:
    out = StringIO()
    docs = tmp_path / "docs"
    rc = await cmd_scan(transport, timeout=0.1, connect=True, docs_dir=docs, out=out)
    assert rc == 0
    text = (docs / "discovered.md").read_text(encoding="utf-8")
    assert "AA:BB:CC:DD:EE:01" in text
    assert "BT-TH-ABC123" in text
    assert WRITE_CHAR_UUID in text or "ffd1" in text.lower()


@pytest.mark.asyncio
async def test_probe_saves_fixture(
    transport: FakeTransport, tmp_path: Path, charger_payload: bytes
) -> None:
    out = StringIO()
    fixtures = tmp_path / "fixtures"
    rc = await cmd_probe(
        transport,
        mac="AA:BB:CC:DD:EE:01",
        device_id=16,
        register=256,
        words=10,
        alias="charger",
        fixtures_dir=fixtures,
        connect_timeout=2.0,
        read_timeout=2.0,
        write_char=WRITE_CHAR_UUID,
        notify_char=NOTIFY_CHAR_UUID,
        out=out,
    )
    assert rc == 0
    bins = list(fixtures.glob("charger_*.bin"))
    assert len(bins) == 1
    raw = bins[0].read_bytes()
    assert parse_response(raw).device_id == 16
    metas = list(fixtures.glob("charger_*.json"))
    assert len(metas) == 1


@pytest.mark.asyncio
async def test_sweep_finds_device_id(transport: FakeTransport, tmp_path: Path) -> None:
    out = StringIO()
    rc = await cmd_sweep(
        transport,
        mac="AA:BB:CC:DD:EE:01",
        register=256,
        words=10,
        device_ids=(15, 16, 17),
        connect_timeout=2.0,
        read_timeout=0.3,
        write_char=WRITE_CHAR_UUID,
        notify_char=NOTIFY_CHAR_UUID,
        fixtures_dir=tmp_path,
        alias="charger",
        out=out,
    )
    assert rc == 0
    text = out.getvalue()
    assert "Responding device_ids: [16]" in text


@pytest.mark.asyncio
async def test_sweep_survives_garbage_response(tmp_path: Path) -> None:
    t = FakeTransport()
    garbage = b"\xde\xad\xbe\xef\x00\x01\x02\x03"
    t.add_peripheral(
        FakePeripheral(
            address="AA:BB:CC:DD:EE:04",
            name="NATIVE-BAT",
            default_response=garbage,
        )
    )
    out = StringIO()
    rc = await cmd_sweep(
        t,
        mac="AA:BB:CC:DD:EE:04",
        register=256,
        words=2,
        device_ids=(33,),
        connect_timeout=2.0,
        read_timeout=1.0,
        write_char=WRITE_CHAR_UUID,
        notify_char=NOTIFY_CHAR_UUID,
        fixtures_dir=tmp_path,
        alias="native",
        out=out,
    )
    # got a response (even if unparsed)
    assert rc == 0
    assert "deadbeef" in out.getvalue() or "RAW" in out.getvalue()


@pytest.mark.asyncio
async def test_make_modbus_helper() -> None:
    frame = make_modbus_success_response(33, [100, 200, 300])
    resp = parse_response(frame)
    assert resp.registers == (100, 200, 300)
