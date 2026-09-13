#!/usr/bin/env python3
"""Start a native pilot inside a pseudo terminal (Issue #426).

This is the only way the G4 gate starts the native Claude pilot. N1 (via the
runner) and I1, F1, F2, F3 (via pilot-gate-i1.NativePilot) all go through it.

Why a terminal: Claude Code started with a non-terminal stdin (a FIFO, a
file, /dev/null) runs in --print mode, has no prompt, and exits with
"Input must be provided either through stdin or as a prompt argument when
using --print". The live PM runs as an interactive session, so the pilot is
measured the same way (runbook section 11 is not relaxed to --print).

Library use (pilot-gate-i1.NativePilot):
    proc, master = spawn(argv, cwd=..., env=...)
    pump(master, log_path, timeout)

Command-line use (scripts/pilot-gate-runner.sh):
    pilot-pty.py run --log <file> --pid-file <file> --cwd <dir> -- <argv...>
It writes the child's PID to --pid-file (the runner checks the binding,
liveness and the process command against that PID), copies everything the
child writes to the terminal into --log, and exits with the child's status
(128 + signal number when the child was killed by a signal).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pathlib
import pty
import select
import signal
import socket
import subprocess
import struct
import sys
import termios
from typing import Sequence


# #444: the size the verifier observed the native screens at. Without it the
# pty reports 0x0 and the CLI may wrap or cut the texts the runner looks for.
TERMINAL_ROWS = 40
TERMINAL_COLUMNS = 120


def spawn(
    argv: Sequence[str],
    *,
    cwd: os.PathLike[str] | str,
    env: dict[str, str],
) -> tuple[subprocess.Popen[bytes], int]:
    """Start argv with a pseudo terminal as stdin, stdout and stderr.

    The child gets its own session (so a caller can signal its whole process
    group) and the controlling terminal is the pty slave. Returns the process
    and the master file descriptor, which the caller owns and must close.
    """
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(
            slave,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", TERMINAL_ROWS, TERMINAL_COLUMNS, 0, 0),
        )
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    return proc, master


def pump(
    master: int,
    log: pathlib.Path,
    timeout: float = 0.0,
) -> bool:
    """Copy what is available on master into log.

    Returns False once the terminal has closed (EOF or EIO, which Linux
    reports when the last slave holder exits), True otherwise.
    """
    ready, _, _ = select.select([master], [], [], timeout)
    if not ready:
        return True
    try:
        data = os.read(master, 65536)
    except OSError:
        return False
    if not data:
        return False
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        fh.write(data)
        fh.flush()
    return True


def exit_status(returncode: int) -> int:
    if returncode < 0:
        return 128 + (-returncode)
    return returncode


def write_pid_file(path: pathlib.Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{pid}\n", encoding="utf-8")
    os.replace(temporary, path)


def write_master(master: int, payload: bytes) -> int:
    """Write one complete control payload to the retained PTY master."""
    try:
        written = os.write(master, payload)
    except OSError:
        return 1
    return 0 if written == len(payload) else 1


def command_send(args: argparse.Namespace) -> int:
    payload = pathlib.Path(args.input).read_bytes()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    client_path = f"{args.socket}.{os.getpid()}"
    try:
        pathlib.Path(client_path).unlink(missing_ok=True)
        client.bind(client_path)
        client.settimeout(2)
        client.connect(args.socket)
        client.sendall(payload)
        return 0 if client.recv(16) == b"ok" else 1
    except OSError:
        return 1
    finally:
        client.close()
        pathlib.Path(client_path).unlink(missing_ok=True)


def command_run(args: argparse.Namespace) -> int:
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("pilot-pty: run needs a command after --", file=sys.stderr)
        return 64

    log = pathlib.Path(args.log)
    control = None
    if args.control_socket:
        # Bind before the pilot starts: a socket that cannot be bound (for
        # example a path longer than AF_UNIX allows) must not leave a running
        # pilot behind with nobody able to reach its terminal.
        control_path = pathlib.Path(args.control_socket)
        try:
            control_path.unlink(missing_ok=True)
            control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            control.bind(str(control_path))
            control.setblocking(False)
        except OSError as error:
            if control is not None:
                control.close()
            print(f"pilot-pty: cannot bind control socket: {error}", file=sys.stderr)
            return 70
    try:
        proc, master = spawn(argv, cwd=args.cwd, env=dict(os.environ))
    except BaseException:
        if control is not None:
            control.close()
            control_path.unlink(missing_ok=True)
        raise

    def forward(signum: int, frame: object) -> None:
        # A signal to this helper stops the pilot it is holding; the loop
        # below then collects its status.
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)

    try:
        write_pid_file(pathlib.Path(args.pid_file), proc.pid)
        open_terminal = True
        while True:
            if control is not None:
                try:
                    payload, peer = control.recvfrom(65536)
                    control.sendto(b"ok" if write_master(master, payload) == 0 else b"error", peer)
                except BlockingIOError:
                    pass
            if open_terminal:
                open_terminal = pump(master, log, 0.2)
            if proc.poll() is not None:
                # Drain whatever the child left in the terminal.
                while (
                    open_terminal
                    and select.select([master], [], [], 0)[0]
                ):
                    open_terminal = pump(master, log, 0.0)
                break
            if not open_terminal:
                proc.wait()
                break
    finally:
        if control is not None:
            control.close()
            pathlib.Path(args.control_socket).unlink(missing_ok=True)
        try:
            os.close(master)
        except OSError:
            pass

    return exit_status(proc.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start a native pilot inside a pseudo terminal (#426)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--log", required=True)
    run.add_argument("--pid-file", required=True)
    run.add_argument("--cwd", required=True)
    run.add_argument("--control-socket")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    run.set_defaults(handler=command_run)
    send = sub.add_parser("send")
    send.add_argument("--socket", required=True)
    send.add_argument("--input", required=True)
    send.set_defaults(handler=command_send)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
