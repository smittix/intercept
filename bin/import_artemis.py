#!/usr/bin/env python3
"""Import signals from the Artemis signal database into data/signals.json.

The Artemis signal database (https://github.com/AresValley/Artemis-DB) is
distributed as a tar archive and can be downloaded without installing Artemis.
This script handles the full workflow: download → extract → import → validate.

Usage:
    # Download the latest database and import automatically (easiest):
    python3 bin/import_artemis.py --download

    # Or point at an already-extracted data.sqlite:
    python3 bin/import_artemis.py /path/to/data.sqlite

Options:
    --download   Fetch the latest Artemis-DB tar, extract, and import
    --dry-run    Show what would be imported without writing anything
    --no-merge   Replace data/signals.json entirely instead of merging
    --limit N    Only process the first N signals (for testing)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections import defaultdict
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_JSON = REPO_ROOT / "data" / "signals.json"

RELEASE_INFO_URL = (
    "https://raw.githubusercontent.com/AresValley/Artemis/master/config/release-info.json"
)

_LOCATION_MAP: dict[str, str] = {
    "worldwide": "GLOBAL",
    "global": "GLOBAL",
    "international": "GLOBAL",
    "europe": "EU",
    "european union": "EU",
    "eu": "EU",
    "north america": "US",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "UK",
    "uk": "UK",
    "great britain": "UK",
    "australia": "AU",
    "au": "AU",
}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def _download_db(dest_dir: Path) -> Path:
    """Download the latest Artemis-DB tar and extract it. Returns the data.sqlite path."""
    print("Fetching release info…")
    info = _fetch_json(RELEASE_INFO_URL)
    db_info = info["sigID_DB"]
    version = db_info["version"]
    url = db_info["url"]
    total = db_info.get("total_bytes", 0)

    print(f"Artemis-DB version {version} ({total // 1_000_000} MB)")
    print(f"Downloading: {url}")

    tar_path = dest_dir / url.split("/")[-1]

    # curl gives real-time progress and is significantly faster than urllib
    result = subprocess.run(
        ["curl", "-L", "--progress-bar", "-o", str(tar_path), url],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed with exit code {result.returncode}")

    print("Extracting…")
    extract_dir = dest_dir / "extracted"
    extract_dir.mkdir()
    with tarfile.open(tar_path) as tf:
        # Safety: strip any absolute paths or '..' traversal
        members = [
            m for m in tf.getmembers()
            if not os.path.isabs(m.name) and ".." not in m.name
        ]
        tf.extractall(extract_dir, members=members)

    # Find data.sqlite anywhere in the extracted tree
    matches = list(extract_dir.rglob("data.sqlite"))
    if not matches:
        raise FileNotFoundError(
            f"No data.sqlite found after extracting {tar_path}. "
            "The Artemis-DB tar structure may have changed."
        )

    return matches[0]


# ---------------------------------------------------------------------------
# Discovery (for users who have Artemis installed)
# ---------------------------------------------------------------------------

def _find_installed_db() -> Path | None:
    home = Path.home()
    candidates = [
        home / "Library" / "Application Support" / "AresValley" / "Artemis" / "data",
        home / ".local" / "share" / "AresValley" / "Artemis" / "data",
        home / "AppData" / "Local" / "AresValley" / "Artemis" / "data",
    ]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "AresValley" / "Artemis" / "data")

    for base in candidates:
        if base.is_dir():
            for candidate in sorted(base.glob("*/data.sqlite")):
                return candidate
    return None


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60]


def _unique_slug(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def _map_location(value: str) -> str:
    return _LOCATION_MAP.get(value.lower().strip(), "GLOBAL")


def _group_frequencies(freq_hz_list: list[int]) -> list[dict[str, int]]:
    """Convert point frequencies (Hz) into min/max range dicts.

    Consecutive frequencies within a 1.5× ratio are merged into one range
    (e.g. 87.5 MHz + 108 MHz → one FM-band range). Isolated points get
    a ±0.5% window, minimum 1 kHz each side.
    """
    freqs = sorted(f for f in freq_hz_list if f > 0)
    if not freqs:
        return []

    groups: list[list[int]] = []
    current = [freqs[0]]
    for f in freqs[1:]:
        if f / current[-1] < 1.5:
            current.append(f)
        else:
            groups.append(current)
            current = [f]
    groups.append(current)

    ranges = []
    for g in groups:
        lo, hi = min(g), max(g)
        if lo == hi:
            margin = max(int(lo * 0.005), 1000)
            lo = max(1, lo - margin)
            hi = hi + margin
        ranges.append({"min_hz": lo, "max_hz": hi})
    return ranges


def _bandwidth_range(bw_values: list[int]) -> dict[str, int] | None:
    values = [v for v in bw_values if v > 0]
    if not values:
        return None
    lo, hi = min(values), max(values)
    if lo == hi:
        margin = max(int(lo * 0.10), 1)
        lo = max(1, lo - margin)
        hi = hi + margin
    return {"min_hz": lo, "max_hz": hi}


# ---------------------------------------------------------------------------
# Database loading
# ---------------------------------------------------------------------------

def load_artemis(db_path: Path, limit: int = 0, existing_ids: set[str] | None = None) -> list[dict]:
    """Read signals from an Artemis data.sqlite and return them in Intercept's schema."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        cur = conn.cursor()
        cur.execute("SELECT SIG_ID, NAME, DESCRIPTION, URL FROM signals ORDER BY NAME")
        all_signals = cur.fetchall()

        cur.execute("SELECT SIG_ID, VALUE FROM frequency")
        freq_rows = cur.fetchall()
        cur.execute("SELECT SIG_ID, VALUE FROM bandwidth")
        bw_rows = cur.fetchall()
        cur.execute("SELECT SIG_ID, VALUE FROM modulation")
        mod_rows = cur.fetchall()
        cur.execute("SELECT SIG_ID, VALUE FROM location")
        loc_rows = cur.fetchall()
        cur.execute("""
            SELECT category.SIG_ID, category_label.VALUE
            FROM category
            JOIN category_label ON category.CLB_ID = category_label.CLB_ID
        """)
        cat_rows = cur.fetchall()

    freqs: dict = defaultdict(list)
    for sid, v in freq_rows:
        if v:
            freqs[sid].append(int(v))

    bws: dict = defaultdict(list)
    for sid, v in bw_rows:
        if v:
            bws[sid].append(int(v))

    mods: dict = defaultdict(list)
    for sid, v in mod_rows:
        if v:
            token = v.strip().upper()
            if token not in mods[sid]:
                mods[sid].append(token)

    locs: dict = defaultdict(list)
    for sid, v in loc_rows:
        if v:
            r = _map_location(v)
            if r not in locs[sid]:
                locs[sid].append(r)

    cats: dict = defaultdict(list)
    for sid, v in cat_rows:
        if v:
            label = v.strip().lower()
            if label not in cats[sid]:
                cats[sid].append(label)

    if limit:
        all_signals = all_signals[:limit]

    used_ids: set[str] = set(existing_ids or [])
    results = []
    skipped_no_freq = 0

    for sig_id, name, description, url in all_signals:
        if not name:
            continue
        freq_ranges = _group_frequencies(freqs[sig_id])
        if not freq_ranges:
            skipped_no_freq += 1
            continue

        sigidwiki_url: str | None = None
        if url and "sigidwiki" in url.lower():
            sigidwiki_url = url.strip()

        slug = _unique_slug(_slugify(name.strip()), used_ids)
        used_ids.add(slug)

        results.append({
            "id": slug,
            "name": name.strip(),
            "description": (description or "").strip(),
            "categories": cats[sig_id],
            "frequency_ranges": freq_ranges,
            "bandwidth_range": _bandwidth_range(bws[sig_id]),
            "modulations": mods[sig_id],
            "regions": locs[sig_id] or ["GLOBAL"],
            "sigidwiki_url": sigidwiki_url,
        })

    if skipped_no_freq:
        print(f"  Skipped {skipped_no_freq} signals with no frequency data")
    return results


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge(existing: list[dict], imported: list[dict]) -> tuple[list[dict], int, int]:
    existing_names = {s["name"].lower() for s in existing}
    merged = list(existing)
    added = skipped = 0
    for sig in imported:
        if sig["name"].lower() in existing_names:
            skipped += 1
        else:
            merged.append(sig)
            existing_names.add(sig["name"].lower())
            added += 1
    return merged, added, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Artemis signal database into data/signals.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "db_path", nargs="?",
        help="Path to data.sqlite (omit to auto-detect installed Artemis DB)",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download the latest Artemis-DB tar (~290 MB) and import automatically",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show import stats without writing data/signals.json",
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="Replace data/signals.json entirely instead of merging",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Only process the first N signals (for testing)",
    )
    args = parser.parse_args()

    tmp_dir: tempfile.TemporaryDirectory | None = None
    db_path: Path | None = None

    try:
        if args.download:
            tmp_dir = tempfile.TemporaryDirectory(prefix="artemis-db-")
            db_path = _download_db(Path(tmp_dir.name))

        elif args.db_path:
            db_path = Path(args.db_path)
            if not db_path.exists():
                print(f"Error: {db_path} does not exist", file=sys.stderr)
                sys.exit(1)

        else:
            db_path = _find_installed_db()
            if db_path is None:
                print(
                    "No Artemis database found automatically.\n\n"
                    "Options:\n"
                    "  1. Download automatically (~290 MB):\n"
                    "       python3 bin/import_artemis.py --download\n\n"
                    "  2. Provide the path manually:\n"
                    "       python3 bin/import_artemis.py /path/to/data.sqlite",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Found installed Artemis database: {db_path}")

        existing = json.loads(SIGNALS_JSON.read_text()) if SIGNALS_JSON.exists() else []
        existing_ids = {s["id"] for s in existing}

        print(f"Loading: {db_path}")
        imported = load_artemis(db_path, limit=args.limit, existing_ids=existing_ids)
        print(f"  {len(imported)} signals with frequency data")

        if args.no_merge:
            final, added, skipped = imported, len(imported), 0
        else:
            print(f"  Existing signals.json: {len(existing)} signals")
            final, added, skipped = merge(existing, imported)
            if skipped:
                print(f"  Skipped {skipped} duplicates (name already in signals.json)")

        print(f"  Adding {added} signals → total {len(final)}")

        if args.dry_run:
            print("\nDry run — nothing written.")
            if imported:
                print("\nSample (first 2 imported signals):")
                for s in imported[:2]:
                    print(json.dumps(s, indent=2))
            return

        SIGNALS_JSON.write_text(json.dumps(final, indent=2) + "\n")
        print(f"\nWritten: {SIGNALS_JSON}")
        print("\nValidate:")
        print("  python3 -m pytest tests/test_signals_json.py --noconftest -v")

    finally:
        if tmp_dir is not None:
            print("Cleaning up temporary files…")
            tmp_dir.cleanup()


if __name__ == "__main__":
    main()
