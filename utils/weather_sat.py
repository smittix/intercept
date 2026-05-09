"""Weather satellite decoder focused on Meteor LRPT workflows.

Provides automated capture and decoding of weather imagery using SatDump.

Active satellites:
    - Meteor-M2-3: 137.900 MHz (LRPT)
    - Meteor-M2-4: 137.900 MHz (LRPT)

Legacy NOAA APT entries remain in ``WEATHER_SATELLITES`` for compatibility
and historical metadata, but they are no longer active operational targets.
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import select
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from utils.logging import get_logger
from utils.process import register_process, safe_terminate

logger = get_logger('intercept.weather_sat')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_OFFLINE_INPUT_DIRS = (
    PROJECT_ROOT / 'data',
    PROJECT_ROOT / 'instance' / 'ground_station' / 'recordings',
)


# Weather satellite definitions.
# NOAA APT entries are retained as inactive compatibility metadata.
WEATHER_SATELLITES = {
    'NOAA-15': {
        'name': 'NOAA 15',
        'frequency': 137.620,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-15',
        'description': 'NOAA-15 APT (decommissioned Aug 2025)',
        'active': False,
    },
    'NOAA-18': {
        'name': 'NOAA 18',
        'frequency': 137.9125,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-18',
        'description': 'NOAA-18 APT (decommissioned Jun 2025)',
        'active': False,
    },
    'NOAA-19': {
        'name': 'NOAA 19',
        'frequency': 137.100,
        'mode': 'APT',
        'pipeline': 'noaa_apt',
        'tle_key': 'NOAA-19',
        'description': 'NOAA-19 APT (decommissioned Aug 2025)',
        'active': False,
    },
    'METEOR-M2-3': {
        'name': 'Meteor-M2-3',
        'frequency': 137.900,
        'mode': 'LRPT',
        'pipeline': 'meteor_m2-x_lrpt',
        'tle_key': 'METEOR-M2-3',
        'description': 'Meteor-M2-3 LRPT (digital color imagery)',
        'active': True,
    },
    'METEOR-M2-4': {
        'name': 'Meteor-M2-4',
        'frequency': 137.900,
        'mode': 'LRPT',
        'pipeline': 'meteor_m2-x_lrpt',
        'tle_key': 'METEOR-M2-4',
        'description': 'Meteor-M2-4 LRPT (digital color imagery)',
        'active': True,
    },
    'METEOR-M2-4-80K': {
        'name': 'Meteor-M2-4 (80k)',
        'frequency': 137.900,
        'mode': 'LRPT',
        'pipeline': 'meteor_m2-x_lrpt_80k',
        'tle_key': 'METEOR-M2-4',
        'description': 'Meteor-M2-4 LRPT 80k baud (fallback symbol rate)',
        'active': True,
    },
}

# Default sample rate for weather satellite reception
try:
    from config import WEATHER_SAT_SAMPLE_RATE as _configured_rate
    DEFAULT_SAMPLE_RATE = _configured_rate
except ImportError:
    DEFAULT_SAMPLE_RATE = 2400000  # 2.4 MHz — minimum for Meteor LRPT


@dataclass
class WeatherSatImage:
    """Decoded weather satellite image."""
    filename: str
    path: Path
    satellite: str
    mode: str  # APT or LRPT
    timestamp: datetime
    frequency: float
    size_bytes: int = 0
    product: str = ''  # e.g. 'RGB', 'Thermal', 'Channel 1'

    def to_dict(self) -> dict:
        return {
            'filename': self.filename,
            'satellite': self.satellite,
            'mode': self.mode,
            'timestamp': self.timestamp.isoformat(),
            'frequency': self.frequency,
            'size_bytes': self.size_bytes,
            'product': self.product,
            'url': f'/weather-sat/images/{self.filename}',
        }


@dataclass
class CaptureProgress:
    """Weather satellite capture/decode progress update."""
    status: str  # 'idle', 'capturing', 'decoding', 'complete', 'error'
    satellite: str = ''
    frequency: float = 0.0
    mode: str = ''
    message: str = ''
    progress_percent: int = 0
    elapsed_seconds: int = 0
    image: WeatherSatImage | None = None
    log_type: str = ''       # 'info', 'debug', 'progress', 'error', 'signal', 'save', 'warning'
    capture_phase: str = ''  # 'tuning', 'listening', 'signal_detected', 'decoding', 'complete', 'error'

    def to_dict(self) -> dict:
        result = {
            'type': 'weather_sat_progress',
            'status': self.status,
            'satellite': self.satellite,
            'frequency': self.frequency,
            'mode': self.mode,
            'message': self.message,
            'progress': self.progress_percent,
            'elapsed_seconds': self.elapsed_seconds,
            'log_type': self.log_type,
            'capture_phase': self.capture_phase,
        }
        if self.image:
            result['image'] = self.image.to_dict()
        return result


class WeatherSatDecoder:
    """Weather satellite decoder using SatDump CLI.

    Manages live SDR capture and offline decode for the active Meteor LRPT
    workflow, while preserving compatibility with older weather-sat metadata.
    """

    def __init__(self, output_dir: str | Path | None = None):
        self._process: subprocess.Popen | None = None
        self._running = False
        self._lock = threading.Lock()
        self._pty_lock = threading.Lock()
        self._images_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._callback: Callable[[CaptureProgress], None] | None = None
        self._output_dir = Path(output_dir) if output_dir else Path('data/weather_sat')
        self._images: list[WeatherSatImage] = []
        self._reader_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._pty_master_fd: int | None = None
        self._current_satellite: str = ''
        self._current_frequency: float = 0.0
        self._current_mode: str = ''
        self._capture_start_time: float = 0
        self._device_index: int = -1
        self._capture_output_dir: Path | None = None
        self._on_complete_callback: Callable[[], None] | None = None
        self._capture_phase: str = 'idle'
        self._last_error_message: str = ''
        self._last_process_returncode: int | None = None

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Detect available decoder
        self._decoder = self._detect_decoder()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def decoder_available(self) -> str | None:
        """Return name of available decoder or None."""
        return self._decoder

    @property
    def current_satellite(self) -> str:
        return self._current_satellite

    @property
    def current_frequency(self) -> float:
        return self._current_frequency

    @property
    def device_index(self) -> int:
        """Return current device index."""
        return self._device_index

    def _detect_decoder(self) -> str | None:
        """Detect which weather satellite decoder is available."""
        if shutil.which('satdump'):
            logger.info("SatDump decoder detected")
            return 'satdump'

        logger.warning(
            "SatDump not found. Install SatDump for weather satellite decoding. "
            "See: https://github.com/SatDump/SatDump"
        )
        return None

    def _close_pty(self) -> None:
        """Close the PTY master fd in a thread-safe manner."""
        with self._pty_lock:
            if self._pty_master_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._pty_master_fd)
                self._pty_master_fd = None

    def set_callback(self, callback: Callable[[CaptureProgress], None]) -> None:
        """Set callback for capture progress updates."""
        self._callback = callback

    def set_on_complete(self, callback: Callable[[], None]) -> None:
        """Set callback invoked when capture process ends (for SDR release)."""
        self._on_complete_callback = callback

    def start_from_file(
        self,
        satellite: str,
        input_file: str | Path,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> tuple[bool, str | None]:
        """Start weather satellite decode from a pre-recorded IQ/WAV file.

        No SDR hardware is required — SatDump runs in offline mode.

        Args:
            satellite: Satellite key (for example ``'METEOR-M2-3'``)
            input_file: Path to IQ baseband or WAV audio file
            sample_rate: Sample rate of the recording in Hz

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        with self._lock:
            if self._running:
                return True, None

            if not self._decoder:
                logger.error("No weather satellite decoder available")
                msg = 'SatDump not installed. Build from source or install via package manager.'
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=msg,
                ))
                return False, msg

            sat_info = WEATHER_SATELLITES.get(satellite)
            if not sat_info:
                logger.error(f"Unknown satellite: {satellite}")
                msg = f'Unknown satellite: {satellite}'
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=msg,
                ))
                return False, msg

            input_path = Path(input_file)

            # Security: restrict offline decode inputs to application-owned
            # capture directories so external paths cannot be injected.
            try:
                resolved = input_path.resolve()
                if not any(resolved.is_relative_to(base) for base in ALLOWED_OFFLINE_INPUT_DIRS):
                    logger.warning(f"Path traversal blocked in start_from_file: {input_file}")
                    msg = 'Input file must be under INTERCEPT data or ground-station recordings'
                    self._emit_progress(CaptureProgress(
                        status='error',
                        message=msg,
                    ))
                    return False, msg
            except (OSError, ValueError):
                msg = 'Invalid file path'
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=msg,
                ))
                return False, msg

            if not input_path.is_file():
                logger.error(f"Input file not found: {input_file}")
                msg = 'Input file not found'
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=msg,
                ))
                return False, msg

            self._current_satellite = satellite
            self._current_frequency = sat_info['frequency']
            self._current_mode = sat_info['mode']
            self._device_index = -1  # Offline decode does not claim an SDR device
            self._capture_start_time = time.time()
            self._capture_phase = 'decoding'
            self._last_error_message = ''
            self._last_process_returncode = None
            self._stop_event.clear()

            try:
                self._running = True
                self._start_satdump_offline(
                    sat_info, input_path, sample_rate,
                )

                logger.info(
                    f"Weather satellite file decode started: {satellite} "
                    f"({sat_info['mode']}) from {input_file}"
                )
                self._emit_progress(CaptureProgress(
                    status='decoding',
                    satellite=satellite,
                    frequency=sat_info['frequency'],
                    mode=sat_info['mode'],
                    message=f"Decoding {sat_info['name']} from file ({sat_info['mode']})...",
                    log_type='info',
                    capture_phase='decoding',
                ))

                return True, None

            except Exception as e:
                self._running = False
                error_msg = str(e)
                logger.error(f"Failed to start file decode: {e}")
                self._emit_progress(CaptureProgress(
                    status='error',
                    satellite=satellite,
                    message=error_msg,
                ))
                return False, error_msg

    def start(
        self,
        satellite: str,
        device_index: int = 0,
        gain: float = 40.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        bias_t: bool = False,
        rtl_tcp_host: str | None = None,
        rtl_tcp_port: int = 1234,
    ) -> tuple[bool, str | None]:
        """Start weather satellite capture and decode.

        Args:
            satellite: Satellite key (for example ``'METEOR-M2-3'``)
            device_index: RTL-SDR device index
            gain: SDR gain in dB
            sample_rate: Sample rate in Hz
            bias_t: Enable bias-T power for LNA
            rtl_tcp_host: Remote rtl_tcp server hostname/IP (None for local SDR)
            rtl_tcp_port: Remote rtl_tcp server port (default 1234)

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        # Validate satellite BEFORE acquiring the lock
        sat_info = WEATHER_SATELLITES.get(satellite)
        if not sat_info:
            logger.error(f"Unknown satellite: {satellite}")
            msg = f'Unknown satellite: {satellite}'
            self._emit_progress(CaptureProgress(
                status='error',
                message=msg,
            ))
            return False, msg

        # Resolve device ID BEFORE lock — this runs rtl_test which can
        # take up to 5s and has no side effects on instance state.
        # Skip for remote rtl_tcp connections.
        source_id = None if rtl_tcp_host else self._resolve_device_id(device_index)

        with self._lock:
            if self._running:
                return True, None

            if not self._decoder:
                logger.error("No weather satellite decoder available")
                msg = 'SatDump not installed. Build from source or install via package manager.'
                self._emit_progress(CaptureProgress(
                    status='error',
                    message=msg,
                ))
                return False, msg

            self._current_satellite = satellite
            self._current_frequency = sat_info['frequency']
            self._current_mode = sat_info['mode']
            self._device_index = device_index
            self._capture_start_time = time.time()
            self._capture_phase = 'tuning'
            self._last_error_message = ''
            self._last_process_returncode = None
            self._stop_event.clear()

            try:
                self._running = True
                self._start_satdump(sat_info, device_index, gain, sample_rate, bias_t, source_id,
                                    rtl_tcp_host=rtl_tcp_host, rtl_tcp_port=rtl_tcp_port)

                logger.info(
                    f"Weather satellite capture started: {satellite} "
                    f"({sat_info['frequency']} MHz, {sat_info['mode']})"
                )
                self._emit_progress(CaptureProgress(
                    status='capturing',
                    satellite=satellite,
                    frequency=sat_info['frequency'],
                    mode=sat_info['mode'],
                    message=f"Capturing {sat_info['name']} on {sat_info['frequency']} MHz ({sat_info['mode']})...",
                    log_type='info',
                    capture_phase=self._capture_phase,
                ))

                return True, None

            except Exception as e:
                self._running = False
                error_msg = str(e)
                logger.error(f"Failed to start weather satellite capture: {e}")
                self._emit_progress(CaptureProgress(
                    status='error',
                    satellite=satellite,
                    message=error_msg,
                ))
                return False, error_msg

    def _start_satdump(
        self,
        sat_info: dict,
        device_index: int,
        gain: float,
        sample_rate: int,
        bias_t: bool,
        source_id: str | None = None,
        rtl_tcp_host: str | None = None,
        rtl_tcp_port: int = 1234,
    ) -> None:
        """Start SatDump live capture and decode."""
        # Create timestamped output directory for this capture
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        sat_name = sat_info['tle_key'].replace(' ', '_')
        self._capture_output_dir = self._output_dir / f"{sat_name}_{timestamp}"
        self._capture_output_dir.mkdir(parents=True, exist_ok=True)

        freq_hz = int(sat_info['frequency'] * 1_000_000)

        if rtl_tcp_host:
            # Remote SDR via rtl_tcp
            cmd = [
                'satdump', 'live',
                sat_info['pipeline'],
                str(self._capture_output_dir),
                '--source', 'rtltcp',
                '--ip_address', rtl_tcp_host,
                '--port', str(rtl_tcp_port),
                '--samplerate', str(sample_rate),
                '--frequency', str(freq_hz),
                '--gain', str(int(gain)),
            ]
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            # Local RTL-SDR device
            # Use pre-resolved source_id, or fall back to resolving now
            if source_id is None:
                source_id = self._resolve_device_id(device_index)

            cmd = [
                'satdump', 'live',
                sat_info['pipeline'],
                str(self._capture_output_dir),
                '--source', 'rtlsdr',
                '--samplerate', str(sample_rate),
                '--frequency', str(freq_hz),
                '--gain', str(int(gain)),
            ]

            # Only pass --source_id if we have a real serial number.
            # When _resolve_device_id returns None (no serial found),
            # omit the flag so SatDump uses the first available device.
            if source_id is not None:
                cmd.extend(['--source_id', source_id])

        if bias_t:
            cmd.append('--bias')

        logger.info(f"Starting SatDump: {' '.join(cmd)}")

        # Use a pseudo-terminal so SatDump thinks it's writing to a real
        # terminal.  C/C++ runtimes disable buffering on TTYs, which lets
        # us see output (including \r progress lines) in real time.
        master_fd, slave_fd = pty.openpty()
        self._pty_master_fd = master_fd

        self._process = subprocess.Popen(
            cmd,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        register_process(self._process)
        try:
            os.close(slave_fd)  # parent doesn't need the slave side
        except OSError:
            pass

        # Synchronous startup check — catch immediate failures (bad args,
        # missing device) before returning to the caller.
        time.sleep(0.5)
        if self._process.poll() is not None:
            error_output = self._drain_pty_output(master_fd)
            if error_output:
                logger.error(f"SatDump output:\n{error_output}")
            error_msg = self._extract_error(error_output, self._process.returncode)
            raise RuntimeError(error_msg)

        # Backup async check for slower failures (e.g. device opens then
        # fails after a second or two).
        def _check_early_exit():
            """Poll once after 2s; if SatDump died, emit an error event."""
            time.sleep(2)
            process = self._process
            if process is None or process.poll() is None:
                return  # still running or already cleaned up
            error_output = self._drain_pty_output(master_fd)
            if error_output:
                logger.error(f"SatDump output:\n{error_output}")
            error_msg = self._extract_error(error_output, process.returncode)
            self._emit_progress(CaptureProgress(
                status='error',
                satellite=self._current_satellite,
                frequency=self._current_frequency,
                mode=self._current_mode,
                message=error_msg,
                log_type='error',
                capture_phase='error',
            ))

        threading.Thread(target=_check_early_exit, daemon=True).start()

        # Start reader thread to monitor output
        self._reader_thread = threading.Thread(
            target=self._read_satdump_output, daemon=True
        )
        self._reader_thread.start()

        # Start image watcher thread
        self._watcher_thread = threading.Thread(
            target=self._watch_images, daemon=True
        )
        self._watcher_thread.start()

    def _start_satdump_offline(
        self,
        sat_info: dict,
        input_file: Path,
        sample_rate: int,
    ) -> None:
        """Start SatDump offline decode from a recorded file."""
        # Create timestamped output directory for this decode
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        sat_name = sat_info['tle_key'].replace(' ', '_')
        self._capture_output_dir = self._output_dir / f"{sat_name}_{timestamp}"
        self._capture_output_dir.mkdir(parents=True, exist_ok=True)

        # Determine input level from file extension.
        # WAV audio files (FM-demodulated) use 'audio_wav' level.
        # Raw IQ baseband files use 'baseband' level.
        suffix = input_file.suffix.lower()
        if suffix in ('.wav', '.wave'):
            input_level = 'audio_wav'
        else:
            input_level = 'baseband'

        cmd = [
            'satdump',
            sat_info['pipeline'],
            input_level,
            str(input_file),
            str(self._capture_output_dir),
            '--samplerate', str(sample_rate),
        ]

        logger.info(f"Starting SatDump offline: {' '.join(cmd)}")

        # Use a pseudo-terminal so SatDump thinks it's writing to a real
        # terminal — same approach as live mode for unbuffered output.
        master_fd, slave_fd = pty.openpty()
        self._pty_master_fd = master_fd

        self._process = subprocess.Popen(
            cmd,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        register_process(self._process)
        try:
            os.close(slave_fd)  # parent doesn't need the slave side
        except OSError:
            pass

        # Synchronous startup check — catch immediate failures (bad args,
        # missing pipeline). For offline mode, exit code 0 is normal success
        # (file decoding can finish quickly), so only raise on non-zero.
        time.sleep(0.5)
        if self._process.poll() is not None and self._process.returncode != 0:
            error_output = self._drain_pty_output(master_fd)
            if error_output:
                logger.error(f"SatDump offline output:\n{error_output}")
            error_msg = self._extract_error(error_output, self._process.returncode)
            raise RuntimeError(error_msg)

        # Start reader thread to monitor output
        self._reader_thread = threading.Thread(
            target=self._read_satdump_output, daemon=True
        )
        self._reader_thread.start()

        # Start image watcher thread
        self._watcher_thread = threading.Thread(
            target=self._watch_images, daemon=True
        )
        self._watcher_thread.start()

    @staticmethod
    def _classify_log_type(line: str) -> str:
        """Classify a SatDump output line into a log type."""
        lower = line.lower()
        if '(e)' in lower or 'error' in lower or 'fail' in lower:
            return 'error'
        if 'progress' in lower and '%' in line:
            return 'progress'
        if 'saved' in lower or 'writing' in lower:
            return 'save'
        if 'detected' in lower or 'lock' in lower or 'sync' in lower:
            return 'signal'
        if '(w)' in lower:
            return 'warning'
        if '(d)' in lower:
            return 'debug'
        return 'info'

    @staticmethod
    def _resolve_device_id(device_index: int) -> str | None:
        """Resolve RTL-SDR device index to serial number string for SatDump v1.2+.

        SatDump v1.2+ expects --source_id as a device serial string, not a
        numeric index. Try to look up the serial via rtl_test, return None
        if no serial can be found (caller should omit --source_id).
        """
        try:
            result = subprocess.run(
                ['rtl_test', '-d', str(device_index), '-t'],
                capture_output=True, text=True, timeout=5,
            )
            # rtl_test outputs: "Found 2 device(s):" then
            # "  0:  RTLSDRBlog, Blog V4, SN: 00004000"
            output = result.stdout + result.stderr
            for line in output.splitlines():
                # Match SN: <serial> pattern
                match = re.search(r'SN:\s*(\S+)', line)
                if match:
                    serial = match.group(1)
                    logger.info(f"RTL-SDR device {device_index} serial: {serial}")
                    return serial
                # Also match "Using device #N: ..." then "Serial number is <serial>"
                match = re.search(r'Serial number is\s+(\S+)', line)
                if match:
                    serial = match.group(1)
                    logger.info(f"RTL-SDR device {device_index} serial: {serial}")
                    return serial
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"Could not detect device serial: {e}")

        # No serial found — caller should omit --source_id
        return None

    @staticmethod
    def _drain_pty_output(master_fd: int) -> str:
        """Read all available output from a PTY master fd."""
        output = b''
        try:
            while True:
                r, _, _ = select.select([master_fd], [], [], 0.1)
                if not r:
                    break
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                output += chunk
        except OSError:
            pass
        return output.decode('utf-8', errors='replace')

    @staticmethod
    def _extract_error(output: str, returncode: int) -> str:
        """Extract a meaningful error message from SatDump output."""
        if output:
            for line in output.strip().splitlines():
                lower = line.lower()
                if 'error' in lower or 'could not' in lower or 'cannot' in lower or 'failed' in lower:
                    return line.strip()
        return f"SatDump exited immediately (code {returncode})"

    def _read_pty_lines(self):
        """Read lines from the PTY master fd, splitting on \\n and \\r.

        SatDump uses \\r carriage returns for progress updates. A PTY gives
        us unbuffered output. We use select() to detect data availability
        and os.read() for raw bytes, then split on line boundaries.
        """
        master_fd = self._pty_master_fd
        if master_fd is None:
            return

        buf = b''
        while self._running:
            try:
                r, _, _ = select.select([master_fd], [], [], 1.0)
                if not r:
                    # Timeout — check if process is still alive
                    if self._process and self._process.poll() is not None:
                        break
                    continue
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                buf += chunk
                # Split on \r and \n
                while b'\n' in buf or b'\r' in buf:
                    # Find earliest delimiter
                    idx_n = buf.find(b'\n')
                    idx_r = buf.find(b'\r')
                    if idx_n == -1:
                        idx = idx_r
                    elif idx_r == -1:
                        idx = idx_n
                    else:
                        idx = min(idx_n, idx_r)
                    line = buf[:idx]
                    buf = buf[idx + 1:]
                    # Skip empty lines
                    text = line.decode('utf-8', errors='replace').strip()
                    # Strip ANSI escape codes that terminals produce
                    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
                    if text:
                        yield text
            except OSError:
                break
        # Drain remaining buffer
        text = buf.decode('utf-8', errors='replace').strip()
        if text:
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
            if text:
                yield text

    def _read_satdump_output(self) -> None:
        """Read SatDump stdout/stderr for progress updates."""
        if not self._process or self._pty_master_fd is None:
            return

        last_emit_time = 0.0

        try:
            for line in self._read_pty_lines():
                if not self._running:
                    break

                line = line.strip()
                if not line:
                    continue

                logger.debug(f"satdump: {line}")

                elapsed = int(time.time() - self._capture_start_time)
                now = time.time()
                log_type = self._classify_log_type(line)

                # Track phase transitions
                lower = line.lower()
                if log_type == 'signal':
                    self._capture_phase = 'signal_detected'
                elif log_type == 'progress':
                    self._capture_phase = 'decoding'
                elif self._capture_phase == 'tuning' and (
                    'freq' in lower or 'processing' in lower
                    or 'starting' in lower or 'source' in lower
                ):
                    self._capture_phase = 'listening'

                # Parse progress from SatDump output
                if log_type == 'progress':
                    match = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
                    pct = int(float(match.group(1))) if match else 0
                    self._emit_progress(CaptureProgress(
                        status='decoding',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        progress_percent=pct,
                        elapsed_seconds=elapsed,
                        log_type=log_type,
                        capture_phase=self._capture_phase,
                    ))
                    last_emit_time = now
                elif log_type == 'save':
                    self._emit_progress(CaptureProgress(
                        status='decoding',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        elapsed_seconds=elapsed,
                        log_type=log_type,
                        capture_phase=self._capture_phase,
                    ))
                    last_emit_time = now
                elif log_type == 'error' or log_type == 'signal':
                    self._emit_progress(CaptureProgress(
                        status='capturing',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=line,
                        elapsed_seconds=elapsed,
                        log_type=log_type,
                        capture_phase=self._capture_phase,
                    ))
                    last_emit_time = now
                else:
                    # Emit other lines, throttled to every 0.5 seconds
                    if now - last_emit_time >= 0.5:
                        self._emit_progress(CaptureProgress(
                            status='capturing',
                            satellite=self._current_satellite,
                            frequency=self._current_frequency,
                            mode=self._current_mode,
                            message=line,
                            elapsed_seconds=elapsed,
                            log_type=log_type,
                            capture_phase=self._capture_phase,
                        ))
                        last_emit_time = now

        except Exception as e:
            logger.error(f"Error reading SatDump output: {e}")
        finally:
            # Close PTY master fd (thread-safe)
            self._close_pty()

            # Signal watcher thread to do final scan and exit
            self._stop_event.set()

            # Acquire lock when modifying shared state to avoid racing
            # with stop() which may have already cleaned up.
            with self._lock:
                was_running = self._running
                self._running = False
                process = self._process
            elapsed = int(time.time() - self._capture_start_time) if self._capture_start_time else 0

            if was_running:
                # Collect exit status (returncode is only set after poll/wait)
                if process and process.returncode is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                retcode = process.returncode if process else None
                self._last_process_returncode = retcode
                if retcode and retcode != 0:
                    self._capture_phase = 'error'
                    self._emit_progress(CaptureProgress(
                        status='error',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=f"SatDump crashed (exit code {retcode}). Check SatDump installation and SDR device.",
                        elapsed_seconds=elapsed,
                        log_type='error',
                        capture_phase='error',
                    ))
                else:
                    self._capture_phase = 'complete'
                    self._emit_progress(CaptureProgress(
                        status='complete',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=f"Capture complete ({elapsed}s)",
                        elapsed_seconds=elapsed,
                        log_type='info',
                        capture_phase='complete',
                    ))

            # Notify route layer to release SDR device
            if self._on_complete_callback:
                try:
                    self._on_complete_callback()
                except Exception as e:
                    logger.error(f"Error in on_complete callback: {e}")

    def _watch_images(self) -> None:
        """Watch output directory for new decoded images."""
        if not self._capture_output_dir:
            return

        known_files: set[str] = set()

        while self._running:
            self._scan_output_dir(known_files)
            # Use stop_event for faster wakeup on process exit
            if self._stop_event.wait(timeout=2):
                break

        # Final scan — SatDump writes images at the end of processing,
        # often after the process has already exited. Do multiple scans
        # with a short delay to catch late-written files.
        for _ in range(3):
            time.sleep(0.5)
            self._scan_output_dir(known_files)

    def _scan_output_dir(self, known_files: set[str]) -> None:
        """Scan capture output directory for new image files."""
        if not self._capture_output_dir:
            return

        try:
            # Recursively scan for image files
            for ext in ('*.png', '*.jpg', '*.jpeg'):
                for filepath in self._capture_output_dir.rglob(ext):
                    file_key = str(filepath)
                    if file_key in known_files:
                        continue

                    # Skip tiny files (likely incomplete)
                    try:
                        stat = filepath.stat()
                        if stat.st_size < 1000:
                            continue
                    except OSError:
                        continue

                    # Determine product type from filename/path
                    product = self._parse_product_name(filepath)

                    # Copy image to main output dir for serving
                    safe_sat = re.sub(r'[^A-Za-z0-9_-]+', '_', self._current_satellite).strip('_') or 'satellite'
                    safe_stem = re.sub(r'[^A-Za-z0-9_-]+', '_', filepath.stem).strip('_') or 'image'
                    suffix = filepath.suffix.lower()
                    if suffix not in ('.png', '.jpg', '.jpeg'):
                        suffix = '.png'
                    serve_name = (
                        f"{safe_sat}_{safe_stem}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
                        f"{suffix}"
                    )
                    serve_path = self._output_dir / serve_name
                    try:
                        shutil.copy2(filepath, serve_path)
                    except OSError:
                        # Copy failed — don't mark as known so it can be retried
                        continue

                    # Only mark as known after successful copy
                    known_files.add(file_key)

                    image = WeatherSatImage(
                        filename=serve_name,
                        path=serve_path,
                        satellite=self._current_satellite,
                        mode=self._current_mode,
                        timestamp=datetime.now(timezone.utc),
                        frequency=self._current_frequency,
                        size_bytes=stat.st_size,
                        product=product,
                    )
                    with self._images_lock:
                        self._images.append(image)

                    logger.info(f"New weather satellite image: {serve_name} ({product})")
                    self._emit_progress(CaptureProgress(
                        status='complete',
                        satellite=self._current_satellite,
                        frequency=self._current_frequency,
                        mode=self._current_mode,
                        message=f'Image decoded: {product}',
                        image=image,
                    ))

        except Exception as e:
            logger.error(f"Error scanning for images: {e}")

    def _parse_product_name(self, filepath: Path) -> str:
        """Parse a human-readable product name from the image filepath."""
        name = filepath.stem.lower()
        parts = filepath.parts

        # Common SatDump product names
        if 'rgb' in name:
            return 'RGB Composite'
        if 'msa' in name or 'multispectral' in name:
            return 'Multispectral Analysis'
        if 'thermal' in name or 'temp' in name:
            return 'Thermal'
        if 'ndvi' in name:
            return 'NDVI Vegetation'
        if 'channel' in name or 'ch' in name:
            match = re.search(r'(?:channel|ch)[\s_-]*(\d+)', name)
            if match:
                return f'Channel {match.group(1)}'
        if 'avhrr' in name:
            return 'AVHRR'
        if 'msu' in name or 'mtvza' in name:
            return 'MSU-MR'

        # Check parent directories for clues
        for part in parts:
            if 'rgb' in part.lower():
                return 'RGB Composite'
            if 'channel' in part.lower():
                return 'Channel Data'

        return filepath.stem

    def stop(self) -> None:
        """Stop weather satellite capture."""
        with self._lock:
            self._running = False
            self._stop_event.set()
            self._close_pty()
            process = self._process
            self._process = None
            elapsed = int(time.time() - self._capture_start_time) if self._capture_start_time else 0
            logger.info(f"Weather satellite capture stopped after {elapsed}s")
            self._device_index = -1

        # Terminate outside the lock so stop() returns quickly
        # and doesn't block start() or other lock acquisitions
        if process:
            safe_terminate(process)

    def get_images(self) -> list[WeatherSatImage]:
        """Get list of decoded images."""
        with self._images_lock:
            self._scan_images()
            return list(self._images)

    def _scan_images(self) -> None:
        """Scan output directory for images not yet tracked.

        Must be called with self._images_lock held.
        """
        known_filenames = {img.filename for img in self._images}

        for ext in ('*.png', '*.jpg', '*.jpeg'):
            for filepath in self._output_dir.glob(ext):
                if filepath.name in known_filenames:
                    continue
                # Skip tiny files
                try:
                    stat = filepath.stat()
                    if stat.st_size < 1000:
                        continue
                except OSError:
                    continue

                # Parse satellite name from filename
                satellite = 'Unknown'
                for sat_key in WEATHER_SATELLITES:
                    if sat_key in filepath.name:
                        satellite = sat_key
                        break

                sat_info = WEATHER_SATELLITES.get(satellite, {})

                image = WeatherSatImage(
                    filename=filepath.name,
                    path=filepath,
                    satellite=satellite,
                    mode=sat_info.get('mode', 'Unknown'),
                    timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    frequency=sat_info.get('frequency', 0.0),
                    size_bytes=stat.st_size,
                    product=self._parse_product_name(filepath),
                )
                self._images.append(image)
                known_filenames.add(filepath.name)

    def delete_image(self, filename: str) -> bool:
        """Delete a decoded image."""
        filepath = self._output_dir / filename
        if filepath.exists():
            try:
                filepath.unlink()
                with self._images_lock:
                    self._images = [img for img in self._images if img.filename != filename]
                return True
            except OSError as e:
                logger.error(f"Failed to delete image {filename}: {e}")
        return False

    def delete_all_images(self) -> int:
        """Delete all decoded images."""
        count = 0
        for ext in ('*.png', '*.jpg', '*.jpeg'):
            for filepath in self._output_dir.glob(ext):
                try:
                    filepath.unlink()
                    count += 1
                except OSError:
                    pass
        with self._images_lock:
            self._images.clear()
        return count

    def _emit_progress(self, progress: CaptureProgress) -> None:
        """Emit progress update to callback."""
        if progress.status == 'error' and progress.message:
            self._last_error_message = str(progress.message)
        if self._callback:
            try:
                self._callback(progress)
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")

    def get_status(self) -> dict:
        """Get current decoder status."""
        elapsed = 0
        if self._running and self._capture_start_time:
            elapsed = int(time.time() - self._capture_start_time)

        return {
            'available': self._decoder is not None,
            'decoder': self._decoder,
            'running': self._running,
            'satellite': self._current_satellite,
            'frequency': self._current_frequency,
            'mode': self._current_mode,
            'capture_phase': self._capture_phase,
            'elapsed_seconds': elapsed,
            'image_count': len(self._images),
            'last_error': self._last_error_message,
            'last_returncode': self._last_process_returncode,
        }


# Global decoder instance
_decoder: WeatherSatDecoder | None = None
_decoder_lock = threading.Lock()


def get_weather_sat_decoder() -> WeatherSatDecoder:
    """Get or create the global weather satellite decoder instance."""
    global _decoder
    if _decoder is None:
        with _decoder_lock:
            if _decoder is None:
                _decoder = WeatherSatDecoder()
    return _decoder


def is_weather_sat_available() -> bool:
    """Check if weather satellite decoding is available."""
    decoder = get_weather_sat_decoder()
    return decoder.decoder_available is not None
