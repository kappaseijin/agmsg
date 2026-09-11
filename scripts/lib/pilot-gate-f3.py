#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import sys
import time
from typing import Any

PILOT_AGENT = "agmsg_pm_pilot_claude"
F3_WORKER = "agmsg_gate_worker"
F3_ISSUE = 396
F3_REPO = "gate/agmsg"
POST_EVENT = "PostToolUse"


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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            count = os.write(fd, payload[offset:])
            if count <= 0:
                raise RuntimeError("short write")
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def digest_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return digest_bytes(raw)


def assertion(name: str, result: bool | None, detail: Any) -> dict[str, Any]:
    return {
        "name": name,
        "verdict": "pass" if result is True else "fail" if result is False else "unknown",
        "detail": detail,
    }


def verdict_from_assertions(checks: list[dict[str, Any]]) -> str:
    if any(item["verdict"] == "fail" for item in checks):
        return "fail"
    if any(item["verdict"] == "unknown" for item in checks):
        return "unknown"
    return "pass"


def verdict_result(verdict: Any) -> bool | None:
    """Lift a phase/case verdict into an assertion result, keeping unknown.

    "pass" -> True, "fail" -> False, anything else -> None (unknown).
    Comparing with == "pass" would fold unknown into fail.
    """
    if verdict == "pass":
        return True
    if verdict == "fail":
        return False
    return None


def require_regular(path: pathlib.Path, executable: bool = False) -> None:
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"not regular file: {path}")
    if executable and not os.access(path, os.X_OK):
        raise RuntimeError(f"not executable: {path}")


def load_profile(path: pathlib.Path) -> dict[str, Any]:
    require_regular(path)
    value = read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError("profile root is not object")
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        raise RuntimeError("profile hooks is not object")
    pre = hooks.get("PreToolUse")
    post = hooks.get(POST_EVENT)
    if not isinstance(pre, list) or not pre:
        raise RuntimeError("PreToolUse is unavailable")
    if not isinstance(post, list) or not post:
        raise RuntimeError("PostToolUse is unavailable")
    return value


