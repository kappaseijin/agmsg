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

@test "used_names follows known wrappers to the real command only" {
  run python3 - "$LEDGER_TOOL" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location('hl', sys.argv[1])
hl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hl)

cases = {
    'run agmsg_runtime_lock_release_owned "$lock"':
        {'agmsg_runtime_lock_release_owned', 'lock'},
    'command target': {'target'},
    'nohup target': {'target'},
    'exec target': {'target'},
    'timeout 5 target': {'target'},
    'env A=B target': {'target'},
    'run env A=B timeout 5 command target': {'target'},
    'command -v target': set(),
    'timeout 5': set(),
    'env A=B': set(),
    '# run agmsg_runtime_lock_release_owned': set(),
    'echo "agmsg_runtime_lock_release_owned"': {'echo'},
    'echo agmsg_runtime_lock_release_owned': {'echo'},
    'if run target; then ( command other ); fi': {'target', 'other'},
}
for body, expected in cases.items():
    actual = hl.used_names(body)
    assert actual == expected, (body, actual, expected)
PY
  [ "$status" -eq 0 ]
}

@test "wrapper transparency and timeout duration mutations are killed" {
  run python3 - "$LEDGER_TOOL" "$BATS_TEST_TMPDIR" <<'PY'
import importlib.util
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding='utf8')
cases = (
    ('if token in WRAPPERS:', 'if False:', 'run target', {'target'}),
    ('if index < len(tokens):  # duration, never a command name\n                index += 1',
     'if False:\n                index += 1', 'timeout duration target', {'target'}),
)
for number, (before, after, body, expected) in enumerate(cases):
    assert before in source
    path = pathlib.Path(sys.argv[2]) / f'mutant-{number}.py'
    path.write_text(source.replace(before, after, 1), encoding='utf8')
    spec = importlib.util.spec_from_file_location(f'mutant_{number}', path)
    mutant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutant)
    assert mutant.used_names(body) == expected, (body, mutant.used_names(body))
PY
  [ "$status" -ne 0 ]
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
  # agguild/port because that is what the mechanical rule decides for the rows
  # it reaches, so a fixture using anything else trips rule-mismatch before the
  # test gets to whatever it was actually about.
  emit_rows | awk -F'\t' 'BEGIN{OFS="\t"} {print $1,$2,"agguild","port","fixture rationale","none","needed","no","worker","classified"}' >> "$out"
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

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$3="offical"} {print}' "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  # Not the whole dict: a wrong value can be wrong in more than one way at once
  # (an owner outside the four also contradicts the disposition beside it), and
  # this test is about the one judgment it is named after.
  grep -Fq -- "'invalid-owner': 1" <<<"$output"

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$4="portt"} {print}' "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "'invalid-disposition': 1" <<<"$output"

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

