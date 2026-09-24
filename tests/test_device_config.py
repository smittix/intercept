"""Per-device SDR configuration: names, PPM, default gain and bias-T.

Covers #269 (named devices) and #238 (per-device frequency correction).
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.sdr import RTLSDRCommandBuilder, SDRDevice, SDRType
from utils.sdr import device_config as dc


def _rtl(index, serial):
    return SDRDevice(
        sdr_type=SDRType.RTL_SDR,
        index=index,
        name="Generic RTL2832U",
        serial=serial,
        driver="rtlsdr",
        capabilities=RTLSDRCommandBuilder.CAPABILITIES,
    )


@pytest.fixture
def temp_db():
    import config
    import utils.database as db

    with tempfile.TemporaryDirectory() as tmp:
        with (
            patch.object(db, "DB_PATH", Path(tmp) / "test.db"),
            patch.object(db, "DB_DIR", Path(tmp)),
            # A configured password stops init_db writing an initial-password file.
            patch.object(config, "ADMIN_PASSWORD", "test-admin-password"),
        ):
            # get_connection() caches a thread-local handle; drop it both sides.
            db.close_db()
            db.init_db()
            try:
                yield db
            finally:
                db.close_db()


@pytest.fixture
def detected():
    """The devices detection reports. Mutate the list to simulate a replug."""
    devices = []
    with patch.object(dc, "_detected_devices", lambda: list(devices)):
        yield devices


class TestNameValidation:
    def test_valid_name_is_trimmed(self):
        assert dc.validate_device_name("  Airband Antenna  ") == "Airband Antenna"

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_blank_clears_the_name(self, blank):
        assert dc.validate_device_name(blank) is None

    def test_length_is_capped(self):
        assert dc.validate_device_name("x" * dc.NAME_MAX_LENGTH)
        with pytest.raises(ValueError, match="at most"):
            dc.validate_device_name("x" * (dc.NAME_MAX_LENGTH + 1))

    @pytest.mark.parametrize(
        "name",
        ["ADS-B\nAntenna", "bell\x07", "tab\there", "null\x00", "bidi\u202eenna", "zero\u200bwidth"],
    )
    def test_control_and_format_characters_are_refused(self, name):
        with pytest.raises(ValueError, match="control characters"):
            dc.validate_device_name(name)

    @pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>", 'a"b', "a'b", "a`b"])
    def test_markup_characters_are_refused(self, name):
        with pytest.raises(ValueError):
            dc.validate_device_name(name)

    def test_non_string_is_refused(self):
        with pytest.raises(ValueError):
            dc.validate_device_name(42)

    def test_unicode_letters_are_allowed(self):
        assert dc.validate_device_name("Récepteur Nord") == "Récepteur Nord"


class TestConfigValidation:
    def test_valid_record(self):
        assert dc.validate_device_config({"name": "Airband", "ppm": "-12", "gain": "38.6", "bias_t": True}) == {
            "name": "Airband",
            "ppm": -12,
            "gain": 38.6,
            "bias_t": True,
        }

    def test_blank_fields_are_dropped(self):
        assert dc.validate_device_config({"name": "", "ppm": "", "gain": None, "bias_t": None}) == {}

    @pytest.mark.parametrize(
        "record",
        [{"ppm": "5000"}, {"ppm": "abc"}, {"gain": "200"}, {"gain": "-1"}, {"bias_t": "yes"}, {"colour": "red"}],
    )
    def test_invalid_records_are_refused(self, record):
        with pytest.raises(ValueError):
            dc.validate_device_config(record)


class TestDeviceKey:
    def test_unique_serial_is_the_key(self):
        a, b = _rtl(0, "AIRBAND1"), _rtl(1, "ADSB0002")
        assert dc.device_key(a, [a, b]) == ("rtlsdr:sn:AIRBAND1", True)

    @pytest.mark.parametrize("serial", ["", "Unknown", "N/A", "0", "00000001", "00000000"])
    def test_placeholder_serial_falls_back_to_index(self, serial):
        d = _rtl(2, serial)
        assert dc.device_key(d, [d]) == ("rtlsdr:idx:2", False)

    def test_shared_serial_falls_back_to_index(self):
        """A serial two devices share identifies neither."""
        a, b = _rtl(0, "12345678"), _rtl(1, "12345678")
        assert dc.device_key(a, [a, b]) == ("rtlsdr:idx:0", False)
        assert dc.device_key(b, [a, b]) == ("rtlsdr:idx:1", False)

    def test_serial_with_unsafe_characters_falls_back_to_index(self):
        d = _rtl(0, "../../etc")
        assert dc.device_key(d, [d])[1] is False


class TestPersistence:
    def test_round_trip(self, temp_db):
        dc.set_device_config("rtlsdr:sn:AIRBAND1", {"name": "Airband", "ppm": 3})
        assert dc.get_device_config("rtlsdr:sn:AIRBAND1") == {"name": "Airband", "ppm": 3}

    def test_empty_record_is_deleted(self, temp_db):
        dc.set_device_config("rtlsdr:sn:AIRBAND1", {"name": "Airband"})
        dc.set_device_config("rtlsdr:sn:AIRBAND1", {"name": ""})
        assert temp_db.get_setting(dc.SETTING_PREFIX + "rtlsdr:sn:AIRBAND1") is None

    def test_invalid_key_is_refused(self, temp_db):
        with pytest.raises(ValueError, match="key"):
            dc.set_device_config("rtlsdr:sn:../x", {"name": "x"})

    def test_record_written_around_validation_is_ignored(self, temp_db):
        """The generic settings API can write any value; loading re-validates."""
        temp_db.set_setting(dc.SETTING_PREFIX + "rtlsdr:idx:0", {"name": "<script>", "ppm": 3})
        assert dc.get_device_config("rtlsdr:idx:0") == {}


class TestApplyDefaults:
    @pytest.fixture(autouse=True)
    def configured(self, temp_db, detected):
        detected.append(_rtl(0, "AIRBAND1"))
        dc.set_device_config("rtlsdr:sn:AIRBAND1", {"ppm": 42, "gain": 38.6, "bias_t": True})

    def test_defaults_fill_omitted_fields(self):
        assert dc.apply_device_defaults({"device": 0}) == {"device": 0, "ppm": 42, "gain": 38.6, "bias_t": True}

    def test_blank_fields_count_as_omitted(self):
        out = dc.apply_device_defaults({"device": "0", "ppm": "", "gain": "  ", "bias_t": None})
        assert (out["ppm"], out["gain"], out["bias_t"]) == (42, 38.6, True)

    def test_explicit_values_win(self):
        out = dc.apply_device_defaults({"device": 0, "ppm": "-7", "gain": "20", "bias_t": False})
        assert (out["ppm"], out["gain"], out["bias_t"]) == ("-7", "20", False)

    def test_explicit_zero_wins(self):
        assert dc.apply_device_defaults({"device": 0, "ppm": "0"})["ppm"] == "0"

    def test_other_device_is_unaffected(self, detected):
        detected.append(_rtl(1, "ADSB0002"))
        assert "ppm" not in dc.apply_device_defaults({"device": 1})

    def test_blank_without_default_is_removed(self, detected):
        """So the route's own default applies, as though the field were omitted."""
        detected.append(_rtl(1, "ADSB0002"))
        assert "gain" not in dc.apply_device_defaults({"device": 1, "gain": ""})

    def test_network_device_is_not_configured(self):
        assert "ppm" not in dc.apply_device_defaults({"device": 0, "rtl_tcp_host": "10.0.0.5"})

    def test_config_follows_the_serial_across_a_replug(self, detected):
        detected[:] = [_rtl(0, "ADSB0002"), _rtl(1, "AIRBAND1")]
        assert dc.apply_device_defaults({"device": 1})["ppm"] == 42
        assert "ppm" not in dc.apply_device_defaults({"device": 0})


