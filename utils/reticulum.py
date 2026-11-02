"""Reticulum (RNS) / LXMF messaging.

Runs an LXMF router inside Intercept, attached either to a local rnsd shared
instance or, over TCP, to a remote rnsd TCPServerInterface. RNS keeps its
config, storage and our identity in a dedicated directory (instance/reticulum
by default), so the user's ~/.reticulum is never read or written.

RNS is a process-wide singleton that cannot be re-initialised, so the stack is
built once, on the first start. Stop/start afterwards only detaches/attaches
our LXMF destination; changing the connection itself needs a restart.

No link (no rnsd running, remote host unreachable) is a normal "waiting"
state: RNS keeps retrying on its own and its log goes to our debug log.

Install: pip install rns lxmf
"""

from __future__ import annotations

import contextlib
import enum
import queue
import re
import signal
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import config
from utils import database
from utils.logging import get_logger

logger = get_logger("intercept.reticulum")

try:
    import LXMF
    import RNS

    HAS_RETICULUM = True
except ImportError:
    HAS_RETICULUM = False
    logger.info("rns/lxmf not installed. Run: pip install rns lxmf")


def is_reticulum_available() -> bool:
    """Return True if the rns and lxmf libraries are installed."""
    return HAS_RETICULUM


SHARED_RETRY_SECONDS = 10
LINK_CHECK_SECONDS = 2
_HASH_RE = re.compile(r"^[0-9a-f]{32}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.:\-\[\]]{1,253}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------


@dataclass
class ReticulumConfig:
    """How to reach the Reticulum network, and how we announce ourselves."""

    mode: str = "shared"  # "shared" (local rnsd) or "tcp" (remote rnsd TCPServerInterface)
    host: str = ""
    port: int = 4242
    display_name: str = "INTERCEPT"
    instance_name: str = "default"  # rnsd shared instance name (shared mode)
    rpc_key: str = ""  # rnsd's rpc_key, optional (shared mode)

    def validate(self) -> None:
        """Raise ValueError when a field is unusable."""
        if self.mode not in ("shared", "tcp"):
            raise ValueError("mode must be 'shared' or 'tcp'")
        if self.mode == "tcp":
            if not _HOST_RE.match(self.host or ""):
                raise ValueError("a valid host is required for TCP mode")
            if not 1 <= int(self.port) <= 65535:
                raise ValueError("port must be 1-65535")
        if not _NAME_RE.match(self.instance_name or ""):
            raise ValueError("invalid instance name")
        if self.rpc_key and not re.fullmatch(r"[0-9a-fA-F]+", self.rpc_key):
            raise ValueError("rpc_key must be hex")
        name = (self.display_name or "").strip()
        if not name or len(name) > 64 or any(c in name for c in "\r\n"):
            raise ValueError("display name must be 1-64 characters")

    def link_key(self) -> tuple:
        """The fields baked into the RNS stack at first start."""
        if self.mode == "tcp":
            return ("tcp", self.host, int(self.port))
        return ("shared", self.instance_name, self.rpc_key)

    def describe(self) -> str:
        if self.mode == "tcp":
            return f"rnsd at {self.host}:{self.port} (TCP)"
        return f"local rnsd shared instance '{self.instance_name}'"


SETTINGS_KEY = "reticulum_connection"


def default_config() -> ReticulumConfig:
    """Env defaults (INTERCEPT_RETICULUM_*), overridden by the last saved UI settings."""
    cfg = ReticulumConfig(
        mode=config.RETICULUM_MODE,
        host=config.RETICULUM_TCP_HOST,
        port=config.RETICULUM_TCP_PORT,
        display_name=config.RETICULUM_DISPLAY_NAME,
        instance_name=config.RETICULUM_INSTANCE_NAME,
        rpc_key=config.RETICULUM_RPC_KEY,
    )
    saved = database.get_setting(SETTINGS_KEY)
    if isinstance(saved, dict):
        for key in ("mode", "host", "port", "display_name"):
            if key in saved:
                setattr(cfg, key, saved[key])
    return cfg


def save_config(cfg: ReticulumConfig) -> None:
    database.set_setting(
        SETTINGS_KEY, {"mode": cfg.mode, "host": cfg.host, "port": int(cfg.port), "display_name": cfg.display_name}
    )


def data_dir() -> Path:
    """Our own RNS/LXMF directory (never ~/.reticulum)."""
    if config.RETICULUM_DATA_DIR:
        return Path(config.RETICULUM_DATA_DIR)
    return database.DB_DIR / "reticulum"


def build_rns_config(cfg: ReticulumConfig) -> str:
    """The RNS config file text for this connection. No local radio interfaces."""
    lines = ["[reticulum]", "  enable_transport = No", "  panic_on_interface_error = No"]
    if cfg.mode == "shared":
        lines += ["  share_instance = Yes", f"  instance_name = {cfg.instance_name}"]
        if cfg.rpc_key:
            lines.append(f"  rpc_key = {cfg.rpc_key}")
    else:
        lines.append("  share_instance = No")
    lines += ["", "[logging]", "  loglevel = 4", "", "[interfaces]"]
    if cfg.mode == "tcp":
        lines += [
            "  [[INTERCEPT TCP]]",
            "    type = TCPClientInterface",
            "    enabled = yes",
            f"    target_host = {cfg.host}",
            f"    target_port = {int(cfg.port)}",
        ]
    return "\n".join(lines) + "\n"


def shared_instance_available(instance_name: str) -> bool:
    """True if a local rnsd shared instance is listening (probe only, no RNS init)."""
    try:
        if RNS.vendor.platformutils.use_af_unix():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target: object = f"\0rns/{instance_name}"
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target = ("127.0.0.1", 37428)
        sock.settimeout(2)
        with sock:
            sock.connect(target)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def _no_signal_handlers():
    """RNS and LXMF install SIGINT/SIGTERM handlers that exit the process; skip them."""
    original = signal.signal
    signal.signal = lambda *_a, **_k: None
    try:
        yield
    finally:
        signal.signal = original


def normalize_hash(value: str) -> str:
    """Accept '<abcd...>', 'ab:cd:...' or plain hex; return lowercase hex or raise ValueError."""
    h = re.sub(r"[\s<>:]", "", value or "").lower()
    if not _HASH_RE.match(h):
        raise ValueError("destination must be a 32-character hex LXMF address")
    return h


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ReticulumState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    WAITING = "waiting"  # running, but no link to the network (yet)
    ONLINE = "online"
    ERROR = "error"


class ReticulumClient:
    """Singleton LXMF messenger. Events go to a queue consumed by SSE."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._event_queue: queue.Queue = queue.Queue(maxsize=500)
        self._state = ReticulumState.STOPPED
        self._message: str | None = None
        self._cfg: ReticulumConfig | None = None
        self._link_key: tuple | None = None  # set once the RNS stack exists
        self._running = False
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._rns = None
        self._router = None
        self._local = None  # our lxmf.delivery destination
        self._announce_handler = None
        self._rpc_error: str | None = None

    # -- Queue / state --

    def get_queue(self) -> queue.Queue:
        return self._event_queue

    def _push(self, event: dict) -> None:
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._event_queue.get_nowait()
            with contextlib.suppress(queue.Full):
                self._event_queue.put_nowait(event)

    def _set_state(self, state: ReticulumState, message: str | None = None) -> None:
        with self._lock:
            changed = (state, message) != (self._state, self._message)
            self._state, self._message = state, message
        if changed:
            self._push({"type": "status", "data": self.get_status()})

    def _link_online(self) -> bool:
        if self._rns is None:
            return False
        return any(getattr(i, "online", False) for i in RNS.Transport.interfaces)

    def get_status(self) -> dict:
        """Current state, refreshing online/waiting from the live interface."""
        with self._lock:
            state, message, cfg, running = self._state, self._message, self._cfg, self._running
        if running and state in (ReticulumState.ONLINE, ReticulumState.WAITING):
            if self._rpc_error:
                state, message = ReticulumState.ERROR, self._rpc_error
            elif self._link_online():
                state, message = ReticulumState.ONLINE, f"Connected to {cfg.describe()}"
            else:
                state, message = ReticulumState.WAITING, f"No link to {cfg.describe()}; retrying"
            with self._lock:
                self._state, self._message = state, message
        status: dict = {"state": state.value, "message": message, "running": running}
        if cfg:
            status["config"] = asdict(cfg) | {"rpc_key": bool(cfg.rpc_key)}
        if self._local is not None:
            status["address"] = self._local.hash.hex()
            status["display_name"] = self._local.display_name
        return status

    # -- Lifecycle --

    def start(self, cfg: ReticulumConfig) -> None:
        """Start (or resume) in the background. Raises ValueError on a bad or changed config."""
        cfg.validate()
        with self._lock:
            if self._link_key is not None and cfg.link_key() != self._link_key:
                raise ValueError("The Reticulum connection is already set up; restart Intercept to change it")
            if self._running or (self._worker and self._worker.is_alive()):
                return
            self._cfg = cfg
            self._state = ReticulumState.STARTING
            self._stop_event = threading.Event()
            self._worker = threading.Thread(target=self._start_worker, args=(cfg, self._stop_event), daemon=True)
        self._push({"type": "status", "data": self.get_status()})
        self._worker.start()

    def _start_worker(self, cfg: ReticulumConfig, stop: threading.Event) -> None:
        try:
            if self._rns is None:
                if cfg.mode == "shared":
                    while not shared_instance_available(cfg.instance_name):
                        self._set_state(
                            ReticulumState.WAITING,
                            f"No local rnsd shared instance '{cfg.instance_name}' found; "
                            f"retrying every {SHARED_RETRY_SECONDS} s",
                        )
                        if stop.wait(SHARED_RETRY_SECONDS):
                            return
                self._build_stack(cfg)
            if stop.is_set():
                self._detach()
                return
            self._attach(cfg, stop)
            if stop.is_set():  # stop() raced the attach
                self.stop()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            logger.exception("Reticulum start failed")
            self._set_state(ReticulumState.ERROR, f"Start failed: {exc}")

    def _build_stack(self, cfg: ReticulumConfig) -> None:
        root = data_dir()
        rns_dir = root / "rns"
        rns_dir.mkdir(parents=True, exist_ok=True)
        (rns_dir / "config").write_text(build_rns_config(cfg))
        with _no_signal_handlers():
            self._rns = RNS.Reticulum(
                configdir=str(rns_dir),
                logdest=lambda line: logger.debug("RNS: %s", line),
                require_shared_instance=cfg.mode == "shared",
            )
            self._link_key = cfg.link_key()
            if cfg.mode == "shared":
                self._check_rpc()
            id_path = root / "identity"
            identity = RNS.Identity.from_file(str(id_path)) if id_path.exists() else None
            if identity is None:
                identity = RNS.Identity()
                identity.to_file(str(id_path))
            self._router = LXMF.LXMRouter(identity=identity, storagepath=str(root / "lxmf"))
            self._local = self._router.register_delivery_identity(identity, display_name=cfg.display_name)
        self._router.register_delivery_callback(self._on_lxmf_message)
        self._announce_handler = _AnnounceHandler(self)
        logger.info("Reticulum stack up, LXMF address %s", self._local.hash.hex())

    def _check_rpc(self) -> None:
        """A shared-instance client with its own config dir needs rnsd's rpc_key,
        or RNS fails on every inbound link packet (incoming messages are lost)."""
        try:
            self._rns.get_interface_stats()
        except Exception as exc:  # noqa: BLE001 - multiprocessing AuthenticationError and friends
            self._rpc_error = (
                f"Attached to rnsd, but its RPC rejected us ({exc}). Incoming messages will fail: "
                "set INTERCEPT_RETICULUM_RPC_KEY to rnsd's rpc_key and restart Intercept"
            )
            logger.warning(self._rpc_error)

    def _attach(self, cfg: ReticulumConfig, stop: threading.Event) -> None:
        if self._local not in RNS.Transport.destinations:
            RNS.Transport.register_destination(self._local)
        if self._announce_handler not in RNS.Transport.announce_handlers:
            RNS.Transport.register_announce_handler(self._announce_handler)
        self._local.display_name = cfg.display_name
        with self._lock:
            self._running = True
            self._state = ReticulumState.WAITING
        self._router.announce(self._local.hash)
        self._push({"type": "status", "data": self.get_status()})
        threading.Thread(target=self._monitor_link, args=(stop,), daemon=True).start()

    def _monitor_link(self, stop: threading.Event) -> None:
        """Push status on link up/down, and re-announce when the link comes up
        (an announce sent while it was down never reached anyone). Starts as
        "down" so a link that came up just after the first announce is covered."""
        was_online = False
        while not stop.wait(LINK_CHECK_SECONDS):
            online = self._link_online()
            if online != was_online:
                was_online = online
                if online:
                    with contextlib.suppress(Exception):
                        self._router.announce(self._local.hash)
                self._push({"type": "status", "data": self.get_status()})

    def _detach(self) -> None:
        if self._local is not None:
            RNS.Transport.deregister_destination(self._local)
            RNS.Transport.deregister_announce_handler(self._announce_handler)

    def stop(self) -> None:
        """Stop receiving: detach our destination and announce handler (stack stays up)."""
        self._stop_event.set()
        self._detach()
        with self._lock:
            self._running = False
        self._set_state(ReticulumState.STOPPED)

    # -- Actions --

    def announce(self) -> None:
        if not self._running:
            raise RuntimeError("Reticulum is not running")
        self._router.announce(self._local.hash)

    def send(self, destination: str, content: str, title: str = "") -> dict:
        """Queue an LXMF message. Raises ValueError/LookupError/RuntimeError on failure."""
        if not self._running:
            raise RuntimeError("Reticulum is not running")
        dest_hex = normalize_hash(destination)
        dest_hash = bytes.fromhex(dest_hex)
        identity = RNS.Identity.recall(dest_hash)
        if identity is None:
            RNS.Transport.request_path(dest_hash)
            raise LookupError("Address not known yet; a path request was sent. Retry once it announces.")
        dest = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        lxm = LXMF.LXMessage(dest, self._local, content, title, desired_method=LXMF.LXMessage.DIRECT)
        lxm.try_propagation_on_fail = False
        lxm.register_delivery_callback(lambda m: self._on_outbound_state(m, "delivered"))
        lxm.register_failed_callback(lambda m: self._on_outbound_state(m, "failed"))
        with self._send_lock:
            self._router.handle_outbound(lxm)
            msg = {
                "msg_hash": lxm.hash.hex(),
                "direction": "out",
                "peer_hash": dest_hex,
                "title": title,
                "content": content,
                "timestamp": time.time(),
                "state": "outbound",
            }
            database.store_reticulum_message(msg)
        self._push({"type": "message", "data": msg})
        return msg

    # -- Callbacks (RNS/LXMF threads) --

    def _on_outbound_state(self, lxm, state: str) -> None:
        with self._send_lock:  # the row is stored right after handle_outbound()
            msg_hash = lxm.hash.hex()
            database.update_reticulum_message_state(msg_hash, state)
        self._push({"type": "message_state", "data": {"msg_hash": msg_hash, "state": state}})

    def _on_lxmf_message(self, message) -> None:
        try:
            msg = {
                "msg_hash": message.hash.hex(),
                "direction": "in",
                "peer_hash": message.source_hash.hex(),
                "title": message.title_as_string(),
                "content": message.content_as_string(),
                "timestamp": message.timestamp or time.time(),
                "state": "received",
            }
            if database.store_reticulum_message(msg):
                self._push({"type": "message", "data": msg})
        except Exception:  # noqa: BLE001 - never raise into the LXMF router
            logger.exception("Failed to handle incoming LXMF message")

    def on_announce(self, destination_hash: bytes, app_data: bytes | None) -> None:
        try:
            name = LXMF.display_name_from_app_data(app_data) if app_data else None
            hops = RNS.Transport.hops_to(destination_hash)
            node = {
                "dest_hash": destination_hash.hex(),
                "name": name or None,
                "hops": hops if hops < RNS.Transport.PATHFINDER_M else None,
                "last_heard": time.time(),
            }
            database.upsert_reticulum_node(node["dest_hash"], node["name"], node["hops"], node["last_heard"])
            self._push({"type": "node", "data": node})
        except Exception:  # noqa: BLE001 - never raise into RNS transport
            logger.exception("Failed to handle LXMF announce")

    # -- Data accessors --

    def get_nodes(self) -> list[dict]:
        return database.get_reticulum_nodes()

    def get_conversations(self) -> list[dict]:
        return database.get_reticulum_conversations()

    def get_messages(self, peer_hash: str | None = None) -> list[dict]:
        return database.get_reticulum_messages(peer_hash)


class _AnnounceHandler:
    """RNS announce handler for LXMF delivery destinations."""

    aspect_filter = "lxmf.delivery"
    receive_path_responses = True

    def __init__(self, client: ReticulumClient) -> None:
        self._client = client

    def received_announce(self, destination_hash, announced_identity, app_data):
        self._client.on_announce(destination_hash, app_data)


_client: ReticulumClient | None = None


def get_reticulum_client() -> ReticulumClient:
    """Return the process-wide ReticulumClient singleton."""
    global _client
    if _client is None:
        _client = ReticulumClient()
    return _client
