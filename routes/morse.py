"""CW/Morse code decoder routes."""

from __future__ import annotations

import contextlib
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.event_pipeline import process_event
from utils.logging import sensor_logger as logger
from utils.morse import (
    decode_morse_wav_file,
    morse_decoder_thread,
)
from utils.process import register_process, safe_terminate, unregister_process
from utils.responses import api_error
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_frequency,
    validate_gain,
    validate_ppm,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
)

morse_bp = Blueprint("morse", __name__)


class _FilteredQueue:
    """Suppress decoder-thread 'stopped' events that race with route lifecycle."""

    def __init__(self, inner: queue.Queue) -> None:
        self._inner = inner

    def put_nowait(self, item: Any) -> None:
        if isinstance(item, dict) and item.get("type") == "status" and item.get("status") == "stopped":
            return
        self._inner.put_nowait(item)

    def put(self, item: Any, **kwargs: Any) -> None:
        if isinstance(item, dict) and item.get("type") == "status" and item.get("status") == "stopped":
            return
        self._inner.put(item, **kwargs)


# Track which device is being used
morse_active_device: int | None = None
morse_active_sdr_type: str | None = None

# Runtime lifecycle state.
MORSE_IDLE = "idle"
MORSE_STARTING = "starting"
MORSE_RUNNING = "running"
MORSE_STOPPING = "stopping"
MORSE_ERROR = "error"

morse_state = MORSE_IDLE
morse_state_message = "Idle"
morse_state_since = time.monotonic()
morse_last_error = ""
morse_runtime_config: dict[str, Any] = {}
morse_session_id = 0

morse_decoder_worker: threading.Thread | None = None
morse_stderr_worker: threading.Thread | None = None
morse_stop_event: threading.Event | None = None
morse_control_queue: queue.Queue | None = None


