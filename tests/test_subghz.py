"""Tests for SubGhzManager utility module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from utils.subghz import SubGhzCapture, SubGhzManager


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory for SubGhz captures."""
    data_dir = tmp_path / "subghz"
    data_dir.mkdir()
    (data_dir / "captures").mkdir()
    return data_dir


@pytest.fixture
def manager(tmp_data_dir):
    """Create a SubGhzManager with temp directory."""
    return SubGhzManager(data_dir=tmp_data_dir)


class TestSubGhzManagerInit:
    def test_creates_data_dirs(self, tmp_path):
        data_dir = tmp_path / "new_subghz"
        SubGhzManager(data_dir=data_dir)
        assert (data_dir / "captures").is_dir()

    def test_active_mode_idle(self, manager):
        assert manager.active_mode == "idle"

    def test_get_status_idle(self, manager):
        status = manager.get_status()
        assert status["mode"] == "idle"


class TestToolDetection:
    def test_check_hackrf_found(self, manager):
        with patch("shutil.which", return_value="/usr/bin/hackrf_transfer"):
            assert manager.check_hackrf() is True

    def test_check_hackrf_not_found(self, manager):
        with patch("shutil.which", return_value=None), patch("utils.subghz.get_tool_path", return_value=None):
            manager._hackrf_available = None  # reset cache
            assert manager.check_hackrf() is False

    def test_check_rtl433_found(self, manager):
        with patch("shutil.which", return_value="/usr/bin/rtl_433"):
            assert manager.check_rtl433() is True

    def test_check_sweep_found(self, manager):
        with patch("shutil.which", return_value="/usr/bin/hackrf_sweep"):
            assert manager.check_sweep() is True


