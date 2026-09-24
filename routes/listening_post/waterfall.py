"""Waterfall / spectrogram routes and implementation."""

from __future__ import annotations

import contextlib
import math
import queue
import struct
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from flask import Response, jsonify, request

import routes.listening_post as _state

from . import (
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
    SDRFactory,
    SDRType,
    _stop_waterfall_internal,
    app_module,
    find_rtl_power,
    logger,
    process_event,
    receiver_bp,
    sse_stream_fanout,
)

# ============================================
# WATERFALL HELPER FUNCTIONS
# ============================================


def _parse_rtl_power_line(line: str) -> tuple[str | None, float | None, float | None, list[float]]:
    """Parse a single rtl_power CSV line into bins."""
    if not line or line.startswith("#"):
        return None, None, None, []

    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 6:
        return None, None, None, []

    # Timestamp in first two fields (YYYY-MM-DD, HH:MM:SS)
    timestamp = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else parts[0]

    start_idx = None
    for i, tok in enumerate(parts):
        try:
            val = float(tok)
        except ValueError:
            continue
        if val > 1e5:
            start_idx = i
            break
    if start_idx is None or len(parts) < start_idx + 4:
        return timestamp, None, None, []

    try:
        seg_start = float(parts[start_idx])
        seg_end = float(parts[start_idx + 1])
        raw_values = []
        for v in parts[start_idx + 3 :]:
            try:
                raw_values.append(float(v))
            except ValueError:
                continue
        if raw_values and raw_values[0] >= 0 and any(val < 0 for val in raw_values[1:]):
            raw_values = raw_values[1:]
        return timestamp, seg_start, seg_end, raw_values
    except ValueError:
        return timestamp, None, None, []


def _queue_waterfall_error(message: str) -> None:
    """Push an error message onto the waterfall SSE queue."""
    with contextlib.suppress(queue.Full):
        _state.waterfall_queue.put_nowait(
            {
                "type": "waterfall_error",
                "message": message,
                "timestamp": datetime.now().isoformat(),
            }
        )


def _downsample_bins(values: list[float], target: int) -> list[float]:
    """Downsample bins to a target length using simple averaging."""
    if target <= 0 or len(values) <= target:
        return values

    out: list[float] = []
    step = len(values) / target
    for i in range(target):
        start = int(i * step)
        end = int((i + 1) * step)
        if end <= start:
            end = min(start + 1, len(values))
        chunk = values[start:end]
        if not chunk:
            continue
        out.append(sum(chunk) / len(chunk))
    return out


# ============================================
# WATERFALL LOOP IMPLEMENTATIONS
# ============================================


def _waterfall_loop():
    """Continuous waterfall sweep loop emitting FFT data."""
    sdr_type_str = _state.waterfall_config.get("sdr_type", "rtlsdr")
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    if sdr_type == SDRType.RTL_SDR:
        _waterfall_loop_rtl_power()
    else:
        _waterfall_loop_iq(sdr_type)


