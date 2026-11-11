"""FFT pipeline for real-time waterfall display.

Converts raw I/Q samples from SDR hardware into quantized power spectrum
frames suitable for binary WebSocket transmission.
"""

from __future__ import annotations

import struct

import numpy as np


def cu8_to_complex(raw: bytes) -> np.ndarray:
    """Convert unsigned 8-bit I/Q bytes to complex64.

    RTL-SDR (and rx_sdr with -F cu8) outputs interleaved unsigned 8-bit
    I/Q pairs where 128 is the zero point.

    Args:
        raw: Raw bytes, length must be even (I/Q pairs).

    Returns:
        Complex64 array of length len(raw) // 2.
    """
    u8 = np.frombuffer(raw, dtype=np.uint8)
    i, q = np.broadcast_arrays(u8[0::2], u8[1::2])
    out = np.empty(i.shape, dtype=np.complex64)
    out.real = i
    out.imag = q
    iq = out.view(np.float32)
    # Normalize in place: 0 -> -1.0, 128 -> ~0.0, 255 -> +1.0
    iq -= 127.5
    iq /= 127.5
    return out


def cs8_to_complex(raw: bytes) -> np.ndarray:
    """Convert signed 8-bit I/Q bytes (hackrf_transfer's format) to complex64."""
    iq = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
    return iq[0::2] + 1j * iq[1::2]


def compute_power_spectrum(
    samples: np.ndarray,
    fft_size: int = 1024,
    avg_count: int = 4,
) -> np.ndarray:
    """Compute averaged power spectrum in dBm.

    Applies a Hann window, computes FFT, converts to power (dB),
    and averages over multiple segments.

    Args:
        samples: Complex64 array, length >= fft_size * avg_count.
        fft_size: Number of FFT bins.
        avg_count: Number of segments to average.

    Returns:
        Float32 array of length fft_size with power in dB (fftshift'd).
    """
    window = np.hanning(fft_size).astype(np.float32)
    accum = np.zeros(fft_size, dtype=np.float32)
    actual_avg = 0

    for i in range(avg_count):
        offset = i * fft_size
        if offset + fft_size > len(samples):
            break
        segment = samples[offset : offset + fft_size] * window
        spectrum = np.fft.fft(segment)
        power = np.real(spectrum * np.conj(spectrum))
        # Avoid log10(0)
        power = np.maximum(power, 1e-20)
        accum += 10.0 * np.log10(power)
        actual_avg += 1

    if actual_avg == 0:
        return np.full(fft_size, -100.0, dtype=np.float32)

    accum /= actual_avg
    return np.fft.fftshift(accum).astype(np.float32)


def quantize_to_uint8(
    power_db: np.ndarray,
    db_min: float | None = None,
    db_max: float | None = None,
) -> bytes:
    """Clamp and scale dB values to 0-255.

    When *db_min* / *db_max* are ``None`` (the default) the range is
    derived from the data so the full colour palette is always used.

    Args:
        power_db: Float32 array of power values in dB.
        db_min: Value mapped to 0 (auto if None).
        db_max: Value mapped to 255 (auto if None).

    Returns:
        Bytes of length len(power_db), each in [0, 255].
    """
    if db_min is None or db_max is None:
        actual_min = float(np.min(power_db))
        actual_max = float(np.max(power_db))
        # Guarantee at least 1 dB of dynamic range
        if actual_max - actual_min < 1.0:
            actual_max = actual_min + 1.0
        if db_min is None:
            db_min = actual_min
        if db_max is None:
            db_max = actual_max

    db_range = db_max - db_min
    if db_range <= 0:
        db_range = 1.0
    scaled = (power_db - db_min) / db_range * 255.0
    clamped = np.clip(scaled, 0, 255).astype(np.uint8)
    return clamped.tobytes()


def build_binary_frame(
    start_freq: float,
    end_freq: float,
    quantized_bins: bytes,
) -> bytes:
    """Pack a binary waterfall frame for WebSocket transmission.

    Wire format (little-endian):
        [uint8 msg_type=0x01]
        [float32 start_freq]
        [float32 end_freq]
        [uint16 bin_count]
        [uint8[] bins]

    Total size = 11 + bin_count bytes.

    Args:
        start_freq: Start frequency in MHz.
        end_freq: End frequency in MHz.
        quantized_bins: Pre-quantized uint8 bin data.

    Returns:
        Binary frame bytes.
    """
    bin_count = len(quantized_bins)
    header = struct.pack("<BffH", 0x01, start_freq, end_freq, bin_count)
    return header + quantized_bins
