"""RF control-link detector — rtl_433 (433/868MHz) + hackrf_sweep (2.4/5.8GHz)."""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timezone

from utils.process import register_process, safe_terminate

from .models import RFObservation
from .signatures import match_signature

logger = logging.getLogger("intercept.drone.rf_detector")

_HACKRF_THRESHOLD_DBM = -90.0
_DRONE_FREQ_RANGES_HZ = [
    (433_000_000, 435_000_000),
    (868_000_000, 869_000_000),
    (2_400_000_000, 2_484_000_000),
    (5_725_000_000, 5_875_000_000),
]


def _in_drone_band(freq_hz: int) -> bool:
    return any(lo <= freq_hz <= hi for lo, hi in _DRONE_FREQ_RANGES_HZ)


class RFDetector:
    def __init__(self, output_queue: queue.Queue) -> None:
        self._queue = output_queue
        self._stop_event = threading.Event()
        self._stop_event.set()  # starts in stopped state
        self._proc_lock = threading.Lock()
        self._rtl_proc: subprocess.Popen | None = None
        self._hackrf_proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return not self._stop_event.is_set()

    def _handle_rtl433_line(self, line: str) -> None:
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        freq = data.get("freq")
        rssi = data.get("rssi")
        if freq is None or rssi is None:
            return
        freq_hz = int(float(freq))
        if not _in_drone_band(freq_hz):
            return
        protocol = match_signature(freq_hz)
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(
                RFObservation(
                    frequency_hz=freq_hz,
                    protocol=protocol,
                    rssi=float(rssi),
                    hardware="RTL433",
                    timestamp=datetime.now(timezone.utc),
                )
            )

    def _handle_hackrf_line(self, line: str) -> None:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            return
        try:
            hz_low = int(parts[2])
            hz_high = int(parts[3])
            db_values = [float(p) for p in parts[6:] if p]
        except (ValueError, IndexError):
            return
        if not db_values:
            return
        avg_db = sum(db_values) / len(db_values)
        if avg_db < _HACKRF_THRESHOLD_DBM:
            return
        freq_hz = (hz_low + hz_high) // 2
        if not _in_drone_band(freq_hz):
            return
        protocol = match_signature(freq_hz)
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(
                RFObservation(
                    frequency_hz=freq_hz,
                    protocol=protocol,
                    rssi=avg_db,
                    hardware="HACKRF",
                    timestamp=datetime.now(timezone.utc),
                )
            )

    def _run_rtl433(self, device_index: int) -> None:
        rtl_bin = shutil.which("rtl_433")
        if not rtl_bin:
            logger.warning("rtl_433 not found — RTL-SDR RF detection disabled")
            return
        cmd = [rtl_bin, "-d", str(device_index), "-F", "json", "-f", "433920000", "-f", "868300000"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            register_process(proc)
            with self._proc_lock:
                self._rtl_proc = proc
            for raw_line in iter(proc.stdout.readline, b""):
                if self._stop_event.is_set():
                    break
                self._handle_rtl433_line(raw_line.decode("utf-8", errors="replace").strip())
            safe_terminate(proc)
        except Exception as exc:
            logger.warning("rtl_433 error: %s", exc)

    def _run_hackrf(self) -> None:
        hackrf_bin = shutil.which("hackrf_sweep")
        if not hackrf_bin:
            logger.warning("hackrf_sweep not found — HackRF RF detection disabled")
            return
        cmd = [hackrf_bin, "-f", "2400:2484", "-f", "5725:5875", "-w", "1000000"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            register_process(proc)
            with self._proc_lock:
                self._hackrf_proc = proc
            for raw_line in iter(proc.stdout.readline, b""):
                if self._stop_event.is_set():
                    break
                self._handle_hackrf_line(raw_line.decode("utf-8", errors="replace").strip())
            safe_terminate(proc)
        except Exception as exc:
            logger.warning("hackrf_sweep error: %s", exc)

    def start(self, rtl_sdr_index: int = 0, use_hackrf: bool = True) -> None:
        if self.running:
            return
        self._stop_event.clear()
        t1 = threading.Thread(target=self._run_rtl433, args=(rtl_sdr_index,), daemon=True)
        t1.start()
        self._threads.append(t1)
        if use_hackrf:
            t2 = threading.Thread(target=self._run_hackrf, daemon=True)
            t2.start()
            self._threads.append(t2)

    def stop(self) -> None:
        self._stop_event.set()
        with self._proc_lock:
            rtl_proc = self._rtl_proc
            hackrf_proc = self._hackrf_proc
            self._rtl_proc = None
            self._hackrf_proc = None
        safe_terminate(rtl_proc)
        safe_terminate(hackrf_proc)
        self._threads.clear()
