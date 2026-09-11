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

    def test_hook_decision_is_unknown_before_the_binding_is_known(self):
        # NativePilot.decisions is None until the launcher has published the
        # binding (#415); that must read as "no decision", never raise.
        self.assertIsNone(I1.hook_decision(None, "toolu_01"))

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


class PilotGateI1RoundBNativePilot(unittest.TestCase):
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"

    def make_native(
        self,
        root: Path,
        *,
        timeout: float = 0.05,
        launcher: Path | None = None,
        env: dict[str, str] | None = None,
    ):
        root = root.resolve()
        gate_repo = root / "repo"
        claude_config = root / "claude"
        artifact = root / "artifacts" / "native"
        gate_repo.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)

        if launcher is None:
            launcher = root / "launcher"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o700)

        return I1.NativePilot(
            launcher,
            gate_repo,
            "gate-team",
            claude_config,
            artifact,
            dict(env or os.environ),
            timeout,
        )

    def open_invoke_pty(self, native):
        master, slave = I1.pty.openpty()
        native.master = master
        native.proc = mock.Mock()
        native.proc.poll.return_value = None
        return master, slave

    def test_init_sets_expected_paths_and_initial_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            launcher = root / "launcher"
            gate_repo = root / "repo"
            claude_config = root / "claude"
            artifact = root / "artifact"
            env = {"KEY": "value"}

            native = I1.NativePilot(
                launcher,
                gate_repo,
                "gate-team",
                claude_config,
                artifact,
                env,
                7.5,
            )

            self.assertEqual(native.launcher, launcher)
            self.assertEqual(native.gate_repo, gate_repo)
            self.assertEqual(native.team, "gate-team")
            self.assertEqual(native.claude_config, claude_config)
            self.assertEqual(native.artifact, artifact)
            self.assertIs(native.env, env)
            self.assertEqual(native.timeout, 7.5)
            self.assertEqual(native.pty_log, artifact / "native-pty.raw")
            # Run logs are placed by the launcher next to the binding and
            # are unknown until the binding is (#415).
            self.assertIsNone(native.decisions)
            self.assertIsNone(native.executions)
            self.assertEqual(native.session_id, "")
            self.assertEqual(native.generation, 0)
            self.assertIsNone(native.binding)
            self.assertIsNone(native.transcript)
            self.assertIsNone(native.proc)
            self.assertIsNone(native.master)

    def test_pump_with_no_master_or_no_ready_data_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)

            native.pump(0)
            self.assertFalse(native.pty_log.exists())

            master, slave = I1.pty.openpty()
            try:
                native.master = master
                native.pump(0.01)
                self.assertFalse(native.pty_log.exists())
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_pump_appends_pty_bytes_across_calls_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = I1.pty.openpty()

            try:
                native.master = master

                os.write(slave, b"first")
                native.pump(0.2)

                os.write(slave, b"-second")
                native.pump(0.2)

                self.assertTrue(native.pty_log.is_file())
                self.assertEqual(
                    native.pty_log.read_bytes(),
                    b"first-second",
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_discover_transcript_sets_only_a_unique_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            native.session_id = self.SESSION_ID

            first = native.claude_config / "first.jsonl"
            first.write_text(
                json.dumps({"sessionId": self.SESSION_ID}) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(native.discover_transcript(), first)
            self.assertEqual(native.transcript, first)

            first.unlink()
            native.transcript = None
            self.assertIsNone(native.discover_transcript())
            self.assertIsNone(native.transcript)

            first.write_text(
                json.dumps({"sessionId": self.SESSION_ID}) + "\n",
                encoding="utf-8",
            )
            second = native.claude_config / "second.jsonl"
            second.write_text(
                json.dumps({"session_id": self.SESSION_ID}) + "\n",
                encoding="utf-8",
            )

            native.transcript = None
            self.assertIsNone(native.discover_transcript())
            self.assertIsNone(native.transcript)

    def test_invoke_returns_native_not_started_without_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            operation_dir = root / "operation"

            with mock.patch.object(native, "pump") as pump_mock:
                result = native.invoke("echo hi", operation_dir)

            self.assertEqual(
                result,
                {
                    "verdict": "unknown",
                    "reason": "native_not_started",
                },
            )
            pump_mock.assert_not_called()
            self.assertFalse(operation_dir.exists())

    def test_invoke_timeout_without_transcript_returns_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root, timeout=0.02)
            master, slave = self.open_invoke_pty(native)
            operation_dir = root / "operation"

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=None,
                    ):
                        result = native.invoke(
                            "echo hi",
                            operation_dir,
                        )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason": "transcript_unavailable",
                    },
                )
                self.assertTrue((operation_dir / "prompt.txt").is_file())
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_transcript_without_tool_returns_native_tool_not_observed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = self.open_invoke_pty(native)
            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            native.proc.poll.return_value = 0

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=(None, None),
                        ):
                            result = native.invoke(
                                "echo hi",
                                root / "operation",
                            )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason": "native_tool_not_observed",
                        "transcript": str(transcript),
                    },
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_missing_pretool_decision_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = self.open_invoke_pty(native)
            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=("tool-1", '{"ok":true}'),
                        ):
                            with mock.patch.object(
                                I1,
                                "hook_decision",
                                return_value=None,
                            ):
                                result = native.invoke(
                                    "echo hi",
                                    root / "operation",
                                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason": "pretool_decision_unavailable",
                        "toolUseId": "tool-1",
                        "transcript": str(transcript),
                    },
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_non_allow_pretool_decision_is_fail_and_reason_contains_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = self.open_invoke_pty(native)
            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=("tool-2", '{"ok":true}'),
                        ):
                            with mock.patch.object(
                                I1,
                                "hook_decision",
                                return_value="deny",
                            ):
                                result = native.invoke(
                                    "echo hi",
                                    root / "operation",
                                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "fail",
                        "reason": "pretool_decision_deny",
                        "toolUseId": "tool-2",
                        "transcript": str(transcript),
                    },
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_allow_without_tool_result_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = self.open_invoke_pty(native)
            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            native.proc.poll.return_value = 0

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=("tool-3", None),
                        ):
                            with mock.patch.object(
                                I1,
                                "hook_decision",
                                return_value="allow",
                            ):
                                result = native.invoke(
                                    "echo hi",
                                    root / "operation",
                                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason": "tool_result_unavailable",
                        "toolUseId": "tool-3",
                        "transcript": str(transcript),
                    },
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_unparseable_broker_result_is_unknown_and_persists_raw_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            master, slave = self.open_invoke_pty(native)
            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            operation_dir = root / "operation"

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=("tool-4", "not-json"),
                        ):
                            with mock.patch.object(
                                I1,
                                "hook_decision",
                                return_value="allow",
                            ):
                                result = native.invoke(
                                    "echo hi",
                                    operation_dir,
                                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "unknown",
                        "reason": "broker_response_unidentifiable",
                        "toolUseId": "tool-4",
                        "transcript": str(transcript),
                    },
                )
                self.assertEqual(
                    (operation_dir / "tool-result.raw").read_text(
                        encoding="utf-8"
                    ),
                    "not-json",
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def test_invoke_success_returns_parsed_broker_and_writes_prompt_with_done_token_to_pty(self):
        import tty

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)
            native.session_id = self.SESSION_ID
            master, slave = self.open_invoke_pty(native)
            tty.setraw(slave)

            transcript = root / "transcript.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            operation_dir = root / "operation"
            command = "echo exact-command"
            broker = {"schemaVersion": 1, "state": "ok"}
            observed_result = "noise\n" + json.dumps(broker) + "\n"

            token = hashlib.sha256(
                (self.SESSION_ID + command).encode("utf-8")
            ).hexdigest()[:16]
            marker = f"AGMSG_GATE_DONE_{token}"

            try:
                with mock.patch.object(native, "pump"):
                    with mock.patch.object(
                        native,
                        "discover_transcript",
                        return_value=transcript,
                    ):
                        with mock.patch.object(
                            I1,
                            "find_tool_result",
                            return_value=("tool-5", observed_result),
                        ):
                            with mock.patch.object(
                                I1,
                                "hook_decision",
                                return_value="allow",
                            ):
                                result = native.invoke(
                                    command,
                                    operation_dir,
                                )

                self.assertEqual(
                    result,
                    {
                        "verdict": "pass",
                        "reason": "native_pretool_broker_path_observed",
                        "toolUseId": "tool-5",
                        "transcript": str(transcript),
                        "broker": broker,
                    },
                )

                prompt = (operation_dir / "prompt.txt").read_text(
                    encoding="utf-8"
                )
                self.assertIn(command, prompt)
                self.assertIn(marker, prompt)

                os.set_blocking(slave, False)
                written = b""
                for _ in range(20):
                    try:
                        chunk = os.read(slave, 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    written += chunk

                self.assertIn(
                    command.encode("utf-8"),
                    written,
                )
                self.assertIn(
                    marker.encode("utf-8"),
                    written,
                )

                self.assertEqual(
                    (operation_dir / "tool-result.raw").read_text(
                        encoding="utf-8"
                    ),
                    observed_result,
                )
            finally:
                os.close(master)
                os.close(slave)
                native.master = None

    def make_launcher_fixture(
        self,
        root: Path,
        *,
        variant: str = "valid",
        previous_generation: int = 0,
        sleep_seconds: float = 0.05,
    ):
        root = root.resolve()
        gate_repo = root / "repo"
        claude_config = root / "claude"
        artifact = root / "artifact"
        gate_repo.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)

        team = "gate-team"
        generation = previous_generation + 1
        session_id = self.SESSION_ID

        if previous_generation:
            I1.atomic_json(
                I1.pilot_state_path(gate_repo, team),
                {"latestGeneration": previous_generation},
            )

        wrong_project = root / "wrong-project"
        wrong_project.mkdir()

        argv_log = root / "launcher-argv.json"
        decisions_log = root / "launcher-decisions.txt"
        launcher = root / "launcher.py"

        launcher.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
