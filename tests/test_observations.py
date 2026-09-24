"""Observations and the activity feed."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import observations


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
            observations.reset()
            try:
                yield database
            finally:
                observations.flush()
                observations.reset()
                database.close_db()


def _rows(db):
    observations.flush()
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM observations ORDER BY id")]


class TestEmit:
    def test_shape(self, db):
        obs = observations.emit_observation(
            "adsb", "4CA7B1", rssi="-12.5", lat=51.47, lon=-0.45, summary="EIN123", raw={"icao": "4CA7B1"}
        )
        assert set(obs) == {"ts", "source", "identifier", "entity", "rssi", "lat", "lon", "summary", "raw"}
        assert (obs["source"], obs["identifier"], obs["entity"]) == ("adsb", "4CA7B1", "4CA7B1")
        assert (obs["rssi"], obs["lat"], obs["lon"]) == (-12.5, 51.47, -0.45)
        [row] = _rows(db)
        assert row["identifier"] == "4CA7B1" and row["summary"] == "EIN123"

    def test_optional_fields_may_be_absent_or_junk(self, db):
        obs = observations.emit_observation("sensor", "Acurite:12", rssi="n/a", lat=None, raw=object())
        assert obs["rssi"] is None and obs["lat"] is None
        assert _rows(db)[0]["raw"]  # unserialisable raw falls back to str()

    @pytest.mark.parametrize("identifier", [None, "", "   ", "x" * 129])
    def test_no_identifier_no_observation(self, db, identifier):
        assert observations.emit_observation("wifi", identifier) is None

    def test_raw_is_capped(self, db):
        observations.emit_observation("ais", "235000001", raw={"blob": "x" * 10_000})
        assert len(_rows(db)[0]["raw"]) == observations.RAW_LIMIT


class TestRateLimiting:
    def test_repeat_sightings_of_one_identifier_are_limited(self, db):
        t = 1_000_000.0
        assert observations.emit_observation("adsb", "4CA7B1", ts=t)
        assert observations.emit_observation("adsb", "4CA7B1", ts=t + 5) is None  # 15 s interval for ADS-B
        assert observations.emit_observation("adsb", "4CA7B1", ts=t + 16)
        assert observations.stats()["adsb"] == {"emitted": 2, "rate_limited": 1}

    def test_busy_source_is_capped(self, db):
        """Hundreds of distinct aircraft in a second: the burst passes, the rest wait."""
        t = 2_000_000.0
        passed = sum(observations.emit_observation("adsb", f"ICAO{i:04d}", ts=t) is not None for i in range(500))
        assert passed == observations.SOURCE_BURST
        # a second later the bucket has refilled by SOURCE_RATE
        later = sum(observations.emit_observation("adsb", f"LATE{i:04d}", ts=t + 1) is not None for i in range(500))
        assert later == observations.SOURCE_RATE
        assert len(_rows(db)) == observations.SOURCE_BURST + observations.SOURCE_RATE

    def test_one_busy_source_does_not_starve_another(self, db):
        t = 3_000_000.0
        for i in range(500):
            observations.emit_observation("adsb", f"ICAO{i:04d}", ts=t)
        assert observations.emit_observation("sensor", "Acurite:1", ts=t)


class TestRetention:
    def test_old_observations_are_deleted(self, db):
        now = time.time()
        observations.emit_observation("wifi", "AA:AA:AA:AA:AA:01", ts=now - 48 * 3600)
        observations.emit_observation("wifi", "AA:AA:AA:AA:AA:02", ts=now)
        assert observations.cleanup_old_observations(max_age_hours=24, max_rows=1000) == 1
        assert [r["identifier"] for r in _rows(db)] == ["AA:AA:AA:AA:AA:02"]

    def test_row_cap_keeps_the_newest(self, db):
        now = time.time()
        for i in range(10):
            observations.emit_observation("ais", f"23500000{i}", ts=now - 100 + i)
        assert observations.cleanup_old_observations(max_age_hours=24, max_rows=3) == 7
        assert [r["identifier"] for r in _rows(db)] == ["235000007", "235000008", "235000009"]

    def test_policy_is_registered_with_the_cleanup_manager(self):
        source = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text()
        assert "register_db_cleanup(cleanup_old_observations" in source


class TestEndpoint:
    @pytest.fixture
    def seeded(self, db):
        now = time.time()
        observations.emit_observation("adsb", "4CA7B1", ts=now - 30, summary="EIN123")
        observations.emit_observation("wifi", "AA:BB:CC:DD:EE:FF", ts=now - 20, summary="Corp")
        observations.emit_observation("sensor", "Acurite:12", ts=now - 10)
        observations.emit_observation("adsb", "4007F1", ts=now - 7200)
        return now

    def test_newest_first(self, client, seeded):
        rows = client.get("/observations").get_json()["observations"]
        assert [r["identifier"] for r in rows] == ["Acurite:12", "AA:BB:CC:DD:EE:FF", "4CA7B1", "4007F1"]
        assert "raw" not in rows[0]

    def test_filter_by_source(self, client, seeded):
        rows = client.get("/observations?source=adsb,sensor").get_json()["observations"]
        assert {r["source"] for r in rows} == {"adsb", "sensor"}

    def test_filter_by_identifier(self, client, seeded):
        rows = client.get("/observations?identifier=4CA7B1").get_json()["observations"]
        assert [r["identifier"] for r in rows] == ["4CA7B1"]

    def test_filter_by_time_window(self, client, seeded):
        rows = client.get("/observations?window_minutes=60").get_json()["observations"]
        assert "4007F1" not in {r["identifier"] for r in rows} and len(rows) == 3
        rows = client.get(f"/observations?since={seeded - 25}&until={seeded - 15}").get_json()["observations"]
        assert [r["identifier"] for r in rows] == ["AA:BB:CC:DD:EE:FF"]

    def test_bad_number_is_a_400(self, client, seeded):
        assert client.get("/observations?since=yesterday").status_code == 400

    def test_stats(self, client, seeded):
        data = client.get("/observations/stats").get_json()
        assert data["sources"]["adsb"]["emitted"] == 2
        assert data["retention"]["max_age_hours"] > 0


class TestAdoption:
    def test_process_event_emits_with_no_per_module_change(self, db):
        from utils.event_pipeline import process_event

        process_event("ais", {"type": "vessel", "mmsi": "235000001", "lat": 50.1, "lon": -1.2, "name": "BOATY"})
        process_event("ais", {"type": "status", "text": "started"})  # not a sighting
        [row] = _rows(db)
        assert (row["source"], row["identifier"], row["summary"], row["lat"]) == ("ais", "235000001", "BOATY", 50.1)

    def test_a_source_emitting_at_ingestion_is_not_emitted_twice(self, db):
        from utils.event_pipeline import process_event

        observations.emit_observation("sensor", "Acurite:12", direct=True)
        process_event("sensor", {"type": "sensor", "id": "Acurite:12"})
        assert len(_rows(db)) == 1

    def test_wifi_sse_copy_of_an_ingested_sighting_is_absorbed(self, db):
        """Wi-Fi emits at ingestion but stays open to process_event(), which is
        the legacy /wifi stream's only route to the feed."""
        from utils.event_pipeline import process_event

        observations.emit_observation("wifi", "AA:BB:CC:DD:EE:FF", summary="Corp")
        process_event("wifi", {"type": "network", "bssid": "AA:BB:CC:DD:EE:FF", "essid": "Corp"})
        process_event("wifi", {"type": "network", "bssid": "11:22:33:44:55:66", "essid": "Legacy"})
        assert [r["identifier"] for r in _rows(db)] == ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"]

    def test_geofence_events_are_not_sightings(self, db):
        from utils.event_pipeline import process_event

        process_event("adsb", {"type": "geofence", "icao": "4CA7B1", "zone": "Home"}, "geofence")
        assert _rows(db) == []

    def test_sensor_emits_at_ingestion_without_a_browser(self, db):
        """Recorded from the decoder's output, not from an SSE subscriber."""
        import json
        import threading

        from routes import sensor
        from tests.fake_process import FakeProcess

        proc = FakeProcess(["rtl_433"], stdout=-1)
        line = json.dumps({"model": "Acurite-Tower", "id": 1234, "channel": "A", "temperature_C": 20.1, "rssi": -7.1})
        proc.emit((line + "\n").encode())
        with patch("routes.sensor.app_module"):
            reader = threading.Thread(target=sensor.stream_sensor_output, args=(proc,), daemon=True)
            reader.start()
            deadline = time.time() + 3
            while time.time() < deadline and not _rows(db):
                time.sleep(0.05)
            proc.kill()
            reader.join(3)
        [row] = _rows(db)
        assert (row["source"], row["identifier"], row["rssi"]) == ("sensor", "Acurite-Tower:1234:A", -7.1)

    def test_bluetooth_entity_is_the_resolved_identity(self, db):
        class Cluster:
            cluster_id = "ble-cluster-7"
            linked_macs = {"11:22:33:44:55:66"}

        with patch("utils.tscm.device_identity.get_identity_engine") as engine:
            engine.return_value.get_clusters.return_value = [Cluster()]
            obs = observations.emit_observation("bluetooth", "11:22:33:44:55:66")
        assert obs["entity"] == "ble-cluster-7"
