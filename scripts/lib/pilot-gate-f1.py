#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import sqlite3
import stat
import sys
import time
from typing import Any


PILOT_AGENT = "agmsg_pm_pilot_claude"
PILOT_TYPE = "claude-code"
WORKER = "agmsg_gate_worker"
SENDER = "agmsg_gate_sender"

CASE_CONTROL = "control"
CASE_FAULT = "fault"
CASE_RECOVERY = "recovery"


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


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def assertion(name: str, result: bool | None, detail: Any) -> dict[str, Any]:
    return {
        "name": name,
        "verdict": "pass" if result is True else "fail" if result is False else "unknown",
        "detail": detail,
    }


def verdict_from_assertions(assertions: list[dict[str, Any]]) -> str:
    if any(item["verdict"] == "fail" for item in assertions):
        return "fail"
    if any(item["verdict"] == "unknown" for item in assertions):
        return "unknown"
    return "pass"


def require_regular_executable(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise RuntimeError(f"not regular executable: {path}")


def safe_case_team(base_team: str, run_id: str, label: str) -> str:
    digest = hashlib.sha256(f"{base_team}:{run_id}:{label}".encode("utf-8")).hexdigest()[:12]
    prefix = {
        CASE_CONTROL: "agmsg-g4f1c",
        CASE_FAULT: "agmsg-g4f1f",
        CASE_RECOVERY: "agmsg-g4f1r",
    }[label]
    return f"{prefix}-{digest}"


def register_member(
    i1: Any,
    *,
    gate_repo: pathlib.Path,
    team: str,
    agent: str,
    project: pathlib.Path,
    role: str,
    kind: str,
    env: dict[str, str],
    mutation_log: pathlib.Path,
) -> None:
    project.mkdir(parents=True, exist_ok=True)
    join = gate_repo / "scripts" / "join.sh"
    argv = [
        str(join),
        team,
        agent,
        PILOT_TYPE,
        str(project),
        "--role",
        role,
        "--kind",
        kind,
    ]
    append_jsonl(
        mutation_log,
        {
            "kind": "team-registration",
            "argv": argv,
            "team": team,
            "target": str(project),
        },
    )
    cp = i1.run(
        ["bash", *argv],
        cwd=gate_repo,
        env={**env, "AGMSG_RESOLVE_PROJECT": "0"},
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"join failed team={team} agent={agent} rc={cp.returncode}: {cp.stderr.strip()}"
        )


def prepare_case_team(
    i1: Any,
    *,
    gate_repo: pathlib.Path,
    team: str,
    env: dict[str, str],
    mutation_log: pathlib.Path,
    label: str,
) -> None:
    root = gate_repo / ".agmsg-gate" / "f1" / label
    register_member(
        i1,
        gate_repo=gate_repo,
        team=team,
        agent=PILOT_AGENT,
        project=gate_repo,
        role="pm",
        kind="seat",
        env=env,
        mutation_log=mutation_log,
    )
    register_member(
        i1,
        gate_repo=gate_repo,
        team=team,
        agent=WORKER,
        project=root / "worker",
        role="worker",
        kind="service",
        env=env,
        mutation_log=mutation_log,
    )
    register_member(
        i1,
        gate_repo=gate_repo,
        team=team,
        agent=SENDER,
        project=root / "sender",
        role="sender",
        kind="service",
        env=env,
        mutation_log=mutation_log,
    )


def delegate_write_count(
    db: pathlib.Path,
    *,
    team: str,
    request_id: str,
) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            """
            SELECT COUNT(*)
              FROM events AS e
             WHERE e.type = 'message_sent'
               AND e.team = ?
               AND e.from_agent = ?
               AND e.to_agent = ?
               AND json_valid(e.body)
               AND json_extract(e.body, '$.schemaVersion') = 1
               AND json_extract(e.body, '$.kind') = 'p2-delegation'
               AND json_extract(e.body, '$.requestId') = ?
            """,
            (team, PILOT_AGENT, WORKER, request_id),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def count_exact_native_tool_use(i1: Any, transcript: pathlib.Path | None, command: str) -> int | None:
    if transcript is None or not transcript.is_file():
        return None
    count = 0
    try:
        with open(transcript, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for node in i1.walk_json(value):
                    if (
                        node.get("type") == "tool_use"
                        and node.get("name") == "Bash"
                        and isinstance(node.get("input"), dict)
                        and node["input"].get("command") == command
                    ):
                        count += 1
    except (OSError, UnicodeError):
        return None
    return count


def fault_shim_bytes() -> bytes:
    program = '''#!/usr/bin/env python3
import json
import os
import sys
import time

log_path = os.environ.get("AGMSG_GATE_F1_SHIM_LOG", "")
if log_path:
    record = {
        "schemaVersion": 1,
        "observedAtMonotonic": time.monotonic(),
        "argv": sys.argv[1:],
    }
    with open(log_path, "a", encoding="utf-8", newline="\\n") as fh:
        fh.write(json.dumps(record, separators=(",", ":")))
        fh.write("\\n")
        fh.flush()
        os.fsync(fh.fileno())

sys.stderr.write("pilot-gate-f1: deterministic provider backend unavailable\\n")
raise SystemExit(1)
'''
    return program.encode("utf-8")


class ProviderFault:
    def __init__(
        self,
        *,
        provider: pathlib.Path,
        artifact: pathlib.Path,
        iso: Any,
        mutation_log: pathlib.Path,
    ):
        self.provider = provider
        self.artifact = artifact
        self.iso = iso
        self.mutation_log = mutation_log
        self.original_bytes: bytes | None = None
        self.original_mode: int | None = None
        self.original_digest = ""
        self.fault_digest = ""
        self.restored_digest = ""
        self.active = False

    def _replace_bytes(self, payload: bytes, mode: int) -> None:
        tmp = self.provider.with_name(
            f".{self.provider.name}.f1-{os.getpid()}-{time.monotonic_ns()}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(tmp, flags, mode)
        try:
            written = 0
            while written < len(payload):
                amount = os.write(fd, payload[written:])
                if amount <= 0:
                    raise RuntimeError("short write while preparing provider replacement")
                written += amount
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, mode)
        os.replace(tmp, self.provider)

    def inject(self) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("provider fault already active")
        require_regular_executable(self.provider)
        metadata = self.provider.stat()
        self.original_mode = stat.S_IMODE(metadata.st_mode)
        self.original_bytes = self.provider.read_bytes()
        self.original_digest = self.iso.sha256_file(self.provider)

        backup = self.artifact / "provider.original"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with open(backup, "wb") as fh:
            fh.write(self.original_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(backup, 0o600)

        shim = fault_shim_bytes()
        shim_artifact = self.artifact / "provider.fault"
        with open(shim_artifact, "wb") as fh:
            fh.write(shim)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(shim_artifact, 0o700)

        self._replace_bytes(shim, 0o700)
        self.fault_digest = self.iso.sha256_file(self.provider)
        self.active = True

        record = {
            "kind": "provider-fault-inject",
            "target": str(self.provider),
            "originalDigest": self.original_digest,
            "faultDigest": self.fault_digest,
        }
        append_jsonl(self.mutation_log, record)
        atomic_json(
            self.artifact / "inject.json",
            {
                "schemaVersion": 1,
                **record,
                "originalMode": self.original_mode,
                "faultArtifactDigest": self.iso.sha256_file(shim_artifact),
            },
        )
        return record

    def restore(self) -> dict[str, Any]:
        if self.original_bytes is None or self.original_mode is None:
            raise RuntimeError("provider restore requested without captured original")
        self._replace_bytes(self.original_bytes, self.original_mode)
        self.restored_digest = self.iso.sha256_file(self.provider)
        self.active = False
        record = {
            "kind": "provider-fault-restore",
            "target": str(self.provider),
            "originalDigest": self.original_digest,
            "restoredDigest": self.restored_digest,
        }
        append_jsonl(self.mutation_log, record)
        atomic_json(
            self.artifact / "restore.json",
            {
                "schemaVersion": 1,
                **record,
                "restoredMatchesOriginal": self.restored_digest == self.original_digest,
            },
        )
        return record


def native_operation(
    i1: Any,
    *,
    native: Any,
    broker: pathlib.Path,
    config: pathlib.Path,
    requests_dir: pathlib.Path,
    artifact: pathlib.Path,
    mutation_log: pathlib.Path,
    run_id: str,
    team: str,
    generation: int,
    operation: str,
    request_id: str,
    **extra: Any,
) -> tuple[dict[str, Any], str]:
    request = i1.make_request(
        run_id,
        request_id,
        operation,
        team,
        generation,
        **extra,
    )
    request_path = requests_dir / f"{operation}.json"
    i1.write_request(request_path, request)
    command = i1.exact_broker_command(broker, config, operation, request_path)
    append_jsonl(
        mutation_log,
        {
            "kind": "native-broker",
            "operation": operation,
            "argv": [command],
            "team": team,
            "target": str(request_path),
        },
    )
    record = native.invoke(command, artifact / operation)
    record["runId"] = run_id
    atomic_json(artifact / operation / "native.json", record)
    return record, command


def validate_receive(
    i1: Any,
    *,
    record: dict[str, Any],
    run_id: str,
    request_id: str,
    team: str,
    generation: int,
    input_id: str,
    owner: str,
) -> dict[str, Any]:
    if record.get("verdict") != "pass":
        return {
            "verdict": record.get("verdict", "unknown"),
            "reason": record.get("reason", "native_path_not_pass"),
        }
    value = record.get("broker")
    if not isinstance(value, dict):
        return {"verdict": "unknown", "reason": "broker_response_missing"}

    checks = i1.expected_common(
        value,
        run_id=run_id,
        request_id=request_id,
        operation="receive",
        team=team,
        generation=generation,
    )
    state_ok, state_reason = i1.classify_broker_state(value, "claimed")
    checks.extend(
        [
            assertion("state", state_ok, state_reason),
            assertion("inputMessageId", value.get("inputMessageId") == input_id, value.get("inputMessageId")),
            assertion("owner", value.get("owner") == owner, value.get("owner")),
        ]
    )
    return {"verdict": verdict_from_assertions(checks), "checks": checks, "broker": value}


def validate_successful_delegate(
    i1: Any,
    *,
    record: dict[str, Any],
    run_id: str,
    request_id: str,
    team: str,
    generation: int,
    input_id: str,
) -> dict[str, Any]:
    if record.get("verdict") != "pass":
        return {
            "verdict": record.get("verdict", "unknown"),
            "reason": record.get("reason", "native_path_not_pass"),
        }
    value = record.get("broker")
    if not isinstance(value, dict):
        return {"verdict": "unknown", "reason": "broker_response_missing"}

    checks = i1.expected_common(
        value,
        run_id=run_id,
        request_id=request_id,
        operation="delegate",
        team=team,
        generation=generation,
    )
    state_ok, state_reason = i1.classify_broker_state(value, "delegated")
    checks.extend(
        [
            assertion("state", state_ok, state_reason),
            assertion("deliveryState", value.get("deliveryState") == "queued", value.get("deliveryState")),
            assertion("worker", value.get("worker") == WORKER, value.get("worker")),
            assertion("inputMessageId", value.get("inputMessageId") == input_id, value.get("inputMessageId")),
            assertion(
                "delegateMessageId",
                isinstance(value.get("delegateMessageId"), str) and bool(value.get("delegateMessageId")),
                value.get("delegateMessageId"),
            ),
            assertion(
                "inputReceiptId",
                isinstance(value.get("inputReceiptId"), str) and bool(value.get("inputReceiptId")),
                value.get("inputReceiptId"),
            ),
        ]
    )
    return {"verdict": verdict_from_assertions(checks), "checks": checks, "broker": value}


def validate_fault_delegate(
    i1: Any,
    *,
    record: dict[str, Any],
    run_id: str,
    request_id: str,
    team: str,
    generation: int,
) -> dict[str, Any]:
    if record.get("verdict") != "pass":
        return {
            "verdict": record.get("verdict", "unknown"),
            "reason": record.get("reason", "native_path_not_pass"),
        }

    value = record.get("broker")
    if not isinstance(value, dict):
        return {"verdict": "unknown", "reason": "broker_response_missing"}

    checks = i1.expected_common(
        value,
        run_id=run_id,
        request_id=request_id,
        operation="delegate",
        team=team,
        generation=generation,
    )

    state = value.get("state")
    reason = value.get("reason")
    if state == "stopped" and reason == "send_failed":
        stopped: bool | None = True
    elif state == "stopped_for_unknown":
        stopped = None
    elif state in {"delegated", "acked", "result_claimed"}:
        stopped = False
    elif state in {"stopped", "error"}:
        stopped = False
    else:
        stopped = None

    checks.extend(
        [
            assertion("fault-stopped", stopped, {"state": state, "reason": reason}),
            assertion(
                "fault-reason-send-failed",
                reason == "send_failed" if state == "stopped" else stopped,
                reason,
            ),
        ]
    )
    return {"verdict": verdict_from_assertions(checks), "checks": checks, "broker": value}


def shim_invocations(path: pathlib.Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return None
                values.append(value)
    except Exception:
        return None
    return values


def run_case(
    i1: Any,
    iso: Any,
    *,
    label: str,
    base_run_id: str,
    base_team: str,
    gate_repo: pathlib.Path,
    run_root: pathlib.Path,
    claude_config: pathlib.Path,
    artifact_root: pathlib.Path,
    env: dict[str, str],
    timeout_seconds: float,
    provider_fault: ProviderFault | None,
) -> dict[str, Any]:
    case_artifact = artifact_root / label
    case_artifact.mkdir(parents=True, exist_ok=True)
    mutation_log = artifact_root / "mutation-log.jsonl"
    team = safe_case_team(base_team, base_run_id, label)
    run_id = f"{base_run_id}-F1-{label}"
    request_id = f"f1-{label}-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:16]}"

    prepare_case_team(
        i1,
        gate_repo=gate_repo,
        team=team,
        env=env,
        mutation_log=mutation_log,
        label=label,
    )

    config = gate_repo / ".agmsg-gate" / "f1" / label / "run-config.json"
    i1.atomic_json(
        config,
        {
            "schemaVersion": 1,
            "runId": run_id,
            "worker": WORKER,
            "testIssueNumber": 396,
            "repo": "gate/agmsg",
        },
    )

    provider = gate_repo / "scripts" / "p2-provider.sh"
    broker = gate_repo / "scripts" / "p2-consumer-broker.sh"
    launcher = gate_repo / "scripts" / "pilot-launcher.sh"

    seed_request = f"seed-{request_id}"
    seed_body = json.dumps(
        {"schemaVersion": 1, "kind": "f1-input", "runId": run_id, "case": label},
        separators=(",", ":"),
    )
    seed = i1.provider_call(
        provider,
        ["message-send", team, SENDER, PILOT_AGENT, seed_request, seed_body],
        gate_repo,
        env,
        mutation_log,
        True,
    )
    input_id = seed.get("messageId")
    if seed.get("state") != "queued" or not isinstance(input_id, str) or not input_id:
        raise RuntimeError(f"F1 {label} seed failed")

    native = i1.NativePilot(
        launcher,
        gate_repo,
        team,
        claude_config,
        case_artifact / "native",
        env,
        timeout_seconds,
    )

    request_dir = gate_repo / ".agmsg-gate" / "f1" / label / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)

    fault_injected = False
    provider_restored = False
    delegate_command = ""
    try:
        native.start()
        generation = native.generation
        owner = f"p2:{native.session_id}:{generation}:{run_id}"

        receive_id = f"receive-{request_id}"
        receive_record, _ = native_operation(
            i1,
            native=native,
            broker=broker,
            config=config,
            requests_dir=request_dir,
            artifact=case_artifact,
            mutation_log=mutation_log,
            run_id=run_id,
            team=team,
            generation=generation,
            operation="receive",
            request_id=receive_id,
        )
        receive_result = validate_receive(
            i1,
            record=receive_record,
            run_id=run_id,
            request_id=receive_id,
            team=team,
            generation=generation,
            input_id=input_id,
            owner=owner,
        )
        atomic_json(case_artifact / "receive" / "result.json", receive_result)

        if receive_result["verdict"] != "pass":
            result = {
                "schemaVersion": 1,
                "case": label,
                "runId": run_id,
                "requestId": request_id,
                "team": team,
                "verdict": receive_result["verdict"],
                "reason": "receive_prerequisite_not_pass",
                "receive": receive_result,
            }
            atomic_json(case_artifact / "result.json", result)
            return result

        if label == CASE_FAULT:
            if provider_fault is None:
                raise RuntimeError("fault case missing ProviderFault")
            provider_fault.inject()
            fault_injected = True

        delegate_record, delegate_command = native_operation(
            i1,
            native=native,
            broker=broker,
            config=config,
            requests_dir=request_dir,
            artifact=case_artifact,
            mutation_log=mutation_log,
            run_id=run_id,
            team=team,
            generation=generation,
            operation="delegate",
            request_id=request_id,
            inputMessageId=input_id,
            worker=WORKER,
            task="Summarize the isolated F1 gate fixture result",
        )

        if label == CASE_FAULT:
            if provider_fault is None:
                raise RuntimeError("fault object disappeared")
            provider_fault.restore()
            provider_restored = True

        if label in {CASE_CONTROL, CASE_RECOVERY}:
            delegate_result = validate_successful_delegate(
                i1,
                record=delegate_record,
                run_id=run_id,
                request_id=request_id,
                team=team,
                generation=generation,
                input_id=input_id,
            )
        else:
            delegate_result = validate_fault_delegate(
                i1,
                record=delegate_record,
                run_id=run_id,
                request_id=request_id,
                team=team,
                generation=generation,
            )

        atomic_json(case_artifact / "delegate" / "result.json", delegate_result)

        db = i1.storage_db(gate_repo, team, env)
        writes = delegate_write_count(db, team=team, request_id=request_id)
        transcript_count = count_exact_native_tool_use(i1, native.transcript, delegate_command)

        checks: list[dict[str, Any]] = [
            assertion("receive-pass", receive_result["verdict"] == "pass", receive_result["verdict"]),
            assertion(
                "delegate-native-command-exactly-once",
                transcript_count == 1 if transcript_count is not None else None,
                transcript_count,
            ),
        ]

        if label in {CASE_CONTROL, CASE_RECOVERY}:
            checks.extend(
                [
                    assertion("delegate-pass", delegate_result["verdict"] == "pass", delegate_result["verdict"]),
                    assertion("persistent-delegate-write-count", writes == 1, writes),
                ]
            )
        else:
            checks.extend(
                [
                    assertion(
                        "delegate-stopped-on-backend-failure",
                        delegate_result["verdict"] == "pass",
                        delegate_result,
                    ),
                    assertion("persistent-fault-request-write-count", writes == 0, writes),
                    assertion("provider-restored-before-case-exit", provider_restored, provider_restored),
                ]
            )

        verdict = verdict_from_assertions(checks)
        result = {
            "schemaVersion": 1,
            "case": label,
            "runId": run_id,
            "requestId": request_id,
            "team": team,
            "sessionId": native.session_id,
            "generation": str(generation),
            "binding": str(native.binding) if native.binding else None,
            "inputMessageId": input_id,
            "delegateCommand": delegate_command,
            "persistentDelegateWriteCount": writes,
            "delegateNativeToolUseCount": transcript_count,
            "receive": receive_result,
            "delegate": delegate_result,
            "checks": checks,
            "verdict": verdict,
        }
        atomic_json(case_artifact / "result.json", result)
        return result

    finally:
        if fault_injected and not provider_restored and provider_fault is not None:
            try:
                provider_fault.restore()
            except Exception as exc:
                atomic_json(
                    case_artifact / "restore-error.json",
                    {
                        "schemaVersion": 1,
                        "verdict": "unknown",
                        "reason": f"provider_restore_failed:{type(exc).__name__}:{exc}",
                    },
                )
        native.stop()


def run_f1(args: argparse.Namespace) -> int:
    script_dir = pathlib.Path(__file__).resolve().parent
    iso = load_module(script_dir / "pilot-gate-isolation.py", "pilot_gate_isolation")
    i1 = load_module(script_dir / "pilot-gate-i1.py", "pilot_gate_i1")

    gate_repo = pathlib.Path(iso.canonical(args.gate_repo))
    run_root = pathlib.Path(iso.canonical(args.run_root))
    claude_config = pathlib.Path(iso.canonical(args.claude_config))
    artifact = pathlib.Path(args.artifact_dir).resolve(strict=False) / "F1"
    artifact.mkdir(parents=True, exist_ok=True)
    mutation_log = artifact / "mutation-log.jsonl"
    if mutation_log.exists():
        mutation_log.unlink()

    provider = gate_repo / "scripts" / "p2-provider.sh"
    broker = gate_repo / "scripts" / "p2-consumer-broker.sh"
    launcher = gate_repo / "scripts" / "pilot-launcher.sh"
    join = gate_repo / "scripts" / "join.sh"

    for path in (provider, broker, launcher, join):
        require_regular_executable(path)

    provider.resolve(strict=True).relative_to(gate_repo.resolve(strict=True))
    broker.resolve(strict=True).relative_to(gate_repo.resolve(strict=True))

    env = i1.sanitize_env(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(claude_config)

    original_provider_digest = iso.sha256_file(provider)
    fault = ProviderFault(
        provider=provider,
        artifact=artifact / "fault-provider",
        iso=iso,
        mutation_log=mutation_log,
    )
    shim_log = artifact / "fault-provider" / "shim-invocations.jsonl"
    if shim_log.exists():
        shim_log.unlink()
    env["AGMSG_GATE_F1_SHIM_LOG"] = str(shim_log)

    control: dict[str, Any] | None = None
    fault_case: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None

    try:
        control = run_case(
            i1,
            iso,
            label=CASE_CONTROL,
            base_run_id=args.run_id,
            base_team=args.gate_team,
            gate_repo=gate_repo,
            run_root=run_root,
            claude_config=claude_config,
            artifact_root=artifact,
            env=env,
            timeout_seconds=float(args.timeout_seconds),
            provider_fault=None,
        )

        if control.get("verdict") != "pass":
            result = {
                "schemaVersion": 1,
                "check": "F1",
                "runId": args.run_id,
                "verdict": control.get("verdict", "unknown"),
                "reason": "control_not_pass",
                "control": control,
            }
            atomic_json(artifact / "result.json", result)
            return 1 if result["verdict"] == "fail" else 2

        fault_case = run_case(
            i1,
            iso,
            label=CASE_FAULT,
            base_run_id=args.run_id,
            base_team=args.gate_team,
            gate_repo=gate_repo,
            run_root=run_root,
            claude_config=claude_config,
            artifact_root=artifact,
            env=env,
            timeout_seconds=float(args.timeout_seconds),
            provider_fault=fault,
        )

        restored_after_fault = iso.sha256_file(provider)
        if restored_after_fault != original_provider_digest:
            result = {
                "schemaVersion": 1,
                "check": "F1",
                "runId": args.run_id,
                "verdict": "unknown",
                "reason": "provider_not_restored_before_recovery",
                "control": control,
                "fault": fault_case,
                "provider": {
                    "originalDigest": original_provider_digest,
                    "observedDigest": restored_after_fault,
                },
            }
            atomic_json(artifact / "result.json", result)
            return 2

        recovery = run_case(
            i1,
            iso,
            label=CASE_RECOVERY,
            base_run_id=args.run_id,
            base_team=args.gate_team,
            gate_repo=gate_repo,
            run_root=run_root,
            claude_config=claude_config,
            artifact_root=artifact,
            env=env,
            timeout_seconds=float(args.timeout_seconds),
            provider_fault=None,
        )

        invocations = shim_invocations(shim_log)
        shim_count = None if invocations is None else len(invocations)

        post_counts: dict[str, int | None] = {}
        for label, record in (
            (CASE_CONTROL, control),
            (CASE_FAULT, fault_case),
            (CASE_RECOVERY, recovery),
        ):
            try:
                team = str(record["team"])
                request_id = str(record["requestId"])
                db = i1.storage_db(gate_repo, team, env)
                post_counts[label] = delegate_write_count(
                    db,
                    team=team,
                    request_id=request_id,
                )
            except Exception:
                post_counts[label] = None

        ids = [
            str(control.get("requestId", "")),
            str(fault_case.get("requestId", "")),
            str(recovery.get("requestId", "")),
        ]

        provider_final_digest = iso.sha256_file(provider)
        checks = [
            assertion("request-ids-nonempty-and-distinct", all(ids) and len(set(ids)) == 3, ids),
            assertion("control-case-pass", control.get("verdict") == "pass", control.get("verdict")),
            assertion("fault-case-pass", fault_case.get("verdict") == "pass", fault_case.get("verdict")),
            assertion("recovery-case-pass", recovery.get("verdict") == "pass", recovery.get("verdict")),
            assertion(
                "control-write-count-one",
                post_counts[CASE_CONTROL] == 1 if post_counts[CASE_CONTROL] is not None else None,
                post_counts[CASE_CONTROL],
            ),
            assertion(
                "fault-write-count-zero",
                post_counts[CASE_FAULT] == 0 if post_counts[CASE_FAULT] is not None else None,
                post_counts[CASE_FAULT],
            ),
            assertion(
                "recovery-write-count-one",
                post_counts[CASE_RECOVERY] == 1 if post_counts[CASE_RECOVERY] is not None else None,
                post_counts[CASE_RECOVERY],
            ),
            assertion(
                "fault-provider-invoked-exactly-once",
                shim_count == 1 if shim_count is not None else None,
                shim_count,
            ),
            assertion(
                "fault-automatic-retry-count-zero",
                (shim_count - 1) == 0 if shim_count is not None and shim_count >= 1 else None,
                None if shim_count is None else max(shim_count - 1, 0),
            ),
            assertion(
                "fault-request-not-replayed-after-recovery",
                post_counts[CASE_FAULT] == 0 if post_counts[CASE_FAULT] is not None else None,
                post_counts[CASE_FAULT],
            ),
            assertion(
                "provider-final-digest-restored",
                provider_final_digest == original_provider_digest,
                {"original": original_provider_digest, "final": provider_final_digest},
            ),
            assertion(
                "fault-digest-different-from-original",
                bool(fault.fault_digest) and fault.fault_digest != original_provider_digest,
                {"original": original_provider_digest, "fault": fault.fault_digest},
            ),
            assertion(
                "restore-digest-equals-original",
                bool(fault.restored_digest) and fault.restored_digest == original_provider_digest,
                {"original": original_provider_digest, "restored": fault.restored_digest},
            ),
        ]

        verdict = verdict_from_assertions(checks)
        result = {
            "schemaVersion": 1,
            "check": "F1",
            "runId": args.run_id,
            "faultMethod": "isolated-provider-atomic-shim-substitution",
            "controlRequestId": ids[0],
            "faultRequestId": ids[1],
            "recoveryRequestId": ids[2],
            "persistentWriteCountsAfterRecovery": post_counts,
            "faultProviderInvocationCount": shim_count,
            "automaticRetryCountForFaultRequest": None if shim_count is None else max(shim_count - 1, 0),
            "providerDigests": {
                "original": original_provider_digest,
                "fault": fault.fault_digest,
                "restored": fault.restored_digest,
                "final": provider_final_digest,
            },
            "control": control,
            "fault": fault_case,
            "recovery": recovery,
            "checks": checks,
            "verdict": verdict,
        }
        atomic_json(artifact / "result.json", result)

        if verdict == "pass":
            return 0
        if verdict == "fail":
            return 1
        return 2

    finally:
        try:
            current_digest = iso.sha256_file(provider)
        except Exception:
            current_digest = ""

        if current_digest != original_provider_digest:
            try:
                if fault.original_bytes is not None and fault.original_mode is not None:
                    fault.restore()
            except Exception as exc:
                try:
                    atomic_json(
                        artifact / "emergency-restore-error.json",
                        {
                            "schemaVersion": 1,
                            "verdict": "unknown",
                            "reason": f"emergency_restore_failed:{type(exc).__name__}:{exc}",
                        },
                    )
                except Exception:
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue #396 F1 broker/backend failure integration gate")
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
        return run_f1(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            f"pilot-gate-f1: internal error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        try:
            artifact = pathlib.Path(args.artifact_dir) / "F1"
            atomic_json(
                artifact / "result.json",
                {
                    "schemaVersion": 1,
                    "check": "F1",
                    "runId": args.run_id,
                    "verdict": "unknown",
                    "reason": f"harness_internal:{type(exc).__name__}:{exc}",
                },
            )
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
