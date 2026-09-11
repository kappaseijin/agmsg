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
        self.assertEqual(
            CL.LIVE_RAW_FILES, ("input.raw", "stdout.raw", "stderr.raw")
        )
        self.assertNotIn("phase", CL.LIVE_CONTROL_FIELDS)
        self.assertNotIn("observedAt", CL.LIVE_CONTROL_FIELDS)

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


def write_config(gate_repo: Path, directory: str, value) -> Path:
    path = gate_repo / "teams" / directory / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")
    return path


def make_claims_db(path: Path, claims) -> Path:
    """claims: (legacy_id, team, owner, event_uuid) rows."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE messages (id TEXT PRIMARY KEY, team TEXT);
            CREATE TABLE message_claims (message_id TEXT, owner TEXT);
            CREATE TABLE events (
              id TEXT, legacy_id TEXT, type TEXT, team TEXT
            );
            """
        )
        for legacy_id, team, owner, event_uuid in claims:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?)", (legacy_id, team)
            )
            connection.execute(
                "INSERT INTO message_claims VALUES (?, ?)",
                (legacy_id, owner),
            )
            if event_uuid is not ...:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, 'message_sent', ?)",
                    (event_uuid, legacy_id, team),
                )
        connection.commit()
    finally:
        connection.close()
    return path


def delete_claim(db: Path, legacy_id: str) -> None:
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "DELETE FROM message_claims WHERE message_id = ?", (legacy_id,)
        )
        connection.commit()
    finally:
        connection.close()


