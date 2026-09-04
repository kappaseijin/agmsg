#!/usr/bin/env bash
# Resolve a startup-file path without changing the symlink itself.
#
# The resolver follows one symbolic-link hop at a time so it can enforce a
# bounded, fail-closed walk. Relative link targets are interpreted from the
# physical directory containing the link. A missing non-link path is returned
# unchanged for the install path, where it is a new file to create; every
# existing final target must be a regular file.

agmsg_resolve_startup_path() {
  local current="$1" link parent
  local hops=0

  if [ -z "$current" ]; then
    echo "startup path is empty" >&2
    return 1
  fi

  # A new startup file has no target to resolve yet. The caller may create it
  # at the configured path. An existing path, including a dangling symlink,
  # must pass through the bounded walk below.
  if [ ! -e "$current" ] && [ ! -L "$current" ]; then
    printf '%s\n' "$current"
    return 0
  fi

  while [ -L "$current" ]; do
    if [ "$hops" -ge 40 ]; then
      echo "startup path symlink chain exceeds 40 hops: $current" >&2
      return 1
    fi
    if ! link="$(readlink "$current")"; then
      echo "cannot read startup path symlink: $current" >&2
      return 1
    fi
    if [ -z "$link" ]; then
      echo "startup path symlink has an empty target: $current" >&2
      return 1
    fi

    case "$link" in
      /*) current="$link" ;;
      *)
        if ! parent="$(cd -P "$(dirname "$current")" 2>/dev/null && pwd -P)"; then
          echo "cannot resolve startup path symlink directory: $current" >&2
          return 1
        fi
        current="$parent/$link"
        ;;
    esac
    hops=$((hops + 1))

    if [ ! -e "$current" ] && [ ! -L "$current" ]; then
      echo "startup path symlink target does not exist: $current" >&2
      return 1
    fi
  done

  if [ ! -f "$current" ]; then
    echo "startup path target is not a regular file: $current" >&2
    return 1
  fi
  if ! parent="$(cd -P "$(dirname "$current")" 2>/dev/null && pwd -P)"; then
    echo "cannot resolve startup path target directory: $current" >&2
    return 1
  fi
  printf '%s/%s\n' "$parent" "$(basename "$current")"
}
