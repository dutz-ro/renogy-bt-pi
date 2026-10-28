"""MQTT uplink with QoS 1 and SQLite backlog draining."""

from __future__ import annotations

import json
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from renogy_monitor.buffer import TelemetryBuffer
from renogy_monitor.telemetry import Telemetry
from renogy_monitor.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MqttConfig:
    host: str
    port: int = 8883
    username: str | None = None
    password: str | None = None
    ca_file: str | None = None
    client_id: str = "renogy-pi"


class MqttUplink:
    """Buffers every sample locally, then tries to drain the whole backlog.

    The buffer is the source of truth: nothing is deleted until the broker
    confirms the QoS 1 publish for that row.
    """

    def __init__(self, config: MqttConfig, buffer: TelemetryBuffer) -> None:
        self.config = config
        self.buffer = buffer

    def _client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=self.config.client_id
        )
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        if self.config.ca_file:
            client.tls_set(ca_certs=self.config.ca_file)
        return client

    def flush(self) -> bool:
        """Publish pending rows. Returns True when the backlog is empty."""
        rows = self.buffer.pending(limit=500)
        if not rows:
            return True

        client = self._client()
        delivered: list[int] = []
        try:
            client.connect(self.config.host, self.config.port, keepalive=30)
            client.loop_start()
            for row_id, payload in rows:
                parsed = json.loads(payload)
                topic = f"renogy/{parsed['site_id']}/{parsed['device_id']}/state"
                info = client.publish(topic, payload, qos=1, retain=True)
                info.wait_for_publish(timeout=10)
                if info.is_published():
                    delivered.append(row_id)
                else:
                    log.warning("mqtt_publish_unconfirmed", row=row_id, rc=info.rc)
        except OSError as exc:
            log.warning("mqtt_unreachable", error=str(exc))
            return False
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # pragma: no cover - best effort cleanup
                pass

        self.buffer.acknowledge(delivered)
        log.info("mqtt_flush", sent=len(delivered), pending=len(rows) - len(delivered))
        return len(delivered) == len(rows)

    def publish(self, item: Telemetry) -> bool:
        """Convenience wrapper: buffer one sample and drain."""
        self.buffer.add(item)
        return self.flush()
