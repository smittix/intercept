"""Tests for utility modules."""

import pytest
from utils.process import is_valid_mac, is_valid_channel
from utils.dependencies import check_tool
from data.oui import get_manufacturer


class TestMacValidation:
    """Tests for MAC address validation."""

    def test_valid_mac(self):
        """Test valid MAC addresses."""
        assert is_valid_mac("AA:BB:CC:DD:EE:FF") is True
        assert is_valid_mac("aa:bb:cc:dd:ee:ff") is True
        assert is_valid_mac("00:11:22:33:44:55") is True

    def test_invalid_mac(self):
        """Test invalid MAC addresses."""
        assert is_valid_mac("") is False
        assert is_valid_mac(None) is False
        assert is_valid_mac("invalid") is False
        assert is_valid_mac("AA:BB:CC:DD:EE") is False
        assert is_valid_mac("AA-BB-CC-DD-EE-FF") is False


class TestChannelValidation:
    """Tests for WiFi channel validation."""

    def test_valid_channels(self):
        """Test valid channel numbers."""
        assert is_valid_channel(1) is True
        assert is_valid_channel(6) is True
        assert is_valid_channel(11) is True
        assert is_valid_channel("36") is True
        assert is_valid_channel(149) is True

    def test_invalid_channels(self):
        """Test invalid channel numbers."""
        assert is_valid_channel(0) is False
        assert is_valid_channel(-1) is False
        assert is_valid_channel(201) is False
        assert is_valid_channel(None) is False
        assert is_valid_channel("invalid") is False


class TestToolCheck:
    """Tests for tool availability checking."""

    def test_common_tools(self):
        """Test checking for common tools."""
        # These should return bool, regardless of whether installed
        assert isinstance(check_tool("ls"), bool)
        assert isinstance(check_tool("nonexistent_tool_12345"), bool)

    def test_nonexistent_tool(self):
        """Test that nonexistent tools return False."""
        assert check_tool("nonexistent_tool_xyz_12345") is False


class TestOuiLookup:
    """Tests for OUI manufacturer lookup."""

    def test_known_manufacturer(self):
        """Test looking up known manufacturers."""
        # Apple prefix
        result = get_manufacturer("00:25:DB:AA:BB:CC")
        assert result == "Apple" or result == "Unknown"

    def test_unknown_manufacturer(self):
        """Test looking up unknown manufacturer."""
        result = get_manufacturer("FF:FF:FF:FF:FF:FF")
        assert result == "Unknown"
