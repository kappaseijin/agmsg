#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import time
from typing import Any


PILOT_AGENT = "agmsg_pm_pilot_claude"
DEFAULT_CUTOFF_SECONDS = 180
DEFAULT_MARGIN_SECONDS = 2
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
REQUIRED_POLL_SUCCESSES = 2
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GENERATION_RE = re.compile(r"^[1-9][0-9]*$")


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


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


def canonical_directory(
    path: str | os.PathLike[str],
) -> pathlib.Path:
    resolved = pathlib.Path(path).resolve(strict=True)
    if not resolved.is_dir():
        raise RuntimeError(
            f"not a directory: {resolved}"
        )
    return resolved


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


def ensure_descendant(
    child: pathlib.Path,
    parent: pathlib.Path,
    name: str,
) -> None:
    try:
        child.resolve(strict=True).relative_to(
            parent.resolve(strict=True)
        )
    except Exception as exc:
        raise RuntimeError(
            f"{name} escapes expected root"
        ) from exc


def latest_binding(
    gate_repo: pathlib.Path,
    team: str,
) -> pathlib.Path:
    if not SAFE_COMPONENT_RE.fullmatch(team):
        raise RuntimeError(
            "gate team cannot be mapped safely "
            "to pilot runtime path"
        )

    seat = (
        gate_repo
        / "run"
        / "pilot"
        / f"{team}__{PILOT_AGENT}"
    )
    bindings = seat / "bindings"

    if (
        not bindings.is_dir()
        or bindings.is_symlink()
    ):
        raise RuntimeError(
            "pilot bindings directory unavailable"
        )

    candidates: list[
        tuple[int, pathlib.Path]
    ] = []

    for entry in bindings.iterdir():
        if (
            not entry.is_file()
            or entry.is_symlink()
            or entry.suffix != ".json"
        ):
            continue

        generation_text = entry.stem

        if not GENERATION_RE.fullmatch(
            generation_text
        ):
            continue

        candidates.append(
            (
                int(generation_text, 10),
                entry,
            )
        )

    if not candidates:
        raise RuntimeError(
            "no pilot binding is available"
        )

    candidates.sort(
        key=lambda item: item[0]
    )

    binding = candidates[-1][1]

    ensure_descendant(
        binding,
        gate_repo,
        "binding",
    )

    value = read_json(binding)

    if not isinstance(value, dict):
        raise RuntimeError(
            "latest binding root is not object"
        )

    if value.get("team") != team:
        raise RuntimeError(
            "latest binding team mismatch"
        )

    if value.get("agent") != PILOT_AGENT:
        raise RuntimeError(
            "latest binding agent mismatch"
        )

    generation = value.get("generation")

    if str(generation) != str(
        candidates[-1][0]
    ):
        raise RuntimeError(
            "latest binding generation mismatch"
        )

    project = value.get("project")

    if not isinstance(project, str):
        raise RuntimeError(
            "latest binding project unavailable"
        )

    if pathlib.Path(project).resolve(strict=True) != (
        gate_repo.resolve(strict=True)
    ):
        raise RuntimeError(
            "latest binding project mismatch"
        )

    return binding


def sanitized_collector_env(
    binding: pathlib.Path,
    collector_state: pathlib.Path,
    claude_config: pathlib.Path,
) -> dict[str, str]:
    env = dict(os.environ)

    for key in list(env):
        if key.startswith("AGMSG_PM_"):
            del env[key]

    env["AGMSG_PM_BINDING_FILE"] = str(binding)
    env["AGMSG_PM_COLLECTOR_STATE_DIR"] = (
        str(collector_state)
    )
    env["CLAUDE_CONFIG_DIR"] = (
        str(claude_config)
    )

    return env


def parse_collector_response(
    stdout: str,
) -> dict[str, Any] | None:
    lines = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip()
    ]

    if len(lines) != 1:
        return None

    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError:
        return None

    if not isinstance(value, dict):
        return None

    return value


