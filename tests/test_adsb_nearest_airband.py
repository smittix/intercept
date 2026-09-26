"""Tests for the /adsb/nearest-airband endpoint (issue #271)."""

import json
from pathlib import Path

import routes.adsb as adsb

FIXTURE = [
    {"ident": "AAAA", "name": "Alpha", "lat": 40.0, "lon": -73.0,
     "freqs": [{"type": "TWR", "mhz": 118.1, "desc": "TWR"}]},
    {"ident": "BBBB", "name": "Bravo", "lat": 50.0, "lon": 10.0,
     "freqs": [{"type": "APP", "mhz": 120.0, "desc": "APP"}]},
]


def _logged_in(client, path):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client.get(path)


def test_nearest_airband_returns_nearby(client, monkeypatch):
    monkeypatch.setattr(adsb, "_load_atc_airports", lambda: FIXTURE)
    resp = _logged_in(client, "/adsb/nearest-airband?lat=40.05&lon=-73.0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["airports"][0]["ident"] == "AAAA"
    assert data["airports"][0]["freqs"][0]["mhz"] == 118.1
    assert data["airports"][0]["distance_nm"] < 5


def test_nearest_airband_empty_when_far(client, monkeypatch):
    monkeypatch.setattr(adsb, "_load_atc_airports", lambda: FIXTURE)
    resp = _logged_in(client, "/adsb/nearest-airband?lat=0&lon=-30")
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 0


def test_nearest_airband_requires_coords(client):
    assert _logged_in(client, "/adsb/nearest-airband").status_code == 400
    assert _logged_in(client, "/adsb/nearest-airband?lat=abc&lon=1").status_code == 400


def test_bundled_dataset_is_valid():
    """The committed ATC-frequency dataset loads and has the expected shape."""
    path = Path(adsb.__file__).resolve().parent.parent / "static" / "data" / "atc_frequencies.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) > 1000
    first = data[0]
    assert {"ident", "name", "lat", "lon", "freqs"} <= set(first)
    assert first["freqs"] and {"type", "mhz"} <= set(first["freqs"][0])
