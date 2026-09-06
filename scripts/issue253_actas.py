#!/usr/bin/env python3
"""Supplemental role-lock observations, never message ACK or recovery proof."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import time

from issue253_public_path import CUTOFFS, Fixture, correlate, wait_for

_TOKEN = object()


class Observation:
    """A named bounded-wait result. Only observe_until constructs it."""
    __slots__ = ("label", "reached")

    def __init__(self, label, reached, _key=None):
        if _key is not _TOKEN:
            raise TypeError("Observation is constructed by observe_until only")
        self.label = label
        self.reached = reached

    def __bool__(self):
        return self.reached


def observe_until(label, predicate, seconds):
    """Wrap wait_for so the outcome cannot reach a verdict as a bare bool."""
    return Observation(label, wait_for(predicate, seconds=seconds), _TOKEN)


def require(observation, label):
    """Unwrap the named observation, or refuse. No default, no coercion."""
    if not isinstance(observation, Observation):
        raise TypeError(f"{label}: expected Observation from observe_until, "
                        f"got {type(observation).__name__}")
    if observation.label != label:
        raise TypeError(f"expected observation {label!r}, got {observation.label!r}")
    return observation.reached


def claim_state(command):
    fields = dict(part.split("=", 1) for part in command["stdout"].split() if "=" in part)
    if command["rc"] == 0 and fields.get("status") == "ok" and fields.get("team") == "team":
        return "acquired", None
    if command["rc"] == 1 and fields.get("status") == "held" and fields.get("team") == "team" and fields.get("owner"):
        return "held", fields["owner"]
    return "start_failure", None


def ownership(instances):
    if len(instances) != 2:
        return "unknown"
    if len({i["host_pid"] for i in instances}) != 2:
        return "unknown"
    if any(i["boundary"] in ("start_failure", "ready_missing") for i in instances):
        return "unknown"
    winners = [i for i in instances if i["boundary"] == "ready"]
    losers = [i for i in instances if i["boundary"] == "held"]
    if len(winners) != 1 or len(losers) != 1:
        return "incompatible"
    if losers[0]["held_owner"] != winners[0]["instance"]:
        return "unknown"
    return "pass"


def evaluate(instances, correlation, polled, observed):
    # A missed observation window says nothing about the event; never report it as a verdict.
    if not require(polled, "watch_poll") or not require(observed, "handoff"):
        return "unknown"
    ids = correlation.get("ids", {})
    if correlation.get("status") != "pass" or len(ids) != 1 or not all(isinstance(value, str) and value for value in ids.values()):
        return "unknown"
    state = ownership(instances)
    if state != "pass":
        return state
    winner = next(i for i in instances if i["boundary"] == "ready")
    loser = next(i for i in instances if i["boundary"] == "held")
    if loser["handoffs"] or winner["handoffs"] > 1:
        return "incompatible"
    return "pass" if winner["handoffs"] == 1 else "unknown"


def observe(source, root, same_sid):
    f = Fixture(source, root, watch_only=True)
    hosts, watches, instances = [], [], []
    try:
        f.send("bootstrap", recipient="sender")
        f.call("inbox.sh", "team", "sender")
        f.inject("storage_watch_after", '_issue253_original_storage_watch_after "$@"\nlocal result=$?\nif [ "$result" -eq 0 ] && [ "$2" = "team:receiver" ]; then touch "$TMPDIR/poll.$AGMSG_TEST_RECEIVER_TAG"; fi\nreturn "$result"')
        for index in range(2):
            host = subprocess.Popen(["sleep", "120"], env=f.env)
            hosts.append(host)
            sid = "fixture-sid" if same_sid else f"fixture-sid-{index}"
            instances.append({"sid": sid, "host_pid": host.pid, "instance": f"{sid}.{host.pid}",
                              "handoffs": 0, "held_owner": None})
        def acquire(instance):
            return f.call("actas-claim.sh", str(root / "project"), "claude-code", "receiver", instance["sid"],
                          extra={"AGMSG_AGENT_PID": str(instance["host_pid"])})
        with ThreadPoolExecutor(max_workers=2) as pool:
            commands = list(pool.map(acquire, instances))
        for instance, command in zip(instances, commands):
            state, owner = claim_state(command)
            instance.update(claim=command, boundary=state, held_owner=owner)
            if state == "acquired":
                handle = f.start("watch.sh", instance["sid"], str(root / "project"), "claude-code", "receiver",
                                 extra={"AGMSG_AGENT_PID": str(instance["host_pid"]),
                                        "AGMSG_TEST_RECEIVER_TAG": str(instance["host_pid"])})
                watches.append((instance, handle))
        polled = observe_until("watch_poll", lambda: all((root / f"tmp/poll.{i['host_pid']}").exists() or h[0].poll() is not None for i, h in watches), 10)
        for instance, handle in watches:
            ready = (root / f"tmp/poll.{instance['host_pid']}").exists()
            instance["boundary"] = "ready" if ready and handle[0].poll() is None else "start_failure" if handle[0].poll() is not None else "ready_missing"
        item = f.send("actas-same" if same_sid else "actas-different")
        observed = observe_until("handoff", lambda: any(item[0] in Path(str(h[2]) + ".stdout").read_text() for _, h in watches), 5)
        time.sleep(0.4)
        for instance, handle in watches:
            command = f.finish(handle, stop=True)
            instance.update(watch=command, handoffs=command["stdout"].count(item[0]))
        watches.clear()
        rows, complete = f.history()
        correlation = correlate(rows, [item], complete)
        # Real rejected registration is a negative startup control, never a held success.
        failure = f.call("actas-claim.sh", str(root / "project"), "claude-code", "unregistered-role", "negative-sid",
                         extra={"AGMSG_AGENT_PID": str(hosts[0].pid)})
        if claim_state(failure)[0] != "start_failure":
            raise RuntimeError("unregistered startup control was misclassified")
        return {"role_ownership": ownership(instances), "role_path": evaluate(instances, correlation, polled, observed),
                "watch_poll_reached": polled.reached, "handoff_observation_reached": observed.reached, "same_sid": same_sid,
                "instances": instances, "correlation": correlation, "startup_negative": failure,
                "message_ack": "unknown", "message_status": "unknown", "recovery": "unknown",
                "general_exclusion": "unknown", "post_handoff_window_seconds": 0.4}
    finally:
        for _, handle in watches:
            if not handle[3].closed:
                f.finish(handle, stop=True)
        for host in hosts:
            if host.poll() is None:
                host.terminate()
            host.wait(timeout=5)
        f.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"harness_status": "running", "sources": {}, "observations": {}, "p2_acceptance": False,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "fixture_module_sha256": hashlib.sha256(Path(__file__).with_name("issue253_public_path.py").read_bytes()).hexdigest(),
              "bash_version": subprocess.check_output(["bash", "--version"], text=True)}
    try:
        for route, cutoff in CUTOFFS.items():
            source = root / ("source-" + route)
            source.mkdir()
            archive = root / (route + ".tar")
            with archive.open("wb") as stream:
                subprocess.run(["git", "-C", str(args.local_repo), "archive", cutoff], stdout=stream, check=True)
            subprocess.run(["tar", "-xf", str(archive), "-C", str(source)], check=True)
            tree = subprocess.check_output(["git", "-C", str(args.local_repo), "rev-parse", cutoff + "^{tree}"], text=True).strip()
            report["sources"][route] = {"commit": cutoff, "tree": tree, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            for same_sid in (False, True):
                case = route + ("-same-sid" if same_sid else "-different-sid")
                print(case, flush=True)
                report["observations"][case] = observe(source, root / case, same_sid)
                (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        report["harness_status"] = "completed"
    except Exception as error:
        report.update(harness_status="error", error=str(error))
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
