"""TSCM Survey workspace: wiring, endpoint coverage and the practitioner flow.

There is no JavaScript test runner, so the workspace module is checked
statically (which endpoints it calls, and that it shows nothing derived from
RSSI beyond the measurement itself), and the flow it drives is exercised
through the same endpoints, in the same order, against a real database.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE = (ROOT / "static" / "js" / "modes" / "tscm-survey.js").read_text()

# Every endpoint the review found unreachable from the UI, and the text the
# workspace uses to call it. None means deliberately omitted, with the reason.
ENDPOINTS = {
    "/tscm/baseline/compare": None,  # takes raw device lists that only the live sweep stream holds; the
    # workspace compares a completed sweep with /baseline/diff instead of re-deriving those lists
    "/tscm/baseline/diff/<baseline_id>/<sweep_id>": "`/tscm/baseline/diff/${baselineId}/${sweepId}`",
    "/tscm/baseline/<id>/activate": "`/tscm/baseline/${b.id}/activate`",
    "/tscm/baseline/active": "'/tscm/baseline/active'",
    "/tscm/baseline/status": "'/tscm/baseline/status'",
    "/tscm/threats": "`/tscm/threats${query}`",
    "/tscm/threats/summary": "'/tscm/threats/summary'",
    "/tscm/threats/<id>": "`/tscm/threats/${t.id}`",
    "/tscm/presets": "'/tscm/presets'",
    "/tscm/presets/<name>": "`/tscm/presets/${encodeURIComponent(key)}`",
    "/tscm/findings/correlations": "'/tscm/findings/correlations'",
    "/tscm/findings/high-interest": "'/tscm/findings/high-interest'",
    "/tscm/findings/<identifier>/playbook": "`/tscm/findings/${encodeURIComponent(d.identifier)}/playbook`",
    "/tscm/device/<identifier>/timeline": "`/tscm/device/${encodeURIComponent(d.identifier)}/timeline",
    "/tscm/known-devices/check/<identifier>": "`/tscm/known-devices/check/${encodeURIComponent(id)}",
    "/tscm/cases/<id>/notes": "`/tscm/cases/${caseSelect.value}/notes`",
    "/tscm/cases/<id>/threats/<threat_id>": "`/tscm/cases/${caseSelect.value}/threats/${t.id}`",
}


class TestWorkspaceModule:
    @pytest.mark.parametrize("endpoint,call", [(e, c) for e, c in ENDPOINTS.items() if c])
    def test_endpoint_is_called(self, endpoint, call):
        assert call in MODULE, f"{endpoint} is not reachable from the workspace"

    def test_every_called_path_is_a_real_route(self, app):
        rules = {re.sub(r"<[^>]+>", "<>", r.rule) for r in app.url_map.iter_rules()}
        for path in re.findall(r"['`](/tscm/[^'`?]*)", MODULE):
            normalised = re.sub(r"\$\{[^}]+\}", "<>", path)
            normalised = re.sub(r"\$\{.*$", "", normalised)  # a query string built inline
            normalised = re.sub(r"(?<!/)<>", "", normalised)  # ${query} appended to a segment
            assert normalised in rules, f"workspace calls {path}, which is not a route"

    def test_nothing_is_derived_from_rssi_beyond_the_measurement(self):
        """Distance, bearing and location cannot be measured with one
        omnidirectional receiver, and the timeline's movement pattern is an
        inference from RSSI variance."""
        code = re.sub(r"/\*.*?\*/", "", MODULE, flags=re.S)  # the header comment names what is excluded
        for term in ("proximity", "distance", "bearing", "metre", "meter", "movement", "appears_stationary"):
            assert term not in code.lower(), term

    def test_registered_as_a_mode(self):
        registry = (ROOT / "static" / "js" / "mode-registry.js").read_text()
        index = (ROOT / "templates" / "index.html").read_text()
        assert "module: 'TscmSurvey'" in registry
        assert "window.TscmSurvey = TscmSurvey" in MODULE
        assert "partials/modes/tscm-survey.html" in index
        assert 'id="tscmSurveyVisuals"' in index
        assert "js/modes/tscm-survey.js" in index and "css/modes/tscm-survey.css" in index


# =============================================================================
# The practitioner flow, through the endpoints the workspace calls
# =============================================================================


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


@pytest.fixture
def surveyed(db):
    """A recorded baseline and a completed sweep that found one new AP."""
    corp = {"bssid": "AA:AA:AA:AA:AA:AA", "essid": "Corp"}
    baseline_id = db.create_tscm_baseline("Boardroom", location="HQ", wifi_networks=[corp])
    sweep_id = db.create_tscm_sweep("standard", baseline_id=baseline_id)
    db.update_tscm_sweep(
        sweep_id,
        status="completed",
        results={"wifi_devices": [corp, {"bssid": "CC:CC:CC:CC:CC:CC", "essid": "Rogue"}], "wifi_count": 2},
        completed=True,
    )
    threat_id = db.add_tscm_threat(sweep_id, "rogue_ap", "high", "wifi", "CC:CC:CC:CC:CC:CC", name="Rogue")
    return {"baseline_id": baseline_id, "sweep_id": sweep_id, "threat_id": threat_id}


def test_practitioner_flow(client, surveyed):
    ids = surveyed

    # 1. Activate the baseline and see it reported as active, with its health
    assert client.post(f"/tscm/baseline/{ids['baseline_id']}/activate").status_code == 200
    active = client.get("/tscm/baseline/active").get_json()["baseline"]
    assert active["name"] == "Boardroom"
    health = client.get(f"/tscm/baseline/{ids['baseline_id']}/health").get_json()["health"]
    assert {"status", "score", "age_hours", "total_devices", "reasons"} <= set(health)

    # 2. What the sweep shows that the baseline did not
    diff = client.get(f"/tscm/baseline/diff/{ids['baseline_id']}/{ids['sweep_id']}").get_json()["diff"]
    assert [d["identifier"] for d in diff["new_devices"]] == ["CC:CC:CC:CC:CC:CC"]

    # 3. Mark the new device known, and confirm it reads as known
    resp = client.post("/tscm/known-devices", json={"identifier": "CC:CC:CC:CC:CC:CC", "protocol": "wifi"})
    assert resp.status_code == 200
    assert client.get("/tscm/known-devices/check/CC:CC:CC:CC:CC:CC").get_json()["is_known"] is True

    # 4. Raise the threat into a case, then resolve it with notes
    case_id = client.post("/tscm/cases", json={"name": "HQ survey"}).get_json()["case_id"]
    assert client.post(f"/tscm/cases/{case_id}/threats/{ids['threat_id']}").status_code == 200
    assert client.post(f"/tscm/cases/{case_id}/notes", json={"content": "Rogue AP traced to IT"}).status_code == 200
    assert client.get("/tscm/threats/summary").get_json()["summary"]["high"] == 1
    resp = client.put(f"/tscm/threats/{ids['threat_id']}", json={"acknowledge": True, "notes": "IT test AP"})
    assert resp.status_code == 200
    assert client.get("/tscm/threats/summary").get_json()["summary"]["high"] == 0
    [threat] = client.get("/tscm/threats").get_json()["threats"]
    assert threat["acknowledged"] and threat["notes"] == "IT test AP"
    case = client.get(f"/tscm/cases/{case_id}").get_json()["case"]
    assert [t["id"] for t in case["threats"]] == [ids["threat_id"]]
    assert case["case_notes"][0]["content"] == "Rogue AP traced to IT"

    # 5. Generate the report for the sweep
    caps = MagicMock()
    caps.to_dict.return_value = {}
    with patch("utils.tscm.advanced.detect_sweep_capabilities", return_value=caps):
        resp = client.get(f"/tscm/report/text?sweep_id={ids['sweep_id']}&site_name=HQ")
    assert resp.status_code == 200
    assert "Site / Location: HQ" in resp.get_data(as_text=True)


def test_report_includes_the_baseline_comparison_and_meeting_windows(client, db):
    """Defect: the report routes passed neither, so the client report never
    had a baseline comparison or a meeting window section."""
    baseline_id = db.create_tscm_baseline("Empty boardroom", wifi_networks=[{"bssid": "AA:AA:AA:AA:AA:AA"}])
    sweep_id = db.create_tscm_sweep("standard", baseline_id=baseline_id)
    meeting_id = db.start_meeting_window(sweep_id, name="Board meeting")
    db.end_meeting_window(meeting_id)
    results = {"wifi_devices": [{"bssid": "AA:AA:AA:AA:AA:AA"}, {"bssid": "BB:BB:BB:BB:BB:BB", "essid": "New AP"}]}
    db.update_tscm_sweep(sweep_id, status="completed", results=results, completed=True)

    caps = MagicMock()
    caps.to_dict.return_value = {}
    with patch("utils.tscm.advanced.detect_sweep_capabilities", return_value=caps):
        text = client.get(f"/tscm/report/text?sweep_id={sweep_id}").get_data(as_text=True)
        html = client.get(f"/tscm/report/print?sweep_id={sweep_id}").get_data(as_text=True)
        annex = client.get(f"/tscm/report/annex?sweep_id={sweep_id}").get_json()["annex"]

    assert "BASELINE COMPARISON (vs 'Empty boardroom'):" in text
    assert "  - New devices: 1" in text
    assert "Meeting: Board meeting" in text
    assert "Board meeting" in html and "Empty boardroom" in html
    assert annex["sweep_details"]["baseline_name"] == "Empty boardroom"
    assert annex["baseline_diff"]["summary"]["new_devices"] == 1
    assert [m["name"] for m in annex["meeting_windows"]] == ["Board meeting"]


def test_presets_and_details(client):
    presets = client.get("/tscm/presets").get_json()["presets"]
    assert "standard" in presets
    detail = client.get("/tscm/presets/standard").get_json()["preset"]
    assert detail["ranges"]


def test_diff_of_an_incomplete_sweep_is_refused(client, db):
    """Defect: a sweep's results are null until it completes, and the diff
    raised on them. An empty diff would instead report every baseline device
    as missing, so the endpoint now says the sweep has no results yet."""
    baseline_id = db.create_tscm_baseline("Boardroom", wifi_networks=[{"bssid": "AA:AA:AA:AA:AA:AA"}])
    sweep_id = db.create_tscm_sweep("standard", baseline_id=baseline_id)
    resp = client.get(f"/tscm/baseline/diff/{baseline_id}/{sweep_id}")
    assert resp.status_code == 409
    assert "no results" in resp.get_json()["message"]


def test_baseline_age_reads_stored_utc_correctly(client, db, monkeypatch):
    """Defect: created_at is UTC (SQLite CURRENT_TIMESTAMP) but was compared
    with local time, so outside UTC a baseline recorded a moment ago read as
    hours old, and aged into 'stale' early or late."""
    import time

    monkeypatch.setenv("TZ", "Europe/London")  # BST in September: UTC+1
    time.tzset()
    try:
        baseline_id = db.create_tscm_baseline("Fresh", wifi_networks=[{"bssid": "AA:AA:AA:AA:AA:AA"}])
        sweep_id = db.create_tscm_sweep("standard", baseline_id=baseline_id)
        db.update_tscm_sweep(sweep_id, status="completed", results={"wifi_devices": []}, completed=True)

        health = client.get(f"/tscm/baseline/{baseline_id}/health").get_json()["health"]
        diff = client.get(f"/tscm/baseline/diff/{baseline_id}/{sweep_id}").get_json()["diff"]
    finally:
        monkeypatch.undo()
        time.tzset()
    assert health["age_hours"] < 0.1
    assert diff["age"]["hours"] < 0.1


def test_past_sweeps_are_listed_newest_first_with_what_they_detected(client, db):
    """There was no way to list sweeps: only the current process's latest
    was reachable, and none at all after a restart."""
    first = db.create_tscm_sweep("quick")
    db.update_tscm_sweep(
        first,
        status="completed",
        results={"wifi_devices": [{"bssid": "AA:AA:AA:AA:AA:AA"}], "bt_devices": [{}, {}], "rf_count": 3},
        completed=True,
    )
    running = db.create_tscm_sweep("standard")

    sweeps = client.get("/tscm/sweeps").get_json()["sweeps"]
    assert [s["id"] for s in sweeps] == [running, first]
    assert sweeps[0]["has_results"] is False and sweeps[0]["detected"] is None
    assert sweeps[1]["detected"] == {"wifi": 1, "wifi_clients": 0, "bluetooth": 2, "rf": 3}
    assert "results" not in sweeps[1]  # summaries, not the device lists

    assert [s["id"] for s in client.get("/tscm/sweeps?limit=1").get_json()["sweeps"]] == [running]
    assert len(client.get("/tscm/sweeps?limit=0").get_json()["sweeps"]) == 1  # clamped to at least one
