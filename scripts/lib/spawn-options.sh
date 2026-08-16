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
  local section="$1" suppressed_keys="${2:-}" file
  file="$(agmsg_spawn_options_file)"
  [ -f "$file" ] || return 0

  printf '%s\n' "$suppressed_keys" |
    awk -v section="$section" '
    NR == FNR {
      if ($0 != "") suppressed[$0] = 1
      next
    }
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
      if (key in suppressed) next
      val = substr(line, idx + 1)
      sub(/[ \t]+#.*$/, "", val)
      sub(/^[ \t]+/, "", val)
      sub(/[ \t]+$/, "", val)
      if (val == "false") next
      print key
      if (val != "" && val != "true") print val
    }
  ' - "$file"
}

# Emit the keys defined by a single section, including keys whose values are
# false. Overlay keys suppress matching base keys before either section emits
# tokens, so false is an explicit override that disables the base flag.
agmsg_spawn_options_section_keys() {
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
      print substr(line, 1, idx - 1)
    }
  ' "$file"
}

# Return success when a section header exists, even when that section has no
# keys. Required overlays distinguish an empty section from a missing section.
agmsg_spawn_options_section_exists() {
  local section="$1" file
  file="$(agmsg_spawn_options_file)"
  [ -f "$file" ] || return 1

  awk -v section="$section" '
    /^[^ #]/ {
      header = $0
      sub(/[ \t]+#.*$/, "", header)
      sub(/[ \t]+$/, "", header)
      if (header == section ":") found = 1
    }
    END { exit(found ? 0 : 1) }
  ' "$file"
}

# Explicit --role selections must name a real type@role section. This is
# intentionally separate from the automatic role path, whose missing section
# remains a backward-compatible no-op.
agmsg_spawn_options_require_explicit_role_section() {
  local type="$1" role="${2:-}" section
  [ -n "$role" ] || return 0
  section="${type}@${role}"
  if agmsg_spawn_options_section_exists "$section"; then
    return 0
  fi
  printf 'spawn: explicit --role requires spawn-options section %s\n' "$section" >&2
  return 1
}

# Read one scalar without passing it through the argv token emitter. Metadata
# and CLI data share the same YAML dialect, but metadata must never become a
# launch argument by accident.
agmsg_spawn_options_section_value() {
  local section="$1" key="$2" file
  file="$(agmsg_spawn_options_file)"
  [ -f "$file" ] || return 1

  awk -v section="$section" -v wanted_key="$key" '
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
      current_key = substr(line, 1, idx - 1)
      sub(/[ \t]+$/, "", current_key)
      if (current_key != wanted_key || found) next
      value = substr(line, idx + 1)
      sub(/[ \t]+#.*$/, "", value)
      sub(/^[ \t]+/, "", value)
      sub(/[ \t]+$/, "", value)
      print value
      found = 1
    }
    END { exit(found ? 0 : 1) }
  ' "$file"
}

# Public query for consumers that need to gate role-overlay provisioning on
# agmsg's fail-closed metadata contract. It reports enabled only for an
# explicit true value; callers that need a diagnostic for malformed metadata
# use agmsg_spawn_options_validate_required_role_overlay instead.
agmsg_spawn_options_requires_role_overlay() {
  local type="${1:-}" policy
  [ "$#" -eq 1 ] || return 2

  if ! agmsg_spawn_options_section_exists 'agmsg.require-role-overlay' \
      >/dev/null 2>&1; then
    return 1
  fi
  if ! policy="$(agmsg_spawn_options_section_value \
      'agmsg.require-role-overlay' "$type" 2>/dev/null)"; then
    return 1
  fi
  [ "$policy" = true ]
}

# Emit raw key/value pairs as tab-separated records. Required profile
# validation must inspect keys that an overlay would suppress with false.
agmsg_spawn_options_section_entries() {
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
      sub(/[ \t]+$/, "", key)
      value = substr(line, idx + 1)
      sub(/[ \t]+#.*$/, "", value)
      sub(/^[ \t]+/, "", value)
      sub(/[ \t]+$/, "", value)
      printf "%s\t%s\n", key, value
    }
  ' "$file"
}

# Classify a token that may spell a manifest-declared profile flag. Exact
# flags and --flag=value are valid forms; concatenated forms fail closed.
agmsg_spawn_options_profile_token_kind() {
  local token="$1" profile_args="$2" flag remainder
  for flag in $profile_args; do
    [ -n "$flag" ] || continue
    if [ "$token" = "$flag" ]; then
      printf 'exact'
      return 0
    fi
    case "$token" in
      "$flag"=*)
        remainder="${token#"$flag="}"
        printf 'equals:%s' "$remainder"
        return 0
        ;;
    esac
    case "$flag" in
      -?|-??)
        case "$token" in
          "$flag"*)
            remainder="${token#"$flag"}"
            [ -n "$remainder" ] || continue
            printf 'concat:%s' "$remainder"
            return 0
            ;;
        esac
        ;;
      --*)
        case "$token" in
          "$flag"*)
            remainder="${token#"$flag"}"
            [ "$remainder" = "=" ] && continue
            [ -n "$remainder" ] || continue
            printf 'concat:%s' "$remainder"
            return 0
            ;;
        esac
        ;;
    esac
  done
  return 1
}

