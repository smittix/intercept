"""Tests for RFDetector (rtl_433 + hackrf_sweep control-link detection)."""

from __future__ import annotations

import json
import queue
from unittest.mock import MagicMock, patch

import pytest

from utils.drone.models import RFObservation
from utils.drone.rf_detector import RFDetector


@pytest.fixture
def detector():
    q = queue.Queue()
    return RFDetector(output_queue=q), q


def test_detector_not_running_initially(detector):
    det, q = detector
    assert not det.running


def test_rtl433_json_line_emits_observation(detector):
    det, q = detector
    rtl433_line = json.dumps(
        {
            "freq": 433920000,
            "rssi": -68.5,
            "protocol": "FrSky",
        }
    )
    det._handle_rtl433_line(rtl433_line)
    obs = q.get_nowait()
    assert isinstance(obs, RFObservation)
    assert obs.frequency_hz == 433_920_000
    assert obs.hardware == "RTL433"
    assert obs.rssi == -68.5


def test_rtl433_non_json_line_ignored(detector):
    det, q = detector
    det._handle_rtl433_line("not json at all")
    assert q.empty()


def test_hackrf_sweep_line_emits_observation(detector):
    det, q = detector
    # hackrf_sweep CSV: date, time, hz_low, hz_high, hz_bin_width, num_samples, db, db, ...
    hz_low = 2_440_000_000
    hz_high = 2_441_000_000
    sweep_line = f"2026-05-03, 12:00:00, {hz_low}, {hz_high}, 1000000, 10, -45.2, -46.1, -44.8"
    det._handle_hackrf_line(sweep_line)
    obs = q.get_nowait()
    assert isinstance(obs, RFObservation)
    assert obs.hardware == "HACKRF"
    assert obs.frequency_hz == (hz_low + hz_high) // 2
    assert obs.rssi < 0


def test_hackrf_sweep_below_threshold_ignored(detector):
    det, q = detector
    hz_low = 2_440_000_000
    hz_high = 2_441_000_000
    # Very low power — should be ignored (below -90 dBm threshold)
    sweep_line = f"2026-05-03, 12:00:00, {hz_low}, {hz_high}, 1000000, 10, -95.0, -96.0, -95.5"
    det._handle_hackrf_line(sweep_line)
    assert q.empty()


def test_out_of_band_frequency_ignored(detector):
    det, q = detector
    # 915 MHz is not in any drone band
    line = json.dumps({"freq": 915_000_000, "rssi": -50.0, "protocol": "Generic"})
    det._handle_rtl433_line(line)
    assert q.empty()


def test_start_stop(detector):
    det, q = detector
    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.readline = MagicMock(side_effect=[b""])
    # Patch both shutil.which calls (rtl_433 in _run_rtl433, hackrf_sweep in _run_hackrf)
    with (
        patch("subprocess.Popen", return_value=mock_proc),
        patch("utils.drone.rf_detector.shutil.which", return_value=None),
    ):
        det.start(rtl_sdr_index=0, use_hackrf=False)
    assert det.running
    det.stop()
    assert not det.running