class TestReceive:
    def test_start_receive_no_hackrf(self, manager):
        with patch("shutil.which", return_value=None), patch("utils.subghz.get_tool_path", return_value=None):
            manager._hackrf_available = None
            result = manager.start_receive(frequency_hz=433920000)
            assert result["status"] == "error"
            assert "not found" in result["message"]

    def test_start_receive_success(self, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.readline = MagicMock(return_value=b"")

        with (
            patch("shutil.which", return_value="/usr/bin/hackrf_transfer"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch.object(manager, "check_hackrf_device", return_value=True),
            patch("utils.subghz.register_process"),
        ):
            manager._hackrf_available = None
            result = manager.start_receive(
                frequency_hz=433920000,
                sample_rate=2000000,
                lna_gain=32,
                vga_gain=20,
            )
            assert result["status"] == "started"
            assert result["frequency_hz"] == 433920000
            assert manager.active_mode == "rx"

    def test_start_receive_already_running(self, manager):
        import time as _time

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager._rx_process = mock_proc
        # Pre-lock device checks now run before active_mode guard. start_receive
        # resolves hackrf_transfer directly rather than going through
        # check_hackrf(), so stubbing _hackrf_available is not enough on a host
        # without the binary installed.
        manager._hackrf_device_cache = True
        manager._hackrf_device_cache_ts = _time.time()

        with patch.object(manager, "_resolve_tool", return_value="/usr/bin/hackrf_transfer"):
            result = manager.start_receive(frequency_hz=433920000)
        assert result["status"] == "error"
        assert "Already running" in result["message"]

    def test_stop_receive_not_running(self, manager):
        result = manager.stop_receive()
        assert result["status"] == "not_running"

    def test_stop_receive_creates_metadata(self, manager, tmp_data_dir):
        # Create a fake IQ file
        iq_file = tmp_data_dir / "captures" / "test.iq"
        iq_file.write_bytes(b"\x00" * 1024)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager._rx_process = mock_proc
        manager._rx_file = iq_file
        manager._rx_frequency_hz = 433920000
        manager._rx_sample_rate = 2000000
        manager._rx_lna_gain = 32
        manager._rx_vga_gain = 20
        manager._rx_start_time = 1000.0
        manager._rx_bursts = [{"start_seconds": 1.23, "duration_seconds": 0.15, "peak_level": 42}]

        with patch("utils.subghz.safe_terminate"), patch("time.time", return_value=1005.0):
            result = manager.stop_receive()

        assert result["status"] == "stopped"
        assert "capture" in result
        assert result["capture"]["frequency_hz"] == 433920000

        # Verify JSON sidecar was written
        meta_path = iq_file.with_suffix(".json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["frequency_hz"] == 433920000
        assert isinstance(meta.get("bursts"), list)
        assert meta["bursts"][0]["peak_level"] == 42


class TestTxSafety:
    def test_validate_tx_frequency_ism_433(self):
        result = SubGhzManager.validate_tx_frequency(433920000)
        assert result is None  # Valid

    def test_validate_tx_frequency_ism_315(self):
        result = SubGhzManager.validate_tx_frequency(315000000)
        assert result is None

    def test_validate_tx_frequency_ism_915(self):
        result = SubGhzManager.validate_tx_frequency(915000000)
        assert result is None

    def test_validate_tx_frequency_out_of_band(self):
        result = SubGhzManager.validate_tx_frequency(100000000)  # 100 MHz
        assert result is not None
        assert "outside allowed TX bands" in result

    def test_validate_tx_frequency_between_bands(self):
        result = SubGhzManager.validate_tx_frequency(500000000)  # 500 MHz
        assert result is not None

    def test_transmit_no_hackrf(self, manager):
        with patch("shutil.which", return_value=None), patch("utils.subghz.get_tool_path", return_value=None):
            manager._hackrf_available = None
            result = manager.transmit(capture_id="abc123")
            assert result["status"] == "error"

    def test_transmit_capture_not_found(self, manager):
        with (
            patch("shutil.which", return_value="/usr/bin/hackrf_transfer"),
            patch.object(manager, "check_hackrf_device", return_value=True),
        ):
            manager._hackrf_available = None
            result = manager.transmit(capture_id="nonexistent")
            assert result["status"] == "error"
            assert "not found" in result["message"]

    def test_transmit_out_of_band_rejected(self, manager, tmp_data_dir):
        # Create a capture with out-of-band frequency
        meta = {
            "id": "test123",
            "filename": "test.iq",
            "frequency_hz": 100000000,  # 100 MHz - out of ISM
            "sample_rate": 2000000,
            "lna_gain": 32,
            "vga_gain": 20,
            "timestamp": "2026-01-01T00:00:00Z",
        }
        meta_path = tmp_data_dir / "captures" / "test.json"
        meta_path.write_text(json.dumps(meta))
        (tmp_data_dir / "captures" / "test.iq").write_bytes(b"\x00" * 100)

        with (
            patch("shutil.which", return_value="/usr/bin/hackrf_transfer"),
            patch.object(manager, "check_hackrf_device", return_value=True),
        ):
            manager._hackrf_available = None
            result = manager.transmit(capture_id="test123")
            assert result["status"] == "error"
            assert "outside allowed TX bands" in result["message"]

    def test_transmit_already_running(self, manager, tmp_data_dir):
        import time as _time

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager._rx_process = mock_proc
        # Pre-lock device checks now run before active_mode guard. transmit()
        # resolves hackrf_transfer directly rather than going through
        # check_hackrf(), so stubbing _hackrf_available is not enough on a host
        # without the binary installed.
        manager._hackrf_device_cache = True
        manager._hackrf_device_cache_ts = _time.time()
        # Capture lookup also runs pre-lock now; provide a valid capture + IQ file
        meta = {
            "id": "test123",
            "filename": "test.iq",
            "frequency_hz": 433920000,
            "sample_rate": 2000000,
            "timestamp": "2025-01-01T00:00:00",
        }
        (tmp_data_dir / "captures" / "test.json").write_text(json.dumps(meta))
        (tmp_data_dir / "captures" / "test.iq").write_bytes(b"\x00" * 64)

        with patch.object(manager, "_resolve_tool", return_value="/usr/bin/hackrf_transfer"):
            result = manager.transmit(capture_id="test123")
        assert result["status"] == "error"
        assert "Already running" in result["message"]

    def test_transmit_segment_extracts_range(self, manager, tmp_data_dir):
        meta = {
            "id": "seg001",
            "filename": "seg.iq",
            "frequency_hz": 433920000,
            "sample_rate": 1000,
            "lna_gain": 24,
            "vga_gain": 20,
            "timestamp": "2026-01-01T00:00:00Z",
            "duration_seconds": 1.0,
            "size_bytes": 2000,
        }
        (tmp_data_dir / "captures" / "seg.json").write_text(json.dumps(meta))
        (tmp_data_dir / "captures" / "seg.iq").write_bytes(bytes(range(200)) * 10)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_timer = MagicMock()
        mock_timer.start = MagicMock()

        with (
            patch("shutil.which", return_value="/usr/bin/hackrf_transfer"),
            patch.object(manager, "check_hackrf_device", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("utils.subghz.register_process"),
            patch("threading.Timer", return_value=mock_timer),
            patch("threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread.start = MagicMock()
            mock_thread_cls.return_value = mock_thread

            manager._hackrf_available = None
            result = manager.transmit(
                capture_id="seg001",
                start_seconds=0.2,
                duration_seconds=0.3,
            )

        assert result["status"] == "transmitting"
        assert result["segment"] is not None
        assert result["segment"]["duration_seconds"] == pytest.approx(0.3, abs=0.01)
        assert manager._tx_temp_file is not None
        assert manager._tx_temp_file.exists()


class TestCaptureLibrary:
    def test_list_captures_empty(self, manager):
        captures = manager.list_captures()
        assert captures == []

    def test_list_captures_with_data(self, manager, tmp_data_dir):
        meta = {
            "id": "cap001",
            "filename": "test.iq",
            "frequency_hz": 433920000,
            "sample_rate": 2000000,
            "lna_gain": 32,
            "vga_gain": 20,
            "timestamp": "2026-01-01T00:00:00Z",
            "duration_seconds": 5.0,
            "size_bytes": 1024,
            "label": "test capture",
        }
        (tmp_data_dir / "captures" / "test.json").write_text(json.dumps(meta))

        captures = manager.list_captures()
        assert len(captures) == 1
        assert captures[0].capture_id == "cap001"
        assert captures[0].label == "test capture"

    def test_get_capture(self, manager, tmp_data_dir):
        meta = {
            "id": "cap002",
            "filename": "test2.iq",
            "frequency_hz": 315000000,
            "sample_rate": 2000000,
            "timestamp": "2026-01-01T00:00:00Z",
        }
        (tmp_data_dir / "captures" / "test2.json").write_text(json.dumps(meta))

        cap = manager.get_capture("cap002")
        assert cap is not None
        assert cap.frequency_hz == 315000000

    def test_get_capture_not_found(self, manager):
        cap = manager.get_capture("nonexistent")
        assert cap is None

    def test_delete_capture(self, manager, tmp_data_dir):
        captures_dir = tmp_data_dir / "captures"
        iq_path = captures_dir / "delete_me.iq"
        meta_path = captures_dir / "delete_me.json"
        iq_path.write_bytes(b"\x00" * 100)
        meta_path.write_text(
            json.dumps(
                {
                    "id": "del001",
                    "filename": "delete_me.iq",
                    "frequency_hz": 433920000,
                    "sample_rate": 2000000,
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            )
        )

        assert manager.delete_capture("del001") is True
        assert not iq_path.exists()
        assert not meta_path.exists()

    def test_delete_capture_not_found(self, manager):
        assert manager.delete_capture("nonexistent") is False

    def test_update_label(self, manager, tmp_data_dir):
        meta = {
            "id": "lbl001",
            "filename": "label_test.iq",
            "frequency_hz": 433920000,
            "sample_rate": 2000000,
            "timestamp": "2026-01-01T00:00:00Z",
            "label": "",
        }
        meta_path = tmp_data_dir / "captures" / "label_test.json"
        meta_path.write_text(json.dumps(meta))

        assert manager.update_capture_label("lbl001", "Garage Remote") is True

        updated = json.loads(meta_path.read_text())
        assert updated["label"] == "Garage Remote"
        assert updated["label_source"] == "manual"

    def test_update_label_not_found(self, manager):
        assert manager.update_capture_label("nonexistent", "test") is False

    def test_get_capture_path(self, manager, tmp_data_dir):
        captures_dir = tmp_data_dir / "captures"
        iq_path = captures_dir / "path_test.iq"
        iq_path.write_bytes(b"\x00" * 100)
        (captures_dir / "path_test.json").write_text(
            json.dumps(
                {
                    "id": "pth001",
                    "filename": "path_test.iq",
                    "frequency_hz": 433920000,
                    "sample_rate": 2000000,
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            )
        )

        path = manager.get_capture_path("pth001")
        assert path is not None
        assert path.name == "path_test.iq"

    def test_get_capture_path_not_found(self, manager):
        assert manager.get_capture_path("nonexistent") is None

    def test_trim_capture_manual_segment(self, manager, tmp_data_dir):
        captures_dir = tmp_data_dir / "captures"
        iq_path = captures_dir / "trim_src.iq"
        iq_path.write_bytes(bytes(range(200)) * 20)  # 4000 bytes at 1000 sps => 2.0s
        (captures_dir / "trim_src.json").write_text(
            json.dumps(
                {
                    "id": "trim001",
                    "filename": "trim_src.iq",
                    "frequency_hz": 433920000,
                    "sample_rate": 1000,
                    "lna_gain": 24,
                    "vga_gain": 20,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "duration_seconds": 2.0,
                    "size_bytes": 4000,
                    "label": "Weather Burst",
                    "bursts": [
                        {
                            "start_seconds": 0.55,
                            "duration_seconds": 0.2,
                            "peak_level": 67,
                            "fingerprint": "abc123",
                            "modulation_hint": "OOK/ASK",
                            "modulation_confidence": 0.9,
                        }
                    ],
                }
            )
        )

        result = manager.trim_capture(
            capture_id="trim001",
            start_seconds=0.5,
            duration_seconds=0.4,
        )

        assert result["status"] == "ok"
        assert result["capture"]["id"] != "trim001"
        assert result["capture"]["size_bytes"] == 800
        assert result["capture"]["label"].endswith("(Trim)")
        trimmed_iq = captures_dir / result["capture"]["filename"]
        assert trimmed_iq.exists()
        trimmed_meta = trimmed_iq.with_suffix(".json")
        assert trimmed_meta.exists()

    def test_trim_capture_auto_burst(self, manager, tmp_data_dir):
        captures_dir = tmp_data_dir / "captures"
        iq_path = captures_dir / "auto_src.iq"
        iq_path.write_bytes(bytes(range(100)) * 40)  # 4000 bytes
        (captures_dir / "auto_src.json").write_text(
            json.dumps(
                {
                    "id": "trim002",
                    "filename": "auto_src.iq",
                    "frequency_hz": 433920000,
                    "sample_rate": 1000,
                    "lna_gain": 24,
                    "vga_gain": 20,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "duration_seconds": 2.0,
                    "size_bytes": 4000,
                    "bursts": [
                        {"start_seconds": 0.2, "duration_seconds": 0.1, "peak_level": 12},
                        {"start_seconds": 1.2, "duration_seconds": 0.25, "peak_level": 88},
                    ],
                }
            )
        )

        result = manager.trim_capture(capture_id="trim002")
        assert result["status"] == "ok"
        assert result["segment"]["auto_selected"] is True
        assert result["capture"]["duration_seconds"] > 0.25

    def test_list_captures_groups_same_fingerprint(self, manager, tmp_data_dir):
        cap_a = {
            "id": "grp001",
            "filename": "a.iq",
            "frequency_hz": 433920000,
            "sample_rate": 2000000,
            "timestamp": "2026-01-01T00:00:00Z",
            "dominant_fingerprint": "deadbeefcafebabe",
        }
        cap_b = {
            "id": "grp002",
            "filename": "b.iq",
            "frequency_hz": 433920000,
            "sample_rate": 2000000,
            "timestamp": "2026-01-01T00:01:00Z",
            "dominant_fingerprint": "deadbeefcafebabe",
        }
        (tmp_data_dir / "captures" / "a.json").write_text(json.dumps(cap_a))
        (tmp_data_dir / "captures" / "b.json").write_text(json.dumps(cap_b))

        captures = manager.list_captures()
        assert len(captures) == 2
        assert all(c.fingerprint_group.startswith("SIG-") for c in captures)
        assert all(c.fingerprint_group_size == 2 for c in captures)


class TestSweep:
    def test_start_sweep_no_tool(self, manager):
        with patch("shutil.which", return_value=None), patch("utils.subghz.get_tool_path", return_value=None):
            manager._sweep_available = None
            result = manager.start_sweep()
            assert result["status"] == "error"

    def test_start_sweep_success(self, manager):
        import time as _time

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout = MagicMock()

        with (
            patch("shutil.which", return_value="/usr/bin/hackrf_sweep"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("utils.subghz.register_process"),
        ):
            manager._sweep_available = None
            manager._hackrf_device_cache = True
            manager._hackrf_device_cache_ts = _time.time()
            result = manager.start_sweep(freq_start_mhz=300, freq_end_mhz=928)
            assert result["status"] == "started"

            # Signal daemon threads to stop so they don't outlive the test
            manager._sweep_running = False

    def test_stop_sweep_not_running(self, manager):
        result = manager.stop_sweep()
        assert result["status"] == "not_running"


class TestDecode:
    def test_start_decode_no_hackrf(self, manager):
        with patch("shutil.which", return_value=None), patch("utils.subghz.get_tool_path", return_value=None):
            manager._hackrf_available = None
            manager._rtl433_available = None
            result = manager.start_decode(frequency_hz=433920000)
            assert result["status"] == "error"
            assert "hackrf_transfer" in result["message"]

    def test_start_decode_no_rtl433(self, manager):
        def which_side_effect(name):
            if name == "hackrf_transfer":
                return "/usr/bin/hackrf_transfer"
            return None

        with (
            patch("shutil.which", side_effect=which_side_effect),
            patch("utils.subghz.get_tool_path", return_value=None),
        ):
            manager._hackrf_available = None
            manager._rtl433_available = None
            result = manager.start_decode(frequency_hz=433920000)
            assert result["status"] == "error"
            assert "rtl_433" in result["message"]

    def test_start_decode_success(self, manager):
        mock_hackrf_proc = MagicMock()
        mock_hackrf_proc.poll.return_value = None
        mock_hackrf_proc.stdout = MagicMock()
        mock_hackrf_proc.stderr = MagicMock()
        mock_hackrf_proc.stderr.readline = MagicMock(return_value=b"")

        mock_rtl433_proc = MagicMock()
        mock_rtl433_proc.poll.return_value = None
        mock_rtl433_proc.stdout = MagicMock()
        mock_rtl433_proc.stderr = MagicMock()
        mock_rtl433_proc.stderr.readline = MagicMock(return_value=b"")

        call_count = [0]

        def popen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_hackrf_proc
            return mock_rtl433_proc

        def which_side_effect(name):
            return f"/usr/bin/{name}"

        with (
            patch("shutil.which", side_effect=which_side_effect),
            patch("subprocess.Popen", side_effect=popen_side_effect) as mock_popen,
            patch("utils.subghz.register_process"),
        ):
            import time as _time

            manager._hackrf_available = None
            manager._rtl433_available = None
            manager._hackrf_device_cache = True
            manager._hackrf_device_cache_ts = _time.time()
            result = manager.start_decode(
                frequency_hz=433920000,
                sample_rate=2000000,
            )
            assert result["status"] == "started"
            assert result["frequency_hz"] == 433920000
            assert manager.active_mode == "decode"

            # Two processes: hackrf_transfer + rtl_433
            assert mock_popen.call_count == 2

            # Verify hackrf_transfer command
            hackrf_cmd = mock_popen.call_args_list[0][0][0]
            assert os.path.basename(hackrf_cmd[0]) == "hackrf_transfer"
            assert "-r" in hackrf_cmd

            # Verify rtl_433 command
            rtl433_cmd = mock_popen.call_args_list[1][0][0]
            assert os.path.basename(rtl433_cmd[0]) == "rtl_433"
            assert "-r" in rtl433_cmd
            assert "cs8:-" in rtl433_cmd

            # Both processes tracked
            assert manager._decode_hackrf_process is mock_hackrf_proc
            assert manager._decode_process is mock_rtl433_proc

            # Signal daemon threads to stop so they don't outlive the test
            manager._decode_stop = True

    def test_stop_decode_not_running(self, manager):
        result = manager.stop_decode()
        assert result["status"] == "not_running"

    def test_stop_decode_terminates_both(self, manager):
        mock_hackrf = MagicMock()
        mock_hackrf.poll.return_value = None
        mock_rtl433 = MagicMock()
        mock_rtl433.poll.return_value = None

        manager._decode_hackrf_process = mock_hackrf
        manager._decode_process = mock_rtl433
        manager._decode_frequency_hz = 433920000

        with patch("utils.subghz.safe_terminate") as mock_term, patch("utils.subghz.unregister_process"):
            result = manager.stop_decode()

        assert result["status"] == "stopped"
        assert manager._decode_hackrf_process is None
        assert manager._decode_process is None
        assert mock_term.call_count == 2


class TestStopAll:
    def test_stop_all_clears_processes(self, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager._rx_process = mock_proc

        with patch("utils.subghz.safe_terminate"):
            manager.stop_all()

        assert manager._rx_process is None
        assert manager._decode_hackrf_process is None
        assert manager._decode_process is None
        assert manager._tx_process is None
        assert manager._sweep_process is None


class TestSubGhzCapture:
    def test_to_dict(self):
        cap = SubGhzCapture(
            capture_id="abc123",
            filename="test.iq",
            frequency_hz=433920000,
            sample_rate=2000000,
            lna_gain=32,
            vga_gain=20,
            timestamp="2026-01-01T00:00:00Z",
            duration_seconds=5.0,
            size_bytes=1024,
            label="Test",
        )
        d = cap.to_dict()
        assert d["id"] == "abc123"
        assert d["frequency_hz"] == 433920000
        assert d["label"] == "Test"
