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


if __name__ == "__main__":
    unittest.main()