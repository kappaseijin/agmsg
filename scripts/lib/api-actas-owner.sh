#!/usr/bin/env bash
# api-actas-owner.sh — read-only, classified observation of one actas owner.
#
# This is deliberately separate from actas_lock_owner().  That helper is a
# writer/GC primitive whose empty output means both "no claim" and "could not
# read".  An API consumer must not make that ambiguity an authorization
# decision, so this reader snapshots the registration and claim independently,
# validates both, and rechecks them before returning.

: "${SKILL_DIR:?api-actas-owner.sh requires SKILL_DIR}"

_api_owner_sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

_api_owner_emit() {
  local team="$1" agent="$2" status="$3" reason="$4"
  local owner="$5" owner_kind="$6" liveness="$7" consistency="$8"
  local team_sql agent_sql status_sql consistency_sql
  local reason_expr="NULL" owner_expr="NULL" kind_expr="NULL" live_expr="NULL"

  team_sql="$(_api_owner_sql_escape "$team")"
  agent_sql="$(_api_owner_sql_escape "$agent")"
  status_sql="$(_api_owner_sql_escape "$status")"
  consistency_sql="$(_api_owner_sql_escape "$consistency")"
  [ -z "$reason" ] || reason_expr="'$(_api_owner_sql_escape "$reason")'"
  [ -z "$owner" ] || owner_expr="'$(_api_owner_sql_escape "$owner")'"
  [ -z "$owner_kind" ] || kind_expr="'$(_api_owner_sql_escape "$owner_kind")'"
  [ -z "$liveness" ] || live_expr="'$(_api_owner_sql_escape "$liveness")'"

  agmsg_sqlite_mem "SELECT json_object(
    'schemaVersion', 1,
    'resource', 'actas-owner',
    'team', '$team_sql',
    'agent', '$agent_sql',
    'status', '$status_sql',
    'reason', $reason_expr,
    'owner', $owner_expr,
    'ownerKind', $kind_expr,
    'liveness', $live_expr,
    'consistency', '$consistency_sql'
  );"
}

_api_owner_fail() {
  local team="$1" agent="$2" reason="$3" code="$4"
  _api_owner_emit "$team" "$agent" unknown "$reason" '' '' '' unknown
  return "$code"
}

_api_owner_cli_fail() {
  local team="$1" agent="$2" reason="$3"
  _api_owner_emit "$team" "$agent" error "$reason" '' '' '' unknown
}

# The fingerprint is a cheap identity check; cmp below is the content check.
# Both are needed because a replacement can preserve size and timestamps.
_api_owner_fingerprint() {
  local path="$1"
  case "$(uname -s 2>/dev/null || printf '%s' unknown)" in
    Darwin) stat -f '%d:%i:%z:%m' "$path" 2>/dev/null ;;
    *)      stat -c '%d:%i:%s:%Y' "$path" 2>/dev/null ;;
  esac
}

# Read a file without command substitution removing its trailing newlines.
# The caller is responsible for creating/removing the temporary snapshot.
_api_owner_read_snapshot() {
  local file="$1" snapshot="$2" raw hex
  if ! cat "$file" > "$snapshot" 2>/dev/null; then
    return 1
  fi
  if ! hex="$(od -An -v -tx1 "$snapshot" 2>/dev/null)"; then
    return 1
  fi
  # Shell variables cannot contain NUL. Reject it before command substitution
  # would silently discard the byte and turn a binary claim into a valid token.
  case " $hex " in
    *" 00 "*) return 2 ;;
  esac
  raw="$(cat "$snapshot" 2>/dev/null; printf '\001')"
  raw="${raw%$'\001'}"
  # Keep the sentinel outside the payload so the caller's command substitution
  # cannot discard a trailing LF (an additional blank owner line is invalid).
  printf '%s\001' "$raw"
}

