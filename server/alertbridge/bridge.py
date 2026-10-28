"""Minimal Grafana webhook -> ntfy bridge (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

NTFY_URL = os.environ.get("NTFY_URL", "http://ntfy:80")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "renogy-alerts")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"message": raw.decode("utf-8", "replace")}

        alerts = payload.get("alerts") or [payload]
        for alert in alerts:
            labels = alert.get("labels") or {}
            title = alert.get("title") or payload.get("title") or labels.get("alertname", "Renogy alert")
            message = alert.get("message") or payload.get("message") or ""

            body = {
                "topic": NTFY_TOPIC,
                "title": str(title),
                "message": str(message),
                "priority": 4 if labels.get("severity") == "critical" else 3,
                "tags": ["battery"],
            }
            request = urllib.request.Request(
                NTFY_URL.rstrip("/"),
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
            except Exception as exc:  # keep Grafana delivery non-fatal
                print(f"ntfy publish failed: {exc}", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt: str, *args: object) -> None:
        print(fmt % args, flush=True)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
