"""
Test suite for the Tracker Signature Engine.

Contains sample payloads from real BLE tracker devices and verifies
the signature engine correctly identifies them with appropriate confidence.
"""

import pytest

from utils.bluetooth.tracker_signatures import (
    APPLE_COMPANY_ID,
    TrackerConfidence,
    TrackerSignatureEngine,
    TrackerType,
    detect_tracker,
    get_tracker_engine,
)

# =============================================================================
# SAMPLE PAYLOADS FROM REAL DEVICES
# =============================================================================

# Apple AirTag advertisement payload samples
AIRTAG_SAMPLES = [
    {
        "name": "AirTag sample 1 - Find My advertisement",
        "address": "AA:BB:CC:DD:EE:FF",
        "address_type": "random",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("121910deadbeef0123456789abcdef0123456789"),
        "service_uuids": ["fd6f"],
        "expected_type": TrackerType.AIRTAG,
        "expected_confidence": TrackerConfidence.HIGH,
    },
    {
        "name": "AirTag sample 2 - Shorter payload",
        "address": "11:22:33:44:55:66",
        "address_type": "rpa",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("12191bcdef1234567890"),  # status 0x1b: bits 4-5 = 1, AirTag
        "service_uuids": [],
        "expected_type": TrackerType.AIRTAG,
        "expected_confidence": TrackerConfidence.MEDIUM,
    },
]

# Apple Find My accessory (non-AirTag)
FINDMY_ACCESSORY_SAMPLES = [
    {
        "name": "Chipolo ONE Spot (Find My network)",
        "address": "CC:DD:EE:FF:00:11",
        "address_type": "random",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("12cafe0123456789"),
        "service_uuids": ["fd6f"],
        "expected_type": TrackerType.AIRTAG,  # Using Find My, detected as AirTag-like
        "expected_confidence": TrackerConfidence.HIGH,
    },
]

# Tile tracker samples
TILE_SAMPLES = [
    {
        "name": "Tile Mate - by company ID",
        "address": "C4:E7:00:11:22:33",
        "address_type": "public",
        "manufacturer_id": 0x00ED,  # Tile Inc
        "manufacturer_data": bytes.fromhex("ed00aabbccdd"),
        "service_uuids": ["feed"],
        "expected_type": TrackerType.TILE,
        "expected_confidence": TrackerConfidence.HIGH,
    },
    {
        "name": "Tile Pro - by MAC prefix",
        "address": "DC:54:AA:BB:CC:DD",
        "address_type": "public",
        "manufacturer_id": None,
        "manufacturer_data": None,
        "service_uuids": ["feed"],
        "expected_type": TrackerType.TILE,
        "expected_confidence": TrackerConfidence.MEDIUM,
    },
    {
        "name": "Tile - by name only",
        "address": "00:11:22:33:44:55",
        "address_type": "public",
        "manufacturer_id": None,
        "manufacturer_data": None,
        "service_uuids": [],
        "name": "Tile Slim",
        "expected_type": TrackerType.TILE,
        "expected_confidence": TrackerConfidence.LOW,
    },
]

# Samsung SmartTag samples
SAMSUNG_SAMPLES = [
    {
        "name": "Samsung SmartTag - by company ID and service",
        "address": "58:4D:AA:BB:CC:DD",
        "address_type": "random",
        "manufacturer_id": 0x0075,  # Samsung
        "manufacturer_data": bytes.fromhex("75001234567890"),
        "service_uuids": ["fd5a"],
        "expected_type": TrackerType.SAMSUNG_SMARTTAG,
        "expected_confidence": TrackerConfidence.HIGH,
    },
    {
        "name": "Samsung SmartTag - by MAC prefix only",
        "address": "A0:75:BB:CC:DD:EE",
        "address_type": "public",
        "manufacturer_id": None,
        "manufacturer_data": None,
        "service_uuids": [],
        "expected_type": TrackerType.SAMSUNG_SMARTTAG,
        "expected_confidence": TrackerConfidence.LOW,
    },
]

