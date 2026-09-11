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


class PilotGateF2RoundD(unittest.TestCase):
    TOOL_ID = "tool-123"

    def binding_check(self, verdict="pass"):
        return {
            "name": "binding-profile-digest",
            "verdict": verdict,
            "detail": {"fixture": True},
        }

    def check_map(self, result):
        return {
            item["name"]: item
            for item in result["checks"]
        }

    def base_observation(
        self,
        *,
        hook_decision=None,
        evidence=None,
        collector=None,
        raw_timeout=None,
        tool_id=TOOL_ID,
    ):
        value = {
            "toolUseId": tool_id,
            "hookDecision": hook_decision,
        }

        if evidence is not None:
            value["evidence"] = evidence

        if collector is not None:
            value["collector"] = collector

        if raw_timeout is not None:
            value["rawTimeoutIndication"] = raw_timeout

        return value

    def test_common_checks_keep_binding_first_and_normalize_nondict_inputs(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "marker"
            binding = self.binding_check()

            observation = {
                "toolUseId": self.TOOL_ID,
                "hookDecision": "deny",
                "evidence": ["not-a-dict"],
                "collector": "not-a-dict",
            }

            result = F2.evaluate_case(
                label="control",
                observation=observation,
                marker=marker,
                binding_check=binding,
            )

            self.assertIs(
                result["checks"][0],
                binding,
            )

            checks = self.check_map(result)

            self.assertEqual(
                checks[
                    "tool-attempt-observable"
                ]["verdict"],
                "pass",
            )
            self.assertEqual(
                checks[
                    "collector-observation"
                ]["verdict"],
                "fail",
            )
            self.assertEqual(
                checks[
                    "collector-observation"
                ]["detail"],
                {},
            )
            self.assertEqual(
                checks[
                    "collector-recorded-failure"
                ]["verdict"],
                "unknown",
            )

    def test_tool_attempt_observable_requires_nonempty_string(
        self,
    ):
        for value, expected in (
            ("tool-1", "pass"),
            ("", "fail"),
            (None, "fail"),
            (123, "fail"),
        ):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    marker = root / "marker"

                    observation = self.base_observation(
                        hook_decision="deny",
                        collector={
                            "verdict": "pass",
                            "observation": {
                                "completionState":
                                    "failure",
                            },
                        },
                        tool_id=value,
                    )

                    result = F2.evaluate_case(
                        label="control",
                        observation=observation,
                        marker=marker,
                        binding_check=self.binding_check(),
                    )

                    checks = self.check_map(result)

                    self.assertEqual(
                        checks[
                            "tool-attempt-observable"
                        ]["verdict"],
                        expected,
                    )

    def test_collector_observation_maps_pass_unknown_and_other_to_tristate(
        self,
    ):
        for collector_value, expected in (
            ({"verdict": "pass"}, "pass"),
            ({"verdict": "unknown"}, "unknown"),
            ({"verdict": "fail"}, "fail"),
            ({}, "fail"),
        ):
            with self.subTest(
                collector_value=collector_value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    marker = root / "marker"

                    observation = self.base_observation(
                        hook_decision="deny",
                        collector=collector_value,
                    )

                    result = F2.evaluate_case(
                        label="control",
                        observation=observation,
                        marker=marker,
                        binding_check=self.binding_check(),
                    )

                    checks = self.check_map(result)

                    self.assertEqual(
                        checks[
                            "collector-observation"
                        ]["verdict"],
                        expected,
                    )

    def test_control_case_passes_with_deny_absent_marker_and_failure_record(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "marker"

            observation = self.base_observation(
                hook_decision="deny",
                collector={
                    "verdict": "pass",
                    "observation": {
                        "completionState": "failure",
                    },
                },
            )

            result = F2.evaluate_case(
                label="control",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
            )

            checks = self.check_map(result)

            self.assertEqual(
                set(checks),
                {
                    "binding-profile-digest",
                    "tool-attempt-observable",
                    "collector-observation",
                    "guard-deny-observed",
                    "probe-marker-absent",
                    "collector-recorded-failure",
                },
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

    def test_control_marker_existing_or_dangling_symlink_fails_absence(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            observation = self.base_observation(
                hook_decision="deny",
                collector={
                    "verdict": "pass",
                    "observation": {
                        "completionState": "failure",
                    },
                },
            )

            marker = root / "marker"
            marker.write_text(
                "present",
                encoding="utf-8",
            )

            result = F2.evaluate_case(
                label="control",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
            )

            self.assertEqual(
                self.check_map(result)[
                    "probe-marker-absent"
                ]["verdict"],
                "fail",
            )

            marker.unlink()

            try:
                marker.symlink_to(
                    root / "missing-target"
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            result = F2.evaluate_case(
                label="control",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
            )

            self.assertEqual(
                self.check_map(result)[
                    "probe-marker-absent"
                ]["verdict"],
                "fail",
            )

    def test_control_empty_collector_record_is_unknown_but_wrong_nonempty_record_is_fail(
        self,
    ):
        for record, expected in (
            ({}, "unknown"),
            (
                {
                    "completionState": "success",
                },
                "fail",
            ),
        ):
            with self.subTest(record=record):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    marker = root / "marker"

                    observation = self.base_observation(
                        hook_decision="deny",
                        collector={
                            "verdict": "pass",
                            "observation": record,
                        },
                    )

                    result = F2.evaluate_case(
                        label="control",
                        observation=observation,
                        marker=marker,
                        binding_check=self.binding_check(),
                    )

                    check = self.check_map(result)[
                        "collector-recorded-failure"
                    ]

                    self.assertEqual(
                        check["verdict"],
                        expected,
                    )

    def test_missing_case_passes_with_no_deny_real_marker_successful_tool_and_collector(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "marker"
            marker.write_text(
                "created",
                encoding="utf-8",
            )

            evidence = {
                "resultFound": True,
                "resultIsError": False,
            }

            observation = self.base_observation(
                hook_decision=None,
                evidence=evidence,
                collector={
                    "verdict": "pass",
                    "observation": {
                        "completionState": "success",
                    },
                },
            )

            result = F2.evaluate_case(
                label="missing",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
            )

            checks = self.check_map(result)

            self.assertEqual(
                set(checks),
                {
                    "binding-profile-digest",
                    "tool-attempt-observable",
                    "collector-observation",
                    "guard-deny-absent",
                    "probe-marker-created",
                    "tool-result-success",
                    "collector-recorded-success",
                },
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

    def test_missing_tool_result_and_collector_record_tristate(
        self,
    ):
        cases = (
            (
                {
                    "resultFound": False,
                    "resultIsError": False,
                },
                {},
                "unknown",
                "unknown",
            ),
            (
                {
                    "resultFound": True,
                    "resultIsError": True,
                },
                {
                    "completionState": "failure",
                },
                "fail",
                "fail",
            ),
        )

        for (
            evidence,
            collector_record,
            tool_expected,
            collector_expected,
        ) in cases:
            with self.subTest(
                evidence=evidence,
                collector_record=collector_record,
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    marker = root / "marker"
                    marker.write_text(
                        "created",
                        encoding="utf-8",
                    )

                    observation = self.base_observation(
                        hook_decision=None,
                        evidence=evidence,
                        collector={
                            "verdict": "pass",
                            "observation":
                                collector_record,
                        },
                    )

                    result = F2.evaluate_case(
                        label="missing",
                        observation=observation,
                        marker=marker,
                        binding_check=self.binding_check(),
                    )

                    checks = self.check_map(result)

                    self.assertEqual(
                        checks[
                            "tool-result-success"
                        ]["verdict"],
                        tool_expected,
                    )
                    self.assertEqual(
                        checks[
                            "collector-recorded-success"
                        ]["verdict"],
                        collector_expected,
                    )

    def test_missing_marker_symlink_is_not_accepted_as_created_probe(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            target = root / "real-marker"
            target.write_text(
                "real",
                encoding="utf-8",
            )

            marker = root / "marker"

            try:
                marker.symlink_to(target)
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            observation = self.base_observation(
                hook_decision=None,
                evidence={
                    "resultFound": True,
                    "resultIsError": False,
                },
                collector={
                    "verdict": "pass",
                    "observation": {
                        "completionState": "success",
                    },
                },
            )

            result = F2.evaluate_case(
                label="missing",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
            )

            self.assertEqual(
                self.check_map(result)[
                    "probe-marker-created"
                ]["verdict"],
                "fail",
            )

    def timeout_observation(self):
        return self.base_observation(
            hook_decision=None,
            evidence={
                "resultFound": True,
                "resultIsError": False,
            },
            collector={
                "verdict": "pass",
                "observation": {
                    "completionState": "success",
                },
            },
            raw_timeout={
                "found": True,
                "matches": [
                    {
                        "source": "pty",
                        "excerpt": "hook timed out",
                    }
                ],
            },
        )

    def test_timeout_case_passes_when_injector_started_but_did_not_complete(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            marker = root / "marker"
            marker.write_text(
                "created",
                encoding="utf-8",
            )

            injector_log = root / "injector.jsonl"
            injector_log.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "event": "started",
                        "pid": 123,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            observation = self.timeout_observation()

            result = F2.evaluate_case(
                label="timeout",
                observation=observation,
                marker=marker,
                binding_check=self.binding_check(),
                injector_log=injector_log,
            )

            checks = self.check_map(result)

            self.assertEqual(
                set(checks),
                {
                    "binding-profile-digest",
                    "tool-attempt-observable",
                    "collector-observation",
                    "timeout-injector-started",
                    (
                        "timeout-injector-killed-"
                        "before-normal-completion"
                    ),
                    "raw-hook-timeout-indication",
                    "guard-deny-absent",
                    "probe-marker-created",
                    "tool-result-success",
                    "collector-recorded-success",
                },
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

    def test_timeout_missing_or_none_injector_log_uses_empty_records(
        self,
    ):
        for injector_log in (
            None,
            Path(
                "/definitely/not/present/"
                "injector.jsonl"
            ),
        ):
            with self.subTest(
                injector_log=injector_log
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()

                    marker = root / "marker"
                    marker.write_text(
                        "created",
                        encoding="utf-8",
                    )

                    result = F2.evaluate_case(
                        label="timeout",
                        observation=
                            self.timeout_observation(),
                        marker=marker,
                        binding_check=
                            self.binding_check(),
                        injector_log=injector_log,
                    )

                    checks = self.check_map(result)

                    self.assertEqual(
                        checks[
                            "timeout-injector-started"
                        ]["verdict"],
                        "fail",
                    )
                    self.assertEqual(
                        checks[
                            (
                                "timeout-injector-killed-"
                                "before-normal-completion"
                            )
                        ]["verdict"],
                        "pass",
                    )
                    self.assertEqual(
                        checks[
                            "timeout-injector-started"
                        ]["detail"],
                        [],
                    )

    def test_timeout_invalid_json_makes_records_none_started_fail_and_killed_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            marker = root / "marker"
            marker.write_text(
                "created",
                encoding="utf-8",
            )

            injector_log = root / "injector.jsonl"
            injector_log.write_text(
                (
                    json.dumps(
                        {
                            "event": "started",
                            "pid": 123,
                        }
                    )
                    + "\n"
                    + "{bad-json\n"
                ),
                encoding="utf-8",
            )

            result = F2.evaluate_case(
                label="timeout",
                observation=
                    self.timeout_observation(),
                marker=marker,
                binding_check=self.binding_check(),
                injector_log=injector_log,
            )

            checks = self.check_map(result)

            started = checks[
                "timeout-injector-started"
            ]
            killed = checks[
                (
                    "timeout-injector-killed-"
                    "before-normal-completion"
                )
            ]

            self.assertEqual(
                started["verdict"],
                "fail",
            )
            self.assertIsNone(
                started["detail"]
            )
            self.assertEqual(
                killed["verdict"],
                "unknown",
            )
            self.assertIsNone(
                killed["detail"]
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_timeout_completed_event_makes_killed_before_completion_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            marker = root / "marker"
            marker.write_text(
                "created",
                encoding="utf-8",
            )

            injector_log = root / "injector.jsonl"
            injector_log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event": "started",
                            }
                        ),
                        json.dumps(
                            {
                                "event":
                                    "completed_without_timeout",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = F2.evaluate_case(
                label="timeout",
                observation=
                    self.timeout_observation(),
                marker=marker,
                binding_check=self.binding_check(),
                injector_log=injector_log,
            )

            checks = self.check_map(result)

            self.assertEqual(
                checks[
                    "timeout-injector-started"
                ]["verdict"],
                "pass",
            )
            self.assertEqual(
                checks[
                    (
                        "timeout-injector-killed-"
                        "before-normal-completion"
                    )
                ]["verdict"],
                "fail",
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_timeout_raw_indication_requires_dict_with_found_true(
        self,
    ):
        variants = (
            ({"found": True}, "pass"),
            ({"found": False}, "fail"),
            ({}, "fail"),
            ("not-a-dict", "fail"),
            (None, "fail"),
        )

        for raw_timeout, expected in variants:
            with self.subTest(
                raw_timeout=raw_timeout
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()

                    marker = root / "marker"
                    marker.write_text(
                        "created",
                        encoding="utf-8",
                    )

                    injector_log = (
                        root / "injector.jsonl"
                    )
                    injector_log.write_text(
                        json.dumps(
                            {
                                "event": "started",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    observation = (
                        self.base_observation(
                            hook_decision=None,
                            evidence={
                                "resultFound": True,
                                "resultIsError":
                                    False,
                            },
                            collector={
                                "verdict": "pass",
                                "observation": {
                                    "completionState":
                                        "success",
                                },
                            },
                        )
                    )

                    if raw_timeout is not None:
                        observation[
                            "rawTimeoutIndication"
                        ] = raw_timeout

                    result = F2.evaluate_case(
                        label="timeout",
                        observation=observation,
                        marker=marker,
                        binding_check=
                            self.binding_check(),
                        injector_log=injector_log,
                    )

                    self.assertEqual(
                        self.check_map(result)[
                            "raw-hook-timeout-indication"
                        ]["verdict"],
                        expected,
                    )

    def test_unknown_label_adds_only_case_known_after_three_common_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "marker"

            observation = {
                "toolUseId": self.TOOL_ID,
                "evidence": None,
                "collector": None,
            }

            binding = self.binding_check()

            result = F2.evaluate_case(
                label="future-case",
                observation=observation,
                marker=marker,
                binding_check=binding,
            )

            self.assertEqual(
                [
                    item["name"]
                    for item in result["checks"]
                ],
                [
                    "binding-profile-digest",
                    "tool-attempt-observable",
                    "collector-observation",
                    "case-known",
                ],
            )
            self.assertEqual(
                result["checks"][-1],
                F2.assertion(
                    "case-known",
                    False,
                    "future-case",
                ),
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

    def test_final_result_preserves_observation_and_verdict_priority(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "marker"

            observation = self.base_observation(
                hook_decision="allow",
                collector={
                    "verdict": "unknown",
                    "observation": {},
                },
            )

            binding = self.binding_check(
                verdict="pass"
            )

            result = F2.evaluate_case(
                label="control",
                observation=observation,
                marker=marker,
                binding_check=binding,
            )

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["case"],
                "control",
            )
            self.assertIs(
                result["observation"],
                observation,
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )
            self.assertIs(
                result["checks"][0],
                binding,
            )


class PilotGateF2RoundE(unittest.TestCase):
    RUN_ID = "round-e"
    GATE_TEAM = "agmsg-g4gate-round-e"

    def make_fixture(self, root: Path, *, gate_inside=True):
        root = root.resolve()
        run_root = root / "run-root"
        run_root.mkdir(parents=True, exist_ok=True)

        if gate_inside:
            gate_repo = run_root / "repo"
        else:
            gate_repo = root / "outside-repo"

        scripts = gate_repo / "scripts"
        claude_dir = gate_repo / ".claude"
        scripts.mkdir(parents=True, exist_ok=True)
        claude_dir.mkdir(parents=True, exist_ok=True)

        launcher = scripts / "pilot-launcher.sh"
        guard = scripts / "pm-pilot-pretool-guard"
        collector = scripts / "pilot-collector.sh"

        for path in (launcher, guard, collector):
            path.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            path.chmod(0o700)

        profile = claude_dir / "settings.local.json"
        profile_value = {
            "schemaVersion": 1,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(guard),
                                "args": [],
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/bin/true",
                                "args": [],
                            }
                        ],
                    }
                ],
            },
        }
        profile.write_text(
            json.dumps(
                profile_value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        profile.chmod(0o640)

        claude_config = run_root / "claude"
        claude_config.mkdir(parents=True, exist_ok=True)

        artifact_dir = root / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        args = F2.argparse.Namespace(
            gate_repo=str(gate_repo),
            run_root=str(run_root),
            claude_config=str(claude_config),
            artifact_dir=str(artifact_dir),
            run_id=self.RUN_ID,
            gate_team=self.GATE_TEAM,
            timeout_seconds=11,
        )

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "launcher": launcher,
            "guard": guard,
            "collector": collector,
            "profile": profile,
            "profile_value": profile_value,
            "claude_config": claude_config,
            "artifact_dir": artifact_dir,
            "artifact": artifact_dir / "F2",
            "runtime": gate_repo / ".agmsg-gate" / "f2",
            "args": args,
        }

    def make_modules(self):
        iso = mock.Mock()
        iso.canonical.side_effect = (
            lambda value: str(
                Path(value).resolve(strict=True)
            )
        )

        i1 = mock.Mock()
        i1.sanitize_env.return_value = {
            "BASE_ENV": "preserved",
        }

        return iso, i1

    def module_loader(self, iso, i1):
        def load(path, name):
            if name == "pilot_gate_isolation":
                return iso
            if name == "pilot_gate_i1":
                return i1
            raise AssertionError(
                f"unexpected module request: {path} {name}"
            )

        return load

    def injector_writer(self, path, log_path):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        path.write_bytes(
            b"#!/usr/bin/env python3\n"
            b"raise SystemExit(0)\n"
        )
        path.chmod(0o700)

    def probe_writer(self, path, marker, run_id, case):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        path.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def passing_containment(self):
        return [
            F2.assertion(
                "containment",
                True,
                "fixture",
            )
        ]

    def failing_containment(self, verdict="fail"):
        return [
            F2.assertion(
                "containment",
                (
                    False
                    if verdict == "fail"
                    else None
                ),
                "fixture",
            )
        ]

    def make_native(self, suffix="control"):
        native = mock.Mock()
        native.session_id = f"session-{suffix}"
        native.generation = 1
        native.binding = Path(
            f"/tmp/binding-{suffix}.json"
        )
        native.start = mock.Mock()
        native.stop = mock.Mock()
        return native

    def install_common_stubs(
        self,
        fixture,
        *,
        containment=None,
        evaluate_side_effect=None,
        invoke_side_effect=None,
        natives=None,
        write_probe=True,
    ):
        iso, i1 = self.make_modules()

        patches = [
            mock.patch.object(
                F2,
                "load_module",
                side_effect=self.module_loader(
                    iso,
                    i1,
                ),
            ),
            mock.patch.object(
                F2,
                "write_timeout_injector",
                side_effect=self.injector_writer,
            ),
        ]

        if write_probe:
            patches.append(
                mock.patch.object(
                    F2,
                    "write_probe",
                    side_effect=self.probe_writer,
                )
            )

        if containment is None:
            containment = self.passing_containment()

        patches.append(
            mock.patch.object(
                F2,
                "prove_probe_contained",
                return_value=containment,
            )
        )

        patches.append(
            mock.patch.object(
                F2,
                "validate_binding_profile",
                return_value=F2.assertion(
                    "binding-profile-digest",
                    True,
                    "fixture",
                ),
            )
        )

        if invoke_side_effect is None:
            invoke_side_effect = {
                "verdict": "pass",
                "toolUseId": "tool-1",
            }

        patches.append(
            mock.patch.object(
                F2,
                "invoke_probe",
                side_effect=(
                    invoke_side_effect
                    if (
                        isinstance(
                            invoke_side_effect,
                            Exception,
                        )
                        or callable(
                            invoke_side_effect
                        )
                    )
                    else None
                ),
                return_value=(
                    None
                    if (
                        isinstance(
                            invoke_side_effect,
                            Exception,
                        )
                        or callable(
                            invoke_side_effect
                        )
                    )
                    else invoke_side_effect
                ),
            )
        )

        if evaluate_side_effect is None:
            def evaluate(**kwargs):
                return {
                    "schemaVersion": 1,
                    "case": kwargs["label"],
                    "verdict": "pass",
                    "checks": [],
                    "observation":
                        kwargs["observation"],
                }

            evaluate_side_effect = evaluate

        patches.append(
            mock.patch.object(
                F2,
                "evaluate_case",
                side_effect=evaluate_side_effect,
            )
        )

        if natives is None:
            natives = [
                self.make_native("control"),
                self.make_native("missing"),
                self.make_native("timeout"),
            ]

        i1.NativePilot.side_effect = natives

        started = [
            patcher.start()
            for patcher in patches
        ]

        for patcher in reversed(patches):
            self.addCleanup(
                patcher.stop
            )

        return {
            "iso": iso,
            "i1": i1,
            "natives": natives,
            "write_timeout_injector":
                started[1],
            "write_probe":
                (
                    started[2]
                    if write_probe
                    else None
                ),
            "prove_probe_contained":
                started[
                    3 if write_probe else 2
                ],
            "validate_binding_profile":
                started[
                    4 if write_probe else 3
                ],
            "invoke_probe":
                started[
                    5 if write_probe else 4
                ],
            "evaluate_case":
                started[
                    6 if write_probe else 5
                ],
        }

    def test_setup_rejects_gate_repo_outside_run_root(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                gate_inside=False,
            )
            iso, i1 = self.make_modules()

            with mock.patch.object(
                F2,
                "load_module",
                side_effect=self.module_loader(
                    iso,
                    i1,
                ),
            ):
                with self.assertRaises(
                    ValueError
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

    def test_setup_rejects_nonexecutable_executables_and_nonregular_profile(
        self,
    ):
        targets = (
            "launcher",
            "guard",
            "collector",
            "profile",
        )

        for target in targets:
            with self.subTest(
                target=target
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )
                    iso, i1 = self.make_modules()

                    if target == "profile":
                        fixture[
                            "profile"
                        ].unlink()
                        fixture[
                            "profile"
                        ].mkdir()
                    else:
                        fixture[
                            target
                        ].chmod(0o600)

                    with mock.patch.object(
                        F2,
                        "load_module",
                        side_effect=
                            self.module_loader(
                                iso,
                                i1,
                            ),
                    ):
                        with self.assertRaises(
                            RuntimeError
                        ):
                            F2.run_f2(
                                fixture["args"]
                            )

    def test_setup_parses_original_profile_checks_guard_builds_runtime_profiles_and_environment(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            original_payload = (
                fixture["profile"].read_bytes()
            )
            original_mode = stat.S_IMODE(
                fixture["profile"].stat().st_mode
            )

            old_log = (
                fixture["artifact"]
                / "timeout"
                / "injector.jsonl"
            )
            old_log.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            old_log.write_text(
                "stale\n",
                encoding="utf-8",
            )

            guard_spy = mock.patch.object(
                F2,
                "guard_handlers",
                wraps=F2.guard_handlers,
            )
            spy = guard_spy.start()
            self.addCleanup(
                guard_spy.stop
            )

            harness = (
                self.install_common_stubs(
                    fixture,
                    containment=
                        self.failing_containment(
                            "fail"
                        ),
                )
            )

            status = F2.run_f2(
                fixture["args"]
            )

            self.assertEqual(
                status,
                1,
            )
            self.assertTrue(
                fixture["runtime"].is_dir()
            )
            fixture[
                "runtime"
            ].resolve(
                strict=True
            ).relative_to(
                fixture[
                    "gate_repo"
                ].resolve(
                    strict=True
                )
            )

            self.assertGreaterEqual(
                spy.call_count,
                2,
            )
            first_profile, first_guard = (
                spy.call_args_list[0].args
            )
            self.assertEqual(
                first_profile,
                fixture["profile_value"],
            )
            self.assertEqual(
                first_guard,
                fixture["guard"],
            )

            injector = (
                fixture["runtime"]
                / "timeout-injector.py"
            )

            harness[
                "write_timeout_injector"
            ].assert_called_once_with(
                injector,
                old_log,
            )
            self.assertFalse(
                old_log.exists()
            )

            original_profile = json.loads(
                original_payload.decode(
                    "utf-8"
                )
            )
            missing_payload = (
                F2.encode_profile(
                    F2.make_missing_profile(
                        original_profile
                    )
                )
            )
            timeout_payload = (
                F2.encode_profile(
                    F2.make_timeout_profile(
                        original_profile,
                        fixture["guard"],
                        injector,
                    )
                )
            )

            profiles = F2.read_json(
                fixture["artifact"]
                / "profiles.json"
            )

            self.assertEqual(
                profiles,
                {
                    "schemaVersion": 1,
                    "originalDigest":
                        F2.binding_digest(
                            original_payload
                        ),
                    "missingDigest":
                        F2.binding_digest(
                            missing_payload
                        ),
                    "timeoutDigest":
                        F2.binding_digest(
                            timeout_payload
                        ),
                    "timeoutSeconds":
                        F2.TIMEOUT_SECONDS,
                    "injectorSleepSeconds":
                        F2.INJECTOR_SLEEP_SECONDS,
                    "injector":
                        str(injector),
                    "injectorDigest":
                        F2.binding_digest(
                            injector.read_bytes()
                        ),
                },
            )

            harness[
                "i1"
            ].sanitize_env.assert_called_once_with(
                os.environ
            )

            harness[
                "i1"
            ].NativePilot.assert_not_called()

            self.assertEqual(
                stat.S_IMODE(
                    fixture["profile"].stat().st_mode
                ),
                original_mode,
            )
            self.assertEqual(
                fixture["profile"].read_bytes(),
                original_payload,
            )

    def test_setup_guard_contract_ambiguity_propagates_runtime_error(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            value = fixture["profile_value"]
            value["hooks"][
                "PreToolUse"
            ][0]["hooks"].append(
                {
                    "type": "command",
                    "command":
                        str(
                            fixture["guard"]
                        ),
                    "args": [],
                }
            )
            fixture["profile"].write_text(
                json.dumps(value)
                + "\n",
                encoding="utf-8",
            )

            iso, i1 = self.make_modules()

            with mock.patch.object(
                F2,
                "load_module",
                side_effect=self.module_loader(
                    iso,
                    i1,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "pilot PreToolUse "
                        "contract ambiguous"
                    ),
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

    def test_execute_rejects_preexisting_marker_or_probe_including_dangling_symlink(
        self,
    ):
        for kind in (
            "marker",
            "probe",
        ):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    runtime = fixture["runtime"]
                    runtime.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    marker = (
                        runtime
                        / (
                            "probe-marker-"
                            f"{self.RUN_ID}-control"
                        )
                    )
                    probe = (
                        runtime
                        / (
                            "probe-"
                            f"{self.RUN_ID}-control.py"
                        )
                    )

                    candidate = (
                        marker
                        if kind == "marker"
                        else probe
                    )

                    try:
                        candidate.symlink_to(
                            runtime
                            / "missing-target"
                        )
                    except OSError as exc:
                        self.skipTest(
                            f"symlink unavailable: {exc}"
                        )

                    harness = (
                        self.install_common_stubs(
                            fixture
                        )
                    )

                    with self.assertRaisesRegex(
                        RuntimeError,
                        (
                            "pre-existing F2 "
                            f"{kind}"
                        ),
                    ):
                        F2.run_f2(
                            fixture["args"]
                        )

                    harness[
                        "write_probe"
                    ].assert_not_called()
                    harness[
                        "i1"
                    ].NativePilot.assert_not_called()

    def test_execute_containment_nonpass_returns_early_without_profile_replacement_or_native_start(
        self,
    ):
        for verdict in (
            "fail",
            "unknown",
        ):
            with self.subTest(
                verdict=verdict
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )
                    original_payload = (
                        fixture["profile"].read_bytes()
                    )

                    harness = (
                        self.install_common_stubs(
                            fixture,
                            containment=
                                self.failing_containment(
                                    verdict
                                ),
                        )
                    )

                    atomic_spy = (
                        mock.patch.object(
                            F2,
                            "atomic_bytes",
                            wraps=F2.atomic_bytes,
                        )
                    )
                    atomic_mock = atomic_spy.start()
                    self.addCleanup(
                        atomic_spy.stop
                    )

                    status = F2.run_f2(
                        fixture["args"]
                    )

                    self.assertEqual(
                        status,
                        (
                            1
                            if verdict == "fail"
                            else 2
                        ),
                    )

                    harness[
                        "i1"
                    ].NativePilot.assert_not_called()
                    atomic_mock.assert_not_called()

                    containment = F2.read_json(
                        fixture["artifact"]
                        / "control"
                        / "containment.json"
                    )
                    self.assertEqual(
                        containment["verdict"],
                        verdict,
                    )

                    final = F2.read_json(
                        fixture["artifact"]
                        / "result.json"
                    )
                    self.assertEqual(
                        final["verdict"],
                        verdict,
                    )
                    self.assertEqual(
                        final["reason"],
                        "control_not_pass",
                    )
                    control = final[
                        "cases"
                    ]["control"]
                    self.assertEqual(
                        control["reason"],
                        (
                            "probe_containment_"
                            "not_proved"
                        ),
                    )
                    self.assertEqual(
                        fixture[
                            "profile"
                        ].read_bytes(),
                        original_payload,
                    )

    def test_execute_profile_replacement_mismatch_raises_and_outer_finally_restores_profile(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            original_payload = (
                fixture["profile"].read_bytes()
            )
            original_mode = stat.S_IMODE(
                fixture["profile"].stat().st_mode
            )

            harness = (
                self.install_common_stubs(
                    fixture
                )
            )

            calls = []

            def corrupt_first_write(
                path,
                payload,
                mode,
            ):
                calls.append(
                    (
                        Path(path),
                        payload,
                        mode,
                    )
                )

                if len(calls) == 1:
                    Path(path).write_bytes(
                        b"wrong-profile"
                    )
                    Path(path).chmod(mode)
                    return

                Path(path).write_bytes(
                    payload
                )
                Path(path).chmod(mode)

            with mock.patch.object(
                F2,
                "atomic_bytes",
                side_effect=corrupt_first_write,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "control profile "
                        "replacement mismatch"
                    ),
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

            self.assertGreaterEqual(
                len(calls),
                2,
            )
            self.assertEqual(
                calls[0],
                (
                    fixture["profile"],
                    original_payload,
                    original_mode,
                ),
            )
            harness[
                "i1"
            ].NativePilot.assert_not_called()
            self.assertEqual(
                fixture["profile"].read_bytes(),
                original_payload,
            )
            self.assertFalse(
                (
                    fixture["artifact"]
                    / "control"
                    / "restore.json"
                ).exists()
            )

    def test_execute_normal_three_cases_replace_profile_start_native_invoke_evaluate_and_restore_each_case(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            original_payload = (
                fixture["profile"].read_bytes()
            )
            original_mode = stat.S_IMODE(
                fixture["profile"].stat().st_mode
            )

            observed_profiles = {}
            observations = {}

            def invoke(
                i1,
                *,
                native,
                command,
                collector,
                claude_config,
                collector_state,
                artifact,
            ):
                label = artifact.name
                observed_profiles[
                    label
                ] = fixture[
                    "profile"
                ].read_bytes()
                observations[label] = {
                    "verdict": "pass",
                    "toolUseId":
                        f"tool-{label}",
                }
                return observations[label]

            def evaluate(**kwargs):
                return {
                    "schemaVersion": 1,
                    "case": kwargs["label"],
                    "verdict": "pass",
                    "checks": [],
                    "observation":
                        kwargs["observation"],
                }

            harness = (
                self.install_common_stubs(
                    fixture,
                    invoke_side_effect=invoke,
                    evaluate_side_effect=evaluate,
                )
            )

            status = F2.run_f2(
                fixture["args"]
            )

            self.assertEqual(
                status,
                0,
            )

            injector = (
                fixture["runtime"]
                / "timeout-injector.py"
            )
            original_profile = json.loads(
                original_payload.decode(
                    "utf-8"
                )
            )
            expected_payloads = {
                "control":
                    original_payload,
                "missing":
                    F2.encode_profile(
                        F2.make_missing_profile(
                            original_profile
                        )
                    ),
                "timeout":
                    F2.encode_profile(
                        F2.make_timeout_profile(
                            original_profile,
                            fixture["guard"],
                            injector,
                        )
                    ),
            }

            self.assertEqual(
                observed_profiles,
                expected_payloads,
            )

            self.assertEqual(
                harness[
                    "i1"
                ].NativePilot.call_count,
                3,
            )

            constructor_calls = (
                harness[
                    "i1"
                ].NativePilot.call_args_list
            )

            for index, label in enumerate(
                (
                    "control",
                    "missing",
                    "timeout",
                )
            ):
                call = constructor_calls[index]

                self.assertEqual(
                    call.args[0],
                    fixture["launcher"],
                )
                self.assertEqual(
                    call.args[1],
                    fixture["gate_repo"],
                )
                self.assertEqual(
                    call.args[2],
                    self.GATE_TEAM,
                )
                self.assertEqual(
                    call.args[3],
                    fixture["claude_config"],
                )
                self.assertEqual(
                    call.args[4],
                    (
                        fixture["artifact"]
                        / label
                        / "native"
                    ),
                )

                env = call.args[5]
                self.assertEqual(
                    env["BASE_ENV"],
                    "preserved",
                )
                self.assertEqual(
                    env[
                        "CLAUDE_CONFIG_DIR"
                    ],
                    str(
                        fixture[
                            "claude_config"
                        ]
                    ),
                )
                self.assertEqual(
                    call.args[6],
                    11.0,
                )

                native = harness[
                    "natives"
                ][index]
                native.start.assert_called_once()
                native.stop.assert_called_once()

                harness[
                    "validate_binding_profile"
                ].assert_any_call(
                    native,
                    expected_payloads[label],
                )

                probe = (
                    fixture["runtime"]
                    / (
                        "probe-"
                        f"{self.RUN_ID}-{label}.py"
                    )
                )
                expected_command = str(
                    probe.resolve(
                        strict=True
                    )
                )

                invoke_call = (
                    harness[
                        "invoke_probe"
                    ].call_args_list[index]
                )
                self.assertIs(
                    invoke_call.args[0],
                    harness["i1"],
                )
                self.assertIs(
                    invoke_call.kwargs[
                        "native"
                    ],
                    native,
                )
                self.assertEqual(
                    invoke_call.kwargs[
                        "command"
                    ],
                    expected_command,
                )
                self.assertEqual(
                    invoke_call.kwargs[
                        "collector"
                    ],
                    fixture["collector"],
                )
                self.assertEqual(
                    invoke_call.kwargs[
                        "claude_config"
                    ],
                    fixture["claude_config"],
                )
                self.assertEqual(
                    invoke_call.kwargs[
                        "collector_state"
                    ],
                    (
                        fixture["artifact"]
                        / label
                        / "collector-state"
                    ),
                )
                self.assertEqual(
                    invoke_call.kwargs[
                        "artifact"
                    ],
                    fixture["artifact"]
                    / label,
                )

                evaluate_call = (
                    harness[
                        "evaluate_case"
                    ].call_args_list[index]
                )
                self.assertEqual(
                    evaluate_call.kwargs[
                        "label"
                    ],
                    label,
                )
                self.assertEqual(
                    evaluate_call.kwargs[
                        "observation"
                    ],
                    observations[label],
                )
                self.assertEqual(
                    evaluate_call.kwargs[
                        "marker"
                    ],
                    (
                        fixture["runtime"]
                        / (
                            "probe-marker-"
                            f"{self.RUN_ID}-{label}"
                        )
                    ),
                )
                self.assertEqual(
                    evaluate_call.kwargs[
                        "injector_log"
                    ],
                    (
                        fixture["artifact"]
                        / "timeout"
                        / "injector.jsonl"
                        if label == "timeout"
                        else None
                    ),
                )

                result = F2.read_json(
                    fixture["artifact"]
                    / label
                    / "result.json"
                )
                self.assertEqual(
                    result["case"],
                    label,
                )
                self.assertEqual(
                    result["verdict"],
                    "pass",
                )

                restore = F2.read_json(
                    fixture["artifact"]
                    / label
                    / "restore.json"
                )
                self.assertEqual(
                    restore[
                        "restoredDigest"
                    ],
                    F2.binding_digest(
                        original_payload
                    ),
                )
                self.assertEqual(
                    restore[
                        "originalDigest"
                    ],
                    F2.binding_digest(
                        original_payload
                    ),
                )
                self.assertIs(
                    restore[
                        "matchesOriginal"
                    ],
                    True,
                )

            self.assertEqual(
                fixture["profile"].read_bytes(),
                original_payload,
            )
            self.assertEqual(
                stat.S_IMODE(
                    fixture["profile"].stat().st_mode
                ),
                original_mode,
            )

    def test_execute_exception_after_native_start_stops_native_restores_profile_writes_restore_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            original_payload = (
                fixture["profile"].read_bytes()
            )

            harness = (
                self.install_common_stubs(
                    fixture,
                    invoke_side_effect=
                        RuntimeError(
                            "invoke exploded"
                        ),
                )
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "invoke exploded",
            ):
                F2.run_f2(
                    fixture["args"]
                )

            native = harness[
                "natives"
            ][0]
            native.start.assert_called_once()
            native.stop.assert_called_once()

            self.assertEqual(
                fixture["profile"].read_bytes(),
                original_payload,
            )

            restore = F2.read_json(
                fixture["artifact"]
                / "control"
                / "restore.json"
            )

            self.assertEqual(
                restore[
                    "restoredDigest"
                ],
                F2.binding_digest(
                    original_payload
                ),
            )
            self.assertEqual(
                restore[
                    "originalDigest"
                ],
                F2.binding_digest(
                    original_payload
                ),
            )
            self.assertIs(
                restore[
                    "matchesOriginal"
                ],
                True,
            )

    def test_execute_restore_failure_overrides_original_exception_and_outer_finally_repairs_profile(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            original_payload = (
                fixture["profile"].read_bytes()
            )
            original_mode = stat.S_IMODE(
                fixture["profile"].stat().st_mode
            )

            harness = (
                self.install_common_stubs(
                    fixture,
                    invoke_side_effect=
                        ValueError(
                            "original invoke failure"
                        ),
                )
            )

            calls = []

            def write_with_bad_inner_restore(
                path,
                payload,
                mode,
            ):
                calls.append(
                    (
                        Path(path),
                        payload,
                        mode,
                    )
                )

                if len(calls) == 2:
                    Path(path).write_bytes(
                        b"corrupt-after-restore"
                    )
                    Path(path).chmod(mode)
                    return

                Path(path).write_bytes(
                    payload
                )
                Path(path).chmod(mode)

            with mock.patch.object(
                F2,
                "atomic_bytes",
                side_effect=
                    write_with_bad_inner_restore,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "control profile "
                        "restore failed"
                    ),
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

            native = harness[
                "natives"
            ][0]
            native.start.assert_called_once()
            native.stop.assert_called_once()

            self.assertGreaterEqual(
                len(calls),
                3,
            )

            restore = F2.read_json(
                fixture["artifact"]
                / "control"
                / "restore.json"
            )

            self.assertEqual(
                restore[
                    "restoredDigest"
                ],
                F2.binding_digest(
                    b"corrupt-after-restore"
                ),
            )
            self.assertEqual(
                restore[
                    "originalDigest"
                ],
                F2.binding_digest(
                    original_payload
                ),
            )
            self.assertIs(
                restore[
                    "matchesOriginal"
                ],
                False,
            )

            self.assertEqual(
                fixture["profile"].read_bytes(),
                original_payload,
            )
            self.assertEqual(
                stat.S_IMODE(
                    fixture["profile"].stat().st_mode
                ),
                original_mode,
            )


class PilotGateF2RoundF(unittest.TestCase):
    RUN_ID = "round-f"
    GATE_TEAM = "agmsg-g4gate-round-f"

    def make_fixture(self, root: Path):
        root = root.resolve()
        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        scripts = gate_repo / "scripts"
        claude_dir = gate_repo / ".claude"
        claude_config = run_root / "claude"
        artifact_dir = root / "artifacts"

        scripts.mkdir(parents=True, exist_ok=True)
        claude_dir.mkdir(parents=True, exist_ok=True)
        claude_config.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        launcher = scripts / "pilot-launcher.sh"
        guard = scripts / "pm-pilot-pretool-guard"
        collector = scripts / "pilot-collector.sh"

        for path in (launcher, guard, collector):
            path.write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            path.chmod(0o700)

        profile = claude_dir / "settings.local.json"
        profile_value = {
            "schemaVersion": 1,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(guard),
                                "args": [],
                            }
                        ],
                    }
                ],
            },
        }
        profile.write_text(
            json.dumps(
                profile_value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        profile.chmod(0o640)

        args = F2.argparse.Namespace(
            gate_repo=str(gate_repo),
            run_root=str(run_root),
            claude_config=str(claude_config),
            artifact_dir=str(artifact_dir),
            run_id=self.RUN_ID,
            gate_team=self.GATE_TEAM,
            timeout_seconds=5,
        )

        return {
            "root": root,
            "run_root": run_root,
            "gate_repo": gate_repo,
            "launcher": launcher,
            "guard": guard,
            "collector": collector,
            "profile": profile,
            "profile_value": profile_value,
            "original_payload": profile.read_bytes(),
            "original_mode": stat.S_IMODE(
                profile.stat().st_mode
            ),
            "claude_config": claude_config,
            "artifact_dir": artifact_dir,
            "artifact": artifact_dir / "F2",
            "runtime": gate_repo / ".agmsg-gate" / "f2",
            "args": args,
        }

    def make_modules(self):
        iso = mock.Mock()
        iso.canonical.side_effect = (
            lambda value: str(
                Path(value).resolve(strict=True)
            )
        )

        i1 = mock.Mock()
        i1.sanitize_env.return_value = {
            "BASE_ENV": "preserved",
        }

        return iso, i1

    def module_loader(self, iso, i1):
        def load(path, name):
            if name == "pilot_gate_isolation":
                return iso
            if name == "pilot_gate_i1":
                return i1
            raise AssertionError(
                f"unexpected module request: {path} {name}"
            )

        return load

    def injector_writer(self, path, log_path):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        path.write_bytes(
            b"#!/usr/bin/env python3\n"
            b"raise SystemExit(0)\n"
        )
        path.chmod(0o700)

    def probe_writer(self, path, marker, run_id, case):
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        path.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def passing_containment(self):
        return [
            F2.assertion(
                "containment",
                True,
                "fixture",
            )
        ]

    def make_native(self, label):
        native = mock.Mock()
        native.binding = Path(
            f"/tmp/f2-{label}-binding.json"
        )
        native.session_id = f"session-{label}"
        native.start = mock.Mock()
        native.stop = mock.Mock()
        return native

    def harness(
        self,
        fixture,
        *,
        verdicts=None,
        evaluate_side_effect=None,
        probe_side_effect=None,
        atomic_json_side_effect=None,
        atomic_bytes_side_effect=None,
    ):
        from contextlib import ExitStack

        iso, i1 = self.make_modules()

        labels = (
            "control",
            "missing",
            "timeout",
        )
        natives = [
            self.make_native(label)
            for label in labels
        ]
        i1.NativePilot.side_effect = natives

        if verdicts is None:
            verdicts = {
                "control": "pass",
                "missing": "pass",
                "timeout": "pass",
            }

        evaluate_labels = []

        if evaluate_side_effect is None:
            def evaluate(**kwargs):
                label = kwargs["label"]
                evaluate_labels.append(label)
                return {
                    "schemaVersion": 1,
                    "case": label,
                    "verdict": verdicts[label],
                    "checks": [],
                    "observation": kwargs["observation"],
                }

            evaluate_side_effect = evaluate

        if probe_side_effect is None:
            probe_side_effect = self.probe_writer

        real_atomic_json = F2.atomic_json
        real_atomic_bytes = F2.atomic_bytes

        stack = ExitStack()

        load_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "load_module",
                side_effect=self.module_loader(
                    iso,
                    i1,
                ),
            )
        )
        injector_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "write_timeout_injector",
                side_effect=self.injector_writer,
            )
        )
        probe_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "write_probe",
                side_effect=probe_side_effect,
            )
        )
        containment_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "prove_probe_contained",
                return_value=self.passing_containment(),
            )
        )
        binding_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "validate_binding_profile",
                return_value=F2.assertion(
                    "binding-profile-digest",
                    True,
                    "fixture",
                ),
            )
        )
        invoke_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "invoke_probe",
                side_effect=lambda i1, **kwargs: {
                    "verdict": "pass",
                    "toolUseId": (
                        "tool-"
                        + kwargs["artifact"].name
                    ),
                },
            )
        )
        evaluate_mock = stack.enter_context(
            mock.patch.object(
                F2,
                "evaluate_case",
                side_effect=evaluate_side_effect,
            )
        )

        if atomic_json_side_effect is not None:
            atomic_json_mock = stack.enter_context(
                mock.patch.object(
                    F2,
                    "atomic_json",
                    side_effect=atomic_json_side_effect,
                )
            )
        else:
            atomic_json_mock = stack.enter_context(
                mock.patch.object(
                    F2,
                    "atomic_json",
                    wraps=real_atomic_json,
                )
            )

        if atomic_bytes_side_effect is not None:
            atomic_bytes_mock = stack.enter_context(
                mock.patch.object(
                    F2,
                    "atomic_bytes",
                    side_effect=atomic_bytes_side_effect,
                )
            )
        else:
            atomic_bytes_mock = stack.enter_context(
                mock.patch.object(
                    F2,
                    "atomic_bytes",
                    wraps=real_atomic_bytes,
                )
            )

        values = {
            "iso": iso,
            "i1": i1,
            "natives": natives,
            "evaluate_labels": evaluate_labels,
            "load_module": load_mock,
            "write_timeout_injector": injector_mock,
            "write_probe": probe_mock,
            "prove_probe_contained": containment_mock,
            "validate_binding_profile": binding_mock,
            "invoke_probe": invoke_mock,
            "evaluate_case": evaluate_mock,
            "atomic_json": atomic_json_mock,
            "atomic_bytes": atomic_bytes_mock,
            "real_atomic_json": real_atomic_json,
            "real_atomic_bytes": real_atomic_bytes,
        }

        class HarnessContext:
            def __enter__(self):
                return values

            def __exit__(
                self,
                exc_type,
                exc,
                traceback,
            ):
                stack.close()
                return False

        return HarnessContext()

    def check_map(self, result):
        return {
            item["name"]: item
            for item in result["checks"]
        }

    def test_control_fail_or_unknown_returns_early_and_skips_missing_timeout(
        self,
    ):
        for verdict, expected_status in (
            ("fail", 1),
            ("unknown", 2),
        ):
            with self.subTest(verdict=verdict):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    with self.harness(
                        fixture,
                        verdicts={
                            "control": verdict,
                            "missing": "pass",
                            "timeout": "pass",
                        },
                    ) as harness:
                        status = F2.run_f2(
                            fixture["args"]
                        )

                    self.assertEqual(
                        status,
                        expected_status,
                    )
                    self.assertEqual(
                        harness["evaluate_labels"],
                        ["control"],
                    )
                    self.assertEqual(
                        harness[
                            "i1"
                        ].NativePilot.call_count,
                        1,
                    )
                    self.assertEqual(
                        harness[
                            "invoke_probe"
                        ].call_count,
                        1,
                    )
                    self.assertEqual(
                        harness[
                            "evaluate_case"
                        ].call_count,
                        1,
                    )

                    result = F2.read_json(
                        fixture["artifact"]
                        / "result.json"
                    )

                    self.assertEqual(
                        result,
                        {
                            "schemaVersion": 1,
                            "check": "F2",
                            "runId": self.RUN_ID,
                            "verdict": verdict,
                            "reason": "control_not_pass",
                            "cases": {
                                "control": {
                                    "schemaVersion": 1,
                                    "case": "control",
                                    "verdict": verdict,
                                    "checks": [],
                                    "observation": {
                                        "verdict": "pass",
                                        "toolUseId":
                                            "tool-control",
                                    },
                                }
                            },
                        },
                    )

    def test_three_cases_execute_in_control_missing_timeout_order_and_full_result_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ) as harness:
                status = F2.run_f2(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )
            self.assertEqual(
                harness["evaluate_labels"],
                [
                    "control",
                    "missing",
                    "timeout",
                ],
            )
            self.assertEqual(
                harness[
                    "i1"
                ].NativePilot.call_count,
                3,
            )

            result = F2.read_json(
                fixture["artifact"]
                / "result.json"
            )
            checks = self.check_map(result)

            self.assertEqual(
                result["schemaVersion"],
                1,
            )
            self.assertEqual(
                result["check"],
                "F2",
            )
            self.assertEqual(
                result["runId"],
                self.RUN_ID,
            )
            self.assertEqual(
                result["verdict"],
                "pass",
            )
            self.assertEqual(
                result["control"]["case"],
                "control",
            )
            self.assertEqual(
                result["F2a"]["case"],
                "missing",
            )
            self.assertEqual(
                result["F2b"]["case"],
                "timeout",
            )
            self.assertEqual(
                set(checks),
                {
                    "F2a-missing-pass",
                    "F2b-timeout-pass",
                    "profile-finally-restored",
                },
            )
            self.assertTrue(
                all(
                    item["verdict"] == "pass"
                    for item in checks.values()
                )
            )

    def run_missing_timeout_scenarios(self, scenarios):
        results = []
        for verdicts, expected_status, expected_verdict in scenarios:
            with self.subTest(verdicts=verdicts):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )
                    with self.harness(
                        fixture,
                        verdicts=verdicts,
                    ):
                        status = F2.run_f2(
                            fixture["args"]
                        )

                    self.assertEqual(status, expected_status)
                    result = F2.read_json(
                        fixture["artifact"]
                        / "result.json"
                    )
                    self.assertEqual(
                        result["verdict"],
                        expected_verdict,
                    )

                    checks = self.check_map(result)
                    # Each case verdict is lifted as-is: unknown stays
                    # unknown, it is not folded into fail.
                    self.assertEqual(
                        checks["F2a-missing-pass"]["verdict"],
                        verdicts["missing"],
                    )
                    self.assertEqual(
                        checks["F2b-timeout-pass"]["verdict"],
                        verdicts["timeout"],
                    )
        return results

    def test_missing_or_timeout_fail_makes_full_aggregate_fail_exit_one(
        self,
    ):
        self.run_missing_timeout_scenarios(
            (
                ({"control": "pass", "missing": "fail",
                  "timeout": "pass"}, 1, "fail"),
                ({"control": "pass", "missing": "pass",
                  "timeout": "fail"}, 1, "fail"),
                # fail is not hidden by unknown
                ({"control": "pass", "missing": "unknown",
                  "timeout": "fail"}, 1, "fail"),
                ({"control": "pass", "missing": "fail",
                  "timeout": "unknown"}, 1, "fail"),
            )
        )

    def test_missing_or_timeout_unknown_makes_full_aggregate_unknown_exit_two(
        self,
    ):
        # Runbook contract: no fail + some unknown -> unknown (rc 2).
        self.run_missing_timeout_scenarios(
            (
                ({"control": "pass", "missing": "unknown",
                  "timeout": "pass"}, 2, "unknown"),
                ({"control": "pass", "missing": "pass",
                  "timeout": "unknown"}, 2, "unknown"),
                ({"control": "pass", "missing": "unknown",
                  "timeout": "unknown"}, 2, "unknown"),
            )
        )

    def test_profile_finally_restored_check_fails_if_profile_is_mutated_after_timeout_restore(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            real_atomic_json = F2.atomic_json

            def atomic_json_side_effect(
                path,
                value,
            ):
                real_atomic_json(
                    path,
                    value,
                )
                path = Path(path)
                if (
                    path.name == "restore.json"
                    and path.parent.name == "timeout"
                ):
                    fixture[
                        "profile"
                    ].write_bytes(
                        b"tampered-after-timeout"
                    )

            with self.harness(
                fixture,
                atomic_json_side_effect=
                    atomic_json_side_effect,
            ) as harness:
                status = F2.run_f2(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                1,
            )

            result = F2.read_json(
                fixture["artifact"]
                / "result.json"
            )
            checks = self.check_map(result)

            self.assertEqual(
                checks[
                    "F2a-missing-pass"
                ]["verdict"],
                "pass",
            )
            self.assertEqual(
                checks[
                    "F2b-timeout-pass"
                ]["verdict"],
                "pass",
            )
            self.assertEqual(
                checks[
                    "profile-finally-restored"
                ]["verdict"],
                "fail",
            )
            self.assertEqual(
                result["verdict"],
                "fail",
            )

            self.assertEqual(
                fixture["profile"].read_bytes(),
                fixture["original_payload"],
            )

            self.assertEqual(
                harness[
                    "atomic_bytes"
                ].call_count,
                7,
            )

    def test_normal_completion_outer_finally_does_not_perform_extra_restore(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            with self.harness(
                fixture
            ) as harness:
                status = F2.run_f2(
                    fixture["args"]
                )

            self.assertEqual(
                status,
                0,
            )
            self.assertEqual(
                harness[
                    "atomic_bytes"
                ].call_count,
                6,
            )
            self.assertEqual(
                fixture["profile"].read_bytes(),
                fixture["original_payload"],
            )
            self.assertFalse(
                (
                    fixture["artifact"]
                    / "emergency-restore-error.json"
                ).exists()
            )

    def test_exception_before_execute_inner_finally_with_dirty_profile_is_emergency_restored_and_original_exception_propagates(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            calls = {"count": 0}

            def probe_side_effect(
                path,
                marker,
                run_id,
                case,
            ):
                calls["count"] += 1
                if case == "missing":
                    fixture[
                        "profile"
                    ].write_bytes(
                        b"dirty-before-inner-finally"
                    )
                    raise ValueError(
                        "missing probe exploded"
                    )
                return self.probe_writer(
                    path,
                    marker,
                    run_id,
                    case,
                )

            with self.harness(
                fixture,
                probe_side_effect=
                    probe_side_effect,
            ) as harness:
                with self.assertRaisesRegex(
                    ValueError,
                    "missing probe exploded",
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

            self.assertEqual(
                calls["count"],
                2,
            )
            self.assertEqual(
                fixture["profile"].read_bytes(),
                fixture["original_payload"],
            )
            self.assertEqual(
                harness[
                    "atomic_bytes"
                ].call_count,
                3,
            )

    def test_emergency_restore_failure_writes_unknown_evidence_and_preserves_original_exception(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )
            real_atomic_bytes = F2.atomic_bytes

            def probe_side_effect(
                path,
                marker,
                run_id,
                case,
            ):
                if case == "missing":
                    fixture[
                        "profile"
                    ].write_bytes(
                        b"dirty-before-emergency"
                    )
                    raise LookupError(
                        "original missing failure"
                    )
                return self.probe_writer(
                    path,
                    marker,
                    run_id,
                    case,
                )

            atomic_calls = {"count": 0}

            def atomic_bytes_side_effect(
                path,
                payload,
                mode,
            ):
                atomic_calls["count"] += 1
                if atomic_calls["count"] == 3:
                    raise OSError(
                        "emergency restore failed"
                    )
                return real_atomic_bytes(
                    path,
                    payload,
                    mode,
                )

            with self.harness(
                fixture,
                probe_side_effect=
                    probe_side_effect,
                atomic_bytes_side_effect=
                    atomic_bytes_side_effect,
            ):
                with self.assertRaisesRegex(
                    LookupError,
                    "original missing failure",
                ):
                    F2.run_f2(
                        fixture["args"]
                    )

            evidence = F2.read_json(
                fixture["artifact"]
                / "emergency-restore-error.json"
            )

            self.assertEqual(
                evidence,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason": (
                        "profile_restore_failed:"
                        "OSError:"
                        "emergency restore failed"
                    ),
                },
            )

    def test_outer_finally_profile_read_failure_writes_evidence_and_original_exception_propagates(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            def probe_side_effect(
                path,
                marker,
                run_id,
                case,
            ):
                if case == "missing":
                    raise RuntimeError(
                        "missing execution failed"
                    )
                return self.probe_writer(
                    path,
                    marker,
                    run_id,
                    case,
                )

            real_read_bytes = Path.read_bytes
            profile_reads = {"count": 0}

            def read_bytes_side_effect(path_self):
                if path_self == fixture["profile"]:
                    profile_reads["count"] += 1
                    if profile_reads["count"] == 4:
                        raise OSError(
                            "profile read unavailable"
                        )
                return real_read_bytes(
                    path_self
                )

            with mock.patch.object(
                Path,
                "read_bytes",
                new=read_bytes_side_effect,
            ):
                with self.harness(
                    fixture,
                    probe_side_effect=
                        probe_side_effect,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "missing execution failed",
                    ):
                        F2.run_f2(
                            fixture["args"]
                        )

            evidence = F2.read_json(
                fixture["artifact"]
                / "emergency-restore-error.json"
            )

            self.assertEqual(
                evidence,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason": (
                        "profile_restore_failed:"
                        "OSError:"
                        "profile read unavailable"
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
            real_atomic_json = F2.atomic_json
            real_atomic_bytes = F2.atomic_bytes

            def probe_side_effect(
                path,
                marker,
                run_id,
                case,
            ):
                if case == "missing":
                    fixture[
                        "profile"
                    ].write_bytes(
                        b"dirty"
                    )
                    raise KeyError(
                        "original-key-error"
                    )
                return self.probe_writer(
                    path,
                    marker,
                    run_id,
                    case,
                )

            atomic_calls = {"count": 0}

            def atomic_bytes_side_effect(
                path,
                payload,
                mode,
            ):
                atomic_calls["count"] += 1
                if atomic_calls["count"] == 3:
                    raise OSError(
                        "restore failed"
                    )
                return real_atomic_bytes(
                    path,
                    payload,
                    mode,
                )

            def atomic_json_side_effect(
                path,
                value,
            ):
                if Path(path).name == (
                    "emergency-restore-error.json"
                ):
                    raise OSError(
                        "evidence write failed"
                    )
                return real_atomic_json(
                    path,
                    value,
                )

            with self.harness(
                fixture,
                probe_side_effect=
                    probe_side_effect,
                atomic_bytes_side_effect=
                    atomic_bytes_side_effect,
                atomic_json_side_effect=
                    atomic_json_side_effect,
            ):
                with self.assertRaises(
                    KeyError
                ) as cm:
                    F2.run_f2(
                        fixture["args"]
                    )

            self.assertEqual(
                cm.exception.args,
                ("original-key-error",),
            )
            self.assertFalse(
                (
                    fixture["artifact"]
                    / "emergency-restore-error.json"
                ).exists()
            )

if __name__ == "__main__":
    unittest.main()