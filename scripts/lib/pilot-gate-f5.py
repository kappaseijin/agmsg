#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Any


PILOT_TYPE = "claude-code"
DELIVERY_TIMEOUT_SECONDS = 20.0
WATCH_POLL_INTERVAL_SECONDS = 1
WATCH_READY_SECONDS = 1.25
FAULT_OBSERVATION_SECONDS = 2.25
POST_DELIVERY_SETTLE_SECONDS = 2.25


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    with open(
        temporary,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as fh:
        json.dump(
            value,
            fh,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(
        path,
        "a",
        encoding="utf-8",
        newline="\n",
    ) as fh:
        fh.write(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def assertion(
    name: str,
    result: bool | None,
    detail: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "verdict": (
            "pass"
            if result is True
            else "fail"
            if result is False
            else "unknown"
        ),
        "detail": detail,
    }


def verdict_result(
    verdict: str,
) -> bool | None:
    """Lift a phase verdict into an assertion result, keeping unknown.

    "pass" -> True, "fail" -> False, anything else -> None (unknown).
    Comparing with == "pass" would fold unknown into fail.
    """
    if verdict == "pass":
        return True

    if verdict == "fail":
        return False

    return None


def verdict_from_assertions(
    checks: list[dict[str, Any]],
) -> str:
    if any(
        item["verdict"] == "fail"
        for item in checks
    ):
        return "fail"

    if any(
        item["verdict"] == "unknown"
        for item in checks
    ):
        return "unknown"

    return "pass"


def require_regular_executable(
    path: pathlib.Path,
) -> None:
    metadata = path.lstat()

    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(path, os.X_OK)
    ):
        raise RuntimeError(
            f"not regular executable: {path}"
        )


def safe_token(
    prefix: str,
    run_id: str,
) -> str:
    digest = hashlib.sha256(
        run_id.encode("utf-8")
    ).hexdigest()[:16]

    return f"{prefix}-{digest}"


def load_team_configs(
    gate_repo: pathlib.Path,
) -> dict[str, dict[str, Any]]:
    teams_dir = gate_repo / "teams"

    if (
        not teams_dir.is_dir()
        or teams_dir.is_symlink()
    ):
        raise RuntimeError(
            "teams directory unavailable"
        )

    result: dict[
        str,
        dict[str, Any],
    ] = {}

    for child in teams_dir.iterdir():
        if (
            not child.is_dir()
            or child.is_symlink()
        ):
            continue

        config_path = (
            child / "config.json"
        )

        if not config_path.is_file():
            continue

        value = read_json(
            config_path
        )

        if not isinstance(value, dict):
            raise RuntimeError(
                f"team config root invalid: "
                f"{config_path}"
            )

        name = value.get("name")

        if not isinstance(name, str) or not name:
            raise RuntimeError(
                f"team name invalid: "
                f"{config_path}"
            )

        if name in result:
            raise RuntimeError(
                f"duplicate team name: {name}"
            )

        result[name] = value

    return result


def team_agents(
    config: dict[str, Any],
) -> set[str] | None:
    agents = config.get("agents")

    if not isinstance(agents, dict):
        return None

    names: set[str] = set()

    for name in agents:
        if not isinstance(name, str):
            return None
        names.add(name)

    return names


def prove_containment(
    *,
    gate_repo: pathlib.Path,
    gate_team: str,
    sender: str,
    recipient: str,
) -> dict[str, Any]:
    configs = load_team_configs(
        gate_repo
    )

    gate_config = configs.get(
        gate_team
    )

    checks: list[
        dict[str, Any]
    ] = []

    if gate_config is None:
        checks.append(
            assertion(
                "gate-team-exists",
                False,
                gate_team,
            )
        )
        return {
            "verdict": "fail",
            "checks": checks,
            "liveTeamNames": sorted(
                configs
            ),
        }

    gate_agents = team_agents(
        gate_config
    )

    checks.append(
        assertion(
            "gate-team-agent-map-identifiable",
            gate_agents is not None,
            (
                sorted(gate_agents)
                if gate_agents is not None
                else None
            ),
        )
    )

    if gate_agents is None:
        return {
            "verdict": "unknown",
            "checks": checks,
            "liveTeamNames": sorted(
                name
                for name in configs
                if name != gate_team
            ),
        }

    checks.extend(
        [
            assertion(
                "sender-in-gate-team",
                sender in gate_agents,
                {
                    "team": gate_team,
                    "sender": sender,
                },
            ),
            assertion(
                "recipient-in-gate-team",
                recipient in gate_agents,
                {
                    "team": gate_team,
                    "recipient": recipient,
                },
            ),
            assertion(
                "sender-and-recipient-distinct",
                sender != recipient,
                {
                    "sender": sender,
                    "recipient": recipient,
                },
            ),
        ]
    )

    live_team_names = sorted(
        name
        for name in configs
        if name != gate_team
    )

    recipient_collisions: list[str] = []
    malformed_live_configs: list[str] = []

    for team_name in live_team_names:
        agents = team_agents(
            configs[team_name]
        )

        if agents is None:
            malformed_live_configs.append(
                team_name
            )
            continue

        if recipient in agents:
            recipient_collisions.append(
                team_name
            )

    checks.append(
        assertion(
            "live-team-agent-maps-identifiable",
            (
                len(
                    malformed_live_configs
                )
                == 0
            ),
            malformed_live_configs,
        )
    )

    checks.append(
        assertion(
            "recipient-name-absent-from-live-teams",
            (
                len(
                    recipient_collisions
                )
                == 0
            ),
            recipient_collisions,
        )
    )

    verdict = verdict_from_assertions(
        checks
    )

    return {
        "verdict": verdict,
        "checks": checks,
        "liveTeamNames":
            live_team_names,
        "recipientCollisions":
            recipient_collisions,
    }


def provider_argv_safe(
    *,
    argv: list[str],
    gate_team: str,
    sender: str,
    recipient: str,
    live_team_names: list[str],
) -> tuple[
    bool,
    dict[str, Any],
]:
    expected_prefix = [
        "message-send",
        gate_team,
        sender,
        recipient,
    ]

    prefix_ok = (
        argv[:4]
        == expected_prefix
    )

    live_hits = sorted(
        {
            team
            for team in live_team_names
            if team in argv
        }
    )

    return (
        prefix_ok
        and not live_hits,
        {
            "argv": argv,
            "expectedPrefix":
                expected_prefix,
            "liveTeamArgvHits":
                live_hits,
        },
    )


def provider_send(
    i1: Any,
    *,
    provider: pathlib.Path,
    gate_repo: pathlib.Path,
    env: dict[str, str],
    gate_team: str,
    sender: str,
    recipient: str,
    request_id: str,
    body: str,
    live_team_names: list[str],
    argv_log: pathlib.Path,
) -> dict[str, Any]:
    args = [
        "message-send",
        gate_team,
        sender,
        recipient,
        request_id,
        body,
    ]

    safe, detail = provider_argv_safe(
        argv=args,
        gate_team=gate_team,
        sender=sender,
        recipient=recipient,
        live_team_names=(
            live_team_names
        ),
    )

    append_jsonl(
        argv_log,
        {
            "kind": "provider",
            "safe": safe,
            **detail,
        },
    )

    if not safe:
        raise RuntimeError(
            "provider argv containment violation"
        )

    value = i1.provider_call(
        provider,
        args,
        gate_repo,
        env,
        None,
        False,
    )

    return value


def storage_message_observation(
    db: pathlib.Path,
    *,
    team: str,
    sender: str,
    recipient: str,
    message_id: str,
    body: str,
) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{db}?mode=ro",
        uri=True,
    )

    try:
        rows = connection.execute(
            """
            SELECT
              e.id,
              e.legacy_id,
              e.team,
              e.from_agent,
              e.to_agent,
              e.body,
              e.at
            FROM events AS e
            WHERE e.type = 'message_sent'
              AND e.team = ?
              AND e.from_agent = ?
              AND e.to_agent = ?
              AND e.id = ?
              AND e.body = ?
            ORDER BY e.seq
            """,
            (
                team,
                sender,
                recipient,
                message_id,
                body,
            ),
        ).fetchall()
    finally:
        connection.close()

    return {
        "count": len(rows),
        "rows": [
            {
                "messageId": row[0],
                "legacyId": row[1],
                "team": row[2],
                "from": row[3],
                "to": row[4],
                "body": row[5],
                "createdAt": row[6],
            }
            for row in rows
        ],
    }


def delivery_count(
    text: str,
    token: str,
) -> int:
    return sum(
        1
        for line in text.splitlines()
        if token in line
    )


class GateWatcher:
    def __init__(
        self,
        *,
        watch_script: pathlib.Path,
        project: pathlib.Path,
        recipient: str,
        session_id: str,
        env: dict[str, str],
        artifact: pathlib.Path,
    ):
        self.watch_script = (
            watch_script
        )
        self.project = project
        self.recipient = recipient
        self.session_id = session_id
        self.env = dict(env)
        self.artifact = artifact

        self.proc: (
            subprocess.Popen[bytes]
            | None
        ) = None
        self.stdout = bytearray()
        self.stderr = bytearray()

    def start(self) -> None:
        if self.proc is not None:
            raise RuntimeError(
                "watcher already started"
            )

        self.artifact.mkdir(
            parents=True,
            exist_ok=True,
        )

        env = dict(self.env)
        env["AGMSG_WATCH_INTERVAL"] = (
            str(
                WATCH_POLL_INTERVAL_SECONDS
            )
        )

        argv = [
            str(self.watch_script),
            self.session_id,
            str(self.project),
            PILOT_TYPE,
            self.recipient,
        ]

        atomic_json(
            self.artifact
            / "start.json",
            {
                "schemaVersion": 1,
                "argv": argv,
                "project":
                    str(self.project),
                "recipient":
                    self.recipient,
                "sessionId":
                    self.session_id,
            },
        )

        self.proc = subprocess.Popen(
            argv,
            cwd=str(
                self.watch_script
                .parent
                .parent
            ),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )

        deadline = (
            time.monotonic()
            + WATCH_READY_SECONDS
        )

        while (
            time.monotonic()
            < deadline
        ):
            self.pump(0.1)

            if (
                self.proc.poll()
                is not None
            ):
                self.pump(0)
                self.persist()
                raise RuntimeError(
                    "gate watcher exited "
                    "during startup"
                )

        self.persist()

    def pump(
        self,
        timeout: float,
    ) -> None:
        if self.proc is None:
            return

        streams = []

        if self.proc.stdout is not None:
            streams.append(
                self.proc.stdout
            )

        if self.proc.stderr is not None:
            streams.append(
                self.proc.stderr
            )

        if not streams:
            return

        try:
            ready, _, _ = select.select(
                streams,
                [],
                [],
                timeout,
            )
        except (OSError, ValueError):
            return

        for stream in ready:
            try:
                data = os.read(
                    stream.fileno(),
                    65536,
                )
            except OSError:
                continue

            if not data:
                continue

            if (
                self.proc.stdout
                is not None
                and stream.fileno()
                == self.proc.stdout.fileno()
            ):
                self.stdout.extend(
                    data
                )
            else:
                self.stderr.extend(
                    data
                )

    def stdout_text(self) -> str:
        return bytes(
            self.stdout
        ).decode(
            "utf-8",
            errors="replace",
        )

    def stderr_text(self) -> str:
        return bytes(
            self.stderr
        ).decode(
            "utf-8",
            errors="replace",
        )

    def wait_for_token(
        self,
        token: str,
        timeout: float,
    ) -> bool | None:
        if self.proc is None:
            return None

        deadline = (
            time.monotonic()
            + timeout
        )

        while (
            time.monotonic()
            < deadline
        ):
            self.pump(0.2)

            if delivery_count(
                self.stdout_text(),
                token,
            ) >= 1:
                self.persist()
                return True

            if (
                self.proc.poll()
                is not None
            ):
                self.pump(0)
                self.persist()
                return False

        self.persist()

        return (
            delivery_count(
                self.stdout_text(),
                token,
            )
            >= 1
        )

    def settle(
        self,
        seconds: float,
    ) -> None:
        deadline = (
            time.monotonic()
            + seconds
        )

        while (
            time.monotonic()
            < deadline
        ):
            self.pump(
                min(
                    0.2,
                    max(
                        0.0,
                        deadline
                        - time.monotonic(),
                    ),
                )
            )

        self.persist()

    def is_running(
        self,
    ) -> bool:
        return (
            self.proc is not None
            and self.proc.poll()
            is None
        )

    def stop(
        self,
    ) -> None:
        if self.proc is None:
            return

        pid = self.proc.pid

        if (
            self.proc.poll()
            is None
        ):
            try:
                os.killpg(
                    pid,
                    signal.SIGTERM,
                )
            except ProcessLookupError:
                pass

            try:
                self.proc.wait(
                    timeout=3
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(
                        pid,
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass

                self.proc.wait(
                    timeout=3
                )

        self.pump(0)
        self.persist()

        atomic_json(
            self.artifact
            / "stop.json",
            {
                "schemaVersion": 1,
                "pid": pid,
                "exitStatus":
                    self.proc.returncode,
                "runningAfterStop":
                    self.is_running(),
            },
        )

    def persist(
        self,
    ) -> None:
        self.artifact.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            self.artifact
            / "stdout.raw"
        ).write_bytes(
            bytes(self.stdout)
        )

        (
            self.artifact
            / "stderr.raw"
        ).write_bytes(
            bytes(self.stderr)
        )


def register_member(
    i1: Any,
    *,
    gate_repo: pathlib.Path,
    team: str,
    agent: str,
    project: pathlib.Path,
    role: str,
    env: dict[str, str],
    mutation_log: pathlib.Path,
) -> None:
    i1.register_fixture_member(
        gate_repo,
        team,
        agent,
        project,
        role,
        env,
        mutation_log,
    )


def run_f5(
    args: argparse.Namespace,
) -> int:
    script_dir = (
        pathlib.Path(__file__)
        .resolve()
        .parent
    )

    i1 = load_module(
        script_dir
        / "pilot-gate-i1.py",
        "pilot_gate_i1",
    )

    gate_repo = (
        pathlib.Path(
            args.gate_repo
        )
        .resolve(strict=True)
    )

    run_root = (
        pathlib.Path(
            args.run_root
        )
        .resolve(strict=True)
    )

    claude_config = (
        pathlib.Path(
            args.claude_config
        )
        .resolve(strict=True)
    )

    gate_repo.relative_to(
        run_root
    )

    artifact = (
        pathlib.Path(
            args.artifact_dir
        )
        .resolve(strict=False)
        / "F5"
    )

    artifact.mkdir(
        parents=True,
        exist_ok=True,
    )

    provider = (
        gate_repo
        / "scripts"
        / "p2-provider.sh"
    )
    watch_script = (
        gate_repo
        / "scripts"
        / "watch.sh"
    )
    join = (
        gate_repo
        / "scripts"
        / "join.sh"
    )

    for executable in (
        provider,
        watch_script,
        join,
    ):
        require_regular_executable(
            executable
        )

    env = i1.sanitize_env(
        os.environ
    )
    env["CLAUDE_CONFIG_DIR"] = (
        str(claude_config)
    )

    sender = safe_token(
        "agmsg_gate_f5_sender",
        args.run_id,
    )
    recipient = safe_token(
        "agmsg_gate_f5_recipient",
        args.run_id,
    )

    runtime = (
        gate_repo
        / ".agmsg-gate"
        / "f5"
    )
    runtime.mkdir(
        parents=True,
        exist_ok=True,
    )

    sender_project = (
        runtime / "sender"
    )
    recipient_project = (
        runtime / "recipient"
    )

    mutation_log = (
        artifact
        / "mutation-log.jsonl"
    )

    if mutation_log.exists():
        mutation_log.unlink()

    #
    # Before registering the recipient, prove that its generated
    # identity does not already exist in any team copied from the
    # source/live repository.
    #
    before_configs = load_team_configs(
        gate_repo
    )

    before_collisions = []

    for team_name, config in (
        before_configs.items()
    ):
        if team_name == args.gate_team:
            continue

        agents = team_agents(
            config
        )

        if agents is None:
            raise RuntimeError(
                "cannot identify live-team "
                f"agent map: {team_name}"
            )

        if recipient in agents:
            before_collisions.append(
                team_name
            )

    if before_collisions:
        atomic_json(
            artifact
            / "containment.json",
            {
                "schemaVersion": 1,
                "verdict": "fail",
                "reason":
                    "recipient_already_exists_"
                    "in_live_team",
                "recipient":
                    recipient,
                "collisions":
                    before_collisions,
            },
        )
        return 1

    register_member(
        i1,
        gate_repo=gate_repo,
        team=args.gate_team,
        agent=sender,
        project=sender_project,
        role="sender",
        env=env,
        mutation_log=mutation_log,
    )

    register_member(
        i1,
        gate_repo=gate_repo,
        team=args.gate_team,
        agent=recipient,
        project=recipient_project,
        role="recipient",
        env=env,
        mutation_log=mutation_log,
    )

    containment = prove_containment(
        gate_repo=gate_repo,
        gate_team=args.gate_team,
        sender=sender,
        recipient=recipient,
    )

    atomic_json(
        artifact
        / "containment.json",
        {
            "schemaVersion": 1,
            "check": "F5-containment",
            "runId":
                args.run_id,
            "gateTeam":
                args.gate_team,
            "sender":
                sender,
            "recipient":
                recipient,
            **containment,
        },
    )

    if containment[
        "verdict"
    ] != "pass":
        return (
            1
            if containment[
                "verdict"
            ] == "fail"
            else 2
        )

    live_team_names = (
        containment[
            "liveTeamNames"
        ]
    )

    argv_log = (
        artifact
        / "provider-argv.jsonl"
    )

    if argv_log.exists():
        argv_log.unlink()

    db = i1.storage_db(
        gate_repo,
        args.gate_team,
        env,
    )

    control_request_id = safe_token(
        "f5-control-request",
        args.run_id,
    )
    fault_request_id = safe_token(
        "f5-fault-request",
        args.run_id,
    )

    control_token = safe_token(
        "AGMSG_F5_CONTROL",
        args.run_id,
    )
    fault_token = safe_token(
        "AGMSG_F5_FAULT",
        args.run_id,
    )

    control_body = json.dumps(
        {
            "schemaVersion": 1,
            "kind":
                "f5-notification-control",
            "runId":
                args.run_id,
            "token":
                control_token,
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    fault_body = json.dumps(
        {
            "schemaVersion": 1,
            "kind":
                "f5-notification-fault",
            "runId":
                args.run_id,
            "token":
                fault_token,
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    watcher_session = safe_token(
        "agmsg-g4-f5-watch",
        args.run_id,
    )

    control_watcher = GateWatcher(
        watch_script=watch_script,
        project=recipient_project,
        recipient=recipient,
        session_id=watcher_session,
        env=env,
        artifact=(
            artifact
            / "control"
            / "watcher"
        ),
    )

    recovery_watcher: (
        GateWatcher | None
    ) = None

    control_result: dict[
        str,
        Any,
    ]
    fault_result: dict[
        str,
        Any,
    ]
    recovery_result: dict[
        str,
        Any,
    ]

    try:
        #
        # CONTROL
        #
        control_watcher.start()

        control_send = provider_send(
            i1,
            provider=provider,
            gate_repo=gate_repo,
            env=env,
            gate_team=args.gate_team,
            sender=sender,
            recipient=recipient,
            request_id=(
                control_request_id
            ),
            body=control_body,
            live_team_names=(
                live_team_names
            ),
            argv_log=argv_log,
        )

        control_message_id = (
            control_send.get(
                "messageId"
            )
        )

        control_queued = (
            control_send.get("state")
            == "queued"
            and control_send.get("team")
            == args.gate_team
            and control_send.get("from")
            == sender
            and control_send.get("to")
            == recipient
            and isinstance(
                control_message_id,
                str,
            )
            and bool(
                control_message_id
            )
        )

        control_observed = (
            control_watcher
            .wait_for_token(
                control_token,
                DELIVERY_TIMEOUT_SECONDS,
            )
        )

        control_watcher.settle(
            POST_DELIVERY_SETTLE_SECONDS
        )

        control_stdout = (
            control_watcher
            .stdout_text()
        )

        control_delivery_count = (
            delivery_count(
                control_stdout,
                control_token,
            )
        )

        if isinstance(
            control_message_id,
            str,
        ):
            control_storage = (
                storage_message_observation(
                    db,
                    team=args.gate_team,
                    sender=sender,
                    recipient=recipient,
                    message_id=(
                        control_message_id
                    ),
                    body=control_body,
                )
            )
        else:
            control_storage = {
                "count": 0,
                "rows": [],
            }

        control_checks = [
            assertion(
                "control-provider-queued",
                control_queued,
                control_send,
            ),
            assertion(
                "control-delivery-observed",
                control_observed,
                {
                    "token":
                        control_token,
                    "count":
                        control_delivery_count,
                },
            ),
            assertion(
                "control-message-identity-"
                "matches-storage",
                (
                    control_storage[
                        "count"
                    ]
                    == 1
                ),
                control_storage,
            ),
            assertion(
                "control-delivered-"
                "exactly-once",
                (
                    control_delivery_count
                    == 1
                ),
                control_delivery_count,
            ),
        ]

        control_verdict = (
            verdict_from_assertions(
                control_checks
            )
        )

        control_result = {
            "schemaVersion": 1,
            "case": "control",
            "verdict":
                control_verdict,
            "requestId":
                control_request_id,
            "messageId":
                control_message_id,
            "token":
                control_token,
            "provider":
                control_send,
            "storage":
                control_storage,
            "deliveryCount":
                control_delivery_count,
            "checks":
                control_checks,
        }

        atomic_json(
            artifact
            / "control"
            / "result.json",
            control_result,
        )

        if control_verdict != "pass":
            final = {
                "schemaVersion": 1,
                "check": "F5",
                "runId":
                    args.run_id,
                "verdict":
                    control_verdict,
                "reason":
                    "control_not_pass",
                "containment":
                    containment,
                "control":
                    control_result,
            }

            atomic_json(
                artifact
                / "result.json",
                final,
            )

            return (
                1
                if control_verdict
                == "fail"
                else 2
            )

        #
        # FAULT
        #
        # Stop only the child process this helper created.
        #
        control_watcher.stop()

        stopped_before_fault = (
            not control_watcher
            .is_running()
        )

        fault_send = provider_send(
            i1,
            provider=provider,
            gate_repo=gate_repo,
            env=env,
            gate_team=args.gate_team,
            sender=sender,
            recipient=recipient,
            request_id=(
                fault_request_id
            ),
            body=fault_body,
            live_team_names=(
                live_team_names
            ),
            argv_log=argv_log,
        )

        fault_message_id = (
            fault_send.get(
                "messageId"
            )
        )

        fault_queued = (
            fault_send.get("state")
            == "queued"
            and fault_send.get("team")
            == args.gate_team
            and fault_send.get("from")
            == sender
            and fault_send.get("to")
            == recipient
            and isinstance(
                fault_message_id,
                str,
            )
            and bool(
                fault_message_id
            )
        )

        #
        # No delivery consumer exists during this window.
        #
        time.sleep(
            FAULT_OBSERVATION_SECONDS
        )

        fault_count_before_recovery = (
            delivery_count(
                control_watcher
                .stdout_text(),
                fault_token,
            )
        )

        if isinstance(
            fault_message_id,
            str,
        ):
            fault_storage = (
                storage_message_observation(
                    db,
                    team=args.gate_team,
                    sender=sender,
                    recipient=recipient,
                    message_id=(
                        fault_message_id
                    ),
                    body=fault_body,
                )
            )
        else:
            fault_storage = {
                "count": 0,
                "rows": [],
            }

        fault_stale = (
            stopped_before_fault
            and fault_queued
            and fault_storage[
                "count"
            ]
            == 1
            and fault_count_before_recovery
            == 0
        )

        fault_checks = [
            assertion(
                "fault-watcher-stopped",
                stopped_before_fault,
                {
                    "running":
                        control_watcher
                        .is_running(),
                },
            ),
            assertion(
                "fault-provider-still-"
                "accepts-message",
                fault_queued,
                fault_send,
            ),
            assertion(
                "fault-message-persisted-"
                "exactly-once",
                (
                    fault_storage[
                        "count"
                    ]
                    == 1
                ),
                fault_storage,
            ),
            assertion(
                "fault-not-delivered-"
                "while-consumer-stopped",
                (
                    fault_count_before_recovery
                    == 0
                ),
                (
                    fault_count_before_recovery
                ),
            ),
            assertion(
                "fault-stale-indication-"
                "observable",
                fault_stale,
                {
                    "watcherStopped":
                        stopped_before_fault,
                    "queued":
                        fault_queued,
                    "storageCount":
                        fault_storage[
                            "count"
                        ],
                    "deliveryCount":
                        fault_count_before_recovery,
                },
            ),
        ]

        fault_verdict = (
            verdict_from_assertions(
                fault_checks
            )
        )

        fault_result = {
            "schemaVersion": 1,
            "case": "fault",
            "verdict":
                fault_verdict,
            "requestId":
                fault_request_id,
            "messageId":
                fault_message_id,
            "token":
                fault_token,
            "provider":
                fault_send,
            "storage":
                fault_storage,
            "deliveryCountBeforeRecovery":
                fault_count_before_recovery,
            "deliveryState":
                (
                    "stale/consumer_stopped"
                    if fault_stale
                    else "unproved"
                ),
            "checks":
                fault_checks,
        }

        atomic_json(
            artifact
            / "fault"
            / "result.json",
            fault_result,
        )

        if fault_verdict != "pass":
            final = {
                "schemaVersion": 1,
                "check": "F5",
                "runId":
                    args.run_id,
                "verdict":
                    fault_verdict,
                "reason":
                    "fault_not_pass",
                "containment":
                    containment,
                "control":
                    control_result,
                "fault":
                    fault_result,
            }

            atomic_json(
                artifact
                / "result.json",
                final,
            )

            return (
                1
                if fault_verdict
                == "fail"
                else 2
            )

        #
        # RECOVERY
        #
        # Reuse the same delivery identity. Do NOT send a new
        # replacement message: the pending fault message itself
        # must be delivered.
        #
        recovery_watcher = GateWatcher(
            watch_script=watch_script,
            project=recipient_project,
            recipient=recipient,
            session_id=watcher_session,
            env=env,
            artifact=(
                artifact
                / "recovery"
                / "watcher"
            ),
        )

        recovery_watcher.start()

        recovery_observed = (
            recovery_watcher
            .wait_for_token(
                fault_token,
                DELIVERY_TIMEOUT_SECONDS,
            )
        )

        recovery_watcher.settle(
            POST_DELIVERY_SETTLE_SECONDS
        )

        recovery_stdout = (
            recovery_watcher
            .stdout_text()
        )

        recovery_delivery_count = (
            delivery_count(
                recovery_stdout,
                fault_token,
            )
        )

        #
        # Verify the queued event was not duplicated by recovery.
        #
        if isinstance(
            fault_message_id,
            str,
        ):
            fault_storage_after = (
                storage_message_observation(
                    db,
                    team=args.gate_team,
                    sender=sender,
                    recipient=recipient,
                    message_id=(
                        fault_message_id
                    ),
                    body=fault_body,
                )
            )
        else:
            fault_storage_after = {
                "count": 0,
                "rows": [],
            }

        provider_log_records = []

        try:
            with open(
                argv_log,
                "r",
                encoding="utf-8",
            ) as fh:
                for line in fh:
                    if line.strip():
                        provider_log_records.append(
                            json.loads(
                                line
                            )
                        )
        except Exception:
            provider_log_records = None

        if provider_log_records is None:
            no_live_team_argv: (
                bool | None
            ) = None
        else:
            no_live_team_argv = all(
                (
                    isinstance(
                        item,
                        dict,
                    )
                    and item.get("safe")
                    is True
                    and not (
                        item.get(
                            "liveTeamArgvHits"
                        )
                        or []
                    )
                )
                for item
                in provider_log_records
            )

        recovery_checks = [
            assertion(
                "recovery-same-"
                "watch-session-id",
                (
                    recovery_watcher
                    .session_id
                    == watcher_session
                ),
                watcher_session,
            ),
            assertion(
                "recovery-pending-message-"
                "observed",
                recovery_observed,
                {
                    "messageId":
                        fault_message_id,
                    "token":
                        fault_token,
                },
            ),
            assertion(
                "recovery-same-message-"
                "identity",
                (
                    fault_storage_after[
                        "count"
                    ]
                    == 1
                ),
                fault_storage_after,
            ),
            assertion(
                "recovery-delivered-"
                "exactly-once",
                (
                    recovery_delivery_count
                    == 1
                ),
                recovery_delivery_count,
            ),
            assertion(
                "recovery-duplicate-"
                "storage-write-zero",
                (
                    fault_storage_after[
                        "count"
                    ]
                    == 1
                ),
                {
                    "persistentWrites":
                        fault_storage_after[
                            "count"
                        ],
                    "duplicateWrites":
                        max(
                            0,
                            fault_storage_after[
                                "count"
                            ]
                            - 1,
                        ),
                },
            ),
            assertion(
                "all-provider-destinations-"
                "inside-disposable-team",
                no_live_team_argv,
                provider_log_records,
            ),
        ]

        recovery_verdict = (
            verdict_from_assertions(
                recovery_checks
            )
        )

        recovery_result = {
            "schemaVersion": 1,
            "case": "recovery",
            "verdict":
                recovery_verdict,
            "messageId":
                fault_message_id,
            "token":
                fault_token,
            "watchSessionId":
                watcher_session,
            "deliveryCount":
                recovery_delivery_count,
            "storage":
                fault_storage_after,
            "checks":
                recovery_checks,
        }

        atomic_json(
            artifact
            / "recovery"
            / "result.json",
            recovery_result,
        )

        final_checks = [
            assertion(
                "containment-pass",
                verdict_result(
                    containment[
                        "verdict"
                    ]
                ),
                containment[
                    "verdict"
                ],
            ),
            assertion(
                "control-delivered",
                verdict_result(
                    control_verdict
                ),
                control_verdict,
            ),
            assertion(
                "fault-not-delivered",
                verdict_result(
                    fault_verdict
                ),
                fault_verdict,
            ),
            assertion(
                "recovery-delivered-"
                "exactly-once",
                verdict_result(
                    recovery_verdict
                ),
                recovery_verdict,
            ),
            assertion(
                "control-and-fault-"
                "message-ids-distinct",
                (
                    isinstance(
                        control_message_id,
                        str,
                    )
                    and isinstance(
                        fault_message_id,
                        str,
                    )
                    and control_message_id
                    != fault_message_id
                ),
                {
                    "controlMessageId":
                        control_message_id,
                    "faultMessageId":
                        fault_message_id,
                },
            ),
        ]

        final_verdict = (
            verdict_from_assertions(
                final_checks
            )
        )

        final = {
            "schemaVersion": 1,
            "check": "F5",
            "runId":
                args.run_id,
            "verdict":
                final_verdict,
            "faultMethod":
                "gate-owned-recipient-"
                "watcher-stopped",
            "gateTeam":
                args.gate_team,
            "sender":
                sender,
            "recipient":
                recipient,
            "containment":
                containment,
            "control":
                control_result,
            "fault":
                fault_result,
            "recovery":
                recovery_result,
            "checks":
                final_checks,
        }

        atomic_json(
            artifact
            / "result.json",
            final,
        )

        if final_verdict == "pass":
            return 0

        if final_verdict == "fail":
            return 1

        return 2

    finally:
        try:
            control_watcher.stop()
        except Exception:
            pass

        if recovery_watcher is not None:
            try:
                recovery_watcher.stop()
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue #396 F5 notification "
            "delivery integration gate"
        )
    )

    parser.add_argument(
        "--run-id",
        required=True,
    )

    parser.add_argument(
        "--run-root",
        required=True,
    )

    parser.add_argument(
        "--gate-repo",
        required=True,
    )

    parser.add_argument(
        "--artifact-dir",
        required=True,
    )

    parser.add_argument(
        "--gate-team",
        required=True,
    )

    parser.add_argument(
        "--claude-config",
        required=True,
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        return run_f5(args)

    except KeyboardInterrupt:
        return 130

    except (
        ValueError,
        RuntimeError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(
            "pilot-gate-f5: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        try:
            artifact = (
                pathlib.Path(
                    args.artifact_dir
                )
                / "F5"
            )

            atomic_json(
                artifact
                / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F5",
                    "runId":
                        args.run_id,
                    "verdict":
                        "unknown",
                    "reason": (
                        "harness_observation_"
                        "unavailable:"
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    ),
                },
            )
        except Exception:
            pass

        return 2

    except Exception as exc:
        print(
            "pilot-gate-f5: internal error: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        try:
            artifact = (
                pathlib.Path(
                    args.artifact_dir
                )
                / "F5"
            )

            atomic_json(
                artifact
                / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F5",
                    "runId":
                        args.run_id,
                    "verdict":
                        "unknown",
                    "reason": (
                        "harness_internal:"
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    ),
                },
            )
        except Exception:
            pass

        return 2


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
