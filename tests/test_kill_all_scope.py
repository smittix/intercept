"""/killall must stop every decoder INTERCEPT starts, and nothing else on the host."""

import os
from unittest.mock import MagicMock, patch


def test_kill_all_targets_exact_names_owned_by_this_user(client):
    with patch("app.subprocess.run", return_value=MagicMock(returncode=1)) as run:
        resp = client.post("/killall")
    assert resp.status_code == 200
    calls = [c.args[0] for c in run.call_args_list if c.args and c.args[0][0] == "pkill"]
    uid = str(os.geteuid())
    for argv in calls:
        # Never every process of every user whose arguments merely mention a name
        assert argv[1:3] == ["-U", uid], argv
        assert ("-x" in argv) != ("-f" in argv), argv
        if "-f" in argv:
            assert argv[-1].endswith(".py"), argv  # scripts by filename only
    targets = {argv[-1] for argv in calls}
    # HackRF ADS-B (readsb), the csdr FM pipeline and the rtl_tcp client
    assert {"readsb", "rx_sdr", "csdr", "rtltcp_iq.py", "rtl_fm", "dump1090"} <= targets
