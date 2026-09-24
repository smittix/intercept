"""WebSocket-based waterfall streaming with I/Q capture and server-side FFT."""

from __future__ import annotations

import json
import queue
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from contextlib import suppress
from typing import Any

import numpy as np
from flask import Flask

try:
    from flask_sock import Sock

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    Sock = None

from utils.logging import get_logger
from utils.process import register_process, safe_terminate, unregister_process
from utils.sdr import SDRFactory, SDRType
from utils.sdr.base import SDRCapabilities, SDRDevice
from utils.sdr.device_config import apply_device_defaults
from utils.validation import validate_ppm
from utils.waterfall_fft import (
    build_binary_frame,
    compute_power_spectrum,
    cu8_to_complex,
    quantize_to_uint8,
)

logger = get_logger("intercept.waterfall_ws")

AUDIO_SAMPLE_RATE = 48000
_shared_state_lock = threading.Lock()
_shared_audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=20)
_shared_state: dict[str, Any] = {
    "running": False,
    "device": None,
    "center_mhz": 0.0,
    "span_mhz": 0.0,
    "sample_rate": 0,
    "monitor_enabled": False,
    "monitor_freq_mhz": 0.0,
    "monitor_modulation": "wfm",
    "monitor_squelch": 0,
}
# Generation counter to prevent stale WebSocket handlers from clobbering
# shared state set by a newer handler (e.g. old handler's finally block
# running after a new connection has already started capture).
_capture_generation: int = 0

# Maximum bandwidth per SDR type (Hz)
MAX_BANDWIDTH = {
    SDRType.RTL_SDR: 2400000,
    SDRType.HACKRF: 20000000,
    SDRType.LIME_SDR: 20000000,
    SDRType.AIRSPY: 10000000,
    SDRType.SDRPLAY: 2000000,
    SDRType.USRP: 25000000,
    SDRType.BLADE_RF: 40000000,
    SDRType.HYDRA_SDR: 10000000,
}


def _clear_shared_audio_queue() -> None:
    while True:
        try:
            _shared_audio_queue.get_nowait()
        except queue.Empty:
            break


def _set_shared_capture_state(
    *,
    running: bool,
    device: int | None = None,
    center_mhz: float | None = None,
    span_mhz: float | None = None,
    sample_rate: int | None = None,
    generation: int | None = None,
) -> int:
    """Update shared capture state.

    Returns the current generation counter.  When *running* is True and
    *generation* is None the counter is bumped; callers should capture
    the returned value and pass it back when setting running=False so
    that stale handlers cannot clobber a newer session.
    """
    global _capture_generation
    with _shared_state_lock:
        if not running and generation is not None:
            # Only allow the matching generation to clear the state.
            if generation != _capture_generation:
                return _capture_generation
        if running and generation is None:
            _capture_generation += 1
        _shared_state["running"] = bool(running)
        _shared_state["device"] = device if running else None
        if center_mhz is not None:
            _shared_state["center_mhz"] = float(center_mhz)
        if span_mhz is not None:
            _shared_state["span_mhz"] = float(span_mhz)
        if sample_rate is not None:
            _shared_state["sample_rate"] = int(sample_rate)
        if not running:
            _shared_state["monitor_enabled"] = False
        gen = _capture_generation
    if not running:
        _clear_shared_audio_queue()
    return gen


def _set_shared_monitor(
    *,
    enabled: bool,
    frequency_mhz: float | None = None,
    modulation: str | None = None,
    squelch: int | None = None,
) -> None:
    was_enabled = False
    freq_changed = False
    with _shared_state_lock:
        was_enabled = bool(_shared_state.get("monitor_enabled"))
        _shared_state["monitor_enabled"] = bool(enabled)
        if frequency_mhz is not None:
            old_freq = float(_shared_state.get("monitor_freq_mhz", 0.0) or 0.0)
            _shared_state["monitor_freq_mhz"] = float(frequency_mhz)
            if abs(float(frequency_mhz) - old_freq) > 1e-6:
                freq_changed = True
        if modulation is not None:
            _shared_state["monitor_modulation"] = str(modulation).lower().strip()
        if squelch is not None:
            _shared_state["monitor_squelch"] = max(0, min(100, int(squelch)))
    if (was_enabled and not enabled) or (enabled and freq_changed):
        _clear_shared_audio_queue()


