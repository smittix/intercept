"""A receive whose radio process dies at once must report the error, not claim started."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import pytest

from utils.subghz import SubGhzManager


@pytest.fixture
def manager(tmp_path):
    d = tmp_path / "subghz"
    (d / "captures").mkdir(parents=True)
    return SubGhzManager(data_dir=d)


def _dead_process(stderr_text=b"hackrf_open() failed: Resource busy (-1000)\n"):
    import os
    proc = MagicMock()
    proc.poll.return_value = 0  # already dead
    rfd, wfd = os.pipe()
    os.write(wfd, stderr_text)
    os.close(wfd)
    proc.stderr = open(rfd, "rb", buffering=0, closefd=True)
    return proc


@patch("utils.subghz.SubGhzManager._resolve_tool", return_value="/usr/bin/hackrf_transfer")
@patch("utils.subghz.SubGhzManager._require_hackrf_device", return_value=None)
@patch("utils.subghz.register_process")
def test_a_dead_radio_process_is_reported_as_an_error(reg, _dev, _res, manager):
    with patch("utils.subghz.subprocess.Popen", return_value=_dead_process()):
        result = manager.start_receive(
            frequency_hz=433920000, sample_rate=2000000, lna_gain=24, vga_gain=20
        )
    assert result["status"] == "error", result
    assert "Resource busy" in result.get("message", ""), result
