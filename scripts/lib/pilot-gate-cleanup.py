#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Any, Iterable, NoReturn


EX_PASS = 0
EX_FAIL = 1
EX_UNKNOWN = 2
EX_USAGE = 64

CHECKS = ("N1", "I1", "F1", "F2", "F3", "F4", "F5")
SOURCE_BY_CHECK = {
    "N1": "binding",
    "I1": "claude-transcript",
    "F1": "broker-response",
    "F2": "claude-transcript",
    "F3": "pilot-collector",
    "F4": "monotonic-clock",
    "F5": "provider-readback",
}
# Live PM deny-probe artifacts compared before/after (runbook §8.2, §36).
# These are exactly what run_live_pm_control() in pilot-gate-runner.sh and
# the isolation helper's record-live-control write. Raw files are compared
# byte for byte, never normalized; control.json only on the fields whose
# value is fixed by that contract ("phase" and "observedAt" differ between
# the two sides by construction and are not compared).
LIVE_RAW_FILES = (
    "input.raw",
    "stdout.raw",
    "stderr.raw",
)
LIVE_CONTROL_FIELDS = (
    "schemaVersion",
    "inputSha256",
    "stdoutSha256",
    "stderrSha256",
    "exitStatus",
    "guardSha256",
)


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


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
    with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
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
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
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


def verdict_code(verdict: str) -> int:
    if verdict == "pass":
        return EX_PASS
    if verdict == "fail":
        return EX_FAIL
    return EX_UNKNOWN


def aggregate_verdict(values: Iterable[str]) -> str:
    items = list(values)
    if any(value == "fail" for value in items):
        return "fail"
    if any(value != "pass" for value in items):
        return "unknown"
    return "pass"


def safe_absolute(path: str | os.PathLike[str]) -> pathlib.Path:
    return pathlib.Path(path).expanduser().absolute()


