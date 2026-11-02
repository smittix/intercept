"""Real loopback: Intercept's ReticulumClient and a second LXMF node exchange
messages through a throwaway rnsd (TCPServerInterface on 127.0.0.1).

Everything runs in subprocesses with temp config dirs and a unique shared
instance name, because RNS is a once-per-process singleton and must not be
started inside the pytest process (or touch ~/.reticulum).
"""

import json
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

RNS = pytest.importorskip("RNS")
pytest.importorskip("LXMF")

ROOT = Path(__file__).resolve().parent.parent

PEER = textwrap.dedent("""
    import sys, time, RNS, LXMF
    cfgdir, port = sys.argv[1], int(sys.argv[2])
    open(cfgdir + "/config", "w").write(
        "[reticulum]\\n  share_instance = No\\n[logging]\\n  loglevel = 2\\n[interfaces]\\n"
        "  [[t]]\\n    type = TCPClientInterface\\n    enabled = yes\\n"
        f"    target_host = 127.0.0.1\\n    target_port = {port}\\n")
    RNS.Reticulum(configdir=cfgdir)
    ident = RNS.Identity()
    router = LXMF.LXMRouter(identity=ident, storagepath=cfgdir + "/lxmf")
    local = router.register_delivery_identity(ident, display_name="EchoPeer")
    print(local.hash.hex(), flush=True)

    def on_msg(m):
        # The sender's announce may still be in flight: ask for it and wait
        src = RNS.Identity.recall(m.source_hash)
        for _ in range(40):
            if src:
                break
            RNS.Transport.request_path(m.source_hash)
            time.sleep(0.5)
            src = RNS.Identity.recall(m.source_hash)
        dest = RNS.Destination(src, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        router.handle_outbound(LXMF.LXMessage(dest, local, "echo: " + m.content_as_string(), "",
                                              desired_method=LXMF.LXMessage.DIRECT))
    router.register_delivery_callback(on_msg)
    while True:
        router.announce(local.hash)
        time.sleep(2)
""")

NODE = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    tmp, port, peer, mode, instance = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
    rpc_key = sys.argv[7]
    os.environ["INTERCEPT_RETICULUM_DATA_DIR"] = tmp + "/node"
    import utils.database as db
    db.DB_DIR = type(db.DB_DIR)(tmp + "/db")
    db.DB_PATH = db.DB_DIR / "intercept.db"
    db.init_db()
    from utils.reticulum import ReticulumConfig, get_reticulum_client
    client = get_reticulum_client()
    client.start(ReticulumConfig(mode=mode, host="127.0.0.1", port=port, instance_name=instance, rpc_key=rpc_key,
                                 display_name="InterceptTest"))
    result = {}

    def wait(pred, secs):
        end = time.time() + secs
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.25)
        return False

    result["online"] = wait(lambda: client.get_status()["state"] == "online", 20)
    result["discovered"] = wait(lambda: any(n["dest_hash"] == peer for n in client.get_nodes()), 20)
    if result["discovered"]:
        client.send(peer, "ping")
        result["delivered"] = wait(lambda: db.get_reticulum_messages(peer)[0]["state"] == "delivered", 30)
        replied = lambda: any(m["direction"] == "in" for m in db.get_reticulum_messages(peer))  # noqa: E731
        # Rarely (~1 in 10 runs) the echo peer gets the delivery proof out but
        # never sees the message in its LXMF callback; one resend covers that.
        result["reply"] = wait(replied, 10) or (client.send(peer, "ping") and wait(replied, 20))
        result["messages"] = [(m["direction"], m["content"]) for m in db.get_reticulum_messages(peer)]
        result["node"] = [n for n in client.get_nodes() if n["dest_hash"] == peer]
    print("RESULT " + json.dumps(result), flush=True)
    os._exit(0)
""")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("mode", ["tcp", "shared"])
def test_lxmf_loopback(tmp_path, mode):
    """tcp: TCP client to rnsd; shared: attach to rnsd as a local shared-instance client."""
    port = _free_port()
    rnsd_dir = tmp_path / "rnsd"
    peer_dir = tmp_path / "peer"
    rnsd_dir.mkdir()
    peer_dir.mkdir()
    instance = f"intercept-test-{uuid.uuid4().hex[:8]}"
    (rnsd_dir / "config").write_text(
        "[reticulum]\n  share_instance = Yes\n"
        f"  instance_name = {instance}\n  enable_transport = Yes\n"
        "[logging]\n  loglevel = 2\n[interfaces]\n"
        "  [[srv]]\n    type = TCPServerInterface\n    enabled = yes\n"
        f"    listen_ip = 127.0.0.1\n    listen_port = {port}\n"
    )
    procs = []
    try:
        procs.append(
            subprocess.Popen(
                [sys.executable, "-m", "RNS.Utilities.rnsd", "--config", str(rnsd_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        for _ in range(50):  # wait for the TCP server
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.skip("throwaway rnsd did not come up")

        # A shared-instance client with its own config dir needs rnsd's rpc_key
        # (derived from rnsd's transport identity), or inbound links fail.
        ident = RNS.Identity.from_file(str(rnsd_dir / "storage" / "transport_identity"))
        rpc_key = RNS.Identity.full_hash(ident.get_private_key()).hex() if mode == "shared" else ""

        peer = subprocess.Popen(
            [sys.executable, "-c", PEER, str(peer_dir), str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        procs.append(peer)
        peer_hash = peer.stdout.readline().strip()
        assert len(peer_hash) == 32, "peer did not start"

        node = subprocess.run(
            [sys.executable, "-c", NODE, str(ROOT), str(tmp_path), str(port), peer_hash, mode, instance, rpc_key],
            capture_output=True,
            text=True,
            timeout=120,
        )
        line = next((ln for ln in node.stdout.splitlines() if ln.startswith("RESULT ")), None)
        assert line, f"node script failed: {node.stderr[-2000:]}"
        result = json.loads(line[len("RESULT ") :])
    finally:
        for p in procs:
            p.kill()
            p.wait()

    assert result["online"], result
    assert result["discovered"], result
    assert result["node"][0]["name"] == "EchoPeer"
    assert result["delivered"], result
    assert result["reply"], result
    assert ("out", "ping") in [tuple(m) for m in result["messages"]]
    assert ("in", "echo: ping") in [tuple(m) for m in result["messages"]]
