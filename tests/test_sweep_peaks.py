"""A finished sweep keeps its strongest readings, so a model can read them after the scan ends."""
import io
from unittest.mock import MagicMock

import pytest

from utils.subghz import SubGhzManager


@pytest.fixture
def manager(tmp_path):
    d = tmp_path / "subghz"
    (d / "captures").mkdir(parents=True)
    return SubGhzManager(data_dir=d)


def _feed(manager, lines):
    """Run the real stdout parser over hackrf_sweep CSV lines."""
    manager._sweep_process = MagicMock(stdout=io.BytesIO("\n".join(lines).encode() + b"\n"))
    manager._sweep_running = True
    manager._parse_sweep_stdout()


# hackrf_sweep CSV: date, time, hz_low, hz_high, hz_bin_width, num_samples, dB...
LINES = [
    "2026-11-13, 13:20:00, 433000000, 434000000, 1000000, 8, -70.0, -40.0, -65.0",
    "2026-11-13, 13:20:01, 433000000, 434000000, 1000000, 8, -72.0, -55.0, -66.0",
]


def test_the_strongest_reading_per_frequency_is_kept(manager):
    _feed(manager, LINES)
    peaks = manager.sweep_peaks()
    assert peaks[433.0] == -70.0  # first line's 433 bin; second line's 433 bin is -72.0, weaker
    assert peaks[434.0] == -40.0
    assert peaks[435.0] == -65.0


def test_peaks_are_cleared_when_a_new_sweep_starts(manager):
    _feed(manager, LINES)
    manager._reset_sweep_peaks()
    assert manager.sweep_peaks() == {}


def test_peaks_list_is_capped_to_the_strongest(manager):
    many = [f"2026-11-13, 13:20:00, {433000000 + i * 1000000}, {434000000 + i * 1000000}, 1000000, 1, -{50 + i % 40}.0" for i in range(300)]
    _feed(manager, many)
    assert len(manager.sweep_peaks()) <= manager.PEAK_LIMIT
