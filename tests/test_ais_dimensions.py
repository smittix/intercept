"""AIS hull dimensions: the antenna offsets are kept for drawing ships to scale."""

from unittest.mock import patch

import routes.ais as ais


class _Store(dict):
    def set(self, key, value):
        self[key] = value


def _process(msg):
    with patch.object(ais.app_module, "ais_vessels", _Store()):
        return ais.process_ais_message(msg)


def test_offsets_kept_with_length_and_width():
    vessel = _process({"mmsi": 235000001, "to_bow": 150, "to_stern": 30, "to_port": 12, "to_starboard": 16})
    assert vessel["length"] == 180 and vessel["width"] == 28
    assert (vessel["to_bow"], vessel["to_stern"], vessel["to_port"], vessel["to_starboard"]) == (150, 30, 12, 16)


def test_unknown_dimensions_not_kept():
    vessel = _process({"mmsi": 235000002, "to_bow": 0, "to_stern": 0, "to_port": 0, "to_starboard": 0})
    assert "to_bow" not in vessel


def test_partial_offsets_not_kept():
    vessel = _process({"mmsi": 235000003, "to_bow": 100, "to_stern": 20})
    assert vessel["length"] == 120
    assert "to_bow" not in vessel
