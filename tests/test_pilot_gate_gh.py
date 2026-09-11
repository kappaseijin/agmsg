"""Tests for the isolated GitHub CLI double used by the G4 I1 gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GH_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_GH_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-gh.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_gh",
    GH_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate gh helper: {GH_HELPER}"
    )

GH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GH)


class PilotGateGhTests(unittest.TestCase):
    REPO = "gate/agmsg"
    ISSUE = "396"

    def make_fixture(
        self,
        root: Path,
    ) -> dict[str, Path | dict[str, str]]:
        root = root.resolve()

        store = root / "gh-store"
        body_root = root / "body-root"
        store.mkdir(parents=True)
        body_root.mkdir(parents=True)

        log = root / "gh-invocations.jsonl"

        env = os.environ.copy()
        env.update(
            {
                "AGMSG_GATE_GH_STORE": str(store),
                "AGMSG_GATE_GH_LOG": str(log),
                "AGMSG_GATE_GH_REPO": self.REPO,
                "AGMSG_GATE_GH_ISSUE": self.ISSUE,
                "AGMSG_GATE_GH_BODY_ROOT": str(body_root),
            }
        )

        return {
            "root": root,
            "store": store,
            "body_root": body_root,
            "log": log,
            "env": env,
        }

    def run_cli(
        self,
        fixture: dict[str, Path | dict[str, str]],
        *argv: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        selected_env = (
            env
            if env is not None
            else fixture["env"]
        )

        return subprocess.run(
            [
                sys.executable,
                str(GH_HELPER),
                *argv,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=selected_env,
            cwd=(
                str(cwd)
                if cwd is not None
                else None
            ),
        )

    def log_records(
        self,
        fixture: dict[str, Path | dict[str, str]],
    ) -> list[dict[str, object]]:
        log = fixture["log"]

        if not log.is_file():
            return []

        return [
            json.loads(line)
            for line
            in log.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

    def store_value(
        self,
        fixture: dict[str, Path | dict[str, str]],
    ) -> dict[str, object]:
        path = (
            fixture["store"]
            / "comments.json"
        )

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def write_body(
        self,
        fixture: dict[str, Path | dict[str, str]],
        name: str,
        body: str,
    ) -> Path:
        path = (
            fixture["body_root"]
            / name
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            body,
            encoding="utf-8",
        )

        return path

    def comment_argv(
        self,
        body: Path,
        *,
        repo: str | None = None,
        issue: str | None = None,
    ) -> list[str]:
        return [
            "issue",
            "comment",
            (
                issue
                if issue is not None
                else self.ISSUE
            ),
            "--repo",
            (
                repo
                if repo is not None
                else self.REPO
            ),
            "--body-file",
            str(body),
        ]

    def view_argv(
        self,
        *,
        repo: str | None = None,
        issue: str | None = None,
        json_value: str = "comments",
    ) -> list[str]:
        return [
            "issue",
            "view",
            (
                issue
                if issue is not None
                else self.ISSUE
            ),
            "--repo",
            (
                repo
                if repo is not None
                else self.REPO
            ),
            "--json",
            json_value,
        ]

    def test_die_writes_prefixed_message_and_defaults_to_exit_64(
        self,
    ):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(
            stderr
        ):
            with self.assertRaises(
                SystemExit
            ) as raised:
                GH.die(
                    "rejected"
                )

        self.assertEqual(
            raised.exception.code,
            64,
        )

        self.assertEqual(
            stderr.getvalue(),
            "pilot-gate-gh: rejected\n",
        )

    def test_write_json_atomic_is_compact_utf8_atomic_and_overwrites(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            output = (
                root
                / "nested"
                / "store.json"
            )

            output.parent.mkdir(
                parents=True
            )

            output.write_text(
                '{"old":true}\n',
                encoding="utf-8",
            )

            value = {
                "schemaVersion": 1,
                "nextId": 2,
                "comments": [
                    {
                        "body": "日本語",
                    }
                ],
            }

            real_replace = os.replace

            with mock.patch.object(
                GH.os,
                "replace",
                wraps=real_replace,
            ) as replace_mock:
                GH.write_json_atomic(
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
                    (
                        f".{output.name}."
                        f"{os.getpid()}.tmp"
                    )
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
                (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ),
            )

            self.assertNotIn(
                ": ",
                raw,
            )
            self.assertIn(
                "日本語",
                raw,
            )
            self.assertNotIn(
                r"\u65e5",
                raw,
            )

    def test_append_jsonl_appends_compact_utf8_lines(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = (
                root
                / "nested"
                / "audit.jsonl"
            )

            GH.append_jsonl(
                path,
                {
                    "n": 1,
                    "text": "日本語",
                },
            )

            GH.append_jsonl(
                path,
                {
                    "n": 2,
                },
            )

            self.assertEqual(
                path.read_text(
                    encoding="utf-8"
                ).splitlines(),
                [
                    '{"n":1,"text":"日本語"}',
                    '{"n":2}',
                ],
            )

    def test_load_store_returns_initial_state_when_absent(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            self.assertEqual(
                GH.load_store(
                    root / "missing.json"
                ),
                {
                    "schemaVersion": 1,
                    "nextId": 1,
                    "comments": [],
                },
            )

    def test_load_store_rejects_invalid_structures_with_exit_64(
        self,
    ):
        cases = (
            (
                [],
                "store root is not object",
            ),
            (
                {
                    "schemaVersion": 2,
                    "nextId": 1,
                    "comments": [],
                },
                "store schema mismatch",
            ),
            (
                {
                    "schemaVersion": 1,
                    "nextId": 0,
                    "comments": [],
                },
                "store nextId invalid",
            ),
            (
                {
                    "schemaVersion": 1,
                    "nextId": "1",
                    "comments": [],
                },
                "store nextId invalid",
            ),
            (
                {
                    "schemaVersion": 1,
                    "nextId": 1,
                    "comments": {},
                },
                "store comments invalid",
            ),
        )

        for value, message in cases:
            with self.subTest(
                message=message
            ):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    path = root / "comments.json"

                    path.write_text(
                        json.dumps(value),
                        encoding="utf-8",
                    )

                    stderr = io.StringIO()

                    with contextlib.redirect_stderr(
                        stderr
                    ):
                        with self.assertRaises(
                            SystemExit
                        ) as raised:
                            GH.load_store(
                                path
                            )

                    self.assertEqual(
                        raised.exception.code,
                        64,
                    )

                    self.assertIn(
                        (
                            "pilot-gate-gh: "
                            + message
                        ),
                        stderr.getvalue(),
                    )

    def test_required_prelog_environment_failures_do_not_create_audit_log(
        self,
    ):
        required_before_log = (
            "AGMSG_GATE_GH_STORE",
            "AGMSG_GATE_GH_LOG",
            "AGMSG_GATE_GH_REPO",
            "AGMSG_GATE_GH_ISSUE",
        )

        for key in required_before_log:
            with self.subTest(
                key=key
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    env = dict(
                        fixture["env"]
                    )

                    env.pop(
                        key,
                        None,
                    )

                    result = self.run_cli(
                        fixture,
                        *self.view_argv(),
                        env=env,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        (
                            "pilot-gate-gh: "
                            "missing environment: "
                            + key
                        ),
                        result.stderr,
                    )

                    self.assertFalse(
                        fixture["log"].exists()
                    )

    def test_view_does_not_require_body_root_environment(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            env = dict(
                fixture["env"]
            )
            env.pop(
                "AGMSG_GATE_GH_BODY_ROOT"
            )

            result = self.run_cli(
                fixture,
                *self.view_argv(),
                env=env,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            self.assertEqual(
                json.loads(
                    result.stdout
                ),
                {
                    "comments": [],
                },
            )

    def test_comment_missing_body_root_is_logged_before_exit_64(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            body = self.write_body(
                fixture,
                "body.txt",
                "hello",
            )

            env = dict(
                fixture["env"]
            )
            env.pop(
                "AGMSG_GATE_GH_BODY_ROOT"
            )

            argv = self.comment_argv(
                body
            )

            result = self.run_cli(
                fixture,
                *argv,
                env=env,
            )

            self.assertEqual(
                result.returncode,
                64,
            )

            self.assertIn(
                (
                    "missing environment: "
                    "AGMSG_GATE_GH_BODY_ROOT"
                ),
                result.stderr,
            )

            records = self.log_records(
                fixture
            )

            self.assertEqual(
                len(records),
                1,
            )

            self.assertEqual(
                records[0]["argv"],
                argv,
            )

    def test_rejected_commands_are_audited_before_die(
        self,
    ):
        cases = (
            (
                [],
                (
                    "only issue comment/view "
                    "are supported"
                ),
            ),
            (
                [
                    "repo",
                    "view",
                    self.ISSUE,
                ],
                (
                    "only issue comment/view "
                    "are supported"
                ),
            ),
            (
                [
                    "issue",
                    "delete",
                    self.ISSUE,
                ],
                (
                    "only issue comment/view "
                    "are supported"
                ),
            ),
        )

        for argv, message in cases:
            with self.subTest(
                argv=argv
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    result = self.run_cli(
                        fixture,
                        *argv,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        message,
                        result.stderr,
                    )

                    records = (
                        self.log_records(
                            fixture
                        )
                    )

                    self.assertEqual(
                        len(records),
                        1,
                    )

                    record = records[0]

                    self.assertEqual(
                        record["schemaVersion"],
                        1,
                    )

                    self.assertIsInstance(
                        record["atMonotonicNs"],
                        int,
                    )

                    self.assertGreater(
                        record["atMonotonicNs"],
                        0,
                    )

                    self.assertEqual(
                        record["argv"],
                        argv,
                    )

                    self.assertTrue(
                        record["cwd"]
                    )

    def test_audit_log_records_actual_working_directory(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            cwd = (
                fixture["root"]
                / "working-directory"
            )
            cwd.mkdir()

            result = self.run_cli(
                fixture,
                *self.view_argv(),
                cwd=cwd,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            record = self.log_records(
                fixture
            )[0]

            self.assertEqual(
                Path(
                    record["cwd"]
                ).resolve(),
                cwd,
            )

    def test_parse_repo_issue_rejects_unknown_missing_and_duplicate_options_and_logs_each_attempt(
        self,
    ):
        cases = (
            (
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--unknown",
                    "value",
                ],
                "unsupported option: --unknown",
            ),
            (
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--repo",
                ],
                "missing value for --repo",
            ),
            (
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                    "--repo",
                    self.REPO,
                ],
                "duplicate option: --repo",
            ),
        )

        for argv, message in cases:
            with self.subTest(
                argv=argv
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    result = self.run_cli(
                        fixture,
                        *argv,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        message,
                        result.stderr,
                    )

                    records = self.log_records(
                        fixture
                    )

                    self.assertEqual(
                        len(records),
                        1,
                    )

                    self.assertEqual(
                        records[0]["argv"],
                        argv,
                    )

    def test_scope_mismatches_are_rejected_and_audited(
        self,
    ):
        cases = (
            (
                self.view_argv(
                    issue="397"
                ),
                (
                    "issue number outside "
                    "isolated scope"
                ),
            ),
            (
                self.view_argv(
                    repo="other/repo"
                ),
                (
                    "repository outside "
                    "isolated scope"
                ),
            ),
        )

        for argv, message in cases:
            with self.subTest(
                argv=argv
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    result = self.run_cli(
                        fixture,
                        *argv,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        message,
                        result.stderr,
                    )

                    self.assertEqual(
                        len(
                            self.log_records(
                                fixture
                            )
                        ),
                        1,
                    )

    def test_comment_requires_exact_repo_and_body_file_option_set(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            body = self.write_body(
                fixture,
                "body.txt",
                "body",
            )

            cases = (
                [
                    "issue",
                    "comment",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                ],
                [
                    "issue",
                    "comment",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                    "--body-file",
                    str(body),
                    "--json",
                    "comments",
                ],
            )

            for argv in cases:
                with self.subTest(
                    argv=argv
                ):
                    result = self.run_cli(
                        fixture,
                        *argv,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        (
                            "issue comment requires "
                            "exactly --repo and "
                            "--body-file"
                        ),
                        result.stderr,
                    )

    def test_comment_rejects_missing_outside_and_directory_body_paths(
        self,
    ):
        cases = (
            "missing",
            "outside",
            "directory",
        )

        for case in cases:
            with self.subTest(
                case=case
            ):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = self.make_fixture(
                        Path(temp)
                    )

                    if case == "missing":
                        body = (
                            fixture["body_root"]
                            / "missing.txt"
                        )
                        expected = (
                            "body file unavailable:"
                        )

                    elif case == "outside":
                        body = (
                            fixture["root"]
                            / "outside.txt"
                        )
                        body.write_text(
                            "outside",
                            encoding="utf-8",
                        )
                        expected = (
                            "body file outside gate "
                            "broker state root"
                        )

                    else:
                        body = (
                            fixture["body_root"]
                            / "directory"
                        )
                        body.mkdir()
                        expected = (
                            "body file must be regular "
                            "non-symlink file"
                        )

                    result = self.run_cli(
                        fixture,
                        *self.comment_argv(
                            body
                        ),
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        expected,
                        result.stderr,
                    )

                    self.assertEqual(
                        len(
                            self.log_records(
                                fixture
                            )
                        ),
                        1,
                    )

    def test_comment_rejects_symlink_body_file_even_when_target_is_inside_allowed_root(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            real = self.write_body(
                fixture,
                "real.txt",
                "body",
            )

            link = (
                fixture["body_root"]
                / "link.txt"
            )

            try:
                link.symlink_to(
                    real
                )
            except OSError as exc:
                self.skipTest(
                    f"symlink unavailable: {exc}"
                )

            result = self.run_cli(
                fixture,
                *self.comment_argv(
                    link
                ),
            )

            self.assertEqual(
                result.returncode,
                64,
            )

            self.assertIn(
                (
                    "body file must be regular "
                    "non-symlink file"
                ),
                result.stderr,
            )

    def test_comment_allows_exactly_4096_utf8_bytes_and_rejects_4097(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            exact = self.write_body(
                fixture,
                "exact.txt",
                "a" * 4096,
            )

            accepted = self.run_cli(
                fixture,
                *self.comment_argv(
                    exact
                ),
            )

            self.assertEqual(
                accepted.returncode,
                0,
                accepted.stderr,
            )

            too_large = self.write_body(
                fixture,
                "too-large.txt",
                "b" * 4097,
            )

            rejected = self.run_cli(
                fixture,
                *self.comment_argv(
                    too_large
                ),
            )

            self.assertEqual(
                rejected.returncode,
                64,
            )

            self.assertIn(
                "body too large",
                rejected.stderr,
            )

    def test_two_comments_allocate_ids_one_then_two_and_persist_compact_store(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            first_body = self.write_body(
                fixture,
                "first.txt",
                "first body",
            )

            second_body = self.write_body(
                fixture,
                "second.txt",
                "second body",
            )

            first = self.run_cli(
                fixture,
                *self.comment_argv(
                    first_body
                ),
            )

            second = self.run_cli(
                fixture,
                *self.comment_argv(
                    second_body
                ),
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

            first_url = (
                "https://github.com/"
                f"{self.REPO}/issues/"
                f"{self.ISSUE}"
                "#issuecomment-1"
            )

            second_url = (
                "https://github.com/"
                f"{self.REPO}/issues/"
                f"{self.ISSUE}"
                "#issuecomment-2"
            )

            self.assertEqual(
                first.stdout.strip(),
                first_url,
            )
            self.assertEqual(
                second.stdout.strip(),
                second_url,
            )

            store = self.store_value(
                fixture
            )

            self.assertEqual(
                store["schemaVersion"],
                1,
            )
            self.assertEqual(
                store["nextId"],
                3,
            )

            self.assertEqual(
                store["comments"],
                [
                    {
                        "url": first_url,
                        "body": "first body",
                        "issue": 396,
                        "repo": self.REPO,
                    },
                    {
                        "url": second_url,
                        "body": "second body",
                        "issue": 396,
                        "repo": self.REPO,
                    },
                ],
            )

            raw = (
                fixture["store"]
                / "comments.json"
            ).read_text(
                encoding="utf-8"
            )

            self.assertEqual(
                raw,
                (
                    json.dumps(
                        store,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ),
            )

            self.assertEqual(
                len(
                    self.log_records(
                        fixture
                    )
                ),
                2,
            )

    def test_comment_then_view_returns_persisted_comment(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            body = self.write_body(
                fixture,
                "body.txt",
                "stored body",
            )

            comment = self.run_cli(
                fixture,
                *self.comment_argv(
                    body
                ),
            )

            self.assertEqual(
                comment.returncode,
                0,
                comment.stderr,
            )

            view = self.run_cli(
                fixture,
                *self.view_argv(),
            )

            self.assertEqual(
                view.returncode,
                0,
                view.stderr,
            )

            value = json.loads(
                view.stdout
            )

            self.assertEqual(
                value,
                {
                    "comments": [
                        {
                            "url":
                                comment.stdout.strip(),
                            "body":
                                "stored body",
                            "issue":
                                396,
                            "repo":
                                self.REPO,
                        }
                    ]
                },
            )

            self.assertEqual(
                len(
                    self.log_records(
                        fixture
                    )
                ),
                2,
            )

    def test_view_requires_exact_repo_json_comments_contract(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            cases = (
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                ],
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                    "--json",
                    "body",
                ],
                [
                    "issue",
                    "view",
                    self.ISSUE,
                    "--repo",
                    self.REPO,
                    "--json",
                    "comments",
                    "--body-file",
                    str(
                        fixture["body_root"]
                        / "unused"
                    ),
                ],
            )

            for argv in cases:
                with self.subTest(
                    argv=argv
                ):
                    result = self.run_cli(
                        fixture,
                        *argv,
                    )

                    self.assertEqual(
                        result.returncode,
                        64,
                    )

                    self.assertIn(
                        (
                            "issue view supports "
                            "exactly --repo ... "
                            "--json comments"
                        ),
                        result.stderr,
                    )

    def test_invalid_store_content_is_rejected_after_audit_log_is_written(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            store_path = (
                fixture["store"]
                / "comments.json"
            )

            store_path.write_text(
                '{"schemaVersion":2}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                fixture,
                *self.view_argv(),
            )

            self.assertEqual(
                result.returncode,
                64,
            )

            self.assertIn(
                "store schema mismatch",
                result.stderr,
            )

            self.assertEqual(
                len(
                    self.log_records(
                        fixture
                    )
                ),
                1,
            )

    def test_missing_store_directory_is_uncaught_file_not_found_exit_one_and_not_audited(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(
                Path(temp)
            )

            missing_store = (
                fixture["root"]
                / "missing-store"
            )

            env = dict(
                fixture["env"]
            )
            env[
                "AGMSG_GATE_GH_STORE"
            ] = str(
                missing_store
            )

            result = self.run_cli(
                fixture,
                *self.view_argv(),
                env=env,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            self.assertIn(
                "FileNotFoundError",
                result.stderr,
            )

            self.assertFalse(
                fixture["log"].exists()
            )


if __name__ == "__main__":
    unittest.main()