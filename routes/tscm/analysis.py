"""
TSCM Analysis Routes

Handles /threats/*, /report/*, /wifi/*, /bluetooth/*, /playbooks/*,
/findings/*, /identity/*, /known-devices/*, /device/*/timeline,
and /timelines endpoints.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Response, jsonify, redirect, render_template, request, url_for

from routes.tscm import (
    _generate_assessment,
    tscm_bp,
)
from utils.database import (
    acknowledge_tscm_threat,
    get_active_tscm_baseline,
    get_meeting_windows,
    get_tscm_baseline,
    get_tscm_sweep,
    get_tscm_threat_summary,
    get_tscm_threats,
)
from utils.tscm.correlation import get_correlation_engine

logger = logging.getLogger("intercept.tscm")

_VALID_CATEGORIES = {"high_interest", "needs_review", "informational"}


def _parse_categories_param(raw: str) -> list[str] | None:
    """Parse a comma-separated `categories` query param into a validated list.

    Returns None (meaning "all") when the param is absent or covers all three
    categories, so callers can skip filtering in the common case.
    """
    if not raw:
        return None
    cats = [c.strip() for c in raw.split(",") if c.strip() in _VALID_CATEGORIES]
    if not cats or set(cats) == _VALID_CATEGORIES:
        return None
    return cats


# =============================================================================
# Threat Endpoints
# =============================================================================


@tscm_bp.route("/threats")
def list_threats():
    """List threats with optional filters."""
    sweep_id = request.args.get("sweep_id", type=int)
    severity = request.args.get("severity")
    acknowledged = request.args.get("acknowledged")
    limit = request.args.get("limit", 100, type=int)

    ack_filter = None
    if acknowledged is not None:
        ack_filter = acknowledged.lower() in ("true", "1", "yes")

    threats = get_tscm_threats(sweep_id=sweep_id, severity=severity, acknowledged=ack_filter, limit=limit)

    return jsonify({"status": "success", "threats": threats})


@tscm_bp.route("/threats/summary")
def threat_summary():
    """Get threat count summary by severity."""
    summary = get_tscm_threat_summary()
    return jsonify({"status": "success", "summary": summary})


@tscm_bp.route("/threats/<int:threat_id>", methods=["PUT"])
def update_threat(threat_id: int):
    """Update a threat (acknowledge, add notes)."""
    data = request.get_json() or {}

    if data.get("acknowledge"):
        notes = data.get("notes")
        success = acknowledge_tscm_threat(threat_id, notes)
        if not success:
            return jsonify({"status": "error", "message": "Threat not found"}), 404

    return jsonify({"status": "success", "message": "Threat updated"})


# =============================================================================
# Correlation & Findings Endpoints
# =============================================================================


@tscm_bp.route("/findings")
def get_findings():
    """
    Get comprehensive TSCM findings from the correlation engine.

    Returns all device profiles organized by risk level, cross-protocol
    correlations, and summary statistics with client-safe disclaimers.
    """
    correlation = get_correlation_engine()
    findings = correlation.get_all_findings()

    # Add client-safe disclaimer
    findings["legal_disclaimer"] = (
        "DISCLAIMER: This TSCM screening system identifies wireless and RF anomalies "
        "and indicators. Results represent potential items of interest, NOT confirmed "
        "surveillance devices. No content has been intercepted or decoded. Findings "
        "require professional analysis and verification. This tool does not prove "
        "malicious intent or illegal activity."
    )

    return jsonify({"status": "success", "findings": findings})


@tscm_bp.route("/findings/high-interest")
def get_high_interest():
    """Get only high-interest devices (score >= 6)."""
    correlation = get_correlation_engine()
    high_interest = correlation.get_high_interest_devices()

    return jsonify(
        {
            "status": "success",
            "count": len(high_interest),
            "devices": [d.to_dict() for d in high_interest],
            "disclaimer": (
                "High-interest classification indicates multiple indicators warrant "
                "investigation. This does NOT confirm surveillance activity."
            ),
        }
    )


@tscm_bp.route("/findings/correlations")
def get_correlations():
    """Get cross-protocol correlation analysis."""
    correlation = get_correlation_engine()
    correlations = correlation.correlate_devices()

    return jsonify(
        {
            "status": "success",
            "count": len(correlations),
            "correlations": correlations,
            "explanation": (
                "Correlations identify devices across different protocols (Bluetooth, "
                "WiFi, RF) that exhibit related behavior patterns. Cross-protocol "
                "activity is one indicator among many in TSCM analysis."
            ),
        }
    )


@tscm_bp.route("/findings/device/<identifier>")
def get_device_profile(identifier: str):
    """Get detailed profile for a specific device."""
    correlation = get_correlation_engine()

    # Search all protocols for the identifier
    for protocol in ["bluetooth", "wifi", "rf"]:
        key = f"{protocol}:{identifier}"
        if key in correlation.device_profiles:
            profile = correlation.device_profiles[key]
            return jsonify({"status": "success", "profile": profile.to_dict()})

    return jsonify({"status": "error", "message": "Device not found"}), 404


# =============================================================================
# Report Generation Endpoints
# =============================================================================


@tscm_bp.route("/report")
def generate_report():
    """
    Generate a comprehensive TSCM sweep report.

    Includes all findings, correlations, indicators, and recommended actions
    in a client-presentable format with appropriate disclaimers.
    """
    correlation = get_correlation_engine()
    findings = correlation.get_all_findings()

    # Build the report structure
    report = {
        "generated_at": datetime.now().isoformat(),
        "report_type": "TSCM Wireless Surveillance Screening",
        "executive_summary": {
            "total_devices_analyzed": findings["summary"]["total_devices"],
            "high_interest_items": findings["summary"]["high_interest"],
            "items_requiring_review": findings["summary"]["needs_review"],
            "cross_protocol_correlations": findings["summary"]["correlations_found"],
            "assessment": _generate_assessment(findings["summary"]),
        },
        "methodology": {
            "protocols_scanned": ["Bluetooth Low Energy", "WiFi 802.11", "RF Spectrum"],
            "analysis_techniques": [
                "Device fingerprinting",
                "Signal stability analysis",
                "Cross-protocol correlation",
                "Time-based pattern detection",
                "Manufacturer identification",
            ],
            "scoring_model": {
                "informational": "0-2 points - Known or expected devices",
                "needs_review": "3-5 points - Unusual devices requiring assessment",
                "high_interest": "6+ points - Multiple indicators warrant investigation",
            },
        },
        "findings": {
            "high_interest": findings["devices"]["high_interest"],
            "needs_review": findings["devices"]["needs_review"],
            "informational": findings["devices"]["informational"],
        },
        "correlations": findings["correlations"],
        "disclaimers": {
            "legal": (
                "This report documents findings from a wireless and RF surveillance "
                "screening. Results indicate anomalies and items of interest, NOT "
                "confirmed surveillance devices. No communications content has been "
                "intercepted, recorded, or decoded. This screening does not prove "
                "malicious intent, illegal activity, or the presence of surveillance "
                "equipment. All findings require professional verification."
            ),
            "technical": (
                "Detection capabilities are limited by equipment sensitivity, "
                "environmental factors, and the technical sophistication of any "
                "potential devices. Absence of findings does NOT guarantee absence "
                "of surveillance equipment."
            ),
            "recommendations": (
                "High-interest items should be investigated by qualified TSCM "
                "professionals using appropriate physical inspection techniques. "
                "This electronic sweep is one component of comprehensive TSCM."
            ),
        },
    }

    return jsonify({"status": "success", "report": report})


def _build_report():
    """The report for the sweep in the request (default: the current one).

    Returns (report, None), or (None, error response). Every client report
    and annex is built here, with the sweep's baseline comparison and its
    meeting windows.
    """
    from routes.tscm import _current_sweep_id
    from utils.tscm.advanced import (
        detect_sweep_capabilities,
        diff_sweep_against_baseline,
        generate_meeting_summary,
        get_timeline_manager,
    )
    from utils.tscm.reports import generate_report

    sweep_id = request.args.get("sweep_id", _current_sweep_id, type=int)
    if not sweep_id:
        return None, (jsonify({"status": "error", "message": "No sweep specified"}), 400)

    sweep = get_tscm_sweep(sweep_id)
    if not sweep:
        return None, (jsonify({"status": "error", "message": "Sweep not found"}), 404)

    correlation = get_correlation_engine()
    profiles = [p.to_dict() for p in correlation.device_profiles.values()]
    caps = detect_sweep_capabilities().to_dict()
    timeline_objects = get_timeline_manager().get_all_timelines()

    # The baseline this sweep ran against, if it ran against one.
    baseline_diff = None
    baseline = get_tscm_baseline(sweep["baseline_id"]) if sweep.get("baseline_id") else None
    if baseline:
        diff = diff_sweep_against_baseline(baseline, sweep)
        if diff is not None:
            baseline_diff = {**diff.to_dict(), "baseline_name": baseline.get("name") or f"#{baseline['id']}"}

    meetings = [
        generate_meeting_summary(window, timeline_objects, profiles).to_dict()
        for window in get_meeting_windows(sweep_id)
    ]
    for meeting in meetings:  # local ISO times, to the minute, as the rest of the report shows them
        for key in ("start_time", "end_time"):
            if meeting.get(key):
                meeting[key] = meeting[key][:16].replace("T", " ")

    report = generate_report(
        sweep_id=sweep_id,
        sweep_data=sweep,
        device_profiles=profiles,
        capabilities=caps,
        timelines=[t.to_dict() for t in timeline_objects],
        baseline_diff=baseline_diff,
        meeting_summaries=meetings,
        categories=_parse_categories_param(request.args.get("categories", "")),
        site_name=request.args.get("site_name", "").strip()[:200],
        examiner_name=request.args.get("examiner_name", "").strip()[:200],
    )
    return report, None


@tscm_bp.route("/report/print")
def get_printable_report():
    """
    Client-safe report as a printable page: executive summary, findings by
    risk tier, meeting windows, limitations and mandatory disclaimers. The
    browser's Print, with "Save as PDF", makes the PDF.
    """
    try:
        from utils.tscm.reports import report_html_context

        report, error = _build_report()
        if error:
            return error
        return render_template("tscm_report.html", **report_html_context(report))
    except Exception as e:
        logger.error(f"Generate printable report error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/report/pdf")
def get_pdf_report():
    """The printable report. Kept for existing links: it was plain text
    served under this name, and never a PDF."""
    return redirect(url_for("tscm.get_printable_report", **request.args))


@tscm_bp.route("/report/text")
def get_text_report():
    """The client report as plain text."""
    try:
        from utils.tscm.reports import get_pdf_report

        report, error = _build_report()
        if error:
            return error
        return Response(
            get_pdf_report(report),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename=tscm_report_{report.sweep_id}.txt"},
        )
    except Exception as e:
        logger.error(f"Generate text report error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/report/annex")
def get_technical_annex():
    """
    Generate technical annex (JSON + CSV).

    Contains device timelines, all indicators, and detailed data
    for audit purposes. No packet data included.
    """
    try:
        from utils.tscm.reports import get_csv_annex, get_json_annex

        report, error = _build_report()
        if error:
            return error

        if request.args.get("format", "json") == "csv":
            return Response(
                get_csv_annex(report),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=tscm_annex_{report.sweep_id}.csv"},
            )
        return jsonify({"status": "success", "annex": get_json_annex(report)})

    except Exception as e:
        logger.error(f"Generate technical annex error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# WiFi Advanced Indicators Endpoints
# =============================================================================


@tscm_bp.route("/wifi/advanced-indicators")
def get_wifi_advanced_indicators():
    """
    Get advanced WiFi indicators (Evil Twin, Probes, Deauth).

    These indicators require analysis of WiFi patterns.
    Some features require monitor mode.
    """
    try:
        from utils.tscm.advanced import get_wifi_detector

        detector = get_wifi_detector()

        return jsonify(
            {
                "status": "success",
                "indicators": detector.get_all_indicators(),
                "unavailable_features": detector.get_unavailable_features(),
                "disclaimer": (
                    "All indicators represent pattern detections, NOT confirmed attacks. "
                    "Further investigation is required."
                ),
            }
        )

    except Exception as e:
        logger.error(f"Get WiFi indicators error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/wifi/analyze-network", methods=["POST"])
def analyze_wifi_network():
    """
    Analyze a WiFi network for evil twin patterns.

    Compares against known networks to detect SSID spoofing.
    """
    try:
        from utils.tscm.advanced import get_wifi_detector

        data = request.get_json() or {}
        detector = get_wifi_detector()

        # Set known networks from baseline if available
        baseline = get_active_tscm_baseline()
        if baseline:
            detector.set_known_networks(baseline.get("wifi_networks", []))

        indicators = detector.analyze_network(data)

        return jsonify({"status": "success", "indicators": [i.to_dict() for i in indicators]})

    except Exception as e:
        logger.error(f"Analyze WiFi network error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Bluetooth Risk Explainability Endpoints
# =============================================================================


@tscm_bp.route("/bluetooth/<identifier>/explain")
def explain_bluetooth_risk(identifier: str):
    """
    Get human-readable risk explanation for a BLE device.

    Includes proximity estimate, tracker explanation, and
    recommended actions.
    """
    try:
        from utils.tscm.advanced import generate_ble_risk_explanation

        # Get device from correlation engine
        correlation = get_correlation_engine()
        profile = None
        key = f"bluetooth:{identifier.upper()}"
        if key in correlation.device_profiles:
            profile = correlation.device_profiles[key].to_dict()

        # Try to find device info
        device = {"mac": identifier}
        if profile:
            device["name"] = profile.get("name")
            device["rssi"] = profile.get("rssi_samples", [None])[-1] if profile.get("rssi_samples") else None

        # Check meeting status
        is_meeting = correlation.is_during_meeting()

        explanation = generate_ble_risk_explanation(device, profile, is_meeting)

        return jsonify({"status": "success", "explanation": explanation.to_dict()})

    except Exception as e:
        logger.error(f"Explain BLE risk error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/bluetooth/<identifier>/proximity")
def get_bluetooth_proximity(identifier: str):
    """Get proximity estimate for a BLE device."""
    try:
        from utils.tscm.advanced import estimate_ble_proximity

        rssi = request.args.get("rssi", type=int)
        if rssi is None:
            # Try to get from correlation engine
            correlation = get_correlation_engine()
            key = f"bluetooth:{identifier.upper()}"
            if key in correlation.device_profiles:
                profile = correlation.device_profiles[key]
                if profile.rssi_samples:
                    rssi = profile.rssi_samples[-1]

        if rssi is None:
            return jsonify({"status": "error", "message": "RSSI value required"}), 400

        proximity, explanation, distance = estimate_ble_proximity(rssi)

        return jsonify(
            {
                "status": "success",
                "proximity": {
                    "estimate": proximity.value,
                    "explanation": explanation,
                    "estimated_distance": distance,
                    "rssi_used": rssi,
                },
                "disclaimer": (
                    "Proximity estimates are approximate and affected by "
                    "environment, obstacles, and device characteristics."
                ),
            }
        )

    except Exception as e:
        logger.error(f"Get BLE proximity error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Operator Playbook Endpoints
# =============================================================================


@tscm_bp.route("/playbooks")
def list_playbooks():
    """List all available operator playbooks."""
    try:
        from utils.tscm.advanced import PLAYBOOKS

        # Return as array with id field for JavaScript compatibility
        playbooks_list = []
        for pid, pb in PLAYBOOKS.items():
            pb_dict = pb.to_dict()
            pb_dict["id"] = pid
            pb_dict["name"] = pb_dict.get("title", pid)
            pb_dict["category"] = pb_dict.get("risk_level", "general")
            playbooks_list.append(pb_dict)

        return jsonify({"status": "success", "playbooks": playbooks_list})

    except Exception as e:
        logger.error(f"List playbooks error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/playbooks/<playbook_id>")
def get_playbook(playbook_id: str):
    """Get a specific playbook."""
    try:
        from utils.tscm.advanced import PLAYBOOKS

        if playbook_id not in PLAYBOOKS:
            return jsonify({"status": "error", "message": "Playbook not found"}), 404

        return jsonify({"status": "success", "playbook": PLAYBOOKS[playbook_id].to_dict()})

    except Exception as e:
        logger.error(f"Get playbook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/findings/<identifier>/playbook")
def get_finding_playbook(identifier: str):
    """Get recommended playbook for a specific finding."""
    try:
        from utils.tscm.advanced import get_playbook_for_finding

        # Get profile
        correlation = get_correlation_engine()
        profile = None

        for protocol in ["bluetooth", "wifi", "rf"]:
            key = f"{protocol}:{identifier.upper()}"
            if key in correlation.device_profiles:
                profile = correlation.device_profiles[key].to_dict()
                break

        if not profile:
            return jsonify({"status": "error", "message": "Finding not found"}), 404

        playbook = get_playbook_for_finding(
            risk_level=profile.get("risk_level", "informational"), indicators=profile.get("indicators", [])
        )

        return jsonify(
            {
                "status": "success",
                "playbook": playbook.to_dict(),
                "suggested_next_steps": [f"Step {s.step_number}: {s.action}" for s in playbook.steps[:3]],
            }
        )

    except Exception as e:
        logger.error(f"Get finding playbook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Device Identity Endpoints (MAC-Randomization Resistant Detection)
# =============================================================================


@tscm_bp.route("/identity/ingest/ble", methods=["POST"])
def ingest_ble_observation():
    """
    Ingest a BLE observation for device identity clustering.

    This endpoint accepts BLE advertisement data and feeds it into the
    MAC-randomization resistant device detection engine.

    Expected JSON payload:
    {
        "timestamp": "2024-01-01T12:00:00",  // ISO format or omit for now
        "addr": "AA:BB:CC:DD:EE:FF",         // BLE address (may be randomized)
        "addr_type": "rpa",                   // public/random_static/rpa/nrpa/unknown
        "rssi": -65,                          // dBm
        "tx_power": -10,                      // dBm (optional)
        "adv_type": "ADV_IND",               // Advertisement type
        "manufacturer_id": 1234,              // Company ID (optional)
        "manufacturer_data": "0102030405",   // Hex string (optional)
        "service_uuids": ["uuid1", "uuid2"], // List of UUIDs (optional)
        "local_name": "Device Name",          // Advertised name (optional)
        "appearance": 960,                    // BLE appearance (optional)
        "packet_length": 31                   // Total packet length (optional)
    }
    """
    try:
        from utils.tscm.device_identity import ingest_ble_dict

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        session = ingest_ble_dict(data)

        return jsonify(
            {
                "status": "success",
                "session_id": session.session_id,
                "observation_count": len(session.observations),
            }
        )

    except Exception as e:
        logger.error(f"BLE ingestion error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/ingest/wifi", methods=["POST"])
def ingest_wifi_observation():
    """
    Ingest a WiFi observation for device identity clustering.

    Expected JSON payload:
    {
        "timestamp": "2024-01-01T12:00:00",
        "src_mac": "AA:BB:CC:DD:EE:FF",       // Client MAC (may be randomized)
        "dst_mac": "11:22:33:44:55:66",       // Destination MAC
        "bssid": "11:22:33:44:55:66",         // AP BSSID
        "ssid": "NetworkName",                 // SSID if available
        "frame_type": "probe_request",        // Frame type
        "rssi": -70,                          // dBm
        "channel": 6,                         // WiFi channel
        "ht_capable": true,                   // 802.11n capable
        "vht_capable": true,                  // 802.11ac capable
        "he_capable": false,                  // 802.11ax capable
        "supported_rates": [1, 2, 5.5, 11],  // Supported rates
        "vendor_ies": [["001122", 10]],      // [(OUI, length), ...]
        "probed_ssids": ["ssid1", "ssid2"]   // For probe requests
    }
    """
    try:
        from utils.tscm.device_identity import ingest_wifi_dict

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        session = ingest_wifi_dict(data)

        return jsonify(
            {
                "status": "success",
                "session_id": session.session_id,
                "observation_count": len(session.observations),
            }
        )

    except Exception as e:
        logger.error(f"WiFi ingestion error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/ingest/batch", methods=["POST"])
def ingest_batch_observations():
    """
    Ingest multiple observations in a single request.

    Expected JSON payload:
    {
        "ble": [<ble_observation>, ...],
        "wifi": [<wifi_observation>, ...]
    }
    """
    try:
        from utils.tscm.device_identity import ingest_ble_dict, ingest_wifi_dict

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        ble_count = 0
        wifi_count = 0

        for ble_obs in data.get("ble", []):
            ingest_ble_dict(ble_obs)
            ble_count += 1

        for wifi_obs in data.get("wifi", []):
            ingest_wifi_dict(wifi_obs)
            wifi_count += 1

        return jsonify(
            {
                "status": "success",
                "ble_ingested": ble_count,
                "wifi_ingested": wifi_count,
            }
        )

    except Exception as e:
        logger.error(f"Batch ingestion error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/clusters")
def get_device_clusters():
    """
    Get all device clusters (probable physical device identities).

    Query parameters:
    - min_confidence: Minimum cluster confidence (0-1, default 0)
    - protocol: Filter by protocol ('ble' or 'wifi')
    - risk_level: Filter by risk level ('high', 'medium', 'low', 'informational')
    """
    try:
        from utils.tscm.device_identity import get_identity_engine

        engine = get_identity_engine()
        min_conf = request.args.get("min_confidence", 0, type=float)
        protocol = request.args.get("protocol")
        risk_filter = request.args.get("risk_level")

        clusters = engine.get_clusters(min_confidence=min_conf)

        if protocol:
            clusters = [c for c in clusters if c.protocol == protocol]

        if risk_filter:
            clusters = [c for c in clusters if c.risk_level.value == risk_filter]

        return jsonify(
            {
                "status": "success",
                "count": len(clusters),
                "clusters": [c.to_dict() for c in clusters],
                "disclaimer": (
                    "Clusters represent PROBABLE device identities based on passive "
                    "fingerprinting. Results are statistical correlations, not "
                    "confirmed matches. False positives/negatives are expected."
                ),
            }
        )

    except Exception as e:
        logger.error(f"Get clusters error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/clusters/high-risk")
def get_high_risk_clusters():
    """Get device clusters with HIGH risk level."""
    try:
        from utils.tscm.device_identity import get_identity_engine

        engine = get_identity_engine()
        clusters = engine.get_high_risk_clusters()

        return jsonify(
            {
                "status": "success",
                "count": len(clusters),
                "clusters": [c.to_dict() for c in clusters],
                "disclaimer": (
                    "High-risk classification indicates multiple behavioral indicators "
                    "consistent with potential surveillance devices. This does NOT "
                    "confirm surveillance activity. Professional verification required."
                ),
            }
        )

    except Exception as e:
        logger.error(f"Get high-risk clusters error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/summary")
def get_identity_summary():
    """
    Get summary of device identity analysis.

    Returns statistics, cluster counts by risk level, and monitoring period.
    """
    try:
        from utils.tscm.device_identity import get_identity_engine

        engine = get_identity_engine()
        summary = engine.get_summary()

        return jsonify({"status": "success", "summary": summary})

    except Exception as e:
        logger.error(f"Get identity summary error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/finalize", methods=["POST"])
def finalize_identity_sessions():
    """
    Finalize all active sessions and complete clustering.

    Call this at the end of a monitoring period to ensure all observations
    are properly clustered and assessed.
    """
    try:
        from utils.tscm.device_identity import get_identity_engine

        engine = get_identity_engine()
        engine.finalize_all_sessions()
        summary = engine.get_summary()

        return jsonify({"status": "success", "message": "All sessions finalized", "summary": summary})

    except Exception as e:
        logger.error(f"Finalize sessions error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/reset", methods=["POST"])
def reset_identity_engine():
    """
    Reset the device identity engine.

    Clears all sessions, clusters, and monitoring state.
    """
    try:
        from utils.tscm.device_identity import reset_identity_engine as reset_engine

        reset_engine()

        return jsonify({"status": "success", "message": "Device identity engine reset"})

    except Exception as e:
        logger.error(f"Reset identity engine error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/identity/cluster/<cluster_id>")
def get_cluster_detail(cluster_id: str):
    """Get detailed information for a specific cluster."""
    try:
        from utils.tscm.device_identity import get_identity_engine

        engine = get_identity_engine()

        if cluster_id not in engine.clusters:
            return jsonify({"status": "error", "message": "Cluster not found"}), 404

        cluster = engine.clusters[cluster_id]

        return jsonify({"status": "success", "cluster": cluster.to_dict()})

    except Exception as e:
        logger.error(f"Get cluster detail error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Device Timeline Endpoints
# =============================================================================


@tscm_bp.route("/device/<identifier>/timeline")
def get_device_timeline_endpoint(identifier: str):
    """
    Get timeline of observations for a device.

    Shows behavior over time including RSSI stability, presence,
    and meeting window correlation.
    """
    try:
        from utils.database import get_device_timeline
        from utils.tscm.advanced import get_timeline_manager

        protocol = request.args.get("protocol", "bluetooth")
        since_hours = request.args.get("since_hours", 24, type=int)

        # Try in-memory timeline first
        manager = get_timeline_manager()
        timeline = manager.get_timeline(identifier, protocol)

        # Also get stored timeline from database
        stored = get_device_timeline(identifier, since_hours=since_hours)

        result = {
            "identifier": identifier,
            "protocol": protocol,
            "observations": stored,
        }

        if timeline:
            result["metrics"] = {
                "first_seen": timeline.first_seen.isoformat() if timeline.first_seen else None,
                "last_seen": timeline.last_seen.isoformat() if timeline.last_seen else None,
                "total_observations": timeline.total_observations,
                "presence_ratio": round(timeline.presence_ratio, 2),
            }
            result["signal"] = {
                "rssi_min": timeline.rssi_min,
                "rssi_max": timeline.rssi_max,
                "rssi_mean": round(timeline.rssi_mean, 1) if timeline.rssi_mean else None,
                "stability": round(timeline.rssi_stability, 2),
            }
            result["movement"] = {
                "appears_stationary": timeline.appears_stationary,
                "pattern": timeline.movement_pattern,
            }
            result["meeting_correlation"] = {
                "correlated": timeline.meeting_correlated,
                "observations_during_meeting": timeline.meeting_observations,
            }

        return jsonify({"status": "success", "timeline": result})

    except Exception as e:
        logger.error(f"Get device timeline error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/timelines")
def get_all_device_timelines():
    """Get all device timelines."""
    try:
        from utils.tscm.advanced import get_timeline_manager

        manager = get_timeline_manager()
        timelines = manager.get_all_timelines()

        return jsonify({"status": "success", "count": len(timelines), "timelines": [t.to_dict() for t in timelines]})

    except Exception as e:
        logger.error(f"Get all timelines error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# Known-Good Registry (Whitelist) Endpoints
# =============================================================================


@tscm_bp.route("/known-devices", methods=["GET"])
def list_known_devices():
    """List all known-good devices."""
    from utils.database import get_all_known_devices

    location = request.args.get("location")
    scope = request.args.get("scope")

    devices = get_all_known_devices(location=location, scope=scope)

    return jsonify({"status": "success", "count": len(devices), "devices": devices})


@tscm_bp.route("/known-devices", methods=["POST"])
def add_known_device_endpoint():
    """
    Add a device to the known-good registry.

    Known devices remain visible but receive reduced risk scores.
    They are NOT suppressed from reports (preserves audit trail).
    """
    from utils.database import add_known_device

    data = request.get_json() or {}

    identifier = data.get("identifier")
    protocol = data.get("protocol")

    if not identifier or not protocol:
        return jsonify({"status": "error", "message": "identifier and protocol are required"}), 400

    device_id = add_known_device(
        identifier=identifier,
        protocol=protocol,
        name=data.get("name"),
        description=data.get("description"),
        location=data.get("location"),
        scope=data.get("scope", "global"),
        added_by=data.get("added_by"),
        score_modifier=data.get("score_modifier", -2),
        metadata=data.get("metadata"),
    )

    return jsonify({"status": "success", "message": "Device added to known-good registry", "device_id": device_id})


@tscm_bp.route("/known-devices/<identifier>", methods=["GET"])
def get_known_device_endpoint(identifier: str):
    """Get a known device by identifier."""
    from utils.database import get_known_device

    device = get_known_device(identifier)
    if not device:
        return jsonify({"status": "error", "message": "Device not found"}), 404

    return jsonify({"status": "success", "device": device})


@tscm_bp.route("/known-devices/<identifier>", methods=["DELETE"])
def delete_known_device_endpoint(identifier: str):
    """Remove a device from the known-good registry."""
    from utils.database import delete_known_device

    success = delete_known_device(identifier)
    if not success:
        return jsonify({"status": "error", "message": "Device not found"}), 404

    return jsonify({"status": "success", "message": "Device removed from known-good registry"})


@tscm_bp.route("/known-devices/check/<identifier>")
def check_known_device(identifier: str):
    """Check if a device is in the known-good registry."""
    from utils.database import is_known_good_device

    location = request.args.get("location")
    result = is_known_good_device(identifier, location=location)

    return jsonify({"status": "success", "is_known": result is not None, "details": result})
