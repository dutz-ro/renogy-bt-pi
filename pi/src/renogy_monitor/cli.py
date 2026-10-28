"""renogyctl — BLE recon and sequential telemetry collector."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from renogy_monitor.ble.constants import (
    ALIAS_PREFIXES,
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_PROBE_WORDS,
    DEFAULT_READ_TIMEOUT_S,
    DEFAULT_SCAN_TIMEOUT_S,
    DEFAULT_SWEEP_REGISTER,
    NOTIFY_CHAR_UUID,
    SWEEP_DEVICE_IDS,
    WRITE_CHAR_UUID,
)
from renogy_monitor.ble.session import BleSession
from renogy_monitor.ble.transport import BleAdvertisement, BleError, BleTransport, GattService
from renogy_monitor.logging_setup import get_logger, setup_logging
from renogy_monitor.modbus.frames import ModbusError, parse_response
from renogy_monitor.config import load_config
from renogy_monitor.buffer import TelemetryBuffer
from renogy_monitor.cycle import collect_cycle
from renogy_monitor.uplink.mqtt import MqttConfig, MqttUplink

log = get_logger(__name__)

# Repo-relative defaults when installed editable from pi/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DOCS = _REPO_ROOT / "docs"
_DEFAULT_FIXTURES = _REPO_ROOT / "pi" / "tests" / "fixtures"


def _repo_paths() -> tuple[Path, Path]:
    """Resolve docs/ and fixtures/ whether cwd is repo root or pi/."""
    cwd = Path.cwd()
    candidates = [
        (cwd / "docs", cwd / "pi" / "tests" / "fixtures"),
        (cwd.parent / "docs", cwd / "tests" / "fixtures"),
        (_DEFAULT_DOCS, _DEFAULT_FIXTURES),
    ]
    for docs, fixtures in candidates:
        if docs.parent.exists():
            return docs, fixtures
    return _DEFAULT_DOCS, _DEFAULT_FIXTURES


def _safe_alias(name: str | None, address: str) -> str:
    base = (name or "unknown").strip() or "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    if cleaned == "unknown":
        cleaned = "dev_" + address.replace(":", "").lower()[-6:]
    return cleaned


def _find_renogy_chars(
    services: tuple[GattService, ...],
) -> tuple[str | None, str | None]:
    write_uuid: str | None = None
    notify_uuid: str | None = None
    for svc in services:
        for char in svc.characteristics:
            props = set(char.properties)
            uuid_l = char.uuid.lower()
            if (
                write_uuid is None
                and ("write" in props or "write-without-response" in props)
                and ("ffd1" in uuid_l or uuid_l == WRITE_CHAR_UUID)
            ):
                write_uuid = char.uuid
            if (
                notify_uuid is None
                and "notify" in props
                and ("fff1" in uuid_l or uuid_l == NOTIFY_CHAR_UUID)
            ):
                notify_uuid = char.uuid
    # fallback: first write / first notify
    if write_uuid is None or notify_uuid is None:
        for svc in services:
            for char in svc.characteristics:
                props = set(char.properties)
                if write_uuid is None and (
                    "write" in props or "write-without-response" in props
                ):
                    write_uuid = char.uuid
                if notify_uuid is None and "notify" in props:
                    notify_uuid = char.uuid
    return write_uuid, notify_uuid


async def cmd_scan(
    transport: BleTransport,
    *,
    timeout: float,
    connect: bool,
    docs_dir: Path,
    out: TextIO,
) -> int:
    ads = await transport.scan(timeout, name_prefixes=ALIAS_PREFIXES)
    if not ads:
        # second pass: unfiltered, still report Renogy-like or all with names
        log.warning("no_prefix_matches_rescanning_all")
        all_ads = await transport.scan(timeout, name_prefixes=())
        ads = [a for a in all_ads if a.name] or all_ads

    rows: list[dict[str, Any]] = []
    for adv in ads:
        row: dict[str, Any] = {
            "address": adv.address,
            "name": adv.name,
            "rssi": adv.rssi,
            "alias": _safe_alias(adv.name, adv.address),
            "services": [],
            "write_char": None,
            "notify_char": None,
            "error": None,
        }
        if connect:
            try:
                await _scan_gatt(transport, adv, row, timeout)
            except BleError as exc:
                row["error"] = str(exc)
                log.warning("gatt_dump_failed", address=adv.address, error=str(exc))
            except Exception as exc:
                row["error"] = str(exc)
                log.warning("gatt_dump_failed", address=adv.address, error=str(exc))
        rows.append(row)
        _print_adv(out, row)

    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / "discovered.md"
    path.write_text(_render_discovered_md(rows), encoding="utf-8")
    out.write(f"\nWrote {path}\n")
    return 0 if rows else 1


async def _scan_gatt(
    transport: BleTransport,
    adv: BleAdvertisement,
    row: dict[str, Any],
    timeout: float,
) -> None:
    await asyncio.wait_for(transport.connect(adv.address, timeout), timeout=timeout)
    try:
        services = await transport.get_services()
        row["services"] = [
            {
                "uuid": s.uuid,
                "handle": s.handle,
                "characteristics": [
                    {
                        "uuid": c.uuid,
                        "handle": c.handle,
                        "properties": list(c.properties),
                        "description": c.description,
                    }
                    for c in s.characteristics
                ],
            }
            for s in services
        ]
        w, n = _find_renogy_chars(services)
        row["write_char"] = w
        row["notify_char"] = n
    finally:
        await transport.disconnect()


def _print_adv(out: TextIO, row: dict[str, Any]) -> None:
    rssi = row["rssi"] if row["rssi"] is not None else "?"
    out.write(f"{row['address']}  rssi={rssi}  name={row['name']!r}\n")
    if row.get("write_char"):
        out.write(f"  write={row['write_char']}  notify={row['notify_char']}\n")
    if row.get("error"):
        out.write(f"  error={row['error']}\n")
    for svc in row.get("services") or []:
        out.write(f"  service {svc['uuid']}\n")
        for c in svc["characteristics"]:
            out.write(
                f"    char {c['uuid']} [{','.join(c['properties'])}] h={c['handle']}\n"
            )


def _render_discovered_md(rows: list[dict[str, Any]]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Discovered Renogy BLE devices",
        "",
        f"Generated: `{now}`",
        "",
        "Fill in `device_id` and `kind` after `renogyctl sweep` / manual check.",
        "",
    ]
    if not rows:
        lines.append("_No devices found._")
        lines.append("")
        return "\n".join(lines)

    for row in rows:
        lines.append(f"## {row['alias']}")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| MAC | `{row['address']}` |")
        lines.append(f"| Name | `{row['name']}` |")
        lines.append(f"| RSSI | {row['rssi']} |")
        lines.append("| kind | _TODO_ |")
        lines.append("| device_id | _TODO_ |")
        lines.append(f"| write_char | `{row.get('write_char')}` |")
        lines.append(f"| notify_char | `{row.get('notify_char')}` |")
        if row.get("error"):
            lines.append(f"| error | `{row['error']}` |")
        lines.append("")
        if row.get("services"):
            lines.append("### GATT")
            lines.append("")
            # rebuild GattService-like md from dicts
            for svc in row["services"]:
                lines.append(f"- Service `{svc['uuid']}` (handle {svc['handle']})")
                for c in svc["characteristics"]:
                    props = ",".join(c["properties"])
                    lines.append(
                        f"  - Char `{c['uuid']}` handle={c['handle']} props=[{props}]"
                    )
            lines.append("")
    return "\n".join(lines)


async def cmd_probe(
    transport: BleTransport,
    *,
    mac: str,
    device_id: int,
    register: int,
    words: int,
    alias: str | None,
    fixtures_dir: Path,
    connect_timeout: float,
    read_timeout: float,
    write_char: str,
    notify_char: str,
    out: TextIO,
) -> int:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    name = alias or _safe_alias(None, mac)
    ts = datetime.now(UTC)
    stem = f"{name}_id{device_id}_reg{register}_{ts.strftime('%Y%m%dT%H%M%SZ')}"
    bin_path = fixtures_dir / f"{stem}.bin"
    meta_path = fixtures_dir / f"{stem}.json"

    meta: dict[str, Any] = {
        "mac": mac,
        "alias": name,
        "device_id": device_id,
        "register": register,
        "words": words,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "write_char": write_char,
        "notify_char": notify_char,
        "ok": False,
        "error": None,
        "response_hex": None,
        "parsed": None,
    }

    try:
        async with BleSession(
            transport,
            mac,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_char=write_char,
            notify_char=notify_char,
        ) as session:
            raw = await session.modbus_read(device_id, register, words)
    except BleError as exc:
        meta["error"] = str(exc)
        log.error("probe_failed", error=str(exc))
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        out.write(f"FAIL: {exc}\n")
        out.write(f"Wrote metadata {meta_path}\n")
        return 1

    bin_path.write_bytes(raw)
    meta["response_hex"] = raw.hex()
    meta["ok"] = True
    parsed_info: dict[str, Any] | None
    try:
        resp = parse_response(raw)
        parsed_info = {
            "device_id": resp.device_id,
            "function": resp.function,
            "is_exception": resp.is_exception,
            "exception_code": resp.exception_code,
            "registers": list(resp.registers),
            "data_hex": resp.data.hex(),
        }
    except ModbusError as exc:
        parsed_info = {"parse_error": str(exc), "raw_hex": raw.hex()}
        log.warning("probe_raw_unparsed", error=str(exc), raw=raw.hex())

    meta["parsed"] = parsed_info
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    out.write(f"OK raw={raw.hex()}\n")
    out.write(f"Wrote {bin_path}\n")
    out.write(f"Wrote {meta_path}\n")
    if parsed_info:
        out.write(json.dumps(parsed_info, indent=2) + "\n")
    return 0


async def cmd_sweep(
    transport: BleTransport,
    *,
    mac: str,
    register: int,
    words: int,
    device_ids: tuple[int, ...],
    connect_timeout: float,
    read_timeout: float,
    write_char: str,
    notify_char: str,
    fixtures_dir: Path,
    alias: str | None,
    out: TextIO,
) -> int:
    """Try multiple device_ids; never crash on unexpected payloads — log raw."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    name = alias or _safe_alias(None, mac)
    hits: list[dict[str, Any]] = []

    out.write(
        f"sweep mac={mac} register={register} words={words} ids={list(device_ids)}\n"
    )

    try:
        async with BleSession(
            transport,
            mac,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_char=write_char,
            notify_char=notify_char,
        ) as session:
            for did in device_ids:
                result: dict[str, Any] = {
                    "device_id": did,
                    "ok": False,
                    "raw_hex": None,
                    "error": None,
                    "parsed": None,
                }
                try:
                    raw = await session.modbus_read(did, register, words)
                    result["raw_hex"] = raw.hex()
                    result["ok"] = True
                    try:
                        resp = parse_response(raw)
                        result["parsed"] = {
                            "device_id": resp.device_id,
                            "function": resp.function,
                            "is_exception": resp.is_exception,
                            "exception_code": resp.exception_code,
                            "registers": list(resp.registers),
                        }
                        status = (
                            "EXCEPTION"
                            if resp.is_exception
                            else f"OK regs={list(resp.registers)[:4]}..."
                            if resp.registers
                            else "OK"
                        )
                    except ModbusError as exc:
                        result["parsed"] = {"parse_error": str(exc)}
                        status = f"RAW ({exc})"
                    out.write(f"  id={did:3d}  {status}  hex={raw.hex()}\n")
                    # save fixture on any response
                    stem = f"{name}_sweep_id{did}_reg{register}"
                    (fixtures_dir / f"{stem}.bin").write_bytes(raw)
                    meta = {
                        "mac": mac,
                        "alias": name,
                        "device_id": did,
                        "register": register,
                        "words": words,
                        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "response_hex": raw.hex(),
                        "parsed": result["parsed"],
                    }
                    (fixtures_dir / f"{stem}.json").write_text(
                        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
                    )
                    hits.append(result)
                except BleError as exc:
                    result["error"] = str(exc)
                    out.write(f"  id={did:3d}  NO_RESPONSE  ({exc})\n")
                    log.info("sweep_no_response", device_id=did, error=str(exc))
                except Exception as exc:
                    result["error"] = str(exc)
                    out.write(f"  id={did:3d}  ERROR  ({exc})\n")
                    log.warning("sweep_error", device_id=did, error=str(exc))
    except BleError as exc:
        out.write(f"FAIL connect: {exc}\n")
        return 1

    responding = [h for h in hits if h.get("ok")]
    out.write(f"\nResponding device_ids: {[h['device_id'] for h in responding]}\n")
    return 0 if responding else 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="renogyctl",
        description="Renogy BLE monitor control (Phase 0: scan / probe / sweep)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Discover Renogy BLE devices + GATT dump")
    scan.add_argument("--timeout", type=float, default=DEFAULT_SCAN_TIMEOUT_S)
    scan.add_argument(
        "--no-connect",
        action="store_true",
        help="Advertisements only (no GATT connect)",
    )
    scan.add_argument("--docs-dir", type=Path, default=None)

    probe = sub.add_parser("probe", help="Single Modbus read; save raw fixture")
    probe.add_argument("--mac", required=True)
    probe.add_argument("--device-id", type=int, required=True)
    probe.add_argument("--register", type=int, default=DEFAULT_SWEEP_REGISTER)
    probe.add_argument("--words", type=int, default=DEFAULT_PROBE_WORDS)
    probe.add_argument("--alias", default=None)
    probe.add_argument("--fixtures-dir", type=Path, default=None)
    probe.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_S)
    probe.add_argument("--read-timeout", type=float, default=DEFAULT_READ_TIMEOUT_S)
    probe.add_argument("--write-char", default=WRITE_CHAR_UUID)
    probe.add_argument("--notify-char", default=NOTIFY_CHAR_UUID)

    sweep = sub.add_parser("sweep", help="Try common device_ids on one register")
    sweep.add_argument("--mac", required=True)
    sweep.add_argument("--register", type=int, default=DEFAULT_SWEEP_REGISTER)
    sweep.add_argument("--words", type=int, default=DEFAULT_PROBE_WORDS)
    sweep.add_argument(
        "--device-ids",
        default=",".join(str(i) for i in SWEEP_DEVICE_IDS),
        help="Comma-separated device ids",
    )
    sweep.add_argument("--alias", default=None)
    sweep.add_argument("--fixtures-dir", type=Path, default=None)
    sweep.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_S)
    sweep.add_argument("--read-timeout", type=float, default=DEFAULT_READ_TIMEOUT_S)
    sweep.add_argument("--write-char", default=WRITE_CHAR_UUID)
    sweep.add_argument("--notify-char", default=NOTIFY_CHAR_UUID)

    run = sub.add_parser("run", help="Poll configured devices and publish telemetry")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--once", action="store_true")
    run.add_argument("--buffer", type=Path, default=Path("/var/lib/renogy-monitor/telemetry.db"))

    return p


