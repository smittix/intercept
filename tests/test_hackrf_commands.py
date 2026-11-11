"""Tests for HackRF command builder."""

import subprocess
from unittest.mock import MagicMock

import pytest

from utils.sdr import hackrf
from utils.sdr.base import SDRDevice, SDRType
from utils.sdr.hackrf import HackRFCommandBuilder


def _make_device(serial: str = "abc123") -> SDRDevice:
    return SDRDevice(
        sdr_type=SDRType.HACKRF,
        index=0,
        name="HackRF One",
        serial=serial,
        driver="hackrf",
        capabilities=HackRFCommandBuilder.CAPABILITIES,
    )


class TestHackRFCapabilities:
    def test_gain_max_reflects_combined_lna_vga(self):
        """gain_max should be LNA(40) + VGA(62) = 102."""
        assert HackRFCommandBuilder.CAPABILITIES.gain_max == 102.0

    def test_frequency_range(self):
        caps = HackRFCommandBuilder.CAPABILITIES
        assert caps.freq_min_mhz == 1.0
        assert caps.freq_max_mhz == 6000.0

    def test_tx_capable(self):
        assert HackRFCommandBuilder.CAPABILITIES.tx_capable is True


class TestSplitGain:
    # LNA moves in 8 dB steps (0-40), VGA in 2 dB steps (0-62). Gain is shared
    # across both: with VGA left at 0 a broadcast FM station measured 0.7 dB
    # above the noise floor on a real HackRF, versus 8-15 dB when balanced.
    def test_low_gain_keeps_vga_active(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(30)
        assert (lna, vga) == (16, 14)

    def test_mid_gain_splits_across_stages(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(40)
        assert (lna, vga) == (16, 24)

    def test_steps_match_hardware(self):
        builder = HackRFCommandBuilder()
        for gain in range(0, 103):
            lna, vga = builder._split_gain(gain)
            assert lna % 8 == 0 and 0 <= lna <= 40
            assert vga % 2 == 0 and 0 <= vga <= 62
            assert lna + vga <= gain

    def test_high_gain_splits_across_stages(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(80)
        assert lna == 40
        assert vga == 40

    def test_max_combined_gain(self):
        builder = HackRFCommandBuilder()
        lna, vga = builder._split_gain(102)
        assert lna == 40
        assert vga == 62


class TestBuildAdsbCommand:
    @pytest.fixture
    def native(self, monkeypatch):
        """readsb built with only the native HackRF backend."""
        monkeypatch.setattr(hackrf, "_readsb_has_native_hackrf", lambda: True)
        monkeypatch.setattr(hackrf, "_readsb_has_soapysdr", lambda: False)

    @pytest.fixture
    def soapy_only(self, monkeypatch):
        monkeypatch.setattr(hackrf, "_readsb_has_native_hackrf", lambda: False)
        monkeypatch.setattr(hackrf, "_readsb_has_soapysdr", lambda: True)

    @pytest.fixture
    def both(self, monkeypatch):
        monkeypatch.setattr(hackrf, "_readsb_has_native_hackrf", lambda: True)
        monkeypatch.setattr(hackrf, "_readsb_has_soapysdr", lambda: True)

    def test_prefers_soapysdr_when_both_backends_exist(self, both):
        # readsb's native callback expects 128 KiB blocks; libhackrf 2026.01
        # delivers 256 KiB and readsb discards the excess: half of all samples
        # (samples_dropped == samples_processed on a real HackRF One).
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(serial="deadbeef"), gain=40)
        assert cmd[cmd.index("--device-type") + 1] == "soapysdr"

    def test_native_hackrf_device_type_with_serial(self, native):
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(serial="deadbeef"), gain=40)
        assert cmd[cmd.index("--device-type") + 1] == "hackrf"
        assert cmd[cmd.index("--device") + 1] == "deadbeef"

    def test_native_gain_split_into_lna_and_vga(self, native):
        # 71 dB -> LNA 32 + VGA 38 (both stages carry gain, in hardware steps)
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(), gain=71)
        assert cmd[cmd.index("--gain") + 1] == "32"
        assert cmd[cmd.index("--hackrf-vgagain") + 1] == "38"
        assert "--hackrf-enable-ampgain" not in cmd

    def test_native_without_serial_or_gain_uses_readsb_defaults(self, native):
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device("N/A"))
        assert "--device" not in cmd and "--gain" not in cmd

    @pytest.mark.parametrize("backend", ["native", "soapy_only"])
    def test_serves_sbs_on_30003(self, backend, request):
        # readsb 3.16 defaults --net-sbs-port to 0; INTERCEPT reads SBS on 30003
        request.getfixturevalue(backend)
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device())
        assert cmd[cmd.index("--net-sbs-port") + 1] == "30003"

    def test_soapy_fallback_uses_soapysdr_device_type(self, soapy_only):
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(serial="deadbeef"), gain=40)
        # wiedehopf readsb rejects "soapy": "SDR type 'soapy' not recognized"
        assert cmd[cmd.index("--device-type") + 1] == "soapysdr"
        # A SoapySDR-only readsb build has no --device option (#387)
        assert "--device" not in cmd
        assert cmd[cmd.index("--soapy-device") + 1] == "driver=hackrf,serial=deadbeef"

    def test_soapy_sets_lna_vga_and_amp_explicitly(self, soapy_only):
        # readsb's overall --gain goes through SoapyHackRF setGain, which lands
        # on odd splits (50 -> LNA 16 + VGA 16) and rejects its max (116). On a
        # real HackRF One, ADS-B decoded best at LNA 40 / VGA 40 with the RF
        # amp off; VGA 62 overloaded (0 messages).
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(serial="deadbeef"), gain=80)
        elements = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--soapy-gain-element"]
        assert elements == ["LNA:40", "VGA:40", "AMP:0"]
        assert "--gain" not in cmd

    @pytest.mark.parametrize("native_backend", [True, False])
    def test_bias_tee_uses_readsb_option_name(self, monkeypatch, native_backend):
        monkeypatch.setattr(hackrf, "_readsb_has_native_hackrf", lambda: native_backend)
        cmd = HackRFCommandBuilder().build_adsb_command(_make_device(), gain=40, bias_t=True)
        assert "--enable-biastee" in cmd and "--enable-bias-t" not in cmd


