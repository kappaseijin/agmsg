"""Round A tests for scripts/lib/pilot-gate-f1.py."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
F1_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_F1_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-f1.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_f1",
    F1_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate F1 helper: {F1_HELPER}"
    )

F1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F1)


class FakeIso:
    @staticmethod
    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()


class FakeWalkI1:
    @staticmethod
    def walk_json(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from FakeWalkI1.walk_json(child)
        elif isinstance(value, list):
            for child in value:
                yield from FakeWalkI1.walk_json(child)


class PilotGateF1RoundA(unittest.TestCase):
    def test_load_module_loads_real_file_and_missing_file_raises_file_not_found(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            module_path = root / "fixture_module.py"
            module_path.write_text(
                "VALUE = 42\n",
                encoding="utf-8",
            )

            module = F1.load_module(
                module_path,
                "pilot_gate_f1_fixture_module",
            )

            self.assertEqual(
                module.VALUE,
                42,
            )

            with self.assertRaises(
                FileNotFoundError
            ):
                F1.load_module(
                    root / "missing.py",
                    "pilot_gate_f1_missing_module",
                )

    def test_atomic_json_creates_parent_is_atomic_utf8_sorted_and_overwrites(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "nested" / "value.json"

            value = {
                "z": 1,
                "日本語": "河童",
                "a": True,
            }

            real_replace = os.replace

            with mock.patch.object(
                F1.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                F1.atomic_json(
                    output,
                    value,
                )

            replace_mock.assert_called_once()

            source, destination = (
                replace_mock.call_args.args
            )

            self.assertEqual(
                Path(source),
                output.with_name(
                    f".{output.name}.{os.getpid()}.tmp"
                ),
            )
            self.assertEqual(
                Path(destination),
                output,
            )

            raw = output.read_text(
                encoding="utf-8"
            )

            self.assertEqual(
                raw,
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )
            self.assertIn(
                "日本語",
                raw,
            )
            self.assertNotIn(
                r"\u65e5",
                raw,
            )

            F1.atomic_json(
                output,
                {"replaced": True},
            )

            self.assertEqual(
                F1.read_json(output),
                {"replaced": True},
            )

    def test_append_jsonl_appends_compact_unicode_records(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "nested" / "events.jsonl"

            F1.append_jsonl(
                output,
                {
                    "n": 1,
                    "text": "日本語",
                },
            )
            F1.append_jsonl(
                output,
                {
                    "n": 2,
                },
            )

            self.assertEqual(
                output.read_text(
                    encoding="utf-8"
                ).splitlines(),
                [
                    '{"n":1,"text":"日本語"}',
                    '{"n":2}',
                ],
            )

    def test_read_json_reads_value(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "value.json"
            path.write_text(
                '{"value":123}\n',
                encoding="utf-8",
            )

            self.assertEqual(
                F1.read_json(path),
                {"value": 123},
            )

    def test_assertion_and_verdict_priority_match_i1_contract(
        self,
    ):
        passed = F1.assertion(
            "p",
            True,
            "pass-detail",
        )
        failed = F1.assertion(
            "f",
            False,
            "fail-detail",
        )
        unknown = F1.assertion(
            "u",
            None,
            "unknown-detail",
        )

        self.assertEqual(
            passed,
            {
                "name": "p",
                "verdict": "pass",
                "detail": "pass-detail",
            },
        )
        self.assertEqual(
            failed["verdict"],
            "fail",
        )
        self.assertEqual(
            unknown["verdict"],
            "unknown",
        )

        self.assertEqual(
            F1.verdict_from_assertions(
                [passed, unknown, failed]
            ),
            "fail",
        )
        self.assertEqual(
            F1.verdict_from_assertions(
                [passed, unknown]
            ),
            "unknown",
        )
        self.assertEqual(
            F1.verdict_from_assertions(
                [passed]
            ),
            "pass",
        )
        self.assertEqual(
            F1.verdict_from_assertions(
                []
            ),
            "pass",
        )

    def test_require_regular_executable_accepts_only_regular_executable(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            executable = root / "executable"
            executable.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)

            F1.require_regular_executable(
                executable
            )

            no_exec = root / "no-exec"
            no_exec.write_text(
                "plain\n",
                encoding="utf-8",
            )
            no_exec.chmod(0o600)

            with self.assertRaises(
                RuntimeError
            ):
                F1.require_regular_executable(
                    no_exec
                )

            directory = root / "directory"
            directory.mkdir()

            with self.assertRaises(
                RuntimeError
            ):
                F1.require_regular_executable(
                    directory
                )

            link = root / "link"
            try:
                link.symlink_to(
                    executable
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            with self.assertRaises(
                RuntimeError
            ):
                F1.require_regular_executable(
                    link
                )

    def test_safe_case_team_is_deterministic_and_uses_label_specific_prefix(
        self,
    ):
        base_team = "base-team"
        run_id = "run-1"

        expected_prefixes = {
            "control": "agmsg-g4f1c",
            "fault": "agmsg-g4f1f",
            "recovery": "agmsg-g4f1r",
        }

        values = {}

        for label, prefix in expected_prefixes.items():
            with self.subTest(
                label=label
            ):
                digest = hashlib.sha256(
                    (
                        f"{base_team}:"
                        f"{run_id}:"
                        f"{label}"
                    ).encode("utf-8")
                ).hexdigest()[:12]

                first = F1.safe_case_team(
                    base_team,
                    run_id,
                    label,
                )
                second = F1.safe_case_team(
                    base_team,
                    run_id,
                    label,
                )

                self.assertEqual(
                    first,
                    f"{prefix}-{digest}",
                )
                self.assertEqual(
                    first,
                    second,
                )

                values[label] = first

        self.assertEqual(
            len(set(values.values())),
            3,
        )

    def test_safe_case_team_unknown_label_raises_key_error(
        self,
    ):
        with self.assertRaises(
            KeyError
        ):
            F1.safe_case_team(
                "base-team",
                "run-1",
                "unexpected",
            )

    def make_delegate_db(
        self,
        path: Path,
    ) -> sqlite3.Connection:
        con = sqlite3.connect(path)
        con.execute(
            """
            CREATE TABLE events (
                type TEXT,
                team TEXT,
                from_agent TEXT,
                to_agent TEXT,
                body TEXT
            )
            """
        )
        return con

    def test_delegate_write_count_counts_only_exact_matching_delegations(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            db = root / "messages.db"

            con = self.make_delegate_db(
                db
            )

            matching = json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "p2-delegation",
                    "requestId": "request-1",
                }
            )

            rows = [
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    matching,
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    matching,
                ),
                (
                    "message_sent",
                    "other-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    matching,
                ),
                (
                    "message_sent",
                    "gate-team",
                    "other-agent",
                    F1.WORKER,
                    matching,
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    "other-worker",
                    matching,
                ),
                (
                    "other-type",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    matching,
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    "{not-json",
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "kind": "other-kind",
                            "requestId": "request-1",
                        }
                    ),
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "kind": "p2-delegation",
                            "requestId": "request-1",
                        }
                    ),
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "kind": "p2-delegation",
                            "requestId": "other-request",
                        }
                    ),
                ),
            ]

            con.executemany(
                (
                    "INSERT INTO events "
                    "(type, team, from_agent, to_agent, body) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                rows,
            )
            con.commit()
            con.close()

            self.assertEqual(
                F1.delegate_write_count(
                    db,
                    team="gate-team",
                    request_id="request-1",
                ),
                2,
            )

            self.assertEqual(
                F1.delegate_write_count(
                    db,
                    team="gate-team",
                    request_id="missing-request",
                ),
                0,
            )

    def test_delegate_write_count_returns_one_for_single_match(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            db = root / "messages.db"

            con = self.make_delegate_db(
                db
            )
            con.execute(
                (
                    "INSERT INTO events "
                    "(type, team, from_agent, to_agent, body) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                (
                    "message_sent",
                    "gate-team",
                    F1.PILOT_AGENT,
                    F1.WORKER,
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "kind": "p2-delegation",
                            "requestId": "request-1",
                        }
                    ),
                ),
            )
            con.commit()
            con.close()

            self.assertEqual(
                F1.delegate_write_count(
                    db,
                    team="gate-team",
                    request_id="request-1",
                ),
                1,
            )

    def test_count_exact_native_tool_use_none_missing_zero_one_and_multiple(
        self,
    ):
        command = "broker --config config delegate < request"

        self.assertIsNone(
            F1.count_exact_native_tool_use(
                FakeWalkI1,
                None,
                command,
            )
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            missing = root / "missing.jsonl"

            self.assertIsNone(
                F1.count_exact_native_tool_use(
                    FakeWalkI1,
                    missing,
                    command,
                )
            )

            transcript = root / "transcript.jsonl"

            transcript.write_text(
                "\n".join(
                    [
                        "{bad-json",
                        json.dumps(
                            {
                                "type": "tool_use",
                                "id": "wrong-name",
                                "name": "Other",
                                "input": {
                                    "command": command,
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F1.count_exact_native_tool_use(
                    FakeWalkI1,
                    transcript,
                    command,
                ),
                0,
            )

            matching_node = {
                "type": "tool_use",
                "id": "tool-1",
                "name": "Bash",
                "input": {
                    "command": command,
                },
            }

            transcript.write_text(
                json.dumps(
                    {
                        "nested": [
                            matching_node,
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F1.count_exact_native_tool_use(
                    FakeWalkI1,
                    transcript,
                    command,
                ),
                1,
            )

            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            matching_node
                        ),
                        json.dumps(
                            {
                                "wrapper": {
                                    "child":
                                        matching_node
                                }
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F1.count_exact_native_tool_use(
                    FakeWalkI1,
                    transcript,
                    command,
                ),
                2,
            )

    def test_fault_shim_bytes_is_valid_fixed_python_source(
        self,
    ):
        payload = F1.fault_shim_bytes()
        source = payload.decode(
            "utf-8"
        )

        self.assertTrue(
            source.startswith(
                "#!/usr/bin/env python3\n"
            )
        )
        self.assertIn(
            'AGMSG_GATE_F1_SHIM_LOG',
            source,
        )
        self.assertIn(
            "json.dumps(record",
            source,
        )
        self.assertIn(
            "raise SystemExit(1)",
            source,
        )

        compile(
            source,
            "<pilot-gate-f1-shim>",
            "exec",
        )

    def test_fault_shim_executes_logs_json_and_exits_one(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            shim = root / "provider"
            log = root / "shim.jsonl"

            shim.write_bytes(
                F1.fault_shim_bytes()
            )
            shim.chmod(0o700)

            env = os.environ.copy()
            env[
                "AGMSG_GATE_F1_SHIM_LOG"
            ] = str(log)

            cp = subprocess.run(
                [
                    str(shim),
                    "message-send",
                    "gate-team",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
                env=env,
            )

            self.assertEqual(
                cp.returncode,
                1,
            )
            self.assertEqual(
                cp.stdout,
                "",
            )
            self.assertIn(
                (
                    "pilot-gate-f1: deterministic "
                    "provider backend unavailable"
                ),
                cp.stderr,
            )

            values = F1.shim_invocations(
                log
            )

            self.assertIsNotNone(
                values
            )
            self.assertEqual(
                len(values),
                1,
            )
            self.assertEqual(
                values[0]["schemaVersion"],
                1,
            )
            self.assertEqual(
                values[0]["argv"],
                [
                    "message-send",
                    "gate-team",
                ],
            )
            self.assertIsInstance(
                values[0][
                    "observedAtMonotonic"
                ],
                float,
            )

    def test_shim_invocations_absent_empty_and_valid_records(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "shim.jsonl"

            self.assertEqual(
                F1.shim_invocations(
                    path
                ),
                [],
            )

            path.write_text(
                "\n\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F1.shim_invocations(
                    path
                ),
                [],
            )

            path.write_text(
                (
                    '{"n":1}\n'
                    '\n'
                    '{"n":2}\n'
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                F1.shim_invocations(
                    path
                ),
                [
                    {"n": 1},
                    {"n": 2},
                ],
            )

    def test_shim_invocations_nonobject_or_invalid_json_makes_entire_result_none(
        self,
    ):
        cases = (
            '[1,2,3]\n',
            '{not-json\n',
        )

        for bad_line in cases:
            with self.subTest(
                bad_line=bad_line
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    path = root / "shim.jsonl"

                    path.write_text(
                        (
                            '{"before":true}\n'
                            + bad_line
                            + '{"after":true}\n'
                        ),
                        encoding="utf-8",
                    )

                    self.assertIsNone(
                        F1.shim_invocations(
                            path
                        )
                    )

    def test_register_member_logs_before_run_creates_project_and_passes_exact_argv_env(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()
            project = root / "project"
            mutation_log = root / "mutation.jsonl"

            observed = {}

            def fake_run(
                argv,
                *,
                cwd=None,
                env=None,
                stdin=None,
            ):
                self.assertTrue(
                    mutation_log.is_file()
                )

                records = [
                    json.loads(line)
                    for line
                    in mutation_log.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]

                self.assertEqual(
                    len(records),
                    1,
                )

                observed[
                    "argv"
                ] = argv
                observed[
                    "cwd"
                ] = cwd
                observed[
                    "env"
                ] = env

                return types.SimpleNamespace(
                    returncode=0,
                    stderr="",
                )

            i1 = types.SimpleNamespace(
                run=fake_run
            )

            env = {
                "PATH": "/bin",
                "AGMSG_RESOLVE_PROJECT":
                    "wrong-before-override",
            }

            F1.register_member(
                i1,
                gate_repo=gate_repo,
                team="gate-team",
                agent="agent-1",
                project=project,
                role="worker",
                kind="service",
                env=env,
                mutation_log=mutation_log,
            )

            self.assertTrue(
                project.is_dir()
            )

            join = (
                gate_repo
                / "scripts"
                / "join.sh"
            )

            expected_argv = [
                str(join),
                "gate-team",
                "agent-1",
                F1.PILOT_TYPE,
                str(project),
                "--role",
                "worker",
                "--kind",
                "service",
            ]

            self.assertEqual(
                observed["argv"],
                [
                    "bash",
                    *expected_argv,
                ],
            )
            self.assertEqual(
                observed["cwd"],
                gate_repo,
            )
            self.assertEqual(
                observed["env"][
                    "AGMSG_RESOLVE_PROJECT"
                ],
                "0",
            )
            self.assertEqual(
                env[
                    "AGMSG_RESOLVE_PROJECT"
                ],
                "wrong-before-override",
            )

            records = [
                json.loads(line)
                for line
                in mutation_log.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

            self.assertEqual(
                records,
                [
                    {
                        "kind":
                            "team-registration",
                        "argv":
                            expected_argv,
                        "team":
                            "gate-team",
                        "target":
                            str(project),
                    }
                ],
            )

    def test_register_member_nonzero_exit_raises_with_team_agent_and_rc(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()
            project = root / "project"
            mutation_log = root / "mutation.jsonl"

            i1 = mock.Mock()
            i1.run.return_value = (
                types.SimpleNamespace(
                    returncode=7,
                    stderr="join rejected",
                )
            )

            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "join failed team=gate-team "
                    "agent=agent-1 rc=7"
                ),
            ):
                F1.register_member(
                    i1,
                    gate_repo=gate_repo,
                    team="gate-team",
                    agent="agent-1",
                    project=project,
                    role="worker",
                    kind="service",
                    env={},
                    mutation_log=mutation_log,
                )

            self.assertTrue(
                mutation_log.is_file()
            )

    def test_prepare_case_team_registers_pm_worker_sender_exactly(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            mutation_log = root / "mutation.jsonl"
            env = {"PATH": "/bin"}

            with mock.patch.object(
                F1,
                "register_member",
            ) as register_mock:
                F1.prepare_case_team(
                    object(),
                    gate_repo=gate_repo,
                    team="case-team",
                    env=env,
                    mutation_log=mutation_log,
                    label="fault",
                )

            case_root = (
                gate_repo
                / ".agmsg-gate"
                / "f1"
                / "fault"
            )

            self.assertEqual(
                register_mock.call_args_list,
                [
                    mock.call(
                        mock.ANY,
                        gate_repo=gate_repo,
                        team="case-team",
                        agent=F1.PILOT_AGENT,
                        project=gate_repo,
                        role="pm",
                        kind="seat",
                        env=env,
                        mutation_log=mutation_log,
                    ),
                    mock.call(
                        mock.ANY,
                        gate_repo=gate_repo,
                        team="case-team",
                        agent=F1.WORKER,
                        project=(
                            case_root
                            / "worker"
                        ),
                        role="worker",
                        kind="service",
                        env=env,
                        mutation_log=mutation_log,
                    ),
                    mock.call(
                        mock.ANY,
                        gate_repo=gate_repo,
                        team="case-team",
                        agent=F1.SENDER,
                        project=(
                            case_root
                            / "sender"
                        ),
                        role="sender",
                        kind="service",
                        env=env,
                        mutation_log=mutation_log,
                    ),
                ],
            )

    def make_provider(
        self,
        root: Path,
    ) -> Path:
        provider = root / "provider"
        provider.write_text(
            (
                "#!/bin/sh\n"
                "printf 'provider-ok\\n'\n"
                "exit 0\n"
            ),
            encoding="utf-8",
        )
        provider.chmod(0o750)
        return provider

    def test_provider_fault_inject_captures_original_writes_artifacts_and_rejects_double_inject(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            provider = self.make_provider(
                root
            )
            original = provider.read_bytes()
            original_mode = stat.S_IMODE(
                provider.stat().st_mode
            )
            original_digest = (
                FakeIso.sha256_file(
                    provider
                )
            )

            artifact = root / "artifact"
            mutation_log = root / "mutation.jsonl"

            fault = F1.ProviderFault(
                provider=provider,
                artifact=artifact,
                iso=FakeIso,
                mutation_log=mutation_log,
            )

            record = fault.inject()

            self.assertTrue(
                fault.active
            )
            self.assertEqual(
                fault.original_bytes,
                original,
            )
            self.assertEqual(
                fault.original_mode,
                original_mode,
            )
            self.assertEqual(
                fault.original_digest,
                original_digest,
            )
            self.assertNotEqual(
                fault.fault_digest,
                original_digest,
            )

            backup = (
                artifact
                / "provider.original"
            )
            shim_artifact = (
                artifact
                / "provider.fault"
            )

            self.assertEqual(
                backup.read_bytes(),
                original,
            )
            self.assertEqual(
                stat.S_IMODE(
                    backup.stat().st_mode
                ),
                0o600,
            )
            self.assertEqual(
                shim_artifact.read_bytes(),
                F1.fault_shim_bytes(),
            )
            self.assertEqual(
                stat.S_IMODE(
                    shim_artifact.stat().st_mode
                ),
                0o700,
            )
            self.assertEqual(
                provider.read_bytes(),
                F1.fault_shim_bytes(),
            )
            self.assertEqual(
                stat.S_IMODE(
                    provider.stat().st_mode
                ),
                0o700,
            )

            self.assertEqual(
                record["kind"],
                "provider-fault-inject",
            )

            records = [
                json.loads(line)
                for line
                in mutation_log.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(
                records[-1]["kind"],
                "provider-fault-inject",
            )

            inject_json = F1.read_json(
                artifact / "inject.json"
            )

            self.assertEqual(
                inject_json[
                    "originalMode"
                ],
                original_mode,
            )
            self.assertEqual(
                inject_json[
                    "faultArtifactDigest"
                ],
                FakeIso.sha256_file(
                    shim_artifact
                ),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "provider fault already active",
            ):
                fault.inject()

    def test_provider_fault_inject_rejects_symlink_provider(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            real = self.make_provider(root)
            link = root / "provider-link"

            try:
                link.symlink_to(real)
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            fault = F1.ProviderFault(
                provider=link,
                artifact=root / "artifact",
                iso=FakeIso,
                mutation_log=root / "mutation.jsonl",
            )

            with self.assertRaises(
                RuntimeError
            ):
                fault.inject()

    def test_provider_fault_restore_before_inject_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            provider = self.make_provider(
                root
            )

            fault = F1.ProviderFault(
                provider=provider,
                artifact=root / "artifact",
                iso=FakeIso,
                mutation_log=root / "mutation.jsonl",
            )

            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "provider restore requested "
                    "without captured original"
                ),
            ):
                fault.restore()

    def test_provider_fault_restore_restores_exact_bytes_mode_digest_and_evidence(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            provider = self.make_provider(
                root
            )
            original_bytes = (
                provider.read_bytes()
            )
            original_mode = stat.S_IMODE(
                provider.stat().st_mode
            )
            original_digest = (
                FakeIso.sha256_file(
                    provider
                )
            )

            artifact = root / "artifact"
            mutation_log = root / "mutation.jsonl"

            fault = F1.ProviderFault(
                provider=provider,
                artifact=artifact,
                iso=FakeIso,
                mutation_log=mutation_log,
            )

            fault.inject()
            record = fault.restore()

            self.assertFalse(
                fault.active
            )
            self.assertEqual(
                provider.read_bytes(),
                original_bytes,
            )
            self.assertEqual(
                stat.S_IMODE(
                    provider.stat().st_mode
                ),
                original_mode,
            )
            self.assertEqual(
                fault.restored_digest,
                original_digest,
            )
            self.assertEqual(
                record[
                    "restoredDigest"
                ],
                original_digest,
            )

            records = [
                json.loads(line)
                for line
                in mutation_log.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

            self.assertEqual(
                [
                    item["kind"]
                    for item
                    in records
                ],
                [
                    "provider-fault-inject",
                    "provider-fault-restore",
                ],
            )

            restore_json = F1.read_json(
                artifact / "restore.json"
            )

            self.assertTrue(
                restore_json[
                    "restoredMatchesOriginal"
                ]
            )
            self.assertEqual(
                restore_json[
                    "originalDigest"
                ],
                original_digest,
            )
            self.assertEqual(
                restore_json[
                    "restoredDigest"
                ],
                original_digest,
            )

    def test_provider_fault_real_inject_execute_restore_flow(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            provider = self.make_provider(
                root
            )
            shim_log = root / "shim.jsonl"

            fault = F1.ProviderFault(
                provider=provider,
                artifact=root / "artifact",
                iso=FakeIso,
                mutation_log=root / "mutation.jsonl",
            )

            original = subprocess.run(
                [str(provider)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(
                original.returncode,
                0,
            )
            self.assertEqual(
                original.stdout,
                "provider-ok\n",
            )

            fault.inject()

            env = os.environ.copy()
            env[
                "AGMSG_GATE_F1_SHIM_LOG"
            ] = str(shim_log)

            broken = subprocess.run(
                [
                    str(provider),
                    "message-send",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(
                broken.returncode,
                1,
            )
            self.assertIn(
                (
                    "deterministic provider "
                    "backend unavailable"
                ),
                broken.stderr,
            )

            fault.restore()

            recovered = subprocess.run(
                [str(provider)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(
                recovered.returncode,
                0,
            )
            self.assertEqual(
                recovered.stdout,
                "provider-ok\n",
            )

    def make_validation_i1(
        self,
        *,
        state_result: tuple[
            bool | None,
            str,
        ] = (
            True,
            "expected_state",
        ),
    ):
        i1 = mock.Mock()

        i1.expected_common.side_effect = (
            lambda *args, **kwargs: [
                F1.assertion(
                    "common",
                    True,
                    None,
                )
            ]
        )

        i1.classify_broker_state.return_value = (
            state_result
        )

        return i1

    def test_validate_helpers_propagate_nonpass_native_record_and_missing_broker(
        self,
    ):
        functions = (
            (
                F1.validate_receive,
                {
                    "input_id": "input-1",
                    "owner": "owner-1",
                },
            ),
            (
                F1.validate_successful_delegate,
                {
                    "input_id": "input-1",
                },
            ),
            (
                F1.validate_fault_delegate,
                {},
            ),
        )

        for function, extra in functions:
            with self.subTest(
                function=function.__name__
            ):
                i1 = self.make_validation_i1()

                result = function(
                    i1,
                    record={
                        "verdict": "fail",
                        "reason": "native-failed",
                    },
                    run_id="run-1",
                    request_id="request-1",
                    team="team-1",
                    generation=1,
                    **extra,
                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "fail",
                        "reason": "native-failed",
                    },
                )

                result = function(
                    i1,
                    record={},
                    run_id="run-1",
                    request_id="request-1",
                    team="team-1",
                    generation=1,
                    **extra,
                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason":
                            "native_path_not_pass",
                    },
                )

                result = function(
                    i1,
                    record={
                        "verdict": "pass",
                        "broker": [],
                    },
                    run_id="run-1",
                    request_id="request-1",
                    team="team-1",
                    generation=1,
                    **extra,
                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason":
                            "broker_response_missing",
                    },
                )

    def test_validate_receive_passes_exact_claim_and_fails_owner_mismatch(
        self,
    ):
        common = {
            "state": "claimed",
            "inputMessageId": "input-1",
            "owner": "owner-1",
        }

        i1 = self.make_validation_i1()

        passed = F1.validate_receive(
            i1,
            record={
                "verdict": "pass",
                "broker": common,
            },
            run_id="run-1",
            request_id="request-1",
            team="team-1",
            generation=1,
            input_id="input-1",
            owner="owner-1",
        )

        self.assertEqual(
            passed["verdict"],
            "pass",
        )

        failed = F1.validate_receive(
            i1,
            record={
                "verdict": "pass",
                "broker": {
                    **common,
                    "owner": "wrong-owner",
                },
            },
            run_id="run-1",
            request_id="request-1",
            team="team-1",
            generation=1,
            input_id="input-1",
            owner="owner-1",
        )

        self.assertEqual(
            failed["verdict"],
            "fail",
        )

        owner_check = next(
            item
            for item
            in failed["checks"]
            if item["name"] == "owner"
        )
        self.assertEqual(
            owner_check["verdict"],
            "fail",
        )

    def test_validate_successful_delegate_passes_complete_delegate_and_fails_missing_fields(
        self,
    ):
        i1 = self.make_validation_i1()

        broker = {
            "state": "delegated",
            "deliveryState": "queued",
            "worker": F1.WORKER,
            "inputMessageId": "input-1",
            "delegateMessageId": "delegate-1",
            "inputReceiptId": "receipt-1",
        }

        passed = (
            F1.validate_successful_delegate(
                i1,
                record={
                    "verdict": "pass",
                    "broker": broker,
                },
                run_id="run-1",
                request_id="request-1",
                team="team-1",
                generation=1,
                input_id="input-1",
            )
        )

        self.assertEqual(
            passed["verdict"],
            "pass",
        )

        failed = (
            F1.validate_successful_delegate(
                i1,
                record={
                    "verdict": "pass",
                    "broker": {
                        **broker,
                        "delegateMessageId": "",
                        "inputReceiptId": None,
                    },
                },
                run_id="run-1",
                request_id="request-1",
                team="team-1",
                generation=1,
                input_id="input-1",
            )
        )

        self.assertEqual(
            failed["verdict"],
            "fail",
        )

    def test_validate_fault_delegate_accepts_only_stopped_send_failed(
        self,
    ):
        i1 = self.make_validation_i1()

        passed = F1.validate_fault_delegate(
            i1,
            record={
                "verdict": "pass",
                "broker": {
                    "state": "stopped",
                    "reason": "send_failed",
                },
            },
            run_id="run-1",
            request_id="request-1",
            team="team-1",
            generation=1,
        )

        self.assertEqual(
            passed["verdict"],
            "pass",
        )

        checks = {
            item["name"]:
                item
            for item
            in passed["checks"]
        }

        self.assertEqual(
            checks[
                "fault-stopped"
            ]["verdict"],
            "pass",
        )
        self.assertEqual(
            checks[
                "fault-reason-send-failed"
            ]["verdict"],
            "pass",
        )

    def test_validate_fault_delegate_success_states_are_definite_fail(
        self,
    ):
        for state in (
            "delegated",
            "acked",
            "result_claimed",
        ):
            with self.subTest(
                state=state
            ):
                i1 = (
                    self.make_validation_i1()
                )

                result = (
                    F1.validate_fault_delegate(
                        i1,
                        record={
                            "verdict": "pass",
                            "broker": {
                                "state": state,
                            },
                        },
                        run_id="run-1",
                        request_id="request-1",
                        team="team-1",
                        generation=1,
                    )
                )

                self.assertEqual(
                    result["verdict"],
                    "fail",
                )

                checks = {
                    item["name"]:
                        item
                    for item
                    in result["checks"]
                }

                self.assertEqual(
                    checks[
                        "fault-stopped"
                    ]["verdict"],
                    "fail",
                )
                self.assertEqual(
                    checks[
                        "fault-reason-send-failed"
                    ]["verdict"],
                    "fail",
                )

    def test_validate_fault_delegate_unknown_and_failure_states_preserve_tristate(
        self,
    ):
        cases = (
            (
                {
                    "state":
                        "stopped_for_unknown",
                    "reason":
                        "unobservable",
                },
                "unknown",
                "unknown",
                "unknown",
            ),
            (
                {
                    "state":
                        "stopped",
                    "reason":
                        "other_failure",
                },
                "fail",
                "fail",
                "fail",
            ),
            (
                {
                    "state":
                        "error",
                    "reason":
                        "send_failed",
                },
                "fail",
                "fail",
                "fail",
            ),
            (
                {
                    "state":
                        "future-state",
                },
                "unknown",
                "unknown",
                "unknown",
            ),
        )

        for (
            broker,
            overall,
            stopped_verdict,
            reason_verdict,
        ) in cases:
            with self.subTest(
                broker=broker
            ):
                i1 = (
                    self.make_validation_i1()
                )

                result = (
                    F1.validate_fault_delegate(
                        i1,
                        record={
                            "verdict": "pass",
                            "broker": broker,
                        },
                        run_id="run-1",
                        request_id="request-1",
                        team="team-1",
                        generation=1,
                    )
                )

                self.assertEqual(
                    result["verdict"],
                    overall,
                )

                checks = {
                    item["name"]:
                        item
                    for item
                    in result["checks"]
                }

                self.assertEqual(
                    checks[
                        "fault-stopped"
                    ]["verdict"],
                    stopped_verdict,
                )
                self.assertEqual(
                    checks[
                        "fault-reason-send-failed"
                    ]["verdict"],
                    reason_verdict,
                )


if __name__ == "__main__":
    unittest.main()
