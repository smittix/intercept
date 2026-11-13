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

        # 10 ms analysis windows: fine enough to time 30+ WPM elements.
        self._block_size = max(64, self.sample_rate // 100)
        self._block_duration = self._block_size / float(self.sample_rate)

        self._active_tone_freq = float(_clamp(self.tone_freq, 300.0, 1200.0))
        # Audio band searched for a CW beat note. Wide on purpose: a TX a
        # kHz or two off the dial still lands in the USB passband, and the
        # tracker should find it rather than decode key-click splatter.
        self._tone_scan_lo_hz = 300.0
        self._tone_scan_hi_hz = min(2900.0, 0.45 * self.sample_rate)
        self._scan_candidate_freq = 0.0
        self._tone_scan_interval_blocks = 8

        if self.detect_mode == "envelope":
            self._detector = EnvelopeDetector(self._block_size)
            self._noise_detector_low = None
            self._noise_detector_high = None
        else:
            self._detector = GoertzelFilter(self._active_tone_freq, self.sample_rate, self._block_size)
            self._noise_detector_low = GoertzelFilter(
                _clamp(self._active_tone_freq - max(150.0, self.bandwidth_hz), 150.0, 3400.0),
                self.sample_rate,
                self._block_size,
            )
            self._noise_detector_high = GoertzelFilter(
                _clamp(self._active_tone_freq + max(150.0, self.bandwidth_hz), 150.0, 3400.0),
                self.sample_rate,
                self._block_size,
            )

        # Mark/space level model: split EMAs of the per-block detector
        # magnitude. Blocks above the midpoint feed the signal peak, blocks
        # below feed the noise floor, and the on/off thresholds sit between
        # the two — detection is relative, so no AGC is needed.
        self._levels_primed = False
        self._noise_floor = 0.0
        self._signal_peak = 0.0
        self._threshold = 0.0
        self._split_alpha = 0.12
        self._peak_decay = 0.002
        self._on_fraction = 0.55
        self._off_fraction = 0.35
        # Peak must exceed floor by this factor before anything counts as
        # signal; split EMAs on pure noise settle near a 2.3x ratio.
        self._min_contrast = 3.0
        # Goertzel squelch: tone bin must beat the adjacent bins by this.
        # 1.5 tolerates the splatter of strong/clipped signals (seen on-air:
        # a local TX raised the adjacent bins enough that 2.0 chopped real
        # marks) while the contrast gate still keeps pure noise silent.
        self._snr_qualifier = 1.5

        # Smoothed level for scope/metrics display only.
        self._env_alpha = 0.5
        self._envelope = 0.0

        self._blocks_processed = 0

        # Timing model (in block units, kept for backward compatibility with tests).
        dit_sec = 1.2 / max(self.wpm, 1)
        dit_blocks = max(1.0, dit_sec / self._block_duration)
        self._dah_threshold = 2.2 * dit_blocks
        # 20 ms absolute floor: a 50 WPM dit is 24 ms, anything shorter is
        # a key click or noise blip, not keying.
        self._min_element_blocks = max(1.0, 0.02 / self._block_duration)
        self._dit_min = max(self._min_element_blocks, 0.38 * dit_blocks)
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

        # Dropout tolerance: bridge brief dropouts mid-element, scaled to the
        # dit estimate so fast CW's inter-element gaps are not swallowed.
        self._dropout_blocks: float = 0.0
        self._dropout_tolerance: float = max(1.0, min(0.45 * dit_blocks, 0.03 / self._block_duration))

        # Output / diagnostics.
        self._last_level = 0.0
        self._last_noise_ref = 0.0

    def reset_calibration(self) -> None:
        """Reset adaptive threshold and timing estimator state."""
        self._levels_primed = False
        self._noise_floor = 0.0
        self._signal_peak = 0.0
        self._threshold = 0.0
        self._blocks_processed = 0
        self._dit_observations.clear()
        self._estimated_wpm = float(self.wpm)
        self._tone_on = False
        self._tone_blocks = 0.0
        self._silence_blocks = 0.0
        self._dropout_blocks = 0.0
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
        else:
            metrics["snr"] = float(self._last_level / max(self._last_noise_ref, 1e-6))
            metrics["noise_ref"] = float(self._last_noise_ref)
        return metrics

    def _rebuild_detectors(self) -> None:
        """Rebuild target/noise Goertzel filters after tone updates."""
        if self.detect_mode == "envelope":
            return  # Envelope detector is frequency-agnostic
        self._detector = GoertzelFilter(self._active_tone_freq, self.sample_rate, self._block_size)
        ref_offset = max(150.0, self.bandwidth_hz)
        self._noise_detector_low = GoertzelFilter(
            _clamp(self._active_tone_freq - ref_offset, 150.0, 3400.0),
            self.sample_rate,
            self._block_size,
        )
        self._noise_detector_high = GoertzelFilter(
            _clamp(self._active_tone_freq + ref_offset, 150.0, 3400.0),
            self.sample_rate,
            self._block_size,
        )

    def _estimate_tone_frequency(
        self,
        normalized: np.ndarray,
        signal_mag: float,
        noise_ref: float,
    ) -> bool:
        """Track the dominant CW tone anywhere in the audio passband.

        Returns True when the detector frequency changed.
        """
        if not self.auto_tone_track or self.tone_lock:
            return False
        if self._tone_scan_hi_hz <= self._tone_scan_lo_hz:
            return False

        # Coarse FFT peak over the whole band (one vectorized op), then a
        # Goertzel confirmation at that frequency in the detector's scale.
        spec = np.abs(np.fft.rfft(normalized, n=4096))
        freqs = np.fft.rfftfreq(4096, 1.0 / self.sample_rate)
        band = (freqs >= self._tone_scan_lo_hz) & (freqs <= self._tone_scan_hi_hz)
        if not np.any(band):
            return False
        best_freq = float(freqs[band][int(np.argmax(spec[band]))])

        # A real tone puts the peak at the same frequency on consecutive
        # scans; noise puts its maximum somewhere new each time. Require
        # two agreeing scans before considering a retune, or the tracker
        # hops across noise maxima and mints phantom dits as it goes.
        candidate = self._scan_candidate_freq
        self._scan_candidate_freq = best_freq
        if abs(best_freq - candidate) > 60.0:
            return False

        best_mag = float(_goertzel_mag(normalized, best_freq, self.sample_rate))

        # Retune only on a clear tone: it must beat the current bin
        # meaningfully, stand well above the adjacent-bin noise, and be at
        # mark level — never chase noise bins during inter-element gaps.
        if (
            best_mag <= (signal_mag * 1.12)
            or best_mag <= (noise_ref * self._snr_qualifier)
            or best_mag < (0.5 * self._signal_peak)
        ):
            return False

        if best_mag >= (signal_mag * 2.0):
            # Current bin is clearly mistuned: jump straight to the tone.
            smoothed = best_freq
        else:
            # Smooth and cap per-step movement to avoid jumps on noisy windows.
            delta = _clamp(best_freq - self._active_tone_freq, -60.0, 60.0)
            smoothed = self._active_tone_freq + (0.35 * delta)
        smoothed = _clamp(smoothed, self._tone_scan_lo_hz, self._tone_scan_hi_hz)

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
            # Observations mix dit and dah durations; the lower quartile
            # lands on the dits without needing prior classification.
            dit_blocks = max(1.0, float(ordered[len(ordered) // 4]))
            est_wpm = 1.2 / (dit_blocks * self._block_duration)
            self._estimated_wpm = _clamp(est_wpm, 5.0, 60.0)
            return dit_blocks

        self._estimated_wpm = float(self.wpm)
        return max(1.0, (1.2 / max(self.wpm, 1)) / self._block_duration)

    def _record_dit_candidate(self, blocks: float) -> None:
        """Feed a tone/gap duration into the dit estimator."""
        if blocks <= 0:
            return
        if self.wpm_mode == "manual" or self.wpm_lock:
            return
        if blocks > (0.45 / self._block_duration):
            # Far longer than any supported dit (0.24 s at 5 WPM): skip.
            return
        if blocks < self._min_element_blocks:
            # Shorter than any supported dit: a click, not keying.
            return
        self._dit_observations.append(float(blocks))

    def _detect_tone(self, mag: float, noise_ref: float) -> bool:
        """Update the mark/space level model and classify this block."""
        if not self._levels_primed:
            self._levels_primed = True
            self._noise_floor = mag
            self._signal_peak = mag
        else:
            midpoint = self._noise_floor + 0.5 * (self._signal_peak - self._noise_floor)
            if mag > midpoint:
                self._signal_peak += self._split_alpha * (mag - self._signal_peak)
            else:
                self._noise_floor += self._split_alpha * (mag - self._noise_floor)
            # Slow droop so the detector re-arms after a signal disappears
            # or fades, instead of holding a stale peak forever.
            self._signal_peak += self._peak_decay * (self._noise_floor - self._signal_peak)
            if self._signal_peak < self._noise_floor:
                self._signal_peak = self._noise_floor

        span = max(0.0, self._signal_peak - self._noise_floor)
        manual = self.threshold_mode == "manual" and self.manual_threshold > 0.0
        if manual:
            on_level = self.manual_threshold * 1.1
            off_level = self.manual_threshold * 0.9
        else:
            on_level = self._noise_floor + self._on_fraction * span
            off_level = self._noise_floor + self._off_fraction * span
        self._threshold = on_level

        # Without real mark/space contrast there is only noise: stay silent.
        if not manual and self._signal_peak <= (self._noise_floor * self._min_contrast + 1e-4):
            return False

        tone = mag >= (off_level if self._tone_on else on_level)
        if tone and self.detect_mode == "goertzel":
            tone = mag >= noise_ref * self._snr_qualifier
        if tone and self.min_signal_gate > 0.0:
            tone = mag >= (self._noise_floor + self.min_signal_gate * span)
        return tone

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

        amplitudes: list[float] = []

        for block in blocks:
            self._blocks_processed += 1

            mag = float(self._detector.magnitude(block))

            if self.detect_mode == "goertzel":
                noise_low = self._noise_detector_low.magnitude(block)
                noise_high = self._noise_detector_high.magnitude(block)
                noise_ref = max(1e-9, (noise_low + noise_high) * 0.5)

                if (
                    self.auto_tone_track
                    and not self.tone_lock
                    and (self._blocks_processed % self._tone_scan_interval_blocks == 0)
                    and self._estimate_tone_frequency(block, mag, noise_ref)
                ):
                    # Detector changed; refresh magnitudes for this window.
                    mag = float(self._detector.magnitude(block))
                    noise_low = self._noise_detector_low.magnitude(block)
                    noise_high = self._noise_detector_high.magnitude(block)
                    noise_ref = max(1e-9, (noise_low + noise_high) * 0.5)
            else:
                noise_ref = 0.0

            self._envelope += self._env_alpha * (mag - self._envelope)
            self._last_level = self._envelope
            self._last_noise_ref = noise_ref
            amplitudes.append(mag)

            tone_detected = self._detect_tone(mag, noise_ref)

            dit_blocks = self._effective_dit_blocks()
            self._dah_threshold = 2.2 * dit_blocks
            self._dit_min = max(self._min_element_blocks, 0.38 * dit_blocks)
            # Bridge dropouts up to ~30 ms, never a real inter-element gap.
            self._dropout_tolerance = max(1.0, min(0.45 * dit_blocks, 0.03 / self._block_duration))
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
                    # Raw duration, dit or dah: the quartile estimator sorts
                    # them out without trusting the current classification.
                    self._record_dit_candidate(tone_count)

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
            else:
                scope_event["snr"] = round(self._last_level / max(self._last_noise_ref, 1e-6), 2)
                scope_event["noise_ref"] = round(self._last_noise_ref, 4)
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
