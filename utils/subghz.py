"""SubGHz transceiver manager for HackRF-based signal capture, decode, and replay.

Provides IQ capture via hackrf_transfer, protocol decoding via hackrf_transfer piped
to rtl_433, signal replay/transmit with safety enforcement, and wideband spectrum
sweeps via hackrf_sweep.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable

import numpy as np

from utils.constants import (
    SUBGHZ_LNA_GAIN_MAX,
    SUBGHZ_LNA_GAIN_MIN,
    SUBGHZ_TX_ALLOWED_BANDS,
    SUBGHZ_TX_MAX_DURATION,
    SUBGHZ_TX_VGA_GAIN_MAX,
    SUBGHZ_TX_VGA_GAIN_MIN,
    SUBGHZ_VGA_GAIN_MAX,
    SUBGHZ_VGA_GAIN_MIN,
)
from utils.dependencies import get_tool_path
from utils.logging import get_logger
from utils.process import register_process, safe_terminate, unregister_process

logger = get_logger("intercept.subghz")


@dataclass
class SubGhzCapture:
    """Metadata for a saved IQ capture."""

    capture_id: str
    filename: str
    frequency_hz: int
    sample_rate: int
    lna_gain: int
    vga_gain: int
    timestamp: str
    duration_seconds: float = 0.0
    size_bytes: int = 0
    label: str = ""
    label_source: str = ""
    decoded_protocols: list[str] = field(default_factory=list)
    bursts: list[dict] = field(default_factory=list)
    modulation_hint: str = ""
    modulation_confidence: float = 0.0
    protocol_hint: str = ""
    dominant_fingerprint: str = ""
    fingerprint_group: str = ""
    fingerprint_group_size: int = 0
    trigger_enabled: bool = False
    trigger_pre_seconds: float = 0.0
    trigger_post_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.capture_id,
            "filename": self.filename,
            "frequency_hz": self.frequency_hz,
            "sample_rate": self.sample_rate,
            "lna_gain": self.lna_gain,
            "vga_gain": self.vga_gain,
            "timestamp": self.timestamp,
            "duration_seconds": self.duration_seconds,
            "size_bytes": self.size_bytes,
            "label": self.label,
            "label_source": self.label_source,
            "decoded_protocols": self.decoded_protocols,
            "bursts": self.bursts,
            "modulation_hint": self.modulation_hint,
            "modulation_confidence": self.modulation_confidence,
            "protocol_hint": self.protocol_hint,
            "dominant_fingerprint": self.dominant_fingerprint,
            "fingerprint_group": self.fingerprint_group,
            "fingerprint_group_size": self.fingerprint_group_size,
            "trigger_enabled": self.trigger_enabled,
            "trigger_pre_seconds": self.trigger_pre_seconds,
            "trigger_post_seconds": self.trigger_post_seconds,
        }


@dataclass
class SweepPoint:
    """A single frequency/power data point from hackrf_sweep."""

    freq_mhz: float
    power_dbm: float

    def to_dict(self) -> dict:
        return {"freq": self.freq_mhz, "power": self.power_dbm}


class SubGhzManager:
    """Singleton manager for SubGHz transceiver operations.

    Manages hackrf_transfer (RX/TX), rtl_433 (decode), and hackrf_sweep (spectrum)
    subprocesses with mutual exclusion and safety enforcement.
    """

    def __init__(self, data_dir: str | Path | None = None):
        self._data_dir = Path(data_dir) if data_dir else Path("data/subghz")
        self._captures_dir = self._data_dir / "captures"
        self._captures_dir.mkdir(parents=True, exist_ok=True)

        # Process state
        self._rx_process: subprocess.Popen | None = None
        self._decode_process: subprocess.Popen | None = None
        self._decode_hackrf_process: subprocess.Popen | None = None
        self._tx_process: subprocess.Popen | None = None
        self._sweep_process: subprocess.Popen | None = None

        self._lock = threading.RLock()
        self._callback: Callable[[dict], None] | None = None

        # RX state
        self._rx_start_time: float = 0
        self._rx_frequency_hz: int = 0
        self._rx_sample_rate: int = 0
        self._rx_lna_gain: int = 0
        self._rx_vga_gain: int = 0
        self._rx_file: Path | None = None
        self._rx_file_handle: BinaryIO | None = None
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = False
        self._rx_bytes_written = 0
        self._rx_bursts: list[dict] = []
        self._rx_trigger_enabled = False
        self._rx_trigger_pre_s = 0.35
        self._rx_trigger_post_s = 0.7
        self._rx_trigger_first_burst_start: float | None = None
        self._rx_trigger_last_burst_end: float | None = None
        self._rx_autostop_pending = False
        self._rx_modulation_hint = ""
        self._rx_modulation_confidence = 0.0
        self._rx_protocol_hint = ""
        self._rx_fingerprint_counts: dict[str, int] = {}

        # Decode state
        self._decode_start_time: float = 0
        self._decode_frequency_hz: int = 0
        self._decode_sample_rate: int = 0
        self._decode_stop = False

        # TX state
        self._tx_start_time: float = 0
        self._tx_watchdog: threading.Timer | None = None
        self._tx_capture_id: str = ""
        self._tx_temp_file: Path | None = None

        # Sweep state
        self._sweep_running = False
        self._sweep_thread: threading.Thread | None = None

        # Tool availability
        self._hackrf_available: bool | None = None
        self._hackrf_info_available: bool | None = None
        self._hackrf_device_cache: bool | None = None
        self._hackrf_device_cache_ts: float = 0.0
        self._rtl433_available: bool | None = None
        self._sweep_available: bool | None = None

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def set_callback(self, callback: Callable[[dict], None] | None) -> None:
        self._callback = callback

    def _emit(self, event: dict) -> None:
        if self._callback:
            try:
                self._callback(event)
            except Exception as e:
                logger.error(f"Error in SubGHz callback: {e}")

    # ------------------------------------------------------------------
    # Tool detection
    # ------------------------------------------------------------------

    def _resolve_tool(self, name: str) -> str | None:
        """Resolve executable path via PATH first, then platform-aware fallbacks."""
        return shutil.which(name) or get_tool_path(name)

    def check_hackrf(self) -> bool:
        if self._hackrf_available is None:
            # Either binary confirms the hackrf package is installed. hackrf_transfer
            # may be absent when only soapysdr-module-hackrf was installed, but
            # hackrf_info alone is sufficient to detect and report devices.
            self._hackrf_available = (
                self._resolve_tool("hackrf_transfer") is not None or self._resolve_tool("hackrf_info") is not None
            )
        return self._hackrf_available

    def check_hackrf_info(self) -> bool:
        if self._hackrf_info_available is None:
            self._hackrf_info_available = self._resolve_tool("hackrf_info") is not None
        return self._hackrf_info_available

    def check_hackrf_device(self) -> bool | None:
        """Return True if a HackRF device is detected, False if not, or None if detection unavailable."""
        if not self.check_hackrf_info():
            return None

        now = time.time()
        if self._hackrf_device_cache is not None and (now - self._hackrf_device_cache_ts) < 2.0:
            return self._hackrf_device_cache

        try:
            from utils.sdr.detection import detect_hackrf_devices

            connected = len(detect_hackrf_devices()) > 0
        except Exception as exc:
            logger.debug(f"HackRF device detection failed: {exc}")
            connected = False

        self._hackrf_device_cache = connected
        self._hackrf_device_cache_ts = now
        return connected

    def _require_hackrf_device(self) -> str | None:
        """Return an error string if HackRF is explicitly not detected."""
        detected = self.check_hackrf_device()
        if detected is False:
            return "HackRF device not detected"
        return None

    def check_rtl433(self) -> bool:
        if self._rtl433_available is None:
            self._rtl433_available = self._resolve_tool("rtl_433") is not None
        return self._rtl433_available

    def check_sweep(self) -> bool:
        if self._sweep_available is None:
            self._sweep_available = self._resolve_tool("hackrf_sweep") is not None
        return self._sweep_available

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def active_mode(self) -> str:
        """Return current active mode or 'idle'."""
        with self._lock:
            if self._rx_process and self._rx_process.poll() is None:
                return "rx"
            if self._decode_process and self._decode_process.poll() is None:
                return "decode"
            if self._tx_process and self._tx_process.poll() is None:
                return "tx"
            if self._sweep_process and self._sweep_process.poll() is None:
                return "sweep"
            return "idle"

    def get_status(self) -> dict:
        mode = self.active_mode
        hackrf_info_available = self.check_hackrf_info()
        detect_paused = mode in {"rx", "decode", "tx", "sweep"}
        if detect_paused:
            # Avoid probing HackRF while a stream is active. A fresh "disconnected"
            # cache result should still surface to the UI, otherwise mark unknown.
            if self._hackrf_device_cache is False and (time.time() - self._hackrf_device_cache_ts) < 15.0:
                hackrf_connected: bool | None = False
            else:
                hackrf_connected = None
        else:
            hackrf_connected = self.check_hackrf_device()
        status: dict = {
            "mode": mode,
            "hackrf_available": self.check_hackrf(),
            "hackrf_info_available": hackrf_info_available,
            "hackrf_connected": hackrf_connected,
            "hackrf_detection_paused": detect_paused,
            "rtl433_available": self.check_rtl433(),
            "sweep_available": self.check_sweep(),
        }
        if mode == "rx":
            elapsed = time.time() - self._rx_start_time if self._rx_start_time else 0
            status.update(
                {
                    "frequency_hz": self._rx_frequency_hz,
                    "sample_rate": self._rx_sample_rate,
                    "elapsed_seconds": round(elapsed, 1),
                    "trigger_enabled": self._rx_trigger_enabled,
                    "trigger_pre_seconds": round(self._rx_trigger_pre_s, 3),
                    "trigger_post_seconds": round(self._rx_trigger_post_s, 3),
                }
            )
        elif mode == "decode":
            elapsed = time.time() - self._decode_start_time if self._decode_start_time else 0
            status.update(
                {
                    "frequency_hz": self._decode_frequency_hz,
                    "sample_rate": self._decode_sample_rate,
                    "elapsed_seconds": round(elapsed, 1),
                }
            )
        elif mode == "tx":
            elapsed = time.time() - self._tx_start_time if self._tx_start_time else 0
            status.update(
                {
                    "capture_id": self._tx_capture_id,
                    "elapsed_seconds": round(elapsed, 1),
                }
            )
        return status

    # ------------------------------------------------------------------
    # RECEIVE (IQ capture via hackrf_transfer -r)
    # ------------------------------------------------------------------

    def start_receive(
        self,
        frequency_hz: int,
        sample_rate: int = 2000000,
        lna_gain: int = 32,
        vga_gain: int = 20,
        trigger_enabled: bool = False,
        trigger_pre_ms: int = 350,
        trigger_post_ms: int = 700,
        device_serial: str | None = None,
    ) -> dict:
        # Pre-lock: tool availability & device detection (blocking I/O)
        hackrf_transfer_path = self._resolve_tool("hackrf_transfer")
        if not hackrf_transfer_path:
            return {"status": "error", "message": "hackrf_transfer not found"}
        device_err = self._require_hackrf_device()
        if device_err:
            return {"status": "error", "message": device_err}

        with self._lock:
            if self.active_mode != "idle":
                return {"status": "error", "message": f"Already running: {self.active_mode}"}

            # Validate gains
            lna_gain = max(SUBGHZ_LNA_GAIN_MIN, min(SUBGHZ_LNA_GAIN_MAX, lna_gain))
            vga_gain = max(SUBGHZ_VGA_GAIN_MIN, min(SUBGHZ_VGA_GAIN_MAX, vga_gain))

            # Generate filename
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            freq_mhz = frequency_hz / 1_000_000
            basename = f"{freq_mhz:.3f}MHz_{ts}"
            iq_file = self._captures_dir / f"{basename}.iq"

            cmd = [
                hackrf_transfer_path,
                "-r",
                str(iq_file),
                "-f",
                str(frequency_hz),
                "-s",
                str(sample_rate),
                "-l",
                str(lna_gain),
                "-g",
                str(vga_gain),
            ]
            if device_serial:
                cmd.extend(["-d", device_serial])

            logger.info(f"SubGHz RX: {' '.join(cmd)}")

            try:
                try:
                    iq_file.touch(exist_ok=True)
                except OSError as e:
                    logger.error(f"Failed to create RX file: {e}")
                    return {"status": "error", "message": "Failed to create capture file"}

                self._rx_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                register_process(self._rx_process)

                try:
                    self._rx_file_handle = open(iq_file, "rb", buffering=0)
                except OSError as e:
                    safe_terminate(self._rx_process)
                    unregister_process(self._rx_process)
                    self._rx_process = None
                    logger.error(f"Failed to open RX file: {e}")
                    return {"status": "error", "message": "Failed to open capture file"}

                self._rx_start_time = time.time()
                self._rx_frequency_hz = frequency_hz
                self._rx_sample_rate = sample_rate
                self._rx_lna_gain = lna_gain
                self._rx_vga_gain = vga_gain
                self._rx_file = iq_file
                self._rx_stop = False
                self._rx_bytes_written = 0
                self._rx_bursts = []
                self._rx_trigger_enabled = bool(trigger_enabled)
                self._rx_trigger_pre_s = max(0.05, min(5.0, float(trigger_pre_ms) / 1000.0))
                self._rx_trigger_post_s = max(0.10, min(10.0, float(trigger_post_ms) / 1000.0))
                self._rx_trigger_first_burst_start = None
                self._rx_trigger_last_burst_end = None
                self._rx_autostop_pending = False
                self._rx_modulation_hint = ""
                self._rx_modulation_confidence = 0.0
                self._rx_protocol_hint = ""
                self._rx_fingerprint_counts = {}

                # Start capture stream reader
                self._rx_thread = threading.Thread(
                    target=self._rx_capture_loop,
                    daemon=True,
                )
                self._rx_thread.start()

                # Monitor stderr in background
                threading.Thread(
                    target=self._monitor_rx_stderr,
                    daemon=True,
                ).start()

                self._emit(
                    {
                        "type": "status",
                        "mode": "rx",
                        "status": "started",
                        "frequency_hz": frequency_hz,
                        "sample_rate": sample_rate,
                        "trigger_enabled": self._rx_trigger_enabled,
                        "trigger_pre_seconds": round(self._rx_trigger_pre_s, 3),
                        "trigger_post_seconds": round(self._rx_trigger_post_s, 3),
                    }
                )

                if self._rx_trigger_enabled:
                    self._emit(
                        {
                            "type": "info",
                            "text": (
                                f"[rx] Smart trigger armed "
                                f"(pre {self._rx_trigger_pre_s:.2f}s, post {self._rx_trigger_post_s:.2f}s)"
                            ),
                        }
                    )

                return {
                    "status": "started",
                    "frequency_hz": frequency_hz,
                    "sample_rate": sample_rate,
                    "file": iq_file.name,
                    "trigger_enabled": self._rx_trigger_enabled,
                    "trigger_pre_seconds": round(self._rx_trigger_pre_s, 3),
                    "trigger_post_seconds": round(self._rx_trigger_post_s, 3),
                }

            except FileNotFoundError:
                return {"status": "error", "message": "hackrf_transfer not found"}
            except Exception as e:
                logger.error(f"Failed to start RX: {e}")
                return {"status": "error", "message": str(e)}

    def _estimate_modulation_hint(
        self,
        data: bytes,
    ) -> tuple[str, float, str]:
        """Estimate coarse modulation family from raw IQ characteristics."""
        if not data:
            return "Unknown", 0.0, "No samples"
        try:
            raw = np.frombuffer(data, dtype=np.int8).astype(np.float32)
            if raw.size < 2048:
                return "Unknown", 0.0, "Insufficient samples"

            i_vals = raw[0::2]
            q_vals = raw[1::2]
            if i_vals.size == 0 or q_vals.size == 0:
                return "Unknown", 0.0, "Invalid IQ frame"

            # Light decimation for lower CPU while preserving burst shape.
            i_vals = i_vals[::4]
            q_vals = q_vals[::4]
            if i_vals.size < 256 or q_vals.size < 256:
                return "Unknown", 0.0, "Short frame"

            iq = i_vals + 1j * q_vals
            amp = np.abs(iq)
            mean_amp = float(np.mean(amp))
            std_amp = float(np.std(amp))
            amp_cv = std_amp / max(mean_amp, 1.0)

            phase_step = np.angle(iq[1:] * np.conj(iq[:-1]))
            phase_var = float(np.std(phase_step))

            # Simple pulse run-length profile on envelope.
            envelope = amp - float(np.median(amp))
            env_scale = float(np.percentile(np.abs(envelope), 92))
            if env_scale <= 1e-6:
                pulse_density = 0.0
                mean_run = 0.0
            else:
                norm = np.clip(envelope / env_scale, -1.0, 1.0)
                high = norm > 0.25
                pulse_density = float(np.mean(high))
                changes = np.where(np.diff(high.astype(np.int8)) != 0)[0]
                if changes.size >= 2:
                    runs = np.diff(np.concatenate(([0], changes, [high.size - 1])))
                    mean_run = float(np.mean(runs))
                else:
                    mean_run = float(high.size)

            scores = {
                "OOK/ASK": 0.0,
                "FSK/GFSK": 0.0,
                "PWM/PPM": 0.0,
            }

            # OOK: stronger amplitude contrast and moderate pulse occupancy.
            scores["OOK/ASK"] += max(0.0, min(1.0, (amp_cv - 0.22) / 0.35))
            scores["OOK/ASK"] += max(0.0, 1.0 - abs(pulse_density - 0.4) / 0.4) * 0.35

            # FSK: flatter amplitude, more phase movement.
            scores["FSK/GFSK"] += max(0.0, min(1.0, (phase_var - 0.45) / 0.9))
            scores["FSK/GFSK"] += max(0.0, min(1.0, (0.33 - amp_cv) / 0.28)) * 0.45

            # PWM/PPM: high edge density with short run lengths.
            edge_density = 0.0 if mean_run <= 0 else min(1.0, 28.0 / max(mean_run, 1.0))
            scores["PWM/PPM"] += max(0.0, min(1.0, (amp_cv - 0.28) / 0.45))
            scores["PWM/PPM"] += edge_density * 0.6

            best_family = max(scores, key=scores.get)
            best_score = float(scores[best_family])
            confidence = max(0.0, min(0.97, best_score))
            if confidence < 0.25:
                return "Unknown", confidence, "No clear modulation signature"

            reason = f"amp_cv={amp_cv:.2f} phase_var={phase_var:.2f} pulse_density={pulse_density:.2f}"
            return best_family, confidence, reason
        except Exception:
            return "Unknown", 0.0, "Modulation analysis failed"

    def _fingerprint_burst_bytes(
        self,
        data: bytes,
        sample_rate: int,
        duration_seconds: float,
    ) -> str:
        """Create a stable burst fingerprint for grouping similar signals."""
        if not data:
            return ""
        try:
            raw = np.frombuffer(data, dtype=np.int8).astype(np.float32)
            if raw.size < 512:
                return ""

            i_vals = raw[0::2]
            q_vals = raw[1::2]
            if i_vals.size == 0 or q_vals.size == 0:
                return ""

            amp = np.sqrt(i_vals * i_vals + q_vals * q_vals)
            if amp.size < 64:
                return ""

            # Normalize and downsample envelope into a fixed-size shape vector.
            amp = amp - float(np.median(amp))
            scale = float(np.percentile(np.abs(amp), 95))
            if scale <= 1e-6:
                scale = 1.0
            amp = np.clip(amp / scale, -1.0, 1.0)
            target = 128
            if amp.size != target:
                idx = np.linspace(0, amp.size - 1, target).astype(int)
                amp = amp[idx]
            quant = np.round((amp + 1.0) * 7.5).astype(np.uint8)

            # Include coarse timing and center-energy traits.
            burst_ms = int(max(1, round(duration_seconds * 1000)))
            sr_khz = int(max(1, round(sample_rate / 1000)))
            payload = (
                quant.tobytes()
                + burst_ms.to_bytes(2, "little", signed=False)
                + sr_khz.to_bytes(2, "little", signed=False)
            )
            return hashlib.sha1(payload).hexdigest()[:16]
        except Exception:
            return ""

    def _protocol_hint_from_capture(
        self,
        frequency_hz: int,
        modulation_hint: str,
        burst_count: int,
    ) -> str:
        freq = frequency_hz / 1_000_000
        mod = (modulation_hint or "").upper()
        if burst_count <= 0:
            return "No burst activity"
        if 433.70 <= freq <= 434.10 and "OOK" in mod and burst_count >= 2:
            return "Likely weather sensor / simple remote telemetry"
        if 868.0 <= freq <= 870.0 and "OOK" in mod:
            return "Likely EU ISM OOK sensor/remote"
        if 902.0 <= freq <= 928.0 and "FSK" in mod:
            return "Likely ISM telemetry (FSK/GFSK)"
        if "PWM" in mod:
            return "Likely pulse-width/distance keyed remote"
        if "FSK" in mod:
            return "Likely continuous-tone telemetry"
        if "OOK" in mod:
            return "Likely OOK keyed burst transmitter"
        return "Unknown protocol family"

    def _auto_capture_label(
        self,
        frequency_hz: int,
        burst_count: int,
        modulation_hint: str,
        protocol_hint: str,
    ) -> str:
        freq = frequency_hz / 1_000_000
        mod = (modulation_hint or "").upper()
        if burst_count <= 0:
            return f"Raw Capture {freq:.3f} MHz"
        if "weather" in protocol_hint.lower():
            return f"Weather-like Burst ({burst_count})"
        if "OOK" in mod:
            return f"OOK Burst Cluster ({burst_count})"
        if "FSK" in mod:
            return f"FSK Telemetry Burst ({burst_count})"
        if "PWM" in mod:
            return f"PWM/PPM Burst ({burst_count})"
        return f"RF Burst Capture ({burst_count})"

    def _trim_capture_to_trigger_window(
        self,
        iq_file: Path,
        sample_rate: int,
        duration_seconds: float,
        bursts: list[dict],
    ) -> tuple[float, list[dict]]:
        """Trim a full capture to trigger window using configured pre/post roll."""
        if not self._rx_trigger_enabled or not bursts or sample_rate <= 0:
            return duration_seconds, bursts

        first_start = min(float(b.get("start_seconds", 0.0)) for b in bursts)
        last_end = max(float(b.get("start_seconds", 0.0)) + float(b.get("duration_seconds", 0.0)) for b in bursts)
        start_s = max(0.0, first_start - self._rx_trigger_pre_s)
        end_s = min(duration_seconds, last_end + self._rx_trigger_post_s)
        if end_s <= start_s:
            return duration_seconds, bursts
        if start_s <= 0.001 and (duration_seconds - end_s) <= 0.001:
            return duration_seconds, bursts

        bytes_per_second = max(2, int(sample_rate) * 2)
        start_byte = int(start_s * bytes_per_second) & ~1
        end_byte = int(end_s * bytes_per_second) & ~1
        if end_byte <= start_byte:
            return duration_seconds, bursts

        tmp_path = iq_file.with_suffix(".trimtmp")
        try:
            with open(iq_file, "rb") as src, open(tmp_path, "wb") as dst:
                src.seek(start_byte)
                remaining = end_byte - start_byte
                while remaining > 0:
                    chunk = src.read(min(262144, remaining))
                    if not chunk:
                        break
                    dst.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp_path, iq_file)
        except OSError as exc:
            logger.error(f"Failed trimming trigger capture: {exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            return duration_seconds, bursts

        trimmed_duration = max(0.0, float(end_byte - start_byte) / float(bytes_per_second))
        adjusted_bursts: list[dict] = []
        for burst in bursts:
            raw_start = float(burst.get("start_seconds", 0.0))
            raw_dur = max(0.0, float(burst.get("duration_seconds", 0.0)))
            raw_end = raw_start + raw_dur
            if raw_end < start_s or raw_start > end_s:
                continue
            adjusted = dict(burst)
            adjusted["start_seconds"] = round(max(0.0, raw_start - start_s), 3)
            adjusted["duration_seconds"] = round(raw_dur, 3)
            adjusted_bursts.append(adjusted)
        return trimmed_duration, adjusted_bursts if adjusted_bursts else bursts

    def _rx_capture_loop(self) -> None:
        """Read IQ data from the capture file and emit UI metrics."""
        process = self._rx_process
        file_handle = self._rx_file_handle

        if not process or not file_handle:
            logger.error("RX capture loop missing process/file handle")
            return

        CHUNK = 262144  # 256 KB (~64 ms @ 2 Msps complex int8 IQ)
        LEVEL_INTERVAL = 0.05
        WAVE_INTERVAL = 0.25
        SPECTRUM_INTERVAL = 0.25
        STATS_INTERVAL = 1.0
        HINT_EVAL_INTERVAL = 0.25
        HINT_EMIT_INTERVAL = 1.5

        last_level = 0.0
        last_wave = 0.0
        last_spectrum = 0.0
        last_stats = time.time()
        last_log = time.time()
        last_hint_eval = 0.0
        last_hint_emit = 0.0
        bytes_since_stats = 0
        first_chunk = True
        burst_active = False
        burst_start = 0.0
        burst_last_high = 0.0
        burst_peak = 0
        burst_bytes = bytearray()
        burst_hint_family = "Unknown"
        burst_hint_conf = 0.0
        BURST_OFF_HOLD = 0.18
        BURST_MIN_DURATION = 0.04
        MAX_BURST_BYTES = max(262144, int(max(1, self._rx_sample_rate) * 2 * 2))
        smooth_level = 0.0
        prev_smooth_level = 0.0
        noise_floor = 0.0
        peak_tracker = 0.0
        on_threshold = 0.0
        warmup_until = time.time() + 1.0
        modulation_scores: dict[str, float] = {
            "OOK/ASK": 0.0,
            "FSK/GFSK": 0.0,
            "PWM/PPM": 0.0,
        }
        last_hint_reason = ""

        try:
            fd = file_handle.fileno()
            if not isinstance(fd, int) or fd < 0:
                logger.error("Invalid file descriptor from RX file handle")
                return
        except (OSError, ValueError, TypeError):
            logger.error("Failed to obtain RX file descriptor")
            return

        try:
            while not self._rx_stop:
                try:
                    data = os.read(fd, CHUNK)
                except OSError:
                    break
                if not data:
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue

                self._rx_bytes_written += len(data)
                bytes_since_stats += len(data)
                if burst_active and len(burst_bytes) < MAX_BURST_BYTES:
                    room = MAX_BURST_BYTES - len(burst_bytes)
                    burst_bytes.extend(data[:room])

                if first_chunk:
                    first_chunk = False
                    self._emit({"type": "info", "text": "[rx] Receiving IQ data..."})

                now = time.time()
                if now - last_hint_eval >= HINT_EVAL_INTERVAL:
                    for key in modulation_scores:
                        modulation_scores[key] *= 0.97
                    hint_family, hint_conf, hint_reason = self._estimate_modulation_hint(data)
                    if hint_family in modulation_scores:
                        modulation_scores[hint_family] += max(0.05, hint_conf)
                        last_hint_reason = hint_reason
                    last_hint_eval = now

                if now - last_level >= LEVEL_INTERVAL:
                    level = float(self._compute_rx_level(data))
                    prev_smooth_level = smooth_level
                    if smooth_level <= 0:
                        smooth_level = level
                    else:
                        smooth_level = (smooth_level * 0.72) + (level * 0.28)

                    if noise_floor <= 0:
                        noise_floor = smooth_level
                    elif not burst_active:
                        # Track receiver noise floor when we are not inside a burst.
                        noise_floor = (noise_floor * 0.94) + (smooth_level * 0.06)

                    peak_tracker = max(smooth_level, peak_tracker * 0.985)
                    spread = max(2.0, peak_tracker - noise_floor)
                    on_delta = max(2.8, spread * 0.52)
                    off_delta = max(1.2, spread * 0.24)
                    on_threshold = min(95.0, noise_floor + on_delta)
                    off_threshold = max(0.8, min(on_threshold - 0.5, noise_floor + off_delta))
                    rising = smooth_level - prev_smooth_level

                    self._emit({"type": "rx_level", "level": int(round(smooth_level))})

                    if not burst_active:
                        if now >= warmup_until and smooth_level >= on_threshold and rising >= 0.35:
                            burst_active = True
                            burst_start = now
                            burst_last_high = now
                            burst_peak = int(round(smooth_level))
                            burst_bytes = bytearray(data[: min(len(data), MAX_BURST_BYTES)])
                            burst_hint_family = "Unknown"
                            burst_hint_conf = 0.0
                            if self._rx_trigger_enabled and self._rx_trigger_first_burst_start is None:
                                self._rx_trigger_first_burst_start = max(0.0, now - self._rx_start_time)
                                self._emit(
                                    {
                                        "type": "info",
                                        "text": "[rx] Trigger fired - capturing burst window",
                                    }
                                )
                            self._emit(
                                {
                                    "type": "rx_burst",
                                    "mode": "rx",
                                    "event": "start",
                                    "start_offset_s": round(max(0.0, now - self._rx_start_time), 3),
                                    "level": int(round(smooth_level)),
                                }
                            )
                    else:
                        if smooth_level >= off_threshold:
                            burst_last_high = now
                            burst_peak = max(burst_peak, int(round(smooth_level)))
                        elif (now - burst_last_high) >= BURST_OFF_HOLD:
                            duration = now - burst_start
                            if duration >= BURST_MIN_DURATION:
                                fp = self._fingerprint_burst_bytes(
                                    bytes(burst_bytes),
                                    self._rx_sample_rate,
                                    duration,
                                )
                                if fp:
                                    self._rx_fingerprint_counts[fp] = self._rx_fingerprint_counts.get(fp, 0) + 1
                                burst_hint_family, burst_hint_conf, burst_reason = self._estimate_modulation_hint(
                                    bytes(burst_bytes)
                                )
                                if burst_hint_family in modulation_scores and burst_hint_conf > 0:
                                    modulation_scores[burst_hint_family] += burst_hint_conf * 1.8
                                    last_hint_reason = burst_reason
                                burst_data = {
                                    "start_seconds": round(max(0.0, burst_start - self._rx_start_time), 3),
                                    "duration_seconds": round(duration, 3),
                                    "peak_level": int(burst_peak),
                                    "fingerprint": fp,
                                    "modulation_hint": burst_hint_family,
                                    "modulation_confidence": round(float(burst_hint_conf), 3),
                                }
                                if len(self._rx_bursts) < 512:
                                    self._rx_bursts.append(burst_data)
                                self._rx_trigger_last_burst_end = max(0.0, now - self._rx_start_time)
                                self._emit(
                                    {
                                        "type": "rx_burst",
                                        "mode": "rx",
                                        "event": "end",
                                        "start_offset_s": burst_data["start_seconds"],
                                        "duration_ms": int(duration * 1000),
                                        "peak_level": int(burst_peak),
                                        "fingerprint": fp,
                                        "modulation_hint": burst_hint_family,
                                        "modulation_confidence": round(float(burst_hint_conf), 3),
                                    }
                                )
                            burst_active = False
                            burst_peak = 0
                            burst_bytes = bytearray()
                    last_level = now

                # Emit live modulation/protocol hint periodically.
                if now - last_hint_emit >= HINT_EMIT_INTERVAL:
                    best_family = max(modulation_scores, key=modulation_scores.get)
                    total_score = sum(max(0.0, v) for v in modulation_scores.values())
                    best_score = max(0.0, modulation_scores.get(best_family, 0.0))
                    hint_conf = 0.0 if total_score <= 0 else min(0.98, best_score / total_score)
                    protocol_hint = self._protocol_hint_from_capture(
                        self._rx_frequency_hz,
                        best_family if hint_conf >= 0.3 else "Unknown",
                        len(self._rx_bursts),
                    )
                    self._rx_protocol_hint = protocol_hint
                    if hint_conf >= 0.30:
                        self._rx_modulation_hint = best_family
                        self._rx_modulation_confidence = hint_conf
                        self._emit(
                            {
                                "type": "rx_hint",
                                "modulation_hint": best_family,
                                "confidence": round(hint_conf, 3),
                                "protocol_hint": protocol_hint,
                                "reason": last_hint_reason,
                            }
                        )
                    last_hint_emit = now

                # Smart-trigger auto-stop after quiet post-roll window.
                if (
                    self._rx_trigger_enabled
                    and self._rx_trigger_first_burst_start is not None
                    and not burst_active
                    and not self._rx_autostop_pending
                ):
                    last_end = self._rx_trigger_last_burst_end
                    if (
                        last_end is not None
                        and (max(0.0, now - self._rx_start_time) - last_end) >= self._rx_trigger_post_s
                    ):
                        self._rx_autostop_pending = True
                        self._emit(
                            {
                                "type": "info",
                                "text": "[rx] Trigger window complete - finalizing capture",
                            }
                        )
                        threading.Thread(target=self.stop_receive, daemon=True).start()
                        break

                if now - last_wave >= WAVE_INTERVAL:
                    samples = self._extract_waveform(data)
                    if samples:
                        self._emit({"type": "rx_waveform", "samples": samples})
                    last_wave = now

                if now - last_spectrum >= SPECTRUM_INTERVAL:
                    bins = self._compute_rx_spectrum(data)
                    if bins:
                        self._emit({"type": "rx_spectrum", "bins": bins})
                    last_spectrum = now

                if now - last_stats >= STATS_INTERVAL:
                    rate_kb = bytes_since_stats / (now - last_stats) / 1024
                    file_size = 0
                    if self._rx_file and self._rx_file.exists():
                        try:
                            file_size = self._rx_file.stat().st_size
                        except OSError:
                            file_size = 0
                    self._emit(
                        {
                            "type": "rx_stats",
                            "rate_kb": round(rate_kb, 1),
                            "file_size": file_size,
                            "elapsed_seconds": round(time.time() - self._rx_start_time, 1)
                            if self._rx_start_time
                            else 0,
                        }
                    )
                    if now - last_log >= 5.0:
                        self._emit(
                            {
                                "type": "info",
                                "text": (
                                    f"[rx] IQ: {rate_kb:.0f} KB/s "
                                    f"(lvl {smooth_level:.1f}, floor {noise_floor:.1f}, thr {on_threshold:.1f})"
                                ),
                            }
                        )
                        last_log = now
                    bytes_since_stats = 0
                    last_stats = now

            if burst_active:
                duration = max(0.0, time.time() - burst_start)
                if duration >= BURST_MIN_DURATION:
                    fp = self._fingerprint_burst_bytes(
                        bytes(burst_bytes),
                        self._rx_sample_rate,
                        duration,
                    )
                    if fp:
                        self._rx_fingerprint_counts[fp] = self._rx_fingerprint_counts.get(fp, 0) + 1
                    burst_hint_family, burst_hint_conf, burst_reason = self._estimate_modulation_hint(
                        bytes(burst_bytes)
                    )
                    if burst_hint_family in modulation_scores and burst_hint_conf > 0:
                        modulation_scores[burst_hint_family] += burst_hint_conf * 1.8
                        last_hint_reason = burst_reason
                    burst_data = {
                        "start_seconds": round(max(0.0, burst_start - self._rx_start_time), 3),
                        "duration_seconds": round(duration, 3),
                        "peak_level": int(burst_peak),
                        "fingerprint": fp,
                        "modulation_hint": burst_hint_family,
                        "modulation_confidence": round(float(burst_hint_conf), 3),
                    }
                    if len(self._rx_bursts) < 512:
                        self._rx_bursts.append(burst_data)
                    self._rx_trigger_last_burst_end = max(0.0, time.time() - self._rx_start_time)
                    self._emit(
                        {
                            "type": "rx_burst",
                            "mode": "rx",
                            "event": "end",
                            "start_offset_s": burst_data["start_seconds"],
                            "duration_ms": int(duration * 1000),
                            "peak_level": int(burst_peak),
                            "fingerprint": fp,
                            "modulation_hint": burst_hint_family,
                            "modulation_confidence": round(float(burst_hint_conf), 3),
                        }
                    )

            # Finalize modulation summary for capture metadata.
            if modulation_scores:
                best_family = max(modulation_scores, key=modulation_scores.get)
                total_score = sum(max(0.0, v) for v in modulation_scores.values())
                best_score = max(0.0, modulation_scores.get(best_family, 0.0))
                hint_conf = 0.0 if total_score <= 0 else min(0.98, best_score / total_score)
                if hint_conf >= 0.3:
                    self._rx_modulation_hint = best_family
                    self._rx_modulation_confidence = hint_conf
            self._rx_protocol_hint = self._protocol_hint_from_capture(
                self._rx_frequency_hz,
                self._rx_modulation_hint,
                len(self._rx_bursts),
            )
        finally:
            with contextlib.suppress(OSError):
                file_handle.close()
            with self._lock:
                if self._rx_file_handle is file_handle:
                    self._rx_file_handle = None

    def _compute_rx_level(self, data: bytes) -> int:
        """Compute a gain-tolerant 0-100 signal activity score from raw IQ bytes."""
        if not data:
            return 0
        try:
            samples = np.frombuffer(data, dtype=np.int8).astype(np.float32)
            if samples.size < 2:
                return 0
            i_vals = samples[0::2]
            q_vals = samples[1::2]
            if i_vals.size == 0 or q_vals.size == 0:
                return 0
            i_vals = i_vals[::4]
            q_vals = q_vals[::4]
            if i_vals.size == 0 or q_vals.size == 0:
                return 0
            mag = np.sqrt(i_vals * i_vals + q_vals * q_vals)
            if mag.size == 0:
                return 0

            noise = float(np.percentile(mag, 30))
            signal = float(np.percentile(mag, 90))
            peak = float(np.percentile(mag, 99))
            contrast = max(0.0, signal - noise)
            crest = max(0.0, peak - signal)
            mean_mag = float(np.mean(mag))

            # Normalize by local floor so changing gain is less likely to break
            # burst visibility (low gain still detectable, high gain not always "on").
            contrast_norm = contrast / max(8.0, noise + 8.0)
            crest_norm = crest / max(8.0, signal + 8.0)
            energy_norm = mean_mag / 60.0
            level_f = (contrast_norm * 55.0) + (crest_norm * 20.0) + (energy_norm * 10.0)
            level = int(max(0, min(100, level_f)))
            if level == 0 and contrast > 0.5:
                level = 1
            return level
        except Exception:
            return 0

    def _extract_waveform(self, data: bytes, points: int = 256) -> list[float]:
        """Extract a normalized envelope waveform for UI display."""
        try:
            samples = np.frombuffer(data, dtype=np.int8).astype(np.float32)
            if samples.size < 2:
                return []
            i_vals = samples[0::2]
            q_vals = samples[1::2]
            if i_vals.size == 0 or q_vals.size == 0:
                return []
            mag = np.sqrt(i_vals * i_vals + q_vals * q_vals)
            if mag.size == 0:
                return []
            step = max(1, mag.size // points)
            scoped = mag[::step][:points]
            if scoped.size == 0:
                return []
            baseline = float(np.median(scoped))
            centered = scoped - baseline
            scale = float(np.percentile(np.abs(centered), 95))
            if scale <= 1e-6:
                normalized = np.zeros_like(centered)
            else:
                normalized = np.clip(centered / (scale * 2.5), -1.0, 1.0)
            return [round(float(x), 3) for x in normalized.tolist()]
        except Exception:
            return []

    def _compute_rx_spectrum(self, data: bytes, bins: int = 256) -> list[int]:
        """Compute a simple FFT magnitude slice for waterfall rendering."""
        try:
            samples = np.frombuffer(data, dtype=np.int8)
            if samples.size < bins * 2:
                return []
            fft_size = max(256, bins)
            needed = fft_size * 2
            if samples.size < needed:
                return []
            samples = samples[:needed].astype(np.float32)
            i_vals = samples[0::2]
            q_vals = samples[1::2]
            iq = i_vals + 1j * q_vals
            window = np.hanning(fft_size)
            spectrum = np.fft.fftshift(np.fft.fft(iq * window))
            mag = 20 * np.log10(np.abs(spectrum) + 1e-6)
            mag -= np.max(mag)
            # Map -60..0 dB range to 0..255
            scaled = np.clip((mag + 60.0) / 60.0, 0.0, 1.0)
            bins_vals = (scaled * 255).astype(np.uint8)
            if bins_vals.size != bins:
                idx = np.linspace(0, bins_vals.size - 1, bins).astype(int)
                bins_vals = bins_vals[idx]
            return bins_vals.tolist()
        except Exception:
            return []

    def _monitor_rx_stderr(self) -> None:
        process = self._rx_process
        if not process or not process.stderr:
            return
        try:
            for line in iter(process.stderr.readline, b""):
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug(f"[hackrf_rx] {text}")
                    if "error" in text.lower():
                        self._emit({"type": "info", "text": f"[hackrf_rx] {text}"})
        except Exception:
            pass

    def stop_receive(self) -> dict:
        thread_to_join: threading.Thread | None = None
        file_handle: BinaryIO | None = None
        proc_to_terminate: subprocess.Popen | None = None
        with self._lock:
            if not self._rx_process or self._rx_process.poll() is not None:
                return {"status": "not_running"}

            self._rx_stop = True
            thread_to_join = self._rx_thread
            self._rx_thread = None
            file_handle = self._rx_file_handle
            proc_to_terminate = self._rx_process
            self._rx_process = None

        # Terminate outside lock to avoid blocking other operations
        if proc_to_terminate:
            safe_terminate(proc_to_terminate)
            unregister_process(proc_to_terminate)

        if thread_to_join and thread_to_join.is_alive():
            thread_to_join.join(timeout=2.0)

        if file_handle:
            with contextlib.suppress(OSError):
                file_handle.close()
        with self._lock:
            if self._rx_file_handle is file_handle:
                self._rx_file_handle = None

        duration = time.time() - self._rx_start_time if self._rx_start_time else 0
        iq_file = self._rx_file

        # Write JSON sidecar metadata
        capture = None
        if iq_file and iq_file.exists():
            bursts = list(self._rx_bursts)
            duration, bursts = self._trim_capture_to_trigger_window(
                iq_file=iq_file,
                sample_rate=self._rx_sample_rate,
                duration_seconds=duration,
                bursts=bursts,
            )
            size = iq_file.stat().st_size
            dominant_fingerprint = ""
            dominant_fingerprint_count = 0
            for fp, count in self._rx_fingerprint_counts.items():
                if count > dominant_fingerprint_count:
                    dominant_fingerprint = fp
                    dominant_fingerprint_count = count

            modulation_hint = self._rx_modulation_hint
            modulation_confidence = float(self._rx_modulation_confidence or 0.0)
            if not modulation_hint and bursts:
                burst_hint_totals: dict[str, float] = {}
                for burst in bursts:
                    hint_name = str(burst.get("modulation_hint") or "").strip()
                    hint_conf = float(burst.get("modulation_confidence") or 0.0)
                    if not hint_name or hint_name.lower() == "unknown":
                        continue
                    burst_hint_totals[hint_name] = burst_hint_totals.get(hint_name, 0.0) + max(0.05, hint_conf)
                if burst_hint_totals:
                    modulation_hint = max(burst_hint_totals, key=burst_hint_totals.get)
                    total_score = sum(burst_hint_totals.values())
                    modulation_confidence = min(
                        0.98,
                        burst_hint_totals[modulation_hint] / max(total_score, 0.001),
                    )

            protocol_hint = self._protocol_hint_from_capture(
                self._rx_frequency_hz,
                modulation_hint,
                len(bursts),
            )
            label = self._auto_capture_label(
                self._rx_frequency_hz,
                len(bursts),
                modulation_hint,
                protocol_hint,
            )
            capture_id = uuid.uuid4().hex[:12]
            capture = SubGhzCapture(
                capture_id=capture_id,
                filename=iq_file.name,
                frequency_hz=self._rx_frequency_hz,
                sample_rate=self._rx_sample_rate,
                lna_gain=self._rx_lna_gain,
                vga_gain=self._rx_vga_gain,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=round(duration, 1),
                size_bytes=size,
                label=label,
                label_source="auto",
                bursts=bursts,
                modulation_hint=modulation_hint,
                modulation_confidence=round(modulation_confidence, 3),
                protocol_hint=protocol_hint,
                dominant_fingerprint=dominant_fingerprint,
                trigger_enabled=self._rx_trigger_enabled,
                trigger_pre_seconds=round(self._rx_trigger_pre_s, 3),
                trigger_post_seconds=round(self._rx_trigger_post_s, 3),
            )
            meta_path = iq_file.with_suffix(".json")
            try:
                meta_path.write_text(json.dumps(capture.to_dict(), indent=2))
            except OSError as e:
                logger.error(f"Failed to write capture metadata: {e}")

        with self._lock:
            self._rx_file = None
            self._rx_start_time = 0
            self._rx_bytes_written = 0
            self._rx_bursts = []
            self._rx_trigger_enabled = False
            self._rx_trigger_first_burst_start = None
            self._rx_trigger_last_burst_end = None
            self._rx_autostop_pending = False
            self._rx_modulation_hint = ""
            self._rx_modulation_confidence = 0.0
            self._rx_protocol_hint = ""
            self._rx_fingerprint_counts = {}

        self._emit(
            {
                "type": "status",
                "mode": "idle",
                "status": "stopped",
                "duration_seconds": round(duration, 1),
            }
        )

        result = {"status": "stopped", "duration_seconds": round(duration, 1)}
        if capture:
            result["capture"] = capture.to_dict()
        return result

    # ------------------------------------------------------------------
    # DECODE (hackrf_transfer piped to rtl_433)
    # ------------------------------------------------------------------

    def start_decode(
        self,
        frequency_hz: int,
        sample_rate: int = 2_000_000,
        lna_gain: int = 32,
        vga_gain: int = 20,
        decode_profile: str = "weather",
        device_serial: str | None = None,
    ) -> dict:
        # Pre-lock: tool availability & device detection (blocking I/O)
        hackrf_transfer_path = self._resolve_tool("hackrf_transfer")
        if not hackrf_transfer_path:
            return {"status": "error", "message": "hackrf_transfer not found"}
        rtl433_path = self._resolve_tool("rtl_433")
        if not rtl433_path:
            return {"status": "error", "message": "rtl_433 not found"}
        device_err = self._require_hackrf_device()
        if device_err:
            return {"status": "error", "message": device_err}

        with self._lock:
            if self.active_mode != "idle":
                return {"status": "error", "message": f"Already running: {self.active_mode}"}

            # Keep decode bandwidth conservative for stability. 2 Msps is enough
            # for common SubGHz protocols while staying within HackRF support.
            requested_sample_rate = int(sample_rate)
            stable_sample_rate = max(2_000_000, min(2_000_000, requested_sample_rate))

            # Build hackrf_transfer command (producer: raw IQ to stdout)
            hackrf_cmd = [
                hackrf_transfer_path,
                "-r",
                "-",
                "-f",
                str(frequency_hz),
                "-s",
                str(stable_sample_rate),
                "-l",
                str(max(SUBGHZ_LNA_GAIN_MIN, min(SUBGHZ_LNA_GAIN_MAX, lna_gain))),
                "-g",
                str(max(SUBGHZ_VGA_GAIN_MIN, min(SUBGHZ_VGA_GAIN_MAX, vga_gain))),
            ]
            if device_serial:
                hackrf_cmd.extend(["-d", device_serial])

            # Build rtl_433 command (consumer: reads IQ from stdin)
            # Feed signed 8-bit complex IQ directly from hackrf_transfer.
            rtl433_cmd = [
                rtl433_path,
                "-r",
                "cs8:-",
                "-s",
                str(stable_sample_rate),
                "-f",
                str(frequency_hz),
                "-F",
                "json",
                "-F",
                "log",
                "-M",
                "level",
                "-M",
                "noise:5",
                "-Y",
                "autolevel",
                "-Y",
                "ampest",
                "-Y",
                "minsnr=2.5",
            ]
            profile = (decode_profile or "weather").strip().lower()
            if profile == "weather":
                # Limit decoder set to weather/temperature/humidity/rain/wind
                # protocols for better sensitivity and lower CPU load.
                weather_protocol_ids = [
                    2,
                    3,
                    8,
                    12,
                    16,
                    18,
                    19,
                    20,
                    31,
                    32,
                    34,
                    40,
                    47,
                    50,
                    52,
                    54,
                    55,
                    56,
                    57,
                    69,
                    73,
                    74,
                    75,
                    76,
                    78,
                    79,
                    85,
                    91,
                    92,
                    108,
                    109,
                    111,
                    112,
                    113,
                    119,
                    120,
                    124,
                    127,
                    132,
                    133,
                    134,
                    138,
                    141,
                    143,
                    144,
                    145,
                    146,
                    147,
                    152,
                    153,
                    157,
                    158,
                    163,
                    165,
                    166,
                    170,
                    171,
                    172,
                    173,
                    175,
                    182,
                    183,
                    184,
                    194,
                    195,
                    196,
                    205,
                    206,
                    213,
                    214,
                    215,
                    217,
                    219,
                    221,
                    222,
                ]
                rtl433_cmd.extend(["-R", "0"])
                for proto_id in weather_protocol_ids:
                    rtl433_cmd.extend(["-R", str(proto_id)])
            else:
                profile = "all"

            logger.info(f"SubGHz decode: {' '.join(hackrf_cmd)} | {' '.join(rtl433_cmd)}")

            try:
                # Start hackrf_transfer (producer). stderr is consumed by a
                # dedicated monitor thread so we can surface stream failures.
                hackrf_proc = subprocess.Popen(
                    hackrf_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                register_process(hackrf_proc)

                # Start rtl_433 (consumer)
                rtl433_proc = subprocess.Popen(
                    rtl433_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                register_process(rtl433_proc)

                self._decode_hackrf_process = hackrf_proc
                self._decode_process = rtl433_proc
                self._decode_start_time = time.time()
                self._decode_frequency_hz = frequency_hz
                self._decode_sample_rate = stable_sample_rate
                self._decode_stop = False
                self._emit({"type": "info", "text": f"[decode] Profile: {profile}"})
                if requested_sample_rate != stable_sample_rate:
                    self._emit(
                        {
                            "type": "info",
                            "text": (
                                f"[decode] Using {stable_sample_rate} sps "
                                f"(requested {requested_sample_rate}) for stable live decode"
                            ),
                        }
                    )

                # Buffered relay: hackrf stdout → queue → rtl_433 stdin
                # with auto-restart when HackRF USB disconnects.
                iq_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=512)

                threading.Thread(
                    target=self._hackrf_reader,
                    args=(hackrf_cmd, rtl433_proc, iq_queue),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=self._monitor_decode_hackrf_stderr,
                    args=(hackrf_proc,),
                    daemon=True,
                ).start()

                threading.Thread(
                    target=self._rtl433_writer,
                    args=(rtl433_proc, iq_queue),
                    daemon=True,
                ).start()

                # Read decoded JSON output from rtl_433 stdout
                threading.Thread(
                    target=self._read_decode_output,
                    daemon=True,
                ).start()

                # Monitor rtl_433 stderr
                threading.Thread(
                    target=self._monitor_decode_stderr,
                    daemon=True,
                ).start()

                self._emit(
                    {
                        "type": "status",
                        "mode": "decode",
                        "status": "started",
                        "frequency_hz": frequency_hz,
                        "sample_rate": stable_sample_rate,
                    }
                )

                return {
                    "status": "started",
                    "frequency_hz": frequency_hz,
                    "sample_rate": stable_sample_rate,
                }

            except FileNotFoundError as e:
                if self._decode_hackrf_process:
                    safe_terminate(self._decode_hackrf_process)
                    unregister_process(self._decode_hackrf_process)
                    self._decode_hackrf_process = None
                return {"status": "error", "message": f"Tool not found: {e.filename or 'unknown'}"}
            except Exception as e:
                for proc in (self._decode_hackrf_process, self._decode_process):
                    if proc:
                        safe_terminate(proc)
                        unregister_process(proc)
                self._decode_hackrf_process = None
                self._decode_process = None
                logger.error(f"Failed to start decode: {e}")
                return {"status": "error", "message": str(e)}

    def _hackrf_reader(
        self,
        hackrf_cmd: list[str],
        rtl433_proc: subprocess.Popen,
        iq_queue: queue.Queue,
    ) -> None:
        """Read IQ from hackrf_transfer stdout into a queue, restarting on USB drops.

        Decouples HackRF USB reads from rtl_433 stdin writes so that any stall
        in rtl_433 cannot back-pressure the USB transfer, which on macOS causes
        the device to disconnect.

        Uses os.read() on the raw fd to drain the pipe immediately (no Python
        buffering), minimising backpressure on the USB transfer path.
        """
        CHUNK = 65536  # 64 KB read size for lower latency
        RESTART_DELAY = 0.15  # seconds before restart attempt
        MAX_RESTARTS = 3600  # allow longer sessions
        MAX_QUICK_RESTARTS = 6
        QUICK_RESTART_WINDOW = 20.0

        restart_times: list[float] = []
        first_chunk = True

        restarts = 0
        while not self._decode_stop:
            if rtl433_proc.poll() is not None:
                break
            if self._decode_process is not rtl433_proc:
                break

            hackrf_proc = self._decode_hackrf_process
            src = hackrf_proc.stdout if hackrf_proc else None

            if not src or (hackrf_proc and hackrf_proc.poll() is not None):
                if restarts >= MAX_RESTARTS:
                    logger.error("hackrf_transfer: max restarts reached")
                    self._emit({"type": "error", "message": "HackRF: max restarts reached"})
                    break

                # Unregister the dead process before restarting
                if hackrf_proc:
                    unregister_process(hackrf_proc)

                time.sleep(RESTART_DELAY)

                # Re-check stop conditions after sleeping
                if self._decode_stop:
                    break
                if rtl433_proc.poll() is not None:
                    break
                if self._decode_process is not rtl433_proc:
                    break

                with self._lock:
                    if self._decode_stop:
                        break
                    try:
                        hackrf_proc = subprocess.Popen(
                            hackrf_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            bufsize=0,
                        )
                        register_process(hackrf_proc)
                        self._decode_hackrf_process = hackrf_proc
                        src = hackrf_proc.stdout
                        restarts += 1
                        now = time.time()
                        restart_times.append(now)
                        restart_times = [t for t in restart_times if (now - t) <= QUICK_RESTART_WINDOW]
                        if len(restart_times) >= MAX_QUICK_RESTARTS:
                            self._emit(
                                {
                                    "type": "error",
                                    "message": (
                                        "HackRF stream is unstable (restarting repeatedly). "
                                        "Try lower gain/sample-rate or reconnect the device."
                                    ),
                                }
                            )
                            break
                        logger.info(f"hackrf_transfer restarted ({restarts})")
                        self._emit({"type": "info", "text": f"[decode] HackRF stream restarted ({restarts})"})
                        threading.Thread(
                            target=self._monitor_decode_hackrf_stderr,
                            args=(hackrf_proc,),
                            daemon=True,
                        ).start()
                    except Exception as e:
                        logger.error(f"Failed to restart hackrf_transfer: {e}")
                        self._emit(
                            {
                                "type": "error",
                                "message": f"Failed to restart hackrf_transfer: {e}",
                            }
                        )
                        break

            if not src:
                break

            # Use raw fd reads to drain the pipe without Python buffering.
            # This returns immediately with whatever bytes are available
            # (up to CHUNK), avoiding the backpressure that buffered reads
            # can cause when they block waiting for a full chunk.
            try:
                fd = src.fileno()
                if not isinstance(fd, int) or fd < 0:
                    logger.error("Invalid file descriptor from hackrf stdout")
                    break
            except (OSError, ValueError, TypeError):
                break

            try:
                while not self._decode_stop:
                    data = os.read(fd, CHUNK)
                    if not data:
                        if hackrf_proc and hackrf_proc.poll() is not None:
                            self._emit({"type": "info", "text": "[decode] HackRF stream stopped"})
                        break
                    if first_chunk:
                        first_chunk = False
                        self._emit({"type": "info", "text": "[decode] IQ source active"})
                    try:
                        iq_queue.put_nowait(data)
                    except queue.Full:
                        # Drop oldest chunk to prevent backpressure
                        logger.debug("IQ queue full, dropping oldest chunk")
                        with contextlib.suppress(queue.Empty):
                            iq_queue.get_nowait()
                        with contextlib.suppress(queue.Full):
                            iq_queue.put_nowait(data)
            except OSError:
                pass

        # Signal writer to stop
        with contextlib.suppress(queue.Full):
            iq_queue.put_nowait(None)

    def _rtl433_writer(
        self,
        rtl433_proc: subprocess.Popen,
        iq_queue: queue.Queue,
    ) -> None:
        """Drain the IQ queue into rtl_433 stdin."""
        dst = rtl433_proc.stdin
        if not dst:
            logger.error("rtl_433 stdin is None — cannot write IQ data")
            return

        first_chunk = True
        last_level = 0.0
        last_wave = 0.0
        last_spectrum = 0.0
        last_stats = time.time()
        bytes_since_stats = 0
        LEVEL_INTERVAL = 0.35
        WAVE_INTERVAL = 0.5
        SPECTRUM_INTERVAL = 0.55
        STATS_INTERVAL = 6.0
        writes_since_flush = 0
        burst_active = False
        burst_start = 0.0
        burst_last_high = 0.0
        burst_peak = 0
        BURST_ON_LEVEL = 9
        BURST_OFF_HOLD = 0.45
        BURST_MIN_DURATION = 0.05
        try:
            while True:
                try:
                    data = iq_queue.get(timeout=2.0)
                except queue.Empty:
                    if rtl433_proc.poll() is not None:
                        break
                    continue
                if data is None:
                    break

                now = time.time()
                bytes_since_stats += len(data)

                if now - last_level >= LEVEL_INTERVAL:
                    level = self._compute_rx_level(data)
                    self._emit({"type": "decode_level", "level": level})
                    if level >= BURST_ON_LEVEL:
                        burst_last_high = now
                        if not burst_active:
                            burst_active = True
                            burst_start = now
                            burst_peak = level
                            self._emit(
                                {
                                    "type": "rx_burst",
                                    "mode": "decode",
                                    "event": "start",
                                    "start_offset_s": round(max(0.0, now - self._decode_start_time), 3),
                                    "level": int(level),
                                }
                            )
                        else:
                            burst_peak = max(burst_peak, level)
                    elif burst_active and (now - burst_last_high) >= BURST_OFF_HOLD:
                        duration = now - burst_start
                        if duration >= BURST_MIN_DURATION:
                            self._emit(
                                {
                                    "type": "rx_burst",
                                    "mode": "decode",
                                    "event": "end",
                                    "start_offset_s": round(max(0.0, burst_start - self._decode_start_time), 3),
                                    "duration_ms": int(duration * 1000),
                                    "peak_level": int(burst_peak),
                                }
                            )
                        burst_active = False
                        burst_peak = 0
                    last_level = now

                if now - last_wave >= WAVE_INTERVAL:
                    samples = self._extract_waveform(data, points=160)
                    if samples:
                        self._emit({"type": "decode_waveform", "samples": samples})
                    last_wave = now

                if now - last_spectrum >= SPECTRUM_INTERVAL:
                    bins = self._compute_rx_spectrum(data, bins=128)
                    if bins:
                        self._emit({"type": "decode_spectrum", "bins": bins})
                    last_spectrum = now

                # Pass HackRF cs8 IQ bytes through directly.
                dst.write(data)
                writes_since_flush += 1
                if writes_since_flush >= 8:
                    dst.flush()
                    writes_since_flush = 0

                if first_chunk:
                    first_chunk = False
                    logger.info(f"IQ data flowing to rtl_433 ({len(data)} bytes)")
                    self._emit(
                        {
                            "type": "info",
                            "text": "[decode] Receiving IQ data from HackRF...",
                        }
                    )

                elapsed = now - last_stats
                if elapsed >= STATS_INTERVAL:
                    rate_kb = bytes_since_stats / elapsed / 1024
                    self._emit(
                        {
                            "type": "info",
                            "text": f"[decode] IQ: {rate_kb:.0f} KB/s — listening for signals...",
                        }
                    )
                    self._emit(
                        {
                            "type": "decode_raw",
                            "text": f"IQ stream active: {rate_kb:.0f} KB/s",
                        }
                    )
                    bytes_since_stats = 0
                    last_stats = now

        except (BrokenPipeError, OSError) as e:
            logger.debug(f"rtl_433 writer pipe closed: {e}")
            self._emit({"type": "info", "text": f"[decode] Writer pipe closed: {e}"})
        except Exception as e:
            logger.error(f"rtl_433 writer error: {e}")
            self._emit({"type": "error", "message": f"Decode writer error: {e}"})
        finally:
            if burst_active:
                duration = max(0.0, time.time() - burst_start)
                if duration >= BURST_MIN_DURATION:
                    self._emit(
                        {
                            "type": "rx_burst",
                            "mode": "decode",
                            "event": "end",
                            "start_offset_s": round(max(0.0, burst_start - self._decode_start_time), 3),
                            "duration_ms": int(duration * 1000),
                            "peak_level": int(burst_peak),
                        }
                    )
            with contextlib.suppress(OSError):
                dst.close()

    def _read_decode_output(self) -> None:
        process = self._decode_process
        if not process or not process.stdout:
            return
        got_output = False
        try:
            for line in iter(process.stdout.readline, b""):
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if not got_output:
                    got_output = True
                    logger.info("rtl_433 producing output")
                try:
                    data = json.loads(text)
                    data["type"] = "decode"
                    self._emit(data)
                except json.JSONDecodeError:
                    self._emit({"type": "decode_raw", "text": text})
        except Exception as e:
            logger.error(f"Error reading decode output: {e}")
        finally:
            rc = process.poll()
            unregister_process(process)
            if rc is not None and rc != 0 and rc != -15:
                logger.warning(f"rtl_433 exited with code {rc}")
                self._emit(
                    {
                        "type": "info",
                        "text": f"[rtl_433] Exited with code {rc}",
                    }
                )
            with self._lock:
                if self._decode_process is process:
                    self._decode_process = None
                    self._decode_frequency_hz = 0
                    self._decode_sample_rate = 0
                    self._decode_start_time = 0
            self._emit(
                {
                    "type": "status",
                    "mode": "idle",
                    "status": "decode_stopped",
                }
            )

    def _monitor_decode_hackrf_stderr(self, process: subprocess.Popen) -> None:
        if not process or not process.stderr:
            return
        fatal_disconnect_emitted = False
        try:
            for line in iter(process.stderr.readline, b""):
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                logger.debug(f"[hackrf_decode] {text}")
                lower = text.lower()
                if not fatal_disconnect_emitted and (
                    "no such device" in lower or "device not found" in lower or "disconnected" in lower
                ):
                    fatal_disconnect_emitted = True
                    self._hackrf_device_cache = False
                    self._hackrf_device_cache_ts = time.time()
                    self._decode_stop = True
                    self._emit(
                        {
                            "type": "error",
                            "message": (
                                "HackRF disconnected during decode. Reconnect the device, then press Start again."
                            ),
                        }
                    )
                if (
                    "error" in lower
                    or "usb" in lower
                    or "overflow" in lower
                    or "underflow" in lower
                    or "failed" in lower
                    or "couldn" in lower
                    or "transfer" in lower
                ):
                    self._emit({"type": "info", "text": f"[hackrf] {text}"})
        except Exception:
            pass

    def _monitor_decode_stderr(self) -> None:
        process = self._decode_process
        if not process or not process.stderr:
            return
        decode_keywords = (
            "pulse",
            "sync",
            "message",
            "decoded",
            "snr",
            "rssi",
            "level",
            "modulation",
            "bitbuffer",
            "symbol",
            "short",
            "noise",
            "detected",
        )
        try:
            for line in iter(process.stderr.readline, b""):
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug(f"[rtl_433] {text}")
                    self._emit({"type": "info", "text": f"[rtl_433] {text}"})
                    if any(k in text.lower() for k in decode_keywords):
                        self._emit({"type": "decode_raw", "text": text})
        except Exception:
            pass

    def stop_decode(self) -> dict:
        hackrf_proc: subprocess.Popen | None = None
        rtl433_proc: subprocess.Popen | None = None

        with self._lock:
            hackrf_running = self._decode_hackrf_process and self._decode_hackrf_process.poll() is None
            rtl433_running = self._decode_process and self._decode_process.poll() is None

            if not hackrf_running and not rtl433_running:
                return {"status": "not_running"}

            # Signal reader thread to stop before killing processes,
            # preventing it from spawning a new hackrf_transfer during cleanup.
            self._decode_stop = True

            # Grab process refs and clear state inside lock
            hackrf_proc = self._decode_hackrf_process
            self._decode_hackrf_process = None
            rtl433_proc = self._decode_process
            self._decode_process = None

            self._decode_frequency_hz = 0
            self._decode_sample_rate = 0
            self._decode_start_time = 0

        # Terminate outside lock — upstream (hackrf_transfer) first, then consumer (rtl_433)
        if hackrf_proc:
            safe_terminate(hackrf_proc)
            unregister_process(hackrf_proc)
        if rtl433_proc:
            safe_terminate(rtl433_proc)
            unregister_process(rtl433_proc)

        # Clean up any hackrf_transfer spawned during the race window
        time.sleep(0.1)
        race_proc: subprocess.Popen | None = None
        with self._lock:
            if self._decode_hackrf_process:
                race_proc = self._decode_hackrf_process
                self._decode_hackrf_process = None
        if race_proc:
            safe_terminate(race_proc)
            unregister_process(race_proc)

        self._emit(
            {
                "type": "status",
                "mode": "idle",
                "status": "stopped",
            }
        )

        return {"status": "stopped"}

    # ------------------------------------------------------------------
    # TRANSMIT (replay via hackrf_transfer -t)
    # ------------------------------------------------------------------

    @staticmethod
    def validate_tx_frequency(frequency_hz: int) -> str | None:
        """Validate that a frequency is within allowed ISM TX bands.

        Returns None if valid, or an error message if invalid.
        """
        freq_mhz = frequency_hz / 1_000_000
        for band_low, band_high in SUBGHZ_TX_ALLOWED_BANDS:
            if band_low <= freq_mhz <= band_high:
                return None
        bands_str = ", ".join(f"{lo}-{hi} MHz" for lo, hi in SUBGHZ_TX_ALLOWED_BANDS)
        return f"Frequency {freq_mhz:.3f} MHz is outside allowed TX bands: {bands_str}"

    @staticmethod
    def _estimate_capture_duration_seconds(capture: SubGhzCapture, file_size: int) -> float:
        if capture.duration_seconds and capture.duration_seconds > 0:
            return float(capture.duration_seconds)
        if capture.sample_rate > 0 and file_size > 0:
            return float(file_size) / float(capture.sample_rate * 2)
        return 0.0

    def _cleanup_tx_temp_file(self) -> None:
        path = self._tx_temp_file
        self._tx_temp_file = None
        if not path:
            return
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.debug(f"Failed to remove TX temp file {path}: {exc}")

    def transmit(
        self,
        capture_id: str,
        tx_gain: int = 20,
        max_duration: int = 10,
        start_seconds: float | None = None,
        duration_seconds: float | None = None,
        device_serial: str | None = None,
    ) -> dict:
        # Pre-lock: tool availability & device detection (blocking I/O)
        hackrf_transfer_path = self._resolve_tool("hackrf_transfer")
        if not hackrf_transfer_path:
            return {"status": "error", "message": "hackrf_transfer not found"}
        device_err = self._require_hackrf_device()
        if device_err:
            return {"status": "error", "message": device_err}

        # Pre-lock: capture lookup, validation, and segment I/O (can be large)
        capture = self._load_capture(capture_id)
        if not capture:
            return {"status": "error", "message": f"Capture not found: {capture_id}"}

        freq_error = self.validate_tx_frequency(capture.frequency_hz)
        if freq_error:
            return {"status": "error", "message": freq_error}

        tx_gain = max(SUBGHZ_TX_VGA_GAIN_MIN, min(SUBGHZ_TX_VGA_GAIN_MAX, tx_gain))
        max_duration = max(1, min(SUBGHZ_TX_MAX_DURATION, max_duration))

        iq_path = self._captures_dir / capture.filename
        if not iq_path.exists():
            return {"status": "error", "message": "IQ file missing"}

        # Build segment file outside lock (potentially megabytes of read/write)
        tx_path = iq_path
        segment_info = None
        segment_path_for_cleanup: Path | None = None
        if start_seconds is not None or duration_seconds is not None:
            try:
                start_s = max(0.0, float(start_seconds or 0.0))
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid start_seconds"}
            try:
                seg_s = None if duration_seconds is None else float(duration_seconds)
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid duration_seconds"}
            if seg_s is not None and seg_s <= 0:
                return {"status": "error", "message": "duration_seconds must be greater than 0"}

            file_size = iq_path.stat().st_size
            total_duration = self._estimate_capture_duration_seconds(capture, file_size)
            if total_duration <= 0:
                return {"status": "error", "message": "Unable to determine capture duration for segment TX"}
            if start_s >= total_duration:
                return {"status": "error", "message": "start_seconds is beyond end of capture"}

            end_s = total_duration if seg_s is None else min(total_duration, start_s + seg_s)
            if end_s <= start_s:
                return {"status": "error", "message": "Selected segment is empty"}

            bytes_per_second = max(2, int(capture.sample_rate) * 2)
            start_byte = int(start_s * bytes_per_second) & ~1
            end_byte = int(end_s * bytes_per_second) & ~1
            if end_byte <= start_byte:
                return {"status": "error", "message": "Selected segment is too short"}

            segment_size = end_byte - start_byte
            segment_name = f".txseg_{capture.capture_id}_{uuid.uuid4().hex[:8]}.iq"
            segment_path = self._captures_dir / segment_name
            segment_path_for_cleanup = segment_path
            try:
                with open(iq_path, "rb") as src, open(segment_path, "wb") as dst:
                    src.seek(start_byte)
                    remaining = segment_size
                    while remaining > 0:
                        chunk = src.read(min(262144, remaining))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)
                written = segment_path.stat().st_size if segment_path.exists() else 0
            except OSError as exc:
                logger.error(f"Failed to build TX segment: {exc}")
                return {"status": "error", "message": "Failed to create TX segment"}

            if written < 2:
                try:
                    segment_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                return {"status": "error", "message": "Selected TX segment has no IQ data"}

            tx_path = segment_path
            segment_info = {
                "start_seconds": round(start_s, 3),
                "duration_seconds": round(written / bytes_per_second, 3),
                "bytes": int(written),
            }

        with self._lock:
            if self.active_mode != "idle":
                # Clean up segment file if we prepared one
                if segment_path_for_cleanup:
                    try:
                        segment_path_for_cleanup.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                return {"status": "error", "message": f"Already running: {self.active_mode}"}

            # Clear any orphaned temp segment from a previous TX attempt.
            self._cleanup_tx_temp_file()
            if segment_path_for_cleanup:
                self._tx_temp_file = segment_path_for_cleanup

            cmd = [
                hackrf_transfer_path,
                "-t",
                str(tx_path),
                "-f",
                str(capture.frequency_hz),
                "-s",
                str(capture.sample_rate),
                "-x",
                str(tx_gain),
            ]
            if device_serial:
                cmd.extend(["-d", device_serial])

            logger.info(f"SubGHz TX: {' '.join(cmd)}")

            try:
                self._tx_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                register_process(self._tx_process)
                self._tx_start_time = time.time()
                self._tx_capture_id = capture_id

                # Start watchdog timer
                self._tx_watchdog = threading.Timer(max_duration, self._tx_watchdog_kill)
                self._tx_watchdog.daemon = True
                self._tx_watchdog.start()

                # Monitor TX process
                threading.Thread(
                    target=self._monitor_tx,
                    daemon=True,
                ).start()

                self._emit(
                    {
                        "type": "tx_status",
                        "status": "transmitting",
                        "capture_id": capture_id,
                        "frequency_hz": capture.frequency_hz,
                        "max_duration": max_duration,
                        "segment": segment_info,
                    }
                )

                return {
                    "status": "transmitting",
                    "capture_id": capture_id,
                    "frequency_hz": capture.frequency_hz,
                    "max_duration": max_duration,
                    "segment": segment_info,
                }

            except FileNotFoundError:
                self._cleanup_tx_temp_file()
                return {"status": "error", "message": "hackrf_transfer not found"}
            except Exception as e:
                self._cleanup_tx_temp_file()
                logger.error(f"Failed to start TX: {e}")
                return {"status": "error", "message": str(e)}

    def _tx_watchdog_kill(self) -> None:
        """Kill TX process when max duration is exceeded."""
        logger.warning("SubGHz TX watchdog triggered - killing transmission")
        self.stop_transmit()

    def _monitor_tx(self) -> None:
        process = self._tx_process
        if not process:
            return
        try:
            returncode = process.wait()
        except Exception:
            returncode = -1
        with self._lock:
            # Only emit if this is still the active TX process
            if self._tx_process is not process:
                return
            unregister_process(process)
            duration = time.time() - self._tx_start_time if self._tx_start_time else 0
            if returncode and returncode != 0 and returncode != -15:
                # Non-zero exit (not SIGTERM) means unexpected death
                logger.warning(f"hackrf_transfer TX exited unexpectedly (rc={returncode})")
                self._emit(
                    {
                        "type": "error",
                        "message": f"Transmission failed (hackrf_transfer exited with code {returncode})",
                    }
                )
            self._tx_process = None
            self._tx_start_time = 0
            self._tx_capture_id = ""
            self._emit(
                {
                    "type": "tx_status",
                    "status": "tx_complete",
                    "duration_seconds": round(duration, 1),
                }
            )
            if self._tx_watchdog:
                self._tx_watchdog.cancel()
                self._tx_watchdog = None
            self._cleanup_tx_temp_file()

    def stop_transmit(self) -> dict:
        proc_to_terminate: subprocess.Popen | None = None
        with self._lock:
            if self._tx_watchdog:
                self._tx_watchdog.cancel()
                self._tx_watchdog = None

            if not self._tx_process or self._tx_process.poll() is not None:
                self._cleanup_tx_temp_file()
                return {"status": "not_running"}

            proc_to_terminate = self._tx_process
            self._tx_process = None
            duration = time.time() - self._tx_start_time if self._tx_start_time else 0
            self._tx_start_time = 0
            self._tx_capture_id = ""
            self._cleanup_tx_temp_file()

        # Terminate outside lock to avoid blocking other operations
        if proc_to_terminate:
            safe_terminate(proc_to_terminate)
            unregister_process(proc_to_terminate)

        self._emit(
            {
                "type": "tx_status",
                "status": "tx_stopped",
                "duration_seconds": round(duration, 1),
            }
        )

        return {"status": "stopped", "duration_seconds": round(duration, 1)}

    # ------------------------------------------------------------------
    # SWEEP (hackrf_sweep)
    # ------------------------------------------------------------------

    def start_sweep(
        self,
        freq_start_mhz: float = 300.0,
        freq_end_mhz: float = 928.0,
        bin_width: int = 100000,
        device_serial: str | None = None,
    ) -> dict:
        # Pre-lock: tool availability & device detection (blocking I/O)
        hackrf_sweep_path = self._resolve_tool("hackrf_sweep")
        if not hackrf_sweep_path:
            return {"status": "error", "message": "hackrf_sweep not found"}
        device_err = self._require_hackrf_device()
        if device_err:
            return {"status": "error", "message": device_err}

        # Wait for previous sweep thread to exit (blocking) before lock
        if self._sweep_thread and self._sweep_thread.is_alive():
            self._sweep_thread.join(timeout=2.0)
            if self._sweep_thread.is_alive():
                return {"status": "error", "message": "Previous sweep still shutting down"}

        with self._lock:
            if self.active_mode != "idle":
                return {"status": "error", "message": f"Already running: {self.active_mode}"}

            cmd = [
                hackrf_sweep_path,
                "-f",
                f"{int(freq_start_mhz)}:{int(freq_end_mhz)}",
                "-w",
                str(bin_width),
            ]
            if device_serial:
                cmd.extend(["-d", device_serial])

            logger.info(f"SubGHz sweep: {' '.join(cmd)}")

            try:
                self._sweep_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                register_process(self._sweep_process)
                self._sweep_running = True

                # Sweep reader with auto-restart on USB drops
                self._sweep_thread = threading.Thread(
                    target=self._sweep_loop,
                    args=(cmd,),
                    daemon=True,
                )
                self._sweep_thread.start()

                self._emit(
                    {
                        "type": "status",
                        "mode": "sweep",
                        "status": "started",
                        "freq_start_mhz": freq_start_mhz,
                        "freq_end_mhz": freq_end_mhz,
                    }
                )

                return {
                    "status": "started",
                    "freq_start_mhz": freq_start_mhz,
                    "freq_end_mhz": freq_end_mhz,
                }

            except FileNotFoundError:
                return {"status": "error", "message": "hackrf_sweep not found"}
            except Exception as e:
                logger.error(f"Failed to start sweep: {e}")
                return {"status": "error", "message": str(e)}

    def _sweep_loop(self, cmd: list[str]) -> None:
        """Run hackrf_sweep with auto-restart on USB drops."""
        RESTART_DELAY = 0.5
        MAX_RESTARTS = 600

        restarts = 0
        while self._sweep_running:
            self._parse_sweep_stdout()

            # Process exited — restart if allowed
            if not self._sweep_running:
                break
            if restarts >= MAX_RESTARTS:
                logger.error("hackrf_sweep: max restarts reached")
                self._emit({"type": "error", "message": "HackRF sweep: max restarts reached"})
                break

            time.sleep(RESTART_DELAY)
            if not self._sweep_running:
                break

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                register_process(proc)
                self._sweep_process = proc
                restarts += 1
                logger.info(f"hackrf_sweep restarted ({restarts})")
            except Exception as e:
                logger.error(f"Failed to restart hackrf_sweep: {e}")
                break

        self._sweep_running = False
        self._emit(
            {
                "type": "status",
                "mode": "idle",
                "status": "sweep_stopped",
            }
        )

    def _parse_sweep_stdout(self) -> None:
        """Parse hackrf_sweep CSV output into SweepPoint events.

        hackrf_sweep CSV format:
        date, time, hz_low, hz_high, hz_bin_width, num_samples, dB, dB, dB, ...
        """
        process = self._sweep_process
        if not process or not process.stdout:
            return
        try:
            for line in iter(process.stdout.readline, b""):
                if not self._sweep_running:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    parts = text.split(",")
                    if len(parts) < 7:
                        continue
                    hz_low = float(parts[2].strip())
                    float(parts[3].strip())
                    hz_bin_width = float(parts[4].strip())
                    powers = [float(p.strip()) for p in parts[6:] if p.strip()]
                    if not powers or hz_bin_width <= 0:
                        continue

                    points = []
                    for i, power in enumerate(powers):
                        freq_hz = hz_low + i * hz_bin_width
                        points.append(
                            {
                                "freq": round(freq_hz / 1_000_000, 4),
                                "power": round(power, 1),
                            }
                        )

                    self._emit(
                        {
                            "type": "sweep",
                            "points": points,
                        }
                    )
                except Exception as exc:
                    logger.debug(f"Skipping malformed sweep line: {exc}")
                    continue
        except Exception as e:
            logger.error(f"Error reading sweep output: {e}")

    def stop_sweep(self) -> dict:
        proc_to_terminate: subprocess.Popen | None = None
        with self._lock:
            self._sweep_running = False
            if not self._sweep_process or self._sweep_process.poll() is not None:
                return {"status": "not_running"}

            proc_to_terminate = self._sweep_process
            self._sweep_process = None

        # Terminate outside lock to avoid blocking other operations
        if proc_to_terminate:
            safe_terminate(proc_to_terminate)
            unregister_process(proc_to_terminate)

        # Join sweep thread outside the lock to avoid blocking other operations
        if self._sweep_thread and self._sweep_thread.is_alive():
            self._sweep_thread.join(timeout=2.0)

        self._emit(
            {
                "type": "status",
                "mode": "idle",
                "status": "stopped",
            }
        )

        return {"status": "stopped"}

    # ------------------------------------------------------------------
    # CAPTURE LIBRARY
    # ------------------------------------------------------------------

    def list_captures(self) -> list[SubGhzCapture]:
        captures = []
        for meta_path in sorted(self._captures_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(meta_path.read_text())
                bursts = data.get("bursts", [])
                dominant_fingerprint = data.get("dominant_fingerprint", "")
                if not dominant_fingerprint and isinstance(bursts, list):
                    fp_counts: dict[str, int] = {}
                    for burst in bursts:
                        fp = ""
                        if isinstance(burst, dict):
                            fp = str(burst.get("fingerprint") or "").strip()
                        if not fp:
                            continue
                        fp_counts[fp] = fp_counts.get(fp, 0) + 1
                    if fp_counts:
                        dominant_fingerprint = max(fp_counts, key=fp_counts.get)
                captures.append(
                    SubGhzCapture(
                        capture_id=data["id"],
                        filename=data["filename"],
                        frequency_hz=data["frequency_hz"],
                        sample_rate=data["sample_rate"],
                        lna_gain=data.get("lna_gain", 0),
                        vga_gain=data.get("vga_gain", 0),
                        timestamp=data["timestamp"],
                        duration_seconds=data.get("duration_seconds", 0),
                        size_bytes=data.get("size_bytes", 0),
                        label=data.get("label", ""),
                        label_source=data.get("label_source", ""),
                        decoded_protocols=data.get("decoded_protocols", []),
                        bursts=bursts,
                        modulation_hint=data.get("modulation_hint", ""),
                        modulation_confidence=data.get("modulation_confidence", 0.0),
                        protocol_hint=data.get("protocol_hint", ""),
                        dominant_fingerprint=dominant_fingerprint,
                        fingerprint_group=data.get("fingerprint_group", ""),
                        fingerprint_group_size=data.get("fingerprint_group_size", 0),
                        trigger_enabled=bool(data.get("trigger_enabled", False)),
                        trigger_pre_seconds=data.get("trigger_pre_seconds", 0.0),
                        trigger_post_seconds=data.get("trigger_post_seconds", 0.0),
                    )
                )
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.debug(f"Skipping invalid capture metadata {meta_path}: {e}")

        # Auto-group repeated fingerprints as likely same button/device clusters.
        fingerprint_groups: dict[str, list[SubGhzCapture]] = {}
        for capture in captures:
            fp = (capture.dominant_fingerprint or "").strip().lower()
            if not fp:
                continue
            fingerprint_groups.setdefault(fp, []).append(capture)
        for fp, grouped in fingerprint_groups.items():
            group_id = f"SIG-{fp[:6].upper()}"
            for capture in grouped:
                capture.fingerprint_group = group_id
                capture.fingerprint_group_size = len(grouped)

        return captures

    def _load_capture(self, capture_id: str) -> SubGhzCapture | None:
        for meta_path in self._captures_dir.glob("*.json"):
            try:
                data = json.loads(meta_path.read_text())
                if data.get("id") == capture_id:
                    bursts = data.get("bursts", [])
                    dominant_fingerprint = data.get("dominant_fingerprint", "")
                    if not dominant_fingerprint and isinstance(bursts, list):
                        fp_counts: dict[str, int] = {}
                        for burst in bursts:
                            fp = ""
                            if isinstance(burst, dict):
                                fp = str(burst.get("fingerprint") or "").strip()
                            if not fp:
                                continue
                            fp_counts[fp] = fp_counts.get(fp, 0) + 1
                        if fp_counts:
                            dominant_fingerprint = max(fp_counts, key=fp_counts.get)
                    return SubGhzCapture(
                        capture_id=data["id"],
                        filename=data["filename"],
                        frequency_hz=data["frequency_hz"],
                        sample_rate=data["sample_rate"],
                        lna_gain=data.get("lna_gain", 0),
                        vga_gain=data.get("vga_gain", 0),
                        timestamp=data["timestamp"],
                        duration_seconds=data.get("duration_seconds", 0),
                        size_bytes=data.get("size_bytes", 0),
                        label=data.get("label", ""),
                        label_source=data.get("label_source", ""),
                        decoded_protocols=data.get("decoded_protocols", []),
                        bursts=bursts,
                        modulation_hint=data.get("modulation_hint", ""),
                        modulation_confidence=data.get("modulation_confidence", 0.0),
                        protocol_hint=data.get("protocol_hint", ""),
                        dominant_fingerprint=dominant_fingerprint,
                        fingerprint_group=data.get("fingerprint_group", ""),
                        fingerprint_group_size=data.get("fingerprint_group_size", 0),
                        trigger_enabled=bool(data.get("trigger_enabled", False)),
                        trigger_pre_seconds=data.get("trigger_pre_seconds", 0.0),
                        trigger_post_seconds=data.get("trigger_post_seconds", 0.0),
                    )
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return None

    def get_capture(self, capture_id: str) -> SubGhzCapture | None:
        return self._load_capture(capture_id)

    def get_capture_path(self, capture_id: str) -> Path | None:
        capture = self._load_capture(capture_id)
        if not capture:
            return None
        path = self._captures_dir / capture.filename
        if path.exists():
            return path
        return None

    def trim_capture(
        self,
        capture_id: str,
        start_seconds: float | None = None,
        duration_seconds: float | None = None,
        label: str = "",
    ) -> dict:
        """Create a trimmed capture from a selected IQ time window.

        If start/duration are omitted and burst markers exist, the strongest burst
        window is selected automatically with short padding.
        """
        with self._lock:
            if self.active_mode != "idle":
                return {"status": "error", "message": f"Already running: {self.active_mode}"}

            capture = self._load_capture(capture_id)
            if not capture:
                return {"status": "error", "message": f"Capture not found: {capture_id}"}

            src_path = self._captures_dir / capture.filename
            if not src_path.exists():
                return {"status": "error", "message": "IQ file missing"}

            try:
                src_size = src_path.stat().st_size
            except OSError:
                return {"status": "error", "message": "Unable to read capture file"}
            if src_size < 2:
                return {"status": "error", "message": "Capture file has no IQ data"}

            total_duration = self._estimate_capture_duration_seconds(capture, src_size)
            if total_duration <= 0:
                return {"status": "error", "message": "Unable to determine capture duration"}

            use_auto_burst = start_seconds is None and duration_seconds is None
            auto_pad = 0.06
            if use_auto_burst:
                bursts = capture.bursts if isinstance(capture.bursts, list) else []
                best_burst: dict | None = None
                for burst in bursts:
                    if not isinstance(burst, dict):
                        continue
                    dur = float(burst.get("duration_seconds", 0.0) or 0.0)
                    if dur <= 0:
                        continue
                    if best_burst is None:
                        best_burst = burst
                        continue
                    best_peak = float(best_burst.get("peak_level", 0.0) or 0.0)
                    cur_peak = float(burst.get("peak_level", 0.0) or 0.0)
                    if (
                        cur_peak > best_peak
                        or cur_peak == best_peak
                        and dur > float(best_burst.get("duration_seconds", 0.0) or 0.0)
                    ):
                        best_burst = burst

                if best_burst:
                    burst_start = max(0.0, float(best_burst.get("start_seconds", 0.0) or 0.0))
                    burst_dur = max(0.0, float(best_burst.get("duration_seconds", 0.0) or 0.0))
                    start_seconds = max(0.0, burst_start - auto_pad)
                    end_seconds = min(total_duration, burst_start + burst_dur + auto_pad)
                    duration_seconds = max(0.0, end_seconds - start_seconds)
                else:
                    return {
                        "status": "error",
                        "message": "No burst markers available. Select a segment manually before trimming.",
                    }

            try:
                start_s = max(0.0, float(start_seconds or 0.0))
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid start_seconds"}
            try:
                seg_s = None if duration_seconds is None else float(duration_seconds)
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid duration_seconds"}

            if seg_s is not None and seg_s <= 0:
                return {"status": "error", "message": "duration_seconds must be greater than 0"}
            if start_s >= total_duration:
                return {"status": "error", "message": "start_seconds is beyond end of capture"}

            end_s = total_duration if seg_s is None else min(total_duration, start_s + seg_s)
            if end_s <= start_s:
                return {"status": "error", "message": "Selected segment is empty"}

            bytes_per_second = max(2, int(capture.sample_rate) * 2)
            start_byte = int(start_s * bytes_per_second) & ~1
            end_byte = int(end_s * bytes_per_second) & ~1
            if end_byte <= start_byte:
                return {"status": "error", "message": "Selected segment is too short"}

            trim_size = end_byte - start_byte
            source_stem = Path(capture.filename).stem
            trim_name = f"{source_stem}_trim_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}.iq"
            trim_path = self._captures_dir / trim_name
            try:
                with open(src_path, "rb") as src, open(trim_path, "wb") as dst:
                    src.seek(start_byte)
                    remaining = trim_size
                    while remaining > 0:
                        chunk = src.read(min(262144, remaining))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)
                written = trim_path.stat().st_size if trim_path.exists() else 0
            except OSError as exc:
                logger.error(f"Failed to create trimmed capture: {exc}")
                try:
                    trim_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                return {"status": "error", "message": "Failed to write trimmed capture"}

            if written < 2:
                try:
                    trim_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                return {"status": "error", "message": "Trimmed capture has no IQ data"}

            trimmed_duration = round(written / bytes_per_second, 3)

            adjusted_bursts: list[dict] = []
            if isinstance(capture.bursts, list):
                for burst in capture.bursts:
                    if not isinstance(burst, dict):
                        continue
                    burst_start = max(0.0, float(burst.get("start_seconds", 0.0) or 0.0))
                    burst_dur = max(0.0, float(burst.get("duration_seconds", 0.0) or 0.0))
                    burst_end = burst_start + burst_dur
                    overlap_start = max(start_s, burst_start)
                    overlap_end = min(end_s, burst_end)
                    overlap_dur = overlap_end - overlap_start
                    if overlap_dur <= 0:
                        continue
                    adjusted = dict(burst)
                    adjusted["start_seconds"] = round(overlap_start - start_s, 3)
                    adjusted["duration_seconds"] = round(overlap_dur, 3)
                    adjusted_bursts.append(adjusted)

            dominant_fingerprint = ""
            fp_counts: dict[str, int] = {}
            for burst in adjusted_bursts:
                fp = str(burst.get("fingerprint") or "").strip()
                if not fp:
                    continue
                fp_counts[fp] = fp_counts.get(fp, 0) + 1
            if fp_counts:
                dominant_fingerprint = max(fp_counts, key=fp_counts.get)
            elif capture.dominant_fingerprint:
                dominant_fingerprint = capture.dominant_fingerprint

            modulation_hint = capture.modulation_hint
            modulation_confidence = float(capture.modulation_confidence or 0.0)
            if adjusted_bursts:
                hint_totals: dict[str, float] = {}
                for burst in adjusted_bursts:
                    hint = str(burst.get("modulation_hint") or "").strip()
                    conf = float(burst.get("modulation_confidence") or 0.0)
                    if not hint or hint.lower() == "unknown":
                        continue
                    hint_totals[hint] = hint_totals.get(hint, 0.0) + max(0.05, conf)
                if hint_totals:
                    modulation_hint = max(hint_totals, key=hint_totals.get)
                    total_score = max(sum(hint_totals.values()), 0.001)
                    modulation_confidence = min(0.98, hint_totals[modulation_hint] / total_score)

            protocol_hint = self._protocol_hint_from_capture(
                capture.frequency_hz,
                modulation_hint,
                len(adjusted_bursts),
            )

            manual_label = str(label or "").strip()
            if manual_label:
                capture_label = manual_label
                label_source = "manual"
            elif capture.label:
                capture_label = f"{capture.label} (Trim)"
                label_source = "auto"
            else:
                capture_label = (
                    self._auto_capture_label(
                        capture.frequency_hz,
                        len(adjusted_bursts),
                        modulation_hint,
                        protocol_hint,
                    )
                    + " (Trim)"
                )
                label_source = "auto"

            trimmed_capture = SubGhzCapture(
                capture_id=uuid.uuid4().hex[:12],
                filename=trim_path.name,
                frequency_hz=capture.frequency_hz,
                sample_rate=capture.sample_rate,
                lna_gain=capture.lna_gain,
                vga_gain=capture.vga_gain,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=round(trimmed_duration, 3),
                size_bytes=int(written),
                label=capture_label,
                label_source=label_source,
                decoded_protocols=list(capture.decoded_protocols),
                bursts=adjusted_bursts,
                modulation_hint=modulation_hint,
                modulation_confidence=round(modulation_confidence, 3),
                protocol_hint=protocol_hint,
                dominant_fingerprint=dominant_fingerprint,
                trigger_enabled=False,
                trigger_pre_seconds=0.0,
                trigger_post_seconds=0.0,
            )

            meta_path = trim_path.with_suffix(".json")
            try:
                meta_path.write_text(json.dumps(trimmed_capture.to_dict(), indent=2))
            except OSError as exc:
                logger.error(f"Failed to write trimmed capture metadata: {exc}")
                try:
                    trim_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                return {"status": "error", "message": "Failed to write trimmed capture metadata"}

            return {
                "status": "ok",
                "capture": trimmed_capture.to_dict(),
                "source_capture_id": capture_id,
                "segment": {
                    "start_seconds": round(start_s, 3),
                    "duration_seconds": round(trimmed_duration, 3),
                    "auto_selected": bool(use_auto_burst),
                },
            }

    def delete_capture(self, capture_id: str) -> bool:
        capture = self._load_capture(capture_id)
        if not capture:
            return False

        iq_path = self._captures_dir / capture.filename
        meta_path = iq_path.with_suffix(".json")

        deleted = False
        for path in (iq_path, meta_path):
            if path.exists():
                try:
                    path.unlink()
                    deleted = True
                except OSError as e:
                    logger.error(f"Failed to delete {path}: {e}")
        return deleted

    def update_capture_label(self, capture_id: str, label: str) -> bool:
        for meta_path in self._captures_dir.glob("*.json"):
            try:
                data = json.loads(meta_path.read_text())
                if data.get("id") == capture_id:
                    data["label"] = label
                    data["label_source"] = "manual" if label else data.get("label_source", "")
                    meta_path.write_text(json.dumps(data, indent=2))
                    return True
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return False

    # ------------------------------------------------------------------
    # STOP ALL
    # ------------------------------------------------------------------

    def stop_all(self) -> None:
        """Stop any running SubGHz process."""
        rx_thread: threading.Thread | None = None
        sweep_thread: threading.Thread | None = None
        rx_file_handle: BinaryIO | None = None

        with self._lock:
            self._decode_stop = True
            self._sweep_running = False
            self._rx_stop = True

            if self._tx_watchdog:
                self._tx_watchdog.cancel()
                self._tx_watchdog = None

            for proc_attr in (
                "_rx_process",
                "_decode_hackrf_process",
                "_decode_process",
                "_tx_process",
                "_sweep_process",
            ):
                process = getattr(self, proc_attr, None)
                if process and process.poll() is None:
                    safe_terminate(process)
                    unregister_process(process)
                setattr(self, proc_attr, None)

            rx_thread = self._rx_thread
            self._rx_thread = None
            sweep_thread = self._sweep_thread
            self._sweep_thread = None
            rx_file_handle = self._rx_file_handle
            self._rx_file_handle = None

            self._cleanup_tx_temp_file()
            self._rx_file = None
            self._tx_capture_id = ""

            self._rx_start_time = 0
            self._rx_bytes_written = 0
            self._rx_bursts = []
            self._rx_trigger_enabled = False
            self._rx_trigger_first_burst_start = None
            self._rx_trigger_last_burst_end = None
            self._rx_autostop_pending = False
            self._rx_modulation_hint = ""
            self._rx_modulation_confidence = 0.0
            self._rx_protocol_hint = ""
            self._rx_fingerprint_counts = {}
            self._tx_start_time = 0
            self._decode_start_time = 0
            self._decode_frequency_hz = 0
            self._decode_sample_rate = 0

        if rx_thread and rx_thread.is_alive():
            rx_thread.join(timeout=1.5)
        if sweep_thread and sweep_thread.is_alive():
            sweep_thread.join(timeout=1.5)

        if rx_file_handle:
            with contextlib.suppress(OSError):
                rx_file_handle.close()


# Global singleton
_manager: SubGhzManager | None = None
_manager_lock = threading.Lock()


def get_subghz_manager() -> SubGhzManager:
    """Get or create the global SubGhzManager singleton."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SubGhzManager()
    return _manager
