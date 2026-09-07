#!/usr/bin/env bats
# The Issue #254 ledger's coverage check, run here because 734 rows cannot be
# confirmed by reading. Named test_*.bats so the shard partition picks it up:
# a check outside the discovered set never runs at all (#293).
load test_helper

LEDGER_TOOL="$BATS_TEST_DIRNAME/../scripts/internal/hunk-ledger.py"
LEDGER_FILE="$BATS_TEST_DIRNAME/../docs/migration/222-hunk-ledger.tsv"

# The contract from the design note. Pinned here on purpose: this runs in a
# different process from whatever wrote the ledger header, which is the only
# arrangement in which the comparison means anything. Once the ledger exists,
# `--check` compares the header against a fresh count too, so a drift between
# these three shows up rather than hiding.
EXPECTED_HUNKS=734
EXPECTED_FILES=267
EXPECTED_DIGEST=258a1e93df7db7fe06334c5d8bb70517e96221b0ea5be9b63bd09b93a42589f1
QUOTED_PATH_HUNK=a75398923c524cca

setup() {
  command -v python3 >/dev/null || skip "python3 unavailable"
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  if ! git -C "$REPO" cat-file -e 3d06318 2>/dev/null; then
    # A skip here would be indistinguishable from a pass, and the whole check
    # would quietly stop running the moment someone trimmed the checkout depth.
    # Locally that is a fair skip; in CI it is the failure worth reporting.
    if [ -n "${CI:-}" ]; then
      echo "base commits absent: the checkout needs fetch-depth 0" >&2
      return 1
    fi
    skip "base commits absent (shallow clone)"
  fi
}

emit_header() { python3 "$LEDGER_TOOL" --repo "$REPO" --emit-header; }
emit_rows() { python3 "$LEDGER_TOOL" --repo "$REPO" --emit-rows; }

@test "the fixed bases still count the hunks the design note contracted for" {
  run emit_header
  [ "$status" -eq 0 ]
  grep -Fq -- "expected-hunks: $EXPECTED_HUNKS" <<<"$output"
  grep -Fq -- "expected-files: $EXPECTED_FILES" <<<"$output"
  grep -Fq -- "expected-digest: $EXPECTED_DIGEST" <<<"$output"
}

@test "the digest matches the shell one-liner the design note publishes" {
  # Two implementations of the same byte-level spec, compared with each other
  # before either is compared with the constant. That ordering does not widen
  # what is caught -- measured: if both implementations change together and the
  # constant is updated to match, this still passes. What it buys is which
  # assertion fails: a disagreement between the two is reported as a
  # disagreement, rather than as two separate quarrels with the constant.
  #
  # Nothing here can catch a coordinated change to all three. That is what the
  # design note asks a person to do instead -- recompute from its own text,
  # without reading either implementation.
  local shell_digest python_digest
  shell_digest="$(emit_rows | cut -f1 | LC_ALL=C sort | shasum -a 256 | cut -d' ' -f1)"
  python_digest="$(emit_header | sed -n 's/^# expected-digest: //p')"
  [ -n "$shell_digest" ]
  [ "$shell_digest" = "$python_digest" ]
  [ "$shell_digest" = "$EXPECTED_DIGEST" ]
}

@test "a non-ASCII path parses and keeps its known id" {
  # core.quotePath=true would rewrite this path and change its id, so the
  # canonical command pins the setting; this is the row that would notice.
  run emit_rows
  [ "$status" -eq 0 ]
  grep -Fq -- "$QUOTED_PATH_HUNK	docs/decisions/2026-08-17T060000_codex" <<<"$output"
}

@test "rule 4.5 refuses a hunk whose body disagrees with its own header" {
  # The rule exists because two independent implementations agreed on a wrong
  # answer: both followed a spec that collected one line too many. Body length
  # against the declared length needs no second implementation to check.
  local bad="$BATS_TEST_TMPDIR/bad.diff"
  printf 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1,2 @@\n+one\n' > "$bad"
  run python3 - "$LEDGER_TOOL" "$bad" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('hl', sys.argv[1])
hl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hl)
try:
    hl.enumerate_hunks(open(sys.argv[2]).read())
except hl.LedgerError as error:
    print(error)
    sys.exit(3)
