"""CelesTrak blocks an address that downloads the same data too often, and
every start used to fetch. The schedule now survives restarts."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import routes.satellite as satellite

HOUR = 3600.0
NOW = 1_800_000_000.0


@pytest.fixture
def settings():
    store = {}
    with (
        patch("utils.database.get_setting", side_effect=lambda k, d=None: store.get(k, d)),
        patch("utils.database.set_setting", side_effect=lambda k, v: store.__setitem__(k, v)),
    ):
        yield store


def test_never_fetched_fetches_at_startup(settings):
    assert satellite._tle_startup_delay(NOW) == 2.0


def test_a_restart_soon_after_a_fetch_waits_for_the_day(settings):
    settings.update({"tle.last_fetch_success": NOW - HOUR, "tle.last_fetch_attempt": NOW - HOUR})
    assert satellite._tle_startup_delay(NOW) == pytest.approx(23 * HOUR)


def test_a_failed_fetch_is_not_retried_within_two_hours(settings):
    settings.update({"tle.last_fetch_success": NOW - 30 * HOUR, "tle.last_fetch_attempt": NOW - 0.5 * HOUR})
    assert satellite._tle_startup_delay(NOW) == pytest.approx(1.5 * HOUR)


def test_overdue_fetches_promptly(settings):
    settings.update({"tle.last_fetch_success": NOW - 30 * HOUR, "tle.last_fetch_attempt": NOW - 30 * HOUR})
    assert satellite._tle_startup_delay(NOW) == 2.0


def test_attempts_and_successes_are_recorded(settings):
    class Response:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch.object(satellite.urllib.request, "urlopen", side_effect=OSError("HTTP Error 403: Forbidden")):
        satellite.refresh_tle_data()
    assert "tle.last_fetch_attempt" in settings and "tle.last_fetch_success" not in settings

    with patch.object(satellite.urllib.request, "urlopen", return_value=Response("")):
        satellite.refresh_tle_data()
    assert "tle.last_fetch_success" in settings
