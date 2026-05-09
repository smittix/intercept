"""Drone RF protocol signature table and frequency matcher."""

from __future__ import annotations

_SIGNATURES = [
    {
        "name": "FRSKY",
        "freq_min_hz": 433_050_000,
        "freq_max_hz": 434_790_000,
    },
    {
        "name": "FRSKY_868",
        "freq_min_hz": 868_000_000,
        "freq_max_hz": 868_600_000,
    },
    {
        "name": "DJI_OCUSYNC",
        "freq_min_hz": 2_400_000_000,
        "freq_max_hz": 2_483_500_000,
    },
    {
        "name": "FPV_VIDEO",
        "freq_min_hz": 5_725_000_000,
        "freq_max_hz": 5_875_000_000,
    },
]


def match_signature(frequency_hz: int) -> str:
    """Return the protocol name for a detected frequency, or 'UNKNOWN'."""
    for sig in _SIGNATURES:
        if sig["freq_min_hz"] <= frequency_hz <= sig["freq_max_hz"]:
            return sig["name"]
    return "UNKNOWN"
