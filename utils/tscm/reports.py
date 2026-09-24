"""
TSCM Report Generation Module

Generates:
1. Client-safe PDF reports with executive summary
2. Technical annex (JSON + CSV) with device timelines and indicators

DISCLAIMER: All reports include mandatory disclaimers.
No packet data. No claims of confirmed surveillance.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from utils.tscm.signal_classification import (
    SIGNAL_ANALYSIS_DISCLAIMER,
    assess_signal,
)

logger = logging.getLogger("intercept.tscm.reports")

# =============================================================================
# Report Data Structures
# =============================================================================


@dataclass
class ReportFinding:
    """A single finding for the report."""

    identifier: str
    protocol: str
    name: str | None
    risk_level: str
    risk_score: int
    description: str
    indicators: list[dict] = field(default_factory=list)
    recommended_action: str = ""
    playbook_reference: str = ""
    # Signal classification data
    signal_strength: str | None = None  # minimal, weak, moderate, strong, very_strong
    signal_confidence: str | None = None  # low, medium, high
    signal_interpretation: str | None = None
    signal_caveats: list[str] = field(default_factory=list)
    # What was measured, for the client report (None when not recorded)
    rssi: float | None = None
    observed_seconds: float | None = None
    sightings: int | None = None


@dataclass
class ReportMeetingSummary:
    """Meeting window summary for report."""

    name: str | None
    start_time: str
    end_time: str | None
    duration_minutes: float
    devices_first_seen: int
    behavior_changes: int
    high_interest_devices: int


@dataclass
class TSCMReport:
    """
    Complete TSCM sweep report.

    Contains all data needed for both client-safe PDF and technical annex.
    """

    # Report metadata
    report_id: str
    generated_at: datetime
    sweep_id: int
    sweep_type: str

    # Location and context
    location: str | None = None
    examiner_name: str = ""
    baseline_id: int | None = None
    baseline_name: str | None = None

    # Executive summary
    executive_summary: str = ""
    overall_risk_assessment: str = "low"  # inconclusive, low, moderate, elevated, high
    key_findings_count: int = 0

    # Capabilities used
    capabilities: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    # Findings by risk tier
    high_interest_findings: list[ReportFinding] = field(default_factory=list)
    needs_review_findings: list[ReportFinding] = field(default_factory=list)
    informational_findings: list[ReportFinding] = field(default_factory=list)

    # Meeting window summaries
    meeting_summaries: list[ReportMeetingSummary] = field(default_factory=list)

    # Statistics
    total_devices_scanned: int = 0
    wifi_devices: int = 0
    wifi_clients: int = 0
    bluetooth_devices: int = 0
    rf_signals: int = 0
    new_devices: int = 0
    missing_devices: int = 0

    # Sweep duration
    sweep_start: datetime | None = None
    sweep_end: datetime | None = None
    duration_minutes: float = 0.0

    # Technical data (for annex only)
    device_timelines: list[dict] = field(default_factory=list)
    all_indicators: list[dict] = field(default_factory=list)
    baseline_diff: dict | None = None
    correlation_data: list[dict] = field(default_factory=list)


# =============================================================================
# Disclaimer Text
# =============================================================================

REPORT_DISCLAIMER = """
IMPORTANT DISCLAIMER

This report documents the findings of a Technical Surveillance Countermeasures
(TSCM) sweep conducted using electronic detection equipment. The following
limitations and considerations apply:

1. DETECTION LIMITATIONS: No TSCM sweep can guarantee detection of all
   surveillance devices. Sophisticated devices may evade detection.

2. FINDINGS ARE INDICATORS: All findings represent patterns and indicators,
   NOT confirmed surveillance devices. Each finding requires professional
   interpretation and may have legitimate explanations.

3. ENVIRONMENTAL FACTORS: Wireless signals are affected by building
   construction, interference, and other environmental factors that may
   impact detection accuracy.

4. POINT-IN-TIME ASSESSMENT: This report reflects conditions at the time
   of the sweep. Conditions may change after the assessment.

5. NOT LEGAL ADVICE: This report does not constitute legal advice. Consult
   qualified legal counsel for guidance on surveillance-related matters.

6. PRIVACY CONSIDERATIONS: Some detected devices may be legitimate personal
   devices of authorized individuals.

This report should be treated as confidential and distributed only to
authorized personnel on a need-to-know basis.
"""

ANNEX_DISCLAIMER = """
TECHNICAL ANNEX DISCLAIMER

This annex contains detailed technical data from the TSCM sweep. This data
is provided for documentation and audit purposes.