class PilotGateCleanupRoundB(SignalGuardedCase):
    """Registrations, bindings, claims, snapshots and safe removal."""

    def setUp(self):
        super().setUp()
        self.run_root = self.root / "run"
        self.gate_repo = self.run_root / "repo"
        (self.gate_repo / "teams").mkdir(parents=True)
        (self.gate_repo / "scripts").mkdir()
        self.artifact = self.root / "evidence"

    # --- registration_rows -----------------------------------------------

    def test_registration_rows_collects_run_scoped_registrations(self):
        inside = str(self.run_root / "proj")
        write_config(self.gate_repo, "g", {
            "name": "gate",
            "agents": {
                "legacy": {"type": "claude-code", "project": inside},
                "multi": {
                    "registrations": [
                        {"type": "codex", "project": inside + "2"},
                        {"type": "claude-code",
                         "project": str(self.root / "live")},
                        {"type": "", "project": inside},
                        {"type": "codex"},
                        {"type": 5, "project": inside},
                    ],
                },
            },
        })
        rows, errors = CL.registration_rows(self.gate_repo, self.run_root)
        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(rows, key=lambda r: (r["agent"], r["type"])),
            [
                {"team": "gate", "agent": "legacy", "project": inside,
                 "type": "claude-code"},
                {"team": "gate", "agent": "multi",
                 "project": inside + "2", "type": "codex"},
            ],
        )

    def test_registration_rows_reports_every_parse_error_kind(self):
        write_config(self.gate_repo, "broken", "{not json")
        write_config(self.gate_repo, "list", "[1]")
        write_config(self.gate_repo, "noname", {"agents": {}})
        write_config(self.gate_repo, "noagents", {"name": "x", "agents": []})
        write_config(self.gate_repo, "badagent", {
            "name": "y", "agents": {"a": "not-a-dict"},
        })
        write_config(self.gate_repo, "badreg", {
            "name": "z", "agents": {"a": {"registrations": ["nope"]}},
        })
        rows, errors = CL.registration_rows(self.gate_repo, self.run_root)
        self.assertEqual(rows, [])
        teams = self.gate_repo / "teams"
        self.assertEqual(
            sorted(errors),
            sorted([
                f"{teams / 'broken' / 'config.json'}:JSONDecodeError",
                f"{teams / 'list' / 'config.json'}:root_not_object",
                f"{teams / 'noname' / 'config.json'}:schema_unidentified",
                f"{teams / 'noagents' / 'config.json'}:schema_unidentified",
                f"{teams / 'badagent' / 'config.json'}:"
                "agent_schema_unidentified",
                f"{teams / 'badreg' / 'config.json'}:a:registration_invalid",
            ]),
        )

    def test_registration_rows_skips_symlinked_config_and_missing_teams(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps({
            "name": "evil",
            "agents": {"a": {"type": "codex",
                             "project": str(self.run_root / "p")}},
        }))
        (self.gate_repo / "teams" / "link").mkdir()
        (self.gate_repo / "teams" / "link" / "config.json").symlink_to(outside)
        self.assertEqual(
            CL.registration_rows(self.gate_repo, self.run_root), ([], [])
        )
        self.assertEqual(
            CL.registration_rows(self.root / "no-repo", self.run_root),
            ([], []),
        )

    # --- disposable_teams ------------------------------------------------

    def test_disposable_teams_always_includes_gate_team_sorted_unique(self):
        self.assertEqual(CL.disposable_teams([], "gate"), ["gate"])
        self.assertEqual(
            CL.disposable_teams(
                [{"team": "b"}, {"team": "gate"}, {"team": "a"},
                 {"team": "b"}],
                "gate",
            ),
            ["a", "b", "gate"],
        )

    # --- release_message_claims ------------------------------------------

    def claims_fixture(self, claims_by_team, *, release_ok=None):
        provider = self.gate_repo / "scripts" / "p2-provider.sh"
        provider.write_text("#!/bin/sh\n")
        dbs = {}
        for team, claims in claims_by_team.items():
            dbs[team] = make_claims_db(self.root / f"{team}.db", claims)
        i1 = mock.Mock()
        i1.storage_db.side_effect = (
            lambda gate_repo, team, env: dbs.get(team, self.root / "none.db")
        )

        def handler(label, argv, *, cwd=None, env=None):
            _, verb, team, message_uuid, owner = argv
            ok = release_ok(message_uuid) if release_ok else True
            if ok is None:
                return None
            if ok:
                legacy = message_uuid.replace("uuid-", "")
                delete_claim(dbs[team], legacy)
                return completed()
            return completed(returncode=1)

        return i1, FakeRecorder(handler), provider, dbs

    def release(self, i1, recorder, teams):
        return CL.release_message_claims(
            i1=i1,
            gate_repo=self.gate_repo,
            teams=teams,
            env={"E": "1"},
            recorder=recorder,
            artifact=self.artifact,
        )

    def test_release_message_claims_releases_each_claim_and_verifies(self):
        i1, recorder, provider, _ = self.claims_fixture({
            "gate": [("m1", "gate", "owner-a", "uuid-m1"),
                     ("m2", "gate", "owner-b", "uuid-m2")],
            "other": [("m3", "other", "owner-c", "uuid-m3")],
        })
        status, claims = self.release(i1, recorder, ["gate", "other"])
        self.assertEqual(status, "pass")
        self.assertEqual(
            [c["legacyId"] for c in claims], ["m1", "m2", "m3"]
        )
        self.assertEqual(
            [call["argv"] for call in recorder.calls],
            [
                [str(provider), "message-release", "gate", "uuid-m1",
                 "owner-a"],
                [str(provider), "message-release", "gate", "uuid-m2",
                 "owner-b"],
                [str(provider), "message-release", "other", "uuid-m3",
                 "owner-c"],
            ],
        )
        self.assertEqual(
            [call["label"] for call in recorder.calls],
            ["message-release-000", "message-release-001",
             "message-release-002"],
        )
        for call in recorder.calls:
            self.assertEqual(call["cwd"], self.gate_repo)
            self.assertEqual(call["env"], {"E": "1"})
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(len(before["claims"]), 3)
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual(after["remaining"], [])
        self.assertEqual(after["releaseFailures"], [])

    def test_release_message_claims_only_touches_listed_teams(self):
        # One storage file shared by the gate team and a live team: only
        # the listed team's claims may be read, released or verified.
        shared = make_claims_db(
            self.root / "shared.db",
            [("m1", "gate", "o", "uuid-m1"), ("m9", "live", "o", "uuid-m9")],
        )
        (self.gate_repo / "scripts" / "p2-provider.sh").write_text("#!/bin/sh\n")
        i1 = mock.Mock()
        i1.storage_db.return_value = shared

        def handler(label, argv, *, cwd=None, env=None):
            delete_claim(shared, argv[3].replace("uuid-", ""))
            return completed()

        recorder = FakeRecorder(handler)
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "pass")
        self.assertEqual([c["team"] for c in claims], ["gate"])
        self.assertEqual([call["argv"][2] for call in recorder.calls], ["gate"])
        connection = sqlite3.connect(shared)
        try:
            remaining = connection.execute(
                "SELECT message_id FROM message_claims"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(remaining, [("m9",)])

    def test_release_message_claims_fail_when_release_fails_or_claim_stays(self):
        i1, recorder, _, _ = self.claims_fixture(
            {"gate": [("m1", "gate", "o", "uuid-m1"),
                      ("m2", "gate", "o", "uuid-m2")]},
            release_ok=lambda uuid: uuid != "uuid-m2",
        )
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "fail")
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual([r["legacyId"] for r in after["remaining"]], ["m2"])
        self.assertEqual(
            [f["legacyId"] for f in after["releaseFailures"]], ["m2"]
        )

    def test_release_message_claims_release_failure_alone_is_fail(self):
        # The provider reports failure although the claim did go away:
        # the failed command must still fail the step on its own, not
        # only through the "remaining" re-check.
        i1, recorder, _, dbs = self.claims_fixture(
            {"gate": [("m1", "gate", "o", "uuid-m1")]}
        )

        def released_but_failed(label, argv, *, cwd=None, env=None):
            delete_claim(dbs["gate"], "m1")
            return completed(returncode=1)

        recorder.handler = released_but_failed
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "fail")
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual(after["remaining"], [])
        self.assertEqual(
            [f["legacyId"] for f in after["releaseFailures"]], ["m1"]
        )

    def test_release_message_claims_timeout_counts_as_release_failure(self):
        i1, recorder, _, _ = self.claims_fixture(
            {"gate": [("m1", "gate", "o", "uuid-m1")]},
            release_ok=lambda uuid: None,
        )
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "fail")

    def test_release_message_claims_fail_when_provider_reports_ok_but_claim_remains(self):
        i1, recorder, _, _ = self.claims_fixture(
            {"gate": [("m1", "gate", "o", "uuid-m1")]}
        )
        recorder.handler = lambda *a, **k: completed()
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "fail")

    def test_release_message_claims_unknown_on_read_errors(self):
        i1, recorder, _, _ = self.claims_fixture(
            {"gate": [("m1", "gate", "o", "uuid-m1")]}
        )
        base = i1.storage_db.side_effect

        def storage(gate_repo, team, env):
            if team == "broken":
                raise RuntimeError("no storage")
            return base(gate_repo, team, env)

        i1.storage_db.side_effect = storage
        status, _ = self.release(i1, recorder, ["gate", "broken"])
        self.assertEqual(status, "unknown")
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], ["broken:storage:RuntimeError"])
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual(after["verifyErrors"], ["broken:storage:RuntimeError"])

    def test_release_message_claims_unknown_on_unreadable_db(self):
        i1, recorder, _, _ = self.claims_fixture({})
        bad = self.root / "bad.db"
        bad.write_text("not a database")
        i1.storage_db.side_effect = lambda *a: bad
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual((status, claims), ("unknown", []))
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertTrue(before["readErrors"][0].startswith("gate:claims:"))

    def test_release_message_claims_unidentified_row_is_unknown_not_released(self):
        i1, recorder, _, _ = self.claims_fixture({
            "gate": [("m1", "gate", "", "uuid-m1")],
        })
        status, claims = self.release(i1, recorder, ["gate"])
        # The claim is neither releasable (no owner) nor gone.
        self.assertEqual(status, "fail")
        self.assertEqual(claims, [])
        self.assertEqual(recorder.calls, [])
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], ["gate:claim_row_unidentified:m1"])

    # --- schema-first claims read (#427) ---------------------------------

    def drop_tables(self, db, *tables):
        connection = sqlite3.connect(db)
        try:
            for table in tables:
                connection.execute(f"DROP TABLE {table}")
            connection.commit()
        finally:
            connection.close()

    def test_release_message_claims_no_events_table_is_pass_with_reason(self):
        # A team that never sent anything has no events table yet.
        i1, recorder, _, dbs = self.claims_fixture({"gate": []})
        self.drop_tables(dbs["gate"], "events")
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual((status, claims), ("pass", []))
        self.assertEqual(recorder.calls, [])
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], [])
        self.assertEqual(before["notes"], ["gate:claims:no_events_table"])
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual(after["remaining"], [])
        self.assertEqual(after["verifyErrors"], [])

    def test_release_message_claims_missing_messages_table_is_unknown(self):
        # The store was never initialised (#423): not a decidable state.
        i1, recorder, _, dbs = self.claims_fixture({"gate": []})
        self.drop_tables(dbs["gate"], "events", "messages")
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual((status, claims), ("unknown", []))
        self.assertEqual(recorder.calls, [])
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], ["gate:claims:messages_table_missing"])
        self.assertEqual(before["notes"], [])

    def test_release_message_claims_missing_messages_alone_is_unknown(self):
        # events is present, so only the messages check can refuse.
        i1, recorder, _, dbs = self.claims_fixture({"gate": []})
        self.drop_tables(dbs["gate"], "messages")
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "unknown")
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], ["gate:claims:messages_table_missing"])

    def test_release_message_claims_missing_claims_table_is_unknown(self):
        i1, recorder, _, dbs = self.claims_fixture({"gate": []})
        self.drop_tables(dbs["gate"], "message_claims")
        status, _ = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "unknown")
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["readErrors"], ["gate:claims:message_claims_table_missing"])
        self.assertEqual(before["notes"], [])

    def test_release_message_claims_events_table_keeps_the_join_path(self):
        i1, recorder, _, _ = self.claims_fixture(
            {"gate": [("m1", "gate", "owner-a", "uuid-m1")]}
        )
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual(status, "pass")
        self.assertEqual(
            claims,
            [{"team": "gate", "messageId": "uuid-m1", "owner": "owner-a",
              "legacyId": "m1"}],
        )
        self.assertEqual([call["argv"][3] for call in recorder.calls], ["uuid-m1"])
        before = json.loads((self.artifact / "claims-before.json").read_text())
        self.assertEqual(before["notes"], [])

    def test_release_message_claims_no_events_table_with_a_claim_left_is_fail(self):
        # Without events nothing can be released by uuid; a claim that still
        # exists is caught by the independent re-check instead of passing.
        i1, recorder, _, dbs = self.claims_fixture(
            {"gate": [("m1", "gate", "owner-a", ...)]}
        )
        self.drop_tables(dbs["gate"], "events")
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual((status, claims), ("fail", []))
        self.assertEqual(recorder.calls, [])
        after = json.loads((self.artifact / "claims-after.json").read_text())
        self.assertEqual([r["legacyId"] for r in after["remaining"]], ["m1"])

    def test_release_message_claims_missing_provider_is_unknown(self):
        i1 = mock.Mock()
        recorder = FakeRecorder()
        status, claims = self.release(i1, recorder, ["gate"])
        self.assertEqual((status, claims), ("unknown", []))
        i1.storage_db.assert_not_called()
        self.assertFalse(self.artifact.exists())

    def test_release_message_claims_absent_db_is_pass_and_dedups(self):
        i1, recorder, _, _ = self.claims_fixture({
            "gate": [("m1", "gate", "o", "uuid-m1")],
        })
        status, claims = self.release(i1, recorder, ["gate", "gate", "nodb"])
        self.assertEqual(status, "pass")
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(recorder.calls), 1)

    # --- reset_registrations ---------------------------------------------

    def reset_fixture(self, *, reset_ok=True):
        inside = str(self.run_root / "proj")
        config = write_config(self.gate_repo, "g1", {
            "name": "gate-1",
            "agents": {
                "a": {"type": "claude-code", "project": inside},
                "b": {"type": "codex", "project": inside},
            },
        })
        write_config(self.gate_repo, "g2", {
            "name": "gate-2",
            "agents": {"a": {"type": "claude-code", "project": inside}},
        })
        write_config(self.gate_repo, "live", {
            "name": "live",
            "agents": {"x": {"type": "codex",
                             "project": str(self.root / "live")}},
        })

        def handler(label, argv, *, cwd=None, env=None):
            _, _, flag, project, kind, agent = argv
            ok = reset_ok(agent) if callable(reset_ok) else reset_ok
            if ok is None:
                return None
            if not ok:
                return completed(returncode=1)
            for path in (self.gate_repo / "teams").glob("*/config.json"):
                try:
                    value = json.loads(path.read_text())
                except ValueError:
                    continue
                entry = value["agents"].get(agent)
                if (entry and entry.get("project") == project
                        and entry.get("type") == kind):
                    del value["agents"][agent]
                    path.write_text(json.dumps(value))
            return completed()

        return FakeRecorder(handler), inside

    def reset(self, recorder):
        return CL.reset_registrations(
            gate_repo=self.gate_repo,
            run_root=self.run_root,
            recorder=recorder,
            env={"E": "1"},
            artifact=self.artifact,
        )

    def test_reset_registrations_resets_unique_tuples_without_session(self):
        recorder, inside = self.reset_fixture()
        status, rows = self.reset(recorder)
        self.assertEqual(status, "pass")
        self.assertEqual(len(rows), 3)
        reset_script = str(self.gate_repo / "scripts" / "reset.sh")
        self.assertEqual(
            sorted(call["argv"] for call in recorder.calls),
            sorted([
                ["bash", reset_script, "--no-resolve", inside,
                 "claude-code", "a"],
                ["bash", reset_script, "--no-resolve", inside, "codex", "b"],
            ]),
        )
        for call in recorder.calls:
            self.assertEqual(len(call["argv"]), 6)  # no session id
            self.assertEqual(call["cwd"], self.gate_repo)
            self.assertEqual(
                call["env"], {"E": "1", "AGMSG_RESOLVE_PROJECT": "0"}
            )
        after = json.loads(
            (self.artifact / "registrations-after.json").read_text()
        )
        self.assertEqual(after["remaining"], [])
        live = json.loads(
            (self.gate_repo / "teams" / "live" / "config.json").read_text()
        )
        self.assertIn("x", live["agents"])

    def test_reset_registrations_fail_on_reset_failure_or_remaining(self):
        for ok in (False, None):
            with self.subTest(ok=ok):
                self._tmp.cleanup()
                self.setUp()
                recorder, _ = self.reset_fixture(
                    reset_ok=lambda agent, ok=ok: ok if agent == "b" else True
                )
                status, _ = self.reset(recorder)
                self.assertEqual(status, "fail")
                after = json.loads(
                    (self.artifact / "registrations-after.json").read_text()
                )
                self.assertEqual(
                    [r["agent"] for r in after["resetFailures"]], ["b"]
                )
                self.assertEqual(
                    [r["agent"] for r in after["remaining"]], ["b"]
                )

    def test_reset_registrations_fail_when_script_succeeds_but_row_remains(self):
        recorder, _ = self.reset_fixture()
        recorder.handler = lambda *a, **k: completed()
        status, _ = self.reset(recorder)
        self.assertEqual(status, "fail")

    def test_reset_registrations_unknown_on_parse_errors(self):
        recorder, _ = self.reset_fixture()
        write_config(self.gate_repo, "broken", "{")
        status, _ = self.reset(recorder)
        self.assertEqual(status, "unknown")
        before = json.loads(
            (self.artifact / "registrations-before.json").read_text()
        )
        self.assertEqual(len(before["parseErrors"]), 1)

    def test_reset_registrations_nothing_to_do_is_pass(self):
        recorder = FakeRecorder()
        status, rows = self.reset(recorder)
        self.assertEqual((status, rows), ("pass", []))
        self.assertEqual(recorder.calls, [])

    # --- snapshots -------------------------------------------------------

    def test_copy_file_fsync_copies_bytes_and_creates_parents(self):
        source = self.root / "src.bin"
        source.write_bytes(bytes(range(256)) * 5000)
        target = self.root / "deep" / "dst.bin"
        CL.copy_file_fsync(source, target)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_snapshot_tree_copies_regular_files_with_suffix_filter(self):
        source = self.root / "src"
        (source / "a" / "b").mkdir(parents=True)
        (source / "top.jsonl").write_text("1")
        (source / "a" / "b" / "deep.log").write_text("2")
        (source / "a" / "skip.bin").write_text("3")
        (source / "a" / "link.jsonl").symlink_to(source / "top.jsonl")
        destination = self.root / "dst"

        copied = CL.snapshot_tree(source, destination,
                                  suffixes={".jsonl", ".log"})
        self.assertEqual(
            sorted(copied),
            sorted([str(destination / "top.jsonl"),
                    str(destination / "a" / "b" / "deep.log")]),
        )
        self.assertFalse((destination / "a" / "skip.bin").exists())
        self.assertFalse((destination / "a" / "link.jsonl").exists())

        everything = CL.snapshot_tree(source, self.root / "all")
        self.assertEqual(len(everything), 3)
        self.assertEqual(CL.snapshot_tree(self.root / "missing", destination),
                         [])

    def test_snapshot_evidence_copies_three_sources_and_records(self):
        claude = self.run_root / "claude"
        (self.gate_repo / "run" / "pilot" / "g").mkdir(parents=True)
        (self.gate_repo / "run" / "pilot" / "g" / "b.json").write_text("{}")
        (self.gate_repo / ".agmsg-gate" / "f5").mkdir(parents=True)
        (self.gate_repo / ".agmsg-gate" / "f5" / "x.raw").write_text("x")
        (claude / "projects").mkdir(parents=True)
        (claude / "projects" / "t.jsonl").write_text("t")
        (claude / "settings.json").write_text("{}")
        artifact_dir = self.root / "artifacts"

        record = CL.snapshot_evidence(
            gate_repo=self.gate_repo,
            claude_config=claude,
            artifact_dir=artifact_dir,
        )
        pre = artifact_dir / "pre-cleanup"
        self.assertEqual(record["copiedCount"], 3)
        self.assertEqual(
            sorted(record["files"]),
            sorted([
                str(pre / "run-pilot" / "g" / "b.json"),
                str(pre / "agmsg-gate" / "f5" / "x.raw"),
                str(pre / "claude-transcripts" / "projects" / "t.jsonl"),
            ]),
        )
        self.assertFalse((pre / "claude-transcripts" / "settings.json").exists())
        self.assertEqual(
            json.loads((pre / "snapshot.json").read_text())["copiedCount"], 3
        )

    # --- remove_tree_safely ----------------------------------------------
    # Every path here lives under this test's temporary directory, so a
    # broken containment check cannot delete anything outside it.

    def test_remove_tree_safely_refuses_outside_and_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("k")
        self.run_root.mkdir(exist_ok=True)
        self.assertEqual(CL.remove_tree_safely(outside, self.run_root), "fail")
        self.assertTrue((outside / "keep").exists())
        self.assertEqual(
            CL.remove_tree_safely(self.run_root, self.run_root), "fail"
        )
        self.assertTrue(self.run_root.exists())

    def test_remove_tree_safely_removes_dir_file_and_missing_is_pass(self):
        target = self.run_root / "xdg" / "config"
        (target / "sub").mkdir(parents=True)
        (target / "sub" / "f").write_text("f")
        self.assertEqual(CL.remove_tree_safely(target, self.run_root), "pass")
        self.assertFalse(target.exists())
        self.assertTrue((self.run_root / "xdg").exists())

        single = self.run_root / "file"
        single.write_text("x")
        self.assertEqual(CL.remove_tree_safely(single, self.run_root), "pass")
        self.assertFalse(single.exists())

        self.assertEqual(
            CL.remove_tree_safely(self.run_root / "absent", self.run_root),
            "pass",
        )

    # Symlink handling (stop, keep, report) is covered by
    # tests/test_pilot_gate_cleanup_containment.py (Issue #400).

    def test_remove_tree_safely_reports_os_errors_as_fail(self):
        target = self.run_root / "home"
        target.mkdir(parents=True)
        with mock.patch.object(CL.shutil, "rmtree", side_effect=OSError):
            self.assertEqual(CL.remove_tree_safely(target, self.run_root),
                             "fail")
        self.assertTrue(target.exists())

        with mock.patch.object(CL.shutil, "rmtree"):  # silently no-op
            self.assertEqual(CL.remove_tree_safely(target, self.run_root),
                             "fail")