team = argv[argv.index("--team") + 1]
project = argv[argv.index("--project") + 1]
generation = int(os.environ["TEST_GENERATION"])
variant = os.environ["TEST_BINDING_VARIANT"]
session = os.environ["TEST_SESSION_ID"]

binding = {
    "schemaVersion": 1,
    "sessionId": session,
    "team": team,
    "agent": "agmsg_pm_pilot_claude",
    "generation": generation,
    "project": project,
}

if variant == "bad-session":
    binding["sessionId"] = "not-a-uuid"
elif variant == "bad-team":
    binding["team"] = "wrong-team"
elif variant == "bad-agent":
    binding["agent"] = "wrong-agent"
elif variant == "bad-generation":
    binding["generation"] = generation + 1
elif variant == "bad-project":
    binding["project"] = os.environ["TEST_WRONG_PROJECT"]

path = (
    Path(project)
    / "run"
    / "pilot"
    / f"{team}__agmsg_pm_pilot_claude"
    / "bindings"
    / f"{generation}.json"
)
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name("." + path.name + ".tmp")
tmp.write_text(json.dumps(binding) + "\\n", encoding="utf-8")
os.replace(tmp, path)

Path(os.environ["TEST_ARGV_LOG"]).write_text(
    json.dumps(argv) + "\\n",
    encoding="utf-8",
)
Path(os.environ["TEST_DECISIONS_LOG"]).write_text(
    os.environ.get("AGMSG_PM_DECISIONS_FILE", ""),
    encoding="utf-8",
)

