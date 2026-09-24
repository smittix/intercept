"""Tests for the HF/Shortwave WebSDR integration."""

from unittest.mock import patch

import pytest

from routes.websdr import _haversine, _parse_gps_coord
from utils.kiwisdr import parse_host_port

# ============================================
# Helper function tests
# ============================================


def test_parse_gps_coord_float():
    """Should parse a simple float string."""
    assert _parse_gps_coord("51.5074") == pytest.approx(51.5074)


def test_parse_gps_coord_negative():
    """Should parse a negative coordinate."""
    assert _parse_gps_coord("-33.87") == pytest.approx(-33.87)


def test_parse_gps_coord_parentheses():
    """Should handle parentheses in coordinate string."""
    assert _parse_gps_coord("(-33.87)") == pytest.approx(-33.87)


def test_parse_gps_coord_empty():
    """Should return None for empty string."""
    assert _parse_gps_coord("") is None
    assert _parse_gps_coord(None) is None


def test_parse_gps_coord_invalid():
    """Should return None for invalid string."""
    assert _parse_gps_coord("abc") is None


def test_haversine_same_point():
    """Distance between same point should be 0."""
    assert _haversine(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0, abs=0.01)


def test_haversine_known_distance():
    """Test with known city pair (London to Paris ~343 km)."""
    dist = _haversine(51.5074, -0.1278, 48.8566, 2.3522)
    assert 340 < dist < 350


# ============================================
# Endpoint tests
# ============================================


