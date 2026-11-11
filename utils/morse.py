"""Morse code (CW) decoding helpers with dual detection modes.

Supports two signal chains:
  goertzel: rtl_fm -M usb -> raw PCM -> Goertzel tone filter -> timing state machine -> characters
  envelope: rtl_fm -M am  -> raw PCM -> RMS envelope       -> timing state machine -> characters

Goertzel mode is the original path for HF CW (beat note detection).
Envelope mode adds support for OOK/AM signals (e.g. 433 MHz carrier keying)
where AM demod already produces a baseband envelope -- no tone to detect.
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import select
import threading
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

try:
    # Reuse existing Goertzel helper when available.
    from utils.sstv.dsp import goertzel_mag as _shared_goertzel_mag
except Exception:  # pragma: no cover - fallback path
    _shared_goertzel_mag = None

# International Morse Code table
MORSE_TABLE: dict[str, str] = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
    ".-.-.-": ".",
    "--..--": ",",
    "..--..": "?",
    ".----.": "'",
    "-.-.--": "!",
    "-..-.": "/",
    "-.--.": "(",
    "-.--.-": ")",
    ".-...": "&",
    "---...": ":",
    "-.-.-.": ";",
    "-...-": "=",
    ".-.-.": "+",
    "-....-": "-",
    "..--.-": "_",
    ".-..-.": '"',
    "...-..-": "$",
    ".--.-.": "@",
    # Prosigns (unique codes only; -...- and -.--.- already mapped above)
    "-.-.-": "<CT>",
    ".-.-": "<AA>",
    "...-.-": "<SK>",
}

# Reverse lookup: character -> morse notation
CHAR_TO_MORSE: dict[str, str] = {v: k for k, v in MORSE_TABLE.items()}


class GoertzelFilter:
    """Single-frequency tone detector using the Goertzel algorithm."""

    def __init__(self, target_freq: float, sample_rate: int, block_size: int):
        self.target_freq = float(target_freq)
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        # Generalized coefficient (does not quantize to integer FFT bins)
        omega = 2.0 * math.pi * self.target_freq / self.sample_rate
        self.coeff = 2.0 * math.cos(omega)

    def magnitude(self, samples: list[float] | tuple[float, ...] | np.ndarray) -> float:
        """Compute magnitude of the target frequency in the sample block."""
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        coeff = self.coeff
        if isinstance(samples, np.ndarray):
            # Iterating Python floats is much cheaper than numpy scalars.
            samples = samples.tolist()
        for sample in map(float, samples):
            s0 = sample + coeff * s1 - s2
            s2 = s1
            s1 = s0
        power = s1 * s1 + s2 * s2 - coeff * s1 * s2
        return math.sqrt(max(power, 0.0))


class EnvelopeDetector:
    """RMS envelope detector for AM-demodulated OOK signals.

    When rtl_fm uses -M am, carrier-on produces a high amplitude envelope
    and carrier-off produces near-silence.  RMS over a short block gives
    a clean on/off metric without needing a specific tone frequency.
    """

    def __init__(self, block_size: int):
        self.block_size = block_size

    def magnitude(self, samples: list[float] | tuple[float, ...] | np.ndarray) -> float:
        """Compute RMS magnitude of the sample block."""
        arr = np.asarray(samples, dtype=np.float64)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(arr))))


def _goertzel_mag(samples: np.ndarray, target_freq: float, sample_rate: int) -> float:
    """Compute Goertzel magnitude, preferring shared DSP helper."""
    if _shared_goertzel_mag is not None:
        try:
            return float(_shared_goertzel_mag(samples, float(target_freq), int(sample_rate)))
        except Exception:
            pass
    filt = GoertzelFilter(target_freq=target_freq, sample_rate=sample_rate, block_size=len(samples))
    return filt.magnitude(samples)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Convert arbitrary JSON-ish values to bool."""
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


def _normalize_threshold_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"auto", "manual"} else "auto"


def _normalize_wpm_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"auto", "manual"} else "auto"


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