sys.exit(0)
PY
  [ "$status" -eq 3 ]
  grep -Fq -- "rule 4.5" <<<"$output"
  grep -Fq -- "declares 2 lines, body has 1" <<<"$output"
}

@test "rule 1 refuses a diff --git line it cannot take a path from" {
  local bad="$BATS_TEST_TMPDIR/nopath.diff"
  printf 'diff --git "a/x" "b/x"\n@@ -0,0 +1 @@\n+one\n' > "$bad"
  run python3 - "$LEDGER_TOOL" "$bad" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('hl', sys.argv[1])
hl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hl)
try:
    hl.enumerate_hunks(open(sys.argv[2]).read())
except hl.LedgerError as error:
    print(error)
    sys.exit(3)
sys.exit(0)
PY
  [ "$status" -eq 3 ]
  grep -Fq -- "rule 1" <<<"$output"
}

@test "rules the current bases never exercise are reported when they appear" {
  # `\ No newline at end of file` and renames appear zero times at these bases,
  # so the spec for them has never been run. Rather than trusting a note to be
  # read later, their first appearance stops the check.
  local sample="$BATS_TEST_TMPDIR/unexercised.diff"
  printf 'diff --git a/x b/x\nrename from y\n@@ -0,0 +1 @@\n+one\n\\ No newline at end of file\n' > "$sample"
  run python3 - "$LEDGER_TOOL" "$sample" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('hl', sys.argv[1])
hl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hl)
_, unexercised = hl.enumerate_hunks(open(sys.argv[2]).read())
print(len(unexercised))
PY
  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
}

@test "an unpinned git setting moves the count rather than passing quietly" {
  # The design note admits it cannot enumerate every setting that changes a
  # partition, and argues the residue is absorbed: whatever the unknown key is,
  # it moves the count or the digest, and the comparison fails. These two keys
  # are the evidence for that argument, not a list of keys that are handled.
  #
  # GIT_CONFIG_COUNT is used rather than `git config` so nothing is written to a
  # repository other tests are reading at the same time.
  run env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.bigFileThreshold GIT_CONFIG_VALUE_0=1k \
    python3 "$LEDGER_TOOL" --repo "$REPO" --emit-header
  [ "$status" -eq 0 ]
  refute grep -Fq -- "expected-hunks: $EXPECTED_HUNKS" <<<"$output"
  refute grep -Fq -- "expected-digest: $EXPECTED_DIGEST" <<<"$output"
}

@test "an attributes file that marks a tracked file binary moves the count too" {
  local attrs="$BATS_TEST_TMPDIR/attributes"
  printf 'uninstall.sh binary\n' > "$attrs"
  run env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.attributesFile GIT_CONFIG_VALUE_0="$attrs" \
    python3 "$LEDGER_TOOL" --repo "$REPO" --emit-header
  [ "$status" -eq 0 ]
  refute grep -Fq -- "expected-hunks: $EXPECTED_HUNKS" <<<"$output"
  refute grep -Fq -- "expected-digest: $EXPECTED_DIGEST" <<<"$output"
}

@test "a setting that breaks path parsing stops instead of guessing" {
  # diff.noprefix removes the `a/`/`b/` prefixes the path rule reads. Rule 1
  # refuses rather than carrying the previous path forward or using an empty
  # one -- both of which two implementations actually did, producing two
  # different digests from the same spec.
  run env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.noprefix GIT_CONFIG_VALUE_0=true \
    python3 "$LEDGER_TOOL" --repo "$REPO" --emit-header
  [ "$status" -eq 1 ]
  grep -Fq -- "rule 1" <<<"$output"
}

# A complete, classified ledger built from the current bases. The real one is a
# separate task (#254); these tests need something to mutate, and building it
# here keeps them honest about what `--check` does before that lands.
build_ledger() {
  local out="$1"
  emit_header > "$out"
  emit_rows | awk -F'\t' 'BEGIN{OFS="\t"} {print $1,$2,"pool","port","placeholder","none","needed","no","worker","classified"}' >> "$out"
}

