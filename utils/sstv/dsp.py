"""DSP utilities for SSTV decoding.

Goertzel algorithm for efficient single-frequency energy detection,
frequency estimation, and frequency-to-pixel luminance mapping.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import firwin, lfilter

from .constants import (
    FREQ_PIXEL_HIGH,
    FREQ_PIXEL_LOW,
    MIN_ENERGY_RATIO,
    SAMPLE_RATE,
)


def goertzel(samples: np.ndarray, target_freq: float, sample_rate: int = SAMPLE_RATE) -> float:
    """Compute Goertzel energy at a single target frequency.

    O(N) per frequency - more efficient than FFT when only a few
    frequencies are needed.

    Args:
        samples: Audio samples (float64, -1.0 to 1.0).
        target_freq: Frequency to detect (Hz).
        sample_rate: Sample rate (Hz).

    Returns:
        Magnitude squared (energy) at the target frequency.
    """
    n = len(samples)
    if n == 0:
        return 0.0

    # Generalized Goertzel (DTFT): use exact target frequency rather than
    # rounding to the nearest DFT bin.  This is critical for short windows
    # (e.g. 13 samples/pixel) where integer-k Goertzel quantizes all SSTV
    # pixel frequencies into 1-2 bins, making estimation impossible.
    w = 2.0 * math.pi * target_freq / sample_rate
    coeff = 2.0 * math.cos(w)

    s0 = 0.0
    s1 = 0.0
    s2 = 0.0

    if isinstance(samples, np.ndarray) and samples.dtype == np.float64 and samples.ndim == 1:
        # Run the same recurrence in C: s[k] = x[k] + coeff*s[k-1] - s[k-2]
        # is an IIR filter with denominator (1, -coeff, 1).
        s = lfilter((1.0,), (1.0, -coeff, 1.0), samples)
        s1 = s[-1]
        if n > 1:
            s2 = s[-2]
        return s1 * s1 + s2 * s2 - coeff * s1 * s2

    for sample in samples:
        s0 = sample + coeff * s1 - s2
        s2 = s1
        s1 = s0

    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def goertzel_mag(samples: np.ndarray, target_freq: float, sample_rate: int = SAMPLE_RATE) -> float:
    """Compute Goertzel magnitude (square root of energy).

    Args:
        samples: Audio samples.
        target_freq: Frequency to detect (Hz).
        sample_rate: Sample rate (Hz).

    Returns:
        Magnitude at the target frequency.
    """
    return math.sqrt(max(0.0, goertzel(samples, target_freq, sample_rate)))


def detect_tone(
    samples: np.ndarray, candidates: list[float], sample_rate: int = SAMPLE_RATE
) -> tuple[float | None, float]:
    """Detect which candidate frequency has the strongest energy.

    Args:
        samples: Audio samples.
        candidates: List of candidate frequencies (Hz).
        sample_rate: Sample rate (Hz).

    Returns:
        Tuple of (detected_frequency or None, energy_ratio).
        Returns None if no tone significantly dominates.
    """
    if len(samples) == 0 or not candidates:
        return None, 0.0

    energies = {f: goertzel(samples, f, sample_rate) for f in candidates}
    max_freq = max(energies, key=energies.get)  # type: ignore[arg-type]
    max_energy = energies[max_freq]

    if max_energy <= 0:
        return None, 0.0

    # Calculate ratio of strongest to average of others
    others = [e for f, e in energies.items() if f != max_freq]
    avg_others = sum(others) / len(others) if others else 0.0

    ratio = max_energy / avg_others if avg_others > 0 else float("inf")

    if ratio >= MIN_ENERGY_RATIO:
        return max_freq, ratio
    return None, ratio


def estimate_frequency(
    samples: np.ndarray,
    freq_low: float = 1000.0,
    freq_high: float = 2500.0,
    step: float = 25.0,
    sample_rate: int = SAMPLE_RATE,
) -> float:
    """Estimate the dominant frequency in a range using Goertzel sweep.

    Sweeps through frequencies in the given range and returns the one
    with maximum energy. Uses a coarse sweep followed by a fine sweep
    for accuracy.

    Args:
        samples: Audio samples.
        freq_low: Lower bound of frequency range (Hz).
        freq_high: Upper bound of frequency range (Hz).
        step: Coarse step size (Hz).
        sample_rate: Sample rate (Hz).

    Returns:
        Estimated dominant frequency (Hz).
    """
    if len(samples) == 0:
        return 0.0

    # Coarse sweep
    best_freq = freq_low
    best_energy = 0.0

    freq = freq_low
    while freq <= freq_high:
        energy = goertzel(samples, freq, sample_rate)
        if energy > best_energy:
            best_energy = energy
            best_freq = freq
        freq += step

    # Fine sweep around the coarse peak (+/- one step, 5 Hz resolution)
    fine_low = max(freq_low, best_freq - step)
    fine_high = min(freq_high, best_freq + step)
    freq = fine_low
    while freq <= fine_high:
        energy = goertzel(samples, freq, sample_rate)
        if energy > best_energy:
            best_energy = energy
            best_freq = freq
        freq += 5.0

    return best_freq


def freq_to_pixel(frequency: float) -> int:
    """Convert SSTV audio frequency to pixel luminance value (0-255).

    Linear mapping: 1500 Hz = 0 (black), 2300 Hz = 255 (white).

    Args:
        frequency: Detected frequency (Hz).

    Returns:
        Pixel value clamped to 0-255.
    """
    normalized = (frequency - FREQ_PIXEL_LOW) / (FREQ_PIXEL_HIGH - FREQ_PIXEL_LOW)
    return max(0, min(255, int(normalized * 255 + 0.5)))


def samples_for_duration(duration_s: float, sample_rate: int = SAMPLE_RATE) -> int:
    """Calculate number of samples for a given duration.

    Args:
        duration_s: Duration in seconds.
        sample_rate: Sample rate (Hz).

    Returns:
        Number of samples.
    """
    return int(duration_s * sample_rate + 0.5)


def goertzel_batch(audio_matrix: np.ndarray, frequencies: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Compute Goertzel energy for multiple audio segments at multiple frequencies.

    Vectorized implementation using numpy broadcasting.  Processes all
    pixel windows and all candidate frequencies simultaneously, giving
    roughly 50-100x speed-up over the scalar ``goertzel`` called in a
    Python loop.

    Args:
        audio_matrix: Shape (M, N) – M audio segments of N samples each.
        frequencies: 1-D array of F target frequencies in Hz.
        sample_rate: Sample rate in Hz.

    Returns:
        Shape (M, F) array of energy values.
    """
    if audio_matrix.size == 0 or len(frequencies) == 0:
        return np.zeros((audio_matrix.shape[0], len(frequencies)))

    _M, N = audio_matrix.shape

    # Generalized Goertzel (DTFT): exact target frequencies, no bin rounding
    w = 2.0 * np.pi * frequencies / sample_rate
    coeff = 2.0 * np.cos(w)  # (F,)

    s1 = np.zeros((audio_matrix.shape[0], len(frequencies)))
    s2 = np.zeros_like(s1)

    for n in range(N):
        samples_n = audio_matrix[:, n : n + 1]  # (M, 1) — broadcasts with (M, F)
        s0 = samples_n + coeff * s1 - s2
        s2 = s1
        s1 = s0

    return s1 * s1 + s2 * s2 - coeff * s1 * s2


