"""Receiver routes for radio monitoring and frequency scanning.

This package splits the listening post into sub-modules:
  scanner  - /scanner/*, /presets routes
  audio    - /audio/* routes
  waterfall - /waterfall/* routes
  tools    - /tools, /signal/guess routes
"""

from __future__ import annotations

import errno
import os
import queue
import shutil
import signal
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from flask import Blueprint

from utils.constants import (
    PROCESS_TERMINATE_TIMEOUT,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.event_pipeline import process_event
from utils.logging import get_logger
from utils.sdr import SDRFactory, SDRType
from utils.sse import sse_stream_fanout

logger = get_logger("intercept.receiver")

receiver_bp = Blueprint("receiver", __name__, url_prefix="/receiver")

# Deferred import to avoid circular import at module load time.
# app.py -> register_blueprints -> from .listening_post import receiver_bp
# must find receiver_bp already defined (above) before this import runs.
import contextlib

import app as app_module  # noqa: E402

# ============================================
# GLOBAL STATE
# ============================================

# Audio demodulation state
audio_process = None
audio_rtl_process = None
audio_lock = threading.Lock()
audio_start_lock = threading.Lock()
audio_running = False
audio_frequency = 0.0
audio_start_error: BaseException | None = None  # why the last start failed, for the route
audio_modulation = "fm"
audio_source = "process"
audio_start_token = 0

# Scanner state
scanner_thread: threading.Thread | None = None
scanner_running = False
scanner_lock = threading.Lock()
scanner_paused = False
scanner_current_freq = 0.0
scanner_active_device: int | None = None
scanner_active_sdr_type: str = "rtlsdr"
receiver_active_device: int | None = None
receiver_active_sdr_type: str = "rtlsdr"
scanner_power_process: subprocess.Popen | None = None
scanner_config = {
    "start_freq": 88.0,
    "end_freq": 108.0,
    "step": 0.1,
    "modulation": "wfm",
    "squelch": 0,
    "dwell_time": 10.0,  # Seconds to stay on active frequency
    "scan_delay": 0.1,  # Seconds between frequency hops (keep low for fast scanning)
    "device": 0,
    "gain": 40,
    "bias_t": False,  # Bias-T power for external LNA
    "sdr_type": "rtlsdr",  # SDR type: rtlsdr, hackrf, airspy, limesdr, sdrplay
    "scan_method": "power",  # power (rtl_power) or classic (rtl_fm hop)
    "snr_threshold": 8,
}

# Activity log
activity_log: list[dict] = []
activity_log_lock = threading.Lock()
MAX_LOG_ENTRIES = 500

# SSE queue for scanner events
scanner_queue: queue.Queue = queue.Queue(maxsize=100)

# Flag to trigger skip from API
scanner_skip_signal = False

# Waterfall / spectrogram state
waterfall_process: subprocess.Popen | None = None
waterfall_thread: threading.Thread | None = None
waterfall_running = False
waterfall_lock = threading.Lock()
waterfall_queue: queue.Queue = queue.Queue(maxsize=200)
waterfall_active_device: int | None = None
waterfall_active_sdr_type: str = "rtlsdr"
waterfall_config = {
    "start_freq": 88.0,
    "end_freq": 108.0,
    "bin_size": 10000,
    "gain": 40,
    "device": 0,
    "max_bins": 1024,
    "interval": 0.4,
}


# ============================================
# HELPER FUNCTIONS (shared across sub-modules)
# ============================================

VALID_MODULATIONS = ["fm", "wfm", "am", "usb", "lsb"]


def find_rtl_fm() -> str | None:
    """Find rtl_fm binary."""
    return shutil.which("rtl_fm")


def find_rtl_power() -> str | None:
    """Find rtl_power binary."""
    return shutil.which("rtl_power")


def find_rx_fm() -> str | None:
    """Find rx_fm binary (SoapySDR FM demodulator for HackRF/Airspy/LimeSDR)."""
    return shutil.which("rx_fm")


def find_ffmpeg() -> str | None:
    """Find ffmpeg for audio encoding."""
    return shutil.which("ffmpeg")


def normalize_modulation(value: str) -> str:
    """Normalize and validate modulation string."""
    mod = str(value or "").lower().strip()
    if mod not in VALID_MODULATIONS:
        raise ValueError(f"Invalid modulation. Use: {', '.join(VALID_MODULATIONS)}")
    return mod


def _rtl_fm_demod_mode(modulation: str) -> str:
    """Map UI modulation names to rtl_fm demod tokens."""
    mod = str(modulation or "").lower().strip()
    return "wbfm" if mod == "wfm" else mod


def _wav_header(sample_rate: int = 48000, bits_per_sample: int = 16, channels: int = 1) -> bytes:
    """Create a streaming WAV header with unknown data length."""
    bytes_per_sample = bits_per_sample // 8
    byte_rate = sample_rate * channels * bytes_per_sample
    block_align = channels * bytes_per_sample
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )


