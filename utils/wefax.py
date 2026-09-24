"""WeFax (Weather Fax) decoder.

Decodes HF radiofax (weather fax) transmissions using any supported SDR
(RTL-SDR, HackRF, LimeSDR, Airspy, SDRPlay) via the SDRFactory
abstraction layer.  The decoder implements the standard WeFax AM protocol:
carrier 1900 Hz, deviation +/-400 Hz (black=1500, white=2300).

Pipeline: rtl_fm/rx_fm -M usb -> stdout PCM -> Python DSP state machine

State machine: SCANNING -> PHASING -> RECEIVING -> COMPLETE
"""

from __future__ import annotations

import base64
import contextlib
import errno
import io
import math
import os
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from utils.dependencies import get_tool_path
from utils.logging import get_logger
from utils.sdr import SDRFactory, SDRType

logger = get_logger("intercept.wefax")

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# WeFax protocol constants
# ---------------------------------------------------------------------------
CARRIER_FREQ = 1900.0  # Hz - center/carrier
BLACK_FREQ = 1500.0  # Hz - black level
WHITE_FREQ = 2300.0  # Hz - white level
START_TONE_FREQ = 300.0  # Hz - start tone
STOP_TONE_FREQ = 450.0  # Hz - stop tone
PHASING_FREQ = WHITE_FREQ  # White pulse during phasing

START_TONE_DURATION = 3.0  # Minimum seconds of start tone to detect
STOP_TONE_DURATION = 3.0  # Minimum seconds of stop tone to detect
PHASING_MIN_LINES = 5  # Minimum phasing lines before image

DEFAULT_SAMPLE_RATE = 22050
DEFAULT_IOC = 576
DEFAULT_LPM = 120


class DecoderState(Enum):
    """WeFax decoder state machine states."""

    SCANNING = "scanning"
    START_DETECTED = "start_detected"
    PHASING = "phasing"
    RECEIVING = "receiving"
    COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WeFaxImage:
    """Decoded WeFax image metadata."""

    filename: str
    path: Path
    station: str
    frequency_khz: float
    timestamp: datetime
    ioc: int
    lpm: int
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "path": str(self.path),
            "station": self.station,
            "frequency_khz": self.frequency_khz,
            "timestamp": self.timestamp.isoformat(),
            "ioc": self.ioc,
            "lpm": self.lpm,
            "size_bytes": self.size_bytes,
            "url": f"/wefax/images/{self.filename}",
        }


@dataclass
class WeFaxProgress:
    """WeFax decode progress update for SSE streaming."""

    status: str  # 'scanning', 'phasing', 'receiving', 'complete', 'error', 'stopped'
    station: str = ""
    message: str = ""
    progress_percent: int = 0
    line_count: int = 0
    image: WeFaxImage | None = None
    partial_image: str | None = None

    def to_dict(self) -> dict:
        result: dict = {
            "type": "wefax_progress",
            "status": self.status,
            "progress": self.progress_percent,
        }
        if self.station:
            result["station"] = self.station
        if self.message:
            result["message"] = self.message
        if self.line_count:
            result["line_count"] = self.line_count
        if self.image:
            result["image"] = self.image.to_dict()
        if self.partial_image:
            result["partial_image"] = self.partial_image
        return result


# ---------------------------------------------------------------------------
# DSP helpers (reuse Goertzel from SSTV where sensible)
# ---------------------------------------------------------------------------


def _goertzel_mag(samples: np.ndarray, target_freq: float, sample_rate: int) -> float:
    """Compute Goertzel magnitude at a single frequency."""
    n = len(samples)
    if n == 0:
        return 0.0
    w = 2.0 * math.pi * target_freq / sample_rate
    coeff = 2.0 * math.cos(w)
    s1 = 0.0
    s2 = 0.0
    for sample in samples:
        s0 = float(sample) + coeff * s1 - s2
        s2 = s1
        s1 = s0
    energy = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(max(0.0, energy))


def _freq_to_pixel(frequency: float) -> int:
    """Map WeFax audio frequency to pixel value (0=black, 255=white).

    Linear mapping: 1500 Hz -> 0 (black), 2300 Hz -> 255 (white).
    """
    normalized = (frequency - BLACK_FREQ) / (WHITE_FREQ - BLACK_FREQ)
    return max(0, min(255, int(normalized * 255 + 0.5)))


