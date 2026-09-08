#!/usr/bin/env python3
"""Kill Patch A regressions in disposable official-cutoff trees."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("harness", ROOT / "scripts/issue247_patch_a.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

MUTATIONS = {
    "empty_to_absent": (
        'if [ -z "$owner" ] && [ -f "$lock" ]; then',
        'if false; then',
    ),
    "read_error_to_absent": (
        '''elif [ -f "$lock" ]; then
    # An unreadable lock that is still present is not permission to clean up.
    printf 'unknown\\n'
    return 1''',
        '''elif [ -f "$lock" ]; then
    owner=""
    lock="/issue247-mutation-absent"''',
    ),
    "stale_query_gc": (
        'echo "free"  # stale owner — effectively free, GC will remove it later',
        'rm -f "$lock"\n    echo "free"  # mutation: query performs GC',
    ),
    "sid_only_compare": (
        'if [ "$owner" = "$sid" ]; then',
        'if [ "${owner%.*}" = "${sid%.*}" ]; then',
    ),
}


def export_tree(destination):
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", HARNESS.CUTOFF], capture_output=True, check=True
    ).stdout
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(destination, filter="data")


results = {}
for name, (before, after) in MUTATIONS.items():
    with tempfile.TemporaryDirectory(prefix=f"issue247-{name}-") as directory:
        tree = Path(directory) / "tree"
        export_tree(tree)
        subprocess.run(
            ["git", "apply", "--unidiff-zero", str(ROOT / HARNESS.PATCH_RELATIVE)],
            cwd=tree, capture_output=True, check=True,
        )
        source = tree / "scripts/lib/actas-lock.sh"
        text = source.read_text()
        if text.count(before) != 1:
            raise RuntimeError(f"mutation anchor count for {name}: {text.count(before)}")
        source.write_text(text.replace(before, after))
        observed = HARNESS.observe(tree)
        expected = HARNESS.expected_observation()["green"]
        killed = any(observed[key] != value for key, value in expected.items()
                     if key not in ("focused_pass", "side_effects_equal"))
        results[name] = {"killed": killed, "observation": observed}

for name, scope in HARNESS.mutation_scope().items():
    if scope == "patch_b":
        results[name] = {"status": "deferred_patch_b", "killed": None}

print(json.dumps(results, indent=2))
raise SystemExit(0 if all(row["killed"] for row in results.values()
                          if row["killed"] is not None) else 1)
