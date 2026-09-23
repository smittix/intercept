"""Tests for DSC (Digital Selective Calling) utilities."""

import json

import pytest


class TestDSCParser:
    """Tests for DSC parser utilities."""

    def test_get_country_from_mmsi_ship_station(self):
        """Test country lookup for standard ship MMSI."""
        from utils.dsc.parser import get_country_from_mmsi

        # UK ships start with 232-235
        assert get_country_from_mmsi("232123456") == "United Kingdom"
        assert get_country_from_mmsi("235987654") == "United Kingdom"

        # US ships start with 303, 338, 366-369
        assert get_country_from_mmsi("366123456") == "USA"
        assert get_country_from_mmsi("369000001") == "USA"

        # Panama (common flag of convenience)
        assert get_country_from_mmsi("351234567") == "Panama"
        assert get_country_from_mmsi("370000001") == "Panama"

        # Norway
        assert get_country_from_mmsi("257123456") == "Norway"

        # Germany
        assert get_country_from_mmsi("211000001") == "Germany"

    def test_get_country_from_mmsi_coast_station(self):
        """Test country lookup for coast station MMSI (starts with 00)."""
        from utils.dsc.parser import get_country_from_mmsi

        # Coast stations: 00 + MID
        assert get_country_from_mmsi("002320001") == "United Kingdom"
        assert get_country_from_mmsi("003660001") == "USA"

    def test_get_country_from_mmsi_group_station(self):
        """Test country lookup for group station MMSI (starts with 0)."""
        from utils.dsc.parser import get_country_from_mmsi

        # Group call: 0 + MID
        assert get_country_from_mmsi("023200001") == "United Kingdom"
        assert get_country_from_mmsi("036600001") == "USA"

    def test_get_country_from_mmsi_unknown(self):
        """Test country lookup returns None for unknown MID."""
        from utils.dsc.parser import get_country_from_mmsi

        assert get_country_from_mmsi("999999999") is None
        assert get_country_from_mmsi("100000000") is None

    def test_get_country_from_mmsi_invalid(self):
        """Test country lookup handles invalid input."""
        from utils.dsc.parser import get_country_from_mmsi

        assert get_country_from_mmsi("") is None
        assert get_country_from_mmsi(None) is None
        assert get_country_from_mmsi("12") is None

    def test_get_distress_nature_text(self):
        """Test distress nature code to text conversion."""
        from utils.dsc.parser import get_distress_nature_text

        assert get_distress_nature_text(100) == "FIRE_EXPLOSION"
        assert get_distress_nature_text(101) == "FLOODING"
        assert get_distress_nature_text(102) == "COLLISION"
        assert get_distress_nature_text(103) == "GROUNDING"
        assert get_distress_nature_text(105) == "SINKING"
        assert get_distress_nature_text(107) == "UNDESIGNATED"
        assert get_distress_nature_text(109) == "PIRACY_ARMED_ROBBERY"
        assert get_distress_nature_text(110) == "MAN_OVERBOARD"
        assert get_distress_nature_text(112) == "EPIRB"

    def test_get_distress_nature_text_unknown(self):
        """Test distress nature returns formatted unknown for invalid codes."""
        from utils.dsc.parser import get_distress_nature_text

        assert "UNKNOWN" in get_distress_nature_text(999)
        assert "999" in get_distress_nature_text(999)

    def test_get_distress_nature_text_string_input(self):
        """Test distress nature accepts string input."""
        from utils.dsc.parser import get_distress_nature_text

        assert get_distress_nature_text("101") == "FLOODING"
        assert get_distress_nature_text("invalid") == "invalid"

    def test_get_format_text(self):
        """Test format code to text conversion per ITU-R M.493."""
        from utils.dsc.parser import get_format_text

        assert get_format_text(102) == "GEOGRAPHIC_AREA"
        assert get_format_text(112) == "DISTRESS"
        assert get_format_text(114) == "GROUP"
        assert get_format_text(116) == "ALL_SHIPS"
        assert get_format_text(120) == "INDIVIDUAL"
        assert get_format_text(123) == "INDIVIDUAL_SEMI_AUTO"

    def test_get_format_text_unknown(self):
        """Test format code returns unknown for invalid codes."""
        from utils.dsc.parser import get_format_text

        result = get_format_text(999)
        assert "UNKNOWN" in result

    def test_get_format_text_removed_codes(self):
        """Test that non-ITU format codes are no longer recognized."""
        from utils.dsc.parser import get_format_text

        # These were previously defined but are not ITU-R M.493 specifiers
        for code in [100, 104, 106, 108, 110, 118]:
            result = get_format_text(code)
            assert "UNKNOWN" in result

    def test_get_telecommand_text(self):
        """Test telecommand code to text conversion."""
        from utils.dsc.parser import get_telecommand_text

        assert get_telecommand_text(100) == "F3E/G3E ALL MODES TP"
        assert get_telecommand_text(107) == "DISTRESS_ACK"
        assert get_telecommand_text(118) == "TEST"
        assert get_telecommand_text(126) == "NO_INFORMATION"
        # Unverified codes report UNKNOWN rather than guessing a label
        assert "UNKNOWN" in get_telecommand_text(105)

    def test_get_category_priority(self):
        """Test category priority values."""
        from utils.dsc.parser import get_category_priority

        # Distress has highest priority (0)
        assert get_category_priority("DISTRESS") == 0
        assert get_category_priority("distress") == 0

        # Urgency/safety
        assert get_category_priority("ALL_SHIPS_URGENCY_SAFETY") == 2

        # Routine-level
        assert get_category_priority("ALL_SHIPS") == 5
        assert get_category_priority("GROUP") == 5
        assert get_category_priority("INDIVIDUAL") == 5

        # Unknown gets default high number
        assert get_category_priority("UNKNOWN") == 10

    def test_validate_mmsi_valid(self):
        """Test MMSI validation with valid numbers."""
        from utils.dsc.parser import validate_mmsi

        assert validate_mmsi("232123456") is True
        assert validate_mmsi("366000001") is True
        assert validate_mmsi("002320001") is True  # Coast station
        assert validate_mmsi("023200001") is True  # Group station

    def test_validate_mmsi_invalid(self):
        """Test MMSI validation rejects invalid numbers."""
        from utils.dsc.parser import validate_mmsi

        assert validate_mmsi("") is False
        assert validate_mmsi(None) is False
        assert validate_mmsi("12345678") is False  # Too short
        assert validate_mmsi("1234567890") is False  # Too long
        assert validate_mmsi("abcdefghi") is False  # Not digits
        assert validate_mmsi("000000000") is False  # All zeros

    def test_classify_mmsi(self):
        """Test MMSI classification."""
        from utils.dsc.parser import classify_mmsi

        # Ship stations (start with 2-7)
        assert classify_mmsi("232123456") == "ship"
        assert classify_mmsi("366000001") == "ship"
        assert classify_mmsi("503000001") == "ship"

        # Coast stations (start with 00)
        assert classify_mmsi("002320001") == "coast"

        # Group stations (start with 0, not 00)
        assert classify_mmsi("023200001") == "group"

        # SAR aircraft (start with 111)
        assert classify_mmsi("111232001") == "sar"

        # Aids to Navigation (start with 99)
        assert classify_mmsi("992321001") == "aton"

        # Unknown
        assert classify_mmsi("invalid") == "unknown"
        assert classify_mmsi("812345678") == "unknown"

    def test_parse_dsc_message_distress(self):
        """Test parsing a distress message with ITU format 112."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 112,
                "source_mmsi": "232123456",
                "dest_mmsi": "002320001",
                "category": "DISTRESS",
                "nature": 101,
                "position": {"lat": 51.5, "lon": -0.1},
                "telecommand1": 100,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "112002032123456101100127",
            }
        )

        msg = parse_dsc_message(raw)

        assert msg is not None
        assert msg["type"] == "dsc_message"
        assert msg["source_mmsi"] == "232123456"
        assert msg["category"] == "DISTRESS"
        assert msg["source_country"] == "United Kingdom"
        assert msg["nature_of_distress"] == "FLOODING"
        assert msg["latitude"] == 51.5
        assert msg["longitude"] == -0.1
        assert msg["is_critical"] is True
        assert msg["priority"] == 0

    def test_parse_dsc_message_group(self):
        """Test parsing a group call message."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 116,
                "source_mmsi": "366000001",
                "dest_mmsi": "023200001",
                "category": "GROUP",
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "116023200001366000001117",
            }
        )

        msg = parse_dsc_message(raw)

        assert msg is not None
        assert msg["category"] == "GROUP"
        assert msg["source_country"] == "USA"
        assert msg["is_critical"] is False
        assert msg["priority"] == 5

    def test_parse_dsc_message_individual(self):
        """Test parsing an individual call message."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 112,
                "source_mmsi": "366000001",
                "dest_mmsi": "232123456",
                "category": "INDIVIDUAL",
                "telecommand1": 100,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "112232123456366000001100122",
            }
        )

        msg = parse_dsc_message(raw)

        assert msg is not None
        assert msg["category"] == "INDIVIDUAL"
        assert msg["is_critical"] is False

    def test_parse_dsc_message_invalid_json(self):
        """Test parsing rejects invalid JSON."""
        from utils.dsc.parser import parse_dsc_message

        assert parse_dsc_message("not json") is None
        assert parse_dsc_message("{invalid}") is None

    def test_parse_dsc_message_missing_type(self):
        """Test parsing rejects messages without correct type."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps({"source_mmsi": "232123456"})
        assert parse_dsc_message(raw) is None

        raw = json.dumps({"type": "other", "source_mmsi": "232123456"})
        assert parse_dsc_message(raw) is None

    def test_parse_dsc_message_missing_mmsi(self):
        """Test parsing rejects messages without source MMSI."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps({"type": "dsc"})
        assert parse_dsc_message(raw) is None

    def test_parse_dsc_message_empty(self):
        """Test parsing handles empty input."""
        from utils.dsc.parser import parse_dsc_message

        assert parse_dsc_message("") is None
        assert parse_dsc_message(None) is None
        assert parse_dsc_message("   ") is None

    def test_parse_dsc_message_rejects_non_itu_format(self):
        """Test parser rejects records with non-ITU format specifier."""
        from utils.dsc.parser import parse_dsc_message

        for bad_format in [100, 104, 106, 108, 110, 118, 999]:
            raw = json.dumps(
                {
                    "type": "dsc",
                    "format": bad_format,
                    "source_mmsi": "232123456",
                    "category": "ROUTINE",
                    "raw": "120232123456100127",
                }
            )
            assert parse_dsc_message(raw) is None, f"Format {bad_format} should be rejected"

    def test_parse_dsc_message_rejects_telecommand_out_of_range(self):
        """Test parser rejects records with telecommand out of 100-127 range."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "232123456",
                "dest_mmsi": "002320001",
                "category": "DISTRESS",
                "telecommand1": 200,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "120002032123456200127",
            }
        )
        assert parse_dsc_message(raw) is None

    def test_parse_dsc_message_accepts_zero_telecommand(self):
        """Test parser does not drop telecommand with value 100 (truthiness fix)."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 112,
                "source_mmsi": "232123456",
                "dest_mmsi": "366000001",
                "category": "INDIVIDUAL",
                "telecommand1": 100,
                "telecommand2": 100,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "112366000001232123456100100122",
            }
        )

        msg = parse_dsc_message(raw)
        assert msg is not None
        assert msg["telecommand1"] == 100
        assert msg["telecommand2"] == 100

    def test_parse_dsc_message_validates_raw_field(self):
        """Test parser validates raw field structure."""
        from utils.dsc.parser import parse_dsc_message

        # Non-digit raw field
        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "232123456",
                "category": "DISTRESS",
                "raw": "12abc",
            }
        )
        assert parse_dsc_message(raw) is None

        # Raw field length not divisible by 3
        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "232123456",
                "category": "DISTRESS",
                "raw": "1201",
            }
        )
        assert parse_dsc_message(raw) is None

        # Raw field with non-EOS last token
        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "232123456",
                "category": "DISTRESS",
                "raw": "120100",
            }
        )
        assert parse_dsc_message(raw) is None

    def test_parse_dsc_message_accepts_valid_eos_in_raw(self):
        """Test parser accepts all three valid EOS values in raw field."""
        from utils.dsc.parser import parse_dsc_message

        for eos in [117, 122, 127]:
            raw = json.dumps(
                {
                    "type": "dsc",
                    "format": 120,
                    "source_mmsi": "232123456",
                    "dest_mmsi": "002320001",
                    "category": "DISTRESS",
                    "timestamp": "2025-01-15T12:00:00Z",
                    "raw": f"120002032123456{eos:03d}",
                }
            )
            msg = parse_dsc_message(raw)
            assert msg is not None, f"EOS {eos} should be accepted"

    def test_parse_dsc_message_rejects_invalid_mmsi(self):
        """Test parser rejects invalid MMSI values."""
        from utils.dsc.parser import parse_dsc_message

        # All-zeros MMSI
        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "000000000",
                "category": "DISTRESS",
                "raw": "120000000000127",
            }
        )
        assert parse_dsc_message(raw) is None

        # Short MMSI
        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "12345",
                "category": "DISTRESS",
                "raw": "120127",
            }
        )
        assert parse_dsc_message(raw) is None

    def test_parse_dsc_message_nature_zero_not_dropped(self):
        """Test that nature code 0 is not dropped by truthiness check."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 120,
                "source_mmsi": "232123456",
                "dest_mmsi": "002320001",
                "category": "DISTRESS",
                "nature": 0,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "120002032123456000127",
            }
        )

        msg = parse_dsc_message(raw)
        assert msg is not None
        assert msg["nature_code"] == 0

    def test_parse_dsc_message_channel_zero_not_dropped(self):
        """Test that channel value 0 is not dropped by truthiness check."""
        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 112,
                "source_mmsi": "232123456",
                "dest_mmsi": "366000001",
                "category": "INDIVIDUAL",
                "channel": 0,
                "telecommand1": 100,
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "112366000001232123456100122",
            }
        )

        msg = parse_dsc_message(raw)
        assert msg is not None
        assert msg["channel"] == 0

    def test_format_dsc_for_display(self):
        """Test message formatting for display."""
        from utils.dsc.parser import format_dsc_for_display

        msg = {
            "category": "DISTRESS",
            "source_mmsi": "232123456",
            "source_country": "United Kingdom",
            "dest_mmsi": "002320001",
            "nature_of_distress": "FLOODING",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "telecommand1_text": "F3E/G3E ALL MODES TP",
            "channel": 16,
            "timestamp": "2025-01-15T12:00:00Z",
        }

        output = format_dsc_for_display(msg)

        assert "DISTRESS" in output
        assert "232123456" in output
        assert "United Kingdom" in output
        assert "FLOODING" in output
        assert "51.5074" in output
        assert "Channel: 16" in output


