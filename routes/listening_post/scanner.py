"""Scanner routes and implementation for frequency scanning."""

from __future__ import annotations

import contextlib
import math
import queue
import struct
import subprocess
import threading
import time

from flask import Response, jsonify, request

import routes.listening_post as _state

from . import (
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
    _rtl_fm_demod_mode,
    _start_audio_stream,
    _stop_audio_stream,
    activity_log,
    activity_log_lock,
    add_activity_log,
    app_module,
    find_rtl_fm,
    find_rtl_power,
    find_rx_fm,
    logger,
    normalize_modulation,
    receiver_bp,
    scanner_config,
    scanner_lock,
    scanner_queue,
    sse_stream_fanout,
)

# ============================================
# SCANNER IMPLEMENTATION
# ============================================


def scanner_loop():
    """Main scanner loop - scans frequencies looking for signals."""
    logger.info("Scanner thread started")
    add_activity_log(
        "scanner_start",
        scanner_config["start_freq"],
        f"Scanning {scanner_config['start_freq']}-{scanner_config['end_freq']} MHz",
    )

    rtl_fm_path = find_rtl_fm()

    if not rtl_fm_path:
        logger.error("rtl_fm not found")
        add_activity_log("error", 0, "rtl_fm not found")
        _state.scanner_running = False
        return

    current_freq = scanner_config["start_freq"]
    last_signal_time = 0
    signal_detected = False

    try:
        while _state.scanner_running:
            # Check if paused
            if _state.scanner_paused:
                time.sleep(0.1)
                continue

            # Read config values on each iteration (allows live updates)
            step_mhz = scanner_config["step"] / 1000.0
            squelch = scanner_config["squelch"]
            mod = scanner_config["modulation"]
            gain = scanner_config["gain"]
            device = scanner_config["device"]

            _state.scanner_current_freq = current_freq

            # Notify clients of frequency change
            with contextlib.suppress(queue.Full):
                scanner_queue.put_nowait(
                    {
                        "type": "freq_change",
                        "frequency": current_freq,
                        "scanning": not signal_detected,
                        "range_start": scanner_config["start_freq"],
                        "range_end": scanner_config["end_freq"],
                    }
                )

            # Start rtl_fm at this frequency
            freq_hz = int(current_freq * 1e6)

            # Sample rates
            if mod == "wfm":
                sample_rate = 170000
                resample_rate = 32000
            elif mod in ["usb", "lsb"]:
                sample_rate = 12000
                resample_rate = 12000
            else:
                sample_rate = 24000
                resample_rate = 24000

            # Don't use squelch in rtl_fm - we want to analyze raw audio
            rtl_cmd = [
                rtl_fm_path,
                "-M",
                _rtl_fm_demod_mode(mod),
                "-f",
                str(freq_hz),
                "-s",
                str(sample_rate),
                "-r",
                str(resample_rate),
                "-g",
                str(gain),
                "-d",
                str(device),
            ]
            # Add bias-t flag if enabled (for external LNA power)
            if scanner_config.get("bias_t", False):
                rtl_cmd.append("-T")

            try:
                # Start rtl_fm
                rtl_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

                # Read audio data for analysis
                audio_data = b""

                # Read audio samples for a short period
                sample_duration = 0.25  # 250ms - balance between speed and detection
                bytes_needed = int(resample_rate * 2 * sample_duration)  # 16-bit mono

                while len(audio_data) < bytes_needed and _state.scanner_running:
                    chunk = rtl_proc.stdout.read(4096)
                    if not chunk:
                        break
                    audio_data += chunk

                # Clean up rtl_fm
                rtl_proc.terminate()
                try:
                    rtl_proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    rtl_proc.kill()

                # Analyze audio level
                audio_detected = False
                rms = 0
                threshold = 500
                if len(audio_data) > 100:
                    samples = struct.unpack(f"{len(audio_data) // 2}h", audio_data)
                    # Calculate RMS level (root mean square)
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

                    # Threshold based on squelch setting
                    # Lower squelch = more sensitive (lower threshold)
                    # squelch 0 = very sensitive, squelch 100 = only strong signals
                    if mod == "wfm":
                        # WFM: threshold 500-10000 based on squelch
                        threshold = 500 + (squelch * 95)
                        min_threshold = 1500
                    else:
                        # AM/NFM: threshold 300-6500 based on squelch
                        threshold = 300 + (squelch * 62)
                        min_threshold = 900

                    effective_threshold = max(threshold, min_threshold)
                    audio_detected = rms > effective_threshold

                # Send level info to clients
                with contextlib.suppress(queue.Full):
                    scanner_queue.put_nowait(
                        {
                            "type": "scan_update",
                            "frequency": current_freq,
                            "level": int(rms),
                            "threshold": int(effective_threshold) if "effective_threshold" in dir() else 0,
                            "detected": audio_detected,
                            "range_start": scanner_config["start_freq"],
                            "range_end": scanner_config["end_freq"],
                        }
                    )

                if audio_detected and _state.scanner_running:
                    if not signal_detected:
                        # New signal found!
                        signal_detected = True
                        last_signal_time = time.time()
                        add_activity_log(
                            "signal_found", current_freq, f"Signal detected on {current_freq:.3f} MHz ({mod.upper()})"
                        )
                        logger.info(f"Signal found at {current_freq} MHz")

                        # Start audio streaming for user
                        _start_audio_stream(current_freq, mod)

                    try:
                        snr_db = (
                            round(10 * math.log10(rms / effective_threshold), 1)
                            if rms > 0 and effective_threshold > 0
                            else 0.0
                        )
                        scanner_queue.put_nowait(
                            {
                                "type": "signal_found",
                                "frequency": current_freq,
                                "modulation": mod,
                                "audio_streaming": True,
                                "level": int(rms),
                                "threshold": int(effective_threshold),
                                "snr": snr_db,
                                "range_start": scanner_config["start_freq"],
                                "range_end": scanner_config["end_freq"],
                            }
                        )
                    except queue.Full:
                        pass

                    # Check for skip signal
                    if _state.scanner_skip_signal:
                        _state.scanner_skip_signal = False
                        signal_detected = False
                        _stop_audio_stream()
                        with contextlib.suppress(queue.Full):
                            scanner_queue.put_nowait({"type": "signal_skipped", "frequency": current_freq})
                        # Move to next frequency (step is in kHz, convert to MHz)
                        current_freq += step_mhz
                        if current_freq > scanner_config["end_freq"]:
                            current_freq = scanner_config["start_freq"]
                        continue

                    # Stay on this frequency (dwell) but check periodically
                    dwell_start = time.time()
                    while (time.time() - dwell_start) < scanner_config["dwell_time"] and _state.scanner_running:
                        if _state.scanner_skip_signal:
                            break
                        time.sleep(0.2)

                    last_signal_time = time.time()

                    # After dwell, move on to keep scanning
                    if _state.scanner_running and not _state.scanner_skip_signal:
                        signal_detected = False
                        _stop_audio_stream()
                        with contextlib.suppress(queue.Full):
                            scanner_queue.put_nowait(
                                {
                                    "type": "signal_lost",
                                    "frequency": current_freq,
                                    "range_start": scanner_config["start_freq"],
                                    "range_end": scanner_config["end_freq"],
                                }
                            )

                        current_freq += step_mhz
                        if current_freq > scanner_config["end_freq"]:
                            current_freq = scanner_config["start_freq"]
                            add_activity_log("scan_cycle", current_freq, "Scan cycle complete")
                        time.sleep(scanner_config["scan_delay"])

                else:
                    # No signal at this frequency
                    if signal_detected:
                        # Signal lost
                        duration = time.time() - last_signal_time + scanner_config["dwell_time"]
                        add_activity_log("signal_lost", current_freq, f"Signal lost after {duration:.1f}s")
                        signal_detected = False

                        # Stop audio
                        _stop_audio_stream()

                        with contextlib.suppress(queue.Full):
                            scanner_queue.put_nowait({"type": "signal_lost", "frequency": current_freq})

                    # Move to next frequency (step is in kHz, convert to MHz)
                    current_freq += step_mhz
                    if current_freq > scanner_config["end_freq"]:
                        current_freq = scanner_config["start_freq"]
                        add_activity_log("scan_cycle", current_freq, "Scan cycle complete")

                    time.sleep(scanner_config["scan_delay"])

            except Exception as e:
                logger.error(f"Scanner error at {current_freq} MHz: {e}")
                time.sleep(0.5)

    except Exception as e:
        logger.error(f"Scanner loop error: {e}")
    finally:
        _state.scanner_running = False
        _stop_audio_stream()
        add_activity_log("scanner_stop", _state.scanner_current_freq, "Scanner stopped")
        logger.info("Scanner thread stopped")