def _waterfall_loop_iq(sdr_type: SDRType):
    """Waterfall loop using rx_sdr IQ capture + FFT for HackRF/SoapySDR devices."""
    start_freq = _state.waterfall_config["start_freq"]
    end_freq = _state.waterfall_config["end_freq"]
    gain = _state.waterfall_config["gain"]
    device = _state.waterfall_config["device"]
    interval = float(_state.waterfall_config.get("interval", 0.4))

    # Use center frequency and sample rate to cover the requested span
    center_mhz = (start_freq + end_freq) / 2.0
    span_hz = (end_freq - start_freq) * 1e6
    # Pick a sample rate that covers the span (minimum 2 MHz for HackRF)
    sample_rate = max(2000000, int(span_hz))
    # Cap to sensible maximum
    sample_rate = min(sample_rate, 20000000)

    sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
    builder = SDRFactory.get_builder(sdr_type)

    cmd = builder.build_iq_capture_command(
        device=sdr_device,
        frequency_mhz=center_mhz,
        sample_rate=sample_rate,
        gain=float(gain),
    )

    fft_size = min(int(_state.waterfall_config.get("max_bins") or 1024), 4096)

    try:
        _state.waterfall_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Detect immediate startup failures
        time.sleep(0.35)
        if _state.waterfall_process.poll() is not None:
            stderr_text = ""
            try:
                if _state.waterfall_process.stderr:
                    stderr_text = _state.waterfall_process.stderr.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                stderr_text = ""
            msg = stderr_text or f"IQ capture exited early (code {_state.waterfall_process.returncode})"
            logger.error(f"Waterfall startup failed: {msg}")
            _queue_waterfall_error(msg)
            return

        if not _state.waterfall_process.stdout:
            _queue_waterfall_error("IQ capture stdout unavailable")
            return

        # Read IQ samples and compute FFT
        # CU8 format: interleaved unsigned 8-bit I/Q pairs
        bytes_per_sample = 2  # 1 byte I + 1 byte Q
        chunk_bytes = fft_size * bytes_per_sample
        received_any = False

        while _state.waterfall_running:
            raw = _state.waterfall_process.stdout.read(chunk_bytes)
            if not raw or len(raw) < chunk_bytes:
                if _state.waterfall_process.poll() is not None:
                    break
                continue

            received_any = True

            # Convert CU8 to complex float: center at 127.5
            iq = struct.unpack(f"{fft_size * 2}B", raw)
            # Compute power spectrum via FFT
            real_parts = [(iq[i * 2] - 127.5) / 127.5 for i in range(fft_size)]
            imag_parts = [(iq[i * 2 + 1] - 127.5) / 127.5 for i in range(fft_size)]

            bins: list[float] = []
            try:
                # Try numpy if available for efficient FFT
                import numpy as np

                samples = np.array(real_parts, dtype=np.float32) + 1j * np.array(imag_parts, dtype=np.float32)
                # Apply Hann window
                window = np.hanning(fft_size)
                samples *= window
                spectrum = np.fft.fftshift(np.fft.fft(samples))
                power_db = 10.0 * np.log10(np.abs(spectrum) ** 2 + 1e-10)
                bins = power_db.tolist()
            except ImportError:
                # Fallback: compute magnitude without full FFT
                # Just report raw magnitudes per sample as approximate power
                for i in range(fft_size):
                    mag = math.sqrt(real_parts[i] ** 2 + imag_parts[i] ** 2)
                    power = 10.0 * math.log10(mag**2 + 1e-10)
                    bins.append(power)

            max_bins = int(_state.waterfall_config.get("max_bins") or 0)
            if max_bins > 0 and len(bins) > max_bins:
                bins = _downsample_bins(bins, max_bins)

            msg = {
                "type": "waterfall_sweep",
                "start_freq": start_freq,
                "end_freq": end_freq,
                "bins": bins,
                "timestamp": datetime.now().isoformat(),
            }
            try:
                _state.waterfall_queue.put_nowait(msg)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    _state.waterfall_queue.get_nowait()
                with contextlib.suppress(queue.Full):
                    _state.waterfall_queue.put_nowait(msg)

            # Throttle to respect interval
            time.sleep(interval)

        if _state.waterfall_running and not received_any:
            _queue_waterfall_error(f"No IQ data received from {sdr_type.value}")

    except Exception as e:
        logger.error(f"Waterfall IQ loop error: {e}")
        _queue_waterfall_error(f"Waterfall loop error: {e}")
    finally:
        _state.waterfall_running = False
        if _state.waterfall_process and _state.waterfall_process.poll() is None:
            try:
                _state.waterfall_process.terminate()
                _state.waterfall_process.wait(timeout=1)
            except Exception:
                with contextlib.suppress(Exception):
                    _state.waterfall_process.kill()
        _state.waterfall_process = None
        logger.info("Waterfall IQ loop stopped")


