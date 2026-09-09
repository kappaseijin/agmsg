#!/usr/bin/env bash
# Source this from an agmsg-managed agent boot script.  It validates the
# installed gh launcher before making it the only supported PATH entry.

AGMSG_GH_GUARD_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || return 1

agmsg_gh_guard_environment() {
  local expected_guard agents_bin launcher guard guard_dir real path_entry
  local normalized='' first=1
  expected_guard="$AGMSG_GH_GUARD_HELPER_DIR/gh-write-owner-guard.sh"
  agents_bin="${AGMSG_AGENTS_BIN:-$HOME/.agents/bin}"
  launcher="$agents_bin/gh"

  [ -x "$launcher" ] && [ ! -d "$launcher" ] || {
    echo 'error: agmsg guard environment: gh launcher is missing' >&2
    return 1
  }
  grep -Fqx '# agmsg gh owner guard launcher' "$launcher" || {
    echo 'error: agmsg guard environment: gh launcher is not agmsg-generated' >&2
    return 1
  }
  guard="$(sed -n "s/^GUARD_SCRIPT='\\([^']*\\)'$/\\1/p" "$launcher")"
  real="$(sed -n "s/^REAL_GH='\\([^']*\\)'$/\\1/p" "$launcher")"
  guard_dir="$(cd "$(dirname "$guard")" 2>/dev/null && pwd -P)" || {
    echo 'error: agmsg guard environment: launcher guard path is unverified' >&2
    return 1
  }
  guard="$guard_dir/$(basename "$guard")"
  [ "$guard" = "$expected_guard" ] && [ -x "$guard" ] || {
    echo 'error: agmsg guard environment: launcher guard path is unverified' >&2
    return 1
  }
  case "$real" in
    /*) [ -x "$real" ] && [ ! -d "$real" ] || return 1 ;;
    *) echo 'error: agmsg guard environment: launcher real gh path is unverified' >&2; return 1 ;;
  esac

  local old_ifs="$IFS"
  IFS=:
  for path_entry in $PATH; do
    [ -n "$path_entry" ] || path_entry='.'
    [ "$path_entry" = "$agents_bin" ] && continue
    if [ "$first" -eq 1 ]; then normalized="$path_entry"; first=0
    else normalized="$normalized:$path_entry"; fi
  done
  IFS="$old_ifs"
  PATH="$agents_bin${normalized:+:$normalized}"
  export PATH
}
