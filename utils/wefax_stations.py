"""WeFax station database loader.

Loads and caches station data from data/wefax_stations.json. Provides
lookup by callsign and current-broadcast filtering based on UTC time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_stations_cache: list[dict] | None = None
_stations_by_callsign: dict[str, dict] = {}
_VALID_FREQUENCY_REFERENCES = {"auto", "carrier", "dial"}
WEFAX_USB_ALIGNMENT_OFFSET_KHZ = 1.9

_STATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "wefax_stations.json"


def load_stations() -> list[dict]:
    """Load all WeFax stations from JSON, caching on first call."""
    global _stations_cache, _stations_by_callsign

    if _stations_cache is not None:
        return _stations_cache

    if not _STATIONS_PATH.exists():
        log.warning("wefax_stations.json not found at %s", _STATIONS_PATH)
        _stations_cache = []
        return _stations_cache

    with open(_STATIONS_PATH) as f:
        data = json.load(f)

    _stations_cache = data.get("stations", [])
    _stations_by_callsign = {s["callsign"]: s for s in _stations_cache}
    return _stations_cache


def get_station(callsign: str) -> dict | None:
    """Get a single station by callsign."""
    load_stations()
    return _stations_by_callsign.get(callsign.upper())


def _normalize_frequency_reference(value: str | None) -> str:
    """Normalize and validate frequency reference token."""
    reference = str(value or "auto").strip().lower()
    if reference not in _VALID_FREQUENCY_REFERENCES:
        choices = ", ".join(sorted(_VALID_FREQUENCY_REFERENCES))
        raise ValueError(f"frequency_reference must be one of: {choices}")
    return reference


def _station_frequency_reference(station: dict, listed_frequency_khz: float) -> str:
    """Infer whether a station frequency entry is carrier or already USB dial."""
    for entry in station.get("frequencies", []):
        try:
            entry_khz = float(entry.get("khz"))
        except (TypeError, ValueError):
            continue
        if abs(entry_khz - listed_frequency_khz) > 0.001:
            continue
        entry_ref = str(entry.get("reference", "")).strip().lower()
        if entry_ref in ("carrier", "dial"):
            return entry_ref

    station_ref = str(station.get("frequency_reference", "")).strip().lower()
    if station_ref in ("carrier", "dial"):
        return station_ref

    # Most published marine WeFax channel lists are carrier frequencies.
    return "carrier"


def resolve_tuning_frequency_khz(
    listed_frequency_khz: float,
    station_callsign: str = "",
    frequency_reference: str = "auto",
) -> tuple[float, str, bool]:
    """Resolve listed frequency to the actual USB dial frequency.

    Args:
        listed_frequency_khz: Frequency value provided by UI/API.
        station_callsign: Station callsign used for metadata lookup.
        frequency_reference: One of auto/carrier/dial.

    Returns:
        (tuned_frequency_khz, resolved_reference, offset_applied)
    """
    listed = float(listed_frequency_khz)
    if listed <= 0:
        raise ValueError("frequency_khz must be greater than zero")

    requested_ref = _normalize_frequency_reference(frequency_reference)
    resolved_ref = requested_ref

    if requested_ref == "auto":
        station = get_station(station_callsign) if station_callsign else None
        if station:
            resolved_ref = _station_frequency_reference(station, listed)
        else:
            # For ad-hoc frequencies (no station metadata), treat input as dial.
            resolved_ref = "dial"

    if resolved_ref == "carrier":
        tuned = round(listed - WEFAX_USB_ALIGNMENT_OFFSET_KHZ, 3)
        if tuned <= 0:
            raise ValueError("frequency_khz too low after USB alignment offset")
        return tuned, resolved_ref, True

    return listed, resolved_ref, False


def get_current_broadcasts(callsign: str) -> list[dict]:
    """Return schedule entries closest to the current UTC time.

    Returns up to 3 entries: the most recent past broadcast and the
    next two upcoming ones, annotated with ``minutes_until`` or
    ``minutes_ago`` relative to now.
    """
    station = get_station(callsign)
    if not station:
        return []

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute

    schedule = station.get("schedule", [])
    if not schedule:
        return []

    # Convert schedule times to minutes-since-midnight for comparison
    entries: list[tuple[int, dict]] = []
    for entry in schedule:
        parts = entry["utc"].split(":")
        mins = int(parts[0]) * 60 + int(parts[1])
        entries.append((mins, entry))
    entries.sort(key=lambda x: x[0])

    # Find closest entries relative to now
    results = []
    for mins, entry in entries:
        diff = mins - current_minutes
        # Wrap around midnight
        if diff < -720:
            diff += 1440
        elif diff > 720:
            diff -= 1440

        annotated = dict(entry)
        if diff >= 0:
            annotated["minutes_until"] = diff
        else:
            annotated["minutes_ago"] = abs(diff)
        annotated["_sort_key"] = abs(diff)
        results.append(annotated)

    results.sort(key=lambda x: x["_sort_key"])

    # Return 3 nearest entries, clean up sort key
    for r in results:
        r.pop("_sort_key", None)
    return results[:3]
