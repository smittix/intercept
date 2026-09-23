"""
Unit tests for Bluetooth proximity visualization features.

Tests device key stability, EMA smoothing, distance estimation,
band classification, and ring buffer functionality.
"""

from datetime import datetime, timedelta

import pytest

from utils.bluetooth.device_key import (
    extract_key_type,
    generate_device_key,
    is_randomized_mac,
)
from utils.bluetooth.distance import (
    RSSI_THRESHOLD_FAR,
    RSSI_THRESHOLD_IMMEDIATE,
    RSSI_THRESHOLD_NEAR,
    DistanceEstimator,
    ProximityBand,
)
from utils.bluetooth.ring_buffer import RingBuffer


class TestDeviceKey:
    """Tests for stable device key generation."""

    def test_identity_address_takes_priority(self):
        """Identity address should always be used when available."""
        key = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            identity_address="11:22:33:44:55:66",
            name="Test Device",
            manufacturer_id=76,
        )
        assert key == "id:11:22:33:44:55:66"

    def test_public_mac_used_directly(self):
        """Public MAC addresses should be used directly."""
        key = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="public",
        )
        assert key == "mac:AA:BB:CC:DD:EE:FF"

    def test_static_random_mac_used_directly(self):
        """Random static addresses should be used directly."""
        key = generate_device_key(
            address="CA:BB:CC:DD:EE:FF",
            address_type="random_static",
        )
        assert key == "mac:CA:BB:CC:DD:EE:FF"

    def test_random_address_fingerprint_with_name(self):
        """Random addresses should generate fingerprint from name."""
        key = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            name="AirPods Pro",
        )
        assert key.startswith("fp:")
        assert len(key) == 19  # 'fp:' + 16 hex chars

    def test_random_address_fingerprint_stability(self):
        """Same name/mfr/services should produce same fingerprint key."""
        key1 = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            name="AirPods Pro",
            manufacturer_id=76,
        )
        key2 = generate_device_key(
            address="11:22:33:44:55:66",  # Different address
            address_type="nrpa",
            name="AirPods Pro",
            manufacturer_id=76,
        )
        assert key1 == key2

    def test_different_names_produce_different_keys(self):
        """Different names should produce different fingerprint keys."""
        key1 = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            name="AirPods Pro",
        )
        key2 = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            name="AirPods Max",
        )
        assert key1 != key2

    def test_random_address_fallback_to_mac(self):
        """Random addresses without fingerprint data fall back to MAC."""
        key = generate_device_key(
            address="AA:BB:CC:DD:EE:FF",
            address_type="rpa",
            # No name, manufacturer, or services
        )
        assert key == "mac:AA:BB:CC:DD:EE:FF"

    def test_is_randomized_mac_public(self):
        """Public addresses are not randomized."""
        assert is_randomized_mac("public") is False

    def test_is_randomized_mac_random_static(self):
        """Random static addresses are not randomized."""
        assert is_randomized_mac("random_static") is False

    def test_is_randomized_mac_rpa(self):
        """RPA addresses are randomized."""
        assert is_randomized_mac("rpa") is True

    def test_is_randomized_mac_nrpa(self):
        """NRPA addresses are randomized."""
        assert is_randomized_mac("nrpa") is True

    def test_extract_key_type_id(self):
        """Extract type from identity key."""
        assert extract_key_type("id:11:22:33:44:55:66") == "id"

    def test_extract_key_type_mac(self):
        """Extract type from MAC key."""
        assert extract_key_type("mac:AA:BB:CC:DD:EE:FF") == "mac"

    def test_extract_key_type_fingerprint(self):
        """Extract type from fingerprint key."""
        assert extract_key_type("fp:abcd1234efgh5678") == "fp"