def add_activity_log(event_type: str, frequency: float, details: str = ""):
    """Add entry to activity log."""
    with activity_log_lock:
        entry = {
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "type": event_type,
            "frequency": frequency,
            "details": details,
        }
        activity_log.insert(0, entry)
        # Trim log
        while len(activity_log) > MAX_LOG_ENTRIES:
            activity_log.pop()

        # Also push to SSE queue
        with contextlib.suppress(queue.Full):
            scanner_queue.put_nowait({"type": "log", "entry": entry})


def _start_audio_stream(
    frequency: float,
    modulation: str,
    *,
    device: int | None = None,
    sdr_type: str | None = None,
    gain: int | None = None,
    squelch: int | None = None,
    bias_t: bool | None = None,
):
    """Start audio streaming at given frequency."""
    global audio_process, audio_rtl_process, audio_running, audio_frequency, audio_modulation, audio_start_error
    audio_start_error = None

    # Stop existing stream and snapshot config under lock
    with audio_lock:
        _stop_audio_stream_internal()

        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            logger.error("ffmpeg not found")
            audio_start_error = FileNotFoundError(errno.ENOENT, "not found", "ffmpeg")
            return

        # Snapshot runtime tuning config so the spawned demod command cannot
        # drift if shared scanner_config changes while startup is in-flight.
        device_index = int(device if device is not None else scanner_config.get("device", 0))
        gain_value = int(gain if gain is not None else scanner_config.get("gain", 40))
        squelch_value = int(squelch if squelch is not None else scanner_config.get("squelch", 0))
        bias_t_enabled = bool(scanner_config.get("bias_t", False) if bias_t is None else bias_t)
        sdr_type_str = str(sdr_type if sdr_type is not None else scanner_config.get("sdr_type", "rtlsdr")).lower()

    # Build commands outside lock (no blocking I/O, just command construction)
    try:
        resolved_sdr_type = SDRType(sdr_type_str)
    except ValueError:
        resolved_sdr_type = SDRType.RTL_SDR

    # Set sample rates based on modulation
    if modulation == "wfm":
        sample_rate = 170000
        resample_rate = 32000
    elif modulation in ["usb", "lsb"]:
        sample_rate = 12000
        resample_rate = 12000
    else:
        sample_rate = 24000
        resample_rate = 24000

    # Build the SDR command based on device type
    if resolved_sdr_type == SDRType.RTL_SDR:
        rtl_fm_path = find_rtl_fm()
        if not rtl_fm_path:
            logger.error("rtl_fm not found")
            audio_start_error = FileNotFoundError(errno.ENOENT, "not found", "rtl_fm")
            return

        freq_hz = int(frequency * 1e6)
        sdr_cmd = [
            rtl_fm_path,
            "-M",
            _rtl_fm_demod_mode(modulation),
            "-f",
            str(freq_hz),
            "-s",
            str(sample_rate),
            "-r",
            str(resample_rate),
            "-g",
            str(gain_value),
            "-d",
            str(device_index),
            "-l",
            str(squelch_value),
        ]
        if bias_t_enabled:
            sdr_cmd.append("-T")
    else:
        rx_fm_path = find_rx_fm()
        if not rx_fm_path:
            logger.error(f"rx_fm not found - required for {resolved_sdr_type.value}. Install SoapySDR utilities.")
            audio_start_error = FileNotFoundError(errno.ENOENT, "not found", "rx_fm")
            return

        sdr_device = SDRFactory.create_default_device(resolved_sdr_type, index=device_index)
        builder = SDRFactory.get_builder(resolved_sdr_type)
        sdr_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=frequency,
            sample_rate=resample_rate,
            gain=float(gain_value),
            modulation=modulation,
            squelch=squelch_value,
            bias_t=bias_t_enabled,
        )
        sdr_cmd[0] = rx_fm_path

    encoder_cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-f",
        "s16le",
        "-ar",
        str(resample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-f",
        "wav",
        "pipe:1",
    ]

    # Retry loop outside lock — spawning + health check sleeps don't block
    # other operations. audio_start_lock already serializes callers.
    try:
        rtl_stderr_log = "/tmp/rtl_fm_stderr.log"
        ffmpeg_stderr_log = "/tmp/ffmpeg_stderr.log"
        logger.info(f"Starting audio: {frequency} MHz, mod={modulation}, device={device_index}")

        new_rtl_proc = None
        new_audio_proc = None
        max_attempts = 3
        for attempt in range(max_attempts):
            new_rtl_proc = None
            new_audio_proc = None
            rtl_err_handle = None
            ffmpeg_err_handle = None
            try:
                rtl_err_handle = open(rtl_stderr_log, "w")
                ffmpeg_err_handle = open(ffmpeg_stderr_log, "w")
                new_rtl_proc = subprocess.Popen(
                    sdr_cmd, stdout=subprocess.PIPE, stderr=rtl_err_handle, bufsize=0, start_new_session=True
                )
                new_audio_proc = subprocess.Popen(
                    encoder_cmd,
                    stdin=new_rtl_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=ffmpeg_err_handle,
                    bufsize=0,
                    start_new_session=True,
                )
                if new_rtl_proc.stdout:
                    new_rtl_proc.stdout.close()
            finally:
                if rtl_err_handle:
                    rtl_err_handle.close()
                if ffmpeg_err_handle:
                    ffmpeg_err_handle.close()

            # Brief delay to check if process started successfully
            time.sleep(0.3)

            if (new_rtl_proc and new_rtl_proc.poll() is not None) or (
                new_audio_proc and new_audio_proc.poll() is not None
            ):
                rtl_stderr = ""
                ffmpeg_stderr = ""
                try:
                    with open(rtl_stderr_log) as f:
                        rtl_stderr = f.read().strip()
                except Exception:
                    pass
                try:
                    with open(ffmpeg_stderr_log) as f:
                        ffmpeg_stderr = f.read().strip()
                except Exception:
                    pass

                if "usb_claim_interface" in rtl_stderr and attempt < max_attempts - 1:
                    logger.warning(f"USB device busy (attempt {attempt + 1}/{max_attempts}), waiting for release...")
                    if new_audio_proc:
                        try:
                            new_audio_proc.terminate()
                            new_audio_proc.wait(timeout=0.5)
                        except Exception:
                            pass
                    if new_rtl_proc:
                        try:
                            new_rtl_proc.terminate()
                            new_rtl_proc.wait(timeout=0.5)
                        except Exception:
                            pass
                    time.sleep(1.0)
                    continue

                if new_audio_proc and new_audio_proc.poll() is None:
                    try:
                        new_audio_proc.terminate()
                        new_audio_proc.wait(timeout=0.5)
                    except Exception:
                        pass
                if new_rtl_proc and new_rtl_proc.poll() is None:
                    try:
                        new_rtl_proc.terminate()
                        new_rtl_proc.wait(timeout=0.5)
                    except Exception:
                        pass
                new_audio_proc = None
                new_rtl_proc = None

                logger.error(
                    f"Audio pipeline exited immediately. rtl_fm stderr: {rtl_stderr}, ffmpeg stderr: {ffmpeg_stderr}"
                )
                return

            # Pipeline started successfully
            break

        # Verify pipeline is still alive, then install under lock
        if (
            not new_audio_proc
            or not new_rtl_proc
            or new_audio_proc.poll() is not None
            or new_rtl_proc.poll() is not None
        ):
            logger.warning("Audio pipeline did not remain alive after startup")
            # Clean up failed processes
            if new_audio_proc:
                try:
                    new_audio_proc.terminate()
                    new_audio_proc.wait(timeout=0.5)
                except Exception:
                    pass
            if new_rtl_proc:
                try:
                    new_rtl_proc.terminate()
                    new_rtl_proc.wait(timeout=0.5)
                except Exception:
                    pass
            return

        # Install processes under lock
        with audio_lock:
            audio_rtl_process = new_rtl_proc
            audio_process = new_audio_proc
            audio_running = True
            audio_frequency = frequency
            audio_modulation = modulation
            logger.info(f"Audio stream started: {frequency} MHz ({modulation}) via {resolved_sdr_type.value}")

    except Exception as e:
        logger.error(f"Failed to start audio stream: {e}")
        audio_start_error = e


