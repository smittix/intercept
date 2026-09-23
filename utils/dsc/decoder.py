#!/usr/bin/env python3
"""
DSC (Digital Selective Calling) decoder.

Decodes VHF DSC signals per ITU-R M.493. Reads 48kHz 16-bit signed
audio from stdin (from rtl_fm) and outputs JSON messages to stdout.

DSC uses 1200 bps FSK on a 1700 Hz subcarrier with:
- Mark (1): 2100 Hz
- Space (0): 1300 Hz

Frame structure:
1. Dot pattern: 200 bits alternating 1/0 for synchronization
2. Phasing sequence: 7 symbols (RX or DX pattern)
3. Format specifier: Identifies message type
4. Address/Self-ID fields
5. Category/Nature fields (if distress)
6. Position data (if present)
7. Telecommand fields
8. EOS (End of Sequence)

Each symbol is 10 bits (7 data + 3 error detection).
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
    DSC_AUDIO_SAMPLE_RATE,
    DSC_BAUD_RATE,
    DSC_MARK_FREQ,
    DSC_SPACE_FREQ,
)

# Configure logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
logger = logging.getLogger("dsc.decoder")


class DSCDecoder:
    """
    DSC FSK decoder.

    Demodulates 1200 bps FSK audio and decodes DSC protocol.
    """

    def __init__(self, sample_rate: int = DSC_AUDIO_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.baud_rate = DSC_BAUD_RATE
        self.samples_per_bit = sample_rate // self.baud_rate

        # FSK frequencies
        # Per ITU-R M.493 Annex 1 Sec 1.4: higher frequency (2100 Hz) is
        # the B-state (binary 0), lower frequency (1300 Hz) is the
        # Y-state (binary 1).
        self.mark_freq = DSC_MARK_FREQ  # 2100 Hz = binary 0 (B state)
        self.space_freq = DSC_SPACE_FREQ  # 1300 Hz = binary 1 (Y state)

        # Bandpass filter for DSC band (1100-2300 Hz)
        nyq = sample_rate / 2
        low = 1100 / nyq
        high = 2300 / nyq
        self.bp_b, self.bp_a = scipy_signal.butter(4, [low, high], btype="band")

        # Build FSK correlators
        self._build_correlators()

        # State
        self.buffer = np.array([], dtype=np.int16)
        self.bit_buffer = []
        self.in_message = False
        self.message_bits = []

    def _build_correlators(self):
        """
        Build quadrature (I/Q) matched filter correlators for mark and
        space frequencies.

        A single-phase sine correlator is phase-sensitive: if the
        incoming tone is ~90 degrees out of phase with the reference,
        the correlation magnitude collapses toward zero even when the
        correct tone is present, since there's no timing recovery
        locking the symbol window to the actual bit transitions. Using
        both a sine (Q) and cosine (I) reference and combining them as
        sqrt(I^2 + Q^2) makes the magnitude phase-independent, which is
        required for a free-running (non-timing-recovered) symbol
        window like this one.
        """
        # Duration for one bit
        t = np.arange(self.samples_per_bit) / self.sample_rate

        # Mark (2100 Hz) quadrature references
        self.mark_ref_i = np.cos(2 * np.pi * self.mark_freq * t)
        self.mark_ref_q = np.sin(2 * np.pi * self.mark_freq * t)

        # Space (1300 Hz) quadrature references
        self.space_ref_i = np.cos(2 * np.pi * self.space_freq * t)
        self.space_ref_q = np.sin(2 * np.pi * self.space_freq * t)

    def process_audio(self, audio_data: bytes) -> Generator[dict, None, None]:
        """
        Process audio data and yield decoded DSC messages.

        Uses a stateful bandpass filter (carrying scipy's lfilter `zi`
        state across calls) instead of re-filtering an overlapping
        buffer each call. The previous approach re-filtered a retained
        tail of samples on every call and re-yielded the resulting
        bits, duplicating already-processed bits into the bit stream
        on every call after the first - this corrupted sync/symbol
        alignment starting right after the first chunk, and compounded
        with every subsequent call. It also introduced small filter
        transients at each chunk boundary from resetting filter state
        to zero every time. Carrying filter state eliminates both:
        confirmed to produce a bit-for-bit identical stream to a
        single continuous one-shot filter pass on real captured audio.

        Any leftover samples that don't complete a full bit period are
        buffered and prepended to the next call's audio, so this works
        correctly regardless of the caller's chunk size.

        Args:
            audio_data: Raw 16-bit signed PCM audio bytes

        Yields:
            Decoded DSC message dicts
        """
        samples = np.frombuffer(audio_data, dtype=np.int16)
        if len(samples) == 0:
            return

        if not hasattr(self, "_filter_zi"):
            self._filter_zi = scipy_signal.lfilter_zi(self.bp_b, self.bp_a) * samples[0]
        if not hasattr(self, "_leftover_samples"):
            self._leftover_samples = np.array([], dtype=np.int16)

        samples = np.concatenate([self._leftover_samples, samples])

        usable_len = (len(samples) // self.samples_per_bit) * self.samples_per_bit
        if usable_len == 0:
            self._leftover_samples = samples
            return

        to_process = samples[:usable_len]
        self._leftover_samples = samples[usable_len:]

        try:
            filtered, self._filter_zi = scipy_signal.lfilter(
                self.bp_b, self.bp_a, to_process.astype(np.float64), zi=self._filter_zi
            )
        except Exception as e:
            logger.warning(f"Filter error: {e}")
            return

        bits = self._demodulate_fsk(filtered)

        for bit in bits:
            message = self._process_bit(bit)
            if message:
                yield message

    def _demodulate_fsk(self, samples: np.ndarray) -> list[int]:
        """
        Demodulate FSK audio to bits using phase-independent quadrature
        correlation.

        Args:
            samples: Filtered audio samples

        Returns:
            List of decoded bits (0 or 1)
        """
        bits = []
        num_bits = len(samples) // self.samples_per_bit

        for i in range(num_bits):
            start = i * self.samples_per_bit
            end = start + self.samples_per_bit
            segment = samples[start:end]

            if len(segment) < self.samples_per_bit:
                break

            # Quadrature (I/Q) magnitude - phase independent, unlike a
            # single-phase correlation against just a sine reference.
            mark_i = np.dot(segment, self.mark_ref_i)
            mark_q = np.dot(segment, self.mark_ref_q)
            mark_mag = np.sqrt(mark_i**2 + mark_q**2)

            space_i = np.dot(segment, self.space_ref_i)
            space_q = np.dot(segment, self.space_ref_q)
            space_mag = np.sqrt(space_i**2 + space_q**2)

            # Per ITU-R M.493: mark (2100 Hz) = binary 0 (B state),
            # space (1300 Hz) = binary 1 (Y state)
            if mark_mag > space_mag:
                bits.append(0)
            else:
                bits.append(1)

        return bits

    def _process_bit(self, bit: int) -> dict | None:
        """
        Process a decoded bit and detect/decode DSC messages.

        Uses a two-stage sync: coarse dot-pattern detection (20+
        alternating bits, per VHF spec) followed by a fine-sync step
        that tests bit offsets 0-9 against the check-bit test to find
        the transmitter's actual 10-bit symbol grid. The coarse
        trigger point is not reliably grid-aligned on its own - the
        grid position is arbitrary relative to wherever our sample
        buffer happens to start, and being off by even a few bits
        breaks every subsequent check-bit test even on a clean signal.

        Args:
            bit: Decoded bit (0 or 1)

        Returns:
            Decoded message dict if complete message found, None otherwise
        """
        self.bit_buffer.append(bit)
        if len(self.bit_buffer) > 2000:
            self.bit_buffer = self.bit_buffer[-1500:]

        if not hasattr(self, "sync_pending"):
            self.sync_pending = False
            self.sync_lookahead = []

        if self.sync_pending:
            self.sync_lookahead.append(bit)
            if len(self.sync_lookahead) >= 40:
                best_offset, best_valid = 0, -1
                for offset in range(10):
                    valid = 0
                    for k in range(3):
                        s = offset + k * 10
                        if s + 10 <= len(self.sync_lookahead):
                            symbol_bits = self.sync_lookahead[s:s + 10]
                            if self._bits_to_symbol(symbol_bits) != -1:
                                valid += 1
                    if valid > best_valid:
                        best_valid, best_offset = valid, offset
                if best_valid >= 2:
                    self.in_message = True
                    self.message_bits = self.sync_lookahead[best_offset:]
                    logger.debug(
                        f"DSC fine sync: offset={best_offset} valid={best_valid}/3"
                    )
                else:
                    logger.debug("DSC fine sync failed, discarding false trigger")
                self.sync_pending = False
                self.sync_lookahead = []
            return None

        if not self.in_message and self._detect_dot_pattern():
            self.sync_pending = True
            self.sync_lookahead = []
            logger.debug("DSC sync detected, refining bit alignment")
            return None

        # Collect message bits
        if self.in_message:
            self.message_bits.append(bit)
            # Check for end of message or timeout
            if len(self.message_bits) >= 10 and len(self.message_bits) % 10 == 0:  # One new complete symbol
                # Try to decode accumulated symbols
                message = self._try_decode_message()
                if message:
                    self.in_message = False
                    self.message_bits = []
                    return message
            # Timeout - too many bits without valid message
            if len(self.message_bits) > 1800:  # ~180 symbols max
                diagnosis = self._diagnose_decode_failure()
                logger.debug(f"DSC message timeout: {diagnosis}")
                self.in_message = False
                self.message_bits = []
        return None

    def _detect_dot_pattern(self) -> bool:
        """
        Detect DSC dot pattern for synchronization.

        Per ITU-R M.493 Annex 1 Sec 3.4, there are two dot pattern
        lengths: 200 bits (HF/MF acknowledgements and coast-station
        calls) and 20 bits (VHF - all calls, per Sec 3.4.2, confirmed
        by ETSI EN 300 338-3: "the equipment shall automatically set
        the dot pattern length to 20 bits for all transmitted DSC
        messages" on VHF). This decoder targets VHF ch. 70, so it must
        use the 20-bit pattern - the previous 100-alternation/200-bit
        requirement was the HF/MF number and made VHF sync structurally
        unreachable, since real VHF traffic only ever produces roughly
        20-27 alternating bits.

        We check a slightly wider window (24 bits) than the strict
        20-bit minimum to tolerate a possible bit error right at the
        start of the pattern.
        """
        if len(self.bit_buffer) < 24:
            return False
        last_bits = self.bit_buffer[-24:]
        alternations = 0
        for i in range(1, len(last_bits)):
            if last_bits[i] != last_bits[i - 1]:
                alternations += 1
            else:
                alternations = 0
            if alternations >= 20:
                return True
        return False

    def _try_decode_message(self) -> dict | None:
        """
        Decode accumulated message bits into a DSC message.

        Per ITU-R M.493 Annex 1 Sec 1.2: apart from phasing, every
        character in a DSC call is transmitted TWICE (DX then RX, ~5
        symbol-positions apart) - this is the actual wire format, not
        an optional extra. The format specifier is additionally doubled
        at the FIELD level (Sec 4.1: "2 identical characters"), giving
        4 raw occurrences for it specifically and providing a reliable
        anchor: the first position where a real format-specifier value
        repeats exactly 2 symbol-positions later. From that anchor,
        every other symbol (stride 2) is the de-interleaved, real
        message content - confirmed empirically against real captures
        and against Annex 1 Figure 1 (call sequence construction).

        Returns:
            Decoded message dict, or None if not yet decodable (anchor
            not found, fields incomplete, or EOS not found yet).
        """
        FORMAT_SPECIFIERS = {102, 112, 114, 116, 120, 123}
        FORMAT_TEXT = {
            102: "GEOGRAPHIC_AREA",
            112: "DISTRESS",
            114: "GROUP",
            116: "ALL_SHIPS",
            120: "INDIVIDUAL",
            123: "INDIVIDUAL_SEMI_AUTO",
        }
        CATEGORY_TEXT = {
            112: "DISTRESS",
            110: "URGENCY",
            108: "SAFETY",
            106: "SHIPS_BUSINESS",
            100: "ROUTINE",
        }
        # Nature of distress (Table 10 / Table A1-3), confirmed against
        # spec text directly, consistent across M.493-8 through -15.
        NATURE_TEXT = {
            100: "FIRE_EXPLOSION",
            101: "FLOODING",
            102: "COLLISION",
            103: "GROUNDING",
            104: "LISTING_CAPSIZE_DANGER",
            105: "SINKING",
            106: "DISABLED_ADRIFT",
            107: "UNDESIGNATED",
            108: "ABANDONING_SHIP",
            109: "PIRACY_ARMED_ROBBERY",
            110: "MAN_OVERBOARD",
        }
        # Telecommand values (Table 11/12), confirmed directly against
        # spec text. This is not the full table - only values we've
        # actually cited are included; anything else reports as
        # UNKNOWN-<code> rather than a guess. 126 ("no information") is
        # by far the most common value for the second telecommand slot,
        # since most calls don't use it for anything.
        TELECOMMAND_TEXT = {
            100: "F3E/G3E ALL MODES TP",
            101: "F3E/G3E DUPLEX TP",
            118: "TEST",
            121: "POSITION_OR_LOCATION_UPDATING",
            126: "NO_INFORMATION",
        }
        # EOS (end of sequence) meaning, per ITU-R M.493 Sec 9, confirmed
        # directly against spec text.
        EOS_TEXT = {
            117: "ACKNOWLEDGEMENT_REQUESTED",
            122: "ACKNOWLEDGEMENT_REPLY",
            127: "NO_ACKNOWLEDGEMENT_REQUIRED",
        }
        VALID_EOS = {117, 122, 127}
        # Field layouts per format specifier: (field_name, symbol_count),
        # in transmission order after the format specifier. Per Table 5
        # (selective calls) and Table 4 (distress). Distress has no
        # address/category field (priority is the format specifier
        # itself).
        FIELD_LAYOUTS = {
            120: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            123: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3)],
            116: [("category", 1), ("self_id", 5), ("telecommand", 2),
                  ("frequency_1", 3)],
            114: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            102: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            112: [("self_id", 5), ("nature", 1), ("coordinates", 5),
                  ("time", 2), ("telecommand_msg4", 1)],
        }
        # Format 116 is overloaded: normally "All Ships" broadcast (the
        # FIELD_LAYOUTS[116] entry above), but per ITU-R M.493-16 it is
        # ALSO used for distress alert acknowledgement / distress
        # self-cancel (Table A1-4.2) - a structurally different message.
        # Per spec: "Distress acknowledgments where the transmitting ID
        # and ship in distress ID are the same...should be interpreted
        # as a self-cancel operation." Distinguished by category: a
        # normal All Ships broadcast uses ROUTINE/SAFETY/URGENCY: a
        # distress acknowledgement/self-cancel uses category DISTRESS
        # (112). Layout below empirically mapped and validated against a
        # real captured self-cancel message (own MMSI confirmed present,
        # nature/coordinates/time matching the alert being cancelled).
        # field_8_unconfirmed's exact semantic meaning is not yet
        # confirmed against spec text - flagged honestly rather than
        # guessed.
        DISTRESS_ACK_LAYOUT_116 = [
            ("ship_in_distress_id", 5),
            ("field_8_unconfirmed", 1),
            ("ship_in_distress_id_repeat", 5),
            ("nature", 1),
            ("coordinates", 5),
            ("time", 2),
        ]

        num_symbols = len(self.message_bits) // 10
        if num_symbols < 5:
            return None

        symbols = []
        for i in range(num_symbols):
            symbol_bits = self.message_bits[i * 10:i * 10 + 10]
            symbol_value = self._bits_to_symbol(symbol_bits)
            symbols.append(symbol_value)

        # Find the message anchor: first position where a real format
        # specifier value repeats exactly 2 positions later.
        anchor = None
        for i in range(len(symbols) - 2):
            if symbols[i] in FORMAT_SPECIFIERS and symbols[i] == symbols[i + 2]:
                anchor = i
                break
        if anchor is None:
            return None

        # Track A is the primary de-interleaved sequence used for field
        # boundaries. Track B (offset by one raw symbol position) is the
        # redundant time-diversity copy: per ITU-R M.493 Annex 1 Sec 1.2,
        # every character apart from phasing is transmitted twice (DX
        # then RX). Confirmed empirically: Track A's logical position k
        # pairs with Track B's position k+2 (Track B starts 2 positions
        # "behind" since it still has 2 trailing phasing remnants before
        # it catches up to real content) - validated with zero mismatches
        # across a full real capture, and confirmed to correctly recover
        # a synthetically-corrupted symbol in testing. When Track A's
        # copy of a character fails the check-bit test, fall back to
        # Track B's copy before giving up on that character - this is
        # the actual error-correction mechanism ITU-R M.493 relies on.
        dedup = symbols[anchor::2]
        dedup_b = symbols[anchor + 1::2]
        if len(dedup) < 2:
            return None

        def value_at(k):
            """Track A position k, falling back to Track B's diversity
            copy (position k+2) if Track A's copy failed check bits.
            This is the diversity-combining fallback that recovers
            isolated real-world bit errors using ITU-R M.493's built-in
            time-diversity redundancy."""
            primary = dedup[k] if k < len(dedup) else -1
            if primary != -1:
                return primary
            fallback_idx = k + 2
            if 0 <= fallback_idx < len(dedup_b):
                return dedup_b[fallback_idx]
            return -1

        format_code = value_at(0)
        layout = FIELD_LAYOUTS.get(format_code)
        if layout is None:
            logger.debug(f"DSC unsupported/unrecognized format specifier: {format_code}")
            return None

        # Format 116 needs a peek at its category field to know which of
        # the two structurally different layouts applies (see
        # DISTRESS_ACK_LAYOUT_116 definition above for why).
        is_distress_ack = False
        if format_code == 116:
            peeked_category = value_at(2)
            if peeked_category == 112:  # DISTRESS
                layout = DISTRESS_ACK_LAYOUT_116
                is_distress_ack = True

        idx = 2  # skip the two format-specifier copies
        fields = {}
        if is_distress_ack:
            fields["category"] = [112]
            idx += 1  # already consumed by the peek above
        for name, count in layout:
            if idx + count > len(dedup):
                return None  # not enough data yet, keep accumulating
            chunk = [value_at(idx + j) for j in range(count)]
            fields[name] = chunk
            idx += count

        eos = None
        for k in range(idx, len(dedup)):
            v = value_at(k)
            if v in VALID_EOS:
                eos = v
                break
        if eos is None:
            return None  # message not complete yet

        message = {
            "type": "dsc",
            "format": format_code,
            "format_text": FORMAT_TEXT.get(format_code, f"UNKNOWN-{format_code}"),
            "eos": eos,
            "eos_text": EOS_TEXT.get(eos, f"UNKNOWN-{eos}"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if is_distress_ack:
            message["format_text"] = "DISTRESS_ACK_OR_SELF_CANCEL"
        if "address" in fields:
            message["dest_mmsi"] = self._decode_mmsi(fields["address"])
        if "self_id" in fields:
            message["source_mmsi"] = self._decode_mmsi(fields["self_id"])
        if "ship_in_distress_id" in fields:
            ship_mmsi = self._decode_mmsi(fields["ship_in_distress_id"])
            message["ship_in_distress_mmsi"] = ship_mmsi
            if "source_mmsi" not in message:
                # for a self-cancel, transmitting station IS the ship in
                # distress - report it as source too for consistency with
                # other message types
                message["source_mmsi"] = ship_mmsi
        if "category" in fields:
            cat_val = fields["category"][0]
            message["category"] = CATEGORY_TEXT.get(cat_val, f"UNKNOWN-{cat_val}")
        if format_code == 112 or is_distress_ack:
            message["category"] = "DISTRESS"
            if "nature" in fields:
                nature_val = fields["nature"][0]
                message["nature"] = nature_val
                message["nature_text"] = NATURE_TEXT.get(nature_val, f"UNKNOWN-{nature_val}")
            if "coordinates" in fields:
                message["coordinates_raw"] = fields["coordinates"]
                message["position"] = self._decode_position(fields["coordinates"])
            if "time" in fields:
                message["time_raw"] = fields["time"]
        if "telecommand" in fields:
            message["telecommand"] = fields["telecommand"]
            message["telecommand_text"] = [
                TELECOMMAND_TEXT.get(t, f"UNKNOWN-{t}") for t in fields["telecommand"]
            ]

        # Plain-English summary, built from the fields actually present -
        # different message types (individual/group/area, all ships,
        # distress alert, distress ack/self-cancel) have different fields,
        # so this is assembled per-type rather than one fixed template.
        eos_phrase = {
            117: "requesting acknowledgement",
            122: "acknowledging a previous call",
            127: "no acknowledgement required",
        }.get(eos, "unknown EOS")
        tc_phrase = message.get("telecommand_text", [None])[0]
        tc_phrase = f", purpose: {tc_phrase}" if tc_phrase and tc_phrase != "NO_INFORMATION" else ""

        if is_distress_ack:
            message["summary"] = (
                f"Distress self-cancel/acknowledgement from {message.get('source_mmsi')} "
                f"for vessel {message.get('ship_in_distress_mmsi')} "
                f"(original distress nature: {message.get('nature_text', 'unknown')})"
            )
        elif format_code == 112:
            message["summary"] = (
                f"DISTRESS ALERT from {message.get('source_mmsi')} - "
                f"nature: {message.get('nature_text', 'unknown')}"
            )
        elif "dest_mmsi" in message:
            message["summary"] = (
                f"{message.get('category', 'UNKNOWN')} individual/group call from "
                f"{message.get('source_mmsi')} to {message.get('dest_mmsi')}, "
                f"{eos_phrase}{tc_phrase}"
            )
        else:
            message["summary"] = (
                f"{message.get('category', 'UNKNOWN')} broadcast from "
                f"{message.get('source_mmsi')} to all ships, {eos_phrase}{tc_phrase}"
            )

        return message

    def _diagnose_decode_failure(self) -> str:
        """
        Explain exactly why the current message_bits buffer failed to
        decode, for logging at timeout. Mirrors _try_decode_message's
        logic but reports where it stopped instead of returning None
        silently - normal accumulation still returns None quietly (this
        is only called once, at the moment of timeout, not on every
        partial-accumulation call).
        """
        FORMAT_SPECIFIERS = {102, 112, 114, 116, 120, 123}
        FIELD_LAYOUTS = {
            120: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            123: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3)],
            116: [("category", 1), ("self_id", 5), ("telecommand", 2),
                  ("frequency_1", 3)],
            114: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            102: [("address", 5), ("category", 1), ("self_id", 5),
                  ("telecommand", 2), ("frequency_1", 3), ("frequency_2", 3)],
            112: [("self_id", 5), ("nature", 1), ("coordinates", 5),
                  ("time", 2), ("telecommand_msg4", 1)],
        }
        VALID_EOS = {117, 122, 127}

        num_symbols = len(self.message_bits) // 10
        symbols = [self._bits_to_symbol(self.message_bits[i * 10:i * 10 + 10]) for i in range(num_symbols)]
        invalid_count = sum(1 for s in symbols if s == -1)

        anchor = None
        for i in range(len(symbols) - 2):
            if symbols[i] in FORMAT_SPECIFIERS and symbols[i] == symbols[i + 2]:
                anchor = i
                break
        if anchor is None:
            return (f"no anchor found ({num_symbols} symbols, {invalid_count} invalid, "
                    f"first 15 symbols={symbols[:15]})")

        dedup = symbols[anchor::2]
        dedup_b = symbols[anchor + 1::2]

        def value_at(k):
            primary = dedup[k] if k < len(dedup) else -1
            if primary != -1:
                return primary
            fb = k + 2
            return dedup_b[fb] if 0 <= fb < len(dedup_b) else -1

        format_code = value_at(0)
        layout = FIELD_LAYOUTS.get(format_code)
        if layout is None:
            return f"anchor found at {anchor}, but format specifier {format_code} not recognized"

        idx = 2
        for name, count in layout:
            if idx + count > len(dedup):
                have = len(dedup) - idx
                return (f"format={format_code}, stalled waiting for field "
                        f"'{name}' (need {count} symbols, have {max(have, 0)})")
            idx += count

        for k in range(idx, len(dedup)):
            if value_at(k) in VALID_EOS:
                return "all fields complete, EOS found - should have decoded (unexpected)"

        return (f"format={format_code}, all fields complete, but no valid EOS "
                f"found in remaining {len(dedup) - idx} symbols")

    def _decode_mmsi(self, symbols: list[int]) -> str | None:
        """
        Decode 5 symbols into a 9-digit MMSI string.

        Per ITU-R M.493-16: identities are transmitted as five
        characters C5C4C3C2C1, comprising ten digits X1...X10, "whereas
        digit X10 is always the digit 0" (unless M.1080 extended
        addressing is in use, not handled here). X10 is the LAST digit
        of the sequence, not the first - so the real 9-digit MMSI is
        digits 1-9, dropping the trailing padding digit. Confirmed
        against real over-the-air captures: this produces MMSIs with
        recognizable, valid Maritime Identification Digits (e.g. "338"
        = USA) consistently across independent real recordings.

        Args:
            symbols: 5 raw symbol values (0-99 each)

        Returns:
            9-digit MMSI string, or None if malformed input.
        """
        if len(symbols) != 5 or any(s < 0 or s > 99 for s in symbols):
            return None
        digits = "".join(f"{s:02d}" for s in symbols)
        return digits[:9]

    def _decode_position(self, symbols: list[int]) -> dict | None:
        """
        Decode 5 coordinate symbols into a position dict, per ITU-R
        M.493 Annex 1 Sec 8.1.2/8.2.3 (Table A1-2), confirmed
        consistent across M.493-8 through -15. 10 digits total:
        digit 1 = quadrant (0=NE, 1=NW, 2=SE, 3=SW), digits 2-5 =
        latitude (2 degrees + 2 minutes), digits 6-10 = longitude
        (3 degrees + 2 minutes). All-9s (9999999999) is the spec-
        defined "position not available" sentinel.

        Physically-impossible values (degrees/minutes outside valid
        range - e.g. from a residual bit error the diversity-combining
        fallback in _process_bit didn't catch) are NOT silently
        discarded or clamped: the computed position is still returned
        (a partial position can still be useful - e.g. for search and
        rescue, where even an approximate position beats none at all),
        but flagged via "position_uncertain"/"position_issue" so
        downstream consumers can decide how much to trust it rather
        than being silently given a wrong-but-plausible-looking value.

        Returns a dict with "lat"/"lon" as signed decimal degrees
        (matching the pre-existing message["position"] shape this
        decoder's downstream consumers - parser.py/routes/dsc.py -
        expect) plus human-readable degree/minute text fields and the
        uncertainty flag described above. Returns None only if the
        position is unavailable (all-9s sentinel) or the input itself
        is malformed (wrong length/out-of-range symbols).

        Args:
            symbols: 5 raw symbol values (0-99 each)
        """
        if len(symbols) != 5 or any(s < 0 or s > 99 for s in symbols):
            return None
        digits = "".join(f"{s:02d}" for s in symbols)
        if digits == "9999999999":
            return None
        quadrant = int(digits[0])
        lat_deg = int(digits[1:3])
        lat_min = int(digits[3:5])
        lon_deg = int(digits[5:8])
        lon_min = int(digits[8:10])

        issues = []
        if lat_deg > 90:
            issues.append(f"latitude degrees ({lat_deg}) exceeds valid range (0-90)")
        if lon_deg > 180:
            issues.append(f"longitude degrees ({lon_deg}) exceeds valid range (0-180)")
        if lat_min > 59:
            issues.append(f"latitude minutes ({lat_min}) exceeds valid range (0-59)")
        if lon_min > 59:
            issues.append(f"longitude minutes ({lon_min}) exceeds valid range (0-59)")

        lat_sign = 1 if quadrant in (0, 1) else -1  # NE, NW -> North
        lon_sign = 1 if quadrant in (0, 2) else -1  # NE, SE -> East
        lat = lat_sign * (lat_deg + lat_min / 60.0)
        lon = lon_sign * (lon_deg + lon_min / 60.0)
        return {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "lat_text": f"{lat_deg:02d}°{lat_min:02d}'{'N' if lat_sign > 0 else 'S'}",
            "lon_text": f"{lon_deg:03d}°{lon_min:02d}'{'E' if lon_sign > 0 else 'W'}",
            "position_uncertain": bool(issues),
            "position_issue": "; ".join(issues) if issues else None,
        }

    def _bits_to_symbol(self, bits: list[int]) -> int:
        """
        Convert 10 bits to symbol value.

        Per ITU-R M.493 Annex 1 Sec 2 / Table 1: each DSC character is
        a 10-bit code - 7 information bits (bits 1-7, transmitted LSB
        first) plus 3 check bits (bits 8-10, transmitted MSB first).
        The check bits encode, as a 3-bit binary number, the count of
        "B" elements (binary 0) among the 7 information bits - this is
        NOT simple parity. E.g. a BYY check-bit sequence (0,1,1) means
        the symbol's info bits contain exactly 3 B-elements (spec's own
        worked example).

        Returns -1 if the check bits don't match the actual B-element
        count in the info bits (character failed the ten-unit
        error-detecting code).
        """
        if len(bits) != 10:
            return -1

        info_bits = bits[:7]
        check_bits = bits[7:10]

        # Information bits: LSB first
        value = 0
        for i in range(7):
            if info_bits[i]:
                value |= 1 << i

        # Check bits: MSB first, encode count of B (0-value) info bits
        check_value = (check_bits[0] << 2) | (check_bits[1] << 1) | check_bits[2]
        num_b_elements = 7 - sum(info_bits)

        if check_value != num_b_elements:
            return -1

        return value

def read_audio_stdin() -> Generator[bytes, None, None]:
    """
    Read audio from stdin in chunks.

    Yields:
        Audio data chunks
    """
    chunk_size = 4800  # 0.1 seconds at 48kHz, 16-bit = 9600 bytes
    while True:
        try:
            data = sys.stdin.buffer.read(chunk_size * 2)  # 2 bytes per sample
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
        description="DSC (Digital Selective Calling) decoder", epilog="Reads 48kHz 16-bit signed PCM audio from stdin"
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
            # Output JSON to stdout
            try:
                print(json.dumps(message), flush=True)
            except Exception as e:
                logger.error(f"Output error: {e}")

    logger.info("DSC decoder stopped")


if __name__ == "__main__":
    main()
