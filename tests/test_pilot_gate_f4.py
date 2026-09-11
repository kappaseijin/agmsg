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


class PilotGateF4RoundD(unittest.TestCase):
    RUN_ID = "round-d-run"
    TEAM = "agmsg-g4gate-round-d"

    def make_fixture(
        self,
        root: Path,
        *,
        gate_inside=True,
        executable_collector=True,
        cutoff=10,
        margin=2,
        poll_interval=0.25,
    ):
        root = root.resolve()

        run_root = root / "run-root"
        run_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        gate_repo = (
            run_root / "repo"
            if gate_inside
            else root / "outside-repo"
        )
        gate_repo.mkdir(
            parents=True,
            exist_ok=True,
        )

        claude_config = (
            run_root
            / "claude"
        )
        claude_config.mkdir(
            parents=True,
            exist_ok=True,
        )

        collector = (
            gate_repo
            / "scripts"
            / "pilot-collector.sh"
        )
        collector.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        collector.chmod(
            0o700
            if executable_collector
            else 0o600
        )

        binding = (
            gate_repo
            / "binding.json"
        )
        binding.write_text(
            "{}\n",
            encoding="utf-8",
        )

        artifact_dir = (
            root
            / "artifacts"
        )
        artifact_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        args = F4.argparse.Namespace(
            gate_repo=str(gate_repo),
            run_root=str(run_root),
            claude_config=str(
                claude_config
            ),
            artifact_dir=str(
                artifact_dir
            ),
            run_id=self.RUN_ID,
            gate_team=self.TEAM,
            cutoff_seconds=cutoff,
            margin_seconds=margin,
            poll_interval_seconds=
                poll_interval,
        )

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "claude_config":
                claude_config,
            "collector": collector,
            "binding": binding,
            "artifact_dir":
                artifact_dir,
            "artifact":
                artifact_dir / "F4",
            "args": args,
        }

    def install_setup_stubs(
        self,
        fixture,
        *,
        discover_result=(
            False,
            {
                "discover": "record",
            },
        ),
        monotonic_origin=100.0,
        wall_origin=(
            "2026-09-11T"
            "01:02:03.004Z"
        ),
        env=None,
    ):
        if env is None:
            env = {
                "COLLECTOR_ENV":
                    "yes",
            }

        latest = mock.patch.object(
            F4,
            "latest_binding",
            return_value=
                fixture["binding"],
        )
        sanitized = mock.patch.object(
            F4,
            "sanitized_collector_env",
            return_value=env,
        )
        collector = mock.patch.object(
            F4,
            "collector_call",
            return_value=
                discover_result,
        )
        monotonic = mock.patch.object(
            F4.time,
            "monotonic",
            return_value=
                monotonic_origin,
        )
        utc = mock.patch.object(
            F4,
            "utc_now",
            return_value=
                wall_origin,
        )

        return (
            latest,
            sanitized,
            collector,
            monotonic,
            utc,
        )

    def test_gate_repo_outside_run_root_is_rejected_before_latest_binding(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                gate_inside=False,
            )

            with mock.patch.object(
                F4,
                "latest_binding",
            ) as latest_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "gate repository "
                        "escapes expected root"
                    ),
                ):
                    F4.run_f4(
                        fixture["args"]
                    )

            latest_mock.assert_not_called()

    def test_nonexecutable_collector_is_rejected_before_timing_and_binding_setup(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                executable_collector=False,
            )

            with mock.patch.object(
                F4,
                "latest_binding",
            ) as latest_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "not regular executable",
                ):
                    F4.run_f4(
                        fixture["args"]
                    )

            latest_mock.assert_not_called()

    def test_numeric_validation_errors_are_checked_in_source_order(
        self,
    ):
        scenarios = (
            (
                {
                    "cutoff":
                        0,
                    "margin":
                        0,
                    "poll_interval":
                        0,
                },
                "cutoff must be positive",
            ),
            (
                {
                    "cutoff":
                        10,
                    "margin":
                        0,
                    "poll_interval":
                        0,
                },
                "margin must be positive",
            ),
            (
                {
                    "cutoff":
                        10,
                    "margin":
                        10,
                    "poll_interval":
                        0,
                },
                (
                    "margin must be "
                    "less than cutoff"
                ),
            ),
            (
                {
                    "cutoff":
                        10,
                    "margin":
                        2,
                    "poll_interval":
                        0,
                },
                (
                    "poll interval "
                    "must be positive"
                ),
            ),
        )

        for values, message in scenarios:
            with self.subTest(
                message=message
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp),
                        cutoff=
                            values[
                                "cutoff"
                            ],
                        margin=
                            values[
                                "margin"
                            ],
                        poll_interval=
                            values[
                                "poll_interval"
                            ],
                    )

                    with mock.patch.object(
                        F4,
                        "latest_binding",
                    ) as latest_mock:
                        with self.assertRaisesRegex(
                            ValueError,
                            message,
                        ):
                            F4.run_f4(
                                fixture[
                                    "args"
                                ]
                            )

                    latest_mock.assert_not_called()

    def test_stale_calls_log_is_removed_and_config_json_records_exact_setup(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                cutoff=12,
                margin=3,
                poll_interval=0.4,
            )

            artifact = (
                fixture["artifact"]
            )
            artifact.mkdir(
                parents=True,
                exist_ok=True,
            )

            calls_log = (
                artifact
                / "collector-calls.jsonl"
            )
            calls_log.write_text(
                "stale\n",
                encoding="utf-8",
            )

            discover_record = {
                "phase":
                    "initial-discover",
            }

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        False,
                        discover_record,
                    ),
                    monotonic_origin=
                        321.5,
                    wall_origin=(
                        "2026-09-11T"
                        "02:03:04.005Z"
                    ),
                    env={
                        "TEST_ENV":
                            "gate"
                    },
                )
            )

            with (
                patches[0] as latest_mock,
                patches[1] as env_mock,
                patches[2] as collector_mock,
                patches[3],
                patches[4],
            ):
                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )
            self.assertFalse(
                calls_log.exists()
            )

            config = F4.read_json(
                artifact
                / "config.json"
            )

            self.assertEqual(
                config,
                {
                    "schemaVersion": 1,
                    "check": "F4",
                    "runId":
                        self.RUN_ID,
                    "gateTeam":
                        self.TEAM,
                    "binding":
                        str(
                            fixture[
                                "binding"
                            ]
                        ),
                    "collector":
                        str(
                            fixture[
                                "collector"
                            ]
                        ),
                    "cutoffSeconds":
                        12.0,
                    "marginSeconds":
                        3.0,
                    "pollIntervalSeconds":
                        0.4,
                    (
                        "requiredPollingLoop"
                        "Successes"
                    ):
                        F4.REQUIRED_POLL_SUCCESSES,
                    "boundaryRule": {
                        "withinCutoff":
                            (
                                "elapsed "
                                "<= cutoff"
                            ),
                        "cutoffExceeded":
                            (
                                "elapsed "
                                "> cutoff"
                            ),
                    },
                    "startedAtWall":
                        (
                            "2026-09-11T"
                            "02:03:04.005Z"
                        ),
                    "startedAtMonotonic":
                        321.5,
                },
            )

            latest_mock.assert_called_once_with(
                fixture["gate_repo"],
                self.TEAM,
            )

            env_mock.assert_called_once_with(
                fixture["binding"],
                artifact
                / "collector-state",
                fixture[
                    "claude_config"
                ],
            )

            collector_mock.assert_called_once_with(
                fixture["collector"],
                "discover",
                cwd=
                    fixture["gate_repo"],
                env={
                    "TEST_ENV":
                        "gate"
                },
                artifact_log=
                    calls_log,
                phase=
                    "initial-discover",
                monotonic_origin=
                    321.5,
            )

    def test_discover_false_returns_fail_one_and_never_constructs_poller(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            discover_record = {
                "exitStatus": 1,
                "collectorStatus":
                    "failed",
            }

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        False,
                        discover_record,
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2] as collector_mock,
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )
            poller_class.assert_not_called()

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            self.assertEqual(
                result,
                {
                    "schemaVersion": 1,
                    "check": "F4",
                    "runId":
                        self.RUN_ID,
                    "verdict":
                        "fail",
                    "reason":
                        (
                            "initial_"
                            "discover_not_ok"
                        ),
                    "discover":
                        discover_record,
                },
            )

            self.assertEqual(
                collector_mock.call_count,
                1,
            )

    def test_discover_none_returns_unknown_two_and_never_constructs_poller(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            discover_record = {
                "exitStatus": 2,
                "collectorStatus":
                    "unknown",
            }

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        None,
                        discover_record,
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )
            poller_class.assert_not_called()

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                (
                    "initial_"
                    "discover_not_ok"
                ),
            )
            self.assertEqual(
                result["discover"],
                discover_record,
            )

    def test_poller_is_constructed_with_exact_environment_and_required_success_count(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                poll_interval=0.75,
            )

            env = {
                "COLLECTOR_ENV":
                    "isolated",
            }

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        True,
                        {
                            "discover":
                                "ok"
                        },
                    ),
                    monotonic_origin=
                        700.0,
                    env=env,
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                poller = (
                    poller_class
                    .return_value
                )
                poller.run_until_successes.return_value = (
                    False
                )
                poller.scan_attempts = 1
                poller.successful_scans = 0
                poller.last_scan_record = {
                    "scan": "failed"
                }
                poller.last_success_monotonic = (
                    None
                )
                poller.last_success_wall = (
                    None
                )

                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            calls_log = (
                fixture["artifact"]
                / "collector-calls.jsonl"
            )

            poller_class.assert_called_once_with(
                collector=
                    fixture["collector"],
                cwd=
                    fixture["gate_repo"],
                env=env,
                artifact_log=
                    calls_log,
                poll_interval_seconds=
                    0.75,
                monotonic_origin=
                    700.0,
            )

            poller.run_until_successes.assert_called_once_with(
                F4.REQUIRED_POLL_SUCCESSES
            )

    def test_polling_false_returns_fail_with_polling_snapshot(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        True,
                        {
                            "discover":
                                "ok"
                        },
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                poller = (
                    poller_class
                    .return_value
                )
                poller.run_until_successes.return_value = (
                    False
                )
                poller.scan_attempts = 3
                poller.successful_scans = 1
                poller.last_scan_record = {
                    "phase":
                        "polling-loop",
                    "exitStatus":
                        1,
                }
                poller.last_success_monotonic = (
                    123.0
                )
                poller.last_success_wall = (
                    "2026-09-11T"
                    "03:00:00.000Z"
                )

                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            self.assertEqual(
                result,
                {
                    "schemaVersion": 1,
                    "check": "F4",
                    "runId":
                        self.RUN_ID,
                    "verdict":
                        "fail",
                    "reason":
                        (
                            "polling_loop_"
                            "not_established"
                        ),
                    "polling": {
                        "scanAttempts":
                            3,
                        "successfulScans":
                            1,
                        "lastScan": {
                            "phase":
                                "polling-loop",
                            "exitStatus":
                                1,
                        },
                    },
                },
            )

    def test_polling_none_returns_unknown_with_polling_snapshot(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        True,
                        {
                            "discover":
                                "ok"
                        },
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                poller = (
                    poller_class
                    .return_value
                )
                poller.run_until_successes.return_value = (
                    None
                )
                poller.scan_attempts = 2
                poller.successful_scans = 1
                poller.last_scan_record = {
                    "scan":
                        "unknown"
                }
                poller.last_success_monotonic = (
                    None
                )
                poller.last_success_wall = (
                    None
                )

                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                (
                    "polling_loop_"
                    "not_established"
                ),
            )
            self.assertEqual(
                result["polling"],
                {
                    "scanAttempts": 2,
                    "successfulScans": 1,
                    "lastScan": {
                        "scan":
                            "unknown"
                    },
                },
            )

    def test_polling_true_but_missing_last_success_monotonic_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        True,
                        {
                            "discover":
                                "ok"
                        },
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                poller = (
                    poller_class
                    .return_value
                )
                poller.run_until_successes.return_value = (
                    True
                )
                poller.scan_attempts = 2
                poller.successful_scans = 2
                poller.last_scan_record = {
                    "scan": "ok"
                }
                poller.last_success_monotonic = (
                    None
                )
                poller.last_success_wall = (
                    "2026-09-11T"
                    "04:00:00.000Z"
                )

                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                (
                    "polling_loop_"
                    "not_established"
                ),
            )

    def test_polling_true_but_missing_last_success_wall_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            patches = (
                self.install_setup_stubs(
                    fixture,
                    discover_result=(
                        True,
                        {
                            "discover":
                                "ok"
                        },
                    ),
                )
            )

            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                mock.patch.object(
                    F4,
                    "AuditPollingLoop",
                ) as poller_class,
            ):
                poller = (
                    poller_class
                    .return_value
                )
                poller.run_until_successes.return_value = (
                    True
                )
                poller.scan_attempts = 2
                poller.successful_scans = 2
                poller.last_scan_record = {
                    "scan": "ok"
                }
                poller.last_success_monotonic = (
                    444.0
                )
                poller.last_success_wall = (
                    None
                )

                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                (
                    "polling_loop_"
                    "not_established"
                ),
            )


