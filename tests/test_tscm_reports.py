"""TSCM report generation: the deliverable a practitioner hands to a client.

Golden files live in tests/golden/tscm/. Regenerate them after an intended
change with:

    UPDATE_GOLDEN=1 pytest tests/test_tscm_reports.py

and review the diff before committing.

Lines carrying the report's per-finding signal confidence, proximity
interpretation and risk score are replaced with "<flagged>" before
comparison. Their wording is under review (whether a confidence figure
built from RSSI, duration and sighting count, or a proximity statement
built from RSSI alone, belongs in a client report), so these tests check
the lines are present without fixing what they say.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.tscm import reports

GOLDEN_DIR = Path(__file__).parent / "golden" / "tscm"
FLAGGED = "<flagged>"


# =============================================================================
# Fixtures
# =============================================================================


class _FrozenDatetime(datetime):
    """datetime with a fixed now(), so report IDs and timestamps are stable."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 24, 12, 0, 0, tzinfo=tz)


@pytest.fixture
def frozen(monkeypatch):
    """Fix the clock and the local timezone (BST in September, UTC+1)."""
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    monkeypatch.setattr(reports, "datetime", _FrozenDatetime)
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def report(frozen, tscm_survey):
    return reports.generate_report(**tscm_survey)


def _empty_survey():
    """A sweep that saw devices but flagged none: the legitimate 'nothing found'.

    Not zero devices scanned: that is inconclusive (see TestNothingDetected).
    """
    return {
        "sweep_id": 1,
        "sweep_data": {
            "started_at": "2026-09-24 09:00:00",
            "completed_at": "2026-09-24 09:20:00",
            "sweep_type": "quick",
            "results": {"wifi_count": 6, "wifi_client_count": 2, "bt_count": 11, "rf_count": 0},
        },
        "device_profiles": [],
        "capabilities": {},
        "timelines": [],
    }


# =============================================================================
# Golden-file helpers
# =============================================================================


def _normalise_text(text: str) -> str:
    """Drop incidental whitespace and the flagged lines' values."""
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        line = re.sub(r"^(\s+)(Signal|Interpretation|Note|Risk Score|Assessment): .+$", rf"\1\2: {FLAGGED}", line)
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _normalise_annex(annex: dict) -> dict:
    annex = json.loads(json.dumps(annex))  # a deep copy that also proves it serialises
    for tier in annex["findings"].values():
        for finding in tier:
            for key in ("risk_score", "description", "signal_classification"):
                finding[key] = FLAGGED
    return annex


def _normalise_csv(text: str) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(text)))
    device_header = rows[0]
    findings_at = rows.index(["--- FINDINGS SUMMARY ---"])
    findings_header = rows[findings_at + 1]

    def blank(row, header, columns):
        return [FLAGGED if header[i] in columns and i < len(row) else v for i, v in enumerate(row)]

    devices = [blank(r, device_header, {"risk_score"}) for r in rows[1:findings_at]]
    findings = [
        blank(
            r, findings_header, {"risk_score", "signal_strength", "signal_confidence", "description", "interpretation"}
        )
        for r in rows[findings_at + 2 :]
    ]
    return [device_header, *devices, *rows[findings_at : findings_at + 2], *findings]


def _golden(name: str, actual: str) -> None:
    path = GOLDEN_DIR / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing golden file {path}; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from its golden file"


# =============================================================================
# Golden files: one per output format
# =============================================================================