def _make_transport() -> BleTransport:
    from renogy_monitor.ble.bleak_transport import BleakTransport

    return BleakTransport()


async def _async_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    docs_dir, fixtures_dir = _repo_paths()
    transport = _make_transport()

    if args.command == "scan":
        return await cmd_scan(
            transport,
            timeout=args.timeout,
            connect=not args.no_connect,
            docs_dir=args.docs_dir or docs_dir,
            out=sys.stdout,
        )
    if args.command == "probe":
        return await cmd_probe(
            transport,
            mac=args.mac,
            device_id=args.device_id,
            register=args.register,
            words=args.words,
            alias=args.alias,
            fixtures_dir=args.fixtures_dir or fixtures_dir,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
            write_char=args.write_char,
            notify_char=args.notify_char,
            out=sys.stdout,
        )
    if args.command == "sweep":
        ids = tuple(int(x.strip()) for x in args.device_ids.split(",") if x.strip())
        return await cmd_sweep(
            transport,
            mac=args.mac,
            register=args.register,
            words=args.words,
            device_ids=ids,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
            write_char=args.write_char,
            notify_char=args.notify_char,
            fixtures_dir=args.fixtures_dir or fixtures_dir,
            alias=args.alias,
            out=sys.stdout,
        )
    if args.command == "run":
        app = load_config(args.config)
        buffer = TelemetryBuffer(args.buffer)
        uplink = MqttUplink(MqttConfig(**asdict(app.mqtt)), buffer) if app.mqtt else None
        while True:
            result = await collect_cycle(transport, app)
            for item in result.telemetry:
                buffer.add(item)
                print(item.to_json(), flush=True)
            for error in result.errors:
                log.error("device_collection_failed", error=error)
            if uplink is not None:
                uplink.flush()
            if args.once:
                break
            await asyncio.sleep(max(1.0, app.cycle_budget_s))
        buffer.close()
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


def main(argv: list[str] | None = None) -> None:
    try:
        raise SystemExit(asyncio.run(_async_main(argv)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
