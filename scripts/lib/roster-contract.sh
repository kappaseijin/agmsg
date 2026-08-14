#!/usr/bin/env bash
# roster-contract.sh — versioned, machine-readable team roster contract.
#
# Requires storage.sh to be sourced first for agmsg_sql_readfile_path,
# agmsg_sqlite_mem, and agmsg_project_sql_in_list. Human-oriented commands
# retain their legacy reader; callers use this helper only for --format json.

[ -n "${_AGMSG_ROSTER_CONTRACT_SH:-}" ] && return 0
_AGMSG_ROSTER_CONTRACT_SH=1

agmsg_roster_contract_schema_error() {
  printf 'schema error: %s\n' "$1" >&2
  return 2
}

# Validate the part of a team config exposed through the versioned roster
# contract. An optional second argument requires the config's name to equal
# the requested team path segment.
agmsg_roster_contract_validate() {
  local config="$1" expected_team="${2:-}" config_sql expected_team_sql error
  config_sql=$(agmsg_sql_readfile_path "$config")
  expected_team_sql=$(printf '%s' "$expected_team" | sed "s/'/''/g")

  error=$(agmsg_sqlite_mem "
    WITH raw(json) AS (
      SELECT CAST(readfile('$config_sql') AS TEXT)
    ),
    cfg(json) AS (
      SELECT json FROM raw WHERE json_valid(json)
    ),
    agents(name, member) AS (
      SELECT a.key, a.value
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents')) AS a
      WHERE json_type(cfg.json, '\$.agents') = 'object'
    ),
    registrations(name, idx, registration) AS (
      SELECT agents.name, r.key, r.value
      FROM agents, json_each(json_extract(agents.member, '\$.registrations')) AS r
      WHERE json_type(agents.member, '\$.registrations') = 'array'
    ),
    errors(priority, message) AS (
      SELECT 1, 'config is not valid JSON'
      WHERE NOT EXISTS (SELECT 1 FROM cfg)
      UNION ALL
      SELECT 2, 'schemaVersion must be integer 1'
      FROM cfg
      WHERE COALESCE(json_type(cfg.json, '\$.schemaVersion'), '') != 'integer'
        OR json_extract(cfg.json, '\$.schemaVersion') != 1
      UNION ALL
      SELECT 3, 'name must be a non-empty string'
      FROM cfg
      WHERE COALESCE(json_type(cfg.json, '\$.name'), '') != 'text'
        OR length(COALESCE(json_extract(cfg.json, '\$.name'), '')) = 0
      UNION ALL
      SELECT 4, 'config name does not match requested team'
      FROM cfg
      WHERE '$expected_team_sql' != ''
        AND json_extract(cfg.json, '\$.name') != '$expected_team_sql'
      UNION ALL
      SELECT 5, 'agents must be an object'
      FROM cfg
      WHERE COALESCE(json_type(cfg.json, '\$.agents'), '') != 'object'
      UNION ALL
      SELECT 6, 'member name must not be empty'
      FROM agents
      WHERE length(name) = 0
      UNION ALL
      SELECT 7, 'member kind must be seat, human, or service'
      FROM agents
      WHERE COALESCE(json_type(member, '\$.kind'), '') != 'text'
        OR COALESCE(json_extract(member, '\$.kind'), '') NOT IN ('seat', 'human', 'service')
      UNION ALL
      SELECT 8, 'member role must be a non-empty string'
      FROM agents
      WHERE COALESCE(json_type(member, '\$.role'), '') != 'text'
        OR length(COALESCE(json_extract(member, '\$.role'), '')) = 0
      UNION ALL
      SELECT 9, 'member registrations must be an array'
      FROM agents
      WHERE COALESCE(json_type(member, '\$.registrations'), '') != 'array'
      UNION ALL
      SELECT 10, 'registration type must be a non-empty string'
      FROM registrations
      WHERE COALESCE(json_type(registration, '\$.type'), '') != 'text'
        OR length(COALESCE(json_extract(registration, '\$.type'), '')) = 0
      UNION ALL
      SELECT 11, 'registration project must be a non-empty string'
      FROM registrations
      WHERE COALESCE(json_type(registration, '\$.project'), '') != 'text'
        OR length(COALESCE(json_extract(registration, '\$.project'), '')) = 0
    )
    SELECT message
    FROM errors
    ORDER BY priority
    LIMIT 1;
  ") || return 2

  [ -z "$error" ] || agmsg_roster_contract_schema_error "$error"
}

# Emit the complete roster JSON for one valid team config. Member order is
# explicitly stable; registration array order is the stored config order.
agmsg_roster_contract_team_json() {
  local config="$1" team="$2" config_sql
  agmsg_roster_contract_validate "$config" "$team" || return $?
  config_sql=$(agmsg_sql_readfile_path "$config")

  agmsg_sqlite_mem "
    WITH raw(json) AS (
      SELECT CAST(readfile('$config_sql') AS TEXT)
    ),
    cfg(json) AS (
      SELECT json FROM raw
    ),
    members(name, kind, role, registrations) AS (
      SELECT
        a.key,
        json_extract(a.value, '\$.kind'),
        json_extract(a.value, '\$.role'),
        json_extract(a.value, '\$.registrations')
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents')) AS a
    ),
    member_objects(name, member_json) AS (
      SELECT
        name,
        json_object(
          'name', name,
          'kind', kind,
          'role', role,
          'registrations', json(registrations)
        )
      FROM members
    )
    SELECT json_object(
      'schemaVersion', 1,
      'team', json_extract(cfg.json, '\$.name'),
      'members', json(COALESCE((
        SELECT json_group_array(json(member_json))
        FROM (
          SELECT member_json
          FROM member_objects
          ORDER BY name COLLATE BINARY
        )
      ), '[]'))
    )
    FROM cfg;
  "
}

# Return success if this config contains an exact project/runtime registration.
# This compatibility probe deliberately understands legacy registrations so the
# caller can fail closed when a matching config lacks the JSON contract.
agmsg_roster_contract_has_registration() {
  local config="$1" project="$2" runtime="$3" config_sql runtime_sql project_sql_in match
  config_sql=$(agmsg_sql_readfile_path "$config")
  runtime_sql=$(printf '%s' "$runtime" | sed "s/'/''/g")
  project_sql_in=$(agmsg_project_sql_in_list "$project")

  match=$(agmsg_sqlite_mem "
    WITH raw(json) AS (
      SELECT CAST(readfile('$config_sql') AS TEXT)
    ),
    cfg(json) AS (
      SELECT json FROM raw WHERE json_valid(json)
    ),
    agents(name, registrations) AS (
      SELECT
        a.key,
        CASE
          WHEN json_type(a.value, '\$.registrations') = 'array'
            THEN json_extract(a.value, '\$.registrations')
          ELSE json_array(json_object(
            'type', json_extract(a.value, '\$.type'),
            'project', json_extract(a.value, '\$.project')
          ))
        END
      FROM cfg, json_each(json_extract(cfg.json, '\$.agents')) AS a
      WHERE json_type(cfg.json, '\$.agents') = 'object'
    )
    SELECT EXISTS(
      SELECT 1
      FROM agents, json_each(agents.registrations) AS r
      WHERE json_extract(r.value, '\$.type') = '$runtime_sql'
        AND json_extract(r.value, '\$.project') IN ($project_sql_in)
    );
  ") || return 1

  [ "$match" = "1" ]
}

# Emit one JSON object per exact registration from a valid versioned config.
# Callers can aggregate the JSONL rows into their own response envelope.
agmsg_roster_contract_matching_json() {
  local config="$1" project="$2" runtime="$3" config_sql runtime_sql project_sql_in
  agmsg_roster_contract_validate "$config" || return $?
  config_sql=$(agmsg_sql_readfile_path "$config")
  runtime_sql=$(printf '%s' "$runtime" | sed "s/'/''/g")
  project_sql_in=$(agmsg_project_sql_in_list "$project")

  agmsg_sqlite_mem "
    WITH raw(json) AS (
      SELECT CAST(readfile('$config_sql') AS TEXT)
    ),
    cfg(json) AS (
      SELECT json FROM raw
    )
    SELECT json_object(
      'team', json_extract(cfg.json, '\$.name'),
      'name', a.key,
      'kind', json_extract(a.value, '\$.kind'),
      'role', json_extract(a.value, '\$.role'),
      'registration', json(r.value)
    )
    FROM cfg,
         json_each(json_extract(cfg.json, '\$.agents')) AS a,
         json_each(json_extract(a.value, '\$.registrations')) AS r
    WHERE json_extract(r.value, '\$.type') = '$runtime_sql'
      AND json_extract(r.value, '\$.project') IN ($project_sql_in)
    ORDER BY a.key COLLATE BINARY, CAST(r.key AS INTEGER);
  "
}
