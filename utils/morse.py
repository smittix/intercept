"""Morse code (CW) decoder with dual detection modes.

Supports two signal chains:
  goertzel: rtl_fm -M usb → raw PCM → Goertzel tone filter → timing state machine → characters
  envelope: rtl_fm -M am  → raw PCM → RMS envelope       → timing state machine → characters

Goertzel mode is the original upstream path for HF CW (beat note detection).
Envelope mode adds support for OOK/AM signals (e.g. 433 MHz carrier keying)
where AM demod already produces a baseband envelope — no tone to detect.
"""

from __future__ import annotations

import contextlib
import math
import queue
import struct
import threading
import time
from datetime import datetime
from typing import Any

# International Morse Code table
MORSE_TABLE: dict[str, str] = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E',
    '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I', '.---': 'J',
    '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O',
    '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T',
    '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y',
    '--..': 'Z',
    '-----': '0', '.----': '1', '..---': '2', '...--': '3',
    '....-': '4', '.....': '5', '-....': '6', '--...': '7',
    '---..': '8', '----.': '9',
    '.-.-.-': '.', '--..--': ',', '..--..': '?', '.----.': "'",
    '-.-.--': '!', '-..-.': '/', '-.--.': '(', '-.--.-': ')',
    '.-...': '&', '---...': ':', '-.-.-.': ';', '-...-': '=',
    '.-.-.': '+', '-....-': '-', '..--.-': '_', '.-..-.': '"',
    '...-..-': '$', '.--.-.': '@',
    # Prosigns (unique codes only; -...- and -.--.- already mapped above)
    '-.-.-': '<CT>', '.-.-': '<AA>', '...-.-': '<SK>',
}

# Reverse lookup: character → morse notation
CHAR_TO_MORSE: dict[str, str] = {v: k for k, v in MORSE_TABLE.items()}


class GoertzelFilter:
    """Single-frequency tone detector using the Goertzel algorithm.

    O(N) per block, much cheaper than FFT for detecting one frequency.
    """

    def __init__(self, target_freq: float, sample_rate: int, block_size: int):
        self.target_freq = target_freq
        self.sample_rate = sample_rate
        self.block_size = block_size
        # Precompute coefficient
        k = round(target_freq * block_size / sample_rate)
        omega = 2.0 * math.pi * k / block_size
        self.coeff = 2.0 * math.cos(omega)

    def magnitude(self, samples: list[float] | tuple[float, ...]) -> float:
        """Compute magnitude of the target frequency in the sample block."""
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        coeff = self.coeff
        for sample in samples:
            s0 = sample + coeff * s1 - s2
            s2 = s1
            s1 = s0
        return math.sqrt(s1 * s1 + s2 * s2 - coeff * s1 * s2)


class EnvelopeDetector:
    """RMS envelope detector for AM-demodulated OOK signals.

    When rtl_fm uses -M am, carrier-on produces a high amplitude envelope
    and carrier-off produces near-silence.  RMS over a short block gives
    a clean on/off metric without needing a specific tone frequency.
    """

    def __init__(self, block_size: int):
        self.block_size = block_size

    def magnitude(self, samples: list[float] | tuple[float, ...]) -> float:
        """Compute RMS magnitude of the sample block."""
        if not samples:
            return 0.0
        sum_sq = 0.0
        for s in samples:
            sum_sq += s * s
        return math.sqrt(sum_sq / len(samples))


