"""A waterfall process that survives terminate() must still be killed on stop, not left running.

Regression: the old stop called wait(timeout=1) and only killed when wait raised, so a decoder
that ignored SIGTERM was left running while waterfall_process was set to None.
"""
import subprocess
import sys
import time

from routes import listening_post as _state

_CHILD_CODE = "import signal,time,sys\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"


def test_waterfall_stop_kills_a_decoder_that_survives_terminate(monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", _CHILD_CODE],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.4)
        assert child.poll() is None, "precondition: child is alive"
        child.terminate()
        time.sleep(0.4)
        assert child.poll() is None, "precondition: child must ignore SIGTERM"

        monkeypatch.setattr(_state, "waterfall_process", child)
        monkeypatch.setattr(_state, "waterfall_running", True)
        monkeypatch.setattr(_state, "waterfall_active_device", None)

        _state._stop_waterfall_internal()

        assert child.poll() is not None, "decoder left running after stop"
        assert _state.waterfall_process is None
        assert child.returncode == -9, f"expected SIGKILL, got {child.returncode}"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