def _set_state(state: str, message: str = "", *, enqueue: bool = True, extra: dict[str, Any] | None = None) -> None:
    """Update lifecycle state and optionally emit a status queue event."""
    global morse_state, morse_state_message, morse_state_since
    morse_state = state
    morse_state_message = message or state
    morse_state_since = time.monotonic()

    if not enqueue:
        return

    payload: dict[str, Any] = {
        "type": "status",
        "status": state,
        "state": state,
        "message": morse_state_message,
        "session_id": morse_session_id,
        "timestamp": time.strftime("%H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(payload)


def _drain_queue(q: queue.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _join_thread(worker: threading.Thread | None, timeout_s: float) -> bool:
    if worker is None:
        return True
    worker.join(timeout=timeout_s)
    return not worker.is_alive()


def _close_pipe(pipe_obj: Any) -> None:
    if pipe_obj is None:
        return
    with contextlib.suppress(Exception):
        pipe_obj.close()


def _queue_morse_event(payload: dict[str, Any]) -> None:
    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(payload)


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _validate_tone_freq(value: Any) -> float:
    """Validate CW tone frequency (300-1200 Hz)."""
    try:
        freq = float(value)
        if not 300 <= freq <= 1200:
            raise ValueError("Tone frequency must be between 300 and 1200 Hz")
        return freq
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid tone frequency: {value}") from e


def _validate_wpm(value: Any) -> int:
    """Validate words per minute (5-50)."""
    try:
        wpm = int(value)
        if not 5 <= wpm <= 50:
            raise ValueError("WPM must be between 5 and 50")
        return wpm
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid WPM: {value}") from e


def _validate_bandwidth(value: Any) -> int:
    try:
        bw = int(value)
        if bw not in (50, 100, 200, 400):
            raise ValueError("Bandwidth must be one of 50, 100, 200, 400 Hz")
        return bw
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid bandwidth: {value}") from e


def _validate_threshold_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise ValueError("threshold_mode must be auto or manual")
    return mode


def _validate_wpm_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise ValueError("wpm_mode must be auto or manual")
    return mode


def _validate_threshold_multiplier(value: Any) -> float:
    try:
        multiplier = float(value)
        if not 1.1 <= multiplier <= 8.0:
            raise ValueError("threshold_multiplier must be between 1.1 and 8.0")
        return multiplier
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid threshold multiplier: {value}") from e


def _validate_non_negative_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f"{field_name} must be non-negative")
        return parsed
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid {field_name}: {value}") from e


def _validate_signal_gate(value: Any) -> float:
    try:
        gate = float(value)
        if not 0.0 <= gate <= 1.0:
            raise ValueError("signal_gate must be between 0.0 and 1.0")
        return gate
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid signal gate: {value}") from e


def _validate_detect_mode(value: Any) -> str:
    """Validate detection mode ('goertzel' or 'envelope')."""
    mode = str(value or "goertzel").lower().strip()
    if mode not in ("goertzel", "envelope"):
        raise ValueError("detect_mode must be 'goertzel' or 'envelope'")
    return mode


def _snapshot_live_resources() -> list[str]:
    alive: list[str] = []
    if morse_decoder_worker and morse_decoder_worker.is_alive():
        alive.append("decoder_thread")
    if morse_stderr_worker and morse_stderr_worker.is_alive():
        alive.append("stderr_thread")
    if app_module.morse_process and app_module.morse_process.poll() is None:
        alive.append("rtl_process")
    return alive


@morse_bp.route("/morse/start", methods=["POST"])
def start_morse() -> Response:
    global morse_active_device, morse_active_sdr_type, morse_decoder_worker, morse_stderr_worker
    global morse_stop_event, morse_control_queue, morse_runtime_config
    global morse_last_error, morse_session_id

    data = request.json or {}
    data = apply_device_defaults(data)

    # Validate detect_mode first — it determines frequency limits.
    try:
        detect_mode = _validate_detect_mode(data.get("detect_mode", "goertzel"))
    except ValueError as e:
        return api_error(str(e), 400)

    freq_max = 1766.0 if detect_mode == "envelope" else 30.0
    try:
        freq = validate_frequency(data.get("frequency", "14.060"), min_mhz=0.5, max_mhz=freq_max)
        gain = validate_gain(data.get("gain", "0"))
        ppm = validate_ppm(data.get("ppm", "0"))
        device = validate_device_index(data.get("device", "0"))
    except ValueError as e:
        return api_error(str(e), 400)

    try:
        tone_freq = _validate_tone_freq(data.get("tone_freq", "700"))
        wpm = _validate_wpm(data.get("wpm", "15"))
        bandwidth_hz = _validate_bandwidth(data.get("bandwidth_hz", "200"))
        threshold_mode = _validate_threshold_mode(data.get("threshold_mode", "auto"))
        wpm_mode = _validate_wpm_mode(data.get("wpm_mode", "auto"))
        threshold_multiplier = _validate_threshold_multiplier(data.get("threshold_multiplier", "2.8"))
        manual_threshold = _validate_non_negative_float(data.get("manual_threshold", "0"), "manual threshold")
        threshold_offset = _validate_non_negative_float(data.get("threshold_offset", "0"), "threshold offset")
        min_signal_gate = _validate_signal_gate(data.get("signal_gate", "0"))
        auto_tone_track = _bool_value(data.get("auto_tone_track", True), True)
        tone_lock = _bool_value(data.get("tone_lock", False), False)
        wpm_lock = _bool_value(data.get("wpm_lock", False), False)
    except ValueError as e:
        return api_error(str(e), 400)

    sdr_type_str = data.get("sdr_type", "rtlsdr")

    # Check for rtl_tcp (remote SDR) connection
    rtl_tcp_host = data.get("rtl_tcp_host")
    rtl_tcp_port = data.get("rtl_tcp_port", 1234)

    with app_module.morse_lock:
        if morse_state in {MORSE_STARTING, MORSE_RUNNING, MORSE_STOPPING}:
            return jsonify(
                {
                    "status": "error",
                    "message": f"Morse decoder is {morse_state}",
                    "state": morse_state,
                }
            ), 409

        # Reserve SDR device (skip for remote rtl_tcp)
        if not rtl_tcp_host:
            device_int = int(device)
            error = app_module.claim_sdr_device(device_int, "morse", sdr_type_str)
            if error:
                return jsonify(
                    {
                        "status": "error",
                        "error_type": "DEVICE_BUSY",
                        "message": error,
                    }
                ), 409

            morse_active_device = device_int
            morse_active_sdr_type = sdr_type_str
        morse_last_error = ""
        morse_session_id += 1

        _drain_queue(app_module.morse_queue)
        _set_state(MORSE_STARTING, "Starting decoder...")

    # Envelope mode (OOK/AM): use AM demod, higher sample rate for better
    # envelope resolution.  Goertzel mode (HF CW): use USB demod.
    if detect_mode == "envelope":
        sample_rate = 48000
        modulation = "am"
    else:
        sample_rate = 22050
        modulation = "usb"

    bias_t = _bool_value(data.get("bias_t", False), False)

    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    # Create network or local SDR device
    network_sdr_device = None
    if rtl_tcp_host:
        try:
            rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
            rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
        except ValueError as e:
            return api_error(str(e), 400)
        network_sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
        logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")

    requested_device_index = int(device)
    active_device_index = requested_device_index
    builder = SDRFactory.get_builder(network_sdr_device.sdr_type if network_sdr_device else sdr_type)

    device_catalog: dict[int, dict[str, str]] = {}
    candidate_device_indices: list[int] = [requested_device_index]
    if not network_sdr_device:
        with contextlib.suppress(Exception):
            detected_devices = SDRFactory.detect_devices()
            same_type_devices = [d for d in detected_devices if d.sdr_type == sdr_type]
            for d in same_type_devices:
                device_catalog[d.index] = {
                    "name": str(d.name or f"SDR {d.index}"),
                    "serial": str(d.serial or "Unknown"),
                }
            for d in sorted(same_type_devices, key=lambda dev: dev.index):
                if d.index not in candidate_device_indices:
                    candidate_device_indices.append(d.index)

    def _device_label(device_index: int) -> str:
        meta = device_catalog.get(device_index, {})
        serial = str(meta.get("serial") or "Unknown")
        name = str(meta.get("name") or f"SDR {device_index}")
        return f"device {device_index} ({name}, SN: {serial})"

    def _build_rtl_cmd(device_index: int, direct_sampling_mode: int | None) -> list[str]:
        # Envelope mode tunes directly to center freq (no tone offset).
        if detect_mode == "envelope":
            tuned_frequency_mhz = max(0.5, float(freq))
        else:
            tuned_frequency_mhz = max(0.5, float(freq) - (float(tone_freq) / 1_000_000.0))
        sdr_device = network_sdr_device or SDRFactory.create_default_device(sdr_type, index=device_index)
        fm_kwargs: dict[str, Any] = {
            "device": sdr_device,
            "frequency_mhz": tuned_frequency_mhz,
            "sample_rate": sample_rate,
            "gain": float(gain) if gain and gain != "0" else None,
            "ppm": int(ppm) if ppm and ppm != "0" else None,
            "modulation": modulation,
            "bias_t": bias_t,
        }
        if direct_sampling_mode in (1, 2):
            fm_kwargs["direct_sampling"] = int(direct_sampling_mode)

        cmd = list(builder.build_fm_demod_command(**fm_kwargs))

        if cmd and cmd[-1] != "-":
            cmd.append("-")
        return cmd

    can_try_direct_sampling = bool(
        sdr_type == SDRType.RTL_SDR
        and detect_mode != "envelope"  # direct sampling is HF-only
        and float(freq) < 24.0
    )
    direct_sampling_attempts: list[int | None] = [2, 1, None] if can_try_direct_sampling else [None]

    runtime_config: dict[str, Any] = {
        "sample_rate": sample_rate,
        "detect_mode": detect_mode,
        "modulation": modulation,
        "rf_frequency_mhz": float(freq),
        "tuned_frequency_mhz": max(0.5, float(freq))
        if detect_mode == "envelope"
        else max(0.5, float(freq) - (float(tone_freq) / 1_000_000.0)),
        "tone_freq": tone_freq,
        "wpm": wpm,
        "bandwidth_hz": bandwidth_hz,
        "auto_tone_track": auto_tone_track,
        "tone_lock": tone_lock,
        "threshold_mode": threshold_mode,
        "manual_threshold": manual_threshold,
        "threshold_multiplier": threshold_multiplier,
        "threshold_offset": threshold_offset,
        "wpm_mode": wpm_mode,
        "wpm_lock": wpm_lock,
        "min_signal_gate": min_signal_gate,
        "source": "rtl_fm",
        "requested_device": requested_device_index,
        "active_device": active_device_index,
        "device_serial": str(device_catalog.get(active_device_index, {}).get("serial") or "Unknown"),
        "candidate_devices": list(candidate_device_indices),
    }

    active_rtl_process: subprocess.Popen[bytes] | None = None
    active_stop_event: threading.Event | None = None
    active_control_queue: queue.Queue | None = None
    active_decoder_thread: threading.Thread | None = None
    active_stderr_thread: threading.Thread | None = None
    rtl_process: subprocess.Popen[bytes] | None = None
    stop_event: threading.Event | None = None
    control_queue: queue.Queue | None = None
    decoder_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None

    def _cleanup_attempt(
        rtl_proc: subprocess.Popen[bytes] | None,
        stop_evt: threading.Event | None,
        control_q: queue.Queue | None,
        decoder_worker: threading.Thread | None,
        stderr_worker: threading.Thread | None,
    ) -> None:
        if stop_evt is not None:
            stop_evt.set()
        if control_q is not None:
            with contextlib.suppress(queue.Full):
                control_q.put_nowait({"cmd": "shutdown"})

        if rtl_proc is not None:
            _close_pipe(getattr(rtl_proc, "stdout", None))
            _close_pipe(getattr(rtl_proc, "stderr", None))

        if rtl_proc is not None:
            safe_terminate(rtl_proc, timeout=0.4)
            unregister_process(rtl_proc)

        _join_thread(decoder_worker, timeout_s=0.35)
        _join_thread(stderr_worker, timeout_s=0.35)

    full_cmd = ""
    attempt_errors: list[str] = []

    try:
        startup_succeeded = False
        for device_pos, candidate_device_index in enumerate(candidate_device_indices, start=1):
            if candidate_device_index != active_device_index:
                prev_device = active_device_index
                claim_error = app_module.claim_sdr_device(candidate_device_index, "morse", sdr_type_str)
                if claim_error:
                    msg = f"{_device_label(candidate_device_index)} unavailable: {claim_error}"
                    attempt_errors.append(msg)
                    logger.warning("Morse startup device fallback skipped: %s", msg)
                    _queue_morse_event({"type": "info", "text": f"[morse] {msg}"})
                    continue

                if prev_device is not None:
                    app_module.release_sdr_device(prev_device, morse_active_sdr_type or "rtlsdr")
                active_device_index = candidate_device_index
                with app_module.morse_lock:
                    morse_active_device = active_device_index

                _queue_morse_event(
                    {
                        "type": "info",
                        "text": (
                            f"[morse] switching to {_device_label(active_device_index)} "
                            f"({device_pos}/{len(candidate_device_indices)})"
                        ),
                    }
                )

            runtime_config["active_device"] = active_device_index
            runtime_config["device_serial"] = str(
                device_catalog.get(active_device_index, {}).get("serial") or "Unknown"
            )
            runtime_config.pop("startup_waiting", None)
            runtime_config.pop("startup_warning", None)

            for attempt_index, direct_sampling_mode in enumerate(direct_sampling_attempts, start=1):
                rtl_process = None
                stop_event = None
                control_queue = None
                decoder_thread = None
                stderr_thread = None

                rtl_cmd = _build_rtl_cmd(active_device_index, direct_sampling_mode)
                direct_mode_label = direct_sampling_mode if direct_sampling_mode is not None else "none"
                full_cmd = " ".join(rtl_cmd)
                logger.info(
                    "Morse decoder attempt device=%s (%s/%s) rf=%.6f tuned=%.6f direct_mode=%s (%s/%s): %s",
                    active_device_index,
                    device_pos,
                    len(candidate_device_indices),
                    float(freq),
                    float(runtime_config.get("tuned_frequency_mhz", freq)),
                    direct_mode_label,
                    attempt_index,
                    len(direct_sampling_attempts),
                    full_cmd,
                )
                _queue_morse_event({"type": "info", "text": f"[cmd] {full_cmd}"})

                rtl_process = subprocess.Popen(
                    rtl_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                register_process(rtl_process)

                stop_event = threading.Event()
                control_queue = queue.Queue(maxsize=16)
                pcm_ready_event = threading.Event()
                stderr_lines: list[str] = []

                def monitor_stderr(
                    proc: subprocess.Popen[bytes] = rtl_process,
                    proc_stop_event: threading.Event = stop_event,
                    capture_lines: list[str] = stderr_lines,
                ) -> None:
                    stderr_stream = proc.stderr
                    if stderr_stream is None:
                        return
                    try:
                        while not proc_stop_event.is_set():
                            line = stderr_stream.readline()
                            if not line:
                                if proc.poll() is not None:
                                    break
                                time.sleep(0.02)
                                continue
                            err_text = line.decode("utf-8", errors="replace").strip()
                            if not err_text:
                                continue
                            if len(capture_lines) >= 40:
                                del capture_lines[:10]
                            capture_lines.append(err_text)
                            _queue_morse_event({"type": "info", "text": f"[rtl_fm] {err_text}"})
                    except (ValueError, OSError):
                        return
                    except Exception:
                        return

                stderr_thread = threading.Thread(target=monitor_stderr, daemon=True, name="morse-stderr")
                stderr_thread.start()

                if rtl_process.stdout is None:
                    raise RuntimeError("rtl_fm stdout unavailable")

                decoder_thread = threading.Thread(
                    target=morse_decoder_thread,
                    kwargs={
                        "rtl_stdout": rtl_process.stdout,
                        "output_queue": _FilteredQueue(app_module.morse_queue),
                        "stop_event": stop_event,
                        "sample_rate": sample_rate,
                        "tone_freq": tone_freq,
                        "wpm": wpm,
                        "decoder_config": runtime_config,
                        "control_queue": control_queue,
                        "pcm_ready_event": pcm_ready_event,
                    },
                    daemon=True,
                    name="morse-decoder",
                )
                decoder_thread.start()

                startup_deadline = time.monotonic() + 4.0
                startup_ok = False
                startup_error = ""

                while time.monotonic() < startup_deadline:
                    if pcm_ready_event.is_set():
                        startup_ok = True
                        break
                    if rtl_process.poll() is not None:
                        startup_error = f"rtl_fm exited during startup (code {rtl_process.returncode})"
                        break
                    time.sleep(0.05)

                if not startup_ok:
                    if not startup_error:
                        startup_error = "No PCM samples received within startup timeout"
                    if stderr_lines:
                        startup_error = f"{startup_error}; stderr: {stderr_lines[-1]}"
                    is_last_device = device_pos == len(candidate_device_indices)
                    is_last_attempt = attempt_index == len(direct_sampling_attempts)
                    if is_last_device and is_last_attempt and rtl_process.poll() is None:
                        startup_ok = True
                        runtime_config["startup_waiting"] = True
                        runtime_config["startup_warning"] = startup_error
                        logger.warning(
                            "Morse startup continuing without PCM on %s: %s",
                            _device_label(active_device_index),
                            startup_error,
                        )
                        _queue_morse_event(
                            {
                                "type": "info",
                                "text": "[morse] waiting for PCM stream...",
                            }
                        )

                if startup_ok:
                    runtime_config["direct_sampling_mode"] = direct_sampling_mode
                    runtime_config["direct_sampling"] = (
                        int(direct_sampling_mode) if direct_sampling_mode is not None else 0
                    )
                    runtime_config["command"] = full_cmd
                    runtime_config["active_device"] = active_device_index

                    active_rtl_process = rtl_process
                    active_stop_event = stop_event
                    active_control_queue = control_queue
                    active_decoder_thread = decoder_thread
                    active_stderr_thread = stderr_thread
                    startup_succeeded = True
                    break

                attempt_errors.append(
                    f"{_device_label(active_device_index)} "
                    f"attempt {attempt_index}/{len(direct_sampling_attempts)} "
                    f"(source=rtl_fm direct_mode={direct_mode_label}): {startup_error}"
                )
                logger.warning("Morse startup attempt failed: %s", attempt_errors[-1])
                _queue_morse_event({"type": "info", "text": f"[morse] startup attempt failed: {startup_error}"})

                _cleanup_attempt(
                    rtl_process,
                    stop_event,
                    control_queue,
                    decoder_thread,
                    stderr_thread,
                )
                rtl_process = None
                stop_event = None
                control_queue = None
                decoder_thread = None
                stderr_thread = None

            if startup_succeeded:
                break

            if device_pos < len(candidate_device_indices):
                next_device = candidate_device_indices[device_pos]
                _queue_morse_event(
                    {
                        "type": "status",
                        "state": MORSE_STARTING,
                        "status": MORSE_STARTING,
                        "message": (
                            f"No PCM on {_device_label(active_device_index)}. Trying {_device_label(next_device)}..."
                        ),
                        "session_id": morse_session_id,
                        "timestamp": time.strftime("%H:%M:%S"),
                    }
                )

        if (
            active_rtl_process is None
            or active_stop_event is None
            or active_control_queue is None
            or active_decoder_thread is None
            or active_stderr_thread is None
        ):
            msg = f"SDR capture started but no PCM stream was received from {_device_label(active_device_index)}."
            if attempt_errors:
                msg += " " + " | ".join(attempt_errors)
            logger.error("Morse startup failed: %s", msg)
            with app_module.morse_lock:
                if morse_active_device is not None:
                    app_module.release_sdr_device(morse_active_device, morse_active_sdr_type or "rtlsdr")
                    morse_active_device = None
                    morse_active_sdr_type = None
                morse_last_error = msg
                _set_state(MORSE_ERROR, msg)
                _set_state(MORSE_IDLE, "Idle")
            return api_error(msg, 500)

        with app_module.morse_lock:
            app_module.morse_process = active_rtl_process
            app_module.morse_process._stop_decoder = active_stop_event
            app_module.morse_process._decoder_thread = active_decoder_thread
            app_module.morse_process._stderr_thread = active_stderr_thread
            app_module.morse_process._control_queue = active_control_queue

            morse_stop_event = active_stop_event
            morse_control_queue = active_control_queue
            morse_decoder_worker = active_decoder_thread
            morse_stderr_worker = active_stderr_thread
            morse_runtime_config = dict(runtime_config)
            _set_state(MORSE_RUNNING, "Listening")

        return jsonify(
            {
                "status": "started",
                "state": MORSE_RUNNING,
                "command": full_cmd,
                "detect_mode": detect_mode,
                "modulation": modulation,
                "tone_freq": tone_freq,
                "wpm": wpm,
                "config": runtime_config,
                "session_id": morse_session_id,
            }
        )

    except FileNotFoundError as e:
        _cleanup_attempt(
            rtl_process if rtl_process is not None else active_rtl_process,
            stop_event if stop_event is not None else active_stop_event,
            control_queue if control_queue is not None else active_control_queue,
            decoder_thread if decoder_thread is not None else active_decoder_thread,
            stderr_thread if stderr_thread is not None else active_stderr_thread,
        )
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device, morse_active_sdr_type or "rtlsdr")
                morse_active_device = None
                morse_active_sdr_type = None
            morse_last_error = f"Tool not found: {e.filename}"
            _set_state(MORSE_ERROR, morse_last_error)
            _set_state(MORSE_IDLE, "Idle")
        return api_error(morse_last_error, 400)

    except Exception as e:
        _cleanup_attempt(
            rtl_process if rtl_process is not None else active_rtl_process,
            stop_event if stop_event is not None else active_stop_event,
            control_queue if control_queue is not None else active_control_queue,
            decoder_thread if decoder_thread is not None else active_decoder_thread,
            stderr_thread if stderr_thread is not None else active_stderr_thread,
        )
        with app_module.morse_lock:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device, morse_active_sdr_type or "rtlsdr")
                morse_active_device = None
                morse_active_sdr_type = None
            morse_last_error = str(e)
            _set_state(MORSE_ERROR, morse_last_error)
            _set_state(MORSE_IDLE, "Idle")
        return api_error(str(e), 500)


@morse_bp.route("/morse/stop", methods=["POST"])
def stop_morse() -> Response:
    global morse_active_device, morse_active_sdr_type, morse_decoder_worker, morse_stderr_worker
    global morse_stop_event, morse_control_queue

    stop_started = time.perf_counter()

    with app_module.morse_lock:
        if morse_state == MORSE_STOPPING:
            return jsonify({"status": "stopping", "state": MORSE_STOPPING}), 202

        rtl_proc = app_module.morse_process
        stop_event = morse_stop_event or getattr(rtl_proc, "_stop_decoder", None)
        decoder_thread = morse_decoder_worker or getattr(rtl_proc, "_decoder_thread", None)
        stderr_thread = morse_stderr_worker or getattr(rtl_proc, "_stderr_thread", None)
        control_queue = morse_control_queue or getattr(rtl_proc, "_control_queue", None)
        active_device = morse_active_device
        active_sdr_type = morse_active_sdr_type

        if not rtl_proc and not stop_event and not decoder_thread and not stderr_thread:
            _set_state(MORSE_IDLE, "Idle", enqueue=False)
            return jsonify({"status": "not_running", "state": MORSE_IDLE})

        _set_state(MORSE_STOPPING, "Stopping decoder...")

        app_module.morse_process = None
        morse_stop_event = None
        morse_control_queue = None
        morse_decoder_worker = None
        morse_stderr_worker = None

    cleanup_steps: list[str] = []

    def _mark(step: str) -> None:
        cleanup_steps.append(step)
        logger.debug(f"[morse.stop] {step}")

    _mark("enter stop")

    if stop_event is not None:
        stop_event.set()
        _mark("stop_event set")

    if control_queue is not None:
        with contextlib.suppress(queue.Full):
            control_queue.put_nowait({"cmd": "shutdown"})
        _mark("control_queue shutdown signal sent")

    if rtl_proc is not None:
        _close_pipe(getattr(rtl_proc, "stdout", None))
        _close_pipe(getattr(rtl_proc, "stderr", None))
        _mark("rtl_fm pipes closed")

    if rtl_proc is not None:
        safe_terminate(rtl_proc, timeout=0.6)
        unregister_process(rtl_proc)
        _mark("rtl_fm process terminated")

    decoder_joined = _join_thread(decoder_thread, timeout_s=0.45)
    stderr_joined = _join_thread(stderr_thread, timeout_s=0.45)
    _mark(f"decoder thread joined={decoder_joined}")
    _mark(f"stderr thread joined={stderr_joined}")

    if active_device is not None:
        app_module.release_sdr_device(active_device, active_sdr_type or "rtlsdr")
        _mark(f"SDR device {active_device} released")

    stop_ms = round((time.perf_counter() - stop_started) * 1000.0, 1)
    alive_after = []
    if not decoder_joined:
        alive_after.append("decoder_thread")
    if not stderr_joined:
        alive_after.append("stderr_thread")
    if rtl_proc is not None and rtl_proc.poll() is None:
        alive_after.append("rtl_process")

    with app_module.morse_lock:
        morse_active_device = None
        morse_active_sdr_type = None
        _set_state(
            MORSE_IDLE,
            "Stopped",
            extra={
                "stop_ms": stop_ms,
                "cleanup_steps": cleanup_steps,
                "alive": alive_after,
            },
        )

    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(
            {
                "type": "status",
                "status": "stopped",
                "state": MORSE_IDLE,
                "stop_ms": stop_ms,
                "cleanup_steps": cleanup_steps,
                "alive": alive_after,
                "timestamp": time.strftime("%H:%M:%S"),
            }
        )

    if stop_ms > 500.0 or alive_after:
        logger.warning(
            "[morse.stop] slow/partial cleanup: stop_ms=%s alive=%s steps=%s",
            stop_ms,
            ",".join(alive_after) if alive_after else "none",
            "; ".join(cleanup_steps),
        )
    else:
        logger.info("[morse.stop] cleanup complete in %sms", stop_ms)

    return jsonify(
        {
            "status": "stopped",
            "state": MORSE_IDLE,
            "stop_ms": stop_ms,
            "alive": alive_after,
            "cleanup_steps": cleanup_steps,
        }
    )


@morse_bp.route("/morse/calibrate", methods=["POST"])
def calibrate_morse() -> Response:
    """Reset decoder threshold/timing estimators without restarting the process."""
    with app_module.morse_lock:
        if morse_state != MORSE_RUNNING or morse_control_queue is None:
            return jsonify(
                {
                    "status": "not_running",
                    "state": morse_state,
                    "message": "Morse decoder is not running",
                }
            ), 409

        with contextlib.suppress(queue.Full):
            morse_control_queue.put_nowait({"cmd": "reset"})

    with contextlib.suppress(queue.Full):
        app_module.morse_queue.put_nowait(
            {
                "type": "info",
                "text": "[morse] Calibration reset requested",
            }
        )

    return jsonify({"status": "ok", "state": morse_state})


@morse_bp.route("/morse/decode-file", methods=["POST"])
def decode_morse_file() -> Response:
    """Decode Morse from an uploaded WAV file."""
    if "audio" not in request.files:
        return api_error("No audio file provided", 400)

    audio_file = request.files["audio"]
    if not audio_file.filename:
        return api_error("No file selected", 400)

    # Parse optional tuning/decoder parameters from form fields.
    form = request.form or {}
    try:
        tone_freq = _validate_tone_freq(form.get("tone_freq", "700"))
        wpm = _validate_wpm(form.get("wpm", "15"))
        bandwidth_hz = _validate_bandwidth(form.get("bandwidth_hz", "200"))
        threshold_mode = _validate_threshold_mode(form.get("threshold_mode", "auto"))
        wpm_mode = _validate_wpm_mode(form.get("wpm_mode", "auto"))
        threshold_multiplier = _validate_threshold_multiplier(form.get("threshold_multiplier", "2.8"))
        manual_threshold = _validate_non_negative_float(form.get("manual_threshold", "0"), "manual threshold")
        threshold_offset = _validate_non_negative_float(form.get("threshold_offset", "0"), "threshold offset")
        signal_gate = _validate_signal_gate(form.get("signal_gate", "0"))
        auto_tone_track = _bool_value(form.get("auto_tone_track", "true"), True)
        tone_lock = _bool_value(form.get("tone_lock", "false"), False)
        wpm_lock = _bool_value(form.get("wpm_lock", "false"), False)
    except ValueError as e:
        return api_error(str(e), 400)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        result = decode_morse_wav_file(
            tmp_path,
            sample_rate=8000,
            tone_freq=tone_freq,
            wpm=wpm,
            bandwidth_hz=bandwidth_hz,
            auto_tone_track=auto_tone_track,
            tone_lock=tone_lock,
            threshold_mode=threshold_mode,
            manual_threshold=manual_threshold,
            threshold_multiplier=threshold_multiplier,
            threshold_offset=threshold_offset,
            wpm_mode=wpm_mode,
            wpm_lock=wpm_lock,
            min_signal_gate=signal_gate,
        )

        text = str(result.get("text", ""))
        raw = str(result.get("raw", ""))
        metrics = result.get("metrics", {})

        return jsonify(
            {
                "status": "ok",
                "text": text,
                "raw": raw,
                "char_count": len(text.replace(" ", "")),
                "word_count": len([w for w in text.split(" ") if w]),
                "metrics": metrics,
            }
        )
    except Exception as e:
        logger.error(f"Morse decode-file error: {e}")
        return api_error(str(e), 500)
    finally:
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)


@morse_bp.route("/morse/status")
def morse_status() -> Response:
    with app_module.morse_lock:
        running = (
            app_module.morse_process is not None
            and app_module.morse_process.poll() is None
            and morse_state in {MORSE_RUNNING, MORSE_STARTING, MORSE_STOPPING}
        )
        since_ms = round((time.monotonic() - morse_state_since) * 1000.0, 1)
        return jsonify(
            {
                "running": running,
                "state": morse_state,
                "message": morse_state_message,
                "since_ms": since_ms,
                "session_id": morse_session_id,
                "config": morse_runtime_config,
                "alive": _snapshot_live_resources(),
                "error": morse_last_error,
            }
        )


@morse_bp.route("/morse/stream")
def morse_stream() -> Response:
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("morse", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.morse_queue,
            channel_key="morse",
            timeout=1.0,
            keepalive_interval=30.0,
            on_message=_on_msg,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
