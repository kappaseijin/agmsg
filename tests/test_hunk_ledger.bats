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

  awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print;next}
    $2=="scripts/watch.sh"{$3="official";$4="adopt";$5="upstream behaviour fix";$10="classified"}
    {print}' "$ledger" > "$judged"
  run python3 "$LEDGER_TOOL" --classify-mechanical "$judged"
  [ "$status" -eq 0 ]
  grep -Fq -- "	tests/test_watch.bats	official	adopt	test follows the implementation" <<<"$output"
}

@test "a test whose implementations disagree is left for a person" {
  # Prefix matching points test_api.bats at three files at once. When those
  # disagree there is no tie to break: the rule says a test follows its
  # implementation, and which one that is has stopped being mechanical.
  local ledger="$BATS_TEST_TMPDIR/split.tsv" judged="$BATS_TEST_TMPDIR/split-judged.tsv"
  mechanical_ledger "$ledger"
  awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print;next}
    $2=="scripts/api.sh"{$3="official";$4="adopt";$5="upstream behaviour fix";$10="classified"}
    $2=="scripts/lib/api-registrations.sh"{$3="agguild";$4="port";$5="fork-only concept";$10="classified"}
    {print}' "$ledger" > "$judged"
  run python3 "$LEDGER_TOOL" --classify-mechanical "$judged"
  [ "$status" -eq 0 ]
  # The disagreeing one stays empty. The positive control sits next to it:
  # test_api_actas_owner.bats also points at several files, but only one of them
  # has been judged, so there is nothing to disagree with and it inherits.
  refute grep -Eq "	tests/test_api\.bats	(official|agguild)" <<<"$output"
  grep -Eq -- "	tests/test_api_actas_owner\.bats	official" <<<"$output"
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

@test "a test does not inherit from an implementation whose hunks disagree" {
  # `owner` is decided per hunk, and a file's hunks routinely disagree --
  # scripts/lib/actas-lock.sh carries 19 agguild and 2 official in the real
  # ledger. Reading one owner per path keeps whichever hunk sorts last, so this
  # test inherited `official` from two hunks out of twenty-one.
  #
  # The check did not notice: the rule and the check read the same collapsed
  # value and agreed with each other. Comparing two things says whether they
  # match, never whether either is right.
  local ledger="$BATS_TEST_TMPDIR/disagree.tsv"
  tiny_ledger "$ledger" agguild agguild official

  run python3 "$LEDGER_TOOL" --classify-mechanical "$ledger"
  [ "$status" -eq 0 ]
  refute grep -Eq "	tests/test_actas_lock\.bats	(official|agguild)" <<<"$output"
  # Named, so that "did not inherit" can be told from "no rule reached it".
  grep -Fq -- "not inherited: scripts/lib/actas-lock.sh carries agguild/official" <<<"$output"
}

@test "a test inherits when every hunk of its implementation agrees" {
  # The positive control for the test above: disagreement has to be what stops
  # the inheritance, not the inheritance having stopped working.
  local ledger="$BATS_TEST_TMPDIR/agree.tsv"
  tiny_ledger "$ledger" agguild agguild agguild

  run python3 "$LEDGER_TOOL" --classify-mechanical "$ledger"
  [ "$status" -eq 0 ]
  grep -Fq -- "	tests/test_actas_lock.bats	agguild	port	test follows the implementation" <<<"$output"
  refute grep -Fq -- "not inherited" <<<"$output"
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

@test "the ledger, once present, covers every hunk and classifies each one" {
  [ -f "$LEDGER_FILE" ] || skip "ledger not generated yet (worker task, Issue #254)"
  run python3 "$LEDGER_TOOL" --repo "$REPO" --check "$LEDGER_FILE"
  [ "$status" -eq 0 ]
}