class PilotGateCleanupRoundC(SignalGuardedCase):
    """cleanup_run: layout guard, idempotent recheck, step sequencing."""

    TEAM = "agmsg-g4gate-c"

    def setUp(self):
        super().setUp()
        self.run_root = self.root / "run"
        self.gate_repo = self.run_root / "repo"
        (self.gate_repo / "scripts").mkdir(parents=True)
        (self.gate_repo / "teams").mkdir()
        self.paths = {
            "gate_home": self.run_root / "home",
            "xdg_config": self.run_root / "xdg" / "config",
            "xdg_cache": self.run_root / "xdg" / "cache",
            "xdg_data": self.run_root / "xdg" / "data",
            "xdg_state": self.run_root / "xdg" / "state",
            "claude_config": self.run_root / "claude",
        }
        for path in self.paths.values():
            path.mkdir(parents=True)
        self.artifact_dir = self.root / "artifacts"
        self.events = []

    def args(self, **overrides):
        values = {
            "run_id": "run-c",
            "run_root": str(self.run_root),
            "gate_repo": str(self.gate_repo),
            "artifact_dir": str(self.artifact_dir),
            "gate_team": self.TEAM,
            **{key: str(value) for key, value in self.paths.items()},
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_cleanup(self, *, terminate=("pass", []), table=(),
                    claims=("pass", []), resets=("pass", []), **overrides):
        i1 = mock.Mock()
        i1.sanitize_env.return_value = {"SANITIZED": "1"}

        def record(name, value):
            def side_effect(*args, **kwargs):
                self.events.append(name)
                self.calls[name] = kwargs or args
                return value
            return side_effect

        self.calls = {}
        table_value = None if table is None else list(table)
        with mock.patch.object(CL, "load_module", return_value=i1) as load, \
                mock.patch.object(
                    CL, "terminate_owned_processes",
                    side_effect=record("terminate", terminate),
                ), \
                mock.patch.object(
                    CL, "process_table",
                    side_effect=record("process_table", table_value),
                ), \
                mock.patch.object(
                    CL, "release_message_claims",
                    side_effect=record("claims", claims),
                ), \
                mock.patch.object(
                    CL, "reset_registrations",
                    side_effect=record("resets", resets),
                ):
            rc = CL.cleanup_run(self.args(**overrides))
        self.i1 = i1
        self.load = load
        path = self.artifact_dir / "cleanup.json"
        cleanup = json.loads(path.read_text()) if path.exists() else None
        return rc, cleanup

    def verdicts(self, cleanup):
        return {name: step["verdict"] for name, step in cleanup["steps"].items()}

    # --- layout guard ----------------------------------------------------

    def test_layout_violation_raises_before_touching_anything(self):
        with self.assertRaisesRegex(RuntimeError, "artifact directory is inside"):
            self.run_cleanup(artifact_dir=str(self.run_root / "art"))
        for path in self.paths.values():
            self.assertTrue(path.is_dir())
        self.assertEqual(self.events, [])

    def test_escaping_disposable_path_raises(self):
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(RuntimeError, "escapes run root"):
            self.run_cleanup(xdg_state=str(outside))
        self.assertTrue(outside.is_dir())

    # --- idempotent recheck ----------------------------------------------

    def remove_run_root(self):
        import shutil as shutil_module
        shutil_module.rmtree(self.run_root)

    def test_absent_run_root_with_previous_pass_is_pass(self):
        self.remove_run_root()
        self.artifact_dir.mkdir()
        (self.artifact_dir / "cleanup.json").write_text(
            json.dumps({"status": "pass"})
        )
        rc, cleanup = self.run_cleanup()
        self.assertEqual(rc, 0)
        self.assertEqual(cleanup["status"], "pass")
        self.assertIs(cleanup["idempotentRecheck"], True)
        self.assertIs(cleanup["runRootAbsent"], True)
        self.assertIs(cleanup["incomplete"], False)
        self.assertEqual(cleanup["remainingPaths"], [])
        self.assertEqual(cleanup["runId"], "run-c")
        self.assertEqual(self.events, [])

    def test_absent_run_root_without_previous_pass_is_unknown(self):
        for previous in (None, "{broken", json.dumps([1]),
                         json.dumps({"status": "unknown"}),
                         json.dumps({"status": "fail"})):
            with self.subTest(previous=previous):
                self._tmp.cleanup()
                self.setUp()
                self.remove_run_root()
                self.artifact_dir.mkdir()
                if previous is not None:
                    (self.artifact_dir / "cleanup.json").write_text(previous)
                rc, cleanup = self.run_cleanup()
                self.assertEqual(rc, 2)
                self.assertEqual(cleanup["status"], "unknown")

    # --- full run --------------------------------------------------------

    def test_full_run_sequence_and_result(self):
        (self.paths["claude_config"] / "t.jsonl").write_text("transcript")
        stale = self.artifact_dir / "cleanup-commands" / "999-old"
        stale.mkdir(parents=True)

        rc, cleanup = self.run_cleanup()

        self.assertEqual(rc, 0)
        self.assertEqual(
            self.events,
            ["terminate", "claims", "resets", "process_table"],
        )
        self.assertEqual(cleanup["status"], "pass")
        self.assertEqual(cleanup["runId"], "run-c")
        self.assertEqual(cleanup["disposableTeams"], [self.TEAM])
        self.assertIs(cleanup["incomplete"], False)
        self.assertEqual(cleanup["remainingPaths"], [])
        # cleanup.json is written with sorted keys, so only the set of
        # steps is observable here; execution order is checked above.
        self.assertEqual(
            sorted(cleanup["steps"]),
            sorted(["evidenceSnapshot", "processes", "claims", "registrations",
             "claudeConfig", "gateHome", "xdgConfig", "xdgCache", "xdgData",
             "xdgState", "repository", "transientGhStore",
             "processVerification", "runRoot"]),
        )
        self.assertEqual(set(self.verdicts(cleanup).values()), {"pass"})
        self.assertFalse(os.path.lexists(self.run_root))
        # Evidence was captured before the claude config was removed.
        self.assertEqual(
            (self.artifact_dir / "pre-cleanup" / "claude-transcripts"
             / "t.jsonl").read_text(),
            "transcript",
        )
        self.assertFalse(stale.exists())
        self.assertEqual(
            self.load.call_args.args,
            (CLEANUP_HELPER.parent / "pilot-gate-i1.py",
             "pilot_gate_i1_cleanup"),
        )

    def test_full_run_wires_env_teams_and_recorder(self):
        write_config(self.gate_repo, "gen2", {
            "name": "pilot-gen-2",
            "agents": {"a": {"type": "codex",
                             "project": str(self.run_root / "p")}},
        })
        self.run_cleanup()
        claims_kwargs = self.calls["claims"]
        self.assertEqual(claims_kwargs["teams"], [self.TEAM, "pilot-gen-2"])
        self.assertEqual(claims_kwargs["env"], {"SANITIZED": "1"})
        self.assertEqual(claims_kwargs["gate_repo"], self.gate_repo)
        self.assertIs(claims_kwargs["i1"], self.i1)
        self.assertEqual(
            claims_kwargs["artifact"], self.artifact_dir / "cleanup-evidence"
        )
        self.assertEqual(self.calls["resets"]["env"], {"SANITIZED": "1"})
        self.assertEqual(self.calls["resets"]["run_root"], self.run_root)
        recorder, run_root, evidence = self.calls["terminate"]
        self.assertIsInstance(recorder, CL.CommandRecorder)
        self.assertEqual(recorder.root, self.artifact_dir / "cleanup-commands")
        self.assertEqual(run_root, self.run_root)
        self.assertEqual(evidence, self.artifact_dir / "cleanup-evidence")

    def test_step_verdicts_aggregate_to_exit_code(self):
        cases = (
            ({"terminate": ("fail", [{"pid": 5}])}, 1, "processes", "fail"),
            ({"claims": ("unknown", [])}, 2, "claims", "unknown"),
            ({"resets": ("fail", [{}])}, 1, "registrations", "fail"),
            ({"table": None}, 2, "processVerification", "unknown"),
        )
        for overrides, rc_expected, step, verdict in cases:
            with self.subTest(step=step):
                self._tmp.cleanup()
                self.setUp()
                rc, cleanup = self.run_cleanup(**overrides)
                self.assertEqual(rc, rc_expected)
                self.assertEqual(cleanup["steps"][step]["verdict"], verdict)
                self.assertEqual(
                    cleanup["status"], "fail" if rc_expected == 1 else "unknown"
                )

    def test_fail_is_not_hidden_by_unknown_steps(self):
        rc, cleanup = self.run_cleanup(
            terminate=("fail", []), claims=("unknown", [])
        )
        self.assertEqual(rc, 1)
        self.assertEqual(cleanup["status"], "fail")

    def test_surviving_owned_process_fails_verification(self):
        token = str(self.run_root)
        table = [{"pid": 4242, "ppid": 1, "command": f"watch.sh {token}"}]
        rc, cleanup = self.run_cleanup(table=table)
        self.assertEqual(rc, 1)
        step = cleanup["steps"]["processVerification"]
        self.assertEqual(step["verdict"], "fail")
        self.assertEqual([r["pid"] for r in step["remaining"]], [4242])

    def test_leftover_gh_store_is_detected(self):
        leftover = self.run_root / "tmp" / "gh-gate-store"
        leftover.mkdir(parents=True)
        rc, cleanup = self.run_cleanup()
        self.assertEqual(rc, 1)
        step = cleanup["steps"]["transientGhStore"]
        self.assertEqual(step["verdict"], "fail")
        self.assertEqual(step["remaining"], [str(leftover)])

    def test_gh_store_matcher_needs_both_words(self):
        (self.run_root / "tmp").mkdir()
        (self.run_root / "tmp" / "gh-cache").mkdir()
        (self.run_root / "tmp" / "gate-notes").mkdir()
        rc, cleanup = self.run_cleanup()
        self.assertEqual(cleanup["steps"]["transientGhStore"]["verdict"],
                         "pass")
        self.assertEqual(rc, 0)

    def test_missing_gate_repo_marks_claims_and_registrations_unknown(self):
        import shutil as shutil_module
        shutil_module.rmtree(self.gate_repo)
        rc, cleanup = self.run_cleanup()
        self.assertEqual(rc, 2)
        self.assertNotIn("claims", self.events)
        self.assertNotIn("resets", self.events)
        self.assertEqual(cleanup["steps"]["claims"]["verdict"], "unknown")
        self.assertEqual(cleanup["steps"]["registrations"]["verdict"],
                         "unknown")
        self.assertEqual(cleanup["steps"]["repository"]["verdict"], "pass")

    def test_target_counts_are_reported(self):
        rc, cleanup = self.run_cleanup(
            terminate=("pass", [{"pid": 1}, {"pid": 2}]),
            claims=("pass", [{"messageId": "m"}]),
            resets=("pass", [{}, {}, {}]),
        )
        self.assertEqual(cleanup["steps"]["processes"]["targetCount"], 2)
        self.assertEqual(cleanup["steps"]["claims"]["targetCount"], 1)
        self.assertEqual(cleanup["steps"]["registrations"]["targetCount"], 3)



class PilotGateCleanupRoundD(SignalGuardedCase):
    """Live-PM comparison and result/assertion helpers."""

    # --- choose_exit_value -----------------------------------------------

    def test_choose_exit_value_requires_one_consistent_integer(self):
        d = self.root / "d"
        (d / "sub").mkdir(parents=True)
        (d / "exit-status").write_text("2\n")
        (d / "sub" / "deny.EXIT").write_text(" 2 ")
        (d / "exit-note").write_text("not a number")
        (d / "other").write_text("7")
        value, paths = CL.choose_exit_value(d)
        self.assertEqual(value, 2)
        self.assertEqual(
            sorted(paths),
            sorted([str(d / "exit-status"), str(d / "sub" / "deny.EXIT")]),
        )

    def test_choose_exit_value_none_when_ambiguous_or_absent(self):
        d = self.root / "d"
        d.mkdir()
        self.assertEqual(CL.choose_exit_value(d), (None, []))
        (d / "exit-a").write_text("0")
        (d / "exit-b").write_text("-1")
        value, paths = CL.choose_exit_value(d)
        self.assertIsNone(value)
        self.assertEqual(len(paths), 2)
        self.assertEqual(CL.choose_exit_value(self.root / "missing"),
                         (None, []))

    def test_choose_exit_value_ignores_symlinks_and_unreadable(self):
        d = self.root / "d"
        d.mkdir()
        target = self.root / "real-exit"
        target.write_text("9")
        (d / "exit-link").symlink_to(target)
        (d / "exit-bin").write_bytes(b"\xff\xfe")
        (d / "exit-ok").write_text("0")
        self.assertEqual(CL.choose_exit_value(d),
                         (0, [str(d / "exit-ok")]))

    # --- compare_live_pm -------------------------------------------------

    def live_fixture(self, *, before=None, after=None):
        artifact = self.root / "art"
        # The artifacts run_live_pm_control() writes (Issue #406). Detailed
        # live-PM cases live in tests/test_pilot_gate_live_pm.py.
        defaults = {
            "guard.sha256": "digest-1",
            "exit-status": "2",
            "input.raw": "{}\n",
            "stdout.raw": "",
            "stderr.raw": "denied\n",
        }
        for side, files in (("before", before), ("after", after)):
            directory = artifact / "live-pm" / side
            directory.mkdir(parents=True)
            values = dict(defaults)
            values.update(files or {})
            for name, content in values.items():
                if content is None:
                    continue
                (directory / name).write_text(content)
        return artifact

    def compare(self, artifact, after_status=0):
        rc = CL.compare_live_pm(
            argparse.Namespace(
                artifact_dir=str(artifact), after_status=after_status
            )
        )
        result = json.loads(
            (artifact / "live-pm" / "result.json").read_text()
        )
        checks = {c["name"]: c["verdict"] for c in result["checks"]}
        return rc, result, checks

    def test_compare_live_pm_pass_when_everything_matches(self):
        rc, result, checks = self.compare(self.live_fixture())
        self.assertEqual(rc, 0)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["check"], "livePmNegativeControl")
        self.assertEqual(
            checks,
            {
                "after-control-runner-status": "pass",
                "guard-digest-before-after": "pass",
                "deny-exit-before-after": "pass",
                "deny-semantic-before-after": "pass",
            },
        )

    def test_compare_live_pm_runner_status_mapping(self):
        for status, verdict, rc_expected in ((1, "fail", 1), (2, "unknown", 2),
                                             (70, "unknown", 2)):
            with self.subTest(status=status):
                self._tmp.cleanup()
                self.setUp()
                rc, _, checks = self.compare(self.live_fixture(), status)
                self.assertEqual(checks["after-control-runner-status"],
                                 verdict)
                self.assertEqual(rc, rc_expected)

    def test_compare_live_pm_guard_digest(self):
        cases = (
            ({"after": {"guard.sha256": "digest-2"}}, "fail"),
            ({"before": {"guard.sha256": ""}, "after": {"guard.sha256": ""}},
             "fail"),
            ({"after": {"guard.sha256": None}}, "unknown"),
            ({"before": {"guard.sha256": None}}, "unknown"),
        )
        for fixture, verdict in cases:
            with self.subTest(fixture=fixture):
                self._tmp.cleanup()
                self.setUp()
                _, result, checks = self.compare(self.live_fixture(**fixture))
                self.assertEqual(checks["guard-digest-before-after"], verdict)
        self.assertEqual(
            [c for c in result["checks"]
             if c["name"] == "guard-digest-before-after"][0]["reason"],
            "guard_digest_artifact_missing",
        )

    def test_compare_live_pm_exit_values(self):
        cases = (
            ({"after": {"exit-status": "0"}}, "fail", 1),
            ({"after": {"exit-status": None}}, "unknown", 2),
            ({"before": {"exit-status": "x"}}, "unknown", 2),
            ({"after": {"extra-exit": "3"}}, "unknown", 2),  # ambiguous
        )
        for fixture, verdict, rc_expected in cases:
            with self.subTest(fixture=fixture):
                self._tmp.cleanup()
                self.setUp()
                rc, _, checks = self.compare(self.live_fixture(**fixture))
                self.assertEqual(checks["deny-exit-before-after"], verdict)
                self.assertEqual(rc, rc_expected)

    def test_compare_live_pm_semantic_values(self):
        cases = (
            ({"after": {"stdout.raw": "x"}}, "fail"),
            # raw bytes are not normalized
            ({"after": {"stderr.raw": "denied\n\n"}}, "fail"),
            ({"after": {"stdout.raw": None}}, "fail"),
            ({"before": {"input.raw": None, "stdout.raw": None,
                         "stderr.raw": None},
              "after": {"input.raw": None, "stdout.raw": None,
                        "stderr.raw": None}}, "unknown"),
        )
        for fixture, verdict in cases:
            with self.subTest(fixture=fixture):
                self._tmp.cleanup()
                self.setUp()
                _, _, checks = self.compare(self.live_fixture(**fixture))
                self.assertEqual(checks["deny-semantic-before-after"], verdict)

    def test_compare_live_pm_fail_wins_over_unknown(self):
        rc, result, _ = self.compare(
            self.live_fixture(after={"guard.sha256": "other",
                                     "exit-status": None}),
        )
        self.assertEqual(rc, 1)
        self.assertEqual(result["verdict"], "fail")

    # --- result_verdict --------------------------------------------------

    def test_result_verdict(self):
        path = self.root / "result.json"
        self.assertEqual(CL.result_verdict(path),
                         ("unknown", "result_file_missing"))
        path.write_text("{")
        self.assertEqual(CL.result_verdict(path),
                         ("unknown", "result_unreadable:JSONDecodeError"))
        path.write_text("[]")
        self.assertEqual(CL.result_verdict(path),
                         ("unknown", "result_root_not_object"))
        for bad in ("PASS", None, "", 1):
            path.write_text(json.dumps({"verdict": bad}))
            self.assertEqual(CL.result_verdict(path),
                             ("unknown", "result_verdict_invalid"))
        for verdict in ("pass", "fail", "unknown"):
            path.write_text(json.dumps({"verdict": verdict, "reason": "r"}))
            self.assertEqual(CL.result_verdict(path), (verdict, "r"))
        path.write_text(json.dumps({"verdict": "fail", "reason": 5}))
        self.assertEqual(CL.result_verdict(path), ("fail", None))

    # --- flatten_problem_assertions --------------------------------------

    def test_flatten_problem_assertions_walks_known_containers(self):
        value = {
            "check": "F5",
            "verdict": "fail",
            "checks": [
                {"name": "a", "verdict": "pass"},
                {"name": "b", "verdict": "unknown"},
                {"name": "c", "verdict": "fail", "reason": "why"},
            ],
            "control": {"verdict": "unknown", "checks": [
                {"name": "d", "verdict": "fail"}]},
            "fault": {"verdict": "pass"},
            "recovery": {"verdict": "fail"},
            "F2a": {"name": "x", "verdict": "unknown"},
            "F2b": {"verdict": "pass"},
            "cases": {"verdict": "unknown"},
            # not walked
            "containment": {"verdict": "fail", "checks": [
                {"name": "hidden", "verdict": "fail"}]},
            "other": [{"name": "hidden2", "verdict": "fail"}],
        }
        problems = CL.flatten_problem_assertions(value, "F5", "F5/result.json")
        self.assertEqual(
            problems,
            [
                {"check": "F5", "reason": "assertion_fail",
                 "evidence": "F5/result.json", "verdict": "fail"},
                {"check": "b", "reason": "assertion_unknown",
                 "evidence": "F5/result.json", "verdict": "unknown"},
                {"check": "c", "reason": "why",
                 "evidence": "F5/result.json", "verdict": "fail"},
                {"check": "F5.control", "reason": "assertion_unknown",
                 "evidence": "F5/result.json", "verdict": "unknown"},
                {"check": "d", "reason": "assertion_fail",
                 "evidence": "F5/result.json", "verdict": "fail"},
                {"check": "F5.recovery", "reason": "assertion_fail",
                 "evidence": "F5/result.json", "verdict": "fail"},
                {"check": "x", "reason": "assertion_unknown",
                 "evidence": "F5/result.json", "verdict": "unknown"},
                {"check": "F5.cases", "reason": "assertion_unknown",
                 "evidence": "F5/result.json", "verdict": "unknown"},
            ],
        )
        self.assertEqual(CL.flatten_problem_assertions("x", "p", "e"), [])
        self.assertEqual(
            CL.flatten_problem_assertions([{"verdict": "fail"}], "p", "e"),
            [{"check": "p[0]", "reason": "assertion_fail", "evidence": "e",
              "verdict": "fail"}],
        )

    # --- observation -----------------------------------------------------

    def test_observation_record_shape_and_default_reason(self):
        record = CL.observation(
            check="F4", value="pass", cutoff=10, source="monotonic-clock",
            command=None, raw_evidence="F4/result.json", verdict="pass",
            reason=None,
        )
        self.assertEqual(record["reason"], "observation_matches_expected")
        self.assertRegex(record["observedAt"], r"Z$")
        self.assertEqual(
            {k: v for k, v in record.items() if k != "observedAt"},
            {"schemaVersion": 1, "check": "F4", "value": "pass",
             "cutoff": 10, "source": "monotonic-clock", "command": None,
             "rawEvidence": "F4/result.json", "verdict": "pass",
             "reason": "observation_matches_expected"},
        )
        self.assertEqual(
            CL.observation(check="F1", value="fail", cutoff=None, source="s",
                           command=None, raw_evidence="r", verdict="fail",
                           reason=None)["reason"],
            "F1_fail",
        )
        self.assertEqual(
            CL.observation(check="F1", value="fail", cutoff=None, source="s",
                           command=None, raw_evidence="r", verdict="fail",
                           reason="given")["reason"],
            "given",
        )



