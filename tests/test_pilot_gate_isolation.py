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


if __name__ == "__main__":
    unittest.main()
