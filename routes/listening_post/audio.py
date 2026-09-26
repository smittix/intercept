"""Audio routes for manual listening and audio streaming."""

from __future__ import annotations

import contextlib
import os
import select
import subprocess
import time

from flask import Response, jsonify, request

import routes.listening_post as _state
from utils.responses import start_failure

from . import (
    _start_audio_stream,
    _stop_audio_stream,
    _stop_waterfall_internal,
    _wav_header,
    app_module,
    logger,
    normalize_modulation,
    receiver_bp,
    scanner_config,
)

# ============================================
# MANUAL AUDIO ENDPOINTS (for direct listening)
# ============================================


@receiver_bp.route("/audio/start", methods=["POST"])
def start_audio() -> Response:
    """Start audio at specific frequency (manual mode)."""
    data = request.json or {}

    try:
        frequency = float(data.get("frequency", 0))
        modulation = normalize_modulation(data.get("modulation", "wfm"))
        squelch = int(data["squelch"]) if data.get("squelch") is not None else 0
        gain = int(data["gain"]) if data.get("gain") is not None else 40
        device = int(data["device"]) if data.get("device") is not None else 0
        sdr_type = str(data.get("sdr_type", "rtlsdr")).lower()
        request_token_raw = data.get("request_token")
        request_token = int(request_token_raw) if request_token_raw is not None else None
        bias_t_raw = data.get("bias_t", scanner_config.get("bias_t", False))
        if isinstance(bias_t_raw, str):
            bias_t = bias_t_raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            bias_t = bool(bias_t_raw)
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": f"Invalid parameter: {e}"}), 400

    if frequency <= 0:
        return jsonify({"status": "error", "message": "frequency is required"}), 400

    valid_sdr_types = ["rtlsdr", "hackrf", "airspy", "limesdr", "sdrplay", "usrp", "bladerf", "hydrasdr"]
    if sdr_type not in valid_sdr_types:
        return jsonify({"status": "error", "message": f"Invalid sdr_type. Use: {', '.join(valid_sdr_types)}"}), 400

    with _state.audio_start_lock:
        if request_token is not None:
            if request_token < _state.audio_start_token:
                return jsonify(
                    {
                        "status": "stale",
                        "message": "Superseded audio start request",
                        "source": _state.audio_source,
                        "superseded": True,
                        "current_token": _state.audio_start_token,
                    }
                ), 409
            _state.audio_start_token = request_token
        else:
            _state.audio_start_token += 1
            request_token = _state.audio_start_token

        # Grab scanner refs inside lock, signal stop, clear state
        need_scanner_teardown = False
        scanner_thread_ref = None
        scanner_proc_ref = None
        if _state.scanner_running:
            _state.scanner_running = False
            if _state.scanner_active_device is not None:
                app_module.release_sdr_device(_state.scanner_active_device, _state.scanner_active_sdr_type)
                _state.scanner_active_device = None
                _state.scanner_active_sdr_type = "rtlsdr"
            scanner_thread_ref = _state.scanner_thread
            scanner_proc_ref = _state.scanner_power_process
            _state.scanner_power_process = None
            need_scanner_teardown = True

        # Update config for audio
        scanner_config["squelch"] = squelch
        scanner_config["gain"] = gain
        scanner_config["device"] = device
        scanner_config["sdr_type"] = sdr_type
        scanner_config["bias_t"] = bias_t

    # Scanner teardown outside lock (blocking: thread join, process wait, pkill, sleep)
    if need_scanner_teardown:
        if scanner_thread_ref and scanner_thread_ref.is_alive():
            with contextlib.suppress(Exception):
                scanner_thread_ref.join(timeout=2.0)
        if scanner_proc_ref and scanner_proc_ref.poll() is None:
            try:
                scanner_proc_ref.terminate()
                scanner_proc_ref.wait(timeout=1)
            except Exception:
                with contextlib.suppress(Exception):
                    scanner_proc_ref.kill()
        with contextlib.suppress(Exception):
            subprocess.run(["pkill", "rtl_power"], capture_output=True, timeout=0.5)
        time.sleep(0.5)

    # Re-acquire lock for waterfall check and device claim
    with _state.audio_start_lock:
        # Preferred path: when waterfall WebSocket is active on the same SDR,
        # derive monitor audio from that IQ stream instead of spawning rtl_fm.
        try:
            from routes.waterfall_websocket import (
                get_shared_capture_status,
                start_shared_monitor_from_capture,
            )

            shared = get_shared_capture_status()
            if shared.get("running") and shared.get("device") == device:
                _stop_audio_stream()
                ok, msg = start_shared_monitor_from_capture(
                    device=device,
                    frequency_mhz=frequency,
                    modulation=modulation,
                    squelch=squelch,
                )
                if ok:
                    _state.audio_running = True
                    _state.audio_frequency = frequency
                    _state.audio_modulation = modulation
                    _state.audio_source = "waterfall"
                    # Shared monitor uses the waterfall's existing SDR claim.
                    if _state.receiver_active_device is not None:
                        app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
                        _state.receiver_active_device = None
                        _state.receiver_active_sdr_type = "rtlsdr"
                    return jsonify(
                        {
                            "status": "started",
                            "frequency": frequency,
                            "modulation": modulation,
                            "source": "waterfall",
                            "request_token": request_token,
                        }
                    )
                logger.warning(f"Shared waterfall monitor unavailable: {msg}")
        except Exception as e:
            logger.debug(f"Shared waterfall monitor probe failed: {e}")

        # Stop waterfall if it's using the same SDR (SSE path)
        if _state.waterfall_running and _state.waterfall_active_device == device:
            _stop_waterfall_internal()
            time.sleep(0.2)

        # Claim device for listening audio.  The WebSocket waterfall handler
        # may still be tearing down its IQ capture process (thread join +
        # safe_terminate can take several seconds), so we retry with back-off
        # to give the USB device time to be fully released.
        if _state.receiver_active_device is None or _state.receiver_active_device != device:
            if _state.receiver_active_device is not None:
                app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
                _state.receiver_active_device = None
                _state.receiver_active_sdr_type = "rtlsdr"

            error = None
            max_claim_attempts = 6
            for attempt in range(max_claim_attempts):
                error = app_module.claim_sdr_device(device, "receiver", sdr_type)
                if not error:
                    break
                if attempt < max_claim_attempts - 1:
                    logger.debug(
                        f"Device claim attempt {attempt + 1}/{max_claim_attempts} failed, retrying in 0.5s: {error}"
                    )
                    time.sleep(0.5)

            if error:
                return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409
            _state.receiver_active_device = device
            _state.receiver_active_sdr_type = sdr_type

        _start_audio_stream(
            frequency,
            modulation,
            device=device,
            sdr_type=sdr_type,
            gain=gain,
            squelch=squelch,
            bias_t=bias_t,
        )

        if _state.audio_running:
            _state.audio_source = "process"
            return jsonify(
                {
                    "status": "started",
                    "frequency": _state.audio_frequency,
                    "modulation": _state.audio_modulation,
                    "source": "process",
                    "request_token": request_token,
                }
            )

        # Avoid leaving a stale device claim after startup failure.
        if _state.receiver_active_device is not None:
            app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
            _state.receiver_active_device = None
            _state.receiver_active_sdr_type = "rtlsdr"

        if _state.audio_start_error is not None:
            return start_failure(_state.audio_start_error, "audio")

        start_error = ""
        for log_path in ("/tmp/rtl_fm_stderr.log", "/tmp/ffmpeg_stderr.log"):
            try:
                with open(log_path) as handle:
                    content = handle.read().strip()
                if content:
                    start_error = content.splitlines()[-1]
                    break
            except Exception:
                continue

        message = "Failed to start audio. Check SDR device."
        if start_error:
            message = f"Failed to start audio: {start_error}"
        return jsonify({"status": "error", "message": message}), 500


