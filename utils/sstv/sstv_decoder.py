"""SSTV decoder orchestrator.

Provides the SSTVDecoder class that manages the full pipeline:
rtl_fm subprocess -> audio stream -> VIS detection -> image decoding -> PNG output.

DopplerTracker and DopplerInfo live in utils/doppler.py and are re-exported
here for backwards compatibility.
"""

from __future__ import annotations

import base64
import contextlib
import io
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

# DopplerTracker/DopplerInfo now live in the shared utils/doppler module.
# Import them here so existing code that does
#   ``from utils.sstv.sstv_decoder import DopplerTracker``
# continues to work unchanged.
from utils.doppler import DopplerInfo, DopplerTracker  # noqa: F401
from utils.logging import get_logger

from .constants import ISS_SSTV_FREQ, SAMPLE_RATE
from .dsp import goertzel_mag, normalize_audio
from .image_decoder import SSTVImageDecoder
from .modes import get_mode
from .vis import VISDetector

logger = get_logger("intercept.sstv")

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

# DopplerInfo is now defined in utils/doppler and imported at the top of
# this module.  The re-export keeps any code that does
#   from utils.sstv.sstv_decoder import DopplerInfo
# working without changes.


@dataclass
class SSTVImage:
    """Decoded SSTV image."""

    filename: str
    path: Path
    mode: str
    timestamp: datetime
    frequency: float
    size_bytes: int = 0
    url_prefix: str = "/sstv"

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "path": str(self.path),
            "mode": self.mode,
            "timestamp": self.timestamp.isoformat(),
            "frequency": self.frequency,
            "size_bytes": self.size_bytes,
            "url": f"{self.url_prefix}/images/{self.filename}",
        }


@dataclass
class DecodeProgress:
    """SSTV decode progress update."""

    status: str  # 'detecting', 'decoding', 'complete', 'error'
    mode: str | None = None
    progress_percent: int = 0
    message: str | None = None
    image: SSTVImage | None = None
    signal_level: int | None = None  # 0-100 RMS audio level, None = not measured
    sstv_tone: str | None = None  # 'leader', 'sync', 'noise', None
    vis_state: str | None = None  # VIS detector state name
    partial_image: str | None = None  # base64 data URL of partial decode

    def to_dict(self) -> dict:
        result: dict = {
            "type": "sstv_progress",
            "status": self.status,
            "progress": self.progress_percent,
        }
        if self.mode:
            result["mode"] = self.mode
        if self.message:
            result["message"] = self.message
        if self.image:
            result["image"] = self.image.to_dict()
        if self.signal_level is not None:
            result["signal_level"] = self.signal_level
        if self.sstv_tone:
            result["sstv_tone"] = self.sstv_tone
        if self.vis_state:
            result["vis_state"] = self.vis_state
        if self.partial_image:
            result["partial_image"] = self.partial_image
        return result


def _encode_scope_waveform(raw_samples: np.ndarray, window_size: int = 256) -> list[int]:
    """Compress recent int16 PCM samples to signed 8-bit values for SSE."""
    if raw_samples.size == 0:
        return []

    window = raw_samples[-window_size:] if raw_samples.size > window_size else raw_samples
    packed = np.rint(window.astype(np.float64) / 256.0).astype(np.int16)
    packed = np.clip(packed, -127, 127)
    return packed.tolist()


# DopplerTracker is now imported from utils/doppler at the top of this module.
# Nothing to define here.


# ---------------------------------------------------------------------------
# SSTVDecoder
# ---------------------------------------------------------------------------