class TestReadsbNativeHackRFProbe:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        hackrf._readsb_has_native_hackrf.cache_clear()
        yield
        hackrf._readsb_has_native_hackrf.cache_clear()

    @pytest.mark.parametrize(
        "help_text,expected",
        [("HackRF options:\nuse with --device-type hackrf\n", True), ("use with --device-type soapysdr\n", False)],
    )
    def test_detects_hackrf_help_section(self, monkeypatch, help_text, expected):
        monkeypatch.setattr(hackrf, "get_tool_path", lambda name: "/usr/bin/readsb")
        monkeypatch.setattr(
            hackrf.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=help_text, stderr="")
        )
        assert hackrf._readsb_has_native_hackrf() is expected

    def test_no_readsb(self, monkeypatch):
        monkeypatch.setattr(hackrf, "get_tool_path", lambda name: None)
        assert hackrf._readsb_has_native_hackrf() is False


def _fake_tools(monkeypatch, available=("rx_sdr", "csdr", "rx_fm", "bash")):
    monkeypatch.setattr(hackrf, "get_tool_path", lambda name: f"/usr/bin/{name}" if name in available else None)


class TestBuildFMDemodCommand:
    def test_fm_uses_rx_sdr_csdr_pipeline(self, monkeypatch):
        _fake_tools(monkeypatch)
        cmd = HackRFCommandBuilder().build_fm_demod_command(_make_device(), frequency_mhz=153.35, sample_rate=22050)
        assert cmd[:2] == ["/usr/bin/bash", "-c"] and len(cmd) == 3
        script = cmd[2]
        # 22050 * 2 (channel) * 46 = 2,028,600 S/s; tuned capture/8 above target
        assert "/usr/bin/rx_sdr -d driver=hackrf,serial=abc123 -f 153603575 -s 2028600 -F CU8 -" in script
        for stage in (
            "convert_u8_f",
            "shift_addition_cc 0.125",
            "fir_decimate_cc 46 0.005435",
            "fmdemod_atan_cf",
            "rational_resampler_ff 1 2",
            "convert_f_s16",
        ):
            assert f"/usr/bin/csdr {stage} 2>/dev/null" in script
        assert "deemphasis" not in script
        assert "rx_fm" not in script

    def test_capture_rate_is_integer_multiple_of_output_rate(self, monkeypatch):
        _fake_tools(monkeypatch)
        for rate in (22050, 24000, 48000, 12000):
            script = HackRFCommandBuilder().build_fm_demod_command(_make_device(), 100.0, sample_rate=rate)[2]
            capture = int(script.split(" -s ")[1].split()[0])
            assert capture >= hackrf.HACKRF_MIN_SAMPLE_RATE
            assert capture % rate == 0

    def test_wfm_uses_wide_channel_and_deemphasis(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = HackRFCommandBuilder().build_fm_demod_command(
            _make_device(), 98.5, sample_rate=32000, modulation="wfm"
        )[2]
        # 32000 * 7 = 224 kHz channel, * 9 = 2,016,000 S/s capture
        assert "-s 2016000" in script
        assert "fir_decimate_cc 9 " in script
        assert "deemphasis_wfm_ff 224000 75e-6" in script
        assert "rational_resampler_ff 1 7" in script

    def test_gain_and_bias_tee_reach_rx_sdr(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = HackRFCommandBuilder().build_fm_demod_command(_make_device(), 100.0, gain=80, bias_t=True)[2]
        rx_part = script.split(" | ")[0]
        assert "-g LNA=40,VGA=40" in rx_part and " -T " in rx_part

    def test_device_string_and_paths_are_shell_quoted(self, monkeypatch):
        monkeypatch.setattr(hackrf, "get_tool_path", lambda name: f"/opt/my tools/{name}")
        evil = "x;touch /tmp/pwned"
        script = HackRFCommandBuilder().build_fm_demod_command(_make_device(serial=evil), 100.0)[2]
        assert f"-d 'driver=hackrf,serial={evil}'" in script
        assert "'/opt/my tools/rx_sdr'" in script and "'/opt/my tools/csdr' convert_u8_f" in script

    def test_bash_trap_stops_pipeline_on_terminate(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = HackRFCommandBuilder().build_fm_demod_command(_make_device(), 100.0)[2]
        assert script.startswith("trap 'kill $! 2>/dev/null; wait' TERM INT HUP; ")
        assert script.endswith(" & wait $!")

    @pytest.mark.parametrize("modulation", ["am", "usb", "lsb"])
    def test_non_fm_modulation_keeps_rx_fm(self, monkeypatch, modulation):
        _fake_tools(monkeypatch)
        cmd = HackRFCommandBuilder().build_fm_demod_command(
            _make_device(), 7.1, sample_rate=12000, modulation=modulation, squelch=5
        )
        assert cmd[0] == "/usr/bin/rx_fm"
        assert cmd[cmd.index("-M") + 1] == modulation
        assert cmd[cmd.index("-l") + 1] == "5"
        assert cmd[-1] == "-"

    @pytest.mark.parametrize("missing", ["rx_sdr", "csdr"])
    def test_falls_back_to_rx_fm_without_csdr_tools(self, monkeypatch, missing):
        _fake_tools(monkeypatch, available={"rx_sdr", "csdr", "rx_fm"} - {missing})
        cmd = HackRFCommandBuilder().build_fm_demod_command(_make_device(), 153.35, modulation="fm")
        assert cmd[0] == "/usr/bin/rx_fm"


class TestListeningPostKeepsPipeline:
    def test_rx_fm_override_does_not_clobber_bash(self, monkeypatch):
        """The listening post must not replace argv[0] of the bash pipeline with rx_fm."""
        import routes.listening_post as lp

        _fake_tools(monkeypatch)
        monkeypatch.setattr(lp, "find_rx_fm", lambda: "/usr/local/bin/rx_fm")
        monkeypatch.setattr(lp, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        spawned = []

        class _Stop(Exception):
            pass

        def fake_popen(cmd, *a, **k):
            spawned.append(cmd)
            raise _Stop

        monkeypatch.setattr(lp.subprocess, "Popen", fake_popen)
        try:
            lp._start_audio_stream(153.35, "fm", device=0, sdr_type="hackrf")
        except _Stop:
            pass
        assert spawned, "audio pipeline was never spawned"
        assert spawned[0][0] == "/usr/bin/bash"


class TestBuildIQCaptureCommand:
    def test_outputs_cu8_to_stdout(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_iq_capture_command(_make_device(), frequency_mhz=100.0, sample_rate=2048000, gain=40)
        assert "-F" in cmd
        assert "CU8" in cmd
        assert cmd[-1] == "-"

    def test_gain_split_in_command(self):
        builder = HackRFCommandBuilder()
        cmd = builder.build_iq_capture_command(_make_device(), frequency_mhz=100.0, gain=80)
        gain_idx = cmd.index("-g")
        assert cmd[gain_idx + 1] == "LNA=40,VGA=40"


class TestBuildHackRFTransferIQCommand:
    """Fallback I/Q capture when SoapySDR's rx_sdr is not installed."""

    def test_streams_to_stdout_with_frequency_and_rate(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(
            _make_device(), frequency_mhz=143.05, sample_rate=2000000
        )
        assert cmd[0].endswith("hackrf_transfer")
        assert cmd[cmd.index("-r") + 1] == "-"
        assert cmd[cmd.index("-f") + 1] == "143050000"
        assert cmd[cmd.index("-s") + 1] == "2000000"
        assert cmd[cmd.index("-d") + 1] == "abc123"

    def test_gain_rounded_to_hardware_steps(self):
        # 70 dB -> LNA 32 (8 dB steps) + VGA 38 (2 dB steps)
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, gain=70)
        assert cmd[cmd.index("-l") + 1] == "32"
        assert cmd[cmd.index("-g") + 1] == "38"
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, gain=21)
        assert cmd[cmd.index("-l") + 1] == "8"
        assert cmd[cmd.index("-g") + 1] == "12"

    def test_auto_gain_and_unknown_serial_add_no_flags(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device("N/A"), frequency_mhz=100.0)
        assert "-l" not in cmd and "-g" not in cmd and "-d" not in cmd

    def test_bias_tee(self):
        cmd = HackRFCommandBuilder().build_hackrf_transfer_iq_command(_make_device(), frequency_mhz=100.0, bias_t=True)
        assert cmd[cmd.index("-p") + 1] == "1"


class TestAISCommand:
    def test_selects_the_hackrf_by_serial_with_native_gain(self):
        cmd = HackRFCommandBuilder().build_ais_command(_make_device("abc123"), gain=55, tcp_port=10110)
        assert cmd[cmd.index("-d") + 1] == "abc123"
        assert cmd[cmd.index("-gf") + 1 : cmd.index("-gf") + 5] == ["LNA", "24", "VGA", "30"]

    @pytest.mark.parametrize("serial", ["N/A", ""])
    def test_refuses_without_a_serial(self, serial):
        # Without -d, AIS-catcher would open whichever SDR it finds first
        with pytest.raises(ValueError, match="serial"):
            HackRFCommandBuilder().build_ais_command(_make_device(serial), gain=40)

    def test_bias_t_is_warned_about_not_silently_dropped(self, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(hackrf.logger, "warning", warn)
        cmd = HackRFCommandBuilder().build_ais_command(_make_device(), gain=40, bias_t=True)
        assert "Bias-T" in warn.call_args[0][0]
        assert "biastee" not in " ".join(cmd).lower()
