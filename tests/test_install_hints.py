"""Install advice matches the machine it is shown on.

Route handlers used to hardcode it, and on the wrong platform: the 433 MHz
sensor told Linux users to `brew install rtl_433`, and /dependencies sent
every Linux distribution, Arch and Fedora included, apt commands.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import dependencies

ROOT = Path(__file__).resolve().parent.parent


def _platform(system: str, *managers: str):
    return (
        patch("utils.dependencies.platform.system", return_value=system),
        patch(
            "utils.dependencies.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in managers else None
        ),
    )


@pytest.mark.parametrize(
    "system,managers,expected",
    [
        ("Darwin", ("brew",), "brew"),
        ("Darwin", (), "manual"),
        ("Linux", ("apt-get",), "apt"),
        ("Linux", ("pacman",), "manual"),  # Arch: never an apt command
        ("Linux", ("dnf",), "manual"),  # Fedora
        ("Windows", (), "manual"),
    ],
)
def test_package_manager_is_detected_not_assumed(system, managers, expected):
    first, second = _platform(system, *managers)
    with first, second:
        assert dependencies.package_manager() == expected


def test_hint_uses_the_detected_manager():
    first, second = _platform("Darwin", "brew")
    with first, second:
        assert dependencies.install_hint("rtl_433") == "Install with: brew install rtl_433"
    first, second = _platform("Linux", "apt-get")
    with first, second:
        assert dependencies.install_hint("rtl_433") == "Install with: sudo apt install rtl-433"


def test_without_a_known_manager_the_hint_is_the_project_page():
    first, second = _platform("Linux", "pacman")
    with first, second:
        hint = dependencies.install_hint("rtl_433")
    assert hint.startswith("See http")
    assert "apt" not in hint and "brew" not in hint


def test_unknown_tool_names_no_manager():
    assert dependencies.install_hint("frobnicator") == "Install frobnicator with your system's package manager."


def test_dependencies_endpoint_carries_hints(client):
    first, second = _platform("Linux", "pacman")
    with first, second:
        data = client.get("/dependencies").get_json()
    assert data["pkg_manager"] == "manual"
    assert data["install_hints"]["rtl_433"].startswith("See http")


def test_sensor_missing_tool_advice_fits_the_platform(client):
    first, second = _platform("Linux", "pacman")
    with first, second, patch("routes.sensor.subprocess.Popen", side_effect=FileNotFoundError("rtl_433")):
        message = client.post("/start_sensor", json={}).get_json()["message"]
    assert "brew" not in message and "apt" not in message
    client.post("/stop_sensor", json={})


def test_no_hardcoded_package_manager_commands():
    """Install commands live in utils/dependencies.py, chosen per platform."""
    command = re.compile(r"\b(?:sudo\s+)?(?:apt(?:-get)?|brew|dnf|yum|zypper)\s+install\b|\bpacman\s+-S\b")
    offenders = []
    sources = [*ROOT.glob("routes/**/*.py"), *ROOT.glob("utils/**/*.py"), ROOT / "app.py", ROOT / "intercept_agent.py"]
    sources += [*ROOT.glob("templates/**/*.html"), *ROOT.glob("static/js/**/*.js")]
    for path in sources:
        if path.name == "dependencies.py" or "vendor" in path.parts or path.name.endswith(".min.js"):
            continue
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if command.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"hardcoded install commands (use utils.dependencies.install_hint): {offenders}"
