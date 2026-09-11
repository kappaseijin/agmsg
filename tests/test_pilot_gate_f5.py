"""Unit tests for scripts/lib/pilot-gate-f5.py."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
F5_HELPER = Path(
    os.environ.get(
        "PILOT_GATE_F5_UNDER_TEST",
        ROOT / "scripts" / "lib" / "pilot-gate-f5.py",
    )
).resolve()

SPEC = importlib.util.spec_from_file_location(
    "pilot_gate_f5",
    F5_HELPER,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        f"cannot load pilot gate F5 helper: {F5_HELPER}"
    )

F5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F5)


def write_team(
    gate_repo: Path,
    directory: str,
    config,
) -> Path:
    team_dir = gate_repo / "teams" / directory
    team_dir.mkdir(parents=True, exist_ok=True)
    path = team_dir / "config.json"
    if isinstance(config, str):
        path.write_text(config, encoding="utf-8")
    else:
        path.write_text(json.dumps(config), encoding="utf-8")
    return path


def team_config(name: str, *agents: str) -> dict:
    return {
        "name": name,
        "agents": {agent: {"type": "claude-code"} for agent in agents},
    }


def make_events_db(path: Path, rows) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              id TEXT,
              legacy_id TEXT,
              type TEXT,
              team TEXT,
              from_agent TEXT,
              to_agent TEXT,
              body TEXT,
              at TEXT
            )
            """
        )
        for row in rows:
            connection.execute(
                """
                INSERT INTO events
                  (id, legacy_id, type, team, from_agent, to_agent, body, at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        connection.commit()
    finally:
        connection.close()
    return path


class PilotGateF5RoundA(unittest.TestCase):
    """Pure helpers: containment, argv safety, storage observation."""

    GATE = "agmsg-g4gate-f5-a"
    SENDER = "gate_sender"
    RECIPIENT = "gate_recipient"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.gate_repo = self.root / "repo"
        (self.gate_repo / "teams").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    # --- constants -------------------------------------------------------

    def test_module_constants_pin_timing_contract(self):
        self.assertEqual(F5.PILOT_TYPE, "claude-code")
        self.assertEqual(F5.DELIVERY_TIMEOUT_SECONDS, 20.0)
        self.assertEqual(F5.WATCH_POLL_INTERVAL_SECONDS, 1)
        self.assertEqual(F5.WATCH_READY_SECONDS, 1.25)
        self.assertEqual(F5.FAULT_OBSERVATION_SECONDS, 2.25)
        self.assertEqual(F5.POST_DELIVERY_SETTLE_SECONDS, 2.25)
        # The fault window must outlast at least two watcher polls,
        # otherwise "not delivered while stopped" proves nothing.
        self.assertGreater(
            F5.FAULT_OBSERVATION_SECONDS,
            2 * F5.WATCH_POLL_INTERVAL_SECONDS,
        )

    # --- file helpers ----------------------------------------------------

    def test_load_module_loads_file_and_rejects_unloadable_path(self):
        module_path = self.root / "sample_mod.py"
        module_path.write_text("VALUE = 42\n", encoding="utf-8")
        module = F5.load_module(module_path, "sample_mod_f5")
        self.assertEqual(module.VALUE, 42)

        with mock.patch.object(
            F5.importlib.util,
            "spec_from_file_location",
            return_value=None,
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot load module"):
                F5.load_module(module_path, "sample_mod_f5")

    def test_atomic_json_writes_sorted_indented_utf8_and_leaves_no_temp(self):
        target = self.root / "nested" / "out.json"
        F5.atomic_json(target, {"b": "値", "a": 1})
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text, '{\n  "a": 1,\n  "b": "値"\n}\n')
        self.assertEqual(
            sorted(p.name for p in target.parent.iterdir()),
            ["out.json"],
        )

        F5.atomic_json(target, [1])
        self.assertEqual(json.loads(target.read_text()), [1])

    def test_append_jsonl_appends_compact_sorted_records(self):
        target = self.root / "logs" / "a.jsonl"
        F5.append_jsonl(target, {"z": 1, "a": "é"})
        F5.append_jsonl(target, {"k": [1, 2]})
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            '{"a":"é","z":1}\n{"k":[1,2]}\n',
        )

    def test_read_json_roundtrip(self):
        target = self.root / "r.json"
        target.write_text('{"x": [1, "y"]}', encoding="utf-8")
        self.assertEqual(F5.read_json(target), {"x": [1, "y"]})

    # --- assertions ------------------------------------------------------

    def test_assertion_maps_tristate_strictly(self):
        self.assertEqual(
            F5.assertion("n", True, {"d": 1}),
            {"name": "n", "verdict": "pass", "detail": {"d": 1}},
        )
        self.assertEqual(F5.assertion("n", False, None)["verdict"], "fail")
        self.assertEqual(F5.assertion("n", None, None)["verdict"], "unknown")
        # Truthy / falsy non-bool values must not be coerced.
        self.assertEqual(F5.assertion("n", 1, None)["verdict"], "unknown")
        self.assertEqual(F5.assertion("n", 0, None)["verdict"], "unknown")
        self.assertEqual(F5.assertion("n", "yes", None)["verdict"], "unknown")

    def test_verdict_from_assertions_priority_fail_unknown_pass(self):
        p = {"verdict": "pass"}
        u = {"verdict": "unknown"}
        f = {"verdict": "fail"}
        self.assertEqual(F5.verdict_from_assertions([]), "pass")
        self.assertEqual(F5.verdict_from_assertions([p, p]), "pass")
        self.assertEqual(F5.verdict_from_assertions([p, u]), "unknown")
        self.assertEqual(F5.verdict_from_assertions([u, f, p]), "fail")
        self.assertEqual(F5.verdict_from_assertions([f]), "fail")

    # --- executables -----------------------------------------------------

    def test_require_regular_executable_accepts_only_regular_executable(self):
        good = self.root / "good.sh"
        good.write_text("#!/bin/sh\n", encoding="utf-8")
        good.chmod(0o700)
        F5.require_regular_executable(good)

        plain = self.root / "plain.sh"
        plain.write_text("#!/bin/sh\n", encoding="utf-8")
        plain.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "not regular executable"):
            F5.require_regular_executable(plain)

        # A symlink is rejected even without the explicit is_symlink()
        # test: lstat() reports S_IFLNK, so S_ISREG is already false.
        # Removing is_symlink() is therefore an equivalent mutant.
        link = self.root / "link.sh"
        link.symlink_to(good)
        with self.assertRaisesRegex(RuntimeError, "not regular executable"):
            F5.require_regular_executable(link)

        directory = self.root / "dir"
        directory.mkdir()
        with self.assertRaisesRegex(RuntimeError, "not regular executable"):
            F5.require_regular_executable(directory)

        with self.assertRaises(FileNotFoundError):
            F5.require_regular_executable(self.root / "missing.sh")

    # --- tokens ----------------------------------------------------------

    def test_safe_token_is_prefix_plus_16_hex_of_sha256(self):
        digest = hashlib.sha256(b"run-1").hexdigest()[:16]
        self.assertEqual(F5.safe_token("p", "run-1"), f"p-{digest}")
        self.assertEqual(
            F5.safe_token("p", "run-1"),
            F5.safe_token("p", "run-1"),
        )
        self.assertNotEqual(
            F5.safe_token("p", "run-1"),
            F5.safe_token("p", "run-2"),
        )
        self.assertNotEqual(
            F5.safe_token("a", "run-1"),
            F5.safe_token("b", "run-1"),
        )
        self.assertRegex(
            F5.safe_token("x", "日本語 / ../"),
            r"^x-[0-9a-f]{16}$",
        )

    # --- team configs ----------------------------------------------------

    def test_load_team_configs_reads_named_teams_and_skips_non_candidates(self):
        write_team(self.gate_repo, "one", team_config("t1", "a"))
        write_team(self.gate_repo, "two", team_config("t2", "b"))
        (self.gate_repo / "teams" / "no-config").mkdir()
        (self.gate_repo / "teams" / "file.json").write_text("{}")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "config.json").write_text(
            json.dumps(team_config("t-link", "z"))
        )
        (self.gate_repo / "teams" / "linked").symlink_to(outside)

        configs = F5.load_team_configs(self.gate_repo)
        self.assertEqual(sorted(configs), ["t1", "t2"])
        self.assertEqual(configs["t1"]["agents"], {"a": {"type": "claude-code"}})

    def test_load_team_configs_rejects_missing_or_symlinked_teams_dir(self):
        missing = self.root / "no-repo"
        missing.mkdir()
        with self.assertRaisesRegex(RuntimeError, "teams directory unavailable"):
            F5.load_team_configs(missing)

        linked_repo = self.root / "linked-repo"
        linked_repo.mkdir()
        (linked_repo / "teams").symlink_to(self.gate_repo / "teams")
        with self.assertRaisesRegex(RuntimeError, "teams directory unavailable"):
            F5.load_team_configs(linked_repo)

    def test_load_team_configs_rejects_invalid_root_name_and_duplicates(self):
        path = write_team(self.gate_repo, "bad-root", "[1, 2]")
        with self.assertRaisesRegex(RuntimeError, "team config root invalid"):
            F5.load_team_configs(self.gate_repo)
        path.write_text(json.dumps({"agents": {}}))
        with self.assertRaisesRegex(RuntimeError, "team name invalid"):
            F5.load_team_configs(self.gate_repo)
        path.write_text(json.dumps({"name": "", "agents": {}}))
        with self.assertRaisesRegex(RuntimeError, "team name invalid"):
            F5.load_team_configs(self.gate_repo)
        path.write_text(json.dumps({"name": 7, "agents": {}}))
        with self.assertRaisesRegex(RuntimeError, "team name invalid"):
            F5.load_team_configs(self.gate_repo)

        path.write_text(json.dumps(team_config("same")))
        write_team(self.gate_repo, "dup", team_config("same"))
        with self.assertRaisesRegex(RuntimeError, "duplicate team name: same"):
            F5.load_team_configs(self.gate_repo)

    def test_load_team_configs_propagates_json_decode_error(self):
        write_team(self.gate_repo, "broken", "{not json")
        with self.assertRaises(json.JSONDecodeError):
            F5.load_team_configs(self.gate_repo)

    def test_team_agents_requires_dict_with_string_keys(self):
        self.assertEqual(
            F5.team_agents({"agents": {"a": {}, "b": {}}}),
            {"a", "b"},
        )
        self.assertEqual(F5.team_agents({"agents": {}}), set())
        self.assertIsNone(F5.team_agents({}))
        self.assertIsNone(F5.team_agents({"agents": ["a", "b"]}))
        self.assertIsNone(F5.team_agents({"agents": None}))
        self.assertIsNone(F5.team_agents({"agents": {1: {}}}))

    # --- containment -----------------------------------------------------

    def contain(self, sender=None, recipient=None):
        return F5.prove_containment(
            gate_repo=self.gate_repo,
            gate_team=self.GATE,
            sender=sender or self.SENDER,
            recipient=recipient or self.RECIPIENT,
        )

    def checks_by_name(self, result):
        return {item["name"]: item["verdict"] for item in result["checks"]}

    def test_prove_containment_passes_for_isolated_gate_team(self):
        write_team(
            self.gate_repo,
            "gate",
            team_config(self.GATE, self.SENDER, self.RECIPIENT),
        )
        write_team(self.gate_repo, "live-b", team_config("live-b", "x"))
        write_team(self.gate_repo, "live-a", team_config("live-a", "y"))

        result = self.contain()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["liveTeamNames"], ["live-a", "live-b"])
        self.assertEqual(result["recipientCollisions"], [])
        self.assertEqual(
            self.checks_by_name(result),
            {
                "gate-team-agent-map-identifiable": "pass",
                "sender-in-gate-team": "pass",
                "recipient-in-gate-team": "pass",
                "sender-and-recipient-distinct": "pass",
                "live-team-agent-maps-identifiable": "pass",
                "recipient-name-absent-from-live-teams": "pass",
            },
        )

    def test_prove_containment_fails_when_recipient_exists_in_live_team(self):
        write_team(
            self.gate_repo,
            "gate",
            team_config(self.GATE, self.SENDER, self.RECIPIENT),
        )
        write_team(
            self.gate_repo,
            "live",
            team_config("live", self.RECIPIENT),
        )
        write_team(self.gate_repo, "other", team_config("other", "x"))

        result = self.contain()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["recipientCollisions"], ["live"])
        self.assertEqual(
            self.checks_by_name(result)[
                "recipient-name-absent-from-live-teams"
            ],
            "fail",
        )

    def test_prove_containment_sender_only_in_live_team_is_not_a_collision(self):
        # Only the recipient name is dangerous: a live watcher with that
        # name would consume the gate's messages.
        write_team(
            self.gate_repo,
            "gate",
            team_config(self.GATE, self.SENDER, self.RECIPIENT),
        )
        write_team(self.gate_repo, "live", team_config("live", self.SENDER))
        self.assertEqual(self.contain()["verdict"], "pass")

    def test_prove_containment_fails_for_missing_gate_team(self):
        write_team(self.gate_repo, "live", team_config("live", "x"))
        result = self.contain()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(
            result["checks"],
            [
                {
                    "name": "gate-team-exists",
                    "verdict": "fail",
                    "detail": self.GATE,
                }
            ],
        )
        self.assertEqual(result["liveTeamNames"], ["live"])

    def test_prove_containment_unknown_when_gate_agent_map_unreadable(self):
        write_team(
            self.gate_repo,
            "gate",
            {"name": self.GATE, "agents": ["not", "a", "map"]},
        )
        write_team(self.gate_repo, "live", team_config("live", "x"))
        result = self.contain()
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(
            result["checks"],
            [
                {
                    "name": "gate-team-agent-map-identifiable",
                    "verdict": "fail",
                    "detail": None,
                }
            ],
        )
        self.assertEqual(result["liveTeamNames"], ["live"])

    def test_prove_containment_membership_and_distinctness_failures(self):
        write_team(
            self.gate_repo,
            "gate",
            team_config(self.GATE, self.SENDER, self.RECIPIENT),
        )
        result = self.contain(sender="stranger")
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(
            self.checks_by_name(result)["sender-in-gate-team"], "fail"
        )

        result = self.contain(recipient="stranger")
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(
            self.checks_by_name(result)["recipient-in-gate-team"], "fail"
        )

        result = self.contain(sender=self.SENDER, recipient=self.SENDER)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(
            self.checks_by_name(result)["sender-and-recipient-distinct"],
            "fail",
        )

    def test_prove_containment_fails_when_live_agent_map_unreadable(self):
        write_team(
            self.gate_repo,
            "gate",
            team_config(self.GATE, self.SENDER, self.RECIPIENT),
        )
        write_team(self.gate_repo, "live", {"name": "live", "agents": None})
        result = self.contain()
        # An unreadable live team could hide the recipient name, so the
        # proof must not pass.
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(
            self.checks_by_name(result)["live-team-agent-maps-identifiable"],
            "fail",
        )
        self.assertEqual(result["recipientCollisions"], [])

    # --- provider argv ---------------------------------------------------

    def argv_safe(self, argv, live=("live-a",)):
        return F5.provider_argv_safe(
            argv=argv,
            gate_team=self.GATE,
            sender=self.SENDER,
            recipient=self.RECIPIENT,
            live_team_names=list(live),
        )

    def test_provider_argv_safe_accepts_exact_prefix_without_live_team(self):
        argv = [
            "message-send", self.GATE, self.SENDER, self.RECIPIENT,
            "req", "body",
        ]
        safe, detail = self.argv_safe(argv)
        self.assertIs(safe, True)
        self.assertEqual(
            detail,
            {
                "argv": argv,
                "expectedPrefix": [
                    "message-send", self.GATE, self.SENDER, self.RECIPIENT,
                ],
                "liveTeamArgvHits": [],
            },
        )

    def test_provider_argv_safe_rejects_live_team_anywhere_in_argv(self):
        base = ["message-send", self.GATE, self.SENDER, self.RECIPIENT]
        safe, detail = self.argv_safe(base + ["live-a", "body"])
        self.assertIs(safe, False)
        self.assertEqual(detail["liveTeamArgvHits"], ["live-a"])

        safe, detail = self.argv_safe(
            base + ["req", "live-b"],
            live=("live-b", "live-a", "unused"),
        )
        self.assertIs(safe, False)
        self.assertEqual(detail["liveTeamArgvHits"], ["live-b"])

        # Substring of an element is not an argv hit (element equality).
        safe, detail = self.argv_safe(base + ["req", "xx live-a xx"])
        self.assertIs(safe, True)
        self.assertEqual(detail["liveTeamArgvHits"], [])

    def test_provider_argv_safe_rejects_wrong_prefix(self):
        for argv in (
            ["message-send", "live-x", self.SENDER, self.RECIPIENT, "r", "b"],
            ["message-send", self.GATE, self.RECIPIENT, self.SENDER, "r", "b"],
            ["send", self.GATE, self.SENDER, self.RECIPIENT, "r", "b"],
            ["message-send", self.GATE, self.SENDER],
            [],
        ):
            with self.subTest(argv=argv):
                safe, _ = self.argv_safe(argv, live=())
                self.assertIs(safe, False)

    def test_provider_send_logs_and_calls_provider_with_exact_args(self):
        i1 = mock.Mock()
        i1.provider_call.return_value = {"state": "queued"}
        argv_log = self.root / "art" / "provider-argv.jsonl"
        provider = self.root / "p2-provider.sh"
        env = {"K": "V"}

        value = F5.provider_send(
            i1,
            provider=provider,
            gate_repo=self.gate_repo,
            env=env,
            gate_team=self.GATE,
            sender=self.SENDER,
            recipient=self.RECIPIENT,
            request_id="req-1",
            body="hello",
            live_team_names=["live-a"],
            argv_log=argv_log,
        )

        expected_args = [
            "message-send", self.GATE, self.SENDER, self.RECIPIENT,
            "req-1", "hello",
        ]
        self.assertEqual(value, {"state": "queued"})
        i1.provider_call.assert_called_once_with(
            provider, expected_args, self.gate_repo, env, None, False,
        )
        records = [
            json.loads(line)
            for line in argv_log.read_text().splitlines()
        ]
        self.assertEqual(
            records,
            [
                {
                    "kind": "provider",
                    "safe": True,
                    "argv": expected_args,
                    "expectedPrefix": expected_args[:4],
                    "liveTeamArgvHits": [],
                }
            ],
        )

    def test_provider_send_refuses_unsafe_argv_after_logging_it(self):
        i1 = mock.Mock()
        argv_log = self.root / "provider-argv.jsonl"
        with self.assertRaisesRegex(
            RuntimeError, "provider argv containment violation"
        ):
            F5.provider_send(
                i1,
                provider=self.root / "p",
                gate_repo=self.gate_repo,
                env={},
                gate_team=self.GATE,
                sender=self.SENDER,
                recipient=self.RECIPIENT,
                request_id="req",
                body="live-a",
                live_team_names=["live-a"],
                argv_log=argv_log,
            )
        i1.provider_call.assert_not_called()
        record = json.loads(argv_log.read_text())
        self.assertIs(record["safe"], False)
        self.assertEqual(record["liveTeamArgvHits"], ["live-a"])

    # --- storage observation ---------------------------------------------

    def test_storage_message_observation_matches_all_identity_columns(self):
        team, s, r = self.GATE, self.SENDER, self.RECIPIENT
        db = make_events_db(
            self.root / "events.db",
            [
                ("m1", "L1", "message_sent", team, s, r, "B", "t1"),
                # Each row below differs in exactly one identity column.
                ("m1", "L2", "message_read", team, s, r, "B", "t2"),
                ("m1", "L3", "message_sent", "other", s, r, "B", "t3"),
                ("m1", "L4", "message_sent", team, "x", r, "B", "t4"),
                ("m1", "L5", "message_sent", team, s, "x", "B", "t5"),
                ("m2", "L6", "message_sent", team, s, r, "B", "t6"),
                ("m1", "L7", "message_sent", team, s, r, "B2", "t7"),
            ],
        )
        result = F5.storage_message_observation(
            db,
            team=team,
            sender=s,
            recipient=r,
            message_id="m1",
            body="B",
        )
        self.assertEqual(
            result,
            {
                "count": 1,
                "rows": [
                    {
                        "messageId": "m1",
                        "legacyId": "L1",
                        "team": team,
                        "from": s,
                        "to": r,
                        "body": "B",
                        "createdAt": "t1",
                    }
                ],
            },
        )

    def test_storage_message_observation_counts_duplicates_in_seq_order(self):
        team, s, r = self.GATE, self.SENDER, self.RECIPIENT
        db = make_events_db(
            self.root / "events.db",
            [
                ("m1", "first", "message_sent", team, s, r, "B", "t1"),
                ("m1", "second", "message_sent", team, s, r, "B", "t2"),
            ],
        )
        result = F5.storage_message_observation(
            db, team=team, sender=s, recipient=r, message_id="m1", body="B",
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            [row["legacyId"] for row in result["rows"]],
            ["first", "second"],
        )

    def test_storage_message_observation_is_read_only(self):
        db = make_events_db(self.root / "events.db", [])
        before = db.read_bytes()
        result = F5.storage_message_observation(
            db,
            team=self.GATE,
            sender=self.SENDER,
            recipient=self.RECIPIENT,
            message_id="none",
            body="B",
        )
        self.assertEqual(result, {"count": 0, "rows": []})
        self.assertEqual(db.read_bytes(), before)

        with self.assertRaises(sqlite3.Error):
            F5.storage_message_observation(
                self.root / "missing.db",
                team=self.GATE,
                sender=self.SENDER,
                recipient=self.RECIPIENT,
                message_id="none",
                body="B",
            )
        self.assertFalse((self.root / "missing.db").exists())

    # --- delivery counting -----------------------------------------------

    def test_delivery_count_counts_lines_containing_token(self):
        self.assertEqual(F5.delivery_count("", "TOK"), 0)
        self.assertEqual(F5.delivery_count("a\nb\n", "TOK"), 0)
        self.assertEqual(F5.delivery_count("x TOK y\n", "TOK"), 1)
        # Two tokens on one line is still one delivery line.
        self.assertEqual(F5.delivery_count("TOK TOK\nTOK\n", "TOK"), 2)
        self.assertEqual(F5.delivery_count("TOK\r\nTOK", "TOK"), 2)

    # --- registration / CLI ----------------------------------------------

    def test_register_member_forwards_positional_arguments(self):
        i1 = mock.Mock()
        F5.register_member(
            i1,
            gate_repo=Path("/g"),
            team="t",
            agent="a",
            project=Path("/p"),
            role="sender",
            env={"E": "1"},
            mutation_log=Path("/m.jsonl"),
        )
        i1.register_fixture_member.assert_called_once_with(
            Path("/g"), "t", "a", Path("/p"), "sender", {"E": "1"},
            Path("/m.jsonl"),
        )

    def test_build_parser_requires_all_options(self):
        parser = F5.build_parser()
        argv = [
            "--run-id", "r", "--run-root", "/rr", "--gate-repo", "/g",
            "--artifact-dir", "/a", "--gate-team", "t",
            "--claude-config", "/c",
        ]
        args = parser.parse_args(argv)
        self.assertEqual(
            vars(args),
            {
                "run_id": "r",
                "run_root": "/rr",
                "gate_repo": "/g",
                "artifact_dir": "/a",
                "gate_team": "t",
                "claude_config": "/c",
            },
        )
        for index in range(0, len(argv), 2):
            partial = argv[:index] + argv[index + 2:]
            with self.subTest(missing=argv[index]):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(partial)

    # --- main ------------------------------------------------------------

    def run_main(self, side_effect):
        artifact_dir = self.root / "artifacts"
        argv = [
            "pilot-gate-f5.py", "--run-id", "run-m", "--run-root", "/rr",
            "--gate-repo", "/g", "--artifact-dir", str(artifact_dir),
            "--gate-team", "t", "--claude-config", "/c",
        ]
        stderr = io.StringIO()
        with mock.patch.object(F5.sys, "argv", argv), mock.patch.object(
            F5, "run_f5", side_effect=side_effect
        ) as run_mock, contextlib.redirect_stderr(stderr):
            rc = F5.main()
        return rc, stderr.getvalue(), artifact_dir / "F5" / "result.json", run_mock

    def test_main_returns_run_f5_result(self):
        for value in (0, 1, 2):
            with self.subTest(value=value):
                rc, _, result, run_mock = self.run_main([value])
                self.assertEqual(rc, value)
                self.assertFalse(result.exists())
                self.assertEqual(run_mock.call_args.args[0].run_id, "run-m")

    def test_main_keyboard_interrupt_returns_130(self):
        rc, _, result, _ = self.run_main(KeyboardInterrupt())
        self.assertEqual(rc, 130)
        self.assertFalse(result.exists())

    def test_main_expected_errors_record_unknown_observation_unavailable(self):
        for exc in (
            ValueError("v"),
            RuntimeError("r"),
            OSError("o"),
            sqlite3.OperationalError("s"),
            json.JSONDecodeError("j", "doc", 0),
        ):
            with self.subTest(exc=type(exc).__name__):
                rc, stderr, result, _ = self.run_main(exc)
                self.assertEqual(rc, 2)
                name = type(exc).__name__
                self.assertIn(f"pilot-gate-f5: {name}: ", stderr)
                self.assertEqual(
                    json.loads(result.read_text()),
                    {
                        "schemaVersion": 1,
                        "check": "F5",
                        "runId": "run-m",
                        "verdict": "unknown",
                        "reason": (
                            "harness_observation_unavailable:"
                            f"{name}:{exc}"
                        ),
                    },
                )

    def test_main_unexpected_errors_record_unknown_internal(self):
        rc, stderr, result, _ = self.run_main(KeyError("k"))
        self.assertEqual(rc, 2)
        self.assertIn("pilot-gate-f5: internal error: KeyError: ", stderr)
        self.assertEqual(
            json.loads(result.read_text())["reason"],
            "harness_internal:KeyError:'k'",
        )
        self.assertEqual(json.loads(result.read_text())["verdict"], "unknown")

    def test_main_still_returns_2_when_result_cannot_be_written(self):
        for exc in (RuntimeError("r"), KeyError("k")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(
                    F5, "atomic_json", side_effect=OSError("disk")
                ):
                    rc, _, result, _ = self.run_main(exc)
                self.assertEqual(rc, 2)
                self.assertFalse(result.exists())



class PilotGateF5RoundB(unittest.TestCase):
    """GateWatcher against real short-lived shell processes."""

    READY = 0.3

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        self.watchers = []
        patcher = mock.patch.object(F5, "WATCH_READY_SECONDS", self.READY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for watcher in self.watchers:
            proc = watcher.proc
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, F5.signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            if proc is not None:
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
        self._tmp.cleanup()

    def script(self, body: str, name: str = "watch.sh") -> Path:
        path = self.repo / "scripts" / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def wait_file(self, path: Path, timeout: float = 10.0) -> str:
        deadline = F5.time.monotonic() + timeout
        while F5.time.monotonic() < deadline:
            if path.exists():
                text = path.read_text()
                if text.endswith("\n"):
                    return text
            F5.time.sleep(0.02)
        self.fail(f"fixture never wrote {path}")

    def watcher(self, script: Path, *, env=None, artifact=None):
        watcher = F5.GateWatcher(
            watch_script=script,
            project=self.root / "project",
            recipient="gate_recipient",
            session_id="sess-1",
            env=env if env is not None else {"PATH": os.environ["PATH"]},
            artifact=artifact or (self.root / "art" / "watcher"),
        )
        self.watchers.append(watcher)
        return watcher

    def test_init_copies_env_and_starts_empty(self):
        env = {"PATH": os.environ["PATH"], "A": "1"}
        watcher = self.watcher(self.script("exit 0\n"), env=env)
        env["A"] = "changed"
        self.assertEqual(watcher.env["A"], "1")
        self.assertIsNone(watcher.proc)
        self.assertEqual(watcher.stdout_text(), "")
        self.assertEqual(watcher.stderr_text(), "")
        self.assertFalse(watcher.is_running())
        self.assertIsNone(watcher.wait_for_token("TOK", 0.1))
        watcher.pump(0)  # no process: no-op
        watcher.stop()  # no process: no-op, writes nothing
        self.assertFalse((self.root / "art" / "watcher").exists())

    def test_start_launches_argv_with_env_cwd_and_records_start_json(self):
        script = self.script(
            'echo "ARGS:$*"\n'
            'echo "INTERVAL:$AGMSG_WATCH_INTERVAL"\n'
            'echo "CWD:$(pwd -P)"\n'
            'echo "ERR-LINE" >&2\n'
            "echo BOOT-DONE\n"
            "exec sleep 30\n"
        )
        artifact = self.root / "art" / "watcher"
        env = {"PATH": os.environ["PATH"], "AGMSG_WATCH_INTERVAL": "99"}
        watcher = self.watcher(script, env=env, artifact=artifact)
        watcher.start()
        # Synchronize on output instead of assuming it arrived within
        # the ready window (process start latency varies under load).
        self.assertIs(watcher.wait_for_token("BOOT-DONE", 10), True)
        deadline = F5.time.monotonic() + 10
        while not watcher.stderr and F5.time.monotonic() < deadline:
            watcher.pump(0.1)
        watcher.persist()

        self.assertTrue(watcher.is_running())
        # The caller's env is not mutated by the interval override.
        self.assertEqual(watcher.env["AGMSG_WATCH_INTERVAL"], "99")
        out = watcher.stdout_text()
        project = self.root / "project"
        self.assertIn(
            f"ARGS:sess-1 {project} claude-code gate_recipient", out
        )
        self.assertIn("INTERVAL:1\n", out)
        self.assertIn(f"CWD:{self.repo}\n", out)
        self.assertNotIn("ERR-LINE", out)
        self.assertEqual(watcher.stderr_text(), "ERR-LINE\n")

        self.assertEqual(
            json.loads((artifact / "start.json").read_text()),
            {
                "schemaVersion": 1,
                "argv": [
                    str(script), "sess-1", str(project), "claude-code",
                    "gate_recipient",
                ],
                "project": str(project),
                "recipient": "gate_recipient",
                "sessionId": "sess-1",
            },
        )
        self.assertEqual(
            (artifact / "stdout.raw").read_bytes(), watcher.stdout
        )
        self.assertEqual(
            (artifact / "stderr.raw").read_bytes(), b"ERR-LINE\n"
        )

    def test_start_waits_for_ready_window(self):
        watcher = self.watcher(self.script("exec sleep 30\n"))
        started = F5.time.monotonic()
        watcher.start()
        self.assertGreaterEqual(F5.time.monotonic() - started, self.READY)

    def test_start_twice_is_rejected(self):
        watcher = self.watcher(self.script("exec sleep 30\n"))
        watcher.start()
        with self.assertRaisesRegex(RuntimeError, "watcher already started"):
            watcher.start()

    def test_start_fails_when_watcher_exits_during_startup(self):
        artifact = self.root / "art" / "watcher"
        watcher = self.watcher(
            self.script('echo "boot"\necho "bad" >&2\nexit 3\n'),
            artifact=artifact,
        )
        # The first exec of a freshly written script is occasionally
        # slow (observed ~0.37s), so a 0.3s window races the exit.
        # start() raises as soon as it sees the exit, so a long window
        # costs nothing when the code is correct.
        with mock.patch.object(
            F5, "WATCH_READY_SECONDS", 15.0
        ), self.assertRaisesRegex(
            RuntimeError, "gate watcher exited during startup"
        ):
            watcher.start()
        self.assertEqual(watcher.proc.returncode, 3)
        self.assertEqual((artifact / "stdout.raw").read_bytes(), b"boot\n")
        self.assertEqual((artifact / "stderr.raw").read_bytes(), b"bad\n")

    def test_wait_for_token_true_when_token_arrives(self):
        artifact = self.root / "art" / "watcher"
        watcher = self.watcher(
            self.script(
                "sleep 0.5\necho 'event AGMSG_TOK here'\nexec sleep 30\n"
            ),
            artifact=artifact,
        )
        watcher.start()
        self.assertIs(watcher.wait_for_token("AGMSG_TOK", 10), True)
        self.assertIn(
            b"AGMSG_TOK", (artifact / "stdout.raw").read_bytes()
        )

    def test_wait_for_token_ignores_token_on_stderr(self):
        watcher = self.watcher(
            self.script("echo AGMSG_TOK >&2\nexec sleep 30\n")
        )
        watcher.start()
        deadline = F5.time.monotonic() + 10
        while not watcher.stderr and F5.time.monotonic() < deadline:
            watcher.pump(0.1)
        # Positive control: the token really was emitted (on stderr).
        self.assertIn("AGMSG_TOK", watcher.stderr_text())
        self.assertIs(watcher.wait_for_token("AGMSG_TOK", 0.5), False)

    def test_wait_for_token_false_promptly_when_process_exits(self):
        watcher = self.watcher(
            self.script("sleep 0.6\necho other\nexit 0\n")
        )
        watcher.start()
        started = F5.time.monotonic()
        self.assertIs(watcher.wait_for_token("AGMSG_TOK", 15), False)
        # Exit is detected long before the 15s deadline.
        self.assertLess(F5.time.monotonic() - started, 5)
        self.assertIn("other", watcher.stdout_text())

    def test_wait_for_token_false_at_deadline_while_running(self):
        watcher = self.watcher(self.script("exec sleep 30\n"))
        watcher.start()
        started = F5.time.monotonic()
        self.assertIs(watcher.wait_for_token("AGMSG_TOK", 0.6), False)
        elapsed = F5.time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertLess(elapsed, 5)
        self.assertTrue(watcher.is_running())

    def test_wait_for_token_true_when_token_already_buffered(self):
        watcher = self.watcher(
            self.script("echo AGMSG_TOK\nexec sleep 30\n")
        )
        watcher.start()
        deadline = F5.time.monotonic() + 10
        while (
            "AGMSG_TOK" not in watcher.stdout_text()
            and F5.time.monotonic() < deadline
        ):
            watcher.pump(0.1)
        self.assertIn("AGMSG_TOK", watcher.stdout_text())
        # With a zero timeout the loop body never runs: the answer must
        # come from the already-collected buffer.
        self.assertIs(watcher.wait_for_token("AGMSG_TOK", 0.0), True)

    def test_settle_keeps_collecting_for_duration_and_persists(self):
        artifact = self.root / "art" / "watcher"
        watcher = self.watcher(
            self.script(
                "echo first\nsleep 0.4\necho second\nexec sleep 30\n"
            ),
            artifact=artifact,
        )
        watcher.start()
        self.assertIs(watcher.wait_for_token("first", 10), True)
        started = F5.time.monotonic()
        watcher.settle(1.2)
        self.assertGreaterEqual(F5.time.monotonic() - started, 1.2)
        self.assertEqual(watcher.stdout_text(), "first\nsecond\n")
        self.assertEqual(
            (artifact / "stdout.raw").read_bytes(), b"first\nsecond\n"
        )

    def test_stop_terminates_whole_process_group_and_records_stop_json(self):
        child_pid_file = self.root / "child.pid"
        artifact = self.root / "art" / "watcher"
        watcher = self.watcher(
            self.script(
                f"sleep 30 &\necho $! > '{child_pid_file}'\nwait\n"
            ),
            artifact=artifact,
        )
        watcher.start()
        child_pid = int(self.wait_file(child_pid_file))
        pid = watcher.proc.pid

        watcher.stop()

        self.assertFalse(watcher.is_running())
        self.assertEqual(watcher.proc.returncode, -F5.signal.SIGTERM)
        self.assertEqual(
            json.loads((artifact / "stop.json").read_text()),
            {
                "schemaVersion": 1,
                "pid": pid,
                "exitStatus": -F5.signal.SIGTERM,
                "runningAfterStop": False,
            },
        )
        # The background child in the watcher's own session is gone too.
        deadline = F5.time.monotonic() + 5
        while F5.time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            F5.time.sleep(0.05)
        else:
            self.fail("watcher child survived stop()")

    def test_stop_does_not_signal_processes_outside_its_group(self):
        outsider = F5.subprocess.Popen(
            ["sleep", "30"], start_new_session=True
        )
        try:
            watcher = self.watcher(self.script("exec sleep 30\n"))
            watcher.start()
            watcher.stop()
            self.assertIsNone(outsider.poll())
        finally:
            outsider.kill()
            outsider.wait(timeout=5)

    def test_stop_escalates_to_sigkill_when_sigterm_ignored(self):
        watcher = self.watcher(
            self.script(
                "trap '' TERM\n"
                f"echo armed > '{self.root / 'armed'}'\n"
                "while :; do sleep 0.1; done\n"
            )
        )
        watcher.start()
        self.wait_file(self.root / "armed")
        started = F5.time.monotonic()
        watcher.stop()
        self.assertFalse(watcher.is_running())
        self.assertEqual(watcher.proc.returncode, -F5.signal.SIGKILL)
        # One 3s grace period before SIGKILL, well under the hang guard.
        self.assertLess(F5.time.monotonic() - started, 8)

    def test_stop_after_exit_does_not_signal_and_records_status(self):
        artifact = self.root / "art" / "watcher"
        watcher = self.watcher(self.script("exec sleep 30\n"), artifact=artifact)
        watcher.start()
        os.killpg(watcher.proc.pid, F5.signal.SIGKILL)
        watcher.proc.wait(timeout=5)
        with mock.patch.object(F5.os, "killpg") as killpg:
            watcher.stop()
        killpg.assert_not_called()
        self.assertEqual(
            json.loads((artifact / "stop.json").read_text())["exitStatus"],
            -F5.signal.SIGKILL,
        )

    def test_stop_tolerates_process_already_gone_at_signal_time(self):
        watcher = self.watcher(self.script("exec sleep 30\n"))
        watcher.start()
        real_killpg = F5.os.killpg

        def vanish(pid, sig):
            real_killpg(pid, F5.signal.SIGKILL)
            raise ProcessLookupError

        with mock.patch.object(F5.os, "killpg", side_effect=vanish):
            watcher.stop()
        self.assertFalse(watcher.is_running())

    def test_pump_returns_quietly_on_select_error(self):
        watcher = self.watcher(self.script("exec sleep 30\n"))
        watcher.start()
        with mock.patch.object(F5.select, "select", side_effect=OSError):
            watcher.pump(0.1)
        with mock.patch.object(F5.select, "select", side_effect=ValueError):
            watcher.pump(0.1)

    def test_pump_skips_stream_read_errors(self):
        watcher = self.watcher(
            self.script("echo data\nexec sleep 30\n")
        )
        watcher.start()
        self.assertIs(watcher.wait_for_token("data", 10), True)
        before = bytes(watcher.stdout)
        with mock.patch.object(
            F5.select, "select",
            return_value=([watcher.proc.stdout], [], []),
        ), mock.patch.object(F5.os, "read", side_effect=OSError):
            watcher.pump(0)
        self.assertEqual(bytes(watcher.stdout), before)

    def test_text_decoding_replaces_invalid_utf8(self):
        watcher = self.watcher(self.script("exit 0\n"))
        watcher.stdout.extend(b"ok \xff\n")
        watcher.stderr.extend("é".encode() + b"\xfe")
        self.assertEqual(watcher.stdout_text(), "ok �\n")
        self.assertEqual(watcher.stderr_text(), "é�")

    def test_persist_creates_artifact_directory(self):
        artifact = self.root / "deep" / "art"
        watcher = self.watcher(self.script("exit 0\n"), artifact=artifact)
        watcher.stdout.extend(b"o")
        watcher.stderr.extend(b"e")
        watcher.persist()
        self.assertEqual((artifact / "stdout.raw").read_bytes(), b"o")
        self.assertEqual((artifact / "stderr.raw").read_bytes(), b"e")


if __name__ == "__main__":
    unittest.main()
