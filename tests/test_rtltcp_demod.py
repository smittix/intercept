"""FM/AM demodulation from remote rtl_tcp servers.

Stock rtl_fm cannot open ``rtl_tcp:host:port``, so the RTL builder feeds the
shared csdr pipeline from utils/sdr/rtltcp_iq.py for network devices.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from utils.sdr import SDRFactory, rtlsdr, rtltcp_iq
from utils.sdr.rtlsdr import RTLSDRCommandBuilder

HAVE_CSDR = shutil.which("csdr") is not None and shutil.which("bash") is not None


def _fake_tools(monkeypatch, available=("csdr", "bash", "rtl_fm")):
    monkeypatch.setattr(rtlsdr, "get_tool_path", lambda name: f"/usr/bin/{name}" if name in available else None)


def _remote(host="100.85.185.5", port=1234):
    return SDRFactory.create_network_device(host, port)


def _source(script: str) -> list[str]:
    return shlex.split(script.split(" HUP; ", 1)[1].split(" | ")[0])


class TestBuilder:
    def test_nfm_streams_from_rtltcp_client_into_csdr(self, monkeypatch):
        _fake_tools(monkeypatch)
        cmd = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 153.35, sample_rate=22050, modulation="fm")
        assert cmd[:2] == ["/usr/bin/bash", "-c"] and len(cmd) == 3
        script = cmd[2]
        assert "rtl_fm" not in script
        # 22050 * 2 (channel) * 6 = 264,600 S/s (RTL low band); tuned capture/8 above target
        assert _source(script) == [
            sys.executable,
            rtlsdr.RTLTCP_IQ_SCRIPT,
            "--host",
            "100.85.185.5",
            "--port",
            "1234",
            "--freq",
            str(153_350_000 + 264_600 // 8),
            "--rate",
            "264600",
        ]
        for stage in ("convert_u8_f", "shift_addition_cc 0.125", "fir_decimate_cc 6 ", "fmdemod_atan_cf"):
            assert f"/usr/bin/csdr {stage}" in script
        assert "rational_resampler_ff 1 2" in script and script.endswith("convert_f_s16 2>/dev/null & wait $!")
        assert script.startswith("trap 'kill $! 2>/dev/null; wait' TERM INT HUP; ")

    @pytest.mark.parametrize("rate", [22050, 24000, 48000, 12000, 8000, 32000])
    @pytest.mark.parametrize("modulation", ["fm", "nfm", "wfm", "am", "usb"])
    def test_capture_rate_is_a_valid_rtl_rate_and_multiple_of_output(self, monkeypatch, rate, modulation):
        _fake_tools(monkeypatch)
        script = RTLSDRCommandBuilder().build_fm_demod_command(
            _remote(), 100.0, sample_rate=rate, modulation=modulation
        )[2]
        source = _source(script)
        capture = int(source[source.index("--rate") + 1])
        assert 225_001 <= capture <= 300_000 or 900_001 <= capture <= 3_200_000
        assert capture % rate == 0

    def test_wfm_uses_high_rate_band_and_deemphasis(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 98.5, sample_rate=48000, modulation="wfm")[2]
        # 48000 * 5 = 240 kHz channel: the low band leaves no room for the offset tune, so * 4 = 960 kS/s
        assert "--rate 960000" in script
        assert "deemphasis_wfm_ff 240000 75e-6" in script and "rational_resampler_ff 1 5" in script

    def test_am_uses_envelope_demod(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 7.03, sample_rate=48000, modulation="am")[2]
        assert "amdemod_cf" in script and "fmdemod" not in script

    @pytest.mark.parametrize("modulation,band", [("usb", "0.000000 0.068027"), ("lsb", "-0.068027 0.000000")])
    def test_ssb_uses_sideband_filter(self, monkeypatch, modulation, band):
        _fake_tools(monkeypatch)
        script = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 7.03, modulation=modulation)[2]
        assert f"bandpass_fir_fft_cc {band} " in script and "realpart_cf" in script

    def test_gain_ppm_bias_and_direct_sampling_reach_the_client(self, monkeypatch):
        _fake_tools(monkeypatch)
        script = RTLSDRCommandBuilder().build_fm_demod_command(
            _remote(), 7.03, gain=29.7, ppm=-3, bias_t=True, modulation="usb", direct_sampling=2, squelch=10
        )[2]
        source = _source(script)
        assert source[source.index("--gain") + 1] == "29.7"
        assert source[source.index("--ppm") + 1] == "-3"
        assert source[source.index("--direct-sampling") + 1] == "2"
        assert "--bias-t" in source

    def test_auto_gain_sends_no_gain(self, monkeypatch):
        _fake_tools(monkeypatch)
        source = _source(RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 153.35)[2])
        assert "--gain" not in source and "--ppm" not in source and "--bias-t" not in source

    def test_local_device_keeps_rtl_fm(self, monkeypatch):
        _fake_tools(monkeypatch)
        device = SDRFactory.create_default_device(rtlsdr.SDRType.RTL_SDR, index=0)
        cmd = RTLSDRCommandBuilder().build_fm_demod_command(device, 153.35)
        assert cmd[0] == "/usr/bin/rtl_fm" and cmd[-1] == "-"

    def test_without_csdr_falls_back_to_rtl_fm(self, monkeypatch):
        _fake_tools(monkeypatch, available=("rtl_fm",))
        cmd = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 153.35)
        assert cmd[0] == "/usr/bin/rtl_fm"
        assert cmd[cmd.index("-d") + 1] == "rtl_tcp:100.85.185.5:1234"

    def test_callers_rtl_fm_flag_appends_skip_the_pipeline(self, monkeypatch):
        """DSC/APRS append -E dc etc. only when argv ends in '-' (rtl_fm)."""
        _fake_tools(monkeypatch)
        cmd = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 156.525, sample_rate=48000)
        assert cmd[-1] != "-"


class FakeRtlTcp:
    """One-client rtl_tcp server: RTL0 header, records commands, streams CU8."""

    def __init__(self, iq: bytes = b"", tuner: int = 5, send_header: bool = True):
        self.iq = iq
        self.tuner = tuner
        self.send_header = send_header
        self.commands: list[tuple[int, int]] = []
        self.client_closed = threading.Event()
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            if not self.send_header:
                self.client_closed.wait(10)
                return
            conn.sendall(b"RTL0" + struct.pack(">II", self.tuner, 29))
            conn.settimeout(0.3)
            buf = b""
            try:
                while True:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
            except TimeoutError:
                pass
            self.commands = [struct.unpack(">BI", buf[i : i + 5]) for i in range(0, len(buf) - 4, 5)]
            if not self.iq:
                return
            try:
                while True:
                    conn.sendall(self.iq)
            except OSError:
                self.client_closed.set()

    def close(self):
        self.client_closed.set()
        self.sock.close()


class TestClient:
    def test_connect_failure_exits_nonzero_with_message(self, capsys):
        port = socket.create_server(("127.0.0.1", 0))
        free = port.getsockname()[1]
        port.close()
        assert rtltcp_iq.main(["--host", "127.0.0.1", "--port", str(free), "--freq", "1", "--rate", "1"]) == 1
        assert "cannot connect to rtl_tcp server 127.0.0.1" in capsys.readouterr().err

    def test_busy_server_without_header_exits_nonzero(self, monkeypatch, capsys):
        monkeypatch.setattr(rtltcp_iq, "HEADER_TIMEOUT_S", 0.2)
        server = FakeRtlTcp(send_header=False)
        try:
            code = rtltcp_iq.main(["--host", "127.0.0.1", "--port", str(server.port), "--freq", "1", "--rate", "1"])
        finally:
            server.close()
        assert code == 1
        assert "busy with another client" in capsys.readouterr().err

    def test_sends_tuning_commands(self, capsys):
        server = FakeRtlTcp()
        args = ["--host", "127.0.0.1", "--port", str(server.port), "--freq", "153383075", "--rate", "264600"]
        assert rtltcp_iq.main(args + ["--gain", "29.7", "--ppm", "-3", "--direct-sampling", "2", "--bias-t"]) == 1
        server.close()
        assert "closed the connection" in capsys.readouterr().err
        assert server.commands == [
            (0x09, 2),
            (0x02, 264600),
            (0x01, 153383075),
            (0x03, 1),
            (0x04, 297),
            (0x05, (-3) & 0xFFFFFFFF),
            (0x0E, 1),
        ]

    def test_auto_gain_and_direct_sampling_off_by_default(self):
        server = FakeRtlTcp()
        rtltcp_iq.main(["--host", "127.0.0.1", "--port", str(server.port), "--freq", "100000000", "--rate", "960000"])
        server.close()
        assert server.commands == [(0x09, 0), (0x02, 960000), (0x01, 100000000), (0x03, 0)]

    def test_r828d_never_gets_direct_sampling(self, capsys):
        """RTL-SDR Blog V4: direct sampling gives a dead input; HF goes via its upconverter."""
        server = FakeRtlTcp(tuner=rtltcp_iq.TUNER_R828D)
        args = ["--host", "127.0.0.1", "--port", str(server.port), "--freq", "7030000", "--rate", "264600"]
        rtltcp_iq.main(args + ["--direct-sampling", "2"])
        server.close()
        assert server.commands[0] == (0x09, 0)
        assert "direct sampling disabled" in capsys.readouterr().err


def _cu8(modulation: str, capture_rate: int, offset_hz: float, tone_hz: float = 1000.0, amplitude: float = 60) -> bytes:
    """One second of CU8 I/Q: a carrier at offset_hz modulated by a tone (3 kHz FM deviation or 50% AM)."""
    t = np.arange(capture_rate) / capture_rate
    phase = 2 * np.pi * offset_hz * t
    envelope = np.ones_like(t)
    if modulation == "fm":
        phase += (3000.0 / tone_hz) * np.sin(2 * np.pi * tone_hz * t)
    else:
        envelope += 0.5 * np.sin(2 * np.pi * tone_hz * t)
    iq = np.empty(2 * capture_rate)
    iq[0::2], iq[1::2] = amplitude * envelope * np.cos(phase), amplitude * envelope * np.sin(phase)
    return np.clip(127.5 + iq, 0, 255).astype(np.uint8).tobytes()


def _children_alive(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-g", str(pid)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


@pytest.mark.skipif(not HAVE_CSDR, reason="needs csdr and bash")
@pytest.mark.parametrize("modulation", ["fm", "am"])
def test_pipeline_demodulates_fake_rtl_tcp_to_a_1khz_tone(modulation):
    rate = 22050
    device = _remote("127.0.0.1", 0)
    capture_rate = 264600
    # The client tunes capture/8 above the target: the signal sits at -capture/8
    server = FakeRtlTcp(iq=_cu8(modulation, capture_rate, -capture_rate / 8))
    device.rtl_tcp_port = server.port
    cmd = RTLSDRCommandBuilder().build_fm_demod_command(
        device, 153.35, sample_rate=rate, gain=30, modulation=modulation
    )
    assert f"--rate {capture_rate}" in cmd[2]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        audio = b""
        deadline = time.monotonic() + 10
        while len(audio) < 2 * rate * 2 and time.monotonic() < deadline:
            audio += proc.stdout.read1(65536)
        assert server.commands[:4] == [
            (0x09, 0),
            (0x02, capture_rate),
            (0x01, 153_350_000 + capture_rate // 8),
            (0x03, 1),
        ]
        pcm = np.frombuffer(audio[rate * 2 : 2 * rate * 2], dtype="<i2").astype(float)  # skip filter warm-up
        assert len(pcm) == rate
        spectrum = np.abs(np.fft.rfft((pcm - pcm.mean()) * np.hanning(len(pcm))))
        peak_hz = np.argmax(spectrum[1:]) + 1
        assert abs(peak_hz - 1000) <= 5, peak_hz
        assert pcm.std() > 1000
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        server.close()
    # bash's trap stopped every stage, including the rtl_tcp client
    for _ in range(50):
        if not _children_alive(proc.pid):
            break
        time.sleep(0.1)
    assert _children_alive(proc.pid) == []
    assert server.client_closed.wait(5), "the rtl_tcp connection must be closed on stop"
    assert os.path.exists(rtlsdr.RTLTCP_IQ_SCRIPT)


def test_squelch_level_maps_slider_to_channel_power():
    from utils.sdr.csdr_pipeline import squelch_level

    assert squelch_level(0) is None  # off
    assert squelch_level(1) == pytest.approx(10 ** (-79.3 / 10))
    assert squelch_level(100) == pytest.approx(10 ** (-10 / 10))


def test_squelch_adds_a_power_gate_before_demod(monkeypatch):
    _fake_tools(monkeypatch)
    cmd = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 153.35, sample_rate=22050, modulation="fm", squelch=40)
    script = cmd[2]
    assert "squelch_and_smeter_cc" in script
    assert script.index("squelch_and_smeter_cc") < script.index("fmdemod_atan_cf")
    assert "rm -rf" in script  # the control fifos are cleaned up
    off = RTLSDRCommandBuilder().build_fm_demod_command(_remote(), 153.35, sample_rate=22050, modulation="fm", squelch=0)
    assert "squelch_and_smeter_cc" not in off[2]


@pytest.mark.skipif(not HAVE_CSDR, reason="needs csdr and bash")
@pytest.mark.parametrize("squelch,opens", [(60, True), (80, False)])
def test_squelch_mutes_a_weak_signal_below_its_threshold(squelch, opens):
    # Carrier at amplitude 3/127.5: channel power about -32.6 dB.
    # Squelch 60 -> -38 dB threshold (open); 80 -> -24 dB (closed).
    rate, capture_rate = 22050, 264600
    server = FakeRtlTcp(iq=_cu8("fm", capture_rate, -capture_rate / 8, amplitude=3))
    device = _remote("127.0.0.1", server.port)
    cmd = RTLSDRCommandBuilder().build_fm_demod_command(device, 153.35, sample_rate=rate, gain=30, modulation="fm", squelch=squelch)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        audio = b""
        deadline = time.monotonic() + 10
        while len(audio) < 3 * rate * 2 and time.monotonic() < deadline:
            audio += proc.stdout.read1(65536)
        pcm = np.frombuffer(audio[2 * rate * 2 : 3 * rate * 2], dtype="<i2").astype(float)  # third second
        assert len(pcm) == rate, "squelch must keep audio flowing (silence, not a stall)"
        if opens:
            spectrum = np.abs(np.fft.rfft((pcm - pcm.mean()) * np.hanning(len(pcm))))
            assert abs(np.argmax(spectrum[1:]) + 1 - 1000) <= 5
        else:
            assert np.abs(pcm).max() < 50
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        server.close()
    for _ in range(50):
        if not _children_alive(proc.pid):
            break
        time.sleep(0.1)
    assert _children_alive(proc.pid) == []