class SSTVDecoder:
    """SSTV decoder using pure-Python DSP with Doppler compensation."""

    RETUNE_THRESHOLD_HZ = 500
    DOPPLER_UPDATE_INTERVAL = 5

    def __init__(self, output_dir: str | Path | None = None, url_prefix: str = "/sstv"):
        self.last_start_error: BaseException | None = None
        self._rtl_process = None
        self._running = False
        self._lock = threading.Lock()
        self._callback: Callable[[dict], None] | None = None
        self._output_dir = Path(output_dir) if output_dir else Path("instance/sstv_images")
        self._url_prefix = url_prefix
        self._images: list[SSTVImage] = []
        self._decode_thread = None
        self._doppler_thread = None
        self._frequency = ISS_SSTV_FREQ
        self._modulation = "fm"
        self._current_tuned_freq_hz: int = 0
        self._device_index = 0

        # Doppler tracking
        self._doppler_tracker = DopplerTracker("ISS")
        self._doppler_enabled = False
        self._last_doppler_info: DopplerInfo | None = None

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def decoder_available(self) -> str:
        """Return name of available decoder. Always available with pure Python."""
        return "python-sstv"

    def set_callback(self, callback: Callable[[dict], None]) -> None:
        """Set callback for decode progress updates."""
        self._callback = callback

    def start(
        self,
        frequency: float = ISS_SSTV_FREQ,
        device_index: int = 0,
        latitude: float | None = None,
        longitude: float | None = None,
        modulation: str = "fm",
    ) -> bool:
        """Start SSTV decoder listening on specified frequency.

        Args:
            frequency: Frequency in MHz (default: 145.800 for ISS).
            device_index: RTL-SDR device index.
            latitude: Observer latitude for Doppler correction.
            longitude: Observer longitude for Doppler correction.
            modulation: Demodulation mode for rtl_fm (fm, usb, lsb).

        Returns:
            True if started successfully.
        """
        with self._lock:
            if self._running:
                return True

            self._frequency = frequency
            self._device_index = device_index
            self._modulation = modulation

            # Configure Doppler tracking if location provided
            self._doppler_enabled = False
            if latitude is not None and longitude is not None:
                if self._doppler_tracker.configure(latitude, longitude):
                    self._doppler_enabled = True
                    logger.info(f"Doppler tracking enabled for location ({latitude}, {longitude})")
                else:
                    logger.warning("Doppler tracking unavailable - using fixed frequency")

            try:
                freq_hz = self._get_doppler_corrected_freq_hz()
                self._current_tuned_freq_hz = freq_hz
                # Set _running BEFORE starting the pipeline so the decode
                # thread sees it as True on its first loop iteration.
                self._running = True
                self._start_pipeline(freq_hz)

                # Start Doppler tracking thread if enabled
                if self._doppler_enabled:
                    self._doppler_thread = threading.Thread(target=self._doppler_tracking_loop, daemon=True)
                    self._doppler_thread.start()
                    logger.info(f"SSTV decoder started on {frequency} MHz with Doppler tracking")
                    self._emit_progress(
                        DecodeProgress(
                            status="detecting", message=f"Listening on {frequency} MHz with Doppler tracking..."
                        )
                    )
                else:
                    logger.info(f"SSTV decoder started on {frequency} MHz (no Doppler tracking)")
                    self._emit_progress(DecodeProgress(status="detecting", message=f"Listening on {frequency} MHz..."))

                return True

            except Exception as e:
                self._running = False
                self.last_start_error = e
                logger.error(f"Failed to start SSTV decoder: {e}")
                self._emit_progress(DecodeProgress(status="error", message=str(e)))
                return False

    def _get_doppler_corrected_freq_hz(self) -> int:
        """Get the Doppler-corrected frequency in Hz."""
        nominal_freq_hz = int(self._frequency * 1_000_000)

        if self._doppler_enabled:
            doppler_info = self._doppler_tracker.calculate(self._frequency)
            if doppler_info:
                self._last_doppler_info = doppler_info
                corrected_hz = int(doppler_info.frequency_hz)
                logger.info(
                    f"Doppler correction: {doppler_info.shift_hz:+.1f} Hz "
                    f"(range rate: {doppler_info.range_rate_km_s:+.3f} km/s, "
                    f"el: {doppler_info.elevation:.1f}\u00b0)"
                )
                return corrected_hz

        return nominal_freq_hz

    def _start_pipeline(self, freq_hz: int) -> None:
        """Start the rtl_fm -> Python decode pipeline."""
        rtl_cmd = [
            "rtl_fm",
            "-d",
            str(self._device_index),
            "-f",
            str(freq_hz),
            "-M",
            self._modulation,
            "-s",
            str(SAMPLE_RATE),
            "-r",
            str(SAMPLE_RATE),
            "-l",
            "0",  # No squelch
            "-",
        ]

        logger.info(f"Starting rtl_fm: {' '.join(rtl_cmd)}")

        self._rtl_process = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Start decode thread that reads from rtl_fm stdout
        self._decode_thread = threading.Thread(target=self._decode_audio_stream, daemon=True)
        self._decode_thread.start()

    def _decode_audio_stream(self) -> None:
        """Read audio from rtl_fm and decode SSTV images.

        Runs in a background thread. Reads 100ms chunks of int16 PCM,
        feeds through VIS detector, then image decoder.
        """
        chunk_bytes = SAMPLE_RATE // 10 * 2  # 100ms of int16 = 9600 bytes
        vis_detector = VISDetector(sample_rate=SAMPLE_RATE)
        image_decoder: SSTVImageDecoder | None = None
        current_mode_name: str | None = None
        chunk_counter = 0
        last_partial_pct = -1

        logger.info("Audio decode thread started")
        rtl_fm_error: str = ""

        while self._running and self._rtl_process:
            try:
                raw_data = self._rtl_process.stdout.read(chunk_bytes)
                if not raw_data:
                    if self._running:
                        # Read stderr to diagnose why rtl_fm exited
                        stderr_msg = ""
                        if self._rtl_process and self._rtl_process.stderr:
                            with contextlib.suppress(Exception):
                                stderr_msg = self._rtl_process.stderr.read().decode(errors="replace").strip()
                        rc = self._rtl_process.poll() if self._rtl_process else None
                        logger.warning(f"rtl_fm stream ended unexpectedly (exit code: {rc})")
                        if stderr_msg:
                            logger.warning(f"rtl_fm stderr: {stderr_msg}")
                            rtl_fm_error = stderr_msg
                    break

                # Convert int16 PCM to float64
                n_samples = len(raw_data) // 2
                if n_samples == 0:
                    continue
                raw_samples = np.frombuffer(raw_data[: n_samples * 2], dtype=np.int16)
                samples = normalize_audio(raw_samples)

                chunk_counter += 1

                # Scope: compute RMS/peak from raw int16 samples every chunk
                rms_val = int(np.sqrt(np.mean(raw_samples.astype(np.float64) ** 2)))
                peak_val = int(np.max(np.abs(raw_samples)))
                waveform = _encode_scope_waveform(raw_samples)

                if image_decoder is not None:
                    # Currently decoding an image
                    complete = image_decoder.feed(samples)

                    # Encode partial image every 5% progress
                    pct = image_decoder.progress_percent
                    partial_url = None
                    if pct >= last_partial_pct + 5 or complete:
                        last_partial_pct = pct
                        try:
                            img = image_decoder.get_image()
                            if img is not None:
                                buf = io.BytesIO()
                                img.save(buf, format="JPEG", quality=40)
                                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                                partial_url = f"data:image/jpeg;base64,{b64}"
                        except Exception:
                            pass

                    # Emit progress
                    self._emit_progress(
                        DecodeProgress(
                            status="decoding",
                            mode=current_mode_name,
                            progress_percent=pct,
                            message=f"Decoding {current_mode_name}: {pct}%",
                            partial_image=partial_url,
                        )
                    )
                    self._emit_scope(rms_val, peak_val, "decoding", waveform)

                    if complete:
                        # Save image
                        self._save_decoded_image(image_decoder, current_mode_name)
                        image_decoder = None
                        current_mode_name = None
                        vis_detector.reset()
                else:
                    # Scanning for VIS header
                    result = vis_detector.feed(samples)
                    if result is not None:
                        vis_code, mode_name = result
                        # Capture samples that arrived after the VIS STOP_BIT —
                        # these are the start of the image and must be fed into
                        # the image decoder before the next chunk arrives.
                        remaining = vis_detector.remaining_buffer.copy()
                        vis_detector.reset()
                        logger.info(
                            f"VIS detected: code={vis_code}, mode={mode_name}, "
                            f"{len(remaining)} image-start samples retained"
                        )

                        mode_spec = get_mode(vis_code)
                        if mode_spec:
                            current_mode_name = mode_name
                            last_partial_pct = -1
                            image_decoder = SSTVImageDecoder(
                                mode_spec,
                                sample_rate=SAMPLE_RATE,
                            )
                            if len(remaining) > 0:
                                image_decoder.feed(remaining)
                            self._emit_progress(
                                DecodeProgress(
                                    status="decoding",
                                    mode=mode_name,
                                    progress_percent=0,
                                    message=f"Detected {mode_name} - decoding...",
                                )
                            )
                        else:
                            logger.warning(f"No mode spec for VIS code {vis_code}")
                            self._emit_progress(
                                DecodeProgress(
                                    status="detecting",
                                    message=f"Detected unknown mode (VIS {vis_code}: {mode_name}) - unsupported",
                                )
                            )

                    # Emit signal level metrics every ~500ms (every 5th 100ms chunk)
                    scope_tone: str | None = None
                    if chunk_counter % 5 == 0 and image_decoder is None:
                        rms = float(np.sqrt(np.mean(samples**2)))
                        signal_level = min(100, int(rms * 500))

                        leader_energy = goertzel_mag(samples, 1900.0, SAMPLE_RATE)
                        sync_energy = goertzel_mag(samples, 1200.0, SAMPLE_RATE)
                        noise_floor = max(rms * 0.5, 0.001)

                        # Require the tone to both exceed the noise floor AND
                        # dominate the other tone by 2x to avoid false positives
                        # from broadband noise.
                        if leader_energy > noise_floor * 5 and leader_energy > sync_energy * 2:
                            sstv_tone = "leader"
                        elif sync_energy > noise_floor * 5 and sync_energy > leader_energy * 2:
                            sstv_tone = "sync"
                        elif signal_level > 10:
                            sstv_tone = "noise"
                        else:
                            sstv_tone = None

                        scope_tone = sstv_tone

                        self._emit_progress(
                            DecodeProgress(
                                status="detecting",
                                message="Listening...",
                                signal_level=signal_level,
                                sstv_tone=sstv_tone,
                                vis_state=vis_detector.state.value,
                            )
                        )

                    self._emit_scope(rms_val, peak_val, scope_tone, waveform)

            except Exception as e:
                logger.error(f"Error in decode thread: {e}")
                if not self._running:
                    break
                time.sleep(0.1)

        # Clean up if the thread exits while we thought we were running.
        # This prevents a "ghost running" state where is_running is True
        # but the thread has already died (e.g. rtl_fm exited).
        orphan_proc = None
        with self._lock:
            was_running = self._running
            self._running = False
            if was_running and self._rtl_process:
                orphan_proc = self._rtl_process
                self._rtl_process = None

        # Terminate outside lock to avoid blocking other operations
        if orphan_proc:
            with contextlib.suppress(Exception):
                orphan_proc.terminate()
                orphan_proc.wait(timeout=2)

        if was_running:
            logger.warning("Audio decode thread stopped unexpectedly")
            err_detail = rtl_fm_error.split("\n")[-1] if rtl_fm_error else ""
            msg = f"rtl_fm failed: {err_detail}" if err_detail else "Decode pipeline stopped unexpectedly"
            self._emit_progress(DecodeProgress(status="error", message=msg))
        else:
            logger.info("Audio decode thread stopped")

    def _save_decoded_image(self, decoder: SSTVImageDecoder, mode_name: str | None) -> None:
        """Save a completed decoded image to disk."""
        try:
            img = decoder.get_image()
            if img is None:
                logger.error("Failed to get image from decoder (Pillow not available?)")
                self._emit_progress(
                    DecodeProgress(status="error", message="Failed to create image - Pillow not installed")
                )
                return

            timestamp = datetime.now(timezone.utc)
            filename = f"sstv_{timestamp.strftime('%Y%m%d_%H%M%S')}_{mode_name or 'unknown'}.png"
            filepath = self._output_dir / filename
            img.save(filepath, "PNG")

            sstv_image = SSTVImage(
                filename=filename,
                path=filepath,
                mode=mode_name or "Unknown",
                timestamp=timestamp,
                frequency=self._frequency,
                size_bytes=filepath.stat().st_size,
                url_prefix=self._url_prefix,
            )
            self._images.append(sstv_image)

            logger.info(f"SSTV image saved: {filename} ({sstv_image.size_bytes} bytes)")
            self._emit_progress(
                DecodeProgress(
                    status="complete",
                    mode=mode_name,
                    progress_percent=100,
                    message="Image decoded",
                    image=sstv_image,
                )
            )

        except Exception as e:
            logger.error(f"Error saving decoded image: {e}")
            self._emit_progress(DecodeProgress(status="error", message=f"Error saving image: {e}"))

    def _doppler_tracking_loop(self) -> None:
        """Background thread that monitors Doppler shift and retunes when needed."""
        logger.info("Doppler tracking thread started")

        while self._running and self._doppler_enabled:
            time.sleep(self.DOPPLER_UPDATE_INTERVAL)

            if not self._running:
                break

            try:
                doppler_info = self._doppler_tracker.calculate(self._frequency)
                if not doppler_info:
                    continue

                self._last_doppler_info = doppler_info
                new_freq_hz = int(doppler_info.frequency_hz)
                freq_diff = abs(new_freq_hz - self._current_tuned_freq_hz)

                logger.debug(
                    f"Doppler: {doppler_info.shift_hz:+.1f} Hz, "
                    f"el: {doppler_info.elevation:.1f}\u00b0, "
                    f"diff from tuned: {freq_diff} Hz"
                )

                self._emit_progress(
                    DecodeProgress(
                        status="detecting",
                        message=f"Doppler: {doppler_info.shift_hz:+.0f} Hz, elevation: {doppler_info.elevation:.1f}\u00b0",
                    )
                )

                if freq_diff >= self.RETUNE_THRESHOLD_HZ:
                    logger.info(
                        f"Retuning: {self._current_tuned_freq_hz} -> {new_freq_hz} Hz "
                        f"(Doppler shift: {doppler_info.shift_hz:+.1f} Hz)"
                    )
                    self._retune_rtl_fm(new_freq_hz)

            except Exception as e:
                logger.error(f"Doppler tracking error: {e}")

        logger.info("Doppler tracking thread stopped")

    def _retune_rtl_fm(self, new_freq_hz: int) -> None:
        """Retune rtl_fm to a new frequency by restarting the process."""
        old_proc = None
        with self._lock:
            if not self._running:
                return
            old_proc = self._rtl_process
            self._rtl_process = None

        # Terminate old process outside lock
        if old_proc:
            try:
                old_proc.terminate()
                old_proc.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    old_proc.kill()

        # Build and start new process outside lock
        rtl_cmd = [
            "rtl_fm",
            "-d",
            str(self._device_index),
            "-f",
            str(new_freq_hz),
            "-M",
            self._modulation,
            "-s",
            str(SAMPLE_RATE),
            "-r",
            str(SAMPLE_RATE),
            "-l",
            "0",
            "-",
        ]

        logger.debug(f"Restarting rtl_fm: {' '.join(rtl_cmd)}")

        new_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Re-acquire lock to install new process
        with self._lock:
            if self._running:
                self._rtl_process = new_proc
                self._current_tuned_freq_hz = new_freq_hz
            else:
                # stop() was called during retune — clean up new process
                with contextlib.suppress(Exception):
                    new_proc.terminate()
                    new_proc.wait(timeout=2)

    @property
    def last_doppler_info(self) -> DopplerInfo | None:
        """Get the most recent Doppler calculation."""
        return self._last_doppler_info

    @property
    def doppler_enabled(self) -> bool:
        """Check if Doppler tracking is enabled."""
        return self._doppler_enabled

    def stop(self) -> None:
        """Stop SSTV decoder."""
        proc_to_terminate = None
        with self._lock:
            self._running = False
            proc_to_terminate = self._rtl_process
            self._rtl_process = None

        # Terminate outside lock to avoid blocking other operations
        if proc_to_terminate:
            try:
                proc_to_terminate.terminate()
                proc_to_terminate.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    proc_to_terminate.kill()

        logger.info("SSTV decoder stopped")

    def get_images(self) -> list[SSTVImage]:
        """Get list of decoded images."""
        self._scan_images()
        return list(self._images)

    def delete_image(self, filename: str) -> bool:
        """Delete a single decoded image by filename."""
        filepath = self._output_dir / filename
        if not filepath.exists():
            return False
        filepath.unlink()
        self._images = [img for img in self._images if img.filename != filename]
        logger.info(f"Deleted SSTV image: {filename}")
        return True

    def delete_all_images(self) -> int:
        """Delete all decoded images. Returns count deleted."""
        count = 0
        for filepath in self._output_dir.glob("*.png"):
            filepath.unlink()
            count += 1
        self._images.clear()
        logger.info(f"Deleted all SSTV images ({count} files)")
        return count

    def _scan_images(self) -> None:
        """Scan output directory for images."""
        known_filenames = {img.filename for img in self._images}

        for filepath in self._output_dir.glob("*.png"):
            if filepath.name not in known_filenames:
                try:
                    stat = filepath.stat()
                    image = SSTVImage(
                        filename=filepath.name,
                        path=filepath,
                        mode="Unknown",
                        timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        frequency=self._frequency,
                        size_bytes=stat.st_size,
                        url_prefix=self._url_prefix,
                    )
                    self._images.append(image)
                except Exception as e:
                    logger.warning(f"Error scanning image {filepath}: {e}")

    def _emit_progress(self, progress: DecodeProgress) -> None:
        """Emit progress update to callback."""
        if self._callback:
            try:
                self._callback(progress.to_dict())
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")

    def _emit_scope(
        self,
        rms: int,
        peak: int,
        tone: str | None = None,
        waveform: list[int] | None = None,
    ) -> None:
        """Emit scope signal levels to callback."""
        if self._callback:
            try:
                payload = {"type": "sstv_scope", "rms": rms, "peak": peak, "tone": tone}
                if waveform:
                    payload["waveform"] = waveform
                self._callback(payload)
            except Exception:
                pass

    def decode_file(self, audio_path: str | Path) -> list[SSTVImage]:
        """Decode SSTV image(s) from an audio file.

        Reads a WAV file and processes it through VIS detection + image
        decoding using the pure Python pipeline.

        Args:
            audio_path: Path to WAV audio file.

        Returns:
            List of decoded images.
        """
        import wave

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        images: list[SSTVImage] = []

        try:
            with wave.open(str(audio_path), "rb") as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                file_sample_rate = wf.getframerate()
                n_frames = wf.getnframes()

                logger.info(
                    f"Decoding WAV: {n_channels}ch, {sample_width * 8}bit, {file_sample_rate}Hz, {n_frames} frames"
                )

                # Read all audio data
                raw_data = wf.readframes(n_frames)

            # Convert to float64 mono
            if sample_width == 2:
                audio = np.frombuffer(raw_data, dtype=np.int16).astype(np.float64) / 32768.0
            elif sample_width == 1:
                audio = np.frombuffer(raw_data, dtype=np.uint8).astype(np.float64) / 128.0 - 1.0
            elif sample_width == 4:
                audio = np.frombuffer(raw_data, dtype=np.int32).astype(np.float64) / 2147483648.0
            else:
                raise ValueError(f"Unsupported sample width: {sample_width}")

            # If stereo, take left channel
            if n_channels > 1:
                audio = audio[::n_channels]

            # Resample if needed
            if file_sample_rate != SAMPLE_RATE:
                audio = self._resample(audio, file_sample_rate, SAMPLE_RATE)

            # Process through VIS detector + image decoder
            vis_detector = VISDetector(sample_rate=SAMPLE_RATE)
            image_decoder: SSTVImageDecoder | None = None
            current_mode_name: str | None = None

            chunk_size = SAMPLE_RATE // 10  # 100ms chunks
            offset = 0

            while offset < len(audio):
                chunk = audio[offset : offset + chunk_size]
                offset += chunk_size

                if image_decoder is not None:
                    complete = image_decoder.feed(chunk)
                    if complete:
                        img = image_decoder.get_image()
                        if img is not None:
                            timestamp = datetime.now(timezone.utc)
                            filename = (
                                f"sstv_{timestamp.strftime('%Y%m%d_%H%M%S')}_{current_mode_name or 'unknown'}.png"
                            )
                            filepath = self._output_dir / filename
                            img.save(filepath, "PNG")

                            sstv_image = SSTVImage(
                                filename=filename,
                                path=filepath,
                                mode=current_mode_name or "Unknown",
                                timestamp=timestamp,
                                frequency=0,
                                size_bytes=filepath.stat().st_size,
                                url_prefix=self._url_prefix,
                            )
                            images.append(sstv_image)
                            self._images.append(sstv_image)
                            logger.info(f"Decoded image from file: {filename}")

                        image_decoder = None
                        current_mode_name = None
                        vis_detector.reset()
                else:
                    result = vis_detector.feed(chunk)
                    if result is not None:
                        vis_code, mode_name = result
                        remaining = vis_detector.remaining_buffer.copy()
                        vis_detector.reset()
                        logger.info(f"VIS detected in file: code={vis_code}, mode={mode_name}")

                        mode_spec = get_mode(vis_code)
                        if mode_spec:
                            current_mode_name = mode_name
                            image_decoder = SSTVImageDecoder(
                                mode_spec,
                                sample_rate=SAMPLE_RATE,
                            )
                            if len(remaining) > 0:
                                image_decoder.feed(remaining)

        except wave.Error as e:
            logger.error(f"Error reading WAV file: {e}")
            raise
        except Exception as e:
            logger.error(f"Error decoding audio file: {e}")
            raise

        return images

    @staticmethod
    def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        """Simple resampling using linear interpolation."""
        if from_rate == to_rate:
            return audio

        ratio = to_rate / from_rate
        new_length = int(len(audio) * ratio)
        indices = np.linspace(0, len(audio) - 1, new_length)
        return np.interp(indices, np.arange(len(audio)), audio)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_decoder: SSTVDecoder | None = None


def get_sstv_decoder() -> SSTVDecoder:
    """Get or create the global SSTV decoder instance."""
    global _decoder
    if _decoder is None:
        _decoder = SSTVDecoder()
    return _decoder


def is_sstv_available() -> bool:
    """Check if SSTV decoding is available.

    Always True with the pure-Python decoder (requires only numpy/Pillow).
    """
    return True


_general_decoder: SSTVDecoder | None = None


def get_general_sstv_decoder() -> SSTVDecoder:
    """Get or create the global general SSTV decoder instance."""
    global _general_decoder
    if _general_decoder is None:
        _general_decoder = SSTVDecoder(
            output_dir="instance/sstv_general_images",
            url_prefix="/sstv-general",
        )
    return _general_decoder
