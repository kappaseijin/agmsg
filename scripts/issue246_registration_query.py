#!/usr/bin/env python3
"""Apply Issue #246's provider patch to an isolated official cutoff."""
import argparse, hashlib, io, json, subprocess, tarfile
from pathlib import Path

CUTOFF = "e58dbafad5a84be625f070385bb0c076c3daa4db"
PATCH = Path("patches/issue246-registration.patch")
FILES = {"README.md", "scripts/api.sh", "scripts/lib/api-registrations.sh", "tests/test_api.bats", "tests/test_api_registrations.bats"}

def run(args, cwd):
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    return {"command": args, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

def export_tree(repo, output):
    data = subprocess.run(["git", "-C", str(repo), "archive", CUTOFF], check=True, capture_output=True).stdout
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(output, filter="data")

def patch_files(text):
    return {line[11:].split(" b/", 1)[1] for line in text.splitlines() if line.startswith("diff --git a/")}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--local-repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    repo, output = args.local_repo.resolve(), args.output.resolve()
    patch = repo / PATCH
    text = patch.read_text()
    if patch_files(text) != FILES:
        raise SystemExit("unexpected patch file set")
    tree = output / "official-cutoff-patched"
    export_tree(repo, tree)
    applied = run(["git", "apply", "--check", str(patch)], tree)
    if applied["rc"] == 0:
        applied = run(["git", "apply", str(patch)], tree)
    syntax = run(["bash", "-n", "scripts/lib/api-registrations.sh"], tree)
    registrations = run(["bats", "tests/test_api_registrations.bats"], tree)
    api = run(["bats", "tests/test_api.bats"], tree)
    report = {"cutoff": CUTOFF, "patch": str(PATCH), "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(), "patch_files": sorted(patch_files(text)), "apply": applied, "syntax": syntax, "registrations": registrations, "api": api}
    report["status"] = "pass" if all(x["rc"] == 0 for x in (applied, syntax, registrations, api)) else "fail"
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)

if __name__ == "__main__":
    main()
