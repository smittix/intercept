"""VIS (Vertical Interval Signaling) header detection.

State machine that processes audio samples to detect the VIS header
that precedes every SSTV image transmission. The VIS header identifies
the SSTV mode (Robot36, Martin1, etc.) via an 8-bit code with even parity.

VIS header structure:
    Leader tone (1900 Hz, ~300ms)
    Break (1200 Hz, ~10ms)
    Leader tone (1900 Hz, ~300ms)
    Start bit (1200 Hz, 30ms)
    7 data bits (1100 Hz = 1, 1300 Hz = 0, 30ms each, LSB first)
    Parity bit (even parity, 30ms)
    Stop bit (1200 Hz, 30ms)

The code is 7 bits, not 8: reading an eighth "data" bit swallows the
parity bit, shifts the stop bit into the parity slot, and hands the
image decoder its audio one bit (30 ms) late — skewing every line.
"""

from __future__ import annotations

import enum

import numpy as np

from .constants import (
    FREQ_LEADER,
    FREQ_SYNC,
    FREQ_VIS_BIT_0,
    FREQ_VIS_BIT_1,
    SAMPLE_RATE,
    VIS_BIT_DURATION,
    VIS_CODES,
    VIS_LEADER_MAX,
    VIS_LEADER_MIN,
)
from .dsp import goertzel, samples_for_duration

# Use 10ms window (480 samples at 48kHz) for 100Hz frequency resolution.
# This cleanly separates 1100, 1200, 1300, 1500, 1900, 2300 Hz tones.
VIS_WINDOW = 480


class VISState(enum.Enum):
    """States of the VIS detection state machine."""

    IDLE = "idle"
    LEADER_1 = "leader_1"
    BREAK = "break"
    LEADER_2 = "leader_2"
    START_BIT = "start_bit"
    DATA_BITS = "data_bits"
    PARITY = "parity"
    STOP_BIT = "stop_bit"
    DETECTED = "detected"


# The four tone classes we need to distinguish in VIS detection.
_VIS_FREQS = [FREQ_VIS_BIT_1, FREQ_SYNC, FREQ_VIS_BIT_0, FREQ_LEADER]
# 1100, 1200, 1300, 1900 Hz

# On SSB the received tones are shifted by however far the receiver is tuned
# from the transmitter; tens to a couple of hundred Hz is normal on HF. The
# VIS tones are only 100 Hz apart, so the leader's measured frequency is used
# to find that offset before the rest of the header is read.
MAX_TUNING_OFFSET = 250.0  # Hz either side of 1900 Hz accepted as a leader
LEADER_PURITY = 0.6  # share of in-band energy near the peak for a clean tone
LEADER_TRACK_TOLERANCE = 40.0  # Hz a leader window may wander from the running estimate
_FFT_SIZE = 8192  # zero-padded, ~6 Hz bins for a 10 ms window


def _classify_tone(samples: np.ndarray, sample_rate: int = SAMPLE_RATE, offset: float = 0.0) -> float | None:
    """Classify which VIS tone is present in the given samples.

    Computes Goertzel energy at each of the four VIS frequencies (shifted by
    the tuning offset) and returns the one with the highest energy, provided
    it dominates sufficiently.

    Returns:
        The nominal frequency detected (1100, 1200, 1300, or 1900), or None.
    """
    if len(samples) < 16:
        return None

    energies = {f: goertzel(samples, f + offset, sample_rate) for f in _VIS_FREQS}
    best_freq = max(energies, key=energies.get)  # type: ignore[arg-type]
    best_energy = energies[best_freq]

    if best_energy <= 0:
        return None

    # Require the best frequency to be at least 2x stronger than the
    # next-strongest tone.
    others = sorted([e for f, e in energies.items() if f != best_freq], reverse=True)
    second_best = others[0] if others else 0.0

    if second_best > 0 and best_energy / second_best < 2.0:
        return None

    return best_freq


