"""Tests for weather satellite auto-scheduler.

Covers WeatherSatScheduler class, pass scheduling, timer management,
and automatic capture execution.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from utils.weather_sat_scheduler import (
    ScheduledPass,
    WeatherSatScheduler,
    _parse_utc_iso,
    get_weather_sat_scheduler,
)


class TestScheduledPass:
    """Tests for ScheduledPass class."""

    def test_scheduled_pass_initialization(self):
        """ScheduledPass should initialize from pass data."""
        pass_data = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }

        sp = ScheduledPass(pass_data)

        assert sp.id == "NOAA-18_202401011200"
        assert sp.satellite == "NOAA-18"
        assert sp.name == "NOAA 18"
        assert sp.frequency == 137.9125
        assert sp.mode == "APT"
        assert sp.max_el == 45.0
        assert sp.duration == 15.0
        assert sp.quality == "good"
        assert sp.status == "scheduled"
        assert sp.skipped is False

    def test_scheduled_pass_start_dt(self):
        """ScheduledPass.start_dt should parse ISO datetime."""
        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }

        sp = ScheduledPass(pass_data)

        assert sp.start_dt.year == 2024
        assert sp.start_dt.month == 1
        assert sp.start_dt.day == 1
        assert sp.start_dt.hour == 12
        assert sp.start_dt.tzinfo == timezone.utc

    def test_scheduled_pass_end_dt(self):
        """ScheduledPass.end_dt should parse ISO datetime."""
        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }

        sp = ScheduledPass(pass_data)

        assert sp.end_dt.year == 2024
        assert sp.end_dt.minute == 15

    def test_scheduled_pass_to_dict(self):
        """ScheduledPass.to_dict() should serialize correctly."""
        pass_data = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }

        sp = ScheduledPass(pass_data)
        sp.status = "complete"

        data = sp.to_dict()

        assert data["id"] == "NOAA-18_202401011200"
        assert data["satellite"] == "NOAA-18"
        assert data["status"] == "complete"
        assert data["skipped"] is False


class TestWeatherSatScheduler:
    """Tests for WeatherSatScheduler class."""

    def test_scheduler_initialization(self):
        """Scheduler should initialize with defaults."""
        scheduler = WeatherSatScheduler()

        assert scheduler.enabled is False
        assert scheduler._lat == 0.0
        assert scheduler._lon == 0.0
        assert scheduler._min_elevation == 15.0
        assert scheduler._device == 0
        assert scheduler._gain == 40.0
        assert scheduler._bias_t is False
        assert scheduler._passes == []

    def test_set_callbacks(self):
        """Scheduler should accept callbacks."""
        scheduler = WeatherSatScheduler()
        progress_cb = MagicMock()
        event_cb = MagicMock()

        scheduler.set_callbacks(progress_cb, event_cb)

        assert scheduler._progress_callback == progress_cb
        assert scheduler._event_callback == event_cb

    @patch("utils.weather_sat_scheduler.WeatherSatScheduler._refresh_passes")
    def test_enable(self, mock_refresh):
        """enable() should start scheduler."""
        scheduler = WeatherSatScheduler()

        result = scheduler.enable(
            lat=51.5,
            lon=-0.1,
            min_elevation=20.0,
            device=1,
            gain=35.0,
            bias_t=True,
        )

        assert scheduler._enabled is True
        assert scheduler._lat == 51.5
        assert scheduler._lon == -0.1
        assert scheduler._min_elevation == 20.0
        assert scheduler._device == 1
        assert scheduler._gain == 35.0
        assert scheduler._bias_t is True
        mock_refresh.assert_called_once()
        assert "enabled" in result

    def test_disable(self):
        """disable() should stop scheduler and cancel timers."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True

        # Add mock timer
        mock_timer = MagicMock()
        scheduler._refresh_timer = mock_timer

        # Add pass with timer
        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        mock_pass_timer = MagicMock()
        mock_stop_timer = MagicMock()
        sp._timer = mock_pass_timer
        sp._stop_timer = mock_stop_timer
        scheduler._passes = [sp]

        result = scheduler.disable()

        assert scheduler._enabled is False
        assert scheduler._passes == []
        mock_timer.cancel.assert_called_once()
        mock_pass_timer.cancel.assert_called_once()
        mock_stop_timer.cancel.assert_called_once()
        assert result["status"] == "disabled"

    def test_skip_pass_success(self):
        """skip_pass() should skip a scheduled pass."""
        scheduler = WeatherSatScheduler()
        event_cb = MagicMock()
        scheduler.set_callbacks(MagicMock(), event_cb)

        pass_data = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        mock_pass_timer = MagicMock()
        sp._timer = mock_pass_timer
        scheduler._passes = [sp]

        result = scheduler.skip_pass("NOAA-18_202401011200")

        assert result is True
        assert sp.status == "skipped"
        assert sp.skipped is True
        mock_pass_timer.cancel.assert_called_once()
        event_cb.assert_called_once()

    def test_skip_pass_not_found(self):
        """skip_pass() should return False for non-existent pass."""
        scheduler = WeatherSatScheduler()

        result = scheduler.skip_pass("NONEXISTENT")

        assert result is False

    def test_skip_pass_already_complete(self):
        """skip_pass() should not skip already complete passes."""
        scheduler = WeatherSatScheduler()

        pass_data = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        sp.status = "complete"
        scheduler._passes = [sp]

        result = scheduler.skip_pass("NOAA-18_202401011200")

        assert result is False
        assert sp.status == "complete"

    def test_get_status(self):
        """get_status() should return scheduler state."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._lat = 51.5
        scheduler._lon = -0.1
        scheduler._device = 0
        scheduler._gain = 40.0
        scheduler._bias_t = False
        scheduler._min_elevation = 15.0

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        scheduler._passes = [sp]

        status = scheduler.get_status()

        assert status["enabled"] is True
        assert status["observer"]["latitude"] == 51.5
        assert status["observer"]["longitude"] == -0.1
        assert status["device"] == 0
        assert status["gain"] == 40.0
        assert status["bias_t"] is False
        assert status["min_elevation"] == 15.0
        assert status["scheduled_count"] == 1
        assert status["total_passes"] == 1

    def test_get_passes(self):
        """get_passes() should return list of scheduled passes."""
        scheduler = WeatherSatScheduler()

        pass_data = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        scheduler._passes = [sp]

        passes = scheduler.get_passes()

        assert len(passes) == 1
        assert passes[0]["id"] == "NOAA-18_202401011200"

    @patch("utils.weather_sat_predict.predict_passes")
    @patch("threading.Timer")
    def test_refresh_passes(self, mock_timer, mock_predict):
        """_refresh_passes() should schedule future passes."""
        now = datetime.now(timezone.utc)
        future_pass = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": (now + timedelta(hours=2)).isoformat(),
            "endTimeISO": (now + timedelta(hours=2, minutes=15)).isoformat(),
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        mock_predict.return_value = [future_pass]

        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._lat = 51.5
        scheduler._lon = -0.1

        scheduler._refresh_passes()

        mock_predict.assert_called_once()
        assert len(scheduler._passes) == 1
        assert scheduler._passes[0].satellite == "NOAA-18"
        mock_timer_instance.start.assert_called()

    @patch("utils.weather_sat_predict.predict_passes")
    def test_refresh_passes_skip_past(self, mock_predict):
        """_refresh_passes() should skip passes that already started."""
        now = datetime.now(timezone.utc)
        past_pass = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": (now - timedelta(hours=1)).isoformat(),
            "endTimeISO": (now - timedelta(hours=1) + timedelta(minutes=15)).isoformat(),
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        mock_predict.return_value = [past_pass]

        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._lat = 51.5
        scheduler._lon = -0.1

        scheduler._refresh_passes()

        # Should not schedule past passes
        assert len(scheduler._passes) == 0

    @patch("utils.weather_sat_predict.predict_passes")
    @patch("threading.Timer")
    def test_refresh_passes_active_window_triggers_immediately(self, mock_timer, mock_predict):
        """_refresh_passes() should trigger immediately during an active pass window."""
        now = datetime.now(timezone.utc)
        active_pass = {
            "id": "NOAA-18_ACTIVE",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": (now - timedelta(minutes=2)).isoformat(),
            "endTimeISO": (now + timedelta(minutes=8)).isoformat(),
            "maxEl": 45.0,
            "duration": 10.0,
            "quality": "good",
        }
        mock_predict.return_value = [active_pass]

        pass_timer = MagicMock()
        refresh_timer = MagicMock()
        mock_timer.side_effect = [pass_timer, refresh_timer]

        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._lat = 51.5
        scheduler._lon = -0.1

        scheduler._refresh_passes()

        assert len(scheduler._passes) == 1
        first_delay = mock_timer.call_args_list[0][0][0]
        assert first_delay == pytest.approx(0.0, abs=0.01)
        pass_timer.start.assert_called_once()

    @patch("utils.weather_sat_predict.predict_passes")
    def test_refresh_passes_disabled(self, mock_predict):
        """_refresh_passes() should do nothing when disabled."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = False

        scheduler._refresh_passes()

        mock_predict.assert_not_called()

    @patch("utils.weather_sat_predict.predict_passes")
    def test_refresh_passes_error_handling(self, mock_predict):
        """_refresh_passes() should handle prediction errors."""
        mock_predict.side_effect = Exception("TLE error")

        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._lat = 51.5
        scheduler._lon = -0.1

        # Should not raise
        scheduler._refresh_passes()

        assert len(scheduler._passes) == 0

    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    def test_execute_capture_disabled(self, mock_get):
        """_execute_capture() should do nothing when disabled."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = False

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._execute_capture(sp)

        mock_get.assert_not_called()

    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    def test_execute_capture_skipped(self, mock_get):
        """_execute_capture() should do nothing for skipped passes."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)
        sp.skipped = True

        scheduler._execute_capture(sp)

        mock_get.assert_not_called()

    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    def test_execute_capture_decoder_busy(self, mock_get):
        """_execute_capture() should skip when decoder is busy."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        event_cb = MagicMock()
        scheduler.set_callbacks(MagicMock(), event_cb)

        mock_decoder = MagicMock()
        mock_decoder.is_running = True
        mock_get.return_value = mock_decoder

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._execute_capture(sp)

        assert sp.status == "skipped"
        assert sp.skipped is True
        event_cb.assert_called_once()
        event_data = event_cb.call_args[0][0]
        assert event_data["type"] == "schedule_capture_skipped"
        assert event_data["reason"] == "sdr_busy"

    @patch("app.claim_sdr_device", return_value=None)
    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    @patch("threading.Timer")
    def test_execute_capture_success(self, mock_timer, mock_get, mock_claim):
        """_execute_capture() should start capture."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        scheduler._device = 0
        scheduler._gain = 40.0
        scheduler._bias_t = False
        progress_cb = MagicMock()
        event_cb = MagicMock()
        scheduler.set_callbacks(progress_cb, event_cb)

        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = (True, None)
        mock_get.return_value = mock_decoder

        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        now = datetime.now(timezone.utc)
        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": (now + timedelta(seconds=10)).isoformat(),
            "endTimeISO": (now + timedelta(minutes=15)).isoformat(),
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._execute_capture(sp)

        assert sp.status == "capturing"
        mock_decoder.set_callback.assert_called_once_with(progress_cb)
        call_kwargs = mock_decoder.start.call_args[1]
        assert call_kwargs["satellite"] == "NOAA-18"
        assert call_kwargs["device_index"] == 0
        assert call_kwargs["gain"] == 40.0
        assert call_kwargs["bias_t"] is False
        event_cb.assert_called_once()
        event_data = event_cb.call_args[0][0]
        assert event_data["type"] == "schedule_capture_start"

    @patch("app.claim_sdr_device", return_value=None)
    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    def test_execute_capture_start_failed(self, mock_get, mock_claim):
        """_execute_capture() should handle start failure."""
        scheduler = WeatherSatScheduler()
        scheduler._enabled = True
        event_cb = MagicMock()
        scheduler.set_callbacks(MagicMock(), event_cb)

        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = (False, "Start failed")
        mock_get.return_value = mock_decoder

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._execute_capture(sp)

        assert sp.status == "skipped"
        event_cb.assert_called_once()
        event_data = event_cb.call_args[0][0]
        assert event_data["reason"] == "start_failed"

    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    def test_stop_capture(self, mock_get):
        """_stop_capture() should stop decoder."""
        scheduler = WeatherSatScheduler()

        mock_decoder = MagicMock()
        mock_decoder.is_running = True
        mock_get.return_value = mock_decoder

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._stop_capture(sp)

        mock_decoder.stop.assert_called_once()

    def test_on_capture_complete(self):
        """_on_capture_complete() should mark pass complete and emit event."""
        scheduler = WeatherSatScheduler()
        event_cb = MagicMock()
        scheduler.set_callbacks(MagicMock(), event_cb)
        release_fn = MagicMock()

        pass_data = {
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": "2024-01-01T12:00:00+00:00",
            "endTimeISO": "2024-01-01T12:15:00+00:00",
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        sp = ScheduledPass(pass_data)

        scheduler._on_capture_complete(sp, release_fn)

        assert sp.status == "complete"
        release_fn.assert_called_once()
        event_cb.assert_called_once()
        event_data = event_cb.call_args[0][0]
        assert event_data["type"] == "schedule_capture_complete"

    def test_emit_event(self):
        """_emit_event() should call event callback."""
        scheduler = WeatherSatScheduler()
        event_cb = MagicMock()
        scheduler.set_callbacks(MagicMock(), event_cb)

        event = {"type": "test_event", "data": "test"}
        scheduler._emit_event(event)

        event_cb.assert_called_once_with(event)

    def test_emit_event_no_callback(self):
        """_emit_event() should handle missing callback."""
        scheduler = WeatherSatScheduler()

        event = {"type": "test_event"}
        scheduler._emit_event(event)  # Should not raise

    def test_emit_event_callback_exception(self):
        """_emit_event() should handle callback exceptions."""
        scheduler = WeatherSatScheduler()
        event_cb = MagicMock(side_effect=Exception("Callback error"))
        scheduler.set_callbacks(MagicMock(), event_cb)

        event = {"type": "test_event"}
        scheduler._emit_event(event)  # Should not raise


class TestGlobalScheduler:
    """Tests for global scheduler singleton."""

    def test_get_weather_sat_scheduler_singleton(self):
        """get_weather_sat_scheduler() should return singleton."""
        import utils.weather_sat_scheduler as mod

        old = mod._scheduler
        mod._scheduler = None

        try:
            scheduler1 = get_weather_sat_scheduler()
            scheduler2 = get_weather_sat_scheduler()

            assert scheduler1 is scheduler2
        finally:
            mod._scheduler = old

    def test_get_weather_sat_scheduler_thread_safe(self):
        """get_weather_sat_scheduler() should be thread-safe."""
        import utils.weather_sat_scheduler as mod

        old = mod._scheduler
        mod._scheduler = None

        schedulers = []

        def create_scheduler():
            schedulers.append(get_weather_sat_scheduler())

        try:
            threads = [threading.Thread(target=create_scheduler) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All should be the same instance
            assert all(s is schedulers[0] for s in schedulers)
        finally:
            mod._scheduler = old


class TestSchedulerConfiguration:
    """Tests for scheduler configuration constants."""

    def test_config_constants(self):
        """Scheduler should have configuration constants."""
        from utils.weather_sat_scheduler import (
            WEATHER_SAT_CAPTURE_BUFFER_SECONDS,
            WEATHER_SAT_SCHEDULE_REFRESH_MINUTES,
        )

        assert isinstance(WEATHER_SAT_SCHEDULE_REFRESH_MINUTES, int)
        assert isinstance(WEATHER_SAT_CAPTURE_BUFFER_SECONDS, int)
        assert WEATHER_SAT_SCHEDULE_REFRESH_MINUTES > 0
        assert WEATHER_SAT_CAPTURE_BUFFER_SECONDS >= 0


class TestUtcIsoParsing:
    """Tests for UTC ISO timestamp parsing."""

    def test_parse_utc_iso_with_z_suffix(self):
        """_parse_utc_iso should handle Z timestamps."""
        dt = _parse_utc_iso("2026-02-19T12:34:56Z")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12
        assert dt.minute == 34
        assert dt.second == 56

    def test_parse_utc_iso_with_legacy_suffix(self):
        """_parse_utc_iso should handle legacy +00:00Z timestamps."""
        dt = _parse_utc_iso("2026-02-19T12:34:56+00:00Z")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12


class TestSchedulerIntegration:
    """Integration tests for scheduler."""

    @patch("app.claim_sdr_device", return_value=None)
    @patch("utils.weather_sat_predict.predict_passes")
    @patch("utils.weather_sat_scheduler.get_weather_sat_decoder")
    @patch("threading.Timer")
    def test_full_scheduling_cycle(self, mock_timer, mock_get_decoder, mock_predict, mock_claim):
        """Test complete scheduling cycle from enable to execute."""
        now = datetime.now(timezone.utc)
        future_pass = {
            "id": "NOAA-18_202401011200",
            "satellite": "NOAA-18",
            "name": "NOAA 18",
            "frequency": 137.9125,
            "mode": "APT",
            "startTimeISO": (now + timedelta(hours=2)).isoformat(),
            "endTimeISO": (now + timedelta(hours=2, minutes=15)).isoformat(),
            "maxEl": 45.0,
            "duration": 15.0,
            "quality": "good",
        }
        mock_predict.return_value = [future_pass]

        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = (True, None)
        mock_get_decoder.return_value = mock_decoder

        scheduler = WeatherSatScheduler()
        progress_cb = MagicMock()
        event_cb = MagicMock()
        scheduler.set_callbacks(progress_cb, event_cb)

        # Enable scheduler
        result = scheduler.enable(lat=51.5, lon=-0.1)

        assert result["enabled"] is True
        assert len(scheduler._passes) == 1
        assert scheduler._passes[0].satellite == "NOAA-18"

        # Simulate timer firing (capture start)
        scheduler._execute_capture(scheduler._passes[0])

        assert scheduler._passes[0].status == "capturing"
        mock_decoder.start.assert_called_once()

        # Simulate completion
        release_fn = MagicMock()
        scheduler._on_capture_complete(scheduler._passes[0], release_fn)

        assert scheduler._passes[0].status == "complete"
        release_fn.assert_called_once()

        # Disable scheduler
        scheduler.disable()

        assert scheduler.enabled is False
        assert len(scheduler._passes) == 0


def test_the_schedule_survives_a_restart(monkeypatch):
    """Enabled settings are saved; a fresh scheduler (after a reboot) switches back on with them."""
    import utils.database as database

    store = {}
    monkeypatch.setattr(database, "set_setting", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(database, "get_setting", lambda k, default=None: store.get(k, default))
    monkeypatch.setattr(WeatherSatScheduler, "_refresh_passes", lambda self: None)

    first = WeatherSatScheduler()
    first.enable(lat=39.2, lon=-84.3, min_elevation=40, gain=46)

    after_reboot = WeatherSatScheduler()
    assert after_reboot.restore() is True
    assert after_reboot.enabled
    assert (after_reboot._lat, after_reboot._min_elevation, after_reboot._gain) == (39.2, 40, 46)

    after_reboot.disable()
    assert WeatherSatScheduler().restore() is False  # switched off stays off


def test_a_refresh_during_a_capture_keeps_its_stop(monkeypatch):
    """The half-hourly refresh cancelled every stop timer, so the 2026-11-09 04:22
    capture ran on for three hours and would have blocked the next pass."""
    monkeypatch.setattr("utils.weather_sat_predict.predict_passes", lambda **kw: [])
    now = datetime.now(timezone.utc)
    sp = ScheduledPass(
        {
            "id": "METEOR-M2-4_now",
            "satellite": "METEOR-M2-4",
            "name": "Meteor-M2-4",
            "frequency": 137.9,
            "mode": "LRPT",
            "startTimeISO": (now - timedelta(minutes=5)).isoformat(),
            "endTimeISO": (now + timedelta(minutes=10)).isoformat(),
            "maxEl": 44.0,
            "duration": 900.0,
            "quality": "good",
        }
    )
    sp.status = "capturing"
    sp._stop_timer = MagicMock()
    waiting = ScheduledPass({**sp.to_dict(), "id": "later", "startTimeISO": (now + timedelta(hours=3)).isoformat(),
                             "endTimeISO": (now + timedelta(hours=3, minutes=15)).isoformat()})
    waiting._timer = MagicMock()

    scheduler = WeatherSatScheduler()
    scheduler._enabled = True
    scheduler._passes = [sp, waiting]
    with patch("threading.Timer"):
        scheduler._refresh_passes()

    sp._stop_timer.cancel.assert_not_called()
    waiting._timer.cancel.assert_called_once()
    assert sp in scheduler._passes

