"""Unit tests for pilot-gate-isolation.py utility and simple CLI contracts."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(
    os.environ.get(
        "PILOT_GATE_ISOLATION_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-isolation.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_isolation",
    HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load pilot gate isolation helper: {HELPER}")

ISOLATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ISOLATION)


UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}Z$"
)
RUN_ID_RE = re.compile(
    r"^\d{8}T\d{6}Z-[0-9a-f]{8}$"
)


class PilotGateIsolationUtilityTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_utc_now_is_second_precision_utc_with_z_suffix(self):
        value = ISOLATION.utc_now()

        self.assertRegex(
            value,
            UTC_RE,
        )
        self.assertTrue(
            value.endswith("Z")
        )
        self.assertNotIn(
            "+00:00",
            value,
        )

    def test_die_writes_prefixed_stderr_and_defaults_to_unknown_status(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                ISOLATION.die(
                    "cannot observe fixture"
                )

        self.assertEqual(
            raised.exception.code,
            2,
        )
        self.assertEqual(
            stderr.getvalue(),
            (
                "pilot-gate-isolation: "
                "cannot observe fixture\n"
            ),
        )

    def test_die_uses_explicit_status(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                ISOLATION.die(
                    "definite contradiction",
                    status=1,
                )

        self.assertEqual(
            raised.exception.code,
            1,
        )
        self.assertEqual(
            stderr.getvalue(),
            (
                "pilot-gate-isolation: "
                "definite contradiction\n"
            ),
        )

    def test_write_json_creates_parent_and_atomically_replaces_sorted_utf8_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = (
                root
                / "missing"
                / "parents"
                / "state.json"
            )

            value = {
                "z": 1,
                "日本語": "河童",
                "a": {
                    "β": True,
                },
            }

            real_replace = os.replace

            with mock.patch.object(
                ISOLATION.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                ISOLATION.write_json(
                    output,
                    value,
                )

            self.assertTrue(
                output.is_file()
            )

            replace_mock.assert_called_once()
            source_arg, destination_arg = (
                replace_mock.call_args.args
            )

            expected_temporary = output.with_name(
                (
                    f".{output.name}."
                    f"{os.getpid()}.tmp"
                )
            )

            self.assertEqual(
                Path(source_arg),
                expected_temporary,
            )
            self.assertEqual(
                Path(destination_arg),
                output,
            )
            self.assertFalse(
                expected_temporary.exists()
            )

            raw = output.read_text(
                encoding="utf-8"
            )

            self.assertEqual(
                raw,
                (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n"
                ),
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
            self.assertIn(
                "河童",
                raw,
            )
            self.assertNotIn(
                r"\u65e5",
                raw,
            )

            self.assertEqual(
                ISOLATION.load_json(output),
                value,
            )

    def test_write_json_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "value.json"

            output.write_text(
                '{"old": true}\n',
                encoding="utf-8",
            )

            ISOLATION.write_json(
                output,
                {
                    "new": "value",
                },
            )

            self.assertEqual(
                ISOLATION.load_json(output),
                {
                    "new": "value",
                },
            )

    def test_sha256_file_matches_hashlib_across_chunk_boundary(self):
        cases = (
            b"known-content\n",
            b"a" * (1024 * 1024),
            (
                b"b" * (1024 * 1024)
                + b"!"
            ),
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            for index, content in enumerate(cases):
                with self.subTest(
                    size=len(content)
                ):
                    path = (
                        root
                        / f"payload-{index}.bin"
                    )
                    path.write_bytes(content)

                    self.assertEqual(
                        ISOLATION.sha256_file(
                            path
                        ),
                        hashlib.sha256(
                            content
                        ).hexdigest(),
                    )

    def test_canonical_resolves_symlink_and_obeys_strict_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()

            link = root / "link"

            try:
                link.symlink_to(
                    real,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            self.assertEqual(
                ISOLATION.canonical(link),
                str(real.resolve()),
            )

            missing = (
                link
                / "missing"
                / "leaf"
            )

            with self.assertRaises(
                FileNotFoundError
            ):
                ISOLATION.canonical(
                    missing,
                    strict=True,
                )

            self.assertEqual(
                ISOLATION.canonical(
                    missing,
                    strict=False,
                ),
                str(
                    real.resolve()
                    / "missing"
                    / "leaf"
                ),
            )

    def test_is_within_handles_inside_equal_outside_and_disjoint_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "parent"
            child = (
                parent
                / "child"
                / "leaf"
            )
            outside = root / "outside"

            parent.mkdir()
            outside.mkdir()

            self.assertTrue(
                ISOLATION.is_within(
                    str(child),
                    str(parent),
                )
            )
            self.assertTrue(
                ISOLATION.is_within(
                    str(parent),
                    str(parent),
                    allow_equal=True,
                )
            )
            self.assertFalse(
                ISOLATION.is_within(
                    str(parent),
                    str(parent),
                    allow_equal=False,
                )
            )
            self.assertFalse(
                ISOLATION.is_within(
                    str(outside),
                    str(parent),
                )
            )

    def test_is_within_returns_false_when_commonpath_relation_is_unavailable(self):
        with mock.patch.object(
            ISOLATION.os.path,
            "commonpath",
            side_effect=ValueError(
                "different drives"
            ),
        ):
            self.assertFalse(
                ISOLATION.is_within(
                    "/child",
                    "/parent",
                )
            )

    def test_run_command_captures_output_and_nonzero_status_without_raising(self):
        result = ISOLATION.run_command(
            [
                "sh",
                "-c",
                (
                    "printf '%s\\n' out; "
                    "printf '%s\\n' err >&2; "
                    "exit 3"
                ),
            ]
        )

        self.assertEqual(
            result.returncode,
            3,
        )
        self.assertEqual(
            result.stdout,
            "out\n",
        )
        self.assertEqual(
            result.stderr,
            "err\n",
        )

    def test_run_command_honors_cwd_and_env(self):
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["PILOT_GATE_TEST_VALUE"] = (
                "isolated-value"
            )

            result = ISOLATION.run_command(
                [
                    "sh",
                    "-c",
                    (
                        "printf '%s\\n' \"$PWD\"; "
                        "printf '%s\\n' "
                        "\"$PILOT_GATE_TEST_VALUE\""
                    ),
                ],
                cwd=temp,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                0,
            )
            lines = (
                result.stdout
                .splitlines()
            )
            self.assertEqual(
                Path(lines[0]).resolve(),
                Path(temp).resolve(),
            )
            self.assertEqual(
                lines[1],
                "isolated-value",
            )

    def test_git_output_runs_git_against_requested_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()

            init = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "init",
                    "-q",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(
                init.returncode,
                0,
                init.stderr,
            )

            result = ISOLATION.git_output(
                str(repo),
                [
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )
            self.assertEqual(
                result.stdout.strip(),
                "true",
            )

    def test_assertion_maps_tristate_verdict_and_preserves_fields(self):
        detail = {
            "source": "fixture",
        }

        cases = (
            (True, "pass"),
            (False, "fail"),
            (None, "unknown"),
        )

        for passed, verdict in cases:
            with self.subTest(
                passed=passed
            ):
                self.assertEqual(
                    ISOLATION.assertion(
                        "X.1",
                        "example",
                        passed,
                        detail,
                    ),
                    {
                        "number": "X.1",
                        "name": "example",
                        "verdict": verdict,
                        "detail": detail,
                    },
                )

    def test_overall_status_uses_fail_unknown_pass_priority_and_empty_is_pass(self):
        passed = ISOLATION.assertion(
            1,
            "pass",
            True,
            None,
        )
        failed = ISOLATION.assertion(
            2,
            "fail",
            False,
            None,
        )
        unknown = ISOLATION.assertion(
            3,
            "unknown",
            None,
            None,
        )

        self.assertEqual(
            ISOLATION.overall_status(
                [
                    passed,
                    unknown,
                    failed,
                ]
            ),
            ("fail", 1),
        )
        self.assertEqual(
            ISOLATION.overall_status(
                [
                    passed,
                    unknown,
                ]
            ),
            ("unknown", 2),
        )
        self.assertEqual(
            ISOLATION.overall_status(
                [
                    passed,
                    passed,
                ]
            ),
            ("pass", 0),
        )
        self.assertEqual(
            ISOLATION.overall_status(
                []
            ),
            ("pass", 0),
        )

    def test_longest_existing_ancestor_returns_deepest_existing_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deepest = (
                root
                / "existing"
                / "deep"
            )
            deepest.mkdir(
                parents=True
            )

            target = (
                deepest
                / "missing"
                / "leaf"
            )

            self.assertEqual(
                ISOLATION.longest_existing_ancestor(
                    str(target)
                ),
                deepest,
            )

    def test_longest_existing_ancestor_raises_when_no_ancestor_can_be_proved(self):
        with mock.patch.object(
            ISOLATION.pathlib.Path,
            "exists",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                FileNotFoundError,
                "no existing ancestor",
            ):
                ISOLATION.longest_existing_ancestor(
                    "/never/exists"
                )

    def test_canonical_nonexistent_resolves_existing_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()

            link = root / "link"

            try:
                link.symlink_to(
                    real,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            target = (
                link
                / "missing"
                / "leaf"
            )

            self.assertEqual(
                ISOLATION.canonical_nonexistent(
                    str(target)
                ),
                str(
                    real.resolve()
                    / "missing"
                    / "leaf"
                ),
            )

    def test_no_symlink_components_accepts_deep_tree_without_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            target_parent = (
                root
                / "one"
                / "two"
                / "three"
            )
            target_parent.mkdir(
                parents=True
            )

            result = (
                ISOLATION.no_symlink_components(
                    str(root),
                    str(target_parent),
                )
            )

            self.assertEqual(
                result,
                (True, []),
            )

    def test_no_symlink_components_detects_symlink_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            real_root = base / "real-root"
            real_root.mkdir()
            (real_root / "child").mkdir()

            symlink_root = base / "root-link"

            try:
                symlink_root.symlink_to(
                    real_root,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            passed, problems = (
                ISOLATION.no_symlink_components(
                    str(symlink_root),
                    str(
                        symlink_root
                        / "child"
                    ),
                )
            )

            self.assertFalse(passed)
            self.assertIn(
                str(
                    symlink_root.absolute()
                ),
                problems,
            )

    def test_no_symlink_components_detects_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "root"
            root.mkdir()

            real_middle = (
                root / "real-middle"
            )
            real_middle.mkdir()
            (real_middle / "leaf").mkdir()

            middle_link = (
                root / "middle-link"
            )

            try:
                middle_link.symlink_to(
                    real_middle,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            passed, problems = (
                ISOLATION.no_symlink_components(
                    str(root),
                    str(
                        middle_link
                        / "leaf"
                    ),
                )
            )

            self.assertFalse(passed)
            self.assertIn(
                str(
                    middle_link.absolute()
                ),
                problems,
            )

    def test_no_symlink_components_returns_unknown_when_target_parent_is_outside_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "root"
            outside = (
                base
                / "outside"
                / "parent"
            )
            root.mkdir()
            outside.mkdir(
                parents=True
            )

            passed, problems = (
                ISOLATION.no_symlink_components(
                    str(root),
                    str(outside),
                )
            )

            self.assertIsNone(
                passed
            )
            self.assertEqual(
                len(problems),
                1,
            )
            self.assertIn(
                "path relation unavailable:",
                problems[0],
            )

    def test_no_symlink_components_skips_nonexistent_trailing_components(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            existing = root / "existing"
            existing.mkdir(
                parents=True
            )

            target_parent = (
                existing
                / "not-created"
                / "also-missing"
            )

            passed, problems = (
                ISOLATION.no_symlink_components(
                    str(root),
                    str(target_parent),
                )
            )

            self.assertTrue(
                passed
            )
            self.assertEqual(
                problems,
                [],
            )

    def test_cli_new_run_id_has_expected_format_and_is_unique(self):
        first = self.run_cli(
            "new-run-id"
        )
        second = self.run_cli(
            "new-run-id"
        )

        self.assertEqual(
            first.returncode,
            0,
            first.stderr,
        )
        self.assertEqual(
            second.returncode,
            0,
            second.stderr,
        )

        first_value = (
            first.stdout.strip()
        )
        second_value = (
            second.stdout.strip()
        )

        self.assertRegex(
            first_value,
            RUN_ID_RE,
        )
        self.assertRegex(
            second_value,
            RUN_ID_RE,
        )
        self.assertNotEqual(
            first_value,
            second_value,
        )

    def test_cli_gate_team_encodes_safe_run_id_and_rejects_unsafe_run_id(self):
        run_id = (
            "20260911T000000Z-deadbeef"
        )

        result = self.run_cli(
            "gate-team",
            run_id,
        )

        self.assertEqual(
            result.returncode,
            0,
            result.stderr,
        )
        self.assertEqual(
            result.stdout.strip(),
            f"agmsg-g4gate-{run_id}",
        )

        rejected = self.run_cli(
            "gate-team",
            "bad/run-id",
        )

        self.assertEqual(
            rejected.returncode,
            2,
        )
        self.assertIn(
            (
                "pilot-gate-isolation: "
                "run id cannot be encoded "
                "as gate team"
            ),
            rejected.stderr,
        )

    def test_cli_canonical_outputs_real_path_and_missing_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()
            link = root / "link"

            try:
                link.symlink_to(
                    real,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            result = self.run_cli(
                "canonical",
                str(link),
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )
            self.assertEqual(
                result.stdout.strip(),
                str(real.resolve()),
            )

            missing = self.run_cli(
                "canonical",
                str(
                    root
                    / "does-not-exist"
                ),
            )

            self.assertEqual(
                missing.returncode,
                2,
            )
            self.assertIn(
                "pilot-gate-isolation: cannot canonicalize",
                missing.stderr,
            )

    def test_cli_sha256_outputs_expected_digest_and_missing_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = (
                root / "payload.bin"
            )
            content = (
                b"pilot-gate-sha256\n"
            )
            payload.write_bytes(
                content
            )

            result = self.run_cli(
                "sha256",
                str(payload),
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )
            self.assertEqual(
                result.stdout.strip(),
                hashlib.sha256(
                    content
                ).hexdigest(),
            )

            missing = self.run_cli(
                "sha256",
                str(
                    root / "missing.bin"
                ),
            )

            self.assertEqual(
                missing.returncode,
                2,
            )
            self.assertIn(
                "pilot-gate-isolation: cannot hash",
                missing.stderr,
            )

    def test_cli_json_field_outputs_scalar_values_and_lowercase_booleans(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "value.json"
            path.write_text(
                json.dumps(
                    {
                        "text": "hello",
                        "integer": 42,
                        "enabled": True,
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )

            expected = {
                "text": "hello",
                "integer": "42",
                "enabled": "true",
                "disabled": "false",
            }

            for field, stdout in (
                expected.items()
            ):
                with self.subTest(
                    field=field
                ):
                    result = self.run_cli(
                        "json-field",
                        str(path),
                        field,
                    )

                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr,
                    )
                    self.assertEqual(
                        result.stdout.strip(),
                        stdout,
                    )

    def test_cli_json_field_rejects_missing_field_non_object_root_and_unsupported_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            object_path = (
                root / "object.json"
            )
            object_path.write_text(
                json.dumps(
                    {
                        "list": [1, 2],
                        "dict": {
                            "nested": True,
                        },
                    }
                ),
                encoding="utf-8",
            )

            array_path = (
                root / "array.json"
            )
            array_path.write_text(
                "[1, 2, 3]\n",
                encoding="utf-8",
            )

            cases = (
                (
                    (
                        "json-field",
                        str(object_path),
                        "missing",
                    ),
                    (
                        "JSON field is unavailable: "
                        "missing"
                    ),
                ),
                (
                    (
                        "json-field",
                        str(array_path),
                        "anything",
                    ),
                    "JSON root is not object",
                ),
                (
                    (
                        "json-field",
                        str(object_path),
                        "list",
                    ),
                    (
                        "JSON field has unsupported "
                        "type: list"
                    ),
                ),
                (
                    (
                        "json-field",
                        str(object_path),
                        "dict",
                    ),
                    (
                        "JSON field has unsupported "
                        "type: dict"
                    ),
                ),
            )

            for argv, message in cases:
                with self.subTest(
                    argv=argv
                ):
                    result = self.run_cli(
                        *argv
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )
                    self.assertIn(
                        (
                            "pilot-gate-isolation: "
                            + message
                        ),
                        result.stderr,
                    )

    def test_cli_write_state_writes_all_required_fields_and_atomically_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = (
                root
                / "nested"
                / "state.json"
            )

            values = {
                "run_id":
                    "20260911T000000Z-1234abcd",
                "run_root":
                    str(root / "run-root"),
                "source":
                    str(root / "source"),
                "source_head":
                    "a" * 40,
                "live_skill_dir":
                    str(root / "live"),
                "artifact_dir":
                    str(root / "artifacts"),
                "gate_team":
                    "agmsg-g4gate-test",
                "gate_repo":
                    str(root / "run-root" / "repo"),
                "gate_home":
                    str(root / "run-root" / "home"),
                "xdg_config":
                    str(root / "run-root" / "xdg" / "config"),
                "xdg_cache":
                    str(root / "run-root" / "xdg" / "cache"),
                "xdg_data":
                    str(root / "run-root" / "xdg" / "data"),
                "xdg_state":
                    str(root / "run-root" / "xdg" / "state"),
                "claude_config":
                    str(root / "run-root" / "claude"),
            }

            output.parent.mkdir(
                parents=True
            )
            output.write_text(
                '{"stale": true}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "write-state",
                "--output",
                str(output),
                "--run-id",
                values["run_id"],
                "--run-root",
                values["run_root"],
                "--source",
                values["source"],
                "--live-skill-dir",
                values["live_skill_dir"],
                "--artifact-dir",
                values["artifact_dir"],
                "--gate-team",
                values["gate_team"],
                "--gate-repo",
                values["gate_repo"],
                "--gate-home",
                values["gate_home"],
                "--xdg-config",
                values["xdg_config"],
                "--xdg-cache",
                values["xdg_cache"],
                "--xdg-data",
                values["xdg_data"],
                "--xdg-state",
                values["xdg_state"],
                "--claude-config",
                values["claude_config"],
                "--source-head",
                values["source_head"],
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )
            self.assertEqual(
                result.stdout,
                "",
            )

            state = json.loads(
                output.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                state["schemaVersion"],
                1,
            )
            self.assertEqual(
                state["part"],
                1,
            )
            self.assertEqual(
                state["runId"],
                values["run_id"],
            )
            self.assertEqual(
                state["runRoot"],
                values["run_root"],
            )
            self.assertEqual(
                state["source"],
                values["source"],
            )
            self.assertEqual(
                state["sourceHead"],
                values["source_head"],
            )
            self.assertEqual(
                state["liveSkillDir"],
                values["live_skill_dir"],
            )
            self.assertEqual(
                state["artifactDir"],
                values["artifact_dir"],
            )
            self.assertEqual(
                state["gateTeam"],
                values["gate_team"],
            )
            self.assertEqual(
                state["gateRepo"],
                values["gate_repo"],
            )
            self.assertEqual(
                state["gateHome"],
                values["gate_home"],
            )
            self.assertEqual(
                state["xdg"],
                {
                    "config":
                        values["xdg_config"],
                    "cache":
                        values["xdg_cache"],
                    "data":
                        values["xdg_data"],
                    "state":
                        values["xdg_state"],
                },
            )
            self.assertEqual(
                state["claudeConfigDir"],
                values["claude_config"],
            )
            self.assertRegex(
                state["createdAt"],
                UTC_RE,
            )
            self.assertNotIn(
                "stale",
                state,
            )

            raw = output.read_text(
                encoding="utf-8"
            )
            self.assertTrue(
                raw.endswith("\n")
            )
            self.assertFalse(
                raw.endswith("\n\n")
            )



class PilotGateIsolationPreflightTests(unittest.TestCase):
    def git_init(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(path), "init", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def git_track(self, repo: Path, relative: str, content: str) -> Path:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            ["git", "-C", str(repo), "add", relative],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return path

    def make_preflight_fixture(self, root: Path) -> dict[str, object]:
        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        live_repo = root / "live"
        source = root / "source"
        artifact_dir = root / "artifacts"
        gate_home = run_root / "home"
        xdg_config = run_root / "xdg" / "config"
        xdg_cache = run_root / "xdg" / "cache"
        xdg_data = run_root / "xdg" / "data"
        xdg_state = run_root / "xdg" / "state"
        claude_config = run_root / "claude"
        claude_bin = root / "native-bin" / "claude"
        output = artifact_dir / "preflight.json"
        gate_team = "agmsg-g4gate-round-b"
        pilot_agent = "agmsg_pm_pilot_claude"
        pilot_type = "claude-code"

        self.git_init(gate_repo)
        self.git_track(gate_repo, "tracked.txt", "gate-only\n")

        live_repo.mkdir(parents=True)
        source.mkdir(parents=True)
        artifact_dir.mkdir(parents=True)

        for directory in (
            gate_home,
            xdg_config,
            xdg_cache,
            xdg_data,
            xdg_state,
            claude_config,
        ):
            directory.mkdir(parents=True)

        claude_bin.parent.mkdir(parents=True)
        claude_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        claude_bin.chmod(0o755)

        team_config = gate_repo / "teams" / gate_team / "config.json"
        team_config.parent.mkdir(parents=True)
        ISOLATION.write_json(
            team_config,
            {
                "name": gate_team,
                "agents": {
                    pilot_agent: {
                        "registrations": [
                            {
                                "type": pilot_type,
                                "project": str(gate_repo),
                            }
                        ]
                    }
                },
            },
        )

        argv = [
            "preflight",
            "--output", str(output),
            "--run-id", "round-b-run",
            "--run-root", str(run_root),
            "--source", str(source),
            "--live-repo", str(live_repo),
            "--gate-repo", str(gate_repo),
            "--artifact-dir", str(artifact_dir),
            "--gate-team", gate_team,
            "--pilot-agent", pilot_agent,
            "--pilot-type", pilot_type,
            "--gate-home", str(gate_home),
            "--xdg-config", str(xdg_config),
            "--xdg-cache", str(xdg_cache),
            "--xdg-data", str(xdg_data),
            "--xdg-state", str(xdg_state),
            "--claude-config", str(claude_config),
            "--claude-bin", str(claude_bin),
        ]

        return {
            "run_root": run_root,
            "gate_repo": gate_repo,
            "live_repo": live_repo,
            "source": source,
            "artifact_dir": artifact_dir,
            "gate_home": gate_home,
            "xdg_config": xdg_config,
            "xdg_cache": xdg_cache,
            "xdg_data": xdg_data,
            "xdg_state": xdg_state,
            "claude_config": claude_config,
            "claude_bin": claude_bin,
            "output": output,
            "gate_team": gate_team,
            "pilot_agent": pilot_agent,
            "pilot_type": pilot_type,
            "team_config": team_config,
            "argv": argv,
        }

    def without_github_credentials(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in ISOLATION.GITHUB_CREDENTIAL_ENV_KEYS:
            env.pop(key, None)
        return env

    def run_preflight_cli(
        self,
        fixture: dict[str, object],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), *fixture["argv"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )

    def parsed_preflight_args(self, fixture: dict[str, object]):
        return ISOLATION.build_parser().parse_args(fixture["argv"])

    def test_git_remote_state_reports_empty_remote_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            self.git_init(repo)

            passed, detail = ISOLATION.git_remote_state(str(repo))

            self.assertTrue(passed)
            self.assertEqual(detail["remoteNames"], [])
            self.assertEqual(detail["remoteConfigEntries"], [])

    def test_git_remote_state_reports_configured_remote(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            self.git_init(repo)

            result = subprocess.run(
                [
                    "git", "-C", str(repo),
                    "remote", "add", "origin",
                    "https://example.invalid/agmsg.git",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            passed, detail = ISOLATION.git_remote_state(str(repo))

            self.assertFalse(passed)
            self.assertEqual(detail["remoteNames"], ["origin"])
            self.assertTrue(detail["remoteConfigEntries"])
            self.assertTrue(
                any(
                    line.startswith("remote.origin.")
                    for line in detail["remoteConfigEntries"]
                )
            )

    def test_git_remote_state_returns_unknown_when_git_remote_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            not_repo = Path(temp) / "not-repo"
            not_repo.mkdir()

            passed, detail = ISOLATION.git_remote_state(str(not_repo))

            self.assertIsNone(passed)
            self.assertIn("error", detail)
            self.assertTrue(detail["error"])

    def test_tracked_hardlink_overlap_detects_shared_inode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / "gate"
            live = root / "live"
            self.git_init(gate)
            live.mkdir()

            gate_file = self.git_track(gate, "same.txt", "shared\n")
            live_file = live / "same.txt"
            os.link(gate_file, live_file)

            passed, overlaps = ISOLATION.tracked_hardlink_overlap(
                str(gate),
                str(live),
            )

            self.assertFalse(passed)
            self.assertEqual(len(overlaps), 1)
            self.assertEqual(overlaps[0]["path"], "same.txt")
            self.assertEqual(overlaps[0]["device"], gate_file.stat().st_dev)
            self.assertEqual(overlaps[0]["inode"], gate_file.stat().st_ino)

    def test_tracked_hardlink_overlap_accepts_distinct_same_name_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / "gate"
            live = root / "live"
            self.git_init(gate)
            live.mkdir()

            self.git_track(gate, "same.txt", "gate\n")
            (live / "same.txt").write_text("live\n", encoding="utf-8")

            passed, overlaps = ISOLATION.tracked_hardlink_overlap(
                str(gate),
                str(live),
            )

            self.assertTrue(passed)
            self.assertEqual(overlaps, [])

    def test_tracked_hardlink_overlap_accepts_repository_with_no_tracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / "gate"
            live = root / "live"
            self.git_init(gate)
            live.mkdir()

            passed, overlaps = ISOLATION.tracked_hardlink_overlap(
                str(gate),
                str(live),
            )

            self.assertTrue(passed)
            self.assertEqual(overlaps, [])

    def test_tracked_hardlink_overlap_returns_unknown_when_git_listing_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = root / "not-repo"
            live = root / "live"
            gate.mkdir()
            live.mkdir()

            passed, detail = ISOLATION.tracked_hardlink_overlap(
                str(gate),
                str(live),
            )

            self.assertIsNone(passed)
            self.assertEqual(len(detail), 1)
            self.assertIn("error", detail[0])
            self.assertTrue(detail[0]["error"])

    def test_credential_files_returns_only_existing_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            xdg = root / "xdg"
            home.mkdir()
            xdg.mkdir()

            self.assertEqual(
                ISOLATION.credential_files(str(home), str(xdg)),
                [],
            )

            home_hosts = home / ".config" / "gh" / "hosts.yml"
            netrc = home / ".netrc"
            home_hosts.parent.mkdir(parents=True)
            home_hosts.write_text("github.com:\n", encoding="utf-8")
            netrc.write_text("machine example.invalid\n", encoding="utf-8")

            self.assertEqual(
                set(ISOLATION.credential_files(str(home), str(xdg))),
                {
                    str(home_hosts.resolve()),
                    str(netrc.resolve()),
                },
            )

            xdg_hosts = xdg / "gh" / "hosts.yml"
            git_credentials = home / ".git-credentials"
            xdg_hosts.parent.mkdir(parents=True)
            xdg_hosts.write_text("github.com:\n", encoding="utf-8")
            git_credentials.write_text(
                "https://example.invalid\n",
                encoding="utf-8",
            )

            self.assertEqual(
                set(ISOLATION.credential_files(str(home), str(xdg))),
                {
                    str(home_hosts.resolve()),
                    str(xdg_hosts.resolve()),
                    str(git_credentials.resolve()),
                    str(netrc.resolve()),
                },
            )

    def test_validate_gate_roster_distinguishes_missing_malformed_and_absent_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            team = "gate-team"
            agent = "pilot"
            pilot_type = "claude-code"
            config = repo / "teams" / team / "config.json"

            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertFalse(passed)
            self.assertEqual(detail["error"], "gate team config missing")

            config.parent.mkdir(parents=True)
            config.write_text("{not-json\n", encoding="utf-8")
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertIsNone(passed)
            self.assertIn("cannot parse gate team config", detail["error"])

            ISOLATION.write_json(config, {"agents": []})
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertIsNone(passed)
            self.assertEqual(
                detail["error"],
                "team config agents is not an object",
            )

            ISOLATION.write_json(
                config,
                {
                    "agents": {
                        agent: {
                            "registrations": {},
                        }
                    }
                },
            )
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertIsNone(passed)
            self.assertEqual(
                detail["error"],
                "registrations is not an array",
            )

            ISOLATION.write_json(config, {"agents": {}})
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertFalse(passed)
            self.assertEqual(detail["error"], "pilot agent absent")

    def test_validate_gate_roster_requires_exactly_one_matching_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            other_project = root / "other"
            other_project.mkdir()
            team = "gate-team"
            agent = "pilot"
            pilot_type = "claude-code"
            config = repo / "teams" / team / "config.json"
            config.parent.mkdir(parents=True)

            def write_registrations(registrations: list[object]) -> None:
                ISOLATION.write_json(
                    config,
                    {
                        "agents": {
                            agent: {
                                "registrations": registrations,
                            }
                        }
                    },
                )

            write_registrations(
                [
                    {
                        "type": pilot_type,
                        "project": str(other_project),
                    }
                ]
            )
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertFalse(passed)
            self.assertEqual(detail["matchingRegistrations"], 0)

            one = {
                "type": pilot_type,
                "project": str(repo),
            }
            write_registrations([one])
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertTrue(passed)
            self.assertEqual(detail["matchingRegistrations"], 1)

            write_registrations(
                [
                    one,
                    dict(one),
                ]
            )
            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )
            self.assertFalse(passed)
            self.assertEqual(detail["matchingRegistrations"], 2)

    def test_validate_gate_roster_skips_malformed_and_unresolvable_registration_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            team = "gate-team"
            agent = "pilot"
            pilot_type = "claude-code"
            config = repo / "teams" / team / "config.json"
            config.parent.mkdir(parents=True)

            ISOLATION.write_json(
                config,
                {
                    "agents": {
                        agent: {
                            "registrations": [
                                "not-an-object",
                                {
                                    "type": pilot_type,
                                    "project": 123,
                                },
                                {
                                    "type": pilot_type,
                                    "project": str(
                                        root / "missing-project"
                                    ),
                                },
                                {
                                    "type": "other-type",
                                    "project": str(repo),
                                },
                                {
                                    "type": pilot_type,
                                    "project": str(repo),
                                },
                            ]
                        }
                    }
                },
            )

            passed, detail = ISOLATION.validate_gate_roster(
                str(repo), team, agent, pilot_type
            )

            self.assertTrue(passed)
            self.assertEqual(detail["matchingRegistrations"], 1)
            self.assertEqual(detail["totalRegistrations"], 5)

    def test_preflight_cli_passes_all_thirteen_checks_with_fake_unauthenticated_gh(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_preflight_fixture(Path(temp))
            fake_bin = Path(temp) / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\nexit 1\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            env = self.without_github_credentials()
            env["PATH"] = (
                f"{fake_bin}"
                f"{os.pathsep}"
                f"{env.get('PATH', '')}"
            )

            result = self.run_preflight_cli(
                fixture,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )
            record = json.loads(
                fixture["output"].read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                record["schemaVersion"],
                1,
            )
            self.assertEqual(
                record["runId"],
                "round-b-run",
            )
            self.assertRegex(
                record["observedAt"],
                UTC_RE,
            )
            self.assertTrue(
                record["safe"]
            )
            self.assertEqual(
                record["verdict"],
                "pass",
            )
            self.assertEqual(
                len(record["checks"]),
                13,
            )
            self.assertEqual(
                [
                    check["number"]
                    for check
                    in record["checks"]
                ],
                [
                    f"P3.{number}"
                    for number in range(1, 14)
                ],
            )
            self.assertTrue(
                all(
                    check["verdict"] == "pass"
                    for check
                    in record["checks"]
                )
            )
            p310 = next(
                check
                for check
                in record["checks"]
                if check["number"]
                == "P3.10"
            )
            self.assertEqual(
                p310["detail"][
                    "authStatusExit"
                ],
                1,
            )

    def test_preflight_internal_passes_when_gh_executable_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_preflight_fixture(
                Path(temp)
            )
            args = self.parsed_preflight_args(
                fixture
            )

            environment = (
                self.without_github_credentials()
            )

            with mock.patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                with mock.patch.object(
                    ISOLATION.shutil,
                    "which",
                    return_value=None,
                ):
                    status = (
                        ISOLATION.command_preflight(
                            args
                        )
                    )

            self.assertEqual(
                status,
                0,
            )
            record = ISOLATION.load_json(
                fixture["output"]
            )
            p310 = next(
                check
                for check
                in record["checks"]
                if check["number"]
                == "P3.10"
            )
            self.assertEqual(
                p310["verdict"],
                "pass",
            )
            self.assertIsNone(
                p310["detail"][
                    "ghExecutable"
                ]
            )
            self.assertEqual(
                p310["detail"]["reason"],
                "gh executable absent",
            )

    def test_preflight_remote_failure_has_fail_verdict_but_barrier_exit_two(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_preflight_fixture(Path(temp))

            add_remote = subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture["gate_repo"]),
                    "remote",
                    "add",
                    "origin",
                    "https://example.invalid/agmsg.git",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(
                add_remote.returncode,
                0,
                add_remote.stderr,
            )

            env = self.without_github_credentials()
            fake_bin = Path(temp) / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\nexit 1\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            env["PATH"] = (
                f"{fake_bin}"
                f"{os.pathsep}"
                f"{env.get('PATH', '')}"
            )

            result = self.run_preflight_cli(
                fixture,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                2,
            )
            record = ISOLATION.load_json(
                fixture["output"]
            )
            self.assertFalse(
                record["safe"]
            )
            self.assertEqual(
                record["verdict"],
                "fail",
            )

            p33 = next(
                check
                for check
                in record["checks"]
                if check["number"]
                == "P3.3"
            )
            self.assertEqual(
                p33["verdict"],
                "fail",
            )

    def test_preflight_missing_roster_is_fail_and_barrier_exit_two(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_preflight_fixture(Path(temp))

            ISOLATION.write_json(
                fixture["team_config"],
                {
                    "agents": {},
                },
            )

            env = self.without_github_credentials()
            fake_bin = Path(temp) / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\nexit 1\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            env["PATH"] = (
                f"{fake_bin}"
                f"{os.pathsep}"
                f"{env.get('PATH', '')}"
            )

            result = self.run_preflight_cli(
                fixture,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                2,
            )
            record = ISOLATION.load_json(
                fixture["output"]
            )
            self.assertEqual(
                record["verdict"],
                "fail",
            )
            self.assertFalse(
                record["safe"]
            )

            p37 = next(
                check
                for check
                in record["checks"]
                if check["number"]
                == "P3.7"
            )
            self.assertEqual(
                p37["verdict"],
                "fail",
            )
            self.assertEqual(
                p37["detail"]["error"],
                "pilot agent absent",
            )

    def test_preflight_canonicalization_failure_writes_unknown_with_no_checks_and_exits_two(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = self.make_preflight_fixture(
                root
            )
            missing_run_root = (
                root
                / "definitely-missing-run-root"
            )

            argv = list(
                fixture["argv"]
            )
            index = argv.index(
                "--run-root"
            )
            argv[index + 1] = str(
                missing_run_root
            )
            fixture["argv"] = argv

            env = self.without_github_credentials()
            result = self.run_preflight_cli(
                fixture,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                2,
            )
            record = ISOLATION.load_json(
                fixture["output"]
            )
            self.assertEqual(
                record["schemaVersion"],
                1,
            )
            self.assertEqual(
                record["runId"],
                "round-b-run",
            )
            self.assertFalse(
                record["safe"]
            )
            self.assertEqual(
                record["verdict"],
                "unknown",
            )
            self.assertEqual(
                record["checks"],
                [],
            )
            self.assertIn(
                "canonicalization unavailable",
                record["reason"],
            )


class PilotGateIsolationF2ProofTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )

    def git_init(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(path), "init", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def clean_environment(self) -> dict[str, str]:
        env = os.environ.copy()

        for key in ISOLATION.GITHUB_CREDENTIAL_ENV_KEYS:
            env.pop(key, None)

        return env

    def make_fixture(
        self,
        root: Path,
        *,
        make_probe: bool = True,
    ) -> dict[str, object]:
        # tempfile.TemporaryDirectory() can return a path with a symlinked
        # ancestor (e.g. macOS /var -> /private/var). command_f2_proof
        # canonicalizes gate_repo but not the raw --probe-target argument
        # before calling no_symlink_components(), so a non-canonical root
        # here would make relative_to() fail and every symlink-sensitive
        # assertion spuriously report unknown instead of the intended
        # pass/fail. Resolve once here so every derived path is consistent.
        root = root.resolve()
        run_root = root / "run-root"
        gate_repo = run_root / "repo"
        live_repo = root / "live-repo"
        gate_home = run_root / "home"
        xdg_config = run_root / "xdg" / "config"
        artifact_dir = root / "artifacts"

        gate_repo.mkdir(parents=True)
        live_repo.mkdir(parents=True)
        gate_home.mkdir(parents=True)
        xdg_config.mkdir(parents=True)
        artifact_dir.mkdir(parents=True)

        self.git_init(gate_repo)

        target = (
            gate_repo
            / "probe-target"
            / "marker"
        )
        target.parent.mkdir(
            parents=True
        )

        program = (
            gate_repo
            / "probe-program"
            / "f2-probe.py"
        )
        manifest = (
            artifact_dir
            / "probe-manifest.json"
        )
        output = (
            artifact_dir
            / "f2-proof.json"
        )
        run_id = "round-c-run"

        fixture: dict[str, object] = {
            "run_root": run_root,
            "gate_repo": gate_repo,
            "live_repo": live_repo,
            "gate_home": gate_home,
            "xdg_config": xdg_config,
            "artifact_dir": artifact_dir,
            "target": target,
            "program": program,
            "manifest": manifest,
            "output": output,
            "run_id": run_id,
        }

        if make_probe:
            result = self.run_make_probe(
                fixture
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

        return fixture

    def run_make_probe(
        self,
        fixture: dict[str, object],
        *,
        target: Path | None = None,
        program: Path | None = None,
        manifest: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "make-f2-probe",
            "--run-id",
            str(fixture["run_id"]),
            "--gate-repo",
            str(fixture["gate_repo"]),
            "--target",
            str(
                target
                if target is not None
                else fixture["target"]
            ),
            "--program",
            str(
                program
                if program is not None
                else fixture["program"]
            ),
            "--manifest",
            str(
                manifest
                if manifest is not None
                else fixture["manifest"]
            ),
            env=self.clean_environment(),
        )

    def f2_argv(
        self,
        fixture: dict[str, object],
    ) -> list[str]:
        return [
            "f2-proof",
            "--output",
            str(fixture["output"]),
            "--run-id",
            str(fixture["run_id"]),
            "--run-root",
            str(fixture["run_root"]),
            "--gate-repo",
            str(fixture["gate_repo"]),
            "--live-repo",
            str(fixture["live_repo"]),
            "--gate-home",
            str(fixture["gate_home"]),
            "--xdg-config",
            str(fixture["xdg_config"]),
            "--probe-target",
            str(fixture["target"]),
            "--probe-program",
            str(fixture["program"]),
            "--probe-manifest",
            str(fixture["manifest"]),
        ]

    def run_f2_proof(
        self,
        fixture: dict[str, object],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            *self.f2_argv(fixture),
            env=(
                env
                if env is not None
                else self.clean_environment()
            ),
        )

    def parsed_f2_args(
        self,
        fixture: dict[str, object],
    ):
        return (
            ISOLATION
            .build_parser()
            .parse_args(
                self.f2_argv(fixture)
            )
        )

    def check(
        self,
        record: dict[str, object],
        number: int,
    ) -> dict[str, object]:
        return next(
            item
            for item
            in record["checks"]
            if item["number"]
            == number
        )

    def test_make_f2_probe_writes_fixed_executable_program_and_manifest(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                make_probe=False,
            )

            program = fixture["program"]

            self.assertFalse(
                program.parent.exists()
            )

            result = self.run_make_probe(
                fixture
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            self.assertTrue(
                program.is_file()
            )

            self.assertEqual(
                program.stat().st_mode
                & 0o777,
                0o700,
            )

            target_real = (
                ISOLATION
                .canonical_nonexistent(
                    str(
                        fixture["target"]
                    )
                )
            )

            program_real = (
                ISOLATION.canonical(
                    program
                )
            )

            python_real = (
                ISOLATION.canonical(
                    sys.executable
                )
            )

            expected_marker = (
                "agmsg-g4-gate-f2:"
                f"{fixture['run_id']}\n"
            )

            manifest = (
                ISOLATION.load_json(
                    fixture["manifest"]
                )
            )

            self.assertEqual(
                manifest["schemaVersion"],
                1,
            )

            self.assertEqual(
                manifest["runId"],
                fixture["run_id"],
            )

            self.assertEqual(
                manifest["probeTarget"],
                target_real,
            )

            self.assertEqual(
                manifest["probeProgram"],
                program_real,
            )

            self.assertEqual(
                manifest[
                    "probeProgramSha256"
                ],
                ISOLATION.sha256_file(
                    program
                ),
            )

            self.assertEqual(
                manifest[
                    "allowedCommand"
                ]["argv"],
                [
                    python_real,
                    program_real,
                ],
            )

            self.assertEqual(
                manifest[
                    "allowedCommand"
                ]["bashCommand"],
                " ".join(
                    ISOLATION.shlex.quote(
                        value
                    )
                    for value
                    in [
                        python_real,
                        program_real,
                    ]
                ),
            )

            self.assertEqual(
                manifest[
                    "markerSha256"
                ],
                hashlib.sha256(
                    expected_marker.encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )

            source = (
                program.read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn(
                (
                    f"TARGET = "
                    f"{target_real!r}"
                ),
                source,
            )

            self.assertIn(
                (
                    f"MARKER = "
                    f"{expected_marker!r}"
                ),
                source,
            )

            self.assertNotIn(
                "sys.argv[",
                source,
            )

            self.assertNotIn(
                "argparse",
                source,
            )

    def test_make_f2_probe_rejects_target_outside_gate_repository(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root,
                make_probe=False,
            )

            outside = (
                root
                / "outside-target"
            )

            result = self.run_make_probe(
                fixture,
                target=outside,
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertIn(
                (
                    "refusing F2 probe "
                    "target outside "
                    "gate repository"
                ),
                result.stderr,
            )

    def test_make_f2_probe_rejects_preexisting_target(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp),
                make_probe=False,
            )

            fixture[
                "target"
            ].write_text(
                "existing\n",
                encoding="utf-8",
            )

            result = self.run_make_probe(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertIn(
                (
                    "refusing pre-existing "
                    "F2 probe target"
                ),
                result.stderr,
            )

    def test_make_f2_probe_rejects_program_parent_outside_gate_repository(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root,
                make_probe=False,
            )

            outside_program = (
                root
                / "outside-program"
                / "probe.py"
            )

            result = self.run_make_probe(
                fixture,
                program=outside_program,
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertIn(
                (
                    "F2 probe program "
                    "must be inside "
                    "gate repository"
                ),
                result.stderr,
            )

    def test_generated_probe_creates_exact_marker_and_second_run_is_refused_by_o_excl(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            first = subprocess.run(
                [
                    sys.executable,
                    str(
                        fixture[
                            "program"
                        ]
                    ),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(
                first.returncode,
                0,
                first.stderr,
            )

            self.assertEqual(
                fixture[
                    "target"
                ].read_text(
                    encoding="utf-8"
                ),
                (
                    "agmsg-g4-gate-f2:"
                    f"{fixture['run_id']}\n"
                ),
            )

            second = subprocess.run(
                [
                    sys.executable,
                    str(
                        fixture[
                            "program"
                        ]
                    ),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(
                second.returncode,
                1,
            )

            self.assertIn(
                "f2-probe: create failed:",
                second.stderr,
            )

            self.assertEqual(
                fixture[
                    "target"
                ].read_text(
                    encoding="utf-8"
                ),
                (
                    "agmsg-g4-gate-f2:"
                    f"{fixture['run_id']}\n"
                ),
            )

    def test_f2_proof_complete_fixture_passes_all_twelve_items_and_exits_zero(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["schemaVersion"],
                1,
            )

            self.assertEqual(
                record["runId"],
                fixture["run_id"],
            )

            self.assertRegex(
                record["observedAt"],
                UTC_RE,
            )

            self.assertEqual(
                record["gateRepo"],
                ISOLATION.canonical(
                    fixture["gate_repo"]
                ),
            )

            self.assertEqual(
                record["liveRepo"],
                ISOLATION.canonical(
                    fixture["live_repo"]
                ),
            )

            self.assertEqual(
                record["probeTarget"],
                (
                    ISOLATION
                    .canonical_nonexistent(
                        str(
                            fixture[
                                "target"
                            ]
                        )
                    )
                ),
            )

            self.assertTrue(
                record["safe"]
            )

            self.assertEqual(
                record["verdict"],
                "pass",
            )

            self.assertEqual(
                len(record["checks"]),
                12,
            )

            self.assertEqual(
                [
                    item["number"]
                    for item
                    in record["checks"]
                ],
                list(
                    range(1, 13)
                ),
            )

            self.assertTrue(
                all(
                    item["verdict"]
                    == "pass"
                    for item
                    in record["checks"]
                )
            )

    def test_f2_proof_item1_unknown_makes_items3_and4_unknown_and_barrier_exits_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            args = self.parsed_f2_args(
                fixture
            )

            with mock.patch.dict(
                os.environ,
                self.clean_environment(),
                clear=True,
            ):
                with mock.patch.object(
                    ISOLATION,
                    "canonical_nonexistent",
                    side_effect=(
                        FileNotFoundError(
                            (
                                "target identity "
                                "unavailable"
                            )
                        )
                    ),
                ):
                    status = (
                        ISOLATION
                        .command_f2_proof(
                            args
                        )
                    )

            self.assertEqual(
                status,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertFalse(
                record["safe"]
            )

            self.assertEqual(
                self.check(
                    record,
                    1,
                )["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    3,
                )["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    4,
                )["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    3,
                )["detail"]["reason"],
                (
                    "target "
                    "canonicalization "
                    "unavailable"
                ),
            )

            self.assertEqual(
                self.check(
                    record,
                    4,
                )["detail"]["reason"],
                (
                    "target "
                    "canonicalization "
                    "unavailable"
                ),
            )

    def test_f2_proof_item2_is_unknown_when_deepest_existing_ancestor_cannot_be_proved(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            args = self.parsed_f2_args(
                fixture
            )

            canonical_target = (
                ISOLATION
                .canonical_nonexistent(
                    str(
                        fixture["target"]
                    )
                )
            )

            with mock.patch.dict(
                os.environ,
                self.clean_environment(),
                clear=True,
            ):
                with mock.patch.object(
                    ISOLATION,
                    "canonical_nonexistent",
                    return_value=(
                        canonical_target
                    ),
                ):
                    with mock.patch.object(
                        ISOLATION,
                        (
                            "longest_existing_"
                            "ancestor"
                        ),
                        side_effect=(
                            FileNotFoundError(
                                (
                                    "ancestor "
                                    "unavailable"
                                )
                            )
                        ),
                    ):
                        status = (
                            ISOLATION
                            .command_f2_proof(
                                args
                            )
                        )

            self.assertEqual(
                status,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    1,
                )["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    record,
                    2,
                )["verdict"],
                "unknown",
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

    def test_f2_proof_item3_fails_for_target_outside_gate_repository(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root
            )

            outside = (
                root
                / "outside"
                / "marker"
            )

            outside.parent.mkdir()

            fixture[
                "target"
            ] = outside

            manifest = (
                ISOLATION.load_json(
                    fixture["manifest"]
                )
            )

            manifest[
                "probeTarget"
            ] = (
                ISOLATION
                .canonical_nonexistent(
                    str(outside)
                )
            )

            ISOLATION.write_json(
                fixture["manifest"],
                manifest,
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    3,
                )["verdict"],
                "fail",
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertFalse(
                record["safe"]
            )

    def test_f2_proof_item4_fails_when_target_is_inside_live_repository(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fixture[
                "live_repo"
            ] = fixture[
                "gate_repo"
            ]

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    3,
                )["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    record,
                    4,
                )["verdict"],
                "fail",
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

    def test_f2_proof_items5_and6_detect_repository_nesting(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root
            )

            fixture[
                "live_repo"
            ] = fixture[
                "run_root"
            ]

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    5,
                )["verdict"],
                "fail",
            )

        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            nested_live = (
                fixture[
                    "gate_repo"
                ]
                / "nested-live"
            )

            nested_live.mkdir()

            fixture[
                "live_repo"
            ] = nested_live

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    6,
                )["verdict"],
                "fail",
            )

    def test_f2_proof_item7_fails_for_symlink_component_in_target_parent(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root,
                make_probe=False,
            )

            real_parent = (
                fixture[
                    "gate_repo"
                ]
                / "real-parent"
            )

            real_parent.mkdir()

            linked_parent = (
                fixture[
                    "gate_repo"
                ]
                / "linked-parent"
            )

            try:
                linked_parent.symlink_to(
                    real_parent,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(
                    (
                        "symlink "
                        f"unavailable: {exc}"
                    )
                )

            fixture[
                "target"
            ] = (
                linked_parent
                / "marker"
            )

            result = self.run_make_probe(
                fixture
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item7 = self.check(
                record,
                7,
            )

            self.assertEqual(
                item7["verdict"],
                "fail",
            )

            self.assertIn(
                str(
                    linked_parent.absolute()
                ),
                item7[
                    "detail"
                ]["problems"],
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

    def test_f2_proof_item8_fails_for_preexisting_target_and_records_mode_and_nlink(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            fixture[
                "target"
            ].write_text(
                "already here\n",
                encoding="utf-8",
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item8 = self.check(
                record,
                8,
            )

            self.assertEqual(
                item8["verdict"],
                "fail",
            )

            self.assertTrue(
                item8[
                    "detail"
                ]["lexists"]
            )

            self.assertIn(
                "mode",
                item8["detail"],
            )

            self.assertIn(
                "nlink",
                item8["detail"],
            )

            self.assertGreaterEqual(
                item8[
                    "detail"
                ]["nlink"],
                1,
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

    def test_f2_proof_item9_remote_failure_is_fail_but_safety_barrier_exits_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            add_remote = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(
                            fixture[
                                "gate_repo"
                            ]
                        ),
                        "remote",
                        "add",
                        "origin",
                        (
                            "https://"
                            "example.invalid/"
                            "agmsg.git"
                        ),
                    ],
                    stdout=(
                        subprocess.PIPE
                    ),
                    stderr=(
                        subprocess.PIPE
                    ),
                    text=True,
                    check=False,
                )
            )

            self.assertEqual(
                add_remote.returncode,
                0,
                add_remote.stderr,
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    9,
                )["verdict"],
                "fail",
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertFalse(
                record["safe"]
            )

    def test_f2_proof_item10_fails_when_gate_credentials_exist(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            credentials = (
                fixture[
                    "gate_home"
                ]
                / ".git-credentials"
            )

            credentials.write_text(
                (
                    "https://"
                    "example.invalid\n"
                ),
                encoding="utf-8",
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item10 = self.check(
                record,
                10,
            )

            self.assertEqual(
                item10["verdict"],
                "fail",
            )

            self.assertIn(
                str(
                    credentials.resolve()
                ),
                item10[
                    "detail"
                ]["found"],
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

    def test_f2_proof_item11_fails_when_github_credential_environment_is_present(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            env = (
                self.clean_environment()
            )

            env["GH_TOKEN"] = (
                "not-a-real-secret-"
                "test-value"
            )

            result = self.run_f2_proof(
                fixture,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item11 = self.check(
                record,
                11,
            )

            self.assertEqual(
                item11["verdict"],
                "fail",
            )

            self.assertTrue(
                item11[
                    "detail"
                ]["GH_TOKEN"]
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

    def test_f2_proof_item12_malformed_manifest_forms_are_unknown_and_exit_two(
        self,
    ):
        mutations = (
            "invalid-json",
            "missing-allowed-command",
            "bad-argv",
        )

        for mutation in mutations:
            with self.subTest(
                mutation=mutation
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_fixture(
                            Path(temp)
                        )
                    )

                    if mutation == "invalid-json":
                        fixture[
                            "manifest"
                        ].write_text(
                            "{not-json\n",
                            encoding="utf-8",
                        )

                    elif (
                        mutation
                        == "missing-allowed-command"
                    ):
                        manifest = (
                            ISOLATION
                            .load_json(
                                fixture[
                                    "manifest"
                                ]
                            )
                        )

                        manifest.pop(
                            "allowedCommand"
                        )

                        ISOLATION.write_json(
                            fixture[
                                "manifest"
                            ],
                            manifest,
                        )

                    else:
                        manifest = (
                            ISOLATION
                            .load_json(
                                fixture[
                                    "manifest"
                                ]
                            )
                        )

                        manifest[
                            "allowedCommand"
                        ]["argv"] = [
                            "only-one-element"
                        ]

                        ISOLATION.write_json(
                            fixture[
                                "manifest"
                            ],
                            manifest,
                        )

                    result = (
                        self.run_f2_proof(
                            fixture
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    record = (
                        ISOLATION
                        .load_json(
                            fixture[
                                "output"
                            ]
                        )
                    )

                    item12 = (
                        self.check(
                            record,
                            12,
                        )
                    )

                    self.assertEqual(
                        item12[
                            "verdict"
                        ],
                        "unknown",
                    )

                    self.assertEqual(
                        record[
                            "verdict"
                        ],
                        "unknown",
                    )

                    self.assertFalse(
                        record["safe"]
                    )

                    self.assertIn(
                        "error",
                        item12["detail"],
                    )

    def test_f2_proof_item12_program_digest_mismatch_is_definite_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            manifest = (
                ISOLATION.load_json(
                    fixture["manifest"]
                )
            )

            manifest[
                "probeProgramSha256"
            ] = "0" * 64

            ISOLATION.write_json(
                fixture["manifest"],
                manifest,
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item12 = self.check(
                record,
                12,
            )

            self.assertEqual(
                item12["verdict"],
                "fail",
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertFalse(
                record["safe"]
            )

            self.assertNotEqual(
                item12[
                    "detail"
                ]["programSha256"],
                manifest[
                    "probeProgramSha256"
                ],
            )

    def test_f2_proof_base_canonicalization_failure_writes_unknown_without_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            fixture = self.make_fixture(
                root
            )

            fixture[
                "gate_repo"
            ] = (
                root
                / "missing-gate-repo"
            )

            result = self.run_f2_proof(
                fixture
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["schemaVersion"],
                1,
            )

            self.assertEqual(
                record["runId"],
                fixture["run_id"],
            )

            self.assertFalse(
                record["safe"]
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                record["checks"],
                [],
            )

            self.assertIn(
                (
                    "base canonicalization "
                    "unavailable"
                ),
                record["reason"],
            )


class PilotGateIsolationN1BindingTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def make_binding_fixture(
        self,
        root: Path,
        *,
        generation: str = "1",
        pid: str = "4242",
        session_id: str | None = None,
    ) -> dict[str, object]:
        # Keep lexical and canonical paths aligned on macOS
        # (/var -> /private/var).
        root = root.resolve()

        gate_repo = root / "gate-repo"
        project = root / "project"
        output = root / "validate-binding.json"

        team = "agmsg-g4gate-round-d1"
        agent = "agmsg_pm_pilot_claude"

        gate_repo.mkdir(parents=True)
        project.mkdir(parents=True)

        if session_id is None:
            session_id = (
                "123e4567-e89b-42d3-a456-426614174000"
            )

        binding = (
            gate_repo
            / "run"
            / "pilot"
            / f"{team}__{agent}"
            / "bindings"
            / f"{generation}.json"
        )

        binding.parent.mkdir(
            parents=True
        )

        value = {
            "schemaVersion": 1,
            "team": team,
            "agent": agent,
            "project": str(project),
            "generation": generation,
            "sessionId": session_id,
            "pid": pid,
            "pidStart":
                "2026-09-11T00:00:00Z",
            "profileDigest":
                "profile-digest",
            "policyVersion":
                "policy-v1",
            "guardDigest":
                "guard-digest",
            "brokerDigest":
                "broker-digest",
            "providerCommit":
                "0123456789abcdef",
        }

        ISOLATION.write_json(
            binding,
            value,
        )

        return {
            "root": root,
            "gate_repo": gate_repo,
            "project": project,
            "output": output,
            "team": team,
            "agent": agent,
            "generation": generation,
            "pid": pid,
            "session_id": session_id,
            "binding": binding,
            "value": value,
        }

    def validate_binding_argv(
        self,
        fixture: dict[str, object],
        *,
        expected_session: str | None = None,
        binding: Path | None = None,
        output: Path | None = None,
        project: Path | None = None,
        generation: str | None = None,
        process_pid: str | None = None,
    ) -> list[str]:
        argv = [
            "validate-binding",
            "--binding",
            str(
                binding
                if binding is not None
                else fixture["binding"]
            ),
            "--output",
            str(
                output
                if output is not None
                else fixture["output"]
            ),
            "--team",
            str(fixture["team"]),
            "--agent",
            str(fixture["agent"]),
            "--project",
            str(
                project
                if project is not None
                else fixture["project"]
            ),
            "--generation",
            str(
                generation
                if generation is not None
                else fixture["generation"]
            ),
            "--process-pid",
            str(
                process_pid
                if process_pid is not None
                else fixture["pid"]
            ),
        ]

        if expected_session is not None:
            argv.extend(
                [
                    "--expected-session",
                    expected_session,
                ]
            )

        return argv

    def check(
        self,
        record: dict[str, object],
        number: str,
    ) -> dict[str, object]:
        return next(
            item
            for item
            in record["checks"]
            if item["number"] == number
        )

    def test_binding_generation_text_and_binding_pid_text_share_same_contract(
        self,
    ):
        cases = (
            (True, None),
            (False, None),
            (1, "1"),
            (42, "42"),
            (0, None),
            (-1, None),
            ("1", "1"),
            ("42", "42"),
            ("0", None),
            ("01", None),
            ("", None),
            ("abc", None),
            (None, None),
            ([], None),
            ({}, None),
        )

        for value, expected in cases:
            with self.subTest(
                value=value
            ):
                self.assertEqual(
                    ISOLATION
                    .binding_generation_text(
                        value
                    ),
                    expected,
                )

                self.assertEqual(
                    ISOLATION
                    .binding_pid_text(
                        value
                    ),
                    expected,
                )

    def test_find_binding_outputs_existing_regular_candidate_and_exits_zero(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            result = self.run_cli(
                "find-binding",
                "--gate-repo",
                str(
                    fixture["gate_repo"]
                ),
                "--team",
                str(
                    fixture["team"]
                ),
                "--agent",
                str(
                    fixture["agent"]
                ),
                "--generation",
                str(
                    fixture["generation"]
                ),
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            self.assertEqual(
                result.stdout.strip(),
                str(
                    fixture["binding"]
                ),
            )

    def test_find_binding_returns_one_when_candidate_is_missing(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            fixture[
                "binding"
            ].unlink()

            result = self.run_cli(
                "find-binding",
                "--gate-repo",
                str(
                    fixture["gate_repo"]
                ),
                "--team",
                str(
                    fixture["team"]
                ),
                "--agent",
                str(
                    fixture["agent"]
                ),
                "--generation",
                str(
                    fixture["generation"]
                ),
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            self.assertEqual(
                result.stdout,
                "",
            )

    def test_find_binding_returns_one_when_candidate_is_symlink(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            binding = fixture["binding"]

            real_binding = (
                fixture["root"]
                / "real-binding.json"
            )

            real_binding.write_text(
                binding.read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )

            binding.unlink()

            try:
                binding.symlink_to(
                    real_binding
                )
            except OSError as exc:
                self.skipTest(
                    "symlink unavailable: "
                    f"{exc}"
                )

            result = self.run_cli(
                "find-binding",
                "--gate-repo",
                str(
                    fixture["gate_repo"]
                ),
                "--team",
                str(
                    fixture["team"]
                ),
                "--agent",
                str(
                    fixture["agent"]
                ),
                "--generation",
                str(
                    fixture["generation"]
                ),
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            self.assertEqual(
                result.stdout,
                "",
            )

    def test_find_binding_rejects_unsafe_team_or_agent_with_exit_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            cases = (
                (
                    "bad/team",
                    str(
                        fixture["agent"]
                    ),
                ),
                (
                    str(
                        fixture["team"]
                    ),
                    "bad/agent",
                ),
            )

            for team, agent in cases:
                with self.subTest(
                    team=team,
                    agent=agent,
                ):
                    result = (
                        self.run_cli(
                            "find-binding",
                            "--gate-repo",
                            str(
                                fixture[
                                    "gate_repo"
                                ]
                            ),
                            "--team",
                            team,
                            "--agent",
                            agent,
                            "--generation",
                            "1",
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    self.assertIn(
                        (
                            "pilot-gate-isolation: "
                            "cannot derive "
                            "binding path:"
                        ),
                        result.stderr,
                    )

    def test_find_binding_rejects_invalid_generation_with_exit_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            for generation in (
                "0",
                "abc",
            ):
                with self.subTest(
                    generation=generation
                ):
                    result = (
                        self.run_cli(
                            "find-binding",
                            "--gate-repo",
                            str(
                                fixture[
                                    "gate_repo"
                                ]
                            ),
                            "--team",
                            str(
                                fixture[
                                    "team"
                                ]
                            ),
                            "--agent",
                            str(
                                fixture[
                                    "agent"
                                ]
                            ),
                            "--generation",
                            generation,
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    self.assertIn(
                        (
                            "pilot-gate-isolation: "
                            "cannot derive binding "
                            "path: invalid generation"
                        ),
                        result.stderr,
                    )

    def test_validate_binding_complete_binding_passes_without_expected_session(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["schemaVersion"],
                1,
            )

            self.assertEqual(
                record["binding"],
                str(
                    fixture["binding"]
                ),
            )

            self.assertRegex(
                record["observedAt"],
                UTC_RE,
            )

            self.assertEqual(
                record["verdict"],
                "pass",
            )

            # 7 fixed checks +
            # 6 immutable identity fields.
            self.assertEqual(
                len(record["checks"]),
                13,
            )

            self.assertNotIn(
                (
                    "N1.binding."
                    "resume-session"
                ),
                [
                    item["number"]
                    for item
                    in record["checks"]
                ],
            )

            self.assertTrue(
                all(
                    item["verdict"]
                    == "pass"
                    for item
                    in record["checks"]
                )
            )

    def test_validate_binding_complete_binding_passes_with_expected_session(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture,
                    expected_session=str(
                        fixture[
                            "session_id"
                        ]
                    ),
                )
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "pass",
            )

            self.assertEqual(
                len(record["checks"]),
                14,
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.binding."
                        "resume-session"
                    ),
                )["verdict"],
                "pass",
            )

    def test_validate_binding_definite_mismatch_uses_normal_fail_exit_one(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            value = fixture["value"]
            value["team"] = (
                "wrong-team"
            )

            ISOLATION.write_json(
                fixture["binding"],
                value,
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.binding.team",
                )["verdict"],
                "fail",
            )

    def test_validate_binding_unprovable_generation_uses_normal_unknown_exit_two(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            value = fixture["value"]

            # bool must not be accepted as int.
            value["generation"] = True

            ISOLATION.write_json(
                fixture["binding"],
                value,
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.binding."
                        "generation"
                    ),
                )["verdict"],
                "unknown",
            )

    def test_validate_binding_project_canonicalization_failure_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            value = fixture["value"]

            value["project"] = str(
                fixture["root"]
                / "missing-project"
            )

            ISOLATION.write_json(
                fixture["binding"],
                value,
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            item = self.check(
                record,
                "N1.binding.project",
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                item["verdict"],
                "unknown",
            )

            self.assertIn(
                "error",
                item["detail"],
            )

    def test_validate_binding_pid_bool_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            value = fixture["value"]

            value["pid"] = True

            ISOLATION.write_json(
                fixture["binding"],
                value,
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.binding.pid",
                )["verdict"],
                "unknown",
            )

    def test_validate_binding_invalid_session_and_resume_session_are_failures(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            value = fixture["value"]
            value["sessionId"] = (
                "not-a-uuid"
            )

            ISOLATION.write_json(
                fixture["binding"],
                value,
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture,
                    expected_session=(
                        "123e4567-e89b-42d3-"
                        "a456-426614174000"
                    ),
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.binding.session",
                )["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.binding."
                        "resume-session"
                    ),
                )["verdict"],
                "fail",
            )

    def test_validate_binding_expected_session_mismatch_is_definite_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture,
                    expected_session=(
                        "aaaaaaaa-aaaa-4aaa-"
                        "8aaa-aaaaaaaaaaaa"
                    ),
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.binding.session",
                )["verdict"],
                "pass",
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.binding."
                        "resume-session"
                    ),
                )["verdict"],
                "fail",
            )

    def test_validate_binding_required_identity_fields_must_be_nonempty_strings(
        self,
    ):
        required = (
            "pidStart",
            "profileDigest",
            "policyVersion",
            "guardDigest",
            "brokerDigest",
            "providerCommit",
        )

        for field in required:
            with self.subTest(
                field=field
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_binding_fixture(
                            Path(temp)
                        )
                    )

                    value = fixture["value"]

                    value[field] = ""

                    ISOLATION.write_json(
                        fixture[
                            "binding"
                        ],
                        value,
                    )

                    result = (
                        self.run_cli(
                            *self
                            .validate_binding_argv(
                                fixture
                            )
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        1,
                    )

                    record = (
                        ISOLATION
                        .load_json(
                            fixture[
                                "output"
                            ]
                        )
                    )

                    self.assertEqual(
                        record["verdict"],
                        "fail",
                    )

                    self.assertEqual(
                        self.check(
                            record,
                            (
                                "N1.binding."
                                f"{field}"
                            ),
                        )["verdict"],
                        "fail",
                    )

    def test_validate_binding_missing_file_is_unknown_early_return(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            missing = (
                fixture["root"]
                / "missing-binding.json"
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture,
                    binding=missing,
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason":
                        "binding unavailable",
                    "checks": [],
                },
            )

    def test_validate_binding_symlink_is_fail_early_return(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            real_binding = (
                fixture["binding"]
            )

            symlink = (
                fixture["root"]
                / "binding-link.json"
            )

            try:
                symlink.symlink_to(
                    real_binding
                )
            except OSError as exc:
                self.skipTest(
                    "symlink unavailable: "
                    f"{exc}"
                )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture,
                    binding=symlink,
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record,
                {
                    "schemaVersion": 1,
                    "verdict": "fail",
                    "reason":
                        "binding is symlink",
                    "checks": [],
                },
            )

    def test_validate_binding_invalid_json_is_unknown_early_return(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            fixture[
                "binding"
            ].write_text(
                "{not-json\n",
                encoding="utf-8",
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["schemaVersion"],
                1,
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                record["checks"],
                [],
            )

            self.assertIn(
                "binding unreadable:",
                record["reason"],
            )

    def test_validate_binding_non_object_root_is_unknown_early_return(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_binding_fixture(
                    Path(temp)
                )
            )

            fixture[
                "binding"
            ].write_text(
                "[1, 2, 3]\n",
                encoding="utf-8",
            )

            result = self.run_cli(
                *self.validate_binding_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason":
                        (
                            "binding root is "
                            "not object"
                        ),
                    "checks": [],
                },
            )


class PilotGateIsolationN1ProcessAndTranscriptTests(unittest.TestCase):
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    OTHER_SESSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def run_cli(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def write_command(
        self,
        root: Path,
        text: str,
    ) -> Path:
        path = root / "command.txt"

        path.write_text(
            text,
            encoding="utf-8",
        )

        return path

    def make_settings(
        self,
        root: Path,
        name: str = "settings.json",
    ) -> Path:
        path = root / name

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            "{}\n",
            encoding="utf-8",
        )

        return path

    def validate_process_argv(
        self,
        command_file: Path,
        *,
        mode: str,
        session_id: str,
        settings: Path,
    ) -> list[str]:
        return [
            "validate-process-command",
            "--command-file",
            str(command_file),
            "--mode",
            mode,
            "--session-id",
            session_id,
            "--settings",
            str(settings),
        ]

    def write_transcript(
        self,
        path: Path,
        records: list[object],
    ) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            path,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as fh:
            for record in records:
                if isinstance(
                    record,
                    str,
                ):
                    fh.write(record)

                else:
                    fh.write(
                        json.dumps(
                            record
                        )
                    )

                fh.write("\n")

    def test_find_option_value_handles_present_missing_and_trailing_option(
        self,
    ):
        self.assertEqual(
            ISOLATION.find_option_value(
                [
                    "claude",
                    "--settings",
                    "/tmp/settings.json",
                    "--session-id",
                    self.SESSION_ID,
                ],
                "--settings",
            ),
            "/tmp/settings.json",
        )

        self.assertIsNone(
            ISOLATION.find_option_value(
                [
                    "claude",
                    "--session-id",
                    self.SESSION_ID,
                ],
                "--settings",
            )
        )

        self.assertIsNone(
            ISOLATION.find_option_value(
                [
                    "claude",
                    "--settings",
                ],
                "--settings",
            )
        )

    def test_validate_process_command_unreadable_empty_and_unparseable_are_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root
            )

            missing = (
                root
                / "missing-command.txt"
            )

            result = self.run_cli(
                *self.validate_process_argv(
                    missing,
                    mode="fresh",
                    session_id=self.SESSION_ID,
                    settings=settings,
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertIn(
                "process command unreadable:",
                result.stderr,
            )

            empty = self.write_command(
                root,
                "   \n\t",
            )

            result = self.run_cli(
                *self.validate_process_argv(
                    empty,
                    mode="fresh",
                    session_id=self.SESSION_ID,
                    settings=settings,
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            malformed = self.write_command(
                root,
                "claude --settings 'unterminated",
            )

            result = self.run_cli(
                *self.validate_process_argv(
                    malformed,
                    mode="fresh",
                    session_id=self.SESSION_ID,
                    settings=settings,
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

    def test_validate_process_command_defensively_rejects_empty_split_result(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root
            )

            command_file = (
                self.write_command(
                    root,
                    "syntactically-present",
                )
            )

            args = (
                ISOLATION
                .build_parser()
                .parse_args(
                    self.validate_process_argv(
                        command_file,
                        mode="fresh",
                        session_id=self.SESSION_ID,
                        settings=settings,
                    )
                )
            )

            with mock.patch.object(
                ISOLATION.shlex,
                "split",
                return_value=[],
            ):
                status = (
                    ISOLATION
                    .command_validate_process_command(
                        args
                    )
                )

            self.assertEqual(
                status,
                2,
            )

    def test_validate_process_command_fresh_accepts_matching_session_and_later_matching_settings(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root,
                "settings dir/settings.json",
            )

            missing_settings = (
                root
                / "missing"
                / "settings.json"
            )

            command = " ".join(
                [
                    "claude",
                    "--settings",
                    ISOLATION.shlex.quote(
                        str(
                            missing_settings
                        )
                    ),
                    "--settings",
                    ISOLATION.shlex.quote(
                        str(settings)
                    ),
                    "--session-id",
                    self.SESSION_ID,
                ]
            )

            command_file = (
                self.write_command(
                    root,
                    command,
                )
            )

            result = self.run_cli(
                *self.validate_process_argv(
                    command_file,
                    mode="fresh",
                    session_id=self.SESSION_ID,
                    settings=settings,
                )
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

    def test_validate_process_command_resume_accepts_matching_resume_and_settings(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root
            )

            command = " ".join(
                [
                    "claude",
                    "--settings",
                    ISOLATION.shlex.quote(
                        str(settings)
                    ),
                    "--resume",
                    self.SESSION_ID,
                ]
            )

            command_file = (
                self.write_command(
                    root,
                    command,
                )
            )

            result = self.run_cli(
                *self.validate_process_argv(
                    command_file,
                    mode="resume",
                    session_id=self.SESSION_ID,
                    settings=settings,
                )
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

    def test_validate_process_command_fresh_rejects_wrong_session_resume_mixture_and_settings_mismatch(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root
            )

            other_settings = (
                self.make_settings(
                    root,
                    "other-settings.json",
                )
            )

            cases = {
                "wrong-session": [
                    "claude",
                    "--settings",
                    str(settings),
                    "--session-id",
                    self.OTHER_SESSION_ID,
                ],
                "resume-mixed": [
                    "claude",
                    "--settings",
                    str(settings),
                    "--session-id",
                    self.SESSION_ID,
                    "--resume",
                    self.SESSION_ID,
                ],
                "settings-mismatch": [
                    "claude",
                    "--settings",
                    str(other_settings),
                    "--session-id",
                    self.SESSION_ID,
                ],
            }

            for name, argv in cases.items():
                with self.subTest(
                    name=name
                ):
                    command_file = (
                        self.write_command(
                            root,
                            " ".join(
                                ISOLATION
                                .shlex
                                .quote(word)
                                for word
                                in argv
                            ),
                        )
                    )

                    result = (
                        self.run_cli(
                            *self
                            .validate_process_argv(
                                command_file,
                                mode="fresh",
                                session_id=(
                                    self.SESSION_ID
                                ),
                                settings=settings,
                            )
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        1,
                        result.stderr,
                    )

    def test_validate_process_command_resume_rejects_wrong_session_fresh_mixture_and_settings_mismatch(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            settings = self.make_settings(
                root
            )

            other_settings = (
                self.make_settings(
                    root,
                    "other-settings.json",
                )
            )

            cases = {
                "wrong-resume-session": [
                    "claude",
                    "--settings",
                    str(settings),
                    "--resume",
                    self.OTHER_SESSION_ID,
                ],
                "fresh-mixed": [
                    "claude",
                    "--settings",
                    str(settings),
                    "--resume",
                    self.SESSION_ID,
                    "--session-id",
                    self.SESSION_ID,
                ],
                "settings-mismatch": [
                    "claude",
                    "--settings",
                    str(other_settings),
                    "--resume",
                    self.SESSION_ID,
                ],
            }

            for name, argv in cases.items():
                with self.subTest(
                    name=name
                ):
                    command_file = (
                        self.write_command(
                            root,
                            " ".join(
                                ISOLATION
                                .shlex
                                .quote(word)
                                for word
                                in argv
                            ),
                        )
                    )

                    result = (
                        self.run_cli(
                            *self
                            .validate_process_argv(
                                command_file,
                                mode="resume",
                                session_id=(
                                    self.SESSION_ID
                                ),
                                settings=settings,
                            )
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        1,
                        result.stderr,
                    )

    def test_json_tree_contains_session_supports_all_identity_key_variants_and_nested_structures(
        self,
    ):
        for key in (
            "sessionId",
            "session_id",
            "sessionID",
        ):
            with self.subTest(
                key=key
            ):
                self.assertTrue(
                    ISOLATION
                    .json_tree_contains_session(
                        {
                            key:
                                self.SESSION_ID,
                        },
                        self.SESSION_ID,
                    )
                )

        nested = {
            "outer": [
                {
                    "middle": {
                        "items": [
                            {
                                "session_id":
                                    self.SESSION_ID,
                            }
                        ]
                    }
                }
            ]
        }

        self.assertTrue(
            ISOLATION
            .json_tree_contains_session(
                nested,
                self.SESSION_ID,
            )
        )

        self.assertFalse(
            ISOLATION
            .json_tree_contains_session(
                {
                    "sessionId":
                        self.OTHER_SESSION_ID,
                    "nested": {
                        "unrelated":
                            self.SESSION_ID,
                    },
                },
                self.SESSION_ID,
            )
        )

        self.assertFalse(
            ISOLATION
            .json_tree_contains_session(
                (
                    "sessionId="
                    + self.SESSION_ID
                ),
                self.SESSION_ID,
            )
        )

        self.assertFalse(
            ISOLATION
            .json_tree_contains_session(
                42,
                self.SESSION_ID,
            )
        )

    def test_transcript_has_session_skips_blank_and_bad_json_and_finds_later_match(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            transcript = (
                root
                / "transcript.jsonl"
            )

            transcript.write_text(
                "\n"
                "{not-json}\n"
                + json.dumps(
                    {
                        "message": {
                            "sessionID":
                                self.SESSION_ID,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(
                ISOLATION
                .transcript_has_session(
                    transcript,
                    self.SESSION_ID,
                )
            )

    def test_transcript_has_session_returns_false_for_no_match_all_bad_missing_and_invalid_utf8(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            no_match = (
                root
                / "no-match.jsonl"
            )

            no_match.write_text(
                json.dumps(
                    {
                        "sessionId":
                            self.OTHER_SESSION_ID,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(
                ISOLATION
                .transcript_has_session(
                    no_match,
                    self.SESSION_ID,
                )
            )

            bad = (
                root
                / "bad.jsonl"
            )

            bad.write_text(
                "{bad}\n"
                "not-json\n"
                "\n",
                encoding="utf-8",
            )

            self.assertFalse(
                ISOLATION
                .transcript_has_session(
                    bad,
                    self.SESSION_ID,
                )
            )

            self.assertFalse(
                ISOLATION
                .transcript_has_session(
                    root
                    / "missing.jsonl",
                    self.SESSION_ID,
                )
            )

            invalid_utf8 = (
                root
                / "invalid-utf8.jsonl"
            )

            invalid_utf8.write_bytes(
                b'{"sessionId":"'
                + self.SESSION_ID.encode(
                    "ascii"
                )
                + b'"}\n'
                + b"\xff\xfe\n"
            )

            self.assertFalse(
                ISOLATION
                .transcript_has_session(
                    invalid_utf8,
                    self.SESSION_ID,
                )
            )

    def test_find_transcript_rejects_invalid_uuid_before_config_directory_check(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(
                    root
                    / "definitely-missing"
                ),
                "--session-id",
                "not-a-uuid",
            )

            # If directory validation ran first,
            # this would be exit 1 instead.
            self.assertEqual(
                result.returncode,
                2,
            )

    def test_find_transcript_returns_one_when_config_is_not_directory_or_no_match_exists(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            missing = (
                root
                / "missing"
            )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(missing),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            config = (
                root
                / "claude"
            )

            config.mkdir()

            self.write_transcript(
                config
                / "unrelated.jsonl",
                [
                    {
                        "sessionId":
                            self.OTHER_SESSION_ID,
                    }
                ],
            )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(config),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

    def test_find_transcript_finds_single_nested_match_even_when_filename_has_no_session_id(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            config = (
                root
                / "claude"
            )

            transcript = (
                config
                / "projects"
                / "project-a"
                / "conversation.jsonl"
            )

            self.write_transcript(
                transcript,
                [
                    {
                        "message": {
                            "metadata": {
                                "session_id":
                                    self.SESSION_ID,
                            }
                        }
                    }
                ],
            )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(config),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            self.assertEqual(
                result.stdout.strip(),
                str(transcript),
            )

    def test_find_transcript_filename_fast_candidate_still_requires_content_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            config = (
                root
                / "claude"
            )

            transcript = (
                config
                / (
                    self.SESSION_ID
                    + ".jsonl"
                )
            )

            self.write_transcript(
                transcript,
                [
                    {
                        "sessionId":
                            self.OTHER_SESSION_ID,
                    }
                ],
            )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(config),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

    def test_find_transcript_ignores_symlink_jsonl_candidate(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            config = (
                root
                / "claude"
            )

            config.mkdir()

            real = (
                root
                / "outside-real.jsonl"
            )

            self.write_transcript(
                real,
                [
                    {
                        "sessionId":
                            self.SESSION_ID,
                    }
                ],
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
                    "symlink unavailable: "
                    f"{exc}"
                )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(config),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

    def test_find_transcript_multiple_distinct_resolved_matches_are_unknown_and_listed(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            config = (
                root
                / "claude"
            )

            config.mkdir()

            first = (
                config
                / "first.jsonl"
            )

            second = (
                config
                / "second.jsonl"
            )

            self.write_transcript(
                first,
                [
                    {
                        "sessionId":
                            self.SESSION_ID,
                    }
                ],
            )

            # Hard links share the inode but resolve() still yields two
            # different pathnames. command_find_transcript de-duplicates by
            # resolved pathname, not inode identity, so both remain matches.
            try:
                os.link(
                    first,
                    second,
                )
            except OSError as exc:
                self.skipTest(
                    "hard link unavailable: "
                    f"{exc}"
                )

            result = self.run_cli(
                "find-transcript",
                "--claude-config",
                str(config),
                "--session-id",
                self.SESSION_ID,
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertEqual(
                result.stdout,
                "",
            )

            self.assertIn(
                (
                    "multiple native "
                    "transcripts found "
                    "for session "
                    + self.SESSION_ID
                ),
                result.stderr,
            )

            self.assertIn(
                str(first),
                result.stderr,
            )

            self.assertIn(
                str(second),
                result.stderr,
            )


class PilotGateIsolationN1StateTests(unittest.TestCase):
    SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
    OTHER_SESSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def run_cli(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def make_state_fixture(
        self,
        root: Path,
        *,
        generation: int = 2,
        explicit_state_file: bool = False,
    ) -> dict[str, object]:
        # Keep lexical and canonical paths aligned on macOS
        # (/var -> /private/var).
        root = root.resolve()

        seat = (
            root
            / "run"
            / "pilot"
            / "gate-team__pilot-agent"
        )

        binding = (
            seat
            / "bindings"
            / f"{generation}.json"
        )

        binding.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if explicit_state_file:
            state = (
                root
                / "explicit-state"
                / "state.json"
            )
        else:
            state = (
                seat
                / "state.json"
            )

        state.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output = (
            root
            / "artifacts"
            / "validate-state.json"
        )

        binding_value: dict[str, object] = {
            "generation": generation,
            "sessionId": self.SESSION_ID,
        }

        if explicit_state_file:
            binding_value["stateFile"] = str(
                state
            )

        state_value: dict[str, object] = {
            "latestGeneration": generation,
            "latestBinding": str(binding),
        }

        ISOLATION.write_json(
            binding,
            binding_value,
        )

        ISOLATION.write_json(
            state,
            state_value,
        )

        return {
            "root": root,
            "seat": seat,
            "binding": binding,
            "state": state,
            "output": output,
            "generation": generation,
            "binding_value": binding_value,
            "state_value": state_value,
        }

    def validate_state_argv(
        self,
        fixture: dict[str, object],
        *,
        binding: Path | None = None,
        expected_generation: int | None = None,
        expected_session: str | None = None,
        output: Path | None = None,
    ) -> list[str]:
        return [
            "validate-state",
            "--binding",
            str(
                binding
                if binding is not None
                else fixture["binding"]
            ),
            "--expected-generation",
            str(
                expected_generation
                if expected_generation is not None
                else fixture["generation"]
            ),
            "--expected-session",
            (
                expected_session
                if expected_session is not None
                else self.SESSION_ID
            ),
            "--output",
            str(
                output
                if output is not None
                else fixture["output"]
            ),
        ]

    def check(
        self,
        record: dict[str, object],
        number: str,
    ) -> dict[str, object]:
        return next(
            item
            for item
            in record["checks"]
            if item["number"] == number
        )

    def test_validate_state_passes_with_explicit_state_file_and_fallback_path(
        self,
    ):
        for explicit_state_file in (
            False,
            True,
        ):
            with self.subTest(
                explicit_state_file=explicit_state_file
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_state_fixture(
                            Path(temp),
                            explicit_state_file=(
                                explicit_state_file
                            ),
                        )
                    )

                    if explicit_state_file:
                        self.assertEqual(
                            ISOLATION
                            .state_path_from_binding(
                                fixture[
                                    "binding_value"
                                ]
                            ),
                            fixture["state"],
                        )
                    else:
                        self.assertIsNone(
                            ISOLATION
                            .state_path_from_binding(
                                fixture[
                                    "binding_value"
                                ]
                            )
                        )

                        self.assertEqual(
                            (
                                fixture["binding"]
                                .parent
                                .parent
                                / "state.json"
                            ),
                            fixture["state"],
                        )

                    result = self.run_cli(
                        *self.validate_state_argv(
                            fixture
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr,
                    )

                    record = (
                        ISOLATION.load_json(
                            fixture["output"]
                        )
                    )

                    self.assertEqual(
                        record["schemaVersion"],
                        1,
                    )

                    self.assertRegex(
                        record["observedAt"],
                        UTC_RE,
                    )

                    self.assertEqual(
                        record["verdict"],
                        "pass",
                    )

                    self.assertEqual(
                        len(record["checks"]),
                        5,
                    )

                    self.assertEqual(
                        [
                            item["number"]
                            for item
                            in record["checks"]
                        ],
                        [
                            (
                                "N1.state."
                                "binding-generation"
                            ),
                            "N1.state.session",
                            "N1.state.read",
                            (
                                "N1.state."
                                "latest-generation"
                            ),
                            (
                                "N1.state."
                                "latest-binding"
                            ),
                        ],
                    )

                    self.assertTrue(
                        all(
                            item["verdict"]
                            == "pass"
                            for item
                            in record["checks"]
                        )
                    )

    def test_validate_state_unreadable_or_invalid_binding_is_unknown_early_return(
        self,
    ):
        cases = (
            "missing",
            "invalid-json",
        )

        for case in cases:
            with self.subTest(
                case=case
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_state_fixture(
                            Path(temp)
                        )
                    )

                    if case == "missing":
                        fixture[
                            "binding"
                        ].unlink()

                    else:
                        fixture[
                            "binding"
                        ].write_text(
                            "{not-json\n",
                            encoding="utf-8",
                        )

                    result = self.run_cli(
                        *self.validate_state_argv(
                            fixture
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    record = (
                        ISOLATION.load_json(
                            fixture["output"]
                        )
                    )

                    self.assertEqual(
                        record["schemaVersion"],
                        1,
                    )

                    self.assertEqual(
                        record["verdict"],
                        "unknown",
                    )

                    self.assertEqual(
                        record["checks"],
                        [],
                    )

                    self.assertIn(
                        (
                            "latest binding "
                            "unreadable:"
                        ),
                        record["reason"],
                    )

    def test_validate_state_non_object_binding_root_is_unknown_early_return(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            fixture[
                "binding"
            ].write_text(
                "[1, 2, 3]\n",
                encoding="utf-8",
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record,
                {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason":
                        (
                            "latest binding root "
                            "is not object"
                        ),
                    "checks": [],
                },
            )

    def test_validate_state_symlink_state_is_unknown_and_returns_before_latest_checks(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp),
                    explicit_state_file=True,
                )
            )

            state = fixture["state"]

            real_state = (
                fixture["root"]
                / "real-state.json"
            )

            real_state.write_text(
                state.read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )

            state.unlink()

            try:
                state.symlink_to(
                    real_state
                )
            except OSError as exc:
                self.skipTest(
                    "symlink unavailable: "
                    f"{exc}"
                )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                len(record["checks"]),
                3,
            )

            self.assertEqual(
                [
                    item["number"]
                    for item
                    in record["checks"]
                ],
                [
                    (
                        "N1.state."
                        "binding-generation"
                    ),
                    "N1.state.session",
                    "N1.state.read",
                ],
            )

            read_check = self.check(
                record,
                "N1.state.read",
            )

            self.assertEqual(
                read_check["verdict"],
                "unknown",
            )

            self.assertIn(
                "state file is symlink",
                read_check[
                    "detail"
                ]["error"],
            )

    def test_validate_state_missing_or_invalid_state_is_unknown_and_returns_before_latest_checks(
        self,
    ):
        cases = (
            "missing",
            "invalid-json",
        )

        for case in cases:
            with self.subTest(
                case=case
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_state_fixture(
                            Path(temp)
                        )
                    )

                    if case == "missing":
                        fixture[
                            "state"
                        ].unlink()

                    else:
                        fixture[
                            "state"
                        ].write_text(
                            "{broken-json\n",
                            encoding="utf-8",
                        )

                    result = self.run_cli(
                        *self.validate_state_argv(
                            fixture
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    record = (
                        ISOLATION.load_json(
                            fixture["output"]
                        )
                    )

                    self.assertEqual(
                        record["verdict"],
                        "unknown",
                    )

                    self.assertEqual(
                        len(record["checks"]),
                        3,
                    )

                    self.assertEqual(
                        [
                            item["number"]
                            for item
                            in record[
                                "checks"
                            ]
                        ],
                        [
                            (
                                "N1.state."
                                "binding-generation"
                            ),
                            "N1.state.session",
                            "N1.state.read",
                        ],
                    )

                    self.assertEqual(
                        self.check(
                            record,
                            "N1.state.read",
                        )["verdict"],
                        "unknown",
                    )

    def test_validate_state_non_object_state_root_is_not_early_return_but_adds_only_read_check(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            fixture[
                "state"
            ].write_text(
                "[1, 2, 3]\n",
                encoding="utf-8",
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            # Unlike the state read exception path, this reaches
            # the common final write_json block.
            self.assertRegex(
                record["observedAt"],
                UTC_RE,
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                len(record["checks"]),
                3,
            )

            self.assertEqual(
                [
                    item["number"]
                    for item
                    in record["checks"]
                ],
                [
                    (
                        "N1.state."
                        "binding-generation"
                    ),
                    "N1.state.session",
                    "N1.state.read",
                ],
            )

            read_check = self.check(
                record,
                "N1.state.read",
            )

            self.assertEqual(
                read_check["verdict"],
                "unknown",
            )

            self.assertEqual(
                read_check[
                    "detail"
                ]["error"],
                "state root is not object",
            )

    def test_validate_state_invalid_binding_generation_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            binding_value = dict(
                fixture["binding_value"]
            )

            # bool must remain invalid even though bool is an int subclass.
            binding_value[
                "generation"
            ] = True

            ISOLATION.write_json(
                fixture["binding"],
                binding_value,
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.state."
                        "binding-generation"
                    ),
                )["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.state.session",
                )["verdict"],
                "pass",
            )

    def test_validate_state_session_mismatch_is_definite_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture,
                    expected_session=(
                        self.OTHER_SESSION_ID
                    ),
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            self.assertEqual(
                self.check(
                    record,
                    "N1.state.session",
                )["verdict"],
                "fail",
            )

    def test_validate_state_invalid_latest_generation_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            state_value = dict(
                fixture["state_value"]
            )

            state_value[
                "latestGeneration"
            ] = "01"

            ISOLATION.write_json(
                fixture["state"],
                state_value,
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            self.assertEqual(
                self.check(
                    record,
                    (
                        "N1.state."
                        "latest-generation"
                    ),
                )["verdict"],
                "unknown",
            )

    def test_validate_state_latest_binding_mismatch_is_definite_fail(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            other_binding = (
                fixture["root"]
                / "other-binding.json"
            )

            other_binding.write_text(
                "{}\n",
                encoding="utf-8",
            )

            state_value = dict(
                fixture["state_value"]
            )

            state_value[
                "latestBinding"
            ] = str(
                other_binding
            )

            ISOLATION.write_json(
                fixture["state"],
                state_value,
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "fail",
            )

            latest_binding = self.check(
                record,
                (
                    "N1.state."
                    "latest-binding"
                ),
            )

            self.assertEqual(
                latest_binding[
                    "verdict"
                ],
                "fail",
            )

            self.assertEqual(
                latest_binding[
                    "detail"
                ]["actual"],
                str(
                    other_binding.resolve()
                ),
            )

            self.assertEqual(
                latest_binding[
                    "detail"
                ]["expected"],
                str(
                    fixture[
                        "binding"
                    ].resolve()
                ),
            )

    def test_validate_state_unresolvable_latest_binding_is_unknown(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = (
                self.make_state_fixture(
                    Path(temp)
                )
            )

            missing_binding = (
                fixture["root"]
                / "does-not-exist"
                / "binding.json"
            )

            state_value = dict(
                fixture["state_value"]
            )

            state_value[
                "latestBinding"
            ] = str(
                missing_binding
            )

            ISOLATION.write_json(
                fixture["state"],
                state_value,
            )

            result = self.run_cli(
                *self.validate_state_argv(
                    fixture
                )
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            record = (
                ISOLATION.load_json(
                    fixture["output"]
                )
            )

            self.assertEqual(
                record["verdict"],
                "unknown",
            )

            latest_binding = self.check(
                record,
                (
                    "N1.state."
                    "latest-binding"
                ),
            )

            self.assertEqual(
                latest_binding[
                    "verdict"
                ],
                "unknown",
            )

            self.assertIn(
                "error",
                latest_binding[
                    "detail"
                ],
            )

    def test_validate_state_empty_or_non_string_latest_binding_is_unknown(
        self,
    ):
        for value in (
            "",
            None,
            42,
            [],
        ):
            with self.subTest(
                value=value
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = (
                        self.make_state_fixture(
                            Path(temp)
                        )
                    )

                    state_value = dict(
                        fixture[
                            "state_value"
                        ]
                    )

                    state_value[
                        "latestBinding"
                    ] = value

                    ISOLATION.write_json(
                        fixture["state"],
                        state_value,
                    )

                    result = self.run_cli(
                        *self
                        .validate_state_argv(
                            fixture
                        )
                    )

                    self.assertEqual(
                        result.returncode,
                        2,
                    )

                    record = (
                        ISOLATION
                        .load_json(
                            fixture[
                                "output"
                            ]
                        )
                    )

                    self.assertEqual(
                        record["verdict"],
                        "unknown",
                    )

                    latest_binding = (
                        self.check(
                            record,
                            (
                                "N1.state."
                                "latest-binding"
                            ),
                        )
                    )

                    self.assertEqual(
                        latest_binding[
                            "verdict"
                        ],
                        "unknown",
                    )

                    self.assertEqual(
                        latest_binding[
                            "detail"
                        ]["actual"],
                        value,
                    )

    def test_write_n1_result_cli_accepts_all_three_verdicts(
        self,
    ):
        for verdict in (
            "pass",
            "fail",
            "unknown",
        ):
            with self.subTest(
                verdict=verdict
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(
                        temp
                    ).resolve()

                    output = (
                        root
                        / "nested"
                        / "result.json"
                    )

                    reason = (
                        f"reason-for-{verdict}"
                    )

                    result = self.run_cli(
                        "write-n1-result",
                        "--output",
                        str(output),
                        "--verdict",
                        verdict,
                        "--reason",
                        reason,
                    )

                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr,
                    )

                    self.assertEqual(
                        result.stdout,
                        "",
                    )

                    record = (
                        ISOLATION.load_json(
                            output
                        )
                    )

                    self.assertEqual(
                        record[
                            "schemaVersion"
                        ],
                        1,
                    )

                    self.assertEqual(
                        record["check"],
                        "N1",
                    )

                    self.assertEqual(
                        record["verdict"],
                        verdict,
                    )

                    self.assertEqual(
                        record["reason"],
                        reason,
                    )

                    self.assertRegex(
                        record[
                            "observedAt"
                        ],
                        UTC_RE,
                    )

    def test_write_n1_result_cli_rejects_invalid_verdict_at_argparse_layer(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            output = (
                root
                / "result.json"
            )

            result = self.run_cli(
                "write-n1-result",
                "--output",
                str(output),
                "--verdict",
                "maybe",
                "--reason",
                "invalid",
            )

            self.assertEqual(
                result.returncode,
                2,
            )

            self.assertIn(
                "invalid choice",
                result.stderr,
            )

            self.assertIn(
                "maybe",
                result.stderr,
            )

            self.assertFalse(
                output.exists()
            )

            # argparse rejected the request before the handler could run.
            self.assertNotIn(
                "invalid N1 verdict",
                result.stderr,
            )

    def test_write_n1_result_handler_defensively_rejects_invalid_verdict_when_called_directly(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            output = (
                root
                / "result.json"
            )

            args = (
                ISOLATION
                .argparse
                .Namespace(
                    output=str(output),
                    verdict="maybe",
                    reason="invalid",
                )
            )

            stderr = io.StringIO()

            with contextlib.redirect_stderr(
                stderr
            ):
                with self.assertRaises(
                    SystemExit
                ) as raised:
                    ISOLATION.command_write_n1_result(
                        args
                    )

            self.assertEqual(
                raised.exception.code,
                2,
            )

            self.assertEqual(
                stderr.getvalue(),
                (
                    "pilot-gate-isolation: "
                    "invalid N1 verdict\n"
                ),
            )

            self.assertFalse(
                output.exists()
            )


if __name__ == "__main__":
    unittest.main()