def _leader_offset(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float | None:
    """Tuning offset (Hz) if the window is a clean tone within reach of the leader, else None."""
    if len(samples) < 16:
        return None
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples)), n=_FFT_SIZE)) ** 2
    freqs = np.fft.rfftfreq(_FFT_SIZE, 1.0 / sample_rate)

    # Strongest tone anywhere in the audio band, so a nearby 1500 Hz tone's
    # skirt at the edge of the leader range is not mistaken for a leader
    voice = (freqs >= 300) & (freqs <= 3000)
    peak = int(np.flatnonzero(voice)[np.argmax(spectrum[voice])])
    if abs(freqs[peak] - FREQ_LEADER) > MAX_TUNING_OFFSET:
        return None

    # Reject noise and busy audio: most of the 300-3000 Hz energy must sit
    # in the main lobe around the peak
    total = float(spectrum[voice].sum())
    near = np.abs(freqs - freqs[peak]) <= 150
    if total <= 0 or float(spectrum[near].sum()) / total < LEADER_PURITY:
        return None

    # Parabolic interpolation between bins for a finer peak estimate
    peak_freq = float(freqs[peak])
    if 0 < peak < len(spectrum) - 1:
        a, b, c = spectrum[peak - 1], spectrum[peak], spectrum[peak + 1]
        denom = a - 2 * b + c
        if denom != 0:
            peak_freq += 0.5 * (a - c) / denom * (freqs[1] - freqs[0])
    return peak_freq - FREQ_LEADER


