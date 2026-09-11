"""Unit tests for scripts/lib/pilot-gate-cleanup.py."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_CLEANUP_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-cleanup.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_cleanup",
    CLEANUP_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate cleanup helper: {CLEANUP_HELPER}"
    )

CL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CL)


class SignalGuardedCase(unittest.TestCase):
    """Base class: no test may deliver a real signal.

    terminate_owned_processes() signals every process whose command line
    contains the run root. A mutated or broken matcher must never reach
    real processes on the machine running the tests, so os.kill and
    os.killpg raise unless a test installs its own fake.
    """

    _real_kill = staticmethod(os.kill)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        for name in ("kill", "killpg"):
            patcher = mock.patch.object(
                CL.os,
                name,
                side_effect=AssertionError(
                    f"real os.{name} reached from a test"
                ),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["fake"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class FakeRecorder:
    """CommandRecorder stand-in returning scripted results per label."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler

    def run(self, label, argv, *, cwd=None, env=None, timeout=30.0):
        self.calls.append(
            {
                "label": label,
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env) if env is not None else None,
                "timeout": timeout,
            }
        )
        if self.handler is None:
            return completed()
        return self.handler(label, argv, cwd=cwd, env=env)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def ps_output(rows):
    return "".join(
        f"{pid:>6} {ppid:>6} {command}\n"
        for pid, ppid, command in rows
    )