# Return present/missing/invalid for the requested member in one valid config.
# A member with an empty registrations array is still a valid target: it is a
# roster member, while an absent key is the distinct target_not_found case.
_api_owner_registration_state() {
  local config="$1" agent="$2" path_sql agent_sql state
  path_sql="$(agmsg_sql_readfile_path "$config")"
  agent_sql="$(_api_owner_sql_escape "$agent")"
  if ! state="$(agmsg_sqlite_mem "
    WITH source(json) AS (
      SELECT CAST(readfile('$path_sql') AS TEXT)
    ),
    safe(json) AS (
      SELECT CASE WHEN json_valid(source.json) = 1 THEN source.json ELSE '{}' END
      FROM source
    ),
    root(original, agents_json) AS (
      SELECT source.json,
             CASE
               WHEN json_type(safe.json, '\$') = 'object'
                AND json_type(safe.json, '\$.agents') = 'object'
               THEN json_extract(safe.json, '\$.agents')
               ELSE '{}'
             END
      FROM source, safe
    ),
    target(value) AS (
      SELECT a.value
      FROM root, json_each(root.agents_json) AS a
      WHERE a.key = '$agent_sql'
    ),
    member(value) AS (
      SELECT value FROM target LIMIT 1
    ),
    registrations(json) AS (
      SELECT CASE
        WHEN json_type(member.value, '\$.registrations') = 'array'
        THEN json_extract(member.value, '\$.registrations')
        ELSE '[]'
      END
      FROM member
    ),
    registration_items(value) AS (
      SELECT r.value
      FROM registrations, json_each(registrations.json) AS r
    )
    SELECT CASE
      WHEN json_valid(root.original) = 0 THEN 'invalid'
      WHEN json_type(safe.json, '\$') <> 'object' THEN 'invalid'
      WHEN json_type(safe.json, '\$.agents') <> 'object' THEN 'invalid'
      WHEN NOT EXISTS (SELECT 1 FROM target) THEN 'missing'
      WHEN COALESCE(json_type(member.value, '\$'), '') <> 'object' THEN 'invalid'
      WHEN json_type(member.value, '\$.registrations') IS NOT NULL
        AND json_type(member.value, '\$.registrations') <> 'array' THEN 'invalid'
      WHEN json_type(member.value, '\$.registrations') IS NULL
        AND (
          json_type(member.value, '\$.type') IS NOT NULL
          OR json_type(member.value, '\$.project') IS NOT NULL
        )
        AND NOT (
          json_type(member.value, '\$.type') = 'text'
          AND length(json_extract(member.value, '\$.type')) > 0
          AND json_type(member.value, '\$.project') = 'text'
          AND length(json_extract(member.value, '\$.project')) > 0
        ) THEN 'invalid'
      WHEN EXISTS (
        SELECT 1 FROM registration_items
        WHERE COALESCE(json_type(registration_items.value, '\$'), '') <> 'object'
          OR json_type(registration_items.value, '\$.type') <> 'text'
          OR length(json_extract(registration_items.value, '\$.type')) = 0
          OR json_type(registration_items.value, '\$.project') <> 'text'
          OR length(json_extract(registration_items.value, '\$.project')) = 0
      ) THEN 'invalid'
      ELSE 'present'
    END
    FROM root, safe LEFT JOIN member ON 1 = 1;
  " 2>/dev/null)"; then
    return 2
  fi
  case "$state" in
    present|missing|invalid) printf '%s' "$state"; return 0 ;;
    *) return 2 ;;
  esac
}

_api_owner_parse_claim() {
  local raw="$1" token kind=legacy
  [ -n "$raw" ] || return 1

  if [ "${raw%$'\n'}" != "$raw" ]; then
    token="${raw%$'\n'}"
    # A normal writer emits exactly one owner line.  Do not let a second line
    # disappear through command-substitution/newline normalization.
    case "$token" in *$'\n'*) return 1 ;; esac
  else
    token="$raw"
    case "$token" in *$'\n'*) return 1 ;; esac
  fi

  case "$token" in
    *[[:cntrl:]]*|*[[:space:]]*) return 1 ;;
  esac
  agmsg_validate_utf8 owner "$token" >/dev/null 2>&1 || return 1
  if agmsg_instance_is_composite "$token"; then
    _agmsg_pid_valid "${token##*.}" || return 1
    kind=composite
  fi
  printf '%s\t%s' "$token" "$kind"
}

