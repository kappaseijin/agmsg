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



class PilotGateF3RoundB(unittest.TestCase):
    TOOL_ID = "tool-123"

    class WalkI1:
        @staticmethod
        def walk_json(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from PilotGateF3RoundB.WalkI1.walk_json(
                        child
                    )
            elif isinstance(value, list):
                for child in value:
                    yield from PilotGateF3RoundB.WalkI1.walk_json(
                        child
                    )

        @staticmethod
        def json_content_text(value):
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                text = value.get("text")
                return text if isinstance(text, str) else ""
            if isinstance(value, list):
                parts = []
                for item in value:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                return "\n".join(parts)
            return ""

    def make_collector_fixture(
        self,
        root: Path,
        generation=7,
    ):
        root = root.resolve()

        repo = root / "repo"
        collector = (
            repo
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
        collector.chmod(0o700)

        binding = root / "binding.json"
        binding.write_text(
            json.dumps(
                {"generation": generation}
            )
            + "\n",
            encoding="utf-8",
        )

        claude_config = root / "claude"
        claude_config.mkdir()

        state_dir = root / "collector-state"
        artifact = root / "artifact"

        return (
            collector,
            binding,
            claude_config,
            state_dir,
            artifact,
        )

    def make_collector_i1(self):
        i1 = mock.Mock()
        i1.run.side_effect = [
            mock.Mock(
                returncode=3,
                stdout="discover-out",
                stderr="discover-err",
            ),
            mock.Mock(
                returncode=4,
                stdout="scan-out",
                stderr="scan-err",
            ),
        ]
        return i1

    def test_collector_observation_sets_copied_environment_runs_discover_then_scan_and_writes_artifacts(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                collector,
                binding,
                claude_config,
                state_dir,
                artifact,
            ) = self.make_collector_fixture(
                root
            )

            i1 = self.make_collector_i1()
            env = {
                "ORIGINAL": "yes",
            }

            result = F3.collector_observation(
                i1,
                collector,
                binding,
                claude_config,
                state_dir,
                self.TOOL_ID,
                env,
                artifact,
            )

            self.assertTrue(
                state_dir.is_dir()
            )
            self.assertEqual(
                env,
                {
                    "ORIGINAL": "yes",
                },
            )
            self.assertEqual(
                i1.run.call_count,
                2,
            )

            discover_call, scan_call = (
                i1.run.call_args_list
            )

            self.assertEqual(
                discover_call.args[0],
                [
                    str(collector),
                    "discover",
                ],
            )
            self.assertEqual(
                scan_call.args[0],
                [
                    str(collector),
                    "scan",
                ],
            )

            for call in (
                discover_call,
                scan_call,
            ):
                self.assertEqual(
                    call.kwargs["cwd"],
                    collector.parent.parent,
                )

                cenv = call.kwargs["env"]

                self.assertEqual(
                    cenv["ORIGINAL"],
                    "yes",
                )
                self.assertEqual(
                    cenv[
                        "AGMSG_PM_BINDING_FILE"
                    ],
                    str(binding),
                )
                self.assertEqual(
                    cenv[
                        "AGMSG_PM_COLLECTOR_STATE_DIR"
                    ],
                    str(state_dir),
                )
                self.assertEqual(
                    cenv[
                        "CLAUDE_CONFIG_DIR"
                    ],
                    str(claude_config),
                )

            self.assertEqual(
                F3.read_json(
                    artifact
                    / "collector-discover.json"
                ),
                {
                    "exitStatus": 3,
                    "stdout": "discover-out",
                    "stderr": "discover-err",
                },
            )
            self.assertEqual(
                F3.read_json(
                    artifact
                    / "collector-scan.json"
                ),
                {
                    "exitStatus": 4,
                    "stdout": "scan-out",
                    "stderr": "scan-err",
                },
            )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                "collector_observation_missing",
            )
            self.assertEqual(
                result["matches"],
                [],
            )

    def test_collector_observation_uses_binding_generation_and_identifies_exact_match(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                collector,
                binding,
                claude_config,
                state_dir,
                artifact,
            ) = self.make_collector_fixture(
                root,
                generation="9",
            )

            i1 = self.make_collector_i1()
            state_dir.mkdir()

            ledger = (
                state_dir
                / "generation-9.observations.jsonl"
            )
            expected = {
                "toolUseId": self.TOOL_ID,
                "completionState": "success",
            }

            ledger.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "toolUseId":
                                    "other",
                                "completionState":
                                    "failure",
                            }
                        ),
                        json.dumps(expected),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = F3.collector_observation(
                i1,
                collector,
                binding,
                claude_config,
                state_dir,
                self.TOOL_ID,
                {},
                artifact,
            )

            self.assertEqual(
                result,
                {
                    "verdict": "pass",
                    "reason":
                        "collector_observation_identified",
                    "discoverExit": 3,
                    "scanExit": 4,
                    "observation": expected,
                },
            )

    def test_collector_observation_invalid_ledger_returns_immediate_unknown_and_discards_partial_matches(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                collector,
                binding,
                claude_config,
                state_dir,
                artifact,
            ) = self.make_collector_fixture(
                root
            )

            i1 = self.make_collector_i1()
            state_dir.mkdir()

            ledger = (
                state_dir
                / "generation-7.observations.jsonl"
            )
            ledger.write_text(
                json.dumps(
                    {
                        "toolUseId":
                            self.TOOL_ID,
                        "completionState":
                            "success",
                    }
                )
                + "\n{bad-json\n",
                encoding="utf-8",
            )

            result = F3.collector_observation(
                i1,
                collector,
                binding,
                claude_config,
                state_dir,
                self.TOOL_ID,
                {},
                artifact,
            )

            self.assertEqual(
                result,
                {
                    "verdict": "unknown",
                    "reason":
                        (
                            "collector_ledger_unreadable:"
                            "JSONDecodeError"
                        ),
                    "discoverExit": 3,
                    "scanExit": 4,
                },
            )
            self.assertNotIn(
                "matches",
                result,
            )

    def test_collector_observation_zero_multiple_and_symlink_ledger_are_unknown(
        self,
    ):
        for (
            mode,
            expected_reason,
            expected_count,
        ) in (
            (
                "zero",
                "collector_observation_missing",
                0,
            ),
            (
                "multiple",
                "collector_observation_ambiguous",
                2,
            ),
            (
                "symlink",
                "collector_observation_missing",
                0,
            ),
        ):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    (
                        collector,
                        binding,
                        claude_config,
                        state_dir,
                        artifact,
                    ) = (
                        self.make_collector_fixture(
                            root
                        )
                    )

                    i1 = (
                        self.make_collector_i1()
                    )
                    state_dir.mkdir()

                    ledger = (
                        state_dir
                        / (
                            "generation-7."
                            "observations.jsonl"
                        )
                    )

                    if mode == "multiple":
                        ledger.write_text(
                            "".join(
                                json.dumps(
                                    {
                                        "toolUseId":
                                            self.TOOL_ID,
                                        "n": n,
                                    }
                                )
                                + "\n"
                                for n in range(2)
                            ),
                            encoding="utf-8",
                        )
                    elif mode == "symlink":
                        target = (
                            root
                            / "real-ledger.jsonl"
                        )
                        target.write_text(
                            json.dumps(
                                {
                                    "toolUseId":
                                        self.TOOL_ID
                                }
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        try:
                            ledger.symlink_to(
                                target
                            )
                        except OSError as exc:
                            self.skipTest(
                                (
                                    "symlink "
                                    f"unavailable: {exc}"
                                )
                            )

                    result = (
                        F3.collector_observation(
                            i1,
                            collector,
                            binding,
                            claude_config,
                            state_dir,
                            self.TOOL_ID,
                            {},
                            artifact,
                        )
                    )

                    self.assertEqual(
                        result["verdict"],
                        "unknown",
                    )
                    self.assertEqual(
                        result["reason"],
                        expected_reason,
                    )
                    self.assertEqual(
                        len(result["matches"]),
                        expected_count,
                    )

    def test_binding_profile_check_none_and_unreadable_are_unknown(
        self,
    ):
        payload = b'{"profile":true}\n'
        native = mock.Mock()
        native.binding = None

        self.assertEqual(
            F3.binding_profile_check(
                native,
                payload,
            ),
            F3.assertion(
                "binding-profile-digest",
                None,
                "binding unavailable",
            ),
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native.binding = (
                root
                / "missing-binding.json"
            )

            result = (
                F3.binding_profile_check(
                    native,
                    payload,
                )
            )

            self.assertEqual(
                result["name"],
                "binding-profile-digest",
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertTrue(
                result["detail"]
            )

    def test_binding_profile_check_pass_fail_and_detail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            binding = root / "binding.json"
            payload = b'{"profile":"value"}\n'
            expected = F3.digest_bytes(
                payload
            )

            native = mock.Mock(
                binding=binding
            )

            for actual, verdict in (
                (
                    expected,
                    "pass",
                ),
                (
                    "sha256:wrong",
                    "fail",
                ),
            ):
                with self.subTest(
                    verdict=verdict
                ):
                    binding.write_text(
                        json.dumps(
                            {
                                "profileDigest":
                                    actual
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    result = (
                        F3.binding_profile_check(
                            native,
                            payload,
                        )
                    )

                    self.assertEqual(
                        result["verdict"],
                        verdict,
                    )
                    self.assertEqual(
                        result["detail"],
                        {
                            "actual": actual,
                            "expected":
                                expected,
                            "binding":
                                str(binding),
                        },
                    )

    def test_transcript_result_missing_and_invalid_utf8_are_unknown(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            ok, detail = (
                F3.transcript_result(
                    i1,
                    root / "missing.jsonl",
                    self.TOOL_ID,
                )
            )
            self.assertIsNone(ok)
            self.assertTrue(detail)

            invalid = (
                root
                / "invalid.jsonl"
            )
            invalid.write_bytes(
                b'{"x":"\xff"}\n'
            )

            ok, detail = (
                F3.transcript_result(
                    i1,
                    invalid,
                    self.TOOL_ID,
                )
            )
            self.assertIsNone(ok)
            self.assertTrue(detail)

    def test_transcript_result_skips_bad_json_and_requires_exactly_one_match(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = (
                root
                / "transcript.jsonl"
            )

            transcript.write_text(
                "{bad-json\n"
                + json.dumps(
                    {
                        "type": "other"
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F3.transcript_result(
                    i1,
                    transcript,
                    self.TOOL_ID,
                ),
                (
                    None,
                    {
                        "resultCount": 0
                    },
                ),
            )

            node = {
                "type": "tool_result",
                "tool_use_id":
                    self.TOOL_ID,
                "is_error": False,
                "content": "ok",
            }

            transcript.write_text(
                json.dumps(
                    {
                        "nodes": [
                            node,
                            node,
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F3.transcript_result(
                    i1,
                    transcript,
                    self.TOOL_ID,
                ),
                (
                    None,
                    {
                        "resultCount": 2
                    },
                ),
            )

    def test_transcript_result_nonbool_is_error_is_unknown(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = (
                root
                / "transcript.jsonl"
            )

            transcript.write_text(
                json.dumps(
                    {
                        "type":
                            "tool_result",
                        "tool_use_id":
                            self.TOOL_ID,
                        "is_error":
                            "false",
                        "content":
                            "ignored",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F3.transcript_result(
                    i1,
                    transcript,
                    self.TOOL_ID,
                ),
                (
                    None,
                    {
                        "isError": "false"
                    },
                ),
            )

    def test_transcript_result_inverts_boolean_is_error_and_extracts_content(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = (
                root
                / "transcript.jsonl"
            )

            for (
                raw_error,
                expected_success,
            ) in (
                (
                    False,
                    True,
                ),
                (
                    True,
                    False,
                ),
            ):
                with self.subTest(
                    raw_error=raw_error
                ):
                    transcript.write_text(
                        json.dumps(
                            {
                                "outer": [
                                    {
                                        "type":
                                            "tool_result",
                                        "tool_use_id":
                                            self.TOOL_ID,
                                        "is_error":
                                            raw_error,
                                        "content": [
                                            {
                                                "text":
                                                    "line-1"
                                            },
                                            "line-2",
                                        ],
                                    }
                                ]
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    result = (
                        F3.transcript_result(
                            i1,
                            transcript,
                            self.TOOL_ID,
                        )
                    )

                    self.assertEqual(
                        result,
                        (
                            expected_success,
                            {
                                "isError":
                                    raw_error,
                                "content":
                                    "line-1\nline-2",
                            },
                        ),
                    )

    def test_classify_collector_nonpass_verdicts_map_unknown_and_fail(
        self,
    ):
        unknown = {
            "verdict": "unknown",
            "reason": "missing",
        }
        failed = {
            "verdict": "fail",
            "reason": "broken",
        }
        other = {
            "verdict": "anything-else",
        }

        self.assertEqual(
            F3.classify_collector(
                unknown,
                self.TOOL_ID,
            ),
            (
                None,
                unknown,
            ),
        )
        self.assertEqual(
            F3.classify_collector(
                failed,
                self.TOOL_ID,
            ),
            (
                False,
                failed,
            ),
        )
        self.assertEqual(
            F3.classify_collector(
                other,
                self.TOOL_ID,
            ),
            (
                False,
                other,
            ),
        )

    def test_classify_collector_pass_requires_dict_same_tool_and_success_state(
        self,
    ):
        non_dict = {
            "verdict": "pass",
            "observation": "bad",
        }
        self.assertEqual(
            F3.classify_collector(
                non_dict,
                self.TOOL_ID,
            ),
            (
                None,
                non_dict,
            ),
        )

        wrong_tool = {
            "verdict": "pass",
            "observation": {
                "toolUseId": "other",
                "completionState":
                    "success",
            },
        }
        self.assertEqual(
            F3.classify_collector(
                wrong_tool,
                self.TOOL_ID,
            ),
            (
                False,
                wrong_tool["observation"],
            ),
        )

        wrong_state = {
            "verdict": "pass",
            "observation": {
                "toolUseId":
                    self.TOOL_ID,
                "completionState":
                    "failure",
            },
        }
        self.assertEqual(
            F3.classify_collector(
                wrong_state,
                self.TOOL_ID,
            ),
            (
                False,
                wrong_state[
                    "observation"
                ],
            ),
        )

        good = {
            "verdict": "pass",
            "observation": {
                "toolUseId":
                    self.TOOL_ID,
                "completionState":
                    "success",
            },
        }
        self.assertEqual(
            F3.classify_collector(
                good,
                self.TOOL_ID,
            ),
            (
                True,
                good["observation"],
            ),
        )

    def test_prepare_observe_owner_builds_paths_request_id_config_request_and_command(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            gate_repo = (
                Path(temp).resolve()
                / "repo"
            )
            gate_repo.mkdir()

            case = "control"
            run_id = "run-123"
            team = "agmsg-team"
            generation = 7

            request_id = (
                "f3-"
                + case
                + "-"
                + hashlib.sha256(
                    run_id.encode("utf-8")
                ).hexdigest()[:16]
            )

            i1 = mock.Mock()
            request_value = {
                "request": "fixture",
            }
            command = "broker-command"

            i1.make_request.return_value = (
                request_value
            )
            i1.exact_broker_command.return_value = (
                command
            )

            (
                config,
                request,
                actual_command,
            ) = F3.prepare_observe_owner(
                i1,
                gate_repo,
                case,
                run_id,
                team,
                generation,
            )

            root = (
                gate_repo
                / ".agmsg-gate"
                / "f3"
                / case
            )
            expected_config = (
                root
                / "run-config.json"
            )
            expected_request = (
                root
                / "observe-owner.json"
            )

            self.assertTrue(
                root.is_dir()
            )
            self.assertEqual(
                config,
                expected_config,
            )
            self.assertEqual(
                request,
                expected_request,
            )
            self.assertEqual(
                actual_command,
                command,
            )

            i1.atomic_json.assert_called_once_with(
                expected_config,
                {
                    "schemaVersion": 1,
                    "runId": run_id,
                    "worker":
                        F3.F3_WORKER,
                    "testIssueNumber":
                        F3.F3_ISSUE,
                    "repo":
                        F3.F3_REPO,
                },
            )

            i1.make_request.assert_called_once_with(
                run_id,
                request_id,
                "observe-owner",
                team,
                generation,
            )

            i1.write_request.assert_called_once_with(
                expected_request,
                request_value,
            )

            i1.exact_broker_command.assert_called_once_with(
                (
                    gate_repo
                    / "scripts"
                    / "p2-consumer-broker.sh"
                ),
                expected_config,
                "observe-owner",
                expected_request,
            )


class PilotGateF3RoundC(unittest.TestCase):
    TEAM = "agmsg-g4gate-round-c"
    TOOL_ID = "tool-123"
    COMMAND = "broker-command"

    def make_fixture(
        self,
        root: Path,
        label: str = "control",
    ):
        root = root.resolve()

        gate_repo = root / "repo"
        claude_config = root / "claude"
        artifact = root / "artifact"

        launcher = (
            gate_repo
            / "scripts"
            / "pilot-launcher.sh"
        )
        collector = (
            gate_repo
            / "scripts"
            / "pilot-collector.sh"
        )
        profile = (
            gate_repo
            / ".claude"
            / "settings.local.json"
        )

        launcher.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        profile.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        claude_config.mkdir(
            parents=True,
            exist_ok=True,
        )
        artifact.mkdir(
            parents=True,
            exist_ok=True,
        )

        launcher.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)

        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        collector.chmod(0o700)

        profile.write_bytes(
            b"original-profile\n"
        )
        profile.chmod(0o640)

        return {
            "root": root,
            "label": label,
            "gate_repo": gate_repo,
            "claude_config": claude_config,
            "artifact": artifact,
            "launcher": launcher,
            "collector": collector,
            "profile": profile,
            "profile_payload":
                b"replacement-profile\n",
            "posttool_log":
                artifact
                / label
                / "posttool-records.jsonl",
            "env": {
                "BASE": "yes",
            },
            "timeout": 12.5,
        }

    def make_native(self):
        native = mock.Mock()
        native.generation = 7
        native.session_id = "session-abc"
        native.binding = Path(
            "/tmp/binding.json"
        )
        native.decisions = Path(
            "/tmp/decisions.jsonl"
        )
        native.env = {
            "NATIVE": "env",
        }
        native.start = mock.Mock()
        native.invoke = mock.Mock()
        native.stop = mock.Mock()
        return native

    def run_case(self, fixture, i1):
        return F3.run_case(
            i1,
            fixture["label"],
            fixture["gate_repo"],
            fixture["claude_config"],
            self.TEAM,
            fixture["artifact"],
            fixture["env"],
            fixture["timeout"],
            fixture["launcher"],
            fixture["collector"],
            fixture["profile"],
            fixture["profile_payload"],
            fixture["posttool_log"],
        )

    def success_operation(
        self,
        transcript="/tmp/transcript.jsonl",
    ):
        return {
            "verdict": "pass",
            "toolUseId": self.TOOL_ID,
            "transcript": transcript,
        }

    def test_profile_replacement_uses_existing_mode_and_mismatch_prevents_native_construction(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()

            original_mode = stat.S_IMODE(
                fixture[
                    "profile"
                ].stat().st_mode
            )

            def corrupt(
                path,
                payload,
                mode,
            ):
                self.assertEqual(
                    mode,
                    original_mode,
                )
                Path(path).write_bytes(
                    b"wrong"
                )
                Path(path).chmod(mode)

            with mock.patch.object(
                F3,
                "atomic_bytes",
                side_effect=corrupt,
            ) as atomic_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "control profile "
                        "replacement mismatch"
                    ),
                ):
                    self.run_case(
                        fixture,
                        i1,
                    )

            atomic_mock.assert_called_once_with(
                fixture["profile"],
                fixture["profile_payload"],
                original_mode,
            )
            i1.NativePilot.assert_not_called()

    def test_native_start_run_id_prepare_and_invoke_arguments(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )

            native.invoke.return_value = {
                "verdict": "fail",
                "reason":
                    "stop-after-command-check",
            }

            binding_check = {
                "name":
                    "binding-profile-digest",
                "verdict": "pass",
                "detail": {},
            }

            with mock.patch.object(
                F3.time,
                "monotonic_ns",
                return_value=123456789,
            ), mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/tmp/config"),
                    Path("/tmp/request"),
                    self.COMMAND,
                ),
            ) as prepare_mock, mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=binding_check,
            ):
                result = self.run_case(
                    fixture,
                    i1,
                )

            case_dir = (
                fixture["artifact"]
                / "control"
            )

            i1.NativePilot.assert_called_once_with(
                fixture["launcher"],
                fixture["gate_repo"],
                self.TEAM,
                fixture["claude_config"],
                case_dir / "native",
                fixture["env"],
                fixture["timeout"],
            )
            native.start.assert_called_once_with()

            prepare_mock.assert_called_once_with(
                i1,
                fixture["gate_repo"],
                "control",
                "f3-control-123456789",
                self.TEAM,
                native.generation,
            )

            native.invoke.assert_called_once_with(
                self.COMMAND,
                case_dir / "operation",
            )

            native.stop.assert_called_once_with()
            self.assertEqual(
                result["reason"],
                "stop-after-command-check",
            )

    def test_operation_nonpass_returns_early_after_binding_check_and_skips_later_observers(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )

            operation = {
                "verdict": "unknown",
                "reason":
                    "native-unobservable",
            }
            native.invoke.return_value = (
                operation
            )

            binding_check = F3.assertion(
                "binding-profile-digest",
                True,
                "fixture",
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=binding_check,
            ) as binding_mock, mock.patch.object(
                F3,
                "transcript_result",
            ) as transcript_mock, mock.patch.object(
                F3,
                "collector_observation",
            ) as collector_mock, mock.patch.object(
                F3,
                "records_for_tool",
            ) as records_mock:
                result = self.run_case(
                    fixture,
                    i1,
                )

            self.assertEqual(
                result,
                {
                    "schemaVersion": 1,
                    "case": "control",
                    "verdict": "unknown",
                    "reason":
                        "native-unobservable",
                    "bindingCheck":
                        binding_check,
                    "operation": operation,
                },
            )

            binding_mock.assert_called_once_with(
                native,
                fixture[
                    "profile_payload"
                ],
            )

            transcript_mock.assert_not_called()
            collector_mock.assert_not_called()
            i1.hook_decision.assert_not_called()
            records_mock.assert_not_called()

            native.stop.assert_called_once_with()

            self.assertEqual(
                F3.read_json(
                    fixture["artifact"]
                    / "control"
                    / "result.json"
                ),
                result,
            )

    def test_operation_nonpass_defaults_missing_verdict_and_reason(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )

            native.invoke.return_value = {}

            binding_check = F3.assertion(
                "binding-profile-digest",
                True,
                "fixture",
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=binding_check,
            ):
                result = self.run_case(
                    fixture,
                    i1,
                )

            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            self.assertEqual(
                result["reason"],
                "native_operation_not_pass",
            )
            native.stop.assert_called_once_with()

    def test_incomplete_tool_or_transcript_evidence_returns_unknown_and_skips_later_observers(
        self,
    ):
        cases = (
            {
                "verdict": "pass",
                "toolUseId": None,
                "transcript":
                    "/tmp/transcript.jsonl",
            },
            {
                "verdict": "pass",
                "toolUseId":
                    self.TOOL_ID,
                "transcript": "",
            },
        )

        for operation in cases:
            with self.subTest(
                operation=operation
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )
                    i1 = mock.Mock()
                    native = (
                        self.make_native()
                    )
                    i1.NativePilot.return_value = (
                        native
                    )
                    native.invoke.return_value = (
                        operation
                    )

                    binding_check = (
                        F3.assertion(
                            "binding-profile-digest",
                            True,
                            "fixture",
                        )
                    )

                    with mock.patch.object(
                        F3,
                        "prepare_observe_owner",
                        return_value=(
                            Path("/c"),
                            Path("/r"),
                            self.COMMAND,
                        ),
                    ), mock.patch.object(
                        F3,
                        "binding_profile_check",
                        return_value=
                            binding_check,
                    ), mock.patch.object(
                        F3,
                        "transcript_result",
                    ) as transcript_mock, mock.patch.object(
                        F3,
                        "collector_observation",
                    ) as collector_mock, mock.patch.object(
                        F3,
                        "records_for_tool",
                    ) as records_mock:
                        result = (
                            self.run_case(
                                fixture,
                                i1,
                            )
                        )

                    self.assertEqual(
                        result["verdict"],
                        "unknown",
                    )
                    self.assertEqual(
                        result["reason"],
                        (
                            "native_evidence_"
                            "incomplete"
                        ),
                    )
                    self.assertEqual(
                        result["bindingCheck"],
                        binding_check,
                    )

                    transcript_mock.assert_not_called()
                    collector_mock.assert_not_called()
                    i1.hook_decision.assert_not_called()
                    records_mock.assert_not_called()
                    native.stop.assert_called_once_with()

    def test_control_builds_all_seven_passing_checks_from_exact_posttool_pair(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                label="control",
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )

            operation = (
                self.success_operation()
            )
            native.invoke.return_value = (
                operation
            )

            binding_check = F3.assertion(
                "binding-profile-digest",
                True,
                "fixture",
            )

            collector_result = {
                "verdict": "pass",
                "observation": {
                    "toolUseId":
                        self.TOOL_ID,
                    "completionState":
                        "success",
                },
            }

            post_records = [
                {
                    "event": "started",
                    "toolUseId":
                        self.TOOL_ID,
                },
                {
                    "event": "completed",
                    "toolUseId":
                        self.TOOL_ID,
                },
            ]

            i1.hook_decision.return_value = (
                "allow"
            )

            with mock.patch.object(
                F3.time,
                "monotonic_ns",
                return_value=99,
            ), mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=binding_check,
            ), mock.patch.object(
                F3,
                "transcript_result",
                return_value=(
                    True,
                    {
                        "isError": False
                    },
                ),
            ) as transcript_mock, mock.patch.object(
                F3,
                "collector_observation",
                return_value=
                    collector_result,
            ) as collector_mock, mock.patch.object(
                F3,
                "classify_collector",
                return_value=(
                    True,
                    collector_result[
                        "observation"
                    ],
                ),
            ) as classify_mock, mock.patch.object(
                F3,
                "records_for_tool",
                return_value=post_records,
            ) as records_mock:
                result = self.run_case(
                    fixture,
                    i1,
                )

            self.assertEqual(
                [
                    item["name"]
                    for item in result["checks"]
                ],
                [
                    "binding-profile-digest",
                    "pretool-allow",
                    (
                        "native-transcript-"
                        "tool-result-success"
                    ),
                    "collector-same-tool-success",
                    "posttool-record-started",
                    "posttool-record-completed",
                    (
                        "posttool-same-"
                        "tool-use-id"
                    ),
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
            self.assertEqual(
                result["verdict"],
                "pass",
            )

            transcript_mock.assert_called_once_with(
                i1,
                Path(
                    operation[
                        "transcript"
                    ]
                ),
                self.TOOL_ID,
            )

            collector_mock.assert_called_once_with(
                i1,
                fixture["collector"],
                native.binding,
                fixture["claude_config"],
                (
                    fixture["artifact"]
                    / "control"
                    / "collector-state"
                ),
                self.TOOL_ID,
                native.env,
                (
                    fixture["artifact"]
                    / "control"
                ),
            )

            classify_mock.assert_called_once_with(
                collector_result,
                self.TOOL_ID,
            )

            i1.hook_decision.assert_called_once_with(
                native.decisions,
                self.TOOL_ID,
            )

            records_mock.assert_called_once_with(
                fixture["posttool_log"],
                self.TOOL_ID,
            )

            native.stop.assert_called_once_with()

    def test_control_posttool_records_none_yields_three_unknown_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                label="control",
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )
            native.invoke.return_value = (
                self.success_operation()
            )
            i1.hook_decision.return_value = (
                "allow"
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=
                    F3.assertion(
                        "binding-profile-digest",
                        True,
                        "fixture",
                    ),
            ), mock.patch.object(
                F3,
                "transcript_result",
                return_value=(True, {}),
            ), mock.patch.object(
                F3,
                "collector_observation",
                return_value={
                    "verdict": "pass"
                },
            ), mock.patch.object(
                F3,
                "classify_collector",
                return_value=(True, {}),
            ), mock.patch.object(
                F3,
                "records_for_tool",
                return_value=None,
            ):
                result = self.run_case(
                    fixture,
                    i1,
                )

            checks = {
                item["name"]:
                    item["verdict"]
                for item
                in result["checks"]
            }

            self.assertEqual(
                checks[
                    "posttool-record-started"
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    "posttool-record-completed"
                ],
                "unknown",
            )
            self.assertEqual(
                checks[
                    (
                        "posttool-same-"
                        "tool-use-id"
                    )
                ],
                "unknown",
            )
            self.assertEqual(
                result["verdict"],
                "unknown",
            )
            native.stop.assert_called_once_with()

    def test_control_posttool_conditions_are_independent_and_exact(
        self,
    ):
        scenarios = (
            (
                [
                    {
                        "event": "started",
                        "toolUseId":
                            self.TOOL_ID,
                    },
                    {
                        "event": "started",
                        "toolUseId":
                            self.TOOL_ID,
                    },
                    {
                        "event": "completed",
                        "toolUseId":
                            self.TOOL_ID,
                    },
                ],
                {
                    "posttool-record-started":
                        "fail",
                    "posttool-record-completed":
                        "pass",
                    "posttool-same-tool-use-id":
                        "fail",
                },
            ),
            (
                [
                    {
                        "event": "started",
                        "toolUseId":
                            self.TOOL_ID,
                    },
                    {
                        "event": "completed",
                        "toolUseId": "other",
                    },
                ],
                {
                    "posttool-record-started":
                        "pass",
                    "posttool-record-completed":
                        "pass",
                    "posttool-same-tool-use-id":
                        "fail",
                },
            ),
        )

        for records, expected in scenarios:
            with self.subTest(
                records=records
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp),
                            label="control",
                        )
                    )
                    i1 = mock.Mock()
                    native = (
                        self.make_native()
                    )
                    i1.NativePilot.return_value = (
                        native
                    )
                    native.invoke.return_value = (
                        self.success_operation()
                    )
                    i1.hook_decision.return_value = (
                        "allow"
                    )

                    with mock.patch.object(
                        F3,
                        "prepare_observe_owner",
                        return_value=(
                            Path("/c"),
                            Path("/r"),
                            self.COMMAND,
                        ),
                    ), mock.patch.object(
                        F3,
                        "binding_profile_check",
                        return_value=
                            F3.assertion(
                                (
                                    "binding-"
                                    "profile-digest"
                                ),
                                True,
                                "fixture",
                            ),
                    ), mock.patch.object(
                        F3,
                        "transcript_result",
                        return_value=(
                            True,
                            {},
                        ),
                    ), mock.patch.object(
                        F3,
                        "collector_observation",
                        return_value={
                            "verdict":
                                "pass"
                        },
                    ), mock.patch.object(
                        F3,
                        "classify_collector",
                        return_value=(
                            True,
                            {},
                        ),
                    ), mock.patch.object(
                        F3,
                        "records_for_tool",
                        return_value=records,
                    ):
                        result = (
                            self.run_case(
                                fixture,
                                i1,
                            )
                        )

                    checks = {
                        item["name"]:
                            item["verdict"]
                        for item
                        in result[
                            "checks"
                        ]
                    }

                    for (
                        name,
                        verdict,
                    ) in expected.items():
                        self.assertEqual(
                            checks[name],
                            verdict,
                        )

                    self.assertEqual(
                        result["verdict"],
                        "fail",
                    )

    def test_fault_posttool_absence_maps_none_empty_and_nonempty_to_unknown_pass_fail(
        self,
    ):
        for records, expected in (
            (
                None,
                "unknown",
            ),
            (
                [],
                "pass",
            ),
            (
                [
                    {
                        "event": "started",
                        "toolUseId":
                            self.TOOL_ID,
                    }
                ],
                "fail",
            ),
        ):
            with self.subTest(
                records=records
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp),
                            label="fault",
                        )
                    )
                    i1 = mock.Mock()
                    native = (
                        self.make_native()
                    )
                    i1.NativePilot.return_value = (
                        native
                    )
                    native.invoke.return_value = (
                        self.success_operation()
                    )
                    i1.hook_decision.return_value = (
                        "allow"
                    )

                    with mock.patch.object(
                        F3,
                        "prepare_observe_owner",
                        return_value=(
                            Path("/c"),
                            Path("/r"),
                            self.COMMAND,
                        ),
                    ), mock.patch.object(
                        F3,
                        "binding_profile_check",
                        return_value=
                            F3.assertion(
                                (
                                    "binding-"
                                    "profile-digest"
                                ),
                                True,
                                "fixture",
                            ),
                    ), mock.patch.object(
                        F3,
                        "transcript_result",
                        return_value=(
                            True,
                            {},
                        ),
                    ), mock.patch.object(
                        F3,
                        "collector_observation",
                        return_value={
                            "verdict":
                                "pass"
                        },
                    ), mock.patch.object(
                        F3,
                        "classify_collector",
                        return_value=(
                            True,
                            {},
                        ),
                    ), mock.patch.object(
                        F3,
                        "records_for_tool",
                        return_value=records,
                    ):
                        result = (
                            self.run_case(
                                fixture,
                                i1,
                            )
                        )

                    checks = {
                        item["name"]:
                            item["verdict"]
                        for item
                        in result[
                            "checks"
                        ]
                    }

                    self.assertEqual(
                        checks[
                            (
                                "posttool-"
                                "record-absent"
                            )
                        ],
                        expected,
                    )
                    self.assertEqual(
                        result["verdict"],
                        expected,
                    )

    def test_unknown_label_adds_only_known_case_specific_failure(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                label="future",
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )
            native.invoke.return_value = (
                self.success_operation()
            )
            i1.hook_decision.return_value = (
                "allow"
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=
                    F3.assertion(
                        "binding-profile-digest",
                        True,
                        "fixture",
                    ),
            ), mock.patch.object(
                F3,
                "transcript_result",
                return_value=(True, {}),
            ), mock.patch.object(
                F3,
                "collector_observation",
                return_value={
                    "verdict": "pass"
                },
            ), mock.patch.object(
                F3,
                "classify_collector",
                return_value=(True, {}),
            ), mock.patch.object(
                F3,
                "records_for_tool",
                return_value=[],
            ):
                result = self.run_case(
                    fixture,
                    i1,
                )

            self.assertEqual(
                [
                    item["name"]
                    for item
                    in result["checks"]
                ],
                [
                    "binding-profile-digest",
                    "pretool-allow",
                    (
                        "native-transcript-"
                        "tool-result-success"
                    ),
                    "collector-same-tool-success",
                    "known-case",
                ],
            )

            self.assertEqual(
                result["checks"][-1],
                F3.assertion(
                    "known-case",
                    False,
                    "future",
                ),
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_final_result_contains_all_expected_fields_and_is_written(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                label="fault",
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )

            operation = self.success_operation(
                transcript=str(
                    fixture["root"]
                    / "transcript.jsonl"
                )
            )
            native.invoke.return_value = (
                operation
            )
            i1.hook_decision.return_value = (
                "allow"
            )

            collector_result = {
                "verdict": "pass",
                "observation": {
                    "toolUseId":
                        self.TOOL_ID,
                    "completionState":
                        "success",
                },
            }

            with mock.patch.object(
                F3.time,
                "monotonic_ns",
                return_value=4242,
            ), mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=
                    F3.assertion(
                        "binding-profile-digest",
                        True,
                        "fixture",
                    ),
            ), mock.patch.object(
                F3,
                "transcript_result",
                return_value=(
                    True,
                    {
                        "isError": False
                    },
                ),
            ), mock.patch.object(
                F3,
                "collector_observation",
                return_value=
                    collector_result,
            ), mock.patch.object(
                F3,
                "classify_collector",
                return_value=(
                    True,
                    {
                        "completionState":
                            "success"
                    },
                ),
            ), mock.patch.object(
                F3,
                "records_for_tool",
                return_value=[],
            ):
                result = self.run_case(
                    fixture,
                    i1,
                )

            self.assertEqual(
                set(result),
                {
                    "schemaVersion",
                    "case",
                    "verdict",
                    "runId",
                    "team",
                    "sessionId",
                    "generation",
                    "toolUseId",
                    "command",
                    "postToolUseRecords",
                    "collector",
                    "operation",
                    "checks",
                },
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["case"],
                "fault",
            )
            self.assertEqual(
                result["runId"],
                "f3-fault-4242",
            )
            self.assertEqual(
                result["team"],
                self.TEAM,
            )
            self.assertEqual(
                result["sessionId"],
                native.session_id,
            )
            self.assertEqual(
                result["generation"],
                str(native.generation),
            )
            self.assertEqual(
                result["toolUseId"],
                self.TOOL_ID,
            )
            self.assertEqual(
                result["command"],
                self.COMMAND,
            )
            self.assertEqual(
                result[
                    "postToolUseRecords"
                ],
                [],
            )
            self.assertEqual(
                result["collector"],
                collector_result,
            )
            self.assertEqual(
                result["operation"],
                operation,
            )

            self.assertEqual(
                F3.read_json(
                    fixture["artifact"]
                    / "fault"
                    / "result.json"
                ),
                result,
            )

            native.stop.assert_called_once_with()

    def test_exception_from_invoke_still_stops_native_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )
            native.invoke.side_effect = (
                ValueError(
                    "invoke exploded"
                )
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "invoke exploded",
                ):
                    self.run_case(
                        fixture,
                        i1,
                    )

            native.stop.assert_called_once_with()

    def test_exception_from_transcript_result_still_stops_native_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            i1 = mock.Mock()
            native = self.make_native()
            i1.NativePilot.return_value = (
                native
            )
            native.invoke.return_value = (
                self.success_operation()
            )

            with mock.patch.object(
                F3,
                "prepare_observe_owner",
                return_value=(
                    Path("/c"),
                    Path("/r"),
                    self.COMMAND,
                ),
            ), mock.patch.object(
                F3,
                "binding_profile_check",
                return_value=
                    F3.assertion(
                        "binding-profile-digest",
                        True,
                        "fixture",
                    ),
            ), mock.patch.object(
                F3,
                "transcript_result",
                side_effect=RuntimeError(
                    "transcript exploded"
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "transcript exploded",
                ):
                    self.run_case(
                        fixture,
                        i1,
                    )

            native.stop.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