class VISDetector:
    """VIS header detection state machine.

    Feed audio samples via ``feed()`` and it returns the detected VIS code
    (and mode name) when a valid header is found.

    The state machine uses a simple approach:

    - **Leader detection**: Count consecutive 1900 Hz windows until minimum
      leader duration is met.
    - **Break/start bit**: Count consecutive 1200 Hz windows. The break is
      short; the start bit is one VIS bit duration.
    - **Data/parity bits**: Accumulate audio for one bit duration, then
      compare 1100 vs 1300 Hz energy to determine bit value.
    - **Stop bit**: Count 1200 Hz windows for one bit duration.

    Usage::

        detector = VISDetector()
        for chunk in audio_chunks:
            result = detector.feed(chunk)
            if result is not None:
                vis_code, mode_name = result
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self._sample_rate = sample_rate
        self._window = VIS_WINDOW
        self._bit_samples = samples_for_duration(VIS_BIT_DURATION, sample_rate)
        self._leader_min_samples = samples_for_duration(VIS_LEADER_MIN, sample_rate)
        self._leader_max_samples = samples_for_duration(VIS_LEADER_MAX, sample_rate)

        # Pre-calculate window counts
        self._leader_min_windows = max(1, self._leader_min_samples // self._window)
        self._leader_max_windows = max(1, self._leader_max_samples // self._window)
        self._bit_windows = max(1, self._bit_samples // self._window)

        self._state = VISState.IDLE
        self._buffer = np.array([], dtype=np.float64)
        self._tone_counter = 0
        self._miss_counter = 0
        self._data_bits: list[int] = []
        self._parity_bit: int = 0
        self._bit_accumulator: list[np.ndarray] = []
        self._offset = 0.0
        self._leader_offsets: list[float] = []

    def reset(self) -> None:
        """Reset the detector to scan for a new VIS header."""
        self._state = VISState.IDLE
        self._buffer = np.array([], dtype=np.float64)
        self._tone_counter = 0
        self._miss_counter = 0
        self._data_bits = []
        self._parity_bit = 0
        self._bit_accumulator = []
        self._offset = 0.0
        self._leader_offsets = []

    @property
    def state(self) -> VISState:
        return self._state

    @property
    def freq_offset(self) -> float:
        """Tuning offset (Hz) measured from the leader; the image decoder needs it too."""
        return self._offset

    @property
    def remaining_buffer(self) -> np.ndarray:
        """Unprocessed samples left after VIS detection.

        Valid immediately after feed() returns a detection result and before
        reset() is called. These samples are the start of the SSTV image and
        must be forwarded to the image decoder.
        """
        return self._buffer

    def feed(self, samples: np.ndarray) -> tuple[int, str] | None:
        """Feed audio samples and attempt VIS detection.

        Args:
            samples: Float64 audio samples (normalized to -1..1).

        Returns:
            (vis_code, mode_name) tuple when a valid VIS header is detected,
            or None if still scanning.
        """
        self._buffer = np.concatenate([self._buffer, samples])

        while len(self._buffer) >= self._window:
            was_idle = self._state == VISState.IDLE
            result = self._process_window(self._buffer[: self._window])
            self._buffer = self._buffer[self._window :]
            if not was_idle and self._state == VISState.IDLE:
                # Header abandoned: measure the next leader afresh
                self._leader_offsets = []
                self._offset = 0.0

            if result is not None:
                return result

        return None

    def _process_window(self, window: np.ndarray) -> tuple[int, str] | None:
        """Process a single analysis window through the state machine.

        The key design: when a state transition occurs due to a tone change,
        the window that triggers the transition counts as the first window
        of the new state (tone_counter = 1).
        """
        if self._state == VISState.IDLE:
            self._track_leader(window)
            return None

        if self._state in (VISState.LEADER_1, VISState.BREAK, VISState.LEADER_2):
            # A steady tone at a clearly different offset means the tone we
            # locked onto was not the leader (e.g. a preamble tone that looks
            # like a mistuned leader); go back to searching for the real one
            other = _leader_offset(window, self._sample_rate)
            if other is not None and abs(other - self._offset) > LEADER_TRACK_TOLERANCE:
                self._tone_counter = 0
                self._state = VISState.IDLE
                return None

        tone = _classify_tone(window, self._sample_rate, self._offset)

        if self._state == VISState.LEADER_1:
            if tone == FREQ_LEADER:
                self._tone_counter += 1
                if self._tone_counter > self._leader_max_windows * 3:
                    self._tone_counter = 0
                    self._state = VISState.IDLE
            elif tone == FREQ_SYNC:
                # Transition to BREAK; this window counts as break window 1
                self._tone_counter = 1
                self._state = VISState.BREAK
            elif tone is None:
                # Mixed leader+break window? Check if 1200 Hz energy is
                # significant relative to 1900 Hz — indicates the break
                # pulse is straddling this analysis window.
                leader_e = goertzel(window, FREQ_LEADER + self._offset, self._sample_rate)
                sync_e = goertzel(window, FREQ_SYNC + self._offset, self._sample_rate)
                if sync_e > leader_e * 0.5:
                    self._tone_counter = 1
                    self._state = VISState.BREAK
                # else: noisy leader window, stay in LEADER_1
            else:
                self._tone_counter = 0
                self._state = VISState.IDLE

        elif self._state == VISState.BREAK:
            if tone == FREQ_SYNC:
                self._tone_counter += 1
                if self._tone_counter > 10:
                    self._tone_counter = 0
                    self._state = VISState.IDLE
            elif tone == FREQ_LEADER:
                # Transition to LEADER_2; this window counts
                self._tone_counter = 1
                self._state = VISState.LEADER_2
            elif tone is None:
                pass  # Ambiguous window at tone boundary — stay in state
            else:
                self._tone_counter = 0
                self._state = VISState.IDLE

        elif self._state == VISState.LEADER_2:
            if tone == FREQ_LEADER:
                self._tone_counter += 1
                if self._tone_counter > self._leader_max_windows * 3:
                    self._tone_counter = 0
                    self._state = VISState.IDLE
            elif tone == FREQ_SYNC:
                # Transition to START_BIT; this window counts
                self._tone_counter = 1
                self._state = VISState.START_BIT
                # Check if start bit is already complete (1-window bit)
                if self._tone_counter >= self._bit_windows:
                    self._tone_counter = 0
                    self._data_bits = []
                    self._bit_accumulator = []
                    self._state = VISState.DATA_BITS
            elif tone is None:
                pass  # Ambiguous window at tone boundary — stay in state
            else:
                self._tone_counter = 0
                self._state = VISState.IDLE

        elif self._state == VISState.START_BIT:
            if tone == FREQ_SYNC:
                self._tone_counter += 1
                if self._tone_counter >= self._bit_windows:
                    self._tone_counter = 0
                    self._data_bits = []
                    self._bit_accumulator = []
                    self._state = VISState.DATA_BITS
            else:
                # Non-sync during start bit: check if we had enough sync
                # windows already (tolerant: accept if within 1 window)
                if self._tone_counter >= self._bit_windows - 1:
                    # Close enough - accept and process this window as data
                    self._data_bits = []
                    self._bit_accumulator = [window]
                    self._tone_counter = 1
                    self._state = VISState.DATA_BITS
                else:
                    self._tone_counter = 0
                    self._state = VISState.IDLE

        elif self._state == VISState.DATA_BITS:
            self._tone_counter += 1
            self._bit_accumulator.append(window)

            if self._tone_counter >= self._bit_windows:
                bit_audio = np.concatenate(self._bit_accumulator)
                bit_val = self._decode_bit(bit_audio)
                self._data_bits.append(bit_val)
                self._tone_counter = 0
                self._bit_accumulator = []

                if len(self._data_bits) == 7:
                    self._state = VISState.PARITY

        elif self._state == VISState.PARITY:
            self._tone_counter += 1
            self._bit_accumulator.append(window)

            if self._tone_counter >= self._bit_windows:
                bit_audio = np.concatenate(self._bit_accumulator)
                self._parity_bit = self._decode_bit(bit_audio)
                self._tone_counter = 0
                self._bit_accumulator = []
                self._state = VISState.STOP_BIT

        elif self._state == VISState.STOP_BIT:
            self._tone_counter += 1

            if self._tone_counter >= self._bit_windows:
                result = self._validate_and_decode()
                if result is not None:
                    # Do NOT call reset() here. self._buffer still holds
                    # samples that arrived after the STOP_BIT window — those
                    # are the very first samples of the image. Wiping the
                    # buffer here causes all of them to be lost, making the
                    # image decoder start mid-stream and producing
                    # garbled/diagonal output.
                    # feed() will advance past the current window, leaving
                    # self._buffer pointing at the image start. The caller
                    # must read remaining_buffer and then call reset().
                    self._state = VISState.DETECTED
                    return result
                else:
                    # Parity failure or unknown VIS code — reset and
                    # continue scanning for the next VIS header.
                    self._tone_counter = 0
                    self._data_bits = []
                    self._parity_bit = 0
                    self._bit_accumulator = []
                    self._state = VISState.IDLE

        elif self._state == VISState.DETECTED:
            # Waiting for caller to call reset() after reading remaining_buffer.
            # Don't process any windows in this state.
            pass

        return None

    def _track_leader(self, window: np.ndarray) -> None:
        """Look for a steady leader tone and measure the tuning offset from it."""
        offset = _leader_offset(window, self._sample_rate)
        steady = offset is not None and (
            not self._leader_offsets or abs(offset - float(np.median(self._leader_offsets))) <= LEADER_TRACK_TOLERANCE
        )
        if steady:
            self._leader_offsets.append(offset)
            self._miss_counter = 0
            if len(self._leader_offsets) >= self._leader_min_windows:
                self._offset = float(np.median(self._leader_offsets))
                self._tone_counter = len(self._leader_offsets)
                self._state = VISState.LEADER_1
        else:
            # Ambiguous window (noise/fading) — tolerate up to 3
            # consecutive misses before resetting the leader count.
            self._miss_counter += 1
            if self._miss_counter > 3:
                self._leader_offsets = []
                self._miss_counter = 0

    def _decode_bit(self, samples: np.ndarray) -> int:
        """Decode a single VIS data bit from its audio samples.

        Compares Goertzel energy at 1100 Hz (bit=1) vs 1300 Hz (bit=0),
        shifted by the tuning offset.
        """
        e1 = goertzel(samples, FREQ_VIS_BIT_1 + self._offset, self._sample_rate)
        e0 = goertzel(samples, FREQ_VIS_BIT_0 + self._offset, self._sample_rate)
        return 1 if e1 > e0 else 0

    def _validate_and_decode(self) -> tuple[int, str] | None:
        """Validate parity and decode the VIS code.

        Includes single-bit error correction for HF noise resilience:
        if parity fails, tries recovering by assuming either the parity
        bit or exactly one data bit was corrupted.

        Returns:
            (vis_code, mode_name) or None if validation fails.
        """
        if len(self._data_bits) != 7:
            return None

        parity_ok = (sum(self._data_bits) + self._parity_bit) % 2 == 0
        vis_code = sum(bit << i for i, bit in enumerate(self._data_bits))

        if parity_ok:
            mode_name = VIS_CODES.get(vis_code)
            if mode_name is not None:
                return vis_code, mode_name
            return None  # Valid parity but unknown code — not SSTV

        # Parity failed — try error correction

        # Case 1: only the parity bit is wrong (data is correct)
        mode_name = VIS_CODES.get(vis_code)
        if mode_name is not None:
            return vis_code, mode_name

        # Case 2: one data bit is wrong — try flipping each
        for flip in range(7):
            corrected = vis_code ^ (1 << flip)
            # Flipping one data bit should fix parity too
            corrected_parity_ok = (bin(corrected).count("1") + self._parity_bit) % 2 == 0
            if corrected_parity_ok:
                mode_name = VIS_CODES.get(corrected)
                if mode_name is not None:
                    return corrected, mode_name

        return None
