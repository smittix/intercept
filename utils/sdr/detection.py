"""
Multi-hardware SDR device detection.

Detects RTL-SDR devices via rtl_test and other SDR hardware via SoapySDR.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Optional

from .base import SDRCapabilities, SDRDevice, SDRType

logger = logging.getLogger(__name__)


def _check_tool(name: str) -> bool:
    """Check if a tool is available in PATH."""
    return shutil.which(name) is not None


def _get_capabilities_for_type(sdr_type: SDRType) -> SDRCapabilities:
    """Get default capabilities for an SDR type."""
    # Import here to avoid circular imports
    from .rtlsdr import RTLSDRCommandBuilder
    from .limesdr import LimeSDRCommandBuilder
    from .hackrf import HackRFCommandBuilder

    builders = {
        SDRType.RTL_SDR: RTLSDRCommandBuilder,
        SDRType.LIME_SDR: LimeSDRCommandBuilder,
        SDRType.HACKRF: HackRFCommandBuilder,
    }

    builder_class = builders.get(sdr_type)
    if builder_class:
        return builder_class.CAPABILITIES

    # Fallback generic capabilities
    return SDRCapabilities(
        sdr_type=sdr_type,
        freq_min_mhz=1.0,
        freq_max_mhz=6000.0,
        gain_min=0.0,
        gain_max=50.0,
        sample_rates=[2048000],
        supports_bias_t=False,
        supports_ppm=False,
        tx_capable=False,
    )


def _driver_to_sdr_type(driver: str) -> Optional[SDRType]:
    """Map SoapySDR driver name to SDRType."""
    mapping = {
        "rtlsdr": SDRType.RTL_SDR,
        "lime": SDRType.LIME_SDR,
        "limesdr": SDRType.LIME_SDR,
        "hackrf": SDRType.HACKRF,
        # Future support
        # 'uhd': SDRType.USRP,
        # 'bladerf': SDRType.BLADE_RF,
    }
    return mapping.get(driver.lower())


def detect_rtlsdr_devices() -> list[SDRDevice]:
    """
    Detect RTL-SDR devices using rtl_test.

    This uses the native rtl_test tool for best compatibility with
    existing RTL-SDR installations.
    """
    devices: list[SDRDevice] = []

    if not _check_tool("rtl_test"):
        logger.debug("rtl_test not found, skipping RTL-SDR detection")
        return devices

    try:
        import os
        import platform
        env = os.environ.copy()
        
        if platform.system() == 'Darwin':
            lib_paths = ['/usr/local/lib', '/opt/homebrew/lib']
            current_ld = env.get('DYLD_LIBRARY_PATH', '')
            env['DYLD_LIBRARY_PATH'] = ':'.join(lib_paths + [current_ld] if current_ld else lib_paths)
        result = subprocess.run(
            ['rtl_test', '-t'],
            capture_output=True,
            text=True,
            timeout=5,
            env=env 
        )
        output = result.stderr + result.stdout

        # Parse device info from rtl_test output
        # Format: "0:  Realtek, RTL2838UHIDIR, SN: 00000001"
        device_pattern = r"(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?$"

        from .rtlsdr import RTLSDRCommandBuilder

        for line in output.split("\n"):
            line = line.strip()
            match = re.match(device_pattern, line)
            if match:
                devices.append(
                    SDRDevice(
                        sdr_type=SDRType.RTL_SDR,
                        index=int(match.group(1)),
                        name=match.group(2).strip().rstrip(","),
                        serial=match.group(3) or "N/A",
                        driver="rtlsdr",
                        capabilities=RTLSDRCommandBuilder.CAPABILITIES,
                    )
                )

        # Fallback: if we found devices but couldn't parse details
        if not devices:
            found_match = re.search(r"Found (\d+) device", output)
            if found_match:
                count = int(found_match.group(1))
                for i in range(count):
                    devices.append(
                        SDRDevice(
                            sdr_type=SDRType.RTL_SDR,
                            index=i,
                            name=f"RTL-SDR Device {i}",
                            serial="Unknown",
                            driver="rtlsdr",
                            capabilities=RTLSDRCommandBuilder.CAPABILITIES,
                        )
                    )

    except subprocess.TimeoutExpired:
        logger.warning("rtl_test timed out")
    except Exception as e:
        logger.debug(f"RTL-SDR detection error: {e}")

    return devices


def detect_soapy_devices() -> list[SDRDevice]:
    """
    Detect SDR devices via SoapySDR.

    This detects LimeSDR, HackRF, USRP, BladeRF, and other SoapySDR-compatible
    devices. RTL-SDR devices may also appear here but we prefer the native
    detection for those.
    """
    devices: list[SDRDevice] = []

    if not _check_tool("SoapySDRUtil"):
        logger.debug("SoapySDRUtil not found, skipping SoapySDR detection")
        return devices

    try:
        result = subprocess.run(["SoapySDRUtil", "--find"], capture_output=True, text=True, timeout=10)

        # Parse SoapySDR output
        # Format varies but typically includes lines like:
        # "  driver = lime"
        # "  serial = 0009060B00123456"
        # "  label = LimeSDR Mini [USB 3.0] 0009060B00123456"

        current_device: dict = {}
        device_counts: dict[SDRType, int] = {}

        for line in result.stdout.split("\n"):
            line = line.strip()

            # Start of new device block
            if line.startswith("Found device"):
                if current_device.get("driver"):
                    _add_soapy_device(devices, current_device, device_counts)
                current_device = {}
                continue

            # Parse key = value pairs
            if " = " in line:
                key, value = line.split(" = ", 1)
                key = key.strip()
                value = value.strip()
                current_device[key] = value

        # Don't forget the last device
        if current_device.get("driver"):
            _add_soapy_device(devices, current_device, device_counts)

    except subprocess.TimeoutExpired:
        logger.warning("SoapySDRUtil timed out")
    except Exception as e:
        logger.debug(f"SoapySDR detection error: {e}")

    return devices


def _add_soapy_device(devices: list[SDRDevice], device_info: dict, device_counts: dict[SDRType, int]) -> None:
    """Add a device from SoapySDR detection to the list."""
    driver = device_info.get("driver", "").lower()
    sdr_type = _driver_to_sdr_type(driver)

    if not sdr_type:
        logger.debug(f"Unknown SoapySDR driver: {driver}")
        return

    # Skip RTL-SDR devices from SoapySDR (we use native detection)
    if sdr_type == SDRType.RTL_SDR:
        return

    # Track device index per type
    if sdr_type not in device_counts:
        device_counts[sdr_type] = 0

    index = device_counts[sdr_type]
    device_counts[sdr_type] += 1

    devices.append(
        SDRDevice(
            sdr_type=sdr_type,
            index=index,
            name=device_info.get("label", device_info.get("driver", "Unknown")),
            serial=device_info.get("serial", "N/A"),
            driver=driver,
            capabilities=_get_capabilities_for_type(sdr_type),
        )
    )


def detect_hackrf_devices() -> list[SDRDevice]:
    """
    Detect HackRF devices using native hackrf_info tool.

    Fallback for when SoapySDR is not available.
    """
    devices: list[SDRDevice] = []

    if not _check_tool("hackrf_info"):
        return devices

    try:
        result = subprocess.run(["hackrf_info"], capture_output=True, text=True, timeout=5)

        # Parse hackrf_info output
        # Look for "Serial number:" lines
        serial_pattern = r"Serial number:\s*(\S+)"
        from .hackrf import HackRFCommandBuilder

        serials_found = re.findall(serial_pattern, result.stdout)

        for i, serial in enumerate(serials_found):
            devices.append(
                SDRDevice(
                    sdr_type=SDRType.HACKRF,
                    index=i,
                    name=f"HackRF One",
                    serial=serial,
                    driver="hackrf",
                    capabilities=HackRFCommandBuilder.CAPABILITIES,
                )
            )

        # Fallback: check if any HackRF found without serial
        if not devices and "Found HackRF" in result.stdout:
            devices.append(
                SDRDevice(
                    sdr_type=SDRType.HACKRF,
                    index=0,
                    name="HackRF One",
                    serial="Unknown",
                    driver="hackrf",
                    capabilities=HackRFCommandBuilder.CAPABILITIES,
                )
            )

    except Exception as e:
        logger.debug(f"HackRF detection error: {e}")

    return devices


def detect_all_devices() -> list[SDRDevice]:
    """
    Detect all connected SDR devices across all supported hardware types.

    Returns a unified list of SDRDevice objects sorted by type and index.
    """
    devices: list[SDRDevice] = []

    # RTL-SDR via native tool (primary method)
    devices.extend(detect_rtlsdr_devices())

    # SoapySDR devices (LimeSDR, HackRF, etc.)
    soapy_devices = detect_soapy_devices()
    devices.extend(soapy_devices)

    # Native HackRF detection (fallback if SoapySDR didn't find it)
    hackrf_from_soapy = any(d.sdr_type == SDRType.HACKRF for d in soapy_devices)
    if not hackrf_from_soapy:
        devices.extend(detect_hackrf_devices())

    # Sort by type name, then index
    devices.sort(key=lambda d: (d.sdr_type.value, d.index))

    logger.info(f"Detected {len(devices)} SDR device(s)")
    for d in devices:
        logger.debug(f"  {d.sdr_type.value}:{d.index} - {d.name} (serial: {d.serial})")

    return devices