- No raw packet captures or intercepted communications are included
- Device identifiers (MAC addresses) are included for tracking purposes
- Signal strength values are approximate and environment-dependent
- Timeline data is time-bucketed to preserve privacy
- All interpretations require professional TSCM expertise

This data should be handled according to organizational data protection
policies and applicable privacy regulations.
"""


# =============================================================================
# Report Generation Functions
# =============================================================================


def generate_executive_summary(report: TSCMReport) -> str:
    """Generate executive summary text."""
    lines = []

    # Opening
    lines.append(f"TSCM Sweep Report - {report.location or 'Location Not Specified'}")
    lines.append(f"Conducted: {report.sweep_start.strftime('%Y-%m-%d %H:%M') if report.sweep_start else 'Unknown'}")
    lines.append(f"Duration: {report.duration_minutes:.0f} minutes")
    lines.append("")

    # Overall assessment: what needs doing, not an adjective derived from a count
    if report.overall_risk_assessment == "inconclusive":
        lines.append("OVERALL ASSESSMENT: INCONCLUSIVE")
        lines.append(
            "No devices were detected on any band. This usually means the sweep equipment "
            "was not receiving, and is not evidence that the area is clear."
        )
    else:
        actions = [
            _count_devices(len(findings), action)
            for findings, action in (
                (report.high_interest_findings, "investigation"),
                (report.needs_review_findings, "review"),
            )
            if findings
        ]
        if actions:
            lines.append(f"OVERALL ASSESSMENT: {', '.join(actions)}.")
        else:
            lines.append("OVERALL ASSESSMENT: No devices require investigation or review.")
            lines.append("No significant indicators of surveillance activity were detected.")
    lines.append("")

    # Key statistics
    lines.append("SCAN STATISTICS:")
    lines.append(f"  - Total devices scanned: {report.total_devices_scanned}")
    lines.append(f"    - WiFi access points: {report.wifi_devices}")
    lines.append(f"    - WiFi clients: {report.wifi_clients}")
    lines.append(f"    - Bluetooth devices: {report.bluetooth_devices}")
    lines.append(f"    - RF signals: {report.rf_signals}")
    lines.append("")

    # Findings summary
    lines.append("FINDINGS SUMMARY:")
    lines.append(f"  - High Interest (require investigation): {len(report.high_interest_findings)}")
    lines.append(f"  - Needs Review: {len(report.needs_review_findings)}")
    lines.append(f"  - Informational: {len(report.informational_findings)}")
    lines.append("")

    # Baseline comparison if available
    if report.baseline_name:
        lines.append(f"BASELINE COMPARISON (vs '{report.baseline_name}'):")
        lines.append(f"  - New devices: {report.new_devices}")
        lines.append(f"  - Missing devices: {report.missing_devices}")
        lines.append("")

    # Meeting window summary if available
    if report.meeting_summaries:
        lines.append("MEETING WINDOW ACTIVITY:")
        for meeting in report.meeting_summaries:
            lines.append(
                f"  - {meeting.name or 'Unnamed meeting'}: "
                f"{meeting.devices_first_seen} new devices, "
                f"{meeting.high_interest_devices} high interest"
            )
        lines.append("")

    # Limitations
    if report.limitations:
        lines.append("SWEEP LIMITATIONS:")
        for limit in report.limitations[:3]:  # Top 3 limitations
            lines.append(f"  - {limit}")
        lines.append("")

    return "\n".join(lines)


def _count_devices(n: int, action: str) -> str:
    return f"{n} device requires {action}" if n == 1 else f"{n} devices require {action}"


def _describe_measurements(finding: ReportFinding) -> str | None:
    """'-48 dBm, observed for 80 minutes (40 sightings)', from what was recorded."""
    parts = []
    if finding.rssi is not None:
        parts.append(f"{finding.rssi:.0f} dBm")
    if finding.observed_seconds is not None:
        minutes = round(finding.observed_seconds / 60)
        if finding.observed_seconds < 60:
            parts.append("observed for under a minute")
        else:
            parts.append(f"observed for {minutes} minute{'' if minutes == 1 else 's'}")
    text = ", ".join(parts)
    if finding.sightings is not None:
        sightings = f"{finding.sightings} sighting{'' if finding.sightings == 1 else 's'}"
        text = f"{text} ({sightings})" if text else sightings
    return text or None


def generate_findings_section(findings: list[ReportFinding], title: str) -> str:
    """Generate a findings section for the client report.

    States what was measured and the pattern observed. The derived signal
    confidence, interpretation and risk score stay in the technical annexes:
    they rest on RSSI, duration and a sum of unrelated indicator scores, and
    read to a client as more certain than they are.
    """
    if not findings:
        return f"{title}\n\nNo findings in this category.\n"

    lines = [title, "=" * len(title), ""]

    for i, finding in enumerate(findings, 1):
        lines.append(f"{i}. {finding.name or finding.identifier}")
        lines.append(f"   Protocol: {finding.protocol.upper()}")
        lines.append(f"   Identifier: {finding.identifier}")

        measurements = _describe_measurements(finding)
        if measurements:
            lines.append(f"   Signal: {measurements}")

        lines.append(f"   Assessment: {finding.description}")

        if finding.indicators:
            lines.append("   Indicators:")
            for ind in finding.indicators[:5]:  # Limit to 5 indicators
                lines.append(f"     - {ind.get('type', 'unknown')}: {ind.get('description', '')}")

        lines.append(f"   Recommended Action: {finding.recommended_action}")

        if finding.playbook_reference:
            lines.append(f"   Reference: {finding.playbook_reference}")

        # Include relevant caveats for high-interest findings
        if finding.signal_caveats and finding.risk_level == "high_interest":
            lines.append("   Note: " + finding.signal_caveats[0])

        lines.append("")

    return "\n".join(lines)


def generate_meeting_section(summaries: list[ReportMeetingSummary]) -> str:
    """Generate meeting window summary section."""
    if not summaries:
        return "MEETING WINDOW SUMMARY\n\nNo meeting windows were marked during this sweep.\n"

    lines = ["MEETING WINDOW SUMMARY", "=" * 22, ""]

    for meeting in summaries:
        lines.append(f"Meeting: {meeting.name or 'Unnamed'}")
        lines.append(f"  Time: {meeting.start_time} - {meeting.end_time or 'ongoing'}")
        lines.append(f"  Duration: {meeting.duration_minutes:.0f} minutes")
        lines.append(f"  Devices first seen during meeting: {meeting.devices_first_seen}")
        lines.append(f"  Behavior changes detected: {meeting.behavior_changes}")
        lines.append(f"  High interest devices active: {meeting.high_interest_devices}")

        if meeting.devices_first_seen > 0 or meeting.high_interest_devices > 0:
            lines.append("  NOTE: Meeting-correlated activity detected - see findings for details")
        lines.append("")

    lines.append("Meeting-correlated activity indicates temporal correlation only.")
    lines.append("Devices appearing during meetings may have legitimate explanations.")
    lines.append("")

    return "\n".join(lines)


def generate_pdf_content(report: TSCMReport) -> str:
    """
    Generate complete PDF report content.

    Returns plain text that can be converted to PDF.
    For actual PDF generation, use a library like reportlab or weasyprint.
    """
    sections = []

    # Header
    sections.append("=" * 70)
    sections.append("TECHNICAL SURVEILLANCE COUNTERMEASURES (TSCM) SWEEP REPORT")
    sections.append("=" * 70)
    sections.append("")
    sections.append(f"Report ID: {report.report_id}")
    sections.append(f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
    sections.append(f"Sweep ID: {report.sweep_id}")
    if report.location:
        sections.append(f"Site / Location: {report.location}")
    if report.examiner_name:
        sections.append(f"Examiner: {report.examiner_name}")
    sections.append("")

    # Executive Summary
    sections.append("-" * 70)
    sections.append("EXECUTIVE SUMMARY")
    sections.append("-" * 70)
    sections.append(report.executive_summary or generate_executive_summary(report))
    sections.append("")

    # High Interest Findings
    if report.high_interest_findings:
        sections.append("-" * 70)
        sections.append(generate_findings_section(report.high_interest_findings, "HIGH INTEREST FINDINGS"))

    # Needs Review Findings
    if report.needs_review_findings:
        sections.append("-" * 70)
        sections.append(generate_findings_section(report.needs_review_findings, "FINDINGS REQUIRING REVIEW"))

    # Meeting Window Summary
    if report.meeting_summaries:
        sections.append("-" * 70)
        sections.append(generate_meeting_section(report.meeting_summaries))

    # Capabilities & Limitations
    sections.append("-" * 70)
    sections.append("SWEEP CAPABILITIES & LIMITATIONS")
    sections.append("=" * 33)
    sections.append("")

    if report.capabilities:
        sections.append("Equipment Used:")
        sections.extend(f"  - {item}" for item in describe_equipment(report.capabilities))
        sections.append("")

    if report.limitations:
        sections.append("Limitations:")
        for limit in report.limitations:
            sections.append(f"  - {limit}")
        sections.append("")

    # Signal Analysis Note
    sections.append("-" * 70)
    sections.append("SIGNAL ANALYSIS METHODOLOGY")
    sections.append("=" * 27)
    sections.append(SIGNAL_ANALYSIS_DISCLAIMER.strip())
    sections.append("")

    # Disclaimer
    sections.append("-" * 70)
    sections.append(REPORT_DISCLAIMER)

    # Footer
    sections.append("")
    sections.append("=" * 70)
    sections.append("END OF REPORT")
    sections.append("=" * 70)

    return "\n".join(sections)


def describe_equipment(caps: dict) -> list[str]:
    """The equipment a sweep used, one line each, from its capabilities."""
    items = []
    if caps.get("wifi", {}).get("mode") != "unavailable":
        items.append(f"WiFi: {caps.get('wifi', {}).get('mode', 'unknown')} mode")
    if caps.get("bluetooth", {}).get("mode") != "unavailable":
        items.append(f"Bluetooth: {caps.get('bluetooth', {}).get('mode', 'unknown')}")
    if caps.get("rf", {}).get("available"):
        items.append(f"RF/SDR: {caps.get('rf', {}).get('device_type', 'unknown')}")
    return items


def report_html_context(report: TSCMReport) -> dict:
    """What the printable client report (templates/tscm_report.html) shows:
    the same content as the text report, structured for HTML. Values are
    left unescaped; the template escapes them."""

    def finding(f: ReportFinding) -> dict:
        return {
            "title": f.name or f.identifier,
            "protocol": f.protocol.upper(),
            "identifier": f.identifier,
            "measurements": _describe_measurements(f),
            "assessment": f.description,
            "indicators": [f"{ind.get('type', 'unknown')}: {ind.get('description', '')}" for ind in f.indicators[:5]],
            "action": f.recommended_action,
            "reference": f.playbook_reference,
            "note": f.signal_caveats[0] if f.signal_caveats and f.risk_level == "high_interest" else None,
        }

    return {
        "report": report,
        "generated": report.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "executive_summary": report.executive_summary or generate_executive_summary(report),
        "sections": [
            (title, [finding(f) for f in findings])
            for title, findings in (
                ("High interest findings", report.high_interest_findings),
                ("Findings requiring review", report.needs_review_findings),
            )
            if findings
        ],
        "meetings": report.meeting_summaries,
        "equipment": describe_equipment(report.capabilities) if report.capabilities else [],
        "limitations": report.limitations,
        "signal_note": SIGNAL_ANALYSIS_DISCLAIMER.strip(),
        "disclaimer": REPORT_DISCLAIMER.strip(),
    }


def generate_technical_annex_json(report: TSCMReport) -> dict:
    """
    Generate technical annex as JSON.

    Contains detailed device timelines, all indicators, and raw data
    for audit and further analysis.
    """
    return {
        "annex_type": "tscm_technical_annex",
        "report_id": report.report_id,
        "generated_at": report.generated_at.isoformat(),
        "sweep_id": report.sweep_id,
        "disclaimer": ANNEX_DISCLAIMER.strip(),
        "sweep_details": {
            "type": report.sweep_type,
            "location": report.location,
            "start_time": report.sweep_start.isoformat() if report.sweep_start else None,
            "end_time": report.sweep_end.isoformat() if report.sweep_end else None,
            "duration_minutes": report.duration_minutes,
            "baseline_id": report.baseline_id,
            "baseline_name": report.baseline_name,
        },
        "capabilities": report.capabilities,
        "limitations": report.limitations,
        "statistics": {
            "total_devices": report.total_devices_scanned,
            "wifi_devices": report.wifi_devices,
            "wifi_clients": report.wifi_clients,
            "bluetooth_devices": report.bluetooth_devices,
            "rf_signals": report.rf_signals,
            "new_devices": report.new_devices,
            "missing_devices": report.missing_devices,
            "high_interest_count": len(report.high_interest_findings),
            "needs_review_count": len(report.needs_review_findings),
            "informational_count": len(report.informational_findings),
        },
        "findings": {
            "high_interest": [
                {
                    "identifier": f.identifier,
                    "protocol": f.protocol,
                    "name": f.name,
                    "risk_score": f.risk_score,
                    "description": f.description,
                    "indicators": f.indicators,
                    "recommended_action": f.recommended_action,
                    "signal_classification": {
                        "strength": f.signal_strength,
                        "confidence": f.signal_confidence,
                        "interpretation": f.signal_interpretation,
                        "caveats": f.signal_caveats,
                    },
                }
                for f in report.high_interest_findings
            ],
            "needs_review": [
                {
                    "identifier": f.identifier,
                    "protocol": f.protocol,
                    "name": f.name,
                    "risk_score": f.risk_score,
                    "description": f.description,
                    "indicators": f.indicators,
                    "signal_classification": {
                        "strength": f.signal_strength,
                        "confidence": f.signal_confidence,
                        "interpretation": f.signal_interpretation,
                        "caveats": f.signal_caveats,
                    },
                }
                for f in report.needs_review_findings
            ],
        },
        "meeting_windows": [
            {
                "name": m.name,
                "start_time": m.start_time,
                "end_time": m.end_time,
                "duration_minutes": m.duration_minutes,
                "devices_first_seen": m.devices_first_seen,
                "behavior_changes": m.behavior_changes,
                "high_interest_devices": m.high_interest_devices,
            }
            for m in report.meeting_summaries
        ],
        "device_timelines": report.device_timelines,
        "all_indicators": report.all_indicators,
        "baseline_diff": report.baseline_diff,
        "correlations": report.correlation_data,
    }


def generate_technical_annex_csv(report: TSCMReport) -> str:
    """
    Generate device timeline data as CSV.

    Provides spreadsheet-compatible format for further analysis.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Timelines carry no risk fields; take them from the device's finding.
    findings_by_id = {
        f.identifier: f
        for f in report.high_interest_findings + report.needs_review_findings + report.informational_findings
    }

    # Header
    writer.writerow(
        [
            "identifier",
            "protocol",
            "name",
            "risk_level",
            "risk_score",
            "first_seen",
            "last_seen",
            "observation_count",
            "rssi_min",
            "rssi_max",
            "rssi_mean",
            "rssi_stability",
            "movement_pattern",
            "meeting_correlated",
            "indicators",
        ]
    )

    # Device data from timelines
    for timeline in report.device_timelines:
        finding = findings_by_id.get(timeline.get("identifier"))
        indicators = timeline.get("indicators") or (finding.indicators if finding else [])
        indicators_str = "; ".join(f"{i.get('type', '')}({i.get('score', 0)})" for i in indicators)

        signal = timeline.get("signal", {})
        metrics = timeline.get("metrics", {})
        movement = timeline.get("movement", {})
        meeting = timeline.get("meeting_correlation", {})

        writer.writerow(
            _formula_safe(
                [
                    timeline.get("identifier", ""),
                    timeline.get("protocol", ""),
                    timeline.get("name", ""),
                    timeline.get("risk_level") or (finding.risk_level if finding else "informational"),
                    timeline.get("risk_score") or (finding.risk_score if finding else 0),
                    metrics.get("first_seen", ""),
                    metrics.get("last_seen", ""),
                    metrics.get("total_observations", 0),
                    signal.get("rssi_min", ""),
                    signal.get("rssi_max", ""),
                    signal.get("rssi_mean", ""),
                    signal.get("stability", ""),
                    movement.get("pattern", ""),
                    meeting.get("correlated", False),
                    indicators_str,
                ]
            )
        )

    # Also add findings summary
    writer.writerow([])
    writer.writerow(["--- FINDINGS SUMMARY ---"])
    writer.writerow(
        [
            "identifier",
            "protocol",
            "risk_level",
            "risk_score",
            "signal_strength",
            "signal_confidence",
            "description",
            "interpretation",
            "recommended_action",
        ]
    )

    all_findings = report.high_interest_findings + report.needs_review_findings

    for finding in all_findings:
        writer.writerow(
            _formula_safe(
                [
                    finding.identifier,
                    finding.protocol,
                    finding.risk_level,
                    finding.risk_score,
                    finding.signal_strength or "",
                    finding.signal_confidence or "",
                    finding.description,
                    finding.signal_interpretation or "",
                    finding.recommended_action,
                ]
            )
        )

    return output.getvalue()