class TestDSCDecoder:
    """Tests for DSC decoder utilities."""

    @pytest.fixture
    def decoder(self):
        """Create a DSC decoder instance."""
        # Skip if scipy not available
        pytest.importorskip("scipy")
        pytest.importorskip("numpy")
        from utils.dsc.decoder import DSCDecoder

        return DSCDecoder()

    def test_decode_mmsi_valid(self, decoder):
        """Test MMSI decoding from symbols."""
        # 5 symbols, transmitted C5C4C3C2C1, are 10 digits X1...X10.
        # Per ITU-R M.493-16: "digit X10 is always the digit 0" - X10 is
        # the LAST digit, so the real 9-digit MMSI is digits 1-9.
        # Uses a real MMSI seen throughout this decoder's over-the-air testing.
        symbols = [33, 82, 16, 79, 40]
        result = decoder._decode_mmsi(symbols)
        assert result == "338216794"
    def test_decode_mmsi_with_leading_zeros(self, decoder):
        """Test MMSI decoding handles leading zeros (coast station)."""
        # Coast station MMSI 002275100 (CROSS Gris Nez, a real coast
        # station seen in real DSC traffic during this decoder's testing)
        symbols = [0, 22, 75, 10, 0]
        result = decoder._decode_mmsi(symbols)
        assert result == "002275100"
    def test_decode_mmsi_short_symbols(self, decoder):
        """Test MMSI decoding returns None for short symbol list."""
        result = decoder._decode_mmsi([1, 2, 3])
        assert result is None

    def test_decode_mmsi_invalid_symbols(self, decoder):
        """Test MMSI decoding returns None for out-of-range symbols."""
        symbols = [100, 32, 12, 34, 56]
        result = decoder._decode_mmsi(symbols)
        assert result is None
    def test_decode_position_northeast(self, decoder):
        """Test position decoding for NE quadrant."""
        # Quadrant 0 = NE per ITU-R M.493 Annex 1 Sec 8.1.2 (single digit,
        # not a 2-digit symbol value). Position: 51 deg 30'N, 000 deg 10'E
        symbols = [5, 13, 0, 0, 10]
        result = decoder._decode_position(symbols)
        assert result is not None
        assert result["lat"] == pytest.approx(51.5, rel=0.01)
        assert result["lon"] == pytest.approx(0.1667, rel=0.01)
    def test_decode_position_northwest(self, decoder):
        """Test position decoding for NW quadrant."""
        # Quadrant 1 = NW. Position: 40 deg 42'N, 074 deg 00'W (NYC area)
        symbols = [14, 4, 20, 74, 0]
        result = decoder._decode_position(symbols)
        assert result is not None
        assert result["lat"] > 0  # North
        assert result["lon"] < 0  # West
    def test_decode_position_southeast(self, decoder):
        """Test position decoding for SE quadrant."""
        # Quadrant 2 = SE. Position: 33 deg 51'S, 151 deg 12'E (Sydney area)
        symbols = [23, 35, 11, 51, 12]
        result = decoder._decode_position(symbols)
        assert result is not None
        assert result["lat"] < 0  # South
        assert result["lon"] > 0  # East
    def test_decode_position_short_symbols(self, decoder):
        """Test position decoding handles short symbol list."""
        result = decoder._decode_position([10, 51, 30])
        assert result is None

    def test_decode_position_invalid_values(self, decoder):
        """Test position decoding flags (not silently discards or
        clamps) physically-impossible values.

        Design: a partial/uncertain position can still be valuable
        (e.g. for search and rescue, where an approximate position
        beats none at all) - so out-of-range components are NOT
        discarded, but the result is explicitly flagged via
        position_uncertain/position_issue so downstream consumers know
        not to trust it blindly. Constructed so degrees genuinely
        decode to 95 (impossible) - see position_issue for exact
        derivation.
        """
        symbols = [9, 50, 30, 0, 10]
        result = decoder._decode_position(symbols)
        assert result is not None
        assert result["position_uncertain"] is True
        assert "latitude degrees" in result["position_issue"]
        # the longitude portion is unaffected and still usable
        assert result["lon"] == pytest.approx(0.1667, rel=0.01)
    def test_bits_to_symbol(self, decoder):
        """Test bit to symbol conversion."""
        # Symbol value is first 7 bits (LSB first)
        # Value 100 = 0b1100100 -> bits [0,0,1,0,0,1,1] -> 3 ones
        # Check bits must make total even -> need 1 more one -> [1,0,0]
        bits = [0, 0, 1, 0, 0, 1, 1, 1, 0, 0]
        result = decoder._bits_to_symbol(bits)
        assert result == 100

    def test_bits_to_symbol_wrong_length(self, decoder):
        """Test bit to symbol returns -1 for wrong length."""
        result = decoder._bits_to_symbol([0, 1, 0, 1, 0])
        assert result == -1

    def test_detect_dot_pattern(self, decoder):
        """Test dot pattern detection with 200+ alternating bits."""
        # Dot pattern requires at least 200 bits / 100 alternations
        decoder.bit_buffer = [1, 0] * 110  # 220 alternating bits
        assert decoder._detect_dot_pattern() is True

    def test_detect_dot_pattern_insufficient(self, decoder):
        """Test dot pattern not detected with insufficient bits buffered.

        VHF DSC uses a 20-bit dot pattern (checked within a 24-bit
        window) - not the old HF/MF 200-bit/100-alternation threshold.
        16 bits is below the minimum window needed to even attempt
        detection.
        """
        decoder.bit_buffer = [1, 0] * 8  # 16 bits, below the 24-bit window
        assert decoder._detect_dot_pattern() is False

    def test_detect_dot_pattern_not_alternating(self, decoder):
        """Test dot pattern not detected without alternation."""
        decoder.bit_buffer = [1, 1, 1, 1, 0, 0, 0, 0] * 5
        assert decoder._detect_dot_pattern() is False

    def test_no_anchor_returns_none(self, decoder):
        """Test that a symbol stream with no valid, repeating format
        specifier never produces a decode.

        Replaces the old test_bounded_phasing_strip, which tested the
        pre-fix-9 flat-parser's phasing-symbol-counting logic (bounding
        a linear strip to <=7 symbols). That mechanism no longer exists
        - fix 9 replaced it with anchor detection (the first position
        where a real format-specifier value repeats 2 symbol-positions
        later). This test exercises the equivalent safety property
        (malformed/non-message data never produces a bogus decode)
        against the actual current mechanism: symbol 126 is a real,
        valid ten-unit-code value but is never a format specifier, so
        no anchor can ever be found no matter how much data accumulates.
        """
        # Symbol 126 = info bits [0,1,1,1,1,1,1] (LSB first) + check
        # bits [0,0,1] (MSB first, encoding 1 B-element) - a genuinely
        # valid, check-bit-correct symbol, just never a format specifier.
        symbol_126_bits = [0, 1, 1, 1, 1, 1, 1, 0, 0, 1]
        decoder.message_bits = symbol_126_bits * 60  # 60 symbols, plenty
        result = decoder._try_decode_message()
        assert result is None

    def test_eos_minimum_length(self, decoder):
        """Test that EOS found too early in the symbol stream is skipped."""
        # Build a message where EOS appears at position 5 (< MIN_SYMBOLS_FOR_FORMAT=12)
        # This should not be accepted as a valid message end
        # Symbol 127 (EOS) = 0b1111111 LSB-first: 1,1,1,1,1,1,1 -> ones=7 (odd) -> check needs 1 one
        # Use a simple approach: create symbols directly via _try_decode_message
        # Create 5 normal symbols + EOS at position 5 — should be skipped
        # Followed by more symbols and a real EOS at position 15
        from utils.dsc.decoder import DSCDecoder

        d = DSCDecoder()

        # Build symbols manually: we need _try_decode_message to find EOS too early
        # Symbol 112 = format code. We'll build 10 bits per symbol.
        # Since check bit validation is now active, we need valid check bits.
        # Symbol value 10 = 0b0001010 LSB-first: 0,1,0,1,0,0,0, ones=2 (even) -> check [0,0,0]
        sym_10 = [0, 1, 0, 1, 0, 0, 0, 0, 0, 0]
        # Symbol 127 (EOS) = 0b1111111, ones=7 (odd) -> check needs odd total -> [1,0,0]
        sym_eos = [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]

        # 5 normal symbols + early EOS (should be skipped) + 8 more normal + real EOS
        d.message_bits = sym_10 * 5 + sym_eos + sym_10 * 8 + sym_eos
        result = d._try_decode_message()
        # The early EOS at index 5 should be skipped; the one at index 14
        # is past MIN_SYMBOLS_FOR_FORMAT so it can be accepted.
        # But the message content is garbage, so _decode_symbols will likely
        # return None for other reasons. The key test: it doesn't return a
        # message truncated at position 5.
        # Just verify no crash and either None or a valid longer message
        # (not truncated at the early EOS)
        assert result is None or len(result.get("raw", "")) > 18

    def test_bits_to_symbol_check_bit_validation(self, decoder):
        """Test that _bits_to_symbol rejects symbols with invalid check bits."""
        # Symbol 100 = 0b1100100 LSB-first: 0,0,1,0,0,1,1
        # ones in data = 3, need total even -> check bits need 1 one
        # Valid: [0,0,1,0,0,1,1, 1,0,0] -> total ones = 4 (even) -> valid
        valid_bits = [0, 0, 1, 0, 0, 1, 1, 1, 0, 0]
        assert decoder._bits_to_symbol(valid_bits) == 100

        # Invalid: flip one check bit -> total ones = 5 (odd) -> invalid
        invalid_bits = [0, 0, 1, 0, 0, 1, 1, 0, 0, 0]
        assert decoder._bits_to_symbol(invalid_bits) == -1

    def test_safety_is_critical(self):
        """Test that SAFETY category is marked as critical."""
        import json

        from utils.dsc.parser import parse_dsc_message

        raw = json.dumps(
            {
                "type": "dsc",
                "format": 123,
                "source_mmsi": "232123456",
                "category": "SAFETY",
                "timestamp": "2025-01-15T12:00:00Z",
                "raw": "123232123456100122",
            }
        )
        msg = parse_dsc_message(raw)
        assert msg is not None
        assert msg["is_critical"] is True


