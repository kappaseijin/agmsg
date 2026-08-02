#!/usr/bin/env bash
# spawn-options.sh — per-agent-type extra CLI args injected by spawn.sh.
#
# Reads a small YAML file mapping agent type -> a flat map of CLI flag ->
# value, using the same simple dialect db/config.yaml already uses (flat
# "section:" header + 2-space-indented "key: value", no nesting, no
# quoting — see config.sh's yaml_get). Turns one type's section into a list
# of ready-to-use shell tokens spawn.sh splices into its launch command.
#
# File resolution: $AGMSG_SPAWN_OPTIONS_FILE if set, else
# ~/.agmsg/config/spawn_options.yaml — agmsg's planned install-path-
# independent config home (#201), distinct from the current skill-dir-rooted
# db/config.yaml so it survives a custom --cmd install or multiple installs.
# A missing file, missing type section, or empty file all mean "no extra
# args" — this feature is fully opt-in and backward compatible.
#
# Value semantics (per key under a type's section):
#   <key>: <value>   -> two tokens: <key> <value>
#   <key>: true      -> one token:  <key>            (boolean flag on)
#   <key>: false     -> no tokens                     (explicitly suppressed)

# Guard against double-source.
[ -n "${_AGMSG_SPAWN_OPTIONS_SH:-}" ] && return 0
_AGMSG_SPAWN_OPTIONS_SH=1

agmsg_spawn_options_file() {
  printf '%s' "${AGMSG_SPAWN_OPTIONS_FILE:-$HOME/.agmsg/config/spawn_options.yaml}"
}

# Emit one shell token per output line for a single section. Each line is a
# complete argv token — the caller must read line-by-line (never word-split
# the output), so a value containing spaces stays a single token.
agmsg_spawn_options_section_tokens() {
  local section="$1" file
  file="$(agmsg_spawn_options_file)"
  [ -f "$file" ] || return 0

  awk -v section="$section" '
    /^[^ #]/ {
      header = $0
      sub(/[ \t]+#.*$/, "", header)
      sub(/[ \t]+$/, "", header)
      in_section = (header == section ":")
    }
    in_section && /^  [^ ]/ {
      line = $0
      sub(/^  /, "", line)
      idx = index(line, ":")
      if (idx == 0) next
      key = substr(line, 1, idx - 1)
      val = substr(line, idx + 1)
      sub(/[ \t]+#.*$/, "", val)
      sub(/^[ \t]+/, "", val)
      sub(/[ \t]+$/, "", val)
      if (val == "false") next
      print key
      if (val != "" && val != "true") print val
    }
  ' "$file"
}

# Emit the type section, followed by its optional role overlay. The two token
# streams are deliberately concatenated without resolving duplicate flags:
# their meaning remains the launched CLI's responsibility.
agmsg_spawn_options_tokens() {
  local type="$1" role="${2:-}"
  agmsg_spawn_options_section_tokens "$type"
  [ -z "$role" ] || agmsg_spawn_options_section_tokens "${type}@${role}"
}