@test "a freshly generated ledger passes the check and only fails the gate" {
  # An empty column means nobody has classified that row yet. That is the state
  # the design starts every row in, so reporting it would keep CI red for the
  # whole classification period -- the exact thing splitting check from gate was
  # for. A wrong value in that column is a different thing, and the check keeps
  # it (see the invalid-owner/invalid-disposition rows next door).
  local fresh="$BATS_TEST_TMPDIR/fresh.tsv"
  emit_header > "$fresh"
  emit_rows >> "$fresh"
  local columns
  columns="$(grep -v '^#' "$fresh" | head -1 | awk -F'\t' '{print NF}')"
  [ "$columns" -eq 10 ]

  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$fresh"
  [ "$status" -eq 0 ]

  run python3 "$LEDGER_TOOL" --repo "$REPO" --gate "$fresh"
  [ "$status" -eq 1 ]
  # The judgment key, not the bare word: every not-verified row carries
  # `status=unclassified` in its detail line, so matching the word alone passes
  # even when the empty-column check has been removed entirely (measured).
  grep -Fq -- "'unclassified':" <<<"$output"
  grep -Fq -- "'not-verified':" <<<"$output"
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

# A ledger with the mechanical rules applied, and nothing else. This is the
# state the classification actually starts from, so it is what the rules below
# are measured against.
mechanical_ledger() {
  local out="$1" raw="$BATS_TEST_TMPDIR/raw-$RANDOM.tsv"
  emit_header > "$raw"
  emit_rows >> "$raw"
  python3 "$LEDGER_TOOL" --classify-mechanical "$raw" > "$out" 2>/dev/null
}

@test "the fork's own records are classified by rule, and nothing else is yet" {
  # docs/decisions and friends are the fork writing about itself, which is not
  # something upstream has an opinion about. Everything else -- including the
  # tests, whose owner depends on an implementation nobody has classified yet --
  # is left alone rather than guessed at.
  local ledger="$BATS_TEST_TMPDIR/mech.tsv"
  mechanical_ledger "$ledger"

  local classified
  classified="$(grep -v '^#' "$ledger" | awk -F'\t' '$3 != "" {print $2}' | sort -u)"
  [ -n "$classified" ]
  refute grep -qv '^docs/' <<<"$classified"

  local owners
  owners="$(grep -v '^#' "$ledger" | awk -F'\t' '$3 != "" {print $3"/"$4"/"$10}' | sort -u)"
  [ "$owners" = "agguild/port/classified" ]
}

@test "a rule firing is not a person checking" {
  # `classified`, not `verified`. The execution gate asks whether somebody
  # confirmed each row; a rule having matched is a different claim, and folding
  # the two would open the gate on nobody's say-so.
  local ledger="$BATS_TEST_TMPDIR/status.tsv"
  mechanical_ledger "$ledger"
  refute grep -q "	verified$" "$ledger"

  run python3 "$LEDGER_TOOL" --repo "$REPO" --gate "$ledger"
  [ "$status" -eq 1 ]
  grep -Fq -- "'not-verified':" <<<"$output"
}

@test "a test follows its implementation once that implementation is judged" {
  # The rule cannot fire on a fresh ledger: which owner a test inherits depends
  # on a judgement nobody has made yet. It fills in behind the judged rows, so
  # it is re-run rather than run once.
  local ledger="$BATS_TEST_TMPDIR/inherit.tsv" judged="$BATS_TEST_TMPDIR/judged.tsv"
  mechanical_ledger "$ledger"
  refute grep -q "^[^#]*	tests/test_watch.bats	official" "$ledger"

  # Both files the name points at, not just the obvious one: an unjudged
  # candidate is an unknown, and the rule waits for it.
  awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print;next}
    $2=="scripts/watch.sh" || $2=="scripts/drivers/types/codex/watch-once.sh" {
      $3="official";$4="adopt";$5="upstream behaviour fix";$10="classified"}
    {print}' "$ledger" > "$judged"
  run python3 "$LEDGER_TOOL" --classify-mechanical "$judged"
  [ "$status" -eq 0 ]
  grep -Fq -- "	tests/test_watch.bats	official	adopt	test follows the implementation" <<<"$output"
}

# A ledger of exactly the rows a test needs, written by hand. The real one is
# 734 rows and its contents move as classification proceeds; these cases are
# about the inheritance rule, not about any particular file's owner.
tiny_ledger() {  # $1=out $2..=owner for each actas-lock hunk, in order
  local out="$1"; shift
  printf '# tiny fixture\n' > "$out"
  local i=0 owner
  for owner in "$@"; do
    i=$((i + 1))
    local disposition=port
    [ "$owner" = official ] && disposition=adopt
    printf 'aaaaaaaaaaaaaa%02d\tscripts/lib/actas-lock.sh\t%s\t%s\tjudged for this fixture\tnone\tneeded\tno\tarchitect\tclassified\n' \
      "$i" "$owner" "$disposition" >> "$out"
  done
  printf 'bbbbbbbbbbbbbb01\ttests/test_actas_lock.bats\t\t\t\t\t\t\t\tunclassified\n' >> "$out"
}

