"""bluetoothctl lines: a device's name and signal must reach the observations the list is built from."""
import io
from unittest.mock import MagicMock

from utils.bluetooth.fallback_scanner import BluetoothctlScanner

ADDR = "AA:BB:CC:DD:EE:FF"
LINES = [
    f"[NEW] Device {ADDR} Marshall Monitor",
    f"[CHG] Device {ADDR} RSSI: -67",
    f"[CHG] Device {ADDR} Name: Marshall Monitor III",
]


def _run(lines):
    seen = []
    scanner = BluetoothctlScanner(on_observation=seen.append)
    scanner._process = MagicMock(stdout=io.StringIO("\n".join(lines) + "\n"))
    scanner._stop_event.clear()
    scanner._read_output()
    return seen


def test_a_signal_line_reaches_an_observation():
    seen = _run(LINES)
    assert any(o.rssi == -67 for o in seen), [(o.name, o.rssi) for o in seen]


def test_a_name_line_reaches_an_observation():
    seen = _run(LINES)
    assert any(o.name == "Marshall Monitor III" for o in seen), [(o.name, o.rssi) for o in seen]


def test_the_device_keeps_its_name_and_signal_after_the_last_line():
    scanner_seen = []
    scanner = BluetoothctlScanner(on_observation=scanner_seen.append)
    scanner._process = MagicMock(stdout=io.StringIO("\n".join(LINES) + "\n"))
    scanner._stop_event.clear()
    scanner._read_output()
    dev = scanner._devices[ADDR]
    assert dev["name"] == "Marshall Monitor III" and dev["rssi"] == -67, dev
