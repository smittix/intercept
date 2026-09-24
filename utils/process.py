from __future__ import annotations

import atexit
import contextlib
import logging
import os
import platform
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

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


def close_process_pipes(process: subprocess.Popen) -> None:
    """Close stdin/stdout/stderr pipes on a subprocess to free file descriptors."""
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe:
            with contextlib.suppress(OSError):
                pipe.close()


def cleanup_all_processes() -> None:
    """Clean up all registered processes and flush DataStores on exit."""
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
            close_process_pipes(process)
        _spawned_processes.clear()

    # Stop DataStore cleanup timers and run final cleanup
    try:
        from utils.cleanup import cleanup_manager

        cleanup_manager.cleanup_now()
        cleanup_manager.stop()
    except Exception as e:
        logger.warning(f"Error during DataStore cleanup: {e}")


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
        close_process_pipes(process)
        unregister_process(process)
        return False

    try:
        process.terminate()
        process.wait(timeout=timeout)
        close_process_pipes(process)
        unregister_process(process)
        return True
    except subprocess.TimeoutExpired:
        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=3)
        close_process_pipes(process)
        unregister_process(process)
        return True
    except Exception as e:
        logger.warning(f"Error terminating process: {e}")
        close_process_pipes(process)
        return False


def _leads_group(process: subprocess.Popen) -> bool:
    """Whether a process leads its own group (started with start_new_session)."""
    try:
        return os.getpgid(process.pid) == process.pid
    except (ProcessLookupError, OSError):
        return False


def _signal_process(process: subprocess.Popen, sig: int, group: bool) -> None:
    """Signal a process, or its whole group if it leads one, so a decoder's
    children go with it."""
    with contextlib.suppress(ProcessLookupError, OSError):
        if group:
            os.killpg(process.pid, sig)
        else:
            process.send_signal(sig)


def terminate_together(
    processes: list[subprocess.Popen | None], timeout: float = 2.0, kill_timeout: float = 2.0
) -> list[subprocess.Popen]:
    """Stop a mode's processes together, and wait until they are gone.

    Every process gets SIGTERM at once and they share one deadline, rather
    than each waiting its turn. Survivors get SIGKILL, and are waited for:
    until a decoder has actually exited it still holds the SDR, and
    releasing the device before then lets the next start fail as busy.
    Returns the processes still alive afterwards (normally none).
    """
    procs = [p for p in processes if p is not None]
    # Noted before signalling: once a leader has exited, its group can no
    # longer be looked up from it, though its children may still be running.
    leaders = {p.pid for p in procs if p.poll() is None and _leads_group(p)}
    for process in procs:
        if process.poll() is None:
            _signal_process(process, signal.SIGTERM, process.pid in leaders)

    deadline = time.monotonic() + timeout
    for process in procs:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=max(0.0, deadline - time.monotonic()))

    stubborn = [p for p in procs if p.poll() is None]
    for process in stubborn:
        _signal_process(process, signal.SIGKILL, process.pid in leaders)
    # A leader that obeyed SIGTERM can leave children that did not
    for pgid in leaders:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pgid, signal.SIGKILL)
    deadline = time.monotonic() + kill_timeout
    for process in stubborn:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=max(0.0, deadline - time.monotonic()))

    for process in procs:
        close_process_pipes(process)
        unregister_process(process)
    survivors = [p for p in procs if p.poll() is None]
    for process in survivors:
        logger.warning(f"Process {process.pid} did not exit after SIGKILL (stuck in the kernel, e.g. on USB)")
    return survivors


# Register cleanup handlers
atexit.register(cleanup_all_processes)


# Handle signals for graceful shutdown
def _signal_handler(signum, frame):
    """Handle termination signals.

    Keep this minimal — logging and lock acquisition in signal handlers
    can deadlock when another thread holds the logging or process lock.
    Process cleanup is handled by the atexit handler registered above.
    """
    import sys

    if signum == signal.SIGINT:
        raise KeyboardInterrupt()
    sys.exit(0)


# Only register signal handlers when running standalone (not under gunicorn).
# Gunicorn manages its own SIGINT/SIGTERM handling for graceful shutdown;
# overriding those signals causes KeyboardInterrupt in the wrong context.
def _is_under_gunicorn():
    """Check if we're running inside a gunicorn worker."""
    try:
        import gunicorn.arbiter  # noqa: F401

        # If gunicorn is importable AND we were invoked via gunicorn, the
        # arbiter will have installed its own signal handlers already.
        # Check the current SIGTERM handler — if it's not the default,
        # gunicorn (or another manager) owns signals.
        current = signal.getsignal(signal.SIGTERM)
        return current not in (signal.SIG_DFL, signal.SIG_IGN, None)
    except ImportError:
        return False


if not _is_under_gunicorn():
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
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            subprocess.run(["pkill", "-9", proc_name], capture_output=True)


_DUMP1090_PID_FILE = Path(__file__).resolve().parent.parent / "instance" / "dump1090.pid"


def write_dump1090_pid(pid: int) -> None:
    """Write the PID of an app-spawned dump1090 process to a PID file."""
    try:
        _DUMP1090_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DUMP1090_PID_FILE.write_text(str(pid))
        logger.debug(f"Wrote dump1090 PID file: {pid}")
    except OSError as e:
        logger.warning(f"Failed to write dump1090 PID file: {e}")


def clear_dump1090_pid() -> None:
    """Remove the dump1090 PID file."""
    try:
        _DUMP1090_PID_FILE.unlink(missing_ok=True)
        logger.debug("Cleared dump1090 PID file")
    except OSError as e:
        logger.warning(f"Failed to clear dump1090 PID file: {e}")


def _is_dump1090_process(pid: int) -> bool:
    """Check if the given PID is actually a dump1090/readsb process."""
    try:
        if platform.system() == "Linux":
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
                return "dump1090" in cmdline or "readsb" in cmdline
        # macOS or fallback
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5)
        comm = result.stdout.strip()
        return "dump1090" in comm or "readsb" in comm
    except Exception:
        return False


def cleanup_stale_dump1090() -> None:
    """Kill a stale app-spawned dump1090 using the PID file.

    Safe no-op if no PID file exists, process is dead, or PID was reused
    by another program.
    """
    if not _DUMP1090_PID_FILE.exists():
        return

    try:
        pid = int(_DUMP1090_PID_FILE.read_text().strip())
    except (ValueError, OSError) as e:
        logger.warning(f"Invalid dump1090 PID file: {e}")
        clear_dump1090_pid()
        return

    # Verify this PID is still a dump1090/readsb process
    if not _is_dump1090_process(pid):
        logger.debug(f"PID {pid} is not dump1090/readsb (dead or reused), removing stale PID file")
        clear_dump1090_pid()
        return

    # Kill the process group
    logger.info(f"Killing stale app-spawned dump1090 (PID {pid})")
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        # Brief wait for graceful shutdown
        for _ in range(10):
            try:
                os.kill(pid, 0)  # Check if still alive
                time.sleep(0.2)
            except OSError:
                break
        else:
            # Still alive, force kill
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGKILL)
    except OSError as e:
        logger.debug(f"Error killing stale dump1090 PID {pid}: {e}")

    clear_dump1090_pid()


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
