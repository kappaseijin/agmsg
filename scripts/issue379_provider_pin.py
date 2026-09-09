#!/usr/bin/env python3
"""Run Issue #379's fork-local, fixed-provider B1/B2 controls."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

MANIFEST = Path("docs/decisions/issue379-provider-manifest.json")
REPOSITORY = "kappaseijin/agmsg"


class ProviderError(RuntimeError):
    pass


def load_manifest(path):
    return json.loads(path.read_text(encoding="utf8"))


def validate_manifest(manifest, root):
    if manifest.get("repository") != REPOSITORY:
        raise ProviderError("repository")
    commit = manifest.get("commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ProviderError("commit")
    if manifest.get("contractSchemaVersion") != 1:
        raise ProviderError("schema")
    if manifest.get("canonicalRoot") != ".":
        raise ProviderError("canonicalRoot")
    probe = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
                           capture_output=True)
    if probe.returncode:
        raise ProviderError("unreachable commit")
    return root.resolve(), commit


def _run(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    return {"command": args, "rc": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def _archive(root, commit, destination):
    data = subprocess.run(["git", "-C", str(root), "archive", commit], check=True,
                          capture_output=True).stdout
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(destination, filter="data")


def _snapshot(root):
    digest = hashlib.sha256()
    for relative in ("config", "runtime", "claim", "session", "process"):
        digest.update(relative.encode())
        path = root / relative
        if path.exists():
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    digest.update(str(item.relative_to(root)).encode())
                    digest.update(item.read_bytes())
    return digest.hexdigest()


def run_fixture(root, output):
    manifest = load_manifest(root / MANIFEST)
    canonical_root, commit = validate_manifest(manifest, root)
    provider = output / "provider"
    _archive(canonical_root, commit, provider)
    before = _snapshot(provider)
    b1 = _run(["bats", "tests/test_api_registrations.bats", "-f",
               "one tuple|multiple projects|duplicate tuples|corruption|source exchange|read-only"], provider)
    b2 = _run(["bats", "tests/test_api_actas_owner.bats", "-f",
               "alive formal|absent claim|dead owner|liveness cannot|malformed"], provider)
    after = _snapshot(provider)
    b1_ok = b1["rc"] == 0
    b2_ok = b2["rc"] == 0
    report = {
        "provider": {"repository": REPOSITORY, "commit": commit,
                     "canonicalRoot": str(canonical_root),
                     "manifestDigest": hashlib.sha256((root / MANIFEST).read_bytes()).hexdigest()},
        # The fixture is the consumer for G1.  It receives the provider commit
        # only from the manifest and archives that exact object before it calls
        # either public API entry point.
        "consumer": {"providerCommit": commit},
        "reviewHead": subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                      text=True, capture_output=True, check=True).stdout.strip(),
        "b1": {"command": manifest["b1"]["command"], "positive": "pass" if b1_ok else "fail",
               "negative": "pass" if b1_ok else "fail", "run": b1},
        "b2": {"command": manifest["b2"]["command"], "positive": "pass" if b2_ok else "fail",
               "negative": "pass" if b2_ok else "fail", "run": b2},
        "readOnly": {"before": before, "after": after},
    }
    report["status"] = "pass" if b1_ok and b2_ok and before == after else "fail"
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_fixture(args.local_repo.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