@receiver_bp.route("/audio/stop", methods=["POST"])
def stop_audio() -> Response:
    """Stop audio."""
    _stop_audio_stream()
    if _state.receiver_active_device is not None:
        app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
        _state.receiver_active_device = None
        _state.receiver_active_sdr_type = "rtlsdr"
    return jsonify({"status": "stopped"})


@receiver_bp.route("/audio/status")
def audio_status() -> Response:
    """Get audio status."""
    running = _state.audio_running
    if _state.audio_source == "waterfall":
        try:
            from routes.waterfall_websocket import get_shared_capture_status

            shared = get_shared_capture_status()
            running = bool(shared.get("running") and shared.get("monitor_enabled"))
        except Exception:
            running = False

    return jsonify(
        {
            "running": running,
            "frequency": _state.audio_frequency,
            "modulation": _state.audio_modulation,
            "source": _state.audio_source,
        }
    )


@receiver_bp.route("/audio/debug")
def audio_debug() -> Response:
    """Get audio debug status and recent stderr logs."""
    rtl_log_path = "/tmp/rtl_fm_stderr.log"
    ffmpeg_log_path = "/tmp/ffmpeg_stderr.log"
    sample_path = "/tmp/audio_probe.bin"

    def _read_log(path: str) -> str:
        try:
            with open(path) as handle:
                return handle.read().strip()
        except Exception:
            return ""

    shared = {}
    if _state.audio_source == "waterfall":
        try:
            from routes.waterfall_websocket import get_shared_capture_status

            shared = get_shared_capture_status()
        except Exception:
            shared = {}

    return jsonify(
        {
            "running": _state.audio_running,
            "frequency": _state.audio_frequency,
            "modulation": _state.audio_modulation,
            "source": _state.audio_source,
            "sdr_type": scanner_config.get("sdr_type", "rtlsdr"),
            "device": scanner_config.get("device", 0),
            "gain": scanner_config.get("gain", 0),
            "squelch": scanner_config.get("squelch", 0),
            "audio_process_alive": bool(_state.audio_process and _state.audio_process.poll() is None),
            "shared_capture": shared,
            "rtl_fm_stderr": _read_log(rtl_log_path),
            "ffmpeg_stderr": _read_log(ffmpeg_log_path),
            "audio_probe_bytes": os.path.getsize(sample_path) if os.path.exists(sample_path) else 0,
        }
    )


