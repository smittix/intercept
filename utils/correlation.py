"""
Device correlation engine for matching WiFi and Bluetooth devices.

Uses timing-based correlation to identify when WiFi and Bluetooth
signals likely belong to the same physical device.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from utils.database import add_correlation
from utils.database import get_correlations as db_get_correlations

logger = logging.getLogger("intercept.correlation")


@dataclass(slots=True)
class DeviceObservation:
    """A single observation of a device."""

    mac: str
    first_seen: datetime
    last_seen: datetime
    rssi: int | None = None
    name: str | None = None
    manufacturer: str | None = None


class DeviceCorrelator:
    """
    Correlates WiFi and Bluetooth devices based on timing patterns.

    Devices are considered potentially correlated if:
    1. They appear within a short time window of each other
    2. They have similar signal strength patterns (optional)
    3. They share the same OUI/manufacturer (bonus confidence)
    """

    def __init__(self, time_window_seconds: int = 30, min_confidence: float = 0.5, rssi_threshold: int = 20):
        """
        Initialize correlator.

        Args:
            time_window_seconds: Max time difference for correlation (default 30s)
            min_confidence: Minimum confidence score to report (default 0.5)
            rssi_threshold: Max RSSI difference for signal-based correlation
        """
        self.time_window = timedelta(seconds=time_window_seconds)
        self.min_confidence = min_confidence
        self.rssi_threshold = rssi_threshold

    def correlate(self, wifi_devices: dict[str, dict[str, Any]], bt_devices: dict[str, dict[str, Any]]) -> list[dict]:
        """
        Find correlations between WiFi and Bluetooth devices.

        Args:
            wifi_devices: Dict of WiFi devices keyed by MAC
            bt_devices: Dict of Bluetooth devices keyed by MAC

        Returns:
            List of correlation results with confidence scores
        """
        correlations = []
        bt_observations = None

        for wifi_mac, wifi_data in wifi_devices.items():
            wifi_obs = self._to_observation(wifi_mac, wifi_data, "wifi")
            if not wifi_obs:
                continue

            # Parse Bluetooth devices once, on first valid WiFi device
            if bt_observations is None:
                bt_observations = [
                    (bt_mac, bt_obs)
                    for bt_mac, bt_data in bt_devices.items()
                    if (bt_obs := self._to_observation(bt_mac, bt_data, "bluetooth"))
                ]

            for bt_mac, bt_obs in bt_observations:
                confidence = self._calculate_confidence(wifi_obs, bt_obs)

                if confidence >= self.min_confidence:
                    correlations.append(
                        {
                            "wifi_mac": wifi_mac,
                            "wifi_name": wifi_obs.name,
                            "bt_mac": bt_mac,
                            "bt_name": bt_obs.name,
                            "confidence": round(confidence, 2),
                            "reason": self._get_correlation_reason(wifi_obs, bt_obs),
                        }
                    )

                    # Persist high-confidence correlations
                    if confidence >= 0.7:
                        try:
                            add_correlation(
                                wifi_mac=wifi_mac,
                                bt_mac=bt_mac,
                                confidence=confidence,
                                metadata={"wifi_name": wifi_obs.name, "bt_name": bt_obs.name},
                            )
                        except Exception as e:
                            logger.debug(f"Failed to persist correlation: {e}")

        # Sort by confidence (highest first)
        correlations.sort(key=lambda x: x["confidence"], reverse=True)

        return correlations

    def _to_observation(self, mac: str, data: dict[str, Any], device_type: str) -> DeviceObservation | None:
        """Convert device dict to observation."""
        try:
            # Handle different timestamp formats
            first_seen = data.get("first_seen") or data.get("firstSeen")
            last_seen = data.get("last_seen") or data.get("lastSeen")

            if isinstance(first_seen, str):
                first_seen = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            elif isinstance(first_seen, (int, float)):
                first_seen = datetime.fromtimestamp(first_seen / 1000)
            else:
                first_seen = datetime.now()

            if isinstance(last_seen, str):
                last_seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            elif isinstance(last_seen, (int, float)):
                last_seen = datetime.fromtimestamp(last_seen / 1000)
            else:
                last_seen = datetime.now()

            # Get RSSI (different field names)
            rssi = data.get("rssi") or data.get("power") or data.get("signal")
            if rssi is not None:
                rssi = int(rssi)

            # Get name
            name = data.get("name") or data.get("essid") or data.get("ssid")

            # Get manufacturer
            manufacturer = data.get("manufacturer") or data.get("vendor")

            return DeviceObservation(
                mac=mac, first_seen=first_seen, last_seen=last_seen, rssi=rssi, name=name, manufacturer=manufacturer
            )
        except Exception as e:
            logger.debug(f"Failed to parse device {mac}: {e}")
            return None

    def _calculate_confidence(self, wifi: DeviceObservation, bt: DeviceObservation) -> float:
        """
        Calculate correlation confidence score.

        Score components:
        - Timing overlap: 0.0-0.5 (primary factor)
        - Same manufacturer: +0.2
        - Similar RSSI: +0.1
        - Both named: +0.1

        Returns:
            Confidence score 0.0-1.0
        """
        confidence = 0.0

        # Timing correlation (most important)
        time_diff = abs((wifi.first_seen - bt.first_seen).total_seconds())
        window_seconds = self.time_window.total_seconds()
        if time_diff <= window_seconds:
            # Linear decay from 0.5 to 0.0 as time difference increases
            timing_score = 0.5 * (1 - time_diff / window_seconds)
            confidence += timing_score
        else:
            # Check if observation windows overlap at all
            wifi_end = wifi.last_seen
            bt_end = bt.last_seen

            # If observation periods overlap
            if wifi.first_seen <= bt_end and bt.first_seen <= wifi_end:
                confidence += 0.25  # Partial credit for overlapping presence

        # Manufacturer match
        if wifi.manufacturer and bt.manufacturer:
            wifi_mfg = wifi.manufacturer.lower()
            bt_mfg = bt.manufacturer.lower()
            if wifi_mfg == bt_mfg:
                confidence += 0.2
            elif wifi_mfg[:5] == bt_mfg[:5]:  # Partial match
                confidence += 0.1

        # OUI match (first 3 octets of MAC)
        if wifi.mac[:8].upper() == bt.mac[:8].upper():
            confidence += 0.15

        # RSSI similarity
        if wifi.rssi is not None and bt.rssi is not None:
            rssi_diff = abs(wifi.rssi - bt.rssi)
            if rssi_diff <= self.rssi_threshold:
                rssi_score = 0.1 * (1 - rssi_diff / self.rssi_threshold)
                confidence += rssi_score

        # Both have names (suggests user device)
        if wifi.name and bt.name:
            confidence += 0.05

        return min(confidence, 1.0)

    def _get_correlation_reason(self, wifi: DeviceObservation, bt: DeviceObservation) -> str:
        """Generate human-readable reason for correlation."""
        reasons = []

        time_diff = abs((wifi.first_seen - bt.first_seen).total_seconds())
        if time_diff <= self.time_window.total_seconds():
            reasons.append(f"appeared within {int(time_diff)}s")

        wifi_oui = wifi.mac[:8].upper()
        bt_oui = bt.mac[:8].upper()
        if wifi_oui == bt_oui:
            reasons.append("same OUI")

        if wifi.manufacturer and bt.manufacturer and wifi.manufacturer.lower() == bt.manufacturer.lower():
            reasons.append(f"same manufacturer ({wifi.manufacturer})")

        if wifi.rssi is not None and bt.rssi is not None:
            rssi_diff = abs(wifi.rssi - bt.rssi)
            if rssi_diff <= self.rssi_threshold:
                reasons.append("similar signal strength")

        return "; ".join(reasons) if reasons else "timing overlap"


# Global correlator instance
correlator = DeviceCorrelator()


def get_correlations(
    wifi_devices: dict[str, dict] | None = None,
    bt_devices: dict[str, dict] | None = None,
    min_confidence: float = 0.5,
    include_historical: bool = True,
) -> list[dict]:
    """
    Get device correlations.

    Args:
        wifi_devices: Current WiFi devices (or None to use only historical)
        bt_devices: Current Bluetooth devices (or None to use only historical)
        min_confidence: Minimum confidence threshold
        include_historical: Include correlations from database

    Returns:
        List of correlations sorted by confidence
    """
    results = []

    # Get live correlations
    if wifi_devices and bt_devices:
        correlator.min_confidence = min_confidence
        results.extend(correlator.correlate(wifi_devices, bt_devices))

    # Get historical correlations from database
    if include_historical:
        try:
            historical = db_get_correlations(min_confidence)
            for h in historical:
                # Avoid duplicates
                existing = next(
                    (r for r in results if r["wifi_mac"] == h["wifi_mac"] and r["bt_mac"] == h["bt_mac"]), None
                )
                if not existing:
                    results.append(
                        {
                            "wifi_mac": h["wifi_mac"],
                            "bt_mac": h["bt_mac"],
                            "confidence": h["confidence"],
                            "reason": "historical correlation",
                            "first_seen": h["first_seen"],
                            "last_seen": h["last_seen"],
                        }
                    )
        except Exception as e:
            logger.debug(f"Failed to get historical correlations: {e}")

    # Sort by confidence
    results.sort(key=lambda x: x["confidence"], reverse=True)

    return results