@test "a test whose implementations disagree, or are half judged, waits" {
  # Prefix matching points test_api.bats at three files at once. When those
  # disagree there is no tie to break. And a file only partly judged is not a
  # majority to follow: the hunks nobody has looked at can still overturn it.
  local ledger="$BATS_TEST_TMPDIR/split.tsv" judged="$BATS_TEST_TMPDIR/split-judged.tsv"
  mechanical_ledger "$ledger"
  awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print;next}
    $2=="scripts/api.sh"{$3="official";$4="adopt";$5="upstream behaviour fix";$10="classified"}
    $2=="scripts/lib/api-registrations.sh"{$3="agguild";$4="port";$5="fork-only concept";$10="classified"}
    {print}' "$ledger" > "$judged"
  run python3 "$LEDGER_TOOL" --classify-mechanical "$judged"
  [ "$status" -eq 0 ]

  # Disagreeing candidates.
  refute grep -Eq "	tests/test_api\.bats	(official|agguild)" <<<"$output"
  # One candidate judged, the other untouched: still not decided.
  refute grep -Eq "	tests/test_api_actas_owner\.bats	(official|agguild)" <<<"$output"
  # And the reason is readable. Three different situations leave the same blank
  # row -- one file internally split, two files disagreeing, hunks not yet
  # judged -- so the line names the candidates and what each of them carries.
  grep -Fq -- "not inherited: tests/test_api.bats <- scripts/api.sh carries official" <<<"$output"
  grep -Fq -- "scripts/lib/api-actas-owner.sh carries (unclassified)" <<<"$output"
}

@test "a partly judged implementation is not a majority to follow" {
  # Seven agguild hunks and three nobody has looked at do not make an agguild
  # file. The three are unknown, not agreeing, and deciding on the seven means
  # the answer can be overturned when they are judged. The set of owners used
  # to have blanks intersected out of it, which turned "unknown" into "absent".
  local ledger="$BATS_TEST_TMPDIR/partial.tsv"
  tiny_ledger "$ledger" agguild agguild ''

  run python3 "$LEDGER_TOOL" --classify-mechanical "$ledger"
  [ "$status" -eq 0 ]
  refute grep -Eq "	tests/test_actas_lock\.bats	(official|agguild)" <<<"$output"
  grep -Fq -- "carries (unclassified)/agguild" <<<"$output"
}

@test "reordering the rows does not change what is classified" {
  # The property, not the instance. The first version of the inheritance rule
  # read one owner per path and kept whichever hunk sorted last, so the same
  # facts in a different order produced a different answer -- the same shape as
  # the batch ordering that had to be corrected earlier. Pinning one wrong
  # inheritance would leave the shape free to come back somewhere else.
  # Every permutation of three rows, so "official last" and "agguild last" are
  # both covered rather than hoped for.
  local ledger="$BATS_TEST_TMPDIR/order.tsv"
  tiny_ledger "$ledger" agguild agguild official

  run python3 "$BATS_TEST_DIRNAME/ledger_order_property.py" "$LEDGER_TOOL" "$ledger"
  [ "$status" -eq 0 ]
  [ "$output" = "1 -" ]

  # And the same over an implementation that does agree. Without this, an
  # implementation that inherits nothing at all would satisfy the invariance
  # above: unchanging is not the same as right.
  local uniform="$BATS_TEST_TMPDIR/order-uniform.tsv"
  tiny_ledger "$uniform" agguild agguild agguild
  run python3 "$BATS_TEST_DIRNAME/ledger_order_property.py" "$LEDGER_TOOL" "$uniform"
  [ "$status" -eq 0 ]
  [ "$output" = "1 agguild" ]
}

