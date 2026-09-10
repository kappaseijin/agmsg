#!/usr/bin/env python3

"""
pilot-gate-isolation.py

Issue #396 / Part 1 helper for the G4 integration-gate harness.

Responsibilities implemented in Part 1:

  P0
    - environment metadata helpers

  P1
    - live PM negative-control metadata recording

  P2/P3
    - disposable repository/HOME/XDG isolation validation
    - no-remotes proof
    - live/gate filesystem disjointness
    - disposable team validation
    - native Claude identity validation

  P4
    - fixed F2 probe construction
    - runbook §7.2 twelve-part F2 containment proof

  N1
    - fresh/resume binding discovery
    - binding validation
    - native argv-semantics validation
    - transcript discovery
    - generation/session continuity validation

This helper performs no network operation and never writes to GitHub.

Validation command exit conventions:

    0  assertion(s) positively proved
    1  definite contradictory observation (fail)
    2  required observation unavailable/unprovable (unknown)

The isolation/preflight commands intentionally fail closed. They never treat an
unknown proof as safe.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Any, Iterable


PILOT_AGENT = "agmsg_pm_pilot_claude"

GITHUB_CREDENTIAL_ENV_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-"
    r"[0-9a-f]{4}-"
    r"[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}$",
    re.IGNORECASE,
)


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def die(
    message: str,
    status: int = 2,
) -> None:
    print(
        f"pilot-gate-isolation: {message}",
        file=sys.stderr,
    )
    raise SystemExit(status)


def load_json(
    path: str | os.PathLike[str],
) -> Any:
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as fh:
        return json.load(fh)


def write_json(
    path: str | os.PathLike[str],
    value: Any,
) -> None:
    output = pathlib.Path(path)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.tmp"
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

    os.replace(
        temporary,
        output,
    )


def sha256_file(
    path: str | os.PathLike[str],
) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def canonical(
    path: str | os.PathLike[str],
    *,
    strict: bool = True,
) -> str:
    value = pathlib.Path(path)

    return str(
        value.resolve(
            strict=strict
        )
    )


def is_within(
    child: str,
    parent: str,
    *,
    allow_equal: bool = True,
) -> bool:
    child_real = canonical(
        child,
        strict=False,
    )

    parent_real = canonical(
        parent,
        strict=False,
    )

    try:
        common = os.path.commonpath(
            [
                child_real,
                parent_real,
            ]
        )
    except ValueError:
        return False

    if common != parent_real:
        return False

    if (
        not allow_equal
        and child_real == parent_real
    ):
        return False

    return True


def run_command(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_output(
    repo: str,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    return run_command(
        [
            "git",
            "-C",
            repo,
            *args,
        ]
    )


def assertion(
    number: int | str,
    name: str,
    passed: bool | None,
    detail: Any,
) -> dict[str, Any]:
    if passed is True:
        verdict = "pass"

    elif passed is False:
        verdict = "fail"

    else:
        verdict = "unknown"

    return {
        "number": number,
        "name": name,
        "verdict": verdict,
        "detail": detail,
    }


def overall_status(
    checks: Iterable[
        dict[str, Any]
    ],
) -> tuple[str, int]:
    values = list(checks)

    if any(
        value["verdict"] == "fail"
        for value in values
    ):
        return "fail", 1

    if any(
        value["verdict"] == "unknown"
        for value in values
    ):
        return "unknown", 2

    return "pass", 0


def longest_existing_ancestor(
    path: str,
) -> pathlib.Path:
    current = pathlib.Path(path)

    while not current.exists():
        parent = current.parent

        if parent == current:
            raise FileNotFoundError(
                "no existing ancestor for "
                f"{path!r}"
            )

        current = parent

    return current


def canonical_nonexistent(
    path: str,
) -> str:
    target = pathlib.Path(path)

    ancestor = (
        longest_existing_ancestor(
            path
        )
    )

    ancestor_real = (
        ancestor.resolve(
            strict=True
        )
    )

    relative = target.relative_to(
        ancestor
    )

    return str(
        ancestor_real.joinpath(
            relative
        )
    )


def no_symlink_components(
    root: str,
    target_parent: str,
) -> tuple[bool | None, list[str]]:
    """
    Prove that each existing path component between root and target_parent
    itself is not a symlink.

    The canonical identity of root is trusted only after resolve(strict=True).
    Components are then inspected lexically with lstat so a symlink component
    cannot disappear merely because realpath resolved it.
    """

    try:
        root_path = pathlib.Path(
            root
        )

        root_real = root_path.resolve(
            strict=True
        )

        parent_path = pathlib.Path(
            target_parent
        )

        parent_normalized = pathlib.Path(
            os.path.abspath(
                parent_path
            )
        )

        root_normalized = pathlib.Path(
            os.path.abspath(
                root_path
            )
        )

        relative = (
            parent_normalized
            .relative_to(
                root_normalized
            )
        )

    except Exception as exc:
        return None, [
            f"path relation unavailable: {exc}"
        ]

    current = root_normalized
    problems: list[str] = []

    try:
        root_stat = os.lstat(
            current
        )
    except OSError as exc:
        return None, [
            f"root lstat failed: {exc}"
        ]

    if stat.S_ISLNK(
        root_stat.st_mode
    ):
        problems.append(
            str(current)
        )

    for component in relative.parts:
        current = current / component

        if not os.path.lexists(
            current
        ):
            break

        try:
            metadata = os.lstat(
                current
            )
        except OSError as exc:
            return None, [
                f"lstat failed for "
                f"{current}: {exc}"
            ]

        if stat.S_ISLNK(
            metadata.st_mode
        ):
            problems.append(
                str(current)
            )

    # Also ensure physical parent, where it already exists, still resolves
    # below the physical gate repository.
    try:
        existing = (
            longest_existing_ancestor(
                str(parent_path)
            )
        )

        existing_real = (
            existing.resolve(
                strict=True
            )
        )

        if not is_within(
            str(existing_real),
            str(root_real),
        ):
            problems.append(
                "existing parent ancestor "
                "physically escapes gate repo: "
                f"{existing_real}"
            )

    except Exception as exc:
        return None, [
            f"parent physical identity "
            f"unavailable: {exc}"
        ]

    return (
        len(problems) == 0,
        problems,
    )


def git_remote_state(
    repo: str,
) -> tuple[
    bool | None,
    dict[str, Any],
]:
    remotes = git_output(
        repo,
        ["remote"],
    )

    if remotes.returncode != 0:
        return None, {
            "error":
                remotes.stderr.strip(),
        }

    names = [
        line.strip()
        for line
        in remotes.stdout.splitlines()
        if line.strip()
    ]

    remote_config = git_output(
        repo,
        [
            "config",
            "--local",
            "--get-regexp",
            r"^remote\.",
        ],
    )

    # git config --get-regexp returns 1 when there was simply no match.
    if remote_config.returncode not in (
        0,
        1,
    ):
        return None, {
            "remoteNames":
                names,
            "configError":
                remote_config.stderr.strip(),
        }

    config_entries = [
        line
        for line
        in remote_config.stdout.splitlines()
        if line.strip()
    ]

    return (
        (
            len(names) == 0
            and len(config_entries) == 0
        ),
        {
            "remoteNames":
                names,
            "remoteConfigEntries":
                config_entries,
        },
    )


def tracked_hardlink_overlap(
    gate_repo: str,
    live_repo: str,
) -> tuple[
    bool | None,
    list[dict[str, Any]],
]:
    """
    Compare every tracked regular file existing at the same relative path in
    the gate and live trees.

    A matching st_dev + st_ino proves that the disposable tree shares the same
    file identity with live state and must therefore be rejected.
    """

    listing = subprocess.run(
        [
            "git",
            "-C",
            gate_repo,
            "ls-files",
            "-z",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if listing.returncode != 0:
        return None, [
            {
                "error":
                    listing.stderr.decode(
                        "utf-8",
                        errors="replace",
                    ),
            }
        ]

    relative_paths = [
        item.decode(
            "utf-8",
            errors="surrogateescape",
        )
        for item
        in listing.stdout.split(b"\0")
        if item
    ]

    overlaps: list[
        dict[str, Any]
    ] = []

    for relative in relative_paths:
        gate_file = os.path.join(
            gate_repo,
            relative,
        )

        live_file = os.path.join(
            live_repo,
            relative,
        )

        try:
            gate_stat = os.lstat(
                gate_file
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, [
                {
                    "path":
                        relative,
                    "error":
                        "gate lstat failed: "
                        f"{exc}",
                }
            ]

        if not stat.S_ISREG(
            gate_stat.st_mode
        ):
            continue

        try:
            live_stat = os.lstat(
                live_file
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, [
                {
                    "path":
                        relative,
                    "error":
                        "live lstat failed: "
                        f"{exc}",
                }
            ]

        if not stat.S_ISREG(
            live_stat.st_mode
        ):
            continue

        if (
            gate_stat.st_dev
            == live_stat.st_dev
            and gate_stat.st_ino
            == live_stat.st_ino
        ):
            overlaps.append(
                {
                    "path":
                        relative,
                    "device":
                        gate_stat.st_dev,
                    "inode":
                        gate_stat.st_ino,
                }
            )

    return (
        len(overlaps) == 0,
        overlaps,
    )


def credential_files(
    gate_home: str,
    xdg_config: str,
) -> list[str]:
    candidates = [
        os.path.join(
            gate_home,
            ".config",
            "gh",
            "hosts.yml",
        ),
        os.path.join(
            xdg_config,
            "gh",
            "hosts.yml",
        ),
        os.path.join(
            gate_home,
            ".git-credentials",
        ),
        os.path.join(
            gate_home,
            ".netrc",
        ),
    ]

    found: list[str] = []

    for candidate in candidates:
        if os.path.lexists(
            candidate
        ):
            found.append(
                canonical(
                    candidate,
                    strict=False,
                )
            )

    return found


def validate_gate_roster(
    gate_repo: str,
    gate_team: str,
    pilot_agent: str,
    pilot_type: str,
) -> tuple[
    bool | None,
    dict[str, Any],
]:
    config_path = os.path.join(
        gate_repo,
        "teams",
        gate_team,
        "config.json",
    )

    if not os.path.isfile(
        config_path
    ):
        return False, {
            "error":
                "gate team config missing",
            "path":
                config_path,
        }

    try:
        config = load_json(
            config_path
        )
    except Exception as exc:
        return None, {
            "error":
                "cannot parse gate team "
                f"config: {exc}",
        }

    agents = config.get(
        "agents"
    )

    if not isinstance(
        agents,
        dict,
    ):
        return None, {
            "error":
                "team config agents is "
                "not an object",
        }

    record = agents.get(
        pilot_agent
    )

    if not isinstance(
        record,
        dict,
    ):
        return False, {
            "error":
                "pilot agent absent",
        }

    registrations = record.get(
        "registrations"
    )

    if not isinstance(
        registrations,
        list,
    ):
        return None, {
            "error":
                "registrations is not "
                "an array",
        }

    try:
        gate_real = canonical(
            gate_repo
        )
    except Exception as exc:
        return None, {
            "error":
                f"gate repo unreadable: {exc}",
        }

    matching: list[
        dict[str, Any]
    ] = []

    for registration in registrations:
        if not isinstance(
            registration,
            dict,
        ):
            continue

        project = registration.get(
            "project"
        )

        if not isinstance(
            project,
            str,
        ):
            continue

        try:
            project_real = canonical(
                project
            )
        except Exception:
            continue

        if (
            registration.get("type")
            == pilot_type
            and project_real
            == gate_real
        ):
            matching.append(
                registration
            )

    return (
        len(matching) == 1,
        {
            "config":
                config_path,
            "matchingRegistrations":
                len(matching),
            "totalRegistrations":
                len(registrations),
        },
    )


def pilot_seat_directory(
    gate_repo: str,
    team: str,
    agent: str,
) -> pathlib.Path:
    # The generated gate team and the fixed pilot agent contain only the
    # characters accepted unchanged by the current actas lock encoding.
    #
    # Fail closed if that assumption ever changes instead of trying to guess
    # a new encoding contract here.
    safe = re.compile(
        r"^[A-Za-z0-9._-]+$"
    )

    if (
        not safe.fullmatch(team)
        or not safe.fullmatch(agent)
    ):
        raise ValueError(
            "team/agent cannot be mapped "
            "to pilot seat directory safely"
        )

    return pathlib.Path(
        gate_repo,
        "run",
        "pilot",
        f"{team}__{agent}",
    )


def binding_path(
    gate_repo: str,
    team: str,
    agent: str,
    generation: str,
) -> pathlib.Path:
    if not re.fullmatch(
        r"[1-9][0-9]*",
        generation,
    ):
        raise ValueError(
            "invalid generation"
        )

    return (
        pilot_seat_directory(
            gate_repo,
            team,
            agent,
        )
        / "bindings"
        / f"{generation}.json"
    )


def state_path_from_binding(
    binding: dict[str, Any],
) -> pathlib.Path | None:
    state = binding.get(
        "stateFile"
    )

    if isinstance(
        state,
        str,
    ) and state:
        return pathlib.Path(
            state
        )

    return None


def command_new_run_id(
    _args: argparse.Namespace,
) -> int:
    timestamp = dt.datetime.now(
        dt.timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    suffix = uuid.uuid4().hex[
        :8
    ]

    print(
        f"{timestamp}-{suffix}"
    )

    return 0


def command_gate_team(
    args: argparse.Namespace,
) -> int:
    if not re.fullmatch(
        r"[A-Za-z0-9._-]+",
        args.run_id,
    ):
        die(
            "run id cannot be encoded "
            "as gate team"
        )

    print(
        f"agmsg-g4gate-{args.run_id}"
    )

    return 0


def command_canonical(
    args: argparse.Namespace,
) -> int:
    try:
        print(
            canonical(
                args.path
            )
        )
    except Exception as exc:
        die(
            "cannot canonicalize "
            f"{args.path!r}: {exc}"
        )

    return 0


def command_sha256(
    args: argparse.Namespace,
) -> int:
    try:
        print(
            sha256_file(
                args.path
            )
        )
    except Exception as exc:
        die(
            "cannot hash "
            f"{args.path!r}: {exc}"
        )

    return 0


def command_json_field(
    args: argparse.Namespace,
) -> int:
    try:
        value = load_json(
            args.path
        )
    except Exception as exc:
        die(
            f"cannot read JSON: {exc}"
        )

    if not isinstance(
        value,
        dict,
    ):
        die(
            "JSON root is not object"
        )

    if args.field not in value:
        die(
            "JSON field is unavailable: "
            f"{args.field}"
        )

    result = value[
        args.field
    ]

    if isinstance(
        result,
        bool,
    ):
        print(
            "true"
            if result
            else "false"
        )
        return 0

    if isinstance(
        result,
        (str, int),
    ):
        print(result)
        return 0

    die(
        "JSON field has unsupported type: "
        f"{args.field}"
    )


def command_write_state(
    args: argparse.Namespace,
) -> int:
    record = {
        "schemaVersion": 1,
        "part": 1,
        "runId":
            args.run_id,
        "runRoot":
            args.run_root,
        "source":
            args.source,
        "sourceHead":
            args.source_head,
        "liveSkillDir":
            args.live_skill_dir,
        "artifactDir":
            args.artifact_dir,
        "gateTeam":
            args.gate_team,
        "gateRepo":
            args.gate_repo,
        "gateHome":
            args.gate_home,
        "xdg": {
            "config":
                args.xdg_config,
            "cache":
                args.xdg_cache,
            "data":
                args.xdg_data,
            "state":
                args.xdg_state,
        },
        "claudeConfigDir":
            args.claude_config,
        "createdAt":
            utc_now(),
    }

    write_json(
        args.output,
        record,
    )

    return 0


def command_capture_environment(
    args: argparse.Namespace,
) -> int:
    sensitive_presence = {
        key: bool(
            os.environ.get(
                key
            )
        )
        for key
        in GITHUB_CREDENTIAL_ENV_KEYS
    }

    git_version = run_command(
        [
            "git",
            "--version",
        ]
    )

    record = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "capturedAt":
            utc_now(),
        "source":
            args.source,
        "sourceHead":
            args.source_head,
        "liveSkillDir":
            args.live_skill_dir,
        "artifactDir":
            args.artifact_dir,
        "runRoot":
            args.run_root,
        "gateTeam":
            args.gate_team,
        "collectorCutoffSeconds":
            args.collector_cutoff_seconds,
        "nativeClaude": {
            "commandPath":
                args.claude_bin,
            "resolvedPath":
                args.claude_resolved,
            "sha256":
                args.claude_digest,
            "version":
                args.claude_version,
        },
        "runtime": {
            "platform":
                sys.platform,
            "python":
                sys.version.split()[0],
            "git":
                git_version.stdout.strip(),
        },
        # Presence only. Secret values are deliberately not recorded.
        "githubCredentialEnvironmentPresent":
            sensitive_presence,
    }

    write_json(
        args.output,
        record,
    )

    return 0


def command_record_live_control(
    args: argparse.Namespace,
) -> int:
    directory = pathlib.Path(
        args.directory
    )

    required = {
        "input":
            directory
            / "input.raw",
        "stdout":
            directory
            / "stdout.raw",
        "stderr":
            directory
            / "stderr.raw",
        "exitStatus":
            directory
            / "exit-status",
        "guardDigest":
            directory
            / "guard.sha256",
    }

    missing = [
        name
        for name, path
        in required.items()
        if not path.exists()
    ]

    if missing:
        die(
            "live control evidence "
            "missing: "
            + ", ".join(missing)
        )

    try:
        exit_text = (
            required[
                "exitStatus"
            ]
            .read_text(
                encoding="utf-8"
            )
            .strip()
        )

        exit_status = int(
            exit_text
        )

    except Exception as exc:
        die(
            "live control exit status "
            f"is unreadable: {exc}"
        )

    metadata = {
        "schemaVersion": 1,
        "phase":
            args.phase,
        "observedAt":
            utc_now(),
        "inputSha256":
            sha256_file(
                required["input"]
            ),
        "stdoutSha256":
            sha256_file(
                required["stdout"]
            ),
        "stderrSha256":
            sha256_file(
                required["stderr"]
            ),
        "exitStatus":
            exit_status,
        "guardSha256":
            (
                required[
                    "guardDigest"
                ]
                .read_text(
                    encoding="utf-8"
                )
                .strip()
            ),
    }

    write_json(
        directory
        / "control.json",
        metadata,
    )

    return 0


def command_preflight(
    args: argparse.Namespace,
) -> int:
    checks: list[
        dict[str, Any]
    ] = []

    try:
        run_root = canonical(
            args.run_root
        )

        gate_repo = canonical(
            args.gate_repo
        )

        live_repo = canonical(
            args.live_repo
        )

        source = canonical(
            args.source
        )

        artifact_dir = canonical(
            args.artifact_dir
        )

        claude_bin = canonical(
            args.claude_bin
        )

    except Exception as exc:
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "runId":
                    args.run_id,
                "safe": False,
                "verdict":
                    "unknown",
                "reason":
                    "canonicalization "
                    f"unavailable: {exc}",
                "checks": [],
            },
        )

        return 2

    git_test = git_output(
        gate_repo,
        [
            "rev-parse",
            "--is-inside-work-tree",
        ],
    )

    checks.append(
        assertion(
            "P3.1",
            "gate repository is a git working tree",
            (
                True
                if (
                    git_test.returncode
                    == 0
                    and
                    git_test.stdout.strip()
                    == "true"
                )
                else False
            ),
            {
                "stdout":
                    git_test.stdout.strip(),
                "stderr":
                    git_test.stderr.strip(),
            },
        )
    )

    expected_repo = canonical(
        os.path.join(
            run_root,
            "repo",
        )
    )

    checks.append(
        assertion(
            "P3.2",
            "gate repository is fixed run-root/repo",
            (
                gate_repo
                == expected_repo
            ),
            {
                "gateRepo":
                    gate_repo,
                "expected":
                    expected_repo,
            },
        )
    )

    remote_ok, remote_detail = (
        git_remote_state(
            gate_repo
        )
    )

    checks.append(
        assertion(
            "P3.3",
            "gate repository has no git remote",
            remote_ok,
            remote_detail,
        )
    )

    disjoint_paths = (
        gate_repo != live_repo
        and not is_within(
            gate_repo,
            live_repo,
        )
        and not is_within(
            live_repo,
            gate_repo,
        )
    )

    checks.append(
        assertion(
            "P3.4",
            "gate and live repositories are path-disjoint",
            disjoint_paths,
            {
                "gateRepo":
                    gate_repo,
                "liveRepo":
                    live_repo,
            },
        )
    )

    hardlink_ok, overlap = (
        tracked_hardlink_overlap(
            gate_repo,
            live_repo,
        )
    )

    checks.append(
        assertion(
            "P3.5",
            "tracked gate files share no inode/device identity with live tree",
            hardlink_ok,
            {
                "overlap":
                    overlap,
            },
        )
    )

    live_gate_team = (
        pathlib.Path(
            live_repo
        )
        / "teams"
        / args.gate_team
    )

    checks.append(
        assertion(
            "P3.6",
            "gate team name is absent from live team namespace",
            not os.path.lexists(
                live_gate_team
            ),
            {
                "liveGateTeamPath":
                    str(
                        live_gate_team
                    ),
            },
        )
    )

    (
        roster_ok,
        roster_detail,
    ) = validate_gate_roster(
        gate_repo,
        args.gate_team,
        args.pilot_agent,
        args.pilot_type,
    )

    checks.append(
        assertion(
            "P3.7",
            "gate pilot has exactly one disposable registration",
            roster_ok,
            roster_detail,
        )
    )

    isolated_roots = [
        args.gate_home,
        args.xdg_config,
        args.xdg_cache,
        args.xdg_data,
        args.xdg_state,
        args.claude_config,
    ]

    roots_detail: list[
        dict[str, Any]
    ] = []

    roots_pass = True

    for value in isolated_roots:
        try:
            resolved = canonical(
                value
            )

            inside = is_within(
                resolved,
                run_root,
            )

            directory = (
                os.path.isdir(
                    resolved
                )
                and not os.path.islink(
                    value
                )
            )

            roots_detail.append(
                {
                    "path":
                        value,
                    "resolved":
                        resolved,
                    "insideRunRoot":
                        inside,
                    "directory":
                        directory,
                }
            )

            if not (
                inside
                and directory
            ):
                roots_pass = False

        except Exception as exc:
            roots_pass = False

            roots_detail.append(
                {
                    "path":
                        value,
                    "error":
                        str(exc),
                }
            )

    checks.append(
        assertion(
            "P3.8",
            "HOME/XDG/CLAUDE_CONFIG are disposable run-root directories",
            roots_pass,
            roots_detail,
        )
    )

    checks.append(
        assertion(
            "P3.9",
            "artifact directory is outside disposable run root",
            not is_within(
                artifact_dir,
                run_root,
            ),
            {
                "artifactDir":
                    artifact_dir,
                "runRoot":
                    run_root,
            },
        )
    )

    gh = shutil.which(
        "gh"
    )

    if gh is None:
        gh_auth_ok: bool | None = True

        gh_detail: dict[
            str,
            Any,
        ] = {
            "ghExecutable":
                None,
            "reason":
                "gh executable absent",
        }

    else:
        gh_result = run_command(
            [
                gh,
                "auth",
                "status",
            ],
            env=os.environ.copy(),
        )

        gh_auth_ok = (
            gh_result.returncode
            != 0
        )

        try:
            gh_resolved = canonical(
                gh
            )
        except Exception:
            gh_resolved = gh

        gh_detail = {
            "ghExecutable":
                gh_resolved,
            "authStatusExit":
                gh_result.returncode,
        }

    checks.append(
        assertion(
            "P3.10",
            "gh authentication is absent in disposable environment",
            gh_auth_ok,
            gh_detail,
        )
    )

    credential_env = {
        key: bool(
            os.environ.get(key)
        )
        for key
        in GITHUB_CREDENTIAL_ENV_KEYS
    }

    checks.append(
        assertion(
            "P3.11",
            "GitHub credential environment variables are absent",
            not any(
                credential_env.values()
            ),
            credential_env,
        )
    )

    try:
        claude_metadata = os.stat(
            claude_bin
        )

        native_ok = (
            stat.S_ISREG(
                claude_metadata.st_mode
            )
            and bool(
                claude_metadata.st_mode
                & (
                    stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )
            )
            and not is_within(
                claude_bin,
                run_root,
            )
        )

        native_detail = {
            "resolved":
                claude_bin,
            "sha256":
                sha256_file(
                    claude_bin
                ),
        }

    except Exception as exc:
        native_ok = None

        native_detail = {
            "error":
                str(exc),
        }

    checks.append(
        assertion(
            "P3.12",
            "native Claude executable is positively identified outside gate root",
            native_ok,
            native_detail,
        )
    )

    checks.append(
        assertion(
            "P3.13",
            "source is copy input only and differs from gate working tree",
            (
                source != gate_repo
                and not is_within(
                    source,
                    gate_repo,
                )
                and not is_within(
                    gate_repo,
                    source,
                )
            ),
            {
                "source":
                    source,
                "gateRepo":
                    gate_repo,
            },
        )
    )

    verdict, _ = (
        overall_status(
            checks
        )
    )

    record = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "observedAt":
            utc_now(),
        "safe":
            verdict == "pass",
        "verdict":
            verdict,
        "checks":
            checks,
    }

    write_json(
        args.output,
        record,
    )

    # P3 is a safety barrier, not a normal gate assertion.
    #
    # Any contradiction OR inability to prove the condition means execution
    # after P3 is prohibited.
    if verdict == "pass":
        return 0

    return 2


def command_make_f2_probe(
    args: argparse.Namespace,
) -> int:
    try:
        gate_repo = canonical(
            args.gate_repo
        )

        target = canonical_nonexistent(
            args.target
        )

    except Exception as exc:
        die(
            "cannot establish F2 probe "
            f"identity: {exc}"
        )

    program_path = pathlib.Path(
        args.program
    )

    manifest_path = pathlib.Path(
        args.manifest
    )

    if not is_within(
        target,
        gate_repo,
        allow_equal=False,
    ):
        die(
            "refusing F2 probe target "
            "outside gate repository"
        )

    if os.path.lexists(
        args.target
    ):
        die(
            "refusing pre-existing "
            "F2 probe target"
        )

    program_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        program_parent = canonical(
            program_path.parent
        )
    except Exception as exc:
        die(
            "cannot establish F2 probe "
            f"program parent: {exc}"
        )

    if not is_within(
        program_parent,
        gate_repo,
        allow_equal=False,
    ):
        die(
            "F2 probe program must be "
            "inside gate repository"
        )

    marker = (
        "agmsg-g4-gate-f2:"
        f"{args.run_id}\n"
    )

    # There is deliberately NO runtime target argument.
    #
    # The destination is compiled into this gate-owned helper. This prevents a
    # later Claude/Bash invocation or caller from redirecting the dangerous F2
    # probe to another path.
    program = f"""#!/usr/bin/env python3