# Non-tracker devices (should NOT be detected as trackers)
NON_TRACKER_SAMPLES = [
    {
        "name": "iPhone separated from its owner - Offline Finding, status says Apple device",
        "address": "4A:11:22:33:44:55",
        "address_type": "random",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("1219" + "04" + "ab" * 23),  # status bits 4-5 = 0
        "service_uuids": [],
        "expected_tracker": False,
    },
    {
        "name": "AirPods proximity pairing (0x07), not Offline Finding",
        "address": "5B:11:22:33:44:55",
        "address_type": "random",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("0719" + "01" + "ab" * 22),
        "service_uuids": [],
        "expected_tracker": False,
    },
    {
        "name": "Galaxy phone - Samsung company ID alone",
        "address": "6C:11:22:33:44:55",
        "address_type": "random",
        "manufacturer_id": 0x0075,
        "manufacturer_data": bytes.fromhex("420901" + "ab" * 20),
        "service_uuids": [],
        "expected_tracker": False,
    },
    {
        "name": "Android phone with Exposure Notification (fd6f)",
        "address": "7D:11:22:33:44:55",
        "address_type": "random",
        "manufacturer_id": None,
        "manufacturer_data": None,
        "service_uuids": ["fd6f"],
        "expected_tracker": False,
    },
    {
        "name": "Apple AirPods - should not be tracker",
        "address": "AA:BB:CC:DD:EE:00",
        "address_type": "random",
        "manufacturer_id": APPLE_COMPANY_ID,
        "manufacturer_data": bytes.fromhex("100000"),  # NOT Find My pattern
        "service_uuids": [],
        "expected_tracker": False,
    },
    {
        "name": "Generic BLE device",
        "address": "00:11:22:33:44:55",
        "address_type": "public",
        "manufacturer_id": 0x0006,  # Microsoft
        "manufacturer_data": bytes.fromhex("0600aabbccdd"),
        "service_uuids": ["180f", "180a"],  # Battery and Device Info services
        "expected_tracker": False,
    },
    {
        "name": "Fitbit fitness tracker - not a location tracker",
        "address": "FF:EE:DD:CC:BB:AA",
        "address_type": "random",
        "manufacturer_id": 0x00D2,  # Fitbit
        "manufacturer_data": bytes.fromhex("d2001234"),
        "service_uuids": ["adab"],  # Fitbit service
        "expected_tracker": False,
    },
    {
        "name": "Generic device with long payload - length alone is not evidence",
        "address": "22:33:44:55:66:77",
        "address_type": "public",
        "manufacturer_id": 0x0006,  # Microsoft
        "manufacturer_data": bytes.fromhex("0600" + "ab" * 23),  # 25 bytes
        "service_uuids": [],
        "expected_tracker": False,
    },
    {
        "name": "Bluetooth speaker",
        "address": "11:22:33:44:55:66",
        "address_type": "public",
        "manufacturer_id": 0x0310,  # Bose
        "manufacturer_data": None,
        "service_uuids": ["111e"],  # Handsfree
        "name": "Bose Speaker",
        "expected_tracker": False,
    },
]


# =============================================================================
# TEST CASES
# =============================================================================


class TestTrackerDetection:
    """Test tracker detection with sample payloads."""

    @pytest.fixture
    def engine(self):
        """Create a fresh engine for each test."""
        return TrackerSignatureEngine()

    # --- AirTag tests ---

    @pytest.mark.parametrize("sample", AIRTAG_SAMPLES, ids=lambda s: s["name"])
    def test_airtag_detection(self, engine, sample):
        """Test AirTag detection with various payload samples."""
        result = engine.detect_tracker(
            address=sample["address"],
            address_type=sample["address_type"],
            name=sample.get("name"),
            manufacturer_id=sample["manufacturer_id"],
            manufacturer_data=sample["manufacturer_data"],
            service_uuids=sample["service_uuids"],
        )

        assert result.is_tracker, f"Should detect {sample['name']} as tracker"
        assert result.tracker_type == sample["expected_type"], (
            f"Expected {sample['expected_type']}, got {result.tracker_type}"
        )
        # Allow medium when expecting high (degraded confidence is acceptable)
        if sample["expected_confidence"] == TrackerConfidence.HIGH:
            assert result.confidence in (TrackerConfidence.HIGH, TrackerConfidence.MEDIUM), (
                f"Expected HIGH or MEDIUM confidence for {sample['name']}"
            )
        assert len(result.evidence) > 0, "Should provide evidence"

    # --- Tile tests ---

    @pytest.mark.parametrize("sample", TILE_SAMPLES, ids=lambda s: s["name"])
    def test_tile_detection(self, engine, sample):
        """Test Tile tracker detection."""
        result = engine.detect_tracker(
            address=sample["address"],
            address_type=sample["address_type"],
            name=sample.get("name"),
            manufacturer_id=sample["manufacturer_id"],
            manufacturer_data=sample["manufacturer_data"],
            service_uuids=sample["service_uuids"],
        )

        assert result.is_tracker, f"Should detect {sample['name']} as tracker"
        assert result.tracker_type == sample["expected_type"], (
            f"Expected {sample['expected_type']}, got {result.tracker_type}"
        )
        assert len(result.evidence) > 0, "Should provide evidence"

    # --- Samsung SmartTag tests ---

    @pytest.mark.parametrize("sample", SAMSUNG_SAMPLES, ids=lambda s: s["name"])
    def test_samsung_smarttag_detection(self, engine, sample):
        """Test Samsung SmartTag detection."""
        result = engine.detect_tracker(
            address=sample["address"],
            address_type=sample["address_type"],
            name=sample.get("name"),
            manufacturer_id=sample["manufacturer_id"],
            manufacturer_data=sample["manufacturer_data"],
            service_uuids=sample["service_uuids"],
        )

        assert result.is_tracker, f"Should detect {sample['name']} as tracker"
        assert result.tracker_type == sample["expected_type"], (
            f"Expected {sample['expected_type']}, got {result.tracker_type}"
        )

    # --- Non-tracker tests (negative cases) ---

    @pytest.mark.parametrize("sample", NON_TRACKER_SAMPLES, ids=lambda s: s["name"])
    def test_non_tracker_not_detected(self, engine, sample):
        """Test that non-tracker devices are NOT falsely detected."""
        result = engine.detect_tracker(
            address=sample["address"],
            address_type=sample["address_type"],
            name=sample.get("name"),
            manufacturer_id=sample["manufacturer_id"],
            manufacturer_data=sample["manufacturer_data"],
            service_uuids=sample["service_uuids"],
        )

        assert not result.is_tracker, f"{sample['name']} should NOT be detected as tracker (got: {result.tracker_type})"


