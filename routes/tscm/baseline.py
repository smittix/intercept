"""
TSCM Baseline Routes

Handles /baseline/*, /baselines endpoints.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import jsonify, request

from routes.tscm import (
    _baseline_recorder,
    tscm_bp,
)
from utils.database import (
    delete_tscm_baseline,
    get_active_tscm_baseline,
    get_all_tscm_baselines,
    get_tscm_baseline,
    get_tscm_sweep,
    set_active_tscm_baseline,
)
from utils.tscm.baseline import (
    get_comparison_for_active_baseline,
)

logger = logging.getLogger("intercept.tscm")


@tscm_bp.route("/baseline/record", methods=["POST"])
def record_baseline():
    """Start recording a new baseline."""
    data = request.get_json() or {}
    name = data.get("name", f"Baseline {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    location = data.get("location")
    description = data.get("description")

    baseline_id = _baseline_recorder.start_recording(name, location, description)

    return jsonify({"status": "success", "message": "Baseline recording started", "baseline_id": baseline_id})


@tscm_bp.route("/baseline/stop", methods=["POST"])
def stop_baseline():
    """Stop baseline recording."""
    result = _baseline_recorder.stop_recording()

    if "error" in result:
        return jsonify({"status": "error", "message": result["error"]})

    return jsonify({"status": "success", "message": "Baseline recording complete", **result})


@tscm_bp.route("/baseline/status")
def baseline_status():
    """Get baseline recording status."""
    return jsonify(_baseline_recorder.get_recording_status())


@tscm_bp.route("/baselines")
def list_baselines():
    """List all baselines."""
    baselines = get_all_tscm_baselines()
    return jsonify({"status": "success", "baselines": baselines})


@tscm_bp.route("/baseline/<int:baseline_id>")
def get_baseline(baseline_id: int):
    """Get a specific baseline."""
    baseline = get_tscm_baseline(baseline_id)
    if not baseline:
        return jsonify({"status": "error", "message": "Baseline not found"}), 404

    return jsonify({"status": "success", "baseline": baseline})


@tscm_bp.route("/baseline/<int:baseline_id>/activate", methods=["POST"])
def activate_baseline(baseline_id: int):
    """Set a baseline as active."""
    success = set_active_tscm_baseline(baseline_id)
    if not success:
        return jsonify({"status": "error", "message": "Baseline not found"}), 404

    return jsonify({"status": "success", "message": "Baseline activated"})


@tscm_bp.route("/baseline/<int:baseline_id>", methods=["DELETE"])
def remove_baseline(baseline_id: int):
    """Delete a baseline."""
    success = delete_tscm_baseline(baseline_id)
    if not success:
        return jsonify({"status": "error", "message": "Baseline not found"}), 404

    return jsonify({"status": "success", "message": "Baseline deleted"})


@tscm_bp.route("/baseline/active")
def get_active_baseline():
    """Get the currently active baseline."""
    baseline = get_active_tscm_baseline()
    if not baseline:
        return jsonify({"status": "success", "baseline": None})

    return jsonify({"status": "success", "baseline": baseline})


@tscm_bp.route("/baseline/compare", methods=["POST"])
def compare_against_baseline():
    """
    Compare provided device data against the active baseline.

    Expects JSON body with:
    - wifi_devices: list of WiFi devices (optional)
    - wifi_clients: list of WiFi clients (optional)
    - bt_devices: list of Bluetooth devices (optional)
    - rf_signals: list of RF signals (optional)

    Returns comparison showing new, missing, and matching devices.
    """
    data = request.get_json() or {}

    wifi_devices = data.get("wifi_devices")
    wifi_clients = data.get("wifi_clients")
    bt_devices = data.get("bt_devices")
    rf_signals = data.get("rf_signals")

    # Use the convenience function that gets active baseline
    comparison = get_comparison_for_active_baseline(
        wifi_devices=wifi_devices, wifi_clients=wifi_clients, bt_devices=bt_devices, rf_signals=rf_signals
    )

    if comparison is None:
        return jsonify({"status": "error", "message": "No active baseline set"}), 400

    return jsonify({"status": "success", "comparison": comparison})


# =============================================================================
# Baseline Diff & Health Endpoints
# =============================================================================


@tscm_bp.route("/baseline/diff/<int:baseline_id>/<int:sweep_id>")
def get_baseline_diff(baseline_id: int, sweep_id: int):
    """
    Get comprehensive diff between a baseline and a sweep.

    Shows new devices, missing devices, changed characteristics,
    and baseline health assessment.
    """
    try:
        from utils.tscm.advanced import diff_sweep_against_baseline

        baseline = get_tscm_baseline(baseline_id)
        if not baseline:
            return jsonify({"status": "error", "message": "Baseline not found"}), 404

        sweep = get_tscm_sweep(sweep_id)
        if not sweep:
            return jsonify({"status": "error", "message": "Sweep not found"}), 404

        diff = diff_sweep_against_baseline(baseline, sweep)
        if diff is None:
            return jsonify({"status": "error", "message": "Sweep has no results yet; compare once it completes"}), 409

        return jsonify({"status": "success", "diff": diff.to_dict()})

    except Exception as e:
        logger.error(f"Get baseline diff error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tscm_bp.route("/baseline/<int:baseline_id>/health")
def get_baseline_health(baseline_id: int):
    """Get health assessment for a baseline."""
    try:
        baseline = get_tscm_baseline(baseline_id)
        if not baseline:
            return jsonify({"status": "error", "message": "Baseline not found"}), 404

        from utils.tscm.advanced import baseline_age_hours

        age_hours = baseline_age_hours(baseline.get("created_at"))

        # Count devices
        total_devices = (
            len(baseline.get("wifi_networks", []))
            + len(baseline.get("bt_devices", []))
            + len(baseline.get("rf_frequencies", []))
        )

        # Determine health
        health = "healthy"
        score = 1.0
        reasons = []

        if age_hours > 168:
            health = "stale"
            score = 0.3
            reasons.append(f"Baseline is {age_hours:.0f} hours old (over 1 week)")
        elif age_hours > 72:
            health = "noisy"
            score = 0.6
            reasons.append(f"Baseline is {age_hours:.0f} hours old (over 3 days)")

        if total_devices < 3:
            score -= 0.2
            reasons.append(f"Baseline has few devices ({total_devices})")
            if health == "healthy":
                health = "noisy"

        return jsonify(
            {
                "status": "success",
                "health": {
                    "status": health,
                    "score": round(max(0, score), 2),
                    "age_hours": round(age_hours, 1),
                    "total_devices": total_devices,
                    "reasons": reasons,
                },
            }
        )

    except Exception as e:
        logger.error(f"Get baseline health error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
