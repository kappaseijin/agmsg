#!/usr/bin/env bash
# validate.sh — input validation for values that become filesystem paths.
#
# Team names are used directly as path segments in the team registry
# (teams/<name>/config.json). A name containing "/", "\", or equal to "." / ".."
# can escape teams/ and create/read/move/delete files outside the agmsg state
# tree (#140). Validate at every entry point that turns a team name into a path:
# join.sh, leave.sh, team.sh, rename.sh, rename-team.sh, doctor.sh (--team).
#
# Team names are intentionally allowed to be arbitrary UTF-8 (e.g. Japanese team
# names like "testチーム" exist in the wild), so this is a deny-list of
# path-dangerous constructs, NOT an ASCII allow-list. Multibyte UTF-8 bytes are
# all >= 0x80, so they never match the control-character range below.

# Guard against double-source.
[ -n "${_AGMSG_VALIDATE_SH:-}" ] && return 0
_AGMSG_VALIDATE_SH=1

# Return 0 when <value> is valid UTF-8, else print a byte-level diagnostic and
# return 1. This is deliberately byte-wise and dependency-free: send.sh is a
# public shell command and must reject malformed input before it loads storage,
# resolves a DB path, initializes a DB, or consults the roster (#146).
#
# Keep the locale change inside the function. Bash 3.2 counts characters under
# a UTF-8 locale, while this validator must inspect each octet. Bash 3.2 can
# also report octets >= 0x80 as signed values from printf '%d'; normalize those
# values before applying the UTF-8 range rules.
agmsg_validate_utf8() {
  local field="$1" value="$2"
  (
    export LC_ALL=C
    local i=0 n=${#value} c1 c2 c3 c4 b1 b2 b3 b4 min_second max_second

    while [ "$i" -lt "$n" ]; do
      c1="${value:$i:1}"
      printf -v b1 '%d' "'$c1"
      [ "$b1" -lt 0 ] && b1=$((b1 + 256))

      if [ "$b1" -le 127 ]; then
        i=$((i + 1))
        continue
      fi

      if [ "$b1" -ge 194 ] && [ "$b1" -le 223 ]; then
        if [ $((i + 1)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c2="${value:$((i + 1)):1}"
        printf -v b2 '%d' "'$c2"
        [ "$b2" -lt 0 ] && b2=$((b2 + 256))
        if [ "$b2" -lt 128 ] || [ "$b2" -gt 191 ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 2)) "$b2" >&2
          exit 1
        fi
        i=$((i + 2))
        continue
      fi

      if [ "$b1" -ge 224 ] && [ "$b1" -le 239 ]; then
        if [ "$b1" -eq 224 ]; then
          min_second=160
          max_second=191
        elif [ "$b1" -eq 237 ]; then
          min_second=128
          max_second=159
        else
          min_second=128
          max_second=191
        fi
        if [ $((i + 1)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c2="${value:$((i + 1)):1}"
        printf -v b2 '%d' "'$c2"
        [ "$b2" -lt 0 ] && b2=$((b2 + 256))
        if [ "$b2" -lt "$min_second" ] || [ "$b2" -gt "$max_second" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 2)) "$b2" >&2
          exit 1
        fi
        if [ $((i + 2)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c3="${value:$((i + 2)):1}"
        printf -v b3 '%d' "'$c3"
        [ "$b3" -lt 0 ] && b3=$((b3 + 256))
        if [ "$b3" -lt 128 ] || [ "$b3" -gt 191 ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 3)) "$b3" >&2
          exit 1
        fi
        i=$((i + 3))
        continue
      fi

      if [ "$b1" -ge 240 ] && [ "$b1" -le 244 ]; then
        if [ "$b1" -eq 240 ]; then
          min_second=144
          max_second=191
        elif [ "$b1" -eq 244 ]; then
          min_second=128
          max_second=143
        else
          min_second=128
          max_second=191
        fi
        if [ $((i + 1)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c2="${value:$((i + 1)):1}"
        printf -v b2 '%d' "'$c2"
        [ "$b2" -lt 0 ] && b2=$((b2 + 256))
        if [ "$b2" -lt "$min_second" ] || [ "$b2" -gt "$max_second" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 2)) "$b2" >&2
          exit 1
        fi
        if [ $((i + 2)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c3="${value:$((i + 2)):1}"
        printf -v b3 '%d' "'$c3"
        [ "$b3" -lt 0 ] && b3=$((b3 + 256))
        if [ "$b3" -lt 128 ] || [ "$b3" -gt 191 ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 3)) "$b3" >&2
          exit 1
        fi
        if [ $((i + 3)) -ge "$n" ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): truncated UTF-8 sequence; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 1)) "$b1" >&2
          exit 1
        fi
        c4="${value:$((i + 3)):1}"
        printf -v b4 '%d' "'$c4"
        [ "$b4" -lt 0 ] && b4=$((b4 + 256))
        if [ "$b4" -lt 128 ] || [ "$b4" -gt 191 ]; then
          printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): expected continuation byte; repair the caller's string construction and retry. Message was not queued.\n" \
            "$field" $((i + 4)) "$b4" >&2
          exit 1
        fi
        i=$((i + 4))
        continue
      fi

      if [ "$b1" -ge 128 ] && [ "$b1" -le 191 ]; then
        reason="unexpected continuation byte"
      else
        reason="invalid leading byte"
      fi
      printf "agmsg: invalid UTF-8 in %s at byte %d (0x%02X): %s; repair the caller's string construction and retry. Message was not queued.\n" \
        "$field" $((i + 1)) "$b1" "$reason" >&2
      exit 1
    done
    exit 0
  )
}

# Return 0 if <name> is safe to use as a single path segment, else print a
# specific error to stderr and return 1.
agmsg_validate_team_name() {
  local name="$1"
  if [ -z "$name" ]; then
    echo "agmsg: invalid team name: must not be empty" >&2
    return 1
  fi
  case "$name" in
    .|..)
      echo "agmsg: invalid team name '$name': '.' and '..' are not allowed" >&2
      return 1 ;;
    */*|*\\*)
      echo "agmsg: invalid team name '$name': must not contain '/' or '\\' (path traversal)" >&2
      return 1 ;;
    -*)
      # Leading '-' would be parsed as an option by downstream tools.
      echo "agmsg: invalid team name '$name': must not start with '-'" >&2
      return 1 ;;
  esac
  # Reject control characters (NUL can't reach a shell var, but newline / tab /
  # other C0 + DEL can corrupt paths, configs, and row-counting output).
  case "$name" in
    *[[:cntrl:]]*)
      echo "agmsg: invalid team name: must not contain control characters" >&2
      return 1 ;;
  esac
  return 0
}

# Agent names are interpolated into a SQLite JSON path ($.agents.<name>); '.',
# '[', ']', '"' would misroute the path (silent wrong-key / array index), and
# '/' '\' / control chars are path/format hazards. UTF-8 (>= 0x80) is fine.
agmsg_validate_agent_name() {
  local name="$1"
  if [ -z "$name" ]; then
    echo "agmsg: invalid agent name: must not be empty" >&2
    return 1
  fi
  case "$name" in
    .|..)
      echo "agmsg: invalid agent name '$name': '.' and '..' are not allowed" >&2
      return 1 ;;
    -*)
      echo "agmsg: invalid agent name '$name': must not start with '-'" >&2
      return 1 ;;
    *[./\\\"]* | *[][]* | *[[:cntrl:]]*)
      echo "agmsg: invalid agent name '$name': must not contain . / \ \" [ ] or control characters" >&2
      return 1 ;;
  esac
  return 0
}