@test "a name that is not a test name points at no implementation" {
  # The stem used to be taken by slicing five characters off unconditionally,
  # so `broker.js` became `r` and prefix-matched README.md and ten other files.
  #
  # Measured: no ledger row was ever affected, because both callers ask
  # is_test_path first and broker.js never reaches the function. The fix is not
  # a repair, it is moving the condition to where the assumption lives -- a
  # function that is only correct when its callers remember something is one
  # refactor away from being wrong.
  run python3 "$BATS_TEST_DIRNAME/ledger_correspondence.py" --probe "$LEDGER_TOOL"
  [ "$status" -eq 0 ]
  grep -Fq -- "tests/fixtures/pm-broker/broker.js 0" <<<"$output"
  grep -Fq -- "tests/fixtures/team-work-audit/closed.json 0" <<<"$output"
  # The positive control: a real test name still resolves, so this is the
  # prefix check working rather than the matching having been switched off.
  grep -Fq -- "tests/test_watch.bats 1" <<<"$output"
}

@test "the correspondence the design counted is the one the code finds" {
  # 44 files / 211 hunks. Pinned because the number moves with the reading:
  # searching only scripts/ gives 42, and skipping the -/_ normalisation gives
  # 28. A change to any of those is a change to which tests are mechanical.
  local ledger="$BATS_TEST_TMPDIR/count.tsv"
  mechanical_ledger "$ledger"
  run python3 "$BATS_TEST_DIRNAME/ledger_correspondence.py" "$LEDGER_TOOL" "$ledger"
  [ "$status" -eq 0 ]
  [ "$output" = "44 211" ]
}

@test "contradictions between two written values are reported by the check" {
  # Every one of these is a value somebody wrote disagreeing with another value
  # somebody wrote, which is data rather than progress -- so the check owns them
  # and the gate does not.
  local ledger="$BATS_TEST_TMPDIR/contra.tsv" broken="$BATS_TEST_TMPDIR/contra-broken.tsv"
  build_ledger "$ledger"
  local first
  first="$(grep -v '^#' "$ledger" | head -1 | cut -f1)"

  bad_row() {  # $1=column index, $2=value, $3=expected judgment
    awk -F'\t' -v id="$first" -v c="$1" -v v="$2" 'BEGIN{OFS="\t"} $1==id{$c=v} {print}' \
      "$ledger" > "$broken"
    run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
    [ "$status" -eq 1 ]
    grep -Fq -- "'$3': 1" <<<"$output"
  }

  # owner=pool, which the design expects zero of: personas live elsewhere.
  bad_row 3 pool unexpected-pool
  # rationale present but saying nothing.
  bad_row 5 port rationale-too-short

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$3="official";$4="port"} {print}' \
    "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "'owner-disposition-mismatch': 1" <<<"$output"

  awk -F'\t' -v id="$first" 'BEGIN{OFS="\t"} $1==id{$3="exception";$4="adopt"} {print}' \
    "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "'owner-disposition-mismatch': 1" <<<"$output"
}

@test "a row the rules cover cannot be given a different owner by hand" {
  # The rule is recomputed and compared, the way the header digest is. Writing
  # a value the rule contradicts is caught; writing one for a row no rule
  # reaches is a judgement and is left alone.
  local ledger="$BATS_TEST_TMPDIR/rule.tsv" broken="$BATS_TEST_TMPDIR/rule-broken.tsv"
  mechanical_ledger "$ledger"

  local doc_row
  doc_row="$(grep -v '^#' "$ledger" | awk -F'\t' '$2 ~ /^docs\/decisions\// {print $1; exit}')"
  [ -n "$doc_row" ]
  awk -F'\t' -v id="$doc_row" 'BEGIN{OFS="\t"} $1==id{$3="official";$4="adopt"} {print}' \
    "$ledger" > "$broken"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$broken"
  [ "$status" -eq 1 ]
  grep -Fq -- "'rule-mismatch': 1" <<<"$output"
}

