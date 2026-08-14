# ADR 0005: Replace `scripts/` via staged rename, not in-place `cp -R`

**Status:** proposed
**Date:** 2026-08-09
**Deciders:** @kappaseijin

## Context

`install.sh` refreshes an existing install's `scripts/` tree with
`cp -R "$SCRIPT_DIR/scripts/." "$SKILL_DIR/scripts/"`, in both the `--update`
path and the fresh-install path. For `--update`, the destination normally has
one or more `watch.sh`-derived watcher processes running against it — agmsg
is explicitly a tool that expects a long-lived watcher per project.

bash does not read a script into memory before executing it: it keeps a file
descriptor and a byte offset, and reads more of the file as execution
proceeds past each statement. `cp` into an existing destination file
truncates that file's inode and rewrites it in place. A process with the file
already open therefore does not get a fresh copy — it keeps reading from the
*same inode*, from its current offset, into whatever new bytes now occupy
that inode. The process does not detect this; there is no error, no signal,
nothing that distinguishes "kept reading the file I opened" from "started
reading a different file".

A reproduction confirms two failure shapes, both silent (`exit 0`, no error
output) unless the mismatch happens to hit invalid syntax:

- **Replacement is shorter than what's already been read past:** the watcher
  reaches EOF mid-loop and exits — cleanly, successfully, having silently
  stopped doing its job partway through a message it should have kept
  processing.
- **Replacement's content differs from a byte offset the watcher hasn't
  reached yet:** the watcher can execute a mix of old-file bytes before the
  overwrite and new-file bytes after it, landing on a syntax error or
  executing code that was never valid in either version standalone.

"The PID is unchanged after `--update`" is not evidence of safety — the
process not dying is exactly what this failure mode produces. The only
reliable check is whether the process keeps doing its actual job (see the
test added alongside this ADR, and `docs/decisions/*` in the
`herdr-agent-monitor` project's Issue #16/#17, which first surfaced and
reproduced this on a live fleet).

## Decision

Add `scripts/lib/safe-dir-sync.sh` (`safe_dir_sync SOURCE_DIR DEST_DIR`) and
use it in both `install.sh` call sites that currently do
`cp -R "$SCRIPT_DIR/scripts/." "$SKILL_DIR/scripts/"`.

`safe_dir_sync` stages the new tree in a fresh temp directory on the *same
filesystem* as the destination (verified via `stat`'s device id; a mismatch
is a loud error, not a silent fallback to the unsafe copy), then moves each
file into the destination one at a time with `mv`. On a single filesystem,
`mv` is `rename(2)`: it swaps the destination directory entry to point at the
new inode and never writes into the old inode's content. A process that
already has the old file open keeps reading the old content, completely
undisturbed, until it closes the descriptor on its own.

This preserves the existing "orphans survive" semantics of
`cp -R src/. dest/` — a destination file with no counterpart in the new
source tree is left alone, not deleted — since `safe_dir_sync` only ever
moves files that exist in the staged copy of the source.

Not platform-gated. Both call sites (the `--update` path, where a running
watcher is expected, and the fresh-install path, where the destination is
normally empty and therefore lower-risk) switch to `safe_dir_sync`, for
implementation consistency and because a fresh install is not guaranteed to
target an empty directory (see Alternatives).

## Alternatives considered

- **Only fix the `--update` call site, leave fresh-install's `cp -R` as is.**
  Rejected: the fresh-install destination is *usually* empty, but nothing
  guarantees it — a user re-running `install.sh` (no `--update`) over a
  partially-broken existing install, or two installs racing, would hit the
  same failure mode. One code path, one property to reason about, is safer
  than two paths with different guarantees that happen to look identical.
- **Detect running watchers and refuse to `--update` while any are active.**
  Rejected: agmsg is designed around always-on watchers; requiring them all
  to stop first defeats the tool's own purpose (every team using agmsg would
  need a coordinated shutdown for every update) and doesn't help the
  fresh-install path.
- **`rsync --inplace` or similar tooling.** Rejected: `--inplace` is the
  opposite of what's needed here (it explicitly *avoids* the temp-file+rename
  dance for space/bandwidth reasons); a plain `rsync` without `--inplace`
  already does temp-file+rename internally and would work, but adds an
  external dependency agmsg doesn't otherwise have, for behavior `mv` already
  provides natively once the source is staged.
- **Branch on `is_windows_host()` and only apply the safe path on POSIX,
  keeping `cp -R` on Windows/MSYS2 until verified.** Rejected: NTFS's
  file-locking semantics differ from POSIX's unlink-while-open guarantee, so
  the *zero-interruption* property is unverified there — but staged `mv`
  failing outright on a locked file is still strictly safer than `cp -R`'s
  silent in-place truncation (a loud, actionable error vs. undetectable
  corruption). No reason to keep the strictly-worse path on any platform
  while the safer path's edge case is merely unverified rather than known-bad.
- **Do nothing; rely on "restart any running sessions" guidance already
  printed after `--update`.** This is the status quo. Rejected: the guidance
  only helps if the user restarts *before* a watcher next reads a changed
  offset, which is a race with no visible signal telling the user how much
  time they have — Issue #16/#17's own incident got lucky rather than safe.

## Consequences

- Positive: `install.sh --update` no longer risks silently corrupting a
  running watcher fleet — the exact failure class this ADR's Context section
  reproduces.
- Positive: the same mechanism, once proven here, is reusable for any other
  install-time directory sync agmsg adds later.
- Neutral: `safe_dir_sync` requires staging and destination to resolve to the
  same filesystem device; this holds for every known install layout
  (`SKILL_DIR` and its `scripts/` subdirectory share a parent), so this is
  not expected to constrain any real install, but is asserted rather than
  assumed.
- Negative / known gap: the *zero-interruption* guarantee is verified only on
  POSIX (macOS/Linux) — Windows/MSYS2 behavior under `mv` onto a
  currently-open file is unverified. Tracked as a follow-up rather than
  blocking this change (see References).

## References

- `herdr-agent-monitor` repo Issues #16 and #17 (where this was first
  reported and investigated; that repo also fixed the analogous problem for
  its own `install.sh` — this ADR ports the same rename-based technique).
- `scripts/lib/safe-dir-sync.sh`, `tests/test_safe_dir_sync.bats`.
- Follow-up (to be filed): verify (or explicitly scope out) Windows/MSYS2
  behavior for `safe_dir_sync`.
