"""
Randomized MAC Resistant Device Detection

Clusters BLE and WiFi observations into "probable same physical device"
identities using passive fingerprinting techniques. Does NOT attempt to
de-randomize MACs cryptographically or bypass privacy protections.

This is passive screening + correlation only for TSCM purposes.

LIMITATIONS AND DISCLAIMERS:
- Clustering confidence scores indicate statistical similarity, not certainty
- False positives and false negatives are expected
- Results should be treated as indicators requiring professional verification
- No attribution claims about specific device models or manufacturers
- Cannot detect devices that don't transmit or use advanced evasion

Key Techniques Used:
1. Advertisement payload fingerprinting (manufacturer data, service UUIDs)
2. Timing correlation (appearance/disappearance patterns, ad intervals)
3. RSSI trajectory analysis (physical proximity/movement patterns)
4. Capability fingerprinting (WiFi HT/VHT/HE, rates, vendor IEs)
5. Behavioral pattern matching (frame types, payload structure)
"""

from __future__ import annotations

import hashlib
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger("intercept.tscm.device_identity")


# =============================================================================
# Constants and Configuration
# =============================================================================

# Session gap thresholds (seconds)
BLE_SESSION_GAP = 60  # New session if no observations for 60s
WIFI_SESSION_GAP = 120  # WiFi clients may probe less frequently

# Clustering thresholds
MIN_CLUSTER_CONFIDENCE = 0.3  # Minimum confidence to consider clustering
HIGH_CONFIDENCE_THRESHOLD = 0.7
VERY_HIGH_CONFIDENCE_THRESHOLD = 0.85

# RSSI proximity threshold for "same location" assessment
RSSI_PROXIMITY_THRESHOLD = 10  # dBm difference

# Time window for temporal correlation
TEMPORAL_CORRELATION_WINDOW = timedelta(seconds=5)

# Fingerprint weights (sum to 1.0 for normalization)
FINGERPRINT_WEIGHTS = {
    "manufacturer_data": 0.25,
    "service_uuids": 0.20,
    "capabilities": 0.15,
    "payload_structure": 0.15,
    "timing_pattern": 0.10,
    "rssi_trajectory": 0.10,
    "name_similarity": 0.05,
}


class AddressType(Enum):
    """BLE address types per Bluetooth spec."""

    PUBLIC = "public"
    RANDOM_STATIC = "random_static"
    RPA = "rpa"  # Resolvable Private Address
    NRPA = "nrpa"  # Non-Resolvable Private Address
    UNKNOWN = "unknown"


class AdvType(Enum):
    """BLE advertisement types."""

    ADV_IND = "ADV_IND"
    ADV_DIRECT_IND = "ADV_DIRECT_IND"
    ADV_NONCONN_IND = "ADV_NONCONN_IND"
    ADV_SCAN_IND = "ADV_SCAN_IND"
    SCAN_RSP = "SCAN_RSP"
    UNKNOWN = "unknown"


class WifiFrameType(Enum):
    """WiFi frame types of interest."""

    BEACON = "beacon"
    PROBE_REQUEST = "probe_request"
    PROBE_RESPONSE = "probe_response"
    AUTH = "auth"
    ASSOC_REQUEST = "assoc_request"
    ASSOC_RESPONSE = "assoc_response"
    DEAUTH = "deauth"
    DISASSOC = "disassoc"
    DATA = "data"
    UNKNOWN = "unknown"


class RiskLevel(Enum):
    """TSCM risk levels for device clusters."""

    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# =============================================================================
# Observation Data Classes
# =============================================================================


@dataclass
class BLEObservation:
    """Single BLE advertisement observation."""

    timestamp: datetime
    addr: str  # MAC-like address
    addr_type: AddressType = AddressType.UNKNOWN
    rssi: int | None = None
    tx_power: int | None = None
    adv_type: AdvType = AdvType.UNKNOWN
    adv_flags: int | None = None
    manufacturer_id: int | None = None
    manufacturer_data: bytes | None = None
    service_uuids: list[str] = field(default_factory=list)
    service_data: bytes | None = None
    local_name: str | None = None
    appearance: int | None = None
    packet_length: int | None = None
    phy: str | None = None

    def __post_init__(self):
        if isinstance(self.addr_type, str):
            try:
                self.addr_type = AddressType(self.addr_type)
            except ValueError:
                self.addr_type = AddressType.UNKNOWN
        if isinstance(self.adv_type, str):
            try:
                self.adv_type = AdvType(self.adv_type)
            except ValueError:
                self.adv_type = AdvType.UNKNOWN

    def compute_fingerprint_hash(self) -> str:
        """
        Compute a fingerprint hash based on stable (non-MAC) features.

        This hash helps identify similar payloads across different MACs.
        """
        components = []

        if self.manufacturer_id is not None:
            components.append(f"mfg:{self.manufacturer_id:04x}")

        if self.manufacturer_data:
            # Use first 8 bytes of manufacturer data (often contains device type)
            data_prefix = self.manufacturer_data[:8].hex()
            components.append(f"mfg_data:{data_prefix}")

        if self.service_uuids:
            # Sort for consistency
            uuids = sorted(set(self.service_uuids))
            components.append(f"uuids:{','.join(uuids)}")

        if self.adv_flags is not None:
            components.append(f"flags:{self.adv_flags:02x}")

        if self.appearance is not None:
            components.append(f"appear:{self.appearance:04x}")

        if self.tx_power is not None:
            components.append(f"txp:{self.tx_power}")

        if self.packet_length is not None:
            components.append(f"plen:{self.packet_length}")

        if not components:
            return ""

        fingerprint_str = "|".join(components)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]

    def is_randomized_address(self) -> bool:
        """Check if the address appears to be randomized."""
        if self.addr_type in (AddressType.RPA, AddressType.NRPA):
            return True

        # Check MAC address format for random bit
        # Bit 1 of first octet set = locally administered (random)
        try:
            first_octet = int(self.addr.split(":")[0], 16)
            return bool(first_octet & 0x02)
        except (ValueError, IndexError):
            return False


