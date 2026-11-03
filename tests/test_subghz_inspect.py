"""Tests for pulse-level inspection of SubGHz captures (utils/subghz_inspect.py)."""

from __future__ import annotations

import json
import shutil
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from utils import subghz_inspect
from utils.subghz import SubGhzManager

# rtl_433 22.11 pulse analyzer output for four repeats of a 28-bit PWM frame
ANALYZER_REPORT = """\
Test mode active. Reading samples from file: /tmp/x.cs8
Detected OOK package\t@0.020001s
Analyzing pulses...
Total count:  112,  width: 157.60 ms\t\t(315204 S)
Pulse width distribution:
 [ 0] count:   60,  width:  800 us [800;802]\t(1600 S)
 [ 1] count:   52,  width:  400 us [400;401]\t( 800 S)
Gap width distribution:
 [ 0] count:   60,  width:  400 us [399;400]\t( 799 S)
 [ 1] count:   48,  width:  800 us [799;800]\t(1599 S)
 [ 2] count:    3,  width: 8800 us [8800;8800]\t(17599 S)
Pulse period distribution:
 [ 0] count:  108,  width: 1200 us [1199;1202]\t(2400 S)
 [ 1] count:    3,  width: 9200 us [9200;9200]\t(18400 S)
Level estimates [high, low]:   3467,      9
RSSI: -6.7 dB SNR: 25.9 dB Noise: -32.6 dB
Frequency offsets [F1, F2]:    3292,      0\t(+100.5 kHz, +0.0 kHz)
Guessing modulation: Pulse Width Modulation with multiple packets
view at https://triq.org/pdv/#AAB024
Attempting demodulation... short_width: 400, long_width: 800, reset_limit: 8800, sync_width: 0
Use a flex decoder with -X 'n=name,m=OOK_PWM,s=400,l=800,r=8800,g=800,t=160,y=0'
pulse_slicer_pwm(): Analyzer Device
bitbuffer:: Number of rows: 2
[00] {28} 4c 70 ad 30 : 01001100 01110000 10101101 0011
[01] {28} 4c 70 ad 30 : 01001100 01110000 10101101 0011
"""

# rtl_433 25.12 reports the same package differently: the period heading is
# "Pulse+gap period distribution:", two more distributions follow, and the
# sliced rows are a "codes" line of {bits}nibbles instead of bitbuffer rows
ANALYZER_REPORT_25 = """\
rtl_433 version 25.12 (2025-12-12) inputs file rtl_tcp RTL-SDR SoapySDR with TLS
Disabling all device decoders.
\x1b[102;92m[\x1b[30mInput\x1b[92m]\x1b[0m Test mode active. Reading samples from file: cs8:/tmp/x.cs8
Detected OOK package\t@0.020001s
Analyzing pulses...
Total count:  112,  width: 157.60 ms\t\t(157605 S)
Pulse width distribution:
 [ 0] count:   60,  width:  800 us [799;804]\t( 800 S)
 [ 1] count:   52,  width:  400 us [400;402]\t( 400 S)
Gap width distribution:
 [ 0] count:   60,  width:  399 us [398;400]\t( 399 S)
 [ 1] count:   48,  width:  799 us [798;801]\t( 799 S)
 [ 2] count:    3,  width: 8799 us [8799;8800]\t(8799 S)
Pulse+gap period distribution:
 [ 0] count:  108,  width: 1200 us [1198;1204]\t(1200 S)
 [ 1] count:    3,  width: 9199 us [9199;9200]\t(9199 S)
Gap+pulse period distribution:
 [ 0] count:   33,  width:  800 us [799;804]\t( 800 S)
 [ 1] count:   28,  width: 1599 us [1599;1601]\t(1599 S)
Timing distribution:
 [ 0] count:  108,  width:  800 us [798;804]\t( 800 S)
Level estimates [high, low]:   3412,     15
RSSI: -6.8 dB SNR: 23.6 dB Noise: -30.4 dB
Frequency offsets [F1, F2]:    3225,      0\t(+49.2 kHz, +0.0 kHz)
Guessing modulation: Pulse Width Modulation with multiple packets
view at https://triq.org/pdv/#AAB024
Attempting demodulation... short_width: 400, long_width: 800, reset_limit: 8801, sync_width: 0
Use a flex decoder with -X 'n=name,m=OOK_PWM,s=400,l=800,r=8801,g=802,t=160,y=0'
\x1b[104;94m[\x1b[37mpulse_slicer_pwm\x1b[94m]\x1b[0m Analyzer Device
codes     : {28}4c70ad3, {28}4c70ad3, {5}a8

"""