def _formula_safe(row: list) -> list:
    """Stop text cells being read as spreadsheet formulas.

    Device names are chosen by whoever owns the device (anyone can advertise
    a Bluetooth name), and the client opens the annex in Excel or
    LibreOffice. A leading apostrophe makes the cell literal text.
    """
    return ["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row]


# =============================================================================
# Report Builder
# =============================================================================


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_TRACKER_PATTERNS = {
    "airtag_detected": "an Apple AirTag",
    "tile_detected": "a Tile tracker",
    "smarttag_detected": "a Samsung SmartTag",
    "known_tracker": "a known tracker",
}


def _signal_inputs(profile: dict) -> tuple[float | None, float | None, int | None]:
    """RSSI, observed duration and sighting count for assess_signal().

    Correlation-engine profiles, which is what the report routes pass, carry
    rssi_current, detection_count and first_seen/last_seen. The summarised
    names (rssi_mean, observation_count, observation_duration_seconds) are
    preferred when a caller provides them.
    """
    rssi = next(
        (n for n in (_number(profile.get(k)) for k in ("rssi_mean", "rssi", "rssi_current")) if n is not None),
        None,
    )

    duration = _number(profile.get("observation_duration_seconds"))
    if duration is None:
        try:
            first = datetime.fromisoformat(profile["first_seen"])
            last = datetime.fromisoformat(profile["last_seen"])
            duration = max(0.0, (last - first).total_seconds())
        except (KeyError, TypeError, ValueError):
            duration = None

    count = _number(profile.get("observation_count"))
    if count is None:
        count = _number(profile.get("detection_count"))
    return rssi, duration, None if count is None else max(1, int(count))


class TSCMReportBuilder:
    """
    Builder for constructing TSCM reports from sweep data.

    Usage:
        builder = TSCMReportBuilder(sweep_id=123)
        builder.set_location("Conference Room A")
        builder.add_capabilities(capabilities_dict)
        builder.add_finding(finding)
        report = builder.build()
    """

    def __init__(self, sweep_id: int):
        self.sweep_id = sweep_id
        self.report = TSCMReport(
            report_id=f"TSCM-{sweep_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            generated_at=datetime.now(),
            sweep_id=sweep_id,
            sweep_type="standard",
        )

    def set_sweep_type(self, sweep_type: str) -> TSCMReportBuilder:
        self.report.sweep_type = sweep_type
        return self

    def set_location(self, location: str) -> TSCMReportBuilder:
        self.report.location = location
        return self

    def set_examiner(self, examiner_name: str) -> TSCMReportBuilder:
        self.report.examiner_name = examiner_name
        return self

    def set_baseline(self, baseline_id: int, baseline_name: str) -> TSCMReportBuilder:
        self.report.baseline_id = baseline_id
        self.report.baseline_name = baseline_name
        return self

    def set_sweep_times(self, start: datetime, end: datetime | None = None) -> TSCMReportBuilder:
        self.report.sweep_start = start
        self.report.sweep_end = end or datetime.now()
        self.report.duration_minutes = (self.report.sweep_end - self.report.sweep_start).total_seconds() / 60
        return self

    def add_capabilities(self, capabilities: dict) -> TSCMReportBuilder:
        self.report.capabilities = capabilities
        self.report.limitations = capabilities.get("all_limitations", [])
        return self

    def add_finding(self, finding: ReportFinding) -> TSCMReportBuilder:
        if finding.risk_level == "high_interest":
            self.report.high_interest_findings.append(finding)
        elif finding.risk_level in ["review", "needs_review"]:
            self.report.needs_review_findings.append(finding)
        else:
            self.report.informational_findings.append(finding)
        return self

    def add_findings_from_profiles(self, profiles: list[dict]) -> TSCMReportBuilder:
        """Add findings from correlation engine device profiles."""
        for profile in profiles:
            # Get signal classification data
            signal_data = self._classify_finding_signal(profile)
            rssi, observed_seconds, sightings = _signal_inputs(profile)

            finding = ReportFinding(
                identifier=profile.get("identifier") or "",
                protocol=profile.get("protocol") or "",
                name=profile.get("name"),
                risk_level=profile.get("risk_level") or "informational",
                risk_score=profile.get("total_score") or 0,
                description=self._generate_finding_description(profile),
                indicators=profile.get("indicators", []),
                recommended_action=profile.get("recommended_action", "monitor"),
                playbook_reference=self._get_playbook_reference(profile),
                signal_strength=signal_data["signal_strength"],
                signal_confidence=signal_data["signal_confidence"],
                signal_interpretation=signal_data["signal_interpretation"],
                signal_caveats=signal_data["signal_caveats"],
                rssi=rssi,
                observed_seconds=observed_seconds,
                sightings=sightings,
            )
            self.add_finding(finding)

        return self

    def _generate_finding_description(self, profile: dict) -> str:
        """Describe the pattern the primary indicator shows.

        Says what was seen, not where the device is or how likely it is to be
        a surveillance device: signal strength alone cannot place a device.
        """
        indicators = profile.get("indicators") or []
        protocol = (profile.get("protocol") or "Unknown").upper()

        if not indicators:
            return f"{protocol} device observed"

        primary = indicators[0]
        indicator_type = primary.get("type")
        if indicator_type in _TRACKER_PATTERNS:
            desc = f"Pattern consistent with {_TRACKER_PATTERNS[indicator_type]}"
        elif indicator_type == "audio_capable":
            desc = "Audio-capable device"
        elif indicator_type in ("hidden_identity", "hidden_ssid"):
            desc = "Concealed identity (hidden name or SSID)"
        else:
            desc = primary.get("description") or f"{protocol} device observed"

        further = len(indicators) - 1
        if further:
            desc += f" (+{further} further indicator{'' if further == 1 else 's'})"
        return desc

    def _classify_finding_signal(self, profile: dict) -> dict:
        """Extract signal classification data for a finding."""
        rssi, duration, count = _signal_inputs(profile)
        assessment = assess_signal(rssi, duration, count or 1)

        return {
            "signal_strength": assessment.signal_strength.value,
            "signal_confidence": assessment.confidence.value,
            "signal_interpretation": assessment.interpretation,
            "signal_caveats": assessment.caveats,
        }

    def _get_playbook_reference(self, profile: dict) -> str:
        """Get playbook reference based on profile."""
        risk_level = profile.get("risk_level", "informational")
        indicators = profile.get("indicators", [])

        # Check for tracker
        tracker_types = ["airtag_detected", "tile_detected", "smarttag_detected", "known_tracker"]
        if any(i.get("type") in tracker_types for i in indicators) and risk_level == "high_interest":
            return "PB-001 (Tracker Detection)"

        if risk_level == "high_interest":
            return "PB-002 (Suspicious Device)"
        elif risk_level in ["review", "needs_review"]:
            return "PB-003 (Unknown Device)"

        return ""

    def add_meeting_summary(self, summary: dict) -> TSCMReportBuilder:
        """Add a meeting window summary: flat counts, or the nested shape
        MeetingWindowSummary.to_dict() produces (device lists, counts under
        "summary")."""
        counts = summary.get("summary") or {}
        first_seen = summary.get("devices_first_seen", counts.get("new_devices", 0))
        if isinstance(first_seen, list):
            first_seen = len(first_seen)
        meeting = ReportMeetingSummary(
            name=summary.get("name"),
            start_time=summary.get("start_time") or "",
            end_time=summary.get("end_time"),
            duration_minutes=summary.get("duration_minutes") or 0,
            devices_first_seen=first_seen,
            behavior_changes=summary.get("behavior_changes", counts.get("behavior_changes", 0)),
            high_interest_devices=summary.get("high_interest_devices", counts.get("high_interest", 0)),
        )
        self.report.meeting_summaries.append(meeting)
        return self

    def add_statistics(
        self, wifi: int = 0, wifi_clients: int = 0, bluetooth: int = 0, rf: int = 0, new: int = 0, missing: int = 0
    ) -> TSCMReportBuilder:
        self.report.wifi_devices = wifi
        self.report.wifi_clients = wifi_clients
        self.report.bluetooth_devices = bluetooth
        self.report.rf_signals = rf
        self.report.total_devices_scanned = wifi + wifi_clients + bluetooth + rf
        self.report.new_devices = new
        self.report.missing_devices = missing
        return self

    def add_device_timelines(self, timelines: list[dict]) -> TSCMReportBuilder:
        self.report.device_timelines = timelines
        return self

    def add_all_indicators(self, indicators: list[dict]) -> TSCMReportBuilder:
        self.report.all_indicators = indicators
        return self

    def add_baseline_diff(self, diff: dict) -> TSCMReportBuilder:
        self.report.baseline_diff = diff
        return self

    def add_correlations(self, correlations: list[dict]) -> TSCMReportBuilder:
        self.report.correlation_data = correlations
        return self

    def build(self) -> TSCMReport:
        """Build and return the complete report."""
        # Calculate overall risk assessment
        if self.report.high_interest_findings:
            if len(self.report.high_interest_findings) >= 3:
                self.report.overall_risk_assessment = "high"
            else:
                self.report.overall_risk_assessment = "elevated"
        elif self.report.needs_review_findings:
            self.report.overall_risk_assessment = "moderate"
        elif not self.report.total_devices_scanned and not self.report.informational_findings:
            # Nothing seen at all points at the equipment, not at a clear room.
            self.report.overall_risk_assessment = "inconclusive"
        else:
            self.report.overall_risk_assessment = "low"

        self.report.key_findings_count = len(self.report.high_interest_findings) + len(
            self.report.needs_review_findings
        )

        # Generate executive summary
        self.report.executive_summary = generate_executive_summary(self.report)

        return self.report


# =============================================================================
# Report Generation API Functions
# =============================================================================


def generate_report(
    sweep_id: int,
    sweep_data: dict,
    device_profiles: list[dict],
    capabilities: dict,
    timelines: list[dict],
    baseline_diff: dict | None = None,
    meeting_summaries: list[dict] | None = None,
    correlations: list[dict] | None = None,
    categories: list[str] | None = None,
    site_name: str = "",
    examiner_name: str = "",
) -> TSCMReport:
    """
    Generate a complete TSCM report from sweep data.

    Args:
        sweep_id: Sweep ID
        sweep_data: Sweep dict from database
        device_profiles: List of DeviceProfile dicts from correlation engine
        capabilities: Capabilities dict
        timelines: Device timeline dicts
        baseline_diff: Optional baseline diff dict
        meeting_summaries: Optional meeting summaries
        correlations: Optional correlation data

    Returns:
        Complete TSCMReport
    """
    builder = TSCMReportBuilder(sweep_id)

    # Basic info
    builder.set_sweep_type(sweep_data.get("sweep_type", "standard"))
    if site_name:
        builder.set_location(site_name)
    if examiner_name:
        builder.set_examiner(examiner_name)

    # Parse times
    started_at = sweep_data.get("started_at")
    completed_at = sweep_data.get("completed_at")
    if started_at:
        if isinstance(started_at, str):
            started_at = _local_time(started_at)
        if completed_at and isinstance(completed_at, str):
            completed_at = _local_time(completed_at)
        builder.set_sweep_times(started_at, completed_at)

    # Capabilities
    builder.add_capabilities(capabilities)

    # Apply category filter before building findings
    if categories:
        _cat_map = {"needs_review": {"review", "needs_review"}}
        allowed = set()
        for c in categories:
            allowed |= _cat_map.get(c, {c})
        device_profiles = [p for p in device_profiles if p.get("risk_level", "informational") in allowed]

    # Add findings from profiles
    builder.add_findings_from_profiles(device_profiles)

    # Statistics (a sweep that has not completed has no results yet)
    results = sweep_data.get("results") or {}
    wifi_count = results.get("wifi_count")
    if wifi_count is None:
        wifi_count = len(results.get("wifi_devices", results.get("wifi", [])))

    wifi_client_count = results.get("wifi_client_count")
    if wifi_client_count is None:
        wifi_client_count = len(results.get("wifi_clients", []))

    bt_count = results.get("bt_count")
    if bt_count is None:
        bt_count = len(results.get("bt_devices", results.get("bluetooth", [])))

    rf_count = results.get("rf_count")
    if rf_count is None:
        rf_count = len(results.get("rf_signals", results.get("rf", [])))

    builder.add_statistics(
        wifi=wifi_count,
        wifi_clients=wifi_client_count,
        bluetooth=bt_count,
        rf=rf_count,
        new=baseline_diff.get("summary", {}).get("new_devices", 0) if baseline_diff else 0,
        missing=baseline_diff.get("summary", {}).get("missing_devices", 0) if baseline_diff else 0,
    )

    # An enabled band that saw nothing points at the equipment. Wi-Fi and
    # Bluetooth are never empty in an occupied building; a quiet RF band can
    # be genuine, so it is not called out. Leads the limitations because the
    # executive summary shows only the first three.
    if sweep_data.get("results") is not None:
        silent = [
            band
            for band, enabled, count in (
                ("Wi-Fi", "wifi_enabled", wifi_count + wifi_client_count),
                ("Bluetooth", "bt_enabled", bt_count),
            )
            if sweep_data.get(enabled, True) and count == 0
        ]
        builder.report.limitations = [
            f"{band} was enabled but detected no devices; verify the adapter" for band in silent
        ] + builder.report.limitations

    # Technical data
    builder.add_device_timelines(timelines)

    if baseline_diff:
        builder.add_baseline_diff(baseline_diff)
        if baseline_diff.get("baseline_name"):
            builder.set_baseline(baseline_diff.get("baseline_id"), baseline_diff["baseline_name"])

    if meeting_summaries:
        for summary in meeting_summaries:
            builder.add_meeting_summary(summary)

    if correlations:
        builder.add_correlations(correlations)

    # Extract all indicators
    all_indicators = []
    for profile in device_profiles:
        for ind in profile.get("indicators", []):
            all_indicators.append({"device": profile.get("identifier"), "protocol": profile.get("protocol"), **ind})
    builder.add_all_indicators(all_indicators)

    return builder.build()


def _local_time(value: str) -> datetime:
    """A stored timestamp as naive local time, the clock the report uses.

    SQLite's CURRENT_TIMESTAMP is UTC with no offset, so a naive value is
    read as UTC rather than as local time.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().replace(tzinfo=None)


def get_pdf_report(report: TSCMReport) -> str:
    """Get PDF-ready report content."""
    return generate_pdf_content(report)


def get_json_annex(report: TSCMReport) -> dict:
    """Get JSON technical annex."""
    return generate_technical_annex_json(report)


def get_csv_annex(report: TSCMReport) -> str:
    """Get CSV technical annex."""
    return generate_technical_annex_csv(report)
