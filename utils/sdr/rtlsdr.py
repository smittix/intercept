"""
RTL-SDR command builder implementation.

Uses native rtl_* tools (rtl_fm, rtl_433) and dump1090 for maximum compatibility
with existing RTL-SDR installations. No SoapySDR dependency required.
"""

from __future__ import annotations

import logging
import re
import subprocess

from utils.dependencies import get_tool_path

from .base import CommandBuilder, SDRCapabilities, SDRDevice, SDRType

logger = logging.getLogger('intercept.sdr.rtlsdr')


def _rtl_fm_demod_mode(modulation: str) -> str:
    """Map app/UI modulation names to rtl_fm demod tokens."""
    mod = str(modulation or '').lower().strip()
    return 'wbfm' if mod == 'wfm' else mod


def _rtl_tool_supports_bias_t(tool_path: str) -> bool:
    """Check if an rtl_* tool (rtl_fm, rtl_sdr) supports the -T bias-tee flag.

    The -T flag is only available in RTL-SDR Blog builds, not in stock
    rtl-sdr packages shipped by most distros.
    """
    try:
        result = subprocess.run(
            [tool_path, '--help'],
            capture_output=True,
            text=True,
            timeout=5
        )
        help_text = result.stdout + result.stderr
        # Match "-T" as a CLI flag (e.g. "[-T]" or "-T enable bias"),
        # not as part of "DVB-T" or similar text.
        return bool(re.search(r'(?<!\w)-T\b', help_text))
    except Exception as e:
        logger.warning(f"Could not detect bias-t support for {tool_path}: {e}")
        return False