@test "every judgment the code reports is named in the design note" {
  # Suggested after the design and the implementation drifted apart once
  # already. The names are how a CI failure is traced back to a decision, so a
  # judgment nobody documented is one nobody can look up.
  local pairs="$BATS_TEST_TMPDIR/judgments.txt"
  python3 "$BATS_TEST_DIRNAME/ledger_judgments.py" \
    "$BATS_TEST_DIRNAME/../scripts/internal/hunk-ledger.py" \
    "$BATS_TEST_DIRNAME/../docs/decisions/*issue-254*.md" > "$pairs"

  # Both sides non-empty first. The docs side is read by matching the table's
  # shape, so a reformatted table returns nothing and the comparison below would
  # otherwise report perfect agreement with nothing at all.
  [ "$(grep -c '^impl ' "$pairs")" -gt 0 ]
  [ "$(grep -c '^docs ' "$pairs")" -gt 0 ]

  local difference
  difference="$(comm -3 <(grep '^impl ' "$pairs" | cut -d' ' -f2 | sort) \
                        <(grep '^docs ' "$pairs" | cut -d' ' -f2 | sort))"
  [ -z "$difference" ]
}

@test "an official hunk that needs an agguild name is reported (#339)" {
  # The official hunks have to be applicable upstream as a set. One of them
  # needing a name that exists only because of an agguild hunk breaks that, and
  # the design note found the cases by hand until now.
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet"
  local run_case="python3 $BATS_TEST_DIRNAME/ledger_dependency.py $LEDGER_TOOL $LEDGER_FILE"

  # The ledger as it stands.
  run $run_case as-is
  [ "$status" -eq 0 ]
  [ "$output" = "0 -" ]

  # Mark the definer of a name an official hunk calls as agguild, and it is
  # named. Without this the "0" above would be satisfied by a check that reports
  # nothing at all.
  run $run_case flipped
  [ "$status" -eq 0 ]
  [ "$output" = "1 agmsg_validate_utf8" ]
}

@test "a stub for an external command is not a dependency, unless it is product code (#339)" {
  # Test fixtures replace mktemp and rm to control what a teardown does. A hunk
  # calling those is calling the command. The exclusion is deliberately narrow:
  # the same name defined outside tests/ is a real dependency, and the second
  # case here is what stops the list from hiding it.
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet"
  local run_case="python3 $BATS_TEST_DIRNAME/ledger_dependency.py $LEDGER_TOOL $LEDGER_FILE"

  run $run_case no-shadow-list
  [ "$status" -eq 0 ]
  [ "$output" = "10 mktemp,rm" ]

  run $run_case product-shadow
  [ "$status" -eq 0 ]
  [ "$output" = "4 mktemp" ]
}

@test "a name this file defines itself is not borrowed from another file (#339)" {
  # Two scripts each defining `usage` are not a dependency. A definition in the
  # same file wins over anything sourced, so the local one is what runs.
  #
  # The sourcing check is the other half of this and is currently carrying
  # nothing: with it disabled the count stays 0, and only disabling both brings
  # `usage` back. Recorded rather than removed -- it is the part that would
  # catch a cross-file name this file does NOT also define -- but it is
  # unexercised by the ledger as it stands.
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet"
  local run_case="python3 $BATS_TEST_DIRNAME/ledger_dependency.py $LEDGER_TOOL $LEDGER_FILE"

  run $run_case no-visibility
  [ "$status" -eq 0 ]
  [ "$output" = "0 -" ]

  run $run_case no-local-wins
  [ "$status" -eq 0 ]
  # Issue #347 makes the Bats `run` wrapper transparent, so this deliberately
  # also exposes the helper it wraps when same-file precedence is disabled.
  [ "$output" = "2 json_value,run_storage_fanout_with_packet" ]
}

@test "the ledger, once present, covers every hunk and classifies each one" {
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet (worker task, Issue #254)"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$LEDGER_FILE"
  [ "$status" -eq 0 ]
}
