#!/usr/bin/env bash
# cli-options.sh — shared parsing for small, position-independent agmsg flags.
#
# Callers choose which flags they allow, then read the normalized globals:
#   AGMSG_OPTION_FORCE
#   AGMSG_OPTION_NO_RESOLVE
#   AGMSG_POSITIONAL_ARGS (indexed array)
#
# `--` ends option parsing so a positional value that starts with `-` can be
# passed deliberately. Unknown long options are rejected instead of being
# silently reinterpreted as positional arguments.

# Guard against double-source.
[ -n "${_AGMSG_CLI_OPTIONS_SH:-}" ] && return 0
_AGMSG_CLI_OPTIONS_SH=1

agmsg_parse_cli_options() {
  local command_name="$1" allow_force="$2" allow_no_resolve="$3" arg supported
  shift 3

  AGMSG_OPTION_FORCE=0
  AGMSG_OPTION_NO_RESOLVE=0
  AGMSG_POSITIONAL_ARGS=()

  supported=""
  [ "$allow_force" -eq 1 ] && supported="--force"
  if [ "$allow_no_resolve" -eq 1 ]; then
    [ -n "$supported" ] && supported="$supported, "
    supported="${supported}--no-resolve"
  fi
  [ -n "$supported" ] || supported="none"

  while [ "$#" -gt 0 ]; do
    arg="$1"
    shift

    case "$arg" in
      --)
        # Everything after the delimiter is a positional argument, including
        # strings that look like options.
        while [ "$#" -gt 0 ]; do
          AGMSG_POSITIONAL_ARGS+=("$1")
          shift
        done
        break
        ;;
      --force)
        if [ "$allow_force" -ne 1 ]; then
          echo "$command_name: option '--force' is not supported here (supported: $supported)." >&2
          return 2
        fi
        if [ "$AGMSG_OPTION_FORCE" -eq 1 ]; then
          echo "$command_name: option '--force' was supplied more than once." >&2
          return 2
        fi
        AGMSG_OPTION_FORCE=1
        ;;
      --no-resolve)
        if [ "$allow_no_resolve" -ne 1 ]; then
          echo "$command_name: option '--no-resolve' is not supported here (supported: $supported)." >&2
          return 2
        fi
        if [ "$AGMSG_OPTION_NO_RESOLVE" -eq 1 ]; then
          echo "$command_name: option '--no-resolve' was supplied more than once." >&2
          return 2
        fi
        AGMSG_OPTION_NO_RESOLVE=1
        ;;
      --*)
        echo "$command_name: unknown option '$arg' (supported: $supported; options may appear anywhere)." >&2
        return 2
        ;;
      *)
        AGMSG_POSITIONAL_ARGS+=("$arg")
        ;;
    esac
  done
}
