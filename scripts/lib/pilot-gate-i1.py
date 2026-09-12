#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Any

PILOT_AGENT = "agmsg_pm_pilot_claude"
PILOT_TYPE = "claude-code"
WORKER = "agmsg_gate_worker"
SENDER = "agmsg_gate_sender"
ISSUE_REPO = "gate/agmsg"
ISSUE_NUMBER = 396
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
SAFE_PATH_TOKEN = re.compile(r"^[A-Za-z0-9_./:-]+$")
CREDENTIAL_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def _load_pty_helper():
    path = pathlib.Path(__file__).resolve().parent / "pilot-pty.py"
    spec = importlib.util.spec_from_file_location("pilot_pty", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_iso(script_dir: pathlib.Path):
    path = script_dir / "pilot-gate-isolation.py"
    spec = importlib.util.spec_from_file_location("pilot_gate_isolation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pilot-gate-isolation.py")
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


def verdict_from_assertions(assertions: list[dict[str, Any]]) -> str:
    if any(a["verdict"] == "fail" for a in assertions):
        return "fail"
    if any(a["verdict"] == "unknown" for a in assertions):
        return "unknown"
    return "pass"


def assertion(name: str, result: bool | None, detail: Any) -> dict[str, Any]:
    return {
        "name": name,
        "verdict": "pass" if result is True else "fail" if result is False else "unknown",
        "detail": detail,
    }


def run(argv: list[str], *, cwd: pathlib.Path | None = None, env: dict[str, str] | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def require_regular_executable(path: pathlib.Path) -> None:
    st = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
        raise RuntimeError(f"not regular executable: {path}")


def sanitize_env(base: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    for key in CREDENTIAL_ENV:
        env.pop(key, None)
    return env


def parse_last_json(text: str) -> dict[str, Any] | None:
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def json_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return ""


def walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def transcript_matches(root: pathlib.Path, session_id: str) -> list[pathlib.Path]:
    matches: list[pathlib.Path] = []
    if not root.is_dir():
        return matches
    for path in root.rglob("*.jsonl"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                found = False
                for line in fh:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for node in walk_json(value):
                        if node.get("sessionId") == session_id or node.get("session_id") == session_id:
                            found = True
                            break
                    if found:
                        break
                if found or session_id in path.name:
                    matches.append(path)
        except (OSError, UnicodeError):
            continue
    unique: dict[str, pathlib.Path] = {}
    for path in matches:
        try:
            unique[str(path.resolve(strict=True))] = path
        except OSError:
            pass
    return list(unique.values())


def find_tool_result(transcript: pathlib.Path, command: str) -> tuple[str | None, str | None]:
    tool_id: str | None = None
    result_text: str | None = None
    try:
        with open(transcript, "r", encoding="utf-8") as fh:
            values: list[Any] = []
            for line in fh:
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None, None

    for value in values:
        for node in walk_json(value):
            if (
                node.get("type") == "tool_use"
                and node.get("name") == "Bash"
                and isinstance(node.get("input"), dict)
                and node["input"].get("command") == command
                and isinstance(node.get("id"), str)
            ):
                tool_id = node["id"]
                break
        if tool_id:
            break

    if tool_id is None:
        return None, None

    for value in values:
        for node in walk_json(value):
            if node.get("type") == "tool_result" and node.get("tool_use_id") == tool_id:
                result_text = json_content_text(node.get("content"))
                if not result_text and isinstance(node.get("text"), str):
                    result_text = node["text"]
                return tool_id, result_text
    return tool_id, None


def hook_decision(decisions: pathlib.Path | None, tool_id: str) -> str | None:
    if decisions is None or not decisions.is_file():
        return None
    matches: list[str] = []
    try:
        with open(decisions, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("toolUseId") == tool_id and isinstance(value.get("decision"), str):
                    matches.append(value["decision"])
    except OSError:
        return None
    return matches[-1] if matches else None


def broker_state_path(gate_repo: pathlib.Path, team: str, run_id: str) -> pathlib.Path:
    key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    seat = gate_repo / "run" / "pilot" / f"{team}__{PILOT_AGENT}"
    return seat / "broker-state" / f"{key}.json"


def pilot_state_path(gate_repo: pathlib.Path, team: str) -> pathlib.Path:
    return gate_repo / "run" / "pilot" / f"{team}__{PILOT_AGENT}" / "state.json"


def current_generation(gate_repo: pathlib.Path, team: str) -> int:
    path = pilot_state_path(gate_repo, team)
    if not path.exists():
        return 0
    value = read_json(path)
    raw = value.get("latestGeneration") if isinstance(value, dict) else None
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
        return int(raw)
    raise RuntimeError("pilot latestGeneration unidentifiable")


def binding_for_generation(gate_repo: pathlib.Path, team: str, generation: int) -> pathlib.Path:
    return gate_repo / "run" / "pilot" / f"{team}__{PILOT_AGENT}" / "bindings" / f"{generation}.json"


def wait_binding(gate_repo: pathlib.Path, team: str, generation: int, proc: subprocess.Popen[bytes], timeout: float, pump) -> tuple[pathlib.Path, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    path = binding_for_generation(gate_repo, team, generation)
    while time.monotonic() < deadline:
        pump(0.1)
        if path.is_file() and not path.is_symlink():
            try:
                value = read_json(path)
            except Exception:
                time.sleep(0.05)
                continue
            if isinstance(value, dict):
                return path, value
        if proc.poll() is not None:
            raise RuntimeError(f"native launcher exited before binding: {proc.returncode}")
    raise TimeoutError("binding publication timeout")


def make_request(run_id: str, request_id: str, operation: str, team: str, generation: int, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schemaVersion": 1,
        "runId": run_id,
        "requestId": request_id,
        "operation": operation,
        "team": team,
        "actor": PILOT_AGENT,
        "generation": str(generation),
    }
    value.update(extra)
    return value


def exact_broker_command(
    broker: pathlib.Path,
    config: pathlib.Path,
    operation: str,
    request: pathlib.Path,
    gh_config_dir: pathlib.Path | None = None,
) -> str:
    """The exact Bash command the pilot runs (Issue #404 guard grammar).

    Form A: <B> --config <C> <OP> < <R>
    Form B: <B> --config <C> --gh-config-dir <G> issue-record < <R>
    The broker refuses issue-record without --gh-config-dir
    (gh_config_dir_required), and the guard allows form B only for
    issue-record, so G is required for issue-record and refused otherwise.
    Every path, G included, must pass SAFE_PATH_TOKEN (Issue #409).
    """
    if operation == "issue-record":
        if gh_config_dir is None:
            raise RuntimeError("issue-record requires gh_config_dir (--gh-config-dir)")
    elif gh_config_dir is not None:
        raise RuntimeError(f"gh_config_dir is only valid for issue-record, not {operation}")
    tokens = [str(broker), str(config), str(request)]
    if gh_config_dir is not None:
        tokens.append(str(gh_config_dir))
    if not all(SAFE_PATH_TOKEN.fullmatch(token) for token in tokens):
        raise RuntimeError("I1 paths contain shell metacharacters/whitespace; exact guarded command cannot be proven")
    if gh_config_dir is not None:
        return f"{broker} --config {config} --gh-config-dir {gh_config_dir} {operation} < {request}"
    return f"{broker} --config {config} {operation} < {request}"


def write_request(path: pathlib.Path, value: dict[str, Any]) -> None:
    atomic_json(path, value)


def parse_provider_json(cp: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if cp.returncode != 0:
        raise RuntimeError(f"provider failed rc={cp.returncode}: {cp.stderr.strip()}")
    value = parse_last_json(cp.stdout)
    if value is None:
        raise RuntimeError("provider response unidentifiable")
    return value


def register_fixture_member(gate_repo: pathlib.Path, team: str, agent: str, project: pathlib.Path, role: str, env: dict[str, str], mutation_log: pathlib.Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    argv = [str(gate_repo / "scripts" / "join.sh"), team, agent, PILOT_TYPE, str(project), "--role", role, "--kind", "service"]
    append_jsonl(mutation_log, {"kind": "team-registration", "argv": argv, "team": team, "target": str(project)})
    cp = run(["bash", *argv], cwd=gate_repo, env={**env, "AGMSG_RESOLVE_PROJECT": "0"})
    if cp.returncode != 0:
        raise RuntimeError(f"join failed for {agent}: {cp.stderr.strip()}")


def provider_call(provider: pathlib.Path, args: list[str], gate_repo: pathlib.Path, env: dict[str, str], mutation_log: pathlib.Path | None = None, mutating: bool = False) -> dict[str, Any]:
    argv = [str(provider), *args]
    if mutating and mutation_log is not None:
        append_jsonl(mutation_log, {"kind": "provider", "argv": argv, "team": args[1] if len(args) > 1 else None})
    cp = run(argv, cwd=gate_repo, env=env)
    return parse_provider_json(cp)


def storage_db(gate_repo: pathlib.Path, team: str, env: dict[str, str]) -> pathlib.Path:
    script = 'source "$1/scripts/lib/storage.sh"; agmsg_storage_load; agmsg_db_path "$2"'
    cp = run(["bash", "-c", script, "bash", str(gate_repo), team], cwd=gate_repo, env=env)
    if cp.returncode != 0 or not cp.stdout.strip():
        raise RuntimeError("cannot resolve gate team storage db")
    path = pathlib.Path(cp.stdout.strip()).resolve(strict=True)
    path.relative_to(gate_repo.resolve(strict=True))
    return path


def receipt_count(db: pathlib.Path, team: str, event_id: str, owner: str, evidence: str) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            """
            SELECT COUNT(*)
              FROM message_receipts AS r
              JOIN events AS e ON e.legacy_id = r.message_id
             WHERE e.type='message_sent'
               AND e.team=?
               AND e.id=?
               AND r.owner=?
               AND r.evidence=?
            """,
            (team, event_id, owner, evidence),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def expected_common(value: dict[str, Any], *, run_id: str, request_id: str, operation: str, team: str, generation: int) -> list[dict[str, Any]]:
    return [
        assertion("schemaVersion", value.get("schemaVersion") == 1, value.get("schemaVersion")),
        assertion("runId", value.get("runId") == run_id, value.get("runId")),
        assertion("requestId", value.get("requestId") == request_id, value.get("requestId")),
        assertion("operation", value.get("operation") == operation, value.get("operation")),
        assertion("team", value.get("team") == team, value.get("team")),
        assertion("actor", value.get("actor") == PILOT_AGENT, value.get("actor")),
        assertion("generation", str(value.get("generation")) == str(generation), value.get("generation")),
    ]


def classify_broker_state(value: dict[str, Any], expected_state: str) -> tuple[bool | None, str]:
    state = value.get("state")
    if state == expected_state:
        return True, "expected_state"
    if state == "stopped_for_unknown":
        return None, str(value.get("reason") or "stopped_for_unknown")
    if state in {"stopped", "error", "absent"}:
        return False, str(value.get("reason") or state)
    return None, "state_unidentifiable"


class NativePilot:
    def __init__(self, launcher: pathlib.Path, gate_repo: pathlib.Path, team: str, claude_config: pathlib.Path, artifact: pathlib.Path, env: dict[str, str], timeout: float):
        self.launcher = launcher
        self.gate_repo = gate_repo
        self.team = team
        self.claude_config = claude_config
        self.artifact = artifact
        self.env = env
        self.timeout = timeout
        self.proc: subprocess.Popen[bytes] | None = None
        self.master: int | None = None
        self.pty_log = artifact / "native-pty.raw"
        # The launcher creates both run logs next to the binding
        # (<bindings_dir>/<generation>.decisions.jsonl / .executions.jsonl,
        # #404/#415) and overwrites any value handed to it, so the harness
        # does not choose them: they are known once the binding is.
        self.decisions: pathlib.Path | None = None
        self.executions: pathlib.Path | None = None
        self.session_id = ""
        self.generation = 0
        self.binding: pathlib.Path | None = None
        self.transcript: pathlib.Path | None = None

    def pump(self, timeout: float = 0.0) -> None:
        if self.master is None:
            return
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return
        if data:
            self.pty_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pty_log, "ab") as fh:
                fh.write(data)
                fh.flush()

    def start(self) -> None:
        previous = current_generation(self.gate_repo, self.team)
        self.generation = previous + 1
        # No AGMSG_PM_* reaches the launcher from the harness: live PM
        # state must not leak into the pilot (#415). The launcher clears
        # and re-sets them as well.
        env = {key: value for key, value in self.env.items() if not key.startswith("AGMSG_PM_")}
        argv = [str(self.launcher), "--team", self.team, "--project", str(self.gate_repo), "--fresh"]
        # The one start path for every native pilot (#426): N1 in the runner
        # uses the same helper through its command line.
        self.proc, self.master = _load_pty_helper().spawn(argv, cwd=self.gate_repo, env=env)
        binding_path, binding = wait_binding(self.gate_repo, self.team, self.generation, self.proc, self.timeout, self.pump)
        session = binding.get("sessionId")
        if not isinstance(session, str) or not UUID_RE.fullmatch(session):
            raise RuntimeError("I1 binding sessionId invalid")
        if binding.get("team") != self.team or binding.get("agent") != PILOT_AGENT:
            raise RuntimeError("I1 binding identity mismatch")
        if str(binding.get("generation")) != str(self.generation):
            raise RuntimeError("I1 binding generation mismatch")
        if pathlib.Path(str(binding.get("project"))).resolve(strict=True) != self.gate_repo.resolve(strict=True):
            raise RuntimeError("I1 binding project mismatch")
        self.session_id = session
        self.binding = binding_path
        self.decisions = binding_path.parent / f"{self.generation}.decisions.jsonl"
        self.executions = binding_path.parent / f"{self.generation}.executions.jsonl"

    def discover_transcript(self) -> pathlib.Path | None:
        matches = transcript_matches(self.claude_config, self.session_id)
        if len(matches) == 1:
            self.transcript = matches[0]
            return matches[0]
        return None

    def invoke(self, command: str, operation_dir: pathlib.Path) -> dict[str, Any]:
        if self.proc is None or self.master is None:
            return {"verdict": "unknown", "reason": "native_not_started"}
        token = hashlib.sha256((self.session_id + command).encode("utf-8")).hexdigest()[:16]
        prompt = (
            "For the G4 integration gate, use the Bash tool exactly once. "
            "Run exactly the following command, without changing, wrapping, retrying, or adding commands:\n"
            f"{command}\n"
            f"After the tool finishes, reply with AGMSG_GATE_DONE_{token}."
        )
        operation_dir.mkdir(parents=True, exist_ok=True)
        (operation_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        os.write(self.master, prompt.encode("utf-8") + b"\r")
        deadline = time.monotonic() + self.timeout
        observed_tool_id: str | None = None
        observed_result: str | None = None
        transcript: pathlib.Path | None = None
        while time.monotonic() < deadline:
            self.pump(0.2)
            transcript = self.discover_transcript()
            if transcript is not None:
                observed_tool_id, observed_result = find_tool_result(transcript, command)
                if observed_tool_id is not None and observed_result is not None:
                    break
            if self.proc.poll() is not None:
                break
        if transcript is None:
            return {"verdict": "unknown", "reason": "transcript_unavailable"}
        if observed_tool_id is None:
            return {"verdict": "unknown", "reason": "native_tool_not_observed", "transcript": str(transcript)}
        decision = hook_decision(self.decisions, observed_tool_id)
        if decision is None:
            return {"verdict": "unknown", "reason": "pretool_decision_unavailable", "toolUseId": observed_tool_id, "transcript": str(transcript)}
        if decision != "allow":
            return {"verdict": "fail", "reason": f"pretool_decision_{decision}", "toolUseId": observed_tool_id, "transcript": str(transcript)}
        if observed_result is None:
            return {"verdict": "unknown", "reason": "tool_result_unavailable", "toolUseId": observed_tool_id, "transcript": str(transcript)}
        (operation_dir / "tool-result.raw").write_text(observed_result, encoding="utf-8")
        value = parse_last_json(observed_result)
        if value is None:
            return {"verdict": "unknown", "reason": "broker_response_unidentifiable", "toolUseId": observed_tool_id, "transcript": str(transcript)}
        return {"verdict": "pass", "reason": "native_pretool_broker_path_observed", "toolUseId": observed_tool_id, "transcript": str(transcript), "broker": value}

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.master is not None:
                try:
                    os.write(self.master, b"\x04")
                except OSError:
                    pass
            deadline = time.monotonic() + 2.0
            while self.proc.poll() is None and time.monotonic() < deadline:
                self.pump(0.1)
            if self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait(timeout=2)
        finally:
            self.pump(0)
            if self.master is not None:
                try:
                    os.close(self.master)
                except OSError:
                    pass
                self.master = None


def validate_gh_store(store: pathlib.Path, expected_body: str) -> list[dict[str, Any]]:
    path = store / "comments.json"
    try:
        value = read_json(path)
    except Exception as exc:
        return [assertion("gh-store-readable", None, str(exc))]
    comments = value.get("comments") if isinstance(value, dict) else None
    if not isinstance(comments, list):
        return [assertion("gh-comments-array", None, comments)]
    matching = [c for c in comments if isinstance(c, dict) and c.get("repo") == ISSUE_REPO and c.get("issue") == ISSUE_NUMBER and c.get("body") == expected_body]
    return [
        assertion("pseudo-comment-exactly-once", len(matching) == 1, {"count": len(matching)}),
        assertion("pseudo-comment-body", len(matching) == 1 and matching[0].get("body") == expected_body, matching[0] if len(matching) == 1 else None),
    ]


def validate_identity(*, gate_repo: pathlib.Path, run_root: pathlib.Path, artifact: pathlib.Path, gate_team: str, binding: dict[str, Any], operation_results: dict[str, dict[str, Any]], mutation_log: pathlib.Path, gh_log: pathlib.Path, live_identity_json: pathlib.Path | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    session = binding.get("sessionId")
    generation = str(binding.get("generation"))
    project = str(binding.get("project"))
    checks.append(assertion("binding-team", binding.get("team") == gate_team, binding.get("team")))
    checks.append(assertion("binding-agent", binding.get("agent") == PILOT_AGENT, binding.get("agent")))
    try:
        checks.append(assertion("binding-project", pathlib.Path(project).resolve(strict=True) == gate_repo.resolve(strict=True), project))
    except Exception as exc:
        checks.append(assertion("binding-project", None, str(exc)))

    state_path = broker_state_path(gate_repo, gate_team, str(next(iter(operation_results.values())).get("runId", ""))) if operation_results else None
    for name, record in operation_results.items():
        broker = record.get("broker") if isinstance(record, dict) else None
        if not isinstance(broker, dict):
            continue
        checks.extend([
            assertion(f"{name}.team", broker.get("team") == gate_team, broker.get("team")),
            assertion(f"{name}.actor", broker.get("actor") == PILOT_AGENT, broker.get("actor")),
            assertion(f"{name}.generation", str(broker.get("generation")) == generation, broker.get("generation")),
        ])
        owner = broker.get("owner")
        if owner is not None:
            checks.append(assertion(f"{name}.owner", isinstance(owner, str) and session in owner and f":{generation}:" in owner, owner))

    mutation_records: list[dict[str, Any]] = []
    for log_path in (mutation_log, gh_log):
        if not log_path.is_file():
            continue
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        mutation_records.append(value)
        except Exception as exc:
            checks.append(assertion(f"read-{log_path.name}", None, str(exc)))

    for index, record in enumerate(mutation_records):
        team = record.get("team")
        if team is not None:
            checks.append(assertion(f"mutation-{index}-team", team == gate_team, team))
        target = record.get("target")
        if isinstance(target, str) and target:
            try:
                target_real = pathlib.Path(target).resolve(strict=False)
                allowed = False
                for root in (gate_repo.resolve(strict=True), run_root.resolve(strict=True), artifact.resolve(strict=True)):
                    try:
                        target_real.relative_to(root)
                        allowed = True
                        break
                    except ValueError:
                        pass
                checks.append(assertion(f"mutation-{index}-target", allowed, str(target_real)))
            except Exception as exc:
                checks.append(assertion(f"mutation-{index}-target", None, str(exc)))

    live_tokens: list[str] = []
    if live_identity_json is not None and live_identity_json.is_file():
        try:
            live_value = read_json(live_identity_json)
            if isinstance(live_value, dict):
                for key in ("team", "bindingPath", "sessionId", "claimFile"):
                    value = live_value.get(key)
                    if isinstance(value, str) and value:
                        live_tokens.append(value)
        except Exception as exc:
            checks.append(assertion("live-identity-read", None, str(exc)))

    mutation_text = json.dumps(mutation_records, ensure_ascii=False, separators=(",", ":"))
    for token in live_tokens:
        checks.append(assertion(f"live-token-not-mutation-target:{hashlib.sha256(token.encode()).hexdigest()[:8]}", token not in mutation_text, "matched" if token in mutation_text else "absent"))

    return {"schemaVersion": 1, "verdict": verdict_from_assertions(checks), "checks": checks}


def run_i1(args: argparse.Namespace) -> int:
    script_dir = pathlib.Path(__file__).resolve().parent
    iso = load_iso(script_dir)
    gate_repo = pathlib.Path(iso.canonical(args.gate_repo))
    run_root = pathlib.Path(iso.canonical(args.run_root))
    artifact = pathlib.Path(args.artifact_dir).resolve(strict=False) / "I1"
    artifact.mkdir(parents=True, exist_ok=True)
    claude_config = pathlib.Path(iso.canonical(args.claude_config))
    launcher = gate_repo / "scripts" / "pilot-launcher.sh"
    provider = gate_repo / "scripts" / "p2-provider.sh"
    broker = gate_repo / "scripts" / "p2-consumer-broker.sh"
    gh_source = script_dir / "pilot-gate-gh.py"
    for path in (launcher, provider, broker, gh_source):
        require_regular_executable(path)

    env = sanitize_env(os.environ)
    gh_bin_dir = run_root / "i1-bin"
    gh_bin_dir.mkdir(parents=True, exist_ok=True)
    gh_bin = gh_bin_dir / "gh"
    shutil.copyfile(gh_source, gh_bin)
    os.chmod(gh_bin, 0o700)
    if iso.sha256_file(gh_source) != iso.sha256_file(gh_bin):
        raise RuntimeError("isolated gh copy digest mismatch")
    gh_store = run_root / "gh-store"
    gh_store.mkdir(parents=True, exist_ok=True)
    # Disposable gh config for the broker's issue-record (--gh-config-dir).
    gh_config_dir = run_root / "gh-config"
    gh_config_dir.mkdir(parents=True, exist_ok=True)
    gh_log = artifact / "gh-invocations.jsonl"
    mutation_log = artifact / "mutation-log.jsonl"
    if mutation_log.exists():
        mutation_log.unlink()
    if gh_log.exists():
        gh_log.unlink()

    worker_project = gate_repo / ".agmsg-gate" / "i1-worker"
    sender_project = gate_repo / ".agmsg-gate" / "i1-sender"
    register_fixture_member(gate_repo, args.gate_team, WORKER, worker_project, "worker", env, mutation_log)
    register_fixture_member(gate_repo, args.gate_team, SENDER, sender_project, "sender", env, mutation_log)

    config = gate_repo / ".agmsg-gate" / "i1-run-config.json"
    atomic_json(config, {"schemaVersion": 1, "runId": args.run_id, "worker": WORKER, "testIssueNumber": ISSUE_NUMBER, "repo": ISSUE_REPO})

    env["PATH"] = str(gh_bin_dir) + os.pathsep + env.get("PATH", "")
    env["AGMSG_GATE_GH_STORE"] = str(gh_store)
    env["AGMSG_GATE_GH_LOG"] = str(gh_log)
    env["AGMSG_GATE_GH_REPO"] = ISSUE_REPO
    env["AGMSG_GATE_GH_ISSUE"] = str(ISSUE_NUMBER)
    env["AGMSG_GATE_GH_BODY_ROOT"] = str(gate_repo / "run" / "pilot")

    seed_request = f"seed-{args.run_id}"
    seed_body = json.dumps({"schemaVersion": 1, "kind": "i1-input", "runId": args.run_id}, separators=(",", ":"))
    seed = provider_call(provider, ["message-send", args.gate_team, SENDER, PILOT_AGENT, seed_request, seed_body], gate_repo, env, mutation_log, True)
    input_id = seed.get("messageId")
    if seed.get("state") != "queued" or not isinstance(input_id, str) or not input_id:
        raise RuntimeError("I1 input seed failed")

    native = NativePilot(launcher, gate_repo, args.gate_team, claude_config, artifact / "native", env, float(args.timeout_seconds))
    operation_results: dict[str, dict[str, Any]] = {}
    operation_verdicts: dict[str, dict[str, Any]] = {}
    try:
        native.start()
        binding = read_json(native.binding) if native.binding else {}
        if not isinstance(binding, dict):
            raise RuntimeError("I1 binding unreadable")
        generation = native.generation
        owner = f"p2:{native.session_id}:{generation}:{args.run_id}"
        requests_dir = gate_repo / ".agmsg-gate" / "i1-requests"
        requests_dir.mkdir(parents=True, exist_ok=True)

        def invoke_operation(operation: str, request_value: dict[str, Any]) -> dict[str, Any]:
            request_path = requests_dir / f"{operation}.json"
            write_request(request_path, request_value)
            command = exact_broker_command(
                broker,
                config,
                operation,
                request_path,
                gh_config_dir if operation == "issue-record" else None,
            )
            append_jsonl(mutation_log, {"kind": "native-broker", "operation": operation, "argv": [command], "team": args.gate_team, "target": str(request_path)})
            native_record = native.invoke(command, artifact / operation)
            native_record["runId"] = args.run_id
            operation_results[operation] = native_record
            if native_record.get("verdict") != "pass":
                operation_verdicts[operation] = native_record
                return native_record
            broker_value = native_record.get("broker")
            if not isinstance(broker_value, dict):
                result = {"verdict": "unknown", "reason": "broker_response_missing"}
                operation_verdicts[operation] = result
                return result
            return native_record

        receive_req = make_request(args.run_id, f"receive-{args.run_id}", "receive", args.gate_team, generation)
        r = invoke_operation("receive", receive_req)
        if r.get("verdict") == "pass":
            value = r["broker"]
            checks = expected_common(value, run_id=args.run_id, request_id=receive_req["requestId"], operation="receive", team=args.gate_team, generation=generation)
            state_ok, state_reason = classify_broker_state(value, "claimed")
            checks.append(assertion("state", state_ok, state_reason))
            checks.append(assertion("inputMessageId", value.get("inputMessageId") == input_id, value.get("inputMessageId")))
            checks.append(assertion("owner", value.get("owner") == owner, value.get("owner")))
            operation_verdicts["receive"] = {"verdict": verdict_from_assertions(checks), "checks": checks}
        if operation_verdicts.get("receive", {}).get("verdict") != "pass":
            for op in ("delegate", "collect-result", "issue-record"):
                operation_verdicts.setdefault(op, {"verdict": "unknown", "reason": "receive_prerequisite_not_pass"})
        else:
            delegate_req = make_request(args.run_id, f"delegate-{args.run_id}", "delegate", args.gate_team, generation, inputMessageId=input_id, worker=WORKER, task="Summarize the isolated gate fixture result")
            d = invoke_operation("delegate", delegate_req)
            delegate_id = None
            input_receipt = None
            if d.get("verdict") == "pass":
                value = d["broker"]
                checks = expected_common(value, run_id=args.run_id, request_id=delegate_req["requestId"], operation="delegate", team=args.gate_team, generation=generation)
                state_ok, state_reason = classify_broker_state(value, "delegated")
                checks.extend([
                    assertion("state", state_ok, state_reason),
                    assertion("deliveryState", value.get("deliveryState") == "queued", value.get("deliveryState")),
                    assertion("worker", value.get("worker") == WORKER, value.get("worker")),
                    assertion("inputMessageId", value.get("inputMessageId") == input_id, value.get("inputMessageId")),
                ])
                delegate_id = value.get("delegateMessageId")
                input_receipt = value.get("inputReceiptId")
                checks.append(assertion("delegateMessageId", isinstance(delegate_id, str) and bool(delegate_id), delegate_id))
                checks.append(assertion("inputReceiptId", isinstance(input_receipt, str) and bool(input_receipt), input_receipt))
                try:
                    peek = provider_call(provider, ["message-peek", args.gate_team, WORKER], gate_repo, env)
                    body = json.loads(peek.get("body", "")) if isinstance(peek.get("body"), str) else None
                    checks.extend([
                        assertion("worker-readback-state", peek.get("state") == "ok", peek),
                        assertion("worker-readback-id", peek.get("messageId") == delegate_id, peek.get("messageId")),
                        assertion("worker-readback-from", peek.get("from") == PILOT_AGENT, peek.get("from")),
                        assertion("worker-readback-to", peek.get("to") == WORKER, peek.get("to")),
                        assertion("worker-readback-envelope", isinstance(body, dict) and body.get("requestId") == delegate_req["requestId"] and body.get("inputMessageId") == input_id, body),
                    ])
                except Exception as exc:
                    checks.append(assertion("worker-readback", None, str(exc)))
                operation_verdicts["delegate"] = {"verdict": verdict_from_assertions(checks), "checks": checks}

            if operation_verdicts.get("delegate", {}).get("verdict") != "pass" or not isinstance(delegate_id, str):
                operation_verdicts.setdefault("collect-result", {"verdict": "unknown", "reason": "delegate_prerequisite_not_pass"})
                operation_verdicts.setdefault("issue-record", {"verdict": "unknown", "reason": "delegate_prerequisite_not_pass"})
            else:
                result_body = json.dumps({"schemaVersion": 1, "requestId": delegate_req["requestId"], "delegateMessageId": delegate_id, "result": "isolated worker result"}, separators=(",", ":"))
                result_send = provider_call(provider, ["message-send", args.gate_team, WORKER, PILOT_AGENT, f"result-{args.run_id}", result_body], gate_repo, env, mutation_log, True)
                result_message_seed = result_send.get("messageId")
                collect_req = make_request(args.run_id, delegate_req["requestId"], "collect-result", args.gate_team, generation, delegateMessageId=delegate_id)
                c = invoke_operation("collect-result", collect_req)
                result_id = None
                result_receipt = None
                if c.get("verdict") == "pass":
                    value = c["broker"]
                    checks = expected_common(value, run_id=args.run_id, request_id=collect_req["requestId"], operation="collect-result", team=args.gate_team, generation=generation)
                    state_ok, state_reason = classify_broker_state(value, "result_claimed")
                    checks.append(assertion("state", state_ok, state_reason))
                    if state_ok is True:
                        result_id = value.get("resultMessageId")
                        result_receipt = value.get("resultReceiptId")
                        checks.extend([
                            assertion("delegateMessageId", value.get("delegateMessageId") == delegate_id, value.get("delegateMessageId")),
                            assertion("resultMessageId", value.get("resultMessageId") == result_message_seed, value.get("resultMessageId")),
                            assertion("owner", value.get("owner") == owner, value.get("owner")),
                            assertion("resultReceiptId", isinstance(result_receipt, str) and bool(result_receipt), result_receipt),
                            assertion("result", value.get("result") == "isolated worker result", value.get("result")),
                        ])
                    operation_verdicts["collect-result"] = {"verdict": verdict_from_assertions(checks), "checks": checks, "knownGapPreserved": state_ok is not True}

                if operation_verdicts.get("collect-result", {}).get("verdict") != "pass" or not isinstance(result_id, str) or not isinstance(result_receipt, str) or not isinstance(input_receipt, str):
                    operation_verdicts.setdefault("issue-record", {"verdict": "unknown", "reason": "collect_result_prerequisite_not_pass", "knownGapPreserved": True})
                else:
                    issue_body = f"G4 I1 isolated issue record {args.run_id}"
                    issue_req = make_request(args.run_id, delegate_req["requestId"], "issue-record", args.gate_team, generation, inputMessageId=input_id, delegateMessageId=delegate_id, resultMessageId=result_id, body=issue_body)
                    i = invoke_operation("issue-record", issue_req)
                    if i.get("verdict") == "pass":
                        value = i["broker"]
                        checks = expected_common(value, run_id=args.run_id, request_id=issue_req["requestId"], operation="issue-record", team=args.gate_team, generation=generation)
                        state_ok, state_reason = classify_broker_state(value, "acked")
                        checks.extend([
                            assertion("state", state_ok, state_reason),
                            assertion("inputMessageId", value.get("inputMessageId") == input_id, value.get("inputMessageId")),
                            assertion("delegateMessageId", value.get("delegateMessageId") == delegate_id, value.get("delegateMessageId")),
                            assertion("resultMessageId", value.get("resultMessageId") == result_id, value.get("resultMessageId")),
                            assertion("issueNumber", value.get("issueNumber") == ISSUE_NUMBER, value.get("issueNumber")),
                        ])
                        checks.extend(validate_gh_store(gh_store, issue_body))
                        try:
                            db = storage_db(gate_repo, args.gate_team, env)
                            checks.append(assertion("input-ack-exactly-once", receipt_count(db, args.gate_team, input_id, owner, input_receipt) == 1, {"messageId": input_id, "owner": owner, "receipt": input_receipt}))
                            checks.append(assertion("result-ack-exactly-once", receipt_count(db, args.gate_team, result_id, owner, result_receipt) == 1, {"messageId": result_id, "owner": owner, "receipt": result_receipt}))
                        except Exception as exc:
                            checks.append(assertion("ack-readback", None, str(exc)))
                        operation_verdicts["issue-record"] = {"verdict": verdict_from_assertions(checks), "checks": checks}

        observe_req = make_request(args.run_id, f"observe-{args.run_id}", "observe-owner", args.gate_team, generation, agent=PILOT_AGENT)
        o = invoke_operation("observe-owner", observe_req)
        if o.get("verdict") == "pass":
            value = o["broker"]
            checks = expected_common(value, run_id=args.run_id, request_id=observe_req["requestId"], operation="observe-owner", team=args.gate_team, generation=generation)
            state = value.get("state")
            if state == "observed":
                state_result: bool | None = True
            elif state == "stopped_for_unknown":
                state_result = None
            elif state in {"stopped", "error"}:
                state_result = False
            else:
                state_result = None
            checks.append(assertion("state", state_result, value))
            if state == "observed":
                checks.append(assertion("owner-status-not-collapsed", value.get("ownerStatus") in {"owned", "absent", "stale", "not_found"}, value.get("ownerStatus")))
            operation_verdicts["observe-owner"] = {"verdict": verdict_from_assertions(checks), "checks": checks}

        identity = validate_identity(
            gate_repo=gate_repo,
            run_root=run_root,
            artifact=artifact,
            gate_team=args.gate_team,
            binding=binding,
            operation_results=operation_results,
            mutation_log=mutation_log,
            gh_log=gh_log,
            live_identity_json=pathlib.Path(args.live_identity_json) if args.live_identity_json else None,
        )
        atomic_json(artifact / "identity" / "result.json", identity)

        for op in ("receive", "delegate", "collect-result", "issue-record", "observe-owner"):
            operation_verdicts.setdefault(op, {"verdict": "unknown", "reason": "operation_not_observed"})
            atomic_json(artifact / op / "result.json", operation_verdicts[op])

        verdicts = [operation_verdicts[op]["verdict"] for op in ("receive", "delegate", "collect-result", "issue-record", "observe-owner")]
        verdicts.append(identity["verdict"])
        overall = "fail" if "fail" in verdicts else "unknown" if "unknown" in verdicts else "pass"
        result = {
            "schemaVersion": 1,
            "check": "I1",
            "runId": args.run_id,
            "sessionId": native.session_id,
            "generation": str(generation),
            "binding": str(native.binding),
            "operations": operation_verdicts,
            "identity": identity,
            "verdict": overall,
            "knownCollectResultGapPreserved": operation_verdicts["collect-result"].get("verdict") != "pass",
        }
        atomic_json(artifact / "result.json", result)
        if overall == "pass":
            return 0
        if overall == "fail":
            return 1
        return 2
    finally:
        native.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue #396 Part 2 I1 integration gate")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--gate-repo", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--gate-team", required=True)
    parser.add_argument("--claude-config", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--live-identity-json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_i1(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"pilot-gate-i1: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            artifact = pathlib.Path(args.artifact_dir) / "I1"
            atomic_json(artifact / "result.json", {"schemaVersion": 1, "check": "I1", "runId": args.run_id, "verdict": "unknown", "reason": f"harness_internal:{type(exc).__name__}:{exc}"})
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
