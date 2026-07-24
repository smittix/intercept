#!/usr/bin/env python3
"""
DSC (Digital Selective Calling) decoder.

Decodes VHF DSC signals per ITU-R M.493. Reads 48kHz 16-bit signed
audio from stdin (from rtl_fm) and outputs JSON messages to stdout.

DSC uses 1200 bps FSK on a 1700 Hz subcarrier with:
- Mark (B state, binary 0): 2100 Hz
- Space (Y state, binary 1): 1300 Hz

Frame structure:
1. Dot pattern: 200 bits alternating 1/0 for synchronization
2. Phasing sequence: 7 symbols (RX or DX pattern)
3. Format specifier: Identifies message type
4. Address/Self-ID fields
5. Category/Nature fields (if distress)
6. Position data (if present)
7. Telecommand fields
8. EOS (End of Sequence)

Each symbol is 10 bits (7 data + 3 check bits).

IMPORTANT: DSC transmits every character TWICE (DX + RX copies ~5 positions
apart). This decoder implements de-interleaving by detecting the format
specifier anchor and taking every other symbol to recover the original data.

Per ITU-R M.493-16:
- 3 check bits encode the count of B-elements (binary 0) among the 7 info bits.
- Dot pattern: 200 alternating 1/0 bits.
- DX/RX phasing: 7 specific symbols to establish symbol framing.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Generator
from datetime import datetime

import numpy as np
from scipy import signal as scipy_signal

from .constants import (
    DISTRESS_NATURE_CODES,
    DSC_AUDIO_SAMPLE_RATE,
    DSC_BAUD_RATE,
    DSC_MARK_FREQ,
    DSC_SPACE_FREQ,
    FORMAT_CODES,
    MIN_SYMBOLS_FOR_FORMAT,
    TELECOMMAND_FORMATS,
    VALID_EOS,
)

# Configure logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
logger = logging.getLogger("dsc.decoder")


def encode_symbol(symbol: int) -> tuple[int, ...]:
    """Encode a 7-bit DSC symbol (0-127) into 10 bits (7 info + 3 check).

    Per ITU-R M.493-16: the 3 check bits encode the count of B-elements
    (binary 0) among the 7 info bits, as a 3-bit binary number (MSB first).
    """
    if not 0 <= symbol <= 127:
        raise ValueError(f"DSC symbol out of range: {symbol}")
    # LSB first per DSC convention
    info = tuple((symbol >> i) & 1 for i in range(7))
    # Count of B-elements (binary 0) among the 7 info bits
    b_count = 7 - sum(info)
    # 3 check bits encode b_count as MSB-first 3-bit value
    check = ((b_count >> 2) & 1, (b_count >> 1) & 1, b_count & 1)
    return info + check


def bits_to_symbol(bits: list[int] | tuple[int, ...]) -> int:
    """Convert 10 bits to symbol value with 1-bit error tolerance.

    Returns -1 if the check bits are invalid (no match within 1-bit error).
    """
    if len(bits) != 10:
        return -1

    # First 7 bits are data (LSB first in DSC)
    value = 0
    for i in range(7):
        if bits[i]:
            value |= 1 << i

    # Generate expected 10-bit encoding for this value
    expected = encode_symbol(value)
    errors = sum(1 for a, b in zip(bits, expected) if a != b)

    if errors == 0:
        return value

    # 1-bit error tolerance: try flipping each of the 10 bit positions
    # and see if it matches any valid symbol
    if errors <= 2:
        # Try nearby symbols (1-bit error in info bits)
        for bit_pos in range(10):
            test_bits = list(bits)
            test_bits[bit_pos] ^= 1  # Flip this bit
            test_val = 0
            for i in range(7):
                if test_bits[i]:
                    test_val |= 1 << i
            test_expected = encode_symbol(test_val)
            test_errors = sum(1 for a, b in zip(test_bits, test_expected) if a != b)
            if test_errors == 0:
                return test_val

    return -1


def pair_digits(symbols: list[int]) -> str:
    """Join symbols as 2-digit strings (BCD encoding)."""
    return "".join(f"{s:02d}" for s in symbols)


def decode_mmsi(symbols: list[int]) -> str | None:
    """Decode MMSI from 5 DSC symbols.

    Each symbol represents 2 BCD digits (00-99).
    5 symbols = 10 digits, but MMSI is 9 digits.
    The first digit is always 0 (leading zero padding), so we strip it.
    """
    if len(symbols) < 5 or any(s < 0 or s > 99 for s in symbols):
        return None
    digits = "".join(f"{s:02d}" for s in symbols)
    # Strip leading zero from 10-digit BCD encoding to get 9-digit MMSI
    if len(digits) == 10:
        return digits[1:]
    return digits


def is_valid_mmsi(mmsi: str | None) -> bool:
    """Validate MMSI per ITU-R M.585."""
    if not mmsi or not isinstance(mmsi, str):
        return False
    if len(mmsi) != 9 or not mmsi.isdigit():
        return False
    if all(c == mmsi[0] for c in mmsi):
        return False
    return True


def decode_position(symbols: list[int]) -> dict | None:
    """Decode DSC position from 10 symbols (ITU-R M.493-11, distress format).

    Each symbol is a single digit 0-9. 10 symbols -> 10 digits.

    Latitude digits:  [quadrant, lat_deg_t, lat_deg_u, lat_min_t, lat_min_u]
    Longitude digits: [lon_deg_h, lon_deg_t, lon_deg_u, lon_min_t, lon_min_u]

    Quadrant: 0=NE, 1=NW, 2=SE, 3=SW
    Digit 9 repeated 10 times = position unavailable.
    """
    if len(symbols) != 10:
        return None

    digits = list(symbols)

    # Check for position unavailable
    if all(d == 9 for d in digits):
        return {"raw": "".join(str(d) for d in digits), "valid": False, "unavailable": True}

    # Each symbol must be 0-9
    if any(d < 0 or d > 9 for d in digits):
        return None

    quadrant = digits[0]
    # Quadrant: 0=NE, 1=NW, 2=SE, 3=SW
    if quadrant not in (0, 1, 2, 3):
        return None

    lat_deg = digits[1] * 10 + digits[2]
    lat_min = digits[3] * 10 + digits[4]
    lon_deg = digits[5] * 100 + digits[6] * 10 + digits[7]
    lon_min = digits[8] * 10 + digits[9]

    if lat_deg > 90 or lat_min >= 60 or lon_deg > 180 or lon_min >= 60:
        return {"raw": "".join(str(d) for d in digits), "valid": False}

    lat = lat_deg + lat_min / 60.0
    lon = lon_deg + lon_min / 60.0
    if quadrant in (2, 3):
        lat = -lat
    if quadrant in (1, 3):
        lon = -lon

    return {
        "raw": "".join(str(d) for d in digits),
        "valid": True,
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
    }


# Per ITU-R M.493-16: format specifier symbol values
FORMAT_SPECIFIERS = {100, 102, 112, 113, 114, 115, 116, 118, 120, 121, 123}
# Per ITU-R M.493-16: distress format specifiers (must carry nature + position)
DISTRESS_FORMATS = {112}

# De-interleaving: DSC transmits every character twice (DX then RX copy).
# After the phasing sequence, the format specifier appears in two consecutive
# symbol positions (stride 1), then every 2nd symbol is a copy.
# Field layout per format specifier (field_name, field_length_in_symbols):
# Symbols are 7-bit values (0-127), 2-digit BCD pairs per MMSI.
FIELD_LAYOUTS = {
    120: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    123: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    116: [("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    114: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    112: [("self_id", 5), ("nature", 1), ("coordinates", 5), ("time_utc", 4), ("telecommand1", 1)],
    102: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    # Extended formats (less common)
    100: [("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    113: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    115: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    118: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
    121: [("address", 5), ("category", 1), ("self_id", 5), ("telecommand1", 1), ("telecommand2", 1)],
}

CATEGORY_TEXT = {
    100: "ROUTINE",
    106: "SHIPS_BUSINESS",
    108: "SAFETY",
    110: "URGENCY",
    112: "DISTRESS",
}

EOS_TEXT = {
    117: "ACK_RQ",
    122: "ACK_BQ",
    127: "EOS",
}


class DSCDecoder:
    """
    DSC FSK decoder.

    Demodulates 1200 bps FSK audio and decodes DSC protocol with
    de-interleaving and 1-bit error tolerant symbol decoding.
    """

    def __init__(self, sample_rate: int = DSC_AUDIO_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.baud_rate = DSC_BAUD_RATE
        self.samples_per_bit = sample_rate // self.baud_rate

        # FSK frequencies
        self.mark_freq = DSC_MARK_FREQ  # 2100 Hz = binary 0 (B state)
        self.space_freq = DSC_SPACE_FREQ  # 1300 Hz = binary 1 (Y state)

        # Bandpass filter for DSC band (800-2800 Hz to preserve both tones)
        nyq = sample_rate / 2
        self.bp_b, self.bp_a = scipy_signal.butter(4, [800 / nyq, 2800 / nyq], btype="band")

        # Build FSK correlators
        self._build_correlators()

        # State
        self.buffer = np.array([], dtype=np.int16)
        self.bit_buffer: list[int] = []
        self.in_message = False
        self.message_bits: list[int] = []

    def _build_correlators(self):
        """Build matched filter correlators for mark and space frequencies."""
        t = np.arange(self.samples_per_bit) / self.sample_rate
        self.mark_ref = np.sin(2 * np.pi * self.mark_freq * t)
        self.space_ref = np.sin(2 * np.pi * self.space_freq * t)

    def process_audio(self, audio_data: bytes) -> Generator[dict, None, None]:
        """Process audio data and yield decoded DSC messages."""
        samples = np.frombuffer(audio_data, dtype=np.int16)
        if len(samples) == 0:
            return

        self.buffer = np.concatenate([self.buffer, samples])

        if len(self.buffer) < self.samples_per_bit:
            return

        # Remove DC offset only — bandpass filters can distort timing
        filtered = self.buffer.astype(np.float64)
        filtered -= np.mean(filtered)
        try:
            filtered = scipy_signal.filtfilt(self.bp_b, self.bp_a, filtered)
        except Exception as e:
            logger.warning(f"Filter error: {e}")
            return

        bits = self._demodulate_fsk(filtered)

        keep_samples = self.samples_per_bit * 8  # keep more context for sync
        if len(self.buffer) > keep_samples:
            self.buffer = self.buffer[-keep_samples:]

        for bit in bits:
            message = self._process_bit(bit)
            if message:
                yield message

    def _demodulate_fsk(self, samples: np.ndarray) -> list[int]:
        """Demodulate FSK audio to bits using correlation."""
        bits = []
        num_bits = len(samples) // self.samples_per_bit

        for i in range(num_bits):
            start = i * self.samples_per_bit
            end = start + self.samples_per_bit
            segment = samples[start:end]

            if len(segment) < self.samples_per_bit:
                break

            mark_corr = np.abs(np.correlate(segment, self.mark_ref, mode="valid"))
            space_corr = np.abs(np.correlate(segment, self.space_ref, mode="valid"))

            # Mark (2100Hz) = binary 0 (B state), Space (1300Hz) = binary 1 (Y state)
            if np.max(mark_corr) > np.max(space_corr):
                bits.append(0)
            else:
                bits.append(1)

        return bits

    def _process_bit(self, bit: int) -> dict | None:
        """Process a decoded bit and detect/decode DSC messages."""
        self.bit_buffer.append(bit)

        if len(self.bit_buffer) > 2000:
            self.bit_buffer = self.bit_buffer[-1500:]

        if not self.in_message and self._detect_dot_pattern():
            self.in_message = True
            self.message_bits = []
            logger.debug("DSC sync detected")
            return None

        if self.in_message:
            self.message_bits.append(bit)

            if len(self.message_bits) >= 10:
                message = self._try_decode_message()
                if message:
                    self.in_message = False
                    self.message_bits = []
                    return message

            if len(self.message_bits) > 2000:
                logger.debug("DSC message timeout")
                self.in_message = False
                self.message_bits = []

        return None

    def _detect_dot_pattern(self) -> bool:
        """Detect DSC dot pattern (>=100 alternating bits)."""
        if len(self.bit_buffer) < 200:
            return False

        last_bits = self.bit_buffer[-200:]
        alternations = 0

        for i in range(1, len(last_bits)):
            if last_bits[i] != last_bits[i - 1]:
                alternations += 1
            else:
                alternations = 0

            if alternations >= 100:
                return True

        return False

    def _try_decode_message(self) -> dict | None:
        """Try to decode accumulated message bits as DSC message.

        Implements de-interleaving: DSC transmits every symbol twice,
        with the duplicated symbol appearing ~5 positions later.
        We detect the format specifier anchor and take every 2nd symbol.
        """
        num_symbols = len(self.message_bits) // 10
        if num_symbols < 5:
            return None

        # Convert raw bits to symbol values (tolerating 1-bit errors)
        symbols = []
        for i in range(num_symbols):
            start = i * 10
            end = start + 10
            if end <= len(self.message_bits):
                sym = bits_to_symbol(self.message_bits[start:end])
                symbols.append(sym)

        # Count valid symbols
        valid_count = sum(1 for s in symbols if s != -1)

        # Try to find the format specifier anchor for de-interleaving.
        # The format specifier appears twice at positions N and N+2
        # (every time). Use this as the anchor to de-interleave.
        for anchor in range(len(symbols) - 2):
            if (
                symbols[anchor] in FORMAT_SPECIFIERS
                and symbols[anchor] != -1
                and symbols[anchor + 2] in FORMAT_SPECIFIERS
                and symbols[anchor + 2] != -1
            ):
                # Found anchor! De-interleave: take stride 2
                dedup = symbols[anchor::2]

                # Helper to read from de-interleaved data
                fmt = dedup[0]
                if fmt not in FIELD_LAYOUTS:
                    continue

                layout = FIELD_LAYOUTS[fmt]
                idx = 1  # skip the format specifier itself
                fields: dict[str, list[int]] = {}
                ok = True
                for name, count in layout:
                    if idx + count > len(dedup):
                        ok = False
                        break
                    fields[name] = dedup[idx : idx + count]
                    idx += count

                if not ok:
                    continue

                # Find EOS
                eos = None
                for k in range(idx, len(dedup)):
                    v = dedup[k]
                    if v in VALID_EOS:
                        eos = v
                        break

                if eos is None:
                    continue

                return self._build_message(fmt, eos, fields, symbols, valid_count, dedup)

        # Fallback: if no de-interleave anchor found, try direct decoding
        # (only for very clean signals)
        return self._try_direct_decode(symbols, valid_count)

    def _try_direct_decode(self, symbols: list[int], valid_count: int) -> dict | None:
        """Fallback: decode without de-interleaving (clean signals only)."""
        # Strip phasing symbols (120-126)
        msg_start = 0
        for i, sym in enumerate(symbols):
            if 120 <= sym <= 126:
                msg_start = i + 1
            else:
                break
        if msg_start > 7:
            return None
        symbols = symbols[msg_start:]

        if len(symbols) < 5:
            return None

        # Find EOS
        eos = None
        for i, sym in enumerate(symbols):
            if sym in VALID_EOS and i >= MIN_SYMBOLS_FOR_FORMAT:
                eos = sym
                symbols = symbols[: i + 1]
                break

        if eos is None:
            return None

        if len(symbols) < 12:
            return None

        fmt = symbols[0]
        if fmt not in FORMAT_SPECIFIERS:
            return None

        # For direct decode, extract fields by position
        # Format specifier + address(5) + category(1) + self_id(5) + tc1(1) + tc2(1) + eos
        fields: dict[str, list[int]] = {}
        if len(symbols) >= 12:
            fields["address"] = symbols[1:6]
            if len(symbols) >= 13:
                fields["category"] = [symbols[6]]
                fields["self_id"] = symbols[7:12]
                if len(symbols) >= 15:
                    fields["telecommand1"] = [symbols[12]]
                    fields["telecommand2"] = [symbols[13]]

        return self._build_message(fmt, eos, fields, symbols, valid_count, None)

    def _build_message(
        self,
        fmt: int,
        eos: int,
        fields: dict[str, list[int]],
        all_symbols: list[int],
        valid_count: int,
        dedup: list[int] | None,
    ) -> dict | None:
        """Build the decoded DSC message dict from parsed fields."""
        message: dict = {
            "type": "dsc",
            "format": fmt,
            "format_text": FORMAT_CODES.get(fmt, f"UNKNOWN-{fmt}"),
            "eos": eos,
            "eos_text": EOS_TEXT.get(eos, str(eos)),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "valid_symbols": valid_count,
        }

        if dedup is not None:
            message["deinterleaved"] = True
        else:
            message["deinterleaved"] = False

        # Decode MMSIs
        if "address" in fields:
            message["dest_mmsi"] = decode_mmsi(fields["address"])
        if "self_id" in fields:
            message["source_mmsi"] = decode_mmsi(fields["self_id"])

        # Validate MMSIs
        if not is_valid_mmsi(message.get("source_mmsi")):
            return None

        # Category
        category_code = None
        if "category" in fields and fields["category"]:
            category_code = fields["category"][0]
            message["category"] = CATEGORY_TEXT.get(category_code, f"UNKNOWN-{category_code}")
            message["category_code"] = category_code

        # Distress format: nature + position
        if fmt == 112:
            message["category"] = "DISTRESS"
            if "nature" in fields and fields["nature"]:
                nature_code = fields["nature"][0]
                # Nature codes are 0-31 (lower 5 bits of symbol value)
                message["nature"] = nature_code
                message["nature_text"] = DISTRESS_NATURE_CODES.get(nature_code, f"UNKNOWN-{nature_code}")

            if "coordinates" in fields:
                pos = decode_position(fields["coordinates"])
                if pos:
                    message["position"] = pos

            if "time_utc" in fields:
                message["time_utc"] = "".join(str(d) for d in fields["time_utc"])

        # Telecommand fields
        if "telecommand1" in fields and fields["telecommand1"]:
            message["telecommand1"] = fields["telecommand1"][0]
        if "telecommand2" in fields and fields["telecommand2"]:
            message["telecommand2"] = fields["telecommand2"][0]

        # Raw symbol data for debugging
        message["raw"] = "".join(f"{s:03d}" for s in all_symbols)

        logger.info(
            f"Decoded DSC: fmt={fmt} src={message.get('source_mmsi', '?')} "
            f"cat={message.get('category', '?')}"
        )
        return message

    def _decode_mmsi(self, symbols: list[int]) -> str | None:
        """Legacy MMSI decoding (kept for backward compatibility)."""
        return decode_mmsi(symbols)

    def _decode_position(self, symbols: list[int]) -> dict | None:
        """Legacy position decoding (kept for backward compatibility)."""
        return decode_position(symbols)

    def _bits_to_symbol(self, bits: list[int] | tuple[int, ...]) -> int:
        """Legacy bit-to-symbol conversion (kept for backward compatibility)."""
        return bits_to_symbol(bits)


def read_audio_stdin() -> Generator[bytes, None, None]:
    """Read audio from stdin in chunks."""
    chunk_size = 4800  # 0.1 seconds at 48kHz, 16-bit = 9600 bytes
    while True:
        try:
            data = sys.stdin.buffer.read(chunk_size * 2)
            if not data:
                break
            yield data
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Read error: {e}")
            break


def main():
    """Main entry point for DSC decoder."""
    parser = argparse.ArgumentParser(
        description="DSC (Digital Selective Calling) decoder",
        epilog="Reads 48kHz 16-bit signed PCM audio from stdin",
    )
    parser.add_argument(
        "-r",
        "--sample-rate",
        type=int,
        default=DSC_AUDIO_SAMPLE_RATE,
        help=f"Audio sample rate (default: {DSC_AUDIO_SAMPLE_RATE})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    decoder = DSCDecoder(sample_rate=args.sample_rate)

    logger.info(f"DSC decoder started (sample rate: {args.sample_rate})")

    for audio_chunk in read_audio_stdin():
        for message in decoder.process_audio(audio_chunk):
            try:
                print(json.dumps(message), flush=True)
            except Exception as e:
                logger.error(f"Output error: {e}")

    logger.info("DSC decoder stopped")


if __name__ == "__main__":
    main()
