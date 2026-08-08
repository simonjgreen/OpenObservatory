"""MQTT publisher and Home Assistant discovery (Milestone 6, ADR-022).

Off unless ``Settings.mqtt_enabled`` is set. See ``publisher.py`` for the
runtime and ``discovery.py`` for the Home Assistant entity payloads; both are
pure enough to unit test without a real broker.
"""

from __future__ import annotations

from .publisher import MqttPublisher

__all__ = ["MqttPublisher"]