def find_single_posttool_handler(profile: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    hooks = profile.get("hooks")
    if not isinstance(hooks, dict):
        raise RuntimeError("profile hooks is not object")
    groups = hooks.get(POST_EVENT)
    if not isinstance(groups, list) or not groups:
        raise RuntimeError("PostToolUse is unavailable")

    found: list[tuple[int, int, dict[str, Any]]] = []
    for gi, group in enumerate(groups):
        if not isinstance(group, dict):
            raise RuntimeError("PostToolUse matcher group is not object")
        handlers = group.get("hooks")
        if not isinstance(handlers, list) or not handlers:
            raise RuntimeError("PostToolUse matcher group hooks invalid")
        for hi, handler in enumerate(handlers):
            if not isinstance(handler, dict):
                raise RuntimeError("PostToolUse handler is not object")
            if handler.get("type") != "command":
                raise RuntimeError("PostToolUse contains unsupported non-command handler")
            command = handler.get("command")
            if not isinstance(command, str) or not command:
                raise RuntimeError("PostToolUse command is invalid")
            args = handler.get("args", [])
            if args not in ([], None):
                raise RuntimeError("PostToolUse command args unsupported")
            found.append((gi, hi, handler))

    if len(found) != 1:
        raise RuntimeError(f"PostToolUse contract ambiguous: commandHandlers={len(found)}")
    return found[0]


def make_fault_profile(original: dict[str, Any]) -> dict[str, Any]:
    faulted = copy.deepcopy(original)
    hooks = faulted.get("hooks")
    if not isinstance(hooks, dict) or POST_EVENT not in hooks:
        raise RuntimeError("PostToolUse unavailable before fault")
    del hooks[POST_EVENT]
    return faulted


def make_control_profile(original: dict[str, Any], wrapper: pathlib.Path) -> dict[str, Any]:
    control = copy.deepcopy(original)
    gi, hi, handler = find_single_posttool_handler(control)
    replacement = copy.deepcopy(handler)
    replacement["command"] = str(wrapper)
    replacement["args"] = []
    control["hooks"][POST_EVENT][gi]["hooks"][hi] = replacement
    return control


def encode_profile(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_posttool_wrapper(path: pathlib.Path, original_command: str, record_path: pathlib.Path) -> None:
    program = """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

ORIGINAL_COMMAND = __ORIGINAL__
RECORD_PATH = __RECORD__

payload = sys.stdin.buffer.read()
tool_use_id = None
parse_error = None
try:
    value = json.loads(payload.decode("utf-8"))
    if isinstance(value, dict):
        raw_id = value.get("tool_use_id")
        if isinstance(raw_id, str) and raw_id:
            tool_use_id = raw_id
except Exception as exc:
    parse_error = type(exc).__name__

os.makedirs(os.path.dirname(RECORD_PATH), mode=0o700, exist_ok=True)
started = {
    "schemaVersion": 1,
    "event": "started",
    "toolUseId": tool_use_id,
    "parseError": parse_error,
    "pid": os.getpid(),
    "monotonic": time.monotonic(),
}
with open(RECORD_PATH, "a", encoding="utf-8", newline="\\n") as fh:
    fh.write(json.dumps(started, separators=(",", ":")) + "\\n")
    fh.flush()
    os.fsync(fh.fileno())

completed = subprocess.run(
    ["/bin/sh", "-c", ORIGINAL_COMMAND],
    input=payload,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
if completed.stdout:
    sys.stdout.buffer.write(completed.stdout)
    sys.stdout.buffer.flush()
if completed.stderr:
    sys.stderr.buffer.write(completed.stderr)
    sys.stderr.buffer.flush()

finished = {
    "schemaVersion": 1,
    "event": "completed",
    "toolUseId": tool_use_id,
    "exitStatus": completed.returncode,
    "pid": os.getpid(),
    "monotonic": time.monotonic(),
}
with open(RECORD_PATH, "a", encoding="utf-8", newline="\\n") as fh:
    fh.write(json.dumps(finished, separators=(",", ":")) + "\\n")
    fh.flush()
    os.fsync(fh.fileno())
raise SystemExit(completed.returncode)
"""
    program = program.replace("__ORIGINAL__", repr(original_command))
    program = program.replace("__RECORD__", repr(str(record_path)))
    atomic_bytes(path, program.encode("utf-8"), 0o700)


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return []
    if not path.is_file() or path.is_symlink():
        return None
    result: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return None
                result.append(value)
    except Exception:
        return None
    return result


def records_for_tool(path: pathlib.Path, tool_id: str) -> list[dict[str, Any]] | None:
    records = read_jsonl(path)
    if records is None:
        return None
    return [item for item in records if item.get("toolUseId") == tool_id]


def collector_observation(
    i1: Any,
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

    discover = i1.run([str(collector), "discover"], cwd=collector.parent.parent, env=cenv)
    atomic_json(artifact / "collector-discover.json", {
        "exitStatus": discover.returncode,
        "stdout": discover.stdout,
        "stderr": discover.stderr,
    })

    scan = i1.run([str(collector), "scan"], cwd=collector.parent.parent, env=cenv)
    atomic_json(artifact / "collector-scan.json", {
        "exitStatus": scan.returncode,
        "stdout": scan.stdout,
        "stderr": scan.stderr,
    })

    binding_value = read_json(binding)
    generation = str(binding_value.get("generation", ""))
    ledger = state_dir / f"generation-{generation}.observations.jsonl"
    matches: list[dict[str, Any]] = []
    if ledger.is_file() and not ledger.is_symlink():
        try:
            with open(ledger, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get("toolUseId") == tool_id:
                        matches.append(value)
        except Exception as exc:
            return {
                "verdict": "unknown",
                "reason": f"collector_ledger_unreadable:{type(exc).__name__}",
                "discoverExit": discover.returncode,
                "scanExit": scan.returncode,
            }

    if len(matches) != 1:
        return {
            "verdict": "unknown",
            "reason": "collector_observation_missing" if not matches else "collector_observation_ambiguous",
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


def binding_profile_check(native: Any, payload: bytes) -> dict[str, Any]:
    if native.binding is None:
        return assertion("binding-profile-digest", None, "binding unavailable")
    try:
        binding = read_json(native.binding)
    except Exception as exc:
        return assertion("binding-profile-digest", None, str(exc))
    expected = digest_bytes(payload)
    return assertion(
        "binding-profile-digest",
        binding.get("profileDigest") == expected,
        {"actual": binding.get("profileDigest"), "expected": expected, "binding": str(native.binding)},
    )


def transcript_result(i1: Any, transcript: pathlib.Path, tool_id: str) -> tuple[bool | None, Any]:
    found: list[dict[str, Any]] = []
    try:
        with open(transcript, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for node in i1.walk_json(value):
                    if node.get("type") == "tool_result" and node.get("tool_use_id") == tool_id:
                        found.append(node)
    except (OSError, UnicodeError) as exc:
        return None, str(exc)

    if len(found) != 1:
        return None, {"resultCount": len(found)}
    raw_error = found[0].get("is_error", False)
    if not isinstance(raw_error, bool):
        return None, {"isError": raw_error}
    return (not raw_error), {
        "isError": raw_error,
        "content": i1.json_content_text(found[0].get("content")),
    }


def classify_collector(result: dict[str, Any], tool_id: str) -> tuple[bool | None, Any]:
    if result.get("verdict") != "pass":
        return (None if result.get("verdict") == "unknown" else False), result
    observation = result.get("observation")
    if not isinstance(observation, dict):
        return None, result
    if observation.get("toolUseId") != tool_id:
        return False, observation
    if observation.get("completionState") != "success":
        return False, observation
    return True, observation


def prepare_observe_owner(i1: Any, gate_repo: pathlib.Path, case: str, run_id: str, team: str, generation: int) -> tuple[pathlib.Path, pathlib.Path, str]:
    root = gate_repo / ".agmsg-gate" / "f3" / case
    root.mkdir(parents=True, exist_ok=True)
    config = root / "run-config.json"
    request = root / "observe-owner.json"
    request_id = "f3-" + case + "-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    i1.atomic_json(config, {
        "schemaVersion": 1,
        "runId": run_id,
        "worker": F3_WORKER,
        "testIssueNumber": F3_ISSUE,
        "repo": F3_REPO,
    })
    i1.write_request(request, i1.make_request(
        run_id,
        request_id,
        "observe-owner",
        team,
        generation,
    ))
    command = i1.exact_broker_command(
        gate_repo / "scripts" / "p2-consumer-broker.sh",
        config,
        "observe-owner",
        request,
    )
    return config, request, command


def run_case(
    i1: Any,
    label: str,
    gate_repo: pathlib.Path,
    claude_config: pathlib.Path,
    team: str,
    artifact: pathlib.Path,
    env: dict[str, str],
    timeout_seconds: float,
    launcher: pathlib.Path,
    collector: pathlib.Path,
    profile: pathlib.Path,
    profile_payload: bytes,
    posttool_log: pathlib.Path,
) -> dict[str, Any]:
    case_dir = artifact / label
    case_dir.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(profile.stat().st_mode)
    atomic_bytes(profile, profile_payload, mode)
    if profile.read_bytes() != profile_payload:
        raise RuntimeError(f"{label} profile replacement mismatch")

    native = i1.NativePilot(
        launcher,
        gate_repo,
        team,
        claude_config,
        case_dir / "native",
        env,
        timeout_seconds,
    )
    try:
        native.start()
        run_id = f"f3-{label}-{time.monotonic_ns()}"
        _, _, command = prepare_observe_owner(
            i1,
            gate_repo,
            label,
            run_id,
            team,
            native.generation,
        )
        operation = native.invoke(command, case_dir / "operation")
        binding_check = binding_profile_check(native, profile_payload)
        if operation.get("verdict") != "pass":
            result = {
                "schemaVersion": 1,
                "case": label,
                "verdict": operation.get("verdict", "unknown"),
                "reason": operation.get("reason", "native_operation_not_pass"),
                "bindingCheck": binding_check,
                "operation": operation,
            }
            atomic_json(case_dir / "result.json", result)
            return result

        tool_id = operation.get("toolUseId")
        transcript_name = operation.get("transcript")
        if not isinstance(tool_id, str) or not tool_id or not isinstance(transcript_name, str) or not transcript_name:
            result = {
                "schemaVersion": 1,
                "case": label,
                "verdict": "unknown",
                "reason": "native_evidence_incomplete",
                "bindingCheck": binding_check,
                "operation": operation,
            }
            atomic_json(case_dir / "result.json", result)
            return result

        transcript = pathlib.Path(transcript_name)
        transcript_ok, transcript_detail = transcript_result(i1, transcript, tool_id)
        collector_result = collector_observation(
            i1,
            collector,
            native.binding,
            claude_config,
            case_dir / "collector-state",
            tool_id,
            native.env,
            case_dir,
        )
        collector_ok, collector_detail = classify_collector(collector_result, tool_id)
        decision = i1.hook_decision(native.decisions, tool_id)
        post_records = records_for_tool(posttool_log, tool_id)

        checks = [
            binding_check,
            assertion("pretool-allow", decision == "allow", decision),
            assertion("native-transcript-tool-result-success", transcript_ok, transcript_detail),
            assertion("collector-same-tool-success", collector_ok, collector_detail),
        ]

        if label == "control":
            if post_records is None:
                started: bool | None = None
                completed: bool | None = None
                same_id: bool | None = None
            else:
                starts = [x for x in post_records if x.get("event") == "started"]
                completes = [x for x in post_records if x.get("event") == "completed"]
                started = len(starts) == 1
                completed = len(completes) == 1
                same_id = all(x.get("toolUseId") == tool_id for x in post_records) and len(post_records) == 2
            checks.extend([
                assertion("posttool-record-started", started, post_records),
                assertion("posttool-record-completed", completed, post_records),
                assertion("posttool-same-tool-use-id", same_id, post_records),
            ])
        elif label == "fault":
            no_post = None if post_records is None else len(post_records) == 0
            checks.append(assertion("posttool-record-absent", no_post, post_records))
        else:
            checks.append(assertion("known-case", False, label))

        verdict = verdict_from_assertions(checks)
        result = {
            "schemaVersion": 1,
            "case": label,
            "verdict": verdict,
            "runId": run_id,
            "team": team,
            "sessionId": native.session_id,
            "generation": str(native.generation),
            "toolUseId": tool_id,
            "command": command,
            "postToolUseRecords": post_records,
            "collector": collector_result,
            "operation": operation,
            "checks": checks,
        }
        atomic_json(case_dir / "result.json", result)
        return result
    finally:
        native.stop()


def run_f3(args: argparse.Namespace) -> int:
    script_dir = pathlib.Path(__file__).resolve().parent
    iso = load_module(script_dir / "pilot-gate-isolation.py", "pilot_gate_isolation")
    i1 = load_module(script_dir / "pilot-gate-i1.py", "pilot_gate_i1")

    gate_repo = pathlib.Path(iso.canonical(args.gate_repo))
    run_root = pathlib.Path(iso.canonical(args.run_root))
    claude_config = pathlib.Path(iso.canonical(args.claude_config))
    artifact = pathlib.Path(args.artifact_dir).resolve(strict=False) / "F3"
    artifact.mkdir(parents=True, exist_ok=True)
    gate_repo.resolve(strict=True).relative_to(run_root.resolve(strict=True))

    launcher = gate_repo / "scripts" / "pilot-launcher.sh"
    broker = gate_repo / "scripts" / "p2-consumer-broker.sh"
    collector = gate_repo / "scripts" / "pilot-collector.sh"
    profile = gate_repo / ".claude" / "settings.local.json"
    for path in (launcher, broker, collector):
        require_regular(path, executable=True)
    require_regular(profile)

    original_payload = profile.read_bytes()
    original_mode = stat.S_IMODE(profile.stat().st_mode)
    original_profile = load_profile(profile)
    original_pretool = copy.deepcopy(original_profile["hooks"]["PreToolUse"])
    _, _, original_post_handler = find_single_posttool_handler(original_profile)
    original_post_command = original_post_handler.get("command")
    if not isinstance(original_post_command, str) or not original_post_command:
        raise RuntimeError("PostToolUse command invalid")

    runtime = gate_repo / ".agmsg-gate" / "f3"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.resolve(strict=True).relative_to(gate_repo.resolve(strict=True))

    control_log = artifact / "control" / "posttool-records.jsonl"
    fault_log = artifact / "fault" / "posttool-records.jsonl"
    for path in (control_log, fault_log):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

    wrapper = runtime / f"posttool-wrapper-{args.run_id}.py"
    if os.path.lexists(wrapper):
        raise RuntimeError(f"pre-existing F3 wrapper: {wrapper}")
    write_posttool_wrapper(wrapper, original_post_command, control_log)

    control_profile = make_control_profile(original_profile, wrapper)
    fault_profile = make_fault_profile(original_profile)
    control_pretool = control_profile["hooks"]["PreToolUse"]
    fault_pretool = fault_profile["hooks"]["PreToolUse"]
    pretool_checks = [
        assertion(
            "control-pretool-unchanged",
            control_pretool == original_pretool,
            {"original": digest_json(original_pretool), "control": digest_json(control_pretool)},
        ),
        assertion(
            "fault-pretool-unchanged",
            fault_pretool == original_pretool,
            {"original": digest_json(original_pretool), "fault": digest_json(fault_pretool)},
        ),
        assertion(
            "fault-posttool-removed",
            POST_EVENT not in fault_profile["hooks"],
            sorted(fault_profile["hooks"].keys()),
        ),
    ]
    pretool_verdict = verdict_from_assertions(pretool_checks)
    if pretool_verdict != "pass":
        atomic_json(artifact / "result.json", {
            "schemaVersion": 1,
            "check": "F3",
            "runId": args.run_id,
            "verdict": pretool_verdict,
            "reason": "profile_fault_construction_not_proved",
            "checks": pretool_checks,
        })
        return 1 if pretool_verdict == "fail" else 2

    control_payload = encode_profile(control_profile)
    fault_payload = encode_profile(fault_profile)
    atomic_json(artifact / "profiles.json", {
        "schemaVersion": 1,
        "originalDigest": digest_bytes(original_payload),
        "controlDigest": digest_bytes(control_payload),
        "faultDigest": digest_bytes(fault_payload),
        "originalPreToolUseDigest": digest_json(original_pretool),
        "controlPreToolUseDigest": digest_json(control_pretool),
        "faultPreToolUseDigest": digest_json(fault_pretool),
        "originalPostToolUseCommand": original_post_command,
        "controlWrapper": str(wrapper),
        "controlWrapperDigest": digest_bytes(wrapper.read_bytes()),
    })

    env = i1.sanitize_env(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(claude_config)

    try:
        control = run_case(
            i1,
            "control",
            gate_repo,
            claude_config,
            args.gate_team,
            artifact,
            env,
            float(args.timeout_seconds),
            launcher,
            collector,
            profile,
            control_payload,
            control_log,
        )
        if control.get("verdict") != "pass":
            final = {
                "schemaVersion": 1,
                "check": "F3",
                "runId": args.run_id,
                "verdict": control.get("verdict", "unknown"),
                "reason": "control_not_pass",
                "control": control,
                "profileChecks": pretool_checks,
            }
            atomic_json(artifact / "result.json", final)
            return 1 if final["verdict"] == "fail" else 2

        fault = run_case(
            i1,
            "fault",
            gate_repo,
            claude_config,
            args.gate_team,
            artifact,
            env,
            float(args.timeout_seconds),
            launcher,
            collector,
            profile,
            fault_payload,
            fault_log,
        )

        checks = [
            *pretool_checks,
            assertion("control-pass", verdict_result(control.get("verdict")), control.get("verdict")),
            assertion("fault-pass", verdict_result(fault.get("verdict")), fault.get("verdict")),
        ]
        verdict = verdict_from_assertions(checks)
        final = {
            "schemaVersion": 1,
            "check": "F3",
            "runId": args.run_id,
            "verdict": verdict,
            "faultMethod": "remove-PostToolUse-only",
            "control": control,
            "fault": fault,
            "checks": checks,
        }
        atomic_json(artifact / "result.json", final)
        if verdict == "pass":
            return 0
        if verdict == "fail":
            return 1
        return 2
    finally:
        try:
            if profile.read_bytes() != original_payload:
                atomic_bytes(profile, original_payload, original_mode)
            restored = profile.read_bytes()
            atomic_json(artifact / "profile-final-restore.json", {
                "schemaVersion": 1,
                "originalDigest": digest_bytes(original_payload),
                "restoredDigest": digest_bytes(restored),
                "matchesOriginal": restored == original_payload,
            })
        except Exception as exc:
            try:
                atomic_json(artifact / "emergency-restore-error.json", {
                    "schemaVersion": 1,
                    "verdict": "unknown",
                    "reason": f"profile_restore_failed:{type(exc).__name__}:{exc}",
                })
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue #396 F3 PostToolUse stop integration gate"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--gate-repo", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--gate-team", required=True)
    parser.add_argument("--claude-config", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_f3(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            f"pilot-gate-f3: internal error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        try:
            artifact = pathlib.Path(args.artifact_dir) / "F3"
            atomic_json(artifact / "result.json", {
                "schemaVersion": 1,
                "check": "F3",
                "runId": args.run_id,
                "verdict": "unknown",
                "reason": f"harness_internal:{type(exc).__name__}:{exc}",
            })
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