@dataclass
class WifiObservation:
    """Single WiFi frame observation."""

    timestamp: datetime
    src_mac: str
    dst_mac: str | None = None
    bssid: str | None = None
    ssid: str | None = None
    frame_type: WifiFrameType = WifiFrameType.UNKNOWN
    rssi: int | None = None
    channel: int | None = None
    bandwidth: int | None = None  # 20/40/80/160
    encryption: str | None = None
    beacon_interval: int | None = None
    capabilities: int | None = None
    supported_rates: list[float] = field(default_factory=list)
    extended_rates: list[float] = field(default_factory=list)
    ht_capable: bool = False
    vht_capable: bool = False
    he_capable: bool = False
    ht_capabilities: int | None = None
    vht_capabilities: int | None = None
    vendor_ies: list[tuple[str, int]] = field(default_factory=list)  # (OUI, length)
    wps_present: bool = False
    sequence_number: int | None = None
    probed_ssids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if isinstance(self.frame_type, str):
            try:
                self.frame_type = WifiFrameType(self.frame_type)
            except ValueError:
                self.frame_type = WifiFrameType.UNKNOWN

    def compute_fingerprint_hash(self) -> str:
        """
        Compute a fingerprint hash based on stable capability features.

        For clients, this captures the "device type" signature.
        """
        components = []

        # Rate set fingerprint
        all_rates = sorted(set(self.supported_rates + self.extended_rates))
        if all_rates:
            components.append(f"rates:{','.join(str(r) for r in all_rates)}")

        # Capability fingerprint
        caps = []
        if self.ht_capable:
            caps.append("HT")
        if self.vht_capable:
            caps.append("VHT")
        if self.he_capable:
            caps.append("HE")
        if caps:
            components.append(f"caps:{'+'.join(caps)}")

        if self.ht_capabilities is not None:
            components.append(f"htcap:{self.ht_capabilities:04x}")

        if self.vht_capabilities is not None:
            components.append(f"vhtcap:{self.vht_capabilities:08x}")

        # Vendor IE fingerprint (OUIs only, not content)
        if self.vendor_ies:
            ouis = sorted({oui for oui, _ in self.vendor_ies})
            components.append(f"vie:{','.join(ouis)}")

        if self.capabilities is not None:
            components.append(f"cap:{self.capabilities:04x}")

        if not components:
            return ""

        fingerprint_str = "|".join(components)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]

    def is_randomized_address(self) -> bool:
        """Check if source MAC appears to be randomized."""
        try:
            first_octet = int(self.src_mac.split(":")[0], 16)
            return bool(first_octet & 0x02)
        except (ValueError, IndexError):
            return False


# =============================================================================
# Session and Cluster Data Classes
# =============================================================================