def _estimate_frequency(
    samples: np.ndarray, sample_rate: int, freq_low: float = 1200.0, freq_high: float = 2500.0
) -> float:
    """Estimate dominant frequency using coarse+fine Goertzel sweep."""
    if len(samples) == 0:
        return 0.0

    best_freq = freq_low
    best_energy = 0.0

    # Coarse sweep (25 Hz steps)
    freq = freq_low
    while freq <= freq_high:
        energy = _goertzel_mag(samples, freq, sample_rate) ** 2
        if energy > best_energy:
            best_energy = energy
            best_freq = freq
        freq += 25.0

    # Fine sweep around peak (+/- 25 Hz, 5 Hz steps)
    fine_low = max(freq_low, best_freq - 25.0)
    fine_high = min(freq_high, best_freq + 25.0)
    freq = fine_low
    while freq <= fine_high:
        energy = _goertzel_mag(samples, freq, sample_rate) ** 2
        if energy > best_energy:
            best_energy = energy
            best_freq = freq
        freq += 5.0

    return best_freq


def _detect_tone(samples: np.ndarray, target_freq: float, sample_rate: int, threshold: float = 3.0) -> bool:
    """Detect if a specific tone dominates the signal."""
    target_mag = _goertzel_mag(samples, target_freq, sample_rate)
    # Check against a few reference frequencies
    refs = [1000.0, 1500.0, 1900.0, 2300.0]
    refs = [f for f in refs if abs(f - target_freq) > 100]
    if not refs:
        return target_mag > 0.01
    avg_ref = sum(_goertzel_mag(samples, f, sample_rate) for f in refs) / len(refs)
    if avg_ref <= 0:
        return target_mag > 0.01
    return target_mag / avg_ref >= threshold


# ---------------------------------------------------------------------------
# WeFaxDecoder
# ---------------------------------------------------------------------------


