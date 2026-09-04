#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/storage.sh"

DB="$(_agmsg_runtime_db_path)"
DB_DIR="$(dirname "$DB")"
mkdir -p "$DB_DIR"

table_state="$(agmsg_sqlite "$DB" "SELECT (SELECT count(*) FROM sqlite_master WHERE type='table' AND name='team_work_dispatch_current') || '|' || (SELECT count(*) FROM sqlite_master WHERE type='table' AND name='team_work_dispatch_revisions');" | tr -d '\r')"
case "$table_state" in
  0\|0) exit 0 ;;
  1\|0|0\|1)
    echo "team-work dispatch migration requires both legacy tables" >&2
    exit 1
    ;;
  1\|1) ;;
  *)
    echo "team-work dispatch migration could not inspect the schema" >&2
    exit 1
    ;;
esac

column_state="$(agmsg_sqlite "$DB" "SELECT (SELECT count(*) FROM pragma_table_info('team_work_dispatch_current') WHERE name='recovery_evidence') || '|' || (SELECT count(*) FROM pragma_table_info('team_work_dispatch_revisions') WHERE name='recovery_evidence');" | tr -d '\r')"
[ "$column_state" = "1|1" ] && exit 0

schema_sql="$(<"$SCRIPT_DIR/team-work-dispatch-schema.sql")"
trigger_sql="$(<"$SCRIPT_DIR/team-work-dispatch-triggers.sql")"
copy_failure_sql=""
if [ "${AGMSG_TEAM_WORK_MIGRATION_FAIL_COPY:-0}" = "1" ]; then
  copy_failure_sql="INSERT INTO agmsg_dispatch_migration_copy_guard(value) VALUES (0);"
fi

agmsg_sqlite "$DB" <<SQL
.bail on
BEGIN IMMEDIATE;

CREATE TEMP TABLE agmsg_dispatch_migration_guard(value INTEGER NOT NULL CHECK(value = 1));
CREATE TEMP TABLE agmsg_dispatch_migration_copy_guard(value INTEGER NOT NULL CHECK(value = 1));

INSERT INTO agmsg_dispatch_migration_guard(value)
SELECT CASE WHEN NOT EXISTS (
  SELECT 1
  FROM team_work_dispatch_current
  WHERE state IS NULL
     OR state NOT IN ('dispatching', 'claimed')
     OR lease_epoch IS NULL
     OR lease_expires_at IS NULL
     OR queue_digest IS NULL
     OR delivery_evidence_json IS NULL
     OR json_valid(delivery_evidence_json) != 1
) THEN 1 ELSE 0 END;

INSERT INTO agmsg_dispatch_migration_guard(value)
SELECT CASE WHEN NOT EXISTS (
  SELECT 1
  FROM team_work_dispatch_revisions
  WHERE revision IS NULL
     OR revision < 1
     OR state IS NULL
     OR state NOT IN ('dispatching', 'claimed')
     OR lease_epoch IS NULL
     OR snapshot_json IS NULL
     OR json_valid(snapshot_json) != 1
     OR (revision = 1 AND previous_revision IS NOT NULL)
     OR (revision > 1 AND (previous_revision IS NULL OR previous_revision != revision - 1))
) THEN 1 ELSE 0 END;

INSERT INTO agmsg_dispatch_migration_guard(value)
SELECT CASE WHEN NOT EXISTS (
  SELECT 1
  FROM (
    SELECT team, work_item_id, MIN(revision) AS first_revision,
           MAX(revision) AS last_revision, COUNT(*) AS revision_count,
           SUM(CASE WHEN revision = 1 THEN 1 ELSE 0 END) AS first_count
    FROM team_work_dispatch_revisions
    GROUP BY team, work_item_id
  )
  WHERE first_revision != 1
     OR last_revision != revision_count
     OR first_count != 1
) THEN 1 ELSE 0 END;

INSERT INTO agmsg_dispatch_migration_guard(value)
SELECT CASE WHEN NOT EXISTS (
  SELECT 1
  FROM team_work_dispatch_current current_row
  WHERE NOT EXISTS (
    SELECT 1
    FROM team_work_dispatch_revisions revision_row
    WHERE revision_row.team = current_row.team
      AND revision_row.work_item_id = current_row.work_item_id
      AND revision_row.revision = (
        SELECT MAX(latest.revision)
        FROM team_work_dispatch_revisions latest
        WHERE latest.team = current_row.team
          AND latest.work_item_id = current_row.work_item_id
      )
      AND json_extract(revision_row.snapshot_json, '$.state') = current_row.state
      AND json_extract(revision_row.snapshot_json, '$.leaseEpoch') = current_row.lease_epoch
  )
) THEN 1 ELSE 0 END;

DROP TRIGGER IF EXISTS team_work_dispatch_current_history_insert;
DROP TRIGGER IF EXISTS team_work_dispatch_current_history_update;
DROP TRIGGER IF EXISTS team_work_dispatch_revisions_immutable_update;
DROP TRIGGER IF EXISTS team_work_dispatch_revisions_immutable_delete;
DROP INDEX IF EXISTS idx_team_work_dispatch_current_lease_expiry;

ALTER TABLE team_work_dispatch_current RENAME TO team_work_dispatch_current_legacy;
ALTER TABLE team_work_dispatch_revisions RENAME TO team_work_dispatch_revisions_legacy;

$schema_sql

$copy_failure_sql

INSERT INTO team_work_dispatch_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat, state,
  lease_epoch, lease_expires_at, queue_digest, delivery_evidence_json,
  ack_evidence, recovery_evidence, last_action, last_actor, created_at, updated_at
)
SELECT
  team, work_item_id, contract_digest, envelope_digest, owner_seat, state,
  lease_epoch, lease_expires_at, queue_digest, delivery_evidence_json,
  ack_evidence, NULL, last_action, last_actor, created_at, updated_at
FROM team_work_dispatch_current_legacy;

INSERT INTO team_work_dispatch_revisions(
  team, work_item_id, revision, previous_revision, action, actor, state,
  lease_epoch, recovery_evidence, snapshot_json, created_at
)
SELECT
  team, work_item_id, revision, previous_revision, action, actor, state,
  lease_epoch, NULL, snapshot_json, created_at
FROM team_work_dispatch_revisions_legacy;

$trigger_sql

DROP TABLE team_work_dispatch_current_legacy;
DROP TABLE team_work_dispatch_revisions_legacy;
DROP TABLE agmsg_dispatch_migration_copy_guard;
DROP TABLE agmsg_dispatch_migration_guard;
COMMIT;
SQL
