"""Per-device configuration: display name, PPM correction, default gain, bias-T.

Each record is one row in the settings table under ``sdr.device.<key>``.
The key is the device serial where that identifies the device uniquely, so
the configuration follows the dongle across replugs. Devices without a
usable serial (none reported, the factory default most RTL-SDRs ship with,
or one shared with a sibling) are keyed by enumeration index instead, and
the configuration then follows the USB position.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from .base import SDRDevice

logger = logging.getLogger(__name__)

SETTING_PREFIX = "sdr.device."
NAME_MAX_LENGTH = 48
FIELDS = ("name", "ppm", "gain", "bias_t")

# Refused in names as a backstop: some device selectors build HTML by
# string concatenation, so markup characters never enter a name at all.
_NAME_FORBIDDEN = set("<>\"'`")

# RTL-SDRs leave the factory as 00000001 (a few as 00000000). Treated as
# absent even when only one is plugged in: otherwise adding a second such
# dongle would move the first from a serial key to an index key and orphan
# its configuration. With a single dongle the index is stable anyway.
_PLACEHOLDER_SERIALS = {"", "unknown", "n/a", "none", "0", "00000000", "00000001"}
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
KEY_RE = re.compile(r"^[a-z0-9]+:(sn:[A-Za-z0-9._-]{1,64}|idx:\d{1,3})$")


def device_key(device: SDRDevice, devices: list[SDRDevice]) -> tuple[str, bool]:
    """Return (key, keyed_by_serial) for a device among those detected with it."""
    sdr_type = device.sdr_type.value
    serial = (device.serial or "").strip()
    if serial.lower() not in _PLACEHOLDER_SERIALS and _SERIAL_RE.match(serial):
        siblings = [d for d in devices if d.sdr_type == device.sdr_type and (d.serial or "").strip() == serial]
        if len(siblings) == 1:
            return f"{sdr_type}:sn:{serial}", True
    return f"{sdr_type}:idx:{device.index}", False


def validate_device_name(name: Any) -> str | None:
    """Return a cleaned display name, or None to clear it.

    The name reaches every device selector and log line, so control and
    formatting characters (including bidi overrides) are refused outright.
    """
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Device name must be a string")
    name = name.strip()
    if not name:
        return None
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError(f"Device name must be at most {NAME_MAX_LENGTH} characters")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise ValueError("Device name must not contain control characters")
    if _NAME_FORBIDDEN.intersection(name):
        raise ValueError("Device name must not contain < > \" ' or `")
    return name


def validate_device_config(data: Any) -> dict[str, Any]:
    """Validate a configuration record. Blank or missing fields are dropped."""
    from utils.validation import validate_gain, validate_ppm

    if not isinstance(data, dict):
        raise ValueError("Device configuration must be an object")
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown device configuration field(s): {', '.join(sorted(unknown))}")

    config: dict[str, Any] = {}
    name = validate_device_name(data.get("name"))
    if name is not None:
        config["name"] = name
    if not _is_blank(data.get("ppm")):
        config["ppm"] = validate_ppm(data["ppm"])
    if not _is_blank(data.get("gain")):
        config["gain"] = validate_gain(data["gain"])
    if data.get("bias_t") is not None:
        if not isinstance(data["bias_t"], bool):
            raise ValueError("bias_t must be true or false")
        config["bias_t"] = data["bias_t"]
    return config


def get_device_config(key: str) -> dict[str, Any]:
    """Load a device's configuration, re-validated so a record written
    through the generic settings API cannot bypass the checks."""
    from utils.database import get_setting

    raw = get_setting(SETTING_PREFIX + key)
    if raw is None:
        return {}
    try:
        return validate_device_config(raw)
    except ValueError as e:
        logger.warning("Ignoring invalid configuration for SDR %s: %s", key, e)
        return {}


def set_device_config(key: str, data: Any) -> dict[str, Any]:
    """Validate and store a device's configuration; an empty one is deleted."""
    from utils.database import delete_setting, set_setting

    if not KEY_RE.match(key):
        raise ValueError("Invalid device key")
    config = validate_device_config(data)
    if config:
        set_setting(SETTING_PREFIX + key, config)
    else:
        delete_setting(SETTING_PREFIX + key)
    return config


def _any_config_stored() -> bool:
    import sqlite3

    from utils.database import get_all_settings

    try:
        return any(k.startswith(SETTING_PREFIX) for k in get_all_settings())
    except sqlite3.OperationalError:
        return False


def _detected_devices() -> list[SDRDevice]:
    from .detection import detect_all_devices, get_cached_devices

    cached = get_cached_devices()
    return cached if cached is not None else detect_all_devices()


def config_for_index(sdr_type: str, index: Any) -> dict[str, Any]:
    """Configuration for the device a start request names by type and index."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        return {}
    if not _any_config_stored():
        return {}  # the common case: no hardware probe on the start path
    try:
        devices = _detected_devices()
    except Exception as e:
        logger.debug("SDR detection failed while resolving device config: %s", e)
        devices = []
    for device in devices:
        if device.sdr_type.value == sdr_type and device.index == index:
            return get_device_config(device_key(device, devices)[0])
    return get_device_config(f"{sdr_type}:idx:{index}")


def apply_device_defaults(
    data: dict[str, Any],
    sdr_type: str | None = None,
    index: Any = None,
    fields: tuple[str, ...] = ("ppm", "gain", "bias_t"),
) -> dict[str, Any]:
    """Return a copy of a start request with the device's defaults filled in.

    Only fields the request leaves out (absent, null or blank) are filled,
    so an explicit value always wins. A blank field with no default is
    removed, so the route's own default applies exactly as if it had been
    omitted. The result still goes through the caller's usual validation;
    nothing here bypasses it.
    """
    if sdr_type is None:
        sdr_type = str(data.get("sdr_type") or "rtlsdr").lower()
    if index is None:
        index = data.get("device", 0)

    merged = dict(data)
    missing = [f for f in fields if _is_blank(data.get(f))]
    for field in missing:
        merged.pop(field, None)
    if not missing or data.get("rtl_tcp_host"):
        return merged  # nothing to fill, or a network device that is not ours
    config = config_for_index(sdr_type, index)
    for field in missing:
        if field in config:
            merged[field] = config[field]
    return merged


def display_names(devices: list[SDRDevice]) -> list[str]:
    """The configured name of each device, or its detected name if unset."""
    return [get_device_config(device_key(d, devices)[0]).get("name") or d.name for d in devices]


def describe_devices(devices: list[SDRDevice]) -> list[dict[str, Any]]:
    """Serialise detected devices with their configuration applied.

    ``name`` becomes the configured display name, so every existing device
    selector shows it without change; the detected name moves to
    ``hardware_name``.
    """
    result = []
    for device in devices:
        key, by_serial = device_key(device, devices)
        config = get_device_config(key)
        d = device.to_dict()
        d["hardware_name"] = device.name
        d["name"] = config.get("name") or device.name
        d["config_key"] = key
        d["keyed_by_serial"] = by_serial
        d["config"] = config
        result.append(d)
    return result


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
