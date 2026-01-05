"""
RTL-SDR command builder implementation.

Uses native rtl_* tools (rtl_fm, rtl_433) and dump1090 for maximum compatibility
with existing RTL-SDR installations. No SoapySDR dependency required.
"""

from __future__ import annotations

from typing import Optional

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType


class RTLSDRCommandBuilder(CommandBuilder):
    """RTL-SDR command builder using native rtl_* tools."""

    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.RTL_SDR,
        freq_min_mhz=24.0,
        freq_max_mhz=1766.0,
        gain_min=0.0,
        gain_max=49.6,
        sample_rates=[250000, 1024000, 1800000, 2048000, 2400000],
        supports_bias_t=True,
        supports_ppm=True,
        tx_capable=False,
    )

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
        Build rtl_fm command for FM demodulation.

        Used for pager decoding.
        """
        cmd = [
            "rtl_fm",
            "-d",
            str(device.index),
            "-f",
            f"{frequency_mhz}M",
            "-M",
            modulation,
            "-s",
            str(sample_rate),
        ]

        if gain is not None and gain > 0:
            cmd.extend(["-g", str(gain)])

        if ppm is not None and ppm != 0:
            cmd.extend(["-p", str(ppm)])

        if squelch is not None and squelch > 0:
            cmd.extend(["-l", str(squelch)])

        # Output to stdout for piping
        cmd.append("-")

        return cmd

    def build_adsb_command(self, device: SDRDevice, gain: Optional[float] = None) -> list[str]:
        """
        Build dump1090 command for ADS-B decoding.

        Uses dump1090 with network output for SBS data streaming.
        """
        cmd = ["dump1090", "--net", "--device-index", str(device.index), "--quiet"]

        if gain is not None:
            cmd.extend(["--gain", str(int(gain))])

        return cmd

    def build_ism_command(
        self, device: SDRDevice, frequency_mhz: float = 433.92, gain: Optional[float] = None, ppm: Optional[int] = None
    ) -> list[str]:
        """
        Build rtl_433 command for ISM band sensor decoding.

        Outputs JSON for easy parsing.
        """
        cmd = ["rtl_433", "-d", str(device.index), "-f", f"{frequency_mhz}M", "-F", "json"]

        if gain is not None and gain > 0:
            cmd.extend(["-g", str(int(gain))])

        if ppm is not None and ppm != 0:
            cmd.extend(["-p", str(ppm)])

        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return RTL-SDR capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.RTL_SDR
