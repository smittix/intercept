"""Tests for the forced password change flow.

An account seeded with a password the operator did not choose must set a
new one before the interface is usable. The shipped default of admin/admin
is gone; first run now generates a random password and requires a change.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def seeded_app():
    """A real app over a temporary DB, with auth genuinely enabled."""
    import utils.database as db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        with patch.object(db, "DB_PATH", db_path), patch.object(db, "DB_DIR", Path(tmp)):
            # get_connection() caches a thread-local handle, so without this a
            # later test keeps talking to an earlier test's deleted temp file.
            db.close_db()
            db.init_db()
            db.set_user_password("admin", "seeded-initial-pw")
            with db.get_db() as conn:
                conn.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")

            from app import app

            app.config["TESTING"] = True
            app.config["WTF_CSRF_ENABLED"] = False
            # /login is rate limited to 5/minute and the limiter store is
            # in-memory and shared across the whole test process, so without
            # clearing it every test after the fifth login gets a 429.
            # The config flag alone is not enough - it is read at init.
            import app as app_module

            if hasattr(app_module, "limiter") and hasattr(app_module.limiter, "reset"):
                app_module.limiter.reset()
            try:
                yield app, db
            finally:
                db.close_db()


def _login(app, password="seeded-initial-pw"):
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": password})
    return client


class TestForcedChangeGate:
    def test_login_redirects_to_change_password(self, seeded_app):
        app, _ = seeded_app
        client = app.test_client()
        resp = client.post(
            "/login", data={"username": "admin", "password": "seeded-initial-pw"}, follow_redirects=False
        )
        assert resp.status_code == 302
        assert "/change-password" in resp.headers["Location"]

    def test_dashboard_is_blocked_until_changed(self, seeded_app):
        app, _ = seeded_app
        resp = _login(app).get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/change-password" in resp.headers["Location"]

    def test_other_routes_are_blocked_until_changed(self, seeded_app):
        app, _ = seeded_app
        client = _login(app)
        for path in ["/adsb/aircraft", "/status", "/settings"]:
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code in (302, 403), f"{path} reachable before password change"

    def test_change_page_itself_is_reachable(self, seeded_app):
        app, _ = seeded_app
        resp = _login(app).get("/change-password")
        assert resp.status_code == 200
        assert "SET A PASSWORD TO CONTINUE" in resp.get_data(as_text=True)

    def test_anonymous_cannot_reach_change_page(self, seeded_app):
        app, _ = seeded_app
        resp = app.test_client().get("/change-password", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestChangePasswordValidation:
    @pytest.mark.parametrize(
        "current,new,confirm,expected",
        [
            ("wrong-pw", "a-long-enough-pw", "a-long-enough-pw", "INCORRECT"),
            ("seeded-initial-pw", "short", "short", "AT LEAST 12"),
            ("seeded-initial-pw", "a-long-enough-pw", "different-enough", "DO NOT MATCH"),
            ("seeded-initial-pw", "seeded-initial-pw", "seeded-initial-pw", "MUST DIFFER"),
        ],
    )
    def test_invalid_submissions_are_rejected(self, seeded_app, current, new, confirm, expected):
        app, db = seeded_app
        resp = _login(app).post(
            "/change-password",
            data={"current_password": current, "new_password": new, "confirm_password": confirm},
        )
        assert expected in resp.get_data(as_text=True).upper()
        assert db.user_must_change_password("admin"), "flag cleared despite invalid submission"


class TestSuccessfulChange:
    def test_valid_change_clears_the_flag_and_unblocks(self, seeded_app):
        app, db = seeded_app
        client = _login(app)
        resp = client.post(
            "/change-password",
            data={
                "current_password": "seeded-initial-pw",
                "new_password": "a-long-enough-pw",
                "confirm_password": "a-long-enough-pw",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert db.user_must_change_password("admin") is False
        assert db.verify_user_password("admin", "a-long-enough-pw") is True
        assert client.get("/", follow_redirects=False).status_code == 200

    def test_old_password_no_longer_works(self, seeded_app):
        app, db = seeded_app
        _login(app).post(
            "/change-password",
            data={
                "current_password": "seeded-initial-pw",
                "new_password": "a-long-enough-pw",
                "confirm_password": "a-long-enough-pw",
            },
        )
        assert db.verify_user_password("admin", "seeded-initial-pw") is False

    def test_no_stale_message_after_success(self, seeded_app):
        """The dashboard renders no flashes, so one queued on success would
        surface later on /login or here, styled as an error."""
        app, _ = seeded_app
        client = _login(app)
        client.post(
            "/change-password",
            data={
                "current_password": "seeded-initial-pw",
                "new_password": "a-long-enough-pw",
                "confirm_password": "a-long-enough-pw",
            },
        )
        client.get("/")
        assert "SIGNAL_" not in client.get("/change-password").get_data(as_text=True)


class TestSeeding:
    def test_generated_password_requires_a_change(self, monkeypatch):
        """No INTERCEPT_ADMIN_PASSWORD means a generated one, which must change."""
        monkeypatch.delenv("INTERCEPT_ADMIN_PASSWORD", raising=False)
        import config
        import utils.database as db

        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", Path(tmp) / "t.db"), patch.object(db, "DB_DIR", Path(tmp)):
                db.close_db()
                db.init_db()
                assert db.user_must_change_password("admin") is True

    def test_operator_chosen_password_does_not_force_a_change(self, monkeypatch):
        """An explicitly configured password is the operator's own choice."""
        import config
        import utils.database as db

        monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-chosen-pw")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", Path(tmp) / "t.db"), patch.object(db, "DB_DIR", Path(tmp)):
                db.close_db()
                db.init_db()
                assert db.user_must_change_password("admin") is False

    def test_existing_install_on_admin_admin_is_flagged(self, monkeypatch):
        """An install still using the old shipped default must change it."""
        import config
        import utils.database as db

        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", Path(tmp) / "t.db"), patch.object(db, "DB_DIR", Path(tmp)):
                db.close_db()
                db.init_db()
                # simulate the historical default and a cleared flag
                db.set_user_password("admin", "admin")
                assert db.user_must_change_password("admin") is False
                db.init_db()  # startup again
                assert db.user_must_change_password("admin") is True


def test_env_not_leaked():
    """Guard against anything leaving auth disabled for later tests."""
    assert os.environ.get("INTERCEPT_DISABLE_AUTH") is None
