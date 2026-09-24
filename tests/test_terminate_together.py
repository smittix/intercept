"""Stopping a mode's processes: together, and not reported done until gone.

Real child processes, because what matters is how actual signals and
waits behave: one that ignores SIGTERM stands in for rtl_fm blocked on a
full pipe, or a decoder busy on USB.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from utils.process import terminate_together

IGNORES_SIGTERM = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
)
OBEYS_SIGTERM = "import time; print('ready', flush=True); time.sleep(60)"


def _spawn(code, **kwargs):
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, **kwargs)
    proc.stdout.readline()  # its signal handling is in place
    return proc


def test_a_process_that_obeys_sigterm_stops_at_once():
    proc = _spawn(OBEYS_SIGTERM)
    started = time.monotonic()
    assert terminate_together([proc], timeout=2) == []
    assert time.monotonic() - started < 1.0
    assert proc.poll() is not None


def test_processes_that_ignore_sigterm_share_one_deadline_and_are_killed():
    procs = [_spawn(IGNORES_SIGTERM), _spawn(IGNORES_SIGTERM)]
    started = time.monotonic()
    assert terminate_together(procs, timeout=0.5, kill_timeout=2) == []
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"took {elapsed:.2f}s: waited in turn, not together"
    assert all(p.poll() is not None for p in procs), "reported done while a process was still alive"


def test_a_decoders_children_go_with_it():
    """A decoder started in its own session is stopped as a group."""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time;"
            f"c = subprocess.Popen([sys.executable, '-c', {IGNORES_SIGTERM!r}], stdout=subprocess.PIPE);"
            "c.stdout.readline(); print(c.pid, flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    child_pid = int(parent.stdout.readline())
    terminate_together([parent], timeout=0.5, kill_timeout=2)
    time.sleep(0.2)
    try:
        os.kill(child_pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "the decoder's child outlived it"


def test_none_and_already_exited_are_fine():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    assert terminate_together([None, done]) == []
