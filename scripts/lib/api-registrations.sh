#!/usr/bin/env bash
# Read-only implementation of api.sh's versioned registrations resource.
#
# This helper deliberately reads one team's source only. It snapshots and
# validates the complete source before emitting anything, then checks the
# source identity and bytes again before returning a successful envelope.

[ -n "${_AGMSG_API_REGISTRATIONS_SH:-}" ] && return 0
_AGMSG_API_REGISTRATIONS_SH=1

_api_registrations_emit() {
  local team="$1" status="$2" reason="$3" complete="$4" registrations="${5:-}"
  local team_sql status_sql reason_sql complete_expr registrations_expr
  team_sql="$(_agmsg_sqlesc "$team")"
  status_sql="$(_agmsg_sqlesc "$status")"
  if [ -n "$reason" ]; then
    reason_sql="'$(_agmsg_sqlesc "$reason")'"
  else
    reason_sql='NULL'
  fi
  if [ "$complete" = true ]; then
    complete_expr="json('true')"
  else
    complete_expr="json('false')"
  fi
  if [ "$registrations" = null ] || [ -z "$registrations" ]; then
    registrations_expr='NULL'
  else
    registrations_expr="json('$(_agmsg_sqlesc "$registrations")')"
  fi
  agmsg_sqlite_mem "SELECT json_object(
    'schemaVersion', 1,
    'resource', 'registrations',
    'team', '$team_sql',
    'status', '$status_sql',
    'reason', $reason_sql,
    'complete', $complete_expr,
    'registrations', $registrations_expr
  );"
}

_api_registrations_fail() {
  local team="$1" status="$2" reason="$3" exit_code="$4"
  _api_registrations_emit "$team" "$status" "$reason" false null
  printf 'api: registrations: status=%s reason=%s\n' "$status" "$reason" >&2
  return "$exit_code"
}

_api_registrations_fingerprint() {
  case "$(uname -s 2>/dev/null || printf '%s' unknown)" in
    Darwin*) stat -f '%d:%i:%z:%m' "$1" 2>/dev/null ;;
    *)       stat -c '%d:%i:%s:%Y' "$1" 2>/dev/null ;;
  esac
}

_api_registrations_validate_snapshot() {
  local snapshot="$1" team="$2" source_sql team_sql error
  source_sql="$(agmsg_sql_readfile_path "$snapshot")"
  team_sql="$(_agmsg_sqlesc "$team")"
  if ! error="$(sqlite3 -batch -noheader :memory: "
    WITH raw(json) AS (
      SELECT CAST(readfile('$source_sql') AS TEXT)
    ),
    cfg(json) AS (
      SELECT json
      FROM raw
      WHERE json_valid(json) AND json_type(json) = 'object'
    ),
    root_keys(key) AS (
      SELECT key
      FROM cfg, json_each(cfg.json)
    ),
    agents(name, member) AS (
      SELECT a.key, a.value
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents')) AS a
      WHERE json_type(cfg.json, '\$.agents') = 'object'
    ),
    member_keys(name, key) AS (
      SELECT agents.name, k.key
      FROM agents, json_each(agents.member) AS k
    ),
    registrations(name, idx, registration) AS (
      SELECT agents.name, r.key, r.value
      FROM agents, json_each(json_extract(agents.member, '\$.registrations')) AS r
      WHERE json_type(agents.member, '\$.registrations') = 'array'
    ),
    errors(priority, reason) AS (
      SELECT 1, 'data_invalid'
      WHERE NOT EXISTS (SELECT 1 FROM cfg)
      UNION ALL
      SELECT 2, 'storage_schema_unsupported'
      FROM cfg
      WHERE EXISTS (SELECT 1 FROM root_keys WHERE key = 'schemaVersion')
        AND (COALESCE(json_type(cfg.json, '\$.schemaVersion'), '') != 'integer'
          OR json_extract(cfg.json, '\$.schemaVersion') != 1)
      UNION ALL
      SELECT 3, 'data_invalid'
      FROM cfg
      WHERE EXISTS (SELECT 1 FROM root_keys WHERE key = 'name')
        AND (COALESCE(json_type(cfg.json, '\$.name'), '') != 'text'
          OR length(COALESCE(json_extract(cfg.json, '\$.name'), '')) = 0
          OR json_extract(cfg.json, '\$.name') != '$team_sql')
      UNION ALL
      SELECT 4, 'data_invalid'
      FROM cfg
      WHERE COALESCE(json_type(cfg.json, '\$.agents'), '') != 'object'
    UNION ALL
      SELECT 5, 'data_invalid'
      FROM agents
      WHERE json_type(member) != 'object'
      UNION ALL
      SELECT 5, 'data_invalid'
      FROM agents
      WHERE length(name) = 0
        OR name IN ('.', '..')
        OR substr(name, 1, 1) = '-'
        OR instr(name, '.') > 0
        OR instr(name, '/') > 0
        OR instr(name, char(92)) > 0
        OR instr(name, char(34)) > 0
        OR instr(name, '[') > 0
        OR instr(name, ']') > 0
      UNION ALL
      SELECT 6, 'data_invalid'
      FROM agents
      WHERE EXISTS (SELECT 1 FROM member_keys WHERE name = agents.name AND key = 'registrations')
        AND COALESCE(json_type(member, '\$.registrations'), '') != 'array'
      UNION ALL
      SELECT 7, 'data_invalid'
      FROM agents
      WHERE NOT EXISTS (SELECT 1 FROM member_keys WHERE name = agents.name AND key = 'registrations')
        AND (
          EXISTS (SELECT 1 FROM member_keys WHERE name = agents.name AND key IN ('type', 'project'))
          AND NOT (
            json_type(member, '\$.type') = 'text'
            AND length(json_extract(member, '\$.type')) > 0
            AND json_type(member, '\$.project') = 'text'
            AND length(json_extract(member, '\$.project')) > 0
          )
        )
      UNION ALL
      SELECT 8, 'data_invalid'
      FROM registrations
      WHERE json_type(registration) != 'object'
      UNION ALL
      SELECT 9, 'data_invalid'
      FROM registrations
      WHERE COALESCE(json_type(registration, '\$.type'), '') != 'text'
        OR length(COALESCE(json_extract(registration, '\$.type'), '')) = 0
      UNION ALL
      SELECT 10, 'data_invalid'
      FROM registrations
      WHERE COALESCE(json_type(registration, '\$.project'), '') != 'text'
        OR length(COALESCE(json_extract(registration, '\$.project'), '')) = 0
    )
    SELECT COALESCE((SELECT reason FROM errors ORDER BY priority LIMIT 1), 'ok');
  " 2>/dev/null)"; then
    printf '%s\n' data_invalid
    return 0
  fi
  case "$error" in
    ok|data_invalid|storage_schema_unsupported) printf '%s\n' "$error" ;;
    *) printf '%s\n' data_invalid ;;
  esac
}