class PilotGateF4RoundE(unittest.TestCase):
    RUN_ID = "round-e-run"
    TEAM = "agmsg-g4gate-round-e"

    def make_fixture(
        self,
        root: Path,
        *,
        cutoff=10,
        margin=2,
        poll_interval=0.25,
    ):
        root = root.resolve()

        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        claude_config = run_root / "claude"
        artifact_dir = root / "artifacts"

        (gate_repo / "scripts").mkdir(
            parents=True,
            exist_ok=True,
        )
        claude_config.mkdir(
            parents=True,
            exist_ok=True,
        )
        artifact_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        collector = (
            gate_repo
            / "scripts"
            / "pilot-collector.sh"
        )
        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        collector.chmod(0o700)

        binding = gate_repo / "binding.json"
        binding.write_text(
            "{}\n",
            encoding="utf-8",
        )

        args = F4.argparse.Namespace(
            gate_repo=str(gate_repo),
            run_root=str(run_root),
            claude_config=str(
                claude_config
            ),
            artifact_dir=str(
                artifact_dir
            ),
            run_id=self.RUN_ID,
            gate_team=self.TEAM,
            cutoff_seconds=cutoff,
            margin_seconds=margin,
            poll_interval_seconds=
                poll_interval,
        )

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "claude_config":
                claude_config,
            "artifact_dir":
                artifact_dir,
            "artifact":
                artifact_dir / "F4",
            "collector": collector,
            "binding": binding,
            "args": args,
        }

    def sample(
        self,
        *,
        captured: bool,
        liveness: str,
        exceeded: bool,
        expected: str,
        timestamp: float,
    ):
        return {
            "wallTimestamp":
                (
                    "2026-09-11T"
                    f"00:00:0{int(timestamp) % 10}"
                    ".000Z"
                ),
            "monotonicTimestamp":
                timestamp,
            "elapsedSinceLastSuccessfulScan":
                timestamp - 100.0,
            "cutoffSeconds":
                10.0,
            "cutoffExceeded":
                exceeded,
            "auditLiveness":
                liveness,
            "expectedObservation":
                expected,
            "expectedObservationCaptured":
                captured,
        }

    def invoke(
        self,
        fixture,
        *,
        below,
        above,
        successful_scans=2,
        scan_attempts=2,
        last_success_mono=100.0,
        last_success_wall=(
            "2026-09-11T"
            "00:00:00.000Z"
        ),
    ):
        env = {
            "AGMSG_PM_BINDING_FILE":
                str(
                    fixture["binding"]
                ),
            "CLAUDE_CONFIG_DIR":
                str(
                    fixture[
                        "claude_config"
                    ]
                ),
        }

        poller = mock.Mock()
        poller.run_until_successes.return_value = (
            True
        )
        poller.scan_attempts = (
            scan_attempts
        )
        poller.successful_scans = (
            successful_scans
        )
        poller.last_scan_record = {
            "phase": "polling-loop",
        }
        poller.last_success_monotonic = (
            last_success_mono
        )
        poller.last_success_wall = (
            last_success_wall
        )

        with mock.patch.object(
            F4,
            "latest_binding",
            return_value=
                fixture["binding"],
        ) as latest_mock, mock.patch.object(
            F4,
            "sanitized_collector_env",
            return_value=env,
        ) as env_mock, mock.patch.object(
            F4,
            "collector_call",
            return_value=(
                True,
                {
                    "phase":
                        "initial-discover",
                },
            ),
        ) as collector_mock, mock.patch.object(
            F4,
            "AuditPollingLoop",
            return_value=poller,
        ) as poller_class, mock.patch.object(
            F4,
            "sleep_until",
        ) as sleep_mock, mock.patch.object(
            F4,
            "liveness_sample",
            side_effect=[
                below,
                above,
            ],
        ) as liveness_mock, mock.patch.object(
            F4.time,
            "monotonic",
            return_value=50.0,
        ), mock.patch.object(
            F4,
            "utc_now",
            return_value=(
                "2026-09-11T"
                "00:00:00.000Z"
            ),
        ):
            status = F4.run_f4(
                fixture["args"]
            )

        return {
            "status": status,
            "poller": poller,
            "latest_mock":
                latest_mock,
            "env_mock":
                env_mock,
            "collector_mock":
                collector_mock,
            "poller_class":
                poller_class,
            "sleep_mock":
                sleep_mock,
            "liveness_mock":
                liveness_mock,
        }

    def test_full_success_records_polling_samples_checks_and_final_result_without_real_sleep(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            below = self.sample(
                captured=True,
                liveness="healthy",
                exceeded=False,
                expected="below",
                timestamp=108.0,
            )
            above = self.sample(
                captured=True,
                liveness="failed/stale",
                exceeded=True,
                expected="above",
                timestamp=112.0,
            )

            observed = self.invoke(
                fixture,
                below=below,
                above=above,
            )

            self.assertEqual(
                observed["status"],
                0,
            )

            artifact = (
                fixture["artifact"]
            )

            polling = F4.read_json(
                artifact
                / "polling-loop.json"
            )

            self.assertEqual(
                polling,
                {
                    "schemaVersion": 1,
                    "runId":
                        self.RUN_ID,
                    "scanAttempts":
                        2,
                    "successfulScans":
                        2,
                    "pollIntervalSeconds":
                        0.25,
                    "lastSuccessfulScanWall":
                        (
                            "2026-09-11T"
                            "00:00:00.000Z"
                        ),
                    (
                        "lastSuccessfulScan"
                        "Monotonic"
                    ):
                        100.0,
                    "stoppedIntentionally":
                        True,
                    "stoppedReason":
                        (
                            "F4 cutoff liveness "
                            "fault injection"
                        ),
                },
            )

            self.assertEqual(
                observed[
                    "sleep_mock"
                ].call_args_list,
                [
                    mock.call(108.0),
                    mock.call(112.0),
                ],
            )

            self.assertEqual(
                observed[
                    "liveness_mock"
                ].call_args_list,
                [
                    mock.call(
                        last_success_monotonic=
                            100.0,
                        cutoff_seconds=
                            10.0,
                        expected="below",
                    ),
                    mock.call(
                        last_success_monotonic=
                            100.0,
                        cutoff_seconds=
                            10.0,
                        expected="above",
                    ),
                ],
            )

            below_artifact = (
                F4.read_json(
                    artifact
                    / "below-cutoff.json"
                )
            )

            self.assertEqual(
                below_artifact,
                {
                    "schemaVersion": 1,
                    "runId":
                        self.RUN_ID,
                    "lastSuccessfulScanWall":
                        (
                            "2026-09-11T"
                            "00:00:00.000Z"
                        ),
                    (
                        "lastSuccessfulScan"
                        "Monotonic"
                    ):
                        100.0,
                    "targetElapsed":
                        8.0,
                    **below,
                },
            )

            above_artifact = (
                F4.read_json(
                    artifact
                    / "above-cutoff.json"
                )
            )

            self.assertEqual(
                above_artifact,
                {
                    "schemaVersion": 1,
                    "runId":
                        self.RUN_ID,
                    "lastSuccessfulScanWall":
                        (
                            "2026-09-11T"
                            "00:00:00.000Z"
                        ),
                    (
                        "lastSuccessfulScan"
                        "Monotonic"
                    ):
                        100.0,
                    "targetElapsed":
                        12.0,
                    **above,
                },
            )

            result = F4.read_json(
                artifact
                / "result.json"
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )
            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["check"],
                "F4",
            )
            self.assertEqual(
                result["runId"],
                self.RUN_ID,
            )
            self.assertEqual(
                result["cutoffSeconds"],
                10.0,
            )
            self.assertEqual(
                result["marginSeconds"],
                2.0,
            )
            self.assertEqual(
                result[
                    "pollIntervalSeconds"
                ],
                0.25,
            )
            self.assertEqual(
                result["binding"],
                str(
                    fixture["binding"]
                ),
            )
            self.assertEqual(
                result[
                    "lastSuccessfulScan"
                ],
                {
                    "wallTimestamp":
                        (
                            "2026-09-11T"
                            "00:00:00.000Z"
                        ),
                    "monotonicTimestamp":
                        100.0,
                },
            )
            self.assertEqual(
                result["belowCutoff"],
                below,
            )
            self.assertEqual(
                result["aboveCutoff"],
                above,
            )

            self.assertEqual(
                [
                    item["name"]
                    for item
                    in result["checks"]
                ],
                [
                    (
                        "polling-loop-"
                        "established"
                    ),
                    (
                        "below-observation-"
                        "captured"
                    ),
                    (
                        "below-cutoff-"
                        "healthy"
                    ),
                    (
                        "above-observation-"
                        "captured"
                    ),
                    (
                        "above-cutoff-"
                        "unhealthy"
                    ),
                    "boundary-rule-fixed",
                ],
            )

            self.assertTrue(
                all(
                    item["verdict"]
                    == "pass"
                    for item
                    in result["checks"]
                )
            )

            observed[
                "collector_mock"
            ].assert_called_once()

            observed[
                "poller_class"
            ].assert_called_once()

            observed[
                "poller"
            ].run_until_successes.assert_called_once_with(
                F4.REQUIRED_POLL_SUCCESSES
            )

    def test_below_uncaptured_makes_below_checks_unknown_and_overall_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            below = self.sample(
                captured=False,
                liveness="healthy",
                exceeded=False,
                expected="below",
                timestamp=110.0,
            )
            above = self.sample(
                captured=True,
                liveness="failed/stale",
                exceeded=True,
                expected="above",
                timestamp=112.0,
            )

            observed = self.invoke(
                fixture,
                below=below,
                above=above,
            )

            self.assertEqual(
                observed["status"],
                2,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            checks = {
                item["name"]:
                    item["verdict"]
                for item
                in result["checks"]
            }

            self.assertEqual(
                checks[
                    (
                        "below-observation-"
                        "captured"
                    )
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    "below-cutoff-healthy"
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    (
                        "above-observation-"
                        "captured"
                    )
                ],
                "pass",
            )
            self.assertEqual(
                checks[
                    (
                        "above-cutoff-"
                        "unhealthy"
                    )
                ],
                "pass",
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            self.assertEqual(
                observed[
                    "sleep_mock"
                ].call_count,
                2,
            )

    def test_below_captured_but_unhealthy_makes_check_fail_and_overall_fail(
        self,
    ):
        scenarios = (
            (
                "failed/stale",
                False,
            ),
            (
                "healthy",
                True,
            ),
        )

        for liveness, exceeded in scenarios:
            with self.subTest(
                liveness=liveness,
                exceeded=exceeded,
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    below = self.sample(
                        captured=True,
                        liveness=liveness,
                        exceeded=exceeded,
                        expected="below",
                        timestamp=108.0,
                    )
                    above = self.sample(
                        captured=True,
                        liveness=
                            "failed/stale",
                        exceeded=True,
                        expected="above",
                        timestamp=112.0,
                    )

                    observed = self.invoke(
                        fixture,
                        below=below,
                        above=above,
                    )

                    self.assertEqual(
                        observed["status"],
                        1,
                    )

                    result = F4.read_json(
                        fixture["artifact"]
                        / "result.json"
                    )

                    checks = {
                        item["name"]:
                            item["verdict"]
                        for item
                        in result["checks"]
                    }

                    self.assertEqual(
                        checks[
                            (
                                "below-observation-"
                                "captured"
                            )
                        ],
                        "pass",
                    )
                    self.assertEqual(
                        checks[
                            (
                                "below-cutoff-"
                                "healthy"
                            )
                        ],
                        "fail",
                    )
                    self.assertEqual(
                        result["verdict"],
                        "fail",
                    )

                    self.assertEqual(
                        observed[
                            "sleep_mock"
                        ].call_count,
                        2,
                    )

    def test_above_uncaptured_makes_above_checks_unknown_and_overall_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            below = self.sample(
                captured=True,
                liveness="healthy",
                exceeded=False,
                expected="below",
                timestamp=108.0,
            )
            above = self.sample(
                captured=False,
                liveness="failed/stale",
                exceeded=True,
                expected="above",
                timestamp=110.0,
            )

            observed = self.invoke(
                fixture,
                below=below,
                above=above,
            )

            self.assertEqual(
                observed["status"],
                2,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            checks = {
                item["name"]:
                    item["verdict"]
                for item
                in result["checks"]
            }

            self.assertEqual(
                checks[
                    (
                        "above-observation-"
                        "captured"
                    )
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    (
                        "above-cutoff-"
                        "unhealthy"
                    )
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    (
                        "below-observation-"
                        "captured"
                    )
                ],
                "pass",
            )
            self.assertEqual(
                checks[
                    "below-cutoff-healthy"
                ],
                "pass",
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            self.assertEqual(
                observed[
                    "sleep_mock"
                ].call_count,
                2,
            )

    def test_above_captured_but_not_stale_makes_check_fail_and_overall_fail(
        self,
    ):
        scenarios = (
            (
                "healthy",
                True,
            ),
            (
                "failed/stale",
                False,
            ),
        )

        for liveness, exceeded in scenarios:
            with self.subTest(
                liveness=liveness,
                exceeded=exceeded,
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    below = self.sample(
                        captured=True,
                        liveness="healthy",
                        exceeded=False,
                        expected="below",
                        timestamp=108.0,
                    )
                    above = self.sample(
                        captured=True,
                        liveness=liveness,
                        exceeded=exceeded,
                        expected="above",
                        timestamp=112.0,
                    )

                    observed = self.invoke(
                        fixture,
                        below=below,
                        above=above,
                    )

                    self.assertEqual(
                        observed["status"],
                        1,
                    )

                    result = F4.read_json(
                        fixture["artifact"]
                        / "result.json"
                    )

                    checks = {
                        item["name"]:
                            item["verdict"]
                        for item
                        in result["checks"]
                    }

                    self.assertEqual(
                        checks[
                            (
                                "above-observation-"
                                "captured"
                            )
                        ],
                        "pass",
                    )
                    self.assertEqual(
                        checks[
                            (
                                "above-cutoff-"
                                "unhealthy"
                            )
                        ],
                        "fail",
                    )
                    self.assertEqual(
                        result["verdict"],
                        "fail",
                    )

                    self.assertEqual(
                        observed[
                            "sleep_mock"
                        ].call_count,
                        2,
                    )

    def test_polling_loop_established_check_fails_if_success_count_drops_below_required(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            below = self.sample(
                captured=True,
                liveness="healthy",
                exceeded=False,
                expected="below",
                timestamp=108.0,
            )
            above = self.sample(
                captured=True,
                liveness="failed/stale",
                exceeded=True,
                expected="above",
                timestamp=112.0,
            )

            observed = self.invoke(
                fixture,
                below=below,
                above=above,
                successful_scans=1,
                scan_attempts=2,
            )

            self.assertEqual(
                observed["status"],
                1,
            )

            result = F4.read_json(
                fixture["artifact"]
                / "result.json"
            )

            checks = {
                item["name"]:
                    item["verdict"]
                for item
                in result["checks"]
            }

            self.assertEqual(
                checks[
                    (
                        "polling-loop-"
                        "established"
                    )
                ],
                "fail",
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_no_collector_or_poller_activity_occurs_between_below_and_above_samples(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            events = []

            below = self.sample(
                captured=True,
                liveness="healthy",
                exceeded=False,
                expected="below",
                timestamp=108.0,
            )
            above = self.sample(
                captured=True,
                liveness="failed/stale",
                exceeded=True,
                expected="above",
                timestamp=112.0,
            )

            env = {
                "COLLECTOR_ENV":
                    "yes",
            }

            poller = mock.Mock()
            poller.run_until_successes.return_value = (
                True
            )
            poller.scan_attempts = 2
            poller.successful_scans = 2
            poller.last_scan_record = {
                "scan": "ok",
            }
            poller.last_success_monotonic = (
                100.0
            )
            poller.last_success_wall = (
                "2026-09-11T"
                "00:00:00.000Z"
            )

            def sleep_side_effect(
                target,
            ):
                events.append(
                    (
                        "sleep",
                        target,
                    )
                )

            def sample_side_effect(
                **kwargs,
            ):
                events.append(
                    (
                        "sample",
                        kwargs["expected"],
                    )
                )

                return (
                    below
                    if kwargs[
                        "expected"
                    ] == "below"
                    else above
                )

            def collector_side_effect(
                *args,
                **kwargs,
            ):
                events.append(
                    (
                        "collector",
                        args[1],
                    )
                )

                return (
                    True,
                    {
                        "phase":
                            "initial-discover",
                    },
                )

            with mock.patch.object(
                F4,
                "latest_binding",
                return_value=
                    fixture["binding"],
            ), mock.patch.object(
                F4,
                "sanitized_collector_env",
                return_value=env,
            ), mock.patch.object(
                F4,
                "collector_call",
                side_effect=
                    collector_side_effect,
            ) as collector_mock, mock.patch.object(
                F4,
                "AuditPollingLoop",
                return_value=poller,
            ) as poller_class, mock.patch.object(
                F4,
                "sleep_until",
                side_effect=
                    sleep_side_effect,
            ) as sleep_mock, mock.patch.object(
                F4,
                "liveness_sample",
                side_effect=
                    sample_side_effect,
            ), mock.patch.object(
                F4.time,
                "monotonic",
                return_value=50.0,
            ), mock.patch.object(
                F4,
                "utc_now",
                return_value=(
                    "2026-09-11T"
                    "00:00:00.000Z"
                ),
            ):
                status = F4.run_f4(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )

            self.assertEqual(
                events,
                [
                    (
                        "collector",
                        "discover",
                    ),
                    (
                        "sleep",
                        108.0,
                    ),
                    (
                        "sample",
                        "below",
                    ),
                    (
                        "sleep",
                        112.0,
                    ),
                    (
                        "sample",
                        "above",
                    ),
                ],
            )

            collector_mock.assert_called_once()
            poller_class.assert_called_once()

            poller.run_until_successes.assert_called_once_with(
                F4.REQUIRED_POLL_SUCCESSES
            )

            self.assertEqual(
                sleep_mock.call_count,
                2,
            )

if __name__ == "__main__":
    unittest.main()
