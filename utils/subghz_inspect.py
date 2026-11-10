"""Pulse-level inspection of saved SubGHz IQ captures.

Reads one burst window from a HackRF capture (interleaved signed 8-bit I/Q),
reduces it to about 1 MS/s, and runs rtl_433's pulse analyzer (-A) over it.
The analyzer measures pulse and gap widths, guesses the modulation, slices
the bits and suggests a flex decoder; this module parses that report and adds
an amplitude envelope for plotting.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

# rtl_433's analyzer works on pulse timings in microseconds; 1 MS/s keeps 1 us
# resolution while keeping the work small on a Pi, whatever the capture rate
TARGET_SAMPLE_RATE = 1_000_000
BURST_PAD_SECONDS = 0.02  # quiet margin either side so the analyzer sees the burst start and end
MAX_WINDOW_SECONDS = 2.0
MAX_ENVELOPE_COLUMNS = 40_000
MAX_PACKAGES = 8
ANALYZER_TIMEOUT_SECONDS = 30

# rtl_433 flex modulations the OOK mode can decode live, and their OOK-mode names
OOK_ENCODINGS = {
    "OOK_PWM": "pwm",
    "OOK_PPM": "ppm",
    "OOK_MC_ZEROBIT": "manchester",
}
FLEX_TO_OOK_FIELDS = {
    "s": "short_pulse",
    "l": "long_pulse",
    "r": "reset_limit",
    "g": "gap_limit",
    "t": "tolerance",
}

_PACKAGE_RE = re.compile(r"^Detected (\w+) package\s+@([\d.]+)s", re.MULTILINE)
_DIST_ENTRY_RE = re.compile(r"^\s*\[\s*\d+\]\s+count:\s+(\d+),\s+width:\s+(\d+) us")
_ROW_RE = re.compile(r"^\[(\d+)\]\s+\{(\d+)\}\s+([0-9a-f ]*?)\s*:\s*([01 ]*)$")
# Newer rtl_433 (25.12) prints the sliced rows as one line: "codes : {28}4c70ad3, {28}4c70ad3"
_CODES_RE = re.compile(r"^codes\s*:\s*(.*)$")
_CODE_RE = re.compile(r"\{(\d+)\}([0-9a-f]+)")
_SIGNAL_RE = re.compile(r"RSSI:\s*(-?[\d.]+) dB SNR:\s*(-?[\d.]+) dB")
_FLEX_RE = re.compile(r"-X '([^']+)'")
_GUESS_RE = re.compile(r"^Guessing modulation:\s*(.+)$", re.MULTILINE)

_DIST_HEADINGS = {
    "Pulse width distribution:": "pulse_widths",
    "Gap width distribution:": "gap_widths",
    "Pulse period distribution:": "periods",
    "Pulse+gap period distribution:": "periods",  # newer rtl_433 (25.12)
}


def read_iq_window(path: Path, sample_rate: int, start_seconds: float, duration_seconds: float) -> np.ndarray:
    """Read part of a cs8 capture as complex samples."""
    start = max(0, int(start_seconds * sample_rate))
    count = max(0, int(duration_seconds * sample_rate))
    with open(path, "rb", buffering=0) as f:
        available = max(0, os.fstat(f.fileno()).st_size - start * 2)
        iq = np.empty(min(count, available // 2), dtype=np.complex64)
        f.seek(start * 2)
        # Fill the result in place through a float32 view of its interleaved I/Q,
        # a small block at a time and without a read buffer, so the window is
        # never held twice
        interleaved = iq.view(np.float32)
        block = np.empty(min(interleaved.size, 1 << 14), dtype=np.int8)
        filled = 0
        while filled < interleaved.size:
            n = f.readinto(block[: interleaved.size - filled])
            if not n:
                break
            interleaved[filled : filled + n] = block[:n]
            filled += n
    return iq


def decimate(iq: np.ndarray, sample_rate: int, target: int = TARGET_SAMPLE_RATE) -> tuple[np.ndarray, int]:
    """Average blocks of samples down to about `target` samples per second.

    A block average is a crude low-pass filter, but on/off keying survives it
    and pulse timing only needs microsecond resolution.
    """
    factor = max(1, sample_rate // target)
    if factor == 1:
        return iq, sample_rate
    usable = iq.size - (iq.size % factor)
    return iq[:usable].reshape(-1, factor).mean(axis=1), sample_rate // factor


def envelope_columns(iq: np.ndarray, max_columns: int = MAX_ENVELOPE_COLUMNS) -> tuple[list[float], int]:
    """Peak amplitude per column, scaled so the strongest column is 1.

    Returns the columns and the samples per column: as fine as possible (so
    zooming in shows individual pulses) while staying under max_columns.
    """
    if iq.size == 0:
        return [], 1
    amp = np.abs(iq)
    per_column = max(1, -(-amp.size // max_columns))
    usable = amp.size - (amp.size % per_column)
    peaks = amp[:usable].reshape(-1, per_column).max(axis=1)
    top = float(peaks.max()) or 1.0
    return [round(float(v) / top, 2) for v in peaks], per_column


def write_cs8(iq: np.ndarray, path: str) -> None:
    out = np.empty(iq.size * 2, dtype=np.int8)
    out[0::2] = np.clip(np.round(iq.real), -128, 127)
    out[1::2] = np.clip(np.round(iq.imag), -128, 127)
    out.tofile(path)


def parse_flex_spec(spec: str) -> dict[str, str]:
    """Split "n=name,m=OOK_PWM,s=400,..." into its key/value pairs."""
    fields = {}
    for part in spec.split(","):
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def ook_settings(flex: dict[str, str]) -> dict | None:
    """OOK-mode start settings equivalent to a flex decoder, when it can decode it."""
    encoding = OOK_ENCODINGS.get(flex.get("m", ""))
    if not encoding:
        return None
    settings: dict = {"encoding": encoding}
    for key, field_name in FLEX_TO_OOK_FIELDS.items():
        try:
            value = int(float(flex.get(key, "0")))
        except ValueError:
            continue
        if value > 0:
            settings[field_name] = value
    return settings


def _code_row(bits: int, nibbles: str) -> dict:
    """A row from rtl_433's "{bits}hex" code notation (hex is MSB-first, zero-padded)."""
    binary = "".join(f"{int(n, 16):04b}" for n in nibbles)[:bits]
    return {"bits": bits, "hex": nibbles, "binary": binary}


