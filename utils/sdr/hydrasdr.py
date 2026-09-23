"""
HydraSDR RFOne command builder implementation.

Uses SoapySDR (via SoapyHydraSDR plugin) for all signal processing tasks.
The RFOne is an RX-only receiver covering 24 MHz to 1800 MHz with a
10 MHz instantaneous bandwidth.

Gain is a linear scale 0–21 (not dB); the SoapyHydraSDR plugin maps this
to the hardware's RF gain stages.

Requires: rfone_host library, SoapySDR, SoapyHydraSDR module installed.
Official: https://github.com/hydrasdr/SoapyHydraSDR
"""

from __future__ import annotations

from utils.dependencies import get_tool_path

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType


class HydraSDRCommandBuilder(CommandBuilder):
    """HydraSDR RFOne command builder using SoapySDR / SoapyHydraSDR."""

    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.HYDRA_SDR,
        freq_min_mhz=24.0,
        freq_max_mhz=1800.0,
        gain_min=0.0,
        gain_max=21.0,  # linear scale; recommended starting point: 12
        sample_rates=[2500000, 5000000, 10000000],
        supports_bias_t=False,
        supports_ppm=False,
        tx_capable=False,
        supports_iq_capture=True,
    )

    def _build_device_string(self, device: SDRDevice) -> str:
        """Build SoapySDR device string for HydraSDR RFOne."""
        if device.serial and device.serial not in ("N/A", "Unknown"):
            return f"driver=hydrasdr,serial={device.serial}"
        return "driver=hydrasdr"

    def build_fm_demod_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 22050,
        gain: float | None = None,
        ppm: int | None = None,
        modulation: str = "fm",
        squelch: int | None = None,
        bias_t: bool = False,
    ) -> list[str]:
        device_str = self._build_device_string(device)
        rx_fm_path = get_tool_path("rx_fm") or "rx_fm"
        cmd = [
            rx_fm_path,
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
            cmd.extend(["-g", str(int(gain))])
        if squelch is not None and squelch > 0:
            cmd.extend(["-l", str(squelch)])
        cmd.append("-")
        return cmd

    def build_adsb_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        ppm: int | None = None,
    ) -> list[str]:
        # 1090 MHz is within the RFOne's range (24–1800 MHz)
        device_str = self._build_device_string(device)
        cmd = ["readsb", "--net", "--device-type", "soapysdr", "--device", device_str, "--quiet"]
        if gain is not None:
            cmd.extend(["--gain", str(int(gain))])
        return cmd

    def build_ism_command(
        self,
        device: SDRDevice,
        frequency_mhz: float = 433.92,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False,
    ) -> list[str]:
        device_str = self._build_device_string(device)
        cmd = ["rtl_433", "-d", device_str, "-f", f"{frequency_mhz}M", "-F", "json"]
        if gain is not None and gain > 0:
            cmd.extend(["-g", str(int(gain))])
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
        device_str = self._build_device_string(device)
        cmd = [
            "AIS-catcher",
            "-d",
            f"soapysdr -d {device_str}",
            "-S",
            str(tcp_port),
            "-o",
            "5",
            "-q",
        ]
        if gain is not None and gain > 0:
            cmd.extend(["-gr", "tuner", str(int(gain))])
        if udp_host and udp_port:
            cmd.extend(["-u", udp_host, str(udp_port)])
        return cmd

    def build_iq_capture_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 2048000,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False,
        output_format: str = "cu8",
    ) -> list[str]:
        device_str = self._build_device_string(device)
        freq_hz = int(frequency_mhz * 1e6)
        rx_sdr_path = get_tool_path("rx_sdr") or "rx_sdr"
        cmd = [
            rx_sdr_path,
            "-d",
            device_str,
            "-f",
            str(freq_hz),
            "-s",
            str(sample_rate),
            "-F",
            "CU8",
        ]
        if gain is not None and gain > 0:
            cmd.extend(["-g", str(int(gain))])
        cmd.append("-")
        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        return SDRType.HYDRA_SDR
