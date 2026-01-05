"""Tests for main application routes."""

import pytest


def test_index_page(client):
    """Test that index page loads."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"INTERCEPT" in response.data


def test_dependencies_endpoint(client):
    """Test dependencies endpoint returns valid JSON."""
    response = client.get("/dependencies")
    assert response.status_code == 200
    data = response.get_json()
    assert "modes" in data
    assert "os" in data


def test_devices_endpoint(client):
    """Test devices endpoint returns list."""
    response = client.get("/devices")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_satellite_dashboard(client):
    """Test satellite dashboard loads."""
    response = client.get("/satellite/dashboard")
    assert response.status_code == 200


def test_adsb_dashboard(client):
    """Test ADS-B dashboard loads."""
    response = client.get("/adsb/dashboard")
    assert response.status_code == 200