class PilotGateCleanupRoundA(SignalGuardedCase):
    """Basic helpers, CommandRecorder and owned-process termination."""

    # --- constants / small helpers ---------------------------------------

    def test_constants(self):
        self.assertEqual(
            (CL.EX_PASS, CL.EX_FAIL, CL.EX_UNKNOWN), (0, 1, 2)
        )
        self.assertEqual(
            CL.CHECKS, ("N1", "I1", "F1", "F2", "F3", "F4", "F5")
        )
        self.assertEqual(set(CL.SOURCE_BY_CHECK), set(CL.CHECKS))
        self.assertEqual(CL.SOURCE_BY_CHECK["F5"], "provider-readback")
        self.assertIn("pid", CL.VOLATILE_KEYS)
        self.assertIn("observedAt", CL.VOLATILE_KEYS)
        self.assertNotIn("verdict", CL.VOLATILE_KEYS)

    def test_utc_now_is_millisecond_utc_with_z(self):
        value = CL.utc_now()
        self.assertRegex(
            value, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$"
        )
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        delta = abs(
            (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()
        )
        self.assertLess(delta, 5)

    def test_load_module(self):
        path = self.root / "m.py"
        path.write_text("X = 'ok'\n")
        self.assertEqual(CL.load_module(path, "cl_m").X, "ok")
        with mock.patch.object(
            CL.importlib.util, "spec_from_file_location", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot load module"):
                CL.load_module(path, "cl_m")

    def test_json_helpers(self):
        target = self.root / "a" / "b.json"
        CL.atomic_json(target, {"z": "é", "a": [1]})
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            '{\n  "a": [\n    1\n  ],\n  "z": "é"\n}\n',
        )
        self.assertEqual(os.listdir(target.parent), ["b.json"])
        self.assertEqual(CL.read_json(target), {"z": "é", "a": [1]})

        log = self.root / "l" / "x.jsonl"
        CL.append_jsonl(log, {"b": 1, "a": 2})
        CL.append_jsonl(log, "s")
        self.assertEqual(log.read_text(), '{"a":2,"b":1}\n"s"\n')

    def test_verdict_code(self):
        self.assertEqual(CL.verdict_code("pass"), 0)
        self.assertEqual(CL.verdict_code("fail"), 1)
        self.assertEqual(CL.verdict_code("unknown"), 2)
        self.assertEqual(CL.verdict_code("PASS"), 2)
        self.assertEqual(CL.verdict_code(""), 2)

    def test_aggregate_verdict(self):
        self.assertEqual(CL.aggregate_verdict([]), "pass")
        self.assertEqual(CL.aggregate_verdict(["pass", "pass"]), "pass")
        self.assertEqual(CL.aggregate_verdict(["pass", "unknown"]), "unknown")
        # Anything that is not literally "pass" is not a pass.
        self.assertEqual(CL.aggregate_verdict(["pass", "skipped"]), "unknown")
        self.assertEqual(CL.aggregate_verdict(["pass", None]), "unknown")
        self.assertEqual(
            CL.aggregate_verdict(["unknown", "fail", "pass"]), "fail"
        )
        self.assertEqual(
            CL.aggregate_verdict(v for v in ["pass", "fail"]), "fail"
        )

    def test_safe_absolute_and_is_within(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(CL.safe_absolute("~/x"), self.root / "x")
        cwd = Path.cwd()
        self.assertEqual(CL.safe_absolute("rel"), cwd / "rel")

        parent = self.root / "run"
        self.assertTrue(CL.is_within(parent, parent))
        self.assertTrue(CL.is_within(parent / "a" / "b", parent))
        self.assertFalse(CL.is_within(self.root / "run2", parent))
        self.assertFalse(CL.is_within(self.root, parent))

    # --- disposable layout -----------------------------------------------

    def layout(self, **overrides):
        run_root = self.root / "a" / "run"
        values = {
            "run_root": run_root,
            "gate_repo": run_root / "repo",
            "gate_home": run_root / "home",
            "xdg_config": run_root / "xdg" / "config",
            "xdg_cache": run_root / "xdg" / "cache",
            "xdg_data": run_root / "xdg" / "data",
            "xdg_state": run_root / "xdg" / "state",
            "claude_config": run_root / "claude",
            "artifact_dir": self.root / "artifacts",
        }
        values.update(overrides)
        return values

    def test_require_disposable_layout_accepts_valid_layout(self):
        CL.require_disposable_layout(**self.layout())

    def test_require_disposable_layout_rejects_root_and_shallow(self):
        with self.assertRaisesRegex(RuntimeError, "refusing root run"):
            CL.require_disposable_layout(**self.layout(run_root=Path("/")))
        with self.assertRaisesRegex(RuntimeError, "unexpectedly shallow"):
            CL.require_disposable_layout(
                **self.layout(run_root=Path("/tmp"))
            )
        # Exactly three parts is the minimum accepted depth.
        shallow_ok = Path("/tmp/x")
        CL.require_disposable_layout(
            **{
                key: (shallow_ok / key if key != "artifact_dir"
                      else self.root / "art")
                for key in self.layout()
                if key != "run_root"
            },
            run_root=shallow_ok,
        )

    def test_require_disposable_layout_rejects_each_escaping_path(self):
        outside = self.root / "outside"
        for key in (
            "gate_repo", "gate_home", "xdg_config", "xdg_cache",
            "xdg_data", "xdg_state", "claude_config",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    RuntimeError, "disposable path escapes run root"
                ):
                    CL.require_disposable_layout(
                        **self.layout(**{key: outside})
                    )

    def test_require_disposable_layout_rejects_artifact_overlap(self):
        run_root = self.layout()["run_root"]
        with self.assertRaisesRegex(
            RuntimeError, "artifact directory is inside disposable run root"
        ):
            CL.require_disposable_layout(
                **self.layout(artifact_dir=run_root / "art")
            )
        with self.assertRaisesRegex(
            RuntimeError, "artifact directory is inside disposable run root"
        ):
            CL.require_disposable_layout(**self.layout(artifact_dir=run_root))
        with self.assertRaisesRegex(
            RuntimeError, "run root is inside artifact directory"
        ):
            CL.require_disposable_layout(
                **self.layout(artifact_dir=self.root / "a")
            )

    # --- CommandRecorder -------------------------------------------------

    def test_command_recorder_records_success_and_failure(self):
        recorder = CL.CommandRecorder(self.root / "cmds")
        workdir = self.root / "work"
        workdir.mkdir()
        result = recorder.run(
            "first",
            ["/bin/sh", "-c", 'pwd -P; echo "V=$V"; echo err >&2; exit 3'],
            cwd=workdir,
            env={"V": "7", "PATH": os.environ["PATH"]},
        )
        self.assertEqual(result.returncode, 3)
        directory = self.root / "cmds" / "001-first"
        self.assertEqual(
            (directory / "stdout.raw").read_text(), f"{workdir}\nV=7\n"
        )
        self.assertEqual((directory / "stderr.raw").read_text(), "err\n")
        self.assertEqual((directory / "exit-status").read_text(), "3\n")
        command = json.loads((directory / "command.json").read_text())
        self.assertEqual(command["argv"][0], "/bin/sh")
        self.assertEqual(command["cwd"], str(workdir))
        self.assertEqual(command["schemaVersion"], 1)
        timing = json.loads((directory / "timing.json").read_text())
        self.assertGreaterEqual(timing["elapsedMonotonic"], 0)
        self.assertEqual(timing["startedAt"], command["startedAt"])

        second = recorder.run("second", ["/bin/sh", "-c", "printf 'ü\\377'"])
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "ü\ufffd")
        second_dir = self.root / "cmds" / "002-second"
        self.assertIsNone(
            json.loads((second_dir / "command.json").read_text())["cwd"]
        )
        self.assertEqual(recorder.counter, 2)

    @contextlib.contextmanager
    def allow_signals_to_own_children(self):
        """Let subprocess.run reap children it spawned in this test only."""
        real_kill = self._real_kill
        spawned = set()
        real_popen = CL.subprocess.Popen

        class RecordingPopen(real_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                spawned.add(self.pid)

        def guarded_kill(pid, sig):
            if pid not in spawned:
                raise AssertionError(f"signal to foreign pid {pid}")
            real_kill(pid, sig)

        with mock.patch.object(CL.subprocess, "Popen", RecordingPopen), \
                mock.patch.object(CL.os, "kill", side_effect=guarded_kill):
            yield spawned

    def test_command_recorder_timeout_returns_none_and_records_it(self):
        recorder = CL.CommandRecorder(self.root / "cmds")
        with self.allow_signals_to_own_children() as spawned:
            result = recorder.run(
                "slow", ["/bin/sh", "-c", "sleep 5"], timeout=0.3
            )
        self.assertEqual(len(spawned), 1)
        self.assertIsNone(result)
        directory = self.root / "cmds" / "001-slow"
        self.assertEqual((directory / "exit-status").read_text(), "timeout\n")
        self.assertEqual((directory / "stdout.raw").read_text(), "")
        self.assertEqual((directory / "stderr.raw").read_text(), "")
        self.assertTrue((directory / "timing.json").is_file())

    def test_command_recorder_timeout_keeps_partial_text_output(self):
        recorder = CL.CommandRecorder(self.root / "cmds")
        exc = subprocess.TimeoutExpired(
            ["x"], 1, output="partial-out", stderr="partial-err"
        )
        with mock.patch.object(CL.subprocess, "run", side_effect=exc):
            self.assertIsNone(recorder.run("t", ["x"]))
        directory = self.root / "cmds" / "001-t"
        self.assertEqual((directory / "stdout.raw").read_text(), "partial-out")
        self.assertEqual((directory / "stderr.raw").read_text(), "partial-err")

    # --- process table ---------------------------------------------------

    def test_process_table_parses_rows_and_skips_noise(self):
        recorder = FakeRecorder(
            lambda *a, **k: completed(
                "  12    1 /usr/bin/foo --x\n"
                "garbage line\n"
                "34 12 bar  with  spaces\n"
                "\n"
            )
        )
        self.assertEqual(
            CL.process_table(recorder),
            [
                {"pid": 12, "ppid": 1, "command": "/usr/bin/foo --x"},
                {"pid": 34, "ppid": 12, "command": "bar  with  spaces"},
            ],
        )
        self.assertEqual(recorder.calls[0]["label"], "ps")
        self.assertEqual(
            recorder.calls[0]["argv"], ["ps", "-axo", "pid=,ppid=,command="]
        )
        self.assertEqual(recorder.calls[0]["timeout"], 15)

    def test_process_table_none_on_timeout_or_nonzero(self):
        self.assertIsNone(CL.process_table(FakeRecorder(lambda *a, **k: None)))
        self.assertIsNone(
            CL.process_table(FakeRecorder(lambda *a, **k: completed("1 0 x\n", 1)))
        )

    def test_process_table_real_ps_includes_this_process(self):
        rows = CL.process_table(CL.CommandRecorder(self.root / "cmds"))
        self.assertIsNotNone(rows)
        self.assertIn(os.getpid(), {row["pid"] for row in rows})

    def test_ancestor_pids_follow_parent_chain(self):
        me = os.getpid()
        rows = [
            {"pid": me, "ppid": 500},
            {"pid": 500, "ppid": 400},
            {"pid": 400, "ppid": 1},
            {"pid": 600, "ppid": me},
        ]
        self.assertEqual(CL.ancestor_pids(rows), {me, 500, 400})
        self.assertEqual(CL.ancestor_pids([]), {me})

    @contextlib.contextmanager
    def deadline(self, seconds):
        """Turn a would-be infinite loop into a test failure, not a hang."""
        import signal as signal_module

        def expired(signum, frame):
            raise AssertionError(f"did not finish within {seconds}s")

        previous = signal_module.signal(signal_module.SIGALRM, expired)
        signal_module.setitimer(signal_module.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal_module.setitimer(signal_module.ITIMER_REAL, 0)
            signal_module.signal(signal_module.SIGALRM, previous)

    def test_ancestor_pids_stop_on_cycle(self):
        me = os.getpid()
        rows = [
            {"pid": me, "ppid": 500},
            {"pid": 500, "ppid": 501},
            {"pid": 501, "ppid": 500},
        ]
        with self.deadline(2.0):
            result = CL.ancestor_pids(rows)
        self.assertEqual(result, {me, 500, 501})

    def test_owned_processes_match_run_root_and_exclude_ancestors(self):
        me = os.getpid()
        run_root = self.root / "run"
        token = str(run_root)
        rows = [
            {"pid": me, "ppid": 500, "command": f"python {token}/x"},
            {"pid": 500, "ppid": 1, "command": f"bash {token}"},
            {"pid": 700, "ppid": 1, "command": f"watch.sh {token}/repo"},
            {"pid": 701, "ppid": 1, "command": "unrelated"},
            {"pid": 702, "ppid": 1, "command": f"x {self.root}/other"},
        ]
        self.assertEqual(
            [row["pid"] for row in CL.owned_processes(rows, run_root)],
            [700],
        )

    # --- terminate_owned_processes ---------------------------------------

    def run_terminate(self, dies_on, *, ps_fails=None, kill_effects=None):
        """Model-driven fake: dies_on maps pid -> "TERM" | "KILL" | None.

        ps_fails(call_index, state) -> bool decides which ps calls fail.
        Processes leave the table when a signal they obey is delivered.
        """
        run_root = self.root / "run"
        artifact = self.root / "evidence"
        token = str(run_root)
        me = os.getpid()
        alive = set(dies_on)
        state = {"calls": 0, "sigkill_sent": False}

        def handler(label, argv, **kwargs):
            index = state["calls"]
            state["calls"] += 1
            if ps_fails is not None and ps_fails(index, state):
                return None
            rows = [(me, 1, f"python {token}")] + [
                (pid, 1, f"watch.sh {token}/repo") for pid in sorted(alive)
            ]
            return completed(ps_output(rows))

        kills = []

        def kill(pid, sig):
            kills.append((pid, sig))
            if sig == CL.signal.SIGKILL:
                state["sigkill_sent"] = True
            if kill_effects and (pid, sig) in kill_effects:
                raise kill_effects[(pid, sig)]
            mode = dies_on.get(pid)
            if (mode == "TERM" and sig == CL.signal.SIGTERM) or (
                mode in ("TERM", "KILL") and sig == CL.signal.SIGKILL
            ):
                alive.discard(pid)

        recorder = FakeRecorder(handler)
        clock = FakeClock()
        with mock.patch.object(CL.os, "kill", side_effect=kill), \
                mock.patch.object(CL.time, "monotonic", clock.monotonic), \
                mock.patch.object(CL.time, "sleep", clock.sleep):
            status, targets = CL.terminate_owned_processes(
                recorder, run_root, artifact
            )
        return status, targets, kills, clock, state, artifact

    def test_terminate_unknown_when_first_ps_fails(self):
        status, targets, kills, _, _, artifact = self.run_terminate(
            {700: "TERM"}, ps_fails=lambda n, s: n == 0
        )
        self.assertEqual((status, targets, kills), ("unknown", [], []))
        self.assertFalse(artifact.exists())

    def test_terminate_sigterm_only_and_pass_when_targets_exit(self):
        status, targets, kills, clock, _, artifact = self.run_terminate(
            {700: "TERM", 701: "TERM"}
        )
        self.assertEqual(status, "pass")
        self.assertEqual([t["pid"] for t in targets], [700, 701])
        self.assertEqual(
            kills, [(700, CL.signal.SIGTERM), (701, CL.signal.SIGTERM)]
        )
        self.assertEqual(clock.sleeps, [])
        before = json.loads((artifact / "processes-before.json").read_text())
        self.assertEqual([t["pid"] for t in before["targets"]], [700, 701])
        self.assertEqual(before["runRoot"], str(self.root / "run"))
        after = json.loads((artifact / "processes-after.json").read_text())
        self.assertEqual(after["remaining"], [])
        # never signals itself
        self.assertNotIn(os.getpid(), [pid for pid, _ in kills])

    def test_terminate_nothing_owned_is_pass_without_signals(self):
        status, targets, kills, _, _, _ = self.run_terminate({})
        self.assertEqual((status, targets, kills), ("pass", [], []))

    def test_terminate_escalates_to_sigkill_after_4s_grace(self):
        status, _, kills, clock, _, _ = self.run_terminate({700: "KILL"})
        self.assertEqual(
            kills, [(700, CL.signal.SIGTERM), (700, CL.signal.SIGKILL)]
        )
        self.assertEqual(status, "pass")
        # Grace loop polls every 0.2s until the 4.0s deadline, then one
        # 0.2s pause after SIGKILL. Float accumulation may add one poll.
        self.assertTrue(all(value == 0.2 for value in clock.sleeps))
        grace = sum(clock.sleeps) - 0.2
        self.assertGreaterEqual(grace, 4.0 - 1e-9)
        self.assertLess(grace, 4.0 + 0.2 + 1e-9)

    def test_terminate_fail_when_process_survives_sigkill(self):
        status, _, kills, _, _, artifact = self.run_terminate({700: None})
        self.assertEqual(status, "fail")
        self.assertEqual(
            kills, [(700, CL.signal.SIGTERM), (700, CL.signal.SIGKILL)]
        )
        after = json.loads((artifact / "processes-after.json").read_text())
        self.assertEqual([r["pid"] for r in after["remaining"]], [700])

    def test_terminate_tolerates_vanished_and_forbidden_pids(self):
        status, _, kills, _, _, _ = self.run_terminate(
            {700: "TERM", 701: "TERM"},
            kill_effects={
                (700, CL.signal.SIGTERM): ProcessLookupError(),
                (701, CL.signal.SIGTERM): PermissionError(),
            },
        )
        # Both exceptions are raised before the fake removes the pid,
        # so both survive TERM and are killed with SIGKILL.
        self.assertEqual(status, "pass")
        self.assertEqual(
            kills,
            [(700, CL.signal.SIGTERM), (701, CL.signal.SIGTERM),
             (700, CL.signal.SIGKILL), (701, CL.signal.SIGKILL)],
        )

    def test_terminate_forbidden_survivor_is_fail(self):
        status, _, _, _, _, _ = self.run_terminate(
            {700: "KILL"},
            kill_effects={
                (700, CL.signal.SIGTERM): PermissionError(),
                (700, CL.signal.SIGKILL): PermissionError(),
            },
        )
        self.assertEqual(status, "fail")

    def test_terminate_unknown_when_recheck_ps_fails(self):
        status, targets, kills, _, _, artifact = self.run_terminate(
            {700: None}, ps_fails=lambda n, s: n >= 1
        )
        self.assertEqual(status, "unknown")
        self.assertEqual([t["pid"] for t in targets], [700])
        self.assertEqual(kills, [(700, CL.signal.SIGTERM)])
        self.assertFalse((artifact / "processes-after.json").exists())

    def test_terminate_unknown_when_final_ps_fails(self):
        status, _, kills, _, _, artifact = self.run_terminate(
            {700: "KILL"}, ps_fails=lambda n, s: s["sigkill_sent"]
        )
        self.assertEqual(status, "unknown")
        self.assertIn((700, CL.signal.SIGKILL), kills)
        self.assertFalse((artifact / "processes-after.json").exists())

if __name__ == "__main__":
    unittest.main()
