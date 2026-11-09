"""Tests for the pure-Python SSTV decoder.

Covers VIS detection, Goertzel accuracy, mode specs, synthetic image
decoding, and integration with the SSTVDecoder orchestrator.
"""

from __future__ import annotations

import math
import tempfile
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from utils.sstv.constants import (
    FREQ_BLACK,
    FREQ_LEADER,
    FREQ_PIXEL_HIGH,
    FREQ_PIXEL_LOW,
    FREQ_SYNC,
    FREQ_VIS_BIT_0,
    FREQ_VIS_BIT_1,
    FREQ_WHITE,
    SAMPLE_RATE,
)
from utils.sstv.dsp import (
    estimate_frequency,
    freq_to_pixel,
    goertzel,
    goertzel_batch,
    goertzel_mag,
    normalize_audio,
    samples_for_duration,
)
from utils.sstv.modes import (
    ALL_MODES,
    MARTIN_1,
    PD_120,
    PD_180,
    ROBOT_36,
    ROBOT_72,
    SCOTTIE_1,
    ColorModel,
    SyncPosition,
    get_mode,
    get_mode_by_name,
)
from utils.sstv.sstv_decoder import (
    DecodeProgress,
    DopplerInfo,
    SSTVDecoder,
    SSTVImage,
    get_sstv_decoder,
    is_sstv_available,
)
from utils.sstv.vis import VISDetector, VISState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_tone(freq: float, duration_s: float, sample_rate: int = SAMPLE_RATE, amplitude: float = 0.8) -> np.ndarray:
    """Generate a pure sine tone."""
    t = np.arange(int(duration_s * sample_rate)) / sample_rate
    return amplitude * np.sin(2 * np.pi * freq * t)


