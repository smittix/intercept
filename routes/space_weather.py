"""Space Weather routes - proxies NOAA SWPC, NASA SDO, and HamQSL data."""

from __future__ import annotations

import concurrent.futures
import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, Response, jsonify

from utils.logging import get_logger
from utils.responses import api_error

logger = get_logger("intercept.space_weather")

space_weather_bp = Blueprint("space_weather", __name__, url_prefix="/space-weather")

# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------

_cache: dict[str, dict[str, Any]] = {}

# Cache TTLs in seconds
TTL_REALTIME = 300  # 5 min for real-time data
TTL_FORECAST = 1800  # 30 min for forecasts
TTL_DAILY = 3600  # 1 hr for daily summaries
TTL_IMAGE = 600  # 10 min for images


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["data"]
    return None


def _cache_set(key: str, data: Any, ttl: int) -> None:
    _cache[key] = {"data": data, "expires": time.time() + ttl}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_TIMEOUT = 15  # seconds

SWPC_BASE = "https://services.swpc.noaa.gov"
SWPC_JSON = f"{SWPC_BASE}/products"


def _fetch_json(url: str, timeout: int = _TIMEOUT) -> Any | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "INTERCEPT/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _fetch_text(url: str, timeout: int = _TIMEOUT) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "INTERCEPT/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _fetch_bytes(url: str, timeout: int = _TIMEOUT) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "INTERCEPT/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Data source fetchers
# ---------------------------------------------------------------------------


def _fetch_cached_json(cache_key: str, url: str, ttl: int) -> Any | None:
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _fetch_json(url)
    if data is not None:
        _cache_set(cache_key, data, ttl)
    return data


def _as_table(data: Any, columns: list[str]) -> Any:
    """SWPC moved several products from header-plus-rows tables to lists of
    objects. The page reads tables, so objects are put back into one; a
    table is returned as it is."""
    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):

        def cell(row: dict, column: str) -> Any:
            value = row.get(column)
            # the tables had "2026-09-24 12:00:00"; the objects have a "T"
            return value.replace("T", " ") if column == "time_tag" and isinstance(value, str) else value

        return [columns] + [[cell(row, c) for c in columns] for row in data]
    return data


def _fetch_kp_index() -> Any | None:
    return _as_table(
        _fetch_cached_json("kp_index", f"{SWPC_JSON}/noaa-planetary-k-index.json", TTL_REALTIME),
        ["time_tag", "Kp", "a_running", "station_count"],
    )


def _fetch_kp_forecast() -> Any | None:
    return _as_table(
        _fetch_cached_json("kp_forecast", f"{SWPC_JSON}/noaa-planetary-k-index-forecast.json", TTL_FORECAST),
        ["time_tag", "kp", "observed", "noaa_scale"],
    )


def _fetch_scales() -> Any | None:
    return _fetch_cached_json("scales", f"{SWPC_JSON}/noaa-scales.json", TTL_REALTIME)


def _fetch_flux() -> Any | None:
    return _as_table(
        _fetch_cached_json("flux", f"{SWPC_JSON}/10cm-flux-30-day.json", TTL_DAILY),
        ["time_tag", "flux"],
    )


def _fetch_alerts() -> Any | None:
    return _fetch_cached_json("alerts", f"{SWPC_JSON}/alerts.json", TTL_REALTIME)


# SWPC retired products/solar-wind/{plasma,mag}-6-hour.json (now 404). The
# real-time solar wind feeds replace them: one-minute rows from every
# spacecraft (DSCOVR as "SOLAR1", ACE, IMAP), newest first, with "active"
# marking the operational source. They are reshaped here into the old
# header-plus-rows tables, which is what the page reads.
RTSW_HOURS = 6


def _rtsw_table(url: str, columns: list[str]) -> list[list] | None:
    rows = _fetch_json(url)
    if not isinstance(rows, list) or not rows:
        return None
    active = [r for r in rows if isinstance(r, dict) and r.get("active")] or [r for r in rows if isinstance(r, dict)]
    active.sort(key=lambda r: r.get("time_tag") or "")
    if not active:
        return None
    try:
        newest = datetime.fromisoformat(active[-1]["time_tag"])
        cutoff = (newest - timedelta(hours=RTSW_HOURS)).isoformat()
        active = [r for r in active if (r.get("time_tag") or "") >= cutoff]
    except (KeyError, TypeError, ValueError):
        pass
    table = [["time_tag", *columns]]
    table.extend([str(r.get("time_tag", "")).replace("T", " "), *(r.get(c) for c in columns)] for r in active)
    return table


