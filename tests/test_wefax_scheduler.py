"""Tests for WeFax auto-scheduler behavior and regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from utils.wefax_scheduler import ScheduledBroadcast, WeFaxScheduler


class TestWeFaxScheduler:
    """WeFaxScheduler regression tests."""

    @patch("threading.Timer")
    def test_refresh_reschedules_same_utc_slot_next_day(self, mock_timer):
        """Completed broadcasts must not block the next day's same UTC slot."""
        scheduler = WeFaxScheduler()
        scheduler._enabled = True
        scheduler._station = "USCG Kodiak"
        scheduler._callsign = "NOJ"
        scheduler._frequency_khz = 4298.0

        now = datetime.now(timezone.utc)
        utc_time = (now - timedelta(hours=2)).strftime("%H:%M")
        today = now.date().isoformat()

        prior = ScheduledBroadcast(
            station="USCG Kodiak",
            callsign="NOJ",
            frequency_khz=4298.0,
            utc_time=utc_time,
            duration_min=20,
            content="Chart",
            occurrence_date=today,
        )
        prior.status = "complete"
        scheduler._broadcasts = [prior]

        mock_timer.return_value = MagicMock()

        with patch(
            "utils.wefax_scheduler.get_station",
            return_value={
                "name": "USCG Kodiak",
                "schedule": [
                    {
                        "utc": utc_time,
                        "duration_min": 20,
                        "content": "Chart",
                    }
                ],
            },
        ):
            scheduler._refresh_schedule()

        capture_calls = [
            c
            for c in mock_timer.call_args_list
            if len(c.args) >= 2 and getattr(c.args[1], "__name__", "") == "_execute_capture"
        ]
        assert capture_calls, "Expected a capture timer for the next-day occurrence"

        scheduled = [b for b in scheduler._broadcasts if b.status == "scheduled"]
        assert len(scheduled) == 1
        assert scheduled[0].occurrence_date != today

    def test_execute_capture_stops_immediately_if_window_elapsed(self):
        """If stop delay computes to <= 0, capture should close out immediately."""
        scheduler = WeFaxScheduler()
        scheduler._enabled = True
        scheduler._callsign = "NOJ"
        scheduler._frequency_khz = 4298.0
        scheduler._device = 0
        scheduler._gain = 40.0
        scheduler._ioc = 576
        scheduler._lpm = 120
        scheduler._direct_sampling = True

        now = datetime.now(timezone.utc)
        sb = ScheduledBroadcast(
            station="USCG Kodiak",
            callsign="NOJ",
            frequency_khz=4298.0,
            utc_time=now.strftime("%H:%M"),
            duration_min=0,
            content="Late chart",
            occurrence_date=now.date().isoformat(),
        )
        sb.status = "scheduled"

        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True

        with (
            patch("utils.wefax_scheduler.get_wefax_decoder", return_value=mock_decoder),
            patch("utils.wefax_scheduler.WEFAX_CAPTURE_BUFFER_SECONDS", 0),
            patch("app.claim_sdr_device", return_value=None),
            patch.object(scheduler, "_stop_capture") as mock_stop_capture,
        ):
            scheduler._execute_capture_inner(sb)

        mock_stop_capture.assert_called_once()

    @patch("threading.Timer")
    def test_terminal_progress_releases_scheduler_device_early(self, mock_timer):
        """Scheduler captures must release SDR as soon as terminal progress arrives."""
        # A broadcast still ahead, whatever the time of day: one whose window
        # has already passed is stopped (and completed) as soon as it starts.
        upcoming = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%H:%M")
        scheduler = WeFaxScheduler()
        scheduler._enabled = True
        scheduler._callsign = "NOJ"
        scheduler._frequency_khz = 4298.0
        scheduler._device = 0
        scheduler._gain = 40.0
        scheduler._ioc = 576
        scheduler._lpm = 120
        scheduler._direct_sampling = True

        sb = ScheduledBroadcast(
            station="USCG Kodiak",
            callsign="NOJ",
            frequency_khz=4298.0,
            utc_time=upcoming,
            duration_min=20,
            content="Chart",
            occurrence_date="2026-01-01",
        )
        sb.status = "scheduled"

        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True
        mock_timer.return_value = MagicMock()

        with (
            patch("utils.wefax_scheduler.get_wefax_decoder", return_value=mock_decoder),
            patch("app.claim_sdr_device", return_value=None),
            patch("app.release_sdr_device") as mock_release,
        ):
            scheduler._execute_capture_inner(sb)
            progress_cb = mock_decoder.set_callback.call_args[0][0]
            progress_cb(
                {
                    "type": "wefax_progress",
                    "status": "error",
                    "message": "rtl_fm failed",
                }
            )

        mock_release.assert_called_once_with(0)
        assert sb.status == "skipped"

    def test_stop_capture_non_capturing_only_releases(self):
        """_stop_capture should be idempotent when capture already ended."""
        scheduler = WeFaxScheduler()
        sb = ScheduledBroadcast(
            station="USCG Kodiak",
            callsign="NOJ",
            frequency_khz=4298.0,
            utc_time="12:00",
            duration_min=20,
            content="Chart",
            occurrence_date="2026-01-01",
        )
        sb.status = "complete"
        release_fn = MagicMock()

        with patch("utils.wefax_scheduler.get_wefax_decoder") as mock_get_decoder:
            scheduler._stop_capture(sb, release_fn)

        release_fn.assert_called_once()
        mock_get_decoder.assert_not_called()