class WeFaxDecoder:
    """WeFax decoder singleton.

    Manages SDR FM demod subprocess and decodes WeFax images using a
    state machine that detects start/stop tones, phasing signals, and
    demodulates image lines.
    """

    def __init__(self) -> None:
        self._sdr_process: subprocess.Popen | None = None
        self._running = False
        self._lock = threading.Lock()
        self._callback: Callable[[dict], None] | None = None
        self._last_scope_time: float = 0.0
        self._output_dir = Path("instance/wefax_images")
        self._images: list[WeFaxImage] = []
        self._decode_thread: threading.Thread | None = None

        # Current session parameters
        self._station = ""
        self._frequency_khz = 0.0
        self._ioc = DEFAULT_IOC
        self._lpm = DEFAULT_LPM
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._device_index = 0
        self._gain = 40.0
        self._direct_sampling = True

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._sdr_tool_name: str = "rtl_fm"
        self._last_error: str = ""
        self.last_start_error: BaseException | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str:
        """Last error message from a failed start() attempt."""
        return self._last_error

    def set_callback(self, callback: Callable[[dict], None]) -> None:
        """Set callback for progress updates (fed to SSE queue)."""
        self._callback = callback

    def start(
        self,
        frequency_khz: float,
        station: str = "",
        device_index: int = 0,
        gain: float = 40.0,
        ioc: int = DEFAULT_IOC,
        lpm: int = DEFAULT_LPM,
        direct_sampling: bool = True,
        sdr_type: str = "rtlsdr",
    ) -> bool:
        """Start WeFax decoder.

        Args:
            frequency_khz: Frequency in kHz (e.g. 4298 for NOJ).
            station: Station callsign for metadata.
            device_index: SDR device index.
            gain: Receiver gain in dB.
            ioc: Index of Cooperation (576 or 288).
            lpm: Lines per minute (120 or 60).
            direct_sampling: Enable RTL-SDR direct sampling for HF.
            sdr_type: SDR hardware type (rtlsdr, hackrf, limesdr, airspy, sdrplay).

        Returns:
            True if started successfully.
        """
        with self._lock:
            if self._running:
                return True

            self._station = station
            self._frequency_khz = frequency_khz
            self._ioc = ioc
            self._lpm = lpm
            self._device_index = device_index
            self._gain = gain
            self._direct_sampling = direct_sampling
            self._sdr_type = sdr_type
            self._sample_rate = DEFAULT_SAMPLE_RATE

            try:
                self._running = True
                self._last_error = ""
                self._start_pipeline_spawn()
            except Exception as e:
                self._running = False
                self._last_error = str(e)
                self.last_start_error = e
                logger.error(f"Failed to start WeFax decoder: {e}")
                self._emit_progress(
                    WeFaxProgress(
                        status="error",
                        message=str(e),
                    )
                )
                return False

        # Health check sleep outside lock
        try:
            self._start_pipeline_health_check()
            logger.info(f"WeFax decoder started: {frequency_khz} kHz, station={station}, IOC={ioc}, LPM={lpm}")
            return True
        except Exception as e:
            with self._lock:
                self._running = False
                self._last_error = str(e)
                self.last_start_error = e
            logger.error(f"Failed to start WeFax decoder: {e}")
            self._emit_progress(
                WeFaxProgress(
                    status="error",
                    message=str(e),
                )
            )
            return False

    def _start_pipeline(self) -> None:
        """Start SDR FM demod subprocess in USB mode for WeFax."""
        self._start_pipeline_spawn()
        self._start_pipeline_health_check()

    def _start_pipeline_spawn(self) -> None:
        """Spawn the SDR FM demod subprocess. Must hold self._lock."""
        try:
            sdr_type_enum = SDRType(self._sdr_type)
        except ValueError:
            sdr_type_enum = SDRType.RTL_SDR

        # Validate that the required tool is available
        if sdr_type_enum == SDRType.RTL_SDR:
            if not get_tool_path("rtl_fm"):
                raise FileNotFoundError(errno.ENOENT, "rtl_fm not found", "rtl_fm")
        else:
            if not get_tool_path("rx_fm"):
                raise FileNotFoundError(errno.ENOENT, "rx_fm not found (required for non-RTL-SDR devices)", "rx_fm")

        sdr_device = SDRFactory.create_default_device(sdr_type_enum, index=self._device_index)
        builder = SDRFactory.get_builder(sdr_type_enum)
        rtl_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=self._frequency_khz / 1000.0,
            sample_rate=self._sample_rate,
            gain=self._gain,
            modulation="usb",
        )

        # RTL-SDR: append direct sampling flag for HF reception
        if sdr_type_enum == SDRType.RTL_SDR and self._direct_sampling:
            # Insert before trailing '-' stdout marker
            if rtl_cmd and rtl_cmd[-1] == "-":
                rtl_cmd.insert(-1, "-E")
                rtl_cmd.insert(-1, "direct2")
            else:
                rtl_cmd.extend(["-E", "direct2", "-"])

        self._sdr_tool_name = rtl_cmd[0] if rtl_cmd else "sdr"
        logger.info(f"Starting {self._sdr_tool_name}: {' '.join(rtl_cmd)}")

        self._sdr_process = subprocess.Popen(
            rtl_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _start_pipeline_health_check(self) -> None:
        """Post-spawn health check and decode thread start. Called outside lock."""
        time.sleep(0.3)

        with self._lock:
            if self._sdr_process and self._sdr_process.poll() is not None:
                stderr_detail = ""
                if self._sdr_process.stderr:
                    stderr_detail = self._sdr_process.stderr.read().decode(errors="replace").strip()
                rc = self._sdr_process.returncode
                self._sdr_process = None
                detail = stderr_detail.split("\n")[-1] if stderr_detail else f"exit code {rc}"
                raise RuntimeError(f"{self._sdr_tool_name} failed: {detail}")

            self._decode_thread = threading.Thread(target=self._decode_audio_stream, daemon=True)
            self._decode_thread.start()

    def _decode_audio_stream(self) -> None:
        """Read audio from SDR FM demod and decode WeFax images.

        Runs in a background thread.  Processes 100ms chunks through
        the start-tone / phasing / image state machine.
        """
        sr = self._sample_rate
        chunk_samples = sr // 10  # 100ms
        chunk_bytes = chunk_samples * 2  # int16

        state = DecoderState.SCANNING
        start_tone_count = 0
        stop_tone_count = 0
        phasing_line_count = 0

        # Image parameters
        pixels_per_line = int(math.pi * self._ioc)
        line_duration_s = 60.0 / self._lpm
        samples_per_line = int(line_duration_s * sr)

        # Image buffer
        image_lines: list[np.ndarray] = []
        line_buffer = np.zeros(0, dtype=np.float64)
        max_lines = 2000  # Safety limit

        sdr_error = ""
        last_partial_line = -1

        logger.info(
            f"WeFax decode thread started: IOC={self._ioc}, "
            f"LPM={self._lpm}, pixels/line={pixels_per_line}, "
            f"samples/line={samples_per_line}"
        )

        # Emit initial scanning progress here (not in start()) so the
        # frontend SSE connection is established before this event fires.
        time.sleep(0.1)
        self._emit_progress(
            WeFaxProgress(
                status="scanning",
                station=self._station,
                message=f"Scanning {self._frequency_khz} kHz for WeFax start tone...",
            )
        )

        while self._running and self._sdr_process:
            try:
                proc = self._sdr_process
                if not proc or not proc.stdout:
                    break
                # Non-blocking read via select() — allows checking _running
                # on timeout instead of blocking indefinitely in read().
                fd = proc.stdout.fileno()
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    if not self._running:
                        break
                    continue
                raw_data = os.read(fd, chunk_bytes)
                if not raw_data:
                    if self._running:
                        stderr_msg = ""
                        if self._sdr_process and self._sdr_process.stderr:
                            with contextlib.suppress(Exception):
                                stderr_msg = self._sdr_process.stderr.read().decode(errors="replace").strip()
                        rc = self._sdr_process.poll() if self._sdr_process else None
                        logger.warning(f"{self._sdr_tool_name} stream ended (exit code: {rc})")
                        if stderr_msg:
                            logger.warning(f"{self._sdr_tool_name} stderr: {stderr_msg}")
                            sdr_error = stderr_msg
                    break

                n_samples = len(raw_data) // 2
                if n_samples == 0:
                    continue

                raw_int16 = np.frombuffer(raw_data[: n_samples * 2], dtype=np.int16)
                samples = raw_int16.astype(np.float64) / 32768.0

                # Emit scope waveform for frontend visualisation
                self._emit_scope(raw_int16)

                if state == DecoderState.SCANNING:
                    # Look for 300 Hz start tone
                    if _detect_tone(samples, START_TONE_FREQ, sr, threshold=2.5):
                        start_tone_count += 1
                        # Need sustained detection (>= START_TONE_DURATION seconds)
                        needed = int(START_TONE_DURATION / 0.1)
                        if start_tone_count >= needed:
                            state = DecoderState.PHASING
                            phasing_line_count = 0
                            logger.info("WeFax start tone detected, entering phasing")
                            self._emit_progress(
                                WeFaxProgress(
                                    status="phasing",
                                    station=self._station,
                                    message="Start tone detected, synchronising...",
                                )
                            )
                    else:
                        start_tone_count = max(0, start_tone_count - 1)

                elif state == DecoderState.PHASING:
                    # Count phasing lines (alternating black/white pulses)
                    phasing_line_count += 1
                    needed_phasing = max(PHASING_MIN_LINES, int(2.0 / 0.1))
                    if phasing_line_count >= needed_phasing:
                        state = DecoderState.RECEIVING
                        image_lines = []
                        line_buffer = np.zeros(0, dtype=np.float64)
                        last_partial_line = -1
                        logger.info("Phasing complete, receiving image")
                        self._emit_progress(
                            WeFaxProgress(
                                status="receiving",
                                station=self._station,
                                message="Receiving image...",
                            )
                        )

                elif state == DecoderState.RECEIVING:
                    # Check for stop tone
                    if _detect_tone(samples, STOP_TONE_FREQ, sr, threshold=2.5):
                        stop_tone_count += 1
                        needed_stop = int(STOP_TONE_DURATION / 0.1)
                        if stop_tone_count >= needed_stop:
                            # Process any remaining line buffer
                            if len(line_buffer) >= samples_per_line * 0.5:
                                line_pixels = self._decode_line(line_buffer, pixels_per_line, sr)
                                image_lines.append(line_pixels)

                            state = DecoderState.COMPLETE
                            logger.info(f"Stop tone detected, image complete: {len(image_lines)} lines")
                            break
                    else:
                        stop_tone_count = max(0, stop_tone_count - 1)

                    # Accumulate samples into line buffer
                    line_buffer = np.concatenate([line_buffer, samples])

                    # Extract complete lines
                    while len(line_buffer) >= samples_per_line:
                        line_samples = line_buffer[:samples_per_line]
                        line_buffer = line_buffer[samples_per_line:]

                        line_pixels = self._decode_line(line_samples, pixels_per_line, sr)
                        image_lines.append(line_pixels)

                    # Safety limit
                    if len(image_lines) >= max_lines:
                        logger.warning("WeFax max lines reached, saving image")
                        state = DecoderState.COMPLETE
                        break

                    # Emit progress periodically
                    current_lines = len(image_lines)
                    if current_lines > 0 and current_lines != last_partial_line and current_lines % 20 == 0:
                        last_partial_line = current_lines
                        # Rough progress estimate (typical chart ~800 lines)
                        pct = min(95, int(current_lines / 8))
                        partial_url = self._encode_partial(image_lines, pixels_per_line)
                        self._emit_progress(
                            WeFaxProgress(
                                status="receiving",
                                station=self._station,
                                message=f"Receiving: {current_lines} lines",
                                progress_percent=pct,
                                line_count=current_lines,
                                partial_image=partial_url,
                            )
                        )

            except Exception as e:
                logger.error(f"Error in WeFax decode thread: {e}")
                if not self._running:
                    break
                time.sleep(0.1)

        # Save image if we got data
        if state == DecoderState.COMPLETE and image_lines:
            self._save_image(image_lines, pixels_per_line)
        elif state == DecoderState.RECEIVING and len(image_lines) > 20:
            # Save partial image if we had significant data
            logger.info(f"Saving partial WeFax image: {len(image_lines)} lines")
            self._save_image(image_lines, pixels_per_line)

        # Clean up
        with self._lock:
            was_running = self._running
            self._running = False
            if self._sdr_process:
                with contextlib.suppress(Exception):
                    self._sdr_process.terminate()
                    self._sdr_process.wait(timeout=2)
                self._sdr_process = None

        if was_running:
            err_detail = sdr_error.split("\n")[-1] if sdr_error else ""
            if state != DecoderState.COMPLETE:
                msg = f"{self._sdr_tool_name} failed: {err_detail}" if err_detail else "Decode stopped unexpectedly"
                self._emit_progress(WeFaxProgress(status="error", message=msg))
        else:
            self._emit_progress(WeFaxProgress(status="stopped", message="Decoder stopped"))

        logger.info("WeFax decode thread ended")

    def _decode_line(self, line_samples: np.ndarray, pixels_per_line: int, sample_rate: int) -> np.ndarray:
        """Decode one scan line from audio samples to pixel values.

        Uses instantaneous frequency estimation via the analytic signal
        (Hilbert transform), then maps frequency to grayscale.
        """
        n = len(line_samples)
        pixels = np.zeros(pixels_per_line, dtype=np.uint8)

        if n < pixels_per_line:
            return pixels

        samples_per_pixel = n / pixels_per_line

        # Use Hilbert transform for instantaneous frequency
        try:
            analytic = np.fft.ifft(np.fft.fft(line_samples) * 2 * (np.arange(n) < n // 2))
            inst_phase = np.unwrap(np.angle(analytic))
            inst_freq = np.diff(inst_phase) / (2.0 * math.pi) * sample_rate
            inst_freq = np.clip(inst_freq, BLACK_FREQ - 200, WHITE_FREQ + 200)

            # Average frequency per pixel
            for px in range(pixels_per_line):
                start_idx = int(px * samples_per_pixel)
                end_idx = int((px + 1) * samples_per_pixel)
                end_idx = min(end_idx, len(inst_freq))
                if start_idx >= end_idx:
                    continue
                avg_freq = float(np.mean(inst_freq[start_idx:end_idx]))
                pixels[px] = _freq_to_pixel(avg_freq)

        except Exception:
            # Fallback: simple Goertzel per pixel window
            for px in range(pixels_per_line):
                start_idx = int(px * samples_per_pixel)
                end_idx = int((px + 1) * samples_per_pixel)
                if start_idx >= len(line_samples) or start_idx >= end_idx:
                    break
                window = line_samples[start_idx:end_idx]
                freq = _estimate_frequency(window, sample_rate, BLACK_FREQ - 200, WHITE_FREQ + 200)
                pixels[px] = _freq_to_pixel(freq)

        return pixels

    def _encode_partial(self, image_lines: list[np.ndarray], width: int) -> str | None:
        """Encode current image lines as a JPEG data URL for live preview."""
        if PILImage is None or not image_lines:
            return None
        try:
            height = len(image_lines)
            img_array = np.zeros((height, width), dtype=np.uint8)
            for i, line in enumerate(image_lines):
                img_array[i, : len(line)] = line[:width]
            img = PILImage.fromarray(img_array, mode="L")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=40)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception:
            return None

    def _save_image(self, image_lines: list[np.ndarray], width: int) -> None:
        """Save completed image to disk."""
        if PILImage is None:
            logger.error("Cannot save image: Pillow not installed")
            self._emit_progress(
                WeFaxProgress(
                    status="error",
                    message="Cannot save image - Pillow not installed",
                )
            )
            return

        try:
            height = len(image_lines)
            img_array = np.zeros((height, width), dtype=np.uint8)
            for i, line in enumerate(image_lines):
                img_array[i, : len(line)] = line[:width]

            img = PILImage.fromarray(img_array, mode="L")
            timestamp = datetime.now(timezone.utc)
            station_tag = self._station or "unknown"
            filename = f"wefax_{timestamp.strftime('%Y%m%d_%H%M%S')}_{station_tag}.png"
            filepath = self._output_dir / filename
            img.save(filepath, "PNG")

            wefax_image = WeFaxImage(
                filename=filename,
                path=filepath,
                station=self._station,
                frequency_khz=self._frequency_khz,
                timestamp=timestamp,
                ioc=self._ioc,
                lpm=self._lpm,
                size_bytes=filepath.stat().st_size,
            )
            self._images.append(wefax_image)

            logger.info(f"WeFax image saved: {filename} ({wefax_image.size_bytes} bytes)")
            self._emit_progress(
                WeFaxProgress(
                    status="complete",
                    station=self._station,
                    message=f"Image decoded: {height} lines",
                    progress_percent=100,
                    line_count=height,
                    image=wefax_image,
                )
            )

        except Exception as e:
            logger.error(f"Error saving WeFax image: {e}")
            self._emit_progress(
                WeFaxProgress(
                    status="error",
                    message=f"Error saving image: {e}",
                )
            )

    def stop(self) -> None:
        """Stop WeFax decoder.

        Sets _running=False and terminates the process outside the lock,
        then waits briefly for the decode thread to finish saving any
        partial image before returning.
        """
        with self._lock:
            self._running = False
            proc = self._sdr_process
            self._sdr_process = None
            thread = self._decode_thread

        if proc:
            with contextlib.suppress(Exception):
                proc.terminate()

        # Wait for the decode thread to save any partial image.
        # With select()-based reads the thread exits within ~0.5s.
        if thread:
            with contextlib.suppress(Exception):
                thread.join(timeout=2)

        logger.info("WeFax decoder stopped")

    def get_images(self) -> list[WeFaxImage]:
        """Get list of decoded images."""
        self._scan_images()
        return list(self._images)

    def delete_image(self, filename: str) -> bool:
        """Delete a single decoded image."""
        filepath = self._output_dir / filename
        if not filepath.exists():
            return False
        filepath.unlink()
        self._images = [img for img in self._images if img.filename != filename]
        logger.info(f"Deleted WeFax image: {filename}")
        return True

    def delete_all_images(self) -> int:
        """Delete all decoded images. Returns count deleted."""
        count = 0
        for filepath in self._output_dir.glob("*.png"):
            filepath.unlink()
            count += 1
        self._images.clear()
        logger.info(f"Deleted all WeFax images ({count} files)")
        return count

    def _scan_images(self) -> None:
        """Scan output directory for images not yet tracked."""
        known = {img.filename for img in self._images}
        for filepath in self._output_dir.glob("*.png"):
            if filepath.name not in known:
                try:
                    stat = filepath.stat()
                    image = WeFaxImage(
                        filename=filepath.name,
                        path=filepath,
                        station="",
                        frequency_khz=0,
                        timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        ioc=self._ioc,
                        lpm=self._lpm,
                        size_bytes=stat.st_size,
                    )
                    self._images.append(image)
                except Exception as e:
                    logger.warning(f"Error scanning image {filepath}: {e}")

    def _emit_progress(self, progress: WeFaxProgress) -> None:
        """Emit progress update to callback."""
        if self._callback:
            try:
                self._callback(progress.to_dict())
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")

    def _emit_scope(self, raw_int16: np.ndarray) -> None:
        """Emit scope waveform data for frontend visualisation."""
        if not self._callback:
            return

        now = time.monotonic()
        if now - self._last_scope_time < 0.1:
            return
        self._last_scope_time = now

        try:
            peak = int(np.max(np.abs(raw_int16)))
            rms = int(np.sqrt(np.mean(raw_int16.astype(np.float64) ** 2)))

            # Downsample to 256 signed int8 values for lightweight transport
            window = raw_int16[-256:] if len(raw_int16) > 256 else raw_int16
            waveform = np.clip(window // 256, -127, 127).astype(np.int8).tolist()

            self._callback(
                {
                    "type": "scope",
                    "rms": rms,
                    "peak": peak,
                    "waveform": waveform,
                }
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_decoder: WeFaxDecoder | None = None


def get_wefax_decoder() -> WeFaxDecoder:
    """Get or create the global WeFax decoder instance."""
    global _decoder
    if _decoder is None:
        _decoder = WeFaxDecoder()
    return _decoder
