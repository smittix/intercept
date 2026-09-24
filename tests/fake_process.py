"""A subprocess.Popen stand-in backed by real OS pipes.

Reader threads block on these pipes exactly as they do on a real decoder.
A MagicMock whose readline() returns instantly makes readers spin and tear
down state mid-assertion; that is what this replaces.

The fake follows Popen where the routes depend on it: wait(timeout) raises
TimeoutExpired while running, text mode decodes, a file descriptor handed to
the child (a PTY slave) is held open the way a child holds it, and process-
group signalling (os.getpgid / os.killpg / os.kill) reaches the fake. Fake
pids sit above pid_max, so a signal can never reach a real process.
"""

import io
import os
import subprocess
import threading


class FakeProcess:
    instances = []
    # Bytes a given executable writes to stdout as soon as it starts, the way
    # rtl_fm starts streaming samples: {"rtl_fm": bytes(...)}.
    startup_output: dict = {}
    _next_pid = 10_000_000  # above the kernel limit (2**22), never a real process

    def __init__(
        self,
        args,
        *a,
        stdout=None,
        stderr=None,
        stdin=None,
        text=None,
        universal_newlines=None,
        encoding=None,
        errors=None,
        bufsize=-1,
        **k,
    ):
        self.args = args
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.returncode = None
        self._lock = threading.Lock()
        self._exited = threading.Event()
        self._writers = {}
        self._text = bool(text or universal_newlines or encoding or errors)
        self._encoding = encoding or "utf-8"
        self._errors = errors or "strict"
        self.stdout = self._stream("stdout", stdout)
        self.stderr = self._stream("stderr", stderr)
        self.stdin = open(os.devnull, "w" if self._text else "wb") if stdin == subprocess.PIPE else None
        FakeProcess.instances.append(self)
        exe = (args[0] if isinstance(args, list | tuple) else str(args).split()[0]).rsplit("/", 1)[-1]
        if exe in FakeProcess.startup_output and "stdout" in self._writers:
            self.emit(FakeProcess.startup_output[exe])

    def _stream(self, name, target):
        if target == subprocess.PIPE:
            return self._pipe(name)
        if isinstance(target, int) and target >= 0:
            # An fd handed to the child (a PTY slave, say): a real child holds
            # its own copy, so the parent closing theirs does not signal EOF.
            self._writers[name] = os.dup(target)
        elif hasattr(target, "fileno"):
            self._writers[name] = os.dup(target.fileno())
        return None

    def _pipe(self, name):
        r, w = os.pipe()
        self._writers[name] = w
        raw = os.fdopen(r, "rb")
        if self._text:
            return io.TextIOWrapper(raw, encoding=self._encoding, errors=self._errors)
        return raw

    def emit(self, data: bytes, stream="stdout"):
        """Write bytes as though the decoder printed them. Never blocks: a
        stream nobody reads fills its pipe, and the rest is dropped."""
        fd = self._writers[stream]
        os.set_blocking(fd, False)
        try:
            os.write(fd, data)
        except (BlockingIOError, BrokenPipeError):
            pass

    def poll(self):
        return self.returncode

    def _exit(self, code):
        with self._lock:
            if self.returncode is not None:
                return
            self.returncode = code
            for w in self._writers.values():
                try:
                    os.close(w)
                except OSError:
                    pass
            self._exited.set()

    def terminate(self):
        self._exit(-15)

    def kill(self):
        self._exit(-9)

    def send_signal(self, sig):
        self._exit(-int(sig))

    def wait(self, timeout=None):
        if not self._exited.wait(timeout if timeout is not None else 5):
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def communicate(self, input=None, timeout=None):
        # communicate() is how subprocess.run() drives a one-shot command
        # (a --help probe, say); a real one prints and exits.
        self._exit(0)
        return ("", "") if self._text else (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def alive(self):
        return self.returncode is None

    # Process-group signalling, as several routes stop decoders with os.killpg.
    @classmethod
    def by_pid(cls, pid):
        return next((p for p in cls.instances if p.pid == pid), None)

    @classmethod
    def getpgid(cls, pid):
        if cls.by_pid(pid) is None:
            raise ProcessLookupError(pid)
        return pid

    @classmethod
    def killpg(cls, pgid, sig):
        proc = cls.by_pid(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.send_signal(sig)

    @classmethod
    def kill_pid(cls, pid, sig):
        proc = cls.by_pid(pid)
        if proc is None:
            raise ProcessLookupError(pid)
        if sig:
            proc.send_signal(sig)