def scanner_loop_power():
    """Power sweep scanner using rtl_power to detect peaks."""
    logger.info("Power sweep scanner thread started")
    add_activity_log(
        "scanner_start",
        scanner_config["start_freq"],
        f"Power sweep {scanner_config['start_freq']}-{scanner_config['end_freq']} MHz",
    )

    rtl_power_path = find_rtl_power()
    if not rtl_power_path:
        logger.error("rtl_power not found")
        add_activity_log("error", 0, "rtl_power not found")
        _state.scanner_running = False
        return

    try:
        while _state.scanner_running:
            if _state.scanner_paused:
                time.sleep(0.1)
                continue

            start_mhz = scanner_config["start_freq"]
            end_mhz = scanner_config["end_freq"]
            step_khz = scanner_config["step"]
            gain = scanner_config["gain"]
            device = scanner_config["device"]
            scanner_config["squelch"]
            mod = scanner_config["modulation"]

            # Configure sweep
            bin_hz = max(1000, int(step_khz * 1000))
            start_hz = int(start_mhz * 1e6)
            end_hz = int(end_mhz * 1e6)
            # Integration time per sweep (seconds)
            integration = max(0.3, min(1.0, scanner_config.get("scan_delay", 0.5)))

            cmd = [
                rtl_power_path,
                "-f",
                f"{start_hz}:{end_hz}:{bin_hz}",
                "-i",
                f"{integration}",
                "-1",
                "-g",
                str(gain),
                "-d",
                str(device),
            ]

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                _state.scanner_power_process = proc
                stdout, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout = b""
            finally:
                _state.scanner_power_process = None

            if not _state.scanner_running:
                break

            if not stdout:
                add_activity_log("error", start_mhz, "Power sweep produced no data")
                with contextlib.suppress(queue.Full):
                    scanner_queue.put_nowait(
                        {
                            "type": "scan_update",
                            "frequency": end_mhz,
                            "level": 0,
                            "threshold": int(float(scanner_config.get("snr_threshold", 12)) * 100),
                            "detected": False,
                            "range_start": scanner_config["start_freq"],
                            "range_end": scanner_config["end_freq"],
                        }
                    )
                time.sleep(0.2)
                continue

            lines = stdout.decode(errors="ignore").splitlines()
            segments = []
            for line in lines:
                if not line or line.startswith("#"):
                    continue

                parts = [p.strip() for p in line.split(",")]
                # Find start_hz token
                start_idx = None
                for i, tok in enumerate(parts):
                    try:
                        val = float(tok)
                    except ValueError:
                        continue
                    if val > 1e5:
                        start_idx = i
                        break
                if start_idx is None or len(parts) < start_idx + 6:
                    continue

                try:
                    sweep_start = float(parts[start_idx])
                    sweep_end = float(parts[start_idx + 1])
                    sweep_bin = float(parts[start_idx + 2])
                    raw_values = []
                    for v in parts[start_idx + 3 :]:
                        try:
                            raw_values.append(float(v))
                        except ValueError:
                            continue
                    # rtl_power may include a samples field before the power list
                    if raw_values and raw_values[0] >= 0 and any(val < 0 for val in raw_values[1:]):
                        raw_values = raw_values[1:]
                    bin_values = raw_values
                except ValueError:
                    continue

                if not bin_values:
                    continue

                segments.append((sweep_start, sweep_end, sweep_bin, bin_values))

            if not segments:
                add_activity_log("error", start_mhz, "Power sweep bins missing")
                with contextlib.suppress(queue.Full):
                    scanner_queue.put_nowait(
                        {
                            "type": "scan_update",
                            "frequency": end_mhz,
                            "level": 0,
                            "threshold": int(float(scanner_config.get("snr_threshold", 12)) * 100),
                            "detected": False,
                            "range_start": scanner_config["start_freq"],
                            "range_end": scanner_config["end_freq"],
                        }
                    )
                time.sleep(0.2)
                continue

            # Process segments in ascending frequency order to avoid backtracking in UI
            segments.sort(key=lambda s: s[0])
            total_bins = sum(len(seg[3]) for seg in segments)
            if total_bins <= 0:
                time.sleep(0.2)
                continue
            segment_offset = 0

            for sweep_start, sweep_end, sweep_bin, bin_values in segments:
                # Noise floor (median)
                sorted_vals = sorted(bin_values)
                mid = len(sorted_vals) // 2
                noise_floor = sorted_vals[mid]

                # SNR threshold (dB)
                snr_threshold = float(scanner_config.get("snr_threshold", 12))

                # Emit progress updates (throttled)
                emit_stride = max(1, len(bin_values) // 60)
                for idx, val in enumerate(bin_values):
                    if idx % emit_stride != 0 and idx != len(bin_values) - 1:
                        continue
                    freq_hz = sweep_start + sweep_bin * idx
                    _state.scanner_current_freq = freq_hz / 1e6
                    snr = val - noise_floor
                    level = int(max(0, snr) * 100)
                    threshold = int(snr_threshold * 100)
                    progress = min(1.0, (segment_offset + idx) / max(1, total_bins - 1))
                    with contextlib.suppress(queue.Full):
                        scanner_queue.put_nowait(
                            {
                                "type": "scan_update",
                                "frequency": _state.scanner_current_freq,
                                "level": level,
                                "threshold": threshold,
                                "detected": snr >= snr_threshold,
                                "progress": progress,
                                "range_start": scanner_config["start_freq"],
                                "range_end": scanner_config["end_freq"],
                            }
                        )
                segment_offset += len(bin_values)

                # Detect peaks (clusters above threshold)
                peaks = []
                in_cluster = False
                peak_idx = None
                peak_val = None
                for idx, val in enumerate(bin_values):
                    snr = val - noise_floor
                    if snr >= snr_threshold:
                        if not in_cluster:
                            in_cluster = True
                            peak_idx = idx
                            peak_val = val
                        else:
                            if val > peak_val:
                                peak_val = val
                                peak_idx = idx
                    else:
                        if in_cluster and peak_idx is not None:
                            peaks.append((peak_idx, peak_val))
                        in_cluster = False
                        peak_idx = None
                        peak_val = None
                if in_cluster and peak_idx is not None:
                    peaks.append((peak_idx, peak_val))

                for idx, val in peaks:
                    freq_hz = sweep_start + sweep_bin * (idx + 0.5)
                    freq_mhz = freq_hz / 1e6
                    snr = val - noise_floor
                    level = int(max(0, snr) * 100)
                    threshold = int(snr_threshold * 100)
                    add_activity_log("signal_found", freq_mhz, f"Peak detected at {freq_mhz:.3f} MHz ({mod.upper()})")
                    with contextlib.suppress(queue.Full):
                        scanner_queue.put_nowait(
                            {
                                "type": "signal_found",
                                "frequency": freq_mhz,
                                "modulation": mod,
                                "audio_streaming": False,
                                "level": level,
                                "threshold": threshold,
                                "snr": round(snr, 1),
                                "range_start": scanner_config["start_freq"],
                                "range_end": scanner_config["end_freq"],
                            }
                        )

            add_activity_log("scan_cycle", start_mhz, "Power sweep complete")
            time.sleep(max(0.1, scanner_config.get("scan_delay", 0.5)))

    except Exception as e:
        logger.error(f"Power sweep scanner error: {e}")
    finally:
        _state.scanner_running = False
        add_activity_log("scanner_stop", _state.scanner_current_freq, "Scanner stopped")
        logger.info("Power sweep scanner thread stopped")


# ============================================
# SCANNER API ENDPOINTS
# ============================================


@receiver_bp.route("/scanner/start", methods=["POST"])
def start_scanner() -> Response:
    """Start the frequency scanner."""
    with scanner_lock:
        if _state.scanner_running:
            return jsonify({"status": "error", "message": "Scanner already running"}), 409

    # Clear stale queue entries so UI updates immediately
    try:
        while True:
            scanner_queue.get_nowait()
    except queue.Empty:
        pass

    data = request.json or {}

    # Update scanner config
    try:
        scanner_config["start_freq"] = float(data.get("start_freq", 88.0))
        scanner_config["end_freq"] = float(data.get("end_freq", 108.0))
        scanner_config["step"] = float(data.get("step", 0.1))
        scanner_config["modulation"] = normalize_modulation(data.get("modulation", "wfm"))
        scanner_config["squelch"] = int(data.get("squelch", 0))
        scanner_config["dwell_time"] = float(data.get("dwell_time", 3.0))
        scanner_config["scan_delay"] = float(data.get("scan_delay", 0.5))
        scanner_config["device"] = int(data.get("device", 0))
        scanner_config["gain"] = int(data.get("gain", 40))
        scanner_config["bias_t"] = bool(data.get("bias_t", False))
        scanner_config["sdr_type"] = str(data.get("sdr_type", "rtlsdr")).lower()
        scanner_config["scan_method"] = str(data.get("scan_method", "")).lower().strip()
        if data.get("snr_threshold") is not None:
            scanner_config["snr_threshold"] = float(data.get("snr_threshold"))
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": f"Invalid parameter: {e}"}), 400

    # Validate
    if scanner_config["start_freq"] >= scanner_config["end_freq"]:
        return jsonify({"status": "error", "message": "start_freq must be less than end_freq"}), 400

    # Decide scan method
    if not scanner_config["scan_method"]:
        scanner_config["scan_method"] = "power" if find_rtl_power() else "classic"

    sdr_type = scanner_config["sdr_type"]

    # Power scan only supports RTL-SDR for now
    if scanner_config["scan_method"] == "power" and (sdr_type != "rtlsdr" or not find_rtl_power()):
        scanner_config["scan_method"] = "classic"

    # Check tools based on chosen method
    if scanner_config["scan_method"] == "power":
        if not find_rtl_power():
            return jsonify({"status": "error", "message": "rtl_power not found. Install rtl-sdr tools."}), 503
        # Release listening device if active
        if _state.receiver_active_device is not None:
            app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
            _state.receiver_active_device = None
            _state.receiver_active_sdr_type = "rtlsdr"
        # Claim device for scanner
        error = app_module.claim_sdr_device(scanner_config["device"], "scanner", scanner_config["sdr_type"])
        if error:
            return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409
        _state.scanner_active_device = scanner_config["device"]
        _state.scanner_active_sdr_type = scanner_config["sdr_type"]
        _state.scanner_running = True
        _state.scanner_thread = threading.Thread(target=scanner_loop_power, daemon=True)
        _state.scanner_thread.start()
    else:
        if sdr_type == "rtlsdr":
            if not find_rtl_fm():
                return jsonify({"status": "error", "message": "rtl_fm not found. Install rtl-sdr tools."}), 503
        else:
            if not find_rx_fm():
                return jsonify(
                    {"status": "error", "message": f"rx_fm not found. Install SoapySDR utilities for {sdr_type}."}
                ), 503
        if _state.receiver_active_device is not None:
            app_module.release_sdr_device(_state.receiver_active_device, _state.receiver_active_sdr_type)
            _state.receiver_active_device = None
            _state.receiver_active_sdr_type = "rtlsdr"
        error = app_module.claim_sdr_device(scanner_config["device"], "scanner", scanner_config["sdr_type"])
        if error:
            return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409
        _state.scanner_active_device = scanner_config["device"]
        _state.scanner_active_sdr_type = scanner_config["sdr_type"]

        _state.scanner_running = True
        _state.scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
        _state.scanner_thread.start()

    return jsonify({"status": "started", "config": scanner_config})


@receiver_bp.route("/scanner/stop", methods=["POST"])
def stop_scanner() -> Response:
    """Stop the frequency scanner."""
    _state.scanner_running = False
    _stop_audio_stream()
    if _state.scanner_power_process and _state.scanner_power_process.poll() is None:
        try:
            _state.scanner_power_process.terminate()
            _state.scanner_power_process.wait(timeout=1)
        except Exception:
            with contextlib.suppress(Exception):
                _state.scanner_power_process.kill()
        _state.scanner_power_process = None
    if _state.scanner_active_device is not None:
        app_module.release_sdr_device(_state.scanner_active_device, _state.scanner_active_sdr_type)
        _state.scanner_active_device = None
        _state.scanner_active_sdr_type = "rtlsdr"

    return jsonify({"status": "stopped"})


@receiver_bp.route("/scanner/pause", methods=["POST"])
def pause_scanner() -> Response:
    """Pause/resume the scanner."""
    _state.scanner_paused = not _state.scanner_paused

    if _state.scanner_paused:
        add_activity_log("scanner_pause", _state.scanner_current_freq, "Scanner paused")
    else:
        add_activity_log("scanner_resume", _state.scanner_current_freq, "Scanner resumed")

    return jsonify({"status": "paused" if _state.scanner_paused else "resumed", "paused": _state.scanner_paused})


@receiver_bp.route("/scanner/skip", methods=["POST"])
def skip_signal() -> Response:
    """Skip current signal and continue scanning."""
    if not _state.scanner_running:
        return jsonify({"status": "error", "message": "Scanner not running"}), 400

    _state.scanner_skip_signal = True
    add_activity_log(
        "signal_skip", _state.scanner_current_freq, f"Skipped signal at {_state.scanner_current_freq:.3f} MHz"
    )

    return jsonify({"status": "skipped", "frequency": _state.scanner_current_freq})


@receiver_bp.route("/scanner/config", methods=["POST"])
def update_scanner_config() -> Response:
    """Update scanner config while running (step, squelch, gain, dwell)."""
    data = request.json or {}

    updated = []

    if "step" in data:
        scanner_config["step"] = float(data["step"])
        updated.append(f"step={data['step']}kHz")

    if "squelch" in data:
        scanner_config["squelch"] = int(data["squelch"])
        updated.append(f"squelch={data['squelch']}")

    if "gain" in data:
        scanner_config["gain"] = int(data["gain"])
        updated.append(f"gain={data['gain']}")

    if "dwell_time" in data:
        scanner_config["dwell_time"] = int(data["dwell_time"])
        updated.append(f"dwell={data['dwell_time']}s")

    if "modulation" in data:
        try:
            scanner_config["modulation"] = normalize_modulation(data["modulation"])
            updated.append(f"mod={data['modulation']}")
        except (ValueError, TypeError) as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    if updated:
        logger.info(f"Scanner config updated: {', '.join(updated)}")

    return jsonify({"status": "updated", "config": scanner_config})


@receiver_bp.route("/scanner/status")
def scanner_status() -> Response:
    """Get scanner status."""
    return jsonify(
        {
            "running": _state.scanner_running,
            "paused": _state.scanner_paused,
            "current_freq": _state.scanner_current_freq,
            "config": scanner_config,
            "audio_streaming": _state.audio_running,
            "audio_frequency": _state.audio_frequency,
        }
    )


@receiver_bp.route("/scanner/stream")
def stream_scanner_events() -> Response:
    """SSE stream for scanner events."""

    response = Response(
        sse_stream_fanout(
            source_queue=scanner_queue,
            channel_key="receiver_scanner",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@receiver_bp.route("/scanner/log")
def get_activity_log() -> Response:
    """Get activity log."""
    limit = request.args.get("limit", 100, type=int)
    with activity_log_lock:
        return jsonify({"log": activity_log[:limit], "total": len(activity_log)})


@receiver_bp.route("/scanner/log/clear", methods=["POST"])
def clear_activity_log() -> Response:
    """Clear activity log."""
    with activity_log_lock:
        activity_log.clear()
    return jsonify({"status": "cleared"})


@receiver_bp.route("/presets")
def get_presets() -> Response:
    """Get scanner presets."""
    presets = [
        {"name": "FM Broadcast", "start": 88.0, "end": 108.0, "step": 0.2, "mod": "wfm"},
        {"name": "Air Band", "start": 118.0, "end": 137.0, "step": 0.025, "mod": "am"},
        {"name": "Marine VHF", "start": 156.0, "end": 163.0, "step": 0.025, "mod": "fm"},
        {"name": "Amateur 2m", "start": 144.0, "end": 148.0, "step": 0.0125, "mod": "fm"},
        {"name": "Amateur 70cm", "start": 430.0, "end": 440.0, "step": 0.025, "mod": "fm"},
        {"name": "PMR446", "start": 446.0, "end": 446.2, "step": 0.0125, "mod": "fm"},
        {"name": "FRS/GMRS", "start": 462.5, "end": 467.7, "step": 0.025, "mod": "fm"},
        {"name": "Weather Radio", "start": 162.4, "end": 162.55, "step": 0.025, "mod": "fm"},
    ]
    return jsonify({"presets": presets})
