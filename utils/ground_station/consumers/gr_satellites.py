"""GrSatConsumer — pipes CU8 IQ to gr_satellites for packet decoding.

``gr_satellites`` is a GNU Radio-based multi-satellite decoder
(https://github.com/daniestevez/gr-satellites).  It accepts complex
float32 (cf32) IQ samples on stdin when invoked with ``--iq``.

This consumer converts CU8 → cf32 via numpy and pipes the result to
``gr_satellites``.  If the tool is not installed it silently stays
disabled.

Decoded JSON packets are forwarded to an optional ``on_decoded`` callback.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Callable

import numpy as np

from utils.logging import get_logger
from utils.process import register_process, safe_terminate, unregister_process

logger = get_logger("intercept.ground_station.gr_satellites")

GR_SATELLITES_BIN = "gr_satellites"


class GrSatConsumer:
    """CU8 IQ → cf32 → gr_satellites stdin → JSON packets."""

    def __init__(
        self,
        satellite_name: str,
        *,
        on_decoded: Callable[[dict], None] | None = None,
    ):
        """
        Args:
            satellite_name: Satellite name as known to gr_satellites
                (e.g. ``'NOAA 15'``, ``'ISS'``).
            on_decoded: Callback invoked with each parsed JSON packet dict.
        """
        self._satellite_name = satellite_name
        self._on_decoded = on_decoded
        self._proc: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._sample_rate = 0
        self._enabled = False

    # ------------------------------------------------------------------
    # IQConsumer protocol
    # ------------------------------------------------------------------

    def on_start(
        self,
        center_mhz: float,
        sample_rate: int,
        *,
        start_freq_mhz: float,
        end_freq_mhz: float,
    ) -> None:
        self._sample_rate = sample_rate
        if not shutil.which(GR_SATELLITES_BIN):
            logger.info(
                "gr_satellites not found — GrSatConsumer disabled. "
                "See https://gr-satellites.readthedocs.io/en/latest/installation.html"
            )
            self._enabled = False
            return
        self._start_proc(sample_rate)

    def on_chunk(self, raw: bytes) -> None:
        if not self._enabled or self._proc is None or self._proc.poll() is not None:
            return
        # Convert CU8 → cf32
        try:
            iq = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            cf32 = ((iq - 127.5) / 127.5).view(np.complex64)
            if self._proc.stdin:
                self._proc.stdin.write(cf32.tobytes())
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        except Exception as e:
            logger.debug(f"GrSatConsumer on_chunk error: {e}")

    def on_stop(self) -> None:
        self._enabled = False
        if self._proc:
            safe_terminate(self._proc)
            unregister_process(self._proc)
            self._proc = None
        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=2)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_proc(self, sample_rate: int) -> None:
        import json as _json

        cmd = [
            GR_SATELLITES_BIN,
            self._satellite_name,
            "--samplerate",
            str(sample_rate),
            "--iq",
            "--json",
            "-",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            register_process(self._proc)
            self._enabled = True
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(_json,),
                daemon=True,
                name="gr-sat-stdout",
            )
            self._stdout_thread.start()
            logger.info(f"GrSatConsumer started for '{self._satellite_name}'")
        except Exception as e:
            logger.error(f"GrSatConsumer: failed to start gr_satellites: {e}")
            self._proc = None
            self._enabled = False

    def _read_stdout(self, _json) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                if self._on_decoded:
                    try:
                        data = _json.loads(text)
                    except _json.JSONDecodeError:
                        data = {"raw": text}
                    try:
                        self._on_decoded(data)
                    except Exception as e:
                        logger.debug(f"GrSatConsumer callback error: {e}")
        except Exception:
            pass