class TestDistanceEstimator:
    """Tests for distance estimation and EMA smoothing."""

    @pytest.fixture
    def estimator(self):
        """Create a distance estimator instance."""
        return DistanceEstimator()

    def test_ema_first_value_initializes(self, estimator):
        """First EMA value should equal the input."""
        ema = estimator.apply_ema_smoothing(current=-50, prev_ema=None)
        assert ema == -50.0

    def test_ema_subsequent_values_weighted(self, estimator):
        """Subsequent EMA values should be weighted correctly."""
        # Default alpha is 0.3
        # new_ema = 0.3 * current + 0.7 * prev_ema
        ema = estimator.apply_ema_smoothing(current=-60, prev_ema=-50.0)
        expected = 0.3 * (-60) + 0.7 * (-50)  # -18 + -35 = -53
        assert ema == expected

    def test_ema_custom_alpha(self, estimator):
        """Custom alpha should be applied correctly."""
        ema = estimator.apply_ema_smoothing(current=-60, prev_ema=-50.0, alpha=0.5)
        expected = 0.5 * (-60) + 0.5 * (-50)  # -30 + -25 = -55
        assert ema == expected

    def test_distance_with_tx_power_path_loss(self, estimator):
        """Distance should be calculated using path-loss formula with TX power."""
        # Formula: d = 10^((tx_power - rssi) / (10 * n)), n=2.5
        distance, confidence = estimator.estimate_distance(rssi=-69, tx_power=-59)
        # ((-59) - (-69)) / 25 = 10/25 = 0.4
        # 10^0.4 = ~2.51 meters
        assert 2.0 < distance < 3.0
        assert confidence >= 0.5  # Higher confidence with TX power

    def test_distance_without_tx_power_band_based(self, estimator):
        """Distance should use band estimation without TX power."""
        distance, confidence = estimator.estimate_distance(rssi=-50, tx_power=None)
        assert distance is not None
        assert confidence < 0.5  # Lower confidence without TX power

    def test_distance_null_rssi(self, estimator):
        """Null RSSI should return None distance."""
        distance, confidence = estimator.estimate_distance(rssi=None)
        assert distance is None
        assert confidence == 0.0

    def test_band_classification_immediate(self, estimator):
        """Strong RSSI should classify as immediate."""
        band = estimator.classify_proximity_band(rssi_ema=-35)
        assert band == ProximityBand.IMMEDIATE

    def test_band_classification_near(self, estimator):
        """Medium RSSI should classify as near."""
        band = estimator.classify_proximity_band(rssi_ema=-50)
        assert band == ProximityBand.NEAR

    def test_band_classification_far(self, estimator):
        """Weak RSSI should classify as far."""
        band = estimator.classify_proximity_band(rssi_ema=-70)
        assert band == ProximityBand.FAR

    def test_band_classification_unknown(self, estimator):
        """Very weak or null RSSI should classify as unknown."""
        band = estimator.classify_proximity_band(rssi_ema=-80)
        assert band == ProximityBand.UNKNOWN

        band = estimator.classify_proximity_band(rssi_ema=None)
        assert band == ProximityBand.UNKNOWN

    def test_band_classification_by_distance(self, estimator):
        """Distance-based classification should work."""
        assert estimator.classify_proximity_band(distance_m=0.5) == ProximityBand.IMMEDIATE
        assert estimator.classify_proximity_band(distance_m=2.0) == ProximityBand.NEAR
        assert estimator.classify_proximity_band(distance_m=5.0) == ProximityBand.FAR
        assert estimator.classify_proximity_band(distance_m=15.0) == ProximityBand.UNKNOWN

    def test_confidence_higher_with_tx_power(self, estimator):
        """Confidence should be higher with TX power than without."""
        _, conf_with_tx = estimator.estimate_distance(rssi=-60, tx_power=-59)
        _, conf_without_tx = estimator.estimate_distance(rssi=-60, tx_power=None)
        assert conf_with_tx > conf_without_tx

    def test_confidence_lower_with_high_variance(self, estimator):
        """High variance should reduce confidence."""
        _, conf_low_var = estimator.estimate_distance(rssi=-60, tx_power=-59, variance=10)
        _, conf_high_var = estimator.estimate_distance(rssi=-60, tx_power=-59, variance=150)
        assert conf_low_var > conf_high_var

    def test_get_rssi_60s_window(self, estimator):
        """60-second window should return correct min/max."""
        now = datetime.now()
        samples = [
            (now - timedelta(seconds=30), -50),
            (now - timedelta(seconds=20), -60),
            (now - timedelta(seconds=10), -55),
            (now - timedelta(seconds=90), -40),  # Outside window
        ]
        min_rssi, max_rssi = estimator.get_rssi_60s_window(samples, window_seconds=60)
        assert min_rssi == -60
        assert max_rssi == -50

    def test_get_rssi_60s_window_empty(self, estimator):
        """Empty samples should return None."""
        min_rssi, max_rssi = estimator.get_rssi_60s_window([])
        assert min_rssi is None
        assert max_rssi is None


