"""
Base classes and types for SDR hardware abstraction.

This module provides the core abstractions for supporting multiple SDR hardware
types (RTL-SDR, LimeSDR, HackRF, etc.) through a unified interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class SDRType(Enum):
    """Supported SDR hardware types."""
    RTL_SDR = "rtlsdr"
    LIME_SDR = "limesdr"
    HACKRF = "hackrf"
    AIRSPY = "airspy"
    SDRPLAY = "sdrplay"
    # Future support
    # USRP = "usrp"
    # BLADE_RF = "bladerf"


@dataclass
class SDRCapabilities:
    """Hardware capabilities for an SDR device."""
    sdr_type: SDRType
    freq_min_mhz: float          # Minimum frequency in MHz
    freq_max_mhz: float          # Maximum frequency in MHz
    gain_min: float              # Minimum gain in dB
    gain_max: float              # Maximum gain in dB
    sample_rates: list[int] = field(default_factory=list)  # Supported sample rates
    supports_bias_t: bool = False    # Bias-T support
    supports_ppm: bool = True        # PPM correction support
    tx_capable: bool = False         # Can transmit
    supports_iq_capture: bool = False  # Raw I/Q sample capture


@dataclass
class SDRDevice:
    """Detected SDR device."""
    sdr_type: SDRType
    index: int
    name: str
    serial: str
    driver: str                  # e.g., "rtlsdr", "lime", "hackrf"
    capabilities: SDRCapabilities
    rtl_tcp_host: str | None = None   # Remote rtl_tcp server host
    rtl_tcp_port: int | None = None   # Remote rtl_tcp server port

    @property
    def is_network(self) -> bool:
        """Check if this is a network/remote device."""
        return self.rtl_tcp_host is not None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            'index': self.index,
            'name': self.name,
            'serial': self.serial,
            'sdr_type': self.sdr_type.value,
            'driver': self.driver,
            'is_network': self.is_network,
            'capabilities': {
                'freq_min_mhz': self.capabilities.freq_min_mhz,
                'freq_max_mhz': self.capabilities.freq_max_mhz,
                'gain_min': self.capabilities.gain_min,
                'gain_max': self.capabilities.gain_max,
                'sample_rates': self.capabilities.sample_rates,
                'supports_bias_t': self.capabilities.supports_bias_t,
                'supports_ppm': self.capabilities.supports_ppm,
                'tx_capable': self.capabilities.tx_capable,
            }
        }
        if self.is_network:
            result['rtl_tcp_host'] = self.rtl_tcp_host
            result['rtl_tcp_port'] = self.rtl_tcp_port
        return result


class CommandBuilder(ABC):
    """Abstract base class for building SDR commands."""

    @abstractmethod
    def build_fm_demod_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 22050,
        gain: float | None = None,
        ppm: int | None = None,
        modulation: str = "fm",
        squelch: int | None = None,
        bias_t: bool = False
    ) -> list[str]:
        """
        Build FM demodulation command (for pager decoding).

        Args:
            device: The SDR device to use
            frequency_mhz: Center frequency in MHz
            sample_rate: Audio sample rate (default 22050 for pager)
            gain: Gain in dB (None for auto)
            ppm: PPM frequency correction
            modulation: Modulation type (fm, am, etc.)
            squelch: Squelch level
            bias_t: Enable bias-T power (for active antennas)

        Returns:
            Command as list of strings for subprocess
        """
        pass

    @abstractmethod
    def build_adsb_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        ppm: int | None = None,
    ) -> list[str]:
        """
        Build ADS-B decoder command.

        Args:
            device: The SDR device to use
            gain: Gain in dB (None for auto)
            bias_t: Enable bias-T power (for active antennas)
            ppm: Frequency correction in parts-per-million (RTL-SDR only)

        Returns:
            Command as list of strings for subprocess
        """
        pass

    @abstractmethod
    def build_ism_command(
        self,
        device: SDRDevice,
        frequency_mhz: float = 433.92,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False
    ) -> list[str]:
        """
        Build ISM band decoder command (433MHz sensors).

        Args:
            device: The SDR device to use
            frequency_mhz: Center frequency in MHz (default 433.92)
            gain: Gain in dB (None for auto)
            ppm: PPM frequency correction
            bias_t: Enable bias-T power (for active antennas)

        Returns:
            Command as list of strings for subprocess
        """
        pass

    @abstractmethod
    def build_ais_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        tcp_port: int = 10110,
        udp_host: str | None = None,
        udp_port: int | None = None,
        ppm: int | None = None,
    ) -> list[str]:
        """
        Build AIS decoder command for vessel tracking.

        Args:
            device: The SDR device to use
            gain: Gain in dB (None for auto)
            bias_t: Enable bias-T power (for active antennas)
            tcp_port: TCP port for JSON output server
            udp_host: Optional host to forward NMEA 0183 sentences via UDP
            udp_port: UDP port for NMEA forwarding (required if udp_host set)

        Returns:
            Command as list of strings for subprocess
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> SDRCapabilities:
        """Return hardware capabilities for this SDR type."""
        pass

    def build_iq_capture_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 2048000,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False,
        output_format: str = 'cu8',
    ) -> list[str]:
        """
        Build raw I/Q capture command for streaming samples to stdout.

        Used for real-time waterfall/spectrum display. Output is unsigned
        8-bit I/Q pairs (cu8) written continuously to stdout.

        Args:
            device: The SDR device to use
            frequency_mhz: Center frequency in MHz
            sample_rate: Sample rate in Hz (default 2048000)
            gain: Gain in dB (None for auto)
            ppm: PPM frequency correction
            bias_t: Enable bias-T power (for active antennas)
            output_format: Output sample format (default 'cu8')

        Returns:
            Command as list of strings for subprocess

        Raises:
            NotImplementedError: If the SDR type does not support I/Q capture.
        """
        if not device.capabilities.supports_iq_capture:
            supported = ', '.join(
                t.value for t in SDRType
                if t == SDRType.RTL_SDR  # known IQ-capable types
            )
            raise ValueError(
                f"{device.sdr_type.value} does not support raw I/Q capture. "
                f"Supported devices: {supported}"
            )
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support raw I/Q capture"
        )

    @classmethod
    @abstractmethod
    def get_sdr_type(cls) -> SDRType:
        """Return the SDR type this builder handles."""
        pass
