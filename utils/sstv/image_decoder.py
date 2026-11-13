"""SSTV scanline-by-scanline image decoder.

Decodes raw audio samples into a PIL Image for all supported SSTV modes.
Handles sync pulse re-synchronization on each line for robust decoding
under weak-signal or drifting conditions.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from .constants import (
    FREQ_BLACK,
    FREQ_PIXEL_HIGH,
    FREQ_PIXEL_LOW,
    FREQ_SYNC,
    SAMPLE_RATE,
)
from .dsp import (
    goertzel_batch,
    samples_for_duration,
)
from .modes import (
    ColorModel,
    SSTVMode,
    SyncPosition,
)

# Pillow is imported lazily to keep the module importable when Pillow
# is not installed (is_sstv_available() just returns True, but actual
# decoding would fail gracefully).
try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment,misc]


# Type alias for progress callback: (current_line, total_lines)
ProgressCallback = Callable[[int, int], None]


class SSTVImageDecoder:
    """Decode an SSTV image from a stream of audio samples.

    Usage::

        decoder = SSTVImageDecoder(mode)
        decoder.feed(samples)
        ...
        if decoder.is_complete:
            image = decoder.get_image()
    """

    def __init__(
        self,
        mode: SSTVMode,
        sample_rate: int = SAMPLE_RATE,
        progress_cb: ProgressCallback | None = None,
        freq_offset: float = 0.0,
    ):
        self._mode = mode
        self._sample_rate = sample_rate
        self._progress_cb = progress_cb
        # SSB tuning offset measured from the VIS leader; every tone arrives shifted by it
        self._freq_offset = freq_offset
        self._sync_freq = FREQ_SYNC + freq_offset
        self._black_freq = FREQ_BLACK + freq_offset

        self._buffer = np.array([], dtype=np.float64)
        self._current_line = 0
        self._complete = False

        # Pre-calculate sample counts
        self._sync_samples = samples_for_duration(mode.sync_duration_ms / 1000.0, sample_rate)
        self._porch_samples = samples_for_duration(mode.sync_porch_ms / 1000.0, sample_rate)
        self._line_samples = samples_for_duration(mode.line_duration_ms / 1000.0, sample_rate)
        self._separator_samples = (
            samples_for_duration(mode.channel_separator_ms / 1000.0, sample_rate)
            if mode.channel_separator_ms > 0
            else 0
        )

        self._channel_samples = [samples_for_duration(ch.duration_ms / 1000.0, sample_rate) for ch in mode.channels]

        # For PD modes, each "line" of audio produces 2 image lines
        if mode.color_model == ColorModel.YCRCB_DUAL:
            self._total_audio_lines = mode.height // 2
        else:
            self._total_audio_lines = mode.height

        # Initialize pixel data arrays per channel
        self._channel_data: list[np.ndarray] = []
        for _i, _ch_spec in enumerate(mode.channels):
            if mode.color_model == ColorModel.YCRCB_DUAL:
                # Y1, Cr, Cb, Y2 - all are width-wide
                self._channel_data.append(np.zeros((self._total_audio_lines, mode.width), dtype=np.uint8))
            else:
                self._channel_data.append(np.zeros((mode.height, mode.width), dtype=np.uint8))

        # Track sync position for re-synchronization
        self._expected_line_start = 0  # Sample offset within buffer
        self._synced = False

        # Per-line mid-sync deviation measurements (Scottie modes only).
        # Each entry is the measured offset (in samples) of the sync pulse
        # relative to its expected position: negative = early, positive = late.
        # Used by get_image() to apply post-processing slant correction.
        self._sync_deviations: list[float | None] = []

    @property
    def is_complete(self) -> bool:
        return self._complete

    @property
    def current_line(self) -> int:
        return self._current_line

    @property
    def total_lines(self) -> int:
        return self._total_audio_lines

    @property
    def progress_percent(self) -> int:
        if self._total_audio_lines == 0:
            return 0
        return min(100, int(100 * self._current_line / self._total_audio_lines))

    def feed(self, samples: np.ndarray) -> bool:
        """Feed audio samples into the decoder.

        Args:
            samples: Float64 audio samples.

        Returns:
            True when image is complete.
        """
        if self._complete:
            return True

        self._buffer = np.concatenate([self._buffer, samples])

        # Process complete lines.
        # Guard against stalls: if _decode_line() cannot consume data
        # (e.g. sub-component samples exceed line_samples due to rounding),
        # break out and wait for more audio.
        while not self._complete and len(self._buffer) >= self._line_samples:
            prev_line = self._current_line
            prev_len = len(self._buffer)
            self._decode_line()
            if self._current_line == prev_line and len(self._buffer) == prev_len:
                break  # No progress — need more data

        # Prevent unbounded buffer growth - keep at most 2 lines worth
        max_buffer = self._line_samples * 2
        if len(self._buffer) > max_buffer and not self._complete:
            self._buffer = self._buffer[-max_buffer:]

        return self._complete

    def _find_sync_end(self, search_region: np.ndarray) -> int | None:
        """Find where the 1200 Hz sync pulse ends within a search region.

        The trailing edge is what anchors pixel timing, and unlike the
        leading edge it is immune to any 1200 Hz remnant merging into the
        front of the pulse (the VIS stop bit before an image's first sync
        is also 1200 Hz). Every window fully inside the pulse has
        near-maximal sync energy, so the end is the last window of the
        first contiguous near-peak run.

        Args:
            search_region: Audio samples to search within.

        Returns:
            Sample offset just past the sync pulse, or None if not found.
        """
        window_size = min(self._sync_samples, 200)
        if len(search_region) < window_size:
            return None

        step = 10
        windows = np.lib.stride_tricks.sliding_window_view(search_region, window_size)[::step]
        energies = goertzel_batch(windows, np.array([self._sync_freq, self._black_freq]), self._sample_rate)
        sync_e = energies[:, 0]
        black_e = energies[:, 1]
        # Check it's actually sync, not data at the 1200 Hz area
        valid = sync_e > black_e * 2
        if not valid.any():
            return None
        peak = float(sync_e[valid].max())
        if peak <= 0:
            return None
        good = np.flatnonzero(valid & (sync_e >= 0.85 * peak))
        run_end = int(good[0])
        for idx in good[1:]:
            if int(idx) - run_end <= 3:
                run_end = int(idx)
            else:
                break
        return run_end * step + window_size

    def _decode_line(self) -> None:
        """Decode one scanline from the buffer."""
        if self._current_line >= self._total_audio_lines:
            self._complete = True
            return

        # Try to find sync pulse for re-synchronization. The first line gets a
        # wide search (10% of a line) to lock on after the VIS header; after
        # that the search stays within ~1 ms, since real clock drift is a
        # sample or two per line and a wide search lets a noise burst pull the
        # decoder forward, shifting every following line.
        if self._synced:
            search_margin = max(24, self._sample_rate // 1000)
        else:
            search_margin = max(100, self._line_samples // 10)

        line_start = 0

        if self._mode.sync_position in (SyncPosition.FRONT, SyncPosition.FRONT_PD):
            # Sync is at the beginning of each line
            search_start = 0
            search_end = min(len(self._buffer), self._sync_samples + search_margin)
            search_region = self._buffer[search_start:search_end]

            sync_end = self._find_sync_end(search_region)
            if sync_end is not None:
                self._synced = True
                pixel_start = sync_end + self._porch_samples
            else:
                # No sync found: assume nominal timing from the buffer start
                pixel_start = line_start + self._sync_samples + self._porch_samples

        elif self._mode.sync_position == SyncPosition.MIDDLE:
            # Scottie: sep(1.5ms) -> G -> sep(1.5ms) -> B -> sync(9ms) -> porch(1.5ms) -> R
            # Skip initial separator (same duration as porch). The very first
            # line additionally starts with a 9 ms "starting sync" pulse that
            # exists only once per image; leaving it unconsumed shifts every
            # line of the image by its duration. Locate it to absorb any
            # VIS-handover jitter — Scottie lines never re-sync after this.
            pixel_start = self._porch_samples
            if self._current_line == 0:
                search_end = min(len(self._buffer), self._sync_samples + search_margin)
                sync_end = self._find_sync_end(self._buffer[:search_end])
                pixel_start += sync_end if sync_end is not None else self._sync_samples
            line_start = 0

        else:
            pixel_start = self._sync_samples + self._porch_samples

        # Decode each channel
        pos = pixel_start
        for ch_idx, ch_samples in enumerate(self._channel_samples):
            if pos + ch_samples > len(self._buffer):
                # Not enough data yet - put the data back and wait
                return

            # Include guard samples around the channel: the FFT Hilbert
            # transform assumes periodicity, so the discontinuity at the
            # segment edges corrupts the instantaneous frequency there —
            # enough to blow out the first and last pixel of fast modes.
            lead = min(64, pos)
            trail = min(64, len(self._buffer) - (pos + ch_samples))
            channel_audio = self._buffer[pos - lead : pos + ch_samples + trail]
            pixels = self._decode_channel_pixels(channel_audio, lead, trail)
            self._channel_data[ch_idx][self._current_line, :] = pixels
            pos += ch_samples

            # Add inter-channel gaps based on mode family
            if ch_idx < len(self._channel_samples) - 1:
                if self._mode.sync_position == SyncPosition.MIDDLE:
                    if ch_idx == 0:
                        # Scottie: separator between G and B
                        pos += self._porch_samples
                    else:
                        # Scottie: sync + porch between B and R.
                        # Measure the actual sync position for post-processing
                        # slant correction without touching pos or consumed —
                        # so a noisy/false measurement never corrupts the decode.
                        r_samples = self._channel_samples[-1]
                        # Large window to cover cumulative SDR clock drift over
                        # the full image.  bwd=50 saturates after ~25 lines for
                        # a 2-sample/line drift; 800 samples covers ±200 ppm.
                        bwd = min(800, pos)
                        remaining = len(self._buffer) - pos
                        fwd = min(800, max(0, remaining - self._sync_samples - self._porch_samples - r_samples))
                        deviation: float | None = None
                        if bwd + fwd > 0:
                            region_start = pos - bwd
                            sync_region = self._buffer[region_start : pos + self._sync_samples + fwd]
                            # Full-sync-length window gives best freq resolution
                            # (~111 Hz at 48 kHz) to cleanly separate 1200 Hz
                            # sync from 1500 Hz pixel data.
                            win = self._sync_samples
                            n_raw = len(sync_region) - win + 1
                            if n_raw > 0:
                                # Step 5 samples (~0.1 ms) — enough resolution
                                # for pixel-level drift, keeps batch size small.
                                step = 5
                                all_windows = np.lib.stride_tricks.sliding_window_view(sync_region, win)
                                windows = all_windows[::step]
                                energies = goertzel_batch(
                                    windows, np.array([self._sync_freq, self._black_freq]), self._sample_rate
                                )
                                sync_e = energies[:, 0]
                                black_e = energies[:, 1]
                                valid_mask = sync_e > black_e * 2
                                if valid_mask.any():
                                    fine_idx = int(np.argmax(np.where(valid_mask, sync_e, 0.0)))
                                    deviation = float(fine_idx * step - bwd)
                        self._sync_deviations.append(deviation)
                        pos += self._sync_samples + self._porch_samples
                elif self._separator_samples > 0:
                    # Robot: separator + porch between channels
                    pos += self._separator_samples
                elif self._mode.sync_position == SyncPosition.FRONT and self._mode.color_model == ColorModel.RGB:
                    # Martin: porch between channels
                    pos += self._porch_samples

        # Advance buffer past this line
        consumed = max(pos, self._line_samples)
        self._buffer = self._buffer[consumed:]

        self._current_line += 1

        if self._progress_cb:
            self._progress_cb(self._current_line, self._total_audio_lines)

        if self._current_line >= self._total_audio_lines:
            self._complete = True

    def _decode_channel_pixels(self, audio: np.ndarray, lead: int = 0, trail: int = 0) -> np.ndarray:
        """Decode pixel values from a channel's audio data.

        Uses the analytic signal (Hilbert transform via FFT) to compute
        the instantaneous frequency at every sample, then averages over
        each pixel's duration.  This is the same FM-demodulation approach
        used by QSSTV and other professional SSTV decoders, and provides
        far better frequency resolution than windowed Goertzel — especially
        for fast modes (Martin2, Scottie2) where each pixel spans only
        ~11-13 audio samples.

        Args:
            audio: Audio samples for one channel of one scanline, with
                ``lead``/``trail`` guard samples on each side that absorb
                the transform's edge artifacts and are discarded.

        Returns:
            Array of pixel values (0-255), shape (width,).
        """
        width = self._mode.width
        n = len(audio)

        if n - lead - trail < width:
            return np.zeros(width, dtype=np.uint8)

        # --- Analytic signal via Hilbert transform (FFT method) ---
        spectrum = np.fft.fft(audio)

        # Build the analytic-signal multiplier:
        #   h[0] = 1 (DC), h[1..N/2-1] = 2 (positive freqs),
        #   h[N/2] = 1 (Nyquist), h[N/2+1..] = 0 (negative freqs)
        h = np.zeros(n)
        if n % 2 == 0:
            h[0] = h[n // 2] = 1
            h[1 : n // 2] = 2
        else:
            h[0] = 1
            h[1 : (n + 1) // 2] = 2

        analytic = np.fft.ifft(spectrum * h)

        # --- Instantaneous frequency ---
        phase = np.unwrap(np.angle(analytic))
        inst_freq = np.diff(phase) * (self._sample_rate / (2.0 * np.pi))

        # Noise spikes send the instantaneous frequency anywhere up to
        # ±Nyquist; one such sample would drag a whole pixel's average to
        # full white or black. No SSTV tone lives outside 400-3000 Hz.
        np.clip(inst_freq, 400.0, 3000.0, out=inst_freq)

        # Drop the guard samples (diff consumed one sample overall)
        inst_freq = inst_freq[lead : len(inst_freq) - trail]

        # --- Average frequency per pixel ---
        freq_len = len(inst_freq)
        if freq_len < width:
            # Fewer freq samples than pixels — index directly
            indices = np.linspace(0, freq_len - 1, width).astype(int)
            avg_freqs = inst_freq[indices]
        else:
            pixel_edges = np.linspace(0, freq_len, width + 1).astype(int)
            segment_starts = pixel_edges[:-1]
            segment_lengths = np.diff(pixel_edges)
            segment_lengths = np.maximum(segment_lengths, 1)
            sums = np.add.reduceat(inst_freq, segment_starts)
            avg_freqs = sums / segment_lengths

        # Map to pixel values (1500 Hz → 0, 2300 Hz → 255), after removing the tuning offset
        normalized = (avg_freqs - self._freq_offset - FREQ_PIXEL_LOW) / (FREQ_PIXEL_HIGH - FREQ_PIXEL_LOW)
        return np.clip(normalized * 255 + 0.5, 0, 255).astype(np.uint8)

    def get_image(self) -> Image.Image | None:
        """Convert decoded channel data to a PIL Image.

        Returns:
            PIL Image in RGB mode, or None if Pillow is not available
            or decoding is incomplete.
        """
        if Image is None:
            return None

        mode = self._mode

        if mode.color_model == ColorModel.RGB:
            img = self._assemble_rgb()
        elif mode.color_model == ColorModel.YCRCB:
            img = self._assemble_ycrcb()
        elif mode.color_model == ColorModel.YCRCB_DUAL:
            img = self._assemble_ycrcb_dual()
        else:
            return None

        return self._apply_slant_correction(img)

    def _apply_slant_correction(self, img: Image.Image) -> Image.Image:
        """Apply per-row horizontal correction based on measured sync drift.

        Uses the sync deviation measurements collected during decoding to
        estimate the per-line SDR clock drift rate via linear regression,
        then circularly shifts each row to compensate. Noisy individual
        measurements are averaged out; if fewer than 10 valid measurements
        exist the image is returned unchanged.
        """
        valid = [(i, d) for i, d in enumerate(self._sync_deviations) if d is not None]
        if len(valid) < 10:
            return img

        lines = np.array([x[0] for x in valid], dtype=np.float64)
        devs = np.array([x[1] for x in valid], dtype=np.float64)

        # Linear fit: deviation[i] ≈ slope × i + intercept.
        # slope < 0 → fast SDR (sync arrives early, image leans left).
        # slope > 0 → slow SDR (sync arrives late, image leans right).
        slope, _ = np.polyfit(lines, devs, 1)

        # Convert samples/line drift to pixels/line for the channel width.
        pixels_per_line = slope * self._mode.width / self._channel_samples[0]

        # Skip correction if drift is negligible (< 1 px over 20 lines).
        if abs(pixels_per_line) < 0.05:
            return img

        arr = np.array(img)
        height, width = arr.shape[:2]

        # Reject clearly implausible estimates. Even with cheap SDR clocks,
        # real SSTV slant is typically modest; extreme values are usually
        # bad sync picks that would over-correct the image.
        total_shift = abs((height - 1) * pixels_per_line)
        if total_shift > width * 0.25:
            return img

        corrected = np.empty_like(arr)

        for row in range(height):
            shift = -int(round(row * pixels_per_line))
            corrected[row] = np.roll(arr[row], shift=shift, axis=0)

        return Image.fromarray(corrected, "RGB")

    def _assemble_rgb(self) -> Image.Image:
        """Assemble RGB image from sequential R, G, B channel data.

        Martin/Scottie channel order: G, B, R.
        """
        height = self._mode.height

        # Channel order for Martin/Scottie: [0]=G, [1]=B, [2]=R
        g_data = self._channel_data[0][:height]
        b_data = self._channel_data[1][:height]
        r_data = self._channel_data[2][:height]

        rgb = np.stack([r_data, g_data, b_data], axis=-1)
        return Image.fromarray(rgb, "RGB")

    def _assemble_ycrcb(self) -> Image.Image:
        """Assemble image from YCrCb data (Robot modes).

        Robot36: Y every line, Cr/Cb alternating (half-rate chroma).
        Robot72: Y, Cr, Cb every line (full-rate chroma).
        """
        height = self._mode.height
        width = self._mode.width

        if not self._mode.has_half_rate_chroma:
            # Full-rate chroma (Robot72): Y, Cr, Cb as separate channels
            y_data = self._channel_data[0][:height].astype(np.float64)
            cr = self._channel_data[1][:height].astype(np.float64)
            cb = self._channel_data[2][:height].astype(np.float64)
            return self._ycrcb_to_rgb(y_data, cr, cb, height, width)

        # Half-rate chroma (Robot36): Y + alternating Cr/Cb
        y_data = self._channel_data[0][:height].astype(np.float64)
        chroma_data = self._channel_data[1][:height].astype(np.float64)

        # Separate Cr (even lines) and Cb (odd lines), then interpolate
        cr = np.zeros((height, width), dtype=np.float64)
        cb = np.zeros((height, width), dtype=np.float64)

        for line in range(height):
            if line % 2 == 0:
                cr[line] = chroma_data[line]
            else:
                cb[line] = chroma_data[line]

        # Interpolate missing chroma lines
        for line in range(height):
            if line % 2 == 1:
                # Missing Cr - interpolate from neighbors
                prev_cr = line - 1 if line > 0 else line + 1
                next_cr = line + 1 if line + 1 < height else line - 1
                cr[line] = (cr[prev_cr] + cr[next_cr]) / 2
            else:
                # Missing Cb - interpolate from neighbors
                prev_cb = line - 1 if line > 0 else line + 1
                next_cb = line + 1 if line + 1 < height else line - 1
                if prev_cb >= 0 and next_cb < height:
                    cb[line] = (cb[prev_cb] + cb[next_cb]) / 2
                elif prev_cb >= 0:
                    cb[line] = cb[prev_cb]
                else:
                    cb[line] = cb[next_cb]

        return self._ycrcb_to_rgb(y_data, cr, cb, height, width)

    def _assemble_ycrcb_dual(self) -> Image.Image:
        """Assemble image from dual-luminance YCrCb data (PD modes).

        PD modes send Y1, Cr, Cb, Y2 per audio line, producing 2 image lines.
        """
        audio_lines = self._total_audio_lines
        width = self._mode.width
        height = self._mode.height

        y1_data = self._channel_data[0][:audio_lines].astype(np.float64)
        cr_data = self._channel_data[1][:audio_lines].astype(np.float64)
        cb_data = self._channel_data[2][:audio_lines].astype(np.float64)
        y2_data = self._channel_data[3][:audio_lines].astype(np.float64)

        # Interleave Y1 and Y2 to produce full-height luminance
        y_full = np.zeros((height, width), dtype=np.float64)
        cr_full = np.zeros((height, width), dtype=np.float64)
        cb_full = np.zeros((height, width), dtype=np.float64)

        for i in range(audio_lines):
            even_line = i * 2
            odd_line = i * 2 + 1
            if even_line < height:
                y_full[even_line] = y1_data[i]
                cr_full[even_line] = cr_data[i]
                cb_full[even_line] = cb_data[i]
            if odd_line < height:
                y_full[odd_line] = y2_data[i]
                cr_full[odd_line] = cr_data[i]
                cb_full[odd_line] = cb_data[i]

        return self._ycrcb_to_rgb(y_full, cr_full, cb_full, height, width)

    @staticmethod
    def _ycrcb_to_rgb(y: np.ndarray, cr: np.ndarray, cb: np.ndarray, height: int, width: int) -> Image.Image:
        """Convert YCrCb pixel data to an RGB PIL Image.

        Uses the SSTV convention where pixel values 0-255 map to the
        standard Y'CbCr color space used by JPEG/SSTV.
        """
        # Normalize from 0-255 pixel range to standard ranges
        # Y: 0-255, Cr/Cb: 0-255 centered at 128
        y_norm = y
        cr_norm = cr - 128.0
        cb_norm = cb - 128.0

        # ITU-R BT.601 conversion
        r = y_norm + 1.402 * cr_norm
        g = y_norm - 0.344136 * cb_norm - 0.714136 * cr_norm
        b = y_norm + 1.772 * cb_norm

        # Clip and convert
        r = np.clip(r, 0, 255).astype(np.uint8)
        g = np.clip(g, 0, 255).astype(np.uint8)
        b = np.clip(b, 0, 255).astype(np.uint8)

        rgb = np.stack([r, g, b], axis=-1)
        return Image.fromarray(rgb, "RGB")