def test_no_hardware_probe_when_nothing_is_configured(temp_db):
    with patch.object(dc, "_detected_devices", side_effect=AssertionError("probed")):
        assert dc.apply_device_defaults({"device": 0}) == {"device": 0}


class TestDeviceEndpoints:
    @pytest.fixture
    def devices(self, temp_db, detected):
        detected.append(_rtl(0, "AIRBAND1"))
        with patch("app.SDRFactory.detect_devices", return_value=list(detected)):
            yield

    def test_configured_name_replaces_the_detected_one(self, client, devices):
        resp = client.put("/devices/config/rtlsdr:sn:AIRBAND1", json={"name": "Airband Antenna", "ppm": -3})
        assert resp.status_code == 200

        [dev] = client.get("/devices").get_json()
        assert dev["name"] == "Airband Antenna"
        assert dev["hardware_name"] == "Generic RTL2832U"
        assert dev["config"] == {"name": "Airband Antenna", "ppm": -3}
        assert dev["keyed_by_serial"] is True
        assert client.get("/devices/status").get_json()[0]["name"] == "Airband Antenna"

    def test_invalid_name_is_rejected(self, client, devices):
        resp = client.put("/devices/config/rtlsdr:sn:AIRBAND1", json={"name": "bad\x1b[31mname"})
        assert resp.status_code == 400
        assert client.get("/devices").get_json()[0]["name"] == "Generic RTL2832U"

    def test_saving_works_with_csrf_enforced(self, app, client, devices):
        """The settings tab saves with fetch(), which carries no CSRF token.
        The test suite disables CSRF, so re-enable it to prove the route is exempt."""
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            resp = client.put("/devices/config/rtlsdr:sn:AIRBAND1", json={"name": "Airband Antenna"})
        finally:
            app.config["WTF_CSRF_ENABLED"] = False
        assert resp.status_code == 200

    def test_delete_restores_the_detected_name(self, client, devices):
        client.put("/devices/config/rtlsdr:sn:AIRBAND1", json={"name": "Airband Antenna"})
        assert client.delete("/devices/config/rtlsdr:sn:AIRBAND1").status_code == 200
        assert client.get("/devices").get_json()[0]["name"] == "Generic RTL2832U"