def _waterfall_loop_rtl_power():
    """Continuous rtl_power sweep loop emitting waterfall data."""
    rtl_power_path = find_rtl_power()
    if not rtl_power_path:
        logger.error("rtl_power not found for waterfall")
        _queue_waterfall_error("rtl_power not found")
        _state.waterfall_running = False
        return

    start_hz = int(_state.waterfall_config["start_freq"] * 1e6)
    end_hz = int(_state.waterfall_config["end_freq"] * 1e6)
    bin_hz = int(_state.waterfall_config["bin_size"])
    gain = _state.waterfall_config["gain"]
    device = _state.waterfall_config["device"]
    interval = float(_state.waterfall_config.get("interval", 0.4))

    cmd = [
        rtl_power_path,
        "-f",
        f"{start_hz}:{end_hz}:{bin_hz}",
        "-i",
        str(interval),
        "-g",
        str(gain),
        "-d",
        str(device),
    ]

    try:
        _state.waterfall_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True,
            # One corrupt byte must not end the waterfall: with strict decoding
            # it raised inside the read loop and tore the capture down.
            errors="replace",
        )

        # Detect immediate startup failures (e.g. device busy / no device).
        time.sleep(0.35)
        if _state.waterfall_process.poll() is not None:
            stderr_text = ""
            try:
                if _state.waterfall_process.stderr:
                    stderr_text = _state.waterfall_process.stderr.read().strip()
            except Exception:
                stderr_text = ""
            msg = stderr_text or f"rtl_power exited early (code {_state.waterfall_process.returncode})"
            logger.error(f"Waterfall startup failed: {msg}")
            _queue_waterfall_error(msg)
            return

        current_ts = None
        all_bins: list[float] = []
        sweep_start_hz = start_hz
        sweep_end_hz = end_hz
        received_any = False

        if not _state.waterfall_process.stdout:
            _queue_waterfall_error("rtl_power stdout unavailable")
            return

        for line in _state.waterfall_process.stdout:
            if not _state.waterfall_running:
                break

            ts, seg_start, seg_end, bins = _parse_rtl_power_line(line)
            if ts is None or not bins:
                continue
            received_any = True

            if current_ts is None:
                current_ts = ts

            if ts != current_ts and all_bins:
                max_bins = int(_state.waterfall_config.get("max_bins") or 0)
                bins_to_send = all_bins
                if max_bins > 0 and len(bins_to_send) > max_bins:
                    bins_to_send = _downsample_bins(bins_to_send, max_bins)
                msg = {
                    "type": "waterfall_sweep",
                    "start_freq": sweep_start_hz / 1e6,
                    "end_freq": sweep_end_hz / 1e6,
                    "bins": bins_to_send,
                    "timestamp": datetime.now().isoformat(),
                }
                try:
                    _state.waterfall_queue.put_nowait(msg)
                except queue.Full:
                    with contextlib.suppress(queue.Empty):
                        _state.waterfall_queue.get_nowait()
                    with contextlib.suppress(queue.Full):
                        _state.waterfall_queue.put_nowait(msg)

                all_bins = []
                sweep_start_hz = start_hz
                sweep_end_hz = end_hz
                current_ts = ts

            all_bins.extend(bins)
            if seg_start is not None:
                sweep_start_hz = min(sweep_start_hz, seg_start)
            if seg_end is not None:
                sweep_end_hz = max(sweep_end_hz, seg_end)

        # Flush any remaining bins
        if all_bins and _state.waterfall_running:
            max_bins = int(_state.waterfall_config.get("max_bins") or 0)
            bins_to_send = all_bins
            if max_bins > 0 and len(bins_to_send) > max_bins:
                bins_to_send = _downsample_bins(bins_to_send, max_bins)
            msg = {
                "type": "waterfall_sweep",
                "start_freq": sweep_start_hz / 1e6,
                "end_freq": sweep_end_hz / 1e6,
                "bins": bins_to_send,
                "timestamp": datetime.now().isoformat(),
            }
            with contextlib.suppress(queue.Full):
                _state.waterfall_queue.put_nowait(msg)

        if _state.waterfall_running and not received_any:
            _queue_waterfall_error("No waterfall FFT data received from rtl_power")

    except Exception as e:
        logger.error(f"Waterfall loop error: {e}")
        _queue_waterfall_error(f"Waterfall loop error: {e}")
    finally:
        _state.waterfall_running = False
        if _state.waterfall_process and _state.waterfall_process.poll() is None:
            try:
                _state.waterfall_process.terminate()
                _state.waterfall_process.wait(timeout=1)
            except Exception:
                with contextlib.suppress(Exception):
                    _state.waterfall_process.kill()
        _state.waterfall_process = None
        logger.info("Waterfall loop stopped")


