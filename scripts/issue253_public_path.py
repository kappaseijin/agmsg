#!/usr/bin/env python3
"""Local fixed-source public-helper experiment; never a live P2 acceptance test."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

CUTOFFS = {"F": "7ea795e93683b0e7ede0fabf017fe503fcfe6aea",
           "O": "e58dbafad5a84be625f070385bb0c076c3daa4db"}


def correlate(rows, expected, complete):
    """Only a complete closed fixture history can establish exact correspondence."""
    if not complete:
        return {"status": "unknown", "reason": "incomplete_history"}
    mapping = {}
    for request, team, recipient, body in expected:
        matches = [row for row in rows if row.get("team") == team
                   and row.get("to") == recipient and row.get("body") == body
                   and request in body]
        if len(matches) != 1 or not matches[0].get("id"):
            return {"status": "unknown", "reason": "missing_or_ambiguous_id"}
        mapping[request] = str(matches[0]["id"])
    if len(set(mapping.values())) != len(expected):
        return {"status": "unknown", "reason": "reused_id"}
    return {"status": "pass", "ids": mapping}


def receiver_verdict(observations, expected_receivers, request):
    if len(observations) != expected_receivers:
        return "unknown"
    deliveries = sum(item["stdout"].count(request) for item in observations)
    return "incompatible" if deliveries > 1 else "pass" if deliveries == 1 else "unknown"


def ack_verdict(rc, evidence, interrupted=False, fault=False):
    # stdout, successful exit, cursor consumption and business reply are not ACK.
    if interrupted or fault or rc != 0 or evidence != "handedOff":
        return "unknown"
    return "pass"


class Fixture:
    def __init__(self, source, root, watch_only=False):
        self.root = root
        root.mkdir(parents=True)
        shutil.copytree(source / "scripts", root / "scripts")
        for directory in ("home", "tmp", "run", "db", "teams", "project", "commands"):
            (root / directory).mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(root / "home"),
                    "TMPDIR": str(root / "tmp"), "LANG": "en_US.UTF-8",
                    "AGMSG_STORAGE_PATH": str(root / "db"),
                    "AGMSG_STORAGE_DRIVER": "sqlite", "AGMSG_RESOLVE_PROJECT": "0",
                    "AGMSG_AGENT_PID": "", "AGMSG_WATCH_INTERVAL": "0.1",
                    "AGMSG_CLAIM_TEAM": "team"}
        self.commands = []
        self.lock = threading.Lock()
        self.injections = []
        for team in (("team",) if watch_only else ("team", "other")):
            for agent in (("sender", "receiver") if watch_only else ("sender", "receiver", "delegate")):
                kind = "codex" if watch_only and agent == "sender" else "claude-code"
                result = self.call("join.sh", team, agent, kind, str(root / "project"))
                if result["rc"] != 0:
                    raise RuntimeError(f"fixture join failed: {root}: {result}")

    def start(self, helper, *args, extra=None):
        with self.lock:
            index = len(self.commands)
            result = {"helper": helper, "args": list(args), "index": index,
                      "fixture_overrides": extra or {}}
            self.commands.append(result)
        prefix = self.root / "commands" / f"{index:03d}"
        out = open(str(prefix) + ".stdout", "w")
        err = open(str(prefix) + ".stderr", "w")
        process = subprocess.Popen(["bash", str(self.root / "scripts" / helper), *map(str, args)],
                                   cwd=self.root / "project", env=self.env | (extra or {}),
                                   stdout=out, stderr=err)
        return process, result, prefix, out, err

    def finish(self, handle, stop=False):
        process, result, prefix, out, err = handle
        if stop and process.poll() is None:
            process.terminate()  # Only this harness-owned Popen, never a PID search/group.
        try:
            rc = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            rc = process.wait(timeout=5)
            result["timeout"] = True
        out.close()
        err.close()
        result.update(rc=rc, stdout=Path(str(prefix) + ".stdout").read_text(),
                      stderr=Path(str(prefix) + ".stderr").read_text(), pid=process.pid)
        Path(str(prefix) + ".json").write_text(json.dumps(result, indent=2) + "\n")
        return result

    def call(self, helper, *args, extra=None):
        return self.finish(self.start(helper, *args, extra=extra))

    def send(self, request, recipient="receiver", team="team", text="identical content"):
        body = f"requestId={request}\n{text}\tend"
        result = self.call("send.sh", team, "sender", recipient, body)
        if result["rc"] != 0:
            raise RuntimeError(f"fixture send failed: {result}")
        return request, team, recipient, body

    def history(self, team="team", max_pages=30):
        rows, seen, cursor = [], set(), None
        for _ in range(max_pages):
            args = ["get", "teams", team, "messages", "--limit", "2"]
            if cursor:
                args += ["--before-id", cursor]
            result = self.call("api.sh", *args)
            if result["rc"]:
                return rows, False
            page = [json.loads(line) for line in result["stdout"].splitlines() if line]
            if not page:
                return rows, True
            ids = [str(row.get("id", "")) for row in page]
            if any(not value or value in seen for value in ids):
                return rows, False
            seen.update(ids)
            rows += page
            cursor = ids[0]
        return rows, False

    def inject(self, function, body):
        path = self.root / "scripts/drivers/storage/sqlite.sh"
        original = path.read_bytes()
        prefix = f'eval "$(declare -f {function} | sed \'1s/{function}/_issue253_original_{function}/\')"\n'
        guard = ""
        if function == "storage_read_cursor_consume":
            guard = 'if [ "$#" -le 3 ]; then _issue253_original_storage_read_cursor_consume "$@"; return $?; fi\n'
        path.write_bytes(original + f'\n# issue253 disposable seam\n{prefix}{function}() {{\n{guard}touch "$TMPDIR/{function}.injected"\n{body}\n}}\n'.encode())
        self.injections.append({"path": str(path), "function": function,
                                "before_sha256": hashlib.sha256(original).hexdigest(),
                                "after_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "body": body})

    def save(self):
        (self.root / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n")
        (self.root / "injections.json").write_text(json.dumps(self.injections, indent=2) + "\n")


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def run_route(source, root):
    results = {}

    def fixture(name):
        print(f"{root.name}: {name}", flush=True)
        return Fixture(source, root / name)

    f = fixture("correlation")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        expected = list(pool.map(f.send, ("parallel-A", "parallel-B")))
    expected += [f.send("foreign-recipient", "delegate"), f.send("foreign-team", team="other")]
    rows, complete = f.history()
    result = correlate(rows, expected[:3], complete)
    received = f.call("inbox.sh", "team", "receiver")
    result["scope_payload"] = (all(item[0] in received["stdout"] for item in expected[:2])
                               and all(item[0] not in received["stdout"] for item in expected[2:])
                               and all(item[3].replace("\n", "\\n").replace("\t", "\\t") in received["stdout"] for item in expected[:2]))
    if not result["scope_payload"]:
        result["status"] = "incompatible"
    result["receiver_ack"] = "unknown"
    results["parallel_send_scope_payload"] = result
    # A fake URL is a fixture output only; these edges do not assert host ACK.
    if result["status"] == "pass":
        incoming = result["ids"]["parallel-A"]
        delegated = f.send("delegate-A", "delegate", text=f"parentMessageId={incoming}")
        rows, complete = f.history()
        delegation = correlate(rows, [delegated], complete)
        if delegation["status"] == "pass":
            delegated_receive = f.call("inbox.sh", "team", "delegate")
            reply = f.send("reply-A", text=f"parentMessageId={delegation['ids']['delegate-A']}")
            reply_receive = f.call("inbox.sh", "team", "receiver")
            rows, complete = f.history()
            chain = correlate(rows, [expected[0], delegated, reply], complete)
            chain["record_url"] = "https://fixture.invalid/issue253/record-A"
            chain["protocol_ack"] = "unknown"
            chain["handoff_payloads_observed"] = (expected[0][0] in received["stdout"]
                                                   and delegated[0] in delegated_receive["stdout"]
                                                   and reply[0] in reply_receive["stdout"])
            results["id_chain"] = chain
    partial, partial_complete = f.history(max_pages=1)
    results["truncated_history"] = correlate(partial, expected[:2], partial_complete)
    results["missing_history"] = correlate([], expected[:2], True)
    f.save()

    for mode in ("before_receive", "interrupted", "parallel", "mark_failure"):
        f = fixture("inbox_" + mode)
        item = f.send("inbox-" + mode)
        stopped = None
        if mode == "before_receive":
            # Fault seam before exec: no consumer behavior is replaced or acknowledged.
            wrapper = f.root / "scripts/issue253-before-entry.sh"
            wrapper.write_text('touch "$TMPDIR/before-entry.reached"\n'
                               'sleep 5\nexec bash "$(dirname "$0")/inbox.sh" "$@"\n')
            held = f.start(wrapper.name, "team", "receiver")
            entry_reached = wait_for(lambda: (f.root / "tmp/before-entry.reached").exists())
            stopped = f.finish(held, stop=True)
        if mode == "mark_failure":
            f.inject("storage_mark_read_batch", "return 73")
        handles, barriers = [], []
        if mode in ("interrupted", "parallel"):
            for number in range(2 if mode == "parallel" else 1):
                barrier = f.root / f"barrier-{number}"
                barriers.append(barrier)
                handles.append(f.start("inbox.sh", "team", "receiver",
                                       extra={"AGMSG_TEST_MARK_BARRIER": str(barrier)}))
            reached = wait_for(lambda: all(Path(str(b) + ".reached").exists() for b in barriers))
            if mode == "parallel":
                for barrier in barriers:
                    Path(str(barrier) + ".release").touch()
            observed = [f.finish(handle, stop=mode == "interrupted") for handle in handles]
        else:
            reached = True
            observed = [f.call("inbox.sh", "team", "receiver")]
        replay = f.call("inbox.sh", "team", "receiver")
        if mode == "mark_failure" and not (f.root / "tmp/storage_mark_read_batch.injected").exists():
            raise RuntimeError("mark-read failure seam was not reached")
        history, complete = f.history()
        results["inbox_" + mode] = {
            "status": receiver_verdict(observed, 2, item[0]) if mode == "parallel" else "unknown",
            "barrier_reached": reached, "correlation": correlate(history, [item], complete),
            "handoffs": [item[0] in row["stdout"] for row in observed],
            "replayed": item[0] in replay["stdout"],
            "ack": ack_verdict(observed[0]["rc"], None, mode == "interrupted", mode == "mark_failure"),
            "pre_receive_handoff": False if mode == "before_receive" else None,
        }
        if stopped is not None:
            results["inbox_" + mode].update(
                status="pass" if entry_reached and not stopped["stdout"] and item[0] in observed[0]["stdout"] else "unknown",
                stopped_before_entry_pid=stopped["pid"], stopped_rc=stopped["rc"],
                pre_receive_handoff=bool(stopped["stdout"]))
        f.save()

    f = fixture("claim")
    if not (source / "scripts/claim.sh").exists():
        results["claim_owner_release_expiry"] = {"status": "unsupported", "helper": "claim.sh"}
    else:
        item = f.send("claim-owner")
        claimed = f.call("claim.sh", "next", "team", "receiver", "sid:pid-A", "60")
        claim_id = claimed["stdout"].split("\x1f")[0].strip()
        if not claim_id.isdecimal():
            raise RuntimeError("reference claim did not return a numeric ID")
        wrong = f.call("claim.sh", "ack", claim_id, "sid:pid-B", "wrong_owner")
        busy = f.call("claim.sh", "next", "team", "receiver", "sid:pid-B", "60")
        release = f.call("claim.sh", "release", claim_id, "sid:pid-A")
        expired = f.call("claim.sh", "next", "team", "receiver", "sid:pid-A", "0")
        resumed = f.call("claim.sh", "next", "team", "receiver", "sid:pid-B", "60")
        ack = f.call("claim.sh", "ack", claim_id, "sid:pid-B", "fixture_handoff")
        status = f.call("message-status.sh", "team", "receiver", "--format", "json", "--id", claim_id)
        state = json.loads(status["stdout"]).get("state") if status["rc"] == 0 else None
        history, complete = f.history()
        mapping = correlate(history, [item], complete)
        history_status = None
        if mapping["status"] == "pass":
            history_status = f.call("message-status.sh", "team", "receiver", "--format", "json",
                                    "--id", mapping["ids"][item[0]])
        results["claim_owner_release_expiry"] = {
            "status": "pass" if item[0] in claimed["stdout"] and wrong["rc"] != 0 and not busy["stdout"].strip()
            and release["rc"] == 0 and expired["stdout"].startswith(claim_id + "\x1f")
            and resumed["stdout"].startswith(claim_id + "\x1f")
            and ack_verdict(ack["rc"], state) == "pass" else "incompatible",
            "claim_id": claim_id, "public_history": mapping,
            "history_id_status_command": history_status,
            "same_sid_different_owner_tokens": True, "actual_session_authentication": "unknown"}
    f.save()
    results.update(run_watch_cases(source, root))
    return results


def run_watch_cases(source, root, modes=("normal", "parallel", "interrupted", "mark_failure", "same_sid_resume")):
    results = {}
    for mode in modes:
        print(f"{root.name}: watch_{mode}", flush=True)
        f = Fixture(source, root / ("watch_" + mode), watch_only=True)
        request = "watch-" + mode
        # Initialize the real store before starting a watcher; bootstrap traffic is drained.
        f.send("bootstrap", recipient="sender")
        f.call("inbox.sh", "team", "sender")
        f.inject("storage_watch_after", '_issue253_original_storage_watch_after "$@"\nlocal result=$?\nif [ "$2" = "team:receiver" ]; then touch "$TMPDIR/watch-poll.$AGMSG_TEST_RECEIVER_TAG"; fi\nreturn "$result"')
        if mode == "mark_failure":
            f.inject("storage_read_cursor_consume", "return 73")
        if mode == "interrupted":
            f.inject("storage_read_cursor_consume", 'touch "$TMPDIR/consume.reached"\nlocal count=0\nwhile [ ! -e "$TMPDIR/consume.release" ] && [ "$count" -lt 100 ]; do sleep 0.05; count=$((count + 1)); done\nreturn 73')
        hosts = []

        def start_watch(sid):
            host = subprocess.Popen(["sleep", "60"], env=f.env)
            hosts.append(host)
            return f.start("watch.sh", sid, str(f.root / "project"), "claude-code",
                           extra={"AGMSG_AGENT_PID": str(host.pid), "AGMSG_TEST_RECEIVER_TAG": str(host.pid)})

        handles = [start_watch("fixture-sid")]
        if mode in ("parallel", "same_sid_resume"):
            sid = "fixture-sid" if mode == "same_sid_resume" else "fixture-other-sid"
            handles.append(start_watch(sid))
        ready = wait_for(lambda: all((f.root / f"tmp/watch-poll.{host.pid}").exists() for host in hosts))
        item = f.send(request)
        reached = wait_for(lambda: any(request in Path(str(h[2]) + ".stdout").read_text() for h in handles))
        if mode == "interrupted":
            reached = reached and wait_for(lambda: (f.root / "tmp/consume.reached").exists())
        # Give both started receivers bounded opportunity; retain empty/exited receivers too.
        time.sleep(0.4)
        observations = [f.finish(handle, stop=True) for handle in handles]
        initial_host_pids = [host.pid for host in hosts]
        for host in hosts:
            host.terminate()
            host.wait(timeout=5)
        if mode in ("interrupted", "mark_failure") and not (f.root / "tmp/storage_read_cursor_consume.injected").exists():
            f.save()
            raise RuntimeError("watch consume failure seam was not reached")
        if mode == "interrupted":
            (f.root / "tmp/consume.release").touch()
            restored = f.root / "scripts/drivers/storage/sqlite.sh"
            shutil.copyfile(source / "scripts/drivers/storage/sqlite.sh", restored)
            f.injections[-1]["restored_sha256"] = hashlib.sha256(restored.read_bytes()).hexdigest()
        resumed = start_watch("fixture-sid")
        replayed = wait_for(lambda: request in Path(str(resumed[2]) + ".stdout").read_text(), seconds=2)
        replay = f.finish(resumed, stop=True)
        hosts[-1].terminate()
        hosts[-1].wait(timeout=5)
        history, complete = f.history()
        results["watch_" + mode] = {
            "status": receiver_verdict(observations, len(handles), request)
            if ready and reached and mode not in ("interrupted", "mark_failure") else "unknown",
            "duplicate_observation": receiver_verdict(observations, len(handles), request),
            "public_helper_only": True, "protocol_ack": "unknown", "observation_reached": reached,
            "all_receivers_polled_before_send": ready,
            "correlation": correlate(history, [item], complete), "replayed": replayed,
            "receiver_pids": [row["pid"] for row in observations], "resume_pid": replay["pid"],
            "synthetic_host_pids": initial_host_pids, "resume_synthetic_host_pid": hosts[-1].pid,
            "host_description": "owned bounded sleep process, not an LLM or Monitor host",
            "handoff_counts": [row["stdout"].count(request) for row in observations],
            "window_seconds_after_first_handoff": 0.4,
        }
        f.save()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"harness_status": "running", "compatibility": {"F": "unknown", "O": "unknown"},
              "live_p2_acceptance": False, "routes": {}, "sources": {},
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        report["versions"] = {program: subprocess.check_output(command, text=True).strip()
                              for program, command in {"python": ["python3", "--version"],
                                                       "bash": ["bash", "--version"],
                                                       "sqlite3": ["sqlite3", "--version"],
                                                       "bats": ["bats", "--version"]}.items()}
        sources = {}
        for route, cutoff in CUTOFFS.items():
            tree = subprocess.check_output(["git", "-C", str(args.local_repo), "rev-parse", cutoff + "^{tree}"], text=True).strip()
            archive = args.output / f"source-{route}.tar"
            with archive.open("wb") as stream:
                subprocess.run(["git", "-C", str(args.local_repo), "archive", cutoff], stdout=stream, check=True)
            source = args.output / f"source-{route}"
            source.mkdir()
            subprocess.run(["tar", "-xf", str(archive), "-C", str(source)], check=True)
            report["sources"][route] = {"cutoff": cutoff, "tree": tree,
                                        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            sources[route] = source
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending = {route: pool.submit(run_route, source, args.output / route)
                       for route, source in sources.items()}
            errors = []
            for route, future in pending.items():
                try:
                    report["routes"][route] = future.result()
                except Exception as error:
                    errors.append(f"{route}: {error}")
                    report["routes"][route] = {"harness_error": str(error)}
                    continue
                cases = report["routes"][route]
                # Completion requires the positive control and intended failure boundaries.
                # Provider incompatibilities remain report data, never coerced into PASS.
                exercised = (cases["parallel_send_scope_payload"]["status"] == "pass"
                             and cases.get("id_chain", {}).get("handoff_payloads_observed")
                             and cases["inbox_before_receive"]["status"] == "pass"
                             and cases["inbox_interrupted"]["barrier_reached"]
                             and cases["inbox_parallel"]["barrier_reached"]
                             and cases["watch_normal"]["observation_reached"]
                             and cases["watch_interrupted"]["observation_reached"])
                if not exercised:
                    errors.append(f"{route}: required control was not exercised; inspect command packet")
                statuses = [value["status"] for value in cases.values()]
                report["compatibility"][route] = "incompatible" if "incompatible" in statuses else "unknown"
        if errors:
            raise RuntimeError("; ".join(errors))
        reference = subprocess.run(["bats", str(sources["F"] / "tests/test_claims.bats")],
                                   capture_output=True, text=True, timeout=120)
        (args.output / "reference-claims.stdout").write_text(reference.stdout)
        (args.output / "reference-claims.stderr").write_text(reference.stderr)
        report["reference_claims"] = {"rc": reference.returncode, "cutoff": CUTOFFS["F"]}
        if reference.returncode:
            raise RuntimeError("unmodified F reference claims tests failed")
        report["harness_status"] = "completed"
    except Exception as error:
        report["harness_status"] = "error"
        report["error"] = str(error)
        raise
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
