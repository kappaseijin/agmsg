CREATE TRIGGER IF NOT EXISTS team_work_dispatch_current_history_insert
AFTER INSERT ON team_work_dispatch_current
BEGIN
  INSERT INTO team_work_dispatch_revisions(
    team, work_item_id, revision, previous_revision, action, actor, state,
    lease_epoch, recovery_evidence, snapshot_json, created_at
  ) VALUES (
    NEW.team,
    NEW.work_item_id,
    1,
    NULL,
    NEW.last_action,
    NEW.last_actor,
    NEW.state,
    NEW.lease_epoch,
    NEW.recovery_evidence,
    json_object(
      'schemaVersion', 1,
      'team', NEW.team,
      'workItemId', NEW.work_item_id,
      'contractDigest', NEW.contract_digest,
      'envelopeDigest', NEW.envelope_digest,
      'ownerSeat', NEW.owner_seat,
      'state', NEW.state,
      'leaseEpoch', NEW.lease_epoch,
      'leaseExpiresAt', NEW.lease_expires_at,
      'queueDigest', NEW.queue_digest,
      'deliveryEvidence', json(NEW.delivery_evidence_json),
      'ackEvidence', NEW.ack_evidence,
      'recoveryEvidence', NEW.recovery_evidence,
      'lastAction', NEW.last_action,
      'lastActor', NEW.last_actor,
      'createdAt', NEW.created_at,
      'updatedAt', NEW.updated_at
    ),
    NEW.updated_at
  );
END;

CREATE TRIGGER IF NOT EXISTS team_work_dispatch_current_history_update
AFTER UPDATE ON team_work_dispatch_current
BEGIN
  INSERT INTO team_work_dispatch_revisions(
    team, work_item_id, revision, previous_revision, action, actor, state,
    lease_epoch, recovery_evidence, snapshot_json, created_at
  ) VALUES (
    NEW.team,
    NEW.work_item_id,
    (
      SELECT COALESCE(MAX(revision), 0) + 1
      FROM team_work_dispatch_revisions
      WHERE team = NEW.team AND work_item_id = NEW.work_item_id
    ),
    (
      SELECT MAX(revision)
      FROM team_work_dispatch_revisions
      WHERE team = NEW.team AND work_item_id = NEW.work_item_id
    ),
    NEW.last_action,
    NEW.last_actor,
    NEW.state,
    NEW.lease_epoch,
    NEW.recovery_evidence,
    json_object(
      'schemaVersion', 1,
      'team', NEW.team,
      'workItemId', NEW.work_item_id,
      'contractDigest', NEW.contract_digest,
      'envelopeDigest', NEW.envelope_digest,
      'ownerSeat', NEW.owner_seat,
      'state', NEW.state,
      'leaseEpoch', NEW.lease_epoch,
      'leaseExpiresAt', NEW.lease_expires_at,
      'queueDigest', NEW.queue_digest,
      'deliveryEvidence', json(NEW.delivery_evidence_json),
      'ackEvidence', NEW.ack_evidence,
      'recoveryEvidence', NEW.recovery_evidence,
      'lastAction', NEW.last_action,
      'lastActor', NEW.last_actor,
      'createdAt', NEW.created_at,
      'updatedAt', NEW.updated_at
    ),
    NEW.updated_at
  );
END;

CREATE TRIGGER IF NOT EXISTS team_work_dispatch_revisions_immutable_update
BEFORE UPDATE ON team_work_dispatch_revisions
BEGIN
  SELECT RAISE(ABORT, 'team_work_dispatch_revisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS team_work_dispatch_revisions_immutable_delete
BEFORE DELETE ON team_work_dispatch_revisions
BEGIN
  SELECT RAISE(ABORT, 'team_work_dispatch_revisions is append-only');
END;
