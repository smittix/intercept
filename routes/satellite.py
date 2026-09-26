"""Satellite tracking routes."""

from __future__ import annotations

import contextlib
import math
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, Response, jsonify, make_response, render_template, request

from config import SHARED_OBSERVER_LOCATION_ENABLED
from utils import tle_store
from utils.database import (
    add_tracked_satellite,
    bulk_add_tracked_satellites,
    get_tracked_satellites,
    remove_tracked_satellite,
    update_tracked_satellite,
)
from utils.logging import satellite_logger as logger
from utils.responses import api_error
from utils.sse import sse_stream_fanout
from utils.validation import validate_elevation, validate_hours, validate_latitude, validate_longitude

satellite_bp = Blueprint("satellite", __name__, url_prefix="/satellite")

# Cache skyfield timescale to avoid re-downloading/re-parsing per request
_cached_timescale = None


def _get_timescale():
    global _cached_timescale
    if _cached_timescale is None:
        from skyfield.api import load

        # Use bundled timescale data so the first request does not block on network I/O.
        _cached_timescale = load.timescale(builtin=True)
    return _cached_timescale


# Maximum response size for external requests (1MB)
MAX_RESPONSE_SIZE = 1024 * 1024

# Allowed hosts for TLE fetching
ALLOWED_TLE_HOSTS = ["celestrak.org", "celestrak.com", "www.celestrak.org", "www.celestrak.com"]


def _get_tle_cache() -> dict:
    """All TLEs from the unified store."""
    return tle_store.all_tles()


# Ground track cache: key=(sat_name, tle_line1[:20]) -> (track_data, computed_at_timestamp)
# TTL is 1800 seconds (30 minutes)
_track_cache: dict = {}
_TRACK_CACHE_TTL = 1800

# Thread pool for background ground-track computation (non-blocking from 1Hz tracker loop)
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

_track_executor = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="sat-track")
_track_in_progress: set = set()  # cache keys currently being computed
_pass_cache: dict = {}
_PASS_CACHE_TTL = 300

_BUILTIN_NORAD_TO_KEY = {
    25544: "ISS",
    40069: "METEOR-M2",
    57166: "METEOR-M2-3",
    59051: "METEOR-M2-4",
}


def _load_db_satellites_into_cache():
    """Load user-tracked satellites from DB into the TLE store."""
    try:
        db_sats = get_tracked_satellites()
        new_entries: dict = {}
        current = _get_tle_cache()
        for sat in db_sats:
            if sat["tle_line1"] and sat["tle_line2"]:
                cache_key = sat["name"].replace(" ", "-").upper()
                if cache_key not in current:
                    new_entries[cache_key] = (sat["name"], sat["tle_line1"], sat["tle_line2"])
        if new_entries:
            tle_store.update(new_entries)
            logger.info(f"Loaded {len(new_entries)} user-tracked satellites into TLE store")
    except Exception as e:
        logger.warning(f"Failed to load DB satellites into TLE store: {e}")


def get_cached_tle(name: str) -> tuple[str, str, str] | None:
    """Return (name, line1, line2) from the live TLE store, or None if not found."""
    return _get_tle_cache().get(name)


def _normalize_satellite_name(value: object) -> str:
    """Normalize satellite identifiers for loose name matching."""
    return str(value or "").strip().replace(" ", "-").upper()


def _get_tracked_satellite_maps() -> tuple[dict[int, dict], dict[str, dict]]:
    """Return tracked satellites indexed by NORAD ID and normalized name."""
    by_norad: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    try:
        for sat in get_tracked_satellites():
            try:
                norad_id = int(sat["norad_id"])
            except (TypeError, ValueError):
                continue
            by_norad[norad_id] = sat
            by_name[_normalize_satellite_name(sat.get("name"))] = sat
    except Exception as e:
        logger.warning(f"Failed to read tracked satellites for lookup: {e}")
    return by_norad, by_name


