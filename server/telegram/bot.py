"""Telegram bot for the Renogy monitor.

Runs on the VPS, reads InfluxDB (already fed by the Pi) and answers queries
about all three devices. Standard library only: no extra dependencies on the
ARMv6 Pi and no heavy image on the VPS.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUX_ORG", "renogy")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telemetry")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Bucharest"))
STATE_DIR = Path(os.environ.get("STATE_DIR", "/data"))
STATE_FILE = STATE_DIR / "state.json"
SOC_ALERT_BELOW = float(os.environ.get("SOC_ALERT_BELOW", "20"))
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "9"))
REPORT_MINUTE = int(os.environ.get("REPORT_MINUTE", "0"))
ALERT_COOLDOWN_H = float(os.environ.get("ALERT_COOLDOWN_H", "3"))

BATTERIES = {"battery_bt2": "Baterie 1 (BT-2)", "battery_native": "Baterie 2 (nativă)"}
DEVICE_NAMES = {
    "dcc50s": "DC-DC / MPPT",
    "battery_bt2": "Baterie 1 (BT-2)",
    "battery_native": "Baterie 2 (nativă)",
    "system": "Sistem",
}


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"chats": [], "soc_alert": {}, "last_report_day": None}


def save_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# telegram
# --------------------------------------------------------------------------
def tg(method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"telegram {method} HTTP {exc.code}: {body[:200]}", flush=True)
        return {"ok": False, "error": body}
    except Exception as exc:  # network hiccup: caller retries
        print(f"telegram {method} failed: {exc}", flush=True)
        return {"ok": False, "error": str(exc)}


def send(chat_id: int | str, text: str) -> None:
    tg(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


# --------------------------------------------------------------------------
# influx
# --------------------------------------------------------------------------
def flux(query: str) -> list[dict]:
    url = f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}"
    req = urllib.request.Request(
        url,
        data=query.encode("utf-8"),
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"influx query failed: {exc}", flush=True)
        return []
    return parse_annotated_csv(raw)


def parse_annotated_csv(raw: str) -> list[dict]:
    rows: list[dict] = []
    header: list[str] | None = None
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        cells = _split_csv(line)
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells, strict=False))
        value = row.get("_value")
        if value in (None, ""):
            continue
        try:
            row["_value"] = float(value)
        except ValueError:
            continue
        rows.append(row)
    return rows


def _split_csv(line: str) -> list[str]:
    out, cur, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def latest_values(windows: str = "-20m") -> dict[str, dict[str, float]]:
    rows = flux(
        f'from(bucket: "{INFLUX_BUCKET}")\n'
        f"  |> range(start: {windows})\n"
        '  |> filter(fn: (r) => r._measurement == "renogy")\n'
        "  |> last()"
    )
    data: dict[str, dict[str, float]] = {}
    for row in rows:
        device = row.get("device_id") or "?"
        data.setdefault(device, {})[row.get("_field", "?")] = row["_value"]
    return data


def window_stats(hours: int) -> dict[str, dict[str, float]]:
    rows = flux(
        f'from(bucket: "{INFLUX_BUCKET}")\n'
        f"  |> range(start: -{hours}h)\n"
        '  |> filter(fn: (r) => r._measurement == "renogy")\n'
        '  |> aggregateWindow(every: 10m, fn: mean, createEmpty: false)'
    )
    stats: dict[str, dict[str, float]] = {}
    for row in rows:
        device = row.get("device_id") or "?"
        field = row.get("_field", "?")
        bucket = stats.setdefault(device, {})
        key_min, key_max, key_sum = f"{field}__min", f"{field}__max", f"{field}__sum"
        value = row["_value"]
        bucket[key_min] = min(bucket.get(key_min, value), value)
        bucket[key_max] = max(bucket.get(key_max, value), value)
        bucket[key_sum] = bucket.get(key_sum, 0.0) + value
        bucket[f"{field}__n"] = bucket.get(f"{field}__n", 0.0) + 1
    return stats


def hours_of(stat: dict[str, float], field: str, sample_minutes: float = 10.0) -> float:
    total = stat.get(f"{field}__sum", 0.0) * (sample_minutes / 60.0)
    return total


def mean_of(stat: dict[str, float], field: str) -> float | None:
    n = stat.get(f"{field}__n", 0.0)
    if not n:
        return None
    return stat.get(f"{field}__sum", 0.0) / n


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def fmt(value: float | None, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{(' ' + unit) if unit else ''}"


def sources_label(values: dict[str, float]) -> str:
    parts = []
    if values.get("solar_current_a", 0) > 0.05:
        parts.append("panouri")
    if values.get("alternator_current_a", 0) > 0.05:
        parts.append("alternator")
    if values.get("ac_charger_current_a", 0) > 0.05:
        parts.append("priză")
    if parts:
        return " + ".join(parts)
    if values.get("battery_net_current_a", 0) < -0.05:
        return "descărcare"
    return "nimic activ"


def status_text() -> str:
    data = latest_values()
    system = data.get("system", {})
    dcc = data.get("dcc50s", {})
    lines = ["<b>⚡ Status acum</b>", ""]

    for device, label in BATTERIES.items():
        dev = data.get(device, {})
        if not dev:
            lines.append(f"{label}: <i>fără date</i>")
            continue
        soc = dev.get("battery_soc_pct")
        volt = dev.get("battery_voltage_v")
        amp = dev.get("battery_current_a")
        state = "încarcă" if (amp or 0) > 0.05 else ("descarcă" if (amp or 0) < -0.05 else "repaus")
        temp = dev.get("temperature_min_c")
        extra = f" · {fmt(temp, 1, '°C')}" if temp is not None else ""
        lines.append(
            f"{label}: <b>{fmt(soc, 1, '%')}</b> · {fmt(volt, 2, 'V')} · "
            f"{fmt(amp, 2, 'A')} ({state}){extra}"
        )

    net = system.get("battery_net_current_a")
    lines.append("")
    lines.append(f"Surse: <b>{sources_label(system)}</b>")
    lines.append(
        "Curent net în baterii: "
        f"<b>{fmt(net, 2, 'A')}</b>"
    )
    lines.append(f"Consum estimat: <b>{fmt(system.get('consumption_current_a'), 2, 'A')}</b>")
    if dcc:
        lines.append(
            f"PV: {fmt(dcc.get('pv_power_w'), 0, 'W')} · "
            f"Alternator: {fmt(dcc.get('alternator_power_w'), 0, 'W')} · "
            f"Temp controller: {fmt(dcc.get('controller_temperature_c'), 0, '°C')}"
        )
    age = latest_age_minutes()
    if age is not None:
        lines.append(f"\n<i>Ultima măsurătoare: acum {age:.0f} min</i>")
    return "\n".join(lines)


def latest_age_minutes() -> float | None:
    rows = flux(
        f'from(bucket: "{INFLUX_BUCKET}")\n'
        "  |> range(start: -6h)\n"
        '  |> filter(fn: (r) => r._measurement == "renogy" and r._field == "battery_soc_pct")\n'
        "  |> last()"
    )
    if not rows:
        return None
    stamps = [_parse_time(r.get("_time", "")) for r in rows]
    stamps = [s for s in stamps if s]
    if not stamps:
        return None
    newest = max(stamps)
    return (datetime.now(TZ) - newest).total_seconds() / 60


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _heating_text(min_temp: float | None, current: float | None) -> str:
    if min_temp is None:
        return "necunoscut"
    if min_temp >= 5.0:
        return "inactivă (peste 5 °C)"
    if (current or 0) > 0.3:
        return "activă (dedus)"
    return "posibil activă"


def _charging_mode(dcc: dict[str, float]) -> str:
    pv = dcc.get("pv_power_w") or 0
    alt = dcc.get("alternator_power_w") or 0
    cur = dcc.get("charge_current_a") or 0
    if pv > 1 and alt > 1:
        return "solar + alternator"
    if pv > 1:
        return "solar (MPPT)"
    if alt > 1:
        return "alternator"
    if cur > 0.05:
        return "priză / extern"
    return "float / repaus"


def batteries_text() -> str:
    data = latest_values()
    lines = ["<b>🔋 Baterii</b>", ""]
    for device, label in BATTERIES.items():
        dev = data.get(device, {})
        if not dev:
            lines.append(f"<b>{label}</b>: fără date")
            lines.append("")
            continue
        lines.append(f"<b>{label}</b>")
        lines.append(f"  SOC: {fmt(dev.get('battery_soc_pct'), 1, '%')}")
        lines.append(f"  Tensiune: {fmt(dev.get('battery_voltage_v'), 2, 'V')}")
        lines.append(f"  Curent: {fmt(dev.get('battery_current_a'), 2, 'A')}")
        lines.append(f"  Putere: {fmt(dev.get('battery_power_w'), 1, 'W')}")
        if dev.get("remaining_charge_ah") is not None:
            lines.append(
                f"  Energie: {fmt(dev.get('remaining_charge_ah'), 1, 'Ah')} "
                f"din {fmt(dev.get('capacity_ah'), 1, 'Ah')}"
            )
        if dev.get("temperature_min_c") is not None:
            lines.append(
                f"  Temperatură: {fmt(dev.get('temperature_min_c'), 1, '°C')} … "
                f"{fmt(dev.get('temperature_max_c'), 1, '°C')} "
                f"(medie {fmt(dev.get('temperature_avg_c'), 1, '°C')}, "
                f"{int(dev.get('temperature_sensor_count') or 0)} senzori)"
            )
        else:
            lines.append(f"  Temperatură: {fmt(dev.get('battery_temperature_c'), 0, '°C')}")
        lines.append(
            f"  Încălzire: {_heating_text(dev.get('temperature_min_c'), dev.get('battery_current_a'))}"
        )
        if dev.get("cell_count"):
            lines.append(
                f"  Celule: {int(dev.get('cell_count'))} × "
                f"{fmt(dev.get('cell_voltage_min_v'), 2, 'V')} … "
                f"{fmt(dev.get('cell_voltage_max_v'), 2, 'V')} "
                f"(delta {fmt(dev.get('cell_voltage_delta_v'), 2, 'V')})"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def charger_text() -> str:
    dcc = latest_values().get("dcc50s", {})
    if not dcc:
        return "Nu am date de la DC-DC / MPPT."
    return "\n".join(
        [
            "<b>🔌 DC-DC + MPPT (RBC50D1S-G1)</b>",
            "",
            f"SOC raportat: {fmt(dcc.get('battery_soc_pct'), 0, '%')}",
            f"Tensiune baterie: {fmt(dcc.get('battery_voltage_v'), 2, 'V')}",
            f"Curent ieșire: {fmt(dcc.get('charge_current_a'), 2, 'A')}",
            f"Putere ieșire: {fmt(dcc.get('charge_power_w'), 1, 'W')}",
            f"Mod încărcare: <b>{_charging_mode(dcc)}</b>",
            "",
            "<b>Panouri (PV)</b>",
            f"  {fmt(dcc.get('pv_voltage_v'), 1, 'V')} · "
            f"{fmt(dcc.get('pv_current_a'), 2, 'A')} · "
            f"{fmt(dcc.get('pv_power_w'), 0, 'W')}",
            "<b>Alternator</b>",
            f"  {fmt(dcc.get('alternator_voltage_v'), 1, 'V')} · "
            f"{fmt(dcc.get('alternator_current_a'), 2, 'A')} · "
            f"{fmt(dcc.get('alternator_power_w'), 0, 'W')}",
            "",
            f"Temp controller: {fmt(dcc.get('controller_temperature_c'), 0, '°C')}",
            f"Temp baterie: {fmt(dcc.get('battery_temperature_c'), 0, '°C')}",
            "",
            "<b>Azi</b>",
            f"  Tensiune min/max: {fmt(dcc.get('battery_min_voltage_today_v'), 2, 'V')} / "
            f"{fmt(dcc.get('battery_max_voltage_today_v'), 2, 'V')}",
            f"  Curent maxim: {fmt(dcc.get('battery_max_current_today_a'), 2, 'A')}",
            f"  Putere maximă: {fmt(dcc.get('max_charging_power_today_w'), 0, 'W')}",
            f"  Ah încărcați: {fmt(dcc.get('charging_amp_hours_today'), 1, 'Ah')}",
            f"  Energie produsă: {fmt(dcc.get('power_generation_today_wh'), 0, 'Wh')}",
            "",
            "<b>Total</b>",
            f"  Energie: {fmt((dcc.get('power_generation_total_wh') or 0) / 1000, 1, 'kWh')}",
            f"  Ah acumulați: {fmt(dcc.get('battery_ah_total_accumulated'), 0, 'Ah')}",
            f"  Zile de funcționare: {fmt(dcc.get('total_working_days'), 0)}",
            f"  Supra-descărcări: {fmt(dcc.get('count_battery_overdischarged'), 0)}",
            f"  Încărcări complete: {fmt(dcc.get('count_battery_fully_charged'), 0)}",
        ]
    )


def energy_text() -> str:
    system = latest_values().get("system", {})
    if not system:
        return "Nu am date de sistem."
    return "\n".join(
        [
            "<b>⚙️ Energie — surse și consum</b>",
            "",
            f"Surse active: <b>{sources_label(system)}</b>",
            f"  Panouri: {fmt(system.get('solar_current_a'), 2, 'A')}",
            f"  Alternator: {fmt(system.get('alternator_current_a'), 2, 'A')}",
            f"  Priză: {fmt(system.get('ac_charger_current_a'), 2, 'A')}",
            "",
            f"Total în baterii: {fmt(system.get('charge_total_current_a'), 2, 'A')}",
            f"Consum: {fmt(system.get('consumption_current_a'), 2, 'A')}",
            f"Curent net: {fmt(system.get('battery_net_current_a'), 2, 'A')}",
        ]
    )


def report_text(hours: int = 24) -> str:
    stats = window_stats(hours)
    system = stats.get("system", {})
    dcc = stats.get("dcc50s", {})
    now = latest_values().get("system", {})

    ah_solar = hours_of(system, "solar_current_a")
    ah_alt = hours_of(system, "alternator_current_a")
    ah_ac = hours_of(system, "ac_charger_current_a")
    ah_net = hours_of(system, "battery_net_current_a")
    ah_consumed = hours_of(system, "consumption_current_a")
    wh_pv = hours_of(dcc, "pv_power_w")
    wh_alt = hours_of(dcc, "alternator_power_w")

    lines = [f"<b>📊 Raport ultimele {hours}h</b>", ""]
    for device, label in BATTERIES.items():
        stat = stats.get(device, {})
        if not stat:
            continue
        lines.append(f"<b>{label}</b>")
        lines.append(
            f"  SOC: min {fmt(stat.get('battery_soc_pct__min'), 1, '%')} · "
            f"max {fmt(stat.get('battery_soc_pct__max'), 1, '%')} · "
            f"mediu {fmt(mean_of(stat, 'battery_soc_pct'), 1, '%')}"
        )
        lines.append(
            f"  Tensiune: min {fmt(stat.get('battery_voltage_v__min'), 2, 'V')} · "
            f"max {fmt(stat.get('battery_voltage_v__max'), 2, 'V')}"
        )
    lines += [
        "",
        "<b>Energie</b>",
        f"  Panouri: {ah_solar:.2f} Ah ({wh_pv:.0f} Wh)",
        f"  Alternator: {ah_alt:.2f} Ah ({wh_alt:.0f} Wh)",
        f"  Priză: {ah_ac:.2f} Ah",
        f"  Net în baterii: {ah_net:+.2f} Ah",
        f"  Consumat: {ah_consumed:.2f} Ah",
        "",
        f"Acum: {fmt(now.get('battery_net_current_a'), 2, 'A')} net · "
        f"{sources_label(now)}",
    ]
    return "\n".join(lines)


HELP = """<b>Comenzi disponibile</b>

