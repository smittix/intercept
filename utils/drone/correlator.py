# utils/drone/correlator.py
from __future__ import annotations

import contextlib
import hashlib
import queue
from datetime import datetime, timezone

from utils.cleanup import DataStore, cleanup_manager

from .models import DroneContact, RemoteIDObservation, RFObservation, RFSignal

_CONTACT_TTL = 120.0
_MAX_POSITION_HISTORY = 500


def _contact_id_from_serial(serial: str) -> str:
    return hashlib.sha1(f"serial:{serial}".encode()).hexdigest()[:12]


def _contact_id_from_rf(freq_hz: int, protocol: str) -> str:
    return hashlib.sha1(f"rf:{freq_hz}:{protocol}".encode()).hexdigest()[:12]


def _compute_risk(contact: DroneContact) -> str:
    if not contact.compliant:
        return "high"
    if len(contact.detection_vectors) > 1:
        return "medium"
    if len(contact.rf_signals) >= 2:
        recent = sorted(contact.rf_signals, key=lambda s: s.timestamp)[-5:]
        if abs(recent[-1].rssi - recent[0].rssi) > 15:
            return "medium"
    return "low"


class DroneCorrelator:
    def __init__(self, output_queue: queue.Queue) -> None:
        self._store: DataStore = DataStore(max_age_seconds=_CONTACT_TTL, name="drone_contacts")
        self._output_queue = output_queue
        cleanup_manager.register(self._store)

    def process(self, obs: RemoteIDObservation | RFObservation) -> None:
        now = datetime.now(timezone.utc)

        if isinstance(obs, RemoteIDObservation):
            contact_id = _contact_id_from_serial(obs.serial_number)
            contact: DroneContact = self._store.get(contact_id) or DroneContact(
                id=contact_id, first_seen=now, last_seen=now
            )
            contact.last_seen = now
            contact.serial_number = obs.serial_number
            contact.operator_id = obs.operator_id
            contact.position = (obs.lat, obs.lon)
            contact.altitude_m = obs.altitude_m
            contact.speed_ms = obs.speed_ms
            contact.heading = obs.heading
            contact.compliant = True
            contact.detection_vectors.add(f"REMOTE_ID_{obs.source}")
            contact.position_history.append((obs.lat, obs.lon, now))
            if len(contact.position_history) > _MAX_POSITION_HISTORY:
                contact.position_history = contact.position_history[-_MAX_POSITION_HISTORY:]
        else:
            contact_id = _contact_id_from_rf(obs.frequency_hz, obs.protocol)
            contact = self._store.get(contact_id) or DroneContact(id=contact_id, first_seen=now, last_seen=now)
            contact.last_seen = now
            contact.compliant = False
            contact.detection_vectors.add(obs.hardware)
            contact.rf_signals.append(
                RFSignal(
                    frequency_hz=obs.frequency_hz,
                    protocol=obs.protocol,
                    rssi=obs.rssi,
                    hardware=obs.hardware,
                    timestamp=now,
                )
            )

        contact.confidence = min(len(contact.detection_vectors) / 4.0, 1.0)
        contact.risk_level = _compute_risk(contact)
        self._store.set(contact_id, contact)

        with contextlib.suppress(queue.Full):
            self._output_queue.put_nowait({"type": "contact", "data": contact.to_dict()})

    def get_all(self) -> list[dict]:
        return [c.to_dict() for c in self._store.values()]
