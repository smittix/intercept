"""
SDRPlay command builder implementation.

Uses SoapySDR-based tools for FM demodulation and signal capture.
SDRPlay RSP devices support 1 kHz to 2 GHz frequency range.
"""

from __future__ import annotations

from utils.dependencies import get_tool_path

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType


class SDRPlayCommandBuilder(CommandBuilder):
    """SDRPlay command builder using SoapySDR tools."""

    # SDRPlay RSP capabilities (RSPdx, RSP1A, RSPduo, etc.)
    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.SDRPLAY,
        freq_min_mhz=0.001,      # 1 kHz
        freq_max_mhz=2000.0,     # 2 GHz
        gain_min=0.0,
        gain_max=59.0,           # IFGR range
        sample_rates=[62500, 96000, 125000, 192000, 250000, 384000, 500000, 1000000, 2000000],
        supports_bias_t=True,
        supports_ppm=False,      # SDRPlay has TCXO, no PPM needed
        tx_capable=False
    )

    def _build_device_string(self, device: SDRDevice) -> str:
        """Build SoapySDR device string for SDRPlay."""
        if device.serial and device.serial != 'N/A':
            return f'driver=sdrplay,serial={device.serial}'
        return 'driver=sdrplay'

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
        Build SoapySDR rx_fm command for FM demodulation.

        For pager decoding with SDRPlay.
        """
        device_str = self._build_device_string(device)

        rx_fm_path = get_tool_path('rx_fm') or 'rx_fm'
        cmd = [
            rx_fm_path,
            '-d', device_str,
            '-f', f'{frequency_mhz}M',
            '-M', modulation,
            '-s', str(sample_rate),
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', f'IFGR={int(gain)}'])

        if squelch is not None and squelch > 0:
            cmd.extend(['-l', str(squelch)])

        if bias_t:
            cmd.extend(['-T'])

        # Output to stdout
        cmd.append('-')

        return cmd

    def build_adsb_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        ppm: int | None = None,
    ) -> list[str]:
        """
        Build dump1090/readsb command with SoapySDR support for ADS-B decoding.

        Uses readsb which has better SoapySDR support.
        """
        device_str = self._build_device_string(device)

        cmd = [
            'readsb',
            '--net',
            '--device-type', 'soapysdr',
            '--device', device_str,
            '--quiet'
        ]

        if gain is not None:
            cmd.extend(['--gain', str(int(gain))])

        if bias_t:
            cmd.extend(['--enable-bias-t'])

        return cmd

    def build_ism_command(
        self,
        device: SDRDevice,
        frequency_mhz: float = 433.92,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False
    ) -> list[str]:
        """
        Build rtl_433 command with SoapySDR support for ISM band decoding.

        rtl_433 has native SoapySDR support via -d flag.
        """
        device_str = self._build_device_string(device)

        cmd = [
            'rtl_433',
            '-d', device_str,
            '-f', f'{frequency_mhz}M',
            '-F', 'json'
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', str(int(gain))])

        if bias_t:
            cmd.extend(['-T'])

        return cmd

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
        Build AIS-catcher command for AIS vessel tracking with SDRPlay.

        Uses AIS-catcher with SoapySDR support.
        """
        device_str = self._build_device_string(device)

        cmd = [
            'AIS-catcher',
            '-d', f'soapysdr -d {device_str}',
            '-S', str(tcp_port),
            '-o', '5',
            '-q',
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-gr', 'tuner', str(int(gain))])

        if bias_t:
            cmd.extend(['-gr', 'biastee', '1'])

        if udp_host and udp_port:
            cmd.extend(['-u', udp_host, str(udp_port)])

        return cmd

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
        Build rx_sdr command for raw I/Q capture with SDRPlay.

        Outputs unsigned 8-bit I/Q pairs to stdout for waterfall display.
        """
        device_str = self._build_device_string(device)
        freq_hz = int(frequency_mhz * 1e6)

        rx_sdr_path = get_tool_path('rx_sdr') or 'rx_sdr'
        cmd = [
            rx_sdr_path,
            '-d', device_str,
            '-f', str(freq_hz),
            '-s', str(sample_rate),
            '-F', 'CU8',
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', f'IFGR={int(gain)}'])

        if bias_t:
            cmd.append('-T')

        # Output to stdout
        cmd.append('-')

        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return SDRPlay capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.SDRPLAY