_agmsg_spawn_options_profile_error() {
  local type="$1" role="$2" message="$3"
  printf 'spawn: required role overlay for type "%s" role "%s": %s\n' \
    "$type" "$role" "$message" >&2
}

# Enforce the opt-in required-role policy and any manifest-declared profile
# contract. On success, the global profile-home values are set only when the
# boot script must export a validated profile directory.
agmsg_spawn_options_validate_required_role_overlay() {
  local type="$1" role policy overlay section_value
  local profile_args profile_env profile_default_dir profile_suffix
  local base_key base_value overlay_key overlay_value match inline
  local profile_count=0 profile_alias="" profile_dir profile_home profile_file
  local home_dir work_dir

  if [ "$#" -ge 2 ]; then
    role="$2"
  else
    role=""
  fi
  AGMSG_SPAWN_OPTIONS_PROFILE_HOME=""
  AGMSG_SPAWN_OPTIONS_PROFILE_HOME_ENV=""

  if ! agmsg_spawn_options_section_exists 'agmsg.require-role-overlay' \
      >/dev/null 2>&1; then
    return 0
  fi
  if ! section_value="$(agmsg_spawn_options_section_value \
      'agmsg.require-role-overlay' "$type" 2>/dev/null)"; then
    return 0
  fi

  policy="$section_value"
  case "$policy" in
    false) return 0 ;;
    true) ;;
    *)
      _agmsg_spawn_options_profile_error "$type" "$role" \
        "metadata agmsg.require-role-overlay must be true or false (got '$policy')"
      return 1
      ;;
  esac

  if [ -z "$role" ]; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "role is required; pass --role <role> or use a <project>_<role>_<vendor> name"
    return 1
  fi

  overlay="$type@$role"
  if ! agmsg_spawn_options_section_exists "$overlay"; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "section '$overlay' is missing"
    return 1
  fi

  # Types opt into profile validation only when their manifest declares the
  # complete generic contract. Other types still get section fail-closed.
  if ! command -v agmsg_type_get >/dev/null 2>&1; then
    return 0
  fi
  profile_args="$(agmsg_type_get "$type" role_overlay_profile_args)"
  profile_env="$(agmsg_type_get "$type" role_overlay_profile_home_env)"
  profile_default_dir="$(agmsg_type_get "$type" role_overlay_profile_default_dir)"
  profile_suffix="$(agmsg_type_get "$type" role_overlay_profile_suffix)"
  if [ -z "$profile_args" ] || [ -z "$profile_env" ] || \
     [ -z "$profile_default_dir" ] || [ -z "$profile_suffix" ]; then
    return 0
  fi

  if ! [[ "$profile_env" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "manifest profile home env name is invalid"
    return 1
  fi

  # Raw base settings are rejected even when an overlay would suppress them
  # with false. This avoids depending on CLI last-wins behavior.
  while IFS=$'\t' read -r base_key base_value; do
    [ -n "$base_key" ] || continue
    match="$(agmsg_spawn_options_profile_token_kind \
      "$base_key" "$profile_args" 2>/dev/null || true)"
    if [ -n "$match" ]; then
      _agmsg_spawn_options_profile_error "$type" "$role" \
        "base section '$type' contains a profile flag ('$base_key'); move it to '$overlay'"
      return 1
    fi
    match="$(agmsg_spawn_options_profile_token_kind \
      "$base_value" "$profile_args" 2>/dev/null || true)"
    if [ -n "$match" ]; then
      _agmsg_spawn_options_profile_error "$type" "$role" \
        "base section '$type' contains a profile flag in a value ('$base_value')"
      return 1
    fi
  done < <(agmsg_spawn_options_section_entries "$type")

  # Find exactly one role-local selector. Accepted spellings are -p: alias /
  # --profile: alias and -p=alias: true / --profile=alias: true.
  while IFS=$'\t' read -r overlay_key overlay_value; do
    [ -n "$overlay_key" ] || continue
    match="$(agmsg_spawn_options_profile_token_kind \
      "$overlay_key" "$profile_args" 2>/dev/null || true)"
    if [ -n "$match" ]; then
      case "$match" in
        exact)
          if [ -z "$overlay_value" ] || [ "$overlay_value" = true ] || \
             [ "$overlay_value" = false ]; then
            _agmsg_spawn_options_profile_error "$type" "$role" \
              "profile flag '$overlay_key' needs one non-boolean profile alias"
            return 1
          fi
          profile_alias="$overlay_value"
          ;;
        equals:*)
          inline="${match#equals:}"
          if [ -z "$inline" ] || { [ -n "$overlay_value" ] && \
             [ "$overlay_value" != true ]; }; then
            _agmsg_spawn_options_profile_error "$type" "$role" \
              "profile flag '$overlay_key' must use an alias and a true/empty value"
            return 1
          fi
          profile_alias="$inline"
          ;;
        concat:*)
          _agmsg_spawn_options_profile_error "$type" "$role" \
            "profile flag '$overlay_key' uses an unsupported concatenated form"
          return 1
          ;;
      esac
      profile_count=$((profile_count + 1))
      continue
    fi

    match="$(agmsg_spawn_options_profile_token_kind \
      "$overlay_value" "$profile_args" 2>/dev/null || true)"
    if [ -n "$match" ]; then
      _agmsg_spawn_options_profile_error "$type" "$role" \
        "profile flag in overlay value '$overlay_value' is not a role profile selector"
      return 1
    fi
  done < <(agmsg_spawn_options_section_entries "$overlay")

  if [ "$profile_count" -ne 1 ]; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "overlay '$overlay' must select exactly one profile with -p or --profile"
    return 1
  fi
  if ! [[ "$profile_alias" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || \
     [ "$profile_alias" = ".." ]; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "profile alias '$profile_alias' is not a safe filename"
    return 1
  fi

  home_dir="$(printenv HOME 2>/dev/null || true)"
  profile_dir="$(printenv "$profile_env" 2>/dev/null || true)"
  if [ -z "$profile_dir" ]; then
    case "$profile_default_dir" in
      /*) profile_dir="$profile_default_dir" ;;
      *) profile_dir="$home_dir/$profile_default_dir" ;;
    esac
  fi
  work_dir="$(pwd -P)"
  case "$profile_dir" in
    /*) profile_home="$profile_dir" ;;
    *) profile_home="$work_dir/$profile_dir" ;;
  esac
  if [ -d "$profile_dir" ]; then
    profile_home="$(cd "$profile_dir" && pwd -P)" || {
      _agmsg_spawn_options_profile_error "$type" "$role" \
        "cannot resolve profile directory '$profile_dir'"
      return 1
    }
  fi
  profile_file="$profile_home/$profile_alias$profile_suffix"
  if [ ! -f "$profile_file" ] || [ ! -r "$profile_file" ]; then
    _agmsg_spawn_options_profile_error "$type" "$role" \
      "profile '$profile_alias' requires readable file '$profile_file'"
    return 1
  fi

  # shellcheck disable=SC2034 # consumed by spawn.sh after this sourced function returns
  AGMSG_SPAWN_OPTIONS_PROFILE_HOME="$profile_home"
  # shellcheck disable=SC2034 # consumed by spawn.sh after this sourced function returns
  AGMSG_SPAWN_OPTIONS_PROFILE_HOME_ENV="$profile_env"
}

# Emit the type section with matching keys suppressed by its optional role
# overlay, then emit the overlay. A false overlay value still suppresses its
# base key, but emits no overlay token.
agmsg_spawn_options_tokens() {
  local type="$1" role="${2:-}" overlay_section overlay_keys
  if [ -z "$role" ]; then
    agmsg_spawn_options_section_tokens "$type"
    return 0
  fi

  overlay_section="${type}@${role}"
  overlay_keys="$(agmsg_spawn_options_section_keys "$overlay_section")"
  agmsg_spawn_options_section_tokens "$type" "$overlay_keys"
  agmsg_spawn_options_section_tokens "$overlay_section"
}