def _resolve_satellite_request(
    sat: object,
    tracked_by_norad: dict[int, dict],
    tracked_by_name: dict[str, dict],
    tles: dict,
    pending_tle_writes: dict,
) -> tuple[str, int | None, tuple[str, str, str] | None]:
    """Resolve a satellite request to display name, NORAD ID, and TLE data."""
    norad_id: int | None = None
    sat_key: str | None = None
    tracked: dict | None = None

    if isinstance(sat, int):
        norad_id = sat
    elif isinstance(sat, str):
        stripped = sat.strip()
        if stripped.isdigit():
            norad_id = int(stripped)
        else:
            sat_key = stripped

    if norad_id is not None:
        tracked = tracked_by_norad.get(norad_id)
        sat_key = _BUILTIN_NORAD_TO_KEY.get(norad_id) or (tracked.get("name") if tracked else str(norad_id))
    else:
        normalized = _normalize_satellite_name(sat_key)
        tracked = tracked_by_name.get(normalized)
        if tracked:
            try:
                norad_id = int(tracked["norad_id"])
            except (TypeError, ValueError):
                norad_id = None
            sat_key = tracked.get("name") or sat_key

    tle_data = None
    candidate_keys: list[str] = []
    if sat_key:
        candidate_keys.extend(
            [
                sat_key,
                _normalize_satellite_name(sat_key),
            ]
        )
    if tracked and tracked.get("name"):
        candidate_keys.extend(
            [
                tracked["name"],
                _normalize_satellite_name(tracked["name"]),
            ]
        )

    seen: set[str] = set()
    for key in candidate_keys:
        norm = _normalize_satellite_name(key)
        if norm in seen:
            continue
        seen.add(norm)
        if key in tles:
            tle_data = tles[key]
            break
        if norm in tles:
            tle_data = tles[norm]
            break

    if tle_data is None and tracked and tracked.get("tle_line1") and tracked.get("tle_line2"):
        display_name = tracked.get("name") or sat_key or str(norad_id or "UNKNOWN")
        tle_data = (display_name, tracked["tle_line1"], tracked["tle_line2"])
        write_key = _normalize_satellite_name(display_name)
        pending_tle_writes[write_key] = tle_data
        tles[write_key] = tle_data

    if tle_data is None and sat_key:
        normalized = _normalize_satellite_name(sat_key)
        for key, value in tles.items():
            if key == normalized or _normalize_satellite_name(value[0]) == normalized:
                tle_data = value
                break

    display_name = _BUILTIN_NORAD_TO_KEY.get(norad_id or -1)
    if not display_name:
        display_name = (
            (tracked.get("name") if tracked else None)
            or (tle_data[0] if tle_data else None)
            or (sat_key if sat_key else str(norad_id or "UNKNOWN"))
        )
    return display_name, norad_id, tle_data


def _make_pass_cache_key(
    lat: float,
    lon: float,
    hours: int,
    min_el: float,
    resolved_satellites: list[tuple[str, int, tuple[str, str, str]]],
) -> tuple:
    """Build a stable cache key for predicted passes."""
    return (
        round(lat, 4),
        round(lon, 4),
        int(hours),
        round(float(min_el), 1),
        tuple(
            (
                sat_name,
                norad_id,
                tle_data[1][:32],
                tle_data[2][:32],
            )
            for sat_name, norad_id, tle_data in resolved_satellites
        ),
    )


