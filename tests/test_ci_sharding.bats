#!/usr/bin/env bats
#
# The CI suite is split across parallel shards by .github/scripts/shard-tests.sh.
# If that split ever stops being a partition of tests/*.bats, CI keeps reporting
# green while quietly running less than it did before — the failure mode this
# file exists to make impossible.

setup() {
  load 'test_helper'
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  SHARD="$REPO_ROOT/.github/scripts/shard-tests.sh"
}

all_test_files() {
  find "$REPO_ROOT/tests" -maxdepth 1 -name '*.bats' -exec basename {} \; | LC_ALL=C sort
}

union_of_shards() {
  local total="$1" i
  for ((i = 1; i <= total; i++)); do
    (cd "$REPO_ROOT" && bash "$SHARD" "$i" "$total")
  done | sed 's|.*/||' | LC_ALL=C sort
}

@test "shard-tests.sh is executable and self-documents its usage" {
  [ -x "$SHARD" ]
  run bash "$SHARD"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "the shards cover every test file exactly once" {
  # Checked across several totals: an off-by-one in the greedy loop can easily
  # be invisible at one shard count and drop a file at another.
  local total
  for total in 1 2 3 4 5 8; do
    run union_of_shards "$total"
    [ "$status" -eq 0 ]
    [ "$output" = "$(all_test_files)" ] || {
      echo "shard total $total did not reproduce the suite" >&2
      diff <(echo "$output") <(all_test_files) >&2 || true
      return 1
    }
  done
}

@test "this test file is itself assigned to a shard" {
  # Guards the specific regression the partition property is meant to prevent:
  # a new test file that lands in no shard and therefore never runs in CI.
  run union_of_shards 4
  [ "$status" -eq 0 ]
  [[ "$output" == *"test_ci_sharding.bats"* ]]
}

@test "the split is deterministic across repeated runs" {
  local first second
  first="$(cd "$REPO_ROOT" && bash "$SHARD" 2 4)"
  second="$(cd "$REPO_ROOT" && bash "$SHARD" 2 4)"
  [ "$first" = "$second" ]
}

@test "no shard is empty at either shard count CI uses" {
  # ubuntu splits 6 ways and macOS 4 (#293); both are checked, because an empty
  # shard is a wasted runner that still reports green.
  local total i out
  for total in 4 6; do
    for i in $(seq 1 "$total"); do
      out="$(cd "$REPO_ROOT" && bash "$SHARD" "$i" "$total")"
      [ -n "$out" ]
    done
  done
}

@test "the heaviest file does not share a shard with the second heaviest" {
  # Not a correctness property — a balance smoke test. Greedy LPT should never
  # put the two costliest files together while lighter shards exist; if it does,
  # the weighting has broken and CI is slower than it looks.
  #
  # Ranked by the measured seconds the script now weights with (#293), not by
  # @test count: reading the count here would test a metric the partition no
  # longer uses, and would pass whatever the real weights did.
  local weights heaviest second
  weights="$REPO_ROOT/.github/data/bats-weights.tsv"
  heaviest="$(grep -v '^[[:space:]]*#' "$weights" | grep -v '^[[:space:]]*$' \
    | LC_ALL=C sort -t"$(printf '\t')" -k2,2nr | sed -n '1s/\t.*//p')"
  second="$(grep -v '^[[:space:]]*#' "$weights" | grep -v '^[[:space:]]*$' \
    | LC_ALL=C sort -t"$(printf '\t')" -k2,2nr | sed -n '2s/\t.*//p')"
  [ -n "$heaviest" ]
  [ -n "$second" ]
  [ "$heaviest" != "$second" ]
  local i shard_files
  for i in 1 2 3 4; do
    shard_files="$(cd "$REPO_ROOT" && bash "$SHARD" "$i" 4)"
    if [[ "$shard_files" == *"$heaviest"* ]]; then
      [[ "$shard_files" != *"$second"* ]]
    fi
  done
}

@test "a file absent from the weights table is weighted, not treated as free" {
  # The table goes stale on purpose (no freshness check), so the default for an
  # unnamed file has to be an ordinary weight rather than zero — otherwise every
  # newly added file looks free and they all pile onto whichever shard is
  # lightest, however many of them there are.
  #
  # Three known files and three unknown ones over four shards: with a zero
  # default the three unknowns all land on the one empty shard, because adding
  # zero never makes it stop being the lightest.
  local dir table out
  dir="$(mktemp -d)"
  table="$dir/weights.tsv"
  printf 'a.bats\t100\nb.bats\t100\nc.bats\t100\n' > "$table"
  : > "$dir/a.bats"; : > "$dir/b.bats"; : > "$dir/c.bats"
  : > "$dir/zz_unlisted_one.bats"; : > "$dir/zz_unlisted_two.bats"; : > "$dir/zz_unlisted_three.bats"
  local i count
  for i in 1 2 3 4; do
    out="$(cd "$REPO_ROOT" && BATS_WEIGHTS_FILE="$table" bash "$SHARD" "$i" 4 "$dir")"
    count="$(printf '%s\n' "$out" | grep -c 'zz_unlisted' || true)"
    [ "$count" -le 2 ]
  done
  rm -rf "$dir"
}

@test "the partition is balanced by the weights the script claims to use" {
  # The point of the seconds table (#293) is balance, so measure it: sum each
  # shard's weights and require the spread to stay well under what an unweighted
  # split gives. Measured at the time of writing: 0% at both 4 and 6 shards,
  # against 42% when every file carries the same weight.
  local weights total i files f w sum max min spread
  weights="$REPO_ROOT/.github/data/bats-weights.tsv"
  for total in 4 6; do
    max=-1; min=-1
    for i in $(seq 1 "$total"); do
      files="$(cd "$REPO_ROOT" && bash "$SHARD" "$i" "$total")"
      sum=0
      while IFS= read -r f; do
        [ -n "$f" ] || continue
        w="$(awk -F'\t' -v n="$(basename "$f")" '$1==n {print $2; exit}' "$weights")"
        case "$w" in ''|*[!0-9]*) w=0 ;; esac
        sum=$((sum + w))
      done <<< "$files"
      [ "$max" -lt 0 ] && max="$sum" && min="$sum"
      [ "$sum" -gt "$max" ] && max="$sum"
      [ "$sum" -lt "$min" ] && min="$sum"
      true
    done
    [ "$max" -gt 0 ]
    spread=$(( (max - min) * 100 / max ))
    echo "shards=$total longest=${max}s spread=${spread}%" >&2
    [ "$spread" -le 25 ]
  done
}