def parse_pulse_analyzer(text: str) -> list[dict]:
    """Parse rtl_433 -A output into one dict per detected package."""
    starts = list(_PACKAGE_RE.finditer(text))
    packages = []
    for i, match in enumerate(starts[:MAX_PACKAGES]):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.start() : end]
        package: dict = {
            "kind": match.group(1),
            "at_seconds": float(match.group(2)),
            "pulse_widths": [],
            "gap_widths": [],
            "periods": [],
            "modulation": "",
            "rssi_db": None,
            "snr_db": None,
            "flex_spec": "",
            "ook": None,
            "rows": [],
        }

        current = None
        for line in block.splitlines():
            stripped = line.strip()
            if stripped in _DIST_HEADINGS:
                current = _DIST_HEADINGS[stripped]
                continue
            entry = _DIST_ENTRY_RE.match(line)
            if entry and current:
                package[current].append({"count": int(entry.group(1)), "width_us": int(entry.group(2))})
                continue
            current = None
            row = _ROW_RE.match(stripped)
            if row:
                package["rows"].append(
                    {
                        "bits": int(row.group(2)),
                        "hex": row.group(3).replace(" ", ""),
                        "binary": row.group(4).replace(" ", ""),
                    }
                )
                continue
            codes = _CODES_RE.match(stripped)
            if codes:
                package["rows"].extend(
                    _code_row(int(bits), nibbles) for bits, nibbles in _CODE_RE.findall(codes.group(1))
                )

        signal = _SIGNAL_RE.search(block)
        if signal:
            package["rssi_db"] = float(signal.group(1))
            package["snr_db"] = float(signal.group(2))
        guess = _GUESS_RE.search(block)
        if guess:
            package["modulation"] = guess.group(1).strip()
        flex = _FLEX_RE.search(block)
        if flex:
            package["flex_spec"] = flex.group(1)
            package["ook"] = ook_settings(parse_flex_spec(flex.group(1)))
        packages.append(package)
    return packages


def run_pulse_analyzer(iq: np.ndarray, sample_rate: int, rtl_433_path: str) -> str:
    """Run rtl_433's pulse analyzer over samples and return its report."""
    fd, tmp_path = tempfile.mkstemp(suffix=".cs8", prefix="subghz_inspect_")
    os.close(fd)
    try:
        write_cs8(iq, tmp_path)
        result = subprocess.run(
            [rtl_433_path, "-r", f"cs8:{tmp_path}", "-s", str(sample_rate), "-A", "-R", "0"],
            # One stream, in order: the analyzer report goes to stderr, but newer
            # rtl_433 prints the sliced rows ("codes") to stdout after each package
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=ANALYZER_TIMEOUT_SECONDS,
        )
        return result.stdout.decode("utf-8", errors="replace")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def inspect_window(
    path: Path,
    sample_rate: int,
    start_seconds: float,
    duration_seconds: float,
    rtl_433_path: str,
) -> dict:
    """Envelope plus parsed pulse analysis for one time window of a capture."""
    iq = read_iq_window(path, sample_rate, start_seconds, duration_seconds)
    if iq.size == 0:
        return {"status": "error", "message": "No samples in the selected window"}
    iq, rate = decimate(iq, sample_rate)
    report = run_pulse_analyzer(iq, rate, rtl_433_path)
    envelope, per_column = envelope_columns(iq)
    return {
        "status": "ok",
        "window": {
            "start_seconds": round(start_seconds, 6),
            "duration_seconds": round(iq.size / rate, 6),
            "analysis_sample_rate": rate,
        },
        "envelope": envelope,
        "envelope_column_us": round(per_column * 1e6 / rate, 3),
        "packages": parse_pulse_analyzer(report),
    }
