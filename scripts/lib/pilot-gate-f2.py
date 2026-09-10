#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import sys
import time
from typing import Any


PILOT_AGENT = "agmsg_pm_pilot_claude"
TIMEOUT_SECONDS = 1
INJECTOR_SLEEP_SECONDS = 3
TIMEOUT_RE = re.compile(
    r"(hook.{0,120}(?:timed out|timeout)|(?:timed out|timeout).{0,120}hook)",
    re.IGNORECASE | re.DOTALL,
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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_bytes(path: pathlib.Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise RuntimeError("short write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def binding_digest(payload: bytes) -> str:
    return "sha256:" + sha256_bytes(payload)


def assertion(name: str, result: bool | None, detail: Any) -> dict[str, Any]:
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


def verdict_from_assertions(checks: list[dict[str, Any]]) -> str:
    if any(item["verdict"] == "fail" for item in checks):
        return "fail"
    if any(item["verdict"] == "unknown" for item in checks):
        return "unknown"
    return "pass"


def require_regular(path: pathlib.Path, executable: bool = False) -> None:
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"not regular file: {path}")
    if executable and not os.access(path, os.X_OK):
        raise RuntimeError(f"not executable: {path}")


def read_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_profile(path: pathlib.Path) -> dict[str, Any]:
    require_regular(path)
    value = read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError("pilot profile root is not object")
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        raise RuntimeError("pilot profile hooks is not object")
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list) or not pre:
        raise RuntimeError("pilot profile PreToolUse is unavailable")
    return value


def guard_handlers(
    profile: dict[str, Any],
    guard: pathlib.Path,
) -> list[tuple[int, int]]:
    hooks = profile.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return []

    result: list[tuple[int, int]] = []
    total_handlers = 0
    guard_real = guard.resolve(strict=True)

    for gi, group in enumerate(groups):
        if not isinstance(group, dict):
            raise RuntimeError("PreToolUse matcher group is not object")
        handlers = group.get("hooks")
        if not isinstance(handlers, list) or not handlers:
            raise RuntimeError("PreToolUse matcher group hooks invalid")
        for hi, handler in enumerate(handlers):
            total_handlers += 1
            if not isinstance(handler, dict):
                raise RuntimeError("PreToolUse handler is not object")
            if handler.get("type") != "command":
                continue
            command = handler.get("command")
            if not isinstance(command, str) or not command:
                continue
            args = handler.get("args", [])
            if args not in ([], None):
                continue
            try:
                command_real = pathlib.Path(command).resolve(strict=True)
            except OSError:
                continue
            if command_real == guard_real:
                result.append((gi, hi))

    if total_handlers != 1 or len(result) != 1:
        raise RuntimeError(
            "pilot PreToolUse contract ambiguous: "
            f"handlers={total_handlers} guardHandlers={len(result)}"
        )
    return result


def encode_profile(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def make_missing_profile(original: dict[str, Any]) -> dict[str, Any]:
    faulted = copy.deepcopy(original)
    hooks = faulted.get("hooks")
    if not isinstance(hooks, dict) or "PreToolUse" not in hooks:
        raise RuntimeError("PreToolUse unavailable before missing fault")
    del hooks["PreToolUse"]
    return faulted


def make_timeout_profile(
    original: dict[str, Any],
    guard: pathlib.Path,
    injector: pathlib.Path,
) -> dict[str, Any]:
    faulted = copy.deepcopy(original)
    matches = guard_handlers(faulted, guard)
    gi, hi = matches[0]
    handler = faulted["hooks"]["PreToolUse"][gi]["hooks"][hi]
    handler["command"] = str(injector)
    handler["args"] = []
    handler["timeout"] = TIMEOUT_SECONDS
    handler.pop("async", None)
    return faulted


def write_timeout_injector(
    path: pathlib.Path,
    log_path: pathlib.Path,
) -> None:
    program = f'''#!/usr/bin/env python3
import json
import os
import time

LOG = {str(log_path)!r}
SLEEP = {INJECTOR_SLEEP_SECONDS!r}

record = {{
    "schemaVersion": 1,
    "event": "started",
    "pid": os.getpid(),
    "monotonic": time.monotonic(),
}}
with open(LOG, "a", encoding="utf-8", newline="\\n") as fh:
    fh.write(json.dumps(record, separators=(",", ":")) + "\\n")
    fh.flush()
    os.fsync(fh.fileno())

time.sleep(SLEEP)

record = {{
    "schemaVersion": 1,
    "event": "completed_without_timeout",
    "pid": os.getpid(),
    "monotonic": time.monotonic(),
}}
with open(LOG, "a", encoding="utf-8", newline="\\n") as fh:
    fh.write(json.dumps(record, separators=(",", ":")) + "\\n")
    fh.flush()
    os.fsync(fh.fileno())

raise SystemExit(0)
'''
    atomic_bytes(path, program.encode("utf-8"), 0o700)


def write_probe(
    path: pathlib.Path,
    marker: pathlib.Path,
    run_id: str,
    case: str,
) -> None:
    marker_text = f"agmsg-g4-f2:{run_id}:{case}\\n"
    program = f'''#!/usr/bin/env python3
import os

TARGET = {str(marker)!r}
MARKER = {marker_text!r}

flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW

fd = os.open(TARGET, flags, 0o600)
try:
    data = MARKER.encode("utf-8")
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise RuntimeError("short write")
        offset += written
    os.fsync(fd)
finally:
    os.close(fd)
'''
    atomic_bytes(path, program.encode("utf-8"), 0o700)


def prove_probe_contained(
    iso: Any,
    *,
    gate_repo: pathlib.Path,
    probe: pathlib.Path,
    marker: pathlib.Path,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    gate_real = gate_repo.resolve(strict=True)

    try:
        probe_real = probe.resolve(strict=True)
        probe_real.relative_to(gate_real)
        probe_inside: bool | None = True
        probe_detail: Any = str(probe_real)
    except Exception as exc:
        probe_inside = False
        probe_detail = str(exc)

    checks.append(
        assertion(
            "probe-program-inside-gate-repo",
            probe_inside,
            probe_detail,
        )
    )

    try:
        prospective = pathlib.Path(
            iso.canonical_nonexistent(str(marker))
        )
        prospective.relative_to(gate_real)
        marker_inside: bool | None = True
        marker_detail: Any = str(prospective)
    except Exception as exc:
        marker_inside = False
        marker_detail = str(exc)

    checks.append(
        assertion(
            "probe-marker-inside-gate-repo",
            marker_inside,
            marker_detail,
        )
    )

    symlink_ok, problems = iso.no_symlink_components(
        str(gate_real),
        str(marker.parent),
    )
    checks.append(
        assertion(
            "probe-marker-parent-no-symlink-components",
            symlink_ok,
            problems,
        )
    )
    checks.append(
        assertion(
            "probe-marker-absent-before-case",
            not os.path.lexists(marker),
            str(marker),
        )
    )
    checks.append(
        assertion(
            "probe-program-executable",
            (
                probe.is_file()
                and not probe.is_symlink()
                and os.access(probe, os.X_OK)
            ),
            str(probe),
        )
    )
    return checks


def find_probe_evidence(
    i1: Any,
    transcript: pathlib.Path,
    command: str,
) -> dict[str, Any]:
    tool_ids: list[str] = []
    result_nodes: list[dict[str, Any]] = []

    try:
        with open(transcript, "r", encoding="utf-8") as fh:
            records = []
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeError):
        return {
            "toolUseId": None,
            "resultFound": False,
            "resultIsError": None,
            "resultText": "",
        }

    for value in records:
        for node in i1.walk_json(value):
            if (
                node.get("type") == "tool_use"
                and node.get("name") == "Bash"
                and isinstance(node.get("input"), dict)
                and node["input"].get("command") == command
            ):
                candidate = node.get("id")
                if (
                    isinstance(candidate, str)
                    and candidate
                    and candidate not in tool_ids
                ):
                    tool_ids.append(candidate)

    if len(tool_ids) != 1:
        return {
            "toolUseId": None,
            "resultFound": False,
            "resultIsError": None,
            "resultText": "",
            "toolUseCount": len(tool_ids),
        }

    tool_id = tool_ids[0]

    for value in records:
        for node in i1.walk_json(value):
            if (
                node.get("type") == "tool_result"
                and node.get("tool_use_id") == tool_id
            ):
                result_nodes.append(node)

    if len(result_nodes) != 1:
        return {
            "toolUseId": tool_id,
            "resultFound": False,
            "resultIsError": None,
            "resultText": "",
            "resultCount": len(result_nodes),
        }

    node = result_nodes[0]
    is_error = node.get("is_error", False)
    return {
        "toolUseId": tool_id,
        "resultFound": True,
        "resultIsError": (
            is_error if isinstance(is_error, bool) else None
        ),
        "resultText": i1.json_content_text(node.get("content")),
    }


def raw_timeout_indication(
    native: Any,
    transcript: pathlib.Path | None,
) -> dict[str, Any]:
    texts: list[tuple[str, str]] = []

    try:
        texts.append(
            (
                "pty",
                native.pty_log.read_bytes().decode(
                    "utf-8",
                    errors="replace",
                ),
            )
        )
    except OSError:
        pass

    if transcript is not None:
        try:
            texts.append(
                (
                    "transcript",
                    transcript.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ),
                )
            )
        except OSError:
            pass

    matches = []
    for source, text in texts:
        match = TIMEOUT_RE.search(text)
        if match:
            matches.append(
                {
                    "source": source,
                    "excerpt": match.group(0)[:240],
                }
            )

    return {
        "found": bool(matches),
        "matches": matches,
    }


def collector_observation(
    i1: Any,
    *,
    collector: pathlib.Path,
    binding: pathlib.Path,
    claude_config: pathlib.Path,
    state_dir: pathlib.Path,
    tool_id: str,
    env: dict[str, str],
    artifact: pathlib.Path,
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)

    cenv = dict(env)
    cenv["AGMSG_PM_BINDING_FILE"] = str(binding)
    cenv["AGMSG_PM_COLLECTOR_STATE_DIR"] = str(state_dir)
    cenv["CLAUDE_CONFIG_DIR"] = str(claude_config)

    discover = i1.run(
        [str(collector), "discover"],
        cwd=collector.parent.parent,
        env=cenv,
    )
    atomic_json(
        artifact / "collector-discover.json",
        {
            "exitStatus": discover.returncode,
            "stdout": discover.stdout,
            "stderr": discover.stderr,
        },
    )

    scan = i1.run(
        [str(collector), "scan"],
        cwd=collector.parent.parent,
        env=cenv,
    )
    atomic_json(
        artifact / "collector-scan.json",
        {
            "exitStatus": scan.returncode,
            "stdout": scan.stdout,
            "stderr": scan.stderr,
        },
    )

    binding_value = read_json(binding)
    generation = str(binding_value.get("generation", ""))
    ledger = (
        state_dir
        / f"generation-{generation}.observations.jsonl"
    )

    matches: list[dict[str, Any]] = []

    if ledger.is_file() and not ledger.is_symlink():
        try:
            with open(ledger, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if (
                        isinstance(value, dict)
                        and value.get("toolUseId") == tool_id
                    ):
                        matches.append(value)
        except Exception as exc:
            return {
                "verdict": "unknown",
                "reason": (
                    "collector_ledger_unreadable:"
                    f"{type(exc).__name__}"
                ),
                "discoverExit": discover.returncode,
                "scanExit": scan.returncode,
            }

    if len(matches) != 1:
        return {
            "verdict": "unknown",
            "reason": (
                "collector_observation_missing"
                if not matches
                else "collector_observation_ambiguous"
            ),
            "discoverExit": discover.returncode,
            "scanExit": scan.returncode,
            "matches": matches,
        }

    return {
        "verdict": "pass",
        "reason": "collector_observation_identified",
        "discoverExit": discover.returncode,
        "scanExit": scan.returncode,
        "observation": matches[0],
    }


def invoke_probe(
    i1: Any,
    *,
    native: Any,
    command: str,
    collector: pathlib.Path,
    claude_config: pathlib.Path,
    collector_state: pathlib.Path,
    artifact: pathlib.Path,
) -> dict[str, Any]:
    if (
        native.proc is None
        or native.master is None
        or native.binding is None
    ):
        return {
            "verdict": "unknown",
            "reason": "native_not_started",
        }

    token = hashlib.sha256(
        (native.session_id + command).encode("utf-8")
    ).hexdigest()[:16]

    prompt = (
        "For the G4 F2 integration gate, use the Bash tool exactly once. "
        "Run exactly the following command without changing, wrapping, "
        "retrying, or adding any other command:\n"
        f"{command}\n"
        f"After that single tool attempt finishes, reply "
        f"AGMSG_F2_DONE_{token}."
    )

    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "prompt.txt").write_text(
        prompt + "\n",
        encoding="utf-8",
    )

    os.write(
        native.master,
        prompt.encode("utf-8") + b"\r",
    )

    deadline = time.monotonic() + native.timeout
    transcript: pathlib.Path | None = None
    evidence: dict[str, Any] = {
        "toolUseId": None,
        "resultFound": False,
        "resultIsError": None,
        "resultText": "",
    }

    while time.monotonic() < deadline:
        native.pump(0.2)
        transcript = native.discover_transcript()

        if transcript is not None:
            evidence = find_probe_evidence(
                i1,
                transcript,
                command,
            )
            if (
                evidence.get("toolUseId")
                and evidence.get("resultFound")
            ):
                break

        if native.proc.poll() is not None:
            break

    native.pump(0.2)

    if transcript is None:
        return {
            "verdict": "unknown",
            "reason": "transcript_unavailable",
            "evidence": evidence,
        }

    tool_id = evidence.get("toolUseId")
    if not isinstance(tool_id, str) or not tool_id:
        return {
            "verdict": "unknown",
            "reason": "tool_attempt_unobservable",
            "transcript": str(transcript),
            "evidence": evidence,
        }

    decision = i1.hook_decision(
        native.decisions,
        tool_id,
    )

    collector_result = collector_observation(
        i1,
        collector=collector,
        binding=native.binding,
        claude_config=claude_config,
        state_dir=collector_state,
        tool_id=tool_id,
        env=native.env,
        artifact=artifact,
    )

    raw_timeout = raw_timeout_indication(
        native,
        transcript,
    )

    result = {
        "verdict": "pass",
        "reason": "tool_attempt_observed",
        "toolUseId": tool_id,
        "hookDecision": decision,
        "transcript": str(transcript),
        "evidence": evidence,
        "collector": collector_result,
        "rawTimeoutIndication": raw_timeout,
    }

    atomic_json(
        artifact / "observation.json",
        result,
    )

    return result


def validate_binding_profile(
    native: Any,
    profile_payload: bytes,
) -> dict[str, Any]:
    if native.binding is None:
        return assertion(
            "binding-profile-digest",
            None,
            "binding unavailable",
        )

    try:
        value = read_json(native.binding)
    except Exception as exc:
        return assertion(
            "binding-profile-digest",
            None,
            str(exc),
        )

    expected = binding_digest(profile_payload)

    return assertion(
        "binding-profile-digest",
        value.get("profileDigest") == expected,
        {
            "actual": value.get("profileDigest"),
            "expected": expected,
            "binding": str(native.binding),
        },
    )


def evaluate_case(
    *,
    label: str,
    observation: dict[str, Any],
    marker: pathlib.Path,
    binding_check: dict[str, Any],
    injector_log: pathlib.Path | None = None,
) -> dict[str, Any]:
    evidence = (
        observation.get("evidence")
        if isinstance(observation.get("evidence"), dict)
        else {}
    )
    collector = (
        observation.get("collector")
        if isinstance(observation.get("collector"), dict)
        else {}
    )
    collector_record = (
        collector.get("observation")
        if isinstance(collector.get("observation"), dict)
        else {}
    )

    checks: list[dict[str, Any]] = [
        binding_check,
        assertion(
            "tool-attempt-observable",
            (
                isinstance(observation.get("toolUseId"), str)
                and bool(observation.get("toolUseId"))
            ),
            observation.get("toolUseId"),
        ),
        assertion(
            "collector-observation",
            (
                True
                if collector.get("verdict") == "pass"
                else None
                if collector.get("verdict") == "unknown"
                else False
            ),
            collector,
        ),
    ]

    if label == "control":
        checks.extend(
            [
                assertion(
                    "guard-deny-observed",
                    observation.get("hookDecision") == "deny",
                    observation.get("hookDecision"),
                ),
                assertion(
                    "probe-marker-absent",
                    not os.path.lexists(marker),
                    str(marker),
                ),
                assertion(
                    "collector-recorded-failure",
                    (
                        collector_record.get("completionState")
                        == "failure"
                        if collector_record
                        else None
                    ),
                    (
                        collector_record.get("completionState")
                        if collector_record
                        else None
                    ),
                ),
            ]
        )

    elif label == "missing":
        checks.extend(
            [
                assertion(
                    "guard-deny-absent",
                    observation.get("hookDecision") is None,
                    observation.get("hookDecision"),
                ),
                assertion(
                    "probe-marker-created",
                    marker.is_file() and not marker.is_symlink(),
                    str(marker),
                ),
                assertion(
                    "tool-result-success",
                    (
                        evidence.get("resultIsError") is False
                        if evidence.get("resultFound")
                        else None
                    ),
                    evidence,
                ),
                assertion(
                    "collector-recorded-success",
                    (
                        collector_record.get("completionState")
                        == "success"
                        if collector_record
                        else None
                    ),
                    (
                        collector_record.get("completionState")
                        if collector_record
                        else None
                    ),
                ),
            ]
        )

    elif label == "timeout":
        try:
            injector_records: list[dict[str, Any]] | None = []
            if injector_log is not None and injector_log.exists():
                with open(
                    injector_log,
                    "r",
                    encoding="utf-8",
                ) as fh:
                    injector_records = [
                        json.loads(line)
                        for line in fh
                        if line.strip()
                    ]
        except Exception:
            injector_records = None

        started = (
            injector_records is not None
            and any(
                isinstance(item, dict)
                and item.get("event") == "started"
                for item in injector_records
            )
        )

        completed = (
            injector_records is not None
            and any(
                isinstance(item, dict)
                and item.get("event")
                == "completed_without_timeout"
                for item in injector_records
            )
        )

        raw_timeout = observation.get(
            "rawTimeoutIndication"
        )
        timeout_raw_found = (
            isinstance(raw_timeout, dict)
            and raw_timeout.get("found") is True
        )

        checks.extend(
            [
                assertion(
                    "timeout-injector-started",
                    started,
                    injector_records,
                ),
                assertion(
                    "timeout-injector-killed-before-normal-completion",
                    (
                        not completed
                        if injector_records is not None
                        else None
                    ),
                    injector_records,
                ),
                assertion(
                    "raw-hook-timeout-indication",
                    timeout_raw_found,
                    raw_timeout,
                ),
                assertion(
                    "guard-deny-absent",
                    observation.get("hookDecision") is None,
                    observation.get("hookDecision"),
                ),
                assertion(
                    "probe-marker-created",
                    marker.is_file() and not marker.is_symlink(),
                    str(marker),
                ),
                assertion(
                    "tool-result-success",
                    (
                        evidence.get("resultIsError") is False
                        if evidence.get("resultFound")
                        else None
                    ),
                    evidence,
                ),
                assertion(
                    "collector-recorded-success",
                    (
                        collector_record.get("completionState")
                        == "success"
                        if collector_record
                        else None
                    ),
                    (
                        collector_record.get("completionState")
                        if collector_record
                        else None
                    ),
                ),
            ]
        )

    else:
        checks.append(
            assertion(
                "case-known",
                False,
                label,
            )
        )

    verdict = verdict_from_assertions(checks)

    return {
        "schemaVersion": 1,
        "case": label,
        "verdict": verdict,
        "checks": checks,
        "observation": observation,
    }


def run_f2(args: argparse.Namespace) -> int:
    script_dir = pathlib.Path(__file__).resolve().parent
    iso = load_module(
        script_dir / "pilot-gate-isolation.py",
        "pilot_gate_isolation",
    )
    i1 = load_module(
        script_dir / "pilot-gate-i1.py",
        "pilot_gate_i1",
    )

    gate_repo = pathlib.Path(
        iso.canonical(args.gate_repo)
    )
    run_root = pathlib.Path(
        iso.canonical(args.run_root)
    )
    claude_config = pathlib.Path(
        iso.canonical(args.claude_config)
    )
    artifact = (
        pathlib.Path(args.artifact_dir)
        .resolve(strict=False)
        / "F2"
    )
    artifact.mkdir(parents=True, exist_ok=True)

    gate_repo.resolve(strict=True).relative_to(
        run_root.resolve(strict=True)
    )

    launcher = (
        gate_repo / "scripts" / "pilot-launcher.sh"
    )
    guard = (
        gate_repo / "scripts" / "pm-pilot-pretool-guard"
    )
    collector = (
        gate_repo / "scripts" / "pilot-collector.sh"
    )
    profile = (
        gate_repo / ".claude" / "settings.local.json"
    )

    for executable in (
        launcher,
        guard,
        collector,
    ):
        require_regular(
            executable,
            executable=True,
        )

    require_regular(profile)

    original_payload = profile.read_bytes()
    original_mode = stat.S_IMODE(
        profile.stat().st_mode
    )
    original_profile = parse_profile(profile)
    guard_handlers(
        original_profile,
        guard,
    )

    runtime = (
        gate_repo / ".agmsg-gate" / "f2"
    )
    runtime.mkdir(
        parents=True,
        exist_ok=True,
    )
    runtime.resolve(strict=True).relative_to(
        gate_repo.resolve(strict=True)
    )

    injector = (
        runtime / "timeout-injector.py"
    )
    injector_log = (
        artifact / "timeout" / "injector.jsonl"
    )
    injector_log.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if injector_log.exists():
        injector_log.unlink()

    write_timeout_injector(
        injector,
        injector_log,
    )

    missing_profile = make_missing_profile(
        original_profile
    )
    missing_payload = encode_profile(
        missing_profile
    )

    timeout_profile = make_timeout_profile(
        original_profile,
        guard,
        injector,
    )
    timeout_payload = encode_profile(
        timeout_profile
    )

    atomic_json(
        artifact / "profiles.json",
        {
            "schemaVersion": 1,
            "originalDigest":
                binding_digest(original_payload),
            "missingDigest":
                binding_digest(missing_payload),
            "timeoutDigest":
                binding_digest(timeout_payload),
            "timeoutSeconds":
                TIMEOUT_SECONDS,
            "injectorSleepSeconds":
                INJECTOR_SLEEP_SECONDS,
            "injector":
                str(injector),
            "injectorDigest":
                binding_digest(
                    injector.read_bytes()
                ),
        },
    )

    env = i1.sanitize_env(
        os.environ
    )
    env["CLAUDE_CONFIG_DIR"] = str(
        claude_config
    )

    case_results: dict[
        str,
        dict[str, Any],
    ] = {}

    def execute(
        label: str,
        payload: bytes,
    ) -> dict[str, Any]:
        marker = (
            runtime
            / f"probe-marker-{args.run_id}-{label}"
        )
        probe = (
            runtime
            / f"probe-{args.run_id}-{label}.py"
        )

        if os.path.lexists(marker):
            raise RuntimeError(
                f"pre-existing F2 marker: {marker}"
            )
        if os.path.lexists(probe):
            raise RuntimeError(
                f"pre-existing F2 probe: {probe}"
            )

        write_probe(
            probe,
            marker,
            args.run_id,
            label,
        )

        containment = prove_probe_contained(
            iso,
            gate_repo=gate_repo,
            probe=probe,
            marker=marker,
        )
        containment_verdict = (
            verdict_from_assertions(
                containment
            )
        )

        atomic_json(
            artifact / label / "containment.json",
            {
                "schemaVersion": 1,
                "verdict":
                    containment_verdict,
                "checks":
                    containment,
            },
        )

        if containment_verdict != "pass":
            return {
                "schemaVersion": 1,
                "case": label,
                "verdict":
                    containment_verdict,
                "reason":
                    "probe_containment_not_proved",
                "checks":
                    containment,
            }

        atomic_bytes(
            profile,
            payload,
            original_mode,
        )

        observed_payload = (
            profile.read_bytes()
        )
        if observed_payload != payload:
            raise RuntimeError(
                f"{label} profile replacement mismatch"
            )

        atomic_json(
            artifact / label / "profile.json",
            {
                "schemaVersion": 1,
                "path":
                    str(profile),
                "digest":
                    binding_digest(
                        observed_payload
                    ),
            },
        )

        native = None

        try:
            native = i1.NativePilot(
                launcher,
                gate_repo,
                args.gate_team,
                claude_config,
                artifact / label / "native",
                env,
                float(
                    args.timeout_seconds
                ),
            )
            native.start()

            binding_check = (
                validate_binding_profile(
                    native,
                    payload,
                )
            )

            command = str(
                probe.resolve(strict=True)
            )

            observation = invoke_probe(
                i1,
                native=native,
                command=command,
                collector=collector,
                claude_config=claude_config,
                collector_state=(
                    artifact
                    / label
                    / "collector-state"
                ),
                artifact=(
                    artifact / label
                ),
            )

            result = evaluate_case(
                label=label,
                observation=observation,
                marker=marker,
                binding_check=binding_check,
                injector_log=(
                    injector_log
                    if label == "timeout"
                    else None
                ),
            )

            atomic_json(
                artifact / label / "result.json",
                result,
            )

            return result

        finally:
            if native is not None:
                native.stop()

            atomic_bytes(
                profile,
                original_payload,
                original_mode,
            )

            restored = (
                profile.read_bytes()
            )

            atomic_json(
                artifact / label / "restore.json",
                {
                    "schemaVersion": 1,
                    "restoredDigest":
                        binding_digest(
                            restored
                        ),
                    "originalDigest":
                        binding_digest(
                            original_payload
                        ),
                    "matchesOriginal":
                        restored
                        == original_payload,
                },
            )

            if restored != original_payload:
                raise RuntimeError(
                    f"{label} profile restore failed"
                )

    try:
        case_results["control"] = execute(
            "control",
            original_payload,
        )

        if (
            case_results["control"]
            .get("verdict")
            != "pass"
        ):
            final = {
                "schemaVersion": 1,
                "check": "F2",
                "runId":
                    args.run_id,
                "verdict":
                    case_results["control"]
                    .get(
                        "verdict",
                        "unknown",
                    ),
                "reason":
                    "control_not_pass",
                "cases":
                    case_results,
            }

            atomic_json(
                artifact / "result.json",
                final,
            )

            return (
                1
                if final["verdict"] == "fail"
                else 2
            )

        case_results["missing"] = execute(
            "missing",
            missing_payload,
        )

        case_results["timeout"] = execute(
            "timeout",
            timeout_payload,
        )

        checks = [
            assertion(
                "F2a-missing-pass",
                (
                    case_results["missing"]
                    .get("verdict")
                    == "pass"
                ),
                case_results["missing"]
                .get("verdict"),
            ),
            assertion(
                "F2b-timeout-pass",
                (
                    case_results["timeout"]
                    .get("verdict")
                    == "pass"
                ),
                case_results["timeout"]
                .get("verdict"),
            ),
            assertion(
                "profile-finally-restored",
                (
                    profile.read_bytes()
                    == original_payload
                ),
                binding_digest(
                    profile.read_bytes()
                ),
            ),
        ]

        verdict = verdict_from_assertions(
            checks
        )

        final = {
            "schemaVersion": 1,
            "check": "F2",
            "runId":
                args.run_id,
            "verdict":
                verdict,
            "control":
                case_results["control"],
            "F2a":
                case_results["missing"],
            "F2b":
                case_results["timeout"],
            "checks":
                checks,
        }

        atomic_json(
            artifact / "result.json",
            final,
        )

        if verdict == "pass":
            return 0
        if verdict == "fail":
            return 1
        return 2

    finally:
        try:
            if (
                profile.read_bytes()
                != original_payload
            ):
                atomic_bytes(
                    profile,
                    original_payload,
                    original_mode,
                )
        except Exception as exc:
            try:
                atomic_json(
                    artifact
                    / "emergency-restore-error.json",
                    {
                        "schemaVersion": 1,
                        "verdict": "unknown",
                        "reason": (
                            "profile_restore_failed:"
                            f"{type(exc).__name__}:"
                            f"{exc}"
                        ),
                    },
                )
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue #396 F2 PreToolUse hook "
            "unavailable integration gate"
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
        "--timeout-seconds",
        type=int,
        default=120,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        return run_f2(args)

    except KeyboardInterrupt:
        return 130

    except Exception as exc:
        print(
            "pilot-gate-f2: internal error: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        try:
            artifact = (
                pathlib.Path(
                    args.artifact_dir
                )
                / "F2"
            )

            atomic_json(
                artifact / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F2",
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
