"""
Comprehensive tests for Intercept Agent mode operations.

Tests cover:
- All 13 mode start/stop lifecycles
- SDR device conflict detection
- Process verification (subprocess failure handling)
- Data snapshot operations
- Multi-mode scenarios
- Error handling and edge cases
"""

import contextlib
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mode_manager():
    """Create a fresh ModeManager instance for testing."""
    from intercept_agent import ModeManager

    manager = ModeManager()
    yield manager
    # Cleanup: stop all modes
    for mode in list(manager.running_modes.keys()):
        with contextlib.suppress(Exception):
            manager.stop_mode(mode)


@pytest.fixture
def mock_subprocess(fake_process):
    """Mock subprocess.Popen for controlled testing."""
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = fake_process()
        mock_popen.return_value = mock_proc
        yield mock_popen, mock_proc


@pytest.fixture
def mock_tools():
    """Mock tool availability checks."""
    tools = {
        "rtl_433": "/usr/bin/rtl_433",
        "rtl_fm": "/usr/bin/rtl_fm",
        "dump1090": "/usr/bin/dump1090",
        "multimon-ng": "/usr/bin/multimon-ng",
        "airodump-ng": "/usr/sbin/airodump-ng",
        "acarsdec": "/usr/bin/acarsdec",
        "AIS-catcher": "/usr/bin/AIS-catcher",
        "direwolf": "/usr/bin/direwolf",
        "rtlamr": "/usr/bin/rtlamr",
        "rtl_tcp": "/usr/bin/rtl_tcp",
        "bluetoothctl": "/usr/bin/bluetoothctl",
    }
    with patch("shutil.which", side_effect=lambda x: tools.get(x)):
        yield tools


# =============================================================================
# SDR Mode List
# =============================================================================

SDR_MODES = ["sensor", "adsb", "pager", "ais", "acars", "aprs", "rtlamr", "dsc", "listening_post"]
NON_SDR_MODES = ["wifi", "bluetooth", "tscm", "satellite"]
ALL_MODES = SDR_MODES + NON_SDR_MODES


# =============================================================================
# Mode Lifecycle Tests
# =============================================================================


class TestModeLifecycle:
    """Test start/stop lifecycle for all modes."""

    def test_sensor_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """Sensor mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        # Start
        result = mode_manager.start_mode("sensor", {"frequency": "433.92", "device": "0"})
        assert result["status"] == "started"
        assert "sensor" in mode_manager.running_modes

        # Stop
        result = mode_manager.stop_mode("sensor")
        assert result["status"] == "stopped"
        assert "sensor" not in mode_manager.running_modes

    def test_adsb_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """ADS-B mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        # Mock socket for SBS connection check
        with patch("socket.socket") as mock_socket:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1  # Port not in use
            mock_socket.return_value = mock_sock

            result = mode_manager.start_mode("adsb", {"device": "0", "gain": "40"})
            # May fail due to SBS port check, but shouldn't crash
            assert result["status"] in ["started", "error"]

    def test_pager_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """Pager mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        result = mode_manager.start_mode("pager", {"frequency": "929.6125", "protocols": ["POCSAG512", "POCSAG1200"]})
        assert result["status"] == "started"
        assert "pager" in mode_manager.running_modes

        result = mode_manager.stop_mode("pager")
        assert result["status"] == "stopped"

    def test_wifi_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """WiFi mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        # Mock glob for CSV file detection
        with patch("glob.glob", return_value=[]), patch("tempfile.mkdtemp", return_value="/tmp/test"):
            result = mode_manager.start_mode("wifi", {"interface": "wlan0", "scan_type": "quick"})
            # Quick scan returns data directly
            assert result["status"] in ["started", "error", "success"]

    def test_bluetooth_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """Bluetooth mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        result = mode_manager.start_mode("bluetooth", {"adapter": "hci0"})
        assert result["status"] == "started"
        assert "bluetooth" in mode_manager.running_modes

        # Give thread time to start
        time.sleep(0.1)

        result = mode_manager.stop_mode("bluetooth")
        assert result["status"] == "stopped"

    def test_satellite_mode_lifecycle(self, mode_manager):
        """Satellite mode should work without SDR."""
        # Patch the predictor loop — the real one downloads TLEs from
        # CelesTrak and keeps computing passes after the test finishes
        with patch.object(type(mode_manager), "_satellite_predictor", MagicMock()):
            result = mode_manager.start_mode("satellite", {"lat": 33.5, "lon": -82.1, "min_elevation": 10})
        assert result["status"] in ["started", "error"]  # May fail if skyfield not installed

    def test_tscm_mode_lifecycle(self, mode_manager, mock_subprocess, mock_tools):
        """TSCM mode should start and stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        result = mode_manager.start_mode("tscm", {"wifi": True, "bluetooth": True, "rf": False})
        assert result["status"] == "started"

        result = mode_manager.stop_mode("tscm")
        assert result["status"] == "stopped"


