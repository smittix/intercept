"""Solar wind: SWPC retired products/solar-wind/*-6-hour.json; the real-time
feeds that replace them are reshaped into the tables the page reads."""

from __future__ import annotations

from unittest.mock import patch

import routes.space_weather as sw


def _row(time_tag, source, active, **values):
    return {"time_tag": time_tag, "source": source, "active": active, **values}


def test_active_spacecraft_last_six_hours_oldest_first():
    feed = [
        _row("2026-09-24T15:00:00", "SOLAR1", True, proton_density=8.1, proton_speed=377.4, proton_temperature=139492),
        _row("2026-09-24T15:00:00", "IMAP", False, proton_density=99, proton_speed=999, proton_temperature=1),
        _row("2026-09-24T12:00:00", "SOLAR1", True, proton_density=9.0, proton_speed=370.0, proton_temperature=120000),
        _row("2026-09-24T08:00:00", "SOLAR1", True, proton_density=5.0, proton_speed=300.0, proton_temperature=90000),
    ]
    with patch.object(sw, "_fetch_json", return_value=feed), patch.object(sw, "_cache_get", return_value=None):
        table = sw._fetch_solar_wind_plasma()
    assert table[0] == ["time_tag", "proton_density", "proton_speed", "proton_temperature"]
    # the inactive spacecraft and the reading over six hours old are left out
    assert table[1:] == [["2026-09-24 12:00:00", 9.0, 370.0, 120000], ["2026-09-24 15:00:00", 8.1, 377.4, 139492]]


def test_magnetometer_keeps_bz_in_the_column_the_page_reads():
    feed = [
        _row(
            "2026-09-24T15:00:00",
            "SOLAR1",
            True,
            bx_gsm=-6.8,
            by_gsm=6.0,
            bz_gsm=-3.5,
            phi_gsm=139.1,
            theta_gsm=-20.6,
            bt=10.1,
        )
    ]
    with patch.object(sw, "_fetch_json", return_value=feed), patch.object(sw, "_cache_get", return_value=None):
        table = sw._fetch_solar_wind_mag()
    assert table[1][3] == -3.5  # space-weather.js reads Bz from column 3


def test_an_unavailable_feed_is_none_not_an_error():
    with patch.object(sw, "_fetch_json", return_value=None), patch.object(sw, "_cache_get", return_value=None):
        assert sw._fetch_solar_wind_plasma() is None


def test_products_now_served_as_objects_are_put_back_into_tables():
    kp = [{"time_tag": "2026-09-24T12:00:00", "Kp": 1.67, "a_running": 6, "station_count": 8}]
    with patch.object(sw, "_fetch_cached_json", return_value=kp):
        assert sw._fetch_kp_index() == [
            ["time_tag", "Kp", "a_running", "station_count"],
            ["2026-09-24 12:00:00", 1.67, 6, 8],
        ]
    table = [["time_tag", "flux"], ["2026-09-24 20:00:00", "131"]]
    with patch.object(sw, "_fetch_cached_json", return_value=table):
        assert sw._fetch_flux() == table  # the old format is left alone


def test_flare_probabilities_oldest_first_so_the_last_rows_are_the_latest():
    feed = [{"date": "2026-09-24T00:00:00"}, {"date": "2026-08-25T00:00:00"}, {"date": "2026-09-23T00:00:00"}]
    with patch.object(sw, "_fetch_cached_json", return_value=feed):
        assert [r["date"][:10] for r in sw._fetch_flare_probability()] == ["2026-08-25", "2026-09-23", "2026-09-24"]