class MorseDecoder:
    """Real-time Morse decoder with adaptive threshold and timing estimation."""

    def __init__(
        self,
        sample_rate: int = 8000,
        tone_freq: float = 700.0,
        wpm: int = 15,
        bandwidth_hz: int = 200,
        auto_tone_track: bool = True,
        tone_lock: bool = False,
        threshold_mode: str = "auto",
        manual_threshold: float = 0.0,
        threshold_multiplier: float = 2.8,
        threshold_offset: float = 0.0,
        wpm_mode: str = "auto",
        wpm_lock: bool = False,
        min_signal_gate: float = 0.0,
        detect_mode: str = "goertzel",
    ):
        self.sample_rate = int(sample_rate)
        self.tone_freq = float(tone_freq)
        self.wpm = int(wpm)
        self.detect_mode = detect_mode if detect_mode in ("goertzel", "envelope") else "goertzel"

        self.bandwidth_hz = int(_clamp(float(bandwidth_hz), 50, 400))
        self.auto_tone_track = bool(auto_tone_track)
        self.tone_lock = bool(tone_lock)
        self.threshold_mode = _normalize_threshold_mode(threshold_mode)
        self.manual_threshold = max(0.0, float(manual_threshold))
        self.threshold_multiplier = float(_clamp(float(threshold_multiplier), 1.1, 8.0))
        self.threshold_offset = max(0.0, float(threshold_offset))
        self.wpm_mode = _normalize_wpm_mode(wpm_mode)
        self.wpm_lock = bool(wpm_lock)
        self.min_signal_gate = float(_clamp(float(min_signal_gate), 0.0, 1.0))

        # ~50 analysis windows/s at 8 kHz keeps CPU low and timing stable.
        self._block_size = max(64, self.sample_rate // 50)
        self._block_duration = self._block_size / float(self.sample_rate)

        self._active_tone_freq = float(_clamp(self.tone_freq, 300.0, 1200.0))
        self._tone_anchor_freq = self._active_tone_freq
        self._tone_scan_range_hz = 180.0
        self._tone_scan_step_hz = 10.0
        self._tone_scan_interval_blocks = 8

        if self.detect_mode == "envelope":
            self._detector = EnvelopeDetector(self._block_size)
            self._noise_detector_low = None
            self._noise_detector_high = None
        else:
            self._detector = GoertzelFilter(self._active_tone_freq, self.sample_rate, self._block_size)
            self._noise_detector_low = GoertzelFilter(
                _clamp(self._active_tone_freq - max(150.0, self.bandwidth_hz), 150.0, 2000.0),
                self.sample_rate,
                self._block_size,
            )
            self._noise_detector_high = GoertzelFilter(
                _clamp(self._active_tone_freq + max(150.0, self.bandwidth_hz), 150.0, 2000.0),
                self.sample_rate,
                self._block_size,
            )

        # AGC for weak HF/direct-sampling signals.
        self._agc_target = 0.22
        self._agc_gain = 1.0
        self._agc_alpha = 0.06

        # Envelope smoothing.
        # OOK has clean binary transitions; use symmetric fast alpha.
        # HF CW has gradual fading (QSB); use asymmetric slower release.
        if self.detect_mode == "envelope":
            self._attack_alpha = 0.55
            self._release_alpha = 0.55
        else:
            self._attack_alpha = 0.55
            self._release_alpha = 0.45
        self._envelope = 0.0

        # Adaptive threshold model.
        self._noise_floor = 0.0
        self._signal_peak = 0.0
        self._threshold = 0.0
        self._hysteresis = 0.12

        # Warm-up bootstrap.
        self._WARMUP_BLOCKS = 16
        self._SETTLE_BLOCKS = 140
        self._mag_min = float("inf")
        self._mag_max = 0.0
        self._blocks_processed = 0

        # Timing model (in block units, kept for backward compatibility with tests).
        dit_sec = 1.2 / max(self.wpm, 1)
        dit_blocks = max(1.0, dit_sec / self._block_duration)
        self._dah_threshold = 2.2 * dit_blocks
        self._dit_min = 0.38 * dit_blocks
        if self.detect_mode == "envelope":
            # Tighter gaps for OOK — clean binary transitions tolerate this.
            self._char_gap = 2.0 * dit_blocks
            self._word_gap = 5.0 * dit_blocks
        else:
            self._char_gap = 2.6 * dit_blocks
            self._word_gap = 6.0 * dit_blocks
        self._dit_observations: deque[float] = deque(maxlen=32)
        self._estimated_wpm = float(self.wpm)

        # State machine.
        self._tone_on = False
        self._tone_blocks = 0.0
        self._silence_blocks = 0.0
        self._current_symbol = ""
        self._pending_buffer: list[int] = []

        # Dropout tolerance: bridge brief signal dropouts mid-element (~40ms).
        self._dropout_blocks: float = 0.0
        self._dropout_tolerance: float = 2.0

        # Output / diagnostics.
        self._last_level = 0.0
        self._last_noise_ref = 0.0

    def reset_calibration(self) -> None:
        """Reset adaptive threshold and timing estimator state."""
        self._noise_floor = 0.0
        self._signal_peak = 0.0
        self._threshold = 0.0
        self._mag_min = float("inf")
        self._mag_max = 0.0
        self._blocks_processed = 0
        self._dit_observations.clear()
        self._estimated_wpm = float(self.wpm)
        self._tone_on = False
        self._tone_blocks = 0.0
        self._silence_blocks = 0.0
        self._current_symbol = ""

    def get_metrics(self) -> dict[str, float | bool]:
        """Return latest decoder metrics for UI/status messages."""
        metrics: dict[str, Any] = {
            "wpm": float(self._estimated_wpm),
            "tone_freq": float(self._active_tone_freq),
            "level": float(self._last_level),
            "noise_floor": float(self._noise_floor),
            "threshold": float(self._threshold),
            "tone_on": bool(self._tone_on),
            "dit_ms": float((self._effective_dit_blocks() * self._block_duration) * 1000.0),
            "detect_mode": self.detect_mode,
        }
        if self.detect_mode == "envelope":
            metrics["snr"] = 0.0
            metrics["noise_ref"] = 0.0
            metrics["snr_on"] = 0.0
            metrics["snr_off"] = 0.0
        else:
            snr_mult = max(1.15, self.threshold_multiplier * 0.5)
            snr_on = snr_mult * (1.0 + self._hysteresis)
            snr_off = snr_mult * (1.0 - self._hysteresis)
            metrics["snr"] = float(self._last_level / max(self._noise_floor, 1e-6))
            metrics["noise_ref"] = float(self._noise_floor)
            metrics["snr_on"] = float(snr_on)
            metrics["snr_off"] = float(snr_off)
        return metrics

    def _rebuild_detectors(self) -> None:
        """Rebuild target/noise Goertzel filters after tone updates."""
        if self.detect_mode == "envelope":
            return  # Envelope detector is frequency-agnostic
        self._detector = GoertzelFilter(self._active_tone_freq, self.sample_rate, self._block_size)
        ref_offset = max(150.0, self.bandwidth_hz)
        self._noise_detector_low = GoertzelFilter(
            _clamp(self._active_tone_freq - ref_offset, 150.0, 2000.0),
            self.sample_rate,
            self._block_size,
        )
        self._noise_detector_high = GoertzelFilter(
            _clamp(self._active_tone_freq + ref_offset, 150.0, 2000.0),
            self.sample_rate,
            self._block_size,
        )

    def _estimate_tone_frequency(
        self,
        normalized: np.ndarray,
        signal_mag: float,
        noise_ref: float,
    ) -> bool:
        """Track dominant CW tone in a local window when a valid tone is present.

        Returns True when the detector frequency changed.
        """
        if not self.auto_tone_track or self.tone_lock:
            return False

        # Skip retunes when the detector is mostly seeing noise.
        if signal_mag <= max(noise_ref * 1.8, 0.02):
            return False

        lo = _clamp(self._active_tone_freq - self._tone_scan_range_hz, 300.0, 1200.0)
        hi = _clamp(self._active_tone_freq + self._tone_scan_range_hz, 300.0, 1200.0)
        if hi <= lo:
            return False

        best_freq = self._active_tone_freq
        best_mag = float(signal_mag)

        freq = lo
        while freq <= hi + 1e-6:
            mag = _goertzel_mag(normalized, freq, self.sample_rate)
            if mag > best_mag:
                best_mag = mag
                best_freq = freq
            freq += self._tone_scan_step_hz

        # Require a meaningful improvement before moving off the current tone.
        if best_mag <= (signal_mag * 1.12):
            return False

        # Smooth and cap per-step movement to avoid jumps on noisy windows.
        delta = _clamp(best_freq - self._active_tone_freq, -30.0, 30.0)
        smoothed = self._active_tone_freq + (0.35 * delta)
        # Do not drift too far from the configured tone unless the user retunes.
        smoothed = _clamp(
            smoothed,
            max(300.0, self._tone_anchor_freq - 240.0),
            min(1200.0, self._tone_anchor_freq + 240.0),
        )

        if abs(smoothed - self._active_tone_freq) >= 2.5:
            self._active_tone_freq = smoothed
            self._rebuild_detectors()
            return True
        return False

    def _effective_dit_blocks(self) -> float:
        """Return current dit estimate in block units."""
        if self.wpm_mode == "manual" or self.wpm_lock:
            wpm = max(5.0, min(50.0, float(self.wpm)))
            dit_blocks = max(1.0, (1.2 / wpm) / self._block_duration)
            self._estimated_wpm = wpm
            return dit_blocks

        if self._dit_observations:
            ordered = sorted(self._dit_observations)
            mid = ordered[len(ordered) // 2]
            dit_blocks = max(1.0, float(mid))
            est_wpm = 1.2 / (dit_blocks * self._block_duration)
            self._estimated_wpm = _clamp(est_wpm, 5.0, 60.0)
            return dit_blocks

        self._estimated_wpm = float(self.wpm)
        return max(1.0, (1.2 / max(self.wpm, 1)) / self._block_duration)

    def _record_dit_candidate(self, blocks: float) -> None:
        """Feed a possible dit duration into the estimator."""
        if blocks <= 0:
            return
        if self.wpm_mode == "manual" or self.wpm_lock:
            return
        if blocks > 20:
            return
        self._dit_observations.append(float(blocks))

    def _decode_symbol(self, symbol: str, timestamp: str) -> dict[str, Any] | None:
        char = MORSE_TABLE.get(symbol)
        if char is None:
            return None
        return {
            "type": "morse_char",
            "char": char,
            "morse": symbol,
            "timestamp": timestamp,
        }

    def process_block(self, pcm_bytes: bytes) -> list[dict[str, Any]]:
        """Process PCM bytes and return decode/scope events."""
        events: list[dict[str, Any]] = []

        n_samples = len(pcm_bytes) // 2
        if n_samples <= 0:
            return events

        pending = np.concatenate(
            (
                np.asarray(self._pending_buffer, dtype=np.int64),
                np.frombuffer(pcm_bytes, dtype="<i2", count=n_samples),
            )
        )
        n_blocks = len(pending) // self._block_size
        n_used = n_blocks * self._block_size
        self._pending_buffer = pending[n_used:].tolist()
        blocks = pending[:n_used].reshape(n_blocks, self._block_size) / 32768.0
        block_rms = np.sqrt(np.mean(np.square(blocks), axis=1)).tolist()

        amplitudes: list[float] = []

        for block, rms in zip(blocks, block_rms):
            # AGC
            if rms > 1e-7:
                desired_gain = self._agc_target / rms
                self._agc_gain += self._agc_alpha * (desired_gain - self._agc_gain)
                self._agc_gain = _clamp(self._agc_gain, 0.2, 450.0)
            normalized = block * self._agc_gain

            self._blocks_processed += 1

            mag = self._detector.magnitude(normalized)

            if self.detect_mode == "envelope":
                # Envelope mode: direct magnitude threshold, no noise detectors
                noise_ref = 0.0
                level = float(mag)
                alpha = self._attack_alpha if level >= self._envelope else self._release_alpha
                self._envelope += alpha * (level - self._envelope)
                self._last_level = self._envelope
                self._last_noise_ref = 0.0
                amplitudes.append(level)

                if self._blocks_processed <= self._WARMUP_BLOCKS:
                    self._mag_min = min(self._mag_min, level)
                    self._mag_max = max(self._mag_max, level)
                    if self._blocks_processed == self._WARMUP_BLOCKS:
                        self._noise_floor = self._mag_min if math.isfinite(self._mag_min) else 0.0
                        if self._mag_max <= (self._noise_floor * 1.2):
                            self._signal_peak = max(self._noise_floor + 0.5, self._noise_floor * 2.5)
                        else:
                            self._signal_peak = max(self._mag_max, self._noise_floor * 1.8)
                        self._threshold = self._noise_floor + 0.22 * (self._signal_peak - self._noise_floor)
                    tone_detected = False
                else:
                    settle_alpha = (
                        0.30 if self._blocks_processed < (self._WARMUP_BLOCKS + self._SETTLE_BLOCKS) else 0.06
                    )
                    if level <= self._threshold:
                        self._noise_floor += settle_alpha * (level - self._noise_floor)
                    else:
                        self._signal_peak += settle_alpha * (level - self._signal_peak)
                    self._signal_peak = max(self._signal_peak, self._noise_floor * 1.05)

                    if self.threshold_mode == "manual":
                        self._threshold = max(0.0, self.manual_threshold)
                    else:
                        self._threshold = (
                            max(0.0, self._noise_floor * self.threshold_multiplier) + self.threshold_offset
                        )
                        self._threshold = max(self._threshold, self._noise_floor + 0.35)

                    dynamic_span = max(0.0, self._signal_peak - self._noise_floor)
                    gate_level = self._noise_floor + (self.min_signal_gate * dynamic_span)
                    gate_ok = self.min_signal_gate <= 0.0 or level >= gate_level

                    # Direct magnitude threshold with hysteresis (no SNR)
                    if self._tone_on:
                        tone_detected = gate_ok and level >= (self._threshold * (1.0 - self._hysteresis))
                    else:
                        tone_detected = gate_ok and level >= (self._threshold * (1.0 + self._hysteresis))
            else:
                # Goertzel mode: SNR-based tone detection with noise reference
                noise_low = self._noise_detector_low.magnitude(normalized)
                noise_high = self._noise_detector_high.magnitude(normalized)
                noise_ref = max(1e-9, (noise_low + noise_high) * 0.5)

                if (
                    self.auto_tone_track
                    and not self.tone_lock
                    and self._blocks_processed > self._WARMUP_BLOCKS
                    and (self._blocks_processed % self._tone_scan_interval_blocks == 0)
                    and self._estimate_tone_frequency(normalized, mag, noise_ref)
                ):
                    # Detector changed; refresh magnitudes for this window.
                    mag = self._detector.magnitude(normalized)
                    noise_low = self._noise_detector_low.magnitude(normalized)
                    noise_high = self._noise_detector_high.magnitude(normalized)
                    noise_ref = max(1e-9, (noise_low + noise_high) * 0.5)

                level = float(mag)
                alpha = self._attack_alpha if level >= self._envelope else self._release_alpha
                self._envelope += alpha * (level - self._envelope)
                self._last_level = self._envelope
                self._last_noise_ref = noise_ref
                amplitudes.append(level)

                if self._blocks_processed <= self._WARMUP_BLOCKS:
                    self._mag_min = min(self._mag_min, level)
                    self._mag_max = max(self._mag_max, level)
                    if self._blocks_processed == self._WARMUP_BLOCKS:
                        self._noise_floor = self._mag_min if math.isfinite(self._mag_min) else 0.0
                        if self._mag_max <= (self._noise_floor * 1.2):
                            self._signal_peak = max(self._noise_floor + 0.5, self._noise_floor * 2.5)
                        else:
                            self._signal_peak = max(self._mag_max, self._noise_floor * 1.8)
                        self._threshold = self._noise_floor + 0.22 * (self._signal_peak - self._noise_floor)
                    tone_detected = False
                else:
                    settle_alpha = (
                        0.30 if self._blocks_processed < (self._WARMUP_BLOCKS + self._SETTLE_BLOCKS) else 0.06
                    )

                    detector_level = level

                    if detector_level <= self._threshold:
                        self._noise_floor += settle_alpha * (detector_level - self._noise_floor)
                    else:
                        self._signal_peak += settle_alpha * (detector_level - self._signal_peak)

                    self._signal_peak = max(self._signal_peak, self._noise_floor * 1.05)

                    # Blend adjacent-band noise reference into noise floor.
                    self._noise_floor += (settle_alpha * 0.25) * (noise_ref - self._noise_floor)

                    if self.threshold_mode == "manual":
                        self._threshold = max(0.0, self.manual_threshold)
                    else:
                        self._threshold = (
                            max(0.0, self._noise_floor * self.threshold_multiplier) + self.threshold_offset
                        )
                        self._threshold = max(self._threshold, self._noise_floor + 0.35)

                    dynamic_span = max(0.0, self._signal_peak - self._noise_floor)
                    gate_level = self._noise_floor + (self.min_signal_gate * dynamic_span)
                    gate_ok = self.min_signal_gate <= 0.0 or detector_level >= gate_level

                    # SNR-based tone detection (gain-invariant).
                    snr = level / max(noise_ref, 1e-6)
                    snr_mult = max(1.15, self.threshold_multiplier * 0.5)
                    snr_on = snr_mult * (1.0 + self._hysteresis)
                    snr_off = snr_mult * (1.0 - self._hysteresis)

                    if self._tone_on:
                        tone_detected = gate_ok and snr >= snr_off
                    else:
                        tone_detected = gate_ok and snr >= snr_on

            dit_blocks = self._effective_dit_blocks()
            self._dah_threshold = 2.2 * dit_blocks
            self._dit_min = max(1.0, 0.38 * dit_blocks)
            if self.detect_mode == "envelope":
                self._char_gap = 2.0 * dit_blocks
                self._word_gap = 5.0 * dit_blocks
            else:
                self._char_gap = 2.6 * dit_blocks
                self._word_gap = 6.0 * dit_blocks

            if tone_detected and not self._tone_on:
                # Tone edge up.
                self._tone_on = True
                self._dropout_blocks = 0.0
                silence_count = self._silence_blocks
                self._silence_blocks = 0.0
                self._tone_blocks = 0.0

                if self._current_symbol and silence_count >= self._char_gap:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    decoded = self._decode_symbol(self._current_symbol, timestamp)
                    if decoded is not None:
                        events.append(decoded)

                    if silence_count >= self._word_gap:
                        events.append(
                            {
                                "type": "morse_space",
                                "timestamp": timestamp,
                            }
                        )
                        events.append(
                            {
                                "type": "morse_gap",
                                "gap": "word",
                                "duration_ms": round(silence_count * self._block_duration * 1000.0, 1),
                            }
                        )
                    else:
                        events.append(
                            {
                                "type": "morse_gap",
                                "gap": "char",
                                "duration_ms": round(silence_count * self._block_duration * 1000.0, 1),
                            }
                        )

                    self._current_symbol = ""
                elif silence_count >= 1.0:
                    # Intra-symbol gap candidate improves dit estimate for Farnsworth-style spacing.
                    if silence_count <= (self._char_gap * 0.95):
                        self._record_dit_candidate(silence_count)

            elif (not tone_detected) and self._tone_on:
                # Possible tone dropout — tolerate brief gaps before confirming edge-down.
                self._dropout_blocks += 1.0
                if self._dropout_blocks <= self._dropout_tolerance:
                    continue

                # Confirmed tone edge down — dropout was genuine silence, not a glitch.
                self._tone_on = False
                tone_count = max(1.0, self._tone_blocks)
                self._silence_blocks = self._dropout_blocks
                self._tone_blocks = 0.0
                self._dropout_blocks = 0.0

                element = ""
                if tone_count >= self._dah_threshold:
                    element = "-"
                elif tone_count >= self._dit_min:
                    element = "."

                if element:
                    self._current_symbol += element
                    events.append(
                        {
                            "type": "morse_element",
                            "element": element,
                            "duration_ms": round(tone_count * self._block_duration * 1000.0, 1),
                        }
                    )
                    if element == ".":
                        self._record_dit_candidate(tone_count)
                    elif tone_count <= (self._dah_threshold * 1.6):
                        # Some operators send short-ish dahs; still useful for tracking.
                        self._record_dit_candidate(tone_count / 3.0)

            elif tone_detected and self._tone_on:
                # Recover any dropout blocks — tone resumed, so they were part of the element.
                self._tone_blocks += self._dropout_blocks + 1.0
                self._dropout_blocks = 0.0

            elif (not tone_detected) and (not self._tone_on):
                self._silence_blocks += 1.0

        if amplitudes:
            scope_event: dict[str, Any] = {
                "type": "scope",
                "amplitudes": amplitudes,
                "threshold": self._threshold,
                "tone_on": self._tone_on,
                "tone_freq": round(self._active_tone_freq, 1),
                "level": self._last_level,
                "noise_floor": self._noise_floor,
                "wpm": round(self._estimated_wpm, 1),
                "dit_ms": round(self._effective_dit_blocks() * self._block_duration * 1000.0, 1),
                "detect_mode": self.detect_mode,
            }
            if self.detect_mode == "envelope":
                scope_event["snr"] = 0.0
                scope_event["noise_ref"] = 0.0
                scope_event["snr_on"] = 0.0
                scope_event["snr_off"] = 0.0
            else:
                snr_mult = max(1.15, self.threshold_multiplier * 0.5)
                snr_on = snr_mult * (1.0 + self._hysteresis)
                snr_off = snr_mult * (1.0 - self._hysteresis)
                scope_event["snr"] = round(self._last_level / max(self._last_noise_ref, 1e-6), 2)
                scope_event["noise_ref"] = round(self._last_noise_ref, 4)
                scope_event["snr_on"] = round(snr_on, 2)
                scope_event["snr_off"] = round(snr_off, 2)
            events.append(scope_event)

        return events

    def flush(self) -> list[dict[str, Any]]:
        """Flush pending symbols at end-of-stream."""
        events: list[dict[str, Any]] = []

        if self._tone_on and (self._tone_blocks + self._dropout_blocks) >= self._dit_min:
            tone_count = self._tone_blocks + self._dropout_blocks
            element = "-" if tone_count >= self._dah_threshold else "."
            self._current_symbol += element
            events.append(
                {
                    "type": "morse_element",
                    "element": element,
                    "duration_ms": round(tone_count * self._block_duration * 1000.0, 1),
                }
            )

        if self._current_symbol:
            decoded = self._decode_symbol(self._current_symbol, datetime.now().strftime("%H:%M:%S"))
            if decoded is not None:
                events.append(decoded)
            self._current_symbol = ""

        self._tone_on = False
        self._tone_blocks = 0.0
        self._silence_blocks = 0.0
        self._dropout_blocks = 0.0
        return events


def _wav_to_mono_float(path: Path) -> tuple[np.ndarray, int]:
    """Load WAV file and return mono float32 samples in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 1:
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        pcm = (pcm - 128.0) / 128.0
    elif sampwidth == 2:
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif sampwidth == 4:
        pcm = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth * 8} bits")

    if n_channels > 1:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1)

    return pcm.astype(np.float64), int(sample_rate)


def _resample_linear(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear resampler with no extra dependencies."""
    if from_rate == to_rate or len(samples) == 0:
        return samples

    ratio = float(to_rate) / float(from_rate)
    new_len = max(1, int(round(len(samples) * ratio)))
    x_old = np.linspace(0.0, 1.0, len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float64)


def decode_morse_wav_file(
    wav_path: str | Path,
    *,
    sample_rate: int = 8000,
    tone_freq: float = 700.0,
    wpm: int = 15,
    bandwidth_hz: int = 200,
    auto_tone_track: bool = True,
    tone_lock: bool = False,
    threshold_mode: str = "auto",
    manual_threshold: float = 0.0,
    threshold_multiplier: float = 2.8,
    threshold_offset: float = 0.0,
    wpm_mode: str = "auto",
    wpm_lock: bool = False,
    min_signal_gate: float = 0.0,
) -> dict[str, Any]:
    """Decode Morse from a WAV file and return text/events/metrics."""
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV file not found: {path}")

    audio, file_rate = _wav_to_mono_float(path)
    if file_rate != sample_rate:
        audio = _resample_linear(audio, file_rate, sample_rate)

    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)

    decoder = MorseDecoder(
        sample_rate=sample_rate,
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
        min_signal_gate=min_signal_gate,
    )

    events: list[dict[str, Any]] = []
    chunk_samples = 2048
    idx = 0
    while idx < len(pcm16):
        chunk = pcm16[idx : idx + chunk_samples]
        if len(chunk) == 0:
            break
        events.extend(decoder.process_block(chunk.tobytes()))
        idx += chunk_samples

    events.extend(decoder.flush())

    text_parts: list[str] = []
    raw_parts: list[str] = []
    for event in events:
        et = event.get("type")
        if et == "morse_char":
            text_parts.append(str(event.get("char", "")))
        elif et == "morse_space":
            text_parts.append(" ")
        elif et == "morse_element":
            raw_parts.append(str(event.get("element", "")))
        elif et == "morse_gap":
            gap = str(event.get("gap", ""))
            if gap == "char":
                raw_parts.append(" / ")
            elif gap == "word":
                raw_parts.append(" // ")

    text = "".join(text_parts)
    raw = "".join(raw_parts).strip()

    return {
        "text": text,
        "raw": raw,
        "events": events,
        "metrics": decoder.get_metrics(),
    }


def _drain_control_queue(control_queue: queue.Queue | None, decoder: MorseDecoder) -> bool:
    """Process pending control commands; return False to request shutdown."""
    if control_queue is None:
        return True

    keep_running = True
    while True:
        try:
            cmd = control_queue.get_nowait()
        except queue.Empty:
            break

        if not isinstance(cmd, dict):
            continue
        action = str(cmd.get("cmd", "")).strip().lower()
        if action == "reset":
            decoder.reset_calibration()
        elif action in {"shutdown", "stop"}:
            keep_running = False

    return keep_running


def _emit_waiting_scope(output_queue: queue.Queue, waiting_since: float) -> None:
    """Emit waiting heartbeat while no PCM arrives."""
    with contextlib.suppress(queue.Full):
        output_queue.put_nowait(
            {
                "type": "scope",
                "amplitudes": [],
                "threshold": 0,
                "tone_on": False,
                "waiting": True,
                "waiting_seconds": round(max(0.0, time.monotonic() - waiting_since), 1),
            }
        )


def _is_probably_rtl_log_text(data: bytes) -> bool:
    """Heuristic: identify rtl_fm stderr log chunks when streams are merged."""
    if not data:
        return False
    # PCM usually contains NULLs/non-printables; plain log lines do not.
    if b"\x00" in data:
        return False
    printable = sum(1 for b in data if (32 <= b <= 126) or b in (9, 10, 13))
    ratio = printable / max(1, len(data))
    if ratio < 0.92:
        return False
    lower = data.lower()
    keywords = (
        b"rtl_fm",
        b"found ",
        b"using device",
        b"tuned to",
        b"sampling at",
        b"output at",
        b"buffer size",
        b"gain",
        b"direct sampling",
        b"oversampling",
        b"exact sample rate",
    )
    return any(token in lower for token in keywords)


def morse_decoder_thread(
    rtl_stdout,
    output_queue: queue.Queue,
    stop_event: threading.Event,
    sample_rate: int = 8000,
    tone_freq: float = 700.0,
    wpm: int = 15,
    decoder_config: dict[str, Any] | None = None,
    control_queue: queue.Queue | None = None,
    pcm_ready_event: threading.Event | None = None,
    stream_ready_event: threading.Event | None = None,
    strip_text_chunks: bool = False,
) -> None:
    """Decode Morse from live PCM stream and push events to *output_queue*."""
    import logging

    logger = logging.getLogger("intercept.morse")

    CHUNK = 4096
    SCOPE_INTERVAL = 0.10
    WAITING_INTERVAL = 0.25
    STALLED_AFTER_DATA_SECONDS = 1.5

    cfg = dict(decoder_config or {})
    decoder = MorseDecoder(
        sample_rate=int(cfg.get("sample_rate", sample_rate)),
        tone_freq=float(cfg.get("tone_freq", tone_freq)),
        wpm=int(cfg.get("wpm", wpm)),
        bandwidth_hz=int(cfg.get("bandwidth_hz", 200)),
        auto_tone_track=_coerce_bool(cfg.get("auto_tone_track", True), True),
        tone_lock=_coerce_bool(cfg.get("tone_lock", False), False),
        threshold_mode=_normalize_threshold_mode(cfg.get("threshold_mode", "auto")),
        manual_threshold=float(cfg.get("manual_threshold", 0.0) or 0.0),
        threshold_multiplier=float(cfg.get("threshold_multiplier", 2.8) or 2.8),
        threshold_offset=float(cfg.get("threshold_offset", 0.0) or 0.0),
        wpm_mode=_normalize_wpm_mode(cfg.get("wpm_mode", "auto")),
        wpm_lock=_coerce_bool(cfg.get("wpm_lock", False), False),
        min_signal_gate=float(cfg.get("min_signal_gate", 0.0) or 0.0),
        detect_mode=str(cfg.get("detect_mode", "goertzel")),
    )

    last_scope = time.monotonic()
    last_waiting_emit = 0.0
    waiting_since: float | None = None
    last_pcm_at: float | None = None
    pcm_bytes = 0
    pcm_report_at = time.monotonic()
    first_pcm_logged = False
    reader_done = threading.Event()
    reader_thread: threading.Thread | None = None
    first_raw_logged = False

    raw_queue: queue.Queue[bytes] = queue.Queue(maxsize=96)

    try:

        def _reader_loop() -> None:
            """Blocking PCM reader isolated from decode/control loop."""
            nonlocal first_raw_logged
            try:
                fd = None
                with contextlib.suppress(Exception):
                    fd = rtl_stdout.fileno()
                while not stop_event.is_set():
                    try:
                        if fd is not None:
                            ready, _, _ = select.select([fd], [], [], 0.20)
                            if not ready:
                                continue
                            data = os.read(fd, CHUNK)
                        elif hasattr(rtl_stdout, "read1"):
                            data = rtl_stdout.read1(CHUNK)
                        else:
                            data = rtl_stdout.read(CHUNK)
                    except Exception as e:
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(
                                {
                                    "type": "info",
                                    "text": f"[pcm] reader error: {e}",
                                }
                            )
                        break

                    if data is None:
                        continue

                    if not data:
                        break

                    if not first_raw_logged:
                        first_raw_logged = True
                        if stream_ready_event is not None:
                            stream_ready_event.set()
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(
                                {
                                    "type": "info",
                                    "text": f"[pcm] first raw chunk: {len(data)} bytes",
                                }
                            )

                    if strip_text_chunks and _is_probably_rtl_log_text(data):
                        try:
                            text = data.decode("utf-8", errors="replace")
                        except Exception:
                            text = ""
                        if text:
                            for line in text.splitlines():
                                clean = line.strip()
                                if not clean:
                                    continue
                                with contextlib.suppress(queue.Full):
                                    output_queue.put_nowait(
                                        {
                                            "type": "info",
                                            "text": f"[rtl_fm] {clean}",
                                        }
                                    )
                        continue

                    try:
                        raw_queue.put(data, timeout=0.2)
                    except queue.Full:
                        # Keep latest PCM flowing even if downstream hiccups.
                        with contextlib.suppress(queue.Empty):
                            raw_queue.get_nowait()
                        with contextlib.suppress(queue.Full):
                            raw_queue.put_nowait(data)
            finally:
                reader_done.set()
                with contextlib.suppress(queue.Full):
                    raw_queue.put_nowait(b"")

        reader_thread = threading.Thread(
            target=_reader_loop,
            daemon=True,
            name="morse-pcm-reader",
        )
        reader_thread.start()

        while not stop_event.is_set():
            if not _drain_control_queue(control_queue, decoder):
                break

            try:
                data = raw_queue.get(timeout=0.20)
            except queue.Empty:
                now = time.monotonic()
                should_emit_waiting = False
                if last_pcm_at is None or (now - last_pcm_at) >= STALLED_AFTER_DATA_SECONDS:
                    should_emit_waiting = True

                if should_emit_waiting and waiting_since is None:
                    waiting_since = now
                if should_emit_waiting and now - last_waiting_emit >= WAITING_INTERVAL:
                    last_waiting_emit = now
                    _emit_waiting_scope(output_queue, waiting_since)

                if reader_done.is_set():
                    break
                continue

            if not data:
                if reader_done.is_set() and last_pcm_at is None:
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait(
                            {
                                "type": "info",
                                "text": "[pcm] stream ended before samples were received",
                            }
                        )
                break

            waiting_since = None
            last_pcm_at = time.monotonic()
            pcm_bytes += len(data)

            if not first_pcm_logged:
                first_pcm_logged = True
                if pcm_ready_event is not None:
                    pcm_ready_event.set()
                with contextlib.suppress(queue.Full):
                    output_queue.put_nowait(
                        {
                            "type": "info",
                            "text": f"[pcm] first chunk: {len(data)} bytes",
                        }
                    )

            events = decoder.process_block(data)
            for event in events:
                if event.get("type") == "scope":
                    now = time.monotonic()
                    if now - last_scope >= SCOPE_INTERVAL:
                        last_scope = now
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(event)
                else:
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait(event)

            now = time.monotonic()
            if (now - pcm_report_at) >= 1.0:
                kbps = (pcm_bytes * 8.0) / max(1e-6, (now - pcm_report_at)) / 1000.0
                with contextlib.suppress(queue.Full):
                    output_queue.put_nowait(
                        {
                            "type": "info",
                            "text": f"[pcm] {pcm_bytes} B in {now - pcm_report_at:.1f}s ({kbps:.1f} kbps)",
                        }
                    )
                pcm_bytes = 0
                pcm_report_at = now

    except Exception as e:  # pragma: no cover - defensive runtime guard
        logger.debug(f"Morse decoder thread error: {e}")
        with contextlib.suppress(queue.Full):
            output_queue.put_nowait(
                {
                    "type": "info",
                    "text": f"[pcm] decoder thread error: {e}",
                }
            )
    finally:
        stop_event.set()
        if reader_thread is not None:
            reader_thread.join(timeout=0.35)

        for event in decoder.flush():
            with contextlib.suppress(queue.Full):
                output_queue.put_nowait(event)

        with contextlib.suppress(queue.Full):
            output_queue.put_nowait(
                {
                    "type": "status",
                    "status": "stopped",
                    "metrics": decoder.get_metrics(),
                }
            )


def _cu8_to_complex(raw: bytes) -> np.ndarray:
    """Convert interleaved unsigned 8-bit IQ to complex64 samples."""
    if len(raw) < 2:
        return np.empty(0, dtype=np.complex64)
    usable = len(raw) - (len(raw) % 2)
    if usable <= 0:
        return np.empty(0, dtype=np.complex64)
    u8 = np.frombuffer(raw[:usable], dtype=np.uint8).astype(np.float32)
    i = (u8[0::2] - 127.5) / 128.0
    q = (u8[1::2] - 127.5) / 128.0
    return (i + 1j * q).astype(np.complex64)


def _iq_usb_to_pcm16(
    iq_samples: np.ndarray,
    iq_sample_rate: int,
    audio_sample_rate: int,
) -> bytes:
    """Minimal USB demod from complex IQ to 16-bit PCM."""
    if iq_samples.size < 16 or iq_sample_rate <= 0 or audio_sample_rate <= 0:
        return b""

    audio = np.real(iq_samples).astype(np.float64)
    audio -= float(np.mean(audio))

    # Cheap decimation first, then linear resample for exact output rate.
    decim = max(1, int(iq_sample_rate // max(audio_sample_rate, 1)))
    if decim > 1:
        usable = (audio.size // decim) * decim
        if usable < decim:
            return b""
        audio = audio[:usable].reshape(-1, decim).mean(axis=1)
    fs1 = float(iq_sample_rate) / float(decim)
    if audio.size < 8:
        return b""

    taps = int(max(1, min(31, fs1 / 12000.0)))
    if taps > 1:
        kernel = np.ones(taps, dtype=np.float64) / float(taps)
        audio = np.convolve(audio, kernel, mode="same")

    if abs(fs1 - float(audio_sample_rate)) > 1.0:
        out_len = int(audio.size * float(audio_sample_rate) / fs1)
        if out_len < 8:
            return b""
        x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False, dtype=np.float64)
        x_new = np.linspace(0.0, 1.0, out_len, endpoint=False, dtype=np.float64)
        audio = np.interp(x_new, x_old, audio)

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.0:
        audio = audio * min(8.0, 0.85 / peak)

    pcm = np.clip(audio, -1.0, 1.0)
    return (pcm * 32767.0).astype(np.int16).tobytes()


def morse_iq_decoder_thread(
    iq_stdout,
    output_queue: queue.Queue,
    stop_event: threading.Event,
    iq_sample_rate: int,
    sample_rate: int = 22050,
    tone_freq: float = 700.0,
    wpm: int = 15,
    decoder_config: dict[str, Any] | None = None,
    control_queue: queue.Queue | None = None,
    pcm_ready_event: threading.Event | None = None,
    stream_ready_event: threading.Event | None = None,
) -> None:
    """Decode Morse from raw IQ (cu8) by in-process USB demodulation."""
    import logging

    logger = logging.getLogger("intercept.morse")

    CHUNK = 65536
    SCOPE_INTERVAL = 0.10
    WAITING_INTERVAL = 0.25
    STALLED_AFTER_DATA_SECONDS = 1.5

    cfg = dict(decoder_config or {})
    decoder = MorseDecoder(
        sample_rate=int(cfg.get("sample_rate", sample_rate)),
        tone_freq=float(cfg.get("tone_freq", tone_freq)),
        wpm=int(cfg.get("wpm", wpm)),
        bandwidth_hz=int(cfg.get("bandwidth_hz", 200)),
        auto_tone_track=_coerce_bool(cfg.get("auto_tone_track", True), True),
        tone_lock=_coerce_bool(cfg.get("tone_lock", False), False),
        threshold_mode=_normalize_threshold_mode(cfg.get("threshold_mode", "auto")),
        manual_threshold=float(cfg.get("manual_threshold", 0.0) or 0.0),
        threshold_multiplier=float(cfg.get("threshold_multiplier", 2.8) or 2.8),
        threshold_offset=float(cfg.get("threshold_offset", 0.0) or 0.0),
        wpm_mode=_normalize_wpm_mode(cfg.get("wpm_mode", "auto")),
        wpm_lock=_coerce_bool(cfg.get("wpm_lock", False), False),
        min_signal_gate=float(cfg.get("min_signal_gate", 0.0) or 0.0),
        detect_mode=str(cfg.get("detect_mode", "goertzel")),
    )

    last_scope = time.monotonic()
    last_waiting_emit = 0.0
    waiting_since: float | None = None
    last_pcm_at: float | None = None
    pcm_bytes = 0
    pcm_report_at = time.monotonic()
    first_pcm_logged = False
    reader_done = threading.Event()
    reader_thread: threading.Thread | None = None
    first_raw_logged = False

    raw_queue: queue.Queue[bytes] = queue.Queue(maxsize=96)

    try:

        def _reader_loop() -> None:
            nonlocal first_raw_logged
            try:
                fd = None
                with contextlib.suppress(Exception):
                    fd = iq_stdout.fileno()
                while not stop_event.is_set():
                    try:
                        if fd is not None:
                            ready, _, _ = select.select([fd], [], [], 0.20)
                            if not ready:
                                continue
                            data = os.read(fd, CHUNK)
                        elif hasattr(iq_stdout, "read1"):
                            data = iq_stdout.read1(CHUNK)
                        else:
                            data = iq_stdout.read(CHUNK)
                    except Exception as e:
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(
                                {
                                    "type": "info",
                                    "text": f"[iq] reader error: {e}",
                                }
                            )
                        break

                    if data is None:
                        continue
                    if not data:
                        break

                    if not first_raw_logged:
                        first_raw_logged = True
                        if stream_ready_event is not None:
                            stream_ready_event.set()
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(
                                {
                                    "type": "info",
                                    "text": f"[iq] first raw chunk: {len(data)} bytes",
                                }
                            )

                    try:
                        raw_queue.put(data, timeout=0.2)
                    except queue.Full:
                        with contextlib.suppress(queue.Empty):
                            raw_queue.get_nowait()
                        with contextlib.suppress(queue.Full):
                            raw_queue.put_nowait(data)
            finally:
                reader_done.set()
                with contextlib.suppress(queue.Full):
                    raw_queue.put_nowait(b"")

        reader_thread = threading.Thread(
            target=_reader_loop,
            daemon=True,
            name="morse-iq-reader",
        )
        reader_thread.start()

        while not stop_event.is_set():
            if not _drain_control_queue(control_queue, decoder):
                break

            try:
                raw = raw_queue.get(timeout=0.20)
            except queue.Empty:
                now = time.monotonic()
                should_emit_waiting = False
                if last_pcm_at is None or (now - last_pcm_at) >= STALLED_AFTER_DATA_SECONDS:
                    should_emit_waiting = True

                if should_emit_waiting and waiting_since is None:
                    waiting_since = now
                if should_emit_waiting and now - last_waiting_emit >= WAITING_INTERVAL:
                    last_waiting_emit = now
                    _emit_waiting_scope(output_queue, waiting_since)

                if reader_done.is_set():
                    break
                continue

            if not raw:
                if reader_done.is_set() and last_pcm_at is None:
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait(
                            {
                                "type": "info",
                                "text": "[iq] stream ended before samples were received",
                            }
                        )
                break

            iq = _cu8_to_complex(raw)
            pcm = _iq_usb_to_pcm16(
                iq_samples=iq,
                iq_sample_rate=int(iq_sample_rate),
                audio_sample_rate=int(decoder.sample_rate),
            )
            if not pcm:
                continue

            waiting_since = None
            last_pcm_at = time.monotonic()
            pcm_bytes += len(pcm)

            if not first_pcm_logged:
                first_pcm_logged = True
                if pcm_ready_event is not None:
                    pcm_ready_event.set()
                with contextlib.suppress(queue.Full):
                    output_queue.put_nowait(
                        {
                            "type": "info",
                            "text": f"[pcm] first IQ demod chunk: {len(pcm)} bytes",
                        }
                    )

            events = decoder.process_block(pcm)
            for event in events:
                if event.get("type") == "scope":
                    now = time.monotonic()
                    if now - last_scope >= SCOPE_INTERVAL:
                        last_scope = now
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(event)
                else:
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait(event)

            now = time.monotonic()
            if (now - pcm_report_at) >= 1.0:
                kbps = (pcm_bytes * 8.0) / max(1e-6, (now - pcm_report_at)) / 1000.0
                with contextlib.suppress(queue.Full):
                    output_queue.put_nowait(
                        {
                            "type": "info",
                            "text": f"[pcm] {pcm_bytes} B in {now - pcm_report_at:.1f}s ({kbps:.1f} kbps)",
                        }
                    )
                pcm_bytes = 0
                pcm_report_at = now

    except Exception as e:  # pragma: no cover - runtime safety
        logger.debug(f"Morse IQ decoder thread error: {e}")
        with contextlib.suppress(queue.Full):
            output_queue.put_nowait(
                {
                    "type": "info",
                    "text": f"[iq] decoder thread error: {e}",
                }
            )
    finally:
        stop_event.set()
        if reader_thread is not None:
            reader_thread.join(timeout=0.35)

        for event in decoder.flush():
            with contextlib.suppress(queue.Full):
                output_queue.put_nowait(event)

        with contextlib.suppress(queue.Full):
            output_queue.put_nowait(
                {
                    "type": "status",
                    "status": "stopped",
                    "metrics": decoder.get_metrics(),
                }
            )
