#!/usr/bin/env bash
# safe-dir-sync.sh — replace a directory's contents without truncating files
# that a running process (e.g. an agmsg watcher) still has open (#16, #17).
#
# bash does not read a script into memory before executing it — it keeps a
# file descriptor and an offset, and reads more as execution proceeds. `cp`
# into an existing file truncates that same inode and rewrites it in place,
# so a live watcher can start reading unrelated new bytes at its next read
# (or hit EOF early), with no error: the shell has no way to detect that the
# file changed under it. (#16 reproduced this in a throwaway directory: a
# shorter replacement script made the watcher exit "successfully" mid-loop,
# a differently-worded one made it crash on a mismatched line offset.)
#
# The fix builds the new tree in a staging directory on the SAME filesystem
# as the destination, then moves each file into place one at a time. `mv`
# on the same filesystem is a rename(2): it swaps the destination directory
# entry to point at the new inode and never touches the old inode's content.
# A process that already has the old file open keeps reading the old
# content undisturbed until it closes the descriptor.
#
# Not verified on Windows/MSYS2: NTFS's file-locking semantics differ from
# POSIX's unlink-while-open guarantee, and a locked destination file could
# make `mv` fail outright rather than transparently replace it. That failure
# is still strictly safer than the previous silent in-place truncation (a
# loud, explicit error vs. undetectable corruption), so this function is not
# platform-gated — but the "no interruption to a running watcher" guarantee
# specifically is only confirmed on POSIX (macOS/Linux). See ADR-0005.
#
# Usage:
#   source scripts/lib/safe-dir-sync.sh
#   safe_dir_sync "$SOURCE_DIR" "$DEST_DIR"
#
# Preserves any file in $DEST_DIR that has no counterpart in $SOURCE_DIR
# (same "orphans survive" behavior as the `cp -R src/. dest/` it replaces).
# $DEST_DIR is created if missing.

# Internal: filesystem device id for a same-filesystem check. macOS's stat
# uses -f; Linux and MSYS2 (which ships a GNU-flavored stat) use -c.
_safe_dir_sync_device() {
  case "$(uname -s)" in
    Darwin*) stat -f '%d' "$1" 2>/dev/null ;;
    *)       stat -c '%d' "$1" 2>/dev/null ;;
  esac
}

safe_dir_sync() {
  local source_dir="$1" dest_dir="$2"
  local dest_parent staging rel d f

  [ -d "$source_dir" ] || {
    echo "error: safe_dir_sync: source not a directory: $source_dir" >&2
    return 1
  }

  mkdir -p "$dest_dir" || {
    echo "error: safe_dir_sync: failed to create destination: $dest_dir" >&2
    return 1
  }
  dest_parent="$(dirname "$dest_dir")"

  staging="$(mktemp -d "$dest_parent/.safe-dir-sync.XXXXXX")" || {
    echo "error: safe_dir_sync: failed to create staging dir under $dest_parent" >&2
    return 1
  }

  if [ "$(_safe_dir_sync_device "$dest_parent")" != "$(_safe_dir_sync_device "$staging")" ]; then
    echo "error: safe_dir_sync: staging and destination resolved to different filesystems" >&2
    rm -rf "$staging"
    return 1
  fi

  if ! cp -R "$source_dir/." "$staging/"; then
    echo "error: safe_dir_sync: failed to stage source tree from $source_dir" >&2
    rm -rf "$staging"
    return 1
  fi

  # Directories first (mkdir -p never truncates existing content), then move
  # files one at a time so each replacement is a single rename(2).
  while IFS= read -r d; do
    [ "$d" = "$staging" ] && continue
    rel="${d#"$staging"/}"
    mkdir -p "$dest_dir/$rel"
  done < <(find "$staging" -type d -print | sort)

  while IFS= read -r f; do
    rel="${f#"$staging"/}"
    if ! mv "$f" "$dest_dir/$rel"; then
      echo "error: safe_dir_sync: failed to move $rel into place; destination may be partially updated" >&2
      rm -rf "$staging"
      return 1
    fi
  done < <(find "$staging" \( -type f -o -type l \) -print | sort)

  find "$staging" -depth -type d -exec rmdir {} + 2>/dev/null || true
}