@dataclass
class DeviceSession:
    """
    A session represents a contiguous presence window of a device.

    Multiple observations from the same MAC (or clustered identity) within
    the session gap threshold belong to the same session.
    """

    session_id: str
    protocol: str  # 'ble' or 'wifi'
    first_seen: datetime
    last_seen: datetime
    observations: list = field(default_factory=list)
    primary_mac: str | None = None
    observed_macs: set[str] = field(default_factory=set)
    fingerprint_hashes: set[str] = field(default_factory=set)

    # Aggregated metrics
    rssi_samples: list[int] = field(default_factory=list)
    observation_intervals: list[float] = field(default_factory=list)

    def add_observation(self, obs) -> None:
        """Add an observation to this session."""
        self.observations.append(obs)
        self.last_seen = obs.timestamp

        if hasattr(obs, "addr"):
            self.observed_macs.add(obs.addr)
            if self.primary_mac is None:
                self.primary_mac = obs.addr
        elif hasattr(obs, "src_mac"):
            self.observed_macs.add(obs.src_mac)
            if self.primary_mac is None:
                self.primary_mac = obs.src_mac

        fp = obs.compute_fingerprint_hash()
        if fp:
            self.fingerprint_hashes.add(fp)

        if obs.rssi is not None:
            self.rssi_samples.append(obs.rssi)

        # Calculate interval from previous observation
        if len(self.observations) > 1:
            prev = self.observations[-2]
            interval = (obs.timestamp - prev.timestamp).total_seconds()
            if interval > 0:
                self.observation_intervals.append(interval)

    def get_duration(self) -> timedelta:
        """Get session duration."""
        return self.last_seen - self.first_seen

    def get_mean_rssi(self) -> float | None:
        """Get mean RSSI across session."""
        if not self.rssi_samples:
            return None
        return statistics.mean(self.rssi_samples)

    def get_rssi_stability(self) -> float:
        """
        Calculate RSSI stability (0-1, higher = more stable).

        Stable RSSI suggests a stationary device.
        """
        if len(self.rssi_samples) < 3:
            return 0.0
        try:
            stdev = statistics.stdev(self.rssi_samples)
            # Convert to 0-1 scale (stdev of 0 = 1.0, stdev of 20+ = ~0)
            return max(0, 1 - (stdev / 20))
        except statistics.StatisticsError:
            return 0.0

    def get_mean_interval(self) -> float | None:
        """Get mean advertising/probing interval."""
        if not self.observation_intervals:
            return None
        return statistics.mean(self.observation_intervals)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "protocol": self.protocol,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "duration_seconds": self.get_duration().total_seconds(),
            "observation_count": len(self.observations),
            "primary_mac": self.primary_mac,
            "observed_macs": list(self.observed_macs),
            "fingerprint_hashes": list(self.fingerprint_hashes),
            "mean_rssi": self.get_mean_rssi(),
            "rssi_stability": self.get_rssi_stability(),
            "mean_interval": self.get_mean_interval(),
        }


