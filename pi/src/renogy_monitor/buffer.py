"""Small SQLite WAL buffer used when the uplink is unavailable."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from renogy_monitor.telemetry import Telemetry


class TelemetryBuffer:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS telemetry ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
        )
        self.db.commit()

    def add(self, item: Telemetry) -> None:
        self.db.execute("INSERT INTO telemetry(payload) VALUES (?)", (item.to_json(),))
        self.db.commit()

    def pending(self, limit: int = 100) -> list[tuple[int, str]]:
        rows = self.db.execute(
            "SELECT id, payload FROM telemetry ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [(int(row[0]), str(row[1])) for row in rows]

    def acknowledge(self, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        self.db.execute(f"DELETE FROM telemetry WHERE id IN ({marks})", ids)
        self.db.commit()

    def close(self) -> None:
        self.db.close()
