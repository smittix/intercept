"""SBS "is on ground" (field 22), for drawing aircraft on the ground."""

from routes.adsb import _sbs_on_ground


def _msg(msg_type: str, on_ground: str = "") -> list[str]:
    parts = ["MSG", msg_type, "1", "1", "4CA2D6", "1", "2026/09/25", "10:00:00.000", "2026/09/25", "10:00:00.000"]
    parts += [""] * 11 + [on_ground]
    return parts


def test_flag_minus_one_is_on_ground():
    assert _sbs_on_ground("3", _msg("3", "-1")) is True


def test_flag_zero_is_airborne():
    assert _sbs_on_ground("3", _msg("3", "0")) is False


def test_no_flag_says_nothing():
    assert _sbs_on_ground("3", _msg("3", "")) is None
    assert _sbs_on_ground("1", ["MSG", "1", "1", "1", "4CA2D6"]) is None


def test_surface_position_is_on_ground():
    assert _sbs_on_ground("2", _msg("2", "")) is True
