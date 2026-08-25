#!/usr/bin/env bash

# Guard against double-sourcing when more than one storage helper loads this
# library in the same shell.
[ -n "${_AGMSG_UTF8_SH:-}" ] && return 0
_AGMSG_UTF8_SH=1

# Read JSONL from stdin and replace malformed UTF-8 one input byte at a time.
# Valid sequences, ASCII JSON syntax, and line terminators are preserved. This
# deliberately works on the already-built JSONL stream: it does not interpret
# JSON quotes, escapes, or field names.
agmsg_sanitize_utf8() {
  (
    export LC_ALL=C
    local line i n c1 c2 c3 c4 b1 b2 b3 b4 replacement valid_second
    # Bash 3.2 can report bytes >= 0x80 as negative from printf '%d'; each
    # byte value below is normalized to unsigned before range checks.
    replacement=$'\357\277\275'
    while IFS= read -r line; do
      i=0
      n=${#line}
      while [ "$i" -lt "$n" ]; do
        c1="${line:$i:1}"
        printf -v b1 '%d' "'$c1"
        [ "$b1" -lt 0 ] && b1=$((b1 + 256))

        if [ "$b1" -le 127 ]; then
          printf '%s' "$c1"
          i=$((i + 1))
          continue
        fi

        if [ "$b1" -ge 194 ] && [ "$b1" -le 223 ]; then
          if [ $((i + 1)) -ge "$n" ]; then
            printf '%s' "$replacement"
            i=$n
            continue
          fi
          c2="${line:$((i + 1)):1}"
          printf -v b2 '%d' "'$c2"
          [ "$b2" -lt 0 ] && b2=$((b2 + 256))
          if [ "$b2" -ge 128 ] && [ "$b2" -le 191 ]; then
            printf '%s%s' "$c1" "$c2"
            i=$((i + 2))
          else
            printf '%s' "$replacement"
            i=$((i + 1))
          fi
          continue
        fi

        if [ "$b1" -ge 224 ] && [ "$b1" -le 239 ]; then
          if [ $((i + 1)) -ge "$n" ]; then
            printf '%s' "$replacement"
            i=$n
            continue
          fi
          c2="${line:$((i + 1)):1}"
          printf -v b2 '%d' "'$c2"
          [ "$b2" -lt 0 ] && b2=$((b2 + 256))
          case "$b1" in
            224) [ "$b2" -ge 160 ] && [ "$b2" -le 191 ] && valid_second=1 || valid_second=0 ;;
            237) [ "$b2" -ge 128 ] && [ "$b2" -le 159 ] && valid_second=1 || valid_second=0 ;;
            *)   [ "$b2" -ge 128 ] && [ "$b2" -le 191 ] && valid_second=1 || valid_second=0 ;;
          esac
          if [ "$valid_second" -eq 0 ]; then
            printf '%s' "$replacement"
            i=$((i + 1))
            continue
          fi
          if [ $((i + 2)) -ge "$n" ]; then
            printf '%s%s' "$replacement" "$replacement"
            i=$n
            continue
          fi
          c3="${line:$((i + 2)):1}"
          printf -v b3 '%d' "'$c3"
          [ "$b3" -lt 0 ] && b3=$((b3 + 256))
          if [ "$b3" -ge 128 ] && [ "$b3" -le 191 ]; then
            printf '%s%s%s' "$c1" "$c2" "$c3"
            i=$((i + 3))
          else
            printf '%s%s' "$replacement" "$replacement"
            i=$((i + 2))
          fi
          continue
        fi

        if [ "$b1" -ge 240 ] && [ "$b1" -le 244 ]; then
          if [ $((i + 1)) -ge "$n" ]; then
            printf '%s' "$replacement"
            i=$n
            continue
          fi
          c2="${line:$((i + 1)):1}"
          printf -v b2 '%d' "'$c2"
          [ "$b2" -lt 0 ] && b2=$((b2 + 256))
          case "$b1" in
            240) [ "$b2" -ge 144 ] && [ "$b2" -le 191 ] && valid_second=1 || valid_second=0 ;;
            244) [ "$b2" -ge 128 ] && [ "$b2" -le 143 ] && valid_second=1 || valid_second=0 ;;
            *)   [ "$b2" -ge 128 ] && [ "$b2" -le 191 ] && valid_second=1 || valid_second=0 ;;
          esac
          if [ "$valid_second" -eq 0 ]; then
            printf '%s' "$replacement"
            i=$((i + 1))
            continue
          fi
          if [ $((i + 2)) -ge "$n" ]; then
            printf '%s%s' "$replacement" "$replacement"
            i=$n
            continue
          fi
          c3="${line:$((i + 2)):1}"
          printf -v b3 '%d' "'$c3"
          [ "$b3" -lt 0 ] && b3=$((b3 + 256))
          if [ "$b3" -lt 128 ] || [ "$b3" -gt 191 ]; then
            printf '%s%s' "$replacement" "$replacement"
            i=$((i + 2))
            continue
          fi
          if [ $((i + 3)) -ge "$n" ]; then
            printf '%s%s%s' "$replacement" "$replacement" "$replacement"
            i=$n
            continue
          fi
          c4="${line:$((i + 3)):1}"
          printf -v b4 '%d' "'$c4"
          [ "$b4" -lt 0 ] && b4=$((b4 + 256))
          if [ "$b4" -ge 128 ] && [ "$b4" -le 191 ]; then
            printf '%s%s%s%s' "$c1" "$c2" "$c3" "$c4"
            i=$((i + 4))
          else
            printf '%s%s%s' "$replacement" "$replacement" "$replacement"
            i=$((i + 3))
          fi
          continue
        fi

        # Continuation bytes, overlong leads, and code points above U+10FFFF
        # are each malformed input bytes and therefore each become one marker.
        printf '%s' "$replacement"
        i=$((i + 1))
      done
      printf '\n'
    done
  )
}
