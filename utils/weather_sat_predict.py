"""Weather satellite pass prediction utility.

Self-contained pass prediction for NOAA/Meteor weather satellites. Uses
Skyfield's find_discrete() for AOS/LOS detection, then enriches results
with weather-satellite-specific metadata (name, frequency, mode, quality).
"""

from __future__ import annotations

import datetime
from typing import Any

from skyfield.api import EarthSatellite, load, wgs84
from skyfield.searchlib import find_discrete

from data.satellites import TLE_SATELLITES
from utils.logging import get_logger
from utils.weather_sat import WEATHER_SATELLITES

logger = get_logger("intercept.weather_sat_predict")


def _format_utc_iso(dt: datetime.datetime) -> str:
    """Format a datetime as a UTC ISO 8601 string ending with 'Z'.

    Handles both aware (UTC) and naive (assumed UTC) datetimes, producing a
    consistent ``YYYY-MM-DDTHH:MM:SSZ`` string without ``+00:00`` suffixes.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_tle_source() -> dict:
    """Return the best available TLE source (unified store, static fallback)."""
    from utils import tle_store

    tles = tle_store.all_tles()
    if tles:
        return tles
    return TLE_SATELLITES


def predict_passes(
    lat: float,
    lon: float,
    hours: int = 24,
    min_elevation: float = 15.0,
    include_trajectory: bool = False,
    include_ground_track: bool = False,
) -> list[dict[str, Any]]:
    """Predict upcoming weather satellite passes for an observer location.

    Args:
        lat: Observer latitude (-90 to 90)
        lon: Observer longitude (-180 to 180)
        hours: Hours ahead to predict (1-72)
        min_elevation: Minimum peak elevation in degrees (0-90)
        include_trajectory: Include 30-point az/el trajectory for polar plot
        include_ground_track: Include 60-point lat/lon ground track for map

    Returns:
        List of pass dicts sorted by start time, each containing:
        id, satellite, name, frequency, mode, startTime, startTimeISO,
        endTimeISO, maxEl, maxElAz, riseAz, setAz, duration, quality,
        and optionally trajectory/groundTrack.
    """
    # Raise ImportError early if skyfield has been disabled (e.g., in tests that
    # patch sys.modules to simulate skyfield being unavailable).
    import skyfield  # noqa: F401

    ts = load.timescale(builtin=True)
    observer = wgs84.latlon(lat, lon)
    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + datetime.timedelta(hours=hours))

    tle_source = _get_tle_source()
    all_passes: list[dict[str, Any]] = []

    for sat_key, sat_info in WEATHER_SATELLITES.items():
        if not sat_info["active"]:
            continue

        try:
            tle_data = tle_source.get(sat_info["tle_key"])
            if not tle_data:
                continue

            satellite = EarthSatellite(tle_data[1], tle_data[2], tle_data[0], ts)
            diff = satellite - observer

            # Rise and set at the horizon, so a capture covers the whole pass;
            # min_elevation only picks which passes (by their peak), below
            def above_horizon(t, _diff=diff):
                alt, _, _ = _diff.at(t).altaz()
                return alt.degrees > 0

            # find_discrete samples every step_days: a pass is minutes above the
            # elevation limit, so sample each minute (rough_period isn't read here,
            # and its coarse default missed most passes)
            above_horizon.step_days = 1 / 1440

            times, is_rising = find_discrete(t0, t1, above_horizon)

            rise_t = None
            for t, rising in zip(times, is_rising):
                if rising:
                    rise_t = t
                elif rise_t is not None:
                    _process_pass(
                        sat_key,
                        sat_info,
                        satellite,
                        diff,
                        ts,
                        rise_t,
                        t,
                        min_elevation,
                        include_trajectory,
                        include_ground_track,
                        all_passes,
                    )
                    rise_t = None

        except Exception as exc:
            logger.debug("Error predicting passes for %s: %s", sat_key, exc)
            continue

    all_passes.sort(key=lambda p: p["startTimeISO"])
    return all_passes


def _process_pass(
    sat_key: str,
    sat_info: dict,
    satellite,
    diff,
    ts,
    rise_t,
    set_t,
    min_elevation: float,
    include_trajectory: bool,
    include_ground_track: bool,
    all_passes: list,
) -> None:
    """Sample a rise/set interval, build the pass dict, append to all_passes."""
    rise_dt = rise_t.utc_datetime()
    set_dt = set_t.utc_datetime()
    duration_secs = (set_dt - rise_dt).total_seconds()

    # Sample 30 points across the pass to find max elevation and trajectory
    N_TRAJ = 30
    max_el = 0.0
    max_el_az = 0.0
    traj_points = []

    for i in range(N_TRAJ):
        frac = i / (N_TRAJ - 1) if N_TRAJ > 1 else 0.0
        t_pt = ts.tt_jd(rise_t.tt + frac * (set_t.tt - rise_t.tt))
        try:
            topo = diff.at(t_pt)
            alt, az, _ = topo.altaz()
            el = float(alt.degrees)
            az_deg = float(az.degrees)
            if el > max_el:
                max_el = el
                max_el_az = az_deg
            if include_trajectory:
                traj_points.append({"az": round(az_deg, 1), "el": round(max(0.0, el), 1)})
        except Exception:
            pass

    # Filter passes that never reach min_elevation
    if max_el < min_elevation:
        return

    # AOS and LOS azimuths
    try:
        rise_az = float(diff.at(rise_t).altaz()[1].degrees)
    except Exception:
        rise_az = 0.0

    try:
        set_az = float(diff.at(set_t).altaz()[1].degrees)
    except Exception:
        set_az = 0.0

    aos_iso = _format_utc_iso(rise_dt)
    try:
        pass_id = f"{sat_key}_{rise_dt.strftime('%Y%m%d%H%M%S')}"
    except Exception:
        pass_id = f"{sat_key}_{aos_iso}"

    pass_dict: dict[str, Any] = {
        "id": pass_id,
        "satellite": sat_key,
        "name": sat_info["name"],
        "frequency": sat_info["frequency"],
        "mode": sat_info["mode"],
        "startTime": rise_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "startTimeISO": aos_iso,
        "endTimeISO": _format_utc_iso(set_dt),
        "maxEl": round(max_el, 1),
        "maxElAz": round(max_el_az, 1),
        "riseAz": round(rise_az, 1),
        "setAz": round(set_az, 1),
        "duration": round(duration_secs, 1),
        "quality": ("excellent" if max_el >= 60 else "good" if max_el >= 30 else "fair"),
        # Backwards-compatible aliases used by weather_sat_scheduler and the frontend
        "aosAz": round(rise_az, 1),
        "losAz": round(set_az, 1),
        "tcaAz": round(max_el_az, 1),
    }

    if include_trajectory:
        pass_dict["trajectory"] = traj_points

    if include_ground_track:
        ground_track = []
        N_TRACK = 60
        for i in range(N_TRACK):
            frac = i / (N_TRACK - 1) if N_TRACK > 1 else 0.0
            t_pt = ts.tt_jd(rise_t.tt + frac * (set_t.tt - rise_t.tt))
            try:
                geocentric = satellite.at(t_pt)
                subpoint = wgs84.subpoint(geocentric)
                ground_track.append(
                    {
                        "lat": round(float(subpoint.latitude.degrees), 4),
                        "lon": round(float(subpoint.longitude.degrees), 4),
                    }
                )
            except Exception:
                pass
        pass_dict["groundTrack"] = ground_track

    all_passes.append(pass_dict)
