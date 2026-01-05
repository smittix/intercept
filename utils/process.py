from __future__ import annotations

import atexit
import logging
import signal
import subprocess
import re
import threading
import time
from typing import Any, Callable

from .dependencies import check_tool

logger = logging.getLogger("intercept.process")

# Track all spawned processes for cleanup
_spawned_processes: list[subprocess.Popen] = []
_process_lock = threading.Lock()


def register_process(process: subprocess.Popen) -> None:
    """Register a spawned process for cleanup on exit."""
    with _process_lock:
        _spawned_processes.append(process)


def unregister_process(process: subprocess.Popen) -> None:
    """Unregister a process from cleanup list."""
    with _process_lock:
        if process in _spawned_processes:
            _spawned_processes.remove(process)


def cleanup_all_processes() -> None:
    """Clean up all registered processes on exit."""
    logger.info("Cleaning up all spawned processes...")
    with _process_lock:
        for process in _spawned_processes:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception as e:
                    logger.warning(f"Error cleaning up process: {e}")
        _spawned_processes.clear()


def safe_terminate(process: subprocess.Popen | None, timeout: float = 2.0) -> bool:
    """
    Safely terminate a process.

    Args:
        process: Process to terminate
        timeout: Seconds to wait before killing

    Returns:
        True if process was terminated, False if already dead or None
    """
    if not process:
        return False

    if process.poll() is not None:
        # Already dead
        unregister_process(process)
        return False

    try:
        process.terminate()
        process.wait(timeout=timeout)
        unregister_process(process)
        return True
    except subprocess.TimeoutExpired:
        process.kill()
        unregister_process(process)
        return True
    except Exception as e:
        logger.warning(f"Error terminating process: {e}")
        return False


# Register cleanup handlers
atexit.register(cleanup_all_processes)


# Handle signals for graceful shutdown
def _signal_handler(signum, frame):
    """Handle termination signals."""
    import sys

    logger.info(f"Received signal {signum}, cleaning up...")
    cleanup_all_processes()
    # Re-raise KeyboardInterrupt for SIGINT so Flask can handle shutdown
    if signum == signal.SIGINT:
        raise KeyboardInterrupt()
    sys.exit(0)


# Only register signal handlers if we're not in a thread
try:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
except ValueError:
    # Can't set signal handlers from a thread
    pass


def cleanup_stale_processes() -> None:
    """Kill any stale processes from previous runs (but not system services)."""
    # Note: dump1090 is NOT included here as users may run it as a system service
    processes_to_kill = ["rtl_adsb", "rtl_433", "multimon-ng", "rtl_fm"]
    for proc_name in processes_to_kill:
        try:
            subprocess.run(["pkill", "-9", proc_name], capture_output=True)
        except (subprocess.SubprocessError, OSError):
            pass


def is_valid_mac(mac: str | None) -> bool:
    """Validate MAC address format."""
    if not mac:
        return False
    return bool(re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", mac))


def is_valid_channel(channel: str | int | None) -> bool:
    """Validate WiFi channel number."""
    try:
        ch = int(channel)  # type: ignore[arg-type]
        return 1 <= ch <= 200
    except (ValueError, TypeError):
        return False


def detect_devices() -> list[dict[str, Any]]:
    """Detect RTL-SDR devices."""
    devices: list[dict[str, Any]] = []

    if not check_tool("rtl_test"):
        return devices

    try:
        result = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=5)
        output = result.stderr + result.stdout

        # Parse device info
        device_pattern = r"(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?$"

        for line in output.split("\n"):
            line = line.strip()
            match = re.match(device_pattern, line)
            if match:
                devices.append(
                    {
                        "index": int(match.group(1)),
                        "name": match.group(2).strip().rstrip(","),
                        "serial": match.group(3) or "N/A",
                    }
                )

        if not devices:
            found_match = re.search(r"Found (\d+) device", output)
            if found_match:
                count = int(found_match.group(1))
                for i in range(count):
                    devices.append({"index": i, "name": f"RTL-SDR Device {i}", "serial": "Unknown"})

    except Exception:
        pass

    return devices