def _fetch_solar_wind_plasma() -> Any | None:
    cached = _cache_get("sw_plasma")
    if cached is not None:
        return cached
    data = _rtsw_table(
        f"{SWPC_BASE}/json/rtsw/rtsw_wind_1m.json", ["proton_density", "proton_speed", "proton_temperature"]
    )
    if data is not None:
        _cache_set("sw_plasma", data, TTL_REALTIME)
    return data


def _fetch_solar_wind_mag() -> Any | None:
    cached = _cache_get("sw_mag")
    if cached is not None:
        return cached
    data = _rtsw_table(
        f"{SWPC_BASE}/json/rtsw/rtsw_mag_1m.json", ["bx_gsm", "by_gsm", "bz_gsm", "phi_gsm", "theta_gsm", "bt"]
    )
    if data is not None:
        _cache_set("sw_mag", data, TTL_REALTIME)
    return data


def _fetch_xrays() -> Any | None:
    return _fetch_cached_json("xrays", f"{SWPC_BASE}/json/goes/primary/xrays-1-day.json", TTL_REALTIME)


def _fetch_xray_flares() -> Any | None:
    return _fetch_cached_json("xray_flares", f"{SWPC_BASE}/json/goes/primary/xray-flares-7-day.json", TTL_REALTIME)


def _fetch_flare_probability() -> Any | None:
    data = _fetch_cached_json("flare_prob", f"{SWPC_BASE}/json/solar_probabilities.json", TTL_FORECAST)
    # Now served newest first; the page shows the last rows as the latest.
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        data = sorted(data, key=lambda row: row.get("date") or "")
    return data


def _fetch_solar_regions() -> Any | None:
    return _fetch_cached_json("solar_regions", f"{SWPC_BASE}/json/solar_regions.json", TTL_DAILY)


def _fetch_sunspot_report() -> Any | None:
    return _fetch_cached_json("sunspot_report", f"{SWPC_BASE}/json/sunspot_report.json", TTL_DAILY)


def _parse_hamqsl_xml(xml_text: str) -> dict[str, Any] | None:
    """Parse HamQSL solar XML into a dict of band conditions."""
    try:
        root = ET.fromstring(xml_text)
        solar = root.find(".//solardata")
        if solar is None:
            return None
        result: dict[str, Any] = {}
        # Scalar fields
        for tag in (
            "sfi",
            "aindex",
            "kindex",
            "kindexnt",
            "xray",
            "sunspots",
            "heliumline",
            "protonflux",
            "electonflux",
            "aurora",
            "normalization",
            "latdegree",
            "solarwind",
            "magneticfield",
            "calculatedconditions",
            "calculatedvhfconditions",
            "geomagfield",
            "signalnoise",
            "fof2",
            "muffactor",
            "muf",
        ):
            el = solar.find(tag)
            if el is not None and el.text:
                result[tag] = el.text.strip()
        # Band conditions
        bands: list[dict[str, str]] = []
        for band_el in solar.findall(".//calculatedconditions/band"):
            bands.append(
                {
                    "name": band_el.get("name", ""),
                    "time": band_el.get("time", ""),
                    "condition": band_el.text.strip() if band_el.text else "",
                }
            )
        result["bands"] = bands
        # VHF conditions
        vhf: list[dict[str, str]] = []
        for phen_el in solar.findall(".//calculatedvhfconditions/phenomenon"):
            vhf.append(
                {
                    "name": phen_el.get("name", ""),
                    "location": phen_el.get("location", ""),
                    "condition": phen_el.text.strip() if phen_el.text else "",
                }
            )
        result["vhf"] = vhf
        return result
    except ET.ParseError as exc:
        logger.warning("Failed to parse HamQSL XML: %s", exc)
        return None


def _fetch_band_conditions() -> dict[str, Any] | None:
    cached = _cache_get("band_conditions")
    if cached is not None:
        return cached
    xml_text = _fetch_text("https://www.hamqsl.com/solarxml.php")
    if xml_text is None:
        return None
    data = _parse_hamqsl_xml(xml_text)
    if data is not None:
        _cache_set("band_conditions", data, TTL_FORECAST)
    return data


