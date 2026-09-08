#!/usr/bin/env python3
"""Verify Issue #247 Patch B against official cutoff plus accepted Patch A."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


CUTOFF = "e58dbafad5a84be625f070385bb0c076c3daa4db"
PATCH_A = Path("patches/issue247-patch-a.patch")
PATCH_A_SHA256 = "3199da771286cf229d91f7c08d7693b8ed76e9bad60f7c53a3fd6681ccdca49f"
PATCH_B = Path("patches/issue247-patch-b.patch")
EXPECTED_FILES = {
    "README.md",
    "scripts/api.sh",
    "scripts/lib/api-actas-owner.sh",
    "tests/test_api_actas_owner.bats",
}


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


def patch_files(text):
    return {
        line.split(" b/", 1)[1]
        for line in text.splitlines()
        if line.startswith("diff --git a/")
    }


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
    listing = subprocess.run(
        ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True
    )
    return sorted(line.strip() for line in listing.stdout.splitlines() if str(root) in line)


def apply_patch(root, patch):
    result = run(["git", "apply", "--unidiff-zero", str(patch)], root)
    if result["rc"] != 0:
        raise RuntimeError(json.dumps(result))


def public_probe(root):
    result = run(
        ["bash", "scripts/api.sh", "get", "teams", "fixture", "actas-owner", "alice", "--schema-version", "1"],
        root,
    )
    return result


def count_bats(result):
    return sum(line.startswith("ok ") for line in result["stdout"].splitlines())


def mutate(source, old, new):
    text = source.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"mutation anchor count={text.count(old)}: {old[:60]!r}")
    source.write_text(text.replace(old, new))


def mutation_cases(root):
    source_rel = Path("scripts/lib/api-actas-owner.sh")
    cases = []
    definitions = [
        (
            "remove_fingerprint_recheck",
            "|| ! _api_owner_claim_stable \"$lock\" \"$claim_state\" \"$claim_fp\" \"$claim_snapshot\" \\\n    \"$run_dir\" \"$run_dir_state\" \"$run_dir_fp\"",
            "|| ! true",
            "claim exchange during observation",
        ),
        (
            "nonregular_to_absent",
            "claim_state=invalid\n      claim_fp=\"nonregular:$(_api_owner_fingerprint \"$lock\" 2>/dev/null || printf '%s' '<unreadable>')\"",
            "claim_state=absent\n      claim_fp=''",
            "malformed, whitespace/control, or nonregular claims",
        ),
        (
            "follow_concurrent_owner",
            "_api_owner_emit \"$team\" \"$agent\" unknown concurrent_change '' '' '' unknown\n    exit 1",
            "unset AGMSG_TEST_API_ACTAS_OWNER_BARRIER\n    _api_owner_query \"$team\" \"$agent\"\n    exit $?",
            "claim exchange during observation",
        ),
    ]
    for name, old, new, test_filter in definitions:
        mutant = root.parent / f"mutant-{name}"
        shutil.copytree(root, mutant)
        mutate(mutant / source_rel, old, new)
        result = run(["bats", "--tap", "-f", test_filter, "tests/test_api_actas_owner.bats"], mutant)
        cases.append({"name": name, "killed": result["rc"] != 0, "result": result})
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.local_repo.resolve()
    output = args.output.resolve()
    before = output / "official-before"
    patch_a_root = output / "patch-a"
    combined = output / "patch-a-b"
    export_tree(repo, CUTOFF, before)
    shutil.copytree(before, patch_a_root)

    patch_a = repo / PATCH_A
    patch_b = repo / PATCH_B
    if hashlib.sha256(patch_a.read_bytes()).hexdigest() != PATCH_A_SHA256:
        raise SystemExit("Patch A digest mismatch")
    apply_patch(patch_a_root, patch_a)
    shutil.copytree(patch_a_root, combined)

    red = public_probe(patch_a_root)
    apply_patch(combined, patch_b)
    watched = ["run", "teams", "db", "data"]
    side_before = digest_paths(combined, watched)
    processes_before = fixture_processes(combined)
    focused = run(["bats", "--tap", "tests/test_api_actas_owner.bats"], combined)
    side_after = digest_paths(combined, watched)
    processes_after = fixture_processes(combined)
    mutations = mutation_cases(combined)

    shape = patch_files(patch_b.read_text())
    status = "pass" if (
        red["rc"] != 0
        and shape == EXPECTED_FILES
        and focused["rc"] == 0
        and count_bats(focused) == 11
        and side_before == side_after
        and processes_before == processes_after
        and all(case["killed"] for case in mutations)
    ) else "incompatible"
    report = {
        "cutoff": CUTOFF,
        "patch_a_sha256": PATCH_A_SHA256,
        "patch_b_sha256": hashlib.sha256(patch_b.read_bytes()).hexdigest(),
        "patch_b_files": sorted(shape),
        "red": red,
        "focused_test": focused,
        "focused_count": count_bats(focused),
        "side_effects": {
            "watched": watched,
            "before": side_before,
            "after": side_after,
            "equal": side_before == side_after,
            "processes_before": processes_before,
            "processes_after": processes_after,
        },
        "mutations": mutations,
        "status": status,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if status == "pass" else 1)


if __name__ == "__main__":
    main()