# Existing instance-id.sh is the liveness authority. A failed observation is
# only called dead when the observation substrate itself was reachable; an
# inaccessible run directory/record remains unknown. Tests can force the
# unavailable branch with this test-only probe without changing production
# liveness code.
_api_owner_liveness() {
  local token="$1" run="$SKILL_DIR/run" record
  case "${AGMSG_TEST_API_ACTAS_OWNER_LIVENESS:-}" in
    unknown) printf 'unknown'; return 0 ;;
  esac

  if agmsg_instance_alive "$token"; then
    printf 'alive'
    return 0
  fi

  [ -d "$run" ] && [ -r "$run" ] && [ -x "$run" ] || {
    printf 'unknown'
    return 0
  }
  if agmsg_instance_is_composite "$token"; then
    record="$run/cc-instance.${token##*.}"
    if [ -L "$record" ] || { [ -e "$record" ] && { [ ! -f "$record" ] || [ ! -r "$record" ]; }; }; then
      printf 'unknown'
      return 0
    fi
  fi
  case "${MSYSTEM:-}" in
    MINGW*|MSYS*|CLANGARM*)
      command -v tasklist >/dev/null 2>&1 || { printf 'unknown'; return 0; }
      ;;
  esac
  printf 'dead'
}

_api_owner_config_stable() {
  local config="$1" initial_state="$2" initial_fp="$3" snapshot="$4"
  local team_dir="$5" team_state="$6" team_fp="$7" current_fp
  _api_owner_directory_stable "$team_dir" "$team_state" "$team_fp" || return 1
  case "$initial_state" in
    absent)
      [ ! -e "$config" ] && [ ! -L "$config" ]
      ;;
    present)
      [ -f "$config" ] && [ ! -L "$config" ] || return 1
      current_fp="$(_api_owner_fingerprint "$config")" || return 1
      [ "$current_fp" = "$initial_fp" ] || return 1
      cmp -s "$config" "$snapshot"
      ;;
    *) return 1 ;;
  esac
}

_api_owner_claim_stable() {
  local lock="$1" initial_state="$2" initial_fp="$3" snapshot="$4"
  local run_dir="$5" run_state="$6" run_fp="$7" current_fp
  _api_owner_directory_stable "$run_dir" "$run_state" "$run_fp" || return 1
  case "$initial_state" in
    absent)
      [ ! -e "$lock" ] && [ ! -L "$lock" ]
      ;;
    present)
      [ -f "$lock" ] && [ ! -L "$lock" ] || return 1
      current_fp="$(_api_owner_fingerprint "$lock")" || return 1
      [ "$current_fp" = "$initial_fp" ] || return 1
      cmp -s "$lock" "$snapshot"
      ;;
    invalid)
      case "$initial_fp" in
        regular:*)
          [ -f "$lock" ] && [ ! -L "$lock" ] || return 1
          current_fp="$(_api_owner_fingerprint "$lock")" || return 1
          [ "$current_fp" = "${initial_fp#regular:}" ] || return 1
          cmp -s "$lock" "$snapshot"
          ;;
        symlink:*)
          [ -L "$lock" ] || return 1
          [ "$(readlink "$lock" 2>/dev/null)" = "${initial_fp#symlink:}" ]
          ;;
        nonregular:*)
          [ -e "$lock" ] && [ ! -f "$lock" ] && [ ! -L "$lock" ] || return 1
          current_fp="$(_api_owner_fingerprint "$lock")" || return 1
          [ "$current_fp" = "${initial_fp#nonregular:}" ]
          ;;
        *) return 1 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

_api_owner_directory_stable() {
  local path="$1" initial_state="$2" initial_fp="$3" current_fp
  case "$initial_state" in
    absent)
      [ ! -e "$path" ] && [ ! -L "$path" ]
      ;;
    present)
      [ -d "$path" ] && [ ! -L "$path" ] && [ -r "$path" ] && [ -x "$path" ] || return 1
      current_fp="$(_api_owner_fingerprint "$path")" || return 1
      [ "$current_fp" = "$initial_fp" ]
      ;;
    *) return 1 ;;
  esac
}

