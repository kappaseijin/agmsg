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
  validateContractPack,
} = require("./team-work");
const {
  readLocalRows,
  runAudit,
} = require("./team-work-audit");

const RECONCILER_COMMANDS = new Set(["reconcile", "watchdog"]);
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

function deliveryBin() {
  if (isNonEmptyString(process.env.AGMSG_TEAM_WORK_DELIVERY_BIN)) {
    return process.env.AGMSG_TEAM_WORK_DELIVERY_BIN;
  }
  if (!isNonEmptyString(process.env.AGMSG_TEAM_WORK_SCRIPT_DIR)) {
    throw new Error("team-work delivery status command is unavailable");
  }
  return path.join(process.env.AGMSG_TEAM_WORK_SCRIPT_DIR, "delivery.sh");
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
    if (seat.deliverable === true && seat.liveness === "alive") {
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
    .filter((item) => item.issueState === "OPEN" && item.localState.status !== "active" && item.relationStatus === "complete")
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
  } else {
    if (args.length < 1 || args.length > 2) schemaError("invalid arguments for watchdog");
    output = runWatchdog(team, pack, roster, args[0], args[1]);
  }
  process.stdout.write(`${canonicalJson(output)}\n`);
}

module.exports = {
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
