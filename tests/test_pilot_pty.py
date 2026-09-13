"""Tests for scripts/lib/pilot-pty.py (Issue #426)."""

from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PTY_HELPER = Path(
    os.environ.get(
        "PILOT_PTY_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-pty.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location("pilot_pty", PTY_HELPER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load pilot pty helper: {PTY_HELPER}")

PTY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PTY)

# Stands in for claude: without a terminal on stdin it takes the --print path
# and exits 1, the way the real CLI did in the #426 reproduction.
TTY_CHECK = r"""
import os, sys, time
if not os.isatty(0):
    sys.stderr.write("Error: Input must be provided either through stdin or "
                     "as a prompt argument when using --print\n")
    sys.exit(1)
print("tty:%d%d%d sid:%d" % (os.isatty(0), os.isatty(1), os.isatty(2),
                              os.getsid(0) == os.getpid()), flush=True)
mode = sys.argv[1] if len(sys.argv) > 1 else "exit"
if mode == "stay":
    time.sleep(60)
sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
"""


def open_fds() -> set[int]:
    # Probe with fstat; listing /dev/fd would itself open a descriptor.
    found = set()
    for fd in range(256):
        try:
            os.fstat(fd)
        except OSError:
            continue
        found.add(fd)
    return found


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-pty-test."))
        self.script = self.tmp / "tty_check.py"
        self.script.write_text(TTY_CHECK, encoding="utf-8")
        self.log = self.tmp / "pty.raw"
        self.pid_file = self.tmp / "launcher-pid"

    def tearDown(self) -> None:
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def child(self, *args: str) -> list[str]:
        return [sys.executable, str(self.script), *args]

    def drain(self, master: int, proc: subprocess.Popen, limit: float = 10.0) -> None:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if not PTY.pump(master, self.log, 0.1) and proc.poll() is not None:
                return
            if proc.poll() is not None and not PTY.select.select([master], [], [], 0)[0]:
                return
        self.fail("child did not finish in time")


class SpawnTests(Base):
    def test_child_has_a_terminal_on_all_three_streams_and_its_own_session(self) -> None:
        proc, master = PTY.spawn(self.child("exit", "0"), cwd=self.tmp, env=dict(os.environ))
        try:
            self.drain(master, proc)
            proc.wait(timeout=10)
        finally:
            os.close(master)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("tty:111 sid:1", self.log.read_text(errors="replace"))

    def test_without_a_terminal_the_stand_in_takes_the_print_path(self) -> None:
        # Control for the stand-in itself: the FIFO/pipe start of the old
        # runner gives exactly the #426 failure.
        result = subprocess.run(
            self.child("stay"), stdin=subprocess.PIPE, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("when using --print", result.stderr)

    def test_the_slave_is_not_left_open_in_the_parent(self) -> None:
        before = open_fds()
        proc, master = PTY.spawn(self.child("exit", "0"), cwd=self.tmp, env=dict(os.environ))
        try:
            self.assertEqual(open_fds() - before, {master})
            self.drain(master, proc)
            proc.wait(timeout=10)
        finally:
            os.close(master)

    def test_a_failed_start_closes_both_ends(self) -> None:
        before = open_fds()
        with self.assertRaises(FileNotFoundError):
            PTY.spawn([str(self.tmp / "no-such-program")], cwd=self.tmp, env=dict(os.environ))
        self.assertEqual(open_fds(), before)


class PumpTests(Base):
    def test_pump_returns_true_when_nothing_is_ready(self) -> None:
        proc, master = PTY.spawn(self.child("stay"), cwd=self.tmp, env=dict(os.environ))
        try:
            # Read the greeting, then there is nothing more to read.
            deadline = time.monotonic() + 10
            while "tty:" not in (self.log.read_text(errors="replace") if self.log.exists() else ""):
                self.assertLess(time.monotonic(), deadline)
                PTY.pump(master, self.log, 0.1)
            self.assertTrue(PTY.pump(master, self.log, 0.1))
        finally:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            os.close(master)

    def test_pump_reports_the_closed_terminal(self) -> None:
        proc, master = PTY.spawn(self.child("exit", "0"), cwd=self.tmp, env=dict(os.environ))
        try:
            proc.wait(timeout=10)
            deadline = time.monotonic() + 10
            while PTY.pump(master, self.log, 0.1):
                self.assertLess(time.monotonic(), deadline, "terminal close not reported")
        finally:
            os.close(master)


class SmallTests(unittest.TestCase):
    def test_exit_status(self) -> None:
        self.assertEqual(PTY.exit_status(0), 0)
        self.assertEqual(PTY.exit_status(7), 7)
        self.assertEqual(PTY.exit_status(-signal.SIGTERM), 128 + signal.SIGTERM)
        self.assertEqual(PTY.exit_status(-signal.SIGKILL), 128 + signal.SIGKILL)

    def test_write_pid_file_leaves_only_the_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "launcher-pid"
            PTY.write_pid_file(path, 4242)
            self.assertEqual(path.read_text(), "4242\n")
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["launcher-pid"])


