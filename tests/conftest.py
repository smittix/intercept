"""Pytest configuration and fixtures."""

import pytest
from app import app as flask_app
from routes import register_blueprints


@pytest.fixture
def app():
    """Create application for testing."""
    register_blueprints(flask_app)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()
