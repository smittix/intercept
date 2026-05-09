"""
HackRF command builder implementation.

Uses SoapySDR-based tools for FM demodulation and signal capture.
HackRF supports 1 MHz to 6 GHz frequency range.
"""

from __future__ import annotations

from utils.dependencies import get_tool_path

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType


class HackRFCommandBuilder(CommandBuilder):
    """HackRF command builder using SoapySDR tools."""

    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.HACKRF,
        freq_min_mhz=1.0,        # 1 MHz
        freq_max_mhz=6000.0,     # 6 GHz
        gain_min=0.0,
        gain_max=102.0,          # LNA (0-40) + VGA (0-62)
        sample_rates=[2000000, 4000000, 8000000, 10000000, 20000000],
        supports_bias_t=True,
        supports_ppm=False,
        tx_capable=True
    )

    def _build_device_string(self, device: SDRDevice) -> str:
        """Build SoapySDR device string for HackRF."""
        if device.serial and device.serial != 'N/A':
            return f'driver=hackrf,serial={device.serial}'
        return 'driver=hackrf'

    def _split_gain(self, gain: float) -> tuple[int, int]:
        """
        Split total gain into LNA and VGA components.

        HackRF has two gain stages:
        - LNA: 0-40 dB (RF amplifier)
        - VGA: 0-62 dB (IF amplifier)

        This function distributes the requested gain across both stages.
        """
        if gain <= 40:
            # All to LNA first
            return int(gain), 0
        else:
            # Max out LNA, rest to VGA
            lna = 40
            vga = min(62, int(gain - 40))
            return lna, vga

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

        For pager decoding with HackRF.
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
            lna, vga = self._split_gain(gain)
            cmd.extend(['-g', f'LNA={lna},VGA={vga}'])

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
        Note: HackRF does not support PPM correction via readsb; ppm is ignored.
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

        Note: rtl_433's -T flag is for timeout, NOT bias-t.
        For SoapySDR devices, bias-t is passed as a device setting.
        """
        # Build device string with optional bias-t setting
        device_str = self._build_device_string(device)
        if bias_t:
            device_str = f'{device_str},bias_t=1'

        cmd = [
            'rtl_433',
            '-d', device_str,
            '-f', f'{frequency_mhz}M',
            '-F', 'json'
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', str(int(gain))])

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
        Build AIS-catcher command for AIS vessel tracking with HackRF.

        Uses AIS-catcher with SoapySDR support.
        Note: HackRF does not support PPM correction; ppm is ignored.
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
        Build rx_sdr command for raw I/Q capture with HackRF.

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
            lna, vga = self._split_gain(gain)
            cmd.extend(['-g', f'LNA={lna},VGA={vga}'])

        if bias_t:
            cmd.append('-T')

        # Output to stdout
        cmd.append('-')

        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return HackRF capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.HACKRF
