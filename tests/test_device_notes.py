"""Operator notes and tags on observed devices.

They share the tscm_known_devices row, which also marks a device known-good
and lowers its risk score. A note must not do that: noting a suspicious
device must not make it look less suspicious.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def db():
    import config
    import utils.database as database

    with tempfile.TemporaryDirectory() as tmp:
        with (
            patch.object(database, "DB_PATH", Path(tmp) / "test.db"),
            patch.object(database, "DB_DIR", Path(tmp)),
            patch.object(config, "ADMIN_PASSWORD", "test-admin-password"),
        ):
            database.close_db()
            database.init_db()
            try:
                yield database
            finally:
                database.close_db()


MAC = "AA:BB:CC:DD:EE:01"


def _put(client, identifier=MAC, **body):
    return client.put(f"/device-notes/{identifier}", json={"protocol": "bluetooth", **body})


def test_note_and_tags_round_trip(client, db):
    assert _put(client, notes="Reception's printer\nIn the cupboard", tags=["office", "printer"]).status_code == 200
    device = client.get("/device-notes").get_json()["devices"][MAC]
    assert device == {
        "protocol": "bluetooth",
        "notes": "Reception's printer\nIn the cupboard",
        "tags": ["office", "printer"],
    }


def test_notes_survive_a_restart(client, db):
    _put(client, notes="Visitor's headphones", tags=["visitor"])
    db.close_db()  # a new process opens a new connection
    db.init_db()
    assert client.get("/device-notes").get_json()["devices"][MAC]["notes"] == "Visitor's headphones"


def test_identifiers_are_matched_case_insensitively(client, db):
    _put(client, identifier="aa:bb:cc:dd:ee:01", notes="lower-case")
    assert client.get("/device-notes").get_json()["devices"][MAC]["notes"] == "lower-case"


class TestNotKnownGood:
    def test_a_noted_device_is_not_known_good(self, client, db):
        _put(client, notes="Suspicious: appeared during the board meeting")
        assert db.is_known_good_device(MAC) is None
        assert client.get("/tscm/known-devices").get_json()["devices"] == []
        assert client.get(f"/tscm/known-devices/check/{MAC}").get_json()["is_known"] is False
        assert client.get(f"/tscm/known-devices/{MAC}").status_code == 404

    def test_marking_known_keeps_the_note(self, client, db):
        _put(client, notes="Reception printer")
        client.post("/tscm/known-devices", json={"identifier": MAC, "protocol": "bluetooth", "name": "Printer"})
        assert db.is_known_good_device(MAC)["name"] == "Printer"
        assert client.get("/device-notes").get_json()["devices"][MAC]["notes"] == "Reception printer"

    def test_unmarking_known_keeps_the_note(self, client, db):
        client.post("/tscm/known-devices", json={"identifier": MAC, "protocol": "bluetooth"})
        _put(client, notes="Keep this")
        client.delete(f"/tscm/known-devices/{MAC}")
        assert db.is_known_good_device(MAC) is None
        assert client.get("/device-notes").get_json()["devices"][MAC]["notes"] == "Keep this"

    def test_clearing_the_note_keeps_a_known_device(self, client, db):
        client.post("/tscm/known-devices", json={"identifier": MAC, "protocol": "bluetooth"})
        _put(client, notes="temporary")
        client.delete(f"/device-notes/{MAC}")
        assert MAC not in client.get("/device-notes").get_json()["devices"]
        assert db.is_known_good_device(MAC) is not None

    def test_clearing_the_only_note_removes_the_row(self, client, db):
        _put(client, notes="temporary")
        client.delete(f"/device-notes/{MAC}")
        with db.get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tscm_known_devices").fetchone()[0] == 0


@pytest.mark.parametrize(
    "identifier,body",
    [
        (MAC, {"notes": "bell\x07"}),
        (MAC, {"notes": "x" * 2001}),
        (MAC, {"tags": ["ok", "<script>"]}),
        (MAC, {"tags": [f"t{i}" for i in range(11)]}),
        (MAC, {"tags": "not-a-list"}),
        (MAC, {"protocol": "carrier-pigeon"}),
        ("bad identifier!!", {"notes": "x"}),
    ],
)
def test_invalid_input_is_rejected(client, db, identifier, body):
    assert client.put(f"/device-notes/{identifier}", json={"protocol": "wifi", **body}).status_code == 400


def test_an_older_database_gains_the_columns(db):
    """init_db() on a database created before notes existed adds them, and
    keeps its existing rows known-good."""
    path = db.DB_PATH
    db.close_db()
    path.unlink()
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tscm_known_devices (id INTEGER PRIMARY KEY AUTOINCREMENT, identifier TEXT NOT NULL UNIQUE, "
        "protocol TEXT NOT NULL, name TEXT, description TEXT, location TEXT, scope TEXT DEFAULT 'global', "
        "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, added_by TEXT, last_verified TIMESTAMP, "
        "score_modifier INTEGER DEFAULT -2, metadata TEXT)"
    )
    conn.execute(
        "INSERT INTO tscm_known_devices (identifier, protocol, name) VALUES (?, 'wifi', 'Old printer')", (MAC,)
    )
    conn.commit()
    conn.close()

    db.init_db()
    assert db.is_known_good_device(MAC)["name"] == "Old printer"
    db.set_device_annotation(MAC, "wifi", "migrated", ["old"])
    assert db.get_device_annotations()[MAC]["tags"] == ["old"]
