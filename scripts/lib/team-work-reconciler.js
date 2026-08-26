#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const {
  SchemaError,
  canonicalJson,
  sha256Digest,
  envelopeDigest,
  validateContractPack,
} = require("./team-work");
const {
  readLocalRows,
  runAudit,
} = require("./team-work-audit");

const RECONCILER_COMMANDS = new Set(["reconcile", "watchdog", "dispatch", "dispatch-ack", "dispatch-abandon"]);
const POSITIVE_INTEGER = /^[1-9][0-9]*$/;
const NON_NEGATIVE_INTEGER = /^(0|[1-9][0-9]*)$/;
const DEFAULT_HEARTBEAT_STALE_SECONDS = 900;

function schemaError(message) {
  throw new SchemaError(message);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

function parseJson(text, message) {
  try {
    return JSON.parse(text);
  } catch (_) {
    schemaError(message);
  }
}

function parseNow() {
  const raw = process.env.TEAM_WORK_NOW;
  if (raw === undefined || raw === "") return Math.floor(Date.now() / 1000);
  if (!NON_NEGATIVE_INTEGER.test(raw)) schemaError("TEAM_WORK_NOW must be a non-negative integer");
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) schemaError("TEAM_WORK_NOW must be a non-negative integer");
  return value;
}

function parsePositiveInteger(value, name, defaultValue) {
  const raw = value === undefined ? String(defaultValue) : value;
  if (typeof raw !== "string" || !POSITIVE_INTEGER.test(raw)) {
    schemaError(`${name} must be a positive integer`);
  }
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed)) schemaError(`${name} must be a positive integer`);
  return parsed;
}

function addDistinct(list, value) {
  const fingerprint = canonicalJson(value);
  if (!list.some((candidate) => canonicalJson(candidate) === fingerprint)) list.push(value);
}

function sorted(values) {
  return values.slice().sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
}

function sqlLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function requireDatabasePath() {
  if (!isNonEmptyString(process.env.AGMSG_TEAM_WORK_DB)) {
    throw new Error("team-work mutation database is unavailable");
  }
  return process.env.AGMSG_TEAM_WORK_DB;
}

