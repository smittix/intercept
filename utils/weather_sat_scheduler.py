"""Weather satellite auto-scheduler.

Automatically captures satellite passes based on predicted pass times.
Uses threading.Timer for scheduling — no external dependencies required.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from utils.logging import get_logger
from utils.weather_sat import CaptureProgress, get_weather_sat_decoder

logger = get_logger("intercept.weather_sat_scheduler")

# Import config defaults
try:
    from config import (
        WEATHER_SAT_CAPTURE_BUFFER_SECONDS,
        WEATHER_SAT_SAMPLE_RATE,
        WEATHER_SAT_SCHEDULE_REFRESH_MINUTES,
    )
except ImportError:
    WEATHER_SAT_SCHEDULE_REFRESH_MINUTES = 30
    WEATHER_SAT_CAPTURE_BUFFER_SECONDS = 30
    WEATHER_SAT_SAMPLE_RATE = 2400000


# Database setting holding the schedule (WeatherSatScheduler._save)
SETTING_KEY = "weather_sat.schedule"

class ScheduledPass:
    """A pass scheduled for automatic capture."""

    def __init__(self, pass_data: dict[str, Any]):
        self.id: str = pass_data.get("id", str(uuid.uuid4())[:8])
        self.satellite: str = pass_data["satellite"]
        self.name: str = pass_data["name"]
        self.frequency: float = pass_data["frequency"]
        self.mode: str = pass_data["mode"]
        self.start_time: str = pass_data["startTimeISO"]
        self.end_time: str = pass_data["endTimeISO"]
        self.max_el: float = pass_data["maxEl"]
        self.duration: float = pass_data["duration"]
        self.quality: str = pass_data["quality"]
        self.status: str = "scheduled"  # scheduled, capturing, complete, skipped
        self.skipped: bool = False
        self._timer: threading.Timer | None = None
        self._stop_timer: threading.Timer | None = None

    @property
    def start_dt(self) -> datetime:
        return _parse_utc_iso(self.start_time)

    @property
    def end_dt(self) -> datetime:
        return _parse_utc_iso(self.end_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "satellite": self.satellite,
            "name": self.name,
            "frequency": self.frequency,
            "mode": self.mode,
            "startTimeISO": self.start_time,
            "endTimeISO": self.end_time,
            "maxEl": self.max_el,
            "duration": self.duration,
            "quality": self.quality,
            "status": self.status,
            "skipped": self.skipped,
        }


class WeatherSatScheduler:
    """Auto-scheduler for weather satellite captures."""

    def __init__(self):
        self._enabled = False
        self._lock = threading.Lock()
        self._passes: list[ScheduledPass] = []
        self._refresh_timer: threading.Timer | None = None
        self._lat: float = 0.0
        self._lon: float = 0.0
        self._min_elevation: float = 15.0
        self._device: int = 0
        self._gain: float = 40.0
        self._bias_t: bool = False
        self._progress_callback: Callable[[CaptureProgress], None] | None = None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_callbacks(
        self,
        progress_callback: Callable[[CaptureProgress], None],
        event_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Set callbacks for progress and scheduler events."""
        self._progress_callback = progress_callback
        self._event_callback = event_callback

    def enable(
        self,
        lat: float,
        lon: float,
        min_elevation: float = 15.0,
        device: int = 0,
        gain: float = 40.0,
        bias_t: bool = False,
    ) -> dict[str, Any]:
        """Enable auto-scheduling.

        Args:
            lat: Observer latitude
            lon: Observer longitude
            min_elevation: Minimum pass elevation to capture
            device: RTL-SDR device index
            gain: SDR gain in dB
            bias_t: Enable bias-T

        Returns:
            Status dict with scheduled passes.
        """
        with self._lock:
            self._lat = lat
            self._lon = lon
            self._min_elevation = min_elevation
            self._device = device
            self._gain = gain
            self._bias_t = bias_t
            self._enabled = True

        self._save(enabled=True)
        self._refresh_passes()

        return self.get_status()

    def disable(self) -> dict[str, Any]:
        """Disable auto-scheduling and cancel all timers."""
        with self._lock:
            self._enabled = False

            # Cancel refresh timer
            if self._refresh_timer:
                self._refresh_timer.cancel()
                self._refresh_timer = None

            # Cancel all pass timers
            for p in self._passes:
                if p._timer:
                    p._timer.cancel()
                    p._timer = None
                if p._stop_timer:
                    p._stop_timer.cancel()
                    p._stop_timer = None

            self._passes.clear()

        self._save(enabled=False)
        logger.info("Weather satellite auto-scheduler disabled")
        return {"status": "disabled"}

    def _save(self, enabled: bool) -> None:
        """Keep the settings, so a restart or reboot doesn't turn the schedule off."""
        settings = {
            "enabled": enabled,
            "lat": self._lat,
            "lon": self._lon,
            "min_elevation": self._min_elevation,
            "device": self._device,
            "gain": self._gain,
            "bias_t": self._bias_t,
        }
        try:
            from utils.database import set_setting

            set_setting(SETTING_KEY, settings)
        except Exception as e:
            logger.warning(f"Couldn't save the weather satellite schedule: {e}")

    def restore(self) -> bool:
        """Switch the schedule back on if it was on before the app stopped."""
        try:
            from utils.database import get_setting

            saved = get_setting(SETTING_KEY)
        except Exception as e:
            logger.warning(f"Couldn't read the saved weather satellite schedule: {e}")
            return False
        if not isinstance(saved, dict) or not saved.get("enabled") or self._enabled:
            return False
        saved = {k: v for k, v in saved.items() if k != "enabled"}
        self.enable(**saved)
        logger.info("Weather satellite auto-scheduler restored from saved settings")
        return True

    def skip_pass(self, pass_id: str) -> bool:
        """Manually skip a scheduled pass."""
        with self._lock:
            for p in self._passes:
                if p.id == pass_id and p.status == "scheduled":
                    p.skipped = True
                    p.status = "skipped"
                    if p._timer:
                        p._timer.cancel()
                        p._timer = None
                    logger.info(f"Skipped pass: {p.satellite} at {p.start_time}")
                    self._emit_event(
                        {
                            "type": "schedule_capture_skipped",
                            "pass": p.to_dict(),
                            "reason": "manual",
                        }
                    )
                    return True
        return False

    def get_status(self) -> dict[str, Any]:
        """Get current scheduler status."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "observer": {"latitude": self._lat, "longitude": self._lon},
                "device": self._device,
                "gain": self._gain,
                "bias_t": self._bias_t,
                "min_elevation": self._min_elevation,
                "scheduled_count": sum(1 for p in self._passes if p.status == "scheduled"),
                "total_passes": len(self._passes),
            }

    def get_passes(self) -> list[dict[str, Any]]:
        """Get list of scheduled passes."""
        with self._lock:
            return [p.to_dict() for p in self._passes]

    def _refresh_passes(self) -> None:
        """Recompute passes and schedule timers."""
        if not self._enabled:
            return

        try:
            from utils.weather_sat_predict import predict_passes

            passes = predict_passes(
                lat=self._lat,
                lon=self._lon,
                hours=24,
                min_elevation=self._min_elevation,
            )
        except Exception as e:
            logger.error(f"Failed to predict passes for scheduler: {e}")
            passes = []

        with self._lock:
            # Cancel existing timers
            for p in self._passes:
                if p._timer:
                    p._timer.cancel()
                # A capture under way keeps its stop: this runs every half hour,
                # and a cancelled stop left a recording running for hours
                if p._stop_timer and p.status != "capturing":
                    p._stop_timer.cancel()

            # Keep completed/skipped for history, replace scheduled
            history = [p for p in self._passes if p.status in ("complete", "skipped", "capturing")]
            self._passes = history

            now = datetime.now(timezone.utc)
            buffer = WEATHER_SAT_CAPTURE_BUFFER_SECONDS

            for pass_data in passes:
                try:
                    sp = ScheduledPass(pass_data)
                    start_dt = sp.start_dt
                    end_dt = sp.end_dt
                except Exception as e:
                    logger.warning(f"Skipping invalid pass data: {e}")
                    continue

                capture_start = start_dt - timedelta(seconds=buffer)
                capture_end = end_dt + timedelta(seconds=buffer)

                # Skip passes that are already over
                if capture_end <= now:
                    continue

                # Check if already in history
                if any(h.id == sp.id for h in history):
                    continue

                # Schedule capture timer. If we're already inside the capture
                # window, trigger immediately instead of skipping the pass.
                delay = max(0.0, (capture_start - now).total_seconds())
                sp._timer = threading.Timer(delay, self._execute_capture, args=[sp])
                sp._timer.daemon = True
                sp._timer.start()
                self._passes.append(sp)

            logger.info(
                f"Scheduler refreshed: {sum(1 for p in self._passes if p.status == 'scheduled')} passes scheduled"
            )

        # Schedule next refresh
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self._refresh_timer = threading.Timer(
            WEATHER_SAT_SCHEDULE_REFRESH_MINUTES * 60,
            self._refresh_passes,
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _execute_capture(self, sp: ScheduledPass) -> None:
        """Execute capture for a scheduled pass."""
        if not self._enabled or sp.skipped:
            return

        decoder = get_weather_sat_decoder()

        if decoder.is_running:
            logger.info(f"SDR busy, skipping scheduled pass: {sp.satellite}")
            sp.status = "skipped"
            sp.skipped = True
            self._emit_event(
                {
                    "type": "schedule_capture_skipped",
                    "pass": sp.to_dict(),
                    "reason": "sdr_busy",
                }
            )
            return

        # Claim SDR device
        try:
            import app as app_module

            error = app_module.claim_sdr_device(self._device, "weather_sat")
            if error:
                logger.info(f"SDR device busy, skipping: {sp.satellite} - {error}")
                sp.status = "skipped"
                sp.skipped = True
                self._emit_event(
                    {
                        "type": "schedule_capture_skipped",
                        "pass": sp.to_dict(),
                        "reason": "device_busy",
                    }
                )
                return
        except ImportError:
            pass

        sp.status = "capturing"

        # Set up callbacks
        if self._progress_callback:
            decoder.set_callback(self._progress_callback)

        def _release_device():
            try:
                import app as app_module

                owner = None
                get_status = getattr(app_module, "get_sdr_device_status", None)
                if callable(get_status):
                    try:
                        owner = get_status().get(self._device)
                    except Exception:
                        owner = None
                if owner and owner != "weather_sat":
                    logger.debug(
                        "Skipping SDR release for device %s owned by %s",
                        self._device,
                        owner,
                    )
                    return
                app_module.release_sdr_device(self._device)
            except ImportError:
                pass

        decoder.set_on_complete(lambda: self._on_capture_complete(sp, _release_device))

        success, _error_msg = decoder.start(
            satellite=sp.satellite,
            device_index=self._device,
            gain=self._gain,
            sample_rate=WEATHER_SAT_SAMPLE_RATE,
            bias_t=self._bias_t,
        )

        if success:
            logger.info(f"Auto-scheduler started capture: {sp.satellite}")
            self._emit_event(
                {
                    "type": "schedule_capture_start",
                    "pass": sp.to_dict(),
                }
            )

            # Schedule stop timer at pass end + buffer
            now = datetime.now(timezone.utc)
            stop_delay = (sp.end_dt + timedelta(seconds=WEATHER_SAT_CAPTURE_BUFFER_SECONDS) - now).total_seconds()
            if stop_delay > 0:
                sp._stop_timer = threading.Timer(stop_delay, self._stop_capture, args=[sp])
                sp._stop_timer.daemon = True
                sp._stop_timer.start()
        else:
            sp.status = "skipped"
            _release_device()
            self._emit_event(
                {
                    "type": "schedule_capture_skipped",
                    "pass": sp.to_dict(),
                    "reason": "start_failed",
                }
            )

    def _stop_capture(self, sp: ScheduledPass) -> None:
        """Stop capture at pass end."""
        decoder = get_weather_sat_decoder()
        if decoder.is_running:
            decoder.stop()
            logger.info(f"Auto-scheduler stopped capture: {sp.satellite}")

    def _on_capture_complete(self, sp: ScheduledPass, release_fn: Callable) -> None:
        """Handle capture completion."""
        sp.status = "complete"
        release_fn()
        self._emit_event(
            {
                "type": "schedule_capture_complete",
                "pass": sp.to_dict(),
            }
        )

    def _emit_event(self, event: dict[str, Any]) -> None:
        """Emit scheduler event to callback."""
        if self._event_callback:
            try:
                self._event_callback(event)
            except Exception as e:
                logger.error(f"Error in scheduler event callback: {e}")


def _parse_utc_iso(value: str) -> datetime:
    """Parse UTC ISO8601 timestamp robustly across Python versions."""
    if not value:
        raise ValueError("missing timestamp")

    text = str(value).strip()
    # Backward compatibility for malformed legacy strings.
    text = text.replace("+00:00Z", "Z")
    # Python <3.11 does not accept trailing 'Z' in fromisoformat.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# Singleton
_scheduler: WeatherSatScheduler | None = None
_scheduler_lock = threading.Lock()


def get_weather_sat_scheduler() -> WeatherSatScheduler:
    """Get or create the global weather satellite scheduler instance."""
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = WeatherSatScheduler()
    return _scheduler