class MorseDecoder:
    """Real-time Morse decoder with adaptive threshold.

    Processes blocks of PCM audio and emits decoded characters.
    Timing based on PARIS standard: dit = 1.2/WPM seconds.

    detect_mode:
      'goertzel' — Goertzel single-frequency tone detection (HF CW via USB demod)
      'envelope' — RMS envelope detection (OOK/AM signals, e.g. 433 MHz keying)
    """

    def __init__(
        self,
        sample_rate: int = 8000,
        tone_freq: float = 700.0,
        wpm: int = 15,
        detect_mode: str = 'goertzel',
    ):
        self.sample_rate = sample_rate
        self.tone_freq = tone_freq
        self.wpm = wpm
        self.detect_mode = detect_mode

        # ~50 blocks/sec at 8kHz, scales with sample rate
        self._block_size = sample_rate // 50
        self._block_duration = self._block_size / sample_rate  # seconds per block

        # Select detection backend
        if detect_mode == 'envelope':
            self._filter = EnvelopeDetector(self._block_size)
        else:
            self._filter = GoertzelFilter(tone_freq, sample_rate, self._block_size)

        # Timing thresholds (in blocks, converted from seconds).
        # Gap thresholds use midpoint values between adjacent gap types
        # to tolerate edge-detection jitter from the adaptive threshold EMA:
        #   element gap = 1 dit  →  char threshold at 2.0 dit  ←  char gap = 3 dit
        #   char gap    = 3 dit  →  word threshold at 5.0 dit  ←  word gap = 7 dit
        dit_sec = 1.2 / wpm
        self._dah_threshold = 2.0 * dit_sec / self._block_duration  # blocks
        self._dit_min = 0.3 * dit_sec / self._block_duration  # min blocks for dit
        self._char_gap = 2.0 * dit_sec / self._block_duration  # blocks
        self._word_gap = 5.0 * dit_sec / self._block_duration  # blocks

        # Adaptive threshold via EMA.
        # Envelope mode uses a faster alpha — OOK has clean binary transitions,
        # unlike the gradual fading of HF CW through QSB.
        self._noise_floor = 0.0
        self._signal_peak = 0.0
        self._threshold = 0.0
        self._ema_alpha = 0.25 if detect_mode == 'envelope' else 0.1

        # State machine (counts in blocks, not wall-clock time)
        self._tone_on = False
        self._tone_blocks = 0  # blocks since tone started
        self._silence_blocks = 0  # blocks since silence started
        self._current_symbol = ''  # accumulates dits/dahs for current char
        self._pending_buffer: list[float] = []
        self._blocks_processed = 0  # total blocks for warm-up tracking

    def process_block(self, pcm_bytes: bytes) -> list[dict[str, Any]]:
        """Process a chunk of 16-bit LE PCM and return decoded events.

        Returns list of event dicts with keys:
          type: 'scope' | 'morse_char' | 'morse_space'
          + type-specific fields
        """
        events: list[dict[str, Any]] = []

        # Unpack PCM samples
        n_samples = len(pcm_bytes) // 2
        if n_samples == 0:
            return events

        samples = struct.unpack(f'<{n_samples}h', pcm_bytes[:n_samples * 2])

        # Feed samples into pending buffer and process in blocks
        self._pending_buffer.extend(samples)

        amplitudes: list[float] = []

        while len(self._pending_buffer) >= self._block_size:
            block = self._pending_buffer[:self._block_size]
            self._pending_buffer = self._pending_buffer[self._block_size:]

            # Normalize to [-1, 1]
            normalized = [s / 32768.0 for s in block]
            mag = self._filter.magnitude(normalized)
            amplitudes.append(mag)

            self._blocks_processed += 1

            # Update adaptive threshold
            if mag < self._threshold or self._threshold == 0:
                self._noise_floor += self._ema_alpha * (mag - self._noise_floor)
            else:
                self._signal_peak += self._ema_alpha * (mag - self._signal_peak)

            self._threshold = (self._noise_floor + self._signal_peak) / 2.0

            tone_detected = mag > self._threshold and self._threshold > 0

            if tone_detected and not self._tone_on:
                # Tone just started - check silence duration for gaps
                self._tone_on = True
                silence_count = self._silence_blocks
                self._tone_blocks = 0

                if self._current_symbol and silence_count >= self._char_gap:
                    # Character gap - decode accumulated symbol
                    char = MORSE_TABLE.get(self._current_symbol)
                    if char:
                        events.append({
                            'type': 'morse_char',
                            'char': char,
                            'morse': self._current_symbol,
                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                        })

                    if silence_count >= self._word_gap:
                        events.append({
                            'type': 'morse_space',
                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                        })

                    self._current_symbol = ''

            elif not tone_detected and self._tone_on:
                # Tone just ended - classify as dit or dah
                self._tone_on = False
                tone_count = self._tone_blocks
                self._silence_blocks = 0

                if tone_count >= self._dah_threshold:
                    self._current_symbol += '-'
                elif tone_count >= self._dit_min:
                    self._current_symbol += '.'

            elif tone_detected and self._tone_on:
                self._tone_blocks += 1

            elif not tone_detected and not self._tone_on:
                self._silence_blocks += 1

        # Emit scope data for visualization (~10 Hz is handled by caller)
        if amplitudes:
            events.append({
                'type': 'scope',
                'amplitudes': amplitudes,
                'threshold': self._threshold,
                'tone_on': self._tone_on,
            })

        return events

    def flush(self) -> list[dict[str, Any]]:
        """Flush any pending symbol at end of stream."""
        events: list[dict[str, Any]] = []
        if self._current_symbol:
            char = MORSE_TABLE.get(self._current_symbol)
            if char:
                events.append({
                    'type': 'morse_char',
                    'char': char,
                    'morse': self._current_symbol,
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                })
            self._current_symbol = ''
        return events


def morse_decoder_thread(
    rtl_stdout,
    output_queue: queue.Queue,
    stop_event: threading.Event,
    sample_rate: int = 8000,
    tone_freq: float = 700.0,
    wpm: int = 15,
    detect_mode: str = 'goertzel',
) -> None:
    """Thread function: reads PCM from rtl_fm, decodes Morse, pushes to queue.

    Reads raw 16-bit LE PCM from *rtl_stdout* and feeds it through the
    MorseDecoder, pushing scope and character events onto *output_queue*.

    detect_mode: 'goertzel' for HF CW tone detection, 'envelope' for OOK/AM.
    """
    import logging
    logger = logging.getLogger('intercept.morse')

    CHUNK = 4096  # bytes per read (2048 samples at 16-bit mono)
    SCOPE_INTERVAL = 0.1  # scope updates at ~10 Hz
    last_scope = time.monotonic()

    decoder = MorseDecoder(
        sample_rate=sample_rate,
        tone_freq=tone_freq,
        wpm=wpm,
        detect_mode=detect_mode,
    )

    try:
        while not stop_event.is_set():
            data = rtl_stdout.read(CHUNK)
            if not data:
                break

            events = decoder.process_block(data)

            for event in events:
                if event['type'] == 'scope':
                    # Throttle scope events to ~10 Hz
                    now = time.monotonic()
                    if now - last_scope >= SCOPE_INTERVAL:
                        last_scope = now
                        with contextlib.suppress(queue.Full):
                            output_queue.put_nowait(event)
                else:
                    # Character and space events always go through
                    with contextlib.suppress(queue.Full):
                        output_queue.put_nowait(event)

    except Exception as e:
        logger.debug(f"Morse decoder thread error: {e}")
    finally:
        # Flush any pending symbol
        for event in decoder.flush():
            with contextlib.suppress(queue.Full):
                output_queue.put_nowait(event)
