# tests/test_drone_models.py
from datetime import datetime, timezone

from utils.drone.models import DroneContact, RFSignal
from utils.drone.signatures import match_signature


def _now():
    return datetime.now(timezone.utc)


def test_drone_contact_to_dict_minimal():
    c = DroneContact(id="abc123", first_seen=_now(), last_seen=_now())
    d = c.to_dict()
    assert d["id"] == "abc123"
    assert d["compliant"] is False
    assert d["risk_level"] == "low"
    assert d["detection_vectors"] == []
    assert d["position"] is None


def test_drone_contact_to_dict_with_position():
    c = DroneContact(id="xyz", first_seen=_now(), last_seen=_now())
    c.position = (51.5, -0.1)
    c.serial_number = "SN001"
    c.compliant = True
    c.detection_vectors = {"REMOTE_ID_WIFI"}
    d = c.to_dict()
    assert d["position"] == [51.5, -0.1]
    assert d["serial_number"] == "SN001"
    assert d["detection_vectors"] == ["REMOTE_ID_WIFI"]


def test_drone_contact_position_history_capped():
    c = DroneContact(id="cap", first_seen=_now(), last_seen=_now())
    for i in range(510):
        c.position_history.append((float(i), float(i), _now()))
    d = c.to_dict()
    # to_dict sends last 50
    assert len(d["position_history"]) == 50


def test_rf_signal_fields():
    s = RFSignal(frequency_hz=433_920_000, protocol="FRSKY", rssi=-65.0, hardware="RTL433", timestamp=_now())
    assert s.frequency_hz == 433_920_000
    assert s.protocol == "FRSKY"


def test_match_signature_frsky_433():
    assert match_signature(433_920_000) == "FRSKY"


def test_match_signature_ocusync_24():
    assert match_signature(2_440_000_000) == "DJI_OCUSYNC"


def test_match_signature_fpv_58():
    assert match_signature(5_800_000_000) == "FPV_VIDEO"


def test_match_signature_ocusync_at_2450mhz():
    # 2,450 MHz is within the DJI_OCUSYNC band
    assert match_signature(2_450_000_000) == "DJI_OCUSYNC"


def test_match_signature_unrecognised():
    assert match_signature(100_000_000) == "UNKNOWN"
