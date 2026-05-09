# tests/test_drone_correlator.py
import queue
import time
from datetime import datetime, timezone

import pytest

from utils.drone.correlator import DroneCorrelator
from utils.drone.models import RemoteIDObservation, RFObservation


def _now():
    return datetime.now(timezone.utc)


def _remote_id_obs(serial="SN001", lat=51.5, lon=-0.1):
    return RemoteIDObservation(
        source="WIFI",
        serial_number=serial,
        operator_id="OP001",
        lat=lat,
        lon=lon,
        altitude_m=50.0,
        speed_ms=5.0,
        heading=90.0,
        timestamp=_now(),
    )


def _rf_obs(freq=433_920_000, proto="FRSKY", rssi=-70.0):
    return RFObservation(
        frequency_hz=freq,
        protocol=proto,
        rssi=rssi,
        hardware="RTL433",
        timestamp=_now(),
    )


@pytest.fixture
def correlator():
    q = queue.Queue()
    return DroneCorrelator(output_queue=q), q


def test_remote_id_creates_contact(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs())
    contacts = corr.get_all()
    assert len(contacts) == 1
    assert contacts[0]["compliant"] is True
    assert contacts[0]["serial_number"] == "SN001"
    assert contacts[0]["position"] == [51.5, -0.1]


def test_rf_creates_contact(correlator):
    corr, q = correlator
    corr.process(_rf_obs())
    contacts = corr.get_all()
    assert len(contacts) == 1
    assert contacts[0]["compliant"] is False


def test_remote_id_emits_sse_event(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs())
    msg = q.get_nowait()
    assert msg["type"] == "contact"
    assert msg["data"]["serial_number"] == "SN001"


def test_same_serial_updates_contact(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs(lat=51.5, lon=-0.1))
    corr.process(_remote_id_obs(lat=51.6, lon=-0.2))
    contacts = corr.get_all()
    assert len(contacts) == 1
    assert contacts[0]["position"] == [51.6, -0.2]


def test_different_serials_create_separate_contacts(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs(serial="SN001"))
    corr.process(_remote_id_obs(serial="SN002"))
    contacts = corr.get_all()
    assert len(contacts) == 2


def test_position_history_grows(correlator):
    corr, q = correlator
    for i in range(5):
        corr.process(_remote_id_obs(lat=51.0 + i * 0.01, lon=-0.1))
    contacts = corr.get_all()
    assert len(contacts[0]["position_history"]) == 5


def test_position_history_capped_at_500(correlator):
    corr, q = correlator
    for i in range(510):
        corr.process(_remote_id_obs(lat=float(i), lon=0.0))
    store_values = list(corr._store.values())
    assert len(store_values[0].position_history) == 500


def test_compliant_single_vector_is_low_risk(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs())
    contacts = corr.get_all()
    assert contacts[0]["risk_level"] == "low"


def test_non_compliant_is_high_risk(correlator):
    corr, q = correlator
    corr.process(_rf_obs())
    contacts = corr.get_all()
    assert contacts[0]["risk_level"] == "high"


def test_confidence_increases_with_vectors(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs())
    contacts = {c["id"]: c for c in corr.get_all()}
    rid_contact = next(c for c in contacts.values() if c["compliant"])
    assert rid_contact["confidence"] == 0.25  # 1/4


def test_ttl_expiry_removes_contact(correlator):
    corr, q = correlator
    corr.process(_remote_id_obs())
    assert len(corr.get_all()) == 1
    for key in corr._store.timestamps:
        corr._store.timestamps[key] = time.time() - 300
    corr._store.cleanup()
    assert len(corr.get_all()) == 0
