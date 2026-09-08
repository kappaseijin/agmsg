#!/usr/bin/env python3
"""Exercise Issue #247 Patch A against an isolated fixed Git object."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time


CUTOFF = "e58dbafad5a84be625f070385bb0c076c3daa4db"
PATCH_RELATIVE = Path("patches/issue247-patch-a.patch")
EXPECTED_SHAPE = {
    "scripts/lib/actas-lock.sh": 2,
    "tests/test_actas_lock.bats": 2,
}


def patch_shape(text):
    current = None
    counts = {}
    for line in text.splitlines():
        if line.startswith("diff --git a/"):
            current = line.split(" b/", 1)[1]
            counts.setdefault(current, 0)
        elif line.startswith("@@"):
            if current is None:
                raise ValueError("hunk before file header")
            counts[current] += 1
    return counts


def mutation_scope():
    return {
        "empty_to_absent": "patch_a",
        "read_error_to_absent": "patch_a",
        "stale_query_gc": "patch_a",
        "sid_only_compare": "patch_a",
        "remove_fingerprint_recheck": "patch_b",
        "nonregular_to_absent": "patch_b",
        "follow_concurrent_owner": "patch_b",
    }


def expected_observation():
    return {
        "red": {"detected": True},
        "green": {
            "focused_pass": True,
            "side_effects_equal": True,
            "empty": "unknown",
            "read_error": "unknown",
            "same_sid_different_pid": "other",
            "stale": "free_preserved",
            "invalid": "legacy_stale_preserved",
            "owner_swap_preserved": True,
        },
    }


def patch_a_verdict(observed):
    expected = expected_observation()
    return "pass" if observed == expected else "incompatible"


def run(command, cwd, env=None):
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    return {"rc": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


def export_tree(repo, revision, destination):
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", revision], capture_output=True, check=True
    ).stdout
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(destination, filter="data")


def digest_paths(root, relatives):
    rows = []
    for relative in relatives:
        path = root / relative
        if not path.exists():
            rows.append((relative, "absent"))
            continue
        for item in sorted([path, *path.rglob("*")]):
            rel = str(item.relative_to(root))
            if item.is_symlink():
                rows.append((rel, "symlink", os.readlink(item)))
            elif item.is_file():
                rows.append((rel, "file", hashlib.sha256(item.read_bytes()).hexdigest()))
            elif item.is_dir():
                rows.append((rel, "dir"))
    return rows


def fixture_processes(root):
    listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True)
    return sorted(line.strip() for line in listing.stdout.splitlines() if str(root) in line)


def state_case(root, setup, caller="sid-me", override=""):
    lock = root / "run/actas.T__alice.session"
    lock.parent.mkdir(exist_ok=True)
    if lock.is_symlink() or lock.is_file():
        lock.unlink()
    elif lock.exists():
        shutil.rmtree(lock)
    setup(lock)
    before = (lock.read_bytes() if lock.is_file() else None)
    script = f'''set +e
export SKILL_DIR="$1"
source "$SKILL_DIR/scripts/lib/actas-lock.sh"
{override}
output="$(actas_lock_state T alice "$2")"
rc=$?
printf 'rc=%s\\noutput=%s\\n' "$rc" "$output"
'''
    result = run(["bash", "-c", script, "fixture", str(root), caller], root)
    values = dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)
    after = (lock.read_bytes() if lock.is_file() else None)
    return {"rc": int(values["rc"]), "output": values.get("output", ""),
            "before": before.decode("utf8", "replace") if before is not None else None,
            "after": after.decode("utf8", "replace") if after is not None else None}


def owner_swap_case(root):
    """Freeze the owner read at an explicit test-only barrier, then swap it."""
    lock = root / "run/actas.T__alice.session"
    lock.parent.mkdir(exist_ok=True)
    lock.write_text("owner-a\n")
    barrier = root / "run/issue247-owner-swap"
    for suffix in (".reached", ".release"):
        candidate = Path(str(barrier) + suffix)
        if candidate.exists():
            candidate.unlink()
    script = r'''set +e
export SKILL_DIR="$1"
source "$SKILL_DIR/scripts/lib/actas-lock.sh"
barrier="$2"
actas_lock_owner() {
  local lock; lock="$(actas_lock_path "$1" "$2")"
  : > "$barrier.reached"
  while [ ! -e "$barrier.release" ]; do :; done
  head -1 "$lock"
}
output="$(actas_lock_state T alice sid-me)"
rc=$?
printf 'rc=%s\noutput=%s\n' "$rc" "$output"
'''
    process = subprocess.Popen(
        ["bash", "-c", script, "fixture", str(root), str(barrier)],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    reached = Path(str(barrier) + ".reached")
    deadline = time.monotonic() + 5
    while not reached.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    barrier_reached = reached.exists()
    if barrier_reached:
        lock.write_text("owner-b\n")
        Path(str(barrier) + ".release").touch()
    stdout, stderr = process.communicate(timeout=5)
    values = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
    return {"rc": int(values.get("rc", "99")), "output": values.get("output", ""),
            "before": "owner-a\n", "after": lock.read_text() if lock.exists() else None,
            "barrier_reached": barrier_reached, "stderr": stderr}


def observe(root):
    empty = state_case(root, lambda lock: lock.write_bytes(b""))
    read_error = state_case(
        root, lambda lock: lock.write_text("owner.999999\n"),
        override="actas_lock_owner() { return 1; }",
    )

    holder = subprocess.Popen(["sleep", "60"])
    instance = root / f"run/cc-instance.{holder.pid}"
    try:
        instance.write_text(f"same.{holder.pid}\n")
        same = state_case(
            root, lambda lock: lock.write_text(f"same.{holder.pid}\n"),
            caller="same.999999",
        )
    finally:
        holder.terminate()
        holder.wait(timeout=5)
        instance.unlink(missing_ok=True)
    stale = state_case(root, lambda lock: lock.write_text("sid-dead\n"))
    invalid = state_case(root, lambda lock: lock.write_text("bad\nextra\n"))
    swap = owner_swap_case(root)
    return {
        "empty": "unknown" if empty["rc"] != 0 and empty["output"] == "unknown" else "free",
        "read_error": "unknown" if read_error["rc"] != 0 and read_error["output"] == "unknown" else "free",
        "same_sid_different_pid": "other" if same["output"].startswith("other:same.") else same["output"],
        "stale": "free_preserved" if stale["output"] == "free" and stale["before"] == stale["after"] else "changed",
        "invalid": "legacy_stale_preserved" if invalid["output"] == "free" and invalid["before"] == invalid["after"] else "changed",
        "owner_swap_preserved": swap["barrier_reached"] and swap["after"] == "owner-b\n",
        "raw": {"empty": empty, "read_error": read_error, "same_sid_different_pid": same,
                "stale": stale, "invalid": invalid, "owner_swap": swap},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.local_repo.resolve()
    output = args.output.resolve()
    before_root, after_root = output / "official-before", output / "patch-a"
    export_tree(repo, CUTOFF, before_root)
    shutil.copytree(before_root, after_root)

    patch_path = repo / PATCH_RELATIVE
    patch_text = patch_path.read_text()
    shape = patch_shape(patch_text)
    if shape != EXPECTED_SHAPE:
        raise SystemExit(f"unexpected patch shape: {shape}")

    red_observation = observe(before_root)
    red = {"detected": red_observation["empty"] == "free",
           "observation": red_observation}
    applied = run(["git", "apply", "--unidiff-zero", str(patch_path)], after_root)
    if applied["rc"] != 0:
        raise SystemExit(json.dumps(applied))

    watched = ["run", "teams", "db", "data"]
    before_side_effects = digest_paths(after_root, watched)
    processes_before = fixture_processes(after_root)
    green_observation = observe(after_root)
    focused = run(["bats", "tests/test_actas_lock.bats"], after_root)
    after_side_effects = digest_paths(after_root, watched)
    processes_after = fixture_processes(after_root)
    # Fixture-authored lock controls live under run/. Compare persistent areas and
    # harness-owned process inventory; each lock case separately proves preservation.
    side_effects_equal = (
        digest_paths(after_root, ["teams", "db", "data"])
        == digest_paths(before_root, ["teams", "db", "data"])
        and processes_before == processes_after
    )
    green = {key: green_observation[key] for key in expected_observation()["green"]
             if key not in ("focused_pass", "side_effects_equal")}
    focused_count = sum(line.startswith("ok ") for line in focused["stdout"].splitlines())
    green.update(focused_pass=focused["rc"] == 0 and focused_count == 24,
                 side_effects_equal=side_effects_equal)
    verdict_input = {"red": {"detected": red["detected"]}, "green": green}
    report = {
        "cutoff": CUTOFF,
        "patch": str(PATCH_RELATIVE),
        "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        "patch_shape": shape,
        "red": red,
        "green": green_observation,
        "focused_test": focused,
        "focused_count": focused_count,
        "side_effects": {"watched": watched, "before": before_side_effects,
                         "after": after_side_effects, "equal": side_effects_equal,
                         "processes_before": processes_before, "processes_after": processes_after},
        "mutation_scope": mutation_scope(),
        "status": patch_a_verdict(verdict_input),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