def is_within(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.absolute().relative_to(parent.absolute())
        return True
    except ValueError:
        return False


def require_disposable_layout(
    *,
    run_root: pathlib.Path,
    gate_repo: pathlib.Path,
    gate_home: pathlib.Path,
    xdg_config: pathlib.Path,
    xdg_cache: pathlib.Path,
    xdg_data: pathlib.Path,
    xdg_state: pathlib.Path,
    claude_config: pathlib.Path,
    artifact_dir: pathlib.Path,
) -> None:
    if run_root == pathlib.Path("/"):
        raise RuntimeError("refusing root run directory")

    if len(run_root.parts) < 3:
        raise RuntimeError("run root is unexpectedly shallow")

    disposable = (
        gate_repo,
        gate_home,
        xdg_config,
        xdg_cache,
        xdg_data,
        xdg_state,
        claude_config,
    )

    for candidate in disposable:
        if not is_within(candidate, run_root):
            raise RuntimeError(
                f"disposable path escapes run root: {candidate}"
            )

    if is_within(artifact_dir, run_root):
        raise RuntimeError(
            "artifact directory is inside disposable run root"
        )

    if is_within(run_root, artifact_dir):
        raise RuntimeError(
            "run root is inside artifact directory"
        )


class CommandRecorder:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.counter = 0

    def run(
        self,
        label: str,
        argv: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str] | None:
        self.counter += 1
        directory = (
            self.root
            / f"{self.counter:03d}-{label}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        started_wall = utc_now()
        started_mono = time.monotonic()

        atomic_json(
            directory / "command.json",
            {
                "schemaVersion": 1,
                "argv": argv,
                "cwd": str(cwd) if cwd is not None else None,
                "startedAt": started_wall,
            },
        )

        try:
            result = subprocess.run(
                argv,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            finished_mono = time.monotonic()
            (directory / "stdout.raw").write_text(
                exc.stdout if isinstance(exc.stdout, str) else "",
                encoding="utf-8",
            )
            (directory / "stderr.raw").write_text(
                exc.stderr if isinstance(exc.stderr, str) else "",
                encoding="utf-8",
            )
            (directory / "exit-status").write_text(
                "timeout\n",
                encoding="utf-8",
            )
            atomic_json(
                directory / "timing.json",
                {
                    "schemaVersion": 1,
                    "startedAt": started_wall,
                    "finishedAt": utc_now(),
                    "elapsedMonotonic":
                        finished_mono - started_mono,
                },
            )
            return None

        finished_mono = time.monotonic()

        (directory / "stdout.raw").write_text(
            result.stdout,
            encoding="utf-8",
        )
        (directory / "stderr.raw").write_text(
            result.stderr,
            encoding="utf-8",
        )
        (directory / "exit-status").write_text(
            f"{result.returncode}\n",
            encoding="utf-8",
        )
        atomic_json(
            directory / "timing.json",
            {
                "schemaVersion": 1,
                "startedAt": started_wall,
                "finishedAt": utc_now(),
                "elapsedMonotonic":
                    finished_mono - started_mono,
            },
        )

        return result


def process_table(
    recorder: CommandRecorder,
) -> list[dict[str, Any]] | None:
    result = recorder.run(
        "ps",
        ["ps", "-axo", "pid=,ppid=,command="],
        timeout=15,
    )
    if result is None or result.returncode != 0:
        return None

    rows: list[dict[str, Any]] = []

    for line in result.stdout.splitlines():
        match = re.match(
            r"^\s*(\d+)\s+(\d+)\s+(.*)$",
            line,
        )
        if match is None:
            continue

        rows.append(
            {
                "pid": int(match.group(1)),
                "ppid": int(match.group(2)),
                "command": match.group(3),
            }
        )

    return rows


def ancestor_pids(
    rows: list[dict[str, Any]],
) -> set[int]:
    by_pid = {
        int(row["pid"]): int(row["ppid"])
        for row in rows
    }

    result = {os.getpid()}
    current = os.getpid()

    while current in by_pid:
        parent = by_pid[current]
        if parent <= 1 or parent in result:
            break
        result.add(parent)
        current = parent

    return result


def owned_processes(
    rows: list[dict[str, Any]],
    run_root: pathlib.Path,
) -> list[dict[str, Any]]:
    token = str(run_root)
    excluded = ancestor_pids(rows)

    return [
        row
        for row in rows
        if int(row["pid"]) not in excluded
        and token in str(row["command"])
    ]


def terminate_owned_processes(
    recorder: CommandRecorder,
    run_root: pathlib.Path,
    artifact: pathlib.Path,
) -> tuple[str, list[dict[str, Any]]]:
    before = process_table(recorder)

    if before is None:
        return "unknown", []

    targets = owned_processes(
        before,
        run_root,
    )

    atomic_json(
        artifact / "processes-before.json",
        {
            "schemaVersion": 1,
            "runRoot": str(run_root),
            "targets": targets,
        },
    )

    for row in targets:
        pid = int(row["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass

    deadline = time.monotonic() + 4.0

    while time.monotonic() < deadline:
        current = process_table(recorder)
        if current is None:
            break

        remaining = owned_processes(
            current,
            run_root,
        )
        if not remaining:
            break

        time.sleep(0.2)

    current = process_table(recorder)

    if current is None:
        return "unknown", targets

    remaining = owned_processes(
        current,
        run_root,
    )

    for row in remaining:
        pid = int(row["pid"])
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass

    if remaining:
        time.sleep(0.2)

    final = process_table(recorder)

    if final is None:
        return "unknown", targets

    final_remaining = owned_processes(
        final,
        run_root,
    )

    atomic_json(
        artifact / "processes-after.json",
        {
            "schemaVersion": 1,
            "runRoot": str(run_root),
            "remaining": final_remaining,
        },
    )

    return (
        "pass" if not final_remaining else "fail",
        targets,
    )


def registration_rows(
    gate_repo: pathlib.Path,
    run_root: pathlib.Path,
) -> tuple[
    list[dict[str, str]],
    list[str],
]:
    teams_root = gate_repo / "teams"

    if not teams_root.is_dir():
        return [], []

    rows: list[dict[str, str]] = []
    errors: list[str] = []

    for config_path in teams_root.glob(
        "*/config.json"
    ):
        if (
            not config_path.is_file()
            or config_path.is_symlink()
        ):
            continue

        try:
            value = read_json(config_path)
        except Exception as exc:
            errors.append(
                f"{config_path}:{type(exc).__name__}"
            )
            continue

        if not isinstance(value, dict):
            errors.append(
                f"{config_path}:root_not_object"
            )
            continue

        team = value.get("name")
        agents = value.get("agents")

        if not isinstance(team, str) or not isinstance(
            agents,
            dict,
        ):
            errors.append(
                f"{config_path}:schema_unidentified"
            )
            continue

        for agent, agent_value in agents.items():
            if (
                not isinstance(agent, str)
                or not isinstance(
                    agent_value,
                    dict,
                )
            ):
                errors.append(
                    f"{config_path}:agent_schema_unidentified"
                )
                continue

            registrations = agent_value.get(
                "registrations"
            )

            if isinstance(registrations, list):
                reg_values = registrations
            else:
                reg_values = [
                    {
                        "type":
                            agent_value.get("type"),
                        "project":
                            agent_value.get("project"),
                    }
                ]

            for registration in reg_values:
                if not isinstance(
                    registration,
                    dict,
                ):
                    errors.append(
                        f"{config_path}:{agent}:registration_invalid"
                    )
                    continue

                project = registration.get(
                    "project"
                )
                kind = registration.get(
                    "type"
                )

                if (
                    not isinstance(project, str)
                    or not isinstance(kind, str)
                    or not project
                    or not kind
                ):
                    continue

                project_path = safe_absolute(
                    project
                )

                if is_within(
                    project_path,
                    run_root,
                ):
                    rows.append(
                        {
                            "team": team,
                            "agent": agent,
                            "project": str(
                                project_path
                            ),
                            "type": kind,
                        }
                    )

    return rows, errors


def disposable_teams(
    registrations: list[dict[str, str]],
    gate_team: str,
) -> list[str]:
    return sorted(
        {
            gate_team,
            *(
                row["team"]
                for row in registrations
            ),
        }
    )


def release_message_claims(
    *,
    i1: Any,
    gate_repo: pathlib.Path,
    teams: list[str],
    env: dict[str, str],
    recorder: CommandRecorder,
    artifact: pathlib.Path,
) -> tuple[str, list[dict[str, Any]]]:
    provider = (
        gate_repo
        / "scripts"
        / "p2-provider.sh"
    )

    if not provider.is_file():
        return "unknown", []

    claims: list[dict[str, Any]] = []
    read_errors: list[str] = []
    notes: list[str] = []

    seen_db_team: set[
        tuple[str, str]
    ] = set()

    for team in teams:
        try:
            db = pathlib.Path(
                i1.storage_db(
                    gate_repo,
                    team,
                    env,
                )
            )
        except Exception as exc:
            read_errors.append(
                f"{team}:storage:{type(exc).__name__}"
            )
            continue

        key = (str(db), team)
        if key in seen_db_team:
            continue
        seen_db_team.add(key)

        if not db.is_file():
            continue

        # Decide from the schema before joining (#427). init-db.sh creates
        # messages and message_claims; the sqlite driver creates events on
        # the first send. So a team that has never sent anything has no
        # events table, which is a decidable state (nothing to release), not
        # a failed observation. A store without messages was never
        # initialised and stays unknown. A missing table is never inferred
        # from an OperationalError.
        try:
            connection = sqlite3.connect(
                f"file:{db}?mode=ro",
                uri=True,
            )
            try:
                tables = {
                    name
                    for (name,) in connection.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                connection.close()
        except sqlite3.Error as exc:
            read_errors.append(
                f"{team}:claims:{type(exc).__name__}:{exc}"
            )
            continue

        if "messages" not in tables:
            read_errors.append(
                f"{team}:claims:messages_table_missing"
            )
            continue

        if "message_claims" not in tables:
            read_errors.append(
                f"{team}:claims:message_claims_table_missing"
            )
            continue

        if "events" not in tables:
            notes.append(
                f"{team}:claims:no_events_table"
            )
            continue

        try:
            connection = sqlite3.connect(
                f"file:{db}?mode=ro",
                uri=True,
            )
            rows = connection.execute(
                """
                SELECT
                  e.id,
                  c.owner,
                  m.team,
                  c.message_id
                FROM message_claims AS c
                JOIN messages AS m
                  ON m.id = c.message_id
                JOIN events AS e
                  ON e.legacy_id = m.id
                 AND e.type = 'message_sent'
                 AND e.team = m.team
                WHERE m.team = ?
                ORDER BY c.message_id
                """,
                (team,),
            ).fetchall()
            connection.close()
        except sqlite3.Error as exc:
            read_errors.append(
                f"{team}:claims:{type(exc).__name__}:{exc}"
            )
            continue

        for message_uuid, owner, row_team, legacy_id in rows:
            if not all(
                isinstance(item, str) and item
                for item in (
                    message_uuid,
                    owner,
                    row_team,
                )
            ):
                read_errors.append(
                    f"{team}:claim_row_unidentified:{legacy_id}"
                )
                continue

            claims.append(
                {
                    "team": row_team,
                    "messageId": message_uuid,
                    "owner": owner,
                    "legacyId": legacy_id,
                }
            )

    atomic_json(
        artifact / "claims-before.json",
        {
            "schemaVersion": 1,
            "claims": claims,
            "readErrors": read_errors,
            "notes": notes,
        },
    )

    release_failures: list[
        dict[str, Any]
    ] = []

    for index, claim in enumerate(claims):
        result = recorder.run(
            f"message-release-{index:03d}",
            [
                str(provider),
                "message-release",
                claim["team"],
                claim["messageId"],
                claim["owner"],
            ],
            cwd=gate_repo,
            env=env,
        )

        if (
            result is None
            or result.returncode != 0
        ):
            release_failures.append(
                claim
            )

    remaining: list[
        dict[str, Any]
    ] = []
    verify_errors: list[str] = []

    for team in teams:
        try:
            db = pathlib.Path(
                i1.storage_db(
                    gate_repo,
                    team,
                    env,
                )
            )
        except Exception as exc:
            verify_errors.append(
                f"{team}:storage:{type(exc).__name__}"
            )
            continue

        if not db.is_file():
            continue

        try:
            connection = sqlite3.connect(
                f"file:{db}?mode=ro",
                uri=True,
            )
            rows = connection.execute(
                """
                SELECT
                  c.message_id,
                  c.owner
                FROM message_claims AS c
                JOIN messages AS m
                  ON m.id = c.message_id
                WHERE m.team = ?
                ORDER BY c.message_id
                """,
                (team,),
            ).fetchall()
            connection.close()
        except sqlite3.Error as exc:
            verify_errors.append(
                f"{team}:verify:{type(exc).__name__}:{exc}"
            )
            continue

        for legacy_id, owner in rows:
            remaining.append(
                {
                    "team": team,
                    "legacyId": legacy_id,
                    "owner": owner,
                }
            )

    atomic_json(
        artifact / "claims-after.json",
        {
            "schemaVersion": 1,
            "remaining": remaining,
            "releaseFailures":
                release_failures,
            "verifyErrors":
                verify_errors,
        },
    )

    if remaining or release_failures:
        return "fail", claims

    if read_errors or verify_errors:
        return "unknown", claims

    return "pass", claims


def reset_registrations(
    *,
    gate_repo: pathlib.Path,
    run_root: pathlib.Path,
    recorder: CommandRecorder,
    env: dict[str, str],
    artifact: pathlib.Path,
) -> tuple[
    str,
    list[dict[str, str]],
]:
    reset_script = (
        gate_repo
        / "scripts"
        / "reset.sh"
    )

    registrations, errors = (
        registration_rows(
            gate_repo,
            run_root,
        )
    )

    atomic_json(
        artifact
        / "registrations-before.json",
        {
            "schemaVersion": 1,
            "registrations":
                registrations,
            "parseErrors":
                errors,
        },
    )

    failures: list[
        dict[str, str]
    ] = []

    # reset.sh removes the same project/type/agent tuple
    # across all teams, so de-duplicate those tuples.
    unique: dict[
        tuple[str, str, str],
        dict[str, str],
    ] = {}

    for row in registrations:
        key = (
            row["project"],
            row["type"],
            row["agent"],
        )
        unique.setdefault(
            key,
            row,
        )

    for index, row in enumerate(
        unique.values()
    ):
        argv = [
            "bash",
            str(reset_script),
            "--no-resolve",
            row["project"],
            row["type"],
            row["agent"],
        ]

        # Do not pass a session id here. reset.sh applies the same
        # project/type/agent tuple across all matching teams; one session id
        # could belong to only one of several disposable pilot generations.
        # Message claims are released separately through p2-provider.sh and
        # any remaining run-scoped lock bytes are removed with RUN_ROOT.
        result = recorder.run(
            f"reset-registration-{index:03d}",
            argv,
            cwd=gate_repo,
            env={
                **env,
                "AGMSG_RESOLVE_PROJECT": "0",
            },
        )

        if (
            result is None
            or result.returncode != 0
        ):
            failures.append(row)

    remaining, verify_errors = (
        registration_rows(
            gate_repo,
            run_root,
        )
    )

    atomic_json(
        artifact
        / "registrations-after.json",
        {
            "schemaVersion": 1,
            "remaining":
                remaining,
            "resetFailures":
                failures,
            "parseErrors":
                verify_errors,
        },
    )

    if remaining or failures:
        return "fail", registrations

    if errors or verify_errors:
        return "unknown", registrations

    return "pass", registrations


def copy_file_fsync(
    source: pathlib.Path,
    destination: pathlib.Path,
) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(source, "rb") as src:
        with open(
            destination,
            "wb",
        ) as dst:
            shutil.copyfileobj(
                src,
                dst,
                length=1024 * 1024,
            )
            dst.flush()
            os.fsync(dst.fileno())


def snapshot_tree(
    source: pathlib.Path,
    destination: pathlib.Path,
    *,
    suffixes: set[str] | None = None,
) -> list[str]:
    copied: list[str] = []

    if not source.is_dir():
        return copied

    for path in source.rglob("*"):
        try:
            if (
                not path.is_file()
                or path.is_symlink()
            ):
                continue
        except OSError:
            continue

        if (
            suffixes is not None
            and path.suffix
            not in suffixes
        ):
            continue

        relative = path.relative_to(
            source
        )
        target = destination / relative

        copy_file_fsync(
            path,
            target,
        )

        copied.append(
            str(target)
        )

    return copied


def snapshot_evidence(
    *,
    gate_repo: pathlib.Path,
    claude_config: pathlib.Path,
    artifact_dir: pathlib.Path,
) -> dict[str, Any]:
    destination = (
        artifact_dir
        / "pre-cleanup"
    )

    copied: list[str] = []

    copied.extend(
        snapshot_tree(
            gate_repo
            / "run"
            / "pilot",
            destination
            / "run-pilot",
        )
    )

    copied.extend(
        snapshot_tree(
            gate_repo
            / ".agmsg-gate",
            destination
            / "agmsg-gate",
        )
    )

    copied.extend(
        snapshot_tree(
            claude_config,
            destination
            / "claude-transcripts",
            suffixes={
                ".jsonl",
                ".log",
                ".txt",
            },
        )
    )

    record = {
        "schemaVersion": 1,
        "copiedCount":
            len(copied),
        "files":
            copied,
        "observedAt":
            utc_now(),
    }

    atomic_json(
        destination
        / "snapshot.json",
        record,
    )

    return record


def symlink_components(
    path: pathlib.Path,
    run_root: pathlib.Path,
) -> tuple[list[str], str | None]:
    """lstat every existing component from run_root down to path.

    Returns (symlinks, error). symlinks lists each component that is a
    symbolic link, the run root and the target included. Walking stops
    at the first missing component: nothing below it can exist. Any
    other lstat failure is returned as error (the caller cannot prove
    the path is free of links).
    """
    relative = path.absolute().relative_to(
        run_root.absolute()
    )

    components = [run_root]
    current = run_root

    for part in relative.parts:
        current = current / part
        components.append(current)

    found: list[str] = []

    for component in components:
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            break
        except OSError as exc:
            return found, (
                f"lstat_failed:{type(exc).__name__}:{component}"
            )

        if stat.S_ISLNK(metadata.st_mode):
            found.append(str(component))

    return found, None


def remove_tree_safely(
    path: pathlib.Path,
    run_root: pathlib.Path,
    evidence: dict[str, Any] | None = None,
) -> str:
    """Remove path, which must lie inside run_root, without leaving it.

    Fail-closed (Issue #400): the pilot processes under test can write
    inside run_root, so any symbolic link between run_root and the
    target (both included) stops the removal with "unknown" and leaves
    everything in place. The resolved target must also lie inside the
    resolved run root. Nothing is partially removed.
    """
    if evidence is None:
        evidence = {}

    evidence["symlinkComponents"] = []

    if not is_within(
        path,
        run_root,
    ):
        evidence["reason"] = "path_outside_run_root"
        return "fail"

    if path == run_root:
        evidence["reason"] = "path_is_run_root"
        return "fail"

    links, error = symlink_components(
        path,
        run_root,
    )
    evidence["symlinkComponents"] = links

    if error is not None:
        evidence["reason"] = error
        return "unknown"

    if links:
        evidence["reason"] = "symlink_in_removal_path"
        return "unknown"

    if not os.path.lexists(path):
        return "pass"

    try:
        resolved_path = path.resolve(strict=True)
        resolved_root = run_root.resolve(strict=True)
    except OSError as exc:
        evidence["reason"] = (
            f"resolve_failed:{type(exc).__name__}"
        )
        return "unknown"

    if (
        resolved_path == resolved_root
        or not is_within(
            resolved_path,
            resolved_root,
        )
    ):
        evidence["reason"] = "resolved_path_escapes_run_root"
        evidence["resolvedPath"] = str(resolved_path)
        return "unknown"

    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        evidence["reason"] = "remove_failed"
        return "fail"

    return (
        "pass"
        if not os.path.lexists(path)
        else "fail"
    )


def cleanup_run(
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
        "pilot_gate_i1_cleanup",
    )

    run_root = safe_absolute(
        args.run_root
    )
    gate_repo = safe_absolute(
        args.gate_repo
    )
    gate_home = safe_absolute(
        args.gate_home
    )
    xdg_config = safe_absolute(
        args.xdg_config
    )
    xdg_cache = safe_absolute(
        args.xdg_cache
    )
    xdg_data = safe_absolute(
        args.xdg_data
    )
    xdg_state = safe_absolute(
        args.xdg_state
    )
    claude_config = safe_absolute(
        args.claude_config
    )
    artifact_dir = safe_absolute(
        args.artifact_dir
    )

    artifact_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    require_disposable_layout(
        run_root=run_root,
        gate_repo=gate_repo,
        gate_home=gate_home,
        xdg_config=xdg_config,
        xdg_cache=xdg_cache,
        xdg_data=xdg_data,
        xdg_state=xdg_state,
        claude_config=claude_config,
        artifact_dir=artifact_dir,
    )

    previous_cleanup = (
        artifact_dir / "cleanup.json"
    )

    if not os.path.lexists(run_root):
        previous = None

        if previous_cleanup.is_file():
            try:
                previous = read_json(
                    previous_cleanup
                )
            except Exception:
                previous = None

        status = (
            "pass"
            if (
                isinstance(previous, dict)
                and previous.get("status")
                == "pass"
            )
            else "unknown"
        )

        atomic_json(
            previous_cleanup,
            {
                "schemaVersion": 1,
                "runId":
                    args.run_id,
                "status":
                    status,
                "idempotentRecheck":
                    True,
                "runRootAbsent":
                    True,
                "incomplete":
                    False,
                "remainingPaths":
                    [],
                "observedAt":
                    utc_now(),
            },
        )

        return verdict_code(status)

    command_root = (
        artifact_dir
        / "cleanup-commands"
    )

    if command_root.exists():
        shutil.rmtree(
            command_root
        )

    recorder = CommandRecorder(
        command_root
    )

    cleanup_artifact = (
        artifact_dir
        / "cleanup-evidence"
    )
    cleanup_artifact.mkdir(
        parents=True,
        exist_ok=True,
    )

    started_at = utc_now()

    steps: dict[
        str,
        dict[str, Any],
    ] = {}

    snapshot = snapshot_evidence(
        gate_repo=gate_repo,
        claude_config=claude_config,
        artifact_dir=artifact_dir,
    )

    steps["evidenceSnapshot"] = {
        "verdict": "pass",
        "detail": snapshot,
    }

    process_status, processes = (
        terminate_owned_processes(
            recorder,
            run_root,
            cleanup_artifact,
        )
    )

    steps["processes"] = {
        "verdict":
            process_status,
        "targetCount":
            len(processes),
    }

    env = i1.sanitize_env(
        os.environ
    )

    registrations: list[
        dict[str, str]
    ] = []

    if gate_repo.is_dir():
        registrations, _ = (
            registration_rows(
                gate_repo,
                run_root,
            )
        )

    teams = disposable_teams(
        registrations,
        args.gate_team,
    )

    if gate_repo.is_dir():
        claim_status, claims = (
            release_message_claims(
                i1=i1,
                gate_repo=gate_repo,
                teams=teams,
                env=env,
                recorder=recorder,
                artifact=cleanup_artifact,
            )
        )
    else:
        claim_status = "unknown"
        claims = []

    steps["claims"] = {
        "verdict":
            claim_status,
        "targetCount":
            len(claims),
        "teams":
            teams,
    }

    if gate_repo.is_dir():
        registration_status, reset_rows = (
            reset_registrations(
                gate_repo=gate_repo,
                run_root=run_root,
                recorder=recorder,
                env=env,
                artifact=cleanup_artifact,
            )
        )
    else:
        registration_status = (
            "unknown"
        )
        reset_rows = []

    steps["registrations"] = {
        "verdict":
            registration_status,
        "targetCount":
            len(reset_rows),
    }

    # Physical cleanup follows logical claim/registration cleanup.
    path_statuses = {}

    # Paths whose removal was stopped because a symbolic link was found
    # on the way (Issue #400). They are left in place and reported.
    stopped_paths: list[str] = []

    for name, path in (
        (
            "claudeConfig",
            claude_config,
        ),
        (
            "gateHome",
            gate_home,
        ),
        (
            "xdgConfig",
            xdg_config,
        ),
        (
            "xdgCache",
            xdg_cache,
        ),
        (
            "xdgData",
            xdg_data,
        ),
        (
            "xdgState",
            xdg_state,
        ),
    ):
        evidence: dict[str, Any] = {}
        status = remove_tree_safely(
            path,
            run_root,
            evidence,
        )
        path_statuses[name] = status
        steps[name] = {
            "verdict": status,
            "path": str(path),
            "absent":
                not os.path.lexists(path),
            **evidence,
        }

        if status == "unknown":
            stopped_paths.append(str(path))

    repo_evidence: dict[str, Any] = {}
    repo_status = remove_tree_safely(
        gate_repo,
        run_root,
        repo_evidence,
    )
    path_statuses["repository"] = (
        repo_status
    )

    steps["repository"] = {
        "verdict":
            repo_status,
        "path":
            str(gate_repo),
        "absent":
            not os.path.lexists(
                gate_repo
            ),
        **repo_evidence,
    }

    if repo_status == "unknown":
        stopped_paths.append(str(gate_repo))

    # The run root itself must not be a link either.
    root_links, root_error = (
        symlink_components(
            run_root,
            run_root,
        )
    )

    if root_error is not None or root_links:
        stopped_paths.append(str(run_root))

    # Any transient gh store created by I1 is under the disposable
    # repository/run root. Verify there is no surviving path whose
    # basename identifies that store.
    gh_candidates = [
        path
        for path in (
            run_root.rglob("*")
            if run_root.is_dir()
            else []
        )
        if (
            "gh" in path.name.lower()
            and "gate" in path.name.lower()
        )
    ]

    gh_status = (
        "pass"
        if not gh_candidates
        else "fail"
    )

    steps["transientGhStore"] = {
        "verdict":
            gh_status,
        "remaining":
            [
                str(path)
                for path in gh_candidates
            ],
    }

    # Re-check processes before deleting the root.
    after_process_table = process_table(
        recorder
    )

    if after_process_table is None:
        process_verify_status = (
            "unknown"
        )
        remaining_processes = []
    else:
        remaining_processes = (
            owned_processes(
                after_process_table,
                run_root,
            )
        )
        process_verify_status = (
            "pass"
            if not remaining_processes
            else "fail"
        )

    steps["processVerification"] = {
        "verdict":
            process_verify_status,
        "remaining":
            remaining_processes,
    }

    # Finally remove the run root itself. At this point only empty
    # parents/runtime leftovers should remain.
    #
    # If any removal was stopped on a symbolic link, stop here as well:
    # do not go after the leftovers. Cleanup is incomplete and the
    # remaining paths are reported (Issue #400).
    if stopped_paths:
        steps["runRoot"] = {
            "verdict":
                "unknown",
            "reason":
                "cleanup_stopped_on_symlink",
            "path":
                str(run_root),
            "absent":
                not os.path.lexists(
                    run_root
                ),
            "symlinkComponents":
                root_links,
        }
    else:
        try:
            if os.path.lexists(run_root):
                shutil.rmtree(
                    run_root
                )
        except OSError:
            pass

        run_root_status = (
            "pass"
            if not os.path.lexists(
                run_root
            )
            else "fail"
        )

        steps["runRoot"] = {
            "verdict":
                run_root_status,
            "path":
                str(run_root),
            "absent":
                not os.path.lexists(
                    run_root
                ),
        }

    remaining_paths = sorted(
        {
            *(
                path
                for path in stopped_paths
                if os.path.lexists(path)
            ),
            *(
                [str(run_root)]
                if os.path.lexists(run_root)
                else []
            ),
        }
    )

    cleanup_status = (
        aggregate_verdict(
            step["verdict"]
            for step in steps.values()
        )
    )

    result = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "status":
            cleanup_status,
        "startedAt":
            started_at,
        "finishedAt":
            utc_now(),
        "disposableTeams":
            teams,
        "incomplete":
            bool(stopped_paths),
        "remainingPaths":
            remaining_paths,
        "steps":
            steps,
    }

    atomic_json(
        artifact_dir
        / "cleanup.json",
        result,
    )

    return verdict_code(
        cleanup_status
    )


def choose_exit_value(
    directory: pathlib.Path,
) -> tuple[int | None, list[str]]:
    candidates: list[
        tuple[pathlib.Path, int]
    ] = []

    for path in directory.rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
        ):
            continue

        name = path.name.lower()

        if "exit" not in name:
            continue

        try:
            text = path.read_text(
                encoding="utf-8",
            ).strip()
        except Exception:
            continue

        if re.fullmatch(
            r"-?[0-9]+",
            text,
        ):
            candidates.append(
                (
                    path,
                    int(text, 10),
                )
            )

    values = {
        value
        for _, value in candidates
    }

    if len(values) != 1:
        return None, [
            str(path)
            for path, _
            in candidates
        ]

    return (
        next(iter(values)),
        [
            str(path)
            for path, _
            in candidates
        ],
    )


def live_control_semantics(
    directory: pathlib.Path,
) -> tuple[dict[str, Any], list[str]]:
    """The deny-probe values recorded in one live-PM control directory.

    Returns (values, unreadable). Keys are raw file names and
    "control.json:<field>"; raw values are bytes. A file or field that
    is absent is simply not a key, so a set difference between the two
    sides shows it. Anything present that cannot be read is listed in
    unreadable instead of being skipped.
    """
    values: dict[str, Any] = {}
    unreadable: list[str] = []

    for name in LIVE_RAW_FILES:
        path = directory / name

        if not os.path.lexists(path):
            continue

        if path.is_symlink() or not path.is_file():
            unreadable.append(name)
            continue

        try:
            values[name] = path.read_bytes()
        except OSError:
            unreadable.append(name)

    control = directory / "control.json"

    if os.path.lexists(control):
        try:
            if control.is_symlink() or not control.is_file():
                raise ValueError("not a regular file")

            record = read_json(control)

            if not isinstance(record, dict):
                raise ValueError("root not object")
        except Exception:
            unreadable.append("control.json")
        else:
            for field in LIVE_CONTROL_FIELDS:
                if field in record:
                    values[f"control.json:{field}"] = record[field]

    return values, sorted(unreadable)


def describe_live_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "sha256": hashlib.sha256(value).hexdigest(),
            "bytes": len(value),
        }
    return value


def compare_live_pm(
    args: argparse.Namespace,
) -> int:
    artifact_dir = safe_absolute(
        args.artifact_dir
    )

    before = (
        artifact_dir
        / "live-pm"
        / "before"
    )
    after = (
        artifact_dir
        / "live-pm"
        / "after"
    )

    output = (
        artifact_dir
        / "live-pm"
        / "result.json"
    )

    checks: list[
        dict[str, Any]
    ] = []

    if args.after_status == 0:
        after_status_verdict = "pass"
    elif args.after_status == 1:
        after_status_verdict = "fail"
    else:
        after_status_verdict = "unknown"

    checks.append(
        {
            "name": "after-control-runner-status",
            "verdict": after_status_verdict,
            "value": args.after_status,
        }
    )

    before_guard = (
        before / "guard.sha256"
    )
    after_guard = (
        after / "guard.sha256"
    )

    if (
        before_guard.is_file()
        and after_guard.is_file()
    ):
        before_guard_value = (
            before_guard.read_text(
                encoding="utf-8"
            ).strip()
        )
        after_guard_value = (
            after_guard.read_text(
                encoding="utf-8"
            ).strip()
        )

        guard_verdict = (
            before_guard_value
            == after_guard_value
            and bool(
                before_guard_value
            )
        )

        checks.append(
            {
                "name":
                    "guard-digest-before-after",
                "verdict":
                    (
                        "pass"
                        if guard_verdict
                        else "fail"
                    ),
                "before":
                    before_guard_value,
                "after":
                    after_guard_value,
            }
        )
    else:
        checks.append(
            {
                "name":
                    "guard-digest-before-after",
                "verdict":
                    "unknown",
                "reason":
                    "guard_digest_artifact_missing",
            }
        )

    before_exit, before_exit_paths = (
        choose_exit_value(
            before
        )
    )
    after_exit, after_exit_paths = (
        choose_exit_value(
            after
        )
    )

    if (
        before_exit is None
        or after_exit is None
    ):
        exit_verdict = "unknown"
    else:
        exit_verdict = (
            "pass"
            if before_exit == after_exit
            else "fail"
        )

    checks.append(
        {
            "name":
                "deny-exit-before-after",
            "verdict":
                exit_verdict,
            "before":
                before_exit,
            "after":
                after_exit,
            "beforeCandidates":
                before_exit_paths,
            "afterCandidates":
                after_exit_paths,
        }
    )

    before_values, before_unreadable = (
        live_control_semantics(
            before
        )
    )
    after_values, after_unreadable = (
        live_control_semantics(
            after
        )
    )

    before_names = {
        *before_values,
        *before_unreadable,
    }
    after_names = {
        *after_values,
        *after_unreadable,
    }

    # A file that could not be read on either side cannot show a set
    # difference: its keys are unknown, not missing. "control.json" covers
    # every "control.json:<field>" key.
    unreadable_files = {
        *before_unreadable,
        *after_unreadable,
    }

    def comparable(key: str) -> bool:
        return key.split(":", 1)[0] not in unreadable_files

    missing_after = sorted(
        key
        for key in before_names - after_names
        if comparable(key)
    )
    added_after = sorted(
        key
        for key in after_names - before_names
        if comparable(key)
    )
    unreadable = sorted(
        {
            *(f"before/{name}" for name in before_unreadable),
            *(f"after/{name}" for name in after_unreadable),
        }
    )
    changed = sorted(
        key
        for key in before_values
        if key in after_values
        and before_values[key] != after_values[key]
    )

    # runbook §36: a probe artifact that disappears or appears is a
    # mutation (fail); a changed byte or fixed field is a mutation (fail);
    # nothing observed, or something unreadable, cannot be compared.
    if (
        not before.is_dir()
        or not after.is_dir()
        or (not before_names and not after_names)
    ):
        semantic_verdict = "unknown"
        semantic_reason = "semantic_artifact_missing"
    elif missing_after or added_after:
        semantic_verdict = "fail"
        semantic_reason = "semantic_file_set_changed"
    elif changed:
        semantic_verdict = "fail"
        semantic_reason = "semantic_content_changed"
    elif unreadable:
        semantic_verdict = "unknown"
        semantic_reason = "semantic_file_unreadable"
    else:
        semantic_verdict = "pass"
        semantic_reason = None

    checks.append(
        {
            "name":
                "deny-semantic-before-after",
            "verdict":
                semantic_verdict,
            "reason":
                semantic_reason,
            "before": {
                key: describe_live_value(value)
                for key, value in sorted(before_values.items())
            },
            "after": {
                key: describe_live_value(value)
                for key, value in sorted(after_values.items())
            },
            "missingAfter":
                missing_after,
            "addedAfter":
                added_after,
            "changed":
                changed,
            "unreadable":
                unreadable,
        }
    )

    verdict = aggregate_verdict(
        item["verdict"]
        for item in checks
    )

    result = {
        "schemaVersion": 1,
        "check":
            "livePmNegativeControl",
        "verdict":
            verdict,
        "checks":
            checks,
        "observedAt":
            utc_now(),
    }

    atomic_json(
        output,
        result,
    )

    return verdict_code(
        verdict
    )


def result_verdict(
    path: pathlib.Path,
) -> tuple[str, str | None]:
    if not path.is_file():
        return (
            "unknown",
            "result_file_missing",
        )

    try:
        value = read_json(path)
    except Exception as exc:
        return (
            "unknown",
            f"result_unreadable:{type(exc).__name__}",
        )

    if not isinstance(value, dict):
        return (
            "unknown",
            "result_root_not_object",
        )

    verdict = value.get(
        "verdict"
    )

    if verdict not in (
        "pass",
        "fail",
        "unknown",
    ):
        return (
            "unknown",
            "result_verdict_invalid",
        )

    reason = value.get("reason")
    if not isinstance(reason, str):
        reason = None

    return verdict, reason


def flatten_problem_assertions(
    value: Any,
    prefix: str,
    evidence: str,
) -> list[dict[str, str]]:
    problems: list[
        dict[str, str]
    ] = []

    if isinstance(value, dict):
        verdict = value.get(
            "verdict"
        )

        if verdict in (
            "fail",
            "unknown",
        ):
            name = value.get(
                "name"
            ) or value.get(
                "check"
            ) or prefix

            reason = value.get(
                "reason"
            )

            if not isinstance(reason, str):
                reason = (
                    f"assertion_{verdict}"
                )

            problems.append(
                {
                    "check":
                        str(name),
                    "reason":
                        reason,
                    "evidence":
                        evidence,
                    "verdict":
                        verdict,
                }
            )

        for key, child in value.items():
            if key in (
                "checks",
                "control",
                "fault",
                "recovery",
                "F2a",
                "F2b",
                "cases",
            ):
                problems.extend(
                    flatten_problem_assertions(
                        child,
                        f"{prefix}.{key}",
                        evidence,
                    )
                )

    elif isinstance(value, list):
        for index, child in enumerate(
            value
        ):
            problems.extend(
                flatten_problem_assertions(
                    child,
                    f"{prefix}[{index}]",
                    evidence,
                )
            )

    return problems


def observation(
    *,
    check: str,
    value: Any,
    cutoff: Any,
    source: str,
    command: Any,
    raw_evidence: str,
    verdict: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "check": check,
        "value": value,
        "cutoff": cutoff,
        "source": source,
        "command": command,
        "rawEvidence": raw_evidence,
        "observedAt": utc_now(),
        "verdict": verdict,
        "reason": (
            reason
            if reason is not None
            else (
                "observation_matches_expected"
                if verdict == "pass"
                else f"{check}_{verdict}"
            )
        ),
    }


def evaluate_run(
    args: argparse.Namespace,
) -> int:
    artifact_dir = safe_absolute(
        args.artifact_dir
    )

    checks: dict[
        str,
        dict[str, str],
    ] = {}

    execution_verdict = (
        "pass"
        if args.execution_status == 0
        else "fail"
        if args.execution_status == 1
        else "unknown"
    )

    unknowns: list[
        dict[str, str]
    ] = []

    failures: list[
        dict[str, str]
    ] = []

    observations_path = (
        artifact_dir
        / "observations.jsonl"
    )

    if observations_path.exists():
        observations_path.unlink()

    for check in CHECKS:
        result_path = (
            artifact_dir
            / check
            / "result.json"
        )

        verdict, reason = (
            result_verdict(
                result_path
            )
        )

        checks[check] = {
            "verdict": verdict,
        }

        relative = str(
            result_path.relative_to(
                artifact_dir
            )
        )

        cutoff: Any = None

        if check == "F4":
            try:
                f4_value = read_json(
                    result_path
                )
                cutoff = (
                    f4_value.get(
                        "cutoffSeconds"
                    )
                    if isinstance(
                        f4_value,
                        dict,
                    )
                    else None
                )
            except Exception:
                cutoff = None

        append_jsonl(
            observations_path,
            observation(
                check=check,
                value=verdict,
                cutoff=cutoff,
                source=SOURCE_BY_CHECK[
                    check
                ],
                command=None,
                raw_evidence=relative,
                verdict=verdict,
                reason=reason,
            ),
        )

        if verdict != "pass":
            item = {
                "check": check,
                "reason": (
                    reason
                    or f"{check}_{verdict}"
                ),
                "evidence": relative,
            }

            if verdict == "fail":
                failures.append(item)
            else:
                unknowns.append(item)

        if result_path.is_file():
            try:
                nested = read_json(
                    result_path
                )

                for problem in (
                    flatten_problem_assertions(
                        nested,
                        check,
                        relative,
                    )
                ):
                    target = (
                        failures
                        if problem[
                            "verdict"
                        ] == "fail"
                        else unknowns
                    )

                    compact = {
                        "check":
                            problem[
                                "check"
                            ],
                        "reason":
                            problem[
                                "reason"
                            ],
                        "evidence":
                            problem[
                                "evidence"
                            ],
                    }

                    if compact not in target:
                        target.append(
                            compact
                        )
            except Exception:
                pass

    live_path = (
        artifact_dir
        / "live-pm"
        / "result.json"
    )

    live_verdict, live_reason = (
        result_verdict(
            live_path
        )
    )

    append_jsonl(
        observations_path,
        observation(
            check=(
                "livePmNegativeControl"
            ),
            value=live_verdict,
            cutoff=None,
            source="live-pm-control",
            command=None,
            raw_evidence=(
                "live-pm/result.json"
            ),
            verdict=live_verdict,
            reason=live_reason,
        ),
    )

    if live_verdict != "pass":
        item = {
            "check":
                "livePmNegativeControl",
            "reason":
                live_reason
                or (
                    "live_pm_negative_"
                    f"control_{live_verdict}"
                ),
            "evidence":
                "live-pm/result.json",
        }

        (
            failures
            if live_verdict == "fail"
            else unknowns
        ).append(item)

    cleanup_path = (
        artifact_dir
        / "cleanup.json"
    )

    cleanup_status = "unknown"
    cleanup_reason = None
    cleanup_incomplete: bool | None = None
    cleanup_remaining: list[str] = []

    if cleanup_path.is_file():
        try:
            cleanup_value = read_json(
                cleanup_path
            )

            if (
                isinstance(
                    cleanup_value,
                    dict,
                )
                and cleanup_value.get(
                    "status"
                )
                in (
                    "pass",
                    "fail",
                    "unknown",
                )
            ):
                cleanup_status = (
                    cleanup_value[
                        "status"
                    ]
                )

                incomplete = cleanup_value.get(
                    "incomplete"
                )
                cleanup_incomplete = (
                    incomplete
                    if isinstance(incomplete, bool)
                    else None
                )

                remaining = cleanup_value.get(
                    "remainingPaths"
                )
                if isinstance(remaining, list):
                    cleanup_remaining = [
                        item
                        for item in remaining
                        if isinstance(item, str)
                    ]

                # Issue #400: a cleanup that stopped and left paths
                # behind is not a pass, and a record that does not say
                # whether it completed cannot prove that it did.
                if cleanup_incomplete is True:
                    cleanup_reason = (
                        "cleanup_incomplete"
                    )
                    if cleanup_status == "pass":
                        cleanup_status = "unknown"
                elif (
                    cleanup_incomplete is None
                    and cleanup_status == "pass"
                ):
                    cleanup_status = "unknown"
                    cleanup_reason = (
                        "cleanup_completion_unproved"
                    )
            else:
                cleanup_reason = (
                    "cleanup_status_invalid"
                )
        except Exception as exc:
            cleanup_reason = (
                "cleanup_unreadable:"
                f"{type(exc).__name__}"
            )
    else:
        cleanup_reason = (
            "cleanup_result_missing"
        )

    append_jsonl(
        observations_path,
        observation(
            check="cleanup",
            value=cleanup_status,
            cutoff=None,
            source="filesystem",
            command=None,
            raw_evidence="cleanup.json",
            verdict=cleanup_status,
            reason=cleanup_reason,
        ),
    )

    if cleanup_status != "pass":
        item = {
            "check": "cleanup",
            "reason": (
                cleanup_reason
                or (
                    "cleanup_"
                    f"{cleanup_status}"
                )
            ),
            "evidence":
                "cleanup.json",
        }

        (
            failures
            if cleanup_status == "fail"
            else unknowns
        ).append(item)

    all_check_verdicts = [
        checks[check]["verdict"]
        for check in CHECKS
    ]

    if execution_verdict != "pass":
        execution_problem = {
            "check": "execution",
            "reason": (
                "gate_execution_failed"
                if execution_verdict == "fail"
                else "gate_execution_incomplete_or_unknown"
            ),
            "evidence": "results.json",
        }
        (
            failures
            if execution_verdict == "fail"
            else unknowns
        ).append(execution_problem)

    pilot_ready = (
        execution_verdict == "pass"
        and all(
            verdict == "pass"
            for verdict in (
                *all_check_verdicts,
                live_verdict,
                cleanup_status,
            )
        )
        and not unknowns
        and not failures
    )

    full_verdict = (
        "pass"
        if pilot_ready
        else (
            "fail"
            if failures
            else "unknown"
        )
    )

    results = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "requestedCheck":
            args.requested_check,
        "executionStatus":
            execution_verdict,
        "checks":
            checks,
        "livePmNegativeControl":
            live_verdict,
        "cleanupStatus":
            cleanup_status,
        "cleanupIncomplete":
            cleanup_incomplete,
        "cleanupRemainingPaths":
            cleanup_remaining,
        "unknown":
            unknowns,
        "failures":
            failures,
        "pilot_ready":
            pilot_ready,
        "verdict":
            full_verdict,
        "evaluatedAt":
            utc_now(),
    }

    atomic_json(
        artifact_dir
        / "results.json",
        results,
    )

    # Process exit reflects the requested scope, while results.json
    # remains authoritative for full pilot readiness.
    if args.requested_check == "all":
        return verdict_code(
            full_verdict
        )

    selected = checks[
        args.requested_check
    ]["verdict"]

    scope_verdict = aggregate_verdict(
        (
            execution_verdict,
            selected,
            live_verdict,
            cleanup_status,
        )
    )

    return verdict_code(
        scope_verdict
    )


class UsageErrorParser(argparse.ArgumentParser):
    """argparse with the runner's invocation-error status (runbook §32).

    A missing or invalid argument is a bug in the caller; stop with 64
    instead of argparse's default 2, which would read as "unknown".
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(
            EX_USAGE,
            f"{self.prog}: error: {message}\n",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = UsageErrorParser(
        description=(
            "Issue #396 pilot gate "
            "cleanup/evaluation helper"
        )
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    cleanup = sub.add_parser(
        "cleanup"
    )
    cleanup.add_argument(
        "--run-id",
        required=True,
    )
    cleanup.add_argument(
        "--run-root",
        required=True,
    )
    cleanup.add_argument(
        "--gate-repo",
        required=True,
    )
    cleanup.add_argument(
        "--gate-home",
        required=True,
    )
    cleanup.add_argument(
        "--xdg-config",
        required=True,
    )
    cleanup.add_argument(
        "--xdg-cache",
        required=True,
    )
    cleanup.add_argument(
        "--xdg-data",
        required=True,
    )
    cleanup.add_argument(
        "--xdg-state",
        required=True,
    )
    cleanup.add_argument(
        "--claude-config",
        required=True,
    )
    cleanup.add_argument(
        "--artifact-dir",
        required=True,
    )
    cleanup.add_argument(
        "--gate-team",
        required=True,
    )
    cleanup.set_defaults(
        handler=cleanup_run
    )

    compare_live = sub.add_parser(
        "compare-live"
    )
    compare_live.add_argument(
        "--artifact-dir",
        required=True,
    )
    # No default: an omitted status must not be read as success.
    compare_live.add_argument(
        "--after-status",
        type=int,
        choices=(0, 1, 2, 70),
        required=True,
    )
    compare_live.set_defaults(
        handler=compare_live_pm
    )

    evaluate = sub.add_parser(
        "evaluate"
    )
    evaluate.add_argument(
        "--run-id",
        required=True,
    )
    evaluate.add_argument(
        "--artifact-dir",
        required=True,
    )
    evaluate.add_argument(
        "--requested-check",
        choices=(
            "all",
            *CHECKS,
        ),
        default="all",
    )
    # No default: an omitted status must not be read as success.
    evaluate.add_argument(
        "--execution-status",
        type=int,
        choices=(0, 1, 2),
        required=True,
    )
    evaluate.set_defaults(
        handler=evaluate_run
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        return args.handler(args)

    except KeyboardInterrupt:
        return 130

    except Exception as exc:
        print(
            "pilot-gate-cleanup: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return EX_UNKNOWN


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