function runSqliteTransaction(dbPath, statements) {
  const script = [
    ".bail on",
    ".timeout 5000",
    "BEGIN IMMEDIATE;",
    statements,
    "COMMIT;",
  ].join("\n");
  const result = childProcess.spawnSync("sqlite3", [dbPath], {
    encoding: "utf8",
    input: script,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) {
    if (result.error.code === "ENOENT") throw new Error("team-work requires sqlite3 on PATH");
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(String(result.stderr || "").trim() || "sqlite3 dispatch mutation failed");
  }
}

function requireOneChange(guard) {
  return [
    `CREATE TEMP TABLE ${guard}(value INTEGER NOT NULL CHECK(value = 1));`,
    `INSERT INTO ${guard}(value) SELECT changes();`,
    `DROP TABLE ${guard};`,
  ].join("\n");
}

function deliveryBin() {
  if (isNonEmptyString(process.env.AGMSG_TEAM_WORK_DELIVERY_BIN)) {
    return process.env.AGMSG_TEAM_WORK_DELIVERY_BIN;
  }
  if (!isNonEmptyString(process.env.AGMSG_TEAM_WORK_SCRIPT_DIR)) {
    throw new Error("team-work delivery status command is unavailable");
  }
  return path.join(process.env.AGMSG_TEAM_WORK_SCRIPT_DIR, "delivery.sh");
}

function isDispatchableDelivery(seat) {
  // The machine ABI is deliberately strict: the JSON string "unknown" is
  // evidence that delivery could not be confirmed, never a truthy permission
  // to dispatch work.
  return isObject(seat) && seat.deliverable === true && seat.liveness === "alive";
}

function deliveryStatus(team, member) {
  if (!member || member.kind !== "seat") {
    return {
      name: member && member.name ? member.name : "",
      role: member && member.role ? member.role : "",
      status: "closed",
      evidence: [],
    };
  }
  if (!Array.isArray(member.registrations) || member.registrations.length === 0) {
    return { name: member.name, role: member.role, status: "unknown", evidence: [] };
  }

  const evidence = [];
  let live = 0;
  let unavailable = 0;
  let unknown = 0;
  for (const registration of member.registrations) {
    if (!isObject(registration) || !isNonEmptyString(registration.type) || !isNonEmptyString(registration.project)) {
      unknown += 1;
      continue;
    }
    const result = childProcess.spawnSync(
      "bash",
      [deliveryBin(), "status", registration.type, registration.project, "--format", "json"],
      { encoding: "utf8", maxBuffer: 1024 * 1024 },
    );
    if (result.error || result.status !== 0) {
      unknown += 1;
      evidence.push({ type: registration.type, project: registration.project, status: "unavailable" });
      continue;
    }
    let payload;
    try {
      payload = JSON.parse(String(result.stdout || ""));
    } catch (_) {
      unknown += 1;
      evidence.push({ type: registration.type, project: registration.project, status: "invalid" });
      continue;
    }
    const seats = isObject(payload) && Array.isArray(payload.seats) ? payload.seats : [];
    const matches = seats.filter((seat) => isObject(seat) && seat.team === team && seat.name === member.name);
    if (matches.length !== 1) {
      unknown += 1;
      evidence.push({ type: registration.type, project: registration.project, status: "ambiguous" });
      continue;
    }
    const seat = matches[0];
    const record = {
      type: registration.type,
      project: registration.project,
      runtime: seat.runtime || "unknown",
      liveness: seat.liveness || "unknown",
      deliverable: seat.deliverable,
    };
    evidence.push(record);
    if (isDispatchableDelivery(seat)) {
      live += 1;
    } else if (seat.deliverable === false) {
      unavailable += 1;
    } else {
      unknown += 1;
    }
  }

  let status = "unknown";
  if (live === 1 && unknown === 0) status = "available";
  if (live === 0 && unknown === 0 && unavailable === member.registrations.length) status = "boot_required";
  if (live > 1) status = "unknown";
  return { name: member.name, role: member.role, status, evidence };
}

function seatStatuses(team, pack, roster) {
  const requested = new Set(pack.workItems.map((item) => item.ownerSeat));
  const members = new Map((roster.members || []).map((member) => [member.name, member]));
  const statuses = [];
  for (const name of Array.from(requested).sort()) {
    statuses.push(deliveryStatus(team, members.get(name)));
  }
  return statuses;
}

function remediationFor(finding) {
  switch (finding.code) {
    case "expired_lease":
      return "owner must verify preservation before an explicit reclaim or release";
    case "upstream_closed":
      return "owner must confirm closeout; no automatic close or release was performed";
    case "orphan_ready":
      return "assign an existing live allowlisted seat or use a separate user gate; do not spawn a closed role";
    case "writeback_required":
      return "owner or manager must record writeback evidence after reevaluating remaining work";
    case "stale_state":
      return "refresh the unavailable or stale source before dispatching work";
    default:
      return "inspect the reconciler evidence before taking an explicit action";
  }
}

function finding(findings, code, item, extra) {
  const value = Object.assign({ code, workItemId: item.workItem.id }, extra || {});
  addDistinct(findings, value);
}

function readyItemsFromAudit(audit, pack) {
  if (audit.classificationBasis.status !== "ready") return [];
  return audit.items
    .filter((item) => item.issueState === "OPEN" && item.localState.status !== "active" && item.localState.workflowState !== "blocked" && item.localState.dispatchState !== "abandoned" && item.relationStatus === "complete")
    .map((item) => {
      const source = pack.workItems.find((candidate) => candidate.workItem.id === item.workItemId);
      return source ? {
        workItemId: source.workItem.id,
        ownerSeat: source.ownerSeat,
      } : null;
    })
    .filter((item) => item !== null);
}

function runReconcile(team, pack, roster, heartbeatPath) {
  validateContractPack(pack, roster, team);
  const now = parseNow();
  const audit = runAudit("audit", team, pack, roster);
  const local = readLocalRows(process.env.AGMSG_TEAM_WORK_DB, team);
  const statuses = seatStatuses(team, pack, roster);
  const statusBySeat = new Map(statuses.map((seat) => [seat.name, seat]));
  const findings = [];

  for (const item of pack.workItems) {
    const auditItem = audit.items.find((candidate) => candidate.workItemId === item.workItem.id);
    if (!auditItem) {
      finding(findings, "stale_state", item, { reason: "audit_item_missing" });
      continue;
    }
    const row = local.rows.get(item.workItem.id);
    const dispatchRow = local.dispatchRows.get(item.workItem.id);
    const leaseExpiresAt = auditItem.localState.leaseExpiresAt;
    if (Number.isInteger(leaseExpiresAt) && leaseExpiresAt <= now) {
      finding(findings, "expired_lease", item, { leaseExpiresAt });
    }
    if (dispatchRow && Number.isInteger(dispatchRow.leaseExpiresAt) && dispatchRow.leaseExpiresAt <= now) {
      finding(findings, "expired_lease", item, { leaseEpoch: dispatchRow.leaseEpoch, leaseExpiresAt: dispatchRow.leaseExpiresAt });
    }
    if (auditItem.issueState === "CLOSED" && auditItem.localState.status === "active") {
      finding(findings, "upstream_closed", item, { leaseOwner: auditItem.localState.leaseOwner || null });
    }
    if (item.writebackRequired && (!row || !Array.isArray(row.writebacks) || row.writebacks.length === 0)) {
      finding(findings, "writeback_required", item);
    }
  }

  for (const ready of readyItemsFromAudit(audit, pack)) {
    const item = pack.workItems.find((candidate) => candidate.workItem.id === ready.workItemId);
    const seat = statusBySeat.get(ready.ownerSeat);
    if (!item || !seat || seat.status !== "available") {
      finding(findings, "orphan_ready", item || { workItem: { id: ready.workItemId } }, {
        ownerSeat: ready.ownerSeat,
        seatStatus: seat ? seat.status : "unknown",
      });
    }
  }

  for (const violation of audit.violations || []) {
    if (["local_state_stale", "local_state_unavailable", "source_unavailable", "relation_incomplete"].includes(violation.code)) {
      const item = pack.workItems.find((candidate) => candidate.workItem.id === violation.workItemId);
      if (item) finding(findings, "stale_state", item, { reason: violation.code });
    }
  }
  if (local.error) {
    for (const item of pack.workItems) finding(findings, "stale_state", item, { reason: "local_state_unavailable" });
  }

  const sortedFindings = sorted(findings);
  const remediation = sorted(sortedFindings.map((item) => Object.assign({}, item, { remediation: remediationFor(item) })));
  const output = {
    schemaVersion: 1,
    command: "reconcile",
    team,
    contractDigest: audit.contractDigest,
    result: sortedFindings.length === 0 ? "healthy" : "attention",
    sourceDigest: audit.sourceDigest,
    auditDigest: audit.auditDigest,
    auditStatus: audit.classificationBasis.status,
    seats: sorted(statuses),
    findings: sortedFindings,
    remediation,
  };
  output.reconcileDigest = sha256Digest(output);

  if (heartbeatPath !== undefined) {
    if (!isNonEmptyString(heartbeatPath)) schemaError("heartbeat path must be a non-empty string");
    const heartbeat = {
      schemaVersion: 1,
      team,
      cycleId: crypto.createHash("sha256").update(`${team}:${now}:${output.reconcileDigest}`, "utf8").digest("hex"),
      startedAt: now,
      finishedAt: now,
      result: output.result,
      sourceDigest: output.sourceDigest,
    };
    const directory = path.dirname(heartbeatPath);
    const temporary = path.join(directory, `.${path.basename(heartbeatPath)}.${process.pid}.${crypto.randomUUID()}.tmp`);
    fs.writeFileSync(temporary, `${canonicalJson(heartbeat)}\n`, "utf8");
    fs.renameSync(temporary, heartbeatPath);
    output.heartbeatPath = heartbeatPath;
    output.reconcileDigest = sha256Digest(Object.assign({}, output, { reconcileDigest: undefined }));
  }
  return output;
}

function validHeartbeat(value, team) {
  return isObject(value) &&
    value.schemaVersion === 1 &&
    value.team === team &&
    isNonEmptyString(value.cycleId) &&
    Number.isInteger(value.startedAt) &&
    Number.isInteger(value.finishedAt) &&
    value.finishedAt >= value.startedAt &&
    isNonEmptyString(value.result) &&
    isNonEmptyString(value.sourceDigest);
}

function runWatchdog(team, pack, roster, heartbeatPath, staleSeconds) {
  validateContractPack(pack, roster, team);
  if (!isNonEmptyString(heartbeatPath)) schemaError("heartbeat path must be a non-empty string");
  const now = parseNow();
  const threshold = parsePositiveInteger(staleSeconds, "stale-seconds", DEFAULT_HEARTBEAT_STALE_SECONDS);
  let heartbeat;
  try {
    heartbeat = JSON.parse(fs.readFileSync(heartbeatPath, "utf8"));
  } catch (_) {
    const output = {
      schemaVersion: 1,
      command: "watchdog",
      team,
      heartbeatPath,
      status: "unknown",
      alarm: true,
      reason: "heartbeat_unavailable",
    };
    output.watchdogDigest = sha256Digest(output);
    return output;
  }
  if (!validHeartbeat(heartbeat, team) || heartbeat.finishedAt > now) {
    const output = {
      schemaVersion: 1,
      command: "watchdog",
      team,
      heartbeatPath,
      status: "unknown",
      alarm: true,
      reason: "heartbeat_invalid",
    };
    output.watchdogDigest = sha256Digest(output);
    return output;
  }
  const ageSeconds = now - heartbeat.finishedAt;
  const output = {
    schemaVersion: 1,
    command: "watchdog",
    team,
    heartbeatPath,
    heartbeat: {
      cycleId: heartbeat.cycleId,
      finishedAt: heartbeat.finishedAt,
      result: heartbeat.result,
      sourceDigest: heartbeat.sourceDigest,
    },
    ageSeconds,
    staleSeconds: threshold,
    status: ageSeconds > threshold ? "stale" : "healthy",
    alarm: ageSeconds > threshold,
  };
  output.watchdogDigest = sha256Digest(output);
  return output;
}

function memberMap(roster) {
  return new Map((roster.members || []).map((member) => [member.name, member]));
}

function dispatchAllowlist() {
  const raw = process.env.TEAM_WORK_DISPATCH_ALLOWLIST;
  if (!isNonEmptyString(raw)) return { valid: false, seats: new Set(), reason: "dispatch_allowlist_unavailable" };
  let value;
  try {
    value = JSON.parse(raw);
  } catch (_) {
    return { valid: false, seats: new Set(), reason: "dispatch_allowlist_invalid" };
  }
  if (!Array.isArray(value) || value.length === 0 || value.some((seat) => !isNonEmptyString(seat))) {
    return { valid: false, seats: new Set(), reason: "dispatch_allowlist_invalid" };
  }
  const seats = new Set(value);
  if (seats.size !== value.length) return { valid: false, seats: new Set(), reason: "dispatch_allowlist_invalid" };
  return { valid: true, seats };
}

function dispatchRemediation(code) {
  const text = {
    dispatch_allowlist_unavailable: "configure a non-empty JSON dispatch allowlist before dispatching",
    dispatch_allowlist_invalid: "correct TEAM_WORK_DISPATCH_ALLOWLIST to a unique JSON string array",
    owner_not_allowlisted: "add the existing owner seat to the explicit allowlist after review",
    work_not_ready: "refresh the queue and select an open unallocated work item",
    source_unknown: "resolve the audit violation before dispatching",
    seat_not_live: "do not spawn; restore the existing seat's live delivery evidence or assign another owner",
    duplicate_dispatch: "wait for the existing dispatch epoch to be ACKed or reconciled explicitly",
  };
  return text[code] || "inspect the dispatch evidence before an explicit action";
}

function dispatchOutput(team, workItemId, managerSeat, details) {
  const output = Object.assign({
    schemaVersion: 1,
    command: "dispatch",
    team,
    workItemId,
    managerSeat,
    dispatched: false,
    state: "not_dispatchable",
    remediation: [],
  }, details || {});
  output.dispatchDigest = sha256Digest(output);
  return output;
}

function readyWorkItem(audit, pack, workItemId) {
  return readyItemsFromAudit(audit, pack).find((item) => item.workItemId === workItemId) || null;
}

function dispatchableWorkItem(audit, pack, workItemId) {
  const ready = readyWorkItem(audit, pack, workItemId);
  if (ready) return ready;
  if (audit.classificationBasis.status === "unknown") return null;
  const auditItem = audit.items.find((item) => item.workItemId === workItemId);
  if (!auditItem || auditItem.issueState !== "OPEN" || auditItem.localState.status === "active" ||
      auditItem.localState.workflowState === "blocked" || auditItem.localState.dispatchState !== "abandoned" ||
      auditItem.relationStatus !== "complete") return null;
  const source = pack.workItems.find((candidate) => candidate.workItem.id === workItemId);
  return source ? { workItemId: source.workItem.id, ownerSeat: source.ownerSeat } : null;
}

function runDispatch(team, pack, roster, workItemId, managerSeat, ackTtl) {
  validateContractPack(pack, roster, team);
  if (!isNonEmptyString(workItemId)) schemaError("work-item-id must be a non-empty string");
  if (!isNonEmptyString(managerSeat)) schemaError("manager-seat must be a non-empty string");
  const members = memberMap(roster);
  const manager = members.get(managerSeat);
  if (!manager || manager.kind !== "seat" || manager.role !== "manager") {
    schemaError("manager-seat must be an exact kind: seat manager");
  }
  const item = pack.workItems.find((candidate) => candidate.workItem.id === workItemId);
  if (!item) schemaError(`work item does not exist: ${workItemId}`);
  const now = parseNow();
  const ttl = parsePositiveInteger(ackTtl, "ack-ttl-seconds", 120);
  const allowlist = dispatchAllowlist();
  if (!allowlist.valid) {
    return dispatchOutput(team, workItemId, managerSeat, {
      remediation: [{ code: allowlist.reason, remediation: dispatchRemediation(allowlist.reason) }],
    });
  }
  if (!allowlist.seats.has(item.ownerSeat)) {
    return dispatchOutput(team, workItemId, managerSeat, {
      remediation: [{ code: "owner_not_allowlisted", ownerSeat: item.ownerSeat, remediation: dispatchRemediation("owner_not_allowlisted") }],
    });
  }

  const audit = runAudit("audit", team, pack, roster);
  if (audit.classificationBasis.status === "unknown") {
    return dispatchOutput(team, workItemId, managerSeat, {
      sourceDigest: audit.sourceDigest,
      remediation: [{ code: "source_unknown", remediation: dispatchRemediation("source_unknown") }],
    });
  }
  if (!dispatchableWorkItem(audit, pack, workItemId)) {
    return dispatchOutput(team, workItemId, managerSeat, {
      sourceDigest: audit.sourceDigest,
      remediation: [{ code: "work_not_ready", remediation: dispatchRemediation("work_not_ready") }],
    });
  }
  const owner = members.get(item.ownerSeat);
  const seat = deliveryStatus(team, owner);
  if (seat.status !== "available") {
    return dispatchOutput(team, workItemId, managerSeat, {
      sourceDigest: audit.sourceDigest,
      ownerSeat: item.ownerSeat,
      delivery: seat,
      remediation: [{ code: "seat_not_live", ownerSeat: item.ownerSeat, seatStatus: seat.status, remediation: dispatchRemediation("seat_not_live") }],
    });
  }

  const leaseEpoch = crypto.randomUUID();
  const leaseExpiresAt = now + ttl;
  const contractDigest = sha256Digest(pack);
  const deliveryEvidence = canonicalJson({
    auditDigest: audit.auditDigest,
    queueDigest: audit.sourceDigest,
    seat,
  });
  const dbPath = requireDatabasePath();
  const sql = `
INSERT INTO team_work_dispatch_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat, state,
  lease_epoch, lease_expires_at, queue_digest, delivery_evidence_json,
  ack_evidence, recovery_evidence, last_action, last_actor, created_at, updated_at
)
SELECT
  ${sqlLiteral(team)}, ${sqlLiteral(item.workItem.id)}, ${sqlLiteral(contractDigest)}, ${sqlLiteral(envelopeDigest(item))}, ${sqlLiteral(item.ownerSeat)}, 'dispatching',
  ${sqlLiteral(leaseEpoch)}, ${leaseExpiresAt}, ${sqlLiteral(audit.sourceDigest)}, ${sqlLiteral(deliveryEvidence)},
  NULL, NULL, 'dispatch', ${sqlLiteral(managerSeat)}, ${now}, ${now}
WHERE NOT EXISTS (
  SELECT 1 FROM team_work_current
  WHERE team = ${sqlLiteral(team)}
    AND work_item_id = ${sqlLiteral(item.workItem.id)}
    AND lease_expires_at > ${now}
)
ON CONFLICT(team, work_item_id) DO UPDATE SET
  contract_digest = excluded.contract_digest,
  envelope_digest = excluded.envelope_digest,
  owner_seat = excluded.owner_seat,
  state = 'dispatching',
  lease_epoch = excluded.lease_epoch,
  lease_expires_at = excluded.lease_expires_at,
  queue_digest = excluded.queue_digest,
  delivery_evidence_json = excluded.delivery_evidence_json,
  ack_evidence = NULL,
  recovery_evidence = NULL,
  last_action = 'dispatch-replace',
  last_actor = ${sqlLiteral(managerSeat)},
  created_at = excluded.created_at,
  updated_at = excluded.updated_at
WHERE team_work_dispatch_current.state = 'abandoned'
  AND team_work_dispatch_current.lease_expires_at <= ${now}
  AND NOT EXISTS (
    SELECT 1 FROM team_work_current
    WHERE team = ${sqlLiteral(team)}
      AND work_item_id = ${sqlLiteral(item.workItem.id)}
      AND lease_expires_at > ${now}
  );
${requireOneChange("team_work_dispatch_insert_or_replace_guard")}
`;
  try {
    runSqliteTransaction(dbPath, sql);
  } catch (_) {
    return dispatchOutput(team, workItemId, managerSeat, {
      sourceDigest: audit.sourceDigest,
      remediation: [{ code: "duplicate_dispatch", remediation: dispatchRemediation("duplicate_dispatch") }],
    });
  }
  const output = dispatchOutput(team, workItemId, managerSeat, {
    dispatched: true,
    state: "dispatching",
    ownerSeat: item.ownerSeat,
    leaseEpoch,
    leaseExpiresAt,
    sourceDigest: audit.sourceDigest,
    auditDigest: audit.auditDigest,
    delivery: seat,
    remediation: [],
    sendInvoked: false,
  });
  output.dispatchDigest = sha256Digest(Object.assign({}, output, { dispatchDigest: undefined }));
  return output;
}

function abandonOutput(team, workItemId, managerSeat, leaseEpoch, details) {
  const output = Object.assign({
    schemaVersion: 1,
    command: "dispatch-abandon",
    team,
    workItemId,
    managerSeat,
    leaseEpoch,
    abandoned: false,
    state: "not_abandoned",
    remediation: [],
  }, details || {});
  output.abandonDigest = sha256Digest(output);
  return output;
}

function runDispatchAbandon(team, pack, roster, workItemId, managerSeat, leaseEpoch, evidence) {
  validateContractPack(pack, roster, team);
  if (!isNonEmptyString(workItemId)) schemaError("work-item-id must be a non-empty string");
  if (!isNonEmptyString(managerSeat)) schemaError("manager-seat must be a non-empty string");
  if (!isNonEmptyString(leaseEpoch)) schemaError("lease-epoch must be a non-empty string");
  if (!isNonEmptyString(evidence)) schemaError("evidence must be a non-empty string");
  const members = memberMap(roster);
  const manager = members.get(managerSeat);
  if (!manager || manager.kind !== "seat" || manager.role !== "manager") {
    schemaError("manager-seat must be an exact kind: seat manager");
  }
  const item = pack.workItems.find((candidate) => candidate.workItem.id === workItemId);
  if (!item) schemaError(`work item does not exist: ${workItemId}`);
  const now = parseNow();
  const local = readLocalRows(process.env.AGMSG_TEAM_WORK_DB, team);
  const dispatch = local.dispatchRows.get(workItemId);
  const contractDigest = sha256Digest(pack);
  const envelope = envelopeDigest(item);
  if (local.error || !dispatch || dispatch.contractDigest !== contractDigest || dispatch.envelopeDigest !== envelope ||
      dispatch.ownerSeat !== item.ownerSeat || dispatch.leaseEpoch !== leaseEpoch ||
      (dispatch.state !== "dispatching" && dispatch.state !== "claimed") ||
      !Number.isInteger(dispatch.leaseExpiresAt) || dispatch.leaseExpiresAt > now) {
    return abandonOutput(team, workItemId, managerSeat, leaseEpoch, {
      remediation: [{ code: "dispatch_epoch_invalid", remediation: "use the exact expired dispatch epoch before recovery" }],
    });
  }
  const current = local.rows.get(workItemId);
  if (current && Number.isInteger(current.leaseExpiresAt) && current.leaseExpiresAt > now) {
    return abandonOutput(team, workItemId, managerSeat, leaseEpoch, {
      remediation: [{ code: "active_claim", remediation: "an unexpired current claim prevents dispatch recovery" }],
    });
  }

  const recoveryEvidence = canonicalJson({ evidence, leaseEpoch, abandonedAt: now });
  const dbPath = requireDatabasePath();
  const sql = `
UPDATE team_work_dispatch_current SET
  state = 'abandoned',
  recovery_evidence = ${sqlLiteral(recoveryEvidence)},
  last_action = 'dispatch-abandon',
  last_actor = ${sqlLiteral(managerSeat)},
  updated_at = ${now}
WHERE team = ${sqlLiteral(team)}
  AND work_item_id = ${sqlLiteral(workItemId)}
  AND contract_digest = ${sqlLiteral(contractDigest)}
  AND envelope_digest = ${sqlLiteral(envelope)}
  AND owner_seat = ${sqlLiteral(item.ownerSeat)}
  AND state IN ('dispatching', 'claimed')
  AND lease_epoch = ${sqlLiteral(leaseEpoch)}
  AND lease_expires_at <= ${now}
  AND NOT EXISTS (
    SELECT 1 FROM team_work_current
    WHERE team = ${sqlLiteral(team)}
      AND work_item_id = ${sqlLiteral(workItemId)}
      AND lease_expires_at > ${now}
  );
${requireOneChange("team_work_dispatch_abandon_guard")}
`;
  try {
    runSqliteTransaction(dbPath, sql);
  } catch (_) {
    return abandonOutput(team, workItemId, managerSeat, leaseEpoch, {
      remediation: [{ code: "dispatch_abandon_conflict", remediation: "refresh the exact dispatch and current claim before retrying recovery" }],
    });
  }
  const output = abandonOutput(team, workItemId, managerSeat, leaseEpoch, {
    abandoned: true,
    state: "abandoned",
    ownerSeat: item.ownerSeat,
    leaseExpiresAt: dispatch.leaseExpiresAt,
    recoveryEvidence: evidence,
    remediation: [],
  });
  output.abandonDigest = sha256Digest(Object.assign({}, output, { abandonDigest: undefined }));
  return output;
}

function ackOutput(team, workItemId, ownerSeat, leaseEpoch, details) {
  const output = Object.assign({
    schemaVersion: 1,
    command: "dispatch-ack",
    team,
    workItemId,
    ownerSeat,
    leaseEpoch,
    acknowledged: false,
    state: "not_acknowledged",
    remediation: [],
  }, details || {});
  output.ackDigest = sha256Digest(output);
  return output;
}

function runDispatchAck(team, pack, roster, workItemId, ownerSeat, leaseEpoch, evidence) {
  validateContractPack(pack, roster, team);
  if (!isNonEmptyString(workItemId)) schemaError("work-item-id must be a non-empty string");
  if (!isNonEmptyString(ownerSeat)) schemaError("owner-seat must be a non-empty string");
  if (!isNonEmptyString(leaseEpoch)) schemaError("lease-epoch must be a non-empty string");
  const members = memberMap(roster);
  const owner = members.get(ownerSeat);
  if (!owner || owner.kind !== "seat") schemaError("owner-seat must be an exact kind: seat member");
  const item = pack.workItems.find((candidate) => candidate.workItem.id === workItemId);
  if (!item) schemaError(`work item does not exist: ${workItemId}`);
  if (item.ownerSeat !== ownerSeat) {
    return ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
      remediation: [{ code: "owner_mismatch", remediation: "only the declared owner seat may acknowledge this dispatch" }],
    });
  }
  const now = parseNow();
  const local = readLocalRows(process.env.AGMSG_TEAM_WORK_DB, team);
  const dispatch = local.dispatchRows.get(workItemId);
  if (local.error || !dispatch || dispatch.state !== "dispatching" || dispatch.ownerSeat !== ownerSeat || dispatch.leaseEpoch !== leaseEpoch || dispatch.leaseExpiresAt <= now) {
    return ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
      remediation: [{ code: "dispatch_epoch_invalid", remediation: "use the exact unexpired dispatch epoch; do not start work without it" }],
    });
  }
  const audit = runAudit("audit", team, pack, roster);
  const auditItem = audit.items.find((candidate) => candidate.workItemId === workItemId);
  if (audit.classificationBasis.status === "unknown" || !auditItem || auditItem.issueState !== "OPEN" || auditItem.relationStatus !== "complete") {
    return ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
      sourceDigest: audit.sourceDigest,
      remediation: [{ code: "source_unknown", remediation: "refresh the source before acknowledging the dispatch" }],
    });
  }
  const seat = deliveryStatus(team, owner);
  if (seat.status !== "available") {
    return ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
      sourceDigest: audit.sourceDigest,
      delivery: seat,
      remediation: [{ code: "seat_not_live", remediation: "restore the existing live delivery evidence before ACK" }],
    });
  }

  const contractDigest = sha256Digest(pack);
  const ackEvidence = canonicalJson({
    evidence: isNonEmptyString(evidence) ? evidence : "owner_ack",
    acknowledgedAt: now,
    leaseEpoch,
    queueDigest: dispatch.queueDigest,
    delivery: seat,
  });
  const dbPath = requireDatabasePath();
  const sql = `
UPDATE team_work_dispatch_current SET
  state = 'claimed',
  ack_evidence = ${sqlLiteral(ackEvidence)},
  last_action = 'dispatch-ack',
  last_actor = ${sqlLiteral(ownerSeat)},
  updated_at = ${now}
WHERE team = ${sqlLiteral(team)}
  AND work_item_id = ${sqlLiteral(workItemId)}
  AND state = 'dispatching'
  AND owner_seat = ${sqlLiteral(ownerSeat)}
  AND lease_epoch = ${sqlLiteral(leaseEpoch)}
  AND lease_expires_at > ${now};
${requireOneChange("team_work_dispatch_ack_guard")}
INSERT INTO team_work_current(
  team, work_item_id, contract_digest, envelope_digest, owner_seat,
  source_repository, source_number, revision, state, lease_owner,
  lease_expires_at, ack_evidence, pr_links_json, writebacks_json,
  last_action, last_actor, created_at, updated_at
) VALUES (
  ${sqlLiteral(team)}, ${sqlLiteral(item.workItem.id)}, ${sqlLiteral(contractDigest)}, ${sqlLiteral(envelopeDigest(item))}, ${sqlLiteral(item.ownerSeat)},
  ${sqlLiteral(item.workItem.source.repository)}, ${item.workItem.source.number}, 1, 'claimed', ${sqlLiteral(ownerSeat)},
  ${dispatch.leaseExpiresAt}, ${sqlLiteral(ackEvidence)}, '[]', '[]',
  'dispatch-ack', ${sqlLiteral(ownerSeat)}, ${now}, ${now}
)
ON CONFLICT(team, work_item_id) DO UPDATE SET
  revision = revision + 1,
  state = 'claimed',
  lease_owner = ${sqlLiteral(ownerSeat)},
  lease_expires_at = ${dispatch.leaseExpiresAt},
  ack_evidence = ${sqlLiteral(ackEvidence)},
  last_action = 'dispatch-ack',
  last_actor = ${sqlLiteral(ownerSeat)},
  updated_at = ${now}
WHERE contract_digest = ${sqlLiteral(contractDigest)}
  AND envelope_digest = ${sqlLiteral(envelopeDigest(item))}
  AND owner_seat = ${sqlLiteral(item.ownerSeat)}
  AND (lease_expires_at IS NULL OR lease_expires_at <= ${now});
${requireOneChange("team_work_claim_ack_guard")}
`;
  try {
    runSqliteTransaction(dbPath, sql);
  } catch (_) {
    return ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
      sourceDigest: audit.sourceDigest,
      remediation: [{ code: "claim_conflict", remediation: "a concurrent lease prevented ACK; refresh before retrying" }],
    });
  }
  const output = ackOutput(team, workItemId, ownerSeat, leaseEpoch, {
    acknowledged: true,
    state: "claimed",
    sourceDigest: audit.sourceDigest,
    leaseExpiresAt: dispatch.leaseExpiresAt,
    delivery: seat,
    remediation: [],
  });
  output.ackDigest = sha256Digest(Object.assign({}, output, { ackDigest: undefined }));
  return output;
}