@receiver_bp.route("/audio/probe")
def audio_probe() -> Response:
    """Grab a small chunk of audio bytes from the pipeline for debugging."""
    if _state.audio_source == "waterfall":
        try:
            from routes.waterfall_websocket import read_shared_monitor_audio_chunk

            data = read_shared_monitor_audio_chunk(timeout=2.0)
            if not data:
                return jsonify({"status": "error", "message": "no shared audio data available"}), 504
            sample_path = "/tmp/audio_probe.bin"
            with open(sample_path, "wb") as handle:
                handle.write(data)
            return jsonify({"status": "ok", "bytes": len(data), "source": "waterfall"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    if not _state.audio_process or not _state.audio_process.stdout:
        return jsonify({"status": "error", "message": "audio process not running"}), 400

    sample_path = "/tmp/audio_probe.bin"
    size = 0
    try:
        ready, _, _ = select.select([_state.audio_process.stdout], [], [], 2.0)
        if not ready:
            return jsonify({"status": "error", "message": "no data available"}), 504
        data = _state.audio_process.stdout.read(4096)
        if not data:
            return jsonify({"status": "error", "message": "no data read"}), 504
        with open(sample_path, "wb") as handle:
            handle.write(data)
        size = len(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok", "bytes": size})


@receiver_bp.route("/audio/stream")
def stream_audio() -> Response:
    """Stream WAV audio."""
    request_token_raw = request.args.get("request_token")
    request_token = None
    if request_token_raw is not None:
        try:
            request_token = int(request_token_raw)
        except (ValueError, TypeError):
            request_token = None

    if request_token is not None and request_token < _state.audio_start_token:
        return Response(b"", mimetype="audio/wav", status=204)

    if _state.audio_source == "waterfall":
        for _ in range(40):
            if _state.audio_running:
                break
            time.sleep(0.05)

        if not _state.audio_running:
            return Response(b"", mimetype="audio/wav", status=204)

        def generate_shared():
            try:
                from routes.waterfall_websocket import (
                    get_shared_capture_status,
                    read_shared_monitor_audio_chunk,
                )
            except Exception:
                return

            # Browser expects an immediate WAV header.
            yield _wav_header(sample_rate=48000)
            inactive_since: float | None = None

            while _state.audio_running and _state.audio_source == "waterfall":
                if request_token is not None and request_token < _state.audio_start_token:
                    break
                chunk = read_shared_monitor_audio_chunk(timeout=1.0)
                if chunk:
                    inactive_since = None
                    yield chunk
                    continue
                shared = get_shared_capture_status()
                if shared.get("running") and shared.get("monitor_enabled"):
                    inactive_since = None
                    continue
                if inactive_since is None:
                    inactive_since = time.monotonic()
                    continue
                if (time.monotonic() - inactive_since) < 4.0:
                    continue
                if not shared.get("running") or not shared.get("monitor_enabled"):
                    _state.audio_running = False
                    _state.audio_source = "process"
                    break

        return Response(
            generate_shared(),
            mimetype="audio/wav",
            headers={
                "Content-Type": "audio/wav",
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "Transfer-Encoding": "chunked",
            },
        )

    # Wait for audio process to be ready (up to 2 seconds).
    for _ in range(40):
        if _state.audio_running and _state.audio_process:
            break
        time.sleep(0.05)

    if not _state.audio_running or not _state.audio_process:
        return Response(b"", mimetype="audio/wav", status=204)

    def generate():
        # Capture local reference to avoid race condition with stop
        proc = _state.audio_process
        if not proc or not proc.stdout:
            return
        try:
            # Drain stale audio that accumulated in the pipe buffer
            # between pipeline start and stream connection.  Keep the
            # first chunk (contains WAV header) and discard the rest
            # so the browser starts close to real-time.
            header_chunk = None
            while True:
                ready, _, _ = select.select([proc.stdout], [], [], 0)
                if not ready:
                    break
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                if header_chunk is None:
                    header_chunk = chunk
            if header_chunk:
                yield header_chunk

            # Stream real-time audio
            first_chunk_deadline = time.time() + 20.0
            warned_wait = False
            while _state.audio_running and proc.poll() is None:
                if request_token is not None and request_token < _state.audio_start_token:
                    break
                # Use select to avoid blocking forever
                ready, _, _ = select.select([proc.stdout], [], [], 2.0)
                if ready:
                    chunk = proc.stdout.read(8192)
                    if chunk:
                        warned_wait = False
                        yield chunk
                    else:
                        break
                else:
                    # Keep connection open while demodulator settles.
                    if time.time() > first_chunk_deadline:
                        if not warned_wait:
                            logger.warning("Audio stream still waiting for first chunk")
                            warned_wait = True
                        continue
                    # Timeout - check if process died
                    if proc.poll() is not None:
                        break
        except GeneratorExit:
            pass
        except Exception as e:
            logger.error(f"Audio stream error: {e}")

    return Response(
        generate(),
        mimetype="audio/wav",
        headers={
            "Content-Type": "audio/wav",
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