@pytest.fixture
def auth_client(client):
    """Client with logged-in session."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


def test_websdr_status(auth_client):
    """Status endpoint should return cache info."""
    resp = auth_client.get("/websdr/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "cached_receivers" in data


def test_websdr_receivers_empty_cache(auth_client):
    """Receivers endpoint should work even with empty cache."""
    with patch("routes.websdr.get_receivers", return_value=[]):
        resp = auth_client.get("/websdr/receivers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["receivers"] == []


def test_websdr_receivers_with_data(auth_client):
    """Receivers endpoint should return filtered data."""
    mock_receivers = [
        {
            "name": "Test RX",
            "url": "http://test.com",
            "lat": 51.5,
            "lon": -0.1,
            "users": 1,
            "users_max": 4,
            "available": True,
            "freq_lo": 0,
            "freq_hi": 30000,
            "antenna": "Dipole",
            "bands": "HF",
        },
        {
            "name": "Full RX",
            "url": "http://full.com",
            "lat": 48.8,
            "lon": 2.3,
            "users": 4,
            "users_max": 4,
            "available": False,
            "freq_lo": 0,
            "freq_hi": 30000,
            "antenna": "Loop",
            "bands": "HF",
        },
    ]
    with patch("routes.websdr.get_receivers", return_value=mock_receivers):
        # Filter available only
        resp = auth_client.get("/websdr/receivers?available=true")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["receivers"]) == 1
        assert data["receivers"][0]["name"] == "Test RX"


def test_websdr_nearest_missing_params(auth_client):
    """Nearest endpoint should require lat/lon."""
    resp = auth_client.get("/websdr/receivers/nearest")
    assert resp.status_code == 400


def test_websdr_nearest_with_coords(auth_client):
    """Nearest endpoint should sort by distance."""
    mock_receivers = [
        {
            "name": "Far RX",
            "url": "http://far.com",
            "lat": -33.87,
            "lon": 151.21,
            "users": 0,
            "users_max": 4,
            "available": True,
            "freq_lo": 0,
            "freq_hi": 30000,
            "antenna": "Dipole",
            "bands": "HF",
        },
        {
            "name": "Near RX",
            "url": "http://near.com",
            "lat": 51.0,
            "lon": -0.5,
            "users": 0,
            "users_max": 4,
            "available": True,
            "freq_lo": 0,
            "freq_hi": 30000,
            "antenna": "Loop",
            "bands": "HF",
        },
    ]
    with patch("routes.websdr.get_receivers", return_value=mock_receivers):
        resp = auth_client.get("/websdr/receivers/nearest?lat=51.5&lon=-0.1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert len(data["receivers"]) == 2
        # Near should be first
        assert data["receivers"][0]["name"] == "Near RX"


def test_websdr_spy_station_receivers(auth_client):
    """Spy station cross-reference should find matching receivers."""
    mock_receivers = [
        {
            "name": "HF RX",
            "url": "http://hf.com",
            "lat": 51.5,
            "lon": -0.1,
            "users": 0,
            "users_max": 4,
            "available": True,
            "freq_lo": 0,
            "freq_hi": 30000,
            "antenna": "Dipole",
            "bands": "HF",
        },
    ]
    with patch("routes.websdr.get_receivers", return_value=mock_receivers):
        # e06 is one of the spy stations
        resp = auth_client.get("/websdr/spy-station/e06/receivers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert "station" in data


def test_websdr_spy_station_not_found(auth_client):
    """Non-existent station should return 404."""
    resp = auth_client.get("/websdr/spy-station/nonexistent/receivers")
    assert resp.status_code == 404


# ============================================
# parse_host_port tests (integration)
# ============================================


def test_parse_host_port_http_url():
    """Should parse standard KiwiSDR URL."""
    host, port = parse_host_port("http://kiwi.example.com:8073")
    assert host == "kiwi.example.com"
    assert port == 8073


def test_parse_host_port_no_protocol():
    """Should handle bare hostname."""
    host, port = parse_host_port("my-kiwi.local:8074")
    assert host == "my-kiwi.local"
    assert port == 8074


def test_parse_host_port_with_trailing_slash():
    """Should handle URL with trailing path."""
    host, port = parse_host_port("http://kiwi.com:8073/")
    assert host == "kiwi.com"
    assert port == 8073


def test_clean_text_strips_tags_and_decodes_entities():
    from routes.websdr import _clean_text

    assert (
        _clean_text("Active loop built by <b>&#9654; PA0EBC &#9664;</b> &#128077;")
        == "Active loop built by ▶ PA0EBC ◀ 👍"
    )
    assert _clean_text("&lt;i&gt;Beverage&lt;/i&gt;  North") == "Beverage North"
    assert _clean_text('Mini-Whip <a href="http://example') == "Mini-Whip"
    assert _clean_text(None) == ""


def test_fetch_kiwi_receivers_cleans_fields():
    import io

    from routes import websdr

    data = (
        'var kiwisdr_com = [{"name": "<b>Test</b> &amp; Co", "url": "rx.example:8073", "gps": "(52.0, 6.5)",'
        ' "antenna": "Loop <i>active</i> &#128077;", "loc": "Assen &#124; NL", "users": "1", "users_max": "4",'
        ' "status": "active", "bands": "0-30000000"}];' + " " * 100
    )

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch("urllib.request.urlopen", return_value=Resp(data.encode())):
        receivers = websdr._fetch_kiwi_receivers()

    assert receivers[0]["name"] == "Test & Co"
    assert receivers[0]["antenna"] == "Loop active 👍"
    assert receivers[0]["location"] == "Assen | NL"


def test_websdr_receivers_sorted_by_distance_with_bearing(auth_client):
    base = {"url": "http://x", "users": 0, "users_max": 4, "available": True, "freq_lo": 0, "freq_hi": 30000}
    mock_receivers = [
        {**base, "name": "Paris", "lat": 48.86, "lon": 2.35},
        {**base, "name": "No position", "lat": None, "lon": None},
        {**base, "name": "Oxford", "lat": 51.75, "lon": -1.26},
    ]
    with patch("routes.websdr.get_receivers", return_value=mock_receivers):
        resp = auth_client.get("/websdr/receivers?lat=51.5&lon=-0.13")
    names = [r["name"] for r in resp.get_json()["receivers"]]
    assert names == ["Oxford", "Paris", "No position"]
    oxford, paris = resp.get_json()["receivers"][:2]
    assert 70 < oxford["distance_km"] < 90 and 280 < oxford["bearing"] < 300  # west-north-west
    assert 330 < paris["distance_km"] < 350 and 140 < paris["bearing"] < 160  # south-east