@test "a complete ledger passes, and each way of breaking it is named once" {
  local ledger="$BATS_TEST_TMPDIR/ledger.tsv" broken="$BATS_TEST_TMPDIR/broken.tsv"
  build_ledger "$ledger"

  # Positive control first. A check that reported these mutations while also
  # rejecting the intact ledger would be reporting nothing (the design note
  # records exactly that happening to an earlier draft).
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$ledger"
  [ "$status" -eq 0 ]

  local first
  first="$(grep -v '^#' "$ledger" | head -1 | cut -f1)"

  grep -v "^$first	" "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'missing': 1}" <<<"$output"
  grep -Fq -- "missing: $first" <<<"$output"

  cp "$ledger" "$broken"
  printf 'deadbeefdeadbeef\tno/such/file\tpool\tport\tp\tnone\tneeded\tno\tworker\tclassified\n' >> "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'stale': 1}" <<<"$output"

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$3=""} {print}' "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'unclassified': 1}" <<<"$output"

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$2="wrong/path"} {print}' "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'path-mismatch': 1}" <<<"$output"

  cp "$ledger" "$broken"
  grep "^$first	" "$ledger" >> "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'duplicate': 1}" <<<"$output"
}

@test "a freshly generated ledger is unclassified, not malformed" {
  # The two are different states in the design, and only one of them is a
  # defect. `--emit-rows` used to write two columns, so the ledger a person had
  # just generated came back as 734 malformed rows -- which reads as "the file
  # is broken" rather than "nobody has classified these yet".
  local fresh="$BATS_TEST_TMPDIR/fresh.tsv"
  emit_header > "$fresh"
  emit_rows >> "$fresh"
  local columns
  columns="$(grep -v '^#' "$fresh" | head -1 | awk -F'\t' '{print NF}')"
  [ "$columns" -eq 10 ]

  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$fresh"
  [ "$status" -eq 1 ]
  grep -Fq -- "'unclassified': 734" <<<"$output"
  refute grep -Fq -- "malformed" <<<"$output"
}

@test "a row with the wrong number of columns is still malformed" {
  # The negative control for the test above: `malformed` has to stay reachable,
  # or that test would pass just as well against a check that never reports it.
  local broken="$BATS_TEST_TMPDIR/short.tsv"
  emit_header > "$broken"
  emit_rows | head -1 | cut -f1,2 >> "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "malformed" <<<"$output"
}

@test "a status outside the three the design defines is reported" {
  local broken="$BATS_TEST_TMPDIR/status.tsv"
  build_ledger "$broken"
  local first
  first="$(grep -v '^#' "$broken" | head -1 | cut -f1)"
  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$10="probably-fine"} {print}' "$broken" \
    > "$broken.tmp" && mv "$broken.tmp" "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'invalid-status': 1}" <<<"$output"
}

@test "the execution gate asks for verified, and the check deliberately does not" {
  # Splitting these is the point. `unclassified` is where the design starts
  # every row, so a check that demanded `verified` would report the planned
  # state as a failure for the whole classification period. The gate is a
  # separate question, asked once, by a person.
  local ledger="$BATS_TEST_TMPDIR/gate.tsv"
  build_ledger "$ledger"

  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$ledger"
  [ "$status" -eq 0 ]

  run python3 "$LEDGER_TOOL" --repo "$REPO" --gate "$ledger"
  [ "$status" -eq 1 ]
  grep -Fq -- "'not-verified': 734" <<<"$output"

  sed 's/\tclassified$/\tverified/' "$ledger" > "$ledger.v"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --gate "$ledger.v"
  [ "$status" -eq 0 ]
}

@test "a header that disagrees with a fresh count is rejected" {
  # The reason the header is emitted rather than typed: a hand-edited header
  # records what someone believed, and only a separate count disagrees with it.
  local ledger="$BATS_TEST_TMPDIR/hdr.tsv" broken="$BATS_TEST_TMPDIR/hdr-broken.tsv"
  build_ledger "$ledger"
  sed 's/^# expected-hunks: .*/# expected-hunks: 484/' "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "{'header-mismatch': 1}" <<<"$output"
  grep -Fq -- "expected-hunks" <<<"$output"
}

@test "the ledger, once present, covers every hunk and classifies each one" {
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet (worker task, Issue #254)"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$LEDGER_FILE"
  [ "$status" -eq 0 ]
}