def _start_satellite_tracker():
    """Background thread: push live satellite positions to satellite_queue every second."""
    import app as app_module

    try:
        from skyfield.api import EarthSatellite, wgs84
    except ImportError:
        logger.warning("skyfield not installed; satellite tracker thread will not run")
        return

    ts = _get_timescale()
    logger.info("Satellite tracker thread started")

    while True:
        try:
            now = ts.now()
            now_dt = now.utc_datetime()

            tracked = get_tracked_satellites(enabled_only=True)
            positions = []

            for sat_rec in tracked:
                sat_name = sat_rec["name"]
                norad_id = sat_rec.get("norad_id", 0)
                tle1 = sat_rec.get("tle_line1")
                tle2 = sat_rec.get("tle_line2")
                if not tle1 or not tle2:
                    # Fall back to TLE store. Try the builtin NORAD-ID key first
                    # (e.g. 'ISS'), then the name-derived key as a last resort.
                    try:
                        norad_int = int(norad_id)
                    except (TypeError, ValueError):
                        norad_int = 0
                    tles = _get_tle_cache()
                    builtin_key = _BUILTIN_NORAD_TO_KEY.get(norad_int)
                    cache_key = (
                        builtin_key if (builtin_key and builtin_key in tles) else sat_name.replace(" ", "-").upper()
                    )
                    if cache_key not in tles:
                        continue
                    tle_entry = tles[cache_key]
                    tle1 = tle_entry[1]
                    tle2 = tle_entry[2]

                try:
                    satellite = EarthSatellite(tle1, tle2, sat_name, ts)
                    geocentric = satellite.at(now)
                    subpoint = wgs84.subpoint(geocentric)

                    # SSE stream is server-wide and cannot know per-client observer
                    # location. Observer-relative fields (elevation, azimuth, distance,
                    # visible) are intentionally omitted here — the per-client HTTP poll
                    # at /satellite/position owns those using the client's actual location.
                    pos = {
                        "satellite": sat_name,
                        "norad_id": norad_id,
                        "lat": float(subpoint.latitude.degrees),
                        "lon": float(subpoint.longitude.degrees),
                        "altitude": float(subpoint.elevation.km),
                    }

                    # Ground track with caching (90 points, TTL 1800s).
                    # If the cache is stale, kick off background computation so the
                    # 1Hz tracker loop is not blocked. The client retains the previous
                    # track via SSE merge until the new one arrives next tick.
                    cache_key_track = (sat_name, tle1[:20])
                    cached = _track_cache.get(cache_key_track)
                    if cached and (time.time() - cached[1]) < _TRACK_CACHE_TTL:
                        pos["groundTrack"] = cached[0]
                    elif cache_key_track not in _track_in_progress:
                        _track_in_progress.add(cache_key_track)
                        _sat_ref = satellite
                        _ts_ref = ts
                        _now_dt_ref = now_dt

                        def _compute_track(_sat=_sat_ref, _ts=_ts_ref, _now_dt=_now_dt_ref, _key=cache_key_track):
                            try:
                                track = []
                                for minutes_offset in range(-45, 46, 1):
                                    t_point = _ts.utc(_now_dt + timedelta(minutes=minutes_offset))
                                    try:
                                        geo = _sat.at(t_point)
                                        sp = wgs84.subpoint(geo)
                                        track.append(
                                            {
                                                "lat": float(sp.latitude.degrees),
                                                "lon": float(sp.longitude.degrees),
                                                "past": minutes_offset < 0,
                                            }
                                        )
                                    except Exception:
                                        continue
                                _track_cache[_key] = (track, time.time())
                            except Exception:
                                pass
                            finally:
                                _track_in_progress.discard(_key)

                        _track_executor.submit(_compute_track)
                        # groundTrack omitted this tick; frontend retains prior value

                    positions.append(pos)
                except Exception:
                    continue

            if positions:
                msg = {
                    "type": "positions",
                    "positions": positions,
                    "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                }
                try:
                    app_module.satellite_queue.put_nowait(msg)
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"Satellite tracker error: {e}")

        time.sleep(1)


_TLE_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours
# CelesTrak blocks addresses that download the same data too often (403
# Forbidden for everything from that IP); it asks for no more than one
# download every two hours. A fetch at every start broke that with a few
# restarts, so the schedule is kept across restarts.
_TLE_MIN_RETRY_SECONDS = 2 * 60 * 60


def _tle_startup_delay(now: float | None = None) -> float:
    """Seconds until the next TLE fetch is due: 24 hours after the last one
    that succeeded, and never within two hours of the last attempt."""
    from utils.database import get_setting

    now = time.time() if now is None else now
    due = 0.0
    try:
        last_success = float(get_setting("tle.last_fetch_success") or 0)
        last_attempt = float(get_setting("tle.last_fetch_attempt") or 0)
    except (TypeError, ValueError):
        return 2.0
    if last_success:
        due = max(due, last_success + _TLE_REFRESH_INTERVAL_SECONDS)
    if last_attempt:
        due = max(due, last_attempt + _TLE_MIN_RETRY_SECONDS)
    return max(2.0, min(due - now, _TLE_REFRESH_INTERVAL_SECONDS))


