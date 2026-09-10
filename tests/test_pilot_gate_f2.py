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


if __name__ == "__main__":
    unittest.main()