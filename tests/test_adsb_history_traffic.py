"""/adsb/history/traffic: per-bucket aircraft, snapshots and messages."""

from __future__ import annotations

import contextlib

import routes.adsb as adsb


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor


def _enable(monkeypatch, cursor):
    monkeypatch.setattr(adsb, "ADSB_HISTORY_ENABLED", True)
    monkeypatch.setattr(adsb, "PSYCOPG2_AVAILABLE", True)
    monkeypatch.setattr(adsb, "_ensure_history_schema", lambda: None)
    monkeypatch.setattr(adsb, "_get_history_connection", lambda: contextlib.nullcontext(_Conn(cursor)))


def test_traffic_fills_buckets_and_zeroes_the_rest(client, monkeypatch):
    cursor = _Cursor(
        [
            [{"bucket": 0, "aircraft": 3, "snapshots": 40}, {"bucket": 3, "aircraft": 7, "snapshots": 90}],
            [{"bucket": 3, "messages": 1200}, {"bucket": 9, "messages": 5}],  # 9 is outside 4 buckets
        ]
    )
    _enable(monkeypatch, cursor)

    resp = client.get("/adsb/history/traffic?since_minutes=240&buckets=4")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["buckets"] == 4
    assert data["bucket_seconds"] == 3600
    assert data["aircraft"] == [3, 0, 0, 7]
    assert data["snapshots"] == [40, 0, 0, 90]
    assert data["messages"] == [0, 0, 0, 1200]
    # Both queries clamp to the last bucket and share the window
    for _sql, params in cursor.executed:
        assert params == (3, "240 minutes", 3600.0, "240 minutes")


def test_traffic_reports_disabled_history(client, monkeypatch):
    monkeypatch.setattr(adsb, "ADSB_HISTORY_ENABLED", False)

    resp = client.get("/adsb/history/traffic")

    assert resp.status_code == 503


def test_traffic_reports_database_failure(client, monkeypatch):
    _enable(monkeypatch, None)
    monkeypatch.setattr(adsb, "_get_history_connection", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    resp = client.get("/adsb/history/traffic")

    assert resp.status_code == 503