def get_shared_capture_status() -> dict[str, Any]:
    with _shared_state_lock:
        return {
            "running": bool(_shared_state["running"]),
            "device": _shared_state["device"],
            "center_mhz": float(_shared_state.get("center_mhz", 0.0) or 0.0),
            "span_mhz": float(_shared_state.get("span_mhz", 0.0) or 0.0),
            "sample_rate": int(_shared_state.get("sample_rate", 0) or 0),
            "monitor_enabled": bool(_shared_state.get("monitor_enabled")),
            "monitor_freq_mhz": float(_shared_state.get("monitor_freq_mhz", 0.0) or 0.0),
            "monitor_modulation": str(_shared_state.get("monitor_modulation", "wfm")),
            "monitor_squelch": int(_shared_state.get("monitor_squelch", 0) or 0),
        }


def start_shared_monitor_from_capture(
    *,
    device: int,
    frequency_mhz: float,
    modulation: str,
    squelch: int,
) -> tuple[bool, str]:
    with _shared_state_lock:
        if not _shared_state["running"]:
            return False, "Waterfall IQ stream not active"
        if _shared_state["device"] != device:
            return False, "Waterfall stream is using a different SDR device"
        _shared_state["monitor_enabled"] = True
        _shared_state["monitor_freq_mhz"] = float(frequency_mhz)
        _shared_state["monitor_modulation"] = str(modulation).lower().strip()
        _shared_state["monitor_squelch"] = max(0, min(100, int(squelch)))
    _clear_shared_audio_queue()
    return True, "started"


def stop_shared_monitor_from_capture() -> None:
    _set_shared_monitor(enabled=False)


def read_shared_monitor_audio_chunk(timeout: float = 1.0) -> bytes | None:
    with _shared_state_lock:
        if not _shared_state["running"] or not _shared_state["monitor_enabled"]:
            return None
    try:
        return _shared_audio_queue.get(timeout=max(0.0, float(timeout)))
    except queue.Empty:
        return None


def _snapshot_monitor_config() -> dict[str, Any] | None:
    with _shared_state_lock:
        if not (_shared_state["running"] and _shared_state["monitor_enabled"]):
            return None
        return {
            "center_mhz": float(_shared_state["center_mhz"]),
            "monitor_freq_mhz": float(_shared_state["monitor_freq_mhz"]),
            "modulation": str(_shared_state["monitor_modulation"]),
            "squelch": int(_shared_state["monitor_squelch"]),
        }


def _push_shared_audio_chunk(chunk: bytes) -> None:
    if not chunk:
        return
    if _shared_audio_queue.full():
        with suppress(queue.Empty):
            _shared_audio_queue.get_nowait()
    with suppress(queue.Full):
        _shared_audio_queue.put_nowait(chunk)


