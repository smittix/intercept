"""
HackRF command builder implementation.

Uses SoapySDR-based tools (rx_sdr, plus csdr for FM demodulation) and
native HackRF backends where available. HackRF supports 1 MHz to 6 GHz.
"""

from __future__ import annotations

import functools
import logging
import math
import shlex
import subprocess

from utils.dependencies import get_tool_path

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType

logger = logging.getLogger("intercept.sdr.hackrf")

# HackRF's lowest supported sample rate
HACKRF_MIN_SAMPLE_RATE = 2_000_000

# Minimum rate the channel is decimated to before FM demodulation.
# Narrowband FM (pager, APRS, voice) fits easily in 40 kHz; broadcast FM
# needs ~200 kHz for its +/-75 kHz deviation.
_NFM_MIN_CHANNEL_RATE = 40_000
_WFM_MIN_CHANNEL_RATE = 200_000

# Modulations demodulated by the rx_sdr | csdr pipeline. Anything else
# (am, usb, lsb, ...) is still handed to rx_fm.
_CSDR_FM_MODULATIONS = {"fm", "nfm", "wfm"}


@functools.lru_cache(maxsize=1)
def _readsb_has_native_hackrf() -> bool:
    """Whether the installed readsb was built with its native HackRF backend.

    wiedehopf readsb only prints its "--device-type hackrf" help section when
    compiled with HACKRF=yes (the Docker image builds it with SoapySDR only).
    """
    readsb = get_tool_path("readsb")
    if not readsb:
        return False
    try:
        result = subprocess.run([readsb, "--help"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--device-type hackrf" in result.stdout + result.stderr


class HackRFCommandBuilder(CommandBuilder):
    """HackRF command builder using SoapySDR tools."""

    CAPABILITIES = SDRCapabilities(
        sdr_type=SDRType.HACKRF,
        freq_min_mhz=1.0,  # 1 MHz
        freq_max_mhz=6000.0,  # 6 GHz
        gain_min=0.0,
        gain_max=102.0,  # LNA (0-40) + VGA (0-62)
        sample_rates=[2000000, 4000000, 8000000, 10000000, 20000000],
        supports_bias_t=True,
        supports_ppm=False,
        tx_capable=True,
    )

    def _build_device_string(self, device: SDRDevice) -> str:
        """Build SoapySDR device string for HackRF."""
        if device.serial and device.serial != "N/A":
            return f"driver=hackrf,serial={device.serial}"
        return "driver=hackrf"

    def _split_gain(self, gain: float) -> tuple[int, int]:
        """
        Split total gain into LNA and VGA components.

        HackRF has two gain stages:
        - LNA: 0-40 dB (RF amplifier)
        - VGA: 0-62 dB (IF amplifier)

        This function distributes the requested gain across both stages,
        in the hardware's step sizes (LNA 8 dB, VGA 2 dB). Both stages must
        carry gain: with VGA at 0 the ADC sees almost no signal.
        """
        lna = min(40, 8 * round(gain / 16))
        vga = min(62, int(gain - lna) // 2 * 2)
        return lna, max(0, vga)

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
        """
        Build an FM demodulation command that writes s16le mono audio at
        ``sample_rate`` to stdout.

        rx_fm's SoapySDR streaming is broken with HackRF, so FM (fm/nfm/wfm)
        uses rx_sdr for raw I/Q capture piped through csdr when both tools are
        installed. AM/SSB, or installs without rx_sdr/csdr, still use rx_fm.
        """
        rx_sdr_path = get_tool_path("rx_sdr")
        csdr_path = get_tool_path("csdr")
        if modulation in _CSDR_FM_MODULATIONS:
            if rx_sdr_path and csdr_path:
                return self._build_csdr_fm_command(
                    device, frequency_mhz, sample_rate, gain, modulation, squelch, bias_t, rx_sdr_path, csdr_path
                )
            logger.warning("rx_sdr or csdr not found; falling back to rx_fm for HackRF FM, which may not stream")

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
            lna, vga = self._split_gain(gain)
            cmd.extend(["-g", f"LNA={lna},VGA={vga}"])

        if squelch is not None and squelch > 0:
            cmd.extend(["-l", str(squelch)])

        if bias_t:
            cmd.extend(["-T"])

        # Output to stdout
        cmd.append("-")

        return cmd

    def _build_csdr_fm_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int,
        gain: float | None,
        modulation: str,
        squelch: int | None,
        bias_t: bool,
        rx_sdr_path: str,
        csdr_path: str,
    ) -> list[str]:
        """
        rx_sdr (CU8 I/Q) | csdr: offset-tune, channel filter, FM demod, s16 out.

        The capture rate is an exact integer multiple of the output rate so
        only integer decimation is needed: csdr's rational_resampler_ff
        outputs silence for large ratios such as 441/40000, which a fixed
        2 MS/s capture would need for 22050 Hz audio.
        """
        wide = modulation == "wfm"
        min_channel_rate = _WFM_MIN_CHANNEL_RATE if wide else _NFM_MIN_CHANNEL_RATE
        audio_decim = max(1, math.ceil(min_channel_rate / sample_rate))
        channel_rate = sample_rate * audio_decim
        channel_decim = max(1, math.ceil(HACKRF_MIN_SAMPLE_RATE / channel_rate))
        capture_rate = channel_rate * channel_decim

        # Tune capture_rate/8 above the target so HackRF's DC spike falls
        # outside the channel; shift_addition_cc 0.125 moves the signal back.
        tune_hz = round(frequency_mhz * 1e6) + capture_rate // 8

        rx_cmd = [rx_sdr_path, "-d", self._build_device_string(device), "-f", str(tune_hz)]
        rx_cmd += ["-s", str(capture_rate), "-F", "CU8"]
        if gain is not None and gain > 0:
            lna, vga = self._split_gain(gain)
            rx_cmd += ["-g", f"LNA={lna},VGA={vga}"]
        if bias_t:
            rx_cmd.append("-T")
        rx_cmd.append("-")

        stages = [
            "convert_u8_f",
            "shift_addition_cc 0.125",
            # Channel filter; transition band = 25% of the channel rate
            f"fir_decimate_cc {channel_decim} {0.25 / channel_decim:.6f}",
            # atan is exact at wide deviation, unlike quadri's sin() approximation
            "fmdemod_atan_cf",
        ]
        if wide:
            stages.append(f"deemphasis_wfm_ff {channel_rate} 75e-6")
        if audio_decim > 1:
            stages.append(f"rational_resampler_ff 1 {audio_decim}")
        stages += ["limit_ff", "convert_f_s16"]

        csdr = shlex.quote(csdr_path)
        pipeline = " | ".join([shlex.join(rx_cmd)] + [f"{csdr} {stage} 2>/dev/null" for stage in stages])

        if squelch:
            logger.debug("Squelch %s ignored: the HackRF rx_sdr/csdr FM pipeline has no squelch", squelch)

        # Callers stop this by signalling bash, which would orphan the
        # pipeline. Run it in the background and, on TERM/INT/HUP, kill its
        # last stage: the SIGPIPE cascade then stops each stage back to
        # rx_sdr, which handles SIGPIPE and releases the HackRF cleanly.
        script = f"trap 'kill $! 2>/dev/null; wait' TERM INT HUP; {pipeline} & wait $!"
        return [get_tool_path("bash") or "bash", "-c", script]

    def build_adsb_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        ppm: int | None = None,
    ) -> list[str]:
        """
        Build readsb command for ADS-B decoding.

        Prefers readsb's native HackRF backend (no SoapySDR layer, direct LNA
        and VGA control); falls back to SoapySDR for builds without it.
        """
        if _readsb_has_native_hackrf():
            cmd = ["readsb", "--net", "--device-type", "hackrf", "--quiet"]
            if device.serial and device.serial != "N/A":
                cmd.extend(["--device", device.serial])
            if gain is not None:
                # In hackrf mode readsb's --gain is the LNA gain
                lna, vga = self._split_gain(gain)
                cmd.extend(["--gain", str(lna), "--hackrf-vgagain", str(vga - vga % 2)])
        else:
            # wiedehopf readsb names this type "soapysdr"; it rejects "soapy"
            device_str = self._build_device_string(device)
            cmd = ["readsb", "--net", "--device-type", "soapysdr", "--device", device_str, "--quiet"]
            if gain is not None:
                cmd.extend(["--gain", str(int(gain))])

        if bias_t:
            # readsb's option is --enable-biastee; --enable-bias-t is rejected
            cmd.append("--enable-biastee")

        return cmd

    def build_ism_command(
        self,
        device: SDRDevice,
        frequency_mhz: float = 433.92,
        gain: float | None = None,
        ppm: int | None = None,
        bias_t: bool = False,
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
            device_str = f"{device_str},bias_t=1"

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
        """
        Build AIS-catcher command for AIS vessel tracking with HackRF.

        Uses AIS-catcher's native HackRF backend (built with libhackrf support,
        flag 'f' in AIS-catcher's device table), not the SoapySDR wrapper.

        Raises:
            ValueError: if the HackRF's serial is unknown. Without ``-d <serial>``
                AIS-catcher opens the first SDR it finds, which may be another device.
        """
        if not device.serial or device.serial == "N/A":
            raise ValueError(
                "Couldn't read the HackRF's serial number, so AIS-catcher can't be pointed at it. "
                "Check that hackrf_info lists the device, then try again."
            )

        cmd = [
            "AIS-catcher",
            "-S",
            str(tcp_port),
            "-o",
            "5",
            "-q",
        ]

        cmd.extend(["-d", device.serial])

        if gain is not None and gain > 0:
            lna, vga = self._split_gain(gain)
            cmd.extend(["-gf", "LNA", str(lna), "VGA", str(vga)])

        # AIS-catcher's native HACKRF backend has no bias-tee setting
        # (unlike its RTLSDR/AIRSPY/HYDRASDR backends), so bias_t can't be applied.
        if bias_t:
            logger.warning("Bias-T requested for AIS on HackRF %s, but AIS-catcher can't enable it", device.serial)

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
        """
        Build rx_sdr command for raw I/Q capture with HackRF.

        Outputs unsigned 8-bit I/Q pairs to stdout for waterfall display.
        """
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
            lna, vga = self._split_gain(gain)
            cmd.extend(["-g", f"LNA={lna},VGA={vga}"])

        if bias_t:
            cmd.append("-T")

        # Output to stdout
        cmd.append("-")

        return cmd

    def build_hackrf_transfer_iq_command(
        self,
        device: SDRDevice,
        frequency_mhz: float,
        sample_rate: int = 2000000,
        gain: float | None = None,
        bias_t: bool = False,
    ) -> list[str]:
        """
        Build hackrf_transfer command for raw I/Q capture, for installs without
        SoapySDR's rx_sdr.

        Unlike build_iq_capture_command, the output is SIGNED 8-bit I/Q (CS8),
        and hackrf_transfer writes a status line to stderr every second, so the
        caller must decode accordingly and keep stderr drained.
        """
        cmd = [
            get_tool_path("hackrf_transfer") or "hackrf_transfer",
            "-r",
            "-",
            "-f",
            str(int(frequency_mhz * 1e6)),
            "-s",
            str(sample_rate),
        ]
        if device.serial and device.serial != "N/A":
            cmd.extend(["-d", device.serial])
        if gain is not None and gain > 0:
            lna, vga = self._split_gain(gain)
            # hackrf_transfer takes LNA in 8 dB and VGA in 2 dB steps
            cmd.extend(["-l", str(lna - lna % 8), "-g", str(vga - vga % 2)])
        if bias_t:
            cmd.extend(["-p", "1"])
        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return HackRF capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.HACKRF
