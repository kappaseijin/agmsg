"""Round A unit tests for scripts/lib/pilot-gate-i1.py."""

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
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
I1_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_I1_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-i1.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_i1",
    I1_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate I1 helper: {I1_HELPER}"
    )

I1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I1)


class PilotGateI1RoundA(unittest.TestCase):
    def resolved_temp_root(
        self,
        temporary: tempfile.TemporaryDirectory[str],
    ) -> Path:
        return Path(temporary.name).resolve()

    def test_load_iso_loads_sidecar_module_with_known_attribute(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            script_dir = root / "lib"
            script_dir.mkdir()

            isolation = script_dir / "pilot-gate-isolation.py"
            isolation.write_text(
                (
                    "def canonical(path, strict=True):\n"
                    "    return 'canonical:' + str(path)\n"
                ),
                encoding="utf-8",
            )

            module = I1.load_iso(script_dir)

            self.assertTrue(
                callable(module.canonical)
            )
            self.assertEqual(
                module.canonical("value"),
                "canonical:value",
            )

    def test_load_iso_missing_sidecar_raises_file_not_found_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            missing = root / "missing"

            with self.assertRaises(
                FileNotFoundError
            ):
                I1.load_iso(missing)

    def test_atomic_json_creates_parent_uses_pid_tmp_and_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = (
                root
                / "missing"
                / "nested"
                / "value.json"
            )
            value = {
                "z": 1,
                "日本語": "河童",
                "a": {
                    "enabled": True,
                },
            }

            real_replace = os.replace

            with mock.patch.object(
                I1.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                I1.atomic_json(
                    output,
                    value,
                )

            replace_mock.assert_called_once()

            source, destination = (
                replace_mock.call_args.args
            )

            expected_tmp = output.with_name(
                f".{output.name}.{os.getpid()}.tmp"
            )

            self.assertEqual(
                Path(source),
                expected_tmp,
            )
            self.assertEqual(
                Path(destination),
                output,
            )
            self.assertFalse(
                expected_tmp.exists()
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
            self.assertTrue(
                raw.endswith("\n")
            )
            self.assertFalse(
                raw.endswith("\n\n")
            )
            self.assertIn(
                "日本語",
                raw,
            )
            self.assertNotIn(
                r"\u65e5",
                raw,
            )

    def test_atomic_json_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "value.json"
            output.write_text(
                '{"old":true}\n',
                encoding="utf-8",
            )

            I1.atomic_json(
                output,
                {
                    "new": "value",
                },
            )

            self.assertEqual(
                I1.read_json(output),
                {
                    "new": "value",
                },
            )

    def test_append_jsonl_creates_parent_and_appends_compact_unicode_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = (
                root
                / "missing"
                / "events.jsonl"
            )

            I1.append_jsonl(
                output,
                {
                    "kind": "first",
                    "日本語": "河童",
                },
            )

            I1.append_jsonl(
                output,
                {
                    "kind": "second",
                    "value": 2,
                },
            )

            raw = output.read_text(
                encoding="utf-8"
            )

            self.assertEqual(
                raw.splitlines(),
                [
                    (
                        '{"kind":"first",'
                        '"日本語":"河童"}'
                    ),
                    (
                        '{"kind":"second",'
                        '"value":2}'
                    ),
                ],
            )
            self.assertNotIn(
                ": ",
                raw,
            )
            self.assertNotIn(
                ", ",
                raw,
            )
            self.assertTrue(
                raw.endswith("\n")
            )

    def test_read_json_round_trips_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "value.json"
            value = {
                "schemaVersion": 1,
                "text": "hello",
            }

            path.write_text(
                json.dumps(value),
                encoding="utf-8",
            )

            self.assertEqual(
                I1.read_json(path),
                value,
            )

    def test_verdict_from_assertions_uses_fail_unknown_pass_priority(self):
        passed = I1.assertion(
            "pass",
            True,
            None,
        )
        failed = I1.assertion(
            "fail",
            False,
            None,
        )
        unknown = I1.assertion(
            "unknown",
            None,
            None,
        )

        self.assertEqual(
            I1.verdict_from_assertions(
                [
                    passed,
                    unknown,
                    failed,
                ]
            ),
            "fail",
        )
        self.assertEqual(
            I1.verdict_from_assertions(
                [
                    passed,
                    unknown,
                ]
            ),
            "unknown",
        )
        self.assertEqual(
            I1.verdict_from_assertions(
                [
                    passed,
                    passed,
                ]
            ),
            "pass",
        )
        self.assertEqual(
            I1.verdict_from_assertions(
                []
            ),
            "pass",
        )

    def test_assertion_maps_tristate_without_number_field(self):
        detail = {
            "source": "fixture",
        }

        cases = (
            (True, "pass"),
            (False, "fail"),
            (None, "unknown"),
        )

        for result, verdict in cases:
            with self.subTest(
                result=result
            ):
                self.assertEqual(
                    I1.assertion(
                        "example",
                        result,
                        detail,
                    ),
                    {
                        "name": "example",
                        "verdict": verdict,
                        "detail": detail,
                    },
                )

    def test_run_passes_stdin_and_captures_stdout_stderr(self):
        result = I1.run(
            ["cat"],
            stdin="hello\n",
        )

        self.assertEqual(
            result.returncode,
            0,
        )
        self.assertEqual(
            result.stdout,
            "hello\n",
        )
        self.assertEqual(
            result.stderr,
            "",
        )

        nonzero = I1.run(
            [
                "sh",
                "-c",
                (
                    "printf 'out\\n'; "
                    "printf 'err\\n' >&2; "
                    "exit 3"
                ),
            ]
        )

        self.assertEqual(
            nonzero.returncode,
            3,
        )
        self.assertEqual(
            nonzero.stdout,
            "out\n",
        )
        self.assertEqual(
            nonzero.stderr,
            "err\n",
        )

    def test_run_honors_cwd_and_env(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = os.environ.copy()
            env[
                "PILOT_GATE_I1_TEST_VALUE"
            ] = "value"

            result = I1.run(
                [
                    "sh",
                    "-c",
                    (
                        "printf '%s\\n' \"$PWD\"; "
                        "printf '%s\\n' "
                        "\"$PILOT_GATE_I1_TEST_VALUE\""
                    ),
                ],
                cwd=root,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            lines = result.stdout.splitlines()

            self.assertEqual(
                Path(lines[0]).resolve(),
                root,
            )
            self.assertEqual(
                lines[1],
                "value",
            )

    def test_require_regular_executable_accepts_only_regular_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            executable = root / "executable"
            executable.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)

            I1.require_regular_executable(
                executable
            )

            no_exec = root / "no-exec"
            no_exec.write_text(
                "not executable\n",
                encoding="utf-8",
            )
            no_exec.chmod(0o600)

            with self.assertRaises(
                RuntimeError
            ):
                I1.require_regular_executable(
                    no_exec
                )

            directory = root / "directory"
            directory.mkdir()
            directory.chmod(0o700)

            with self.assertRaises(
                RuntimeError
            ):
                I1.require_regular_executable(
                    directory
                )

            symlink = root / "symlink"

            try:
                symlink.symlink_to(
                    executable
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            with self.assertRaises(
                RuntimeError
            ):
                I1.require_regular_executable(
                    symlink
                )

    def test_sanitize_env_removes_all_credentials_without_mutating_input(self):
        base = {
            "PATH": "/bin",
            "KEEP": "value",
            "GH_TOKEN": "one",
            "GITHUB_TOKEN": "two",
            "GH_ENTERPRISE_TOKEN": "three",
            "GITHUB_ENTERPRISE_TOKEN": "four",
        }

        original = dict(base)

        sanitized = I1.sanitize_env(
            base
        )

        self.assertEqual(
            base,
            original,
        )
        self.assertIsNot(
            sanitized,
            base,
        )

        for key in I1.CREDENTIAL_ENV:
            self.assertNotIn(
                key,
                sanitized,
            )

        self.assertEqual(
            sanitized["PATH"],
            "/bin",
        )
        self.assertEqual(
            sanitized["KEEP"],
            "value",
        )

    def test_parse_last_json_returns_last_object_and_ignores_other_json_types(self):
        text = (
            "\n"
            '{"first":1}\n'
            "[1,2,3]\n"
            '"literal"\n'
            "not-json\n"
            '{"last":2}\n'
            "\n"
        )

        self.assertEqual(
            I1.parse_last_json(text),
            {
                "last": 2,
            },
        )

        self.assertIsNone(
            I1.parse_last_json(
                "\n[1,2]\n\"text\"\ninvalid\n"
            )
        )

    def test_json_content_text_handles_string_list_dict_and_ignores_other_values(
        self,
    ):
        self.assertEqual(
            I1.json_content_text(
                "plain"
            ),
            "plain",
        )

        self.assertEqual(
            I1.json_content_text(
                [
                    "first",
                    {
                        "text": "second",
                    },
                    42,
                    {
                        "other": "ignored",
                    },
                    {
                        "text": 123,
                    },
                    "third",
                ]
            ),
            "first\nsecond\nthird",
        )

        self.assertEqual(
            I1.json_content_text(
                {
                    "text": "dict-text",
                }
            ),
            "dict-text",
        )

        for value in (
            None,
            42,
            {
                "text": 123,
            },
            {
                "other": "value",
            },
        ):
            with self.subTest(
                value=value
            ):
                self.assertEqual(
                    I1.json_content_text(
                        value
                    ),
                    "",
                )

    def test_walk_json_yields_dict_nodes_but_not_list_nodes(self):
        root = {
            "top": 1,
            "nested": {
                "middle": True,
                "items": [
                    {
                        "leaf": "a",
                    },
                    [
                        {
                            "leaf": "b",
                        }
                    ],
                ],
            },
        }

        nodes = list(
            I1.walk_json(root)
        )

        self.assertEqual(
            nodes,
            [
                root,
                root["nested"],
                root["nested"]["items"][0],
                root["nested"]["items"][1][0],
            ],
        )

        self.assertTrue(
            all(
                isinstance(node, dict)
                for node in nodes
            )
        )

    def test_transcript_matches_supports_content_and_filename_and_deduplicates_resolved_paths(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / "claude"
            config.mkdir()

            session_id = (
                "123e4567-e89b-42d3-a456-"
                "426614174000"
            )

            content_only = (
                config
                / "content-only.jsonl"
            )
            content_only.write_text(
                (
                    '{"nested":{"session_id":"'
                    + session_id
                    + '"}}\n'
                ),
                encoding="utf-8",
            )

            filename_only = (
                config
                / f"{session_id}-filename.jsonl"
            )
            filename_only.write_text(
                '{"unrelated":true}\n',
                encoding="utf-8",
            )

            unrelated = (
                config
                / "unrelated.jsonl"
            )
            unrelated.write_text(
                '{"sessionId":"other"}\n',
                encoding="utf-8",
            )

            matches = I1.transcript_matches(
                config,
                session_id,
            )

            self.assertEqual(
                {
                    path.resolve()
                    for path in matches
                },
                {
                    content_only.resolve(),
                    filename_only.resolve(),
                },
            )

            self.assertEqual(
                I1.transcript_matches(
                    root / "missing",
                    session_id,
                ),
                [],
            )

    def test_transcript_matches_ignores_symlink_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / "claude"
            config.mkdir()

            session_id = "session-1"

            real = (
                root
                / "outside.jsonl"
            )
            real.write_text(
                (
                    '{"sessionId":"'
                    + session_id
                    + '"}\n'
                ),
                encoding="utf-8",
            )

            link = (
                config
                / "linked.jsonl"
            )

            try:
                link.symlink_to(
                    real
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            self.assertEqual(
                I1.transcript_matches(
                    config,
                    session_id,
                ),
                [],
            )

    def test_find_tool_result_finds_bash_use_and_content_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = (
                root
                / "transcript.jsonl"
            )
            command = (
                "/tmp/broker --config /tmp/config "
                "receive < /tmp/request"
            )

            records = [
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-123",
                                "name": "Bash",
                                "input": {
                                    "command": command,
                                },
                            }
                        ]
                    }
                },
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-123",
                                "content": [
                                    {
                                        "text": '{"state":"ok"}',
                                    },
                                    "tail",
                                ],
                            }
                        ]
                    }
                },
            ]

            transcript.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in records
                )
                + "\n",
                encoding="utf-8",
            )

            tool_id, result = (
                I1.find_tool_result(
                    transcript,
                    command,
                )
            )

            self.assertEqual(
                tool_id,
                "tool-123",
            )
            self.assertEqual(
                result,
                '{"state":"ok"}\ntail',
            )

    def test_find_tool_result_supports_text_fallback_and_missing_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = (
                root
                / "transcript.jsonl"
            )
            command = "echo test"

            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Bash",
                                "input": {
                                    "command": command,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": None,
                                "text": "fallback-text",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                I1.find_tool_result(
                    transcript,
                    command,
                ),
                (
                    "tool-1",
                    "fallback-text",
                ),
            )

            transcript.write_text(
                json.dumps(
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {
                            "command": command,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                I1.find_tool_result(
                    transcript,
                    command,
                ),
                (
                    "tool-1",
                    None,
                ),
            )

            self.assertEqual(
                I1.find_tool_result(
                    transcript,
                    "different-command",
                ),
                (
                    None,
                    None,
                ),
            )

    def test_hook_decision_returns_last_matching_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            decisions = (
                root
                / "decisions.jsonl"
            )

            decisions.write_text(
                "\n".join(
                    [
                        "{invalid-json}",
                        json.dumps(
                            {
                                "toolUseId": "other",
                                "decision": "deny",
                            }
                        ),
                        json.dumps(
                            {
                                "toolUseId": "tool-1",
                                "decision": "deny",
                            }
                        ),
                        json.dumps(
                            {
                                "toolUseId": "tool-1",
                                "decision": "allow",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                I1.hook_decision(
                    decisions,
                    "tool-1",
                ),
                "allow",
            )

            self.assertIsNone(
                I1.hook_decision(
                    decisions,
                    "missing",
                )
            )

            self.assertIsNone(
                I1.hook_decision(
                    root / "absent.jsonl",
                    "tool-1",
                )
            )

    def test_broker_pilot_and_binding_paths_have_expected_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            team = "gate-team"
            run_id = "run-123"

            key = hashlib.sha256(
                run_id.encode("utf-8")
            ).hexdigest()

            seat = (
                gate_repo
                / "run"
                / "pilot"
                / f"{team}__{I1.PILOT_AGENT}"
            )

            self.assertEqual(
                I1.broker_state_path(
                    gate_repo,
                    team,
                    run_id,
                ),
                (
                    seat
                    / "broker-state"
                    / f"{key}.json"
                ),
            )

            self.assertEqual(
                I1.pilot_state_path(
                    gate_repo,
                    team,
                ),
                seat / "state.json",
            )

            self.assertEqual(
                I1.binding_for_generation(
                    gate_repo,
                    team,
                    7,
                ),
                (
                    seat
                    / "bindings"
                    / "7.json"
                ),
            )

    def test_current_generation_handles_absent_positive_int_and_digit_string(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            team = "gate-team"

            self.assertEqual(
                I1.current_generation(
                    gate_repo,
                    team,
                ),
                0,
            )

            state = I1.pilot_state_path(
                gate_repo,
                team,
            )

            I1.atomic_json(
                state,
                {
                    "latestGeneration": 3,
                },
            )

            self.assertEqual(
                I1.current_generation(
                    gate_repo,
                    team,
                ),
                3,
            )

            I1.atomic_json(
                state,
                {
                    "latestGeneration": "4",
                },
            )

            self.assertEqual(
                I1.current_generation(
                    gate_repo,
                    team,
                ),
                4,
            )

            # Current implementation intentionally follows str.isdigit().
            I1.atomic_json(
                state,
                {
                    "latestGeneration": "01",
                },
            )

            self.assertEqual(
                I1.current_generation(
                    gate_repo,
                    team,
                ),
                1,
            )

    def test_current_generation_rejects_unidentifiable_state(self):
        invalid_values = (
            0,
            -1,
            "0",
            "-1",
            "abc",
            "",
            None,
        )

        for value in invalid_values:
            with self.subTest(
                value=value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    gate_repo = root / "repo"
                    team = "gate-team"

                    I1.atomic_json(
                        I1.pilot_state_path(
                            gate_repo,
                            team,
                        ),
                        {
                            "latestGeneration": value,
                        },
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        (
                            "pilot latestGeneration "
                            "unidentifiable"
                        ),
                    ):
                        I1.current_generation(
                            gate_repo,
                            team,
                        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            team = "gate-team"

            for value in (
                {},
                [],
            ):
                with self.subTest(
                    root_value=value
                ):
                    I1.atomic_json(
                        I1.pilot_state_path(
                            gate_repo,
                            team,
                        ),
                        value,
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        (
                            "pilot latestGeneration "
                            "unidentifiable"
                        ),
                    ):
                        I1.current_generation(
                            gate_repo,
                            team,
                        )

    def test_make_request_builds_common_fields_stringifies_generation_and_adds_extra(
        self,
    ):
        request = I1.make_request(
            "run-1",
            "request-1",
            "delegate",
            "gate-team",
            7,
            worker="worker-1",
            task="do work",
        )

        self.assertEqual(
            request,
            {
                "schemaVersion": 1,
                "runId": "run-1",
                "requestId": "request-1",
                "operation": "delegate",
                "team": "gate-team",
                "actor": I1.PILOT_AGENT,
                "generation": "7",
                "worker": "worker-1",
                "task": "do work",
            },
        )

    def test_exact_broker_command_returns_fixed_format_for_safe_paths(self):
        broker = Path(
            "/tmp/gate/scripts/p2-consumer-broker.sh"
        )
        config = Path(
            "/tmp/gate/config:i1.json"
        )
        request = Path(
            "/tmp/gate/request_1.json"
        )

        self.assertEqual(
            I1.exact_broker_command(
                broker,
                config,
                "receive",
                request,
            ),
            (
                f"{broker} --config "
                f"{config} receive < {request}"
            ),
        )

    def test_exact_broker_command_rejects_unsafe_path_tokens(self):
        safe = Path(
            "/tmp/gate/file.json"
        )

        unsafe_values = (
            Path("/tmp/gate/with space.json"),
            Path("/tmp/gate/file;rm"),
            Path("/tmp/gate/file$(id)"),
        )

        for unsafe in unsafe_values:
            for position in (
                "broker",
                "config",
                "request",
            ):
                with self.subTest(
                    unsafe=str(unsafe),
                    position=position,
                ):
                    broker = (
                        unsafe
                        if position == "broker"
                        else safe
                    )
                    config = (
                        unsafe
                        if position == "config"
                        else safe
                    )
                    request = (
                        unsafe
                        if position == "request"
                        else safe
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        (
                            "I1 paths contain shell "
                            "metacharacters/whitespace"
                        ),
                    ):
                        I1.exact_broker_command(
                            broker,
                            config,
                            "receive",
                            request,
                        )

    def test_write_request_is_atomic_json_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "request.json"
            value = {
                "requestId": "request-1",
            }

            with mock.patch.object(
                I1,
                "atomic_json",
            ) as atomic_mock:
                I1.write_request(
                    output,
                    value,
                )

            atomic_mock.assert_called_once_with(
                output,
                value,
            )

    def test_parse_provider_json_accepts_last_object_and_rejects_failures(
        self,
    ):
        success = subprocess.CompletedProcess(
            args=["provider"],
            returncode=0,
            stdout=(
                "noise\n"
                '{"first":1}\n'
                "[1,2]\n"
                '{"schemaVersion":1}\n'
            ),
            stderr="",
        )

        self.assertEqual(
            I1.parse_provider_json(
                success
            ),
            {
                "schemaVersion": 1,
            },
        )

        failed = subprocess.CompletedProcess(
            args=["provider"],
            returncode=3,
            stdout="",
            stderr="backend failed",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "provider failed rc=3",
        ):
            I1.parse_provider_json(
                failed
            )

        unidentifiable = (
            subprocess.CompletedProcess(
                args=["provider"],
                returncode=0,
                stdout=(
                    "noise\n"
                    "[1,2,3]\n"
                ),
                stderr="",
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "provider response "
                "unidentifiable"
            ),
        ):
            I1.parse_provider_json(
                unidentifiable
            )

    def test_register_fixture_member_passes_expected_argv_env_and_logs_mutation(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            scripts = gate_repo / "scripts"
            scripts.mkdir(parents=True)

            args_log = root / "join-args.txt"
            env_log = root / "join-env.txt"

            join = scripts / "join.sh"
            join.write_text(
                (
                    "#!/bin/sh\n"
                    "printf '%s\\n' \"$@\" > "
                    "\"$TEST_JOIN_ARGS_LOG\"\n"
                    "printf '%s\\n' "
                    "\"${AGMSG_RESOLVE_PROJECT-}\" > "
                    "\"$TEST_JOIN_ENV_LOG\"\n"
                    "exit 0\n"
                ),
                encoding="utf-8",
            )
            join.chmod(0o700)

            project = (
                gate_repo
                / ".agmsg-gate"
                / "worker"
            )
            mutation_log = (
                root
                / "mutation.jsonl"
            )

            env = os.environ.copy()
            env[
                "TEST_JOIN_ARGS_LOG"
            ] = str(args_log)
            env[
                "TEST_JOIN_ENV_LOG"
            ] = str(env_log)
            env[
                "AGMSG_RESOLVE_PROJECT"
            ] = "should-be-overridden"

            I1.register_fixture_member(
                gate_repo,
                "gate-team",
                "worker-agent",
                project,
                "worker",
                env,
                mutation_log,
            )

            self.assertTrue(
                project.is_dir()
            )

            self.assertEqual(
                args_log.read_text(
                    encoding="utf-8"
                ).splitlines(),
                [
                    "gate-team",
                    "worker-agent",
                    I1.PILOT_TYPE,
                    str(project),
                    "--role",
                    "worker",
                    "--kind",
                    "service",
                ],
            )

            self.assertEqual(
                env_log.read_text(
                    encoding="utf-8"
                ),
                "0\n",
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

            record = records[0]

            self.assertEqual(
                record["kind"],
                "team-registration",
            )
            self.assertEqual(
                record["team"],
                "gate-team",
            )
            self.assertEqual(
                record["target"],
                str(project),
            )
            self.assertEqual(
                record["argv"],
                [
                    str(join),
                    "gate-team",
                    "worker-agent",
                    I1.PILOT_TYPE,
                    str(project),
                    "--role",
                    "worker",
                    "--kind",
                    "service",
                ],
            )

    def test_register_fixture_member_failure_raises_and_still_logs_attempt(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            scripts = gate_repo / "scripts"
            scripts.mkdir(parents=True)

            join = scripts / "join.sh"
            join.write_text(
                (
                    "#!/bin/sh\n"
                    "printf 'join rejected\\n' >&2\n"
                    "exit 1\n"
                ),
                encoding="utf-8",
            )
            join.chmod(0o700)

            mutation_log = (
                root
                / "mutation.jsonl"
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "join failed for worker-agent",
            ):
                I1.register_fixture_member(
                    gate_repo,
                    "gate-team",
                    "worker-agent",
                    gate_repo / "project",
                    "worker",
                    os.environ.copy(),
                    mutation_log,
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
            self.assertEqual(
                records[0]["kind"],
                "team-registration",
            )

    def test_provider_call_returns_json_and_logs_only_mutating_calls(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()

            args_log = root / "provider-args.txt"

            provider = root / "provider"
            provider.write_text(
                (
                    "#!/bin/sh\n"
                    "printf '%s\\n' \"$@\" > "
                    "\"$TEST_PROVIDER_ARGS_LOG\"\n"
                    "printf '%s\\n' "
                    "'{\"schemaVersion\":1,"
                    "\"state\":\"ok\"}'\n"
                ),
                encoding="utf-8",
            )
            provider.chmod(0o700)

            env = os.environ.copy()
            env[
                "TEST_PROVIDER_ARGS_LOG"
            ] = str(args_log)

            mutation_log = (
                root
                / "mutation.jsonl"
            )

            value = I1.provider_call(
                provider,
                [
                    "message-send",
                    "gate-team",
                    "sender",
                ],
                gate_repo,
                env,
                mutation_log,
                True,
            )

            self.assertEqual(
                value,
                {
                    "schemaVersion": 1,
                    "state": "ok",
                },
            )

            self.assertEqual(
                args_log.read_text(
                    encoding="utf-8"
                ).splitlines(),
                [
                    "message-send",
                    "gate-team",
                    "sender",
                ],
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
                        "kind": "provider",
                        "argv": [
                            str(provider),
                            "message-send",
                            "gate-team",
                            "sender",
                        ],
                        "team": "gate-team",
                    }
                ],
            )

            mutation_log.unlink()

            I1.provider_call(
                provider,
                [
                    "message-peek",
                    "gate-team",
                ],
                gate_repo,
                env,
                mutation_log,
                False,
            )

            self.assertFalse(
                mutation_log.exists()
            )

    def test_provider_call_nonzero_exit_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            gate_repo.mkdir()

            provider = root / "provider"
            provider.write_text(
                (
                    "#!/bin/sh\n"
                    "printf 'provider failed\\n' >&2\n"
                    "exit 4\n"
                ),
                encoding="utf-8",
            )
            provider.chmod(0o700)

            with self.assertRaisesRegex(
                RuntimeError,
                "provider failed rc=4",
            ):
                I1.provider_call(
                    provider,
                    [
                        "message-peek",
                        "gate-team",
                    ],
                    gate_repo,
                    os.environ.copy(),
                )

    def test_storage_db_sources_storage_helper_and_returns_contained_database(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            storage_dir = (
                gate_repo
                / "scripts"
                / "lib"
            )
            storage_dir.mkdir(
                parents=True
            )

            db = (
                gate_repo
                / "storage"
                / "gate-team"
                / "messages.db"
            )
            db.parent.mkdir(
                parents=True
            )
            db.touch()

            storage = (
                storage_dir
                / "storage.sh"
            )
            storage.write_text(
                (
                    "agmsg_storage_load() { :; }\n"
                    "agmsg_db_path() {\n"
                    "  printf '%s\\n' "
                    "\"$AGMSG_TEST_DB\"\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["AGMSG_TEST_DB"] = str(
                db
            )

            self.assertEqual(
                I1.storage_db(
                    gate_repo,
                    "gate-team",
                    env,
                ),
                db.resolve(),
            )

    def test_storage_db_rejects_database_outside_gate_repository(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            storage_dir = (
                gate_repo
                / "scripts"
                / "lib"
            )
            storage_dir.mkdir(
                parents=True
            )

            outside = (
                root
                / "outside"
                / "messages.db"
            )
            outside.parent.mkdir(
                parents=True
            )
            outside.touch()

            storage = (
                storage_dir
                / "storage.sh"
            )
            storage.write_text(
                (
                    "agmsg_storage_load() { :; }\n"
                    "agmsg_db_path() {\n"
                    "  printf '%s\\n' "
                    "\"$AGMSG_TEST_DB\"\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["AGMSG_TEST_DB"] = str(
                outside
            )

            with self.assertRaises(
                ValueError
            ):
                I1.storage_db(
                    gate_repo,
                    "gate-team",
                    env,
                )

    def test_storage_db_rejects_unresolvable_storage_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate_repo = root / "repo"
            storage_dir = (
                gate_repo
                / "scripts"
                / "lib"
            )
            storage_dir.mkdir(
                parents=True
            )

            storage = (
                storage_dir
                / "storage.sh"
            )
            storage.write_text(
                (
                    "agmsg_storage_load() { return 1; }\n"
                    "agmsg_db_path() { :; }\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "cannot resolve gate "
                    "team storage db"
                ),
            ):
                I1.storage_db(
                    gate_repo,
                    "gate-team",
                    os.environ.copy(),
                )

    def make_receipt_db(
        self,
        path: Path,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            path
        )

        connection.executescript(
            """
            CREATE TABLE events (
                legacy_id INTEGER,
                type TEXT,
                team TEXT,
                id TEXT
            );

            CREATE TABLE message_receipts (
                message_id INTEGER,
                owner TEXT,
                evidence TEXT
            );
            """
        )

        return connection

    def test_receipt_count_returns_zero_one_and_zero_for_mismatched_conditions(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            db = root / "messages.db"

            connection = (
                self.make_receipt_db(
                    db
                )
            )

            connection.execute(
                (
                    "INSERT INTO events "
                    "(legacy_id, type, team, id) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (
                    10,
                    "message_sent",
                    "gate-team",
                    "event-1",
                ),
            )

            connection.execute(
                (
                    "INSERT INTO "
                    "message_receipts "
                    "(message_id, owner, evidence) "
                    "VALUES (?, ?, ?)"
                ),
                (
                    10,
                    "owner-1",
                    "receipt-1",
                ),
            )

            connection.commit()
            connection.close()

            self.assertEqual(
                I1.receipt_count(
                    db,
                    "gate-team",
                    "event-1",
                    "owner-1",
                    "receipt-1",
                ),
                1,
            )

            self.assertEqual(
                I1.receipt_count(
                    db,
                    "gate-team",
                    "missing-event",
                    "owner-1",
                    "receipt-1",
                ),
                0,
            )

            self.assertEqual(
                I1.receipt_count(
                    db,
                    "wrong-team",
                    "event-1",
                    "owner-1",
                    "receipt-1",
                ),
                0,
            )

            self.assertEqual(
                I1.receipt_count(
                    db,
                    "gate-team",
                    "event-1",
                    "wrong-owner",
                    "receipt-1",
                ),
                0,
            )

            self.assertEqual(
                I1.receipt_count(
                    db,
                    "gate-team",
                    "event-1",
                    "owner-1",
                    "wrong-evidence",
                ),
                0,
            )

    def test_expected_common_returns_seven_assertions_and_detects_mismatch(
        self,
    ):
        value = {
            "schemaVersion": 1,
            "runId": "run-1",
            "requestId": "request-1",
            "operation": "receive",
            "team": "gate-team",
            "actor": I1.PILOT_AGENT,
            "generation": 7,
        }

        checks = I1.expected_common(
            value,
            run_id="run-1",
            request_id="request-1",
            operation="receive",
            team="gate-team",
            generation=7,
        )

        self.assertEqual(
            len(checks),
            7,
        )

        self.assertEqual(
            [
                item["name"]
                for item
                in checks
            ],
            [
                "schemaVersion",
                "runId",
                "requestId",
                "operation",
                "team",
                "actor",
                "generation",
            ],
        )

        self.assertTrue(
            all(
                item["verdict"]
                == "pass"
                for item in checks
            )
        )

        mismatched = dict(value)
        mismatched["team"] = (
            "wrong-team"
        )

        checks = I1.expected_common(
            mismatched,
            run_id="run-1",
            request_id="request-1",
            operation="receive",
            team="gate-team",
            generation=7,
        )

        self.assertEqual(
            I1.verdict_from_assertions(
                checks
            ),
            "fail",
        )

        team_check = next(
            item
            for item in checks
            if item["name"] == "team"
        )

        self.assertEqual(
            team_check["verdict"],
            "fail",
        )

    def test_classify_broker_state_covers_expected_unknown_fail_and_unidentifiable(
        self,
    ):
        self.assertEqual(
            I1.classify_broker_state(
                {
                    "state": "claimed",
                },
                "claimed",
            ),
            (
                True,
                "expected_state",
            ),
        )

        self.assertEqual(
            I1.classify_broker_state(
                {
                    "state":
                        "stopped_for_unknown",
                    "reason":
                        "dependency_unobservable",
                },
                "claimed",
            ),
            (
                None,
                "dependency_unobservable",
            ),
        )

        self.assertEqual(
            I1.classify_broker_state(
                {
                    "state":
                        "stopped_for_unknown",
                },
                "claimed",
            ),
            (
                None,
                "stopped_for_unknown",
            ),
        )

        for state in (
            "stopped",
            "error",
            "absent",
        ):
            with self.subTest(
                state=state
            ):
                self.assertEqual(
                    I1.classify_broker_state(
                        {
                            "state": state,
                        },
                        "claimed",
                    ),
                    (
                        False,
                        state,
                    ),
                )

                self.assertEqual(
                    I1.classify_broker_state(
                        {
                            "state": state,
                            "reason": "reason",
                        },
                        "claimed",
                    ),
                    (
                        False,
                        "reason",
                    ),
                )

        for value in (
            {
                "state": "future-state",
            },
            {},
        ):
            with self.subTest(
                value=value
            ):
                self.assertEqual(
                    I1.classify_broker_state(
                        value,
                        "claimed",
                    ),
                    (
                        None,
                        "state_unidentifiable",
                    ),
                )


if __name__ == "__main__":
    unittest.main()