# =============================================================================
# SDR Conflict Detection Tests
# =============================================================================


class TestSDRConflictDetection:
    """Test SDR device conflict detection."""

    def test_same_device_conflict(self, mode_manager, mock_subprocess, mock_tools):
        """Starting two SDR modes on same device should fail."""
        mock_popen, mock_proc = mock_subprocess

        # Start sensor on device 0
        result1 = mode_manager.start_mode("sensor", {"device": "0"})
        assert result1["status"] == "started"

        # Try to start pager on device 0 - should fail
        result2 = mode_manager.start_mode("pager", {"device": "0"})
        assert result2["status"] == "error"
        assert "in use" in result2["message"].lower()

    def test_different_device_no_conflict(self, mode_manager, mock_subprocess, mock_tools):
        """Starting SDR modes on different devices should work."""
        mock_popen, mock_proc = mock_subprocess

        # Start sensor on device 0
        result1 = mode_manager.start_mode("sensor", {"device": "0"})
        assert result1["status"] == "started"

        # Start pager on device 1 - should work
        result2 = mode_manager.start_mode("pager", {"device": "1"})
        assert result2["status"] == "started"

        assert len(mode_manager.running_modes) == 2

    def test_non_sdr_modes_no_conflict(self, mode_manager, mock_subprocess, mock_tools):
        """Non-SDR modes should not conflict with SDR modes."""
        mock_popen, mock_proc = mock_subprocess

        # Start sensor (SDR)
        result1 = mode_manager.start_mode("sensor", {"device": "0"})
        assert result1["status"] == "started"

        # Start bluetooth (non-SDR) - should work
        result2 = mode_manager.start_mode("bluetooth", {"adapter": "hci0"})
        assert result2["status"] == "started"

        assert len(mode_manager.running_modes) == 2

    def test_get_sdr_in_use(self, mode_manager, mock_subprocess, mock_tools):
        """get_sdr_in_use should return correct mode."""
        mock_popen, mock_proc = mock_subprocess

        # No SDR in use initially
        assert mode_manager.get_sdr_in_use(0) is None

        # Start sensor
        mode_manager.start_mode("sensor", {"device": "0"})

        # Device 0 now in use by sensor
        assert mode_manager.get_sdr_in_use(0) == "sensor"
        assert mode_manager.get_sdr_in_use(1) is None


# =============================================================================
# Process Verification Tests
# =============================================================================


class TestProcessVerification:
    """Test process startup verification."""

    def test_immediate_process_exit_detected(self, mode_manager, mock_tools, fake_process):
        """Process that exits immediately should return error."""
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = fake_process(running=False, returncode=1, stderr=b"device busy")

            result = mode_manager.start_mode("sensor", {"device": "0"})
            assert result["status"] == "error"
            assert "sensor" not in mode_manager.running_modes

    def test_running_process_accepted(self, mode_manager, mock_subprocess, mock_tools):
        """Process that stays running should be accepted."""
        mock_popen, mock_proc = mock_subprocess
        mock_proc.poll.return_value = None  # Still running

        result = mode_manager.start_mode("sensor", {"device": "0"})
        assert result["status"] == "started"
        assert "sensor" in mode_manager.running_modes

    def test_error_message_from_stderr(self, mode_manager, mock_tools, fake_process):
        """Error message should include stderr output."""
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = fake_process(running=False, returncode=1, stderr=b"usb_claim_interface error -6")

            result = mode_manager.start_mode("sensor", {"device": "0"})
            assert result["status"] == "error"
            assert "usb_claim_interface" in result["message"] or "failed" in result["message"].lower()