@dataclass
class RiskIndicator:
    """A TSCM risk indicator for a device cluster."""

    indicator_type: str
    description: str
    score: int  # 0-10
    evidence: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "type": self.indicator_type,
            "description": self.description,
            "score": self.score,
            "evidence": self.evidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DeviceCluster:
    """
    A cluster represents a probable physical device identity.

    Multiple sessions and MACs may be linked to the same cluster based
    on fingerprint similarity, temporal correlation, and RSSI patterns.
    """

    cluster_id: str
    protocol: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    sessions: list[DeviceSession] = field(default_factory=list)
    linked_macs: set[str] = field(default_factory=set)
    fingerprint_hashes: set[str] = field(default_factory=set)

    # Cluster confidence and linking evidence
    confidence: float = 0.0
    link_evidence: list[dict] = field(default_factory=list)

    # Best available identifiers
    best_name: str | None = None
    manufacturer_id: int | None = None
    manufacturer_name: str | None = None
    device_type: str | None = None

    # TSCM risk assessment
    risk_level: RiskLevel = RiskLevel.INFORMATIONAL
    risk_score: int = 0
    risk_indicators: list[RiskIndicator] = field(default_factory=list)

    # Behavioral profile
    total_observations: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    presence_ratio: float = 0.0  # % of monitoring period device was present

    def add_session(self, session: DeviceSession, link_reason: str, link_confidence: float) -> None:
        """Add a session to this cluster with linking evidence."""
        self.sessions.append(session)
        self.linked_macs.update(session.observed_macs)
        self.fingerprint_hashes.update(session.fingerprint_hashes)
        self.total_observations += len(session.observations)
        self.updated_at = datetime.now()

        if self.first_seen is None or session.first_seen < self.first_seen:
            self.first_seen = session.first_seen
        if self.last_seen is None or session.last_seen > self.last_seen:
            self.last_seen = session.last_seen

        self.link_evidence.append(
            {
                "session_id": session.session_id,
                "reason": link_reason,
                "confidence": link_confidence,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Update overall confidence (weighted average)
        if self.link_evidence:
            self.confidence = statistics.mean(e["confidence"] for e in self.link_evidence)

    def add_risk_indicator(self, indicator: RiskIndicator) -> None:
        """Add a risk indicator and update risk assessment."""
        self.risk_indicators.append(indicator)
        self.risk_score = sum(i.score for i in self.risk_indicators)

        # Update risk level based on score
        if self.risk_score >= 15:
            self.risk_level = RiskLevel.HIGH
        elif self.risk_score >= 8:
            self.risk_level = RiskLevel.MEDIUM
        elif self.risk_score >= 3:
            self.risk_level = RiskLevel.LOW
        else:
            self.risk_level = RiskLevel.INFORMATIONAL

    def get_all_rssi_samples(self) -> list[int]:
        """Get all RSSI samples across all sessions."""
        samples = []
        for session in self.sessions:
            samples.extend(session.rssi_samples)
        return samples

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "cluster_id": self.cluster_id,
            "protocol": self.protocol,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "confidence": round(self.confidence, 3),
            "session_count": len(self.sessions),
            "linked_macs": list(self.linked_macs),
            "fingerprint_hashes": list(self.fingerprint_hashes),
            "best_name": self.best_name,
            "manufacturer_id": self.manufacturer_id,
            "manufacturer_name": self.manufacturer_name,
            "device_type": self.device_type,
            "risk_level": self.risk_level.value,
            "risk_score": self.risk_score,
            "risk_indicators": [i.to_dict() for i in self.risk_indicators],
            "total_observations": self.total_observations,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "presence_ratio": round(self.presence_ratio, 3),
            "link_evidence": self.link_evidence,
            "sessions": [s.to_dict() for s in self.sessions],
        }


# =============================================================================
# Fingerprint Similarity Functions
# =============================================================================


def jaccard_similarity(set1: set, set2: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 and not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def manufacturer_data_similarity(data1: bytes | None, data2: bytes | None) -> float:
    """
    Calculate similarity between manufacturer data blobs.

    Many devices include consistent patterns in manufacturer data
    even when MAC randomizes.
    """
    if not data1 or not data2:
        return 0.0

    # Compare lengths
    len_sim = 1.0 - abs(len(data1) - len(data2)) / max(len(data1), len(data2))

    # Compare common prefix (often contains device type info)
    prefix_len = min(8, len(data1), len(data2))
    prefix_match = sum(1 for i in range(prefix_len) if data1[i] == data2[i]) / prefix_len if prefix_len > 0 else 0.0

    # Compare full content via byte-level similarity
    min_len = min(len(data1), len(data2))
    byte_matches = sum(1 for i in range(min_len) if data1[i] == data2[i])
    content_sim = byte_matches / max(len(data1), len(data2))

    # Weight prefix more heavily (device type usually in prefix)
    return 0.5 * prefix_match + 0.3 * content_sim + 0.2 * len_sim


def rssi_trajectory_similarity(samples1: list[int], samples2: list[int], time_window: float = 5.0) -> float:
    """
    Calculate RSSI trajectory similarity.

    Devices at the same physical location show similar RSSI patterns.
    This helps correlate observations that may be from the same device.
    """
    if len(samples1) < 3 or len(samples2) < 3:
        return 0.0

    # Compare mean RSSI (proximity indicator)
    mean1 = statistics.mean(samples1)
    mean2 = statistics.mean(samples2)
    mean_diff = abs(mean1 - mean2)

    # If means are very different, devices are likely in different locations
    if mean_diff > 20:
        return 0.0

    mean_sim = 1.0 - (mean_diff / 20)

    # Compare RSSI variance (movement pattern)
    try:
        var1 = statistics.variance(samples1)
        var2 = statistics.variance(samples2)
        var_diff = abs(var1 - var2)
        var_sim = 1.0 / (1.0 + var_diff / 50)
    except statistics.StatisticsError:
        var_sim = 0.5

    return 0.6 * mean_sim + 0.4 * var_sim


def timing_pattern_similarity(intervals1: list[float], intervals2: list[float]) -> float:
    """
    Calculate advertising/probing interval similarity.

    Devices often have characteristic timing patterns.
    """
    if len(intervals1) < 2 or len(intervals2) < 2:
        return 0.0

    mean1 = statistics.mean(intervals1)
    mean2 = statistics.mean(intervals2)

    # Calculate relative difference
    if mean1 == 0 or mean2 == 0:
        return 0.0

    ratio = min(mean1, mean2) / max(mean1, mean2)

    # Also compare variance in timing
    try:
        cv1 = statistics.stdev(intervals1) / mean1 if mean1 > 0 else 0
        cv2 = statistics.stdev(intervals2) / mean2 if mean2 > 0 else 0
        cv_sim = 1.0 - abs(cv1 - cv2)
    except statistics.StatisticsError:
        cv_sim = 0.5

    return 0.7 * ratio + 0.3 * max(0, cv_sim)


def name_similarity(name1: str | None, name2: str | None) -> float:
    """Calculate similarity between device names."""
    if not name1 or not name2:
        return 0.0

    # Normalize names
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()

    if n1 == n2:
        return 1.0

    # Check if one is prefix of other (common with truncation)
    if n1.startswith(n2) or n2.startswith(n1):
        return 0.8

    # Simple character-level similarity
    common = sum(1 for c in set(n1) if c in n2)
    total = len(set(n1) | set(n2))
    return common / total if total > 0 else 0.0


# =============================================================================
# Device Identity Engine
# =============================================================================


class DeviceIdentityEngine:
    """
    Main engine for MAC-randomization resistant device detection.

    Ingests BLE and WiFi observations, creates sessions, clusters them
    into probable device identities, and generates TSCM risk assessments.
    """

    def __init__(self):
        self.ble_sessions: dict[str, DeviceSession] = {}
        self.wifi_sessions: dict[str, DeviceSession] = {}
        self.clusters: dict[str, DeviceCluster] = {}

        # Fingerprint index for efficient lookup
        self._fingerprint_to_sessions: dict[str, list[str]] = defaultdict(list)

        # Session counters
        self._session_counter = 0
        self._cluster_counter = 0

        # Monitoring period for presence calculation
        self.monitoring_start: datetime | None = None
        self.monitoring_end: datetime | None = None

    def _generate_session_id(self, protocol: str) -> str:
        """Generate unique session ID."""
        self._session_counter += 1
        return f"{protocol}_{self._session_counter:06d}"

    def _generate_cluster_id(self, protocol: str) -> str:
        """Generate unique cluster ID."""
        self._cluster_counter += 1
        return f"cluster_{protocol}_{self._cluster_counter:06d}"

    def ingest_ble_observation(self, obs: BLEObservation) -> DeviceSession:
        """
        Ingest a BLE observation and return/update the associated session.
        """
        if self.monitoring_start is None:
            self.monitoring_start = obs.timestamp
        self.monitoring_end = obs.timestamp

        # Find or create session for this MAC
        session_key = f"ble_{obs.addr}"

        if session_key in self.ble_sessions:
            session = self.ble_sessions[session_key]
            # Check if this is a continuation or new session
            gap = (obs.timestamp - session.last_seen).total_seconds()
            if gap > BLE_SESSION_GAP:
                # Close old session, start new one
                self._finalize_session(session)
                session = self._create_ble_session(obs)
                self.ble_sessions[session_key] = session
            else:
                session.add_observation(obs)
        else:
            session = self._create_ble_session(obs)
            self.ble_sessions[session_key] = session

        # Update fingerprint index
        fp = obs.compute_fingerprint_hash()
        if fp and session.session_id not in self._fingerprint_to_sessions[fp]:
            self._fingerprint_to_sessions[fp].append(session.session_id)

        return session

    def _create_ble_session(self, obs: BLEObservation) -> DeviceSession:
        """Create a new BLE session from initial observation."""
        session = DeviceSession(
            session_id=self._generate_session_id("ble"),
            protocol="ble",
            first_seen=obs.timestamp,
            last_seen=obs.timestamp,
        )
        session.add_observation(obs)
        return session

    def ingest_wifi_observation(self, obs: WifiObservation) -> DeviceSession:
        """
        Ingest a WiFi observation and return/update the associated session.
        """
        if self.monitoring_start is None:
            self.monitoring_start = obs.timestamp
        self.monitoring_end = obs.timestamp

        # For WiFi, track by source MAC
        session_key = f"wifi_{obs.src_mac}"

        if session_key in self.wifi_sessions:
            session = self.wifi_sessions[session_key]
            gap = (obs.timestamp - session.last_seen).total_seconds()
            if gap > WIFI_SESSION_GAP:
                self._finalize_session(session)
                session = self._create_wifi_session(obs)
                self.wifi_sessions[session_key] = session
            else:
                session.add_observation(obs)
        else:
            session = self._create_wifi_session(obs)
            self.wifi_sessions[session_key] = session

        # Update fingerprint index
        fp = obs.compute_fingerprint_hash()
        if fp and session.session_id not in self._fingerprint_to_sessions[fp]:
            self._fingerprint_to_sessions[fp].append(session.session_id)

        return session

    def _create_wifi_session(self, obs: WifiObservation) -> DeviceSession:
        """Create a new WiFi session from initial observation."""
        session = DeviceSession(
            session_id=self._generate_session_id("wifi"),
            protocol="wifi",
            first_seen=obs.timestamp,
            last_seen=obs.timestamp,
        )
        session.add_observation(obs)
        return session

    def _finalize_session(self, session: DeviceSession) -> None:
        """Finalize a session and attempt to cluster it."""
        # Try to find existing cluster for this session
        cluster = self._find_matching_cluster(session)

        if cluster:
            # Add to existing cluster
            similarity = self._calculate_cluster_similarity(cluster, session)
            cluster.add_session(session, link_reason="Fingerprint/behavioral match", link_confidence=similarity)
        else:
            # Create new cluster
            cluster = self._create_cluster_from_session(session)
            self.clusters[cluster.cluster_id] = cluster

        # Run risk assessment on the cluster
        self._assess_cluster_risk(cluster)

    def _find_matching_cluster(self, session: DeviceSession) -> DeviceCluster | None:
        """
        Find an existing cluster that matches this session.

        Uses fingerprint matching, temporal correlation, and RSSI similarity.
        """
        best_match = None
        best_score = MIN_CLUSTER_CONFIDENCE

        for cluster in self.clusters.values():
            if cluster.protocol != session.protocol:
                continue

            similarity = self._calculate_cluster_similarity(cluster, session)
            if similarity > best_score:
                best_score = similarity
                best_match = cluster

        return best_match

    def _calculate_cluster_similarity(self, cluster: DeviceCluster, session: DeviceSession) -> float:
        """
        Calculate similarity between a cluster and a session.

        Returns a confidence score 0-1.
        """
        scores = {}

        # 1. Fingerprint hash matching (strongest signal)
        fp_overlap = cluster.fingerprint_hashes & session.fingerprint_hashes
        if fp_overlap:
            fp_score = len(fp_overlap) / max(len(cluster.fingerprint_hashes), len(session.fingerprint_hashes))
            scores["fingerprint"] = min(1.0, fp_score * 1.5)  # Boost for exact match

        # 2. Manufacturer data similarity
        cluster_mfg_data = self._get_cluster_manufacturer_data(cluster)
        session_mfg_data = self._get_session_manufacturer_data(session)
        if cluster_mfg_data and session_mfg_data:
            scores["manufacturer_data"] = manufacturer_data_similarity(cluster_mfg_data, session_mfg_data)

        # 3. Service UUID overlap
        cluster_uuids = self._get_cluster_service_uuids(cluster)
        session_uuids = self._get_session_service_uuids(session)
        if cluster_uuids or session_uuids:
            scores["service_uuids"] = jaccard_similarity(cluster_uuids, session_uuids)

        # 4. RSSI trajectory similarity
        cluster_rssi = cluster.get_all_rssi_samples()
        if cluster_rssi and session.rssi_samples:
            scores["rssi_trajectory"] = rssi_trajectory_similarity(cluster_rssi, session.rssi_samples)

        # 5. Timing pattern similarity
        cluster_intervals = self._get_cluster_intervals(cluster)
        if cluster_intervals and session.observation_intervals:
            scores["timing_pattern"] = timing_pattern_similarity(cluster_intervals, session.observation_intervals)

        # 6. Name similarity
        session_name = self._get_session_name(session)
        if cluster.best_name and session_name:
            scores["name_similarity"] = name_similarity(cluster.best_name, session_name)

        if not scores:
            return 0.0

        # Weighted average
        total_weight = 0.0
        weighted_sum = 0.0

        for key, score in scores.items():
            weight = FINGERPRINT_WEIGHTS.get(key, 0.1)
            weighted_sum += score * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _get_cluster_manufacturer_data(self, cluster: DeviceCluster) -> bytes | None:
        """Get representative manufacturer data from cluster."""
        for session in cluster.sessions:
            for obs in session.observations:
                if hasattr(obs, "manufacturer_data") and obs.manufacturer_data:
                    return obs.manufacturer_data
        return None

    def _get_session_manufacturer_data(self, session: DeviceSession) -> bytes | None:
        """Get manufacturer data from session."""
        for obs in session.observations:
            if hasattr(obs, "manufacturer_data") and obs.manufacturer_data:
                return obs.manufacturer_data
        return None

    def _get_cluster_service_uuids(self, cluster: DeviceCluster) -> set[str]:
        """Get all service UUIDs from cluster."""
        uuids = set()
        for session in cluster.sessions:
            uuids.update(self._get_session_service_uuids(session))
        return uuids

    def _get_session_service_uuids(self, session: DeviceSession) -> set[str]:
        """Get service UUIDs from session."""
        uuids = set()
        for obs in session.observations:
            if hasattr(obs, "service_uuids") and obs.service_uuids:
                uuids.update(obs.service_uuids)
        return uuids

    def _get_cluster_intervals(self, cluster: DeviceCluster) -> list[float]:
        """Get all observation intervals from cluster."""
        intervals = []
        for session in cluster.sessions:
            intervals.extend(session.observation_intervals)
        return intervals

    def _get_session_name(self, session: DeviceSession) -> str | None:
        """Get device name from session."""
        for obs in session.observations:
            if hasattr(obs, "local_name") and obs.local_name:
                return obs.local_name
        return None

    def _create_cluster_from_session(self, session: DeviceSession) -> DeviceCluster:
        """Create a new cluster from a session."""
        cluster = DeviceCluster(
            cluster_id=self._generate_cluster_id(session.protocol),
            protocol=session.protocol,
        )

        cluster.add_session(session, link_reason="Initial session", link_confidence=1.0)

        # Extract identifying information
        for obs in session.observations:
            if hasattr(obs, "local_name") and obs.local_name:
                cluster.best_name = obs.local_name
            if hasattr(obs, "manufacturer_id") and obs.manufacturer_id:
                cluster.manufacturer_id = obs.manufacturer_id

        return cluster

    def _assess_cluster_risk(self, cluster: DeviceCluster) -> None:
        """
        Assess TSCM risk indicators for a cluster.

        Flags behaviors that may indicate surveillance devices:
        - High presence ratio (always present)
        - Stable RSSI (stationary/hidden device)
        - Audio-capable services
        - ESP32/generic chipsets
        - Suspicious advertising patterns
        - MAC rotation patterns
        """
        # Calculate presence ratio
        if self.monitoring_start and self.monitoring_end:
            total_duration = (self.monitoring_end - self.monitoring_start).total_seconds()
            if total_duration > 0 and cluster.first_seen and cluster.last_seen:
                presence_duration = (cluster.last_seen - cluster.first_seen).total_seconds()
                cluster.presence_ratio = min(1.0, presence_duration / total_duration)

        # Risk: High presence ratio (device always present)
        if cluster.presence_ratio > 0.8:
            cluster.add_risk_indicator(
                RiskIndicator(
                    indicator_type="high_presence",
                    description="Device present for >80% of monitoring period",
                    score=2,
                    evidence={"presence_ratio": round(cluster.presence_ratio, 2)},
                )
            )

        # Risk: Very stable RSSI (stationary device)
        rssi_samples = cluster.get_all_rssi_samples()
        if len(rssi_samples) >= 5:
            try:
                stdev = statistics.stdev(rssi_samples)
                if stdev < 3:
                    cluster.add_risk_indicator(
                        RiskIndicator(
                            indicator_type="stable_rssi",
                            description="Very stable signal suggests fixed placement",
                            score=2,
                            evidence={"rssi_stdev": round(stdev, 2), "sample_count": len(rssi_samples)},
                        )
                    )
            except statistics.StatisticsError:
                pass

        # Risk: Multiple MAC addresses observed (MAC rotation)
        if len(cluster.linked_macs) > 1:
            cluster.add_risk_indicator(
                RiskIndicator(
                    indicator_type="mac_rotation",
                    description=f"Multiple MACs ({len(cluster.linked_macs)}) linked to same device",
                    score=1,
                    evidence={"mac_count": len(cluster.linked_macs)},
                )
            )

        # Risk: Check for suspicious manufacturer IDs
        if cluster.manufacturer_id:
            suspicious_mfg = {
                0x02E5: ("Espressif", 3, "Programmable ESP32/ESP8266 device"),
            }
            if cluster.manufacturer_id in suspicious_mfg:
                name, score, desc = suspicious_mfg[cluster.manufacturer_id]
                cluster.add_risk_indicator(
                    RiskIndicator(
                        indicator_type="suspicious_chipset",
                        description=desc,
                        score=score,
                        evidence={"manufacturer": name, "id": hex(cluster.manufacturer_id)},
                    )
                )

        # Risk: Check for audio-capable services (BLE)
        audio_service_prefixes = ["0000110", "00001108", "00001203"]  # A2DP, Headset, Audio
        cluster_uuids = set()
        for session in cluster.sessions:
            cluster_uuids.update(self._get_session_service_uuids(session))

        for uuid in cluster_uuids:
            if any(uuid.lower().startswith(prefix) for prefix in audio_service_prefixes):
                cluster.add_risk_indicator(
                    RiskIndicator(
                        indicator_type="audio_capable",
                        description="Audio-capable BLE services detected",
                        score=2,
                        evidence={"service_uuid": uuid},
                    )
                )
                break

        # Risk: No name advertised (hidden identity)
        if not cluster.best_name:
            cluster.add_risk_indicator(
                RiskIndicator(
                    indicator_type="no_name", description="Device does not advertise a name", score=1, evidence={}
                )
            )

        # Risk: High observation count relative to duration (aggressive advertising)
        if cluster.first_seen and cluster.last_seen:
            duration = (cluster.last_seen - cluster.first_seen).total_seconds()
            if duration > 60 and cluster.total_observations > 0:
                obs_rate = cluster.total_observations / duration
                if obs_rate > 2.0:  # More than 2 observations per second
                    cluster.add_risk_indicator(
                        RiskIndicator(
                            indicator_type="high_ad_rate",
                            description="Unusually high advertising rate",
                            score=2,
                            evidence={
                                "rate": round(obs_rate, 2),
                                "observations": cluster.total_observations,
                                "duration": round(duration, 1),
                            },
                        )
                    )

    def finalize_all_sessions(self) -> None:
        """Finalize all active sessions (call at end of monitoring)."""
        def memoize(method, version):
            cache = {}

            def cached(*args):
                key = version(*args)
                if key not in cache:
                    cache[key] = method(*args)
                return cache[key]

            return cached

        # Cluster features change when a session is appended. Session features
        # stay fixed during this pass; discard every cache when the pass ends.
        methods = {
            "_get_cluster_manufacturer_data": lambda cluster: (id(cluster), len(cluster.sessions)),
            "_get_cluster_service_uuids": lambda cluster: (id(cluster), len(cluster.sessions)),
            "_get_cluster_intervals": lambda cluster: (id(cluster), len(cluster.sessions)),
            "_get_session_manufacturer_data": lambda session: id(session),
            "_get_session_service_uuids": lambda session: id(session),
            "_get_session_name": lambda session: id(session),
            "_calculate_cluster_similarity": lambda cluster, session: (
                id(cluster), len(cluster.sessions), id(session)
            ),
        }
        previous = {name: self.__dict__.get(name) for name in methods}
        present = {name for name in methods if name in self.__dict__}
        try:
            for name, version in methods.items():
                setattr(self, name, memoize(getattr(self, name), version))
            for session in list(self.ble_sessions.values()):
                self._finalize_session(session)
            for session in list(self.wifi_sessions.values()):
                self._finalize_session(session)
        finally:
            for name in methods:
                if name in present:
                    setattr(self, name, previous[name])
                else:
                    delattr(self, name)

    def get_clusters(self, min_confidence: float = 0.0) -> list[DeviceCluster]:
        """Get all clusters above minimum confidence."""
        return [c for c in self.clusters.values() if c.confidence >= min_confidence]

    def get_high_risk_clusters(self) -> list[DeviceCluster]:
        """Get clusters with HIGH risk level."""
        return [c for c in self.clusters.values() if c.risk_level == RiskLevel.HIGH]

    def get_summary(self) -> dict:
        """Get summary of all clusters and sessions."""
        clusters_by_risk = {"high": [], "medium": [], "low": [], "informational": []}

        for cluster in self.clusters.values():
            clusters_by_risk[cluster.risk_level.value].append(cluster.to_dict())

        return {
            "monitoring_period": {
                "start": self.monitoring_start.isoformat() if self.monitoring_start else None,
                "end": self.monitoring_end.isoformat() if self.monitoring_end else None,
                "duration_seconds": (
                    (self.monitoring_end - self.monitoring_start).total_seconds()
                    if self.monitoring_start and self.monitoring_end
                    else 0
                ),
            },
            "statistics": {
                "total_clusters": len(self.clusters),
                "ble_sessions": len(self.ble_sessions),
                "wifi_sessions": len(self.wifi_sessions),
                "high_risk_count": len(clusters_by_risk["high"]),
                "medium_risk_count": len(clusters_by_risk["medium"]),
                "low_risk_count": len(clusters_by_risk["low"]),
                "unique_fingerprints": len(self._fingerprint_to_sessions),
            },
            "clusters_by_risk": clusters_by_risk,
            "disclaimer": (
                "Device clustering uses passive fingerprinting and statistical correlation. "
                "Results indicate probable device identities, NOT confirmed matches. "
                "Confidence scores reflect similarity measures, not certainty. "
                "False positives and false negatives are expected."
            ),
        }

    def clear(self) -> None:
        """Clear all state."""
        self.ble_sessions.clear()
        self.wifi_sessions.clear()
        self.clusters.clear()
        self._fingerprint_to_sessions.clear()
        self._session_counter = 0
        self._cluster_counter = 0
        self.monitoring_start = None
        self.monitoring_end = None


# =============================================================================
# Convenience Functions
# =============================================================================

# Global engine instance
_identity_engine: DeviceIdentityEngine | None = None


def get_identity_engine() -> DeviceIdentityEngine:
    """Get or create the global identity engine."""
    global _identity_engine
    if _identity_engine is None:
        _identity_engine = DeviceIdentityEngine()
    return _identity_engine


def reset_identity_engine() -> None:
    """Reset the global identity engine."""
    global _identity_engine
    _identity_engine = DeviceIdentityEngine()


def _convert_to_bytes(value) -> bytes | None:
    """Convert various data types to bytes safely."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        # Assume hex string
        try:
            return bytes.fromhex(value)
        except ValueError:
            # Not a valid hex string, encode as UTF-8
            return value.encode("utf-8")
    if isinstance(value, (list, tuple)):
        # Array of integers (like dbus.Array)
        try:
            return bytes(value)
        except (TypeError, ValueError):
            return None
    return None


def ingest_ble_dict(data: dict) -> DeviceSession:
    """
    Ingest BLE observation from dictionary.

    Convenience function for API integration.
    """
    obs = BLEObservation(
        timestamp=datetime.fromisoformat(data["timestamp"])
        if isinstance(data.get("timestamp"), str)
        else data.get("timestamp", datetime.now()),
        addr=data.get("addr", data.get("mac", "")).upper(),
        addr_type=data.get("addr_type", "unknown"),
        rssi=data.get("rssi"),
        tx_power=data.get("tx_power"),
        adv_type=data.get("adv_type", "unknown"),
        adv_flags=data.get("adv_flags"),
        manufacturer_id=data.get("manufacturer_id"),
        manufacturer_data=_convert_to_bytes(data.get("manufacturer_data")),
        service_uuids=data.get("service_uuids", []),
        service_data=_convert_to_bytes(data.get("service_data")),
        local_name=data.get("local_name", data.get("name")),
        appearance=data.get("appearance"),
        packet_length=data.get("packet_length"),
        phy=data.get("phy"),
    )
    return get_identity_engine().ingest_ble_observation(obs)


def ingest_wifi_dict(data: dict) -> DeviceSession:
    """
    Ingest WiFi observation from dictionary.

    Convenience function for API integration.
    """
    obs = WifiObservation(
        timestamp=datetime.fromisoformat(data["timestamp"])
        if isinstance(data.get("timestamp"), str)
        else data.get("timestamp", datetime.now()),
        src_mac=data.get("src_mac", data.get("mac", "")).upper(),
        dst_mac=data.get("dst_mac"),
        bssid=data.get("bssid"),
        ssid=data.get("ssid"),
        frame_type=data.get("frame_type", "unknown"),
        rssi=data.get("rssi"),
        channel=data.get("channel"),
        bandwidth=data.get("bandwidth"),
        encryption=data.get("encryption"),
        beacon_interval=data.get("beacon_interval"),
        capabilities=data.get("capabilities"),
        supported_rates=data.get("supported_rates", []),
        extended_rates=data.get("extended_rates", []),
        ht_capable=data.get("ht_capable", False),
        vht_capable=data.get("vht_capable", False),
        he_capable=data.get("he_capable", False),
        ht_capabilities=data.get("ht_capabilities"),
        vht_capabilities=data.get("vht_capabilities"),
        vendor_ies=data.get("vendor_ies", []),
        wps_present=data.get("wps_present", False),
        sequence_number=data.get("sequence_number"),
        probed_ssids=data.get("probed_ssids", []),
    )
    return get_identity_engine().ingest_wifi_observation(obs)