_api_registrations_write_rows() {
  local snapshot="$1" rows="$2" source_sql
  source_sql="$(agmsg_sql_readfile_path "$snapshot")"
  if ! sqlite3 -batch -noheader :memory: "
    WITH cfg(json) AS (
      SELECT CAST(readfile('$source_sql') AS TEXT)
    ),
    agents(name, member) AS (
      SELECT a.key, a.value
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents')) AS a
    ),
    normalized(name, registrations) AS (
      SELECT
        name,
        CASE
          WHEN json_type(member, '\$.registrations') = 'array'
            THEN json_extract(member, '\$.registrations')
          WHEN json_type(member, '\$.type') = 'text'
            AND length(json_extract(member, '\$.type')) > 0
            AND json_type(member, '\$.project') = 'text'
            AND length(json_extract(member, '\$.project')) > 0
            THEN json_array(json_object(
              'type', json_extract(member, '\$.type'),
              'project', json_extract(member, '\$.project')
            ))
          ELSE json('[]')
        END
      FROM agents
    )
    SELECT json_object(
      'agent', name,
      'type', json_extract(r.value, '\$.type'),
      'project', json_extract(r.value, '\$.project')
    )
    FROM normalized, json_each(normalized.registrations) AS r
    ORDER BY name COLLATE BINARY,
             json_extract(r.value, '\$.type') COLLATE BINARY,
             json_extract(r.value, '\$.project') COLLATE BINARY;
  " >"$rows" 2>/dev/null; then
    return 1
  fi
  local clean_rows="${rows}.clean"
  if ! tr -d '\r' <"$rows" >"$clean_rows"; then
    return 1
  fi
  mv "$clean_rows" "$rows"
}

_api_registrations_json_field() {
  local object="$1" field="$2" object_sql value
  object_sql="$(_agmsg_sqlesc "$object")"
  if ! value="$(sqlite3 -batch -noheader :memory: \
    "SELECT json_extract('$object_sql', '\$.$field');" 2>/dev/null | tr -d '\r')"; then
    return 1
  fi
  printf '%s' "$value"
}

_api_registrations_test_barrier() {
  local barrier="${AGMSG_TEST_API_REGISTRATIONS_BARRIER:-}"
  [ -n "$barrier" ] || return 0
  : >"$barrier.reached" || return 1
  while [ ! -e "$barrier.release" ]; do
    sleep 0.01 || return 1
  done
}

