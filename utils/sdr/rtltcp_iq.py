"""Minimal rtl_tcp client: tune a remote RTL-SDR and stream raw CU8 I/Q to stdout.

Stock osmocom rtl_fm/rtl_sdr have no rtl_tcp client, so remote devices feed
the csdr demod pipeline through this script. Standard library only; it runs
as its own process, e.g.

    python rtltcp_iq.py --host 10.0.0.5 --port 1234 --freq 153350000 --rate 264600 | csdr ...

Exits 0 when the reader closes the pipe, non-zero with a message on stderr
when the server is unreachable, busy with another client, or stops sending.
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys

# rtl_tcp commands: 1-byte id + big-endian uint32 argument
CMD_FREQ = 0x01
CMD_SAMPLE_RATE = 0x02
CMD_GAIN_MODE = 0x03  # 0 = auto, 1 = manual
CMD_GAIN = 0x04  # tenths of a dB
CMD_PPM = 0x05
CMD_DIRECT_SAMPLING = 0x09
CMD_BIAS_TEE = 0x0E

HEADER_LEN = 12  # b"RTL0" + tuner type + gain count
TUNER_R828D = 6  # RTL-SDR Blog V4: HF through its upconverter, not direct sampling

CONNECT_TIMEOUT_S = 5.0
# rtl_tcp accepts one client; a second one connects but never gets a header
HEADER_TIMEOUT_S = 5.0
STREAM_TIMEOUT_S = 10.0


def command(cmd: int, value: int) -> bytes:
    return struct.pack(">BI", cmd, value & 0xFFFFFFFF)


def tune_commands(args: argparse.Namespace, tuner: int) -> list[bytes]:
    direct_sampling = args.direct_sampling
    if direct_sampling and tuner == TUNER_R828D:
        print("rtltcp_iq: R828D tuner (RTL-SDR Blog V4): direct sampling disabled", file=sys.stderr)
        direct_sampling = 0
    cmds = [
        command(CMD_DIRECT_SAMPLING, direct_sampling),
        command(CMD_SAMPLE_RATE, args.rate),
        command(CMD_FREQ, args.freq),
    ]
    if args.gain is None:
        cmds.append(command(CMD_GAIN_MODE, 0))
    else:
        cmds += [command(CMD_GAIN_MODE, 1), command(CMD_GAIN, round(args.gain * 10))]
    if args.ppm:
        cmds.append(command(CMD_PPM, args.ppm))
    if args.bias_t:
        cmds.append(command(CMD_BIAS_TEE, 1))
    return cmds


def read_header(sock: socket.socket) -> bytes:
    header = b""
    while len(header) < HEADER_LEN:
        chunk = sock.recv(HEADER_LEN - len(header))
        if not chunk:
            break
        header += chunk
    return header


def fail(message: str) -> int:
    print(f"rtltcp_iq: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--freq", type=int, required=True, help="Hz")
    parser.add_argument("--rate", type=int, required=True, help="samples/s")
    parser.add_argument("--gain", type=float, help="dB; omit for auto gain")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--direct-sampling", type=int, default=0, choices=(0, 1, 2))
    parser.add_argument("--bias-t", action="store_true")
    args = parser.parse_args(argv)

    server = f"rtl_tcp server {args.host}:{args.port}"
    try:
        sock = socket.create_connection((args.host, args.port), timeout=CONNECT_TIMEOUT_S)
    except OSError as e:
        return fail(f"cannot connect to {server}: {e}")

    with sock:
        try:
            sock.settimeout(HEADER_TIMEOUT_S)
            header = read_header(sock)
        except OSError:
            header = b""
        if len(header) < HEADER_LEN or not header.startswith(b"RTL0"):
            return fail(f"no RTL0 header from {server} (busy with another client, or not rtl_tcp)")
        tuner = struct.unpack(">I", header[4:8])[0]

        try:
            sock.sendall(b"".join(tune_commands(args, tuner)))
            sock.settimeout(STREAM_TIMEOUT_S)
            while True:
                data = sock.recv(65536)
                if not data:
                    return fail(f"{server} closed the connection")
                # Unbuffered, so no data is left to flush into a closed pipe
                view = memoryview(data)
                while view:
                    view = view[os.write(1, view) :]
        except BrokenPipeError:
            # The demodulator went away: a normal stop
            return 0
        except TimeoutError:
            return fail(f"no samples from {server} for {STREAM_TIMEOUT_S:.0f} s")
        except OSError as e:
            return fail(f"{server}: {e}")


if __name__ == "__main__":
    sys.exit(main())
