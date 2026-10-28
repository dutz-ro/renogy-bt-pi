"""Unit tests for Modbus RTU framing — no BLE."""

from __future__ import annotations

import pytest

from renogy_monitor.modbus.frames import (
    ModbusCrcError,
    ModbusFrameError,
    build_read_request,
    crc16_modbus,
    parse_response,
)


def test_crc16_known_vector() -> None:
    # Classic Modbus: 01 03 00 00 00 0A → CRC 0xCDC5 → wire bytes C5 CD
    # renogy-bt tables append [crc_high, crc_low] = C5 CD for this request
    body = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x0A])
    crc = crc16_modbus(body)
    assert len(crc) == 2
    assert body + crc == build_read_request(1, 0, 10)


def test_build_read_request_length() -> None:
    req = build_read_request(255, 256, 10)
    assert len(req) == 8
    assert req[0] == 255
    assert req[1] == 0x03
    assert int.from_bytes(req[2:4], "big") == 256
    assert int.from_bytes(req[4:6], "big") == 10
    assert req[-2:] == crc16_modbus(req[:-2])


def test_build_rejects_bad_word_count() -> None:
    with pytest.raises(ValueError):
        build_read_request(1, 0, 0)
    with pytest.raises(ValueError):
        build_read_request(1, 0, 126)


def test_parse_success_response() -> None:
    # device 0x10, FC03, 4 data bytes = 2 registers 0x0528 0x000A
    payload = bytes([0x05, 0x28, 0x00, 0x0A])
    body = bytes([0x10, 0x03, 0x04]) + payload
    frame = body + crc16_modbus(body)
    resp = parse_response(frame)
    assert not resp.is_exception
    assert resp.device_id == 0x10
    assert resp.function == 0x03
    assert resp.registers == (0x0528, 0x000A)


def test_parse_exception() -> None:
    body = bytes([0x10, 0x83, 0x02])  # illegal data address
    frame = body + crc16_modbus(body)
    resp = parse_response(frame)
    assert resp.is_exception
    assert resp.exception_code == 2
    assert resp.registers == ()


def test_parse_crc_error() -> None:
    body = bytes([0x10, 0x03, 0x02, 0x00, 0x01])
    frame = body + b"\x00\x00"
    with pytest.raises(ModbusCrcError):
        parse_response(frame)


def test_parse_too_short() -> None:
    with pytest.raises(ModbusFrameError):
        parse_response(b"\x01\x03")


def test_roundtrip_request_crc() -> None:
    for device_id in (1, 16, 255):
        for reg in (0, 256, 0x1000):
            req = build_read_request(device_id, reg, 8)
            assert crc16_modbus(req[:-2]) == req[-2:]