_api_registrations_query() (
  local team="$1" team_dir config snapshot rows before_fp snapshot_fp final_fp
  local validation row agent type project physical canonical record records_json='[' separator=''
  team_dir="$SCRIPT_DIR/../teams/$team"
  config="$team_dir/config.json"

  if [ ! -d "$team_dir" ] || [ ! -e "$config" ]; then
    _api_registrations_fail "$team" not_found team_not_found 1
    exit $?
  fi
  if [ ! -f "$config" ]; then
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  fi

  snapshot="$(mktemp "${TMPDIR:-/tmp}/agmsg-api-registrations.XXXXXX" 2>/dev/null)" || {
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  }
  rows="$(mktemp "${TMPDIR:-/tmp}/agmsg-api-registrations-rows.XXXXXX" 2>/dev/null)" || {
    rm -f "$snapshot"
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  }
  chmod 600 "$snapshot" "$rows" 2>/dev/null || :
  trap 'rm -f "$snapshot" "$rows" "${rows}.clean"' EXIT INT TERM

  before_fp="$(_api_registrations_fingerprint "$config")" || {
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  }
  if ! cat "$config" >"$snapshot"; then
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  fi
  snapshot_fp="$(_api_registrations_fingerprint "$config")" || {
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  }
  if [ "$before_fp" != "$snapshot_fp" ] || ! cmp -s "$snapshot" "$config"; then
    _api_registrations_fail "$team" unknown concurrent_change 1
    exit $?
  fi

  validation="$(_api_registrations_validate_snapshot "$snapshot" "$team")"
  case "$validation" in
    ok) ;;
    storage_schema_unsupported)
      _api_registrations_fail "$team" unknown storage_schema_unsupported 1
      exit $?
      ;;
    *)
      _api_registrations_fail "$team" unknown data_invalid 1
      exit $?
      ;;
  esac

  if ! _api_registrations_write_rows "$snapshot" "$rows"; then
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  fi

  while IFS= read -r row || [ -n "$row" ]; do
    [ -n "$row" ] || continue
    agent="$(_api_registrations_json_field "$row" agent)" || {
      _api_registrations_fail "$team" unknown read_failed 1
      exit $?
    }
    if ! agmsg_validate_agent_name "$agent" >/dev/null 2>&1; then
      _api_registrations_fail "$team" unknown data_invalid 1
      exit $?
    fi
    type="$(_api_registrations_json_field "$row" type)" || {
      _api_registrations_fail "$team" unknown read_failed 1
      exit $?
    }
    project="$(_api_registrations_json_field "$row" project)" || {
      _api_registrations_fail "$team" unknown read_failed 1
      exit $?
    }
    physical="$(agmsg_canonical_path "$project")" || {
      _api_registrations_fail "$team" unknown project_unresolvable 1
      exit $?
    }
    canonical="$(agmsg_normalize_project_path "$physical")" || {
      _api_registrations_fail "$team" unknown project_unresolvable 1
      exit $?
    }
    record="$(agmsg_sqlite_mem "SELECT json_object(
      'agent', '$(_agmsg_sqlesc "$agent")',
      'type', '$(_agmsg_sqlesc "$type")',
      'project', '$(_agmsg_sqlesc "$project")',
      'canonicalProject', '$(_agmsg_sqlesc "$canonical")'
    );")" || {
      _api_registrations_fail "$team" unknown read_failed 1
      exit $?
    }
    records_json="${records_json}${separator}${record}"
    separator=','
  done <"$rows"
  records_json="${records_json}]"

  if ! _api_registrations_test_barrier; then
    _api_registrations_fail "$team" unknown read_failed 1
    exit $?
  fi
  final_fp="$(_api_registrations_fingerprint "$config")" || {
    _api_registrations_fail "$team" unknown concurrent_change 1
    exit $?
  }
  if [ "$before_fp" != "$final_fp" ] || ! cmp -s "$snapshot" "$config"; then
    _api_registrations_fail "$team" unknown concurrent_change 1
    exit $?
  fi

  _api_registrations_emit "$team" ok '' true "$records_json"
  exit 0
)

get_registrations() {
  local team="$1" schema_version='' option
  shift
  while [ "$#" -gt 0 ]; do
    option="$1"
    case "$option" in
      --schema-version)
        if [ "$#" -lt 2 ] || [ -n "$schema_version" ]; then
          _api_registrations_fail "$team" error invalid_argument 2
          return $?
        fi
        schema_version="$2"
        shift 2
        ;;
      *)
        _api_registrations_fail "$team" error invalid_argument 2
        return $?
        ;;
    esac
  done
  if [ -z "$schema_version" ]; then
    _api_registrations_fail "$team" error invalid_argument 2
    return $?
  fi
  if [ "$schema_version" != 1 ]; then
    _api_registrations_fail "$team" error unsupported_schema_version 2
    return $?
  fi
  _api_registrations_query "$team"
}
