"""Activity feed: observations from every mode that emits them."""

from __future__ import annotations

import time

from flask import Blueprint, Response, jsonify, request

import config
from utils import observations
from utils.sse import sse_stream_fanout

observations_bp = Blueprint("observations", __name__, url_prefix="/observations")


def _float_arg(name: str) -> float | None:
    value = request.args.get(name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError as e:
        raise ValueError(f"{name} must be a number") from e


@observations_bp.route("")
def list_observations() -> Response:
    """Observations, newest first.

    Query: source (comma separated), identifier (matches the entity too),
    since and until (epoch seconds) or window_minutes, limit (max 1000).
    """
    try:
        since = _float_arg("since")
        until = _float_arg("until")
        window = _float_arg("window_minutes")
        limit = int(request.args.get("limit", 200))
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    if window is not None and since is None:
        since = time.time() - window * 60
    sources = [s for s in request.args.get("source", "").split(",") if s.strip()] or None
    rows = observations.query(
        source=sources, identifier=request.args.get("identifier") or None, since=since, until=until, limit=limit
    )
    return jsonify({"status": "success", "count": len(rows), "observations": rows})


@observations_bp.route("/stream")
def stream_observations() -> Response:
    """Live tail of new observations (SSE)."""
    response = Response(
        sse_stream_fanout(source_queue=observations.live_queue, channel_key="observations"),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@observations_bp.route("/stats")
def observation_stats() -> Response:
    """Per-source emitted and rate-limited counts, and the retention policy."""
    return jsonify(
        {
            "status": "success",
            "sources": observations.stats(),
            "retention": {
                "max_age_hours": config.OBSERVATION_RETENTION_HOURS,
                "max_rows": config.OBSERVATION_MAX_ROWS,
            },
            "rate_limit": {
                "per_identifier_seconds": {**observations.MIN_INTERVAL, "default": observations.DEFAULT_MIN_INTERVAL},
                "per_source_per_second": observations.SOURCE_RATE,
                "per_source_burst": observations.SOURCE_BURST,
            },
        }
    )