def _demodulate_monitor_audio(
    samples: np.ndarray,
    sample_rate: int,
    center_mhz: float,
    monitor_freq_mhz: float,
    modulation: str,
    squelch: int,
    rotator_phase: float = 0.0,
) -> tuple[bytes | None, float]:
    if samples.size < 32 or sample_rate <= 0:
        return None, float(rotator_phase)

    fs = float(sample_rate)
    freq_offset_hz = (float(monitor_freq_mhz) - float(center_mhz)) * 1e6
    nyquist = fs * 0.5
    if abs(freq_offset_hz) > nyquist * 0.98:
        return None, float(rotator_phase)

    phase_inc = (2.0 * np.pi * freq_offset_hz) / fs
    n = np.arange(samples.size, dtype=np.float64)
    rotator = np.exp(-1j * (float(rotator_phase) + phase_inc * n)).astype(np.complex64)
    next_phase = float((float(rotator_phase) + phase_inc * samples.size) % (2.0 * np.pi))
    shifted = samples * rotator

    mod = str(modulation or "wfm").lower().strip()
    target_bb = 220000.0 if mod == "wfm" else 48000.0
    pre_decim = max(1, int(fs // target_bb))
    if pre_decim > 1:
        usable = (shifted.size // pre_decim) * pre_decim
        if usable < pre_decim:
            return None, next_phase
        shifted = shifted[:usable].reshape(-1, pre_decim).mean(axis=1)
    fs1 = fs / pre_decim
    if shifted.size < 16:
        return None, next_phase

    if mod in ("wfm", "fm"):
        audio = np.angle(shifted[1:] * np.conj(shifted[:-1])).astype(np.float32)
    elif mod == "am":
        envelope = np.abs(shifted).astype(np.float32)
        audio = envelope - float(np.mean(envelope))
    elif mod == "usb":
        audio = np.real(shifted).astype(np.float32)
    elif mod == "lsb":
        audio = -np.real(shifted).astype(np.float32)
    else:
        audio = np.real(shifted).astype(np.float32)

    if audio.size < 8:
        return None, next_phase

    audio = audio - float(np.mean(audio))

    if mod in ("fm", "am", "usb", "lsb"):
        taps = int(max(1, min(31, fs1 / 12000.0)))
        if taps > 1:
            kernel = np.ones(taps, dtype=np.float32) / float(taps)
            audio = np.convolve(audio, kernel, mode="same")

    out_len = int(audio.size * AUDIO_SAMPLE_RATE / fs1)
    if out_len < 32:
        return None, next_phase
    x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False, dtype=np.float32)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False, dtype=np.float32)
    audio = np.interp(x_new, x_old, audio).astype(np.float32)

    rms = float(np.sqrt(np.mean(audio * audio) + 1e-12))
    level = min(100.0, rms * 450.0)
    if squelch > 0 and level < float(squelch):
        audio.fill(0.0)

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio * min(20.0, 0.85 / peak)

    pcm = np.clip(audio, -1.0, 1.0)
    return (pcm * 32767.0).astype(np.int16).tobytes(), next_phase


def _parse_center_freq_mhz(payload: dict[str, Any]) -> float:
    """Parse center frequency from mixed legacy/new payload formats."""
    if payload.get("center_freq_mhz") is not None:
        return float(payload["center_freq_mhz"])

    if payload.get("center_freq_hz") is not None:
        return float(payload["center_freq_hz"]) / 1e6

    raw = float(payload.get("center_freq", 100.0))
    # Backward compatibility: some clients still send center_freq in Hz.
    if raw > 100000:
        return raw / 1e6
    return raw


def _parse_span_mhz(payload: dict[str, Any]) -> float:
    """Parse display span in MHz from mixed payload formats."""
    if payload.get("span_hz") is not None:
        return float(payload["span_hz"]) / 1e6
    return float(payload.get("span_mhz", 2.0))


def _pick_sample_rate(span_hz: int, caps: SDRCapabilities, sdr_type: SDRType) -> int:
    """Pick a valid hardware sample rate nearest the requested span."""
    valid_rates = sorted({int(r) for r in caps.sample_rates if int(r) > 0})
    if valid_rates:
        return min(valid_rates, key=lambda rate: abs(rate - span_hz))

    max_bw = MAX_BANDWIDTH.get(sdr_type, 2400000)
    return max(62500, min(span_hz, max_bw))


def _resolve_sdr_type(sdr_type_str: str) -> SDRType:
    """Convert client sdr_type string to SDRType enum."""
    mapping = {
        "rtlsdr": SDRType.RTL_SDR,
        "rtl_sdr": SDRType.RTL_SDR,
        "hackrf": SDRType.HACKRF,
        "limesdr": SDRType.LIME_SDR,
        "lime_sdr": SDRType.LIME_SDR,
        "airspy": SDRType.AIRSPY,
        "sdrplay": SDRType.SDRPLAY,
        "usrp": SDRType.USRP,
        "bladerf": SDRType.BLADE_RF,
        "hydrasdr": SDRType.HYDRA_SDR,
    }
    return mapping.get(sdr_type_str.lower(), SDRType.RTL_SDR)


def _build_dummy_device(device_index: int, sdr_type: SDRType) -> SDRDevice:
    """Build a minimal SDRDevice for command building."""
    builder = SDRFactory.get_builder(sdr_type)
    caps = builder.get_capabilities()
    return SDRDevice(
        sdr_type=sdr_type,
        index=device_index,
        name=f"{sdr_type.value}-{device_index}",
        serial="N/A",
        driver=sdr_type.value,
        capabilities=caps,
    )


_TUNE_CMDS = ("tune", "set_vfo")


def _coalesce_commands(batch: list[dict]) -> list[dict]:
    """Of the commands waiting on the socket, the ones still worth running.

    A start restarts the SDR capture, which takes a second or more. Clicking
    along the waterfall faster than that queued a start per click, and each
    was run in turn, so the radio visited every frequency on the way to the
    last. Only the latest intent matters: a start supersedes earlier starts
    and tunes, a tune supersedes earlier tunes, and a stop supersedes
    everything before it. Other commands keep their place.
    """
    kept: list[dict] = []
    for command in batch:
        cmd = command.get("cmd")
        if cmd == "stop":
            kept = [c for c in kept if c.get("cmd") not in ("start", "stop", *_TUNE_CMDS)]
        elif cmd == "start":
            kept = [c for c in kept if c.get("cmd") not in ("start", *_TUNE_CMDS)]
        elif cmd in _TUNE_CMDS:
            kept = [c for c in kept if c.get("cmd") not in _TUNE_CMDS]
        kept.append(command)
    return kept


def _drain_waiting(ws, limit: int = 64) -> list[dict]:
    """Commands already waiting on the socket, without blocking."""
    waiting = []
    for _ in range(limit):
        try:
            msg = ws.receive(timeout=0.001)
        except Exception:
            break
        if msg is None:
            break
        with suppress(json.JSONDecodeError, TypeError):
            data = json.loads(msg)
            if isinstance(data, dict):
                waiting.append(data)
    return waiting


def init_waterfall_websocket(app: Flask):
    """Initialize WebSocket waterfall streaming."""
    if not WEBSOCKET_AVAILABLE:
        logger.warning("flask-sock not installed, WebSocket waterfall disabled")
        return

    sock = Sock(app)

    @sock.route("/ws/waterfall")
    def waterfall_stream(ws):
        """WebSocket endpoint for real-time waterfall streaming."""
        logger.info("WebSocket waterfall client connected")

        # Import app module for device claiming
        import app as app_module

        iq_process = None
        reader_thread = None
        stop_event = threading.Event()
        claimed_device = None
        claimed_sdr_type = "rtlsdr"
        my_generation = None  # tracks which capture generation this handler owns
        capture_center_mhz = 0.0
        capture_start_freq = 0.0
        capture_end_freq = 0.0
        # Queue for outgoing messages — only the main loop touches ws.send()
        send_queue = queue.Queue(maxsize=120)
        # Commands received but not yet run, already coalesced
        pending: deque[dict] = deque()

        try:
            while True:
                # Drain send queue first (non-blocking)
                while True:
                    try:
                        outgoing = send_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        ws.send(outgoing)
                    except Exception:
                        stop_event.set()
                        break

                if pending:
                    # Clicks that arrived during the last restart are folded in too
                    waiting = _drain_waiting(ws)
                    if waiting:
                        pending = deque(_coalesce_commands([*pending, *waiting]))
                    data = pending.popleft()
                    cmd = data.get("cmd")
                    msg = None
                else:
                    cmd = None
                try:
                    msg = None if cmd else ws.receive(timeout=0.01)
                except Exception as e:
                    err = str(e).lower()
                    if "closed" in err:
                        break
                    if "timed out" not in err:
                        logger.error(f"WebSocket receive error: {e}")
                    continue

                if cmd is None:
                    if msg is None:
                        # simple-websocket returns None on timeout AND on
                        # close; check ws.connected to tell them apart.
                        if not ws.connected:
                            break
                        if stop_event.is_set():
                            break
                        continue

                    try:
                        data = json.loads(msg)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(data, dict):
                        continue

                    # Anything else already sent (a burst of clicks) is folded in,
                    # so the capture goes straight to the latest request.
                    pending.extend(_coalesce_commands([data, *_drain_waiting(ws)]))
                    data = pending.popleft()
                    cmd = data.get("cmd")

                if cmd == "start":
                    shared_before = get_shared_capture_status()
                    keep_monitor_enabled = bool(shared_before.get("monitor_enabled"))
                    keep_monitor_modulation = str(shared_before.get("monitor_modulation", "wfm"))
                    keep_monitor_squelch = int(shared_before.get("monitor_squelch", 0) or 0)
                    # Stop any existing capture
                    was_restarting = iq_process is not None
                    stop_event.set()
                    if reader_thread and reader_thread.is_alive():
                        reader_thread.join(timeout=2)
                    if iq_process:
                        safe_terminate(iq_process)
                        unregister_process(iq_process)
                        iq_process = None
                    if claimed_device is not None:
                        app_module.release_sdr_device(claimed_device, claimed_sdr_type)
                        claimed_device = None
                        claimed_sdr_type = "rtlsdr"
                    _set_shared_capture_state(running=False, generation=my_generation)
                    my_generation = None
                    stop_event.clear()
                    # Flush stale frames from previous capture
                    while not send_queue.empty():
                        try:
                            send_queue.get_nowait()
                        except queue.Empty:
                            break
                    # Allow USB device to be released by the kernel
                    if was_restarting:
                        time.sleep(0.5)

                    data = apply_device_defaults(data)

                    # Parse config
                    try:
                        center_freq_mhz = _parse_center_freq_mhz(data)
                        requested_vfo_mhz = float(
                            data.get(
                                "vfo_freq_mhz",
                                data.get("frequency_mhz", center_freq_mhz),
                            )
                        )
                        span_mhz = _parse_span_mhz(data)
                        gain_raw = data.get("gain")
                        if gain_raw is None or str(gain_raw).lower() == "auto":
                            gain = None
                        else:
                            gain = float(gain_raw)
                        device_index = int(data.get("device", 0))
                        sdr_type_str = data.get("sdr_type", "rtlsdr")
                        fft_size = int(data.get("fft_size", 1024))
                        fps = int(data.get("fps", 25))
                        avg_count = int(data.get("avg_count", 4))
                        ppm = data.get("ppm")
                        if ppm is not None:
                            ppm = validate_ppm(ppm)
                        bias_t = bool(data.get("bias_t", False))
                        db_min = data.get("db_min")
                        db_max = data.get("db_max")
                        if db_min is not None:
                            db_min = float(db_min)
                        if db_max is not None:
                            db_max = float(db_max)
                    except (TypeError, ValueError) as exc:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Invalid waterfall configuration: {exc}",
                                }
                            )
                        )
                        continue

                    # Clamp and normalize runtime settings
                    fft_size = max(256, min(8192, fft_size))
                    fps = max(2, min(60, fps))
                    avg_count = max(1, min(32, avg_count))
                    if center_freq_mhz <= 0 or span_mhz <= 0:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": "center_freq_mhz and span_mhz must be > 0",
                                }
                            )
                        )
                        continue

                    # Resolve SDR type and choose a valid sample rate
                    sdr_type = _resolve_sdr_type(sdr_type_str)
                    builder = SDRFactory.get_builder(sdr_type)
                    caps = builder.get_capabilities()
                    requested_span_hz = max(1000, int(span_mhz * 1e6))
                    sample_rate = _pick_sample_rate(requested_span_hz, caps, sdr_type)

                    # Compute effective frequency range
                    effective_span_mhz = sample_rate / 1e6
                    start_freq = center_freq_mhz - effective_span_mhz / 2
                    end_freq = center_freq_mhz + effective_span_mhz / 2
                    target_vfo_mhz = requested_vfo_mhz
                    if not (start_freq <= target_vfo_mhz <= end_freq):
                        target_vfo_mhz = center_freq_mhz

                    # Claim the device (retry when restarting to allow
                    # the kernel time to release the USB handle).
                    max_claim_attempts = 4 if was_restarting else 1
                    claim_err = None
                    for _claim_attempt in range(max_claim_attempts):
                        claim_err = app_module.claim_sdr_device(device_index, "waterfall", sdr_type_str)
                        if not claim_err:
                            break
                        if _claim_attempt < max_claim_attempts - 1:
                            time.sleep(0.4)
                    if claim_err:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": claim_err,
                                    "error_type": "DEVICE_BUSY",
                                }
                            )
                        )
                        continue
                    claimed_device = device_index
                    claimed_sdr_type = sdr_type_str

                    # Build I/Q capture command
                    try:
                        device = _build_dummy_device(device_index, sdr_type)
                        iq_cmd = builder.build_iq_capture_command(
                            device=device,
                            frequency_mhz=center_freq_mhz,
                            sample_rate=sample_rate,
                            gain=gain,
                            ppm=ppm,
                            bias_t=bias_t,
                        )
                    except NotImplementedError as e:
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        claimed_sdr_type = "rtlsdr"
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": str(e),
                                }
                            )
                        )
                        continue

                    # Pre-flight: check the capture binary exists
                    if not shutil.which(iq_cmd[0]):
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        claimed_sdr_type = "rtlsdr"
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f'Required tool "{iq_cmd[0]}" not found. Install SoapySDR tools (rx_sdr).',
                                }
                            )
                        )
                        continue

                    # Spawn I/Q capture process (retry to handle USB release lag)
                    max_attempts = 3 if was_restarting else 1
                    try:
                        for attempt in range(max_attempts):
                            logger.info(
                                f"Starting I/Q capture: {center_freq_mhz:.6f} MHz, "
                                f"span={effective_span_mhz:.1f} MHz, "
                                f"sr={sample_rate}, fft={fft_size}"
                            )
                            iq_process = subprocess.Popen(
                                iq_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                bufsize=0,
                            )
                            register_process(iq_process)

                            # Brief check that process started
                            time.sleep(0.3)
                            if iq_process.poll() is not None:
                                stderr_out = ""
                                if iq_process.stderr:
                                    with suppress(Exception):
                                        stderr_out = iq_process.stderr.read().decode("utf-8", errors="replace").strip()
                                unregister_process(iq_process)
                                iq_process = None
                                if attempt < max_attempts - 1:
                                    logger.info(
                                        f"I/Q process exited immediately, "
                                        f"retrying ({attempt + 1}/{max_attempts})..."
                                        + (f" stderr: {stderr_out}" if stderr_out else "")
                                    )
                                    time.sleep(0.5)
                                    continue
                                detail = f": {stderr_out}" if stderr_out else ""
                                raise RuntimeError(f"I/Q capture process exited immediately{detail}")
                            break  # Process started successfully
                    except Exception as e:
                        logger.error(f"Failed to start I/Q capture: {e}")
                        if iq_process:
                            safe_terminate(iq_process)
                            unregister_process(iq_process)
                            iq_process = None
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        claimed_sdr_type = "rtlsdr"
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Failed to start I/Q capture: {e}",
                                }
                            )
                        )
                        continue

                    capture_center_mhz = center_freq_mhz
                    capture_start_freq = start_freq
                    capture_end_freq = end_freq

                    my_generation = _set_shared_capture_state(
                        running=True,
                        device=device_index,
                        center_mhz=center_freq_mhz,
                        span_mhz=effective_span_mhz,
                        sample_rate=sample_rate,
                    )
                    _set_shared_monitor(
                        enabled=keep_monitor_enabled,
                        frequency_mhz=target_vfo_mhz,
                        modulation=keep_monitor_modulation,
                        squelch=keep_monitor_squelch,
                    )

                    # Send started confirmation
                    ws.send(
                        json.dumps(
                            {
                                "status": "started",
                                "center_mhz": center_freq_mhz,
                                "start_freq": start_freq,
                                "end_freq": end_freq,
                                "fft_size": fft_size,
                                "sample_rate": sample_rate,
                                "effective_span_mhz": effective_span_mhz,
                                "db_min": db_min,
                                "db_max": db_max,
                                "vfo_freq_mhz": target_vfo_mhz,
                            }
                        )
                    )

                    # Start reader thread — puts frames on queue, never calls ws.send()
                    def fft_reader(
                        proc,
                        _send_q,
                        stop_evt,
                        _fft_size,
                        _avg_count,
                        _fps,
                        _sample_rate,
                        _start_freq,
                        _end_freq,
                        _center_mhz,
                        _db_min=None,
                        _db_max=None,
                    ):
                        """Read I/Q from subprocess, compute FFT, enqueue binary frames."""
                        required_fft_samples = _fft_size * _avg_count
                        timeslice_samples = max(required_fft_samples, int(_sample_rate / max(1, _fps)))
                        bytes_per_frame = timeslice_samples * 2
                        frame_interval = 1.0 / _fps
                        monitor_rotator_phase = 0.0
                        last_monitor_offset_hz = None

                        try:
                            while not stop_evt.is_set():
                                if proc.poll() is not None:
                                    break

                                frame_start = time.monotonic()

                                # Read raw I/Q bytes
                                raw = b""
                                remaining = bytes_per_frame
                                while remaining > 0 and not stop_evt.is_set():
                                    chunk = proc.stdout.read(min(remaining, 65536))
                                    if not chunk:
                                        break
                                    raw += chunk
                                    remaining -= len(chunk)

                                if len(raw) < _fft_size * 2:
                                    break

                                # Process FFT pipeline
                                samples = cu8_to_complex(raw)
                                fft_samples = (
                                    samples[-required_fft_samples:] if len(samples) > required_fft_samples else samples
                                )
                                power_db = compute_power_spectrum(
                                    fft_samples,
                                    fft_size=_fft_size,
                                    avg_count=_avg_count,
                                )
                                quantized = quantize_to_uint8(
                                    power_db,
                                    db_min=_db_min,
                                    db_max=_db_max,
                                )
                                frame = build_binary_frame(
                                    _start_freq,
                                    _end_freq,
                                    quantized,
                                )

                                # Drop frame if main loop cannot keep up.
                                with suppress(queue.Full):
                                    _send_q.put_nowait(frame)

                                monitor_cfg = _snapshot_monitor_config()
                                if monitor_cfg:
                                    center_mhz_cfg = float(monitor_cfg.get("center_mhz", _center_mhz))
                                    monitor_mhz_cfg = float(monitor_cfg.get("monitor_freq_mhz", _center_mhz))
                                    offset_hz = (monitor_mhz_cfg - center_mhz_cfg) * 1e6
                                    if last_monitor_offset_hz is None or abs(offset_hz - last_monitor_offset_hz) > 1.0:
                                        monitor_rotator_phase = 0.0
                                        last_monitor_offset_hz = offset_hz

                                    audio_chunk, monitor_rotator_phase = _demodulate_monitor_audio(
                                        samples=samples,
                                        sample_rate=_sample_rate,
                                        center_mhz=center_mhz_cfg,
                                        monitor_freq_mhz=monitor_mhz_cfg,
                                        modulation=monitor_cfg.get("modulation", "wfm"),
                                        squelch=int(monitor_cfg.get("squelch", 0)),
                                        rotator_phase=monitor_rotator_phase,
                                    )
                                    if audio_chunk:
                                        _push_shared_audio_chunk(audio_chunk)
                                else:
                                    monitor_rotator_phase = 0.0
                                    last_monitor_offset_hz = None

                                # Pace to target FPS
                                elapsed = time.monotonic() - frame_start
                                sleep_time = frame_interval - elapsed
                                if sleep_time > 0:
                                    stop_evt.wait(sleep_time)

                        except Exception as e:
                            logger.debug(f"FFT reader stopped: {e}")

                    reader_thread = threading.Thread(
                        target=fft_reader,
                        args=(
                            iq_process,
                            send_queue,
                            stop_event,
                            fft_size,
                            avg_count,
                            fps,
                            sample_rate,
                            start_freq,
                            end_freq,
                            center_freq_mhz,
                            db_min,
                            db_max,
                        ),
                        daemon=True,
                    )
                    reader_thread.start()

                elif cmd in ("tune", "set_vfo"):
                    if not iq_process or claimed_device is None or iq_process.poll() is not None:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": "Waterfall capture is not running",
                                }
                            )
                        )
                        continue
                    try:
                        shared = get_shared_capture_status()
                        vfo_freq_mhz = float(
                            data.get(
                                "vfo_freq_mhz",
                                data.get("frequency_mhz", data.get("center_freq_mhz", capture_center_mhz)),
                            )
                        )
                        squelch = int(data.get("squelch", shared.get("monitor_squelch", 0)))
                        modulation = str(data.get("modulation", shared.get("monitor_modulation", "wfm")))
                    except (TypeError, ValueError) as exc:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Invalid tune request: {exc}",
                                }
                            )
                        )
                        continue

                    if not (capture_start_freq <= vfo_freq_mhz <= capture_end_freq):
                        ws.send(
                            json.dumps(
                                {
                                    "status": "retune_required",
                                    "message": "Frequency outside current capture span",
                                    "capture_start_freq": capture_start_freq,
                                    "capture_end_freq": capture_end_freq,
                                    "vfo_freq_mhz": vfo_freq_mhz,
                                }
                            )
                        )
                        continue

                    monitor_enabled = bool(shared.get("monitor_enabled"))
                    _set_shared_monitor(
                        enabled=monitor_enabled,
                        frequency_mhz=vfo_freq_mhz,
                        modulation=modulation,
                        squelch=squelch,
                    )
                    ws.send(
                        json.dumps(
                            {
                                "status": "tuned",
                                "vfo_freq_mhz": vfo_freq_mhz,
                                "start_freq": capture_start_freq,
                                "end_freq": capture_end_freq,
                                "center_mhz": capture_center_mhz,
                            }
                        )
                    )

                elif cmd == "stop":
                    stop_event.set()
                    if reader_thread and reader_thread.is_alive():
                        reader_thread.join(timeout=2)
                        reader_thread = None
                    if iq_process:
                        safe_terminate(iq_process)
                        unregister_process(iq_process)
                        iq_process = None
                    if claimed_device is not None:
                        app_module.release_sdr_device(claimed_device, claimed_sdr_type)
                        claimed_device = None
                        claimed_sdr_type = "rtlsdr"
                    _set_shared_capture_state(running=False, generation=my_generation)
                    my_generation = None
                    stop_event.clear()
                    ws.send(json.dumps({"status": "stopped"}))

        except Exception as e:
            logger.info(f"WebSocket waterfall closed: {e}")
        finally:
            # Cleanup — use generation guard so a stale handler cannot
            # clobber shared state owned by a newer WS connection.
            stop_event.set()
            if reader_thread and reader_thread.is_alive():
                reader_thread.join(timeout=2)
            if iq_process:
                safe_terminate(iq_process)
                unregister_process(iq_process)
            if claimed_device is not None:
                app_module.release_sdr_device(claimed_device, claimed_sdr_type)
            _set_shared_capture_state(running=False, generation=my_generation)
            # Complete WebSocket close handshake, then shut down the
            # raw socket so Werkzeug cannot write its HTTP 200 response
            # on top of the WebSocket stream (which browsers see as
            # "Invalid frame header").
            with suppress(Exception):
                ws.close()
            with suppress(Exception):
                ws.sock.shutdown(socket.SHUT_RDWR)
            with suppress(Exception):
                ws.sock.close()
            logger.info("WebSocket waterfall client disconnected")
