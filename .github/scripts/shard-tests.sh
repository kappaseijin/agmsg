#!/usr/bin/env bash
#
# Deterministically partition the bats suite into shards, and print the test
# files belonging to one of them (one path per line).
#
#   .github/scripts/shard-tests.sh <index> <total> [tests-dir]
#
# The partition is computed from the tree itself, not from a list someone has
# to remember to update. That is the point: with a hand-maintained matrix, a
# newly added test file lands in no shard at all and simply never runs — a
# green CI that silently stopped testing something. Here every `*.bats` file in
# the directory is assigned to exactly one shard, so the union of all shards is
# always the whole suite (asserted by tests/test_ci_sharding.bats).
#
# Balancing is by measured per-file runtime in seconds, greedy longest-
# processing-time first, rather than by file count: the suite's files differ by
# more than an order of magnitude in cost, so splitting on names alone would
# leave one shard doing most of the work and cap the speedup at whatever that
# shard costs.
#
# The weights live in .github/data/bats-weights.tsv (see its header for how they
# were measured). They replace the @test count this script used through #293.
# Count was a loose proxy for runtime -- per-test cost across the suite runs
# ~0.0s to ~8s depending on how much a file forks or waits -- and the looseness
# grows with the shard count, because a finer partition has less room to average
# the error out. Projected from one green run (34037049929): at 10 shards the
# count weight leaves 68% spread between the longest and shortest shard, and the
# second weight leaves 0%.
#
# This script's own header used to reject exactly this change, on the grounds
# that a checked-in table "goes stale silently". The objection is real and is
# accepted rather than answered: nothing here checks the table's freshness. What
# makes it affordable is stated below -- a bad weight costs balance, never
# coverage -- and the count weight was already badly balanced, so staleness is a
# regression from nothing.
#
# The floor either way is the slowest single file (test_remote.bats, 272s on
# macOS) — no split beats that.
#
# Whatever the weights, the property that matters is coverage, not balance: the
# worst case of a bad weight is an unevenly filled shard, never a missing file.
#
# --- Pinned-apart files (#847, #848) ---------------------------------------
#
# @test count is a loose proxy for runtime in general (above), but for a
# specific shape of file it is not loose, it is blind: a file whose cost is
# almost entirely waiting (background processes, poll loops with
# hundred-plus-iteration bounds) rather than how many @test blocks it
# contains can carry a tiny weight here while dominating its shard's actual
# wall clock. A file that is merely large -- many @test blocks, ordinary
# per-test cost -- is NOT this case; count already weights it correctly, and
# it is not pinned.
#
# Measured 2026-08-19 on a green main run (head 626a625b, run 32193147987) by
# correlating each `ok N <desc>` line's own GitHub Actions timestamp against
# which file's `@test` block that description belongs to, then ranking every
# file by seconds-per-test rather than by raw duration (raw duration alone
# does not distinguish "slow because few tests wait a long time" from "slow
# because there are simply many tests", and only the former is what count
# weighting misses):
#
#   tests/test_remote_engine_start_refusal.bats    722s /  9 tests = ~80s/test
#   tests/test_remote_status_liveness.bats         380s / 31 tests = ~12s/test
#
# against a whole-suite per-test cost this script's own header already says
# runs ~0.0s-8s. Both are 1.5x-10x above that ceiling on a low test count, so
# both rank near the bottom of the count-weighted sort while carrying some of
# the largest absolute durations in the suite. (Files that are merely large in
# absolute terms -- e.g. a 179-test file at a very ordinary ~1.2s/test -- were
# checked and excluded: their weight already reflects their real cost.)
#
# #847's own trigger was exactly this class of file landing next to another
# heavy one purely because an unrelated 15-test addition elsewhere repacked
# the partition — the count weight cannot tell "heavy because slow" from
# "heavy because voluminous", so nothing stops two slow-but-few-tests files
# from drifting onto the same shard as the tree changes shape. Pinning these
# apart, in fixed shard slots decided before the ordinary weighted pass runs,
# means no future change to any OTHER file's test count can put two of them
# together again — that was the actual, demonstrated failure, not merely a
# theoretical one.
#
# This does not bound a shard's total duration: the heavier entry above
# (722s) is heavy enough on its own that no repacking of the rest of the
# suite moves its shard's floor by much. See tests.yml's bats-shard
# timeout-minutes for the ceiling this is paired with, sized to cover that
# floor plus a fair share of everything else with real margin. And a file NOT
# on this list can still turn out to be similarly disproportionate and land
# next to another one by chance — nothing here detects that case in general,
# only these two measured instances of it. Revisit alongside the counting
# scheme itself once the concurrent effort to shorten these files (tracked
# separately from #847/#848) lands and the numbers above are stale.
#
# FOLLOW-UP: whichever of that effort's PRs (#876 et al.) touches either file
# named below changes its real cost, possibly enough to make pinning it
# pointless or to make some other, currently-unremarkable file the next
# hidden outlier. Re-run this script's own measurement method (correlate a
# green run's `ok N` timestamps against each file, rank by seconds-per-test)
# on main once that work lands, and drop or replace entries here based on
# what it says then — this list is not meant to be permanent. The 30-minute
# job cap in tests.yml is a separate decision and does not depend on this one.
#
# Matched by basename, not by the `$dir`-relative path `files` below uses, so
# the pin still resolves when this script is invoked against a different
# tests-dir (as several of this file's own tests do). A pinned name that no
# longer exists in the tree (renamed, removed) is silently skipped rather
# than treated as an error: the partition's correctness (full coverage,
# asserted by tests/test_ci_sharding.bats) never depended on it.
set -euo pipefail

