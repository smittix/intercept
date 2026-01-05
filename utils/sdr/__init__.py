"""
SDR Hardware Abstraction Layer.

This module provides a unified interface for multiple SDR hardware types
including RTL-SDR, LimeSDR, and HackRF. Use SDRFactory to detect devices
and get appropriate command builders.

Example usage:
    from utils.sdr import SDRFactory, SDRType

    # Detect all connected devices
    devices = SDRFactory.detect_devices()

    # Get a command builder for a specific device
    builder = SDRFactory.get_builder_for_device(devices[0])

    # Or get a builder by type
    builder = SDRFactory.get_builder(SDRType.RTL_SDR)

    # Build commands
    cmd = builder.build_fm_demod_command(device, frequency_mhz=153.35)
"""

from __future__ import annotations

from typing import Optional

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType
from .detection import detect_all_devices
from .rtlsdr import RTLSDRCommandBuilder
from .limesdr import LimeSDRCommandBuilder
from .hackrf import HackRFCommandBuilder
from .validation import (
    SDRValidationError,
    validate_frequency,
    validate_gain,
    validate_sample_rate,
    validate_ppm,
    validate_device_index,
    validate_squelch,
    get_capabilities_for_type,
)


class SDRFactory:
    """Factory for creating SDR command builders and detecting devices."""

    _builders: dict[SDRType, type[CommandBuilder]] = {
        SDRType.RTL_SDR: RTLSDRCommandBuilder,
        SDRType.LIME_SDR: LimeSDRCommandBuilder,
        SDRType.HACKRF: HackRFCommandBuilder,
    }

    @classmethod
    def get_builder(cls, sdr_type: SDRType) -> CommandBuilder:
        """
        Get a command builder for the specified SDR type.

        Args:
            sdr_type: The SDR hardware type

        Returns:
            CommandBuilder instance for the specified type

        Raises:
            ValueError: If the SDR type is not supported
        """
        builder_class = cls._builders.get(sdr_type)
        if not builder_class:
            raise ValueError(f"Unsupported SDR type: {sdr_type}")
        return builder_class()

    @classmethod
    def get_builder_for_device(cls, device: SDRDevice) -> CommandBuilder:
        """
        Get a command builder for a specific device.

        Args:
            device: The SDR device

        Returns:
            CommandBuilder instance for the device's type
        """
        return cls.get_builder(device.sdr_type)

    @classmethod
    def detect_devices(cls) -> list[SDRDevice]:
        """
        Detect all available SDR devices.

        Returns:
            List of detected SDR devices
        """
        return detect_all_devices()

    @classmethod
    def get_supported_types(cls) -> list[SDRType]:
        """
        Get list of supported SDR types.

        Returns:
            List of supported SDRType values
        """
        return list(cls._builders.keys())

    @classmethod
    def get_capabilities(cls, sdr_type: SDRType) -> SDRCapabilities:
        """
        Get capabilities for an SDR type.

        Args:
            sdr_type: The SDR hardware type

        Returns:
            SDRCapabilities for the specified type
        """
        builder = cls.get_builder(sdr_type)
        return builder.get_capabilities()

    @classmethod
    def get_all_capabilities(cls) -> dict[str, dict]:
        """
        Get capabilities for all supported SDR types.

        Returns:
            Dictionary mapping SDR type names to capability dicts
        """
        capabilities = {}
        for sdr_type in cls._builders:
            caps = cls.get_capabilities(sdr_type)
            capabilities[sdr_type.value] = {
                "name": sdr_type.name.replace("_", " "),
                "freq_min_mhz": caps.freq_min_mhz,
                "freq_max_mhz": caps.freq_max_mhz,
                "gain_min": caps.gain_min,
                "gain_max": caps.gain_max,
                "sample_rates": caps.sample_rates,
                "supports_bias_t": caps.supports_bias_t,
                "supports_ppm": caps.supports_ppm,
                "tx_capable": caps.tx_capable,
            }
        return capabilities

    @classmethod
    def create_default_device(cls, sdr_type: SDRType, index: int = 0, serial: str = "N/A") -> SDRDevice:
        """
        Create a default device object for a given SDR type.

        Useful when device detection didn't provide full details but
        you know the hardware type.

        Args:
            sdr_type: The SDR hardware type
            index: Device index (default 0)
            serial: Device serial (default 'N/A')

        Returns:
            SDRDevice with default capabilities for the type
        """
        caps = cls.get_capabilities(sdr_type)
        return SDRDevice(
            sdr_type=sdr_type,
            index=index,
            name=f'{sdr_type.name.replace("_", " ")} Device {index}',
            serial=serial,
            driver=sdr_type.value,
            capabilities=caps,
        )

    @classmethod
    def create_network_device(
        cls,
        host: str,
        port: int = 1234
    ) -> SDRDevice:
        """
        Create a network device for rtl_tcp connection.

        Args:
            host: rtl_tcp server hostname or IP address
            port: rtl_tcp server port (default 1234)

        Returns:
            SDRDevice configured for rtl_tcp connection
        """
        caps = cls.get_capabilities(SDRType.RTL_SDR)
        return SDRDevice(
            sdr_type=SDRType.RTL_SDR,
            index=0,
            name=f'{host}:{port}',
            serial='rtl_tcp',
            driver='rtl_tcp',
            capabilities=caps,
            rtl_tcp_host=host,
            rtl_tcp_port=port
        )


# Export commonly used items at package level
__all__ = [
    # Factory
    "SDRFactory",
    # Types and classes
    "SDRType",
    "SDRDevice",
    "SDRCapabilities",
    "CommandBuilder",
    # Builders
    "RTLSDRCommandBuilder",
    "LimeSDRCommandBuilder",
    "HackRFCommandBuilder",
    # Validation
    "SDRValidationError",
    "validate_frequency",
    "validate_gain",
    "validate_sample_rate",
    "validate_ppm",
    "validate_device_index",
    "validate_squelch",
    "get_capabilities_for_type",
]
