"""
Multi-hardware SDR device detection.

Detects RTL-SDR devices via rtl_test and other SDR hardware via SoapySDR.
"""

from __future__ import annotations

import contextlib
import logging
import re
import signal
import subprocess
import time

from utils.dependencies import get_tool_path

from .base import SDRCapabilities, SDRDevice, SDRType

logger = logging.getLogger(__name__)

# Cache HackRF detection results so polling endpoints don't repeatedly run
# hackrf_info while the device is actively streaming in SubGHz mode.
_hackrf_cache: list[SDRDevice] = []
_hackrf_cache_ts: float = 0.0
_HACKRF_CACHE_TTL_SECONDS = 3.0

# Cache all-device detection results.  Multiple endpoints call
# detect_all_devices() on the same page load (e.g. /devices and /adsb/tools
# both trigger it from DOMContentLoaded).  On a Pi the subprocess calls
# (rtl_test, SoapySDRUtil, hackrf_info) each take seconds and block the
# single gevent worker, serialising every other request behind them.
# A cache avoids duplicate subprocess storms and prevents USB contention.
_all_devices_cache: list[SDRDevice] = []
_all_devices_cache_ts: float = 0.0
_ALL_DEVICES_CACHE_TTL_SECONDS = 30.0


def _hackrf_probe_blocked() -> bool:
    """Return True when probing HackRF would interfere with an active stream."""
    try:
        from utils.subghz import get_subghz_manager

        return get_subghz_manager().active_mode in {"rx", "decode", "tx", "sweep"}
    except Exception:
        return False


def _is_sdr_in_use() -> bool:
    """Return True when probing SDR devices would interfere with an active stream."""
    try:
        import sys

        app_mod = sys.modules.get("app")
        if app_mod:
            if hasattr(app_mod, "get_sdr_device_status") and app_mod.get_sdr_device_status():
                return True
            for attr in [
                "sensor_process",
                "current_process",
                "adsb_process",
                "rtlamr_process",
                "ais_process",
                "acars_process",
                "vdl2_process",
                "aprs_process",
                "radiosonde_process",
                "morse_process",
            ]:
                p = getattr(app_mod, attr, None)
                if p is not None and (p.poll() is None if hasattr(p, "poll") else True):
                    return True

        # Check companion processes (e.g. rtl_tcp in rtlamr, audio streaming in listening post)
        rtlamr_mod = sys.modules.get("routes.rtlamr")
        if rtlamr_mod and getattr(rtlamr_mod, "rtl_tcp_process", None):
            p = rtlamr_mod.rtl_tcp_process
            if p is not None and p.poll() is None:
                return True

        lp_mod = sys.modules.get("routes.listening_post")
        if lp_mod and getattr(lp_mod, "audio_running", False):
            return True
    except Exception:
        pass
    return _hackrf_probe_blocked()