class TestRingBuffer:
    """Tests for ring buffer time-windowed storage."""

    @pytest.fixture
    def buffer(self):
        """Create a ring buffer instance."""
        return RingBuffer(
            retention_minutes=30,
            min_interval_seconds=2.0,
            max_observations_per_device=100,
        )

    def test_ingest_new_device(self, buffer):
        """Ingesting a new device should succeed."""
        now = datetime.now()
        result = buffer.ingest("device:1", rssi=-50, timestamp=now)
        assert result is True
        assert buffer.get_device_count() == 1
        assert buffer.get_observation_count("device:1") == 1

    def test_ingest_rate_limited(self, buffer):
        """Ingestion should be rate-limited to min_interval."""
        now = datetime.now()
        buffer.ingest("device:1", rssi=-50, timestamp=now)

        # Try to ingest again within rate limit (1 second later)
        result = buffer.ingest("device:1", rssi=-55, timestamp=now + timedelta(seconds=1))
        assert result is False
        assert buffer.get_observation_count("device:1") == 1

    def test_ingest_after_interval(self, buffer):
        """Ingestion should succeed after min_interval."""
        now = datetime.now()
        buffer.ingest("device:1", rssi=-50, timestamp=now)

        # Ingest after rate limit passes (3 seconds later)
        result = buffer.ingest("device:1", rssi=-55, timestamp=now + timedelta(seconds=3))
        assert result is True
        assert buffer.get_observation_count("device:1") == 2

    def test_prune_old_observations(self, buffer):
        """Old observations should be pruned."""
        now = datetime.now()
        old_time = now - timedelta(minutes=45)  # Older than retention

        buffer.ingest("device:1", rssi=-50, timestamp=old_time)
        buffer.ingest("device:2", rssi=-60, timestamp=now)

        removed = buffer.prune_old()
        assert removed == 1
        assert buffer.get_device_count() == 1

    def test_get_timeseries(self, buffer):
        """Timeseries should return downsampled data."""
        now = datetime.now()

        # Add observations
        for i in range(10):
            ts = now - timedelta(seconds=i * 5)
            buffer.ingest("device:1", rssi=-50 - i, timestamp=ts)

        timeseries = buffer.get_timeseries("device:1", window_minutes=5, downsample_seconds=10)
        assert isinstance(timeseries, list)
        assert len(timeseries) > 0

        for point in timeseries:
            assert "timestamp" in point
            assert "rssi" in point

    def test_get_timeseries_empty_device(self, buffer):
        """Unknown device should return empty timeseries."""
        timeseries = buffer.get_timeseries("unknown:device")
        assert timeseries == []

    def test_get_all_timeseries_sorted_by_recency(self, buffer):
        """All timeseries should be sorted by recency."""
        now = datetime.now()
        buffer.ingest("device:old", rssi=-50, timestamp=now - timedelta(minutes=5))
        buffer.ingest("device:new", rssi=-60, timestamp=now)

        all_ts = buffer.get_all_timeseries(sort_by="recency")
        keys = list(all_ts.keys())
        assert keys[0] == "device:new"  # Most recent first

    def test_get_all_timeseries_sorted_by_strength(self, buffer):
        """All timeseries should be sortable by signal strength."""
        now = datetime.now()
        buffer.ingest("device:weak", rssi=-80, timestamp=now)
        buffer.ingest("device:strong", rssi=-40, timestamp=now + timedelta(seconds=3))

        all_ts = buffer.get_all_timeseries(sort_by="strength")
        keys = list(all_ts.keys())
        assert keys[0] == "device:strong"  # Strongest first

    def test_get_all_timeseries_top_n_limit(self, buffer):
        """Top N should limit returned devices."""
        now = datetime.now()
        for i in range(10):
            buffer.ingest(f"device:{i}", rssi=-50, timestamp=now + timedelta(seconds=i * 3))

        all_ts = buffer.get_all_timeseries(top_n=5)
        assert len(all_ts) == 5

    def test_clear(self, buffer):
        """Clear should remove all observations."""
        now = datetime.now()
        buffer.ingest("device:1", rssi=-50, timestamp=now)
        buffer.ingest("device:2", rssi=-60, timestamp=now)

        buffer.clear()
        assert buffer.get_device_count() == 0

    def test_downsampling_bucket_average(self, buffer):
        """Downsampling should average RSSI in each bucket."""
        # Anchor to a bucket boundary. _downsample() buckets on the wall-clock
        # second, so starting at an arbitrary now means now+2s crosses into the
        # next 10s bucket whenever now.second is 8 or 9 -- a ~20% failure rate.
        now = datetime.now().replace(microsecond=0)
        now -= timedelta(seconds=now.second % 10)

        # Add multiple observations in same 10s bucket
        buffer._observations["device:1"] = [
            (now, -50),
            (now + timedelta(seconds=1), -60),
            (now + timedelta(seconds=2), -55),
        ]
        buffer._last_ingested["device:1"] = now + timedelta(seconds=2)

        timeseries = buffer.get_timeseries("device:1", window_minutes=5, downsample_seconds=10)
        assert len(timeseries) == 1
        # Average of -50, -60, -55 = -55
        assert timeseries[0]["rssi"] == -55.0

    def test_get_device_stats(self, buffer):
        """Device stats should return correct values."""
        now = datetime.now()
        buffer._observations["device:1"] = [
            (now - timedelta(seconds=10), -50),
            (now - timedelta(seconds=5), -60),
            (now, -55),
        ]

        stats = buffer.get_device_stats("device:1")
        assert stats is not None
        assert stats["observation_count"] == 3
        assert stats["rssi_min"] == -60
        assert stats["rssi_max"] == -50
        assert stats["rssi_avg"] == -55.0

    def test_get_device_stats_unknown_device(self, buffer):
        """Unknown device should return None."""
        stats = buffer.get_device_stats("unknown:device")
        assert stats is None