import os
import sys

TARGET = {target!r}
MARKER = {marker!r}

flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW

try:
    fd = os.open(TARGET, flags, 0o600)
except Exception as exc:
    print(
        "f2-probe: create failed: " + str(exc),
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    data = MARKER.encode("utf-8")
    written = os.write(fd, data)

    if written != len(data):
        raise RuntimeError("short write")

    os.fsync(fd)
finally:
    os.close(fd)
"""

    program_path.write_text(
        program,
        encoding="utf-8",
        newline="\n",
    )

    os.chmod(
        program_path,
        0o700,
    )

    program_real = canonical(
        program_path
    )

    python_real = canonical(
        sys.executable
    )

    argv = [
        python_real,
        program_real,
    ]

    manifest = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "probeTarget":
            target,
        "probeProgram":
            program_real,
        "probeProgramSha256":
            sha256_file(
                program_real
            ),
        "allowedCommand": {
            "argv":
                argv,
            "bashCommand":
                " ".join(
                    shlex.quote(
                        item
                    )
                    for item
                    in argv
                ),
        },
        "markerSha256":
            hashlib.sha256(
                marker.encode(
                    "utf-8"
                )
            ).hexdigest(),
    }

    write_json(
        manifest_path,
        manifest,
    )

    return 0


def command_f2_proof(
    args: argparse.Namespace,
) -> int:
    checks: list[
        dict[str, Any]
    ] = []

    try:
        gate_repo = canonical(
            args.gate_repo
        )

        live_repo = canonical(
            args.live_repo
        )

        gate_home = canonical(
            args.gate_home
        )

        xdg_config = canonical(
            args.xdg_config
        )

    except Exception as exc:
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "runId":
                    args.run_id,
                "safe": False,
                "verdict":
                    "unknown",
                "reason":
                    "base canonicalization "
                    f"unavailable: {exc}",
                "checks": [],
            },
        )

        return 2

    #
    # §7.2 item 1:
    # probe target can be canonicalized.
    #
    try:
        target_canonical = (
            canonical_nonexistent(
                args.probe_target
            )
        )

        checks.append(
            assertion(
                1,
                "probe target canonicalized",
                True,
                {
                    "target":
                        target_canonical,
                },
            )
        )

    except Exception as exc:
        target_canonical = (
            args.probe_target
        )

        checks.append(
            assertion(
                1,
                "probe target canonicalized",
                None,
                {
                    "error":
                        str(exc),
                },
            )
        )

    #
    # §7.2 item 2:
    # longest existing ancestor can be canonicalized.
    #
    try:
        ancestor = (
            longest_existing_ancestor(
                args.probe_target
            )
        )

        ancestor_canonical = str(
            ancestor.resolve(
                strict=True
            )
        )

        checks.append(
            assertion(
                2,
                "longest existing probe ancestor canonicalized",
                True,
                {
                    "ancestor":
                        str(ancestor),
                    "canonical":
                        ancestor_canonical,
                },
            )
        )

    except Exception as exc:
        ancestor_canonical = ""

        checks.append(
            assertion(
                2,
                "longest existing probe ancestor canonicalized",
                None,
                {
                    "error":
                        str(exc),
                },
            )
        )

    #
    # §7.2 item 3:
    # target is physically inside gate repository.
    #
    if (
        checks[0]["verdict"]
        == "pass"
    ):
        target_inside = is_within(
            target_canonical,
            gate_repo,
            allow_equal=False,
        )

        checks.append(
            assertion(
                3,
                "probe target is inside gate repository",
                target_inside,
                {
                    "target":
                        target_canonical,
                    "gateRepo":
                        gate_repo,
                },
            )
        )

    else:
        checks.append(
            assertion(
                3,
                "probe target is inside gate repository",
                None,
                {
                    "reason":
                        "target canonicalization unavailable",
                },
            )
        )

    #
    # §7.2 item 4:
    # target is not physically inside live repository.
    #
    if (
        checks[0]["verdict"]
        == "pass"
    ):
        target_not_live = (
            not is_within(
                target_canonical,
                live_repo,
            )
        )

        checks.append(
            assertion(
                4,
                "probe target is outside live repository",
                target_not_live,
                {
                    "target":
                        target_canonical,
                    "liveRepo":
                        live_repo,
                },
            )
        )

    else:
        checks.append(
            assertion(
                4,
                "probe target is outside live repository",
                None,
                {
                    "reason":
                        "target canonicalization unavailable",
                },
            )
        )

    #
    # §7.2 item 5:
    # gate repository is not below live repository.
    #
    checks.append(
        assertion(
            5,
            "gate repository is not inside live repository",
            not is_within(
                gate_repo,
                live_repo,
            ),
            {
                "gateRepo":
                    gate_repo,
                "liveRepo":
                    live_repo,
            },
        )
    )

    #
    # §7.2 item 6:
    # live repository is not below gate repository.
    #
    checks.append(
        assertion(
            6,
            "live repository is not inside gate repository",
            not is_within(
                live_repo,
                gate_repo,
            ),
            {
                "gateRepo":
                    gate_repo,
                "liveRepo":
                    live_repo,
            },
        )
    )

    #
    # §7.2 item 7:
    # no symlink component in target parent path.
    #
    (
        symlink_ok,
        symlink_detail,
    ) = no_symlink_components(
        gate_repo,
        str(
            pathlib.Path(
                args.probe_target
            ).parent
        ),
    )

    checks.append(
        assertion(
            7,
            "probe target parent has no symlink path component",
            symlink_ok,
            {
                "problems":
                    symlink_detail,
            },
        )
    )

    #
    # §7.2 item 8:
    # target is neither an existing symlink nor a hardlink to existing state.
    #
    # The probe contract requires a NEW file and uses O_EXCL when eventually
    # executed. Therefore proof is positive only while the target has no
    # directory entry at all.
    #
    try:
        target_exists = os.path.lexists(
            args.probe_target
        )

        target_absent = (
            not target_exists
        )

        target_detail: dict[
            str,
            Any,
        ] = {
            "lexists":
                target_exists,
            "required":
                "path absent before probe; "
                "probe uses O_EXCL and O_NOFOLLOW when available",
        }

        if target_exists:
            metadata = os.lstat(
                args.probe_target
            )

            target_detail[
                "mode"
            ] = stat.filemode(
                metadata.st_mode
            )

            target_detail[
                "nlink"
            ] = metadata.st_nlink

        checks.append(
            assertion(
                8,
                "probe target is not an existing symlink/hardlink/file",
                target_absent,
                target_detail,
            )
        )

    except OSError as exc:
        checks.append(
            assertion(
                8,
                "probe target is not an existing symlink/hardlink/file",
                None,
                {
                    "error":
                        str(exc),
                },
            )
        )

    #
    # §7.2 item 9:
    # gate repository still has no remote.
    #
    (
        remote_ok,
        remote_detail,
    ) = git_remote_state(
        gate_repo
    )

    checks.append(
        assertion(
            9,
            "gate repository has no git remote",
            remote_ok,
            remote_detail,
        )
    )

    #
    # §7.2 item 10:
    # gate HOME/XDG contains no gh/git credential file.
    #
    try:
        found_credentials = (
            credential_files(
                gate_home,
                xdg_config,
            )
        )

        checks.append(
            assertion(
                10,
                "gate HOME contains no GitHub credential material",
                (
                    len(
                        found_credentials
                    )
                    == 0
                ),
                {
                    "found":
                        found_credentials,
                },
            )
        )

    except Exception as exc:
        checks.append(
            assertion(
                10,
                "gate HOME contains no GitHub credential material",
                None,
                {
                    "error":
                        str(exc),
                },
            )
        )

    #
    # §7.2 item 11:
    # GitHub credential environment variables are absent.
    #
    credential_environment = {
        key: bool(
            os.environ.get(key)
        )
        for key
        in GITHUB_CREDENTIAL_ENV_KEYS
    }

    checks.append(
        assertion(
            11,
            "GitHub credential environment is absent",
            not any(
                credential_environment.values()
            ),
            credential_environment,
        )
    )

    #
    # §7.2 item 12:
    # allowed F2 command is fixed by manifest digest.
    #
    try:
        manifest = load_json(
            args.probe_manifest
        )

        if not isinstance(
            manifest,
            dict,
        ):
            raise ValueError(
                "manifest root is not object"
            )

        manifest_target = (
            manifest.get(
                "probeTarget"
            )
        )

        manifest_program = (
            manifest.get(
                "probeProgram"
            )
        )

        recorded_program_digest = (
            manifest.get(
                "probeProgramSha256"
            )
        )

        allowed = manifest.get(
            "allowedCommand"
        )

        if not isinstance(
            allowed,
            dict,
        ):
            raise ValueError(
                "allowedCommand is not object"
            )

        argv = allowed.get(
            "argv"
        )

        if (
            not isinstance(
                argv,
                list,
            )
            or len(argv) != 2
            or not all(
                isinstance(
                    item,
                    str,
                )
                and item
                for item
                in argv
            )
        ):
            raise ValueError(
                "allowed argv must contain "
                "exactly python + fixed program"
            )

        program_real = canonical(
            args.probe_program
        )

        program_digest = sha256_file(
            program_real
        )

        python_real = canonical(
            sys.executable
        )

        expected_argv = [
            python_real,
            program_real,
        ]

        manifest_digest = (
            sha256_file(
                args.probe_manifest
            )
        )

        item12_pass = (
            manifest_target
            == target_canonical
            and manifest_program
            == program_real
            and recorded_program_digest
            == program_digest
            and argv
            == expected_argv
            and os.access(
                program_real,
                os.X_OK,
            )
            and is_within(
                program_real,
                gate_repo,
                allow_equal=False,
            )
        )

        checks.append(
            assertion(
                12,
                "F2 allowed command is fixed by manifest digest",
                item12_pass,
                {
                    "manifest":
                        canonical(
                            args.probe_manifest
                        ),
                    "manifestSha256":
                        manifest_digest,
                    "program":
                        program_real,
                    "programSha256":
                        program_digest,
                    "argv":
                        argv,
                    "expectedArgv":
                        expected_argv,
                    "probeTarget":
                        manifest_target,
                },
            )
        )

    except Exception as exc:
        checks.append(
            assertion(
                12,
                "F2 allowed command is fixed by manifest digest",
                None,
                {
                    "error":
                        str(exc),
                },
            )
        )

    verdict, _ = overall_status(
        checks
    )

    record = {
        "schemaVersion": 1,
        "runId":
            args.run_id,
        "observedAt":
            utc_now(),
        "gateRepo":
            gate_repo,
        "liveRepo":
            live_repo,
        "probeTarget":
            (
                target_canonical
                if isinstance(
                    target_canonical,
                    str,
                )
                else args.probe_target
            ),
        "safe":
            verdict == "pass",
        "verdict":
            verdict,
        "checks":
            checks,
    }

    write_json(
        args.output,
        record,
    )

    # This is the critical F2 barrier.
    #
    # NEVER distinguish "probably fine" from "unknown" here. Anything except
    # twelve positive proofs must prevent later fault-capable native sessions.
    if verdict == "pass":
        return 0

    return 2


def command_find_binding(
    args: argparse.Namespace,
) -> int:
    try:
        candidate = binding_path(
            args.gate_repo,
            args.team,
            args.agent,
            args.generation,
        )
    except Exception as exc:
        die(
            f"cannot derive binding path: {exc}"
        )

    if not candidate.is_file():
        return 1

    if candidate.is_symlink():
        return 1

    try:
        candidate_real = (
            candidate.resolve(
                strict=True
            )
        )

        gate_real = pathlib.Path(
            args.gate_repo
        ).resolve(
            strict=True
        )

        candidate_real.relative_to(
            gate_real
        )

    except Exception:
        return 1

    print(
        str(candidate)
    )

    return 0


def binding_generation_text(
    value: Any,
) -> str | None:
    if isinstance(
        value,
        bool,
    ):
        return None

    if isinstance(
        value,
        int,
    ):
        if value < 1:
            return None

        return str(value)

    if isinstance(
        value,
        str,
    ) and re.fullmatch(
        r"[1-9][0-9]*",
        value,
    ):
        return value

    return None


def binding_pid_text(
    value: Any,
) -> str | None:
    if isinstance(
        value,
        bool,
    ):
        return None

    if isinstance(
        value,
        int,
    ):
        if value < 1:
            return None

        return str(value)

    if isinstance(
        value,
        str,
    ) and re.fullmatch(
        r"[1-9][0-9]*",
        value,
    ):
        return value

    return None


def command_validate_binding(
    args: argparse.Namespace,
) -> int:
    checks: list[
        dict[str, Any]
    ] = []

    try:
        path = pathlib.Path(
            args.binding
        )

        if not path.is_file():
            write_json(
                args.output,
                {
                    "schemaVersion": 1,
                    "verdict":
                        "unknown",
                    "reason":
                        "binding unavailable",
                    "checks": [],
                },
            )

            return 2

        if path.is_symlink():
            write_json(
                args.output,
                {
                    "schemaVersion": 1,
                    "verdict":
                        "fail",
                    "reason":
                        "binding is symlink",
                    "checks": [],
                },
            )

            return 1

        binding = load_json(
            path
        )

    except Exception as exc:
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "verdict":
                    "unknown",
                "reason":
                    f"binding unreadable: {exc}",
                "checks": [],
            },
        )

        return 2

    if not isinstance(
        binding,
        dict,
    ):
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "verdict":
                    "unknown",
                "reason":
                    "binding root is not object",
                "checks": [],
            },
        )

        return 2

    checks.append(
        assertion(
            "N1.binding.schema",
            "binding schemaVersion is 1",
            (
                binding.get(
                    "schemaVersion"
                )
                == 1
            ),
            {
                "value":
                    binding.get(
                        "schemaVersion"
                    ),
            },
        )
    )

    checks.append(
        assertion(
            "N1.binding.team",
            "binding team is disposable gate team",
            (
                binding.get("team")
                == args.team
            ),
            {
                "actual":
                    binding.get(
                        "team"
                    ),
                "expected":
                    args.team,
            },
        )
    )

    checks.append(
        assertion(
            "N1.binding.agent",
            "binding agent is fixed pilot agent",
            (
                binding.get("agent")
                == args.agent
            ),
            {
                "actual":
                    binding.get(
                        "agent"
                    ),
                "expected":
                    args.agent,
            },
        )
    )

    try:
        actual_project = canonical(
            str(
                binding.get(
                    "project",
                    "",
                )
            )
        )

        expected_project = canonical(
            args.project
        )

        project_result: bool | None = (
            actual_project
            == expected_project
        )

        project_detail = {
            "actual":
                actual_project,
            "expected":
                expected_project,
        }

    except Exception as exc:
        project_result = None

        project_detail = {
            "error":
                str(exc),
        }

    checks.append(
        assertion(
            "N1.binding.project",
            "binding project is canonical gate repository",
            project_result,
            project_detail,
        )
    )

    generation = binding_generation_text(
        binding.get(
            "generation"
        )
    )

    checks.append(
        assertion(
            "N1.binding.generation",
            "binding generation matches expected generation",
            (
                None
                if generation is None
                else (
                    generation
                    == args.generation
                )
            ),
            {
                "actual":
                    binding.get(
                        "generation"
                    ),
                "expected":
                    args.generation,
            },
        )
    )

    session_id = binding.get(
        "sessionId"
    )

    session_valid = (
        isinstance(
            session_id,
            str,
        )
        and bool(
            UUID_RE.fullmatch(
                session_id
            )
        )
    )

    checks.append(
        assertion(
            "N1.binding.session",
            "binding sessionId is a non-empty UUID",
            session_valid,
            {
                "sessionId":
                    (
                        session_id
                        if isinstance(
                            session_id,
                            str,
                        )
                        else None
                    ),
            },
        )
    )

    if (
        args.expected_session
        is not None
    ):
        checks.append(
            assertion(
                "N1.binding.resume-session",
                "resume preserves fresh sessionId",
                (
                    session_valid
                    and session_id
                    == args.expected_session
                ),
                {
                    "actual":
                        session_id,
                    "expected":
                        args.expected_session,
                },
            )
        )

    process_pid = binding_pid_text(
        binding.get(
            "pid"
        )
    )

    checks.append(
        assertion(
            "N1.binding.pid",
            "binding PID matches launcher/native process PID",
            (
                None
                if process_pid is None
                else (
                    process_pid
                    == args.process_pid
                )
            ),
            {
                "actual":
                    binding.get(
                        "pid"
                    ),
                "expected":
                    args.process_pid,
            },
        )
    )

    # The binding is expected to contain the G4-A immutable identity fields.
    required_text_fields = (
        "pidStart",
        "profileDigest",
        "policyVersion",
        "guardDigest",
        "brokerDigest",
        "providerCommit",
    )

    for field in required_text_fields:
        value = binding.get(
            field
        )

        checks.append(
            assertion(
                f"N1.binding.{field}",
                f"binding {field} is present",
                (
                    isinstance(
                        value,
                        str,
                    )
                    and bool(value)
                ),
                {
                    "present":
                        (
                            isinstance(
                                value,
                                str,
                            )
                            and bool(value)
                        ),
                },
            )
        )

    verdict, status_code = (
        overall_status(
            checks
        )
    )

    write_json(
        args.output,
        {
            "schemaVersion": 1,
            "binding":
                str(path),
            "observedAt":
                utc_now(),
            "verdict":
                verdict,
            "checks":
                checks,
        },
    )

    return status_code


def find_option_value(
    argv: list[str],
    option: str,
) -> str | None:
    try:
        index = argv.index(
            option
        )
    except ValueError:
        return None

    if (
        index + 1
        >= len(argv)
    ):
        return None

    return argv[
        index + 1
    ]


def command_validate_process_command(
    args: argparse.Namespace,
) -> int:
    try:
        raw = pathlib.Path(
            args.command_file
        ).read_text(
            encoding="utf-8",
            errors="replace",
        )

    except Exception as exc:
        print(
            f"process command unreadable: {exc}",
            file=sys.stderr,
        )
        return 2

    raw = raw.strip()

    if not raw:
        return 2

    try:
        argv = shlex.split(
            raw,
            posix=True,
        )
    except ValueError:
        # ps output is observably present but not reliably parseable.
        return 2

    if not argv:
        return 2

    settings_real = canonical(
        args.settings
    )

    expected_settings_seen = False

    for index, word in enumerate(
        argv[:-1]
    ):
        if word != "--settings":
            continue

        try:
            actual_settings = canonical(
                argv[index + 1]
            )
        except Exception:
            continue

        if (
            actual_settings
            == settings_real
        ):
            expected_settings_seen = True
            break

    if args.mode == "fresh":
        session_seen = (
            find_option_value(
                argv,
                "--session-id",
            )
            == args.session_id
        )

        resume_seen = (
            "--resume"
            in argv
        )

        expected = (
            session_seen
            and not resume_seen
            and expected_settings_seen
        )

    elif args.mode == "resume":
        session_seen = (
            find_option_value(
                argv,
                "--resume",
            )
            == args.session_id
        )

        fresh_seen = (
            "--session-id"
            in argv
        )

        expected = (
            session_seen
            and not fresh_seen
            and expected_settings_seen
        )

    else:
        return 2

    if expected:
        return 0

    return 1


def json_tree_contains_session(
    value: Any,
    session_id: str,
) -> bool:
    if isinstance(
        value,
        dict,
    ):
        for key, child in value.items():
            if (
                key
                in (
                    "sessionId",
                    "session_id",
                    "sessionID",
                )
                and child
                == session_id
            ):
                return True

            if json_tree_contains_session(
                child,
                session_id,
            ):
                return True

        return False

    if isinstance(
        value,
        list,
    ):
        return any(
            json_tree_contains_session(
                child,
                session_id,
            )
            for child
            in value
        )

    return False


def transcript_has_session(
    path: pathlib.Path,
    session_id: str,
) -> bool:
    # Claude native transcripts are JSONL in current supported layouts.
    # We do not infer success merely from arbitrary raw text containing the
    # UUID; at least one parsed JSON record must carry session identity.
    try:
        with open(
            path,
            "r",
            encoding="utf-8",
            errors="strict",
        ) as fh:
            for line in fh:
                stripped = line.strip()

                if not stripped:
                    continue

                try:
                    value = json.loads(
                        stripped
                    )
                except json.JSONDecodeError:
                    continue

                if json_tree_contains_session(
                    value,
                    session_id,
                ):
                    return True

    except (
        OSError,
        UnicodeError,
    ):
        return False

    return False


def command_find_transcript(
    args: argparse.Namespace,
) -> int:
    if not UUID_RE.fullmatch(
        args.session_id
    ):
        return 2

    root = pathlib.Path(
        args.claude_config
    )

    if not root.is_dir():
        return 1

    matches: list[
        pathlib.Path
    ] = []

    try:
        for candidate in root.rglob(
            "*.jsonl"
        ):
            try:
                if (
                    not candidate.is_file()
                    or candidate.is_symlink()
                ):
                    continue

                # Fast positive candidate: many Claude layouts use session
                # UUID as transcript filename.
                if (
                    args.session_id
                    in candidate.name
                ):
                    if transcript_has_session(
                        candidate,
                        args.session_id,
                    ):
                        matches.append(
                            candidate
                        )
                    continue

                if transcript_has_session(
                    candidate,
                    args.session_id,
                ):
                    matches.append(
                        candidate
                    )

            except OSError:
                continue

    except OSError:
        return 2

    unique: list[
        pathlib.Path
    ] = []

    seen: set[str] = set()

    for candidate in matches:
        try:
            resolved = str(
                candidate.resolve(
                    strict=True
                )
            )
        except OSError:
            continue

        if resolved in seen:
            continue

        seen.add(
            resolved
        )

        unique.append(
            candidate
        )

    if len(unique) == 0:
        return 1

    # The runbook explicitly classifies multiple transcripts as unknown.
    if len(unique) != 1:
        print(
            "multiple native transcripts "
            f"found for session {args.session_id}",
            file=sys.stderr,
        )

        for candidate in unique:
            print(
                str(candidate),
                file=sys.stderr,
            )

        return 2

    print(
        str(unique[0])
    )

    return 0


def command_validate_state(
    args: argparse.Namespace,
) -> int:
    checks: list[
        dict[str, Any]
    ] = []

    try:
        binding_path_value = pathlib.Path(
            args.binding
        )

        binding = load_json(
            binding_path_value
        )

    except Exception as exc:
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "verdict":
                    "unknown",
                "reason":
                    f"latest binding unreadable: {exc}",
                "checks": [],
            },
        )

        return 2

    if not isinstance(
        binding,
        dict,
    ):
        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "verdict":
                    "unknown",
                "reason":
                    "latest binding root is not object",
                "checks": [],
            },
        )

        return 2

    generation = (
        binding_generation_text(
            binding.get(
                "generation"
            )
        )
    )

    checks.append(
        assertion(
            "N1.state.binding-generation",
            "latest resume binding is expected generation",
            (
                None
                if generation is None
                else (
                    generation
                    == str(
                        args.expected_generation
                    )
                )
            ),
            {
                "actual":
                    binding.get(
                        "generation"
                    ),
                "expected":
                    args.expected_generation,
            },
        )
    )

    session_id = binding.get(
        "sessionId"
    )

    checks.append(
        assertion(
            "N1.state.session",
            "latest resume binding preserves fresh session",
            (
                isinstance(
                    session_id,
                    str,
                )
                and session_id
                == args.expected_session
            ),
            {
                "actual":
                    session_id,
                "expected":
                    args.expected_session,
            },
        )
    )

    #
    # Resolve authoritative state.json.
    #
    # G4-A state normally contains latestGeneration/latestBinding, but
    # binding itself does not need to duplicate stateFile. Derive state.json
    # from the immutable binding's .../bindings/<generation>.json layout when
    # no explicit field exists.
    #
    state_candidate = (
        state_path_from_binding(
            binding
        )
    )

    if state_candidate is None:
        state_candidate = (
            binding_path_value
            .parent
            .parent
            / "state.json"
        )

    try:
        if state_candidate.is_symlink():
            raise ValueError(
                "state file is symlink"
            )

        state_value = load_json(
            state_candidate
        )

    except Exception as exc:
        checks.append(
            assertion(
                "N1.state.read",
                "pilot state.json is readable",
                None,
                {
                    "path":
                        str(
                            state_candidate
                        ),
                    "error":
                        str(exc),
                },
            )
        )

        verdict, status_code = (
            overall_status(
                checks
            )
        )

        write_json(
            args.output,
            {
                "schemaVersion": 1,
                "verdict":
                    verdict,
                "checks":
                    checks,
            },
        )

        return status_code

    if not isinstance(
        state_value,
        dict,
    ):
        checks.append(
            assertion(
                "N1.state.read",
                "pilot state.json is readable",
                None,
                {
                    "path":
                        str(
                            state_candidate
                        ),
                    "error":
                        "state root is not object",
                },
            )
        )

    else:
        checks.append(
            assertion(
                "N1.state.read",
                "pilot state.json is readable",
                True,
                {
                    "path":
                        str(
                            state_candidate
                        ),
                },
            )
        )

        latest_generation = (
            binding_generation_text(
                state_value.get(
                    "latestGeneration"
                )
            )
        )

        checks.append(
            assertion(
                "N1.state.latest-generation",
                "state latestGeneration is resume generation",
                (
                    None
                    if latest_generation
                    is None
                    else (
                        latest_generation
                        == str(
                            args.expected_generation
                        )
                    )
                ),
                {
                    "actual":
                        state_value.get(
                            "latestGeneration"
                        ),
                    "expected":
                        args.expected_generation,
                },
            )
        )

        latest_binding = (
            state_value.get(
                "latestBinding"
            )
        )

        if isinstance(
            latest_binding,
            str,
        ) and latest_binding:
            try:
                latest_binding_real = (
                    canonical(
                        latest_binding
                    )
                )

                expected_binding_real = (
                    canonical(
                        binding_path_value
                    )
                )

                latest_binding_match: (
                    bool | None
                ) = (
                    latest_binding_real
                    == expected_binding_real
                )

                latest_binding_detail = {
                    "actual":
                        latest_binding_real,
                    "expected":
                        expected_binding_real,
                }

            except Exception as exc:
                latest_binding_match = None

                latest_binding_detail = {
                    "error":
                        str(exc),
                }

        else:
            latest_binding_match = None

            latest_binding_detail = {
                "actual":
                    latest_binding,
            }

        checks.append(
            assertion(
                "N1.state.latest-binding",
                "state latestBinding identifies resume binding",
                latest_binding_match,
                latest_binding_detail,
            )
        )

    verdict, status_code = (
        overall_status(
            checks
        )
    )

    write_json(
        args.output,
        {
            "schemaVersion": 1,
            "observedAt":
                utc_now(),
            "verdict":
                verdict,
            "checks":
                checks,
        },
    )

    return status_code


def command_write_n1_result(
    args: argparse.Namespace,
) -> int:
    if args.verdict not in (
        "pass",
        "fail",
        "unknown",
    ):
        die(
            "invalid N1 verdict"
        )

    write_json(
        args.output,
        {
            "schemaVersion": 1,
            "check":
                "N1",
            "verdict":
                args.verdict,
            "reason":
                args.reason,
            "observedAt":
                utc_now(),
        },
    )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue #396 pilot gate "
            "isolation helper"
        )
    )

    subparsers = (
        parser.add_subparsers(
            dest="command",
            required=True,
        )
    )

    command = (
        subparsers.add_parser(
            "new-run-id"
        )
    )

    command.set_defaults(
        handler=command_new_run_id
    )

    command = (
        subparsers.add_parser(
            "gate-team"
        )
    )

    command.add_argument(
        "run_id"
    )

    command.set_defaults(
        handler=command_gate_team
    )

    command = (
        subparsers.add_parser(
            "canonical"
        )
    )

    command.add_argument(
        "path"
    )

    command.set_defaults(
        handler=command_canonical
    )

    command = (
        subparsers.add_parser(
            "sha256"
        )
    )

    command.add_argument(
        "path"
    )

    command.set_defaults(
        handler=command_sha256
    )

    command = (
        subparsers.add_parser(
            "json-field"
        )
    )

    command.add_argument(
        "path"
    )

    command.add_argument(
        "field"
    )

    command.set_defaults(
        handler=command_json_field
    )

    command = (
        subparsers.add_parser(
            "write-state"
        )
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--run-id",
        required=True,
    )

    command.add_argument(
        "--run-root",
        required=True,
    )

    command.add_argument(
        "--source",
        required=True,
    )

    command.add_argument(
        "--live-skill-dir",
        required=True,
    )

    command.add_argument(
        "--artifact-dir",
        required=True,
    )

    command.add_argument(
        "--gate-team",
        required=True,
    )

    command.add_argument(
        "--gate-repo",
        required=True,
    )

    command.add_argument(
        "--gate-home",
        required=True,
    )

    command.add_argument(
        "--xdg-config",
        required=True,
    )

    command.add_argument(
        "--xdg-cache",
        required=True,
    )

    command.add_argument(
        "--xdg-data",
        required=True,
    )

    command.add_argument(
        "--xdg-state",
        required=True,
    )

    command.add_argument(
        "--claude-config",
        required=True,
    )

    command.add_argument(
        "--source-head",
        required=True,
    )

    command.set_defaults(
        handler=command_write_state
    )

    command = (
        subparsers.add_parser(
            "capture-environment"
        )
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--run-id",
        required=True,
    )

    command.add_argument(
        "--source",
        required=True,
    )

    command.add_argument(
        "--source-head",
        required=True,
    )

    command.add_argument(
        "--live-skill-dir",
        required=True,
    )

    command.add_argument(
        "--artifact-dir",
        required=True,
    )

    command.add_argument(
        "--run-root",
        required=True,
    )

    command.add_argument(
        "--gate-team",
        required=True,
    )

    command.add_argument(
        "--claude-bin",
        required=True,
    )

    command.add_argument(
        "--claude-resolved",
        required=True,
    )

    command.add_argument(
        "--claude-version",
        required=True,
    )

    command.add_argument(
        "--claude-digest",
        required=True,
    )

    command.add_argument(
        "--collector-cutoff-seconds",
        required=True,
        type=int,
    )

    command.set_defaults(
        handler=command_capture_environment
    )

    command = (
        subparsers.add_parser(
            "record-live-control"
        )
    )

    command.add_argument(
        "--directory",
        required=True,
    )

    command.add_argument(
        "--phase",
        required=True,
        choices=(
            "before",
            "after",
        ),
    )

    command.set_defaults(
        handler=command_record_live_control
    )

    command = (
        subparsers.add_parser(
            "preflight"
        )
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--run-id",
        required=True,
    )

    command.add_argument(
        "--run-root",
        required=True,
    )

    command.add_argument(
        "--source",
        required=True,
    )

    command.add_argument(
        "--live-repo",
        required=True,
    )

    command.add_argument(
        "--gate-repo",
        required=True,
    )

    command.add_argument(
        "--artifact-dir",
        required=True,
    )

    command.add_argument(
        "--gate-team",
        required=True,
    )

    command.add_argument(
        "--pilot-agent",
        required=True,
    )

    command.add_argument(
        "--pilot-type",
        required=True,
    )

    command.add_argument(
        "--gate-home",
        required=True,
    )

    command.add_argument(
        "--xdg-config",
        required=True,
    )

    command.add_argument(
        "--xdg-cache",
        required=True,
    )

    command.add_argument(
        "--xdg-data",
        required=True,
    )

    command.add_argument(
        "--xdg-state",
        required=True,
    )

    command.add_argument(
        "--claude-config",
        required=True,
    )

    command.add_argument(
        "--claude-bin",
        required=True,
    )

    command.set_defaults(
        handler=command_preflight
    )

    command = (
        subparsers.add_parser(
            "make-f2-probe"
        )
    )

    command.add_argument(
        "--run-id",
        required=True,
    )

    command.add_argument(
        "--gate-repo",
        required=True,
    )

    command.add_argument(
        "--target",
        required=True,
    )

    command.add_argument(
        "--program",
        required=True,
    )

    command.add_argument(
        "--manifest",
        required=True,
    )

    command.set_defaults(
        handler=command_make_f2_probe
    )

    command = (
        subparsers.add_parser(
            "f2-proof"
        )
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--run-id",
        required=True,
    )

    command.add_argument(
        "--run-root",
        required=True,
    )

    command.add_argument(
        "--gate-repo",
        required=True,
    )

    command.add_argument(
        "--live-repo",
        required=True,
    )

    command.add_argument(
        "--gate-home",
        required=True,
    )

    command.add_argument(
        "--xdg-config",
        required=True,
    )

    command.add_argument(
        "--probe-target",
        required=True,
    )

    command.add_argument(
        "--probe-program",
        required=True,
    )

    command.add_argument(
        "--probe-manifest",
        required=True,
    )

    command.set_defaults(
        handler=command_f2_proof
    )

    command = (
        subparsers.add_parser(
            "find-binding"
        )
    )

    command.add_argument(
        "--gate-repo",
        required=True,
    )

    command.add_argument(
        "--team",
        required=True,
    )

    command.add_argument(
        "--agent",
        required=True,
    )

    command.add_argument(
        "--generation",
        required=True,
    )

    command.set_defaults(
        handler=command_find_binding
    )

    command = (
        subparsers.add_parser(
            "validate-binding"
        )
    )

    command.add_argument(
        "--binding",
        required=True,
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--team",
        required=True,
    )

    command.add_argument(
        "--agent",
        required=True,
    )

    command.add_argument(
        "--project",
        required=True,
    )

    command.add_argument(
        "--generation",
        required=True,
    )

    command.add_argument(
        "--process-pid",
        required=True,
    )

    command.add_argument(
        "--expected-session",
        required=False,
    )

    command.set_defaults(
        handler=command_validate_binding
    )

    command = (
        subparsers.add_parser(
            "validate-process-command"
        )
    )

    command.add_argument(
        "--command-file",
        required=True,
    )

    command.add_argument(
        "--mode",
        required=True,
        choices=(
            "fresh",
            "resume",
        ),
    )

    command.add_argument(
        "--session-id",
        required=True,
    )

    command.add_argument(
        "--settings",
        required=True,
    )

    command.set_defaults(
        handler=command_validate_process_command
    )

    command = (
        subparsers.add_parser(
            "find-transcript"
        )
    )

    command.add_argument(
        "--claude-config",
        required=True,
    )

    command.add_argument(
        "--session-id",
        required=True,
    )

    command.set_defaults(
        handler=command_find_transcript
    )

    command = (
        subparsers.add_parser(
            "validate-state"
        )
    )

    command.add_argument(
        "--binding",
        required=True,
    )

    command.add_argument(
        "--expected-generation",
        required=True,
        type=int,
    )

    command.add_argument(
        "--expected-session",
        required=True,
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.set_defaults(
        handler=command_validate_state
    )

    command = (
        subparsers.add_parser(
            "write-n1-result"
        )
    )

    command.add_argument(
        "--output",
        required=True,
    )

    command.add_argument(
        "--verdict",
        required=True,
        choices=(
            "pass",
            "fail",
            "unknown",
        ),
    )

    command.add_argument(
        "--reason",
        required=True,
    )

    command.set_defaults(
        handler=command_write_n1_result
    )

    return parser


def main() -> int:
    parser = build_parser()

    args = parser.parse_args()

    try:
        result = args.handler(
            args
        )

    except KeyboardInterrupt:
        return 130

    except SystemExit:
        raise

    except Exception as exc:
        print(
            "pilot-gate-isolation: "
            f"internal exception: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return 2

    if result not in (
        0,
        1,
        2,
    ):
        print(
            "pilot-gate-isolation: "
            "handler returned invalid status "
            f"{result!r}",
            file=sys.stderr,
        )

        return 2

    return result


if __name__ == "__main__":
    raise SystemExit(
        main()
    )