FRAME_BITS = "1011001110001111010100101100"


def synth_pwm_capture(sample_rate: int, repeats: int = 4) -> bytes:
    """cs8 IQ of a PWM frame (800 us pulse = 1, 400 us = 0) on a 50 kHz offset carrier."""

    def samples(us: int) -> int:
        return int(us * sample_rate / 1e6)

    parts = [np.zeros(samples(20_000))]
    for _ in range(repeats):
        for bit in FRAME_BITS:
            on, off = (800, 400) if bit == "1" else (400, 800)
            parts += [np.ones(samples(on)), np.zeros(samples(off))]
        parts.append(np.zeros(samples(8_000)))
    parts.append(np.zeros(samples(20_000)))
    env = np.concatenate(parts)
    rng = np.random.default_rng(1)
    t = np.arange(env.size)
    sig = env * np.exp(2j * np.pi * 50_000 * t / sample_rate) * 60
    sig += (rng.normal(size=env.size) + 1j * rng.normal(size=env.size)) * 3
    iq = np.empty(env.size * 2, dtype=np.int8)
    iq[0::2] = np.clip(sig.real, -127, 127)
    iq[1::2] = np.clip(sig.imag, -127, 127)
    return iq.tobytes()


class TestParsePulseAnalyzer:
    def test_parses_a_package(self):
        (package,) = subghz_inspect.parse_pulse_analyzer(ANALYZER_REPORT)
        assert package["kind"] == "OOK"
        assert package["at_seconds"] == pytest.approx(0.020001)
        assert package["pulse_widths"] == [{"count": 60, "width_us": 800}, {"count": 52, "width_us": 400}]
        assert [g["width_us"] for g in package["gap_widths"]] == [400, 800, 8800]
        assert [p["width_us"] for p in package["periods"]] == [1200, 9200]
        assert package["modulation"] == "Pulse Width Modulation with multiple packets"
        assert package["rssi_db"] == -6.7
        assert package["snr_db"] == 25.9
        assert package["flex_spec"] == "n=name,m=OOK_PWM,s=400,l=800,r=8800,g=800,t=160,y=0"
        assert package["rows"] == [
            {"bits": 28, "hex": "4c70ad30", "binary": "0100110001110000101011010011"},
            {"bits": 28, "hex": "4c70ad30", "binary": "0100110001110000101011010011"},
        ]

    def test_parses_rtl_433_25_report(self):
        (package,) = subghz_inspect.parse_pulse_analyzer(ANALYZER_REPORT_25)
        assert [p["width_us"] for p in package["pulse_widths"]] == [800, 400]
        assert [p["width_us"] for p in package["periods"]] == [1200, 9199]
        assert package["flex_spec"] == "n=name,m=OOK_PWM,s=400,l=800,r=8801,g=802,t=160,y=0"
        assert package["rows"] == [
            {"bits": 28, "hex": "4c70ad3", "binary": "0100110001110000101011010011"},
            {"bits": 28, "hex": "4c70ad3", "binary": "0100110001110000101011010011"},
            {"bits": 5, "hex": "a8", "binary": "10101"},
        ]

    def test_ook_settings_from_flex(self):
        (package,) = subghz_inspect.parse_pulse_analyzer(ANALYZER_REPORT)
        assert package["ook"] == {
            "encoding": "pwm",
            "short_pulse": 400,
            "long_pulse": 800,
            "reset_limit": 8800,
            "gap_limit": 800,
            "tolerance": 160,
        }

    def test_splits_multiple_packages(self):
        second = ANALYZER_REPORT.replace("@0.020001s", "@0.500000s").split("\n", 1)[1]
        packages = subghz_inspect.parse_pulse_analyzer(ANALYZER_REPORT + second)
        assert [p["at_seconds"] for p in packages] == [pytest.approx(0.020001), pytest.approx(0.5)]
        assert all(len(p["rows"]) == 2 for p in packages)

    def test_no_packages(self):
        assert subghz_inspect.parse_pulse_analyzer("Test mode active. Reading samples from file\n") == []


