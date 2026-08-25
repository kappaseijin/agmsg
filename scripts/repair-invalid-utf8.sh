#!/usr/bin/env bash
set -euo pipefail

# Explicit operator tool for repairing invalid UTF-8 already persisted in the
# SQLite message store. Read/display paths do not call this command.

export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/utf8.sh"

usage() {
  printf '%s\n' \
    "Usage: repair-invalid-utf8.sh --check <team>" \
    "       repair-invalid-utf8.sh --apply <team> --backup <path>" >&2
}

MODE=""
TEAM=""
BACKUP=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)
      [ -z "$MODE" ] || { usage; exit 2; }
      MODE=check
      shift
      [ "$#" -gt 0 ] || { usage; exit 2; }
      TEAM="$1"
      shift
      ;;
    --apply)
      [ -z "$MODE" ] || { usage; exit 2; }
      MODE=apply
      shift
      [ "$#" -gt 0 ] || { usage; exit 2; }
      TEAM="$1"
      shift
      ;;
    --backup)
      [ -z "$BACKUP" ] || { usage; exit 2; }
      shift
      [ "$#" -gt 0 ] || { usage; exit 2; }
      BACKUP="$1"
      shift
      ;;
    -h|--help)
      usage >&1
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ -z "$MODE" ] || [ -z "$TEAM" ]; then
  usage
  exit 2
fi
if [ "$MODE" = check ] && [ -n "$BACKUP" ]; then
  usage
  exit 2
fi
if [ "$MODE" = apply ] && [ -z "$BACKUP" ]; then
  usage
  exit 2
fi

if [ "$(agmsg_storage_driver)" != sqlite ]; then
  printf 'status=unsupported_storage\n'
  exit 2
fi

DB="$(agmsg_db_path "$TEAM")" || {
  printf 'status=invalid_team\n'
  exit 2
}
if [ ! -f "$DB" ]; then
  printf 'status=missing_database\n'
  exit 1
fi

sql_literal() {
  local quote="'"
  printf '%s' "${1//$quote/$quote$quote}"
}

TEAM_SQL="$(sql_literal "$TEAM")"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-repair.XXXXXX")"
SCAN_QUERY="$TMP_ROOT/scan.sql"
SCAN_ROWS="$TMP_ROOT/scan.rows"
REPAIR_ROWS="$TMP_ROOT/repair.rows"
APPLY_SQL="$TMP_ROOT/apply.sql"
trap 'rm -rf "$TMP_ROOT"' EXIT HUP INT TERM

TAB="$(printf '\t')"

# The query returns only table/key/field/hex columns for the selected team. In
# particular, no raw SQLite TEXT value crosses the shell boundary. The shared
# partition stores multiple teams in this same file, so every table branch must
# retain the team predicate.
printf '%s\n' \
  "SELECT table_name || char(9) || row_key || char(9) || field_name || char(9) || value_hex" \
  "  FROM (" \
  "    SELECT 'events' AS table_name, CAST(seq AS TEXT) AS row_key, 1 AS field_order, 'id' AS field_name, COALESCE(hex(id),'') AS value_hex" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'events', CAST(seq AS TEXT), 2, 'team', COALESCE(hex(team),'')" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'events', CAST(seq AS TEXT), 3, 'from_agent', COALESCE(hex(from_agent),'')" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'events', CAST(seq AS TEXT), 4, 'to_agent', COALESCE(hex(to_agent),'')" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'events', CAST(seq AS TEXT), 5, 'body', COALESCE(hex(body),'')" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'events', CAST(seq AS TEXT), 6, 'at', COALESCE(hex(at),'')" \
  "      FROM events WHERE type='message_sent' AND team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'messages', CAST(id AS TEXT), 1, 'team', COALESCE(hex(team),'')" \
  "      FROM messages WHERE team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'messages', CAST(id AS TEXT), 2, 'from_agent', COALESCE(hex(from_agent),'')" \
  "      FROM messages WHERE team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'messages', CAST(id AS TEXT), 3, 'to_agent', COALESCE(hex(to_agent),'')" \
  "      FROM messages WHERE team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'messages', CAST(id AS TEXT), 4, 'body', COALESCE(hex(body),'')" \
  "      FROM messages WHERE team='$TEAM_SQL'" \
  "    UNION ALL" \
  "    SELECT 'messages', CAST(id AS TEXT), 5, 'created_at', COALESCE(hex(created_at),'')" \
  "      FROM messages WHERE team='$TEAM_SQL'" \
  "  )" \
  " ORDER BY table_name, CAST(row_key AS INTEGER), field_order;" > "$SCAN_QUERY"

repairable_count=0
unsupported_count=0

report_summary() {
  printf 'repairable_count=%s unsupported_count=%s\n' "$repairable_count" "$unsupported_count"
}

scan_database() {
  repairable_count=0
  unsupported_count=0
  : > "$REPAIR_ROWS"

  if ! {
    set -o pipefail
    agmsg_sqlite -batch -noheader -separator "$TAB" "$DB" < "$SCAN_QUERY" |
      LC_ALL=C tr -d '\r'
  } > "$SCAN_ROWS"; then
    printf 'status=scan_failed\n'
    return 1
  fi

  while IFS="$TAB" read -r table_name row_key field_name old_hex; do
    [ -n "$table_name" ] || continue
    local sanitized
    if ! sanitized="$(agmsg_sanitize_utf8_hex "$old_hex")"; then
      printf 'status=scan_failed\n'
      return 1
    fi
    [ "$sanitized" != "$old_hex" ] || continue

    if [ "$field_name" = body ]; then
      printf '%s\t%s\t%s\t%s\n' "$table_name" "$row_key" "$old_hex" "$sanitized" >> "$REPAIR_ROWS"
      repairable_count=$((repairable_count + 1))
      printf 'table=%s key=%s field=%s status=repairable\n' "$table_name" "$row_key" "$field_name"
    else
      unsupported_count=$((unsupported_count + 1))
      printf 'table=%s key=%s field=%s status=unsupported_corruption\n' \
        "$table_name" "$row_key" "$field_name"
    fi
  done < "$SCAN_ROWS"
  return 0
}

