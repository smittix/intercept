"""Tests for SDR detection parsing (RTL-SDR and HackRF)."""

from unittest.mock import MagicMock, patch

import pytest

import utils.sdr.detection as detection_mod
from utils.sdr.base import SDRType
from utils.sdr.detection import detect_hackrf_devices, detect_rtlsdr_devices


@pytest.fixture(autouse=True)
def _clear_detection_caches():
    """Reset detection caches before each test."""
    detection_mod._hackrf_cache = []
    detection_mod._hackrf_cache_ts = 0.0
    yield


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
@patch("utils.sdr.detection.subprocess.Popen")
def test_detect_rtlsdr_devices_filters_empty_serial_entries(mock_popen, _mock_tool_path):
    """Ignore malformed rtl_test rows that have an empty SN field."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (
        "",
        "Found 3 device(s):\n  0:  ??C?, , SN:\n  1:  ??C?, , SN:\n  2:  RTLSDRBlog, Blog V4, SN: 1\n",
    )
    mock_popen.return_value = mock_proc

    devices = detect_rtlsdr_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.RTL_SDR
    assert devices[0].index == 2
    assert devices[0].name == "RTLSDRBlog, Blog V4"
    assert devices[0].serial == "1"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
@patch("utils.sdr.detection.subprocess.Popen")
def test_detect_rtlsdr_devices_uses_replace_decode_mode(mock_popen, _mock_tool_path):
    """Run rtl_test with tolerant decoding for malformed output bytes."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "Found 0 device(s):")
    mock_popen.return_value = mock_proc

    detect_rtlsdr_devices()

    _, kwargs = mock_popen.call_args
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/rtl_test")
@patch("utils.sdr.detection.subprocess.Popen")
def test_detect_rtlsdr_devices_timeout_graceful_recovery(mock_popen, _mock_tool_path):
    """Gracefully catch timeout, terminate with SIGINT, and recover parsed devices."""
    import subprocess

    mock_proc = MagicMock()
    # First communicate raises TimeoutExpired, second returns output captured so far
    mock_proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["rtl_test"], timeout=5),
        ("", "Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR, SN: 00000001\n"),
    ]
    mock_popen.return_value = mock_proc

    devices = detect_rtlsdr_devices()

    assert len(devices) == 1
    assert devices[0].serial == "00000001"
    assert mock_proc.send_signal.called


# ---- HackRF detection tests ----

HACKRF_INFO_OUTPUT = (
    "hackrf_info version: 2024.02.1\n"
    "libhackrf version: 2024.02.1 (0.9)\n"
    "Found HackRF\n"
    "Index: 0\n"
    "Serial number: 0000000000000000a06063c8234e925f\n"
    "Board ID Number: 2 (HackRF One)\n"
    "Firmware Version: 2024.02.1 (API:1.08)\n"
    "Part ID Number: 0xa000cb3c 0x00614764\n"
    "Hardware Revision: r9\n"
    "Hardware supported by installed firmware:\n"
    "    HackRF One\n"
)


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_from_stdout(mock_run, _mock_tool_path):
    """Parse HackRF device info from stdout."""
    mock_result = MagicMock()
    mock_result.stdout = HACKRF_INFO_OUTPUT
    mock_result.stderr = ""
    mock_run.return_value = mock_result

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.HACKRF
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"
    assert devices[0].index == 0


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_from_stderr(mock_run, _mock_tool_path):
    """Parse HackRF device info when output goes to stderr (newer firmware)."""
    mock_result = MagicMock()
    mock_result.stdout = ""
    mock_result.stderr = HACKRF_INFO_OUTPUT
    mock_run.return_value = mock_result

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].sdr_type == SDRType.HACKRF
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_nonzero_exit_with_valid_output(mock_run, _mock_tool_path):
    """Parse HackRF info even when hackrf_info exits non-zero (device busy)."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = HACKRF_INFO_OUTPUT
    mock_run.return_value = mock_result

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF One"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_fallback_no_serial(mock_run, _mock_tool_path):
    """Fallback detection when serial is missing but 'Found HackRF' present."""
    mock_result = MagicMock()
    mock_result.stdout = "Found HackRF\nBoard ID Number: 2 (HackRF One)\n"
    mock_result.stderr = ""
    mock_run.return_value = mock_result

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF One"
    assert devices[0].serial == "Unknown"


@patch("utils.sdr.detection.get_tool_path", return_value="/usr/bin/hackrf_info")
@patch("utils.sdr.detection.subprocess.run")
def test_detect_hackrf_parses_legacy_serial_format(mock_run, _mock_tool_path):
    """Accept legacy 'Serial Number' casing and spaced hex format."""
    mock_result = MagicMock()
    mock_result.stdout = (
        "Found HackRF\n"
        "Index: 0\n"
        "Serial Number: 0x00000000 00000000 a06063c8 234e925f\n"
        "Board ID Number: 3 (HackRF Pro)\n"
    )
    mock_result.stderr = ""
    mock_run.return_value = mock_result

    devices = detect_hackrf_devices()

    assert len(devices) == 1
    assert devices[0].name == "HackRF Pro"
    assert devices[0].serial == "0000000000000000a06063c8234e925f"