class TestProximityBand:
    """Tests for ProximityBand enum."""

    def test_proximity_band_str(self):
        """ProximityBand should convert to string correctly."""
        assert str(ProximityBand.IMMEDIATE) == "immediate"
        assert str(ProximityBand.NEAR) == "near"
        assert str(ProximityBand.FAR) == "far"
        assert str(ProximityBand.UNKNOWN) == "unknown"

    def test_proximity_band_values(self):
        """ProximityBand values should match expected strings."""
        assert ProximityBand.IMMEDIATE.value == "immediate"
        assert ProximityBand.NEAR.value == "near"
        assert ProximityBand.FAR.value == "far"
        assert ProximityBand.UNKNOWN.value == "unknown"


class TestRssiThresholds:
    """Tests for RSSI threshold constants."""

    def test_threshold_order(self):
        """Thresholds should be in descending order."""
        assert RSSI_THRESHOLD_IMMEDIATE > RSSI_THRESHOLD_NEAR
        assert RSSI_THRESHOLD_NEAR > RSSI_THRESHOLD_FAR

    def test_threshold_values(self):
        """Threshold values should match expected dBm levels."""
        assert RSSI_THRESHOLD_IMMEDIATE == -40
        assert RSSI_THRESHOLD_NEAR == -55
        assert RSSI_THRESHOLD_FAR == -75