class TestModeStartUsesDefaults:
    """A start request that omits PPM/gain gets the device's; explicit wins."""

    @pytest.fixture
    def started_cmd(self, client, temp_db, detected):
        detected.append(_rtl(0, "AIRBAND1"))
        dc.set_device_config("rtlsdr:sn:AIRBAND1", {"ppm": 42, "gain": 30})
        captured = []

        def fake_popen(cmd, *args, **kwargs):
            captured.append(cmd)
            raise FileNotFoundError(cmd[0])  # stop before any reader threads start

        def start(**body):
            captured.clear()
            with (
                patch("app.claim_sdr_device", return_value=None),
                patch("app.release_sdr_device"),
                patch("routes.sensor.subprocess.Popen", side_effect=fake_popen),
            ):
                client.post("/start_sensor", json={"frequency": "433.92", "device": "0", **body})
            return captured[0]

        return start

    @staticmethod
    def _flag(cmd, flag):
        return cmd[cmd.index(flag) + 1] if flag in cmd else None

    def test_defaults_applied_when_omitted(self, started_cmd):
        cmd = started_cmd()
        assert self._flag(cmd, "-p") == "42"
        assert float(self._flag(cmd, "-g")) == 30

    def test_defaults_applied_when_blank(self, started_cmd):
        cmd = started_cmd(ppm="", gain="")
        assert self._flag(cmd, "-p") == "42"

    def test_explicit_values_override_defaults(self, started_cmd):
        cmd = started_cmd(ppm="-5", gain="12")
        assert self._flag(cmd, "-p") == "-5"
        assert float(self._flag(cmd, "-g")) == 12

    def test_invalid_explicit_value_is_still_rejected(self, client, started_cmd):
        resp = client.post("/start_sensor", json={"device": "0", "ppm": "99999"})
        assert resp.status_code == 400


class TestBuildersCarryPpm:
    def test_dump1090_gets_ppm(self):
        cmd = RTLSDRCommandBuilder().build_adsb_command(_rtl(0, "X"), gain=40, ppm=-12)
        assert cmd[cmd.index("--ppm") + 1] == "-12"

    def test_ais_catcher_gets_ppm(self):
        cmd = RTLSDRCommandBuilder().build_ais_command(_rtl(0, "X"), gain=40, ppm=7)
        assert cmd[cmd.index("-p") + 1] == "7"

    def test_no_flag_without_ppm(self):
        assert "--ppm" not in RTLSDRCommandBuilder().build_adsb_command(_rtl(0, "X"), gain=40)
        assert "-p" not in RTLSDRCommandBuilder().build_ais_command(_rtl(0, "X"), gain=40)


class TestAgent:
    @pytest.fixture
    def manager(self):
        from intercept_agent import ModeManager

        return ModeManager()

    def test_agent_applies_device_defaults(self, manager):
        seen = {}

        def fake_start(params):
            seen.update(params)
            return {"status": "started"}

        with (
            patch.object(dc, "config_for_index", return_value={"ppm": 9, "gain": 25.0}),
            patch.object(manager, "_start_sensor", side_effect=fake_start),
        ):
            manager._start_mode_internal("sensor", {"device": "0", "ppm": ""})
        assert seen["ppm"] == 9 and seen["gain"] == 25.0

    def test_agent_explicit_value_wins(self, manager):
        seen = {}
        with (
            patch.object(dc, "config_for_index", return_value={"ppm": 9}),
            patch.object(manager, "_start_sensor", side_effect=lambda p: seen.update(p) or {"status": "started"}),
        ):
            manager._start_mode_internal("sensor", {"device": "0", "ppm": "-2"})
        assert seen["ppm"] == "-2"

    def test_agent_rejects_invalid_ppm(self, manager):
        with patch.object(manager, "_start_sensor") as start:
            result = manager._start_mode_internal("sensor", {"device": "0", "ppm": "5000"})
        assert result["status"] == "error"
        start.assert_not_called()
