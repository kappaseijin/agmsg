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



class PilotGateF1RoundBRunCase(unittest.TestCase):
    BASE_RUN_ID = "round-b"
    BASE_TEAM = "agmsg-g4gate-base"
    CASE_TEAM = "agmsg-g4f1-test"
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    GENERATION = 4
    INPUT_ID = "input-message-1"
    REQUEST_ID = (
        "f1-control-"
        + hashlib.sha256(
            b"round-b-F1-control"
        ).hexdigest()[:16]
    )

    def make_paths(self, root: Path):
        root = root.resolve()
        gate_repo = root / "repo"
        run_root = root / "run-root"
        claude_config = run_root / "claude"
        artifact_root = root / "artifacts" / "F1"

        gate_repo.mkdir(parents=True, exist_ok=True)
        run_root.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)
        artifact_root.mkdir(parents=True, exist_ok=True)

        return {
            "root": root,
            "gate_repo": gate_repo,
            "run_root": run_root,
            "claude_config": claude_config,
            "artifact_root": artifact_root,
            "mutation_log": artifact_root / "mutation-log.jsonl",
        }

    def make_i1(
        self,
        *,
        seed=None,
    ):
        i1 = mock.Mock()

        if seed is None:
            seed = {
                "state": "queued",
                "messageId": self.INPUT_ID,
            }

        i1.provider_call.return_value = seed
        i1.storage_db.return_value = Path(
            "/tmp/f1-round-b.db"
        )

        native = mock.Mock()
        native.session_id = self.SESSION_ID
        native.generation = self.GENERATION
        native.binding = Path(
            "/tmp/f1-binding.json"
        )
        native.transcript = Path(
            "/tmp/f1-transcript.jsonl"
        )
        native.start = mock.Mock()
        native.stop = mock.Mock()

        i1.NativePilot.return_value = native

        return i1, native

    def default_receive_result(self):
        return {
            "verdict": "pass",
            "checks": [
                F1.assertion(
                    "receive",
                    True,
                    None,
                )
            ],
            "broker": {
                "state": "claimed",
            },
        }

    def default_delegate_result(self):
        return {
            "verdict": "pass",
            "checks": [
                F1.assertion(
                    "delegate",
                    True,
                    None,
                )
            ],
            "broker": {
                "state": "delegated",
            },
        }

    def native_side_effect(self):
        return [
            (
                {
                    "verdict": "pass",
                    "broker": {
                        "state": "claimed",
                    },
                },
                "receive-command",
            ),
            (
                {
                    "verdict": "pass",
                    "broker": {
                        "state": "delegated",
                    },
                },
                "delegate-command",
            ),
        ]

    @contextlib.contextmanager
    def run_case_harness(
        self,
        *,
        paths,
        i1,
        receive_result=None,
        delegate_result=None,
        native_operation_side_effect=None,
        writes=1,
        transcript_count=1,
        case_team=None,
        prepare_side_effect=None,
        successful_delegate_side_effect=None,
        fault_delegate_side_effect=None,
        write_count_side_effect=None,
        tool_count_side_effect=None,
    ):
        if receive_result is None:
            receive_result = (
                self.default_receive_result()
            )

        if delegate_result is None:
            delegate_result = (
                self.default_delegate_result()
            )

        if native_operation_side_effect is None:
            native_operation_side_effect = (
                self.native_side_effect()
            )

        if case_team is None:
            case_team = self.CASE_TEAM

        stack = contextlib.ExitStack()

        safe_team = stack.enter_context(
            mock.patch.object(
                F1,
                "safe_case_team",
                return_value=case_team,
            )
        )

        prepare = stack.enter_context(
            mock.patch.object(
                F1,
                "prepare_case_team",
                side_effect=prepare_side_effect,
            )
        )

        native_operation = stack.enter_context(
            mock.patch.object(
                F1,
                "native_operation",
                side_effect=native_operation_side_effect,
            )
        )

        validate_receive = stack.enter_context(
            mock.patch.object(
                F1,
                "validate_receive",
                return_value=receive_result,
            )
        )

        validate_success = stack.enter_context(
            mock.patch.object(
                F1,
                "validate_successful_delegate",
                side_effect=successful_delegate_side_effect,
                return_value=delegate_result,
            )
        )

        validate_fault = stack.enter_context(
            mock.patch.object(
                F1,
                "validate_fault_delegate",
                side_effect=fault_delegate_side_effect,
                return_value=delegate_result,
            )
        )

        delegate_count = stack.enter_context(
            mock.patch.object(
                F1,
                "delegate_write_count",
                side_effect=write_count_side_effect,
                return_value=writes,
            )
        )

        tool_count = stack.enter_context(
            mock.patch.object(
                F1,
                "count_exact_native_tool_use",
                side_effect=tool_count_side_effect,
                return_value=transcript_count,
            )
        )

        try:
            yield {
                "safe_case_team": safe_team,
                "prepare_case_team": prepare,
                "native_operation": native_operation,
                "validate_receive": validate_receive,
                "validate_successful_delegate": validate_success,
                "validate_fault_delegate": validate_fault,
                "delegate_write_count": delegate_count,
                "count_exact_native_tool_use": tool_count,
            }
        finally:
            stack.close()

    def call_run_case(
        self,
        i1,
        paths,
        *,
        label,
        provider_fault=None,
        timeout_seconds=7.0,
    ):
        return F1.run_case(
            i1,
            mock.Mock(),
            label=label,
            base_run_id=self.BASE_RUN_ID,
            base_team=self.BASE_TEAM,
            gate_repo=paths["gate_repo"],
            run_root=paths["run_root"],
            claude_config=paths["claude_config"],
            artifact_root=paths["artifact_root"],
            env={"PATH": "/bin"},
            timeout_seconds=timeout_seconds,
            provider_fault=provider_fault,
        )

    def test_native_operation_builds_request_logs_invokes_and_persists_record(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            broker = root / "broker"
            config = root / "config.json"
            requests_dir = root / "requests"
            artifact = root / "artifact"
            mutation_log = root / "mutation.jsonl"

            i1 = mock.Mock()

            request = {
                "requestId": "request-1",
                "operation": "delegate",
            }
            i1.make_request.return_value = request
            i1.exact_broker_command.return_value = (
                "exact-broker-command"
            )

            native = mock.Mock()
            native.invoke.return_value = {
                "verdict": "pass",
                "broker": {
                    "state": "delegated",
                },
            }

            with mock.patch.object(
                F1,
                "append_jsonl",
            ) as append_mock:
                with mock.patch.object(
                    F1,
                    "atomic_json",
                ) as atomic_mock:
                    record, command = (
                        F1.native_operation(
                            i1,
                            native=native,
                            broker=broker,
                            config=config,
                            requests_dir=requests_dir,
                            artifact=artifact,
                            mutation_log=mutation_log,
                            run_id="run-1",
                            team="team-1",
                            generation=9,
                            operation="delegate",
                            request_id="request-1",
                            inputMessageId="input-1",
                            worker=F1.WORKER,
                        )
                    )

            i1.make_request.assert_called_once_with(
                "run-1",
                "request-1",
                "delegate",
                "team-1",
                9,
                inputMessageId="input-1",
                worker=F1.WORKER,
            )

            request_path = (
                requests_dir
                / "delegate.json"
            )

            i1.write_request.assert_called_once_with(
                request_path,
                request,
            )

            i1.exact_broker_command.assert_called_once_with(
                broker,
                config,
                "delegate",
                request_path,
            )

            append_mock.assert_called_once_with(
                mutation_log,
                {
                    "kind": "native-broker",
                    "operation": "delegate",
                    "argv": [
                        "exact-broker-command"
                    ],
                    "team": "team-1",
                    "target": str(
                        request_path
                    ),
                },
            )

            native.invoke.assert_called_once_with(
                "exact-broker-command",
                artifact / "delegate",
            )

            self.assertEqual(
                record["runId"],
                "run-1",
            )
            self.assertEqual(
                command,
                "exact-broker-command",
            )

            atomic_mock.assert_called_once_with(
                artifact
                / "delegate"
                / "native.json",
                record,
            )

    def test_run_case_builds_team_run_request_and_prepares_case(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
            ) as harness:
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_CONTROL,
                )

            expected_run_id = (
                f"{self.BASE_RUN_ID}"
                "-F1-control"
            )
            expected_request_id = (
                "f1-control-"
                + hashlib.sha256(
                    expected_run_id.encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
            )

            harness[
                "safe_case_team"
            ].assert_called_once_with(
                self.BASE_TEAM,
                self.BASE_RUN_ID,
                F1.CASE_CONTROL,
            )

            harness[
                "prepare_case_team"
            ].assert_called_once_with(
                i1,
                gate_repo=paths[
                    "gate_repo"
                ],
                team=self.CASE_TEAM,
                env={"PATH": "/bin"},
                mutation_log=paths[
                    "mutation_log"
                ],
                label=F1.CASE_CONTROL,
            )

            self.assertEqual(
                result["runId"],
                expected_run_id,
            )
            self.assertEqual(
                result["requestId"],
                expected_request_id,
            )
            self.assertEqual(
                result["team"],
                self.CASE_TEAM,
            )

            i1.provider_call.assert_called_once()

            provider_argv = (
                i1.provider_call.call_args.args[
                    1
                ]
            )

            self.assertEqual(
                provider_argv[:5],
                [
                    "message-send",
                    self.CASE_TEAM,
                    F1.SENDER,
                    F1.PILOT_AGENT,
                    f"seed-{expected_request_id}",
                ],
            )

            seed_body = json.loads(
                provider_argv[5]
            )

            self.assertEqual(
                seed_body,
                {
                    "schemaVersion": 1,
                    "kind": "f1-input",
                    "runId":
                        expected_run_id,
                    "case":
                        F1.CASE_CONTROL,
                },
            )

            native.stop.assert_called_once()

    def test_run_case_seed_failure_raises_before_native_is_created(
        self,
    ):
        cases = (
            {
                "state": "error",
                "messageId": self.INPUT_ID,
            },
            {
                "state": "queued",
                "messageId": "",
            },
            {
                "state": "queued",
                "messageId": 123,
            },
        )

        for seed in cases:
            with self.subTest(
                seed=seed
            ):
                with tempfile.TemporaryDirectory() as temp:
                    paths = self.make_paths(
                        Path(temp)
                    )
                    i1, native = self.make_i1(
                        seed=seed
                    )

                    with self.run_case_harness(
                        paths=paths,
                        i1=i1,
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            (
                                "F1 control "
                                "seed failed"
                            ),
                        ):
                            self.call_run_case(
                                i1,
                                paths,
                                label=(
                                    F1.CASE_CONTROL
                                ),
                            )

                    i1.NativePilot.assert_not_called()
                    native.stop.assert_not_called()

    def test_receive_nonpass_returns_early_without_delegate_and_stops_native(
        self,
    ):
        for receive_verdict in (
            "fail",
            "unknown",
        ):
            with self.subTest(
                receive_verdict=receive_verdict
            ):
                with tempfile.TemporaryDirectory() as temp:
                    paths = self.make_paths(
                        Path(temp)
                    )
                    i1, native = self.make_i1()

                    receive_result = {
                        "verdict":
                            receive_verdict,
                        "reason":
                            "synthetic-receive",
                    }

                    with self.run_case_harness(
                        paths=paths,
                        i1=i1,
                        receive_result=(
                            receive_result
                        ),
                        native_operation_side_effect=[
                            (
                                {
                                    "verdict": "pass",
                                },
                                "receive-command",
                            )
                        ],
                    ) as harness:
                        result = self.call_run_case(
                            i1,
                            paths,
                            label=(
                                F1.CASE_CONTROL
                            ),
                        )

                    self.assertEqual(
                        result[
                            "schemaVersion"
                        ],
                        1,
                    )
                    self.assertEqual(
                        result["case"],
                        F1.CASE_CONTROL,
                    )
                    self.assertEqual(
                        result["verdict"],
                        receive_verdict,
                    )
                    self.assertEqual(
                        result["reason"],
                        (
                            "receive_"
                            "prerequisite_"
                            "not_pass"
                        ),
                    )
                    self.assertEqual(
                        result["receive"],
                        receive_result,
                    )

                    self.assertEqual(
                        harness[
                            "native_operation"
                        ].call_count,
                        1,
                    )
                    harness[
                        "validate_successful_delegate"
                    ].assert_not_called()
                    harness[
                        "validate_fault_delegate"
                    ].assert_not_called()
                    harness[
                        "delegate_write_count"
                    ].assert_not_called()
                    native.stop.assert_called_once()

    def test_fault_requires_provider_fault_after_receive_pass(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                native_operation_side_effect=[
                    (
                        {
                            "verdict": "pass",
                        },
                        "receive-command",
                    )
                ],
            ) as harness:
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "fault case missing "
                        "ProviderFault"
                    ),
                ):
                    self.call_run_case(
                        i1,
                        paths,
                        label=F1.CASE_FAULT,
                        provider_fault=None,
                    )

            self.assertEqual(
                harness[
                    "native_operation"
                ].call_count,
                1,
            )
            native.stop.assert_called_once()

    def test_fault_inject_occurs_before_delegate_and_restore_after_delegate_before_validation(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            events = []

            provider_fault = mock.Mock()

            provider_fault.inject.side_effect = (
                lambda: events.append(
                    "inject"
                )
            )
            provider_fault.restore.side_effect = (
                lambda: events.append(
                    "restore"
                )
            )

            def native_op(*args, **kwargs):
                operation = kwargs[
                    "operation"
                ]

                events.append(
                    f"native:{operation}"
                )

                return (
                    {
                        "verdict": "pass",
                        "broker": {},
                    },
                    f"{operation}-command",
                )

            def fault_validate(*args, **kwargs):
                events.append(
                    "validate-fault"
                )
                return self.default_delegate_result()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                native_operation_side_effect=native_op,
                fault_delegate_side_effect=(
                    fault_validate
                ),
                writes=0,
            ) as harness:
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_FAULT,
                    provider_fault=provider_fault,
                )

            self.assertEqual(
                events,
                [
                    "native:receive",
                    "inject",
                    "native:delegate",
                    "restore",
                    "validate-fault",
                ],
            )

            provider_fault.inject.assert_called_once()
            provider_fault.restore.assert_called_once()

            harness[
                "validate_fault_delegate"
            ].assert_called_once()
            harness[
                "validate_successful_delegate"
            ].assert_not_called()

            self.assertEqual(
                result["verdict"],
                "pass",
            )
            native.stop.assert_called_once()

    def test_control_and_recovery_never_inject_and_use_success_delegate_validation(
        self,
    ):
        for label in (
            F1.CASE_CONTROL,
            F1.CASE_RECOVERY,
        ):
            with self.subTest(
                label=label
            ):
                with tempfile.TemporaryDirectory() as temp:
                    paths = self.make_paths(
                        Path(temp)
                    )
                    i1, native = self.make_i1()
                    provider_fault = mock.Mock()

                    with self.run_case_harness(
                        paths=paths,
                        i1=i1,
                        writes=1,
                    ) as harness:
                        result = self.call_run_case(
                            i1,
                            paths,
                            label=label,
                            provider_fault=(
                                provider_fault
                            ),
                        )

                    provider_fault.inject.assert_not_called()
                    provider_fault.restore.assert_not_called()

                    harness[
                        "validate_successful_delegate"
                    ].assert_called_once()
                    harness[
                        "validate_fault_delegate"
                    ].assert_not_called()

                    self.assertEqual(
                        result["verdict"],
                        "pass",
                    )
                    native.stop.assert_called_once()

    def test_control_checks_include_common_and_success_specific_assertions(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                writes=1,
                transcript_count=1,
            ):
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_CONTROL,
                )

            checks = {
                item["name"]:
                    item
                for item
                in result["checks"]
            }

            self.assertEqual(
                set(checks),
                {
                    "receive-pass",
                    (
                        "delegate-native-command-"
                        "exactly-once"
                    ),
                    "delegate-pass",
                    (
                        "persistent-delegate-"
                        "write-count"
                    ),
                },
            )

            self.assertTrue(
                all(
                    item["verdict"] == "pass"
                    for item in checks.values()
                )
            )

            native.stop.assert_called_once()

    def test_fault_checks_include_common_and_fault_specific_assertions(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()
            provider_fault = mock.Mock()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                writes=0,
                transcript_count=1,
            ):
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_FAULT,
                    provider_fault=provider_fault,
                )

            checks = {
                item["name"]:
                    item
                for item
                in result["checks"]
            }

            self.assertEqual(
                set(checks),
                {
                    "receive-pass",
                    (
                        "delegate-native-command-"
                        "exactly-once"
                    ),
                    (
                        "delegate-stopped-on-"
                        "backend-failure"
                    ),
                    (
                        "persistent-fault-request-"
                        "write-count"
                    ),
                    (
                        "provider-restored-"
                        "before-case-exit"
                    ),
                },
            )

            self.assertTrue(
                all(
                    item["verdict"] == "pass"
                    for item in checks.values()
                )
            )

            native.stop.assert_called_once()

    def test_transcript_count_none_makes_exact_native_command_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                transcript_count=None,
            ):
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_CONTROL,
                )

            check = next(
                item
                for item
                in result["checks"]
                if item["name"]
                == (
                    "delegate-native-command-"
                    "exactly-once"
                )
            )

            self.assertEqual(
                check["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            native.stop.assert_called_once()

    def test_wrong_persistent_write_count_fails_for_control_and_fault(
        self,
    ):
        cases = (
            (
                F1.CASE_CONTROL,
                0,
                None,
                (
                    "persistent-delegate-"
                    "write-count"
                ),
            ),
            (
                F1.CASE_FAULT,
                1,
                mock.Mock(),
                (
                    "persistent-fault-request-"
                    "write-count"
                ),
            ),
        )

        for (
            label,
            writes,
            provider_fault,
            check_name,
        ) in cases:
            with self.subTest(
                label=label
            ):
                with tempfile.TemporaryDirectory() as temp:
                    paths = self.make_paths(
                        Path(temp)
                    )
                    i1, native = self.make_i1()

                    with self.run_case_harness(
                        paths=paths,
                        i1=i1,
                        writes=writes,
                    ):
                        result = self.call_run_case(
                            i1,
                            paths,
                            label=label,
                            provider_fault=(
                                provider_fault
                            ),
                        )

                    check = next(
                        item
                        for item
                        in result["checks"]
                        if item["name"]
                        == check_name
                    )

                    self.assertEqual(
                        check["verdict"],
                        "fail",
                    )
                    self.assertEqual(
                        result["verdict"],
                        "fail",
                    )
                    native.stop.assert_called_once()

    def test_fault_exception_after_inject_retries_restore_in_finally_and_stops_native(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()
            provider_fault = mock.Mock()

            native_calls = 0

            def native_op(*args, **kwargs):
                nonlocal native_calls
                native_calls += 1

                if native_calls == 1:
                    return (
                        {
                            "verdict": "pass",
                        },
                        "receive-command",
                    )

                raise RuntimeError(
                    "delegate exploded"
                )

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                native_operation_side_effect=native_op,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "delegate exploded",
                ):
                    self.call_run_case(
                        i1,
                        paths,
                        label=F1.CASE_FAULT,
                        provider_fault=provider_fault,
                    )

            provider_fault.inject.assert_called_once()
            provider_fault.restore.assert_called_once()
            native.stop.assert_called_once()

    def test_fault_restore_failure_in_finally_writes_restore_error_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()
            provider_fault = mock.Mock()

            provider_fault.restore.side_effect = (
                RuntimeError(
                    "restore also failed"
                )
            )

            native_calls = 0

            def native_op(*args, **kwargs):
                nonlocal native_calls
                native_calls += 1

                if native_calls == 1:
                    return (
                        {
                            "verdict": "pass",
                        },
                        "receive-command",
                    )

                raise ValueError(
                    "delegate original failure"
                )

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                native_operation_side_effect=native_op,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "delegate original failure",
                ):
                    self.call_run_case(
                        i1,
                        paths,
                        label=F1.CASE_FAULT,
                        provider_fault=provider_fault,
                    )

            provider_fault.inject.assert_called_once()
            provider_fault.restore.assert_called_once()
            native.stop.assert_called_once()

            restore_error = F1.read_json(
                paths[
                    "artifact_root"
                ]
                / F1.CASE_FAULT
                / "restore-error.json"
            )

            self.assertEqual(
                restore_error[
                    "schemaVersion"
                ],
                1,
            )
            self.assertEqual(
                restore_error["verdict"],
                "unknown",
            )
            self.assertEqual(
                restore_error["reason"],
                (
                    "provider_restore_failed:"
                    "RuntimeError:"
                    "restore also failed"
                ),
            )

    def test_fault_immediate_restore_failure_is_retried_by_finally(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()
            provider_fault = mock.Mock()

            provider_fault.restore.side_effect = [
                RuntimeError(
                    "first restore failed"
                ),
                None,
            ]

            with self.run_case_harness(
                paths=paths,
                i1=i1,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "first restore failed",
                ):
                    self.call_run_case(
                        i1,
                        paths,
                        label=F1.CASE_FAULT,
                        provider_fault=provider_fault,
                    )

            self.assertEqual(
                provider_fault.restore.call_count,
                2,
            )
            native.stop.assert_called_once()

    def test_final_result_contains_all_required_fields(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
                writes=1,
                transcript_count=1,
            ):
                result = self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_CONTROL,
                    timeout_seconds=9.0,
                )

            expected_run_id = (
                f"{self.BASE_RUN_ID}"
                "-F1-control"
            )
            expected_request_id = (
                "f1-control-"
                + hashlib.sha256(
                    expected_run_id.encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
            )

            expected_keys = {
                "schemaVersion",
                "case",
                "runId",
                "requestId",
                "team",
                "sessionId",
                "generation",
                "binding",
                "inputMessageId",
                "delegateCommand",
                "persistentDelegateWriteCount",
                "delegateNativeToolUseCount",
                "receive",
                "delegate",
                "checks",
                "verdict",
            }

            self.assertEqual(
                set(result),
                expected_keys,
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["case"],
                F1.CASE_CONTROL,
            )
            self.assertEqual(
                result["runId"],
                expected_run_id,
            )
            self.assertEqual(
                result["requestId"],
                expected_request_id,
            )
            self.assertEqual(
                result["team"],
                self.CASE_TEAM,
            )
            self.assertEqual(
                result["sessionId"],
                self.SESSION_ID,
            )
            self.assertEqual(
                result["generation"],
                str(self.GENERATION),
            )
            self.assertEqual(
                result["binding"],
                str(native.binding),
            )
            self.assertEqual(
                result["inputMessageId"],
                self.INPUT_ID,
            )
            self.assertEqual(
                result["delegateCommand"],
                "delegate-command",
            )
            self.assertEqual(
                result[
                    "persistentDelegateWriteCount"
                ],
                1,
            )
            self.assertEqual(
                result[
                    "delegateNativeToolUseCount"
                ],
                1,
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )

            result_path = (
                paths["artifact_root"]
                / F1.CASE_CONTROL
                / "result.json"
            )

            self.assertEqual(
                F1.read_json(
                    result_path
                ),
                result,
            )

    def test_native_constructor_and_start_receive_expected_case_context(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.make_paths(
                Path(temp)
            )
            i1, native = self.make_i1()

            with self.run_case_harness(
                paths=paths,
                i1=i1,
            ):
                self.call_run_case(
                    i1,
                    paths,
                    label=F1.CASE_CONTROL,
                    timeout_seconds=13.0,
                )

            launcher = (
                paths["gate_repo"]
                / "scripts"
                / "pilot-launcher.sh"
            )

            i1.NativePilot.assert_called_once_with(
                launcher,
                paths["gate_repo"],
                self.CASE_TEAM,
                paths["claude_config"],
                (
                    paths["artifact_root"]
                    / F1.CASE_CONTROL
                    / "native"
                ),
                {"PATH": "/bin"},
                13.0,
            )

            native.start.assert_called_once()
            native.stop.assert_called_once()


class PilotGateF1RoundCRunF1(unittest.TestCase):
    ORIGINAL_DIGEST = "provider-original-digest"
    FAULT_DIGEST = "provider-fault-digest"
    RUN_ID = "round-c-run"
    GATE_TEAM = "agmsg-g4gate-round-c"

    def make_fixture(
        self,
        root: Path,
    ):
        root = root.resolve()
        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        claude_config = run_root / "claude"
        artifact_dir = root / "artifacts"

        scripts = gate_repo / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        for name in (
            "p2-provider.sh",
            "p2-consumer-broker.sh",
            "pilot-launcher.sh",
            "join.sh",
        ):
            path = scripts / name
            path.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            path.chmod(0o700)

        args = types.SimpleNamespace(
            gate_repo=str(gate_repo),
            run_root=str(run_root),
            claude_config=str(claude_config),
            artifact_dir=str(artifact_dir),
            run_id=self.RUN_ID,
            gate_team=self.GATE_TEAM,
            timeout_seconds=17,
        )

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "claude_config": claude_config,
            "artifact_dir": artifact_dir,
            "artifact": artifact_dir / "F1",
            "provider": scripts / "p2-provider.sh",
            "broker": scripts / "p2-consumer-broker.sh",
            "launcher": scripts / "pilot-launcher.sh",
            "join": scripts / "join.sh",
            "args": args,
        }

    def case_record(
        self,
        label: str,
        *,
        verdict: str = "pass",
        request_id: str | None = None,
        team: str | None = None,
    ):
        return {
            "schemaVersion": 1,
            "case": label,
            "runId": f"{self.RUN_ID}-F1-{label}",
            "requestId": (
                request_id
                if request_id is not None
                else f"request-{label}"
            ),
            "team": (
                team
                if team is not None
                else f"team-{label}"
            ),
            "verdict": verdict,
        }

    def default_cases(self):
        return [
            self.case_record(F1.CASE_CONTROL),
            self.case_record(F1.CASE_FAULT),
            self.case_record(F1.CASE_RECOVERY),
        ]

    @contextlib.contextmanager
    def harness(
        self,
        fixture,
        *,
        run_case_side_effect=None,
        sha_side_effect=None,
        shim_result=None,
        storage_side_effect=None,
        write_count_side_effect=None,
        fault=None,
        sanitize_env=None,
        atomic_json_side_effect=None,
    ):
        iso = mock.Mock()
        iso.canonical.side_effect = (
            lambda value: str(
                Path(value).resolve()
            )
        )

        if sha_side_effect is None:
            sha_side_effect = [
                self.ORIGINAL_DIGEST,
                self.ORIGINAL_DIGEST,
                self.ORIGINAL_DIGEST,
                self.ORIGINAL_DIGEST,
            ]
        iso.sha256_file.side_effect = sha_side_effect

        i1 = mock.Mock()
        if sanitize_env is None:
            sanitize_env = {
                "BASE_ENV": "preserved",
            }
        i1.sanitize_env.return_value = dict(
            sanitize_env
        )

        if storage_side_effect is None:
            i1.storage_db.side_effect = (
                lambda gate_repo, team, env:
                    Path(
                        fixture["root"]
                        / f"{team}.db"
                    )
            )
        else:
            i1.storage_db.side_effect = (
                storage_side_effect
            )

        if fault is None:
            fault = mock.Mock()
            fault.original_bytes = b"captured-original"
            fault.original_mode = 0o700
            fault.fault_digest = self.FAULT_DIGEST
            fault.restored_digest = (
                self.ORIGINAL_DIGEST
            )

        if run_case_side_effect is None:
            run_case_side_effect = (
                self.default_cases()
            )

        if shim_result is None:
            shim_result = [
                {
                    "schemaVersion": 1,
                    "argv": ["message-send"],
                }
            ]

        if write_count_side_effect is None:
            counts = {
                "request-control": 1,
                "request-fault": 0,
                "request-recovery": 1,
            }

            def write_count(
                db,
                *,
                team,
                request_id,
            ):
                return counts[
                    request_id
                ]

            write_count_side_effect = (
                write_count
            )

        script_dir = Path(
            F1.__file__
        ).resolve().parent

        def load_side_effect(
            path,
            name,
        ):
            path = Path(path)
            if path == (
                script_dir
                / "pilot-gate-isolation.py"
            ):
                self.assertEqual(
                    name,
                    "pilot_gate_isolation",
                )
                return iso

            if path == (
                script_dir
                / "pilot-gate-i1.py"
            ):
                self.assertEqual(
                    name,
                    "pilot_gate_i1",
                )
                return i1

            raise AssertionError(
                f"unexpected module load: {path} {name}"
            )

        stack = contextlib.ExitStack()

        patches = {
            "load_module":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "load_module",
                        side_effect=load_side_effect,
                    )
                ),
            "require_regular_executable":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "require_regular_executable",
                    )
                ),
            "ProviderFault":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "ProviderFault",
                        return_value=fault,
                    )
                ),
            "run_case":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "run_case",
                        side_effect=run_case_side_effect,
                    )
                ),
            "shim_invocations":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "shim_invocations",
                        return_value=shim_result,
                    )
                ),
            "delegate_write_count":
                stack.enter_context(
                    mock.patch.object(
                        F1,
                        "delegate_write_count",
                        side_effect=write_count_side_effect,
                    )
                ),
        }

        if atomic_json_side_effect is not None:
            patches[
                "atomic_json"
            ] = stack.enter_context(
                mock.patch.object(
                    F1,
                    "atomic_json",
                    side_effect=atomic_json_side_effect,
                )
            )

        try:
            yield {
                "iso": iso,
                "i1": i1,
                "fault": fault,
                **patches,
            }
        finally:
            stack.close()

    def read_result(
        self,
        fixture,
    ):
        return F1.read_json(
            fixture["artifact"]
            / "result.json"
        )

    def checks_by_name(
        self,
        result,
    ):
        return {
            item["name"]: item
            for item
            in result["checks"]
        }

    def test_setup_canonicalizes_paths_cleans_old_logs_validates_binaries_and_builds_fault_environment(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            artifact = fixture["artifact"]
            mutation_log = (
                artifact
                / "mutation-log.jsonl"
            )
            shim_log = (
                artifact
                / "fault-provider"
                / "shim-invocations.jsonl"
            )

            shim_log.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            mutation_log.write_text(
                "stale mutation\n",
                encoding="utf-8",
            )
            shim_log.write_text(
                "stale shim\n",
                encoding="utf-8",
            )

            with self.harness(
                fixture
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )
            self.assertTrue(
                artifact.is_dir()
            )
            self.assertFalse(
                mutation_log.exists()
            )
            self.assertFalse(
                shim_log.exists()
            )

            self.assertEqual(
                harness[
                    "iso"
                ].canonical.call_args_list,
                [
                    mock.call(
                        fixture[
                            "args"
                        ].gate_repo
                    ),
                    mock.call(
                        fixture[
                            "args"
                        ].run_root
                    ),
                    mock.call(
                        fixture[
                            "args"
                        ].claude_config
                    ),
                ],
            )

            self.assertEqual(
                harness[
                    "require_regular_executable"
                ].call_args_list,
                [
                    mock.call(
                        fixture["provider"]
                    ),
                    mock.call(
                        fixture["broker"]
                    ),
                    mock.call(
                        fixture["launcher"]
                    ),
                    mock.call(
                        fixture["join"]
                    ),
                ],
            )

            harness[
                "i1"
            ].sanitize_env.assert_called_once_with(
                os.environ
            )

            first_run = harness[
                "run_case"
            ].call_args_list[0]

            env = first_run.kwargs["env"]

            self.assertEqual(
                env["BASE_ENV"],
                "preserved",
            )
            self.assertEqual(
                env["CLAUDE_CONFIG_DIR"],
                str(
                    fixture[
                        "claude_config"
                    ]
                ),
            )
            self.assertEqual(
                env[
                    "AGMSG_GATE_F1_SHIM_LOG"
                ],
                str(shim_log),
            )

            harness[
                "ProviderFault"
            ].assert_called_once_with(
                provider=fixture["provider"],
                artifact=(
                    artifact
                    / "fault-provider"
                ),
                iso=harness["iso"],
                mutation_log=mutation_log,
            )

            self.assertEqual(
                harness[
                    "iso"
                ].sha256_file.call_args_list[
                    0
                ],
                mock.call(
                    fixture["provider"]
                ),
            )

    def test_provider_and_broker_real_paths_are_contained_under_gate_repo(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ):
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )

    def test_control_nonpass_returns_early_and_skips_fault_and_recovery(
        self,
    ):
        for verdict, expected_status in (
            ("fail", 1),
            ("unknown", 2),
        ):
            with self.subTest(
                verdict=verdict
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    control = self.case_record(
                        F1.CASE_CONTROL,
                        verdict=verdict,
                    )

                    with self.harness(
                        fixture,
                        run_case_side_effect=[
                            control
                        ],
                        sha_side_effect=[
                            self.ORIGINAL_DIGEST,
                            self.ORIGINAL_DIGEST,
                        ],
                    ) as harness:
                        status = F1.run_f1(
                            fixture["args"]
                        )

                    self.assertEqual(
                        status,
                        expected_status,
                    )
                    self.assertEqual(
                        harness[
                            "run_case"
                        ].call_count,
                        1,
                    )

                    result = self.read_result(
                        fixture
                    )

                    self.assertEqual(
                        result["verdict"],
                        verdict,
                    )
                    self.assertEqual(
                        result["reason"],
                        "control_not_pass",
                    )
                    self.assertEqual(
                        result["control"],
                        control,
                    )

                    harness[
                        "shim_invocations"
                    ].assert_not_called()

    def test_provider_not_restored_after_fault_is_unknown_exit_two_and_skips_recovery(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            control = self.case_record(
                F1.CASE_CONTROL
            )
            fault_case = self.case_record(
                F1.CASE_FAULT
            )

            with self.harness(
                fixture,
                run_case_side_effect=[
                    control,
                    fault_case,
                ],
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    "still-faulted",
                    self.ORIGINAL_DIGEST,
                ],
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )
            self.assertEqual(
                harness[
                    "run_case"
                ].call_count,
                2,
            )

            labels = [
                call.kwargs["label"]
                for call
                in harness[
                    "run_case"
                ].call_args_list
            ]

            self.assertEqual(
                labels,
                [
                    F1.CASE_CONTROL,
                    F1.CASE_FAULT,
                ],
            )

            result = self.read_result(
                fixture
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                (
                    "provider_not_restored_"
                    "before_recovery"
                ),
            )
            self.assertEqual(
                result["provider"],
                {
                    "originalDigest":
                        self.ORIGINAL_DIGEST,
                    "observedDigest":
                        "still-faulted",
                },
            )

    def test_post_count_exception_is_isolated_to_only_that_case(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            def storage(
                gate_repo,
                team,
                env,
            ):
                if team == "team-fault":
                    raise RuntimeError(
                        "fault db unavailable"
                    )

                return Path(
                    fixture["root"]
                    / f"{team}.db"
                )

            with self.harness(
                fixture,
                storage_side_effect=storage,
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )

            result = self.read_result(
                fixture
            )

            self.assertEqual(
                result[
                    "persistentWriteCountsAfterRecovery"
                ],
                {
                    F1.CASE_CONTROL: 1,
                    F1.CASE_FAULT: None,
                    F1.CASE_RECOVERY: 1,
                },
            )

            checks = self.checks_by_name(
                result
            )

            self.assertEqual(
                checks[
                    "control-write-count-one"
                ]["verdict"],
                "pass",
            )
            self.assertEqual(
                checks[
                    "fault-write-count-zero"
                ]["verdict"],
                "unknown",
            )
            self.assertEqual(
                checks[
                    "recovery-write-count-one"
                ]["verdict"],
                "pass",
            )

            self.assertEqual(
                harness[
                    "delegate_write_count"
                ].call_count,
                2,
            )

    def test_normal_result_contains_all_thirteen_passing_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ):
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )

            result = self.read_result(
                fixture
            )
            checks = self.checks_by_name(
                result
            )

            expected_names = {
                "request-ids-nonempty-and-distinct",
                "control-case-pass",
                "fault-case-pass",
                "recovery-case-pass",
                "control-write-count-one",
                "fault-write-count-zero",
                "recovery-write-count-one",
                "fault-provider-invoked-exactly-once",
                "fault-automatic-retry-count-zero",
                "fault-request-not-replayed-after-recovery",
                "provider-final-digest-restored",
                "fault-digest-different-from-original",
                "restore-digest-equals-original",
            }

            self.assertEqual(
                set(checks),
                expected_names,
            )
            self.assertEqual(
                len(checks),
                13,
            )
            self.assertTrue(
                all(
                    item["verdict"] == "pass"
                    for item in checks.values()
                )
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )

    def test_shim_unknown_makes_invocation_and_retry_checks_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ) as harness:
                harness[
                    "shim_invocations"
                ].return_value = None

                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                2,
            )

            checks = self.checks_by_name(
                self.read_result(
                    fixture
                )
            )

            self.assertEqual(
                checks[
                    "fault-provider-invoked-exactly-once"
                ]["verdict"],
                "unknown",
            )
            self.assertEqual(
                checks[
                    "fault-automatic-retry-count-zero"
                ]["verdict"],
                "unknown",
            )

    def test_wrong_control_post_count_is_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            def counts(
                db,
                *,
                team,
                request_id,
            ):
                return {
                    "request-control": 2,
                    "request-fault": 0,
                    "request-recovery": 1,
                }[
                    request_id
                ]

            with self.harness(
                fixture,
                write_count_side_effect=counts,
            ):
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            checks = self.checks_by_name(
                self.read_result(
                    fixture
                )
            )

            self.assertEqual(
                checks[
                    "control-write-count-one"
                ]["verdict"],
                "fail",
            )

    def test_duplicate_request_ids_fail_distinctness_check(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            cases = [
                self.case_record(
                    F1.CASE_CONTROL,
                    request_id="duplicate",
                ),
                self.case_record(
                    F1.CASE_FAULT,
                    request_id="duplicate",
                ),
                self.case_record(
                    F1.CASE_RECOVERY,
                    request_id="recovery-id",
                ),
            ]

            def counts(
                db,
                *,
                team,
                request_id,
            ):
                if team == "team-fault":
                    return 0
                return 1

            with self.harness(
                fixture,
                run_case_side_effect=cases,
                write_count_side_effect=counts,
            ):
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            checks = self.checks_by_name(
                self.read_result(
                    fixture
                )
            )

            self.assertEqual(
                checks[
                    "request-ids-nonempty-and-distinct"
                ]["verdict"],
                "fail",
            )

    def test_final_provider_digest_mismatch_is_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture,
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    self.ORIGINAL_DIGEST,
                    "wrong-final-digest",
                    self.ORIGINAL_DIGEST,
                ],
            ):
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            checks = self.checks_by_name(
                self.read_result(
                    fixture
                )
            )

            self.assertEqual(
                checks[
                    "provider-final-digest-restored"
                ]["verdict"],
                "fail",
            )

    def test_final_result_shape_and_pass_exit_code(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            control, fault_case, recovery = (
                self.default_cases()
            )

            with self.harness(
                fixture,
                run_case_side_effect=[
                    control,
                    fault_case,
                    recovery,
                ],
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )

            result = self.read_result(
                fixture
            )

            self.assertEqual(
                set(result),
                {
                    "schemaVersion",
                    "check",
                    "runId",
                    "faultMethod",
                    "controlRequestId",
                    "faultRequestId",
                    "recoveryRequestId",
                    "persistentWriteCountsAfterRecovery",
                    "faultProviderInvocationCount",
                    "automaticRetryCountForFaultRequest",
                    "providerDigests",
                    "control",
                    "fault",
                    "recovery",
                    "checks",
                    "verdict",
                },
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["check"],
                "F1",
            )
            self.assertEqual(
                result["runId"],
                self.RUN_ID,
            )
            self.assertEqual(
                result["faultMethod"],
                (
                    "isolated-provider-atomic-"
                    "shim-substitution"
                ),
            )
            self.assertEqual(
                result["controlRequestId"],
                "request-control",
            )
            self.assertEqual(
                result["faultRequestId"],
                "request-fault",
            )
            self.assertEqual(
                result["recoveryRequestId"],
                "request-recovery",
            )
            self.assertEqual(
                result[
                    "persistentWriteCountsAfterRecovery"
                ],
                {
                    F1.CASE_CONTROL: 1,
                    F1.CASE_FAULT: 0,
                    F1.CASE_RECOVERY: 1,
                },
            )
            self.assertEqual(
                result[
                    "faultProviderInvocationCount"
                ],
                1,
            )
            self.assertEqual(
                result[
                    "automaticRetryCountForFaultRequest"
                ],
                0,
            )
            self.assertEqual(
                result["providerDigests"],
                {
                    "original":
                        self.ORIGINAL_DIGEST,
                    "fault":
                        self.FAULT_DIGEST,
                    "restored":
                        self.ORIGINAL_DIGEST,
                    "final":
                        self.ORIGINAL_DIGEST,
                },
            )
            self.assertEqual(
                result["control"],
                control,
            )
            self.assertEqual(
                result["fault"],
                fault_case,
            )
            self.assertEqual(
                result["recovery"],
                recovery,
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )

            harness[
                "fault"
            ].restore.assert_not_called()

    def test_final_verdict_maps_pass_fail_unknown_to_zero_one_two(
        self,
    ):
        scenarios = (
            (
                "pass",
                {},
                0,
            ),
            (
                "fail",
                {
                    "write_count_side_effect":
                        lambda db, *,
                        team,
                        request_id:
                            (
                                2
                                if request_id
                                == "request-control"
                                else 0
                                if request_id
                                == "request-fault"
                                else 1
                            ),
                },
                1,
            ),
            (
                "unknown",
                {
                    "storage_side_effect":
                        lambda gate_repo,
                        team,
                        env:
                            (
                                (_ for _ in ())
                                .throw(
                                    RuntimeError(
                                        "unreadable"
                                    )
                                )
                                if team
                                == "team-control"
                                else Path(
                                    gate_repo
                                    / f"{team}.db"
                                )
                            ),
                },
                2,
            ),
        )

        for (
            expected_verdict,
            overrides,
            expected_status,
        ) in scenarios:
            with self.subTest(
                expected_verdict=(
                    expected_verdict
                )
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    with self.harness(
                        fixture,
                        **overrides,
                    ):
                        status = F1.run_f1(
                            fixture["args"]
                        )

                    self.assertEqual(
                        status,
                        expected_status,
                    )
                    self.assertEqual(
                        self.read_result(
                            fixture
                        )["verdict"],
                        expected_verdict,
                    )

    def test_normal_finally_matching_digest_does_not_restore(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture,
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    self.ORIGINAL_DIGEST,
                    self.ORIGINAL_DIGEST,
                    self.ORIGINAL_DIGEST,
                ],
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )
            harness[
                "fault"
            ].restore.assert_not_called()

    def test_exception_with_changed_digest_emergency_restores_when_original_was_captured(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fault = mock.Mock()
            fault.original_bytes = (
                b"captured"
            )
            fault.original_mode = 0o700
            fault.fault_digest = (
                self.FAULT_DIGEST
            )
            fault.restored_digest = (
                self.ORIGINAL_DIGEST
            )

            with self.harness(
                fixture,
                fault=fault,
                run_case_side_effect=(
                    RuntimeError(
                        "run-case exploded"
                    )
                ),
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    "changed-provider",
                ],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "run-case exploded",
                ):
                    F1.run_f1(
                        fixture["args"]
                    )

            fault.restore.assert_called_once()

    def test_emergency_restore_is_skipped_if_original_bytes_or_mode_was_not_captured(
        self,
    ):
        for (
            original_bytes,
            original_mode,
        ) in (
            (None, 0o700),
            (b"captured", None),
            (None, None),
        ):
            with self.subTest(
                original_bytes=(
                    original_bytes
                ),
                original_mode=(
                    original_mode
                ),
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    fault = mock.Mock()
                    fault.original_bytes = (
                        original_bytes
                    )
                    fault.original_mode = (
                        original_mode
                    )
                    fault.fault_digest = (
                        self.FAULT_DIGEST
                    )
                    fault.restored_digest = ""

                    with self.harness(
                        fixture,
                        fault=fault,
                        run_case_side_effect=(
                            RuntimeError(
                                "before-inject"
                            )
                        ),
                        sha_side_effect=[
                            self.ORIGINAL_DIGEST,
                            "changed-provider",
                        ],
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "before-inject",
                        ):
                            F1.run_f1(
                                fixture["args"]
                            )

                    fault.restore.assert_not_called()

    def test_emergency_restore_failure_writes_unknown_evidence_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fault = mock.Mock()
            fault.original_bytes = b"captured"
            fault.original_mode = 0o700
            fault.fault_digest = (
                self.FAULT_DIGEST
            )
            fault.restored_digest = ""
            fault.restore.side_effect = (
                RuntimeError(
                    "restore failed"
                )
            )

            with self.harness(
                fixture,
                fault=fault,
                run_case_side_effect=(
                    ValueError(
                        "original failure"
                    )
                ),
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    "changed-provider",
                ],
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "original failure",
                ):
                    F1.run_f1(
                        fixture["args"]
                    )

            fault.restore.assert_called_once()

            evidence = F1.read_json(
                fixture["artifact"]
                / "emergency-restore-error.json"
            )

            self.assertEqual(
                evidence,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason":
                        (
                            "emergency_restore_failed:"
                            "RuntimeError:"
                            "restore failed"
                        ),
                },
            )

    def test_emergency_evidence_write_failure_is_swallowed_and_original_exception_survives(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fault = mock.Mock()
            fault.original_bytes = b"captured"
            fault.original_mode = 0o700
            fault.fault_digest = (
                self.FAULT_DIGEST
            )
            fault.restored_digest = ""
            fault.restore.side_effect = (
                RuntimeError(
                    "restore failed"
                )
            )

            real_atomic = F1.atomic_json

            def atomic_side_effect(
                path,
                value,
            ):
                if Path(path).name == (
                    "emergency-restore-error.json"
                ):
                    raise OSError(
                        "evidence write failed"
                    )
                return real_atomic(
                    path,
                    value,
                )

            with self.harness(
                fixture,
                fault=fault,
                run_case_side_effect=(
                    LookupError(
                        "original lookup failure"
                    )
                ),
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    "changed-provider",
                ],
                atomic_json_side_effect=(
                    atomic_side_effect
                ),
            ):
                with self.assertRaisesRegex(
                    LookupError,
                    "original lookup failure",
                ):
                    F1.run_f1(
                        fixture["args"]
                    )

            fault.restore.assert_called_once()
            self.assertFalse(
                (
                    fixture["artifact"]
                    / "emergency-restore-error.json"
                ).exists()
            )

    def test_finally_sha256_failure_falls_back_to_empty_digest_and_attempts_restore(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fault = mock.Mock()
            fault.original_bytes = b"captured"
            fault.original_mode = 0o700
            fault.fault_digest = (
                self.FAULT_DIGEST
            )
            fault.restored_digest = ""

            with self.harness(
                fixture,
                fault=fault,
                run_case_side_effect=(
                    RuntimeError(
                        "run-case failure"
                    )
                ),
                sha_side_effect=[
                    self.ORIGINAL_DIGEST,
                    OSError(
                        "digest unavailable"
                    ),
                ],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "run-case failure",
                ):
                    F1.run_f1(
                        fixture["args"]
                    )

            fault.restore.assert_called_once()

    def test_run_case_receives_control_fault_recovery_provider_fault_contract(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ) as harness:
                status = F1.run_f1(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )

            calls = harness[
                "run_case"
            ].call_args_list

            self.assertEqual(
                len(calls),
                3,
            )

            self.assertEqual(
                [
                    call.kwargs["label"]
                    for call in calls
                ],
                [
                    F1.CASE_CONTROL,
                    F1.CASE_FAULT,
                    F1.CASE_RECOVERY,
                ],
            )

            self.assertIsNone(
                calls[0].kwargs[
                    "provider_fault"
                ]
            )
            self.assertIs(
                calls[1].kwargs[
                    "provider_fault"
                ],
                harness["fault"],
            )
            self.assertIsNone(
                calls[2].kwargs[
                    "provider_fault"
                ]
            )

            for call in calls:
                self.assertEqual(
                    call.kwargs[
                        "base_run_id"
                    ],
                    self.RUN_ID,
                )
                self.assertEqual(
                    call.kwargs[
                        "base_team"
                    ],
                    self.GATE_TEAM,
                )
                self.assertEqual(
                    call.kwargs[
                        "gate_repo"
                    ],
                    fixture["gate_repo"],
                )
                self.assertEqual(
                    call.kwargs[
                        "run_root"
                    ],
                    fixture["run_root"],
                )
                self.assertEqual(
                    call.kwargs[
                        "claude_config"
                    ],
                    fixture[
                        "claude_config"
                    ],
                )
                self.assertEqual(
                    call.kwargs[
                        "artifact_root"
                    ],
                    fixture["artifact"],
                )
                self.assertEqual(
                    call.kwargs[
                        "timeout_seconds"
                    ],
                    17.0,
                )

if __name__ == "__main__":
    unittest.main()
