"""First-run / Unraid installation wizard API."""

from __future__ import annotations

from flask import Blueprint, Response

from utils.database import get_setting, set_setting
from utils.logging import get_logger
from utils.responses import api_error, api_success
from utils.unraid import (
    running_in_docker,
    running_on_unraid,
    storage_advice,
    usb_passthrough_present,
)

logger = get_logger("intercept.setup")

setup_bp = Blueprint("setup", __name__, url_prefix="/setup")

SETUP_COMPLETE_KEY = "setup.complete.v1"


def _observer_configured() -> tuple[bool, float, float]:
    try:
        from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE
    except Exception:
        return False, 0.0, 0.0
    configured = DEFAULT_LATITUDE != 0.0 or DEFAULT_LONGITUDE != 0.0
    return configured, float(DEFAULT_LATITUDE), float(DEFAULT_LONGITUDE)


def _cached_sdr_summary() -> list[dict[str, object]]:
    try:
        from utils.sdr.detection import get_cached_devices
    except Exception:
        return []
    devices = get_cached_devices()
    if not devices:
        return []
    summary: list[dict[str, object]] = []
    for device in devices:
        sdr_type = device.sdr_type.value if hasattr(device.sdr_type, "value") else str(device.sdr_type)
        summary.append(
            {
                "type": sdr_type,
                "index": device.index,
                "name": device.name,
                "serial": device.serial or "",
            }
        )
    return summary


def build_setup_status() -> dict[str, object]:
    """Return wizard state without probing USB hardware."""
    from utils.database import get_instance_dir

    complete = bool(get_setting(SETUP_COMPLETE_KEY, False))
    observer_ok, lat, lon = _observer_configured()
    instance_dir = get_instance_dir()
    try:
        from config import ADMIN_PASSWORD, ADMIN_USERNAME
    except Exception:
        admin_username, default_admin = "admin", True
    else:
        admin_username = ADMIN_USERNAME
        default_admin = ADMIN_PASSWORD == "admin"

    return {
        "complete": complete,
        "platform": {
            "unraid": running_on_unraid(),
            "docker": running_in_docker(),
            "usb_passthrough": usb_passthrough_present(),
        },
        "storage": storage_advice(instance_dir),
        "observer": {
            "configured": observer_ok,
            "lat": lat,
            "lon": lon,
        },
        "admin": {
            "username": admin_username,
            "using_default_password": default_admin,
        },
        "sdr_devices": _cached_sdr_summary(),
    }


@setup_bp.route("/status", methods=["GET"])
def setup_status() -> Response:
    """Current first-run / Unraid wizard status."""
    try:
        return api_success(data=build_setup_status())
    except Exception as exc:
        logger.error("Error building setup status: %s", exc)
        return api_error(str(exc), 500)


@setup_bp.route("/complete", methods=["POST"])
def mark_setup_complete() -> Response:
    """Persist setup completion in SQLite so it survives browser/localStorage."""
    try:
        set_setting(SETUP_COMPLETE_KEY, True)
        return api_success(data={"complete": True}, message="Setup marked complete")
    except Exception as exc:
        logger.error("Error marking setup complete: %s", exc)
        return api_error(str(exc), 500)