integrity_ok() {
  local path="$1" result
  if ! result="$(
    set -o pipefail
    agmsg_sqlite -batch -noheader "$path" 'PRAGMA integrity_check;' | LC_ALL=C tr -d '\r'
  )"; then
    return 1
  fi
  [ "$result" = ok ]
}

prepare_backup_path() {
  local backup_dir db_dir backup_name db_name
  case "$BACKUP" in
    *"'"*|*$'\n'*|*$'\t'*)
      printf 'status=invalid_backup_path\n'
      return 1
      ;;
  esac
  backup_dir="$(dirname "$BACKUP")"
  backup_dir="$(cd "$backup_dir" 2>/dev/null && pwd -P)" || {
    printf 'status=invalid_backup_path\n'
    return 1
  }
  backup_name="$(basename "$BACKUP")"
  BACKUP="$backup_dir/$backup_name"
  if [ -e "$BACKUP" ] || [ -L "$BACKUP" ]; then
    printf 'status=backup_exists\n'
    return 1
  fi
  db_dir="$(cd "$(dirname "$DB")" 2>/dev/null && pwd -P)" || return 1
  db_name="$(basename "$DB")"
  if [ "$BACKUP" = "$db_dir/$db_name" ]; then
    printf 'status=backup_is_source\n'
    return 1
  fi
}

create_backup() {
  if ! agmsg_sqlite "$DB" ".backup '$BACKUP'" >/dev/null 2>&1; then
    rm -f "$BACKUP"
    printf 'status=backup_failed\n'
    return 1
  fi
  if [ ! -f "$BACKUP" ] || ! integrity_ok "$BACKUP"; then
    rm -f "$BACKUP"
    printf 'status=backup_integrity_failed\n'
    return 1
  fi
}

apply_repairs() {
  printf '%s\n' \
    'CREATE TEMP TABLE _agmsg_repair_guard (ok INTEGER NOT NULL CHECK(ok=1));' \
    'BEGIN IMMEDIATE;' > "$APPLY_SQL"
  while IFS="$TAB" read -r table_name row_key old_hex new_hex; do
    [ -n "$table_name" ] || continue
    case "$table_name" in events|messages) ;; *) printf 'status=apply_input_failed\n'; return 1 ;; esac
    case "$row_key" in ''|*[!0-9]*) printf 'status=apply_input_failed\n'; return 1 ;; esac
    case "$old_hex$new_hex" in *[!0123456789ABCDEF]*) printf 'status=apply_input_failed\n'; return 1 ;; esac
    if [ "$table_name" = events ]; then
      printf "UPDATE events SET body=CAST(X'%s' AS TEXT) WHERE seq=%s AND hex(body)='%s';\n" \
        "$new_hex" "$row_key" "$old_hex" >> "$APPLY_SQL"
    else
      printf "UPDATE messages SET body=CAST(X'%s' AS TEXT) WHERE id=%s AND hex(body)='%s';\n" \
        "$new_hex" "$row_key" "$old_hex" >> "$APPLY_SQL"
    fi
    printf 'INSERT INTO _agmsg_repair_guard(ok) VALUES (changes());\n' >> "$APPLY_SQL"
  done < "$REPAIR_ROWS"
  printf '%s\n' 'COMMIT;' >> "$APPLY_SQL"

  # A guard mismatch violates the CHECK constraint before COMMIT. sqlite3 exits
  # with -bail and closing the connection rolls the transaction back.
  if ! agmsg_sqlite -batch -bail "$DB" < "$APPLY_SQL" >/dev/null 2>&1; then
    printf 'status=apply_failed\n'
    return 1
  fi
}

scan_database || exit 1
if [ "$MODE" = check ]; then
  report_summary
  printf 'status=scan_complete\n'
  exit 0
fi

if [ "$unsupported_count" -gt 0 ]; then
  report_summary
  printf 'status=unsupported_corruption\n'
  exit 1
fi
if ! integrity_ok "$DB"; then
  printf 'status=integrity_failed\n'
  exit 1
fi
if [ "$repairable_count" -eq 0 ]; then
  report_summary
  printf 'status=no_changes\n'
  exit 0
fi

initial_repairable_count="$repairable_count"
initial_unsupported_count="$unsupported_count"
prepare_backup_path || exit 1
create_backup || exit 1
apply_repairs || exit 1

if ! integrity_ok "$DB"; then
  printf 'status=post_integrity_failed\n'
  exit 1
fi

# Re-scan after commit. This is read-only and also ensures that the exact bytes
# found before the backup are no longer present as invalid body data.
scan_database || exit 1
if [ "$repairable_count" -ne 0 ] || [ "$unsupported_count" -ne 0 ]; then
  report_summary
  printf 'status=post_scan_failed\n'
  exit 1
fi

printf 'repairable_count=%s unsupported_count=%s\n' \
  "$initial_repairable_count" "$initial_unsupported_count"
printf 'status=applied\n'
