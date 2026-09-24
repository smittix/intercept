"""
Tracker Signature Engine for BLE device classification.

Detects Apple AirTag, Find My accessories, Tile trackers, Samsung SmartTag,
and other known BLE trackers based on manufacturer data patterns, service UUIDs,
and advertising payload analysis.

This module provides reliable tracker detection that:
1. Works with MAC randomization (uses payload fingerprinting)
2. Provides confidence scores and evidence for each match
3. Does NOT claim certainty - provides "indicators" not proof
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger("intercept.bluetooth.tracker_signatures")


# =============================================================================
# TRACKER TYPES
# =============================================================================


class TrackerType(str, Enum):
    """Known tracker device types."""

    AIRTAG = "airtag"
    FINDMY_ACCESSORY = "findmy_accessory"
    TILE = "tile"
    SAMSUNG_SMARTTAG = "samsung_smarttag"
    CHIPOLO = "chipolo"
    PEBBLEBEE = "pebblebee"
    NUTFIND = "nutfind"
    ORBIT = "orbit"
    EUFY = "eufy"
    CUBE = "cube"
    UNKNOWN_TRACKER = "unknown_tracker"
    NOT_A_TRACKER = "not_a_tracker"


class TrackerConfidence(str, Enum):
    """Confidence level for tracker detection."""

    HIGH = "high"  # Multiple strong indicators match
    MEDIUM = "medium"  # Some indicators match
    LOW = "low"  # Weak indicators, needs investigation
    NONE = "none"  # Not detected as tracker


# =============================================================================
# TRACKER SIGNATURES DATABASE
# =============================================================================

# Apple Manufacturer ID
APPLE_COMPANY_ID = 0x004C

# Apple Find My / AirTag advertisement types (first byte of manufacturer data after company ID)
APPLE_FINDMY_ADV_TYPE = 0x12  # Find My network advertisement
APPLE_NEARBY_ADV_TYPE = 0x10  # Nearby action
APPLE_AIRTAG_ADV_PATTERN = bytes([0x12, 0x19])  # Offline Finding, separated from owner
APPLE_FINDMY_PREFIX_SHORT = bytes([0x12])  # Offline Finding (any length)
APPLE_CONTINUITY_SERVICE_UUID = "d0611e78-bbb4-4591-a5f8-487910ae4366"

# Every Find My device sends the same Offline Finding (0x12) advertisement:
# iPhones, iPads and Macs, AirPods, AirTags and third-party accessories. Bits
# 4-5 of its status byte (the byte after type and length) say which kind of
# device sent it; the mapping is the one AirGuard (TU Darmstadt, Heinrich et
# al.) uses to tell AirTags from the rest.
APPLE_OF_DEVICE_KINDS = {0: "apple_device", 1: "airtag", 2: "airpods", 3: "findmy_accessory"}

# 0xFD6F is the Exposure Notification service (COVID contact tracing), used
# by Android phones and iPhones alike. It is not Find My, and not evidence
# of a tracker.
EXPOSURE_NOTIFICATION_SERVICE_UUID = "fd6f"

# Companies whose ID appears on phones, earbuds, TVs and much else besides
# trackers: their ID alone says nothing about whether a device is a tracker.
MULTI_PRODUCT_COMPANY_IDS = {0x004C, 0x0075}  # Apple, Samsung

# Tile
TILE_COMPANY_ID = 0x00ED  # Tile Inc
TILE_ALT_COMPANY_ID = 0x038F  # Alternative Tile ID
TILE_SERVICE_UUID = "feed"  # Tile service UUID (16-bit)
TILE_MAC_PREFIXES = ["C4:E7", "DC:54", "E4:B0", "F8:8A", "E6:43", "90:32", "D0:72"]

# Samsung SmartTag
SAMSUNG_COMPANY_ID = 0x0075
SMARTTAG_SERVICE_UUID = "fd5a"  # SmartThings Find service
SMARTTAG_MAC_PREFIXES = ["58:4D", "A0:75", "B8:D7", "50:32"]

# Chipolo
CHIPOLO_COMPANY_ID = 0x0A09
CHIPOLO_SERVICE_UUID = "feaa"  # Eddystone beacon (used by some Chipolo)
CHIPOLO_ALT_SERVICE = "feb1"

# PebbleBee
PEBBLEBEE_SERVICE_UUID = "feab"
PEBBLEBEE_MAC_PREFIXES = ["D4:3D", "E0:E5"]

# Other known trackers
NUTFIND_COMPANY_ID = 0x0A09
EUFY_COMPANY_ID = 0x0590

# Generic beacon patterns that may indicate a tracker
BEACON_SERVICE_UUIDS = [
    "feaa",  # Eddystone
    "feab",  # Nokia beacon
    "feb1",  # Dialog Semiconductor
    "febe",  # Bose
]


@dataclass
class TrackerSignature:
    """Defines a tracker signature pattern."""

    tracker_type: TrackerType
    name: str
    description: str
    company_id: int | None = None
    company_ids: list[int] = field(default_factory=list)
    manufacturer_data_prefixes: list[bytes] = field(default_factory=list)
    service_uuids: list[str] = field(default_factory=list)
    service_data_prefixes: dict[str, bytes] = field(default_factory=dict)
    mac_prefixes: list[str] = field(default_factory=list)
    name_patterns: list[str] = field(default_factory=list)
    min_manufacturer_data_len: int = 0
    confidence_boost: float = 0.0  # Extra confidence for specific patterns


# Tracker signatures database
TRACKER_SIGNATURES: list[TrackerSignature] = [
    # Apple AirTag
    TrackerSignature(
        tracker_type=TrackerType.AIRTAG,
        name="Apple AirTag",
        description="Apple AirTag tracking device using Find My network",
        company_id=APPLE_COMPANY_ID,
        manufacturer_data_prefixes=[
            APPLE_AIRTAG_ADV_PATTERN,
            APPLE_FINDMY_PREFIX_SHORT,
        ],
        name_patterns=["airtag"],
        min_manufacturer_data_len=22,  # AirTags have 22+ byte payloads
        confidence_boost=0.2,
    ),
    # Apple Find My Accessory (non-AirTag)
    TrackerSignature(
        tracker_type=TrackerType.FINDMY_ACCESSORY,
        name="Find My Accessory",
        description="Third-party Apple Find My network accessory",
        company_id=APPLE_COMPANY_ID,
        manufacturer_data_prefixes=[
            APPLE_FINDMY_PREFIX_SHORT,
        ],
        name_patterns=["findmy", "find my", "chipolo one spot", "belkin"],
    ),
    # Tile
    TrackerSignature(
        tracker_type=TrackerType.TILE,
        name="Tile Tracker",
        description="Tile Bluetooth tracker",
        company_ids=[TILE_COMPANY_ID, TILE_ALT_COMPANY_ID],
        service_uuids=[TILE_SERVICE_UUID],
        mac_prefixes=TILE_MAC_PREFIXES,
        name_patterns=["tile"],
    ),
    # Samsung SmartTag
    TrackerSignature(
        tracker_type=TrackerType.SAMSUNG_SMARTTAG,
        name="Samsung SmartTag",
        description="Samsung SmartThings tracker",
        company_id=SAMSUNG_COMPANY_ID,
        service_uuids=[SMARTTAG_SERVICE_UUID],
        mac_prefixes=SMARTTAG_MAC_PREFIXES,
        name_patterns=["smarttag", "smart tag", "galaxy tag"],
    ),
    # Chipolo
    TrackerSignature(
        tracker_type=TrackerType.CHIPOLO,
        name="Chipolo",
        description="Chipolo Bluetooth tracker",
        company_id=CHIPOLO_COMPANY_ID,
        service_uuids=[CHIPOLO_SERVICE_UUID, CHIPOLO_ALT_SERVICE],
        name_patterns=["chipolo"],
    ),
    # PebbleBee
    TrackerSignature(
        tracker_type=TrackerType.PEBBLEBEE,
        name="PebbleBee",
        description="PebbleBee Bluetooth tracker",
        service_uuids=[PEBBLEBEE_SERVICE_UUID],
        mac_prefixes=PEBBLEBEE_MAC_PREFIXES,
        name_patterns=["pebblebee", "pebble bee", "honey"],
    ),
    # Eufy
    TrackerSignature(
        tracker_type=TrackerType.EUFY,
        name="Eufy SmartTrack",
        description="Eufy/Anker smart tracker",
        company_id=EUFY_COMPANY_ID,
        name_patterns=["eufy", "smarttrack"],
    ),
]


# =============================================================================
# TRACKER DETECTION RESULT
# =============================================================================


@dataclass
class TrackerDetectionResult:
    """Result of tracker detection analysis."""

    is_tracker: bool = False
    tracker_type: TrackerType = TrackerType.NOT_A_TRACKER
    tracker_name: str = ""
    confidence: TrackerConfidence = TrackerConfidence.NONE
    confidence_score: float = 0.0  # 0.0 to 1.0
    evidence: list[str] = field(default_factory=list)
    matched_signature: str | None = None

    # For suspicious presence heuristics
    risk_factors: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 to 1.0

    # Raw data used for detection
    manufacturer_id: int | None = None
    manufacturer_data_hex: str | None = None
    service_uuids_found: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "is_tracker": self.is_tracker,
            "tracker_type": self.tracker_type.value if self.tracker_type else None,
            "tracker_name": self.tracker_name,
            "confidence": self.confidence.value if self.confidence else None,
            "confidence_score": round(self.confidence_score, 2),
            "evidence": self.evidence,
            "matched_signature": self.matched_signature,
            "risk_factors": self.risk_factors,
            "risk_score": round(self.risk_score, 2),
            "manufacturer_id": self.manufacturer_id,
            "manufacturer_data_hex": self.manufacturer_data_hex,
            "service_uuids_found": self.service_uuids_found,
        }


# =============================================================================
# DEVICE FINGERPRINT (survives MAC randomization)
# =============================================================================


@dataclass
class DeviceFingerprint:
    """
    Stable fingerprint for a BLE device that can survive MAC randomization.

    Uses stable parts of the advertising payload to create a probabilistic
    identity. This is NOT perfect - randomized devices may produce different
    fingerprints over time. Document this as a limitation.
    """

    fingerprint_id: str  # SHA256 hash of stable features

    # Features used for fingerprinting
    manufacturer_id: int | None = None
    manufacturer_data_prefix: bytes | None = None  # First 4 bytes (stable across MACs)
    manufacturer_data_length: int = 0
    service_uuids: list[str] = field(default_factory=list)
    service_data_keys: list[str] = field(default_factory=list)
    tx_power_bucket: str | None = None  # "high"/"medium"/"low"
    name_hint: str | None = None

    # Confidence in this fingerprint's stability
    stability_confidence: float = 0.5  # 0.0-1.0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "fingerprint_id": self.fingerprint_id,
            "manufacturer_id": self.manufacturer_id,
            "manufacturer_data_prefix": self.manufacturer_data_prefix.hex() if self.manufacturer_data_prefix else None,
            "manufacturer_data_length": self.manufacturer_data_length,
            "service_uuids": self.service_uuids,
            "service_data_keys": self.service_data_keys,
            "tx_power_bucket": self.tx_power_bucket,
            "name_hint": self.name_hint,
            "stability_confidence": round(self.stability_confidence, 2),
        }


def generate_fingerprint(
    manufacturer_id: int | None,
    manufacturer_data: bytes | None,
    service_uuids: list[str],
    service_data: dict[str, bytes],
    tx_power: int | None,
    name: str | None,
) -> DeviceFingerprint:
    """
    Generate a stable fingerprint for a BLE device.

    Fingerprint is based on stable parts of the advertising payload that
    typically persist across MAC address rotations.

    Limitations:
    - Devices that fully randomize their payload will not be consistently tracked
    - Some devices change manufacturer data patterns periodically
    - Best for trackers which have consistent advertising patterns
    """
    # Build fingerprint features
    features = []
    stability_score = 0.0

    mfr_prefix = None
    mfr_length = 0

    if manufacturer_id is not None:
        features.append(f"mfr:{manufacturer_id:04x}")
        stability_score += 0.2

    if manufacturer_data:
        mfr_length = len(manufacturer_data)
        features.append(f"mfr_len:{mfr_length}")
        stability_score += 0.1

        # First 4 bytes of manufacturer data are often stable
        mfr_prefix = manufacturer_data[: min(4, len(manufacturer_data))]
        features.append(f"mfr_pfx:{mfr_prefix.hex()}")
        stability_score += 0.2

    sorted_uuids = sorted(service_uuids)
    if sorted_uuids:
        features.append(f"uuids:{','.join(sorted_uuids)}")
        stability_score += 0.2

    sd_keys = sorted(service_data.keys())
    if sd_keys:
        features.append(f"sd_keys:{','.join(sd_keys)}")
        stability_score += 0.1

    # TX power bucket
    tx_bucket = None
    if tx_power is not None:
        if tx_power >= 0:
            tx_bucket = "high"
        elif tx_power >= -10:
            tx_bucket = "medium"
        else:
            tx_bucket = "low"
        features.append(f"tx:{tx_bucket}")
        stability_score += 0.05

    # Name hint (for devices that advertise names)
    name_hint = None
    if name:
        # Only use first word of name (often stable)
        name_hint = name.split()[0].lower() if name else None
        if name_hint:
            features.append(f"name:{name_hint}")
            stability_score += 0.15

    # Generate fingerprint ID
    feature_str = "|".join(features)
    fingerprint_id = hashlib.sha256(feature_str.encode()).hexdigest()[:16]

    return DeviceFingerprint(
        fingerprint_id=fingerprint_id,
        manufacturer_id=manufacturer_id,
        manufacturer_data_prefix=mfr_prefix,
        manufacturer_data_length=mfr_length,
        service_uuids=sorted_uuids,
        service_data_keys=sd_keys,
        tx_power_bucket=tx_bucket,
        name_hint=name_hint,
        stability_confidence=min(1.0, stability_score),
    )


# =============================================================================
# TRACKER DETECTION ENGINE
# =============================================================================


class TrackerSignatureEngine:
    """
    Engine for detecting known BLE trackers from advertising data.

    Detection is based on multiple indicators:
    1. Manufacturer ID matching known tracker companies
    2. Manufacturer data patterns specific to tracker types
    3. Service UUID matching known tracker services
    4. MAC address prefix matching known tracker OUIs
    5. Device name pattern matching

    Confidence is cumulative - more matching indicators = higher confidence.
    """

    def __init__(self):
        self.signatures = TRACKER_SIGNATURES

        # Tracking for suspicious presence detection
        self._sighting_history: dict[str, list[datetime]] = {}
        self._fingerprint_cache: dict[str, DeviceFingerprint] = {}

    def detect_tracker(
        self,
        address: str,
        address_type: str,
        name: str | None = None,
        manufacturer_id: int | None = None,
        manufacturer_data: bytes | None = None,
        service_uuids: list[str] | None = None,
        service_data: dict[str, bytes] | None = None,
        tx_power: int | None = None,
    ) -> TrackerDetectionResult:
        """
        Analyze a BLE device for tracker indicators.

        Returns a TrackerDetectionResult with:
        - is_tracker: True if any tracker indicators match
        - tracker_type: The most likely tracker type
        - confidence: HIGH/MEDIUM/LOW based on indicator strength
        - evidence: List of matching indicators for transparency

        IMPORTANT: This is heuristic detection. A match indicates
        the device RESEMBLES a known tracker, not proof it IS one.
        """
        result = TrackerDetectionResult()
        service_uuids = service_uuids or []
        service_data = service_data or {}

        # Store raw data in result for transparency
        result.manufacturer_id = manufacturer_id
        if manufacturer_data:
            result.manufacturer_data_hex = manufacturer_data.hex()
        result.service_uuids_found = service_uuids

        # Normalize service UUIDs to lowercase 16-bit format where possible
        normalized_uuids = self._normalize_service_uuids(service_uuids)

        # An Apple Offline Finding advertisement says what kind of device sent it
        of_kind = apple_offline_finding_kind(manufacturer_id, manufacturer_data)

        # Score each signature
        best_match = None
        best_score = 0.0
        best_evidence = []

        for signature in self.signatures:
            if of_kind and not self._of_kind_fits(of_kind, signature.tracker_type):
                continue
            score, evidence = self._score_signature(
                signature=signature,
                address=address,
                name=name,
                manufacturer_id=manufacturer_id,
                manufacturer_data=manufacturer_data,
                normalized_uuids=normalized_uuids,
                service_data=service_data,
            )

            if score > best_score:
                best_score = score
                best_match = signature
                best_evidence = evidence

        # Check for generic tracker indicators if no specific match. An Apple
        # device that said what it is has been classified already.
        if best_score < 0.3 and of_kind is None:
            generic_score, generic_evidence = self._check_generic_tracker_indicators(
                address=address,
                address_type=address_type,
                manufacturer_id=manufacturer_id,
                manufacturer_data=manufacturer_data,
                normalized_uuids=normalized_uuids,
            )
            if generic_score > best_score:
                best_score = generic_score
                best_match = None
                best_evidence = generic_evidence

        # Build result
        if best_score >= 0.3:  # Minimum threshold for tracker detection
            result.is_tracker = True
            result.confidence_score = min(1.0, best_score)
            result.evidence = best_evidence

            if best_match:
                result.tracker_type = best_match.tracker_type
                result.tracker_name = "AirPods (Find My)" if of_kind == "airpods" else best_match.name
                result.matched_signature = best_match.name
                if of_kind:
                    result.evidence.append(f"Offline Finding status byte identifies the sender as: {of_kind}")
            else:
                result.tracker_type = TrackerType.UNKNOWN_TRACKER
                result.tracker_name = "Unknown Tracker"

            # Determine confidence level
            if best_score >= 0.7:
                result.confidence = TrackerConfidence.HIGH
            elif best_score >= 0.5:
                result.confidence = TrackerConfidence.MEDIUM
            else:
                result.confidence = TrackerConfidence.LOW

        return result

    def _score_signature(
        self,
        signature: TrackerSignature,
        address: str,
        name: str | None,
        manufacturer_id: int | None,
        manufacturer_data: bytes | None,
        normalized_uuids: list[str],
        service_data: dict[str, bytes],
    ) -> tuple[float, list[str]]:
        """Score how well a device matches a tracker signature."""
        score = 0.0
        evidence = []

        # Check company ID
        # For Apple, company ID alone is NOT enough - require additional indicators
        # Many Apple devices (AirPods, Watch, etc.) share the same manufacturer ID
        company_id_matches = False
        if manufacturer_id is not None:
            if signature.company_id == manufacturer_id or manufacturer_id in signature.company_ids:
                company_id_matches = True

        # A multi-product company's ID counts only alongside that company's
        # tracker-specific signal: Find My (0x12) data for Apple, the
        # SmartThings Find service for Samsung. On its own it matched every
        # Galaxy phone and pair of Buds as a SmartTag.
        if company_id_matches:
            if manufacturer_id in MULTI_PRODUCT_COMPANY_IDS:
                has_findmy_pattern = bool(manufacturer_data) and manufacturer_data[0] == APPLE_FINDMY_ADV_TYPE
                has_tracker_service = any(uuid.lower() in normalized_uuids for uuid in signature.service_uuids)
                specific = has_findmy_pattern if manufacturer_id == APPLE_COMPANY_ID else has_tracker_service
                if specific:
                    score += 0.35
                    evidence.append(f"Manufacturer ID 0x{manufacturer_id:04X} matches {signature.name}")
            else:
                # Non-Apple trackers - company ID is strong evidence
                score += 0.35
                evidence.append(f"Manufacturer ID 0x{manufacturer_id:04X} matches {signature.name}")

        # Check manufacturer data prefix (high weight for specific patterns)
        if manufacturer_data and signature.manufacturer_data_prefixes:
            for prefix in signature.manufacturer_data_prefixes:
                if manufacturer_data.startswith(prefix):
                    score += 0.30
                    evidence.append(f"Manufacturer data pattern matches {signature.name}")
                    break

        # Check manufacturer data length (corroborative - only counts alongside
        # an identifying indicator, mirroring _check_generic_tracker_indicators)
        if manufacturer_data and signature.min_manufacturer_data_len > 0 and score > 0:
            if len(manufacturer_data) >= signature.min_manufacturer_data_len:
                score += 0.10
                evidence.append(
                    f"Manufacturer data length ({len(manufacturer_data)} bytes) consistent with {signature.name}"
                )

        # Check service UUIDs (medium weight)
        for sig_uuid in signature.service_uuids:
            if sig_uuid.lower() in normalized_uuids:
                score += 0.25
                evidence.append(f"Service UUID {sig_uuid} matches {signature.name}")
                break

        # Check MAC prefix (medium weight)
        if signature.mac_prefixes:
            mac_upper = address.upper()
            for prefix in signature.mac_prefixes:
                if mac_upper.startswith(prefix):
                    score += 0.20
                    evidence.append(f"MAC prefix {prefix} matches known {signature.name} range")
                    break

        # Check name patterns - a name match alone yields a LOW-confidence
        # detection (0.30 = detection threshold); names can be spoofed, so it
        # stays below the company-ID weight
        if name and signature.name_patterns:
            name_lower = name.lower()
            for pattern in signature.name_patterns:
                if pattern.lower() in name_lower:
                    score += 0.30
                    evidence.append(f'Device name "{name}" contains pattern "{pattern}"')
                    break

        # Apply confidence boost for specific signatures, but only when at
        # least one indicator actually matched - never as a free baseline
        if score > 0:
            score += signature.confidence_boost

        return score, evidence

    @staticmethod
    def _of_kind_fits(of_kind: str, tracker_type: TrackerType) -> bool:
        """Whether an Offline Finding sender kind may match a signature: an
        AirTag only the AirTag signature, AirPods and accessories only the Find
        My accessory one, and an iPhone, iPad or Mac none."""
        if of_kind == "airtag":
            return tracker_type == TrackerType.AIRTAG
        if of_kind in ("airpods", "findmy_accessory"):
            return tracker_type == TrackerType.FINDMY_ACCESSORY
        return False

    def _check_generic_tracker_indicators(
        self,
        address: str,
        address_type: str,
        manufacturer_id: int | None,
        manufacturer_data: bytes | None,
        normalized_uuids: list[str],
    ) -> tuple[float, list[str]]:
        """Check for generic tracker-like indicators."""
        score = 0.0
        evidence = []

        # Apple manufacturer with Find My advertisement type
        if manufacturer_id == APPLE_COMPANY_ID and manufacturer_data and len(manufacturer_data) >= 2:
            adv_type = manufacturer_data[0]
            if adv_type == APPLE_FINDMY_ADV_TYPE:
                score += 0.35
                evidence.append("Apple Find My network advertisement detected")

        # Check for beacon-like service UUIDs
        for beacon_uuid in BEACON_SERVICE_UUIDS:
            if beacon_uuid in normalized_uuids:
                score += 0.15
                evidence.append(f"Uses beacon service UUID ({beacon_uuid})")
                break

        # Random address (most trackers use random addresses)
        if address_type in ("random", "rpa", "nrpa"):
            # This is a weak indicator - many devices use random addresses
            if score > 0:  # Only add if other indicators present
                score += 0.05
                evidence.append("Uses randomized MAC address")

        # Small manufacturer data payload typical of beacons
        if manufacturer_data and 20 <= len(manufacturer_data) <= 30 and score > 0:
            score += 0.05
            evidence.append(f"Manufacturer data length ({len(manufacturer_data)} bytes) typical of beacon")

        return score, evidence

    def _normalize_service_uuids(self, uuids: list[str]) -> list[str]:
        """Normalize service UUIDs to lowercase, extracting 16-bit UUIDs where possible."""
        normalized = []
        for uuid in uuids:
            uuid_lower = uuid.lower()
            # Extract 16-bit UUID from full 128-bit Bluetooth Base UUID
            # Format: 0000XXXX-0000-1000-8000-00805f9b34fb
            if len(uuid_lower) == 36 and uuid_lower.endswith("-0000-1000-8000-00805f9b34fb"):
                short_uuid = uuid_lower[4:8]
                normalized.append(short_uuid)
            else:
                normalized.append(uuid_lower)
        return normalized

    def generate_device_fingerprint(
        self,
        manufacturer_id: int | None,
        manufacturer_data: bytes | None,
        service_uuids: list[str],
        service_data: dict[str, bytes],
        tx_power: int | None,
        name: str | None,
    ) -> DeviceFingerprint:
        """Generate a fingerprint for device tracking across MAC rotations."""
        return generate_fingerprint(
            manufacturer_id=manufacturer_id,
            manufacturer_data=manufacturer_data,
            service_uuids=service_uuids,
            service_data=service_data,
            tx_power=tx_power,
            name=name,
        )

    def record_sighting(self, fingerprint_id: str, timestamp: datetime | None = None) -> int:
        """
        Record a device sighting for persistence tracking.

        Returns the number of times this fingerprint has been seen.
        """
        ts = timestamp or datetime.now()

        if fingerprint_id not in self._sighting_history:
            self._sighting_history[fingerprint_id] = []

        # Keep only last 24 hours of sightings
        cutoff = ts - timedelta(hours=24)
        self._sighting_history[fingerprint_id] = [t for t in self._sighting_history[fingerprint_id] if t > cutoff]

        self._sighting_history[fingerprint_id].append(ts)
        return len(self._sighting_history[fingerprint_id])

    def get_sighting_count(self, fingerprint_id: str, window_hours: int = 24) -> int:
        """Get the number of times a fingerprint has been seen in the time window."""
        if fingerprint_id not in self._sighting_history:
            return 0

        cutoff = datetime.now() - timedelta(hours=window_hours)
        return sum(1 for t in self._sighting_history[fingerprint_id] if t > cutoff)

    def evaluate_suspicious_presence(
        self,
        fingerprint_id: str,
        is_tracker: bool,
        seen_count: int,
        duration_seconds: float,
        seen_rate: float,
        rssi_variance: float | None,
        is_new: bool,
    ) -> tuple[float, list[str]]:
        """
        Evaluate if a device shows suspicious "following" behavior.

        Returns (risk_score, risk_factors) where:
        - risk_score: 0.0-1.0 indicating likelihood of suspicious presence
        - risk_factors: List of reasons contributing to the score

        IMPORTANT: These are HEURISTICS only. They indicate patterns that
        MIGHT suggest a device is following/tracking, but cannot prove intent.
        Always present to users with appropriate caveats.
        """
        risk_score = 0.0
        risk_factors = []

        # Tracker baseline - if it's a tracker, start with some risk
        if is_tracker:
            risk_score += 0.3
            risk_factors.append("Device matches known tracker signature")

        # Heuristic 1: Persistently near - seen many times over a long period
        if seen_count >= 20 and duration_seconds >= 600:  # 10+ minutes
            points = min(0.25, (seen_count / 100) * 0.25)
            risk_score += points
            risk_factors.append(f"Persistently present: seen {seen_count} times over {duration_seconds / 60:.1f} min")
        elif seen_count >= 50:
            risk_score += 0.2
            risk_factors.append(f"High observation count: {seen_count} sightings")

        # Heuristic 2: Consistent presence rate (beacon-like behavior)
        if seen_rate >= 3.0:  # 3+ observations per minute
            points = min(0.15, (seen_rate / 10) * 0.15)
            risk_score += points
            risk_factors.append(f"Beacon-like presence: {seen_rate:.1f} obs/min")

        # Heuristic 3: Stable RSSI (moving with us, same relative distance)
        if rssi_variance is not None and rssi_variance < 10:
            risk_score += 0.1
            risk_factors.append(f"Stable signal strength (variance: {rssi_variance:.1f})")

        # Heuristic 4: New device appearing (not in baseline)
        if is_new and is_tracker:
            risk_score += 0.15
            risk_factors.append("New tracker appeared after baseline was set")

        # Cross-session persistence (from sighting history)
        historical_count = self.get_sighting_count(fingerprint_id, window_hours=24)
        if historical_count >= 10:
            points = min(0.15, (historical_count / 50) * 0.15)
            risk_score += points
            risk_factors.append(f"Seen across multiple sessions: {historical_count} total sightings in 24h")

        return min(1.0, risk_score), risk_factors


# =============================================================================
# SINGLETON ENGINE INSTANCE
# =============================================================================

_engine_instance: TrackerSignatureEngine | None = None


def apple_offline_finding_kind(manufacturer_id: int | None, manufacturer_data: bytes | None) -> str | None:
    """For an Apple Offline Finding advertisement, the kind of device that
    sent it ("apple_device", "airtag", "airpods" or "findmy_accessory");
    otherwise None. manufacturer_data starts after the company ID."""
    if manufacturer_id != APPLE_COMPANY_ID or not manufacturer_data or len(manufacturer_data) < 3:
        return None
    if manufacturer_data[0] != APPLE_FINDMY_ADV_TYPE:
        return None
    return APPLE_OF_DEVICE_KINDS[(manufacturer_data[2] & 0x30) >> 4]


def get_tracker_engine() -> TrackerSignatureEngine:
    """Get the singleton tracker signature engine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = TrackerSignatureEngine()
    return _engine_instance


def detect_tracker(
    address: str,
    address_type: str = "public",
    name: str | None = None,
    manufacturer_id: int | None = None,
    manufacturer_data: bytes | None = None,
    service_uuids: list[str] | None = None,
    service_data: dict[str, bytes] | None = None,
    tx_power: int | None = None,
) -> TrackerDetectionResult:
    """
    Convenience function to detect if a BLE device is a tracker.

    See TrackerSignatureEngine.detect_tracker for full documentation.
    """
    engine = get_tracker_engine()
    return engine.detect_tracker(
        address=address,
        address_type=address_type,
        name=name,
        manufacturer_id=manufacturer_id,
        manufacturer_data=manufacturer_data,
        service_uuids=service_uuids,
        service_data=service_data,
        tx_power=tx_power,
    )