time.sleep(float(os.environ["TEST_SLEEP_SECONDS"]))
""",
            encoding="utf-8",
        )
        launcher.chmod(0o700)

        env = os.environ.copy()
        env.update(
            {
                "TEST_GENERATION": str(generation),
                "TEST_BINDING_VARIANT": variant,
                "TEST_SESSION_ID": session_id,
                "TEST_WRONG_PROJECT": str(wrong_project),
                "TEST_ARGV_LOG": str(argv_log),
                "TEST_DECISIONS_LOG": str(decisions_log),
                "TEST_SLEEP_SECONDS": str(sleep_seconds),
            }
        )

        native = I1.NativePilot(
            launcher,
            gate_repo,
            team,
            claude_config,
            artifact,
            env,
            1.0,
        )

        return {
            "native": native,
            "gate_repo": gate_repo,
            "team": team,
            "generation": generation,
            "session_id": session_id,
            "binding": I1.binding_for_generation(
                gate_repo,
                team,
                generation,
            ),
            "argv_log": argv_log,
            "decisions_log": decisions_log,
        }

    def test_start_launches_fresh_pilot_and_accepts_valid_next_generation_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            fixture = self.make_launcher_fixture(
                root,
                previous_generation=2,
                sleep_seconds=30.0,
            )
            native = fixture["native"]

            try:
                # A live PM value in the harness environment must not reach
                # the launcher (#415).
                native.env["AGMSG_PM_DECISIONS_FILE"] = "/live/pm/decisions.jsonl"

                native.start()

                self.assertEqual(native.generation, 3)
                self.assertEqual(native.session_id, self.SESSION_ID)
                self.assertEqual(native.binding, fixture["binding"])
                self.assertTrue(native.binding.is_file())
                self.assertIsNotNone(native.proc)
                self.assertIsNotNone(native.master)

                argv = json.loads(
                    fixture["argv_log"].read_text(encoding="utf-8")
                )
                self.assertEqual(
                    argv,
                    [
                        "--team",
                        fixture["team"],
                        "--project",
                        str(fixture["gate_repo"]),
                        "--fresh",
                    ],
                )
                self.assertEqual(
                    fixture["decisions_log"].read_text(
                        encoding="utf-8"
                    ),
                    "",
                )
                self.assertEqual(
                    native.decisions,
                    fixture["binding"].parent / "3.decisions.jsonl",
                )
                self.assertEqual(
                    native.executions,
                    fixture["binding"].parent / "3.executions.jsonl",
                )
            finally:
                native.stop()

    def test_start_rejects_invalid_session_identity_generation_and_project_bindings(self):
        cases = (
            ("bad-session", "I1 binding sessionId invalid"),
            ("bad-team", "I1 binding identity mismatch"),
            ("bad-agent", "I1 binding identity mismatch"),
            ("bad-generation", "I1 binding generation mismatch"),
            ("bad-project", "I1 binding project mismatch"),
        )

        for variant, message in cases:
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    fixture = self.make_launcher_fixture(
                        root,
                        variant=variant,
                        sleep_seconds=0.05,
                    )
                    native = fixture["native"]

                    try:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            message,
                        ):
                            native.start()
                    finally:
                        native.stop()

                    if native.proc is not None:
                        self.assertIsNotNone(
                            native.proc.poll()
                        )

    def test_stop_terminates_real_process_and_resets_master(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)

            master, slave = I1.pty.openpty()
            native.master = master
            native.proc = subprocess.Popen(
                [
                    "sh",
                    "-c",
                    "cat >/dev/null",
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave)

            native.stop()

            self.assertIsNone(native.master)
            self.assertIsNotNone(native.proc.poll())

    def test_stop_escalates_from_sigterm_to_sigkill_when_wait_times_out(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native = self.make_native(root)

            proc = mock.Mock()
            proc.pid = 424242
            proc.poll.return_value = None
            proc.wait.side_effect = [
                subprocess.TimeoutExpired(
                    cmd="dummy",
                    timeout=2,
                ),
                0,
            ]

            native.proc = proc
            native.master = None

            with mock.patch.object(
                native,
                "pump",
            ):
                with mock.patch.object(
                    I1.time,
                    "monotonic",
                    side_effect=[0.0, 3.0],
                ):
                    with mock.patch.object(
                        I1.os,
                        "killpg",
                    ) as killpg_mock:
                        native.stop()

            self.assertEqual(
                killpg_mock.call_args_list,
                [
                    mock.call(
                        424242,
                        I1.signal.SIGTERM,
                    ),
                    mock.call(
                        424242,
                        I1.signal.SIGKILL,
                    ),
                ],
            )
            self.assertEqual(
                proc.wait.call_count,
                2,
            )

class PilotGateI1RoundCValidation(unittest.TestCase):
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    GENERATION = "7"
    GATE_TEAM = "agmsg-g4gate-round-c"

    def make_identity_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        root = root.resolve()

        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        artifact = root / "artifacts" / "I1"
        mutation_log = artifact / "mutation-log.jsonl"
        gh_log = artifact / "gh-invocations.jsonl"
        live_identity = root / "live-identity.json"

        gate_repo.mkdir(parents=True)
        artifact.mkdir(parents=True)

        binding = {
            "team": self.GATE_TEAM,
            "agent": I1.PILOT_AGENT,
            "project": str(gate_repo),
            "sessionId": self.SESSION_ID,
            "generation": self.GENERATION,
        }

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "artifact": artifact,
            "mutation_log": mutation_log,
            "gh_log": gh_log,
            "live_identity": live_identity,
            "binding": binding,
        }

    def validate_identity(
        self,
        fixture: dict[str, object],
        *,
        binding: dict[str, object] | None = None,
        operation_results: dict[str, dict[str, object]] | None = None,
        live_identity_json: Path | None = None,
    ) -> dict[str, object]:
        return I1.validate_identity(
            gate_repo=fixture["gate_repo"],
            run_root=fixture["run_root"],
            artifact=fixture["artifact"],
            gate_team=self.GATE_TEAM,
            binding=(
                binding
                if binding is not None
                else fixture["binding"]
            ),
            operation_results=(
                operation_results
                if operation_results is not None
                else {}
            ),
            mutation_log=fixture["mutation_log"],
            gh_log=fixture["gh_log"],
            live_identity_json=live_identity_json,
        )

    def check(
        self,
        record: dict[str, object],
        name: str,
    ) -> dict[str, object]:
        return next(
            item
            for item in record["checks"]
            if item["name"] == name
        )

    def append_record(
        self,
        path: Path,
        value: object,
    ) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            path,
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            fh.write("\n")

    def test_validate_gh_store_unreadable_or_invalid_json_is_unknown(
        self,
    ):
        cases = (
            "missing",
            "invalid-json",
        )

        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    store = root / "gh-store"
                    store.mkdir()

                    if case == "invalid-json":
                        (
                            store
                            / "comments.json"
                        ).write_text(
                            "{not-json\n",
                            encoding="utf-8",
                        )

                    checks = I1.validate_gh_store(
                        store,
                        "expected body",
                    )

                    self.assertEqual(
                        len(checks),
                        1,
                    )
                    self.assertEqual(
                        checks[0]["name"],
                        "gh-store-readable",
                    )
                    self.assertEqual(
                        checks[0]["verdict"],
                        "unknown",
                    )
                    self.assertTrue(
                        checks[0]["detail"]
                    )

    def test_validate_gh_store_non_object_or_non_list_comments_is_unknown(
        self,
    ):
        cases = (
            (
                [],
                None,
            ),
            (
                {
                    "comments": {},
                },
                {},
            ),
            (
                {
                    "comments": None,
                },
                None,
            ),
        )

        for value, expected_detail in cases:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    store = root / "gh-store"
                    store.mkdir()

                    I1.atomic_json(
                        store / "comments.json",
                        value,
                    )

                    checks = I1.validate_gh_store(
                        store,
                        "expected body",
                    )

                    self.assertEqual(
                        checks,
                        [
                            {
                                "name":
                                    "gh-comments-array",
                                "verdict":
                                    "unknown",
                                "detail":
                                    expected_detail,
                            }
                        ],
                    )

    def test_validate_gh_store_exact_single_matching_comment_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            store = root / "gh-store"
            store.mkdir()

            expected_body = "isolated issue body"

            matching = {
                "url":
                    "https://github.com/gate/agmsg/"
                    "issues/396#issuecomment-1",
                "body": expected_body,
                "issue": I1.ISSUE_NUMBER,
                "repo": I1.ISSUE_REPO,
            }

            I1.atomic_json(
                store / "comments.json",
                {
                    "schemaVersion": 1,
                    "comments": [
                        {
                            "body": expected_body,
                            "issue": 999,
                            "repo": I1.ISSUE_REPO,
                        },
                        matching,
                        {
                            "body": "other body",
                            "issue": I1.ISSUE_NUMBER,
                            "repo": I1.ISSUE_REPO,
                        },
                    ],
                },
            )

            checks = I1.validate_gh_store(
                store,
                expected_body,
            )

            self.assertEqual(
                len(checks),
                2,
            )

            self.assertEqual(
                checks[0],
                {
                    "name":
                        "pseudo-comment-exactly-once",
                    "verdict":
                        "pass",
                    "detail":
                        {
                            "count": 1,
                        },
                },
            )

            self.assertEqual(
                checks[1],
                {
                    "name":
                        "pseudo-comment-body",
                    "verdict":
                        "pass",
                    "detail":
                        matching,
                },
            )

    def test_validate_gh_store_zero_or_duplicate_matches_fail_and_non_dict_entries_are_ignored(
        self,
    ):
        expected_body = "expected body"

        cases = (
            (
                [
                    "not-an-object",
                    123,
                    {
                        "repo":
                            I1.ISSUE_REPO,
                        "issue":
                            I1.ISSUE_NUMBER,
                        "body":
                            "different",
                    },
                ],
                0,
            ),
            (
                [
                    "ignored",
                    {
                        "repo":
                            I1.ISSUE_REPO,
                        "issue":
                            I1.ISSUE_NUMBER,
                        "body":
                            expected_body,
                    },
                    {
                        "repo":
                            I1.ISSUE_REPO,
                        "issue":
                            I1.ISSUE_NUMBER,
                        "body":
                            expected_body,
                    },
                ],
                2,
            ),
        )

        for comments, expected_count in cases:
            with self.subTest(
                expected_count=expected_count
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    store = root / "gh-store"
                    store.mkdir()

                    I1.atomic_json(
                        store / "comments.json",
                        {
                            "comments": comments,
                        },
                    )

                    checks = I1.validate_gh_store(
                        store,
                        expected_body,
                    )

                    self.assertEqual(
                        len(checks),
                        2,
                    )

                    self.assertEqual(
                        checks[0]["name"],
                        (
                            "pseudo-comment-"
                            "exactly-once"
                        ),
                    )
                    self.assertEqual(
                        checks[0]["verdict"],
                        "fail",
                    )
                    self.assertEqual(
                        checks[0]["detail"],
                        {
                            "count":
                                expected_count,
                        },
                    )

                    self.assertEqual(
                        checks[1]["name"],
                        "pseudo-comment-body",
                    )
                    self.assertEqual(
                        checks[1]["verdict"],
                        "fail",
                    )
                    self.assertIsNone(
                        checks[1]["detail"]
                    )

    def test_validate_identity_binding_identity_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            result = self.validate_identity(
                fixture
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    result,
                    "binding-team",
                )["verdict"],
                "pass",
            )
            self.assertEqual(
                self.check(
                    result,
                    "binding-agent",
                )["verdict"],
                "pass",
            )
            self.assertEqual(
                self.check(
                    result,
                    "binding-project",
                )["verdict"],
                "pass",
            )

    def test_validate_identity_binding_mismatch_is_fail_and_unresolvable_project_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            wrong = dict(
                fixture["binding"]
            )
            wrong["team"] = "wrong-team"
            wrong["agent"] = "wrong-agent"

            other_project = (
                fixture["root"]
                / "other-project"
            )
            other_project.mkdir()
            wrong["project"] = str(
                other_project
            )

            result = self.validate_identity(
                fixture,
                binding=wrong,
            )

            self.assertEqual(
                result["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    result,
                    "binding-team",
                )["verdict"],
                "fail",
            )
            self.assertEqual(
                self.check(
                    result,
                    "binding-agent",
                )["verdict"],
                "fail",
            )
            self.assertEqual(
                self.check(
                    result,
                    "binding-project",
                )["verdict"],
                "fail",
            )

        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            unprovable = dict(
                fixture["binding"]
            )
            unprovable["project"] = str(
                fixture["root"]
                / "missing-project"
            )

            result = self.validate_identity(
                fixture,
                binding=unprovable,
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            project_check = self.check(
                result,
                "binding-project",
            )

            self.assertEqual(
                project_check["verdict"],
                "unknown",
            )
            self.assertTrue(
                project_check["detail"]
            )

    def test_validate_identity_adds_broker_checks_only_for_dict_brokers_and_owner_only_when_present(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            valid_owner = (
                "p2:"
                f"{self.SESSION_ID}:"
                f"{self.GENERATION}:run-1"
            )

            operation_results = {
                "receive": {
                    "broker": {
                        "team":
                            self.GATE_TEAM,
                        "actor":
                            I1.PILOT_AGENT,
                        "generation":
                            self.GENERATION,
                        "owner":
                            valid_owner,
                    }
                },
                "delegate": {
                    "broker": {
                        "team":
                            self.GATE_TEAM,
                        "actor":
                            I1.PILOT_AGENT,
                        "generation":
                            int(
                                self.GENERATION
                            ),
                    }
                },
                "ignored-none": {
                    "broker": None,
                },
                "ignored-list": {
                    "broker": [],
                },
                "ignored-record": "not-dict",
            }

            result = self.validate_identity(
                fixture,
                operation_results=(
                    operation_results
                ),
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            names = {
                item["name"]
                for item in result["checks"]
            }

            for suffix in (
                "team",
                "actor",
                "generation",
            ):
                self.assertIn(
                    f"receive.{suffix}",
                    names,
                )
                self.assertIn(
                    f"delegate.{suffix}",
                    names,
                )

            self.assertIn(
                "receive.owner",
                names,
            )
            self.assertNotIn(
                "delegate.owner",
                names,
            )

            self.assertFalse(
                any(
                    name.startswith(
                        "ignored-"
                    )
                    for name in names
                )
            )

    def test_validate_identity_broker_mismatches_and_bad_owner_are_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            operation_results = {
                "receive": {
                    "broker": {
                        "team": "wrong-team",
                        "actor": "wrong-agent",
                        "generation": "99",
                        "owner": "wrong-owner",
                    }
                }
            }

            result = self.validate_identity(
                fixture,
                operation_results=(
                    operation_results
                ),
            )

            self.assertEqual(
                result["verdict"],
                "fail",
            )

            for name in (
                "receive.team",
                "receive.actor",
                "receive.generation",
                "receive.owner",
            ):
                self.assertEqual(
                    self.check(
                        result,
                        name,
                    )["verdict"],
                    "fail",
                )

    def test_validate_identity_combines_mutation_and_gh_logs_and_accepts_allowed_targets(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            gate_target = (
                fixture["gate_repo"]
                / "request.json"
            )
            run_target = (
                fixture["run_root"]
                / "runtime"
                / "state.json"
            )
            artifact_target = (
                fixture["artifact"]
                / "evidence.json"
            )

            self.append_record(
                fixture["mutation_log"],
                {
                    "team": self.GATE_TEAM,
                    "target":
                        str(gate_target),
                },
            )
            self.append_record(
                fixture["mutation_log"],
                {
                    "team": self.GATE_TEAM,
                    "target":
                        str(run_target),
                },
            )
            self.append_record(
                fixture["gh_log"],
                {
                    "team": self.GATE_TEAM,
                    "target":
                        str(artifact_target),
                },
            )

            result = self.validate_identity(
                fixture
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            for index in range(3):
                self.assertEqual(
                    self.check(
                        result,
                        f"mutation-{index}-team",
                    )["verdict"],
                    "pass",
                )

                self.assertEqual(
                    self.check(
                        result,
                        (
                            f"mutation-{index}-"
                            "target"
                        ),
                    )["verdict"],
                    "pass",
                )

    def test_validate_identity_mutation_wrong_team_or_target_outside_allowed_roots_is_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            outside = (
                fixture["root"]
                / "outside"
                / "target"
            )

            self.append_record(
                fixture["mutation_log"],
                {
                    "team": "live-team",
                    "target": str(outside),
                },
            )

            result = self.validate_identity(
                fixture
            )

            self.assertEqual(
                result["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-team",
                )["verdict"],
                "fail",
            )

            target_check = self.check(
                result,
                "mutation-0-target",
            )

            self.assertEqual(
                target_check["verdict"],
                "fail",
            )
            self.assertEqual(
                target_check["detail"],
                str(
                    outside.resolve(
                        strict=False
                    )
                ),
            )

    def test_validate_identity_mutation_target_resolution_failure_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            failing_target = (
                fixture["gate_repo"]
                / "resolution-failure"
            )

            self.append_record(
                fixture["mutation_log"],
                {
                    "team":
                        self.GATE_TEAM,
                    "target":
                        str(failing_target),
                },
            )

            original_resolve = (
                I1.pathlib.Path.resolve
            )

            def selective_resolve(
                path_self,
                strict=False,
            ):
                if (
                    str(path_self)
                    == str(failing_target)
                ):
                    raise OSError(
                        "synthetic resolution failure"
                    )

                return original_resolve(
                    path_self,
                    strict=strict,
                )

            with mock.patch.object(
                I1.pathlib.Path,
                "resolve",
                selective_resolve,
            ):
                result = (
                    self.validate_identity(
                        fixture
                    )
                )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-team",
                )["verdict"],
                "pass",
            )

            target_check = self.check(
                result,
                "mutation-0-target",
            )

            self.assertEqual(
                target_check["verdict"],
                "unknown",
            )
            self.assertIn(
                "synthetic resolution failure",
                target_check["detail"],
            )

    def test_validate_identity_invalid_jsonl_adds_read_unknown_and_keeps_records_read_before_failure(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            valid_target = (
                fixture["gate_repo"]
                / "before-error"
            )

            fixture[
                "mutation_log"
            ].write_text(
                (
                    json.dumps(
                        {
                            "team":
                                self.GATE_TEAM,
                            "target":
                                str(
                                    valid_target
                                ),
                        }
                    )
                    + "\n"
                    + "{not-json}\n"
                    + json.dumps(
                        {
                            "team":
                                "must-not-be-read",
                        }
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            result = self.validate_identity(
                fixture
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            read_check = self.check(
                result,
                (
                    "read-"
                    + fixture[
                        "mutation_log"
                    ].name
                ),
            )

            self.assertEqual(
                read_check["verdict"],
                "unknown",
            )

            # The first valid record was appended before json.loads()
            # failed on the second line.
            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-team",
                )["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-target",
                )["verdict"],
                "pass",
            )

            names = [
                item["name"]
                for item in result["checks"]
            ]

            self.assertNotIn(
                "mutation-1-team",
                names,
            )

    def test_validate_identity_non_dict_jsonl_records_are_ignored(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            fixture[
                "mutation_log"
            ].write_text(
                (
                    "[]\n"
                    '"string"\n'
                    "42\n"
                    + json.dumps(
                        {
                            "team":
                                self.GATE_TEAM,
                        }
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            result = self.validate_identity(
                fixture
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            names = [
                item["name"]
                for item in result["checks"]
            ]

            self.assertIn(
                "mutation-0-team",
                names,
            )
            self.assertNotIn(
                "mutation-1-team",
                names,
            )

    def test_validate_identity_live_tokens_absent_from_mutations_all_pass(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            self.append_record(
                fixture["mutation_log"],
                {
                    "team": self.GATE_TEAM,
                    "target": str(
                        fixture["gate_repo"]
                        / "safe-target"
                    ),
                },
            )

            live = {
                "team": "live-production-team",
                "bindingPath":
                    "/live/run/pilot/binding.json",
                "sessionId":
                    "aaaaaaaa-aaaa-4aaa-8aaa-"
                    "aaaaaaaaaaaa",
                "claimFile":
                    "/live/run/claim.json",
                "ignored": "not-a-token",
            }

            I1.atomic_json(
                fixture["live_identity"],
                live,
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=(
                    fixture[
                        "live_identity"
                    ]
                ),
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            for key in (
                "team",
                "bindingPath",
                "sessionId",
                "claimFile",
            ):
                token = live[key]

                name = (
                    "live-token-not-"
                    "mutation-target:"
                    + hashlib.sha256(
                        token.encode()
                    ).hexdigest()[:8]
                )

                check = self.check(
                    result,
                    name,
                )

                self.assertEqual(
                    check["verdict"],
                    "pass",
                )
                self.assertEqual(
                    check["detail"],
                    "absent",
                )

    def test_validate_identity_live_token_in_mutation_record_is_definite_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            live_session = (
                "aaaaaaaa-aaaa-4aaa-8aaa-"
                "aaaaaaaaaaaa"
            )

            live = {
                "sessionId": live_session,
            }

            I1.atomic_json(
                fixture["live_identity"],
                live,
            )

            # Keep the mutation target itself inside gate_repo so the
            # ordinary mutation containment assertion passes. The live
            # token assertion must be the one that detects this leak.
            target = (
                fixture["gate_repo"]
                / "mutation"
                / live_session
                / "record.json"
            )

            self.append_record(
                fixture["mutation_log"],
                {
                    "team":
                        self.GATE_TEAM,
                    "target":
                        str(target),
                },
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=(
                    fixture[
                        "live_identity"
                    ]
                ),
            )

            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-team",
                )["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    result,
                    "mutation-0-target",
                )["verdict"],
                "pass",
            )

            token_name = (
                "live-token-not-"
                "mutation-target:"
                + hashlib.sha256(
                    live_session.encode()
                ).hexdigest()[:8]
            )

            token_check = self.check(
                result,
                token_name,
            )

            self.assertEqual(
                token_check["verdict"],
                "fail",
            )
            self.assertEqual(
                token_check["detail"],
                "matched",
            )

            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_validate_identity_live_token_match_is_substring_based_across_entire_record(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            live_team = "live-team-token"

            I1.atomic_json(
                fixture["live_identity"],
                {
                    "team": live_team,
                },
            )

            # validate_identity serializes the entire mutation record,
            # not only target/team fields. Therefore a live token in
            # another field must also fail the non-mutation assertion.
            self.append_record(
                fixture["mutation_log"],
                {
                    "team":
                        self.GATE_TEAM,
                    "note":
                        (
                            "contains:"
                            + live_team
                        ),
                },
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=(
                    fixture[
                        "live_identity"
                    ]
                ),
            )

            token_name = (
                "live-token-not-"
                "mutation-target:"
                + hashlib.sha256(
                    live_team.encode()
                ).hexdigest()[:8]
            )

            self.assertEqual(
                self.check(
                    result,
                    token_name,
                )["verdict"],
                "fail",
            )

            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_validate_identity_live_identity_ignores_empty_and_non_string_tokens(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            I1.atomic_json(
                fixture["live_identity"],
                {
                    "team": "",
                    "bindingPath": None,
                    "sessionId": 123,
                    "claimFile": [],
                },
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=(
                    fixture[
                        "live_identity"
                    ]
                ),
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            self.assertFalse(
                any(
                    item["name"].startswith(
                        "live-token-not-"
                        "mutation-target:"
                    )
                    for item in result["checks"]
                )
            )

    def test_validate_identity_unreadable_live_identity_adds_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            fixture[
                "live_identity"
            ].write_text(
                "{not-json\n",
                encoding="utf-8",
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=(
                    fixture[
                        "live_identity"
                    ]
                ),
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )

            live_read = self.check(
                result,
                "live-identity-read",
            )

            self.assertEqual(
                live_read["verdict"],
                "unknown",
            )

            self.assertTrue(
                live_read["detail"]
            )

    def test_validate_identity_missing_live_identity_file_adds_no_live_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_identity_fixture(
                    Path(temp)
                )
            )

            missing = (
                fixture["root"]
                / "missing-live-identity.json"
            )

            result = self.validate_identity(
                fixture,
                live_identity_json=missing,
            )

            self.assertEqual(
                result["verdict"],
                "pass",
            )

            names = {
                item["name"]
                for item in result["checks"]
            }

            self.assertNotIn(
                "live-identity-read",
                names,
            )

            self.assertFalse(
                any(
                    name.startswith(
                        "live-token-not-"
                        "mutation-target:"
                    )
                    for name in names
                )
            )

class PilotGateI1RoundDRunI1(unittest.TestCase):
    RUN_ID = "round-d-run"
    GATE_TEAM = "agmsg-g4gate-round-d"
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    GENERATION = 7
    INPUT_ID = "input-message-1"
    DELEGATE_ID = "delegate-message-1"
    INPUT_RECEIPT = "input-receipt-1"
    RESULT_ID = "result-message-1"
    RESULT_RECEIPT = "result-receipt-1"

    def make_args(
        self,
        root: Path,
        *,
        live_identity_json: str | None = None,
    ):
        root = root.resolve()
        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        artifact_dir = root / "artifacts"
        claude_config = run_root / "claude"

        gate_repo.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)

        return I1.argparse.Namespace(
            run_id=self.RUN_ID,
            run_root=str(run_root),
            gate_repo=str(gate_repo),
            artifact_dir=str(artifact_dir),
            gate_team=self.GATE_TEAM,
            claude_config=str(claude_config),
            timeout_seconds=12,
            live_identity_json=live_identity_json,
        )

    def common_broker(
        self,
        *,
        operation: str,
        request_id: str,
        **extra,
    ) -> dict[str, object]:
        value = {
            "schemaVersion": 1,
            "runId": self.RUN_ID,
            "requestId": request_id,
            "operation": operation,
            "team": self.GATE_TEAM,
            "actor": I1.PILOT_AGENT,
            "generation": str(self.GENERATION),
        }
        value.update(extra)
        return value

    def default_native_results(
        self,
    ) -> dict[str, dict[str, object]]:
        owner = (
            f"p2:{self.SESSION_ID}:"
            f"{self.GENERATION}:{self.RUN_ID}"
        )

        return {
            "receive": {
                "verdict": "pass",
                "broker": self.common_broker(
                    operation="receive",
                    request_id=f"receive-{self.RUN_ID}",
                    state="claimed",
                    inputMessageId=self.INPUT_ID,
                    owner=owner,
                ),
            },
            "delegate": {
                "verdict": "pass",
                "broker": self.common_broker(
                    operation="delegate",
                    request_id=f"delegate-{self.RUN_ID}",
                    state="delegated",
                    deliveryState="queued",
                    worker=I1.WORKER,
                    inputMessageId=self.INPUT_ID,
                    delegateMessageId=self.DELEGATE_ID,
                    inputReceiptId=self.INPUT_RECEIPT,
                ),
            },
            "collect-result": {
                "verdict": "pass",
                "broker": self.common_broker(
                    operation="collect-result",
                    request_id=f"delegate-{self.RUN_ID}",
                    state="result_claimed",
                    delegateMessageId=self.DELEGATE_ID,
                    resultMessageId=self.RESULT_ID,
                    owner=owner,
                    resultReceiptId=self.RESULT_RECEIPT,
                    result="isolated worker result",
                ),
            },
            "issue-record": {
                "verdict": "pass",
                "broker": self.common_broker(
                    operation="issue-record",
                    request_id=f"delegate-{self.RUN_ID}",
                    state="acked",
                    inputMessageId=self.INPUT_ID,
                    delegateMessageId=self.DELEGATE_ID,
                    resultMessageId=self.RESULT_ID,
                    issueNumber=I1.ISSUE_NUMBER,
                ),
            },
            "observe-owner": {
                "verdict": "pass",
                "broker": self.common_broker(
                    operation="observe-owner",
                    request_id=f"observe-{self.RUN_ID}",
                    state="observed",
                    ownerStatus="owned",
                ),
            },
        }

    def make_native(
        self,
        args,
        native_results=None,
    ):
        gate_repo = Path(
            args.gate_repo
        ).resolve()

        binding = (
            gate_repo
            / "run"
            / "pilot"
            / f"{self.GATE_TEAM}__{I1.PILOT_AGENT}"
            / "bindings"
            / f"{self.GENERATION}.json"
        )

        binding.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        binding.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "team": self.GATE_TEAM,
                    "agent": I1.PILOT_AGENT,
                    "project": str(gate_repo),
                    "sessionId": self.SESSION_ID,
                    "generation": self.GENERATION,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        native = mock.Mock()
        native.session_id = self.SESSION_ID
        native.generation = self.GENERATION
        native.binding = binding
        native.start = mock.Mock()
        native.stop = mock.Mock()

        results = (
            native_results
            if native_results is not None
            else self.default_native_results()
        )

        def invoke(
            command,
            operation_dir,
        ):
            value = results.get(
                str(command),
                {
                    "verdict": "unknown",
                    "reason": "fixture-operation-missing",
                },
            )

            return json.loads(
                json.dumps(value)
            )

        native.invoke = mock.Mock(
            side_effect=invoke
        )

        return native

    def default_provider_side_effect(
        self,
        provider,
        argv,
        gate_repo,
        env,
        mutation_log=None,
        mutating=False,
    ):
        if argv[0] == "message-send":
            if argv[2] == I1.SENDER:
                return {
                    "state": "queued",
                    "messageId": self.INPUT_ID,
                }

            if argv[2] == I1.WORKER:
                return {
                    "state": "queued",
                    "messageId": self.RESULT_ID,
                }

        if argv[0] == "message-peek":
            return {
                "state": "ok",
                "messageId": self.DELEGATE_ID,
                "from": I1.PILOT_AGENT,
                "to": I1.WORKER,
                "body": json.dumps(
                    {
                        "requestId":
                            f"delegate-{self.RUN_ID}",
                        "inputMessageId":
                            self.INPUT_ID,
                    },
                    separators=(",", ":"),
                ),
            }

        raise AssertionError(
            f"unexpected provider call: {argv}"
        )

    @contextlib.contextmanager
    def harness(
        self,
        args,
        *,
        native=None,
        native_results=None,
        provider_side_effect=None,
        identity=None,
        gh_checks=None,
        receipt_side_effect=None,
        storage_side_effect=None,
        digest_side_effect=None,
    ):
        if native is None:
            native = self.make_native(
                args,
                native_results,
            )

        if identity is None:
            identity = {
                "schemaVersion": 1,
                "verdict": "pass",
                "checks": [],
            }

        if gh_checks is None:
            gh_checks = [
                I1.assertion(
                    "pseudo-gh",
                    True,
                    None,
                )
            ]

        writes = {}

        def capture_atomic(
            path,
            value,
        ):
            writes[str(Path(path))] = (
                json.loads(
                    json.dumps(value)
                )
            )

        iso = mock.Mock()
        iso.canonical.side_effect = (
            lambda value:
                str(
                    Path(value).resolve()
                )
        )

        if digest_side_effect is None:
            iso.sha256_file.return_value = (
                "same-digest"
            )
        else:
            iso.sha256_file.side_effect = (
                digest_side_effect
            )

        if provider_side_effect is None:
            provider_side_effect = (
                self.default_provider_side_effect
            )

        if receipt_side_effect is None:
            receipt_side_effect = (
                lambda *unused: 1
            )

        stack = contextlib.ExitStack()

        patches = {
            "load_iso":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "load_iso",
                        return_value=iso,
                    )
                ),
            "require_regular_executable":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "require_regular_executable",
                    )
                ),
            "copyfile":
                stack.enter_context(
                    mock.patch.object(
                        I1.shutil,
                        "copyfile",
                    )
                ),
            "chmod":
                stack.enter_context(
                    mock.patch.object(
                        I1.os,
                        "chmod",
                    )
                ),
            "register_fixture_member":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "register_fixture_member",
                    )
                ),
            "provider_call":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "provider_call",
                        side_effect=(
                            provider_side_effect
                        ),
                    )
                ),
            "NativePilot":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "NativePilot",
                        return_value=native,
                    )
                ),
            "make_request":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "make_request",
                        wraps=I1.make_request,
                    )
                ),
            "exact_broker_command":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "exact_broker_command",
                        side_effect=(
                            lambda broker,
                            config,
                            operation,
                            request:
                                operation
                        ),
                    )
                ),
            "write_request":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "write_request",
                    )
                ),
            "append_jsonl":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "append_jsonl",
                    )
                ),
            "expected_common":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "expected_common",
                        wraps=I1.expected_common,
                    )
                ),
            "classify_broker_state":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "classify_broker_state",
                        wraps=(
                            I1.classify_broker_state
                        ),
                    )
                ),
            "validate_gh_store":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "validate_gh_store",
                        return_value=gh_checks,
                    )
                ),
            "storage_db":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "storage_db",
                        side_effect=(
                            storage_side_effect
                        ),
                        return_value=(
                            Path(
                                args.gate_repo
                            )
                            / "db"
                            / "messages.db"
                        ),
                    )
                ),
            "receipt_count":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "receipt_count",
                        side_effect=(
                            receipt_side_effect
                        ),
                    )
                ),
            "validate_identity":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "validate_identity",
                        return_value=identity,
                    )
                ),
            "atomic_json":
                stack.enter_context(
                    mock.patch.object(
                        I1,
                        "atomic_json",
                        side_effect=capture_atomic,
                    )
                ),
        }

        try:
            yield {
                "iso": iso,
                "native": native,
                "writes": writes,
                **patches,
            }
        finally:
            stack.close()

    def written(
        self,
        harness,
        path: Path,
    ):
        return harness["writes"][
            str(path)
        ]

    def final_result(
        self,
        args,
        harness,
    ):
        return self.written(
            harness,
            (
                Path(
                    args.artifact_dir
                ).resolve()
                / "I1"
                / "result.json"
            ),
        )

    def operation_result(
        self,
        args,
        harness,
        operation: str,
    ):
        return self.written(
            harness,
            (
                Path(
                    args.artifact_dir
                ).resolve()
                / "I1"
                / operation
                / "result.json"
            ),
        )

    def test_setup_validates_four_executables_copies_gh_registers_members_and_builds_isolated_env(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)
            native = self.make_native(args)

            with self.harness(
                args,
                native=native,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                0,
            )

            gate_repo = Path(
                args.gate_repo
            ).resolve()
            script_dir = Path(
                I1.__file__
            ).resolve().parent

            expected_paths = [
                gate_repo
                / "scripts"
                / "pilot-launcher.sh",
                gate_repo
                / "scripts"
                / "p2-provider.sh",
                gate_repo
                / "scripts"
                / "p2-consumer-broker.sh",
                script_dir
                / "pilot-gate-gh.py",
            ]

            self.assertEqual(
                harness[
                    "require_regular_executable"
                ].call_args_list,
                [
                    mock.call(path)
                    for path in expected_paths
                ],
            )

            gh_bin = (
                Path(
                    args.run_root
                ).resolve()
                / "i1-bin"
                / "gh"
            )

            harness[
                "copyfile"
            ].assert_called_once_with(
                expected_paths[3],
                gh_bin,
            )

            harness[
                "chmod"
            ].assert_called_once_with(
                gh_bin,
                0o700,
            )

            self.assertEqual(
                harness[
                    "iso"
                ].sha256_file.call_args_list,
                [
                    mock.call(
                        expected_paths[3]
                    ),
                    mock.call(
                        gh_bin
                    ),
                ],
            )

            worker_project = (
                gate_repo
                / ".agmsg-gate"
                / "i1-worker"
            )
            sender_project = (
                gate_repo
                / ".agmsg-gate"
                / "i1-sender"
            )

            register_calls = harness[
                "register_fixture_member"
            ].call_args_list

            self.assertEqual(
                len(register_calls),
                2,
            )

            self.assertEqual(
                register_calls[0].args[:5],
                (
                    gate_repo,
                    self.GATE_TEAM,
                    I1.WORKER,
                    worker_project,
                    "worker",
                ),
            )

            self.assertEqual(
                register_calls[1].args[:5],
                (
                    gate_repo,
                    self.GATE_TEAM,
                    I1.SENDER,
                    sender_project,
                    "sender",
                ),
            )

            native_ctor = harness[
                "NativePilot"
            ].call_args

            native_env = native_ctor.args[5]

            self.assertTrue(
                native_env["PATH"].startswith(
                    str(
                        Path(
                            args.run_root
                        ).resolve()
                        / "i1-bin"
                    )
                    + os.pathsep
                )
            )
            self.assertEqual(
                native_env[
                    "AGMSG_GATE_GH_STORE"
                ],
                str(
                    Path(
                        args.run_root
                    ).resolve()
                    / "gh-store"
                ),
            )
            self.assertEqual(
                native_env[
                    "AGMSG_GATE_GH_LOG"
                ],
                str(
                    Path(
                        args.artifact_dir
                    ).resolve()
                    / "I1"
                    / "gh-invocations.jsonl"
                ),
            )
            self.assertEqual(
                native_env[
                    "AGMSG_GATE_GH_REPO"
                ],
                I1.ISSUE_REPO,
            )
            self.assertEqual(
                native_env[
                    "AGMSG_GATE_GH_ISSUE"
                ],
                str(
                    I1.ISSUE_NUMBER
                ),
            )
            self.assertEqual(
                native_env[
                    "AGMSG_GATE_GH_BODY_ROOT"
                ],
                str(
                    gate_repo
                    / "run"
                    / "pilot"
                ),
            )

    def test_gh_digest_mismatch_raises_before_registration_or_native_start(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)
            native = self.make_native(args)

            with self.harness(
                args,
                native=native,
                digest_side_effect=[
                    "source-digest",
                    "different-copy-digest",
                ],
            ) as harness:
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "isolated gh copy "
                        "digest mismatch"
                    ),
                ):
                    I1.run_i1(args)

            harness[
                "register_fixture_member"
            ].assert_not_called()
            harness[
                "NativePilot"
            ].assert_not_called()
            native.start.assert_not_called()
            native.stop.assert_not_called()

    def test_seed_failure_raises_before_native_construction_so_finally_cannot_stop_native(
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
            with self.subTest(seed=seed):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    args = self.make_args(root)
                    native = self.make_native(args)

                    def provider(
                        provider_path,
                        argv,
                        gate_repo,
                        env,
                        mutation_log=None,
                        mutating=False,
                    ):
                        return seed

                    with self.harness(
                        args,
                        native=native,
                        provider_side_effect=provider,
                    ) as harness:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "I1 input seed failed",
                        ):
                            I1.run_i1(args)

                    harness[
                        "NativePilot"
                    ].assert_not_called()
                    native.stop.assert_not_called()

    def test_receive_pass_validates_claim_input_and_owner(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            with self.harness(
                args
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                0,
            )

            receive = self.operation_result(
                args,
                harness,
                "receive",
            )

            self.assertEqual(
                receive["verdict"],
                "pass",
            )

            checks = {
                item["name"]:
                    item
                for item
                in receive["checks"]
            }

            for name in (
                "state",
                "inputMessageId",
                "owner",
            ):
                self.assertEqual(
                    checks[name]["verdict"],
                    "pass",
                )

    def test_receive_nonpass_stops_dependent_chain_but_observe_owner_still_runs(
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
                    root = Path(temp).resolve()
                    args = self.make_args(root)

                    native_results = (
                        self.default_native_results()
                    )
                    native_results[
                        "receive"
                    ] = {
                        "verdict":
                            receive_verdict,
                        "reason":
                            "synthetic-receive",
                    }

                    native = self.make_native(
                        args,
                        native_results,
                    )

                    with self.harness(
                        args,
                        native=native,
                    ) as harness:
                        status = I1.run_i1(
                            args
                        )

                    expected_status = (
                        1
                        if receive_verdict
                        == "fail"
                        else 2
                    )

                    self.assertEqual(
                        status,
                        expected_status,
                    )

                    for operation in (
                        "delegate",
                        "collect-result",
                        "issue-record",
                    ):
                        self.assertEqual(
                            self.operation_result(
                                args,
                                harness,
                                operation,
                            ),
                            {
                                "verdict":
                                    "unknown",
                                "reason":
                                    (
                                        "receive_"
                                        "prerequisite_"
                                        "not_pass"
                                    ),
                            },
                        )

                    invoked = [
                        call.args[0]
                        for call
                        in native.invoke.call_args_list
                    ]

                    self.assertEqual(
                        invoked,
                        [
                            "receive",
                            "observe-owner",
                        ],
                    )

    def test_delegate_nonpass_or_missing_delegate_id_blocks_collect_and_issue(
        self,
    ):
        scenarios = (
            (
                "native-fail",
                {
                    "verdict": "fail",
                    "reason":
                        "synthetic-delegate",
                },
            ),
            (
                "missing-id",
                {
                    "verdict": "pass",
                    "broker":
                        self.common_broker(
                            operation="delegate",
                            request_id=(
                                f"delegate-"
                                f"{self.RUN_ID}"
                            ),
                            state="delegated",
                            deliveryState="queued",
                            worker=I1.WORKER,
                            inputMessageId=(
                                self.INPUT_ID
                            ),
                            delegateMessageId=None,
                            inputReceiptId=(
                                self.INPUT_RECEIPT
                            ),
                        ),
                },
            ),
        )

        for label, delegate_record in scenarios:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    args = self.make_args(root)

                    native_results = (
                        self.default_native_results()
                    )
                    native_results[
                        "delegate"
                    ] = delegate_record

                    native = self.make_native(
                        args,
                        native_results,
                    )

                    with self.harness(
                        args,
                        native=native,
                    ) as harness:
                        status = I1.run_i1(
                            args
                        )

                    self.assertIn(
                        status,
                        (1, 2),
                    )

                    for operation in (
                        "collect-result",
                        "issue-record",
                    ):
                        self.assertEqual(
                            self.operation_result(
                                args,
                                harness,
                                operation,
                            ),
                            {
                                "verdict":
                                    "unknown",
                                "reason":
                                    (
                                        "delegate_"
                                        "prerequisite_"
                                        "not_pass"
                                    ),
                            },
                        )

                    invoked = [
                        call.args[0]
                        for call
                        in native.invoke.call_args_list
                    ]
                    self.assertNotIn(
                        "collect-result",
                        invoked,
                    )
                    self.assertNotIn(
                        "issue-record",
                        invoked,
                    )
                    self.assertIn(
                        "observe-owner",
                        invoked,
                    )

    def test_delegate_provider_readback_exception_becomes_unknown_assertion(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            def provider(
                provider_path,
                argv,
                gate_repo,
                env,
                mutation_log=None,
                mutating=False,
            ):
                if argv[0] == "message-peek":
                    raise RuntimeError(
                        "peek unavailable"
                    )

                return (
                    self.default_provider_side_effect(
                        provider_path,
                        argv,
                        gate_repo,
                        env,
                        mutation_log,
                        mutating,
                    )
                )

            with self.harness(
                args,
                provider_side_effect=provider,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                2,
            )

            delegate = self.operation_result(
                args,
                harness,
                "delegate",
            )

            readback = next(
                item
                for item
                in delegate["checks"]
                if item["name"]
                == "worker-readback"
            )

            self.assertEqual(
                readback["verdict"],
                "unknown",
            )

    def test_collect_result_stopped_for_unknown_preserves_gap_as_unknown_not_pass(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            native_results = (
                self.default_native_results()
            )
            native_results[
                "collect-result"
            ] = {
                "verdict": "pass",
                "broker":
                    self.common_broker(
                        operation="collect-result",
                        request_id=(
                            f"delegate-"
                            f"{self.RUN_ID}"
                        ),
                        state=(
                            "stopped_for_unknown"
                        ),
                        reason=(
                            "known-g2-g3-gap"
                        ),
                    ),
            }

            with self.harness(
                args,
                native_results=native_results,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                2,
            )

            collect = self.operation_result(
                args,
                harness,
                "collect-result",
            )

            self.assertEqual(
                collect["verdict"],
                "unknown",
            )
            self.assertTrue(
                collect[
                    "knownGapPreserved"
                ]
            )

            issue = self.operation_result(
                args,
                harness,
                "issue-record",
            )

            self.assertEqual(
                issue["reason"],
                (
                    "collect_result_"
                    "prerequisite_not_pass"
                ),
            )
            self.assertTrue(
                issue["knownGapPreserved"]
            )

            final = self.final_result(
                args,
                harness,
            )

            self.assertTrue(
                final[
                    "knownCollectResultGapPreserved"
                ]
            )

    def test_collect_result_missing_receipt_is_fail_and_issue_is_unknown_prerequisite(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            native_results = (
                self.default_native_results()
            )
            native_results[
                "collect-result"
            ]["broker"][
                "resultReceiptId"
            ] = None

            with self.harness(
                args,
                native_results=native_results,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                1,
            )

            collect = self.operation_result(
                args,
                harness,
                "collect-result",
            )
            self.assertEqual(
                collect["verdict"],
                "fail",
            )

            issue = self.operation_result(
                args,
                harness,
                "issue-record",
            )
            self.assertEqual(
                issue["verdict"],
                "unknown",
            )
            self.assertEqual(
                issue["reason"],
                (
                    "collect_result_"
                    "prerequisite_not_pass"
                ),
            )
            self.assertTrue(
                issue["knownGapPreserved"]
            )

            self.assertTrue(
                self.final_result(
                    args,
                    harness,
                )[
                    "knownCollectResultGapPreserved"
                ]
            )

    def test_issue_record_success_merges_gh_checks_and_verifies_both_receipts_once(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            gh_checks = [
                I1.assertion(
                    "gh-exactly-once",
                    True,
                    {"count": 1},
                ),
                I1.assertion(
                    "gh-body",
                    True,
                    "body",
                ),
            ]

            with self.harness(
                args,
                gh_checks=gh_checks,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                0,
            )

            issue = self.operation_result(
                args,
                harness,
                "issue-record",
            )

            names = {
                item["name"]:
                    item["verdict"]
                for item
                in issue["checks"]
            }

            self.assertEqual(
                names["gh-exactly-once"],
                "pass",
            )
            self.assertEqual(
                names["gh-body"],
                "pass",
            )
            self.assertEqual(
                names["input-ack-exactly-once"],
                "pass",
            )
            self.assertEqual(
                names["result-ack-exactly-once"],
                "pass",
            )

            harness[
                "validate_gh_store"
            ].assert_called_once()

            self.assertEqual(
                len(
                    harness[
                        "receipt_count"
                    ].call_args_list
                ),
                2,
            )

    def test_issue_record_storage_exception_becomes_ack_readback_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            def storage_failure(
                *unused,
            ):
                raise RuntimeError(
                    "db unavailable"
                )

            with self.harness(
                args,
                storage_side_effect=(
                    storage_failure
                ),
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                2,
            )

            issue = self.operation_result(
                args,
                harness,
                "issue-record",
            )

            ack = next(
                item
                for item
                in issue["checks"]
                if item["name"]
                == "ack-readback"
            )

            self.assertEqual(
                ack["verdict"],
                "unknown",
            )

            harness[
                "receipt_count"
            ].assert_not_called()

    def test_observe_owner_maps_states_without_collapsing_unknown(
        self,
    ):
        cases = (
            ("observed", "owned", "pass"),
            (
                "stopped_for_unknown",
                None,
                "unknown",
            ),
            ("stopped", None, "fail"),
            ("error", None, "fail"),
            ("future-state", None, "unknown"),
        )

        for (
            state,
            owner_status,
            expected_verdict,
        ) in cases:
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    args = self.make_args(root)

                    native_results = (
                        self.default_native_results()
                    )

                    broker = self.common_broker(
                        operation="observe-owner",
                        request_id=(
                            f"observe-{self.RUN_ID}"
                        ),
                        state=state,
                    )

                    if owner_status is not None:
                        broker[
                            "ownerStatus"
                        ] = owner_status

                    native_results[
                        "observe-owner"
                    ] = {
                        "verdict": "pass",
                        "broker": broker,
                    }

                    with self.harness(
                        args,
                        native_results=native_results,
                    ) as harness:
                        status = I1.run_i1(
                            args
                        )

                    observe = (
                        self.operation_result(
                            args,
                            harness,
                            "observe-owner",
                        )
                    )

                    self.assertEqual(
                        observe["verdict"],
                        expected_verdict,
                    )

                    names = {
                        item["name"]
                        for item
                        in observe["checks"]
                    }

                    self.assertEqual(
                        (
                            "owner-status-not-collapsed"
                            in names
                        ),
                        state == "observed",
                    )

                    self.assertEqual(
                        status,
                        {
                            "pass": 0,
                            "fail": 1,
                            "unknown": 2,
                        }[
                            expected_verdict
                        ],
                    )

    def test_validate_identity_receives_exact_context_and_result_is_written(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            for value in (
                None,
                "",
                "/tmp/live-identity.json",
            ):
                with self.subTest(
                    live_identity_json=value
                ):
                    args = self.make_args(
                        root,
                        live_identity_json=value,
                    )

                    identity = {
                        "schemaVersion": 1,
                        "verdict": "pass",
                        "checks": [],
                    }

                    with self.harness(
                        args,
                        identity=identity,
                    ) as harness:
                        status = I1.run_i1(
                            args
                        )

                    self.assertEqual(
                        status,
                        0,
                    )

                    kwargs = harness[
                        "validate_identity"
                    ].call_args.kwargs

                    self.assertEqual(
                        kwargs["gate_repo"],
                        Path(
                            args.gate_repo
                        ).resolve(),
                    )
                    self.assertEqual(
                        kwargs["run_root"],
                        Path(
                            args.run_root
                        ).resolve(),
                    )
                    self.assertEqual(
                        kwargs["gate_team"],
                        self.GATE_TEAM,
                    )
                    self.assertEqual(
                        kwargs[
                            "live_identity_json"
                        ],
                        (
                            Path(value)
                            if value
                            else None
                        ),
                    )

                    identity_path = (
                        Path(
                            args.artifact_dir
                        ).resolve()
                        / "I1"
                        / "identity"
                        / "result.json"
                    )

                    self.assertEqual(
                        self.written(
                            harness,
                            identity_path,
                        ),
                        identity,
                    )

    def test_prerequisite_skips_use_specific_unknowns_not_operation_not_observed(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            native_results = (
                self.default_native_results()
            )
            native_results[
                "receive"
            ] = {
                "verdict": "unknown",
                "reason": "synthetic",
            }

            with self.harness(
                args,
                native_results=native_results,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                2,
            )

            for operation in (
                "delegate",
                "collect-result",
                "issue-record",
            ):
                result = (
                    self.operation_result(
                        args,
                        harness,
                        operation,
                    )
                )

                self.assertEqual(
                    result["reason"],
                    (
                        "receive_prerequisite_"
                        "not_pass"
                    ),
                )
                self.assertNotEqual(
                    result["reason"],
                    "operation_not_observed",
                )

    def test_overall_pass_writes_complete_result_and_returns_zero(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)
            native = self.make_native(args)

            with self.harness(
                args,
                native=native,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                0,
            )

            result = self.final_result(
                args,
                harness,
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["check"],
                "I1",
            )
            self.assertEqual(
                result["runId"],
                self.RUN_ID,
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
                result["verdict"],
                "pass",
            )
            self.assertFalse(
                result[
                    "knownCollectResultGapPreserved"
                ]
            )

            self.assertEqual(
                set(
                    result["operations"]
                ),
                {
                    "receive",
                    "delegate",
                    "collect-result",
                    "issue-record",
                    "observe-owner",
                },
            )

    def test_overall_fail_dominates_unknown_and_returns_one(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            native_results = (
                self.default_native_results()
            )
            native_results[
                "receive"
            ] = {
                "verdict": "fail",
                "reason": "definite-failure",
            }
            native_results[
                "observe-owner"
            ] = {
                "verdict": "unknown",
                "reason": "unobservable",
            }

            with self.harness(
                args,
                native_results=native_results,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                1,
            )
            self.assertEqual(
                self.final_result(
                    args,
                    harness,
                )["verdict"],
                "fail",
            )

    def test_overall_unknown_without_fail_returns_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)

            identity = {
                "schemaVersion": 1,
                "verdict": "unknown",
                "checks": [],
            }

            with self.harness(
                args,
                identity=identity,
            ) as harness:
                status = I1.run_i1(args)

            self.assertEqual(
                status,
                2,
            )
            self.assertEqual(
                self.final_result(
                    args,
                    harness,
                )["verdict"],
                "unknown",
            )

    def test_final_known_collect_gap_flag_is_true_for_any_nonpass_collect_verdict(
        self,
    ):
        for collect_verdict in (
            "fail",
            "unknown",
        ):
            with self.subTest(
                collect_verdict=collect_verdict
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    args = self.make_args(root)

                    native_results = (
                        self.default_native_results()
                    )
                    native_results[
                        "collect-result"
                    ] = {
                        "verdict":
                            collect_verdict,
                        "reason":
                            "synthetic-collect",
                    }

                    with self.harness(
                        args,
                        native_results=native_results,
                    ) as harness:
                        status = I1.run_i1(
                            args
                        )

                    self.assertEqual(
                        status,
                        (
                            1
                            if collect_verdict
                            == "fail"
                            else 2
                        ),
                    )

                    self.assertTrue(
                        self.final_result(
                            args,
                            harness,
                        )[
                            "knownCollectResultGapPreserved"
                        ]
                    )

    def test_native_stop_runs_after_success_and_after_exception_inside_try(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)
            native = self.make_native(args)

            with self.harness(
                args,
                native=native,
            ):
                self.assertEqual(
                    I1.run_i1(args),
                    0,
                )

            native.stop.assert_called_once()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = self.make_args(root)
            native = self.make_native(args)

            native.start.side_effect = (
                RuntimeError(
                    "native start failed"
                )
            )

            with self.harness(
                args,
                native=native,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "native start failed",
                ):
                    I1.run_i1(args)

            native.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