def _get_capabilities_for_type(sdr_type: SDRType) -> SDRCapabilities:
    """Get default capabilities for an SDR type."""
    # Import here to avoid circular imports
    from .airspy import AirspyCommandBuilder
    from .bladerf import BladeRFCommandBuilder
    from .hackrf import HackRFCommandBuilder
    from .hydrasdr import HydraSDRCommandBuilder
    from .limesdr import LimeSDRCommandBuilder
    from .rtlsdr import RTLSDRCommandBuilder
    from .sdrplay import SDRPlayCommandBuilder
    from .usrp import USRPCommandBuilder

    builders = {
        SDRType.RTL_SDR: RTLSDRCommandBuilder,
        SDRType.LIME_SDR: LimeSDRCommandBuilder,
        SDRType.HACKRF: HackRFCommandBuilder,
        SDRType.AIRSPY: AirspyCommandBuilder,
        SDRType.SDRPLAY: SDRPlayCommandBuilder,
        SDRType.USRP: USRPCommandBuilder,
        SDRType.BLADE_RF: BladeRFCommandBuilder,
        SDRType.HYDRA_SDR: HydraSDRCommandBuilder,
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


def _driver_to_sdr_type(driver: str) -> SDRType | None:
    """Map SoapySDR driver name to SDRType."""
    mapping = {
        "rtlsdr": SDRType.RTL_SDR,
        "lime": SDRType.LIME_SDR,
        "limesdr": SDRType.LIME_SDR,
        "hackrf": SDRType.HACKRF,
        "airspy": SDRType.AIRSPY,
        "airspyhf": SDRType.AIRSPY,  # Airspy HF+ uses same builder
        "sdrplay": SDRType.SDRPLAY,
        "uhd": SDRType.USRP,
        "bladerf": SDRType.BLADE_RF,
        "hydrasdr": SDRType.HYDRA_SDR,
    }
    return mapping.get(driver.lower())


def detect_rtlsdr_devices() -> list[SDRDevice]:
    """
    Detect RTL-SDR devices using rtl_test.

    This uses the native rtl_test tool for best compatibility with
    existing RTL-SDR installations.
    """
    devices: list[SDRDevice] = []

    rtl_test_path = get_tool_path("rtl_test")
    if not rtl_test_path:
        logger.debug("rtl_test not found, skipping RTL-SDR detection")
        return devices

    try:
        import os
        import platform

        env = os.environ.copy()

        if platform.system() == "Darwin":
            lib_paths = ["/usr/local/lib", "/opt/homebrew/lib"]
            current_ld = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = ":".join(lib_paths + [current_ld] if current_ld else lib_paths)
        proc = subprocess.Popen(
            [rtl_test_path, "-t"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        try:
            # rtl_test prints device list immediately on startup, then continues
            # its tuner benchmark indefinitely. Give it 0.3s to emit the device
            # list, then send SIGINT to cleanly exit without blocking or stalling USB.
            time.sleep(0.3)
            with contextlib.suppress(OSError):
                proc.send_signal(signal.SIGINT)
            stdout, stderr = proc.communicate(timeout=1.0)
            output = (stderr or "") + (stdout or "")
        except subprocess.TimeoutExpired:
            logger.warning("rtl_test timed out; stopping gracefully")
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
                stdout, stderr = proc.communicate()
            output = (stderr or "") + (stdout or "")

        # Parse device info from rtl_test output
        # Format: "0:  Realtek, RTL2838UHIDIR, SN: 00000001"
        # Require a non-empty serial to avoid matching malformed lines like "SN:".
        device_pattern = r"(\d+):\s+(.+?),\s*SN:\s*(\S+)\s*$"

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
                        serial=match.group(3),
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


def _find_soapy_util() -> str | None:
    """Find SoapySDR utility command (name varies by distribution)."""
    # Try different command names used across distributions
    for cmd in ["SoapySDRUtil", "soapy_sdr_util", "soapysdr-util"]:
        tool_path = get_tool_path(cmd)
        if tool_path:
            return tool_path
    return None


def _get_soapy_env() -> dict:
    """Get environment variables needed for SoapySDR on macOS.

    On macOS with Homebrew, SoapySDR modules are installed in paths that
    require SOAPY_SDR_ROOT or DYLD_LIBRARY_PATH to be set. This fixes
    detection issues where modules like SoapyHackRF are installed but
    not found by SoapySDRUtil.

    See: https://github.com/smittix/intercept/issues/77
    """
    import os
    import platform

    env = os.environ.copy()

    if platform.system() == "Darwin":
        # Homebrew paths for Apple Silicon and Intel Macs
        homebrew_paths = ["/opt/homebrew", "/usr/local"]
        lib_paths = []

        for base in homebrew_paths:
            lib_path = f"{base}/lib"
            if os.path.isdir(lib_path):
                lib_paths.append(lib_path)

        if lib_paths:
            current_dyld = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = ":".join(lib_paths + ([current_dyld] if current_dyld else []))

        # Set SOAPY_SDR_ROOT if we found Homebrew installation
        for base in homebrew_paths:
            if os.path.isdir(f"{base}/lib/SoapySDR"):
                env["SOAPY_SDR_ROOT"] = base
                break

    return env


def detect_soapy_devices(skip_types: set[SDRType] | None = None) -> list[SDRDevice]:
    """
    Detect SDR devices via SoapySDR.

    This detects LimeSDR, HackRF, Airspy, and other SoapySDR-compatible devices.

    Args:
        skip_types: Set of SDRType values to skip (e.g., if already found via native detection)
    """
    devices: list[SDRDevice] = []
    skip_types = skip_types or set()

    soapy_cmd = _find_soapy_util()
    if not soapy_cmd:
        logger.debug("SoapySDR utility not found, skipping SoapySDR detection")
        return devices

    try:
        # Use macOS-aware environment to find Homebrew-installed modules
        env = _get_soapy_env()
        proc = subprocess.Popen(
            [soapy_cmd, "--find"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("SoapySDRUtil timed out; terminating gracefully")
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                stdout, _ = proc.communicate(timeout=1.5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
                stdout, _ = proc.communicate()
        output = stdout or ""

        # Parse SoapySDR output
        # Format varies but typically includes lines like:
        # "  driver = lime"
        # "  serial = 0009060B00123456"
        # "  label = LimeSDR Mini [USB 3.0] 0009060B00123456"

        current_device: dict = {}
        device_counts: dict[SDRType, int] = {}

        for line in output.split("\n"):
            line = line.strip()

            # Start of new device block
            if line.startswith("Found device"):
                if current_device.get("driver"):
                    _add_soapy_device(devices, current_device, device_counts, skip_types)
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
            _add_soapy_device(devices, current_device, device_counts, skip_types)

    except Exception as e:
        logger.debug(f"SoapySDR detection error: {e}")

    return devices


def _add_soapy_device(
    devices: list[SDRDevice], device_info: dict, device_counts: dict[SDRType, int], skip_types: set[SDRType]
) -> None:
    """Add a device from SoapySDR detection to the list."""
    driver = device_info.get("driver", "").lower()
    sdr_type = _driver_to_sdr_type(driver)

    if not sdr_type:
        logger.debug(f"Unknown SoapySDR driver: {driver}")
        return

    # Skip device types that were already found via native detection
    if sdr_type in skip_types:
        logger.debug(f"Skipping {driver} from SoapySDR (already found via native detection)")
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
    global _hackrf_cache, _hackrf_cache_ts
    now = time.time()

    # While HackRF is actively streaming in SubGHz mode, skip probe calls.
    # Re-running hackrf_info during active RX/TX can disrupt the USB stream.
    if _hackrf_probe_blocked():
        return list(_hackrf_cache)

    if _hackrf_cache and (now - _hackrf_cache_ts) < _HACKRF_CACHE_TTL_SECONDS:
        return list(_hackrf_cache)

    devices: list[SDRDevice] = []

    hackrf_info_path = get_tool_path("hackrf_info")
    if not hackrf_info_path:
        _hackrf_cache = devices
        _hackrf_cache_ts = now
        return devices

    try:
        result = subprocess.run([hackrf_info_path], capture_output=True, text=True, timeout=5)

        # Combine stdout + stderr: newer firmware may print to stderr,
        # and hackrf_info may exit non-zero when device is briefly busy
        # but still output valid info.
        output = f"{result.stdout or ''}\n{result.stderr or ''}"

        # Parse hackrf_info output
        # Extract board name from "Board ID Number: X (Name)" and serial
        from .hackrf import HackRFCommandBuilder

        serial_pattern = re.compile(
            r"^\s*Serial\s+number:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        )
        board_pattern = re.compile(
            r"Board\s+ID\s+Number:\s*\d+\s*\(([^)]+)\)",
            re.IGNORECASE,
        )

        serials_found = []
        for raw in serial_pattern.findall(output):
            # Normalise legacy formats like "0x1234 5678" to plain hex.
            serial = re.sub(r"0x", "", raw, flags=re.IGNORECASE)
            serial = re.sub(r"[^0-9A-Fa-f]", "", serial)
            if serial:
                serials_found.append(serial)
        boards_found = board_pattern.findall(output)

        for i, serial in enumerate(serials_found):
            board_name = boards_found[i] if i < len(boards_found) else "HackRF"
            devices.append(
                SDRDevice(
                    sdr_type=SDRType.HACKRF,
                    index=i,
                    name=board_name,
                    serial=serial,
                    driver="hackrf",
                    capabilities=HackRFCommandBuilder.CAPABILITIES,
                )
            )

        # Fallback: check if any HackRF found without serial
        if not devices and re.search(r"Found\s+HackRF", output, re.IGNORECASE):
            board_match = board_pattern.search(output)
            board_name = board_match.group(1) if board_match else "HackRF"
            devices.append(
                SDRDevice(
                    sdr_type=SDRType.HACKRF,
                    index=0,
                    name=board_name,
                    serial="Unknown",
                    driver="hackrf",
                    capabilities=HackRFCommandBuilder.CAPABILITIES,
                )
            )

    except Exception as e:
        logger.debug(f"HackRF detection error: {e}")

    _hackrf_cache = list(devices)
    _hackrf_cache_ts = now
    return devices


def probe_rtlsdr_device(device_index: int) -> str | None:
    """Probe whether an RTL-SDR device is available at the USB level.

    Runs a quick ``rtl_test`` invocation targeting a single device to
    check for USB claim errors that indicate the device is held by an
    external process (or a stale handle from a previous crash).

    Args:
        device_index: The RTL-SDR device index to probe.

    Returns:
        An error message string if the device cannot be opened,
        or ``None`` if the device is available.
    """
    rtl_test_path = get_tool_path("rtl_test")
    if not rtl_test_path:
        # Can't probe without rtl_test — let the caller proceed and
        # surface errors from the actual decoder process instead.
        return None

    try:
        import os
        import platform

        env = os.environ.copy()

        if platform.system() == "Darwin":
            lib_paths = ["/usr/local/lib", "/opt/homebrew/lib"]
            current_ld = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = ":".join(lib_paths + [current_ld] if current_ld else lib_paths)

        # Use Popen with early termination instead of run() with full timeout.
        # rtl_test prints device info to stderr quickly, then keeps running
        # its test loop. We kill it as soon as we see success or failure.
        proc = subprocess.Popen(
            [rtl_test_path, "-d", str(device_index), "-t"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
        )

        import select

        error_found = False
        device_found = False
        deadline = time.monotonic() + 3.0

        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Wait for stderr output with timeout
                ready, _, _ = select.select([proc.stderr], [], [], min(remaining, 0.1))
                if ready:
                    line = proc.stderr.readline()
                    if not line:
                        break  # EOF — process closed stderr
                    # Check for no-device messages first (before success check,
                    # since "No supported devices found" also contains "Found" + "device")
                    if "no supported devices" in line.lower() or "no matching devices" in line.lower():
                        error_found = True
                        break
                    if "usb_claim_interface" in line or "Failed to open" in line:
                        error_found = True
                        break
                    if "Found" in line and "device" in line.lower():
                        # Device opened successfully — no need to wait longer
                        device_found = True
                        break
                if proc.poll() is not None:
                    break  # Process exited
            if not device_found and not error_found and proc.poll() is not None and proc.returncode != 0:
                # rtl_test exited with error and we never saw a success message
                error_found = True
        finally:
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        proc.terminate()
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(OSError):
                            proc.kill()
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=0.5)
            if device_found:
                # Allow the kernel to fully release the USB interface
                # before the caller opens the device with dump1090/rtl_fm/etc.
                time.sleep(0.5)

        if error_found:
            logger.warning(f"RTL-SDR device {device_index} USB probe failed: device busy or unavailable")
            return (
                f"SDR device {device_index} is not available — "
                f"check that the SDR device is connected and not in use by another process."
            )

    except Exception as e:
        logger.debug(f"RTL-SDR probe error for device {device_index}: {e}")

    return None


def detect_all_devices(force: bool = False) -> list[SDRDevice]:
    """
    Detect all connected SDR devices across all supported hardware types.

    Results are cached to avoid duplicate subprocess storms.
    If an SDR is actively in use, cached results are returned to prevent
    hardware contention and streaming disruptions.

    Args:
        force: Bypass the cache and re-probe hardware.

    Returns a unified list of SDRDevice objects sorted by type and index.
    """
    global _all_devices_cache, _all_devices_cache_ts

    now = time.time()
    cache_valid = bool(_all_devices_cache and (now - _all_devices_cache_ts) < _ALL_DEVICES_CACHE_TTL_SECONDS)

    # When an SDR device is actively in use or streaming, avoid re-probing hardware
    # which can cause USB bus contention and crash active captures.
    if _all_devices_cache and (_is_sdr_in_use() or (not force and cache_valid)):
        logger.debug("Returning cached device list (%d device(s))", len(_all_devices_cache))
        return list(_all_devices_cache)

    if not force and cache_valid:
        logger.debug("Returning cached device list (%d device(s))", len(_all_devices_cache))
        return list(_all_devices_cache)

    devices: list[SDRDevice] = []
    skip_in_soapy: set[SDRType] = set()

    # RTL-SDR via native tool (primary method)
    rtlsdr_devices = detect_rtlsdr_devices()
    devices.extend(rtlsdr_devices)
    if rtlsdr_devices:
        skip_in_soapy.add(SDRType.RTL_SDR)

    # Native HackRF detection (primary method)
    hackrf_devices = detect_hackrf_devices()
    devices.extend(hackrf_devices)
    if hackrf_devices:
        skip_in_soapy.add(SDRType.HACKRF)

    # SoapySDR devices (LimeSDR, Airspy, and fallback for HackRF/RTL-SDR if native failed)
    soapy_devices = detect_soapy_devices(skip_types=skip_in_soapy)
    devices.extend(soapy_devices)

    # Sort by type name, then index
    devices.sort(key=lambda d: (d.sdr_type.value, d.index))

    logger.info(f"Detected {len(devices)} SDR device(s)")
    for d in devices:
        logger.debug(f"  {d.sdr_type.value}:{d.index} - {d.name} (serial: {d.serial})")

    # Update cache
    _all_devices_cache = list(devices)
    _all_devices_cache_ts = time.time()

    return devices


def get_cached_devices() -> list[SDRDevice] | None:
    """Return the cached device list without probing hardware.

    Returns None if no cached data is available (never probed).
    """
    if _all_devices_cache_ts == 0.0:
        return None
    return list(_all_devices_cache)


def invalidate_device_cache() -> None:
    """Clear the all-devices cache so the next call re-probes hardware."""
    global _all_devices_cache, _all_devices_cache_ts
    _all_devices_cache = []
    _all_devices_cache_ts = 0.0