# ============================================
# WATERFALL API ENDPOINTS
# ============================================


@receiver_bp.route("/waterfall/start", methods=["POST"])
def start_waterfall() -> Response:
    """Start the waterfall/spectrogram display."""
    with _state.waterfall_lock:
        if _state.waterfall_running:
            return jsonify(
                {
                    "status": "started",
                    "already_running": True,
                    "message": "Waterfall already running",
                    "config": _state.waterfall_config,
                }
            )

    data = request.json or {}

    # Determine SDR type
    sdr_type_str = data.get("sdr_type", "rtlsdr")
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR
        sdr_type_str = sdr_type.value

    # RTL-SDR uses rtl_power; other types use rx_sdr via IQ capture
    if sdr_type == SDRType.RTL_SDR and not find_rtl_power():
        return jsonify({"status": "error", "message": "rtl_power not found"}), 503

    try:
        _state.waterfall_config["start_freq"] = float(data.get("start_freq", 88.0))
        _state.waterfall_config["end_freq"] = float(data.get("end_freq", 108.0))
        _state.waterfall_config["bin_size"] = int(data.get("bin_size", 10000))
        _state.waterfall_config["gain"] = int(data.get("gain", 40))
        _state.waterfall_config["device"] = int(data.get("device", 0))
        _state.waterfall_config["sdr_type"] = sdr_type_str
        if data.get("interval") is not None:
            interval = float(data.get("interval", _state.waterfall_config["interval"]))
            if interval < 0.1 or interval > 5:
                return jsonify({"status": "error", "message": "interval must be between 0.1 and 5 seconds"}), 400
            _state.waterfall_config["interval"] = interval
        if data.get("max_bins") is not None:
            max_bins = int(data.get("max_bins", _state.waterfall_config["max_bins"]))
            if max_bins < 64 or max_bins > 4096:
                return jsonify({"status": "error", "message": "max_bins must be between 64 and 4096"}), 400
            _state.waterfall_config["max_bins"] = max_bins
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": f"Invalid parameter: {e}"}), 400

    if _state.waterfall_config["start_freq"] >= _state.waterfall_config["end_freq"]:
        return jsonify({"status": "error", "message": "start_freq must be less than end_freq"}), 400

    # Clear stale queue
    try:
        while True:
            _state.waterfall_queue.get_nowait()
    except queue.Empty:
        pass

    # Claim SDR device
    error = app_module.claim_sdr_device(_state.waterfall_config["device"], "waterfall", sdr_type_str)
    if error:
        return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409

    _state.waterfall_active_device = _state.waterfall_config["device"]
    _state.waterfall_active_sdr_type = sdr_type_str
    _state.waterfall_running = True
    _state.waterfall_thread = threading.Thread(target=_waterfall_loop, daemon=True)
    _state.waterfall_thread.start()

    return jsonify({"status": "started", "config": _state.waterfall_config})


@receiver_bp.route("/waterfall/stop", methods=["POST"])
def stop_waterfall() -> Response:
    """Stop the waterfall display."""
    _stop_waterfall_internal()

    return jsonify({"status": "stopped"})


@receiver_bp.route("/waterfall/stream")
def stream_waterfall() -> Response:
    """SSE stream for waterfall data."""

    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("waterfall", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=_state.waterfall_queue,
            channel_key="receiver_waterfall",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
