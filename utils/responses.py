"""Standardized API response helpers.

Use these in new or modified routes for consistent JSON responses.
Existing routes are NOT being refactored to avoid unnecessary churn.
"""

from flask import jsonify


def api_success(data=None, message=None, status_code=200):
    """Return a success JSON response.

    Args:
        data: Optional dict of additional fields merged into the response.
        message: Optional human-readable message.
        status_code: HTTP status code (default 200).
    """
    payload = {"status": "success"}
    if message:
        payload["message"] = message
    if data:
        payload.update(data)
    return jsonify(payload), status_code


def api_error(message, status_code=400, error_type=None):
    """Return an error JSON response.

    Args:
        message: Human-readable error message.
        status_code: HTTP status code (default 400).
        error_type: Optional machine-readable error category (e.g. 'DEVICE_BUSY').
    """
    payload = {"status": "error", "message": message}
    if error_type:
        payload["error_type"] = error_type
    return jsonify(payload), status_code


def start_failure(error: BaseException | None, what: str = "decoder"):
    """Error response for a mode that failed to start.

    A missing executable is the operator's to fix, so it is a 400 naming the
    tool; anything else is a 500 carrying the underlying reason.
    """
    if isinstance(error, FileNotFoundError):
        tool = error.filename or "A required tool"
        return api_error(f"{tool} not found. Install it to use this mode.", 400, error_type="TOOL_MISSING")
    if error is not None:
        return api_error(f"Failed to start {what}: {error}", 500)
    return api_error(f"Failed to start {what}", 500)