_api_owner_wait_barrier() {
  local barrier="${AGMSG_TEST_API_ACTAS_OWNER_BARRIER:-}"
  [ -n "$barrier" ] || return 0
  printf '%s\n' reached > "${barrier}.reached" 2>/dev/null || return 1
  while [ ! -e "${barrier}.release" ]; do
    sleep 0.01
  done
}

_api_owner_query() (
  local team="$1" agent="$2"
  local config teams_dir team_dir run_dir lock
  local team_dir_state=absent team_dir_fp='' run_dir_state=absent run_dir_fp=''
  local config_state=absent config_fp='' config_snapshot=''
  local registration_state claim_state=absent claim_fp='' claim_snapshot=''
  local claim_raw='' owner='' owner_kind='' liveness='' parsed_claim='' read_rc
  local tmp_dir

  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-owner.XXXXXX" 2>/dev/null)" || {
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  }
  config_snapshot="$tmp_dir/config"
  claim_snapshot="$tmp_dir/claim"
  trap 'rm -rf "$tmp_dir"' EXIT INT TERM

  teams_dir="$SCRIPT_DIR/../teams"
  team_dir="$teams_dir/$team"
  config="$teams_dir/$team/config.json"
  if [ -L "$teams_dir" ] || { [ -e "$teams_dir" ] && [ ! -d "$teams_dir" ]; }; then
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  fi
  if [ -e "$teams_dir" ]; then
    [ -r "$teams_dir" ] && [ -x "$teams_dir" ] || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
  fi
  if [ -L "$team_dir" ] || { [ -e "$team_dir" ] && [ ! -d "$team_dir" ]; }; then
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  fi
  if [ -e "$team_dir" ]; then
    [ -r "$team_dir" ] && [ -x "$team_dir" ] || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
    team_dir_state=present
    team_dir_fp="$(_api_owner_fingerprint "$team_dir")" || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
  fi
  if [ -L "$config" ] || { [ -e "$config" ] && [ ! -f "$config" ]; }; then
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  fi
  if [ -e "$config" ]; then
    [ -r "$config" ] || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
    if ! cat "$config" > "$config_snapshot" 2>/dev/null; then
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    fi
    config_state=present
    config_fp="$(_api_owner_fingerprint "$config")" || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
    if ! registration_state="$(_api_owner_registration_state "$config_snapshot" "$agent")"; then
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    fi
  else
    registration_state=missing
  fi

  case "$registration_state" in
    invalid)
      if ! _api_owner_config_stable "$config" "$config_state" "$config_fp" "$config_snapshot" \
        "$team_dir" "$team_dir_state" "$team_dir_fp"; then
        _api_owner_emit "$team" "$agent" unknown concurrent_change '' '' '' unknown
      else
        _api_owner_emit "$team" "$agent" unknown read_failed '' '' '' unknown
      fi
      exit 1
      ;;
    missing)
      if _api_owner_config_stable "$config" "$config_state" "$config_fp" "$config_snapshot" \
        "$team_dir" "$team_dir_state" "$team_dir_fp"; then
        _api_owner_emit "$team" "$agent" not_found target_not_found '' '' '' observed
      else
        _api_owner_emit "$team" "$agent" unknown concurrent_change '' '' '' unknown
      fi
      exit 1
      ;;
  esac

  run_dir="$SKILL_DIR/run"
  if [ -L "$run_dir" ] || { [ -e "$run_dir" ] && [ ! -d "$run_dir" ]; }; then
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  fi
  if [ -e "$run_dir" ]; then
    [ -d "$run_dir" ] && [ -r "$run_dir" ] && [ -x "$run_dir" ] || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
    run_dir_state=present
    run_dir_fp="$(_api_owner_fingerprint "$run_dir")" || {
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    }
  fi
  lock="$(actas_lock_path "$team" "$agent")"
  if [ -L "$lock" ]; then
    claim_state=invalid
    claim_fp="symlink:$(readlink "$lock" 2>/dev/null || printf '%s' '<unreadable>')"
  elif [ -e "$lock" ]; then
    if [ ! -f "$lock" ]; then
      claim_state=invalid
      claim_fp="nonregular:$(_api_owner_fingerprint "$lock" 2>/dev/null || printf '%s' '<unreadable>')"
    elif [ ! -r "$lock" ]; then
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
    else
      claim_fp="$(_api_owner_fingerprint "$lock")" || {
        _api_owner_fail "$team" "$agent" read_failed 1
        exit 1
      }
      if ! cat "$lock" > "$claim_snapshot" 2>/dev/null; then
        _api_owner_fail "$team" "$agent" read_failed 1
        exit 1
      fi
      if claim_raw="$(_api_owner_read_snapshot "$lock" "$claim_snapshot")"; then
        claim_raw="${claim_raw%$'\001'}"
        if parsed_claim="$(_api_owner_parse_claim "$claim_raw")"; then
          claim_state=present
          IFS=$'\t' read -r owner owner_kind <<< "$parsed_claim"
          liveness="$(_api_owner_liveness "$owner")"
        else
          claim_state=invalid
          claim_fp="regular:$claim_fp"
        fi
      else
        read_rc=$?
        if [ "$read_rc" -eq 2 ]; then
          claim_state=invalid
          claim_fp="regular:$claim_fp"
        else
          _api_owner_fail "$team" "$agent" read_failed 1
          exit 1
        fi
      fi
    fi
  fi

  _api_owner_wait_barrier || {
    _api_owner_fail "$team" "$agent" read_failed 1
    exit 1
  }

  if ! _api_owner_config_stable "$config" "$config_state" "$config_fp" "$config_snapshot" \
    "$team_dir" "$team_dir_state" "$team_dir_fp" \
    || ! _api_owner_claim_stable "$lock" "$claim_state" "$claim_fp" "$claim_snapshot" \
    "$run_dir" "$run_dir_state" "$run_dir_fp"; then
    _api_owner_emit "$team" "$agent" unknown concurrent_change '' '' '' unknown
    exit 1
  fi

  case "$claim_state" in
    absent)
      _api_owner_emit "$team" "$agent" absent claim_absent '' '' '' observed
      exit 0
      ;;
    invalid)
      _api_owner_emit "$team" "$agent" unknown claim_invalid '' '' '' unknown
      exit 1
      ;;
    present)
      case "$liveness" in
        alive)
          _api_owner_emit "$team" "$agent" owned '' "$owner" "$owner_kind" alive observed
          exit 0
          ;;
        dead)
          _api_owner_emit "$team" "$agent" stale owner_dead "$owner" "$owner_kind" dead observed
          exit 0
          ;;
        *)
          _api_owner_emit "$team" "$agent" unknown liveness_unavailable '' '' unknown unknown
          exit 1
          ;;
      esac
      ;;
    *)
      _api_owner_fail "$team" "$agent" read_failed 1
      exit 1
      ;;
  esac
)

get_actas_owner() {
  local team="$1" agent="${2:-}" schema='' schema_seen=0
  shift
  [ -n "$agent" ] || {
    _api_owner_cli_fail "$team" '' invalid_argument
    return 2
  }
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --schema-version)
        [ "$schema_seen" -eq 0 ] && [ "$#" -ge 2 ] || {
          _api_owner_cli_fail "$team" "$agent" invalid_argument
          return 2
        }
        schema="$2"
        schema_seen=1
        shift 2
        ;;
      *)
        _api_owner_cli_fail "$team" "$agent" invalid_argument
        return 2
        ;;
    esac
  done
  [ "$schema_seen" -eq 1 ] || {
    _api_owner_cli_fail "$team" "$agent" invalid_argument
    return 2
  }
  [ "$schema" = 1 ] || {
    _api_owner_cli_fail "$team" "$agent" unsupported_schema_version
    return 2
  }
  if ! agmsg_validate_agent_name "$agent" >/dev/null 2>&1; then
    _api_owner_cli_fail "$team" "$agent" invalid_argument
    return 2
  fi
  _api_owner_query "$team" "$agent"
}