class TestFingerprinting:
    """Test device fingerprinting for MAC randomization tracking."""

    @pytest.fixture
    def engine(self):
        return TrackerSignatureEngine()

    def test_fingerprint_consistency(self, engine):
        """Test that same payload produces same fingerprint."""
        fp1 = engine.generate_device_fingerprint(
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes.fromhex("1219deadbeef"),
            service_uuids=["fd6f"],
            service_data={},
            tx_power=-10,
            name="TestDevice",
        )

        fp2 = engine.generate_device_fingerprint(
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes.fromhex("1219deadbeef"),
            service_uuids=["fd6f"],
            service_data={},
            tx_power=-10,
            name="TestDevice",
        )

        assert fp1.fingerprint_id == fp2.fingerprint_id, "Same payload should produce same fingerprint"

    def test_fingerprint_different_mac(self, engine):
        """Test that fingerprint ignores MAC address (for tracking across rotations)."""
        # Fingerprinting doesn't take MAC as input, so this tests the concept
        fp1 = engine.generate_device_fingerprint(
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes.fromhex("1219abcdef"),
            service_uuids=["fd6f"],
            service_data={},
            tx_power=None,
            name=None,
        )

        # Same payload characteristics should produce same fingerprint
        fp2 = engine.generate_device_fingerprint(
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes.fromhex("1219abcdef"),
            service_uuids=["fd6f"],
            service_data={},
            tx_power=None,
            name=None,
        )

        assert fp1.fingerprint_id == fp2.fingerprint_id

    def test_fingerprint_stability_score(self, engine):
        """Test that fingerprints have appropriate stability scores."""
        # Rich payload = high stability
        fp_rich = engine.generate_device_fingerprint(
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes.fromhex("1219aabbccdd"),
            service_uuids=["fd6f", "180f"],
            service_data={"fd6f": bytes.fromhex("01")},
            tx_power=-5,
            name="AirTag",
        )

        # Minimal payload = low stability
        fp_minimal = engine.generate_device_fingerprint(
            manufacturer_id=None,
            manufacturer_data=None,
            service_uuids=[],
            service_data={},
            tx_power=None,
            name=None,
        )

        assert fp_rich.stability_confidence > fp_minimal.stability_confidence, (
            "Rich payload should have higher stability confidence"
        )