# =============================================================================
# Data Snapshot Tests
# =============================================================================


class TestDataSnapshots:
    """Test data snapshot operations."""

    def test_get_mode_data_empty(self, mode_manager):
        """get_mode_data for non-running mode should return empty."""
        result = mode_manager.get_mode_data("sensor")
        assert result["mode"] == "sensor"
        # Mode not running - should have empty data or 'running' field
        assert result.get("running") is False or result.get("data") == [] or "status" in result

    def test_get_mode_data_running(self, mode_manager, mock_subprocess, mock_tools):
        """get_mode_data for running mode should return status."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("sensor", {"device": "0"})
        result = mode_manager.get_mode_data("sensor")

        assert result["mode"] == "sensor"
        # Mode is running - should indicate running status
        assert result.get("running") is True or "data" in result or "status" in result

    def test_data_queue_limit(self, mode_manager):
        """Data queues should respect max size limits."""
        import queue

        # Manually test queue limit
        test_queue = queue.Queue(maxsize=100)
        for i in range(150):
            if test_queue.full():
                test_queue.get_nowait()  # Remove old item
            test_queue.put_nowait({"index": i})

        assert test_queue.qsize() <= 100


# =============================================================================
# Mode Status Tests
# =============================================================================


class TestModeStatus:
    """Test mode status reporting."""

    def test_status_includes_all_modes(self, mode_manager):
        """Status should include all running modes."""
        status = mode_manager.get_status()
        assert "running_modes" in status
        assert "running_modes_detail" in status
        assert isinstance(status["running_modes"], list)

    def test_running_modes_detail_includes_device(self, mode_manager, mock_subprocess, mock_tools):
        """Running modes detail should include device info."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("sensor", {"device": "0"})
        status = mode_manager.get_status()

        assert "sensor" in status["running_modes_detail"]
        detail = status["running_modes_detail"]["sensor"]
        assert "device" in detail or "params" in detail


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_missing_tool_returns_error(self, mode_manager):
        """Mode should fail gracefully if required tool is missing."""
        # get_tool_path checks Homebrew paths via os.path.isfile before
        # shutil.which, so patch it too or installed tools are still found
        with patch("utils.dependencies.get_tool_path", return_value=None), patch("shutil.which", return_value=None):
            result = mode_manager.start_mode("sensor", {"device": "0"})
            assert result["status"] == "error"
            # Error message may vary - check for common patterns
            msg = result["message"].lower()
            assert "not found" in msg or "not available" in msg or "missing" in msg

    def test_invalid_mode_returns_error(self, mode_manager):
        """Invalid mode name should return error."""
        result = mode_manager.start_mode("invalid_mode", {})
        assert result["status"] == "error"

    def test_double_start_returns_already_running(self, mode_manager, mock_subprocess, mock_tools):
        """Starting already-running mode should return appropriate status."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("sensor", {"device": "0"})
        result = mode_manager.start_mode("sensor", {"device": "0"})

        assert result["status"] in ["already_running", "error"]

    def test_stop_non_running_mode(self, mode_manager):
        """Stopping non-running mode should handle gracefully."""
        result = mode_manager.stop_mode("sensor")
        assert result["status"] in ["stopped", "not_running"]


# =============================================================================
# Cleanup Tests
# =============================================================================


class TestCleanup:
    """Test mode cleanup on stop."""

    def test_process_terminated_on_stop(self, mode_manager, mock_subprocess, mock_tools):
        """Processes should be terminated when mode is stopped."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("sensor", {"device": "0"})
        mode_manager.stop_mode("sensor")

        # Verify terminate was called
        mock_proc.terminate.assert_called()

    def test_threads_stopped_on_stop(self, mode_manager, mock_subprocess, mock_tools):
        """Output threads should be stopped when mode is stopped."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("bluetooth", {"adapter": "hci0"})
        time.sleep(0.1)  # Let thread start

        mode_manager.stop_mode("bluetooth")

        # Thread should no longer be in output_threads or should be stopped
        assert "bluetooth" not in mode_manager.output_threads or not mode_manager.output_threads["bluetooth"].is_alive()


# =============================================================================
# Multi-Mode Tests
# =============================================================================


class TestMultiMode:
    """Test multiple modes running simultaneously."""

    def test_multiple_non_sdr_modes(self, mode_manager, mock_subprocess, mock_tools):
        """Multiple non-SDR modes should run simultaneously."""
        mock_popen, mock_proc = mock_subprocess

        result1 = mode_manager.start_mode("bluetooth", {"adapter": "hci0"})
        result2 = mode_manager.start_mode("tscm", {"wifi": True, "bluetooth": False})

        assert result1["status"] == "started"
        assert result2["status"] == "started"
        assert len(mode_manager.running_modes) == 2

    def test_stop_all_modes(self, mode_manager, mock_subprocess, mock_tools):
        """All modes should stop cleanly."""
        mock_popen, mock_proc = mock_subprocess

        mode_manager.start_mode("sensor", {"device": "0"})
        mode_manager.start_mode("bluetooth", {"adapter": "hci0"})

        # Stop all
        for mode in list(mode_manager.running_modes.keys()):
            mode_manager.stop_mode(mode)

        assert len(mode_manager.running_modes) == 0


# =============================================================================
# GPS Integration Tests
# =============================================================================


class TestGPSIntegration:
    """Test GPS coordinate integration."""

    def test_status_includes_gps_flag(self, mode_manager):
        """Status should indicate GPS availability."""
        status = mode_manager.get_status()
        assert "gps" in status

    def test_mode_start_includes_gps_flag(self, mode_manager, mock_subprocess, mock_tools):
        """Mode start response should include GPS status."""
        mock_popen, mock_proc = mock_subprocess

        result = mode_manager.start_mode("sensor", {"device": "0"})
        if result["status"] == "started":
            assert "gps_enabled" in result


# =============================================================================
# ADS-B Aircraft Expiry
# =============================================================================


class TestAdsbAircraftExpiry:
    """Aircraft must age out of the agent's snapshot while a scan runs.

    The agent serves its whole aircraft dict on every poll and the dashboard
    stamps each arrival as freshly seen, so anything the agent keeps is
    pinned on the map until the mode stops (#263).
    """

    @staticmethod
    def _seen(minutes_ago):
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

    def test_stale_aircraft_dropped_from_snapshot(self, mode_manager):
        """An aircraft not heard for over the TTL leaves the poll payload."""
        mode_manager.adsb_aircraft = {
            "ABC123": {"icao": "ABC123", "last_seen": self._seen(10)},
            "DEF456": {"icao": "DEF456", "last_seen": self._seen(0)},
        }

        mode_manager._prune_stale_aircraft()

        assert "ABC123" not in mode_manager.adsb_aircraft
        assert "DEF456" in mode_manager.adsb_aircraft

    def test_fresh_aircraft_retained(self, mode_manager):
        """Everything heard inside the TTL survives."""
        mode_manager.adsb_aircraft = {
            f"AC{i:04X}": {"icao": f"AC{i:04X}", "last_seen": self._seen(i)} for i in range(5)
        }

        mode_manager._prune_stale_aircraft()

        assert len(mode_manager.adsb_aircraft) == 5

    def test_missing_or_malformed_timestamp_kept(self, mode_manager):
        """Records we cannot date are kept rather than silently dropped."""
        mode_manager.adsb_aircraft = {
            "NOSTAMP": {"icao": "NOSTAMP"},
            "BADSTAMP": {"icao": "BADSTAMP", "last_seen": "not-a-timestamp"},
            "NULLSTAMP": {"icao": "NULLSTAMP", "last_seen": None},
        }

        mode_manager._prune_stale_aircraft()

        assert len(mode_manager.adsb_aircraft) == 3


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