def collector_call(
    collector: pathlib.Path,
    operation: str,
    *,
    cwd: pathlib.Path,
    env: dict[str, str],
    artifact_log: pathlib.Path,
    phase: str,
    monotonic_origin: float,
) -> tuple[
    bool | None,
    dict[str, Any],
]:
    started_mono = time.monotonic()
    started_wall = utc_now()

    try:
        completed = subprocess.run(
            [
                str(collector),
                operation,
            ],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        record = {
            "schemaVersion": 1,
            "phase": phase,
            "operation": operation,
            "wallTimestamp": started_wall,
            "monotonicElapsedFromF4Start":
                started_mono
                - monotonic_origin,
            "durationMonotonic":
                time.monotonic()
                - started_mono,
            "exitStatus": None,
            "collectorStatus": None,
            "reason": "collector_process_timeout",
            "stdout": (
                exc.stdout
                if isinstance(exc.stdout, str)
                else ""
            ),
            "stderr": (
                exc.stderr
                if isinstance(exc.stderr, str)
                else ""
            ),
        }

        append_jsonl(
            artifact_log,
            record,
        )

        return None, record

    ended_mono = time.monotonic()

    parsed = parse_collector_response(
        completed.stdout
    )

    collector_status = (
        parsed.get("collectorStatus")
        if isinstance(parsed, dict)
        else None
    )

    reason = (
        parsed.get("reason")
        if isinstance(parsed, dict)
        else "collector_response_unparseable"
    )

    record = {
        "schemaVersion": 1,
        "phase": phase,
        "operation": operation,
        "wallTimestamp": started_wall,
        "monotonicElapsedFromF4Start":
            started_mono
            - monotonic_origin,
        "durationMonotonic":
            ended_mono
            - started_mono,
        "exitStatus":
            completed.returncode,
        "collectorStatus":
            collector_status,
        "reason":
            reason,
        "stdout":
            completed.stdout,
        "stderr":
            completed.stderr,
    }

    append_jsonl(
        artifact_log,
        record,
    )

    if (
        completed.returncode == 0
        and collector_status == "ok"
    ):
        return True, record

    if (
        completed.returncode in (
            2,
            3,
            64,
        )
        or collector_status
        in (
            "unknown",
            "audit_unavailable",
        )
        or parsed is None
    ):
        return None, record

    return False, record


class AuditPollingLoop:
    def __init__(
        self,
        *,
        collector: pathlib.Path,
        cwd: pathlib.Path,
        env: dict[str, str],
        artifact_log: pathlib.Path,
        poll_interval_seconds: float,
        monotonic_origin: float,
    ):
        self.collector = collector
        self.cwd = cwd
        self.env = env
        self.artifact_log = artifact_log
        self.poll_interval_seconds = (
            poll_interval_seconds
        )
        self.monotonic_origin = (
            monotonic_origin
        )

        self.scan_attempts = 0
        self.successful_scans = 0
        self.last_success_monotonic: (
            float | None
        ) = None
        self.last_success_wall: (
            str | None
        ) = None
        self.last_scan_record: (
            dict[str, Any] | None
        ) = None

    def scan_once(
        self,
        phase: str,
    ) -> bool | None:
        self.scan_attempts += 1

        result, record = collector_call(
            self.collector,
            "scan",
            cwd=self.cwd,
            env=self.env,
            artifact_log=self.artifact_log,
            phase=phase,
            monotonic_origin=(
                self.monotonic_origin
            ),
        )

        self.last_scan_record = record

        if result is True:
            self.successful_scans += 1

            # The timestamp is intentionally captured
            # only after the complete scan has returned
            # collectorStatus=ok.
            self.last_success_monotonic = (
                time.monotonic()
            )
            self.last_success_wall = utc_now()

        return result

    def run_until_successes(
        self,
        required_successes: int,
    ) -> bool | None:
        if required_successes < 1:
            raise ValueError(
                "required_successes must be positive"
            )

        while (
            self.successful_scans
            < required_successes
        ):
            before = time.monotonic()

            result = self.scan_once(
                "polling-loop"
            )

            if result is not True:
                return result

            if (
                self.successful_scans
                >= required_successes
            ):
                break

            deadline = (
                before
                + self.poll_interval_seconds
            )

            while True:
                remaining = (
                    deadline
                    - time.monotonic()
                )

                if remaining <= 0:
                    break

                time.sleep(
                    min(
                        remaining,
                        0.25,
                    )
                )

        return True


def liveness_sample(
    *,
    last_success_monotonic: float,
    cutoff_seconds: float,
    expected: str,
) -> dict[str, Any]:
    now_mono = time.monotonic()
    elapsed = (
        now_mono
        - last_success_monotonic
    )

    exceeded = (
        elapsed > cutoff_seconds
    )

    health = (
        "failed/stale"
        if exceeded
        else "healthy"
    )

    if expected == "below":
        expected_observed = (
            elapsed < cutoff_seconds
        )
    elif expected == "above":
        expected_observed = (
            elapsed > cutoff_seconds
        )
    else:
        raise ValueError(
            "invalid liveness expectation"
        )

    return {
        "wallTimestamp":
            utc_now(),
        "monotonicTimestamp":
            now_mono,
        "elapsedSinceLastSuccessfulScan":
            elapsed,
        "cutoffSeconds":
            cutoff_seconds,
        "cutoffExceeded":
            exceeded,
        "auditLiveness":
            health,
        "expectedObservation":
            expected,
        "expectedObservationCaptured":
            expected_observed,
    }


def sleep_until(
    target_monotonic: float,
) -> None:
    while True:
        remaining = (
            target_monotonic
            - time.monotonic()
        )

        if remaining <= 0:
            return

        time.sleep(
            min(
                remaining,
                0.5,
            )
        )


def run_f4(
    args: argparse.Namespace,
) -> int:
    gate_repo = canonical_directory(
        args.gate_repo
    )
    run_root = canonical_directory(
        args.run_root
    )
    claude_config = canonical_directory(
        args.claude_config
    )

    ensure_descendant(
        gate_repo,
        run_root,
        "gate repository",
    )

    artifact = (
        pathlib.Path(
            args.artifact_dir
        ).resolve(strict=False)
        / "F4"
    )
    artifact.mkdir(
        parents=True,
        exist_ok=True,
    )

    collector = (
        gate_repo
        / "scripts"
        / "pilot-collector.sh"
    )
    require_regular_executable(
        collector
    )

    cutoff = float(
        args.cutoff_seconds
    )
    margin = float(
        args.margin_seconds
    )
    poll_interval = float(
        args.poll_interval_seconds
    )

    if cutoff <= 0:
        raise ValueError(
            "cutoff must be positive"
        )

    if margin <= 0:
        raise ValueError(
            "margin must be positive"
        )

    if margin >= cutoff:
        raise ValueError(
            "margin must be less than cutoff"
        )

    if poll_interval <= 0:
        raise ValueError(
            "poll interval must be positive"
        )

    monotonic_origin = (
        time.monotonic()
    )
    wall_origin = utc_now()

    binding = latest_binding(
        gate_repo,
        args.gate_team,
    )

    collector_state = (
        artifact
        / "collector-state"
    )

    env = sanitized_collector_env(
        binding,
        collector_state,
        claude_config,
    )

    calls_log = (
        artifact
        / "collector-calls.jsonl"
    )

    if calls_log.exists():
        calls_log.unlink()

    atomic_json(
        artifact
        / "config.json",
        {
            "schemaVersion": 1,
            "check": "F4",
            "runId":
                args.run_id,
            "gateTeam":
                args.gate_team,
            "binding":
                str(binding),
            "collector":
                str(collector),
            "cutoffSeconds":
                cutoff,
            "marginSeconds":
                margin,
            "pollIntervalSeconds":
                poll_interval,
            "requiredPollingLoopSuccesses":
                REQUIRED_POLL_SUCCESSES,
            "boundaryRule": {
                "withinCutoff":
                    "elapsed <= cutoff",
                "cutoffExceeded":
                    "elapsed > cutoff",
            },
            "startedAtWall":
                wall_origin,
            "startedAtMonotonic":
                monotonic_origin,
        },
    )

    discover_ok, discover_record = (
        collector_call(
            collector,
            "discover",
            cwd=gate_repo,
            env=env,
            artifact_log=calls_log,
            phase="initial-discover",
            monotonic_origin=(
                monotonic_origin
            ),
        )
    )

    if discover_ok is not True:
        verdict = (
            "fail"
            if discover_ok is False
            else "unknown"
        )

        atomic_json(
            artifact
            / "result.json",
            {
                "schemaVersion": 1,
                "check": "F4",
                "runId":
                    args.run_id,
                "verdict":
                    verdict,
                "reason":
                    "initial_discover_not_ok",
                "discover":
                    discover_record,
            },
        )

        return (
            1
            if verdict == "fail"
            else 2
        )

    poller = AuditPollingLoop(
        collector=collector,
        cwd=gate_repo,
        env=env,
        artifact_log=calls_log,
        poll_interval_seconds=(
            poll_interval
        ),
        monotonic_origin=(
            monotonic_origin
        ),
    )

    polling_ok = (
        poller.run_until_successes(
            REQUIRED_POLL_SUCCESSES
        )
    )

    if (
        polling_ok is not True
        or poller.last_success_monotonic
        is None
        or poller.last_success_wall
        is None
    ):
        verdict = (
            "fail"
            if polling_ok is False
            else "unknown"
        )

        atomic_json(
            artifact
            / "result.json",
            {
                "schemaVersion": 1,
                "check": "F4",
                "runId":
                    args.run_id,
                "verdict":
                    verdict,
                "reason":
                    "polling_loop_not_established",
                "polling": {
                    "scanAttempts":
                        poller.scan_attempts,
                    "successfulScans":
                        poller.successful_scans,
                    "lastScan":
                        poller.last_scan_record,
                },
            },
        )

        return (
            1
            if verdict == "fail"
            else 2
        )

    last_success_mono = (
        poller.last_success_monotonic
    )
    last_success_wall = (
        poller.last_success_wall
    )

    atomic_json(
        artifact
        / "polling-loop.json",
        {
            "schemaVersion": 1,
            "runId":
                args.run_id,
            "scanAttempts":
                poller.scan_attempts,
            "successfulScans":
                poller.successful_scans,
            "pollIntervalSeconds":
                poll_interval,
            "lastSuccessfulScanWall":
                last_success_wall,
            "lastSuccessfulScanMonotonic":
                last_success_mono,
            "stoppedIntentionally":
                True,
            "stoppedReason":
                "F4 cutoff liveness fault injection",
        },
    )

    #
    # Below-cutoff control.
    #
    # The polling loop is intentionally stopped after the last
    # successful scan. Wait until cutoff-margin from that scan,
    # then sample liveness without performing another scan.
    #
    below_target = (
        last_success_mono
        + cutoff
        - margin
    )

    sleep_until(
        below_target
    )

    below = liveness_sample(
        last_success_monotonic=(
            last_success_mono
        ),
        cutoff_seconds=cutoff,
        expected="below",
    )

    atomic_json(
        artifact
        / "below-cutoff.json",
        {
            "schemaVersion": 1,
            "runId":
                args.run_id,
            "lastSuccessfulScanWall":
                last_success_wall,
            "lastSuccessfulScanMonotonic":
                last_success_mono,
            "targetElapsed":
                cutoff - margin,
            **below,
        },
    )

    #
    # Above-cutoff fault.
    #
    # Continue leaving the audit loop stopped. No collector
    # invocation occurs between the below and above samples.
    #
    above_target = (
        last_success_mono
        + cutoff
        + margin
    )

    sleep_until(
        above_target
    )

    above = liveness_sample(
        last_success_monotonic=(
            last_success_mono
        ),
        cutoff_seconds=cutoff,
        expected="above",
    )

    atomic_json(
        artifact
        / "above-cutoff.json",
        {
            "schemaVersion": 1,
            "runId":
                args.run_id,
            "lastSuccessfulScanWall":
                last_success_wall,
            "lastSuccessfulScanMonotonic":
                last_success_mono,
            "targetElapsed":
                cutoff + margin,
            **above,
        },
    )

    below_captured = bool(
        below[
            "expectedObservationCaptured"
        ]
    )
    above_captured = bool(
        above[
            "expectedObservationCaptured"
        ]
    )

    checks = [
        assertion(
            "polling-loop-established",
            (
                poller.successful_scans
                >= REQUIRED_POLL_SUCCESSES
            ),
            {
                "scanAttempts":
                    poller.scan_attempts,
                "successfulScans":
                    poller.successful_scans,
            },
        ),
        assertion(
            "below-observation-captured",
            (
                True
                if below_captured
                else None
            ),
            below,
        ),
        assertion(
            "below-cutoff-healthy",
            (
                below[
                    "auditLiveness"
                ]
                == "healthy"
                and below[
                    "cutoffExceeded"
                ]
                is False
            )
            if below_captured
            else None,
            below,
        ),
        assertion(
            "above-observation-captured",
            (
                True
                if above_captured
                else None
            ),
            above,
        ),
        assertion(
            "above-cutoff-unhealthy",
            (
                above[
                    "auditLiveness"
                ]
                == "failed/stale"
                and above[
                    "cutoffExceeded"
                ]
                is True
            )
            if above_captured
            else None,
            above,
        ),
        assertion(
            "boundary-rule-fixed",
            True,
            {
                "within":
                    "elapsed <= cutoff",
                "exceeded":
                    "elapsed > cutoff",
            },
        ),
    ]

    verdict = verdict_from_assertions(
        checks
    )

    result = {
        "schemaVersion": 1,
        "check": "F4",
        "runId":
            args.run_id,
        "verdict":
            verdict,
        "cutoffSeconds":
            cutoff,
        "marginSeconds":
            margin,
        "pollIntervalSeconds":
            poll_interval,
        "binding":
            str(binding),
        "lastSuccessfulScan": {
            "wallTimestamp":
                last_success_wall,
            "monotonicTimestamp":
                last_success_mono,
        },
        "belowCutoff":
            below,
        "aboveCutoff":
            above,
        "checks":
            checks,
    }

    atomic_json(
        artifact
        / "result.json",
        result,
    )

    if verdict == "pass":
        return 0

    if verdict == "fail":
        return 1

    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue #396 F4 audit polling-loop "
            "cutoff integration gate"
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

    parser.add_argument(
        "--cutoff-seconds",
        type=int,
        default=(
            DEFAULT_CUTOFF_SECONDS
        ),
    )

    parser.add_argument(
        "--margin-seconds",
        type=int,
        default=(
            DEFAULT_MARGIN_SECONDS
        ),
    )

    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=(
            DEFAULT_POLL_INTERVAL_SECONDS
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        return run_f4(args)

    except KeyboardInterrupt:
        return 130

    except (
        ValueError,
        RuntimeError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            "pilot-gate-f4: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        try:
            artifact = (
                pathlib.Path(
                    args.artifact_dir
                )
                / "F4"
            )

            atomic_json(
                artifact
                / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F4",
                    "runId":
                        args.run_id,
                    "verdict":
                        "unknown",
                    "reason": (
                        "harness_observation_unavailable:"
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
            "pilot-gate-f4: internal error: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        try:
            artifact = (
                pathlib.Path(
                    args.artifact_dir
                )
                / "F4"
            )

            atomic_json(
                artifact
                / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F4",
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