/status — rezumat acum (SOC, tensiune, curent, surse)
/baterii — detalii per baterie
/incarcator — DC-DC, MPPT, alternator, temperaturi
/energie — surse de încărcare și consum
/azi — raport de la miezul nopții
/raport — raport ultimele 24h
/ajutor — acest mesaj

Poți scrie și simplu: „status", „baterie", „consum", „raport"."""


# --------------------------------------------------------------------------
# alerts and daily report
# --------------------------------------------------------------------------
def check_soc_alerts(state: dict) -> None:
    data = latest_values("-10m")
    now = time.time()
    changed = False
    for device, label in BATTERIES.items():
        dev = data.get(device)
        if not dev or dev.get("battery_soc_pct") is None:
            continue
        soc = dev["battery_soc_pct"]
        entry = state["soc_alert"].get(device, {})
        if soc < SOC_ALERT_BELOW:
            last = entry.get("last_sent", 0)
            if now - last > ALERT_COOLDOWN_H * 3600:
                text = (
                    f"⚠️ <b>Baterie scăzută</b>\n\n"
                    f"{label}: <b>{soc:.1f}%</b> "
                    f"({fmt(dev.get('battery_voltage_v'), 2, 'V')})\n"
                    f"Prag: {SOC_ALERT_BELOW:.0f}%"
                )
                for chat in state["chats"]:
                    send(chat, text)
                state["soc_alert"][device] = {"last_sent": now, "alerting": True}
                changed = True
        else:
            if entry.get("alerting"):
                text = f"✅ <b>Baterie revenită</b>\n\n{label}: {soc:.1f}%"
                for chat in state["chats"]:
                    send(chat, text)
                state["soc_alert"][device] = {"last_sent": entry.get("last_sent", 0), "alerting": False}
                changed = True
    if changed:
        save_state(state)


def maybe_daily_report(state: dict) -> None:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    if state.get("last_report_day") == today:
        return
    if (now.hour, now.minute) < (REPORT_HOUR, REPORT_MINUTE):
        return
    text = report_text(24)
    for chat in state["chats"]:
        send(chat, text)
    state["last_report_day"] = today
    save_state(state)


# --------------------------------------------------------------------------
# command routing
# --------------------------------------------------------------------------
def handle_command(text: str) -> str:
    cmd = text.strip().lower().lstrip("/").split("@")[0].split()[0] if text.strip() else ""
    if cmd in {"start", "ajutor", "help"}:
        return HELP
    if cmd in {"status", "stare", "s"}:
        return status_text()
    if cmd in {"baterii", "baterie", "soc", "batteries"}:
        return batteries_text()
    if cmd in {"incarcator", "charger", "dcc", "mppt", "incarcare"}:
        return charger_text()
    if cmd in {"energie", "consum", "surse", "energy"}:
        return energy_text()
    if cmd in {"azi", "today"}:
        return report_text(hours=max(1, datetime.now(TZ).hour))
    if cmd in {"raport", "report", "zi"}:
        return report_text(24)
    return "Nu am înțeles. Scrie /ajutor pentru lista comenzilor."


def main() -> None:
    state = load_state()
    print(f"bot started, bound chats: {state['chats']}", flush=True)
    offset = 0
    last_tick = 0.0
    while True:
        try:
            result = tg("getUpdates", {"offset": offset, "timeout": 25}, timeout=40)
            for update in result.get("result", []) or []:
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message") or {}
                chat_id = (message.get("chat") or {}).get("id")
                text = message.get("text") or ""
                if not chat_id or not text:
                    continue
                if chat_id not in state["chats"]:
                    if state["chats"]:
                        print(f"ignored unknown chat {chat_id}", flush=True)
                        continue
                    state["chats"].append(chat_id)
                    save_state(state)
                    print(f"bound new chat {chat_id}", flush=True)
                try:
                    send(chat_id, handle_command(text))
                except Exception as exc:
                    print(f"handler failed: {exc}", flush=True)
        except Exception as exc:
            print(f"polling error: {exc}", flush=True)
            time.sleep(5)

        if time.time() - last_tick > 60:
            last_tick = time.time()
            try:
                check_soc_alerts(state)
                maybe_daily_report(state)
            except Exception as exc:
                print(f"tick failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
