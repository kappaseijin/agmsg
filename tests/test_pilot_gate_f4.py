"""Round A tests for scripts/lib/pilot-gate-f4.py."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
F4_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_F4_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-f4.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_f4",
    F4_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate F4 helper: {F4_HELPER}"
    )

F4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F4)


class PilotGateF4RoundA(unittest.TestCase):
    TEAM = "agmsg-g4gate-round-a"

    def write_binding(
        self,
        gate_repo: Path,
        generation: int,
        *,
        team: str | None = None,
        agent: str | None = None,
        generation_value=None,
        project=None,
        root_value=None,
    ) -> Path:
        bindings = (
            gate_repo
            / "run"
            / "pilot"
            / f"{self.TEAM}__{F4.PILOT_AGENT}"
            / "bindings"
        )
        bindings.mkdir(parents=True, exist_ok=True)
        path = bindings / f"{generation}.json"

        if root_value is None:
            value = {
                "team": self.TEAM if team is None else team,
                "agent": F4.PILOT_AGENT if agent is None else agent,
                "generation": (
                    generation
                    if generation_value is None
                    else generation_value
                ),
                "project": (
                    str(gate_repo)
                    if project is None
                    else project
                ),
            }
        else:
            value = root_value

        path.write_text(
            json.dumps(value, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def bindings_dir(self, gate_repo: Path) -> Path:
        return (
            gate_repo
            / "run"
            / "pilot"
            / f"{self.TEAM}__{F4.PILOT_AGENT}"
            / "bindings"
        )

    def test_utc_now_is_millisecond_iso8601_utc_with_z_suffix(self):
        value = F4.utc_now()
        self.assertTrue(value.endswith("Z"))
        self.assertNotIn("+00:00", value)

        parsed = dt.datetime.fromisoformat(
            value[:-1] + "+00:00"
        )
        self.assertEqual(parsed.tzinfo, dt.timezone.utc)

        fractional = value.split(".", 1)[1][:-1]
        self.assertEqual(len(fractional), 3)
        self.assertTrue(fractional.isdigit())

    def test_atomic_json_uses_pid_monotonic_temp_name_utf8_and_replaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "nested" / "value.json"
            fixed_ns = 123456789
            value = {
                "z": 1,
                "日本語": "河童",
                "a": True,
            }
            real_replace = os.replace

            with mock.patch.object(
                F4.time,
                "monotonic_ns",
                return_value=fixed_ns,
            ), mock.patch.object(
                F4.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                F4.atomic_json(output, value)

            expected_tmp = output.with_name(
                f".{output.name}.{os.getpid()}.{fixed_ns}.tmp"
            )
            replace_mock.assert_called_once_with(
                expected_tmp,
                output,
            )
            self.assertFalse(expected_tmp.exists())
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )

            F4.atomic_json(output, {"replaced": "値"})
            self.assertEqual(
                F4.read_json(output),
                {"replaced": "値"},
            )

    def test_append_jsonl_appends_compact_sorted_utf8_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "nested" / "events.jsonl"

            F4.append_jsonl(
                path,
                {"z": 1, "日本語": "一"},
            )
            F4.append_jsonl(
                path,
                {"b": 2, "a": "二"},
            )

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                (
                    '{"z":1,"日本語":"一"}\n'
                    '{"a":"二","b":2}\n'
                ),
            )

    def test_read_json_reads_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "value.json"
            path.write_text(
                '{"value":"日本語"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                F4.read_json(path),
                {"value": "日本語"},
            )

    def test_assertion_and_verdict_priority(self):
        passed = F4.assertion("pass", True, "p")
        failed = F4.assertion("fail", False, "f")
        unknown = F4.assertion("unknown", None, "u")

        self.assertEqual(
            passed,
            {
                "name": "pass",
                "verdict": "pass",
                "detail": "p",
            },
        )
        self.assertEqual(failed["verdict"], "fail")
        self.assertEqual(unknown["verdict"], "unknown")
        self.assertEqual(
            F4.verdict_from_assertions(
                [passed, unknown, failed]
            ),
            "fail",
        )
        self.assertEqual(
            F4.verdict_from_assertions([passed, unknown]),
            "unknown",
        )
        self.assertEqual(
            F4.verdict_from_assertions([passed]),
            "pass",
        )
        self.assertEqual(
            F4.verdict_from_assertions([]),
            "pass",
        )

    def test_canonical_directory_resolves_symlink_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            real = root / "real"
            real.mkdir()
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            self.assertEqual(
                F4.canonical_directory(link),
                real.resolve(strict=True),
            )

    def test_canonical_directory_rejects_file_and_missing_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            file_path = root / "file"
            file_path.write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError,
                "not a directory",
            ):
                F4.canonical_directory(file_path)

            with self.assertRaises(FileNotFoundError):
                F4.canonical_directory(root / "missing")

    def test_require_regular_executable_accepts_regular_executable_and_rejects_other_forms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            executable = root / "ok.sh"
            executable.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            F4.require_regular_executable(executable)

            nonexec = root / "nonexec.sh"
            nonexec.write_text("exit 0\n", encoding="utf-8")
            nonexec.chmod(0o600)
            with self.assertRaisesRegex(
                RuntimeError,
                "not regular executable",
            ):
                F4.require_regular_executable(nonexec)

            directory = root / "directory"
            directory.mkdir()
            with self.assertRaisesRegex(
                RuntimeError,
                "not regular executable",
            ):
                F4.require_regular_executable(directory)

            link = root / "link.sh"
            try:
                link.symlink_to(executable)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            with self.assertRaisesRegex(
                RuntimeError,
                "not regular executable",
            ):
                F4.require_regular_executable(link)

    def test_ensure_descendant_accepts_child_and_rejects_escape_with_cause(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            parent = root / "parent"
            child = parent / "child"
            outside = root / "outside"
            child.mkdir(parents=True)
            outside.mkdir()

            F4.ensure_descendant(child, parent, "fixture")

            with self.assertRaisesRegex(
                RuntimeError,
                "fixture escapes expected root",
            ) as cm:
                F4.ensure_descendant(
                    outside,
                    parent,
                    "fixture",
                )

            self.assertIsInstance(cm.exception.__cause__, ValueError)

    def test_ensure_descendant_wraps_missing_path_resolution_failure_with_cause(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            parent = root / "parent"
            parent.mkdir()

            with self.assertRaisesRegex(
                RuntimeError,
                "missing-child escapes expected root",
            ) as cm:
                F4.ensure_descendant(
                    parent / "missing",
                    parent,
                    "missing-child",
                )

            self.assertIsInstance(
                cm.exception.__cause__,
                FileNotFoundError,
            )

    def test_sanitized_collector_env_removes_all_existing_agmsg_pm_keys_without_mutating_environ(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            binding = root / "binding.json"
            collector_state = root / "state"
            claude_config = root / "claude"

            original_patch = {
                "KEEP_ME": "yes",
                "AGMSG_PM_OLD": "remove",
                "AGMSG_PM_BINDING_FILE": "old-binding",
                "AGMSG_PM_COLLECTOR_STATE_DIR": "old-state",
                "CLAUDE_CONFIG_DIR": "old-claude",
            }

            with mock.patch.dict(
                os.environ,
                original_patch,
                clear=True,
            ):
                before = dict(os.environ)
                result = F4.sanitized_collector_env(
                    binding,
                    collector_state,
                    claude_config,
                )

                self.assertEqual(dict(os.environ), before)
                self.assertEqual(result["KEEP_ME"], "yes")
                self.assertNotIn("AGMSG_PM_OLD", result)
                self.assertEqual(
                    result["AGMSG_PM_BINDING_FILE"],
                    str(binding),
                )
                self.assertEqual(
                    result["AGMSG_PM_COLLECTOR_STATE_DIR"],
                    str(collector_state),
                )
                self.assertEqual(
                    result["CLAUDE_CONFIG_DIR"],
                    str(claude_config),
                )
                self.assertEqual(
                    sorted(
                        key
                        for key in result
                        if key.startswith("AGMSG_PM_")
                    ),
                    [
                        "AGMSG_PM_BINDING_FILE",
                        "AGMSG_PM_COLLECTOR_STATE_DIR",
                    ],
                )

    def test_parse_collector_response_requires_exactly_one_nonblank_json_object_line(self):
        self.assertIsNone(F4.parse_collector_response(""))
        self.assertIsNone(F4.parse_collector_response(" \n\t\n"))
        self.assertIsNone(
            F4.parse_collector_response(
                '{"one":1}\n{"two":2}\n'
            )
        )
        self.assertIsNone(
            F4.parse_collector_response("{bad-json}\n")
        )
        self.assertIsNone(
            F4.parse_collector_response('[1,2,3]\n')
        )
        self.assertEqual(
            F4.parse_collector_response(
                '\n  {"collectorStatus":"ok","n":1}  \n\n'
            ),
            {
                "collectorStatus": "ok",
                "n": 1,
            },
        )

    def test_latest_binding_rejects_unsafe_team_component(self):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = Path(temp).resolve() / "repo"
            gate_repo.mkdir()

            with self.assertRaisesRegex(
                RuntimeError,
                "gate team cannot be mapped safely",
            ):
                F4.latest_binding(
                    gate_repo,
                    "bad/team",
                )

    def test_latest_binding_rejects_missing_and_symlink_bindings_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()

            with self.assertRaisesRegex(
                RuntimeError,
                "pilot bindings directory unavailable",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

            bindings = self.bindings_dir(gate_repo)
            bindings.parent.mkdir(parents=True, exist_ok=True)
            real_bindings = root / "real-bindings"
            real_bindings.mkdir()
            try:
                bindings.symlink_to(
                    real_bindings,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            with self.assertRaisesRegex(
                RuntimeError,
                "pilot bindings directory unavailable",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

    def test_latest_binding_skips_invalid_entries_and_requires_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()
            bindings = self.bindings_dir(gate_repo)
            bindings.mkdir(parents=True)

            (bindings / "1").mkdir()
            (bindings / "2.txt").write_text("x", encoding="utf-8")
            (bindings / "0.json").write_text("{}", encoding="utf-8")
            (bindings / "01.json").write_text("{}", encoding="utf-8")
            (bindings / "abc.json").write_text("{}", encoding="utf-8")

            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = bindings / "3.json"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            with self.assertRaisesRegex(
                RuntimeError,
                "no pilot binding is available",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

    def test_latest_binding_chooses_highest_generation_numerically_not_lexically(self):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = Path(temp).resolve() / "repo"
            gate_repo.mkdir()

            nine = self.write_binding(gate_repo, 9)
            ten = self.write_binding(gate_repo, 10)

            selected = F4.latest_binding(
                gate_repo,
                self.TEAM,
            )

            self.assertEqual(selected, ten)
            self.assertNotEqual(selected, nine)

    def test_latest_binding_propagates_ensure_descendant_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = Path(temp).resolve() / "repo"
            gate_repo.mkdir()
            self.write_binding(gate_repo, 1)

            with mock.patch.object(
                F4,
                "ensure_descendant",
                side_effect=RuntimeError(
                    "binding escapes expected root"
                ),
            ) as ensure_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "binding escapes expected root",
                ):
                    F4.latest_binding(
                        gate_repo,
                        self.TEAM,
                    )

            ensure_mock.assert_called_once()

    def test_latest_binding_rejects_nonobject_json_root(self):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = Path(temp).resolve() / "repo"
            gate_repo.mkdir()
            self.write_binding(
                gate_repo,
                1,
                root_value=[],
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "latest binding root is not object",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

    def test_latest_binding_rejects_team_agent_and_generation_mismatches(self):
        scenarios = (
            (
                {"team": "wrong-team"},
                "latest binding team mismatch",
            ),
            (
                {"agent": "wrong-agent"},
                "latest binding agent mismatch",
            ),
            (
                {"generation_value": 2},
                "latest binding generation mismatch",
            ),
            (
                {"generation_value": "01"},
                "latest binding generation mismatch",
            ),
        )

        for kwargs, message in scenarios:
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as temp:
                    gate_repo = Path(temp).resolve() / "repo"
                    gate_repo.mkdir()
                    self.write_binding(
                        gate_repo,
                        1,
                        **kwargs,
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        message,
                    ):
                        F4.latest_binding(
                            gate_repo,
                            self.TEAM,
                        )

    def test_latest_binding_rejects_project_nonstring_and_existing_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()

            self.write_binding(
                gate_repo,
                1,
                project=None,
                root_value={
                    "team": self.TEAM,
                    "agent": F4.PILOT_AGENT,
                    "generation": 1,
                    "project": 123,
                },
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "latest binding project unavailable",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            other_project = root / "other-project"
            gate_repo.mkdir()
            other_project.mkdir()

            self.write_binding(
                gate_repo,
                1,
                project=str(other_project),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "latest binding project mismatch",
            ):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

    def test_latest_binding_missing_project_path_propagates_file_not_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()
            missing_project = root / "missing-project"

            self.write_binding(
                gate_repo,
                1,
                project=str(missing_project),
            )

            with self.assertRaises(FileNotFoundError):
                F4.latest_binding(
                    gate_repo,
                    self.TEAM,
                )

    def test_latest_binding_returns_valid_selected_path(self):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = Path(temp).resolve() / "repo"
            gate_repo.mkdir()
            expected = self.write_binding(
                gate_repo,
                42,
                generation_value="42",
            )

            selected = F4.latest_binding(
                gate_repo,
                self.TEAM,
            )

            self.assertEqual(selected, expected)
            self.assertTrue(selected.is_file())



class PilotGateF4RoundB(unittest.TestCase):
    def make_loop_fixture(self, root: Path, *, poll_interval=0.05):
        root = root.resolve()
        collector = root / "collector.sh"
        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        collector.chmod(0o700)
        cwd = root / "cwd"
        cwd.mkdir()
        artifact_log = root / "collector-calls.jsonl"
        return F4.AuditPollingLoop(
            collector=collector,
            cwd=cwd,
            env={"TEST_ENV": "yes"},
            artifact_log=artifact_log,
            poll_interval_seconds=poll_interval,
            monotonic_origin=10.0,
        )

    def write_collector(
        self,
        root: Path,
        *,
        stdout: str,
        stderr: str = "",
        exit_status: int = 0,
    ) -> Path:
        root = root.resolve()
        script = root / "collector.sh"
        lines = ["#!/bin/sh"]

        if stdout:
            lines.append(
                "printf '%s\\n' "
                + repr(stdout)
            )

        if stderr:
            lines.append(
                "printf '%s\\n' "
                + repr(stderr)
                + " >&2"
            )

        lines.append(
            f"exit {exit_status}"
        )

        script.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
        return script

    def read_jsonl(self, path: Path):
        return [
            json.loads(line)
            for line in path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

    def test_collector_call_timeout_returns_unknown_and_records_raw_timeout(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            collector = root / "collector.sh"
            collector.write_text(
                "#!/bin/sh\nsleep 60\n",
                encoding="utf-8",
            )
            collector.chmod(0o700)
            log = root / "calls.jsonl"

            timeout = F4.subprocess.TimeoutExpired(
                cmd=[
                    str(collector),
                    "scan",
                ],
                timeout=30,
                output="partial-out",
                stderr=b"byte-stderr",
            )

            with mock.patch.object(
                F4.subprocess,
                "run",
                side_effect=timeout,
            ) as run_mock, mock.patch.object(
                F4.time,
                "monotonic",
                side_effect=[
                    100.0,
                    100.25,
                ],
            ), mock.patch.object(
                F4,
                "utc_now",
                return_value=(
                    "2026-09-11T"
                    "00:00:00.000Z"
                ),
            ):
                result, record = (
                    F4.collector_call(
                        collector,
                        "scan",
                        cwd=root,
                        env={
                            "PATH":
                                os.environ.get(
                                    "PATH",
                                    "",
                                )
                        },
                        artifact_log=log,
                        phase="timeout-phase",
                        monotonic_origin=90.0,
                    )
                )

            self.assertIsNone(result)

            self.assertEqual(
                record,
                {
                    "schemaVersion": 1,
                    "phase":
                        "timeout-phase",
                    "operation":
                        "scan",
                    "wallTimestamp":
                        (
                            "2026-09-11T"
                            "00:00:00.000Z"
                        ),
                    (
                        "monotonicElapsed"
                        "FromF4Start"
                    ):
                        10.0,
                    "durationMonotonic":
                        0.25,
                    "exitStatus":
                        None,
                    "collectorStatus":
                        None,
                    "reason":
                        (
                            "collector_"
                            "process_timeout"
                        ),
                    "stdout":
                        "partial-out",
                    "stderr":
                        "",
                },
            )

            self.assertEqual(
                self.read_jsonl(log),
                [record],
            )

            _, kwargs = (
                run_mock.call_args
            )

            self.assertEqual(
                kwargs["timeout"],
                30,
            )
            self.assertFalse(
                kwargs["check"]
            )
            self.assertTrue(
                kwargs["text"]
            )
            self.assertEqual(
                kwargs["encoding"],
                "utf-8",
            )
            self.assertEqual(
                kwargs["errors"],
                "replace",
            )

    def test_collector_call_real_process_ok_returns_true_and_records_outputs(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            collector = (
                self.write_collector(
                    root,
                    stdout=(
                        '{"collectorStatus":"ok",'
                        '"reason":"ready"}'
                    ),
                    stderr=
                        "collector-stderr",
                    exit_status=0,
                )
            )

            log = (
                root
                / "calls.jsonl"
            )

            result, record = (
                F4.collector_call(
                    collector,
                    "discover",
                    cwd=root,
                    env=dict(os.environ),
                    artifact_log=log,
                    phase=
                        "initial-discover",
                    monotonic_origin=0.0,
                )
            )

            self.assertIs(
                result,
                True,
            )
            self.assertEqual(
                record["exitStatus"],
                0,
            )
            self.assertEqual(
                record[
                    "collectorStatus"
                ],
                "ok",
            )
            self.assertEqual(
                record["reason"],
                "ready",
            )
            self.assertEqual(
                record["stdout"],
                (
                    '{"collectorStatus":"ok",'
                    '"reason":"ready"}\n'
                ),
            )
            self.assertEqual(
                record["stderr"],
                "collector-stderr\n",
            )
            self.assertGreaterEqual(
                record[
                    "durationMonotonic"
                ],
                0.0,
            )
            self.assertGreaterEqual(
                record[
                    (
                        "monotonicElapsed"
                        "FromF4Start"
                    )
                ],
                0.0,
            )

            self.assertEqual(
                self.read_jsonl(log),
                [record],
            )

    def test_collector_call_real_process_unknown_branches(
        self,
    ):
        scenarios = (
            (
                "unknown-exit",
                (
                    '{"collectorStatus":"ok",'
                    '"reason":"rc-2"}'
                ),
                2,
                "ok",
                "rc-2",
            ),
            (
                "unknown-status",
                (
                    '{"collectorStatus":"unknown",'
                    '"reason":"uncertain"}'
                ),
                0,
                "unknown",
                "uncertain",
            ),
            (
                "audit-unavailable",
                (
                    '{"collectorStatus":'
                    '"audit_unavailable",'
                    '"reason":"missing"}'
                ),
                0,
                "audit_unavailable",
                "missing",
            ),
            (
                "unparseable",
                "not-json",
                0,
                None,
                (
                    "collector_response_"
                    "unparseable"
                ),
            ),
        )

        for (
            name,
            stdout,
            exit_status,
            expected_status,
            expected_reason,
        ) in scenarios:
            with self.subTest(
                name=name
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = (
                        Path(temp)
                        .resolve()
                    )

                    collector = (
                        self.write_collector(
                            root,
                            stdout=stdout,
                            exit_status=
                                exit_status,
                        )
                    )

                    log = (
                        root
                        / "calls.jsonl"
                    )

                    result, record = (
                        F4.collector_call(
                            collector,
                            "scan",
                            cwd=root,
                            env=
                                dict(
                                    os.environ
                                ),
                            artifact_log=
                                log,
                            phase=name,
                            monotonic_origin=
                                0.0,
                        )
                    )

                    self.assertIsNone(
                        result
                    )
                    self.assertEqual(
                        record[
                            "exitStatus"
                        ],
                        exit_status,
                    )
                    self.assertEqual(
                        record[
                            "collectorStatus"
                        ],
                        expected_status,
                    )
                    self.assertEqual(
                        record[
                            "reason"
                        ],
                        expected_reason,
                    )
                    self.assertEqual(
                        self.read_jsonl(
                            log
                        ),
                        [record],
                    )

    def test_collector_call_real_process_other_failure_returns_false(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            collector = (
                self.write_collector(
                    root,
                    stdout=(
                        '{"collectorStatus":'
                        '"failed",'
                        '"reason":"broken"}'
                    ),
                    exit_status=1,
                )
            )

            log = (
                root
                / "calls.jsonl"
            )

            result, record = (
                F4.collector_call(
                    collector,
                    "scan",
                    cwd=root,
                    env=dict(os.environ),
                    artifact_log=log,
                    phase=
                        "hard-failure",
                    monotonic_origin=0.0,
                )
            )

            self.assertIs(
                result,
                False,
            )
            self.assertEqual(
                record[
                    "collectorStatus"
                ],
                "failed",
            )
            self.assertEqual(
                record["reason"],
                "broken",
            )
            self.assertEqual(
                record["exitStatus"],
                1,
            )

    def test_audit_polling_loop_init_preserves_arguments_and_zero_state(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            loop = (
                self.make_loop_fixture(
                    root,
                    poll_interval=0.125,
                )
            )

            self.assertEqual(
                loop.collector,
                root / "collector.sh",
            )
            self.assertEqual(
                loop.cwd,
                root / "cwd",
            )
            self.assertEqual(
                loop.env,
                {
                    "TEST_ENV":
                        "yes"
                },
            )
            self.assertEqual(
                loop.artifact_log,
                (
                    root
                    / "collector-calls.jsonl"
                ),
            )
            self.assertEqual(
                loop.poll_interval_seconds,
                0.125,
            )
            self.assertEqual(
                loop.monotonic_origin,
                10.0,
            )
            self.assertEqual(
                loop.scan_attempts,
                0,
            )
            self.assertEqual(
                loop.successful_scans,
                0,
            )
            self.assertIsNone(
                loop.last_success_monotonic
            )
            self.assertIsNone(
                loop.last_success_wall
            )
            self.assertIsNone(
                loop.last_scan_record
            )

    def test_scan_once_success_updates_all_success_state_after_collector_returns(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            loop = (
                self.make_loop_fixture(
                    root
                )
            )

            record = {
                "record":
                    "success"
            }

            with mock.patch.object(
                F4,
                "collector_call",
                return_value=(
                    True,
                    record,
                ),
            ) as collector_mock, mock.patch.object(
                F4.time,
                "monotonic",
                return_value=123.5,
            ), mock.patch.object(
                F4,
                "utc_now",
                return_value=(
                    "2026-09-11T"
                    "00:01:02.003Z"
                ),
            ):
                result = (
                    loop.scan_once(
                        "phase-a"
                    )
                )

            self.assertIs(
                result,
                True,
            )
            self.assertEqual(
                loop.scan_attempts,
                1,
            )
            self.assertEqual(
                loop.successful_scans,
                1,
            )
            self.assertEqual(
                loop.last_success_monotonic,
                123.5,
            )
            self.assertEqual(
                loop.last_success_wall,
                (
                    "2026-09-11T"
                    "00:01:02.003Z"
                ),
            )
            self.assertIs(
                loop.last_scan_record,
                record,
            )

            collector_mock.assert_called_once_with(
                loop.collector,
                "scan",
                cwd=loop.cwd,
                env=loop.env,
                artifact_log=
                    loop.artifact_log,
                phase="phase-a",
                monotonic_origin=
                    loop.monotonic_origin,
            )

    def test_scan_once_false_or_unknown_updates_attempt_and_record_but_not_success_state(
        self,
    ):
        for result_value in (
            False,
            None,
        ):
            with self.subTest(
                result=result_value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = (
                        Path(temp)
                        .resolve()
                    )

                    loop = (
                        self.make_loop_fixture(
                            root
                        )
                    )

                    record = {
                        "result":
                            result_value
                    }

                    with mock.patch.object(
                        F4,
                        "collector_call",
                        return_value=(
                            result_value,
                            record,
                        ),
                    ), mock.patch.object(
                        F4.time,
                        "monotonic",
                    ) as mono_mock, mock.patch.object(
                        F4,
                        "utc_now",
                    ) as utc_mock:
                        result = (
                            loop.scan_once(
                                "phase-b"
                            )
                        )

                    self.assertIs(
                        result,
                        result_value,
                    )
                    self.assertEqual(
                        loop.scan_attempts,
                        1,
                    )
                    self.assertEqual(
                        loop.successful_scans,
                        0,
                    )
                    self.assertIs(
                        loop.last_scan_record,
                        record,
                    )
                    self.assertIsNone(
                        loop.last_success_monotonic
                    )
                    self.assertIsNone(
                        loop.last_success_wall
                    )

                    mono_mock.assert_not_called()
                    utc_mock.assert_not_called()

    def test_run_until_successes_rejects_nonpositive_required_count(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            loop = (
                self.make_loop_fixture(
                    root
                )
            )

            for required in (
                0,
                -1,
            ):
                with self.subTest(
                    required=required
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        (
                            "required_successes "
                            "must be positive"
                        ),
                    ):
                        loop.run_until_successes(
                            required
                        )

    def test_run_until_successes_returns_first_nontrue_result_after_one_scan(
        self,
    ):
        for result_value in (
            False,
            None,
        ):
            with self.subTest(
                result=result_value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = (
                        Path(temp)
                        .resolve()
                    )

                    loop = (
                        self.make_loop_fixture(
                            root
                        )
                    )

                    with mock.patch.object(
                        loop,
                        "scan_once",
                        return_value=
                            result_value,
                    ) as scan_mock:
                        result = (
                            loop
                            .run_until_successes(
                                2
                            )
                        )

                    self.assertIs(
                        result,
                        result_value,
                    )

                    scan_mock.assert_called_once_with(
                        "polling-loop"
                    )

    def test_run_until_successes_stops_at_required_successes_and_sleeps_between_scans(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            loop = (
                self.make_loop_fixture(
                    root,
                    poll_interval=0.05,
                )
            )

            records = [
                {
                    "scan": 1
                },
                {
                    "scan": 2
                },
            ]

            with mock.patch.object(
                F4,
                "collector_call",
                side_effect=[
                    (
                        True,
                        records[0],
                    ),
                    (
                        True,
                        records[1],
                    ),
                ],
            ) as collector_mock, mock.patch.object(
                F4.time,
                "monotonic",
                side_effect=[
                    10.00,
                    10.01,
                    10.02,
                    10.05,
                    10.06,
                    10.07,
                ],
            ), mock.patch.object(
                F4.time,
                "sleep",
            ) as sleep_mock, mock.patch.object(
                F4,
                "utc_now",
                side_effect=[
                    (
                        "2026-09-11T"
                        "00:00:01.000Z"
                    ),
                    (
                        "2026-09-11T"
                        "00:00:02.000Z"
                    ),
                ],
            ):
                result = (
                    loop
                    .run_until_successes(
                        2
                    )
                )

            self.assertIs(
                result,
                True,
            )
            self.assertEqual(
                loop.scan_attempts,
                2,
            )
            self.assertEqual(
                loop.successful_scans,
                2,
            )
            self.assertIs(
                loop.last_scan_record,
                records[1],
            )
            self.assertEqual(
                loop.last_success_monotonic,
                10.07,
            )
            self.assertEqual(
                loop.last_success_wall,
                (
                    "2026-09-11T"
                    "00:00:02.000Z"
                ),
            )
            self.assertEqual(
                collector_mock.call_count,
                2,
            )
            self.assertEqual(
                sleep_mock.call_count,
                1,
            )
            self.assertAlmostEqual(
                sleep_mock.call_args.args[
                    0
                ],
                0.03,
                places=7,
            )

    def test_run_until_successes_with_required_one_does_not_sleep_after_success(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            loop = (
                self.make_loop_fixture(
                    root
                )
            )

            with mock.patch.object(
                F4,
                "collector_call",
                return_value=(
                    True,
                    {
                        "scan": 1
                    },
                ),
            ), mock.patch.object(
                F4.time,
                "monotonic",
                side_effect=[
                    20.0,
                    20.1,
                ],
            ), mock.patch.object(
                F4.time,
                "sleep",
            ) as sleep_mock, mock.patch.object(
                F4,
                "utc_now",
                return_value=(
                    "2026-09-11T"
                    "00:00:03.000Z"
                ),
            ):
                result = (
                    loop
                    .run_until_successes(
                        1
                    )
                )

            self.assertIs(
                result,
                True,
            )
            self.assertEqual(
                loop.scan_attempts,
                1,
            )
            self.assertEqual(
                loop.successful_scans,
                1,
            )

            sleep_mock.assert_not_called()


class PilotGateF4RoundC(unittest.TestCase):
    def test_liveness_sample_below_cutoff_is_healthy_and_captured(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            return_value=108.0,
        ), mock.patch.object(
            F4,
            "utc_now",
            return_value="2026-09-11T00:00:00.123Z",
        ):
            result = F4.liveness_sample(
                last_success_monotonic=100.0,
                cutoff_seconds=10.0,
                expected="below",
            )

        self.assertEqual(
            result,
            {
                "wallTimestamp":
                    "2026-09-11T00:00:00.123Z",
                "monotonicTimestamp":
                    108.0,
                "elapsedSinceLastSuccessfulScan":
                    8.0,
                "cutoffSeconds":
                    10.0,
                "cutoffExceeded":
                    False,
                "auditLiveness":
                    "healthy",
                "expectedObservation":
                    "below",
                "expectedObservationCaptured":
                    True,
            },
        )

    def test_liveness_sample_above_cutoff_is_stale_and_captured(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            return_value=111.5,
        ), mock.patch.object(
            F4,
            "utc_now",
            return_value="2026-09-11T00:00:01.456Z",
        ):
            result = F4.liveness_sample(
                last_success_monotonic=100.0,
                cutoff_seconds=10.0,
                expected="above",
            )

        self.assertEqual(
            result,
            {
                "wallTimestamp":
                    "2026-09-11T00:00:01.456Z",
                "monotonicTimestamp":
                    111.5,
                "elapsedSinceLastSuccessfulScan":
                    11.5,
                "cutoffSeconds":
                    10.0,
                "cutoffExceeded":
                    True,
                "auditLiveness":
                    "failed/stale",
                "expectedObservation":
                    "above",
                "expectedObservationCaptured":
                    True,
            },
        )

    def test_liveness_sample_exact_cutoff_is_healthy_but_not_below_observation(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            return_value=110.0,
        ), mock.patch.object(
            F4,
            "utc_now",
            return_value="2026-09-11T00:00:02.000Z",
        ):
            result = F4.liveness_sample(
                last_success_monotonic=100.0,
                cutoff_seconds=10.0,
                expected="below",
            )

        self.assertEqual(
            result["elapsedSinceLastSuccessfulScan"],
            10.0,
        )
        self.assertIs(
            result["cutoffExceeded"],
            False,
        )
        self.assertEqual(
            result["auditLiveness"],
            "healthy",
        )
        self.assertEqual(
            result["expectedObservation"],
            "below",
        )
        self.assertIs(
            result["expectedObservationCaptured"],
            False,
        )

    def test_liveness_sample_exact_cutoff_is_healthy_but_not_above_observation(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            return_value=110.0,
        ), mock.patch.object(
            F4,
            "utc_now",
            return_value="2026-09-11T00:00:03.000Z",
        ):
            result = F4.liveness_sample(
                last_success_monotonic=100.0,
                cutoff_seconds=10.0,
                expected="above",
            )

        self.assertEqual(
            result["elapsedSinceLastSuccessfulScan"],
            10.0,
        )
        self.assertIs(
            result["cutoffExceeded"],
            False,
        )
        self.assertEqual(
            result["auditLiveness"],
            "healthy",
        )
        self.assertEqual(
            result["expectedObservation"],
            "above",
        )
        self.assertIs(
            result["expectedObservationCaptured"],
            False,
        )

    def test_liveness_sample_rejects_unknown_expectation(
        self,
    ):
        for expected in (
            "exact",
            "",
            "BELOW",
        ):
            with self.subTest(expected=expected):
                with mock.patch.object(
                    F4.time,
                    "monotonic",
                    return_value=105.0,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "invalid liveness expectation",
                    ):
                        F4.liveness_sample(
                            last_success_monotonic=100.0,
                            cutoff_seconds=10.0,
                            expected=expected,
                        )

    def test_sleep_until_returns_without_sleep_when_target_already_reached(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            return_value=10.0,
        ) as monotonic_mock, mock.patch.object(
            F4.time,
            "sleep",
        ) as sleep_mock:
            result = F4.sleep_until(
                10.0
            )

        self.assertIsNone(result)
        monotonic_mock.assert_called_once_with()
        sleep_mock.assert_not_called()

    def test_sleep_until_caps_long_waits_and_uses_exact_short_remaining_time(
        self,
    ):
        with mock.patch.object(
            F4.time,
            "monotonic",
            side_effect=[
                8.8,
                9.3,
                9.8,
                10.0,
            ],
        ) as monotonic_mock, mock.patch.object(
            F4.time,
            "sleep",
        ) as sleep_mock:
            result = F4.sleep_until(
                10.0
            )

        self.assertIsNone(result)

        self.assertEqual(
            monotonic_mock.call_count,
            4,
        )

        self.assertEqual(
            sleep_mock.call_count,
            3,
        )

        first, second, third = (
            sleep_mock.call_args_list
        )

        self.assertEqual(
            first.args[0],
            0.5,
        )
        self.assertEqual(
            second.args[0],
            0.5,
        )
        self.assertAlmostEqual(
            third.args[0],
            0.2,
            places=7,
        )

if __name__ == "__main__":
    unittest.main()
