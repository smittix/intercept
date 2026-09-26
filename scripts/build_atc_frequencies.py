#!/usr/bin/env python3
"""Build static/data/atc_frequencies.json from OurAirports open data.

Source (public domain): https://ourairports.com/data/
  - airports.csv            (airport coordinates + names)
  - airport-frequencies.csv (per-airport frequencies)

We keep only airports that have at least one TWR / APP / ATIS frequency, each
with its coordinates, so the ADS-B dashboard can look up the nearest airport's
tower/approach/ATIS audio for a selected aircraft (issue #271).

Usage:
    python scripts/build_atc_frequencies.py                # download fresh
    python scripts/build_atc_frequencies.py A.csv F.csv    # use local CSVs

Re-run to refresh the bundled data. Output is compact JSON, sorted by ident.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://davidmegginson.github.io/ourairports-data"
AIRPORTS_URL = f"{BASE}/airports.csv"
FREQS_URL = f"{BASE}/airport-frequencies.csv"
WANT_TYPES = ("TWR", "APP", "ATIS")
OUT = Path(__file__).resolve().parent.parent / "static" / "data" / "atc_frequencies.json"


def _read_csv(source: str) -> list[dict]:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=60) as resp:  # noqa: S310 (trusted URL)
            text = resp.read().decode("utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def build(airports_src: str, freqs_src: str) -> list[dict]:
    airports = {}
    for row in _read_csv(airports_src):
        try:
            lat = float(row["latitude_deg"])
            lon = float(row["longitude_deg"])
        except (KeyError, ValueError):
            continue
        airports[row["id"]] = {
            "ident": row.get("ident") or row.get("gps_code") or "",
            "name": row.get("name", ""),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
        }

    by_airport: dict[str, list[dict]] = {}
    for row in _read_csv(freqs_src):
        ftype = row.get("type", "")
        if ftype not in WANT_TYPES:
            continue
        try:
            mhz = round(float(row["frequency_mhz"]), 3)
        except (KeyError, ValueError):
            continue
        by_airport.setdefault(row["airport_ref"], []).append(
            {"type": ftype, "mhz": mhz, "desc": (row.get("description") or "").strip()}
        )

    out = []
    for ref, freqs in by_airport.items():
        ap = airports.get(ref)
        if not ap or not ap["ident"]:
            continue
        # Stable order: TWR, APP, ATIS then by frequency
        freqs.sort(key=lambda f: (WANT_TYPES.index(f["type"]), f["mhz"]))
        out.append({**ap, "freqs": freqs})

    out.sort(key=lambda a: a["ident"])
    return out


def main() -> None:
    if len(sys.argv) == 3:
        airports_src, freqs_src = sys.argv[1], sys.argv[2]
    else:
        airports_src, freqs_src = AIRPORTS_URL, FREQS_URL
    data = build(airports_src, freqs_src)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} — {len(data)} airports, {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
