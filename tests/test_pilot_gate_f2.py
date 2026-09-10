"""Round A tests for scripts/lib/pilot-gate-f2.py."""

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
F2_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_F2_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-f2.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_f2",
    F2_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate F2 helper: {F2_HELPER}"
    )

F2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F2)


class PilotGateF2RoundA(unittest.TestCase):
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

            module = F2.load_module(
                module_path,
                "pilot_gate_f2_fixture_module",
            )

            self.assertEqual(
                module.VALUE,
                42,
            )

            with self.assertRaises(
                FileNotFoundError
            ):
                F2.load_module(
                    root / "missing.py",
                    "pilot_gate_f2_missing_module",
                )

    def test_atomic_json_creates_parent_writes_sorted_unicode_and_replaces_existing_file(
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
                F2.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                F2.atomic_json(
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

            F2.atomic_json(
                output,
                {"replaced": True},
            )

            self.assertEqual(
                F2.read_json(output),
                {"replaced": True},
            )

    def test_sha256_bytes_and_binding_digest(
        self,
    ):
        payload = (
            b"pilot-gate-f2\x00payload"
        )
        expected = hashlib.sha256(
            payload
        ).hexdigest()

        self.assertEqual(
            F2.sha256_bytes(payload),
            expected,
        )
        self.assertEqual(
            F2.binding_digest(payload),
            f"sha256:{expected}",
        )

    def test_assertion_and_verdict_priority(
        self,
    ):
        passed = F2.assertion(
            "pass-check",
            True,
            "p",
        )
        failed = F2.assertion(
            "fail-check",
            False,
            "f",
        )
        unknown = F2.assertion(
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
            F2.verdict_from_assertions(
                [passed, unknown, failed]
            ),
            "fail",
        )
        self.assertEqual(
            F2.verdict_from_assertions(
                [passed, unknown]
            ),
            "unknown",
        )
        self.assertEqual(
            F2.verdict_from_assertions(
                [passed]
            ),
            "pass",
        )
        self.assertEqual(
            F2.verdict_from_assertions(
                []
            ),
            "pass",
        )

    def test_require_regular_default_does_not_require_executable_permission(
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

            F2.require_regular(
                plain
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "not executable",
            ):
                F2.require_regular(
                    plain,
                    executable=True,
                )

            plain.chmod(0o700)
            F2.require_regular(
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
                F2.require_regular(
                    link
                )

            directory = root / "directory"
            directory.mkdir()

            with self.assertRaisesRegex(
                RuntimeError,
                "not regular file",
            ):
                F2.require_regular(
                    directory
                )

    def test_read_json_reads_object(
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
                F2.read_json(path),
                {"value": 123},
            )

    def write_profile(
        self,
        root: Path,
        value,
    ) -> Path:
        profile = root / "settings.local.json"
        profile.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        profile.chmod(0o600)
        return profile

    def valid_profile(
        self,
        guard: Path,
        *,
        handler_extra=None,
    ):
        handler = {
            "type": "command",
            "command": str(guard),
            "args": [],
        }
        if handler_extra:
            handler.update(
                handler_extra
            )

        return {
            "schemaVersion": 1,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            handler
                        ],
                    }
                ]
            },
        }

    def test_parse_profile_accepts_valid_profile_without_executable_requirement(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )

            value = self.valid_profile(
                guard
            )
            profile = self.write_profile(
                root,
                value,
            )

            profile.chmod(0o600)

            self.assertEqual(
                F2.parse_profile(
                    profile
                ),
                value,
            )

    def test_parse_profile_rejects_invalid_root_hooks_and_pretooluse(
        self,
    ):
        cases = (
            (
                [],
                "pilot profile root is not object",
            ),
            (
                {},
                "pilot profile hooks is not object",
            ),
            (
                {
                    "hooks": []
                },
                "pilot profile hooks is not object",
            ),
            (
                {
                    "hooks": {}
                },
                "pilot profile PreToolUse is unavailable",
            ),
            (
                {
                    "hooks": {
                        "PreToolUse": []
                    }
                },
                "pilot profile PreToolUse is unavailable",
            ),
            (
                {
                    "hooks": {
                        "PreToolUse": {}
                    }
                },
                "pilot profile PreToolUse is unavailable",
            ),
        )

        for value, message in cases:
            with self.subTest(
                value=value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    profile = (
                        self.write_profile(
                            root,
                            value,
                        )
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        message,
                    ):
                        F2.parse_profile(
                            profile
                        )

    def test_guard_handlers_returns_single_exact_guard_coordinate(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )

            profile = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(
                                        guard
                                    ),
                                    "args": None,
                                }
                            ],
                        }
                    ]
                }
            }

            self.assertEqual(
                F2.guard_handlers(
                    profile,
                    guard,
                ),
                [(0, 0)],
            )

    def test_guard_handlers_returns_empty_when_hooks_or_pretooluse_is_not_dict_list(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )

            self.assertEqual(
                F2.guard_handlers(
                    {},
                    guard,
                ),
                [],
            )
            self.assertEqual(
                F2.guard_handlers(
                    {
                        "hooks": []
                    },
                    guard,
                ),
                [],
            )
            self.assertEqual(
                F2.guard_handlers(
                    {
                        "hooks": {
                            "PreToolUse": {}
                        }
                    },
                    guard,
                ),
                [],
            )

    def test_guard_handlers_rejects_invalid_group_hooks_or_handler_shape(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )

            cases = (
                (
                    {
                        "hooks": {
                            "PreToolUse": [
                                "bad-group"
                            ]
                        }
                    },
                    (
                        "PreToolUse matcher group "
                        "is not object"
                    ),
                ),
                (
                    {
                        "hooks": {
                            "PreToolUse": [
                                {}
                            ]
                        }
                    },
                    (
                        "PreToolUse matcher group "
                        "hooks invalid"
                    ),
                ),
                (
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": []
                                }
                            ]
                        }
                    },
                    (
                        "PreToolUse matcher group "
                        "hooks invalid"
                    ),
                ),
                (
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        "bad-handler"
                                    ]
                                }
                            ]
                        }
                    },
                    (
                        "PreToolUse handler "
                        "is not object"
                    ),
                ),
            )

            for profile, message in cases:
                with self.subTest(
                    message=message
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        message,
                    ):
                        F2.guard_handlers(
                            profile,
                            guard,
                        )

    def test_guard_handlers_requires_exactly_one_total_handler_and_one_guard_match(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            other = root / "other"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )
            other.write_text(
                "other\n",
                encoding="utf-8",
            )

            no_match = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(
                                        other
                                    ),
                                    "args": [],
                                }
                            ]
                        }
                    ]
                }
            }

            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "pilot PreToolUse contract "
                    "ambiguous: handlers=1 "
                    "guardHandlers=0"
                ),
            ):
                F2.guard_handlers(
                    no_match,
                    guard,
                )

            two_handlers = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(
                                        guard
                                    ),
                                    "args": [],
                                },
                                {
                                    "type": "command",
                                    "command": str(
                                        other
                                    ),
                                    "args": [],
                                },
                            ]
                        }
                    ]
                }
            }

            with self.assertRaisesRegex(
                RuntimeError,
                (
                    "pilot PreToolUse contract "
                    "ambiguous: handlers=2 "
                    "guardHandlers=1"
                ),
            ):
                F2.guard_handlers(
                    two_handlers,
                    guard,
                )

    def test_guard_handlers_skips_noncommand_nonempty_args_and_unresolvable_commands_then_fails_ambiguity(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )

            profiles = (
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {
                                        "type": "other",
                                        "command": str(
                                            guard
                                        ),
                                        "args": [],
                                    }
                                ]
                            }
                        ]
                    }
                },
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": str(
                                            guard
                                        ),
                                        "args": [
                                            "--bad"
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                },
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": str(
                                            root
                                            / "missing-command"
                                        ),
                                        "args": [],
                                    }
                                ]
                            }
                        ]
                    }
                },
            )

            for profile in profiles:
                with self.subTest(
                    profile=profile
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        (
                            "handlers=1 "
                            "guardHandlers=0"
                        ),
                    ):
                        F2.guard_handlers(
                            profile,
                            guard,
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

        payload = F2.encode_profile(
            value
        )

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

    def test_make_missing_profile_removes_only_copy_and_preserves_original(
        self,
    ):
        original = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/tmp/guard",
                            }
                        ]
                    }
                ],
                "PostToolUse": [
                    {
                        "hooks": []
                    }
                ],
            },
            "nested": {
                "value": [
                    1,
                    2,
                ]
            },
        }

        before = json.loads(
            json.dumps(original)
        )

        faulted = (
            F2.make_missing_profile(
                original
            )
        )

        self.assertNotIn(
            "PreToolUse",
            faulted["hooks"],
        )
        self.assertIn(
            "PostToolUse",
            faulted["hooks"],
        )
        self.assertEqual(
            original,
            before,
        )

        faulted["nested"]["value"].append(
            3
        )
        self.assertEqual(
            original["nested"]["value"],
            [1, 2],
        )

    def test_make_missing_profile_requires_pretooluse_key(
        self,
    ):
        cases = (
            {},
            {
                "hooks": []
            },
            {
                "hooks": {
                    "PostToolUse": []
                }
            },
        )

        for original in cases:
            with self.subTest(
                original=original
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "PreToolUse unavailable "
                        "before missing fault"
                    ),
                ):
                    F2.make_missing_profile(
                        original
                    )

    def test_make_timeout_profile_rewrites_guard_handler_and_preserves_original(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            guard = root / "guard"
            injector = root / "injector.py"
            guard.write_text(
                "guard\n",
                encoding="utf-8",
            )
            injector.write_text(
                "injector\n",
                encoding="utf-8",
            )

            original = self.valid_profile(
                guard,
                handler_extra={
                    "timeout": 99,
                    "async": True,
                    "extra": "preserve",
                },
            )

            before = json.loads(
                json.dumps(original)
            )

            faulted = (
                F2.make_timeout_profile(
                    original,
                    guard,
                    injector,
                )
            )

            handler = (
                faulted["hooks"]
                ["PreToolUse"][0]
                ["hooks"][0]
            )

            self.assertEqual(
                handler["command"],
                str(injector),
            )
            self.assertEqual(
                handler["args"],
                [],
            )
            self.assertEqual(
                handler["timeout"],
                F2.TIMEOUT_SECONDS,
            )
            self.assertNotIn(
                "async",
                handler,
            )
            self.assertEqual(
                handler["extra"],
                "preserve",
            )

            self.assertEqual(
                original,
                before,
            )

    def test_make_timeout_profile_empty_matches_propagates_index_error_when_guard_handlers_is_stubbed(
        self,
    ):
        original = {
            "hooks": {
                "PreToolUse": []
            }
        }

        with mock.patch.object(
            F2,
            "guard_handlers",
            return_value=[],
        ):
            with self.assertRaises(
                IndexError
            ):
                F2.make_timeout_profile(
                    original,
                    Path("/tmp/guard"),
                    Path("/tmp/injector"),
                )

    def test_atomic_bytes_writes_mode_replaces_existing_and_uses_pid_monotonic_temp_name(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "nested" / "payload.bin"
            output.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            output.write_bytes(
                b"old"
            )
            output.chmod(
                0o600
            )

            fixed_ns = 123456789
            real_replace = os.replace

            with mock.patch.object(
                F2.time,
                "monotonic_ns",
                return_value=fixed_ns,
            ):
                with mock.patch.object(
                    F2.os,
                    "replace",
                    wraps=real_replace,
                ) as replace_mock:
                    F2.atomic_bytes(
                        output,
                        b"new-payload",
                        0o640,
                    )

            expected_tmp = (
                output.with_name(
                    (
                        f".{output.name}."
                        f"{os.getpid()}."
                        f"{fixed_ns}.tmp"
                    )
                )
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
                stat.S_IMODE(
                    output.stat().st_mode
                ),
                0o640,
            )
            self.assertFalse(
                expected_tmp.exists()
            )

    def test_atomic_bytes_o_excl_rejects_preexisting_exact_temp_path_without_overwriting_it(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "payload.bin"
            fixed_ns = 987654321

            tmp = output.with_name(
                (
                    f".{output.name}."
                    f"{os.getpid()}."
                    f"{fixed_ns}.tmp"
                )
            )
            tmp.write_bytes(
                b"sentinel"
            )

            with mock.patch.object(
                F2.time,
                "monotonic_ns",
                return_value=fixed_ns,
            ):
                with self.assertRaises(
                    FileExistsError
                ):
                    F2.atomic_bytes(
                        output,
                        b"new",
                        0o600,
                    )

            self.assertEqual(
                tmp.read_bytes(),
                b"sentinel",
            )
            self.assertFalse(
                output.exists()
            )

    def test_atomic_bytes_includes_o_nofollow_when_platform_exposes_it(
        self,
    ):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest(
                "O_NOFOLLOW unavailable"
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "payload.bin"

            real_open = os.open
            seen_flags = []

            def recording_open(
                path,
                flags,
                mode=0o777,
            ):
                seen_flags.append(
                    flags
                )
                return real_open(
                    path,
                    flags,
                    mode,
                )

            with mock.patch.object(
                F2.os,
                "open",
                side_effect=recording_open,
            ):
                F2.atomic_bytes(
                    output,
                    b"payload",
                    0o600,
                )

            self.assertEqual(
                len(seen_flags),
                1,
            )
            self.assertTrue(
                seen_flags[0]
                & os.O_EXCL
            )
            self.assertTrue(
                seen_flags[0]
                & os.O_NOFOLLOW
            )

    def test_write_timeout_injector_generates_expected_source_and_mode(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            injector = root / "injector.py"
            log = root / "injector.jsonl"

            F2.write_timeout_injector(
                injector,
                log,
            )

            source = injector.read_text(
                encoding="utf-8"
            )

            self.assertTrue(
                source.startswith(
                    "#!/usr/bin/env python3\n"
                )
            )
            self.assertIn(
                f"LOG = {str(log)!r}",
                source,
            )
            self.assertIn(
                (
                    "SLEEP = "
                    f"{F2.INJECTOR_SLEEP_SECONDS!r}"
                ),
                source,
            )
            self.assertIn(
                '"event": "started"',
                source,
            )
            self.assertIn(
                "time.sleep(SLEEP)",
                source,
            )
            self.assertIn(
                (
                    '"event": '
                    '"completed_without_timeout"'
                ),
                source,
            )
            self.assertIn(
                "raise SystemExit(0)",
                source,
            )

            compile(
                source,
                "<timeout-injector>",
                "exec",
            )

            self.assertEqual(
                stat.S_IMODE(
                    injector.stat().st_mode
                ),
                0o700,
            )

    def test_write_timeout_injector_executes_and_logs_started_then_completed_with_child_pid(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            injector = root / "injector.py"
            log = root / "injector.jsonl"

            F2.write_timeout_injector(
                injector,
                log,
            )

            started_at = time.monotonic()

            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-S",
                    str(injector),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = (
                proc.communicate(
                    timeout=(
                        F2.INJECTOR_SLEEP_SECONDS
                        + 5
                    )
                )
            )

            elapsed = (
                time.monotonic()
                - started_at
            )

            self.assertEqual(
                proc.returncode,
                0,
            )
            self.assertEqual(
                stdout,
                "",
            )
            self.assertEqual(
                stderr,
                "",
            )
            self.assertGreaterEqual(
                elapsed,
                F2.INJECTOR_SLEEP_SECONDS
                - 0.2,
            )

            records = [
                json.loads(line)
                for line
                in log.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            self.assertEqual(
                len(records),
                2,
            )
            self.assertEqual(
                [
                    item["event"]
                    for item in records
                ],
                [
                    "started",
                    "completed_without_timeout",
                ],
            )
            self.assertEqual(
                records[0][
                    "schemaVersion"
                ],
                1,
            )
            self.assertEqual(
                records[1][
                    "schemaVersion"
                ],
                1,
            )
            self.assertEqual(
                records[0]["pid"],
                proc.pid,
            )
            self.assertEqual(
                records[1]["pid"],
                proc.pid,
            )
            self.assertLessEqual(
                records[0][
                    "monotonic"
                ],
                records[1][
                    "monotonic"
                ],
            )

    def test_write_probe_generates_expected_source_mode_and_marker_contract(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            probe = root / "probe.py"
            marker = root / "marker"
            run_id = "run-123"
            case = "missing"

            F2.write_probe(
                probe,
                marker,
                run_id,
                case,
            )

            source = probe.read_text(
                encoding="utf-8"
            )

            marker_text = (
                f"agmsg-g4-f2:"
                f"{run_id}:{case}\\n"
            )

            self.assertTrue(
                source.startswith(
                    "#!/usr/bin/env python3\n"
                )
            )
            self.assertIn(
                f"TARGET = {str(marker)!r}",
                source,
            )
            self.assertIn(
                f"MARKER = {marker_text!r}",
                source,
            )
            self.assertIn(
                (
                    "flags = os.O_WRONLY "
                    "| os.O_CREAT | os.O_EXCL"
                ),
                source,
            )
            self.assertIn(
                'hasattr(os, "O_NOFOLLOW")',
                source,
            )
            self.assertIn(
                "flags |= os.O_NOFOLLOW",
                source,
            )

            compile(
                source,
                "<f2-probe>",
                "exec",
            )

            self.assertEqual(
                stat.S_IMODE(
                    probe.stat().st_mode
                ),
                0o700,
            )

    def test_write_probe_executes_once_creates_exact_marker_and_second_execution_fails_o_excl(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            probe = root / "probe.py"
            marker = root / "marker"
            run_id = "run-abc"
            case = "timeout"

            F2.write_probe(
                probe,
                marker,
                run_id,
                case,
            )

            first = subprocess.run(
                [str(probe)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(
                first.returncode,
                0,
            )
            self.assertEqual(
                marker.read_text(
                    encoding="utf-8"
                ),
                (
                    "agmsg-g4-f2:"
                    f"{run_id}:{case}\\n"
                ),
            )

            second = subprocess.run(
                [str(probe)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertNotEqual(
                second.returncode,
                0,
            )
            self.assertEqual(
                marker.read_text(
                    encoding="utf-8"
                ),
                (
                    "agmsg-g4-f2:"
                    f"{run_id}:{case}\\n"
                ),
            )



class PilotGateF2RoundB(unittest.TestCase):
    COMMAND = "/gate/probe.py"
    TOOL_ID = "tool-123"

    class WalkI1:
        @staticmethod
        def walk_json(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from PilotGateF2RoundB.WalkI1.walk_json(
                        child
                    )
            elif isinstance(value, list):
                for child in value:
                    yield from PilotGateF2RoundB.WalkI1.walk_json(
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

    def check_map(self, checks):
        return {
            item["name"]: item
            for item in checks
        }

    def make_probe_fixture(self, root: Path):
        root = root.resolve()
        gate_repo = root / "repo"
        runtime = gate_repo / ".agmsg-gate" / "f2"
        runtime.mkdir(parents=True, exist_ok=True)

        probe = runtime / "probe.py"
        probe.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        probe.chmod(0o700)

        marker = runtime / "marker"

        iso = mock.Mock()
        iso.canonical_nonexistent.return_value = str(
            marker
        )
        iso.no_symlink_components.return_value = (
            True,
            [],
        )

        return gate_repo, probe, marker, iso

    def test_prove_probe_contained_returns_five_passing_checks_in_fixed_order(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                gate_repo,
                probe,
                marker,
                iso,
            ) = self.make_probe_fixture(root)

            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )

            self.assertEqual(
                [item["name"] for item in checks],
                [
                    "probe-program-inside-gate-repo",
                    "probe-marker-inside-gate-repo",
                    (
                        "probe-marker-parent-"
                        "no-symlink-components"
                    ),
                    "probe-marker-absent-before-case",
                    "probe-program-executable",
                ],
            )
            self.assertEqual(
                [item["verdict"] for item in checks],
                ["pass"] * 5,
            )
            self.assertEqual(
                checks[0]["detail"],
                str(probe.resolve(strict=True)),
            )
            self.assertEqual(
                checks[1]["detail"],
                str(marker),
            )
            self.assertEqual(
                checks[2]["detail"],
                [],
            )

            iso.canonical_nonexistent.assert_called_once_with(
                str(marker)
            )
            iso.no_symlink_components.assert_called_once_with(
                str(gate_repo.resolve(strict=True)),
                str(marker.parent),
            )

    def test_prove_probe_contained_missing_or_outside_probe_fails_inside_check(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                gate_repo,
                probe,
                marker,
                iso,
            ) = self.make_probe_fixture(root)

            missing = (
                gate_repo
                / ".agmsg-gate"
                / "f2"
                / "missing.py"
            )

            for candidate in (
                missing,
                root / "outside-probe.py",
            ):
                with self.subTest(
                    candidate=str(candidate)
                ):
                    if candidate.name == "outside-probe.py":
                        candidate.write_text(
                            "#!/bin/sh\nexit 0\n",
                            encoding="utf-8",
                        )
                        candidate.chmod(0o700)

                    checks = F2.prove_probe_contained(
                        iso,
                        gate_repo=gate_repo,
                        probe=candidate,
                        marker=marker,
                    )
                    mapped = self.check_map(
                        checks
                    )

                    self.assertEqual(
                        mapped[
                            "probe-program-inside-gate-repo"
                        ]["verdict"],
                        "fail",
                    )
                    self.assertTrue(
                        mapped[
                            "probe-program-inside-gate-repo"
                        ]["detail"],
                    )

    def test_prove_probe_contained_marker_outside_and_symlink_status_are_transferred(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                gate_repo,
                probe,
                marker,
                iso,
            ) = self.make_probe_fixture(root)

            outside = root / "outside" / "marker"
            iso.canonical_nonexistent.return_value = str(
                outside
            )
            iso.no_symlink_components.return_value = (
                None,
                ["unprovable-component"],
            )

            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            mapped = self.check_map(checks)

            self.assertEqual(
                mapped[
                    "probe-marker-inside-gate-repo"
                ]["verdict"],
                "fail",
            )
            self.assertEqual(
                mapped[
                    "probe-marker-parent-no-symlink-components"
                ]["verdict"],
                "unknown",
            )
            self.assertEqual(
                mapped[
                    "probe-marker-parent-no-symlink-components"
                ]["detail"],
                ["unprovable-component"],
            )

            iso.no_symlink_components.return_value = (
                False,
                ["symlink-found"],
            )
            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            mapped = self.check_map(checks)
            self.assertEqual(
                mapped[
                    "probe-marker-parent-no-symlink-components"
                ]["verdict"],
                "fail",
            )

    def test_prove_probe_contained_existing_and_dangling_symlink_marker_fail_absence(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                gate_repo,
                probe,
                marker,
                iso,
            ) = self.make_probe_fixture(root)

            marker.write_text(
                "exists",
                encoding="utf-8",
            )
            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            self.assertEqual(
                self.check_map(checks)[
                    "probe-marker-absent-before-case"
                ]["verdict"],
                "fail",
            )

            marker.unlink()
            try:
                marker.symlink_to(
                    marker.parent / "missing-target"
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            self.assertEqual(
                self.check_map(checks)[
                    "probe-marker-absent-before-case"
                ]["verdict"],
                "fail",
            )

    def test_prove_probe_contained_nonexecutive_or_symlink_probe_fails_executable_check(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (
                gate_repo,
                probe,
                marker,
                iso,
            ) = self.make_probe_fixture(root)

            probe.chmod(0o600)
            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            self.assertEqual(
                self.check_map(checks)[
                    "probe-program-executable"
                ]["verdict"],
                "fail",
            )

            real_probe = (
                probe.parent / "real-probe.py"
            )
            real_probe.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            real_probe.chmod(0o700)
            probe.unlink()

            try:
                probe.symlink_to(
                    real_probe
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            checks = F2.prove_probe_contained(
                iso,
                gate_repo=gate_repo,
                probe=probe,
                marker=marker,
            )
            self.assertEqual(
                self.check_map(checks)[
                    "probe-program-executable"
                ]["verdict"],
                "fail",
            )

    def test_find_probe_evidence_missing_and_invalid_utf8_return_base_unknown_shape(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            expected = {
                "toolUseId": None,
                "resultFound": False,
                "resultIsError": None,
                "resultText": "",
            }

            self.assertEqual(
                F2.find_probe_evidence(
                    i1,
                    root / "missing.jsonl",
                    self.COMMAND,
                ),
                expected,
            )

            invalid = root / "invalid.jsonl"
            invalid.write_bytes(
                b'{"x":"\xff"}\n'
            )

            self.assertEqual(
                F2.find_probe_evidence(
                    i1,
                    invalid,
                    self.COMMAND,
                ),
                expected,
            )

    def test_find_probe_evidence_skips_bad_json_and_requires_exactly_one_unique_tool_use(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = root / "transcript.jsonl"

            transcript.write_text(
                "\n".join(
                    [
                        "{bad-json",
                        json.dumps(
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": "wrong-command",
                                "input": {
                                    "command": "other"
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = F2.find_probe_evidence(
                i1,
                transcript,
                self.COMMAND,
            )

            self.assertEqual(
                result,
                {
                    "toolUseId": None,
                    "resultFound": False,
                    "resultIsError": None,
                    "resultText": "",
                    "toolUseCount": 0,
                },
            )

            one = {
                "type": "tool_use",
                "name": "Bash",
                "id": "tool-1",
                "input": {
                    "command": self.COMMAND
                },
            }
            two = {
                "type": "tool_use",
                "name": "Bash",
                "id": "tool-2",
                "input": {
                    "command": self.COMMAND
                },
            }

            transcript.write_text(
                json.dumps(
                    {
                        "nodes": [
                            one,
                            two,
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = F2.find_probe_evidence(
                i1,
                transcript,
                self.COMMAND,
            )

            self.assertEqual(
                result["toolUseCount"],
                2,
            )
            self.assertIsNone(
                result["toolUseId"]
            )
            self.assertFalse(
                result["resultFound"]
            )

            transcript.write_text(
                json.dumps(
                    {
                        "nodes": [
                            one,
                            one,
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = F2.find_probe_evidence(
                i1,
                transcript,
                self.COMMAND,
            )

            self.assertNotIn(
                "toolUseCount",
                result,
            )
            self.assertEqual(
                result["toolUseId"],
                "tool-1",
            )
            self.assertEqual(
                result["resultCount"],
                0,
            )

    def test_find_probe_evidence_requires_exactly_one_tool_result(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = root / "transcript.jsonl"

            tool = {
                "type": "tool_use",
                "name": "Bash",
                "id": self.TOOL_ID,
                "input": {
                    "command": self.COMMAND
                },
            }

            for result_nodes, expected_count in (
                ([], 0),
                (
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": self.TOOL_ID,
                            "content": "one",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": self.TOOL_ID,
                            "content": "two",
                        },
                    ],
                    2,
                ),
            ):
                with self.subTest(
                    expected_count=expected_count
                ):
                    transcript.write_text(
                        json.dumps(
                            {
                                "nodes": [
                                    tool,
                                    *result_nodes,
                                ]
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    result = (
                        F2.find_probe_evidence(
                            i1,
                            transcript,
                            self.COMMAND,
                        )
                    )

                    self.assertEqual(
                        result,
                        {
                            "toolUseId":
                                self.TOOL_ID,
                            "resultFound":
                                False,
                            "resultIsError":
                                None,
                            "resultText":
                                "",
                            "resultCount":
                                expected_count,
                        },
                    )

    def test_find_probe_evidence_returns_result_text_and_tristate_is_error(
        self,
    ):
        i1 = self.WalkI1()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = root / "transcript.jsonl"

            for is_error, expected in (
                (False, False),
                (True, True),
                ("not-bool", None),
            ):
                with self.subTest(
                    is_error=is_error
                ):
                    transcript.write_text(
                        json.dumps(
                            {
                                "nodes": [
                                    {
                                        "type": "tool_use",
                                        "name": "Bash",
                                        "id": self.TOOL_ID,
                                        "input": {
                                            "command":
                                                self.COMMAND
                                        },
                                    },
                                    {
                                        "type": "tool_result",
                                        "tool_use_id":
                                            self.TOOL_ID,
                                        "is_error":
                                            is_error,
                                        "content": [
                                            {
                                                "text": "line-1"
                                            },
                                            "line-2",
                                        ],
                                    },
                                ]
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    result = (
                        F2.find_probe_evidence(
                            i1,
                            transcript,
                            self.COMMAND,
                        )
                    )

                    self.assertEqual(
                        result["toolUseId"],
                        self.TOOL_ID,
                    )
                    self.assertTrue(
                        result["resultFound"]
                    )
                    self.assertIs(
                        result["resultIsError"],
                        expected,
                    )
                    self.assertEqual(
                        result["resultText"],
                        "line-1\nline-2",
                    )

    def test_raw_timeout_indication_no_match_and_transcript_none(
        self,
    ):
        native = mock.Mock()
        native.pty_log.read_bytes.return_value = (
            b"ordinary output"
        )

        result = F2.raw_timeout_indication(
            native,
            None,
        )

        self.assertEqual(
            result,
            {
                "found": False,
                "matches": [],
            },
        )

        native.pty_log.read_bytes.assert_called_once()

    def test_raw_timeout_indication_matches_pty_and_transcript_case_insensitively(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            transcript = root / "transcript.jsonl"

            transcript.write_text(
                (
                    "prefix TIMED OUT while waiting "
                    "for PreToolUse HOOK suffix"
                ),
                encoding="utf-8",
            )

            native = mock.Mock()
            native.pty_log.read_bytes.return_value = (
                b"HOOK execution timeout after one second"
            )

            result = F2.raw_timeout_indication(
                native,
                transcript,
            )

            self.assertTrue(
                result["found"]
            )
            self.assertEqual(
                [
                    item["source"]
                    for item in result["matches"]
                ],
                [
                    "pty",
                    "transcript",
                ],
            )
            self.assertIn(
                "HOOK",
                result["matches"][0]["excerpt"],
            )
            self.assertLessEqual(
                len(
                    result["matches"][0][
                        "excerpt"
                    ]
                ),
                240,
            )

    def test_raw_timeout_indication_silently_skips_pty_and_transcript_os_errors(
        self,
    ):
        native = mock.Mock()
        native.pty_log.read_bytes.side_effect = (
            OSError("pty unavailable")
        )

        transcript = mock.Mock()
        transcript.read_text.side_effect = (
            OSError("transcript unavailable")
        )

        result = F2.raw_timeout_indication(
            native,
            transcript,
        )

        self.assertEqual(
            result,
            {
                "found": False,
                "matches": [],
            },
        )

    def make_collector_fixture(
        self,
        root: Path,
        *,
        generation=7,
    ):
        root = root.resolve()
        repo = root / "repo"
        collector = repo / "scripts" / "pilot-collector.sh"

        collector.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )

        binding = root / "binding.json"
        binding.write_text(
            json.dumps(
                {
                    "generation": generation
                }
            )
            + "\n",
            encoding="utf-8",
        )

        claude_config = root / "claude"
        claude_config.mkdir()

        state_dir = root / "state"
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

    def test_collector_observation_builds_environment_runs_discover_then_scan_and_writes_raw_results(
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

            result = F2.collector_observation(
                i1,
                collector=collector,
                binding=binding,
                claude_config=claude_config,
                state_dir=state_dir,
                tool_id=self.TOOL_ID,
                env=env,
                artifact=artifact,
            )

            self.assertTrue(
                state_dir.is_dir()
            )
            self.assertEqual(
                env,
                {
                    "ORIGINAL": "yes"
                },
            )
            self.assertEqual(
                i1.run.call_count,
                2,
            )

            discover_call = (
                i1.run.call_args_list[0]
            )
            scan_call = (
                i1.run.call_args_list[1]
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
                F2.read_json(
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
                F2.read_json(
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

    def test_collector_observation_reads_generation_ledger_and_identifies_exact_match(
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
                        "",
                        json.dumps(
                            {
                                "toolUseId": "other",
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

            result = F2.collector_observation(
                i1,
                collector=collector,
                binding=binding,
                claude_config=claude_config,
                state_dir=state_dir,
                tool_id=self.TOOL_ID,
                env={},
                artifact=artifact,
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

    def test_collector_observation_invalid_ledger_returns_immediate_unknown_read_error(
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
                (
                    json.dumps(
                        {
                            "toolUseId":
                                self.TOOL_ID
                        }
                    )
                    + "\n"
                    + "{bad-json\n"
                ),
                encoding="utf-8",
            )

            result = F2.collector_observation(
                i1,
                collector=collector,
                binding=binding,
                claude_config=claude_config,
                state_dir=state_dir,
                tool_id=self.TOOL_ID,
                env={},
                artifact=artifact,
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

    def test_collector_observation_zero_and_multiple_matches_are_unknown(
        self,
    ):
        for count, expected_reason in (
            (
                0,
                "collector_observation_missing",
            ),
            (
                2,
                "collector_observation_ambiguous",
            ),
        ):
            with self.subTest(
                count=count
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
                        / (
                            "generation-7."
                            "observations.jsonl"
                        )
                    )

                    records = [
                        {
                            "toolUseId":
                                self.TOOL_ID,
                            "n": index,
                        }
                        for index in range(count)
                    ]

                    ledger.write_text(
                        "".join(
                            json.dumps(item)
                            + "\n"
                            for item in records
                        ),
                        encoding="utf-8",
                    )

                    result = (
                        F2.collector_observation(
                            i1,
                            collector=collector,
                            binding=binding,
                            claude_config=(
                                claude_config
                            ),
                            state_dir=state_dir,
                            tool_id=self.TOOL_ID,
                            env={},
                            artifact=artifact,
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
                        count,
                    )

    def test_collector_observation_symlink_ledger_is_treated_as_missing(
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

            target = root / "real-ledger.jsonl"
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

            ledger = (
                state_dir
                / "generation-7.observations.jsonl"
            )

            try:
                ledger.symlink_to(
                    target
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            result = F2.collector_observation(
                i1,
                collector=collector,
                binding=binding,
                claude_config=claude_config,
                state_dir=state_dir,
                tool_id=self.TOOL_ID,
                env={},
                artifact=artifact,
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

    def test_validate_binding_profile_none_and_unreadable_are_unknown(
        self,
    ):
        payload = b'{"profile":true}\n'

        native = mock.Mock()
        native.binding = None

        self.assertEqual(
            F2.validate_binding_profile(
                native,
                payload,
            ),
            F2.assertion(
                "binding-profile-digest",
                None,
                "binding unavailable",
            ),
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            native.binding = (
                root / "missing-binding.json"
            )

            result = (
                F2.validate_binding_profile(
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

    def test_validate_binding_profile_pass_and_fail_include_actual_expected_and_binding(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            binding = root / "binding.json"
            payload = b'{"profile":"value"}\n'
            expected = F2.binding_digest(
                payload
            )

            native = mock.Mock()
            native.binding = binding

            for actual, verdict in (
                (expected, "pass"),
                ("sha256:wrong", "fail"),
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
                        F2.validate_binding_profile(
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
                        verdict,
                    )
                    self.assertEqual(
                        result["detail"],
                        {
                            "actual": actual,
                            "expected": expected,
                            "binding": str(
                                binding
                            ),
                        },
                    )


class PilotGateF2RoundC(unittest.TestCase):
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    COMMAND = "/gate/probe.py"
    TOOL_ID = "tool-123"

    def make_fixture(self, root: Path):
        root = root.resolve()
        artifact = root / "artifact"
        collector = root / "repo" / "scripts" / "pilot-collector.sh"
        collector.parent.mkdir(parents=True, exist_ok=True)
        collector.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        collector.chmod(0o700)

        claude_config = root / "claude"
        claude_config.mkdir()
        collector_state = root / "collector-state"
        binding = root / "binding.json"
        binding.write_text(
            '{"generation":1}\n',
            encoding="utf-8",
        )
        decisions = root / "decisions.jsonl"

        native = mock.Mock()
        native.proc = mock.Mock()
        native.master = 123
        native.binding = binding
        native.session_id = self.SESSION_ID
        native.timeout = 1.0
        native.decisions = decisions
        native.env = {"BASE": "yes"}
        native.pump = mock.Mock()
        native.discover_transcript = mock.Mock()
        native.proc.poll = mock.Mock(return_value=None)

        i1 = mock.Mock()
        i1.hook_decision.return_value = "allow"

        return {
            "root": root,
            "artifact": artifact,
            "collector": collector,
            "claude_config": claude_config,
            "collector_state": collector_state,
            "binding": binding,
            "decisions": decisions,
            "native": native,
            "i1": i1,
        }

    def invoke(self, fixture):
        return F2.invoke_probe(
            fixture["i1"],
            native=fixture["native"],
            command=self.COMMAND,
            collector=fixture["collector"],
            claude_config=fixture["claude_config"],
            collector_state=fixture["collector_state"],
            artifact=fixture["artifact"],
        )

    def test_native_not_started_guard_for_proc_master_or_binding(
        self,
    ):
        fields = ("proc", "master", "binding")

        for field in fields:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )
                    setattr(
                        fixture["native"],
                        field,
                        None,
                    )

                    with mock.patch.object(
                        F2.os,
                        "write",
                    ) as write_mock, mock.patch.object(
                        F2,
                        "find_probe_evidence",
                    ) as evidence_mock, mock.patch.object(
                        F2,
                        "collector_observation",
                    ) as collector_mock, mock.patch.object(
                        F2,
                        "raw_timeout_indication",
                    ) as timeout_mock:
                        result = self.invoke(
                            fixture
                        )

                    self.assertEqual(
                        result,
                        {
                            "verdict": "unknown",
                            "reason": "native_not_started",
                        },
                    )
                    write_mock.assert_not_called()
                    evidence_mock.assert_not_called()
                    collector_mock.assert_not_called()
                    timeout_mock.assert_not_called()
                    fixture[
                        "native"
                    ].pump.assert_not_called()
                    fixture[
                        "native"
                    ].discover_transcript.assert_not_called()

    def test_prompt_token_text_file_and_master_write_are_exact(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            transcript = (
                fixture["root"]
                / "transcript.jsonl"
            )
            transcript.write_text(
                "{}\n",
                encoding="utf-8",
            )
            fixture[
                "native"
            ].discover_transcript.return_value = transcript

            evidence = {
                "toolUseId": self.TOOL_ID,
                "resultFound": True,
                "resultIsError": False,
                "resultText": "ok",
            }

            token = hashlib.sha256(
                (
                    self.SESSION_ID
                    + self.COMMAND
                ).encode("utf-8")
            ).hexdigest()[:16]

            expected_prompt = (
                "For the G4 F2 integration gate, use the Bash tool exactly once. "
                "Run exactly the following command without changing, wrapping, "
                "retrying, or adding any other command:\n"
                f"{self.COMMAND}\n"
                "After that single tool attempt finishes, reply "
                f"AGMSG_F2_DONE_{token}."
            )

            with mock.patch.object(
                F2.os,
                "write",
                return_value=len(
                    expected_prompt.encode("utf-8")
                ) + 1,
            ) as write_mock, mock.patch.object(
                F2,
                "find_probe_evidence",
                return_value=evidence,
            ), mock.patch.object(
                F2,
                "collector_observation",
                return_value={
                    "verdict": "pass",
                },
            ), mock.patch.object(
                F2,
                "raw_timeout_indication",
                return_value={
                    "found": False,
                    "matches": [],
                },
            ):
                result = self.invoke(
                    fixture
                )

            self.assertTrue(
                fixture["artifact"].is_dir()
            )
            self.assertEqual(
                (
                    fixture["artifact"]
                    / "prompt.txt"
                ).read_text(
                    encoding="utf-8"
                ),
                expected_prompt + "\n",
            )
            write_mock.assert_called_once_with(
                123,
                expected_prompt.encode("utf-8")
                + b"\r",
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )

    def test_poll_loop_breaks_immediately_on_unique_tool_result_and_final_pumps_once_more(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            transcript = (
                fixture["root"]
                / "transcript.jsonl"
            )
            transcript.write_text(
                "{}\n",
                encoding="utf-8",
            )
            fixture[
                "native"
            ].discover_transcript.return_value = transcript

            evidence = {
                "toolUseId": self.TOOL_ID,
                "resultFound": True,
                "resultIsError": False,
                "resultText": "ok",
            }

            with mock.patch.object(
                F2.os,
                "write",
                return_value=1,
            ), mock.patch.object(
                F2,
                "find_probe_evidence",
                return_value=evidence,
            ) as evidence_mock, mock.patch.object(
                F2,
                "collector_observation",
                return_value={
                    "verdict": "pass",
                },
            ), mock.patch.object(
                F2,
                "raw_timeout_indication",
                return_value={
                    "found": False,
                    "matches": [],
                },
            ):
                result = self.invoke(
                    fixture
                )

            self.assertEqual(
                result["verdict"],
                "pass",
            )
            self.assertEqual(
                fixture[
                    "native"
                ].pump.call_args_list,
                [
                    mock.call(0.2),
                    mock.call(0.2),
                ],
            )
            fixture[
                "native"
            ].discover_transcript.assert_called_once()
            evidence_mock.assert_called_once_with(
                fixture["i1"],
                transcript,
                self.COMMAND,
            )
            fixture[
                "native"
            ].proc.poll.assert_not_called()

    def test_poll_loop_breaks_when_process_exits_and_still_performs_final_pump(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            transcript = (
                fixture["root"]
                / "transcript.jsonl"
            )
            transcript.write_text(
                "{}\n",
                encoding="utf-8",
            )
            fixture[
                "native"
            ].discover_transcript.return_value = transcript
            fixture[
                "native"
            ].proc.poll.return_value = 9

            evidence = {
                "toolUseId": None,
                "resultFound": False,
                "resultIsError": None,
                "resultText": "",
                "toolUseCount": 0,
            }

            with mock.patch.object(
                F2.os,
                "write",
                return_value=1,
            ), mock.patch.object(
                F2,
                "find_probe_evidence",
                return_value=evidence,
            ) as evidence_mock, mock.patch.object(
                F2,
                "collector_observation",
            ) as collector_mock, mock.patch.object(
                F2,
                "raw_timeout_indication",
            ) as timeout_mock:
                result = self.invoke(
                    fixture
                )

            self.assertEqual(
                fixture[
                    "native"
                ].pump.call_args_list,
                [
                    mock.call(0.2),
                    mock.call(0.2),
                ],
            )
            fixture[
                "native"
            ].discover_transcript.assert_called_once()
            evidence_mock.assert_called_once()
            fixture[
                "native"
            ].proc.poll.assert_called_once()

            self.assertEqual(
                result,
                {
                    "verdict": "unknown",
                    "reason":
                        "tool_attempt_unobservable",
                    "transcript":
                        str(transcript),
                    "evidence": evidence,
                },
            )
            collector_mock.assert_not_called()
            timeout_mock.assert_not_called()

    def test_poll_loop_deadline_exit_is_deterministic_and_final_pump_occurs(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            fixture["native"].timeout = 0.5
            fixture[
                "native"
            ].discover_transcript.return_value = None
            fixture[
                "native"
            ].proc.poll.return_value = None

            with mock.patch.object(
                F2.time,
                "monotonic",
                side_effect=[
                    100.0,
                    100.1,
                    100.6,
                ],
            ), mock.patch.object(
                F2.os,
                "write",
                return_value=1,
            ), mock.patch.object(
                F2,
                "find_probe_evidence",
            ) as evidence_mock, mock.patch.object(
                F2,
                "collector_observation",
            ) as collector_mock, mock.patch.object(
                F2,
                "raw_timeout_indication",
            ) as timeout_mock:
                result = self.invoke(
                    fixture
                )

            self.assertEqual(
                fixture[
                    "native"
                ].pump.call_args_list,
                [
                    mock.call(0.2),
                    mock.call(0.2),
                ],
            )
            fixture[
                "native"
            ].discover_transcript.assert_called_once()
            fixture[
                "native"
            ].proc.poll.assert_called_once()

            evidence_mock.assert_not_called()
            collector_mock.assert_not_called()
            timeout_mock.assert_not_called()

            self.assertEqual(
                result,
                {
                    "verdict": "unknown",
                    "reason":
                        "transcript_unavailable",
                    "evidence": {
                        "toolUseId": None,
                        "resultFound": False,
                        "resultIsError": None,
                        "resultText": "",
                    },
                },
            )

    def test_transcript_unavailable_returns_last_evidence_default(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            fixture["native"].timeout = 0.5
            fixture[
                "native"
            ].discover_transcript.return_value = None

            with mock.patch.object(
                F2.time,
                "monotonic",
                side_effect=[
                    10.0,
                    10.1,
                    10.6,
                ],
            ), mock.patch.object(
                F2.os,
                "write",
                return_value=1,
            ):
                result = self.invoke(
                    fixture
                )

            self.assertEqual(
                result,
                {
                    "verdict": "unknown",
                    "reason":
                        "transcript_unavailable",
                    "evidence": {
                        "toolUseId": None,
                        "resultFound": False,
                        "resultIsError": None,
                        "resultText": "",
                    },
                },
            )

    def test_transcript_found_but_missing_tool_id_returns_tool_attempt_unobservable(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            transcript = (
                fixture["root"]
                / "transcript.jsonl"
            )
            transcript.write_text(
                "{}\n",
                encoding="utf-8",
            )
            fixture[
                "native"
            ].discover_transcript.return_value = transcript
            fixture[
                "native"
            ].proc.poll.return_value = 0

            for tool_id in (
                None,
                "",
            ):
                with self.subTest(
                    tool_id=tool_id
                ):
                    fixture[
                        "native"
                    ].reset_mock()
                    fixture[
                        "native"
                    ].proc.poll.return_value = 0
                    fixture[
                        "native"
                    ].discover_transcript.return_value = transcript

                    evidence = {
                        "toolUseId": tool_id,
                        "resultFound": False,
                        "resultIsError": None,
                        "resultText": "",
                    }

                    with mock.patch.object(
                        F2.os,
                        "write",
                        return_value=1,
                    ), mock.patch.object(
                        F2,
                        "find_probe_evidence",
                        return_value=evidence,
                    ), mock.patch.object(
                        F2,
                        "collector_observation",
                    ) as collector_mock, mock.patch.object(
                        F2,
                        "raw_timeout_indication",
                    ) as timeout_mock:
                        result = self.invoke(
                            fixture
                        )

                    self.assertEqual(
                        result,
                        {
                            "verdict": "unknown",
                            "reason":
                                "tool_attempt_unobservable",
                            "transcript":
                                str(transcript),
                            "evidence":
                                evidence,
                        },
                    )
                    collector_mock.assert_not_called()
                    timeout_mock.assert_not_called()

    def test_success_aggregates_hook_collector_timeout_writes_observation_and_returns_same_value(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            transcript = (
                fixture["root"]
                / "transcript.jsonl"
            )
            transcript.write_text(
                "{}\n",
                encoding="utf-8",
            )
            fixture[
                "native"
            ].discover_transcript.return_value = transcript

            evidence = {
                "toolUseId": self.TOOL_ID,
                "resultFound": True,
                "resultIsError": False,
                "resultText":
                    '{"state":"ok"}',
            }
            collector_result = {
                "verdict": "pass",
                "reason":
                    "collector_observation_identified",
                "observation": {
                    "toolUseId":
                        self.TOOL_ID,
                    "completionState":
                        "success",
                },
            }
            raw_timeout = {
                "found": True,
                "matches": [
                    {
                        "source": "pty",
                        "excerpt":
                            "hook timed out",
                    }
                ],
            }

            fixture[
                "i1"
            ].hook_decision.return_value = "deny"

            with mock.patch.object(
                F2.os,
                "write",
                return_value=1,
            ), mock.patch.object(
                F2,
                "find_probe_evidence",
                return_value=evidence,
            ) as evidence_mock, mock.patch.object(
                F2,
                "collector_observation",
                return_value=collector_result,
            ) as collector_mock, mock.patch.object(
                F2,
                "raw_timeout_indication",
                return_value=raw_timeout,
            ) as timeout_mock:
                result = self.invoke(
                    fixture
                )

            fixture[
                "i1"
            ].hook_decision.assert_called_once_with(
                fixture["native"].decisions,
                self.TOOL_ID,
            )

            collector_mock.assert_called_once_with(
                fixture["i1"],
                collector=fixture["collector"],
                binding=fixture["native"].binding,
                claude_config=fixture[
                    "claude_config"
                ],
                state_dir=fixture[
                    "collector_state"
                ],
                tool_id=self.TOOL_ID,
                env=fixture["native"].env,
                artifact=fixture["artifact"],
            )

            timeout_mock.assert_called_once_with(
                fixture["native"],
                transcript,
            )

            expected = {
                "verdict": "pass",
                "reason":
                    "tool_attempt_observed",
                "toolUseId": self.TOOL_ID,
                "hookDecision": "deny",
                "transcript": str(transcript),
                "evidence": evidence,
                "collector": collector_result,
                "rawTimeoutIndication":
                    raw_timeout,
            }

            self.assertEqual(
                result,
                expected,
            )

            self.assertEqual(
                F2.read_json(
                    fixture["artifact"]
                    / "observation.json"
                ),
                expected,
            )

            evidence_mock.assert_called_once_with(
                fixture["i1"],
                transcript,
                self.COMMAND,
            )

if __name__ == "__main__":
    unittest.main()