@test "a missing weights table still partitions every file exactly once" {
  # Coverage must not depend on the table being present or readable.
  local total i all expected
  total=4
  all=""
  for i in $(seq 1 "$total"); do
    all="$all$(cd "$REPO_ROOT" && BATS_WEIGHTS_FILE=/nonexistent bash "$SHARD" "$i" "$total")
"
  done
  all="$(printf '%s' "$all" | grep -c .)"
  expected="$(cd "$REPO_ROOT" && find tests -maxdepth 1 -name '*.bats' | grep -c .)"
  [ "$all" -eq "$expected" ]
}

# The test above catches drift in the TOP TWO BY COUNT — exactly the metric
# that misses the files pinned below (#847, #848): both are near the bottom
# of the count-weighted sort (9 and 31 tests) despite carrying some of the
# largest measured durations in the suite (722s and 380s; see
# shard-tests.sh's own comment for the measurement). This test guards the
# actual fix, not the metric that already worked.
@test "the pinned-apart heavy files never share a shard, at any shard total >= 2 (#847, #848)" {
  # total=1 is deliberately not checked: with one shard both pins wrap into
  # slot 0 and land together by construction, same as every other file — the
  # pin has nothing to separate them FROM at total=1, so that is not a case
  # this property claims to hold.
  local pin1="test_remote_engine_start_refusal.bats"
  local pin2="test_remote_status_liveness.bats"
  local total i shard_files together
  for total in 2 3 4 5 8; do
    together=0
    for ((i = 1; i <= total; i++)); do
      shard_files="$(cd "$REPO_ROOT" && bash "$SHARD" "$i" "$total")"
      if grep -qF "$pin1" <<<"$shard_files" && grep -qF "$pin2" <<<"$shard_files"; then
        together=1
      fi
    done
    [ "$together" -eq 0 ]
  done
}