def _stop_audio_stream():
    """Stop audio streaming."""
    with audio_lock:
        _stop_audio_stream_internal()


def _stop_audio_stream_internal():
    """Internal stop (must hold lock)."""
    global audio_process, audio_rtl_process, audio_running, audio_frequency, audio_source

    # Set flag first to stop any streaming
    audio_running = False
    audio_frequency = 0.0
    previous_source = audio_source
    audio_source = "process"

    if previous_source == "waterfall":
        try:
            from routes.waterfall_websocket import stop_shared_monitor_from_capture

            stop_shared_monitor_from_capture()
        except Exception:
            pass

    had_processes = audio_process is not None or audio_rtl_process is not None

    def _stop_proc_group(proc):
        if not proc:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(Exception):
                proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(Exception):
                    proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=0.5)

    _stop_proc_group(audio_process)
    _stop_proc_group(audio_rtl_process)
    audio_process = None
    audio_rtl_process = None

    # Brief pause for SDR device USB interface to be released by kernel.
    # The _start_audio_stream retry loop handles longer contention windows
    # so only a minimal delay is needed here.
    if had_processes:
        time.sleep(0.15)


def _stop_waterfall_internal() -> None:
    """Stop the waterfall display and release resources."""
    global waterfall_running, waterfall_process, waterfall_active_device, waterfall_active_sdr_type

    waterfall_running = False
    if waterfall_process and waterfall_process.poll() is None:
        try:
            waterfall_process.terminate()
            waterfall_process.wait(timeout=1)
        except Exception:
            with contextlib.suppress(Exception):
                waterfall_process.kill()
        waterfall_process = None

    if waterfall_active_device is not None:
        app_module.release_sdr_device(waterfall_active_device, waterfall_active_sdr_type)
        waterfall_active_device = None
        waterfall_active_sdr_type = "rtlsdr"


# ============================================
# Import sub-modules to register routes on receiver_bp
# ============================================
from . import (
    audio,  # noqa: E402, F401
    scanner,  # noqa: E402, F401
    tools,  # noqa: E402, F401
    waterfall,  # noqa: E402, F401
)