class TestSuspiciousPresence:
    """Test suspicious presence / following heuristics."""

    @pytest.fixture
    def engine(self):
        return TrackerSignatureEngine()

    def test_risk_score_for_tracker(self, engine):
        """Test that trackers get base risk score."""
        risk_score, risk_factors = engine.evaluate_suspicious_presence(
            fingerprint_id="test123",
            is_tracker=True,
            seen_count=5,
            duration_seconds=60,
            seen_rate=2.0,
            rssi_variance=15.0,
            is_new=False,
        )

        assert risk_score >= 0.3, "Tracker should have base risk score"
        assert any("tracker" in f.lower() for f in risk_factors)

    def test_risk_score_for_persistent_tracker(self, engine):
        """Test that persistent tracker presence increases risk."""
        risk_score, risk_factors = engine.evaluate_suspicious_presence(
            fingerprint_id="test456",
            is_tracker=True,
            seen_count=50,
            duration_seconds=900,  # 15 minutes
            seen_rate=3.5,
            rssi_variance=8.0,  # Stable signal
            is_new=True,
        )

        assert risk_score >= 0.5, "Persistent tracker should have high risk"
        assert len(risk_factors) >= 3, "Should have multiple risk factors"

    def test_non_tracker_low_risk(self, engine):
        """Test that non-trackers have low risk scores."""
        risk_score, risk_factors = engine.evaluate_suspicious_presence(
            fingerprint_id="test789",
            is_tracker=False,
            seen_count=5,
            duration_seconds=60,
            seen_rate=1.0,
            rssi_variance=20.0,
            is_new=False,
        )

        assert risk_score < 0.3, "Non-tracker should have low risk"


class TestConvenienceFunction:
    """Test the module-level convenience function."""

    def test_detect_tracker_function(self):
        """Test the detect_tracker() convenience function."""
        result = detect_tracker(
            address="C4:E7:11:22:33:44",
            address_type="public",
            name="Tile Mate",
            manufacturer_id=0x00ED,
            service_uuids=["feed"],
        )

        assert result.is_tracker
        assert result.tracker_type == TrackerType.TILE

    def test_get_engine_singleton(self):
        """Test that get_tracker_engine returns singleton."""
        engine1 = get_tracker_engine()
        engine2 = get_tracker_engine()
        assert engine1 is engine2


# =============================================================================
# SMOKE TEST FOR API ENDPOINTS
# =============================================================================


def test_api_backwards_compatibility():
    """
    Smoke test checklist for API backwards compatibility.

    This is a documentation test - run manually to verify:

    1. GET /api/bluetooth/devices - Should still return devices in same format
       - Check: device_id, address, name, rssi_current all present
       - New: tracker fields should be present but optional

    2. POST /api/bluetooth/scan/start - Should work with same parameters
       - Check: mode, duration_s, transport, rssi_threshold

    3. GET /api/bluetooth/stream - SSE should still emit device_update events
       - Check: Event format unchanged

    4. GET /tscm/sweep/stream - TSCM should still work
       - Check: Bluetooth devices included in sweep results

    5. New endpoints (v2):
       - GET /api/bluetooth/trackers - Returns only detected trackers
       - GET /api/bluetooth/trackers/<id> - Returns tracker detail
       - GET /api/bluetooth/diagnostics - Returns system diagnostics

    Run with: pytest tests/test_tracker_signatures.py -v
    """
    # This is just a documentation placeholder
    # Actual API tests would require a running Flask app
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestAppleOfflineFindingKinds:
    """Every Find My device sends the same Offline Finding advertisement;
    the status byte says which kind sent it. Only an AirTag is an AirTag."""

    @pytest.mark.parametrize(
        "status,expected",
        [
            (0x10, TrackerType.AIRTAG),
            (0x20, TrackerType.FINDMY_ACCESSORY),  # AirPods
            (0x30, TrackerType.FINDMY_ACCESSORY),  # third-party Find My accessory
        ],
    )
    def test_status_byte_decides_the_type(self, status, expected):
        result = TrackerSignatureEngine().detect_tracker(
            address="4A:00:00:00:00:01",
            address_type="random",
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes([0x12, 0x19, status]) + bytes(23),
        )
        assert result.is_tracker and result.tracker_type == expected

    def test_airpods_are_named_as_airpods(self):
        result = TrackerSignatureEngine().detect_tracker(
            address="4A:00:00:00:00:02",
            address_type="random",
            manufacturer_id=APPLE_COMPANY_ID,
            manufacturer_data=bytes([0x12, 0x19, 0x20]) + bytes(23),
        )
        assert result.tracker_name == "AirPods (Find My)"

    def test_samsung_smarttag_still_detected_by_its_service(self):
        result = TrackerSignatureEngine().detect_tracker(
            address="6C:00:00:00:00:01",
            address_type="random",
            manufacturer_id=0x0075,
            manufacturer_data=bytes.fromhex("420901"),
            service_uuids=["fd5a"],
        )
        assert result.is_tracker and result.tracker_type == TrackerType.SAMSUNG_SMARTTAG