PINNED_APART="test_remote_engine_start_refusal.bats test_remote_status_liveness.bats"

usage() {
  echo "usage: ${0##*/} <shard-index> <shard-total> [tests-dir]" >&2
  echo "  shard-index is 1-based and must be <= shard-total" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
index="$1"
total="$2"
dir="${3:-tests}"

case "$index" in ''|*[!0-9]*) usage ;; esac
case "$total" in ''|*[!0-9]*) usage ;; esac
[ "$total" -ge 1 ] || usage
[ "$index" -ge 1 ] || usage
[ "$index" -le "$total" ] || usage
[ -d "$dir" ] || { echo "${0##*/}: no such directory: $dir" >&2; exit 1; }

# LC_ALL=C keeps the enumeration order identical across the GNU and BSD
# userlands the suite already runs on, so a given tree always produces the same
# partition regardless of which runner computes it.
files="$(find "$dir" -maxdepth 1 -name '*.bats' | LC_ALL=C sort)"
[ -n "$files" ] || { echo "${0##*/}: no .bats files under $dir" >&2; exit 1; }

# Measured seconds per file, keyed by basename so the lookup still resolves when
# this script is invoked against a different tests-dir (as several of its own
# tests do). Comment and blank lines are dropped here, once, so the per-file
# lookup below is a plain field match.
WEIGHTS_FILE="${BATS_WEIGHTS_FILE:-$(dirname "$0")/../data/bats-weights.tsv}"
weights=""
if [ -f "$WEIGHTS_FILE" ]; then
  weights="$(grep -v '^[[:space:]]*#' "$WEIGHTS_FILE" | grep -v '^[[:space:]]*$' || true)"
fi

# The default for a file the table does not name. Median, not zero: zero would
# let every newly added file look free and pile onto one shard. Median, not
# mean, because the distribution is long-tailed (a handful of files carry
# minutes while most carry seconds) and the mean would overstate a typical new
# file by ~3x. With an even number of entries this takes the upper of the two
# middle values, which keeps the choice deterministic without needing decimals.
default_weight=1
if [ -n "$weights" ]; then
  default_weight="$(printf '%s\n' "$weights" | cut -f2 | LC_ALL=C sort -n | \
    awk '{v[NR]=$1} END {if (NR) print v[int(NR/2)+1]; else print 1}')"
  [ -n "$default_weight" ] || default_weight=1
fi

file_weight() {
  local base n
  base="$(basename "$1")"
  n=""
  if [ -n "$weights" ]; then
    n="$(printf '%s\n' "$weights" | awk -F'\t' -v f="$base" '$1==f {print $2; exit}')"
  fi
  case "$n" in
    ''|*[!0-9]*) n="$default_weight" ;;
  esac
  printf '%s' "$n"
}

# Seed the pinned files into distinct shards first, in PINNED_APART's own
# (measured-heaviest-first) order — not the order they happen to sort in
# below, which is by count and is exactly the metric these files defeat. Each
# consumes one shard slot (wrapping if there are more pinned files than
# shards); everything else is decided by the ordinary weighted pass afterward,
# which never reconsiders a file placed here.
i=0
while [ "$i" -lt "$total" ]; do
  load[i]=0
  i=$((i + 1))
done

pinned_paths=" "
slot=0
for p in $PINNED_APART; do
  match=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ "$(basename "$f")" = "$p" ]; then
      match="$f"
      break
    fi
  done <<EOF
$files
EOF
  [ -n "$match" ] || continue
  s=$((slot % total))
  load[s]=$((load[s] + $(file_weight "$match")))
  if [ "$s" -eq "$((index - 1))" ]; then
    printf '%s\n' "$match"
  fi
  pinned_paths="${pinned_paths}${match} "
  slot=$((slot + 1))
done

# Weight every remaining (non-pinned) file the same way as before.
weighted=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$pinned_paths" in
    *" $f "*) continue ;;
  esac
  weighted="${weighted}$(file_weight "$f")	${f}
"
done <<EOF
$files
EOF

# Heaviest first; ties broken by path so the order is total, not incidental.
sorted="$(printf '%s' "$weighted" | LC_ALL=C sort -t'	' -k1,1nr -k2,2)"

# Greedy LPT: hand each remaining file to the currently lightest shard. `load`
# already carries the pinned seeds from above, not reset here.
while IFS='	' read -r n f; do
  [ -n "$f" ] || continue
  best=0
  best_load=${load[0]}
  j=1
  while [ "$j" -lt "$total" ]; do
    if [ "${load[j]}" -lt "$best_load" ]; then
      best=$j
      best_load=${load[j]}
    fi
    j=$((j + 1))
  done
  load[best]=$((best_load + n))
  # An `if`, not `[ ... ] && printf`: a false test as the loop's last command
  # becomes the script's exit status, so the caller would see failure or
  # success depending on nothing but whether the final file happened to land
  # in the requested shard.
  if [ "$best" -eq "$((index - 1))" ]; then
    printf '%s\n' "$f"
  fi
done <<EOF
$sorted
EOF

exit 0
