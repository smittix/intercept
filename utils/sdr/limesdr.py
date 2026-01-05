"""
LimeSDR command builder implementation.

Uses SoapySDR-based tools for FM demodulation and signal capture.
LimeSDR supports 100 kHz to 3.8 GHz frequency range.
"""

from __future__ import annotations

from typing import Optional

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType


class LimeSDRCommandBuilder(CommandBuilder):
    """LimeSDR command builder using SoapySDR tools."""

    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.LIME_SDR,
        freq_min_mhz=0.1,  # 100 kHz
        freq_max_mhz=3800.0,  # 3.8 GHz
        gain_min=0.0,
        gain_max=73.0,  # Combined LNA + TIA + PGA
        sample_rates=[1000000, 2000000, 4000000, 8000000, 10000000, 20000000],
        supports_bias_t=False,
        supports_ppm=False,  # Uses TCXO, no PPM correction needed
        tx_capable=True,
    )

    def _build_device_string(self, device: SDRDevice) -> str:
        """Build SoapySDR device string for LimeSDR."""
        if device.serial and device.serial != "N/A":
            return f"driver=lime,serial={device.serial}"
        return f"driver=lime"

    def build_fm_demod_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 22050,
        gain: Optional[float] = None,
        ppm: Optional[int] = None,
        modulation: str = "fm",
        squelch: Optional[int] = None,
    ) -> list[str]:
        """
        Build SoapySDR rx_fm command for FM demodulation.

        For pager decoding and iridium capture with LimeSDR.
        """
        device_str = self._build_device_string(device)

        cmd = [
            "rx_fm",
            "-d",
            device_str,
            "-f",
            f"{frequency_mhz}M",
            "-M",
            modulation,
            "-s",
            str(sample_rate),
        ]

        if gain is not None and gain > 0:
            # LimeSDR gain is applied to LNAH element
            cmd.extend(["-g", f"LNAH={int(gain)}"])

        if squelch is not None and squelch > 0:
            cmd.extend(["-l", str(squelch)])

        # Output to stdout
        cmd.append("-")

        return cmd

    def build_adsb_command(self, device: SDRDevice, gain: Optional[float] = None) -> list[str]:
        """
        Build dump1090 command with SoapySDR support for ADS-B decoding.

        Uses dump1090 compiled with SoapySDR support, or readsb as alternative.
        Note: Requires dump1090 with SoapySDR support or readsb.
        """
        device_str = self._build_device_string(device)

        # Try readsb first (better SoapySDR support), fallback to dump1090
        cmd = ["readsb", "--net", "--device-type", "soapysdr", "--device", device_str, "--quiet"]

        if gain is not None:
            cmd.extend(["--gain", str(int(gain))])

        return cmd

    def build_ism_command(
        self, device: SDRDevice, frequency_mhz: float = 433.92, gain: Optional[float] = None, ppm: Optional[int] = None
    ) -> list[str]:
        """
        Build rtl_433 command with SoapySDR support for ISM band decoding.

        rtl_433 has native SoapySDR support via -d flag.
        """
        device_str = self._build_device_string(device)

        cmd = ["rtl_433", "-d", device_str, "-f", f"{frequency_mhz}M", "-F", "json"]

        if gain is not None and gain > 0:
            cmd.extend(["-g", str(int(gain))])

        # PPM not typically needed for LimeSDR (TCXO)
        # but include if specified
        if ppm is not None and ppm != 0:
            cmd.extend(["-p", str(ppm)])

        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return LimeSDR capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.LIME_SDR
