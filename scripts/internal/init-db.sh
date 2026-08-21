#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/storage.sh"
# The install-time bootstrap is not team-scoped: it runs before any team
# exists, and what it creates is the legacy `messages` table plus the store
# file itself. It therefore takes the non-team resolver, not a selector.
DB="$(_agmsg_runtime_db_path)"
DB_DIR="$(dirname "$DB")"
mkdir -p "$DB_DIR"

# Keep direct internal initialization safe for deployed stores too. The public
# storage facade runs the migration first and marks that successful handoff so
# the normal path does not spawn the migration process twice.
if [ "${AGMSG_DISPATCH_MIGRATION_DONE:-0}" != "1" ]; then
  migration_script="$SCRIPT_DIR/migrate-team-work-dispatch.sh"
  AGMSG_STORAGE_PATH="$(agmsg_storage_dir)" bash "$migration_script"
fi

# Idempotent and safe to run concurrently. When a leader fans a job out to N
# members against a fresh/override store (see #106), every send.sh races to
# initialize. Running unconditionally with IF NOT EXISTS (rather than guarding
# on the file's existence) means a process that sees the DB file but not yet
# its schema still ends up with a usable table. See #114.

# WAL is a persistent, one-time DB property and only an optimization. Changing
# the journal mode wants exclusive access, so a concurrent set on a brand-new
# DB can return "database is locked" even with a busy_timeout — set it
# best-effort; whichever initializer wins makes it stick for everyone.
agmsg_sqlite "$DB" "PRAGMA journal_mode=WAL;" >/dev/null 2>&1 || true

# Schema. IF NOT EXISTS + the busy_timeout from agmsg_sqlite make a concurrent
# first-time creation a no-op for the losers rather than an "already exists"
# abort.
agmsg_sqlite "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT NOT NULL,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_unread ON messages(team, to_agent, read_at) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_history ON messages(team, created_at DESC);

CREATE TABLE IF NOT EXISTS message_claims (
  message_id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_claims_expiry ON message_claims(expires_at);

CREATE TABLE IF NOT EXISTS message_receipts (
  message_id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  handed_off_at TEXT NOT NULL,
  evidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_work_current (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  contract_digest TEXT NOT NULL,
  envelope_digest TEXT NOT NULL,
  owner_seat TEXT NOT NULL,
  source_repository TEXT NOT NULL,
  source_number INTEGER NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  state TEXT NOT NULL CHECK (state IN ('claimed', 'acknowledged', 'in_progress', 'blocked', 'completed')),
  lease_owner TEXT,
  lease_expires_at INTEGER,
  ack_evidence TEXT,
  pr_links_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pr_links_json)),
  writebacks_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(writebacks_json)),
  last_action TEXT NOT NULL,
  last_actor TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id),
  CHECK (
    (lease_owner IS NULL AND lease_expires_at IS NULL) OR
    (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_team_work_current_lease_expiry
  ON team_work_current(lease_expires_at)
  WHERE lease_expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS team_work_revisions (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  previous_revision INTEGER,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id, revision)
);

CREATE TRIGGER IF NOT EXISTS team_work_current_history_insert
AFTER INSERT ON team_work_current
BEGIN
  INSERT INTO team_work_revisions(
    team, work_item_id, revision, previous_revision, action, actor, snapshot_json, created_at
  ) VALUES (
    NEW.team,
    NEW.work_item_id,
    NEW.revision,
    NULL,
    NEW.last_action,
    NEW.last_actor,
    json_object(
      'schemaVersion', 1,
      'team', NEW.team,
      'workItemId', NEW.work_item_id,
      'contractDigest', NEW.contract_digest,
      'envelopeDigest', NEW.envelope_digest,
      'ownerSeat', NEW.owner_seat,
      'source', json_object('repository', NEW.source_repository, 'number', NEW.source_number),
      'revision', NEW.revision,
      'state', NEW.state,
      'leaseOwner', NEW.lease_owner,
      'leaseExpiresAt', NEW.lease_expires_at,
      'ackEvidence', NEW.ack_evidence,
      'prLinks', json(NEW.pr_links_json),
      'writebacks', json(NEW.writebacks_json),
      'lastAction', NEW.last_action,
      'lastActor', NEW.last_actor,
      'createdAt', NEW.created_at,
      'updatedAt', NEW.updated_at
    ),
    NEW.updated_at
  );
END;

CREATE TRIGGER IF NOT EXISTS team_work_current_history_update
AFTER UPDATE ON team_work_current
BEGIN
  INSERT INTO team_work_revisions(
    team, work_item_id, revision, previous_revision, action, actor, snapshot_json, created_at
  ) VALUES (
    NEW.team,
    NEW.work_item_id,
    NEW.revision,
    OLD.revision,
    NEW.last_action,
    NEW.last_actor,
    json_object(
      'schemaVersion', 1,
      'team', NEW.team,
      'workItemId', NEW.work_item_id,
      'contractDigest', NEW.contract_digest,
      'envelopeDigest', NEW.envelope_digest,
      'ownerSeat', NEW.owner_seat,
      'source', json_object('repository', NEW.source_repository, 'number', NEW.source_number),
      'revision', NEW.revision,
      'state', NEW.state,
      'leaseOwner', NEW.lease_owner,
      'leaseExpiresAt', NEW.lease_expires_at,
      'ackEvidence', NEW.ack_evidence,
      'prLinks', json(NEW.pr_links_json),
      'writebacks', json(NEW.writebacks_json),
      'lastAction', NEW.last_action,
      'lastActor', NEW.last_actor,
      'createdAt', NEW.created_at,
      'updatedAt', NEW.updated_at
    ),
    NEW.updated_at
  );
END;

CREATE TRIGGER IF NOT EXISTS team_work_revisions_immutable_update
BEFORE UPDATE ON team_work_revisions
BEGIN
  SELECT RAISE(ABORT, 'team_work_revisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS team_work_revisions_immutable_delete
BEFORE DELETE ON team_work_revisions
BEGIN
  SELECT RAISE(ABORT, 'team_work_revisions is append-only');
END;

SQL

# Dispatch has its own schema lifecycle because deployed stores may still have
# the legacy CHECK constraint that excludes `abandoned`. Migration runs before
# this fresh-schema creation, so this shared definition is safe for both fresh
# and already-migrated stores.
agmsg_sqlite "$DB" < "$SCRIPT_DIR/team-work-dispatch-schema.sql"
agmsg_sqlite "$DB" < "$SCRIPT_DIR/team-work-dispatch-triggers.sql"
