"""Tests for WeatherSatDecoder class.

Covers WeatherSatDecoder methods, subprocess management, progress callbacks,
and image handling.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.weather_sat import (
    WEATHER_SATELLITES,
    CaptureProgress,
    WeatherSatDecoder,
    WeatherSatImage,
    get_weather_sat_decoder,
    is_weather_sat_available,
)


@pytest.fixture(autouse=True)
def _stop_decoder_threads():
    """Stop watcher/reader threads leaked by tests that call start().

    Leaked threads keep scanning the output dir and contend for the SQLite
    lock, slowing every later test in the session. Full stop() is unsafe
    here: it would os.close() the mocked pty fds (10, 11), which are real
    fds of the pytest process.
    """
    created: list[WeatherSatDecoder] = []
    orig_init = WeatherSatDecoder.__init__

    def tracking_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        created.append(self)

    with patch.object(WeatherSatDecoder, "__init__", tracking_init):
        yield
    for decoder in created:
        decoder._running = False
        decoder._stop_event.set()


class TestWeatherSatDecoder:
    """Tests for WeatherSatDecoder class."""

    def test_decoder_initialization(self):
        """Decoder should initialize with default output directory."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            assert decoder.is_running is False
            assert decoder.decoder_available == "satdump"
            assert decoder.current_satellite == ""
            assert decoder.current_frequency == 0.0

    def test_decoder_initialization_no_satdump(self):
        """Decoder should detect when SatDump is unavailable."""
        with patch("shutil.which", return_value=None):
            decoder = WeatherSatDecoder()
            assert decoder.decoder_available is None

    def test_decoder_custom_output_dir(self):
        """Decoder should accept custom output directory."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            custom_dir = "/tmp/custom_output"
            decoder = WeatherSatDecoder(output_dir=custom_dir)
            assert decoder._output_dir == Path(custom_dir)

    def test_set_callback(self):
        """Decoder should accept progress callback."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)
            assert decoder._callback == callback

    def test_set_on_complete(self):
        """Decoder should accept on_complete callback."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_on_complete(callback)
            assert decoder._on_complete_callback == callback

    def test_start_no_decoder(self):
        """start() should fail when no decoder available."""
        with patch("shutil.which", return_value=None):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start(satellite="NOAA-18", device_index=0, gain=40.0)

            assert success is False
            assert error_msg is not None
            callback.assert_called()
            progress = callback.call_args[0][0]
            assert progress.status == "error"
            assert "SatDump" in progress.message

    def test_start_invalid_satellite(self):
        """start() should fail with invalid satellite."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start(satellite="FAKE-SAT", device_index=0, gain=40.0)

            assert success is False
            callback.assert_called()
            progress = callback.call_args[0][0]
            assert progress.status == "error"
            assert "Unknown satellite" in progress.message

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    @patch("utils.weather_sat.register_process")
    def test_start_success(self, mock_register, mock_pty, mock_popen):
        """start() should successfully start SatDump."""
        with (
            patch("shutil.which", return_value="/usr/bin/satdump"),
            patch("utils.weather_sat.WeatherSatDecoder._resolve_device_id", return_value="0"),
        ):
            # Use a real pipe — hardcoded fds like (10, 11) collide with fds
            # the test process actually has open (DB, log files, capture)
            mock_pty.return_value = os.pipe()
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start(
                satellite="NOAA-18",
                device_index=0,
                gain=40.0,
                bias_t=True,
            )

            assert success is True
            assert error_msg is None
            assert decoder.is_running is True
            assert decoder.current_satellite == "NOAA-18"
            assert decoder.current_frequency == 137.9125
            assert decoder.current_mode == "APT"
            assert decoder.device_index == 0

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "satdump"
            assert "live" in cmd
            assert "noaa_apt" in cmd
            assert "--bias" in cmd

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    @patch("utils.weather_sat.register_process")
    def test_start_rtl_tcp_uses_rtltcp_source(self, mock_register, mock_pty, mock_popen):
        """start() with rtl_tcp should use --source rtltcp instead of rtlsdr."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            # Use a real pipe — hardcoded fds like (10, 11) collide with fds
            # the test process actually has open (DB, log files, capture)
            mock_pty.return_value = os.pipe()
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start(
                satellite="NOAA-18",
                device_index=0,
                gain=40.0,
                rtl_tcp_host="192.168.1.100",
                rtl_tcp_port=1234,
            )

            assert success is True
            assert error_msg is None

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert "--source" in cmd
            source_idx = cmd.index("--source")
            assert cmd[source_idx + 1] == "rtltcp"
            assert "--ip_address" in cmd
            assert "192.168.1.100" in cmd
            assert "--port" in cmd
            assert "1234" in cmd
            # Should NOT have --source_id for remote
            assert "--source_id" not in cmd

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    @patch("utils.weather_sat.register_process")
    def test_start_rtl_tcp_skips_device_resolve(self, mock_register, mock_pty, mock_popen):
        """start() with rtl_tcp should skip _resolve_device_id."""
        with (
            patch("shutil.which", return_value="/usr/bin/satdump"),
            patch("utils.weather_sat.WeatherSatDecoder._resolve_device_id") as mock_resolve,
        ):
            # Use a real pipe — hardcoded fds like (10, 11) collide with fds
            # the test process actually has open (DB, log files, capture)
            mock_pty.return_value = os.pipe()
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            decoder = WeatherSatDecoder()

            success, _ = decoder.start(
                satellite="NOAA-18",
                device_index=0,
                gain=40.0,
                rtl_tcp_host="10.0.0.1",
            )

            assert success is True
            mock_resolve.assert_not_called()

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    def test_start_already_running(self, mock_pty, mock_popen):
        """start() should return True when already running."""
        with (
            patch("shutil.which", return_value="/usr/bin/satdump"),
            patch("utils.weather_sat.WeatherSatDecoder._resolve_device_id", return_value="0"),
        ):
            decoder = WeatherSatDecoder()
            decoder._running = True

            success, error_msg = decoder.start(satellite="NOAA-18", device_index=0, gain=40.0)

            assert success is True
            assert error_msg is None
            mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    def test_start_exception_handling(self, mock_pty, mock_popen):
        """start() should handle exceptions gracefully."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            # Use a real pipe — hardcoded fds like (10, 11) collide with fds
            # the test process actually has open (DB, log files, capture)
            mock_pty.return_value = os.pipe()
            mock_popen.side_effect = OSError("Device not found")

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start(satellite="NOAA-18", device_index=0, gain=40.0)

            assert success is False
            assert error_msg is not None
            assert decoder.is_running is False
            callback.assert_called()
            progress = callback.call_args[0][0]
            assert progress.status == "error"

    def test_start_from_file_no_decoder(self):
        """start_from_file() should fail when no decoder available."""
        with patch("shutil.which", return_value=None):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start_from_file(
                satellite="NOAA-18",
                input_file="data/test.wav",
            )

            assert success is False
            assert error_msg is not None
            callback.assert_called()

    @patch("subprocess.Popen")
    @patch("pty.openpty")
    @patch("pathlib.Path.is_file", return_value=True)
    @patch("pathlib.Path.resolve")
    def test_start_from_file_success(self, mock_resolve, mock_is_file, mock_pty, mock_popen):
        """start_from_file() should successfully decode from file."""
        with patch("shutil.which", return_value="/usr/bin/satdump"), patch("utils.weather_sat.register_process"):
            # Mock path resolution
            mock_path = MagicMock()
            mock_path.is_relative_to.return_value = True
            mock_path.suffix = ".wav"
            mock_resolve.return_value = mock_path

            # Use a real pipe — hardcoded fds like (10, 11) collide with fds
            # the test process actually has open (DB, log files, capture)
            mock_pty.return_value = os.pipe()
            mock_process = MagicMock()
            mock_process.poll.return_value = None  # Process still running
            mock_popen.return_value = mock_process

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start_from_file(
                satellite="NOAA-18",
                input_file="data/test.wav",
                sample_rate=1000000,
            )

            assert success is True
            assert error_msg is None
            assert decoder.is_running is True
            assert decoder.current_satellite == "NOAA-18"

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "satdump"
            assert "noaa_apt" in cmd
            assert "audio_wav" in cmd
            assert "--samplerate" in cmd

    @patch("pathlib.Path.resolve")
    def test_start_from_file_path_traversal(self, mock_resolve):
        """start_from_file() should block path traversal."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            # Mock path outside allowed directory
            mock_path = MagicMock()
            mock_path.is_relative_to.return_value = False
            mock_resolve.return_value = mock_path

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start_from_file(
                satellite="NOAA-18",
                input_file="/etc/passwd",
            )

            assert success is False
            callback.assert_called()
            progress = callback.call_args[0][0]
            assert "must be under INTERCEPT data" in progress.message

    @patch("pathlib.Path.is_file", return_value=False)
    @patch("pathlib.Path.resolve")
    def test_start_from_file_not_found(self, mock_resolve, mock_is_file):
        """start_from_file() should fail when file not found."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            mock_path = MagicMock()
            mock_path.is_relative_to.return_value = True
            mock_resolve.return_value = mock_path

            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            success, error_msg = decoder.start_from_file(
                satellite="NOAA-18",
                input_file="data/missing.wav",
            )

            assert success is False
            callback.assert_called()
            progress = callback.call_args[0][0]
            assert "not found" in progress.message.lower()

    def test_stop_not_running(self):
        """stop() should be safe when not running."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            decoder.stop()  # Should not raise

    @patch("utils.weather_sat.safe_terminate")
    def test_stop_running(self, mock_terminate):
        """stop() should terminate process."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            mock_process = MagicMock()
            decoder._process = mock_process
            decoder._running = True
            decoder._pty_master_fd = 10

            with patch("os.close") as mock_close:
                decoder.stop()

            assert decoder._running is False
            mock_terminate.assert_called_once_with(mock_process)
            mock_close.assert_called_once_with(10)

    def test_get_images_empty(self):
        """get_images() should return empty list initially."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            images = decoder.get_images()
            assert images == []

    @patch("pathlib.Path.glob")
    @patch("pathlib.Path.stat")
    def test_get_images_scans_directory(self, mock_stat, mock_glob):
        """get_images() should scan output directory."""
        # Hand back a real os.stat_result: pathlib internals unrelated to this
        # test still call Path.stat() and mask the mode with S_ISDIR, which
        # raises "TypeError: an integer is required" against a bare MagicMock
        # on Python 3.11.
        mock_stat.return_value = os.stat(__file__)
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()

            # Mock image files
            mock_file = MagicMock()
            mock_file.name = "NOAA-18_test.png"
            mock_file.stat.return_value.st_size = 10000
            mock_file.stat.return_value.st_mtime = time.time()
            mock_glob.return_value = [mock_file]

            images = decoder.get_images()

            assert len(images) == 1
            assert images[0].filename == "NOAA-18_test.png"
            assert images[0].satellite == "NOAA-18"

    def test_delete_image_success(self):
        """delete_image() should delete file."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()

            with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.unlink") as mock_unlink:
                result = decoder.delete_image("test.png")

                assert result is True
                mock_unlink.assert_called_once()

    def test_delete_image_not_found(self):
        """delete_image() should return False for non-existent file."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()

            with patch("pathlib.Path.exists", return_value=False):
                result = decoder.delete_image("missing.png")

                assert result is False

    def test_delete_all_images(self):
        """delete_all_images() should delete all images."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()

            mock_files = [MagicMock() for _ in range(3)]
            # delete_all_images globs three extensions; return files for the
            # first pattern only so each mock is deleted exactly once
            with patch("pathlib.Path.glob", side_effect=[mock_files, [], []]):
                count = decoder.delete_all_images()

                assert count == 3
                for f in mock_files:
                    f.unlink.assert_called_once()

    def test_get_status_idle(self):
        """get_status() should return idle status."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            status = decoder.get_status()

            assert status["available"] is True
            assert status["decoder"] == "satdump"
            assert status["running"] is False
            assert status["satellite"] == ""

    def test_get_status_running(self):
        """get_status() should return running status."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            decoder._running = True
            decoder._current_satellite = "NOAA-18"
            decoder._current_frequency = 137.9125
            decoder._current_mode = "APT"
            decoder._capture_start_time = time.time() - 60

            status = decoder.get_status()

            assert status["running"] is True
            assert status["satellite"] == "NOAA-18"
            assert status["frequency"] == 137.9125
            assert status["mode"] == "APT"
            assert status["elapsed_seconds"] >= 60

    def test_classify_log_type_error(self):
        """_classify_log_type() should detect errors."""
        assert WeatherSatDecoder._classify_log_type("(E) Error occurred") == "error"
        assert WeatherSatDecoder._classify_log_type("Failed to open device") == "error"

    def test_classify_log_type_progress(self):
        """_classify_log_type() should detect progress."""
        assert WeatherSatDecoder._classify_log_type("Progress: 50%") == "progress"

    def test_classify_log_type_save(self):
        """_classify_log_type() should detect save events."""
        assert WeatherSatDecoder._classify_log_type("Saved image: test.png") == "save"
        assert WeatherSatDecoder._classify_log_type("Writing output file") == "save"

    def test_classify_log_type_signal(self):
        """_classify_log_type() should detect signal events."""
        assert WeatherSatDecoder._classify_log_type("Signal detected") == "signal"
        assert WeatherSatDecoder._classify_log_type("Lock acquired") == "signal"

    def test_classify_log_type_warning(self):
        """_classify_log_type() should detect warnings."""
        assert WeatherSatDecoder._classify_log_type("(W) Low signal quality") == "warning"

    def test_classify_log_type_debug(self):
        """_classify_log_type() should detect debug messages."""
        assert WeatherSatDecoder._classify_log_type("(D) Debug info") == "debug"

    @patch("subprocess.run")
    def test_resolve_device_id_success(self, mock_run):
        """_resolve_device_id() should extract serial from rtl_test."""
        mock_result = MagicMock()
        mock_result.stdout = "Found 1 device(s):\n  0: RTLSDRBlog, SN: 00004000"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        serial = WeatherSatDecoder._resolve_device_id(0)

        assert serial == "00004000"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_resolve_device_id_fallback(self, mock_run):
        """_resolve_device_id() should return None when no serial found."""
        mock_run.side_effect = FileNotFoundError

        serial = WeatherSatDecoder._resolve_device_id(0)

        assert serial is None

    def test_parse_product_name_rgb(self):
        """_parse_product_name() should identify RGB composite."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            product = decoder._parse_product_name(Path("/tmp/output/rgb_composite.png"))
            assert product == "RGB Composite"

    def test_parse_product_name_thermal(self):
        """_parse_product_name() should identify thermal imagery."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            product = decoder._parse_product_name(Path("/tmp/output/thermal_image.png"))
            assert product == "Thermal"

    def test_parse_product_name_channel(self):
        """_parse_product_name() should identify channel images."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            product = decoder._parse_product_name(Path("/tmp/output/channel_3.png"))
            assert product == "Channel 3"

    def test_parse_product_name_unknown(self):
        """_parse_product_name() should return stem for unknown products."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            product = decoder._parse_product_name(Path("/tmp/output/unknown_image.png"))
            assert product == "unknown_image"

    def test_emit_progress(self):
        """_emit_progress() should call callback."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            callback = MagicMock()
            decoder.set_callback(callback)

            progress = CaptureProgress(status="capturing", message="Test")
            decoder._emit_progress(progress)

            callback.assert_called_once_with(progress)

    def test_emit_progress_no_callback(self):
        """_emit_progress() should handle missing callback."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            progress = CaptureProgress(status="capturing", message="Test")
            decoder._emit_progress(progress)  # Should not raise

    def test_emit_progress_callback_exception(self):
        """_emit_progress() should handle callback exceptions."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            decoder = WeatherSatDecoder()
            callback = MagicMock(side_effect=Exception("Callback error"))
            decoder.set_callback(callback)

            progress = CaptureProgress(status="capturing", message="Test")
            decoder._emit_progress(progress)  # Should not raise


class TestWeatherSatImage:
    """Tests for WeatherSatImage dataclass."""

    def test_to_dict(self):
        """WeatherSatImage.to_dict() should serialize correctly."""
        image = WeatherSatImage(
            filename="test.png",
            path=Path("/tmp/test.png"),
            satellite="NOAA-18",
            mode="APT",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            frequency=137.9125,
            size_bytes=12345,
            product="RGB Composite",
        )

        data = image.to_dict()

        assert data["filename"] == "test.png"
        assert data["satellite"] == "NOAA-18"
        assert data["mode"] == "APT"
        assert data["timestamp"] == "2024-01-01T12:00:00+00:00"
        assert data["frequency"] == 137.9125
        assert data["size_bytes"] == 12345
        assert data["product"] == "RGB Composite"
        assert data["url"] == "/weather-sat/images/test.png"


class TestCaptureProgress:
    """Tests for CaptureProgress dataclass."""

    def test_to_dict_minimal(self):
        """CaptureProgress.to_dict() with minimal fields."""
        progress = CaptureProgress(status="idle")
        data = progress.to_dict()

        assert data["type"] == "weather_sat_progress"
        assert data["status"] == "idle"
        assert data["satellite"] == ""
        assert data["message"] == ""
        assert data["progress"] == 0

    def test_to_dict_complete(self):
        """CaptureProgress.to_dict() with all fields."""
        image = WeatherSatImage(
            filename="test.png",
            path=Path("/tmp/test.png"),
            satellite="NOAA-18",
            mode="APT",
            timestamp=datetime.now(timezone.utc),
            frequency=137.9125,
        )

        progress = CaptureProgress(
            status="complete",
            satellite="NOAA-18",
            frequency=137.9125,
            mode="APT",
            message="Capture complete",
            progress_percent=100,
            elapsed_seconds=600,
            image=image,
            log_type="info",
            capture_phase="complete",
        )

        data = progress.to_dict()

        assert data["status"] == "complete"
        assert data["satellite"] == "NOAA-18"
        assert data["frequency"] == 137.9125
        assert data["mode"] == "APT"
        assert data["message"] == "Capture complete"
        assert data["progress"] == 100
        assert data["elapsed_seconds"] == 600
        assert "image" in data
        assert data["log_type"] == "info"
        assert data["capture_phase"] == "complete"


class TestGlobalFunctions:
    """Tests for global utility functions."""

    def test_get_weather_sat_decoder_singleton(self):
        """get_weather_sat_decoder() should return singleton."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            import utils.weather_sat as mod

            old = mod._decoder
            mod._decoder = None

            try:
                decoder1 = get_weather_sat_decoder()
                decoder2 = get_weather_sat_decoder()

                assert decoder1 is decoder2
            finally:
                mod._decoder = old

    def test_is_weather_sat_available_true(self):
        """is_weather_sat_available() should return True when available."""
        with patch("shutil.which", return_value="/usr/bin/satdump"):
            import utils.weather_sat as mod

            old = mod._decoder
            mod._decoder = None

            try:
                assert is_weather_sat_available() is True
            finally:
                mod._decoder = old

    def test_is_weather_sat_available_false(self):
        """is_weather_sat_available() should return False when unavailable."""
        with patch("shutil.which", return_value=None):
            import utils.weather_sat as mod

            old = mod._decoder
            mod._decoder = None

            try:
                assert is_weather_sat_available() is False
            finally:
                mod._decoder = old


class TestWeatherSatellitesConstant:
    """Tests for WEATHER_SATELLITES constant."""

    def test_weather_satellites_structure(self):
        """WEATHER_SATELLITES should have correct structure."""
        assert "NOAA-18" in WEATHER_SATELLITES
        sat = WEATHER_SATELLITES["NOAA-18"]

        assert "name" in sat
        assert "frequency" in sat
        assert "mode" in sat
        assert "pipeline" in sat
        assert "tle_key" in sat
        assert "description" in sat
        assert "active" in sat

    def test_noaa_satellites(self):
        """NOAA satellites should have correct frequencies."""
        assert WEATHER_SATELLITES["NOAA-15"]["frequency"] == 137.620
        assert WEATHER_SATELLITES["NOAA-18"]["frequency"] == 137.9125
        assert WEATHER_SATELLITES["NOAA-19"]["frequency"] == 137.100

    def test_meteor_satellite(self):
        """Meteor satellite should use LRPT mode."""
        meteor = WEATHER_SATELLITES["METEOR-M2-3"]
        assert meteor["mode"] == "LRPT"
        assert meteor["frequency"] == 137.900
        assert meteor["pipeline"] == "meteor_m2-x_lrpt"