class TestGoldenFiles:
    def test_executive_summary(self, report):
        _golden("summary.txt", _normalise_text(report.executive_summary))

    def test_report_text(self, report):
        _golden("report.txt", _normalise_text(reports.get_pdf_report(report)))

    def test_json_annex(self, report):
        annex = _normalise_annex(reports.get_json_annex(report))
        _golden("annex.json", json.dumps(annex, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    def test_csv_annex(self, report):
        rows = _normalise_csv(reports.get_csv_annex(report))
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        _golden("annex.csv", out.getvalue())

    def test_empty_report_text(self, frozen):
        _golden("report_empty.txt", _normalise_text(reports.get_pdf_report(reports.generate_report(**_empty_survey()))))


# =============================================================================
# Structure and required content
# =============================================================================


class TestReportContent:
    def test_findings_land_in_the_right_tiers(self, report):
        assert [f.identifier for f in report.high_interest_findings] == ["4C:E6:76:12:34:56", "433.920"]
        assert [f.identifier for f in report.needs_review_findings] == ["F0:12:34:AB:CD:EF", "02:11:22:33:44:55"]
        assert [f.identifier for f in report.informational_findings] == ["00:1B:A9:77:88:99"]
        assert report.overall_risk_assessment == "elevated"
        assert report.key_findings_count == 4

    def test_report_carries_mandatory_sections(self, report):
        text = reports.get_pdf_report(report)
        for required in (
            "Report ID: TSCM-42-20260924120000",
            "Sweep ID: 42",
            "Site / Location: Head Office, 3rd floor boardroom",
            "Examiner: A. Examiner",
            "EXECUTIVE SUMMARY",
            "HIGH INTEREST FINDINGS",
            "FINDINGS REQUIRING REVIEW",
            "MEETING WINDOW SUMMARY",
            "SWEEP CAPABILITIES & LIMITATIONS",
            "SIGNAL ANALYSIS METHODOLOGY",
            "IMPORTANT DISCLAIMER",
            "END OF REPORT",
        ):
            assert required in text, required

    def test_every_key_finding_is_listed(self, report):
        text = reports.get_pdf_report(report)
        for f in report.high_interest_findings + report.needs_review_findings:
            assert f"Identifier: {f.identifier}" in text

    def test_statistics_and_baseline(self, report):
        summary = report.executive_summary
        assert "Total devices scanned: 50" in summary
        annex = reports.get_json_annex(report)
        assert annex["statistics"]["new_devices"] == 3
        assert annex["statistics"]["missing_devices"] == 1

    def test_annex_is_valid_json(self, report):
        json.dumps(reports.get_json_annex(report))


class TestSignalAssessmentUsesObservations:
    """Defect: the report read rssi_mean/observation_count/observation_duration_seconds,
    none of which a DeviceProfile provides, so every finding was assessed from
    nothing and came out 'minimal' with 'low' confidence."""

    def test_assessment_reflects_the_profile(self, report):
        airtag = report.high_interest_findings[0]
        assert airtag.signal_strength == "strong"  # rssi_current -48 dBm
        assert airtag.signal_confidence == "high"  # 80 minutes, 40 sightings

    def test_brief_weak_sighting_stays_low(self, report):
        speaker = report.needs_review_findings[0]
        assert speaker.signal_strength == "weak"  # -74 dBm
        assert speaker.signal_confidence == "low"  # 2 minutes, 2 sightings


class TestCsvAnnex:
    def test_device_rows_carry_their_finding_risk(self, report):
        """Defect: timelines carry no risk fields, so every device row read
        'informational', score 0, whatever the finding said."""
        rows = list(csv.DictReader(io.StringIO(reports.get_csv_annex(report).split("\r\n\r\n")[0])))
        by_id = {r["identifier"]: r for r in rows}
        assert by_id["4C:E6:76:12:34:56"]["risk_level"] == "high_interest"
        assert by_id["4C:E6:76:12:34:56"]["risk_score"] == "7"
        assert "airtag_detected(3)" in by_id["4C:E6:76:12:34:56"]["indicators"]
        assert by_id["00:1B:A9:77:88:99"]["risk_level"] == "informational"

    @pytest.mark.parametrize("payload", ['=HYPERLINK("http://x","x")', "+1+1", "-2+3", "@SUM(A1)"])
    def test_spreadsheet_formulas_are_neutralised(self, frozen, payload):
        """Bluetooth names are chosen by whoever owns the device. A name read as
        a formula would execute when the client opens the annex in Excel."""
        survey = _empty_survey()
        survey["device_profiles"] = [
            {"identifier": "AA:BB:CC:DD:EE:FF", "protocol": "bluetooth", "name": payload, "risk_level": "needs_review"}
        ]
        survey["timelines"] = [{"identifier": "AA:BB:CC:DD:EE:FF", "protocol": "bluetooth", "name": payload}]
        text = reports.get_csv_annex(reports.generate_report(**survey))
        cells = [c for row in csv.reader(io.StringIO(text)) for c in row]
        assert payload not in cells
        assert "'" + payload in cells


# =============================================================================
# Failure modes that matter for a client deliverable
# =============================================================================


class TestEmptySurvey:
    def test_produces_a_complete_nothing_found_report(self, frozen):
        report = reports.generate_report(**_empty_survey())
        text = reports.get_pdf_report(report)
        assert report.overall_risk_assessment == "low"
        assert "No significant indicators of surveillance activity were detected." in text
        assert "High Interest (require investigation): 0" in text
        assert "IMPORTANT DISCLAIMER" in text
        assert text.rstrip().endswith("=" * 70)

    def test_annexes_are_well_formed(self, frozen):
        report = reports.generate_report(**_empty_survey())
        annex = reports.get_json_annex(report)
        json.dumps(annex)
        assert annex["statistics"]["total_devices"] == 19
        rows = list(csv.reader(io.StringIO(reports.get_csv_annex(report))))
        assert rows[0][0] == "identifier"
        assert ["--- FINDINGS SUMMARY ---"] in rows

    def test_sweep_without_results_yet(self, frozen):
        """Defect: a running or aborted sweep has results = None in the
        database, and generate_report() raised AttributeError on it."""
        survey = _empty_survey()
        survey["sweep_data"].update(results=None, completed_at=None)
        report = reports.generate_report(**survey)
        assert "END OF REPORT" in reports.get_pdf_report(report)


class TestTimes:
    """Defect: SQLite stores UTC, but the report printed it as though it were
    local time, beside a 'Generated' time that really is local."""

    def test_sweep_start_is_shown_in_local_time(self, report):
        assert "Conducted: 2026-09-24 10:00" in report.executive_summary  # 09:00 UTC in BST

    def test_duration_of_a_completed_sweep(self, report):
        assert report.duration_minutes == 90

    def test_duration_of_a_running_sweep_uses_the_same_clock(self, frozen):
        survey = _empty_survey()
        survey["sweep_data"].update(started_at="2026-09-24 10:30:00", completed_at=None)  # 11:30 local
        report = reports.generate_report(**survey)
        assert report.duration_minutes == 30  # now() is 12:00 local


class TestMalformedDevice:
    @pytest.mark.parametrize(
        "device",
        [
            {},
            {"identifier": "AA:BB:CC:DD:EE:01", "protocol": None, "name": None, "risk_level": "high_interest"},
            {
                "identifier": "AA:BB:CC:DD:EE:02",
                "protocol": "bluetooth",
                "indicators": [{"type": None}],
                "risk_level": "high_interest",
            },
            {
                "identifier": "AA:BB:CC:DD:EE:03",
                "rssi_current": "strong",
                "detection_count": None,
                "risk_level": "needs_review",
            },
            {
                "identifier": "AA:BB:CC:DD:EE:04",
                "first_seen": "not a date",
                "last_seen": None,
                "risk_level": "needs_review",
            },
            {"identifier": "AA:BB:CC:DD:EE:05", "total_score": None, "risk_level": None},
        ],
        ids=["empty", "null-protocol", "null-indicator-type", "bad-rssi", "bad-times", "null-score"],
    )
    def test_report_still_generates(self, frozen, device):
        survey = _empty_survey()
        survey["device_profiles"] = [device]
        survey["timelines"] = [{"identifier": device.get("identifier")}]
        report = reports.generate_report(**survey)
        text = reports.get_pdf_report(report)
        json.dumps(reports.get_json_annex(report))
        reports.get_csv_annex(report)
        assert "END OF REPORT" in text
        if device.get("identifier") and device.get("risk_level") in ("high_interest", "needs_review"):
            assert device["identifier"] in text, "a malformed device must not silently vanish"


class TestUnicode:
    NAME = "Café Überwachung 監視 📡"

    def test_unicode_survives_every_format(self, frozen):
        survey = _empty_survey()
        survey["site_name"] = "Zürich — Sala Riunioni"
        survey["examiner_name"] = "Zoë Ångström"
        survey["device_profiles"] = [
            {
                "identifier": "AA:BB:CC:DD:EE:FF",
                "protocol": "bluetooth",
                "name": self.NAME,
                "risk_level": "needs_review",
            }
        ]
        survey["timelines"] = [{"identifier": "AA:BB:CC:DD:EE:FF", "protocol": "bluetooth", "name": self.NAME}]
        survey["meeting_summaries"] = [{"name": "Réunion du conseil", "start_time": "10:00", "devices_first_seen": 1}]
        report = reports.generate_report(**survey)

        text = reports.get_pdf_report(report)
        for s in (self.NAME, "Zürich — Sala Riunioni", "Zoë Ångström", "Réunion du conseil"):
            assert s in text
        assert self.NAME in json.dumps(reports.get_json_annex(report), ensure_ascii=False)
        assert self.NAME in reports.get_csv_annex(report)
        text.encode("utf-8")


class TestLargeSurvey:
    def test_five_thousand_findings(self, frozen):
        survey = _empty_survey()
        survey["device_profiles"] = [
            {
                "identifier": f"AA:BB:CC:{i // 65536:02X}:{i // 256 % 256:02X}:{i % 256:02X}",
                "protocol": "bluetooth",
                "risk_level": ("high_interest", "needs_review", "informational")[i % 3],
                "total_score": 6,
                "indicators": [{"type": "unknown_device", "description": "Not in baseline", "score": 1}],
                "rssi_current": -60,
                "detection_count": 5,
            }
            for i in range(5000)
        ]
        started = time.monotonic()
        report = reports.generate_report(**survey)
        text = reports.get_pdf_report(report)
        annex = reports.get_json_annex(report)
        reports.get_csv_annex(report)
        assert time.monotonic() - started < 10

        assert len(report.high_interest_findings) == 1667
        assert annex["statistics"]["high_interest_count"] == 1667
        assert text.count("   Identifier: ") == 1667 + 1667
        assert report.overall_risk_assessment == "high"


# =============================================================================
# Routes
# =============================================================================


class TestReportRoutes:
    @pytest.fixture
    def sweep(self, tscm_survey):
        sweep = dict(tscm_survey["sweep_data"])
        engine = MagicMock(device_profiles={})
        manager = MagicMock()
        manager.get_all_timelines.return_value = []
        caps = MagicMock()
        caps.to_dict.return_value = {}
        with (
            patch("routes.tscm.analysis.get_tscm_sweep", return_value=sweep),
            patch("routes.tscm.analysis.get_correlation_engine", return_value=engine),
            patch("utils.tscm.advanced.get_timeline_manager", return_value=manager),
            patch("utils.tscm.advanced.detect_sweep_capabilities", return_value=caps),
        ):
            yield sweep

    def test_pdf_route(self, client, sweep):
        resp = client.get("/tscm/report/pdf?sweep_id=42&site_name=Boardroom")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == "attachment; filename=tscm_report_42.txt"
        assert "Site / Location: Boardroom" in resp.get_data(as_text=True)

    @pytest.mark.parametrize("fmt", ["json", "csv"])
    def test_annex_route(self, client, sweep, fmt):
        resp = client.get(f"/tscm/report/annex?sweep_id=42&format={fmt}")
        assert resp.status_code == 200

    def test_running_sweep_does_not_500(self, client, sweep):
        sweep.update(results=None, completed_at=None, status="running")
        assert client.get("/tscm/report/pdf?sweep_id=42").status_code == 200


class TestNothingDetected:
    """A sweep that detected nothing is inconclusive, not clear.

    In an occupied building Wi-Fi and Bluetooth are never empty, so zero
    devices almost always means the equipment was not receiving. Reporting
    that as 'LOW, no significant indicators' would present a failed sweep
    to the client as a clean room.
    """

    def _survey(self, **results):
        survey = _empty_survey()
        survey["sweep_data"]["results"] = results
        survey["sweep_data"].update(wifi_enabled=True, bt_enabled=True, rf_enabled=True)
        return survey

    def test_zero_devices_is_inconclusive(self, frozen):
        report = reports.generate_report(**self._survey())
        text = reports.get_pdf_report(report)
        assert report.overall_risk_assessment == "inconclusive"
        assert "OVERALL ASSESSMENT: INCONCLUSIVE" in text
        assert "not evidence that the area is clear" in text
        assert "No significant indicators" not in text

    def test_a_device_seen_without_counts_is_not_inconclusive(self, frozen):
        """A running sweep has no result counts yet but may have seen devices."""
        survey = self._survey()
        survey["sweep_data"]["results"] = None
        survey["device_profiles"] = [{"identifier": "AA:BB:CC:DD:EE:FF", "protocol": "wifi"}]
        assert reports.generate_report(**survey).overall_risk_assessment == "low"

    def test_empty_enabled_band_is_called_out(self, frozen):
        report = reports.generate_report(**self._survey(wifi_count=0, bt_count=12, rf_count=0))
        assert report.overall_risk_assessment == "low"
        assert "Wi-Fi was enabled but detected no devices; verify the adapter" in report.executive_summary
        assert "Bluetooth was enabled" not in report.executive_summary

    def test_band_warnings_lead_the_limitations(self, frozen, tscm_survey):
        """The summary shows only the first three limitations."""
        tscm_survey["sweep_data"]["results"] = {"wifi_count": 0, "bt_count": 0, "rf_count": 4}
        report = reports.generate_report(**tscm_survey)
        assert report.limitations[:2] == [
            "Wi-Fi was enabled but detected no devices; verify the adapter",
            "Bluetooth was enabled but detected no devices; verify the adapter",
        ]
        assert "No coverage above 1.7 GHz" in report.limitations
        assert tscm_survey["capabilities"]["all_limitations"] == [
            "No coverage above 1.7 GHz",
            "Single-antenna Bluetooth; no direction finding",
        ], "the caller's capabilities must not be modified"

    def test_disabled_band_is_not_called_out(self, frozen):
        survey = self._survey(wifi_count=0, bt_count=12)
        survey["sweep_data"]["wifi_enabled"] = False
        assert "Wi-Fi was enabled" not in reports.generate_report(**survey).executive_summary

    def test_no_band_warnings_before_results_exist(self, frozen):
        survey = self._survey()
        survey["sweep_data"]["results"] = None
        assert not any("was enabled" in x for x in reports.generate_report(**survey).limitations)