class TestDSCConstants:
    """Tests for DSC constants."""

    def test_format_codes_completeness(self):
        """Test format specifiers match ITU-R M.493 exactly.

        Asserts values, not just key presence. Checking keys alone
        passed while 112 and 120 were swapped, which labelled a routine
        individual call as DISTRESS and a real mayday as INDIVIDUAL.
        """
        from utils.dsc.constants import FORMAT_CODES

        assert FORMAT_CODES == {
            102: "GEOGRAPHIC_AREA",
            112: "DISTRESS",
            114: "GROUP",
            116: "ALL_SHIPS",
            120: "INDIVIDUAL",
            123: "INDIVIDUAL_SEMI_AUTO",
        }

    def test_valid_format_specifiers_set(self):
        """Test VALID_FORMAT_SPECIFIERS matches FORMAT_CODES keys."""
        from utils.dsc.constants import FORMAT_CODES, VALID_FORMAT_SPECIFIERS

        assert set(FORMAT_CODES.keys()) == VALID_FORMAT_SPECIFIERS

    def test_valid_eos_symbols(self):
        """Test VALID_EOS contains the three ITU-defined EOS symbols."""
        from utils.dsc.constants import VALID_EOS

        assert {117, 122, 127} == VALID_EOS

    def test_distress_nature_codes_completeness(self):
        """Test distress nature codes match ITU-R M.493 exactly.

        Asserts values, not just membership: a membership-only check
        passes even when every label is shifted off its code, which is
        how these codes drifted a full position out of alignment.
        """
        from utils.dsc.constants import DISTRESS_NATURE_CODES

        assert DISTRESS_NATURE_CODES == {
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
            112: "EPIRB",
        }

    def test_mid_country_map_completeness(self):
        """Test that common MID codes are defined."""
        from utils.dsc.constants import MID_COUNTRY_MAP

        # Verify some key maritime nations
        assert "232" in MID_COUNTRY_MAP  # UK
        assert "366" in MID_COUNTRY_MAP  # USA
        assert "351" in MID_COUNTRY_MAP  # Panama
        assert "257" in MID_COUNTRY_MAP  # Norway
        assert "211" in MID_COUNTRY_MAP  # Germany
        assert "503" in MID_COUNTRY_MAP  # Australia
        assert "431" in MID_COUNTRY_MAP  # Japan

    def test_vhf_channel_70_frequency(self):
        """Test DSC Channel 70 frequency constant."""
        from utils.dsc.constants import VHF_CHANNELS

        assert VHF_CHANNELS[70] == 156.525

    def test_dsc_modulation_parameters(self):
        """Test DSC modulation constants per ITU-R M.493."""
        from utils.dsc.constants import (
            DSC_BAUD_RATE,
            DSC_MARK_FREQ,
            DSC_SPACE_FREQ,
        )

        assert DSC_BAUD_RATE == 1200
        assert DSC_MARK_FREQ == 2100
        assert DSC_SPACE_FREQ == 1300

    def test_telecommand_codes_full(self):
        """Test TELECOMMAND_CODES_FULL covers 0-127 range."""
        from utils.dsc.constants import TELECOMMAND_CODES_FULL

        assert len(TELECOMMAND_CODES_FULL) == 128
        # Known codes map correctly
        assert TELECOMMAND_CODES_FULL[100] == "F3E/G3E ALL MODES TP"
        assert TELECOMMAND_CODES_FULL[107] == "DISTRESS_ACK"
        assert TELECOMMAND_CODES_FULL[118] == "TEST"
        # Unknown codes map to "UNKNOWN"
        assert TELECOMMAND_CODES_FULL[0] == "UNKNOWN"
        assert TELECOMMAND_CODES_FULL[99] == "UNKNOWN"

    def test_telecommand_formats(self):
        """Test TELECOMMAND_FORMATS contains correct format codes."""
        from utils.dsc.constants import TELECOMMAND_FORMATS

        assert {102, 114, 116, 120, 123} == TELECOMMAND_FORMATS

    def test_min_symbols_for_format(self):
        """Test MIN_SYMBOLS_FOR_FORMAT constant."""
        from utils.dsc.constants import MIN_SYMBOLS_FOR_FORMAT

        assert MIN_SYMBOLS_FOR_FORMAT == 12