def enable_bias_t_via_rtl_biast(device_index: int = 0) -> bool:
    """Enable bias-t power using rtl_biast (RTL-SDR Blog drivers).

    Runs rtl_biast to set the bias-t register on the device, then exits.
    The setting persists across device opens until the device is reset.

    Returns True if bias-t was enabled successfully.
    """
    rtl_biast_path = get_tool_path('rtl_biast') or 'rtl_biast'
    try:
        result = subprocess.run(
            [rtl_biast_path, '-b', '1', '-d', str(device_index)],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.info(f"Bias-t enabled via rtl_biast on device {device_index}")
            return True
        logger.warning(f"rtl_biast failed (exit {result.returncode}): {result.stderr.strip()}")
        return False
    except FileNotFoundError:
        logger.warning("rtl_biast not found — install RTL-SDR Blog drivers for bias-t support")
        return False
    except Exception as e:
        logger.warning(f"Failed to enable bias-t via rtl_biast: {e}")
        return False


def disable_bias_t_via_rtl_biast(device_index: int = 0) -> bool:
    """Disable bias-t power using rtl_biast (RTL-SDR Blog drivers).

    Should be called when stopping an SDR mode that had bias-t enabled,
    since the hardware register persists after the device is closed.

    Returns True if bias-t was disabled successfully.
    """
    rtl_biast_path = get_tool_path('rtl_biast') or 'rtl_biast'
    try:
        result = subprocess.run(
            [rtl_biast_path, '-b', '0', '-d', str(device_index)],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            logger.info(f"Bias-t disabled via rtl_biast on device {device_index}")
            return True
        logger.warning(f"rtl_biast failed (exit {result.returncode}): {result.stderr.strip()}")
        return False
    except FileNotFoundError:
        logger.warning("rtl_biast not found — bias-t may remain on after stop")
        return False
    except Exception as e:
        logger.warning(f"Failed to disable bias-t via rtl_biast: {e}")
        return False


def _get_dump1090_bias_t_flag(dump1090_path: str) -> str | None:
    """Detect the correct bias-t flag for the installed dump1090 variant.

    Different dump1090 forks use different flags:
    - dump1090-fa, readsb: --enable-biast (no hyphen before 't')
    - dump1090-mutability, original dump1090: no bias-t support

    Returns the correct flag string or None if bias-t is not supported.
    """
    try:
        result = subprocess.run(
            [dump1090_path, '--help'],
            capture_output=True,
            text=True,
            timeout=5
        )
        help_text = result.stdout + result.stderr

        # Check for dump1090-fa/readsb style flag (no hyphen)
        if '--enable-biast' in help_text:
            return '--enable-biast'

        # No bias-t support found
        return None
    except Exception as e:
        logger.warning(f"Could not detect dump1090 bias-t support: {e}")
        return None


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
        supports_iq_capture=True
    )

    def _get_device_arg(self, device: SDRDevice) -> str:
        """Get device argument for rtl_* tools.

        Returns rtl_tcp connection string for network devices,
        or device index for local devices.
        """
        if device.is_network:
            return f"rtl_tcp:{device.rtl_tcp_host}:{device.rtl_tcp_port}"
        return str(device.index)

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
        direct_sampling: int | None = None,
    ) -> list[str]:
        """
        Build rtl_fm command for FM demodulation.

        Used for pager decoding. Supports local devices and rtl_tcp connections.

        Args:
            direct_sampling: Enable direct sampling mode (0=off, 1=I-branch,
                2=Q-branch). Use 2 for HF reception below 24 MHz.
        """
        rtl_fm_path = get_tool_path('rtl_fm') or 'rtl_fm'
        demod_mode = _rtl_fm_demod_mode(modulation)
        cmd = [
            rtl_fm_path,
            '-d', self._get_device_arg(device),
            '-f', f'{frequency_mhz}M',
            '-M', demod_mode,
            '-s', str(sample_rate),
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', str(gain)])

        if ppm is not None and ppm != 0:
            cmd.extend(['-p', str(ppm)])

        if squelch is not None and squelch > 0:
            cmd.extend(['-l', str(squelch)])

        if direct_sampling is not None:
            # Older rtl_fm builds (common in Docker/distro packages) don't
            # support -D; they use -E direct / -E direct2 instead.
            if direct_sampling == 1:
                cmd.extend(['-E', 'direct'])
            elif direct_sampling == 2:
                cmd.extend(['-E', 'direct2'])

        if bias_t:
            if _rtl_tool_supports_bias_t(rtl_fm_path):
                cmd.append('-T')
            else:
                logger.warning("Bias-t requested but rtl_fm does not support -T (RTL-SDR Blog drivers required).")

        # Output to stdout for piping
        cmd.append('-')

        return cmd

    def build_adsb_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False
    ) -> list[str]:
        """
        Build dump1090 command for ADS-B decoding.

        Uses dump1090 with network output for SBS data streaming.

        Note: dump1090 does not support rtl_tcp. For remote SDR, connect to
        a remote dump1090's SBS output (port 30003) instead.
        """
        if device.is_network:
            raise ValueError(
                "dump1090 does not support rtl_tcp. "
                "For remote ADS-B, run dump1090 on the remote machine and "
                "connect to its SBS output (port 30003)."
            )

        dump1090_path = get_tool_path('dump1090') or 'dump1090'
        cmd = [
            dump1090_path,
            '--net',
            '--device-index', str(device.index),
            '--quiet'
        ]

        if gain is not None:
            cmd.extend(['--gain', str(int(gain))])

        if bias_t:
            bias_t_flag = _get_dump1090_bias_t_flag(dump1090_path)
            if bias_t_flag:
                cmd.append(bias_t_flag)
            else:
                # Fallback: use rtl_biast to set bias-t before starting dump1090
                if not enable_bias_t_via_rtl_biast(device.index):
                    logger.warning(
                        f"Bias-t requested but {dump1090_path} does not support it "
                        "and rtl_biast is not available. Install RTL-SDR Blog drivers "
                        "or use dump1090-fa/readsb for bias-t support."
                    )

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
        Build rtl_433 command for ISM band sensor decoding.

        Outputs JSON for easy parsing. Supports local devices and rtl_tcp connections.

        Note: rtl_433's -T flag is for timeout, NOT bias-t.
        Bias-t is enabled via the device string suffix :biast=1
        """
        rtl_433_path = get_tool_path('rtl_433') or 'rtl_433'

        # Build device argument with optional bias-t suffix
        # rtl_433 uses :biast=1 suffix on device string, not -T flag
        # (-T is timeout in rtl_433)
        device_arg = self._get_device_arg(device)
        if bias_t:
            device_arg = f'{device_arg}:biast=1'

        cmd = [
            rtl_433_path,
            '-d', device_arg,
            '-f', f'{frequency_mhz}M',
            '-F', 'json'
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', str(int(gain))])

        if ppm is not None and ppm != 0:
            cmd.extend(['-p', str(ppm)])

        return cmd

    def build_ais_command(
        self,
        device: SDRDevice,
        gain: float | None = None,
        bias_t: bool = False,
        tcp_port: int = 10110,
        udp_host: str | None = None,
        udp_port: int | None = None,
    ) -> list[str]:
        """
        Build AIS-catcher command for AIS vessel tracking.

        Uses AIS-catcher with TCP JSON output for real-time vessel data.
        AIS operates on 161.975 MHz and 162.025 MHz (handled automatically).
        """
        if device.is_network:
            raise ValueError(
                "AIS-catcher does not support rtl_tcp. "
                "For remote AIS, run AIS-catcher on the remote machine."
            )

        cmd = [
            'AIS-catcher',
            f'-d:{device.index}',  # Device index (colon format required)
            '-S', str(tcp_port), 'JSON_FULL', 'on',  # TCP server with full JSON output
            '-q',  # Quiet mode (less console output)
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-gr', 'TUNER', str(int(gain))])

        if bias_t:
            cmd.extend(['-gr', 'BIASTEE', 'on'])

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
        Build rtl_sdr command for raw I/Q capture.

        Outputs unsigned 8-bit I/Q pairs to stdout for waterfall display.
        """
        rtl_sdr_path = get_tool_path('rtl_sdr') or 'rtl_sdr'
        freq_hz = int(frequency_mhz * 1e6)

        cmd = [
            rtl_sdr_path,
            '-d', self._get_device_arg(device),
            '-f', str(freq_hz),
            '-s', str(sample_rate),
        ]

        if gain is not None and gain > 0:
            cmd.extend(['-g', str(gain)])

        if ppm is not None and ppm != 0:
            cmd.extend(['-p', str(ppm)])

        if bias_t:
            if _rtl_tool_supports_bias_t(rtl_sdr_path):
                cmd.append('-T')
            else:
                logger.warning("Bias-t requested but rtl_sdr does not support -T (RTL-SDR Blog drivers required).")

        # Output to stdout
        cmd.append('-')

        return cmd

    def get_capabilities(self) -> SDRCapabilities:
        """Return RTL-SDR capabilities."""
        return self.CAPABILITIES

    @classmethod
    def get_sdr_type(cls) -> SDRType:
        """Return SDR type."""
        return SDRType.RTL_SDR