class StreamingBandpass:
    """Linear-phase FIR bandpass applied statefully across chunks.

    rtl_fm's SSB output carries noise across the full output bandwidth
    (24 kHz at 48 kS/s) while all SSTV tones live between 1100 and
    2300 Hz (±250 Hz tuning offset). The instantaneous-frequency pixel
    demodulator reads the spectral centroid of whatever it is given, so
    unfiltered wideband noise drags every pixel toward white even when
    the narrowband Goertzel VIS detector hears the signal clearly.
    Linear phase keeps sync-to-pixel timing exact; the constant group
    delay is harmless because VIS detection and pixel decoding happen
    on the same delayed stream.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        low_hz: float = 400.0,
        high_hz: float = 3000.0,
        num_taps: int = 257,
    ):
        self._taps = firwin(num_taps, [low_hz, high_hz], pass_zero=False, fs=sample_rate)
        self._zi = np.zeros(num_taps - 1, dtype=np.float64)
        # Discard the group delay so the filtered stream stays time-aligned
        # with the input: otherwise the tail of the VIS stop bit (1200 Hz)
        # leaks past the handover point and merges with the first sync pulse.
        self._to_drop = (num_taps - 1) // 2

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Filter one chunk, carrying filter state to the next call."""
        if len(samples) == 0:
            return samples
        out, self._zi = lfilter(self._taps, (1.0,), samples, zi=self._zi)
        if self._to_drop:
            k = min(self._to_drop, len(out))
            out = out[k:]
            self._to_drop -= k
        return out

    def flush(self) -> np.ndarray:
        """Push out the samples still inside the filter (file decoding)."""
        return self.process(np.zeros((len(self._taps) - 1) // 2, dtype=np.float64))


def normalize_audio(raw: np.ndarray) -> np.ndarray:
    """Normalize int16 PCM audio to float64 in range [-1.0, 1.0].

    Args:
        raw: Raw int16 samples from rtl_fm.

    Returns:
        Float64 normalized samples.
    """
    return raw.astype(np.float64) / 32768.0
