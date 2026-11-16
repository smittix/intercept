"""The decoder's availability must reflect the machine now, not the first answer it ever gave.

A cached hit that never rechecks makes the route trust a SatDump that is gone, so a missing tool becomes a 500.
"""
from unittest import mock

from utils import weather_sat


def test_availability_is_rechecked_after_a_hit_is_cached():
    decoder = weather_sat.WeatherSatDecoder()
    with mock.patch.object(weather_sat.shutil, "which", return_value="/usr/bin/satdump"):
        assert decoder.decoder_available == "satdump"  # first answer: installed, and cached
    with mock.patch.object(weather_sat.shutil, "which", return_value=None):
        assert decoder.decoder_available is None, "SatDump was removed; the cached hit must not stick"