function main() {
  const [command, team, packPath, ...args] = process.argv.slice(2);
  if (!RECONCILER_COMMANDS.has(command)) {
    process.stderr.write(`Error: unknown team-work reconciler command: ${command || ""}\n`);
    process.exitCode = 1;
    return;
  }
  const pack = parseJson(fs.readFileSync(packPath, "utf8"), "contract pack is not valid JSON");
  const roster = parseJson(fs.readFileSync(0, "utf8"), "roster contract is not valid JSON");
  let output;
  if (command === "reconcile") {
    if (args.length > 1) schemaError("invalid arguments for reconcile");
    output = runReconcile(team, pack, roster, args[0]);
  } else if (command === "watchdog") {
    if (args.length < 1 || args.length > 2) schemaError("invalid arguments for watchdog");
    output = runWatchdog(team, pack, roster, args[0], args[1]);
  } else if (command === "dispatch") {
    if (args.length < 2 || args.length > 3) schemaError("invalid arguments for dispatch");
    output = runDispatch(team, pack, roster, args[0], args[1], args[2]);
  } else if (command === "dispatch-abandon") {
    if (args.length !== 4) schemaError("invalid arguments for dispatch-abandon");
    output = runDispatchAbandon(team, pack, roster, args[0], args[1], args[2], args[3]);
  } else {
    if (args.length < 3 || args.length > 4) schemaError("invalid arguments for dispatch-ack");
    output = runDispatchAck(team, pack, roster, args[0], args[1], args[2], args[3]);
  }
  process.stdout.write(`${canonicalJson(output)}\n`);
}

module.exports = {
  runDispatch,
  runDispatchAbandon,
  runDispatchAck,
  runReconcile,
  runWatchdog,
};

if (require.main === module) {
  try {
    main();
  } catch (error) {
    if (error instanceof SchemaError) {
      process.stderr.write(`schema error: ${error.message}\n`);
      process.exitCode = 2;
    } else {
      process.stderr.write(`Error: ${error.message}\n`);
      process.exitCode = 1;
    }
  }
}