def init_tle_auto_refresh():
    """Initialize TLE auto-refresh. Called by app.py after initialization."""

    def _schedule_next_tle_refresh(delay: float = _TLE_REFRESH_INTERVAL_SECONDS) -> None:
        t = threading.Timer(delay, _auto_refresh_tle)
        t.daemon = True
        t.start()

    def _auto_refresh_tle():
        try:
            _load_db_satellites_into_cache()
            updated = refresh_tle_data()
            if updated:
                logger.info(f"Auto-refreshed TLE data for: {', '.join(updated)}")
        except Exception as e:
            logger.warning(f"Auto TLE refresh failed: {e}")
        finally:
            # Schedule next refresh regardless of success/failure
            _schedule_next_tle_refresh()

    # First refresh when due (2 seconds after startup if never fetched), then every 24 hours
    delay = _tle_startup_delay()
    threading.Timer(delay, _auto_refresh_tle).start()
    logger.info(f"TLE auto-refresh scheduled (24h interval, next in {delay / 3600:.1f} h)")

    # Start live position tracker thread
    tracker_thread = threading.Thread(
        target=_start_satellite_tracker,
        daemon=True,
        name="satellite-tracker",
    )
    tracker_thread.start()
    logger.info("Satellite tracker thread launched")


def _fetch_iss_realtime(observer_lat: float | None = None, observer_lon: float | None = None) -> dict | None:
    """
    Fetch real-time ISS position from external APIs.

    Returns position data dict or None if all APIs fail.
    """
    iss_lat = None
    iss_lon = None
    iss_alt = 420  # Default altitude in km
    source = None

    # Try primary API: Where The ISS At
    try:
        response = requests.get("https://api.wheretheiss.at/v1/satellites/25544", timeout=5)
        if response.status_code == 200:
            data = response.json()
            iss_lat = float(data["latitude"])
            iss_lon = float(data["longitude"])
            iss_alt = float(data.get("altitude", 420))
            source = "wheretheiss"
    except Exception as e:
        logger.debug(f"Where The ISS At API failed: {e}")

    # Try fallback API: Open Notify
    if iss_lat is None:
        try:
            response = requests.get("http://api.open-notify.org/iss-now.json", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("message") == "success":
                    iss_lat = float(data["iss_position"]["latitude"])
                    iss_lon = float(data["iss_position"]["longitude"])
                    source = "open-notify"
        except Exception as e:
            logger.debug(f"Open Notify API failed: {e}")

    if iss_lat is None:
        return None

    result = {
        "satellite": "ISS",
        "norad_id": 25544,
        "lat": iss_lat,
        "lon": iss_lon,
        "altitude": iss_alt,
        "source": source,
    }

    # Calculate observer-relative data if location provided
    if observer_lat is not None and observer_lon is not None:
        # Earth radius in km
        earth_radius = 6371

        # Convert to radians
        lat1 = math.radians(observer_lat)
        lat2 = math.radians(iss_lat)
        lon1 = math.radians(observer_lon)
        lon2 = math.radians(iss_lon)

        # Haversine for ground distance
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        ground_distance = earth_radius * c

        # Calculate slant range
        slant_range = math.sqrt(ground_distance**2 + iss_alt**2)

        # Calculate elevation angle (simplified)
        if ground_distance > 0:
            elevation = math.degrees(math.atan2(iss_alt - (ground_distance**2 / (2 * earth_radius)), ground_distance))
        else:
            elevation = 90.0

        # Calculate azimuth
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        azimuth = math.degrees(math.atan2(y, x))
        azimuth = (azimuth + 360) % 360

        result["elevation"] = round(elevation, 1)
        result["azimuth"] = round(azimuth, 1)
        result["distance"] = round(slant_range, 1)
        result["visible"] = elevation > 0

    return result


@satellite_bp.route("/dashboard")
def satellite_dashboard():
    """Popout satellite tracking dashboard."""
    embedded = request.args.get("embedded", "false") == "true"
    response = make_response(
        render_template(
            "satellite_dashboard.html",
            shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
            embedded=embedded,
        )
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@satellite_bp.route("/predict", methods=["POST"])
def predict_passes():
    """Calculate satellite passes using skyfield."""
    try:
        from skyfield.api import EarthSatellite, wgs84
    except ImportError:
        return jsonify({"status": "error", "message": "skyfield library not installed. Run: pip install skyfield"}), 503

    from utils.satellite_predict import predict_passes as _predict_passes

    data = request.json or {}

    try:
        # Validate inputs
        lat = validate_latitude(data.get("latitude", data.get("lat", 51.5074)))
        lon = validate_longitude(data.get("longitude", data.get("lon", -0.1278)))
        hours = validate_hours(data.get("hours", 24))
        min_el = validate_elevation(data.get("minEl", 10))
    except ValueError as e:
        return api_error(str(e), 400)

    try:
        sat_input = data.get("satellites", ["ISS", "METEOR-M2-3", "METEOR-M2-4"])
        passes = []
        colors = {
            "ISS": "#00ffff",
            "METEOR-M2": "#9370DB",
            "METEOR-M2-3": "#ff00ff",
            "METEOR-M2-4": "#00ff88",
        }
        tracked_by_norad, tracked_by_name = _get_tracked_satellite_maps()
        tles = _get_tle_cache()
        pending_tle_writes: dict = {}

        resolved_satellites: list[tuple[str, int, tuple[str, str, str]]] = []
        for sat in sat_input:
            sat_name, norad_id, tle_data = _resolve_satellite_request(
                sat,
                tracked_by_norad,
                tracked_by_name,
                tles,
                pending_tle_writes,
            )
            if not tle_data:
                continue
            resolved_satellites.append((sat_name, norad_id or 0, tle_data))

        if pending_tle_writes:
            try:
                tle_store.update(pending_tle_writes)
            except Exception as e:
                logger.warning(f"TLE write-back failed (non-fatal): {e}")

        if not resolved_satellites:
            return jsonify(
                {
                    "status": "success",
                    "passes": [],
                    "cached": False,
                }
            )

        cache_key = _make_pass_cache_key(lat, lon, hours, min_el, resolved_satellites)
        cached = _pass_cache.get(cache_key)
        now_ts = time.time()
        if cached and (now_ts - cached[1]) < _PASS_CACHE_TTL:
            return jsonify(
                {
                    "status": "success",
                    "passes": cached[0],
                    "cached": True,
                }
            )

        ts = _get_timescale()
        observer = wgs84.latlon(lat, lon)
        t0 = ts.now()
        t1 = ts.utc(t0.utc_datetime() + timedelta(hours=hours))

        for sat_name, norad_id, tle_data in resolved_satellites:
            current_pos = None
            try:
                satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)
                geo = satellite.at(t0)
                sp = wgs84.subpoint(geo)
                current_pos = {
                    "lat": float(sp.latitude.degrees),
                    "lon": float(sp.longitude.degrees),
                    "altitude": float(sp.elevation.km),
                }
                # Add observer-relative data using the request's observer location
                try:
                    diff = satellite - observer
                    topo = diff.at(t0)
                    alt_deg, az_deg, dist_km = topo.altaz()
                    current_pos["elevation"] = round(float(alt_deg.degrees), 1)
                    current_pos["azimuth"] = round(float(az_deg.degrees), 1)
                    current_pos["distance"] = round(float(dist_km.km), 1)
                    current_pos["visible"] = bool(alt_deg.degrees > 0)
                except Exception:
                    pass
            except Exception:
                pass

            sat_passes = _predict_passes(tle_data, observer, ts, t0, t1, min_el=min_el)
            for p in sat_passes:
                p["satellite"] = sat_name
                p["norad"] = norad_id
                p["color"] = colors.get(sat_name, "#00ff00")
                if current_pos:
                    p["currentPos"] = current_pos
            passes.extend(sat_passes)

        passes.sort(key=lambda p: p["startTimeISO"])
        # Only cache non-empty results to avoid serving a stale empty response
        # on the next request (which could happen if TLEs were too old to produce
        # any events — the auto-refresh will update them shortly after startup).
        if passes:
            _pass_cache[cache_key] = (passes, now_ts)

        return jsonify(
            {
                "status": "success",
                "passes": passes,
                "cached": False,
            }
        )
    except Exception as exc:
        logger.exception("Satellite pass calculation failed")
        if "cache_key" in locals():
            stale_cached = _pass_cache.get(cache_key)
            if stale_cached and stale_cached[0]:
                return jsonify(
                    {
                        "status": "success",
                        "passes": stale_cached[0],
                        "cached": True,
                        "stale": True,
                    }
                )
        return api_error(f"Failed to calculate passes: {exc}", 500)


@satellite_bp.route("/position", methods=["POST"])
def get_satellite_position():
    """Get real-time positions of satellites."""
    try:
        from skyfield.api import EarthSatellite, wgs84
    except ImportError:
        return api_error("skyfield not installed", 503)

    data = request.json or {}

    # Validate inputs
    try:
        lat = validate_latitude(data.get("latitude", data.get("lat", 51.5074)))
        lon = validate_longitude(data.get("longitude", data.get("lon", -0.1278)))
    except ValueError as e:
        return api_error(str(e), 400)

    sat_input = data.get("satellites", [])
    include_track = bool(data.get("includeTrack", True))
    prefer_realtime_api = bool(data.get("preferRealtimeApi", False))

    observer = wgs84.latlon(lat, lon)
    ts = None
    now = None
    now_dt = None
    tracked_by_norad, tracked_by_name = _get_tracked_satellite_maps()
    tles = _get_tle_cache()
    pending_tle_writes: dict = {}

    positions = []

    for sat in sat_input:
        sat_name, norad_id, tle_data = _resolve_satellite_request(
            sat, tracked_by_norad, tracked_by_name, tles, pending_tle_writes
        )
        # Optional special handling for ISS. The dashboard does not enable this
        # because external API latency can make live updates stall.
        if prefer_realtime_api and (norad_id == 25544 or sat_name == "ISS"):
            iss_data = _fetch_iss_realtime(lat, lon)
            if iss_data:
                # Add orbit track if requested (using TLE for track prediction)
                iss_tle = _get_tle_cache().get("ISS")
                if include_track and iss_tle:
                    try:
                        if ts is None:
                            ts = _get_timescale()
                            now = ts.now()
                            now_dt = now.utc_datetime()
                        tle_data = iss_tle
                        satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)
                        orbit_track = []
                        for minutes_offset in range(-45, 46, 1):
                            t_point = ts.utc(now_dt + timedelta(minutes=minutes_offset))
                            try:
                                geo = satellite.at(t_point)
                                sp = wgs84.subpoint(geo)
                                orbit_track.append(
                                    {
                                        "lat": float(sp.latitude.degrees),
                                        "lon": float(sp.longitude.degrees),
                                        "past": minutes_offset < 0,
                                    }
                                )
                            except Exception:
                                continue
                        iss_data["track"] = orbit_track
                    except Exception:
                        pass
                positions.append(iss_data)
                continue

        # Other satellites - use TLE data
        if not tle_data:
            continue

        try:
            if ts is None:
                ts = _get_timescale()
                now = ts.now()
                now_dt = now.utc_datetime()
            satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)

            geocentric = satellite.at(now)
            subpoint = wgs84.subpoint(geocentric)

            diff = satellite - observer
            topocentric = diff.at(now)
            alt, az, distance = topocentric.altaz()

            pos_data = {
                "satellite": sat_name,
                "norad_id": norad_id,
                "lat": float(subpoint.latitude.degrees),
                "lon": float(subpoint.longitude.degrees),
                "altitude": float(subpoint.elevation.km),
                "elevation": float(alt.degrees),
                "azimuth": float(az.degrees),
                "distance": float(distance.km),
                "visible": bool(alt.degrees > 0),
            }

            if include_track:
                orbit_track = []
                for minutes_offset in range(-45, 46, 1):
                    t_point = ts.utc(now_dt + timedelta(minutes=minutes_offset))
                    try:
                        geo = satellite.at(t_point)
                        sp = wgs84.subpoint(geo)
                        orbit_track.append(
                            {
                                "lat": float(sp.latitude.degrees),
                                "lon": float(sp.longitude.degrees),
                                "past": minutes_offset < 0,
                            }
                        )
                    except Exception:
                        continue

                pos_data["track"] = orbit_track
                pos_data["groundTrack"] = orbit_track

            positions.append(pos_data)
        except Exception:
            continue

    if pending_tle_writes:
        try:
            tle_store.update(pending_tle_writes)
        except Exception as e:
            logger.warning(f"TLE write-back failed (non-fatal): {e}")

    return jsonify({"status": "success", "positions": positions, "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})


@satellite_bp.route("/transmitters/<int:norad_id>")
def get_transmitters_endpoint(norad_id: int):
    """Return SatNOGS transmitter data for a satellite by NORAD ID."""
    from utils.satnogs import get_transmitters

    transmitters = get_transmitters(norad_id)
    return jsonify({"status": "success", "norad_id": norad_id, "transmitters": transmitters})


@satellite_bp.route("/parse-packet", methods=["POST"])
def parse_packet():
    """Parse a raw satellite telemetry packet (base64-encoded)."""
    import base64

    from utils.satellite_telemetry import auto_parse

    data = request.json or {}
    try:
        raw_bytes = base64.b64decode(data.get("data", ""))
    except Exception:
        return api_error("Invalid base64 data", 400)
    result = auto_parse(raw_bytes)
    return jsonify({"status": "success", "parsed": result})


@satellite_bp.route("/stream_satellite")
def stream_satellite() -> Response:
    """SSE endpoint streaming live satellite positions from the background tracker."""
    import app as app_module

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.satellite_queue,
            channel_key="satellite",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


def refresh_tle_data() -> list:
    """
    Refresh TLE data from CelesTrak.

    This can be called at startup or periodically to keep TLE data fresh.
    Returns list of satellite names that were updated.
    """
    name_mappings = {
        "ISS (ZARYA)": "ISS",
        "NOAA 15": "NOAA-15",
        "NOAA 18": "NOAA-18",
        "NOAA 19": "NOAA-19",
        "NOAA 20 (JPSS-1)": "NOAA-20",
        "NOAA 21 (JPSS-2)": "NOAA-21",
        "METEOR-M 2": "METEOR-M2",
        "METEOR-M2 3": "METEOR-M2-3",
        "METEOR-M2 4": "METEOR-M2-4",
    }

    from utils.database import set_setting

    updated = []
    new_entries: dict = {}
    current = _get_tle_cache()
    fetched_any = False
    with contextlib.suppress(Exception):
        set_setting("tle.last_fetch_attempt", time.time())

    for group in ["stations", "weather", "noaa"]:
        url = f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                content = response.read().decode("utf-8")
                lines = content.strip().split("\n")
                fetched_any = True

                i = 0
                while i + 2 < len(lines):
                    name = lines[i].strip()
                    line1 = lines[i + 1].strip()
                    line2 = lines[i + 2].strip()

                    if not (line1.startswith("1 ") and line2.startswith("2 ")):
                        i += 1
                        continue

                    internal_name = name_mappings.get(name, name)

                    if internal_name in current:
                        new_entries[internal_name] = (name, line1, line2)
                        if internal_name not in updated:
                            updated.append(internal_name)

                    i += 3
        except Exception as e:
            logger.warning(f"Error fetching TLE group {group}: {e}")
            continue

    if new_entries:
        tle_store.update(new_entries)
    if fetched_any:
        with contextlib.suppress(Exception):
            set_setting("tle.last_fetch_success", time.time())

    return updated


@satellite_bp.route("/update-tle", methods=["POST"])
def update_tle():
    """Update TLE data from CelesTrak (API endpoint)."""
    try:
        updated = refresh_tle_data()
        return jsonify({"status": "success", "updated": updated})
    except Exception as e:
        logger.error(f"Error updating TLE data: {e}")
        return api_error("TLE update failed")


@satellite_bp.route("/celestrak/<category>")
def fetch_celestrak(category):
    """Fetch TLE data from CelesTrak for a category."""
    valid_categories = [
        "stations",
        "weather",
        "noaa",
        "goes",
        "resource",
        "sarsat",
        "dmc",
        "tdrss",
        "argos",
        "planet",
        "spire",
        "geo",
        "intelsat",
        "ses",
        "iridium",
        "iridium-NEXT",
        "starlink",
        "oneweb",
        "amateur",
        "cubesat",
        "visual",
    ]

    if category not in valid_categories:
        return api_error(f"Invalid category. Valid: {valid_categories}")

    try:
        url = f"https://celestrak.org/NORAD/elements/gp.php?GROUP={category}&FORMAT=tle"
        with urllib.request.urlopen(url, timeout=10) as response:
            content = response.read().decode("utf-8")

        satellites = []
        lines = content.strip().split("\n")

        i = 0
        while i + 2 < len(lines):
            name = lines[i].strip()
            line1 = lines[i + 1].strip()
            line2 = lines[i + 2].strip()

            if not (line1.startswith("1 ") and line2.startswith("2 ")):
                i += 1
                continue

            try:
                norad_id = int(line1[2:7])
                satellites.append({"name": name, "norad": norad_id, "tle1": line1, "tle2": line2})
            except (ValueError, IndexError):
                pass

            i += 3

        return jsonify({"status": "success", "category": category, "satellites": satellites})

    except Exception as e:
        logger.error(f"Error fetching CelesTrak data: {e}")
        return api_error("Failed to fetch satellite data")


# =============================================================================
# Tracked Satellites CRUD
# =============================================================================


@satellite_bp.route("/tracked", methods=["GET"])
def list_tracked_satellites():
    """Return all tracked satellites from the database."""
    enabled_only = request.args.get("enabled", "").lower() == "true"
    sats = get_tracked_satellites(enabled_only=enabled_only)
    return jsonify({"status": "success", "satellites": sats})


@satellite_bp.route("/tracked", methods=["POST"])
def add_tracked_satellites_endpoint():
    """Add one or more tracked satellites."""
    data = request.get_json(silent=True)
    if not data:
        return api_error("No data provided", 400)

    # Accept a single satellite dict or a list
    sat_list = data if isinstance(data, list) else [data]

    normalized: list[dict] = []
    new_tle_entries: dict = {}
    for sat in sat_list:
        norad_id = str(sat.get("norad_id", sat.get("norad", "")))
        name = sat.get("name", "")
        if not norad_id or not name:
            continue
        tle1 = sat.get("tle_line1", sat.get("tle1"))
        tle2 = sat.get("tle_line2", sat.get("tle2"))
        enabled = sat.get("enabled", True)

        normalized.append(
            {
                "norad_id": norad_id,
                "name": name,
                "tle_line1": tle1,
                "tle_line2": tle2,
                "enabled": bool(enabled),
                "builtin": False,
            }
        )

        # Also inject into TLE store if we have TLE data
        if tle1 and tle2:
            cache_key = name.replace(" ", "-").upper()
            new_tle_entries[cache_key] = (name, tle1, tle2)

    if new_tle_entries:
        tle_store.update(new_tle_entries)

    # Single inserts preserve previous behavior; list inserts use DB-level bulk path.
    if len(normalized) == 1:
        sat = normalized[0]
        added = (
            1
            if add_tracked_satellite(
                sat["norad_id"],
                sat["name"],
                sat.get("tle_line1"),
                sat.get("tle_line2"),
                sat.get("enabled", True),
                sat.get("builtin", False),
            )
            else 0
        )
    else:
        added = bulk_add_tracked_satellites(normalized)

    response_payload = {
        "status": "success",
        "added": added,
        "processed": len(normalized),
    }

    # Returning all tracked satellites for very large imports can stall the UI.
    include_satellites = request.args.get("include_satellites", "").lower() == "true"
    if include_satellites or len(normalized) <= 32:
        response_payload["satellites"] = get_tracked_satellites()

    return jsonify(response_payload)


@satellite_bp.route("/tracked/<norad_id>", methods=["PUT"])
def update_tracked_satellite_endpoint(norad_id):
    """Update the enabled state of a tracked satellite."""
    data = request.json or {}
    enabled = data.get("enabled")
    if enabled is None:
        return api_error("Missing enabled field", 400)

    ok = update_tracked_satellite(str(norad_id), bool(enabled))
    if ok:
        return jsonify({"status": "success"})
    return api_error("Satellite not found", 404)


@satellite_bp.route("/tracked/<norad_id>", methods=["DELETE"])
def delete_tracked_satellite_endpoint(norad_id):
    """Remove a tracked satellite by NORAD ID."""
    ok, msg = remove_tracked_satellite(str(norad_id))
    if ok:
        return jsonify({"status": "success", "message": msg})
    status_code = 403 if "builtin" in msg.lower() else 404
    return api_error(msg, status_code)
