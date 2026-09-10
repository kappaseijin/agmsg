"""Round A tests for scripts/lib/pilot-gate-f3.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
F3_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_F3_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-f3.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_f3",
    F3_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate F3 helper: {F3_HELPER}"
    )

F3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F3)


class PilotGateF3RoundA(unittest.TestCase):
    def make_profile(self, command: str, **handler_extra):
        handler = {
            "type": "command",
            "command": command,
            "args": [],
        }
        handler.update(handler_extra)
        return {
            "schemaVersion": 1,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/tmp/pretool",
                                "args": [],
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [handler],
                    }
                ],
            },
        }

    def write_json_file(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def test_load_module_loads_real_module_and_missing_file_raises_file_not_found(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            module_path = root / "fixture_module.py"
            module_path.write_text(
                "VALUE = 42\n",
                encoding="utf-8",
            )

            module = F3.load_module(
                module_path,
                "pilot_gate_f3_fixture_module",
            )

            self.assertEqual(module.VALUE, 42)

            with self.assertRaises(FileNotFoundError):
                F3.load_module(
                    root / "missing.py",
                    "pilot_gate_f3_missing_module",
                )

    def test_atomic_json_creates_parent_is_utf8_sorted_and_replaces_existing(
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

            F3.atomic_json(output, value)

            raw = output.read_text(encoding="utf-8")
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
            self.assertIn("日本語", raw)
            self.assertNotIn(r"\u65e5", raw)

            F3.atomic_json(
                output,
                {"replaced": True},
            )
            self.assertEqual(
                F3.read_json(output),
                {"replaced": True},
            )

    def test_atomic_bytes_writes_payload_mode_and_replaces_existing(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "nested" / "payload.bin"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"old")
            output.chmod(0o600)

            fixed_ns = 123456789
            real_replace = os.replace

            with mock.patch.object(
                F3.time,
                "monotonic_ns",
                return_value=fixed_ns,
            ), mock.patch.object(
                F3.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                F3.atomic_bytes(
                    output,
                    b"new-payload",
                    0o640,
                )

            expected_tmp = output.with_name(
                f".{output.name}.{os.getpid()}.{fixed_ns}.tmp"
            )
            replace_mock.assert_called_once_with(
                expected_tmp,
                output,
            )
            self.assertEqual(
                output.read_bytes(),
                b"new-payload",
            )
            self.assertEqual(
                stat.S_IMODE(output.stat().st_mode),
                0o640,
            )
            self.assertFalse(expected_tmp.exists())

    def test_atomic_bytes_o_excl_rejects_existing_exact_temp_path(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "payload.bin"
            fixed_ns = 555
            tmp = output.with_name(
                f".{output.name}.{os.getpid()}.{fixed_ns}.tmp"
            )
            tmp.write_bytes(b"sentinel")

            with mock.patch.object(
                F3.time,
                "monotonic_ns",
                return_value=fixed_ns,
            ):
                with self.assertRaises(FileExistsError):
                    F3.atomic_bytes(
                        output,
                        b"new",
                        0o600,
                    )

            self.assertEqual(
                tmp.read_bytes(),
                b"sentinel",
            )
            self.assertFalse(output.exists())

    def test_atomic_bytes_includes_o_nofollow_when_available(
        self,
    ):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW unavailable")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "payload.bin"
            seen_flags = []
            real_open = os.open

            def recording_open(path, flags, mode=0o777):
                seen_flags.append(flags)
                return real_open(path, flags, mode)

            with mock.patch.object(
                F3.os,
                "open",
                side_effect=recording_open,
            ):
                F3.atomic_bytes(
                    output,
                    b"payload",
                    0o600,
                )

            self.assertEqual(len(seen_flags), 1)
            self.assertTrue(seen_flags[0] & os.O_EXCL)
            self.assertTrue(seen_flags[0] & os.O_NOFOLLOW)

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
                F3.read_json(path),
                {"value": 123},
            )

    def test_digest_bytes_and_digest_json_match_exact_contract(
        self,
    ):
        payload = b"pilot-gate-f3\x00payload"
        self.assertEqual(
            F3.digest_bytes(payload),
            "sha256:"
            + hashlib.sha256(payload).hexdigest(),
        )

        value = {
            "z": 1,
            "日本語": "河童",
            "a": [True, None],
        }
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            F3.digest_json(value),
            "sha256:"
            + hashlib.sha256(raw).hexdigest(),
        )

    def test_assertion_and_verdict_priority(
        self,
    ):
        passed = F3.assertion(
            "pass-check",
            True,
            "p",
        )
        failed = F3.assertion(
            "fail-check",
            False,
            "f",
        )
        unknown = F3.assertion(
            "unknown-check",
            None,
            "u",
        )

        self.assertEqual(
            passed,
            {
                "name": "pass-check",
                "verdict": "pass",
                "detail": "p",
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
            F3.verdict_from_assertions(
                [passed, unknown, failed]
            ),
            "fail",
        )
        self.assertEqual(
            F3.verdict_from_assertions(
                [passed, unknown]
            ),
            "unknown",
        )
        self.assertEqual(
            F3.verdict_from_assertions([passed]),
            "pass",
        )
        self.assertEqual(
            F3.verdict_from_assertions([]),
            "pass",
        )

    def test_require_regular_default_allows_nonexecutable_but_executable_mode_requires_it(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            plain = root / "plain"
            plain.write_text(
                "data\n",
                encoding="utf-8",
            )
            plain.chmod(0o600)

            F3.require_regular(plain)

            with self.assertRaisesRegex(
                RuntimeError,
                "not executable",
            ):
                F3.require_regular(
                    plain,
                    executable=True,
                )

            plain.chmod(0o700)
            F3.require_regular(
                plain,
                executable=True,
            )

    def test_require_regular_rejects_symlink_and_directory(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target = root / "target"
            target.write_text(
                "data\n",
                encoding="utf-8",
            )
            link = root / "link"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            with self.assertRaisesRegex(
                RuntimeError,
                "not regular file",
            ):
                F3.require_regular(link)

            directory = root / "directory"
            directory.mkdir()
            with self.assertRaisesRegex(
                RuntimeError,
                "not regular file",
            ):
                F3.require_regular(directory)

    def test_load_profile_accepts_valid_pre_and_post_hooks_without_executable_requirement(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            profile_value = self.make_profile(
                "/tmp/posttool"
            )
            path = root / "settings.local.json"
            self.write_json_file(
                path,
                profile_value,
            )
            path.chmod(0o600)

            self.assertEqual(
                F3.load_profile(path),
                profile_value,
            )

    def test_load_profile_rejects_invalid_root_hooks_pre_and_post(
        self,
    ):
        cases = (
            (
                [],
                "profile root is not object",
            ),
            (
                {},
                "profile hooks is not object",
            ),
            (
                {"hooks": []},
                "profile hooks is not object",
            ),
            (
                {
                    "hooks": {
                        "PreToolUse": [],
                        "PostToolUse": [
                            {"hooks": [{"type": "command"}]}
                        ],
                    }
                },
                "PreToolUse is unavailable",
            ),
            (
                {
                    "hooks": {
                        "PreToolUse": [
                            {"hooks": [{"type": "command"}]}
                        ],
                        "PostToolUse": [],
                    }
                },
                "PostToolUse is unavailable",
            ),
            (
                {
                    "hooks": {
                        "PreToolUse": [
                            {"hooks": [{"type": "command"}]}
                        ],
                    }
                },
                "PostToolUse is unavailable",
            ),
        )

        for value, message in cases:
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    path = root / "settings.local.json"
                    self.write_json_file(path, value)

                    with self.assertRaisesRegex(
                        RuntimeError,
                        message,
                    ):
                        F3.load_profile(path)

    def test_find_single_posttool_handler_returns_exact_single_handler(
        self,
    ):
        profile = self.make_profile(
            "/tmp/posttool",
            timeout=5,
            async_=True,
        )
        handler = (
            profile["hooks"]["PostToolUse"][0]
            ["hooks"][0]
        )

        gi, hi, found = (
            F3.find_single_posttool_handler(
                profile
            )
        )

        self.assertEqual((gi, hi), (0, 0))
        self.assertIs(found, handler)

    def test_find_single_posttool_handler_rejects_invalid_shapes_immediately(
        self,
    ):
        valid = self.make_profile("/tmp/posttool")

        cases = []

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0] = "bad-group"
        cases.append(
            (
                value,
                "PostToolUse matcher group is not object",
            )
        )

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0]["hooks"] = []
        cases.append(
            (
                value,
                "PostToolUse matcher group hooks invalid",
            )
        )

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0]["hooks"][0] = "bad-handler"
        cases.append(
            (
                value,
                "PostToolUse handler is not object",
            )
        )

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0]["hooks"][0]["type"] = "prompt"
        cases.append(
            (
                value,
                "PostToolUse contains unsupported non-command handler",
            )
        )

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = ""
        cases.append(
            (
                value,
                "PostToolUse command is invalid",
            )
        )

        value = json.loads(json.dumps(valid))
        value["hooks"]["PostToolUse"][0]["hooks"][0]["args"] = ["--bad"]
        cases.append(
            (
                value,
                "PostToolUse command args unsupported",
            )
        )

        for value, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    RuntimeError,
                    message,
                ):
                    F3.find_single_posttool_handler(
                        value
                    )

    def test_find_single_posttool_handler_does_not_silently_skip_invalid_handler_before_valid_one(
        self,
    ):
        profile = self.make_profile("/tmp/valid")
        profile["hooks"]["PostToolUse"][0][
            "hooks"
        ].insert(
            0,
            {
                "type": "prompt",
                "command": "/tmp/ignored-if-f2-style",
                "args": [],
            },
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "unsupported non-command handler",
        ):
            F3.find_single_posttool_handler(
                profile
            )

    def test_find_single_posttool_handler_rejects_multiple_command_handlers_as_ambiguous(
        self,
    ):
        profile = self.make_profile("/tmp/one")
        profile["hooks"]["PostToolUse"][0][
            "hooks"
        ].append(
            {
                "type": "command",
                "command": "/tmp/two",
                "args": None,
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "PostToolUse contract ambiguous: "
                "commandHandlers=2"
            ),
        ):
            F3.find_single_posttool_handler(
                profile
            )

    def test_make_fault_profile_removes_only_posttool_from_deep_copy(
        self,
    ):
        original = self.make_profile(
            "/tmp/posttool"
        )
        original["nested"] = {
            "values": [1, 2]
        }
        before = json.loads(
            json.dumps(original)
        )

        faulted = F3.make_fault_profile(
            original
        )

        self.assertNotIn(
            "PostToolUse",
            faulted["hooks"],
        )
        self.assertIn(
            "PreToolUse",
            faulted["hooks"],
        )
        self.assertEqual(original, before)

        faulted["nested"]["values"].append(3)
        self.assertEqual(
            original["nested"]["values"],
            [1, 2],
        )

    def test_make_fault_profile_requires_posttool_key(
        self,
    ):
        for original in (
            {},
            {"hooks": []},
            {
                "hooks": {
                    "PreToolUse": []
                }
            },
        ):
            with self.subTest(original=original):
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "PostToolUse unavailable "
                        "before fault"
                    ),
                ):
                    F3.make_fault_profile(original)

    def test_make_control_profile_replaces_only_command_and_args_preserving_extra_fields_and_original(
        self,
    ):
        original = self.make_profile(
            "/tmp/original-posttool",
            timeout=9,
            async_=True,
            custom={
                "nested": ["keep"]
            },
        )
        before = json.loads(
            json.dumps(original)
        )
        wrapper = Path(
            "/tmp/posttool-wrapper.py"
        )

        control = F3.make_control_profile(
            original,
            wrapper,
        )

        handler = (
            control["hooks"]["PostToolUse"][0]
            ["hooks"][0]
        )
        self.assertEqual(
            handler["command"],
            str(wrapper),
        )
        self.assertEqual(
            handler["args"],
            [],
        )
        self.assertEqual(
            handler["timeout"],
            9,
        )
        self.assertIs(
            handler["async_"],
            True,
        )
        self.assertEqual(
            handler["custom"],
            {
                "nested": ["keep"]
            },
        )
        self.assertEqual(
            original,
            before,
        )

        handler["custom"]["nested"].append(
            "changed"
        )
        self.assertEqual(
            original["hooks"]["PostToolUse"][0]
            ["hooks"][0]["custom"],
            {
                "nested": ["keep"]
            },
        )

    def test_make_control_profile_propagates_handler_contract_errors(
        self,
    ):
        profile = self.make_profile("/tmp/one")
        profile["hooks"]["PostToolUse"][0][
            "hooks"
        ].append(
            {
                "type": "command",
                "command": "/tmp/two",
                "args": [],
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "commandHandlers=2",
        ):
            F3.make_control_profile(
                profile,
                Path("/tmp/wrapper"),
            )

        with mock.patch.object(
            F3,
            "find_single_posttool_handler",
            side_effect=RuntimeError(
                "PostToolUse contract ambiguous: commandHandlers=0"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "commandHandlers=0",
            ):
                F3.make_control_profile(
                    self.make_profile("/tmp/one"),
                    Path("/tmp/wrapper"),
                )

    def test_encode_profile_is_sorted_pretty_utf8_with_single_trailing_newline(
        self,
    ):
        value = {
            "z": 1,
            "日本語": "河童",
            "a": {
                "b": True,
            },
        }

        payload = F3.encode_profile(value)

        self.assertEqual(
            payload,
            (
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8"),
        )

    def test_read_jsonl_missing_empty_valid_nonobject_invalid_directory_and_symlink(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            missing = root / "missing.jsonl"
            self.assertEqual(
                F3.read_jsonl(missing),
                [],
            )

            empty = root / "empty.jsonl"
            empty.write_text(
                "\n\n",
                encoding="utf-8",
            )
            self.assertEqual(
                F3.read_jsonl(empty),
                [],
            )

            valid = root / "valid.jsonl"
            valid.write_text(
                "\n".join(
                    [
                        "",
                        json.dumps(
                            {"a": 1}
                        ),
                        json.dumps(
                            {"b": "日本語"}
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                F3.read_jsonl(valid),
                [
                    {"a": 1},
                    {"b": "日本語"},
                ],
            )

            nonobject = root / "nonobject.jsonl"
            nonobject.write_text(
                (
                    json.dumps(
                        {"before": 1}
                    )
                    + "\n"
                    + json.dumps([1, 2])
                    + "\n"
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                F3.read_jsonl(nonobject)
            )

            invalid = root / "invalid.jsonl"
            invalid.write_text(
                (
                    json.dumps(
                        {"before": 1}
                    )
                    + "\n"
                    + "{bad-json\n"
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                F3.read_jsonl(invalid)
            )

            directory = root / "directory"
            directory.mkdir()
            self.assertIsNone(
                F3.read_jsonl(directory)
            )

            target = root / "target.jsonl"
            target.write_text(
                '{"value":1}\n',
                encoding="utf-8",
            )
            link = root / "link.jsonl"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            self.assertIsNone(
                F3.read_jsonl(link)
            )

    def test_read_jsonl_dangling_symlink_is_treated_as_missing_by_current_order(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            link = root / "dangling.jsonl"
            try:
                link.symlink_to(
                    root / "missing-target.jsonl"
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            self.assertEqual(
                F3.read_jsonl(link),
                [],
            )

    def test_records_for_tool_filters_and_propagates_none(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "records.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "toolUseId": "one",
                                "event": "started",
                            }
                        ),
                        json.dumps(
                            {
                                "toolUseId": "two",
                                "event": "started",
                            }
                        ),
                        json.dumps(
                            {
                                "toolUseId": "one",
                                "event": "completed",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F3.records_for_tool(
                    path,
                    "one",
                ),
                [
                    {
                        "toolUseId": "one",
                        "event": "started",
                    },
                    {
                        "toolUseId": "one",
                        "event": "completed",
                    },
                ],
            )

            with mock.patch.object(
                F3,
                "read_jsonl",
                return_value=None,
            ):
                self.assertIsNone(
                    F3.records_for_tool(
                        path,
                        "one",
                    )
                )

    def test_write_posttool_wrapper_generates_expected_source_and_mode(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            wrapper = root / "wrapper.py"
            record_path = root / "records" / "posttool.jsonl"
            original_command = "cat >&2; exit 7"

            F3.write_posttool_wrapper(
                wrapper,
                original_command,
                record_path,
            )

            source = wrapper.read_text(
                encoding="utf-8"
            )

            self.assertTrue(
                source.startswith(
                    "#!/usr/bin/env python3\n"
                )
            )
            self.assertIn(
                (
                    "ORIGINAL_COMMAND = "
                    f"{original_command!r}"
                ),
                source,
            )
            self.assertIn(
                (
                    "RECORD_PATH = "
                    f"{str(record_path)!r}"
                ),
                source,
            )
            self.assertIn(
                "payload = sys.stdin.buffer.read()",
                source,
            )
            self.assertIn(
                'raw_id = value.get("tool_use_id")',
                source,
            )
            self.assertIn(
                '"event": "started"',
                source,
            )
            self.assertIn(
                (
                    '["/bin/sh", "-c", '
                    "ORIGINAL_COMMAND]"
                ),
                source,
            )
            self.assertIn(
                "input=payload",
                source,
            )
            self.assertIn(
                "stdout=subprocess.PIPE",
                source,
            )
            self.assertIn(
                "stderr=subprocess.PIPE",
                source,
            )
            self.assertIn(
                "sys.stdout.buffer.write(completed.stdout)",
                source,
            )
            self.assertIn(
                "sys.stderr.buffer.write(completed.stderr)",
                source,
            )
            self.assertIn(
                '"event": "completed"',
                source,
            )
            self.assertIn(
                '"exitStatus": completed.returncode',
                source,
            )
            self.assertIn(
                "raise SystemExit(completed.returncode)",
                source,
            )

            compile(
                source,
                "<posttool-wrapper>",
                "exec",
            )

            self.assertEqual(
                stat.S_IMODE(
                    wrapper.stat().st_mode
                ),
                0o700,
            )

    def test_write_posttool_wrapper_executes_command_forwards_payload_output_and_exit_status(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            wrapper = root / "wrapper.py"
            record_path = root / "records" / "posttool.jsonl"
            original_command = (
                "cat >&2; "
                "printf 'wrapped-stdout'; "
                "exit 7"
            )

            F3.write_posttool_wrapper(
                wrapper,
                original_command,
                record_path,
            )

            payload = json.dumps(
                {
                    "tool_use_id": "tool-1",
                    "value": "日本語",
                },
                ensure_ascii=False,
            ).encode("utf-8")

            completed = subprocess.run(
                [sys.executable, "-S", str(wrapper)],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                7,
            )
            self.assertEqual(
                completed.stdout,
                b"wrapped-stdout",
            )
            self.assertEqual(
                completed.stderr,
                payload,
            )

            records = F3.read_jsonl(
                record_path
            )
            self.assertIsNotNone(records)
            self.assertEqual(
                len(records),
                2,
            )
            self.assertEqual(
                records[0]["event"],
                "started",
            )
            self.assertEqual(
                records[0]["toolUseId"],
                "tool-1",
            )
            self.assertIsNone(
                records[0]["parseError"]
            )
            self.assertEqual(
                records[1]["event"],
                "completed",
            )
            self.assertEqual(
                records[1]["toolUseId"],
                "tool-1",
            )
            self.assertEqual(
                records[1]["exitStatus"],
                7,
            )
            self.assertEqual(
                records[0]["pid"],
                completed.pid
                if hasattr(completed, "pid")
                else records[0]["pid"],
            )
            self.assertEqual(
                records[0]["pid"],
                records[1]["pid"],
            )
            self.assertLessEqual(
                records[0]["monotonic"],
                records[1]["monotonic"],
            )

    def test_write_posttool_wrapper_invalid_json_records_parse_error_and_still_runs_original_command(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            wrapper = root / "wrapper.py"
            record_path = root / "records" / "posttool.jsonl"
            original_command = (
                "cat; "
                "printf 'stderr-ok' >&2; "
                "exit 4"
            )

            F3.write_posttool_wrapper(
                wrapper,
                original_command,
                record_path,
            )

            payload = b"{bad-json"

            completed = subprocess.run(
                [sys.executable, "-S", str(wrapper)],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                4,
            )
            self.assertEqual(
                completed.stdout,
                payload,
            )
            self.assertEqual(
                completed.stderr,
                b"stderr-ok",
            )

            records = F3.read_jsonl(
                record_path
            )
            self.assertIsNotNone(records)
            self.assertEqual(
                len(records),
                2,
            )
            self.assertEqual(
                records[0]["event"],
                "started",
            )
            self.assertIsNone(
                records[0]["toolUseId"]
            )
            self.assertEqual(
                records[0]["parseError"],
                "JSONDecodeError",
            )
            self.assertEqual(
                records[1]["event"],
                "completed",
            )
            self.assertIsNone(
                records[1]["toolUseId"]
            )
            self.assertEqual(
                records[1]["exitStatus"],
                4,
            )


if __name__ == "__main__":
    unittest.main()