def generate_vis_header(vis_code: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a synthetic VIS header for a given code.

    Structure: leader1 (300ms) + break (10ms) + leader2 (300ms)
             + start_bit (30ms) + 8 data bits (30ms each)
             + parity bit (30ms) + stop_bit (30ms)
    """
    parts = []

    # Leader 1 (1900 Hz, 300ms)
    parts.append(generate_tone(FREQ_LEADER, 0.300, sample_rate))

    # Break (1200 Hz, 10ms)
    parts.append(generate_tone(FREQ_SYNC, 0.010, sample_rate))

    # Leader 2 (1900 Hz, 300ms)
    parts.append(generate_tone(FREQ_LEADER, 0.300, sample_rate))

    # Start bit (1200 Hz, 30ms)
    parts.append(generate_tone(FREQ_SYNC, 0.030, sample_rate))

    # 8 data bits (LSB first)
    ones_count = 0
    for i in range(8):
        bit = (vis_code >> i) & 1
        if bit:
            ones_count += 1
            parts.append(generate_tone(FREQ_VIS_BIT_1, 0.030, sample_rate))
        else:
            parts.append(generate_tone(FREQ_VIS_BIT_0, 0.030, sample_rate))

    # Even parity bit
    parity = ones_count % 2
    if parity:
        parts.append(generate_tone(FREQ_VIS_BIT_1, 0.030, sample_rate))
    else:
        parts.append(generate_tone(FREQ_VIS_BIT_0, 0.030, sample_rate))

    # Stop bit (1200 Hz, 30ms)
    parts.append(generate_tone(FREQ_SYNC, 0.030, sample_rate))

    return np.concatenate(parts)


# ---------------------------------------------------------------------------
# Goertzel / DSP tests
# ---------------------------------------------------------------------------


class TestGoertzel:
    """Tests for the Goertzel algorithm."""

    def test_detects_exact_frequency(self):
        """Goertzel should have peak energy at the generated frequency."""
        tone = generate_tone(1200.0, 0.01)
        energy_1200 = goertzel(tone, 1200.0)
        energy_1500 = goertzel(tone, 1500.0)
        energy_1900 = goertzel(tone, 1900.0)

        assert energy_1200 > energy_1500 * 5
        assert energy_1200 > energy_1900 * 5

    def test_different_frequencies(self):
        """Each candidate frequency should produce peak at its own freq."""
        for freq in [1100, 1200, 1300, 1500, 1900, 2300]:
            tone = generate_tone(float(freq), 0.01)
            energy = goertzel(tone, float(freq))
            # Should have significant energy at the target
            assert energy > 0

    def test_empty_samples(self):
        """Goertzel on empty array should return 0."""
        assert goertzel(np.array([], dtype=np.float64), 1200.0) == 0.0

    def test_goertzel_mag(self):
        """goertzel_mag should return sqrt of energy."""
        tone = generate_tone(1200.0, 0.01)
        energy = goertzel(tone, 1200.0)
        mag = goertzel_mag(tone, 1200.0)
        assert abs(mag - math.sqrt(energy)) < 1e-10


class TestEstimateFrequency:
    """Tests for frequency estimation."""

    def test_estimates_known_frequency(self):
        """Should accurately estimate a known tone frequency."""
        tone = generate_tone(1900.0, 0.02)
        estimated = estimate_frequency(tone, 1000.0, 2500.0)
        assert abs(estimated - 1900.0) <= 30.0

    def test_estimates_black_level(self):
        """Should detect the black level frequency."""
        tone = generate_tone(FREQ_BLACK, 0.02)
        estimated = estimate_frequency(tone, 1400.0, 1600.0)
        assert abs(estimated - FREQ_BLACK) <= 30.0

    def test_estimates_white_level(self):
        """Should detect the white level frequency."""
        tone = generate_tone(FREQ_WHITE, 0.02)
        estimated = estimate_frequency(tone, 2200.0, 2400.0)
        assert abs(estimated - FREQ_WHITE) <= 30.0

    def test_empty_samples(self):
        """Should return 0 for empty input."""
        assert estimate_frequency(np.array([], dtype=np.float64)) == 0.0


class TestFreqToPixel:
    """Tests for frequency-to-pixel mapping."""

    def test_black_level(self):
        """1500 Hz should map to 0 (black)."""
        assert freq_to_pixel(FREQ_PIXEL_LOW) == 0

    def test_white_level(self):
        """2300 Hz should map to 255 (white)."""
        assert freq_to_pixel(FREQ_PIXEL_HIGH) == 255

    def test_midpoint(self):
        """Middle frequency should map to approximately 128."""
        mid_freq = (FREQ_PIXEL_LOW + FREQ_PIXEL_HIGH) / 2
        pixel = freq_to_pixel(mid_freq)
        assert 120 <= pixel <= 135

    def test_below_black_clamps(self):
        """Frequencies below black level should clamp to 0."""
        assert freq_to_pixel(1000.0) == 0

    def test_above_white_clamps(self):
        """Frequencies above white level should clamp to 255."""
        assert freq_to_pixel(3000.0) == 255


class TestNormalizeAudio:
    """Tests for int16 to float64 normalization."""

    def test_max_positive(self):
        """int16 max should normalize to ~1.0."""
        raw = np.array([32767], dtype=np.int16)
        result = normalize_audio(raw)
        assert abs(result[0] - (32767.0 / 32768.0)) < 1e-10

    def test_zero(self):
        """int16 zero should normalize to 0.0."""
        raw = np.array([0], dtype=np.int16)
        result = normalize_audio(raw)
        assert result[0] == 0.0

    def test_negative(self):
        """int16 min should normalize to -1.0."""
        raw = np.array([-32768], dtype=np.int16)
        result = normalize_audio(raw)
        assert result[0] == -1.0


class TestSamplesForDuration:
    """Tests for duration-to-samples calculation."""

    def test_one_second(self):
        """1 second at 48kHz should be 48000 samples."""
        assert samples_for_duration(1.0) == 48000

    def test_five_ms(self):
        """5ms at 48kHz should be 240 samples."""
        assert samples_for_duration(0.005) == 240

    def test_custom_rate(self):
        """Should work with custom sample rates."""
        assert samples_for_duration(1.0, 22050) == 22050


class TestGoertzelBatch:
    """Tests for the vectorized batch Goertzel function."""

    def test_matches_scalar_goertzel(self):
        """Batch result should match individual goertzel calls."""
        rng = np.random.default_rng(42)
        # 10 pixel windows of 20 samples each
        audio_matrix = rng.standard_normal((10, 20))
        freqs = np.array([1200.0, 1500.0, 1900.0, 2300.0])

        batch_result = goertzel_batch(audio_matrix, freqs)
        assert batch_result.shape == (10, 4)

        for i in range(10):
            for j, f in enumerate(freqs):
                scalar = goertzel(audio_matrix[i], f)
                assert abs(batch_result[i, j] - scalar) < 1e-6, f"Mismatch at pixel {i}, freq {f}"

    def test_detects_correct_frequency(self):
        """Batch should find peak at the correct frequency for each pixel.

        Uses 96-sample windows (2ms at 48kHz) matching the decoder's
        minimum analysis window, with 5Hz resolution.
        """
        freqs = np.arange(1400.0, 2405.0, 5.0)  # 5Hz step, same as decoder
        window_size = 96  # Matches _MIN_ANALYSIS_WINDOW
        pixels = []
        for target in [1500.0, 1900.0, 2300.0]:
            t = np.arange(window_size) / SAMPLE_RATE
            pixels.append(0.8 * np.sin(2 * np.pi * target * t))
        audio_matrix = np.array(pixels)

        energies = goertzel_batch(audio_matrix, freqs)
        best_idx = np.argmax(energies, axis=1)
        best_freqs = freqs[best_idx]

        # With 96 samples, frequency accuracy is within ~25 Hz
        assert abs(best_freqs[0] - 1500.0) <= 30.0
        assert abs(best_freqs[1] - 1900.0) <= 30.0
        assert abs(best_freqs[2] - 2300.0) <= 30.0

    def test_empty_input(self):
        """Should handle empty inputs gracefully."""
        result = goertzel_batch(np.zeros((0, 10)), np.array([1200.0]))
        assert result.shape == (0, 1)

        result = goertzel_batch(np.zeros((5, 10)), np.array([]))
        assert result.shape == (5, 0)


# ---------------------------------------------------------------------------
# VIS detection tests
# ---------------------------------------------------------------------------


class TestVISDetector:
    """Tests for VIS header detection."""

    def test_initial_state(self):
        """Detector should start in IDLE state."""
        detector = VISDetector()
        assert detector.state == VISState.IDLE

    def test_reset(self):
        """Reset should return to IDLE state."""
        detector = VISDetector()
        # Feed some leader tone to change state
        detector.feed(generate_tone(FREQ_LEADER, 0.250))
        detector.reset()
        assert detector.state == VISState.IDLE

    def test_detect_robot36(self):
        """Should detect Robot36 VIS code (8)."""
        detector = VISDetector()
        header = generate_vis_header(8)  # Robot36
        # Add some silence before and after
        audio = np.concatenate(
            [
                np.zeros(2400),
                header,
                np.zeros(2400),
            ]
        )

        result = detector.feed(audio)
        assert result is not None
        vis_code, mode_name = result
        assert vis_code == 8
        assert mode_name == "Robot36"

    def test_detect_martin1(self):
        """Should detect Martin1 VIS code (44)."""
        detector = VISDetector()
        header = generate_vis_header(44)  # Martin1
        audio = np.concatenate([np.zeros(2400), header, np.zeros(2400)])

        result = detector.feed(audio)
        assert result is not None
        vis_code, mode_name = result
        assert vis_code == 44
        assert mode_name == "Martin1"

    def test_detect_scottie1(self):
        """Should detect Scottie1 VIS code (60)."""
        detector = VISDetector()
        header = generate_vis_header(60)  # Scottie1
        audio = np.concatenate([np.zeros(2400), header, np.zeros(2400)])

        result = detector.feed(audio)
        assert result is not None
        vis_code, mode_name = result
        assert vis_code == 60
        assert mode_name == "Scottie1"

    def test_detect_pd120(self):
        """Should detect PD120 VIS code (95)."""
        detector = VISDetector()
        header = generate_vis_header(95)  # PD120
        audio = np.concatenate([np.zeros(2400), header, np.zeros(2400)])

        result = detector.feed(audio)
        assert result is not None
        vis_code, mode_name = result
        assert vis_code == 95
        assert mode_name == "PD120"

    def test_noise_rejection(self):
        """Should not falsely detect VIS in noise."""
        detector = VISDetector()
        rng = np.random.default_rng(42)
        noise = rng.standard_normal(48000) * 0.1  # 1 second of noise
        result = detector.feed(noise)
        assert result is None

    def test_incremental_feeding(self):
        """Should work with small chunks fed incrementally."""
        detector = VISDetector()
        header = generate_vis_header(8)
        audio = np.concatenate([np.zeros(2400), header, np.zeros(2400)])

        # Feed in small chunks (100 samples each)
        chunk_size = 100
        result = None
        offset = 0
        while offset < len(audio):
            chunk = audio[offset : offset + chunk_size]
            offset += chunk_size
            result = detector.feed(chunk)
            if result is not None:
                break

        assert result is not None
        vis_code, mode_name = result
        assert vis_code == 8
        assert mode_name == "Robot36"

    def test_noisy_leader_detection(self):
        """Should detect VIS despite intermittent None windows in leader.

        Simulates HF fading by inserting short silence gaps (which produce
        ambiguous tone classification) into the leader tone.
        """
        detector = VISDetector()
        parts = []

        # Build leader1 with gaps: 50ms tone, 10ms silence, repeated
        # Total ~300ms of leader with interruptions
        for _ in range(6):
            parts.append(generate_tone(FREQ_LEADER, 0.050))
            parts.append(np.zeros(int(SAMPLE_RATE * 0.010)))  # 10ms gap

        # Break (1200 Hz, 10ms)
        parts.append(generate_tone(FREQ_SYNC, 0.010))

        # Leader 2 (clean)
        parts.append(generate_tone(FREQ_LEADER, 0.300))

        # Start bit + data bits + parity + stop (standard for Robot36 = VIS 8)
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # start bit
        vis_code = 8
        ones_count = 0
        for i in range(8):
            bit = (vis_code >> i) & 1
            if bit:
                ones_count += 1
                parts.append(generate_tone(FREQ_VIS_BIT_1, 0.030))
            else:
                parts.append(generate_tone(FREQ_VIS_BIT_0, 0.030))
        parity = ones_count % 2
        parts.append(generate_tone(FREQ_VIS_BIT_1 if parity else FREQ_VIS_BIT_0, 0.030))
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # stop bit

        audio = np.concatenate([np.zeros(2400)] + parts + [np.zeros(2400)])
        result = detector.feed(audio)
        assert result is not None
        assert result[0] == 8
        assert result[1] == "Robot36"

    def test_vis_error_correction_parity_bit(self):
        """Should recover when only the parity bit is corrupted."""
        detector = VISDetector()
        # Generate Martin1 header (VIS 44) but flip the parity bit
        parts = []
        parts.append(generate_tone(FREQ_LEADER, 0.300))
        parts.append(generate_tone(FREQ_SYNC, 0.010))
        parts.append(generate_tone(FREQ_LEADER, 0.300))
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # start bit

        vis_code = 44  # Martin1
        ones_count = 0
        for i in range(8):
            bit = (vis_code >> i) & 1
            if bit:
                ones_count += 1
                parts.append(generate_tone(FREQ_VIS_BIT_1, 0.030))
            else:
                parts.append(generate_tone(FREQ_VIS_BIT_0, 0.030))

        # Wrong parity (flip it)
        correct_parity = ones_count % 2
        wrong_parity = 1 - correct_parity
        parts.append(generate_tone(FREQ_VIS_BIT_1 if wrong_parity else FREQ_VIS_BIT_0, 0.030))
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # stop bit

        audio = np.concatenate([np.zeros(2400)] + parts + [np.zeros(2400)])
        result = detector.feed(audio)
        assert result is not None
        assert result[0] == 44
        assert result[1] == "Martin1"

    def test_vis_error_correction_data_bit(self):
        """Should recover Martin1 when one data bit is flipped by HF noise.

        Simulates: Martin1 (VIS 44) transmitted correctly, but bit 0 is
        corrupted during reception. The parity bit is received correctly
        (computed for the original code 44), so parity check fails → error
        correction tries flipping each data bit and finds VIS 44.
        """
        detector = VISDetector()
        original_code = 44  # Martin1
        corrupted_code = 44 ^ 1  # flip bit 0 → 45

        parts = []
        parts.append(generate_tone(FREQ_LEADER, 0.300))
        parts.append(generate_tone(FREQ_SYNC, 0.010))
        parts.append(generate_tone(FREQ_LEADER, 0.300))
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # start bit

        # Transmit corrupted data bits
        for i in range(8):
            bit = (corrupted_code >> i) & 1
            if bit:
                parts.append(generate_tone(FREQ_VIS_BIT_1, 0.030))
            else:
                parts.append(generate_tone(FREQ_VIS_BIT_0, 0.030))

        # Parity bit computed for the ORIGINAL code (received correctly)
        original_ones = bin(original_code).count("1")
        parity = original_ones % 2
        parts.append(generate_tone(FREQ_VIS_BIT_1 if parity else FREQ_VIS_BIT_0, 0.030))
        parts.append(generate_tone(FREQ_SYNC, 0.030))  # stop bit

        audio = np.concatenate([np.zeros(2400)] + parts + [np.zeros(2400)])
        result = detector.feed(audio)
        assert result is not None
        assert result[0] == 44
        assert result[1] == "Martin1"

    @pytest.mark.parametrize("offset", [-200, -100, -50, 50, 100, 200])
    def test_detects_mistuned_ssb_header(self, offset):
        """On SSB every tone is shifted by the tuning error; the leader measures it."""
        detector = VISDetector()
        audio = generate_shifted_vis(44, offset, lead_samples=4800 + 137)
        result = feed_in_chunks(detector, audio)
        assert result == (44, "Martin1")
        assert detector.freq_offset == pytest.approx(offset, abs=5)

    @pytest.mark.parametrize("offset", [0, 100, 200])
    def test_preamble_tone_before_leader(self, offset):
        """A 1500 Hz preamble (shifted, it can look like a mistuned leader) must not
        stop the real leader being found."""
        detector = VISDetector()
        audio = generate_shifted_vis(60, offset, preamble_s=0.5)
        assert feed_in_chunks(detector, audio) == (60, "Scottie1")

    def test_noise_gives_no_offset_lock(self):
        rng = np.random.default_rng(3)
        detector = VISDetector()
        assert feed_in_chunks(detector, rng.normal(scale=0.3, size=SAMPLE_RATE * 5)) is None
        assert detector.freq_offset == 0.0


def generate_shifted_vis(vis_code: int, offset: float, lead_samples: int = 4800, preamble_s: float = 0.0) -> np.ndarray:
    """VIS header with every tone shifted by `offset` Hz, generated with continuous
    phase like a real transmitter heard through a mistuned SSB receiver."""
    bits = [(vis_code >> i) & 1 for i in range(8)]
    seq = [(FREQ_BLACK, preamble_s)] if preamble_s else []
    seq += [(FREQ_LEADER, 0.300), (FREQ_SYNC, 0.010), (FREQ_LEADER, 0.300), (FREQ_SYNC, 0.030)]
    seq += [(FREQ_VIS_BIT_1 if b else FREQ_VIS_BIT_0, 0.030) for b in bits]
    seq += [(FREQ_VIS_BIT_1 if sum(bits) % 2 else FREQ_VIS_BIT_0, 0.030), (FREQ_SYNC, 0.030), (FREQ_BLACK, 0.2)]
    freqs = np.concatenate([np.full(int(d * SAMPLE_RATE), f + offset, dtype=np.float64) for f, d in seq])
    tones = 0.5 * np.sin(np.cumsum(2 * np.pi * freqs / SAMPLE_RATE))
    return np.concatenate([np.zeros(lead_samples), tones])


def robot36_audio(y_for_line, offset: float = 0.0, burst_line: int | None = None) -> np.ndarray:
    """Robot36 scanlines (neutral chroma) with continuous phase and exact line timing.

    `burst_line` puts a loud 9 ms 1200 Hz burst 15 ms into that line's
    luminance, where a wide sync search would mistake it for the sync pulse.
    """
    segments: list[np.ndarray] = []
    elapsed = 0.0

    def add(freqs_hz: np.ndarray | float, seconds: float, amplitude: float = 0.5) -> None:
        nonlocal elapsed
        n = int(round((elapsed + seconds) * SAMPLE_RATE)) - int(round(elapsed * SAMPLE_RATE))
        elapsed += seconds
        f = (
            np.full(n, float(freqs_hz))
            if np.isscalar(freqs_hz)
            else np.asarray(freqs_hz)[np.arange(n) * len(freqs_hz) // n]
        )
        segments.append(np.stack([f + offset, np.full(n, amplitude)]))

    for line in range(ROBOT_36.height):
        y_freqs = FREQ_PIXEL_LOW + np.asarray(y_for_line(line)) / 255.0 * (FREQ_PIXEL_HIGH - FREQ_PIXEL_LOW)
        add(FREQ_SYNC, 0.009)
        add(FREQ_BLACK, 0.003)
        if line == burst_line:
            add(y_freqs[:36], 0.003)
            add(FREQ_SYNC, 0.009, amplitude=1.0)
            add(y_freqs[144:], 0.076)
        else:
            add(y_freqs, 0.088)
        add(FREQ_BLACK, 0.006)
        add(1900.0, 0.044)  # neutral chroma
    add(FREQ_BLACK, 0.2)  # tail, so the last line is complete
    freqs, amps = np.concatenate(segments, axis=1)
    return amps * np.sin(np.cumsum(2 * np.pi * freqs / SAMPLE_RATE))


def feed_in_chunks(detector: VISDetector, audio: np.ndarray):
    """Feed 100 ms chunks, as the live decoder does."""
    chunk = SAMPLE_RATE // 10
    for i in range(0, len(audio), chunk):
        result = detector.feed(audio[i : i + chunk])
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Mode spec tests
# ---------------------------------------------------------------------------


class TestModes:
    """Tests for SSTV mode specifications."""

    def test_all_vis_codes_have_modes(self):
        """All defined VIS codes should have matching mode specs."""
        for vis_code in [8, 12, 44, 40, 60, 56, 95, 97, 99, 98, 96, 76]:
            mode = get_mode(vis_code)
            assert mode is not None, f"No mode for VIS code {vis_code}"

    def test_robot36_spec(self):
        """Robot36 should have correct dimensions and timing."""
        assert ROBOT_36.width == 320
        assert ROBOT_36.height == 240
        assert ROBOT_36.vis_code == 8
        assert ROBOT_36.color_model == ColorModel.YCRCB
        assert ROBOT_36.has_half_rate_chroma is True
        assert ROBOT_36.sync_position == SyncPosition.FRONT

    def test_martin1_spec(self):
        """Martin1 should have correct dimensions."""
        assert MARTIN_1.width == 320
        assert MARTIN_1.height == 256
        assert MARTIN_1.vis_code == 44
        assert MARTIN_1.color_model == ColorModel.RGB
        assert len(MARTIN_1.channels) == 3

    def test_scottie1_spec(self):
        """Scottie1 should have middle sync position."""
        assert SCOTTIE_1.sync_position == SyncPosition.MIDDLE
        assert SCOTTIE_1.width == 320
        assert SCOTTIE_1.height == 256

    def test_pd120_spec(self):
        """PD120 should have dual-luminance YCrCb."""
        assert PD_120.width == 640
        assert PD_120.height == 496
        assert PD_120.color_model == ColorModel.YCRCB_DUAL
        assert len(PD_120.channels) == 4  # Y1, Cr, Cb, Y2

    def test_get_mode_unknown(self):
        """Unknown VIS code should return None."""
        assert get_mode(999) is None

    def test_get_mode_by_name(self):
        """Should look up modes by name."""
        mode = get_mode_by_name("Robot36")
        assert mode is not None
        assert mode.vis_code == 8

    def test_mode_by_name_unknown(self):
        """Unknown mode name should return None."""
        assert get_mode_by_name("FakeMode") is None

    def test_robot72_spec(self):
        """Robot72 should have 3 channels and full-rate chroma."""
        assert ROBOT_72.width == 320
        assert ROBOT_72.height == 240
        assert ROBOT_72.vis_code == 12
        assert ROBOT_72.color_model == ColorModel.YCRCB
        assert ROBOT_72.has_half_rate_chroma is False
        assert len(ROBOT_72.channels) == 3  # Y, Cr, Cb
        assert ROBOT_72.channel_separator_ms == 6.0

    def test_robot36_separator(self):
        """Robot36 should have a 6ms separator between Y and chroma."""
        assert ROBOT_36.channel_separator_ms == 6.0
        assert ROBOT_36.has_half_rate_chroma is True
        assert len(ROBOT_36.channels) == 2  # Y, alternating Cr/Cb

    def test_pd120_channel_timings(self):
        """PD120 channel durations should sum to line_duration minus sync+porch."""
        channel_sum = sum(ch.duration_ms for ch in PD_120.channels)
        expected = PD_120.line_duration_ms - PD_120.sync_duration_ms - PD_120.sync_porch_ms
        assert abs(channel_sum - expected) < 0.1, f"PD120 channels sum to {channel_sum}ms, expected {expected}ms"

    def test_pd180_channel_timings(self):
        """PD180 channel durations should sum to line_duration minus sync+porch."""
        channel_sum = sum(ch.duration_ms for ch in PD_180.channels)
        expected = PD_180.line_duration_ms - PD_180.sync_duration_ms - PD_180.sync_porch_ms
        assert abs(channel_sum - expected) < 0.1, f"PD180 channels sum to {channel_sum}ms, expected {expected}ms"

    def test_robot36_timing_consistency(self):
        """Robot36 total channel + sync + porch + separator should equal line_duration."""
        total = (
            ROBOT_36.sync_duration_ms
            + ROBOT_36.sync_porch_ms
            + sum(ch.duration_ms for ch in ROBOT_36.channels)
            + ROBOT_36.channel_separator_ms
        )  # 1 separator for 2 channels
        assert abs(total - ROBOT_36.line_duration_ms) < 0.1

    def test_robot72_timing_consistency(self):
        """Robot72 total should equal line_duration."""
        # 3 channels with 2 separators
        total = (
            ROBOT_72.sync_duration_ms
            + ROBOT_72.sync_porch_ms
            + sum(ch.duration_ms for ch in ROBOT_72.channels)
            + ROBOT_72.channel_separator_ms * 2
        )
        assert abs(total - ROBOT_72.line_duration_ms) < 0.1

    def test_all_modes_have_positive_dimensions(self):
        """All modes should have positive width and height."""
        for _vis_code, mode in ALL_MODES.items():
            assert mode.width > 0, f"{mode.name} has invalid width"
            assert mode.height > 0, f"{mode.name} has invalid height"
            assert mode.line_duration_ms > 0, f"{mode.name} has invalid line duration"


# ---------------------------------------------------------------------------
# Image decoder tests
# ---------------------------------------------------------------------------


class TestImageDecoder:
    """Tests for the SSTV image decoder."""

    def test_creates_decoder(self):
        """Should create an image decoder for any supported mode."""
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(ROBOT_36)
        assert decoder.is_complete is False
        assert decoder.current_line == 0
        assert decoder.total_lines == 240

    def test_pd120_dual_luminance_lines(self):
        """PD120 decoder should expect half the image height in audio lines."""
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(PD_120)
        assert decoder.total_lines == 248  # 496 / 2

    def test_progress_percent(self):
        """Progress should start at 0."""
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(ROBOT_36)
        assert decoder.progress_percent == 0

    def test_synthetic_robot36_decode(self):
        """Should decode a synthetic Robot36 image (all white)."""
        pytest.importorskip("PIL")
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(ROBOT_36)

        # Generate synthetic scanlines (all white = 2300 Hz)
        # Each line: sync(9ms) + porch(3ms) + Y(88ms) + separator(6ms) + Cr/Cb(44ms)
        for _line in range(240):
            parts = []
            # Sync pulse
            parts.append(generate_tone(FREQ_SYNC, 0.009))
            # Porch
            parts.append(generate_tone(FREQ_BLACK, 0.003))
            # Y channel (white = 2300 Hz)
            parts.append(generate_tone(FREQ_WHITE, 0.088))
            # Separator + porch (6ms)
            parts.append(generate_tone(FREQ_BLACK, 0.006))
            # Chroma channel (mid value = 1900 Hz ~ 128)
            parts.append(generate_tone(1900.0, 0.044))
            # Pad to line duration
            line_audio = np.concatenate(parts)
            line_samples = samples_for_duration(ROBOT_36.line_duration_ms / 1000.0)
            if len(line_audio) < line_samples:
                line_audio = np.concatenate([line_audio, np.zeros(line_samples - len(line_audio))])

            decoder.feed(line_audio)

        assert decoder.is_complete
        img = decoder.get_image()
        assert img is not None
        assert img.size == (320, 240)

    @pytest.mark.parametrize("freq_offset, expected_grey", [(100.0, 128), (0.0, 160)])
    def test_pixels_corrected_for_tuning_offset(self, freq_offset, expected_grey):
        """Mid-grey (1900 Hz) received 100 Hz high reads as grey only when the offset is removed."""
        pytest.importorskip("PIL")
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(ROBOT_36, freq_offset=freq_offset)
        decoder.feed(robot36_audio(lambda line: np.full(320, 128.0), offset=100.0))
        assert decoder.is_complete
        grey = np.asarray(decoder.get_image().convert("L"), dtype=np.float64)
        assert grey[10:-10, 20:-20].mean() == pytest.approx(expected_grey, abs=8)

    def test_noise_burst_does_not_shift_following_lines(self):
        """A loud sync-like burst inside one line must not drag every later line sideways."""
        pytest.importorskip("PIL")
        from utils.sstv.image_decoder import SSTVImageDecoder

        # Black bar on the left, white elsewhere
        pattern = np.where(np.arange(320) < 40, 0.0, 255.0)
        decoder = SSTVImageDecoder(ROBOT_36)
        decoder.feed(robot36_audio(lambda line: pattern, burst_line=100))
        assert decoder.is_complete
        grey = np.asarray(decoder.get_image().convert("L"), dtype=np.float64)

        def bar_edge(row):
            return int(np.argmax(grey[row] > 128))

        assert abs(bar_edge(230) - bar_edge(20)) <= 3

    def test_slant_correction_wraps_rows_without_blank_wedge(self):
        """Slant correction should rotate rows, not introduce black fill."""
        PIL = pytest.importorskip("PIL")
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(SCOTTIE_1)
        decoder._sync_deviations = [float(i * 4) for i in range(SCOTTIE_1.height)]

        source = np.full((SCOTTIE_1.height, SCOTTIE_1.width, 3), 128, dtype=np.uint8)
        img = PIL.Image.fromarray(source, "RGB")

        corrected = decoder._apply_slant_correction(img)
        corrected_arr = np.array(corrected)

        # If correction clips/fills, zeros appear. Circular shift should preserve all values.
        assert corrected_arr.min() == 128
        assert corrected_arr.max() == 128

    def test_slant_correction_skips_implausible_drift(self):
        """Very large estimated drift should be treated as a bad fit and ignored."""
        PIL = pytest.importorskip("PIL")
        from utils.sstv.image_decoder import SSTVImageDecoder

        decoder = SSTVImageDecoder(SCOTTIE_1)
        decoder._sync_deviations = [float(i * 40) for i in range(SCOTTIE_1.height)]

        source = np.full((SCOTTIE_1.height, SCOTTIE_1.width, 3), 177, dtype=np.uint8)
        img = PIL.Image.fromarray(source, "RGB")

        corrected = decoder._apply_slant_correction(img)

        # Implausible slope should return original image unchanged.
        assert np.array_equal(np.array(corrected), source)


# ---------------------------------------------------------------------------
# SSTVDecoder orchestrator tests
# ---------------------------------------------------------------------------


class TestSSTVDecoder:
    """Tests for the SSTVDecoder orchestrator."""

    def test_decoder_available(self):
        """Python decoder should always be available."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        assert decoder.decoder_available == "python-sstv"

    def test_is_sstv_available(self):
        """is_sstv_available() should always return True."""
        assert is_sstv_available() is True

    def test_not_running_initially(self):
        """Decoder should not be running on creation."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        assert decoder.is_running is False

    def test_doppler_disabled_by_default(self):
        """Doppler should be disabled by default."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        assert decoder.doppler_enabled is False
        assert decoder.last_doppler_info is None

    def test_start_passes_modulation_gain_and_ppm_to_rtl_fm(self):
        """rtl_fm should get the chosen modulation, gain and PPM correction."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        with (
            patch("utils.sstv.sstv_decoder.subprocess.Popen") as mock_popen,
            patch.object(decoder, "_decode_audio_stream"),
        ):
            assert decoder.start(frequency=145.8, modulation="usb", gain=38.6, ppm=-12)
            decoder._running = False

        cmd = mock_popen.call_args.args[0]
        assert cmd[cmd.index("-f") + 1] == "145800000"
        assert cmd[cmd.index("-M") + 1] == "usb"
        assert cmd[cmd.index("-g") + 1] == "38.6"
        assert cmd[cmd.index("-p") + 1] == "-12"
        assert cmd[-1] == "-"
        assert decoder.modulation == "usb"

    def test_rtl_fm_defaults_to_auto_gain_and_no_ppm(self):
        """Without gain/PPM, rtl_fm should run on auto gain with no correction."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        cmd = decoder._build_rtl_cmd(145_800_000)
        assert "-g" not in cmd
        assert "-p" not in cmd

    def test_stop_when_not_running(self):
        """Stop should be safe to call when not running."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        decoder.stop()  # Should not raise

    def test_set_callback(self):
        """Should accept a callback function."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        cb = MagicMock()
        decoder.set_callback(cb)
        # Trigger a progress emit
        decoder._emit_progress(DecodeProgress(status="detecting"))
        cb.assert_called_once()

    def test_get_images_empty(self):
        """Should return empty list initially."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        images = decoder.get_images()
        assert images == []

    def test_decode_file_not_found(self):
        """Should raise FileNotFoundError for missing file."""
        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        with pytest.raises(FileNotFoundError):
            decoder.decode_file("/nonexistent/audio.wav")

    def test_decode_file_with_synthetic_wav(self):
        """Should process a WAV file through the decode pipeline."""
        pytest.importorskip("PIL")

        output_dir = tempfile.mkdtemp()
        decoder = SSTVDecoder(output_dir=output_dir)

        # Generate a synthetic WAV with a VIS header + short image data
        vis_header = generate_vis_header(8)  # Robot36

        # Add 240 lines of image data after the header
        image_lines = []
        for _line in range(240):
            parts = []
            parts.append(generate_tone(FREQ_SYNC, 0.009))
            parts.append(generate_tone(FREQ_BLACK, 0.003))
            parts.append(generate_tone(1900.0, 0.088))  # mid-gray Y
            parts.append(generate_tone(FREQ_BLACK, 0.006))  # separator
            parts.append(generate_tone(1900.0, 0.044))  # chroma
            line_audio = np.concatenate(parts)
            line_samples = samples_for_duration(ROBOT_36.line_duration_ms / 1000.0)
            if len(line_audio) < line_samples:
                line_audio = np.concatenate([line_audio, np.zeros(line_samples - len(line_audio))])
            image_lines.append(line_audio)

        audio = np.concatenate(
            [
                np.zeros(4800),  # 100ms silence
                vis_header,
                *image_lines,
                np.zeros(4800),
            ]
        )

        # Write WAV file
        wav_path = Path(output_dir) / "test_input.wav"
        raw_int16 = (audio * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(raw_int16.tobytes())

        images = decoder.decode_file(wav_path)
        assert len(images) >= 1
        assert images[0].mode == "Robot36"
        assert Path(images[0].path).exists()


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for dataclass serialization."""

    def test_decode_progress_to_dict(self):
        """DecodeProgress should serialize correctly."""
        progress = DecodeProgress(
            status="decoding",
            mode="Robot36",
            progress_percent=50,
            message="Halfway done",
        )
        d = progress.to_dict()
        assert d["type"] == "sstv_progress"
        assert d["status"] == "decoding"
        assert d["mode"] == "Robot36"
        assert d["progress"] == 50
        assert d["message"] == "Halfway done"

    def test_decode_progress_minimal(self):
        """DecodeProgress with only status should omit optional fields."""
        progress = DecodeProgress(status="detecting")
        d = progress.to_dict()
        assert "mode" not in d
        assert "message" not in d
        assert "image" not in d

    def test_sstv_image_to_dict(self):
        """SSTVImage should serialize with URL."""
        from datetime import datetime, timezone

        image = SSTVImage(
            filename="test.png",
            path=Path("/tmp/test.png"),
            mode="Robot36",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            frequency=145.800,
            size_bytes=1234,
        )
        d = image.to_dict()
        assert d["filename"] == "test.png"
        assert d["mode"] == "Robot36"
        assert d["url"] == "/sstv/images/test.png"

    def test_doppler_info_to_dict(self):
        """DopplerInfo should serialize with rounding."""
        from datetime import datetime, timezone

        info = DopplerInfo(
            frequency_hz=145800123.456,
            shift_hz=123.456,
            range_rate_km_s=-1.23456,
            elevation=45.678,
            azimuth=180.123,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        d = info.to_dict()
        assert d["shift_hz"] == 123.5
        assert d["range_rate_km_s"] == -1.235
        assert d["elevation"] == 45.7


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests verifying the package works as a drop-in replacement."""

    def test_import_from_utils_sstv(self):
        """Routes should be able to import from utils.sstv."""
        from utils.sstv import (
            ISS_SSTV_FREQ,
            is_sstv_available,
        )

        assert ISS_SSTV_FREQ == 145.800
        assert is_sstv_available() is True

    def test_sstv_modes_constant(self):
        """SSTV_MODES list should be importable."""
        from utils.sstv import SSTV_MODES

        assert "Robot36" in SSTV_MODES
        assert "Martin1" in SSTV_MODES
        assert "PD120" in SSTV_MODES

    def test_decoder_singleton(self):
        """get_sstv_decoder should return a valid decoder."""
        # Reset the global singleton for test isolation
        import utils.sstv.sstv_decoder as mod

        old = mod._decoder
        mod._decoder = None
        try:
            decoder = get_sstv_decoder()
            assert decoder is not None
            assert decoder.decoder_available == "python-sstv"
        finally:
            mod._decoder = old

    @patch("subprocess.Popen")
    def test_start_creates_subprocess(self, mock_popen):
        """start() should create an rtl_fm subprocess."""
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.read = MagicMock(return_value=b"")
        mock_process.stderr = MagicMock()
        mock_popen.return_value = mock_process

        decoder = SSTVDecoder(output_dir=tempfile.mkdtemp())
        success = decoder.start(frequency=145.800, device_index=0)
        assert success is True
        assert decoder.is_running is True

        # Verify rtl_fm was called
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "rtl_fm"
        assert "-f" in cmd
        assert "-M" in cmd

        decoder.stop()
        assert decoder.is_running is False
