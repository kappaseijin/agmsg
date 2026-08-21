CREATE TABLE IF NOT EXISTS team_work_dispatch_current (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  contract_digest TEXT NOT NULL,
  envelope_digest TEXT NOT NULL,
  owner_seat TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('dispatching', 'claimed', 'abandoned')),
  lease_epoch TEXT NOT NULL,
  lease_expires_at INTEGER NOT NULL,
  queue_digest TEXT NOT NULL,
  delivery_evidence_json TEXT NOT NULL CHECK (json_valid(delivery_evidence_json)),
  ack_evidence TEXT,
  recovery_evidence TEXT,
  last_action TEXT NOT NULL,
  last_actor TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id)
);

CREATE INDEX IF NOT EXISTS idx_team_work_dispatch_current_lease_expiry
  ON team_work_dispatch_current(lease_expires_at);

CREATE TABLE IF NOT EXISTS team_work_dispatch_revisions (
  team TEXT NOT NULL,
  work_item_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0),
  previous_revision INTEGER,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('dispatching', 'claimed', 'abandoned')),
  lease_epoch TEXT NOT NULL,
  recovery_evidence TEXT,
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (team, work_item_id, revision)
);