class PilotGateCleanupRoundE(SignalGuardedCase):
    """evaluate_run final aggregation, parser and main."""

    def setUp(self):
        super().setUp()
        self.artifact_dir = self.root / "artifacts"

    def write(self, *, checks=None, live="pass", cleanup=None):
        verdicts = {check: "pass" for check in CL.CHECKS}
        verdicts.update(checks or {})
        for check, value in verdicts.items():
            path = self.artifact_dir / check / "result.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            if value is None:
                continue
            if isinstance(value, dict):
                path.write_text(json.dumps(value))
            else:
                path.write_text(json.dumps({"verdict": value}))
        live_path = self.artifact_dir / "live-pm" / "result.json"
        live_path.parent.mkdir(parents=True, exist_ok=True)
        if live is not None:
            live_path.write_text(json.dumps({"verdict": live}))
        if cleanup is None:
            cleanup = {"status": "pass", "incomplete": False,
                       "remainingPaths": []}
        if cleanup != "absent":
            target = self.artifact_dir / "cleanup.json"
            target.write_text(
                cleanup if isinstance(cleanup, str) else json.dumps(cleanup)
            )

    def evaluate(self, *, execution_status=0, requested="all"):
        rc = CL.evaluate_run(
            argparse.Namespace(
                artifact_dir=str(self.artifact_dir),
                execution_status=execution_status,
                run_id="run-e",
                requested_check=requested,
            )
        )
        results = json.loads((self.artifact_dir / "results.json").read_text())
        observations = [
            json.loads(line) for line in
            (self.artifact_dir / "observations.jsonl").read_text().splitlines()
        ]
        return rc, results, observations

    def test_all_pass_is_pilot_ready(self):
        self.write(checks={"F4": {"verdict": "pass", "cutoffSeconds": 30}})
        (self.artifact_dir / "observations.jsonl").write_text("stale\n")
        rc, results, observations = self.evaluate()
        self.assertEqual(rc, 0)
        self.assertEqual(results["verdict"], "pass")
        self.assertIs(results["pilot_ready"], True)
        self.assertEqual(results["unknown"], [])
        self.assertEqual(results["failures"], [])
        self.assertEqual(results["executionStatus"], "pass")
        self.assertEqual(results["livePmNegativeControl"], "pass")
        self.assertEqual(results["cleanupStatus"], "pass")
        self.assertEqual(results["runId"], "run-e")
        self.assertEqual(results["requestedCheck"], "all")
        self.assertEqual(
            results["checks"], {c: {"verdict": "pass"} for c in CL.CHECKS}
        )
        self.assertEqual(
            [o["check"] for o in observations],
            [*CL.CHECKS, "livePmNegativeControl", "cleanup"],
        )
        by_check = {o["check"]: o for o in observations}
        for check in CL.CHECKS:
            self.assertEqual(by_check[check]["source"],
                             CL.SOURCE_BY_CHECK[check])
            self.assertEqual(by_check[check]["rawEvidence"],
                             f"{check}/result.json")
        self.assertEqual(by_check["F4"]["cutoff"], 30)
        self.assertIsNone(by_check["F3"]["cutoff"])

    def test_check_fail_and_unknown(self):
        self.write(checks={"F2": {"verdict": "fail", "reason": "boom"},
                           "F5": "unknown"})
        rc, results, _ = self.evaluate()
        self.assertEqual(rc, 1)
        self.assertEqual(results["verdict"], "fail")
        self.assertIs(results["pilot_ready"], False)
        self.assertIn(
            {"check": "F2", "reason": "boom", "evidence": "F2/result.json"},
            results["failures"],
        )
        self.assertIn(
            {"check": "F5", "reason": "F5_unknown",
             "evidence": "F5/result.json"},
            results["unknown"],
        )

    def test_unknown_only_is_unknown_exit_two(self):
        self.write(checks={"I1": None})  # missing result file
        rc, results, _ = self.evaluate()
        self.assertEqual(rc, 2)
        self.assertEqual(results["verdict"], "unknown")
        self.assertIn(
            {"check": "I1", "reason": "result_file_missing",
             "evidence": "I1/result.json"},
            results["unknown"],
        )

    def test_nested_problem_assertions_are_collected_once(self):
        nested = {
            "verdict": "unknown",
            "checks": [{"name": "leaf", "verdict": "fail"},
                       {"name": "leaf", "verdict": "fail"}],
        }
        self.write(checks={"F1": nested})
        rc, results, _ = self.evaluate()
        leaf = {"check": "leaf", "reason": "assertion_fail",
                "evidence": "F1/result.json"}
        self.assertEqual(results["failures"].count(leaf), 1)
        # A failing nested assertion makes the whole gate fail even though
        # the check itself only reported unknown.
        self.assertEqual(rc, 1)

    def test_nested_fail_under_a_passing_check_blocks_readiness(self):
        self.write(checks={"F3": {"verdict": "pass", "checks": [
            {"name": "inner", "verdict": "fail"}]}})
        rc, results, _ = self.evaluate()
        self.assertIs(results["pilot_ready"], False)
        self.assertEqual(results["verdict"], "fail")
        self.assertEqual(rc, 1)

    def test_nested_unknown_under_a_passing_check_blocks_readiness(self):
        # Every top-level verdict is pass; only a nested assertion is
        # unknown. pilot_ready must still be false and the gate unknown.
        self.write(checks={"F3": {"verdict": "pass", "checks": [
            {"name": "inner", "verdict": "unknown"}]}})
        rc, results, _ = self.evaluate()
        self.assertIs(results["pilot_ready"], False)
        self.assertEqual(results["verdict"], "unknown")
        self.assertEqual(rc, 2)
        self.assertIn(
            {"check": "inner", "reason": "assertion_unknown",
             "evidence": "F3/result.json"},
            results["unknown"],
        )

    def test_live_pm_negative_control(self):
        for live, rc_expected, bucket in (("fail", 1, "failures"),
                                          ("unknown", 2, "unknown"),
                                          (None, 2, "unknown")):
            with self.subTest(live=live):
                self._tmp.cleanup()
                self.setUp()
                self.write(live=live)
                rc, results, _ = self.evaluate()
                self.assertEqual(rc, rc_expected)
                self.assertIn(
                    "livePmNegativeControl",
                    [item["check"] for item in results[bucket]],
                )

    def test_cleanup_record_problems(self):
        cases = (
            ("absent", "cleanup_result_missing"),
            ("{", "cleanup_unreadable:JSONDecodeError"),
            ({"status": "done"}, "cleanup_status_invalid"),
            ([1], "cleanup_status_invalid"),
            ({"status": "unknown", "incomplete": False}, "cleanup_unknown"),
        )
        for cleanup, reason in cases:
            with self.subTest(cleanup=cleanup):
                self._tmp.cleanup()
                self.setUp()
                self.write(cleanup=cleanup)
                rc, results, _ = self.evaluate()
                self.assertEqual(rc, 2)
                self.assertEqual(results["cleanupStatus"], "unknown")
                self.assertIn(
                    {"check": "cleanup", "reason": reason,
                     "evidence": "cleanup.json"},
                    results["unknown"],
                )

    def test_cleanup_fail_is_failure(self):
        self.write(cleanup={"status": "fail", "incomplete": False,
                            "remainingPaths": []})
        rc, results, _ = self.evaluate()
        self.assertEqual(rc, 1)
        self.assertIn("cleanup",
                      [item["check"] for item in results["failures"]])

    def test_execution_status_mapping(self):
        for status, verdict, rc_expected, reason in (
            (1, "fail", 1, "gate_execution_failed"),
            (2, "unknown", 2, "gate_execution_incomplete_or_unknown"),
        ):
            with self.subTest(status=status):
                self._tmp.cleanup()
                self.setUp()
                self.write()
                rc, results, _ = self.evaluate(execution_status=status)
                self.assertEqual(rc, rc_expected)
                self.assertEqual(results["executionStatus"], verdict)
                bucket = "failures" if status == 1 else "unknown"
                self.assertIn(
                    {"check": "execution", "reason": reason,
                     "evidence": "results.json"},
                    results[bucket],
                )
                self.assertIs(results["pilot_ready"], False)

    def test_requested_check_scopes_exit_code_only(self):
        self.write(checks={"F1": "fail"})
        rc, results, _ = self.evaluate(requested="F3")
        # F3 itself, live control, cleanup and execution all pass.
        self.assertEqual(rc, 0)
        # results.json keeps the full-pilot verdict.
        self.assertEqual(results["verdict"], "fail")
        self.assertIs(results["pilot_ready"], False)
        self.assertEqual(results["requestedCheck"], "F3")

    def test_requested_check_includes_live_cleanup_and_execution(self):
        cases = (
            ({"checks": {"F3": "unknown"}}, {}, 2),
            ({"live": "fail"}, {}, 1),
            ({"cleanup": {"status": "unknown", "incomplete": True,
                          "remainingPaths": []}}, {}, 2),
            ({}, {"execution_status": 1}, 1),
        )
        for write_kwargs, eval_kwargs, rc_expected in cases:
            with self.subTest(write=write_kwargs, eval=eval_kwargs):
                self._tmp.cleanup()
                self.setUp()
                self.write(**write_kwargs)
                rc, _, _ = self.evaluate(requested="F3", **eval_kwargs)
                self.assertEqual(rc, rc_expected)

    # --- parser / main ---------------------------------------------------

    def test_parser_subcommands_and_choices(self):
        parser = CL.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([])
            with self.assertRaises(SystemExit):
                parser.parse_args(["evaluate", "--run-id", "r",
                                   "--artifact-dir", "a",
                                   "--requested-check", "F9"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["evaluate", "--run-id", "r",
                                   "--artifact-dir", "a",
                                   "--execution-status", "3"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["compare-live", "--artifact-dir", "a",
                                   "--after-status", "5"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["cleanup", "--run-id", "r"])

        args = parser.parse_args(["evaluate", "--run-id", "r",
                                  "--artifact-dir", "a",
                                  "--requested-check", "F4",
                                  "--execution-status", "2"])
        self.assertIs(args.handler, CL.evaluate_run)
        self.assertEqual((args.requested_check, args.execution_status),
                         ("F4", 2))
        args = parser.parse_args(["compare-live", "--artifact-dir", "a",
                                  "--after-status", "70"])
        self.assertIs(args.handler, CL.compare_live_pm)
        self.assertEqual(args.after_status, 70)
        cleanup_argv = ["cleanup"]
        for name in ("run-id", "run-root", "gate-repo", "gate-home",
                     "xdg-config", "xdg-cache", "xdg-data", "xdg-state",
                     "claude-config", "artifact-dir", "gate-team"):
            cleanup_argv += [f"--{name}", f"v-{name}"]
        args = parser.parse_args(cleanup_argv)
        self.assertIs(args.handler, CL.cleanup_run)
        self.assertEqual(args.xdg_state, "v-xdg-state")

    def run_main(self, handler):
        argv = ["pilot-gate-cleanup.py", "compare-live", "--artifact-dir",
                str(self.root), "--after-status", "0"]
        stderr = io.StringIO()
        with mock.patch.object(CL.sys, "argv", argv), \
                mock.patch.object(CL, "compare_live_pm", handler), \
                contextlib.redirect_stderr(stderr):
            # build_parser binds the handler at call time
            rc = CL.main()
        return rc, stderr.getvalue()

    def test_main_returns_handler_result_and_maps_errors(self):
        rc, _ = self.run_main(lambda args: 1)
        self.assertEqual(rc, 1)
        rc, _ = self.run_main(mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(rc, 130)
        rc, err = self.run_main(mock.Mock(side_effect=RuntimeError("bad")))
        self.assertEqual(rc, 2)
        self.assertIn("pilot-gate-cleanup: RuntimeError: bad", err)


if __name__ == "__main__":
    unittest.main()
