#!/usr/bin/env python3
"""Prove eight Issue #246 contract regressions are rejected in isolation."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("issue246", ROOT / "scripts/issue246_registration_query.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

MUTATIONS = {
    "limit_1": ("FROM normalized, json_each(normalized.registrations) AS r\n    ORDER BY", "FROM normalized, json_each(normalized.registrations) AS r\n    LIMIT 1\n    ORDER BY", "duplicate tuples remain visible"),
    "distinct_rows": ("SELECT json_object(\n      'agent', name,", "SELECT DISTINCT json_object(\n      'agent', name,", "duplicate tuples remain visible"),
    "validation_bypass": ('validation="$(_api_registrations_validate_snapshot "$snapshot" "$team")"', "validation=ok", "corruption does not become an empty successful array"),
    "early_stdout": ('  _api_registrations_emit "$team" ok \'\' true "$records_json"', '  printf \'partial\\n\'\n  _api_registrations_emit "$team" ok \'\' true "$records_json"', "one tuple is returned in a complete envelope"),
    "schema_bypass": ('storage_schema_unsupported)\n      _api_registrations_fail "$team" unknown storage_schema_unsupported 1', 'ok)\n      _api_registrations_fail "$team" unknown storage_schema_unsupported 1', "unsupported storage schema fails closed"),
    "target_scope": ('team_dir="$SCRIPT_DIR/../teams/$team"', 'team_dir="$SCRIPT_DIR/../teams/broken"', "corrupting another team does not affect the target"),
    "final_snapshot_bypass": ('if [ "$before_fp" != "$final_fp" ] || ! cmp -s "$snapshot" "$config"; then', "if false; then", "source exchange is reported instead of returning mixed data"),
    "tuple_pair": ("'project', json_extract(r.value, '\\$.project')", "'project', '/tmp/project-z'", "multiple projects and types preserve pairs without a cartesian product"),
}

READ_ONLY_MUTATIONS = {
    "session_manifest": (
        '  _api_registrations_emit "$team" ok \'\' true "$records_json"',
        '  printf "mutated\\n" >"$AGMSG_TEST_API_REGISTRATIONS_SESSION_RECORD"\n'
        '  _api_registrations_emit "$team" ok \'\' true "$records_json"',
        "preserves session manifest",
    ),
    "process_boundary": (
        '  _api_registrations_emit "$team" ok \'\' true "$records_json"',
        '  python3 -c \'import os, subprocess; sink=open(os.devnull, "wb"); subprocess.Popen(["bash", "-c", "exec -a \\\"$0\\\" sleep 30", os.environ["AGMSG_TEST_API_REGISTRATIONS_PROCESS_TOKEN"]], stdin=sink, stdout=sink, stderr=sink, start_new_session=True)\'\n'
        '  _api_registrations_emit "$team" ok \'\' true "$records_json"',
        "preserves session manifest",
    ),
}

def export_tree(dest):
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", HARNESS.CUTOFF], check=True, capture_output=True).stdout
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(dest, filter="data")

def run_case(name, before, after, test):
    with tempfile.TemporaryDirectory(prefix=f"issue246-{name}-") as temp:
        tree = Path(temp) / "tree"
        export_tree(tree)
        subprocess.run(["git", "apply", str(ROOT / HARNESS.PATCH)], cwd=tree, check=True, capture_output=True)
        source = tree / "scripts/lib/api-registrations.sh"
        text = source.read_text()
        if text.count(before) != 1:
            raise RuntimeError(f"{name}: anchor count {text.count(before)}")
        source.write_text(text.replace(before, after))
        syntax = subprocess.run(["bash", "-n", str(source)], capture_output=True, text=True)
        test_run = subprocess.run(["bats", "tests/test_api_registrations.bats", "-f", test], cwd=tree, capture_output=True, text=True)
        return {"syntax_rc": syntax.returncode, "test_rc": test_run.returncode, "killed": syntax.returncode == 0 and test_run.returncode != 0}

results = {name: run_case(name, *spec) for name, spec in MUTATIONS.items()}
read_only_results = {name: run_case(name, *spec) for name, spec in READ_ONLY_MUTATIONS.items()}
report = {"provider": results, "readOnly": read_only_results}
print(json.dumps(report, indent=2))
raise SystemExit(0 if len(results) == 8 and len(read_only_results) == 2 and all(row["killed"] for row in results.values()) and all(row["killed"] for row in read_only_results.values()) else 1)