# ---------------------------------------------------------------------------
# Image proxy whitelist
# ---------------------------------------------------------------------------

IMAGE_WHITELIST: dict[str, dict[str, str]] = {
    # D-RAP absorption maps
    "drap_global": {
        "url": f"{SWPC_BASE}/images/animations/d-rap/global/latest.png",
        "content_type": "image/png",
    },
    "drap_5": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f05.png",
        "content_type": "image/png",
    },
    "drap_10": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f10.png",
        "content_type": "image/png",
    },
    "drap_15": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f15.png",
        "content_type": "image/png",
    },
    "drap_20": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f20.png",
        "content_type": "image/png",
    },
    "drap_25": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f25.png",
        "content_type": "image/png",
    },
    "drap_30": {
        "url": f"{SWPC_BASE}/images/d-rap/global_f30.png",
        "content_type": "image/png",
    },
    # Aurora forecast
    "aurora_north": {
        "url": f"{SWPC_BASE}/images/animations/ovation/north/latest.jpg",
        "content_type": "image/jpeg",
    },
    # SDO solar imagery
    "sdo_193": {
        "url": "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0193.jpg",
        "content_type": "image/jpeg",
    },
    "sdo_304": {
        "url": "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0304.jpg",
        "content_type": "image/jpeg",
    },
    "sdo_magnetogram": {
        "url": "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_HMIBC.jpg",
        "content_type": "image/jpeg",
    },
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@space_weather_bp.route("/data")
def get_data():
    """Return aggregated space weather data from all sources."""
    fetchers = {
        "kp_index": _fetch_kp_index,
        "kp_forecast": _fetch_kp_forecast,
        "scales": _fetch_scales,
        "flux": _fetch_flux,
        "alerts": _fetch_alerts,
        "solar_wind_plasma": _fetch_solar_wind_plasma,
        "solar_wind_mag": _fetch_solar_wind_mag,
        "xrays": _fetch_xrays,
        "xray_flares": _fetch_xray_flares,
        "flare_probability": _fetch_flare_probability,
        "solar_regions": _fetch_solar_regions,
        "sunspot_report": _fetch_sunspot_report,
        "band_conditions": _fetch_band_conditions,
    }
    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=13) as executor:
        futures = {executor.submit(fn): key for key, fn in fetchers.items()}
        for future in concurrent.futures.as_completed(futures):
            data[futures[future]] = future.result()
    data["timestamp"] = time.time()
    return jsonify(data)


@space_weather_bp.route("/image/<key>")
def get_image(key: str):
    """Proxy and cache whitelisted space weather images."""
    entry = IMAGE_WHITELIST.get(key)
    if not entry:
        return api_error("Unknown image key", 404)

    cache_key = f"img_{key}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return Response(cached, content_type=entry["content_type"], headers={"Cache-Control": "public, max-age=300"})

    img_data = _fetch_bytes(entry["url"])
    if img_data is None:
        return api_error("Failed to fetch image", 502)

    _cache_set(cache_key, img_data, TTL_IMAGE)
    return Response(img_data, content_type=entry["content_type"], headers={"Cache-Control": "public, max-age=300"})


@space_weather_bp.route("/prefetch-images")
def prefetch_images():
    """Warm the image cache by fetching all whitelisted images in parallel."""
    # Only fetch images not already cached
    to_fetch = {}
    for key, entry in IMAGE_WHITELIST.items():
        cache_key = f"img_{key}"
        if _cache_get(cache_key) is None:
            to_fetch[key] = entry

    if not to_fetch:
        return jsonify({"status": "all cached", "count": 0})

    def _fetch_and_cache(key: str, entry: dict) -> bool:
        img_data = _fetch_bytes(entry["url"])
        if img_data:
            _cache_set(f"img_{key}", img_data, TTL_IMAGE)
            return True
        return False

    fetched = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_and_cache, k, e): k for k, e in to_fetch.items()}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                fetched += 1

    return jsonify({"status": "ok", "fetched": fetched, "cached": len(IMAGE_WHITELIST) - len(to_fetch)})