@test "adding tests to an unrelated file does not reunite the pinned-apart files (#847 positive control)" {
  # Reproduces #847's actual trigger, not a stand-in for it: that issue's
  # collision was caused by appending @test cases to ONE file
  # (test_roster_journal.bats) and having the repack land two OTHER, entirely
  # untouched files in the same shard. This does the same append and checks
  # the two files pinned above specifically, since a pin is exactly the part
  # of the fix that is supposed to make this kind of drift unable to reunite
  # them, whatever else in the tree changes shape.
  local pin1="test_remote_engine_start_refusal.bats"
  local pin2="test_remote_status_liveness.bats"
  local dir="$BATS_TEST_TMPDIR/tests-plus"
  mkdir -p "$dir"
  cp "$REPO_ROOT"/tests/*.bats "$dir"/
  local i
  for i in $(seq 1 20); do
    printf '\n@test "synthetic case %d" {\n  true\n}\n' "$i" >> "$dir/test_roster_journal.bats"
  done

  local shard1="" shard2="" shard_files
  for i in 1 2 3 4; do
    shard_files="$(cd "$REPO_ROOT" && bash "$SHARD" "$i" 4 "$dir")"
    grep -qF "$pin1" <<<"$shard_files" && shard1="$i"
    grep -qF "$pin2" <<<"$shard_files" && shard2="$i"
  done
  [ -n "$shard1" ]
  [ -n "$shard2" ]
  [ "$shard1" != "$shard2" ]
}

@test "shard-tests.sh rejects out-of-range and non-numeric arguments" {
  run bash "$SHARD" 0 4
  [ "$status" -eq 2 ]
  run bash "$SHARD" 5 4
  [ "$status" -eq 2 ]
  run bash "$SHARD" abc 4
  [ "$status" -eq 2 ]
  run bash "$SHARD" 1 0
  [ "$status" -eq 2 ]
}

@test "shard-tests.sh fails loudly on a directory with no test files" {
  local empty="$BATS_TEST_TMPDIR/empty"
  mkdir -p "$empty"
  run bash "$SHARD" 1 4 "$empty"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no .bats files"* ]]

  run bash "$SHARD" 1 4 "$BATS_TEST_TMPDIR/does-not-exist"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no such directory"* ]]
}

@test "the CI workflow shard matrix matches the per-OS totals it passes" {
  # The matrix is a literal list (so a broken `changes` job cannot take the
  # whole suite down with an unevaluatable dynamic matrix), which means the
  # list, the excludes and the per-OS totals have to be kept in step by hand.
  # CI verifies coverage end-to-end from the shard manifests as well; this
  # catches the drift here, where the fix is obvious.
  local wf="$REPO_ROOT/.github/workflows/tests.yml"
  local mac ubu matrix_entries excluded
  # SHARD_TOTAL is `matrix.os == 'macos-latest' && <mac> || <ubuntu>`.
  mac="$(sed -n "s/.*matrix.os == 'macos-latest' && \([0-9]*\) || \([0-9]*\).*/\1/p" "$wf")"
  ubu="$(sed -n "s/.*matrix.os == 'macos-latest' && \([0-9]*\) || \([0-9]*\).*/\2/p" "$wf")"
  [ -n "$mac" ]
  [ -n "$ubu" ]
  # The literal list must cover the larger of the two totals exactly.
  matrix_entries="$(sed -n 's/^ *shard: \[\(.*\)\]$/\1/p' "$wf" | tr ',' '\n' | grep -c '[0-9]')"
  if [ "$mac" -ge "$ubu" ]; then [ "$matrix_entries" -eq "$mac" ]; else [ "$matrix_entries" -eq "$ubu" ]; fi
  # ...and the smaller side must exclude every surplus shard, or it would start
  # a job whose index its own total does not cover.
  excluded="$(sed -n '/^ *exclude:/,/^ *steps:/p' "$wf" | grep -c '^ *- os: macos-latest$')"
  [ "$excluded" -eq "$((matrix_entries - mac))" ]
  # The display name must not claim a total, since the two OSes no longer share
  # one; a hard-coded number there would be wrong for at least one of them.
  grep -q "bats (\${{ matrix.os }} shard \${{ matrix.shard }})" "$wf"
  ! grep -q "matrix.shard }}/[0-9]" "$wf"
}