class CommandLineTests(Base):
    def run_helper(self, *argv: str, timeout: float = 20) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PTY_HELPER), "run", "--log", str(self.log),
             "--pid-file", str(self.pid_file), "--cwd", str(self.tmp), "--", *argv],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        )

    def test_run_passes_the_child_status_through_and_logs_its_terminal(self) -> None:
        # The helper's own stdin is /dev/null, as in the runner; the child
        # still gets a terminal.
        result = self.run_helper(*self.child("exit", "7"))
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("tty:111", self.log.read_text(errors="replace"))
        self.assertNotIn("--print", self.log.read_text(errors="replace"))

    def test_run_writes_the_child_pid_not_its_own(self) -> None:
        helper = subprocess.Popen(
            [sys.executable, str(PTY_HELPER), "run", "--log", str(self.log),
             "--pid-file", str(self.pid_file), "--cwd", str(self.tmp), "--", *self.child("stay")],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_pid = None
        try:
            deadline = time.monotonic() + 10
            while not self.pid_file.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
            child_pid = int(self.pid_file.read_text())
            self.assertNotEqual(child_pid, helper.pid)
            self.assertEqual(os.getsid(child_pid), child_pid)
        finally:
            helper.send_signal(signal.SIGTERM)
            try:
                status = helper.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Fail, but leave nothing running behind the test.
                helper.kill()
                helper.wait()
                if child_pid is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child_pid, signal.SIGKILL)
                raise
        # SIGTERM to the helper stops the child; the helper reports it.
        self.assertEqual(status, 128 + signal.SIGTERM)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_a_child_killed_by_a_signal_is_reported_as_128_plus_signal(self) -> None:
        result = self.run_helper("/bin/sh", "-c", "kill -KILL $$")
        self.assertEqual(result.returncode, 128 + signal.SIGKILL)

    def test_an_unbindable_control_socket_starts_nothing(self) -> None:
        # #434: longer than AF_UNIX allows; the pilot must not be started.
        too_long = self.tmp / ("s" * 200)
        marker = self.tmp / "started"
        result = subprocess.run(
            [sys.executable, str(PTY_HELPER), "run", "--log", str(self.log),
             "--pid-file", str(self.pid_file), "--control-socket", str(too_long),
             "--cwd", str(self.tmp), "--", "/bin/sh", "-c", f": > {marker}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn("cannot bind control socket", result.stderr)
        time.sleep(0.5)
        self.assertFalse(marker.exists())
        self.assertFalse(self.pid_file.exists())

    def test_run_without_a_command_is_a_usage_error(self) -> None:
        result = self.run_helper()
        self.assertEqual(result.returncode, 64)
        self.assertIn("needs a command", result.stderr)
        self.assertFalse(self.pid_file.exists())


if __name__ == "__main__":
    unittest.main()