class TestOokSettings:
    def test_manchester(self):
        flex = subghz_inspect.parse_flex_spec("n=name,m=OOK_MC_ZEROBIT,s=500,l=0,r=2000")
        assert subghz_inspect.ook_settings(flex) == {"encoding": "manchester", "short_pulse": 500, "reset_limit": 2000}

    def test_modulation_ook_mode_cannot_decode(self):
        flex = subghz_inspect.parse_flex_spec("n=name,m=FSK_PCM,s=52,l=52,r=1000")
        assert subghz_inspect.ook_settings(flex) is None


class TestSignalHelpers:
    def test_decimate_to_about_one_msps(self):
        iq = np.ones(20_000_000 // 10, dtype=np.complex64)
        out, rate = subghz_inspect.decimate(iq, 20_000_000)
        assert rate == 1_000_000
        assert out.size == iq.size // 20

    def test_no_decimation_at_or_below_target(self):
        iq = np.ones(1000, dtype=np.complex64)
        out, rate = subghz_inspect.decimate(iq, 1_000_000)
        assert rate == 1_000_000 and out is iq

    def test_envelope_resolution_and_scale(self):
        iq = np.zeros(100_000, dtype=np.complex64)
        iq[50_000:50_010] = 10
        columns, per_column = subghz_inspect.envelope_columns(iq, max_columns=40_000)
        assert per_column == 3
        assert len(columns) == 100_000 // 3
        assert max(columns) == 1.0
        assert columns[0] == 0.0

    def test_read_iq_window(self, tmp_path):
        path = tmp_path / "c.iq"
        path.write_bytes(bytes([1, 2, 3, 4, 5, 6, 7, 8]))
        iq = subghz_inspect.read_iq_window(path, sample_rate=1, start_seconds=1, duration_seconds=2)
        assert list(iq) == [3 + 4j, 5 + 6j]


@pytest.fixture
def manager(tmp_path):
    data_dir = tmp_path / "subghz"
    (data_dir / "captures").mkdir(parents=True)
    return SubGhzManager(data_dir=data_dir)


def save_capture(manager, iq: bytes, sample_rate: int, bursts: list[dict]) -> str:
    captures = manager._captures_dir
    (captures / "cap1.iq").write_bytes(iq)
    meta = {
        "id": "cap1",
        "filename": "cap1.iq",
        "frequency_hz": 433_920_000,
        "sample_rate": sample_rate,
        "lna_gain": 32,
        "vga_gain": 20,
        "timestamp": "2026-09-29T00:00:00Z",
        "bursts": bursts,
    }
    (captures / "cap1.json").write_text(json.dumps(meta))
    return "cap1"


class TestManagerInspect:
    def test_burst_window_is_padded(self, manager):
        capture_id = save_capture(
            manager, b"\x00" * 4_000_000, 2_000_000, [{"start_seconds": 0.5, "duration_seconds": 0.2}]
        )
        with (
            patch.object(manager, "_resolve_tool", return_value="/usr/bin/rtl_433"),
            patch("utils.subghz.subghz_inspect.inspect_window", return_value={"status": "ok"}) as inspect,
        ):
            result = manager.inspect_capture(capture_id, 0)
        _, sample_rate, start, duration, _ = inspect.call_args.args
        assert sample_rate == 2_000_000
        assert start == pytest.approx(0.48)
        assert duration == pytest.approx(0.24)
        assert result["burst_count"] == 1
        assert result["truncated"] is False
        assert result["frequency_hz"] == 433_920_000

    def test_long_window_is_truncated(self, manager):
        capture_id = save_capture(manager, b"\x00" * 20_000_000, 2_000_000, [])
        with (
            patch.object(manager, "_resolve_tool", return_value="/usr/bin/rtl_433"),
            patch("utils.subghz.subghz_inspect.inspect_window", return_value={"status": "ok"}) as inspect,
        ):
            result = manager.inspect_capture(capture_id, None)
        assert inspect.call_args.args[3] == subghz_inspect.MAX_WINDOW_SECONDS
        assert result["truncated"] is True

    def test_unknown_burst(self, manager):
        capture_id = save_capture(manager, b"\x00" * 1000, 2_000_000, [])
        with patch.object(manager, "_resolve_tool", return_value="/usr/bin/rtl_433"):
            result = manager.inspect_capture(capture_id, 3)
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_missing_rtl_433(self, manager):
        capture_id = save_capture(manager, b"\x00" * 1000, 2_000_000, [])
        with patch.object(manager, "_resolve_tool", return_value=None):
            result = manager.inspect_capture(capture_id, None)
        assert result["status"] == "error"
        assert "rtl_433" in result["message"]

    def test_unknown_capture(self, manager):
        assert manager.inspect_capture("nope", None)["status"] == "error"


@pytest.mark.skipif(not shutil.which("rtl_433"), reason="rtl_433 not installed")
@pytest.mark.parametrize("sample_rate", [2_000_000, 10_000_000])
def test_real_rtl_433_decodes_synthetic_capture(manager, sample_rate):
    capture_id = save_capture(manager, synth_pwm_capture(sample_rate), sample_rate, [])
    result = manager.inspect_capture(capture_id, None)
    assert result["status"] == "ok"
    assert result["window"]["analysis_sample_rate"] == 1_000_000
    (package,) = result["packages"]
    assert package["ook"]["encoding"] == "pwm"
    assert package["ook"]["short_pulse"] == pytest.approx(400, abs=10)
    assert package["ook"]["long_pulse"] == pytest.approx(800, abs=10)
    # rtl_433's PWM slicer reads the short pulse as 1, so the frame comes back inverted
    inverted = FRAME_BITS.translate(str.maketrans("01", "10"))
    assert [row["binary"] for row in package["rows"]] == [inverted] * 4


class TestInspectRoute:
    @pytest.fixture
    def auth_client(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        return client

    def test_passes_burst_index(self, auth_client):
        with patch("routes.subghz.get_subghz_manager") as mock_get:
            mock_get.return_value = MagicMock(inspect_capture=MagicMock(return_value={"status": "ok", "packages": []}))
            resp = auth_client.get("/subghz/captures/abc123/inspect?burst=2")
        assert resp.status_code == 200
        mock_get.return_value.inspect_capture.assert_called_once_with("abc123", 2)

    def test_whole_capture_when_no_burst(self, auth_client):
        with patch("routes.subghz.get_subghz_manager") as mock_get:
            mock_get.return_value = MagicMock(inspect_capture=MagicMock(return_value={"status": "ok"}))
            auth_client.get("/subghz/captures/abc123/inspect")
        mock_get.return_value.inspect_capture.assert_called_once_with("abc123", None)

    @pytest.mark.parametrize("burst", ["x", "-1"])
    def test_rejects_bad_burst(self, auth_client, burst):
        assert auth_client.get(f"/subghz/captures/abc123/inspect?burst={burst}").status_code == 400

    def test_rejects_bad_capture_id(self, auth_client):
        assert auth_client.get("/subghz/captures/abc-123/inspect").status_code == 400

    def test_not_found_is_404(self, auth_client):
        with patch("routes.subghz.get_subghz_manager") as mock_get:
            mock_get.return_value = MagicMock(
                inspect_capture=MagicMock(return_value={"status": "error", "message": "Capture not found: abc"})
            )
            assert auth_client.get("/subghz/captures/abc/inspect").status_code == 404
