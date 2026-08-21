#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const {
  SchemaError,
  canonicalJson,
  sha256Digest,
  envelopeDigest,
  validateContractPack,
} = require("./team-work");

const AUDIT_COMMANDS = new Set(["observe", "queue", "audit"]);
const POSITIVE_INTEGER = /^[1-9][0-9]*$/;

class LiveSourceError extends Error {
  constructor(message) {
    super(message);
    this.name = "LiveSourceError";
  }
}

function schemaError(message) {
  throw new SchemaError(message);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

function sqlLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function parseJson(text, message) {
  try {
    return JSON.parse(text);
  } catch (_) {
    throw new LiveSourceError(message);
  }
}

function splitRepository(repository) {
  if (!isNonEmptyString(repository)) throw new LiveSourceError("repository is unavailable");
  const parts = repository.split("/");
  if (parts.length !== 2 || !isNonEmptyString(parts[0]) || !isNonEmptyString(parts[1])) {
    throw new LiveSourceError("repository must be owner/name");
  }
  return { owner: parts[0], name: parts[1] };
}

function referenceKey(repository, number) {
  return `${repository}#${number}`;
}

function issueQuery() {
  return [
    "query TeamWorkIssueClosingRelations($owner: String!, $name: String!, $number: Int!, $after: String) {",
    "  repository(owner: $owner, name: $name) {",
    "    issue(number: $number) {",
    "      number",
    "      state",
    "      closedByPullRequestsReferences(first: 100, after: $after) {",
    "        totalCount",
    "        nodes { number repository { nameWithOwner } }",
    "        pageInfo { hasNextPage endCursor }",
    "      }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

function pullRequestQuery() {
  return [
    "query TeamWorkPullRequestClosingRelations($owner: String!, $name: String!, $number: Int!, $after: String) {",
    "  repository(owner: $owner, name: $name) {",
    "    pullRequest(number: $number) {",
    "      number",
    "      closingIssuesReferences(first: 100, after: $after) {",
    "        totalCount",
    "        nodes { number repository { nameWithOwner } }",
    "        pageInfo { hasNextPage endCursor }",
    "      }",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

function runGraphql(query, repository, number, after) {
  const parsedRepository = splitRepository(repository);
  const args = [
    "api",
    "graphql",
    "-f", `query=${query}`,
    "-f", `owner=${parsedRepository.owner}`,
    "-f", `name=${parsedRepository.name}`,
    "-F", `number=${number}`,
  ];
  if (after !== null) args.push("-f", `after=${after}`);

  const result = childProcess.spawnSync("gh", args, {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) throw new LiveSourceError("GitHub GraphQL command is unavailable");
  if (result.status !== 0) throw new LiveSourceError("GitHub GraphQL request failed");
  const response = parseJson(String(result.stdout || ""), "GitHub GraphQL returned invalid JSON");
  if (!isObject(response) || !isObject(response.data) || (Array.isArray(response.errors) && response.errors.length > 0)) {
    throw new LiveSourceError("GitHub GraphQL response contains errors");
  }
  return response.data;
}

function requireConnection(value, name) {
  if (!isObject(value)) throw new LiveSourceError(`${name} connection is unavailable`);
  if (!Number.isInteger(value.totalCount) || value.totalCount < 0) {
    throw new LiveSourceError(`${name} totalCount is invalid`);
  }
  if (!Array.isArray(value.nodes) || !isObject(value.pageInfo) || typeof value.pageInfo.hasNextPage !== "boolean") {
    throw new LiveSourceError(`${name} page info is invalid`);
  }
  return value;
}

function normalizeNode(node, name) {
  if (!isObject(node) || !Number.isInteger(node.number) || node.number <= 0 || !isObject(node.repository)) {
    throw new LiveSourceError(`${name} node is invalid`);
  }
  if (!isNonEmptyString(node.repository.nameWithOwner)) {
    throw new LiveSourceError(`${name} repository is invalid`);
  }
  return {
    repository: node.repository.nameWithOwner,
    number: node.number,
  };
}

function fetchPaginatedConnection(fetchPage, getConnection, name) {
  const nodes = [];
  const seenNodes = new Set();
  const seenCursors = new Set();
  let expectedCount = null;
  let after = null;

  while (true) {
    if (after !== null) {
      if (seenCursors.has(after)) throw new LiveSourceError(`${name} cursor repeated`);
      seenCursors.add(after);
    }
    const connection = requireConnection(getConnection(fetchPage(after)), name);
    if (expectedCount === null) expectedCount = connection.totalCount;
    if (expectedCount !== connection.totalCount) throw new LiveSourceError(`${name} totalCount changed`);

    for (const rawNode of connection.nodes) {
      const node = normalizeNode(rawNode, name);
      const key = referenceKey(node.repository, node.number);
      if (seenNodes.has(key)) throw new LiveSourceError(`${name} node repeated`);
      seenNodes.add(key);
      nodes.push(node);
    }

    if (!connection.pageInfo.hasNextPage) {
      if (nodes.length !== expectedCount) throw new LiveSourceError(`${name} page count is incomplete`);
      return nodes;
    }
    if (!isNonEmptyString(connection.pageInfo.endCursor)) {
      throw new LiveSourceError(`${name} next-page cursor is missing`);
    }
    after = connection.pageInfo.endCursor;
  }
}

function fetchIssueEvidence(source) {
  const parsedRepository = splitRepository(source.repository);
  let observedState = "";
  const closingPullRequests = fetchPaginatedConnection(
    (after) => {
      const data = runGraphql(issueQuery(), source.repository, source.number, after);
      const issue = data.repository && data.repository.issue;
      if (!isObject(issue) || issue.number !== source.number || !isNonEmptyString(issue.state)) {
        throw new LiveSourceError("source Issue is unavailable");
      }
      if (observedState !== "" && observedState !== issue.state) {
        throw new LiveSourceError("source Issue state changed during pagination");
      }
      observedState = issue.state;
      return issue;
    },
    (issue) => issue.closedByPullRequestsReferences,
    `Issue ${parsedRepository.owner}/${parsedRepository.name}#${source.number}`,
  );
  if (observedState !== "OPEN" && observedState !== "CLOSED") {
    throw new LiveSourceError("source Issue state is invalid");
  }
  return { state: observedState, closingPullRequests };
}

function fetchPullRequestEvidence(repository, number) {
  return fetchPaginatedConnection(
    (after) => {
      const data = runGraphql(pullRequestQuery(), repository, number, after);
      const pullRequest = data.repository && data.repository.pullRequest;
      if (!isObject(pullRequest) || pullRequest.number !== number) {
        throw new LiveSourceError("related pull request is unavailable");
      }
      return pullRequest;
    },
    (pullRequest) => pullRequest.closingIssuesReferences,
    `Pull request ${repository}#${number}`,
  );
}

function parseLocalRows(output) {
  const rows = new Map();
  for (const line of String(output || "").replace(/\r/g, "").split("\n")) {
    if (line.length === 0) continue;
    const row = JSON.parse(line);
    if (!isObject(row) || !isNonEmptyString(row.workItemId) || rows.has(row.workItemId)) {
      throw new Error("invalid row");
    }
    rows.set(row.workItemId, row);
  }
  return rows;
}

function readLocalRows(dbPath, team) {
  if (!isNonEmptyString(dbPath)) {
    return { error: true, rows: new Map(), dispatchRows: new Map() };
  }
  const sql = [
    "SELECT json_object(",
    "  'workItemId', work_item_id,",
    "  'contractDigest', contract_digest,",
    "  'envelopeDigest', envelope_digest,",
    "  'ownerSeat', owner_seat,",
    "  'source', json_object('repository', source_repository, 'number', source_number),",
    "  'revision', revision,",
    "  'state', state,",
    "  'leaseOwner', lease_owner,",
    "  'leaseExpiresAt', lease_expires_at,",
    "  'prLinks', json(pr_links_json),",
    "  'writebacks', json(writebacks_json)",
    ")",
    "FROM team_work_current",
    `WHERE team = ${sqlLiteral(team)}`,
    "ORDER BY work_item_id;",
  ].join("\n");
  const result = childProcess.spawnSync("sqlite3", ["-readonly", dbPath, sql], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) return { error: true, rows: new Map(), dispatchRows: new Map() };

  let rows;
  try {
    rows = parseLocalRows(result.stdout);
  } catch (_) {
    return { error: true, rows: new Map(), dispatchRows: new Map() };
  }

  const tableResult = childProcess.spawnSync(
    "sqlite3",
    ["-readonly", dbPath, "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'team_work_dispatch_current');"],
    { encoding: "utf8", maxBuffer: 1024 * 1024 },
  );
  if (tableResult.error || tableResult.status !== 0) return { error: true, rows: new Map(), dispatchRows: new Map() };
  const hasDispatchTable = String(tableResult.stdout || "").trim();
  if (hasDispatchTable === "0") return { error: false, rows, dispatchRows: new Map() };
  if (hasDispatchTable !== "1") return { error: true, rows: new Map(), dispatchRows: new Map() };

  const dispatchSql = [
    "SELECT json_object(",
    "  'workItemId', work_item_id,",
    "  'contractDigest', contract_digest,",
    "  'envelopeDigest', envelope_digest,",
    "  'ownerSeat', owner_seat,",
    "  'state', state,",
    "  'leaseEpoch', lease_epoch,",
    "  'leaseExpiresAt', lease_expires_at,",
    "  'queueDigest', queue_digest,",
    "  'deliveryEvidence', json(delivery_evidence_json),",
    "  'ackEvidence', ack_evidence",
    ")",
    "FROM team_work_dispatch_current",
    `WHERE team = ${sqlLiteral(team)}`,
    "ORDER BY work_item_id;",
  ].join("\n");
  const dispatchResult = childProcess.spawnSync("sqlite3", ["-readonly", dbPath, dispatchSql], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (dispatchResult.error || dispatchResult.status !== 0) return { error: true, rows: new Map(), dispatchRows: new Map() };
  try {
    return { error: false, rows, dispatchRows: parseLocalRows(dispatchResult.stdout) };
  } catch (_) {
    return { error: true, rows: new Map(), dispatchRows: new Map() };
  }
}

function addViolation(violations, code, item, extra) {
  const violation = Object.assign({ code, workItemId: item.workItem.id }, extra || {});
  const fingerprint = canonicalJson(violation);
  if (!violations.some((candidate) => canonicalJson(candidate) === fingerprint)) violations.push(violation);
}

function sortViolations(violations) {
  return violations.slice().sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
}

function localStateForItem(local, item, contractDigest, now, violations) {
  if (local.error) {
    addViolation(violations, "local_state_unavailable", item);
    return { status: "unavailable", row: null, dispatchRow: null, prLinks: [] };
  }
  const row = local.rows.get(item.workItem.id);
  const dispatchRow = local.dispatchRows.get(item.workItem.id);

  const source = item.workItem.source;
  if (row) {
    const validRow =
      isObject(row.source) &&
      row.contractDigest === contractDigest &&
      row.envelopeDigest === envelopeDigest(item) &&
      row.ownerSeat === item.ownerSeat &&
      row.source.repository === source.repository &&
      row.source.number === source.number &&
      Array.isArray(row.prLinks) &&
      Array.isArray(row.writebacks);
    if (!validRow) {
      addViolation(violations, "local_state_stale", item);
      return { status: "stale", row, dispatchRow, prLinks: [] };
    }
  }

  const prLinks = [];
  for (const link of row ? row.prLinks : []) {
    if (!isObject(link) || !isNonEmptyString(link.repository) || !Number.isInteger(link.number) || link.number <= 0 ||
      (link.relation !== "contributes" && link.relation !== "closes")) {
      addViolation(violations, "local_state_stale", item);
      return { status: "stale", row, dispatchRow, prLinks: [] };
    }
    prLinks.push({ repository: link.repository, number: link.number, relation: link.relation });
  }

  const currentActive = Boolean(row) && isNonEmptyString(row.leaseOwner) && Number.isInteger(row.leaseExpiresAt) && row.leaseExpiresAt > now;
  if (dispatchRow) {
    const validDispatch =
      dispatchRow.contractDigest === contractDigest &&
      dispatchRow.envelopeDigest === envelopeDigest(item) &&
      dispatchRow.ownerSeat === item.ownerSeat &&
      (dispatchRow.state === "dispatching" || dispatchRow.state === "claimed") &&
      isNonEmptyString(dispatchRow.leaseEpoch) &&
      Number.isInteger(dispatchRow.leaseExpiresAt) &&
      isNonEmptyString(dispatchRow.queueDigest) &&
      isObject(dispatchRow.deliveryEvidence);
    if (!validDispatch || (currentActive && (dispatchRow.state !== "claimed" || row.leaseOwner !== item.ownerSeat))) {
      addViolation(violations, "local_state_stale", item);
      return { status: "stale", row, dispatchRow, prLinks: [] };
    }
    const dispatchActive = dispatchRow.leaseExpiresAt > now;
    if (dispatchActive) {
      return {
        status: "active",
        row,
        dispatchRow,
        dispatchState: dispatchRow.state,
        prLinks,
        writebacks: row ? row.writebacks : [],
        leaseOwner: dispatchRow.ownerSeat,
        leaseExpiresAt: dispatchRow.leaseExpiresAt,
      };
    }
  }

  if (!row) return { status: "absent", row: null, dispatchRow, prLinks: [], writebacks: [] };
  return {
    status: currentActive ? "active" : "inactive",
    row,
    dispatchRow,
    prLinks,
    writebacks: row.writebacks,
    leaseOwner: row.leaseOwner || null,
    leaseExpiresAt: Number.isInteger(row.leaseExpiresAt) ? row.leaseExpiresAt : null,
  };
}

function collectExpectedRelations(item, localState, violations) {
  const relations = new Map();
  function add(relation, origin) {
    const key = referenceKey(relation.repository, relation.number);
    let aggregate = relations.get(key);
    if (!aggregate) {
      aggregate = {
        repository: relation.repository,
        number: relation.number,
        kinds: new Set(),
        origins: new Set(),
      };
      relations.set(key, aggregate);
    }
    aggregate.kinds.add(relation.relation);
    aggregate.origins.add(origin);
  }
  item.relations.forEach((relation) => add(relation, "pack"));
  localState.prLinks.forEach((relation) => add(relation, "local"));

  const sorted = Array.from(relations.values()).sort((left, right) => {
    return referenceKey(left.repository, left.number).localeCompare(referenceKey(right.repository, right.number));
  });
  for (const relation of sorted) {
    if (relation.kinds.size > 1) addViolation(violations, "relation_incomplete", item, {
      repository: relation.repository,
      number: relation.number,
    });
  }
  return sorted;
}

function evaluateRelations(item, sourceEvidence, expectedRelations, violations) {
  const issueClosers = new Set(sourceEvidence.closingPullRequests.map((pr) => referenceKey(pr.repository, pr.number)));
  const relationChecks = [];
  const allRelations = new Map();
  expectedRelations.forEach((relation) => allRelations.set(referenceKey(relation.repository, relation.number), relation));
  sourceEvidence.closingPullRequests.forEach((relation) => {
    const key = referenceKey(relation.repository, relation.number);
    if (!allRelations.has(key)) {
      allRelations.set(key, {
        repository: relation.repository,
        number: relation.number,
        kinds: new Set(),
        origins: new Set(),
      });
    }
  });

  const all = Array.from(allRelations.values()).sort((left, right) => {
    return referenceKey(left.repository, left.number).localeCompare(referenceKey(right.repository, right.number));
  });
  for (const relation of all) {
    const key = referenceKey(relation.repository, relation.number);
    let prClosers;
    try {
      prClosers = fetchPullRequestEvidence(relation.repository, relation.number);
    } catch (_) {
      addViolation(violations, "source_unavailable", item, { repository: relation.repository, number: relation.number });
      relationChecks.push({
        repository: relation.repository,
        number: relation.number,
        expected: relation.kinds.size === 1 ? Array.from(relation.kinds)[0] : "untracked",
        complete: false,
      });
      continue;
    }

    const sourceKey = referenceKey(item.workItem.source.repository, item.workItem.source.number);
    const prClosesSource = prClosers.some((issue) => referenceKey(issue.repository, issue.number) === sourceKey);
    const issueClosesByPr = issueClosers.has(key);
    const kinds = Array.from(relation.kinds);
    const expected = kinds.length === 1 ? kinds[0] : "untracked";
    const complete =
      expected === "closes" ? issueClosesByPr && prClosesSource :
      expected === "contributes" ? !issueClosesByPr && !prClosesSource :
      false;
    relationChecks.push({
      repository: relation.repository,
      number: relation.number,
      expected,
      complete,
      issueClosesByPr,
      prClosesSource,
    });
    if (!complete) {
      addViolation(violations, "relation_incomplete", item, {
        repository: relation.repository,
        number: relation.number,
      });
    }
  }
  return relationChecks;
}

function parseNow() {
  const raw = process.env.TEAM_WORK_NOW;
  if (raw === undefined || raw === "") return Math.floor(Date.now() / 1000);
  if (!POSITIVE_INTEGER.test(raw) && raw !== "0") schemaError("TEAM_WORK_NOW must be a non-negative integer");
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) schemaError("TEAM_WORK_NOW must be a non-negative integer");
  return value;
}

function summarizeItem(item, sourceEvidence, localState, relationChecks, hasViolation) {
  return {
    workItemId: item.workItem.id,
    source: {
      repository: item.workItem.source.repository,
      number: item.workItem.source.number,
    },
    ownerSeat: item.ownerSeat,
    issueState: sourceEvidence ? sourceEvidence.state : "unknown",
    localState: {
      status: localState.status,
      workflowState: localState.row ? localState.row.state || null : null,
      dispatchState: localState.dispatchState || null,
      leaseOwner: localState.leaseOwner || null,
      leaseExpiresAt: localState.leaseExpiresAt || null,
    },
    relationStatus: hasViolation ? "unknown" : "complete",
    relationChecks,
  };
}

function runAudit(command, team, pack, roster) {
  validateContractPack(pack, roster, team);
  const contractDigest = sha256Digest(pack);
  const now = parseNow();
  const local = readLocalRows(process.env.AGMSG_TEAM_WORK_DB, team);
  const items = [];
  const relationChecks = [];
  const violations = [];
  const itemFacts = [];

  for (const item of pack.workItems) {
    const before = violations.length;
    const localState = localStateForItem(local, item, contractDigest, now, violations);
    let sourceEvidence = null;
    try {
      sourceEvidence = fetchIssueEvidence(item.workItem.source);
    } catch (_) {
      addViolation(violations, "source_unavailable", item);
    }

    let checks = [];
    if (sourceEvidence) {
      const expectedRelations = collectExpectedRelations(item, localState, violations);
      checks = evaluateRelations(item, sourceEvidence, expectedRelations, violations);
    }
    const itemViolations = violations.slice(before).some((violation) => violation.workItemId === item.workItem.id);
    items.push(summarizeItem(item, sourceEvidence, localState, checks, itemViolations));
    relationChecks.push(...checks.map((check) => Object.assign({ workItemId: item.workItem.id }, check)));
    itemFacts.push({ item, sourceEvidence, localState, itemViolations });
  }

  const sortedViolations = sortViolations(violations);
  const unknown = sortedViolations.length > 0;
  let ready = [];
  let openItemCount = 0;
  let allocatedItemCount = 0;
  let blockedItemCount = 0;
  const blockedWorkItemIds = [];
  let closedItemCount = 0;
  if (!unknown) {
    for (const fact of itemFacts) {
      if (fact.sourceEvidence.state === "OPEN") {
        openItemCount += 1;
        if (fact.localState.status === "active") {
          allocatedItemCount += 1;
        } else if (fact.localState.row && fact.localState.row.state === "blocked") {
          blockedItemCount += 1;
          blockedWorkItemIds.push(fact.item.workItem.id);
        } else {
          ready.push({
            workItemId: fact.item.workItem.id,
            source: {
              repository: fact.item.workItem.source.repository,
              number: fact.item.workItem.source.number,
            },
            ownerSeat: fact.item.ownerSeat,
            workKinds: fact.item.workKinds.slice(),
          });
        }
      } else {
        closedItemCount += 1;
      }
    }
  }

  let status = "unknown";
  let reasons = sortedViolations;
  if (!unknown && ready.length > 0) status = "ready";
  if (!unknown && ready.length === 0 && openItemCount > 0 && allocatedItemCount === openItemCount) {
    status = "fully_allocated";
  }
  if (!unknown && openItemCount === 0 && closedItemCount === pack.workItems.length) status = "quiescent";
  if (!unknown && ready.length === 0 && blockedItemCount > 0) {
    status = "unknown";
    reasons = sortViolations(blockedWorkItemIds
      .map((workItemId) => ({ code: "blocked_work_item", workItemId })));
  }
  if (status === "unknown") ready = [];

  const sourceEvidence = {
    team,
    contractDigest,
    items,
    relationChecks,
    violations: sortedViolations,
  };
  const sourceDigest = sha256Digest(sourceEvidence);
  const classificationBasis = {
    status,
    readyCount: ready.length,
    openItemCount,
    allocatedItemCount,
    closedItemCount,
    reasons,
    sourceDigest,
  };
  const output = {
    schemaVersion: 1,
    command,
    team,
    contractDigest,
    items,
    classificationBasis,
    sourceDigest,
  };
  if (command === "queue") output.ready = ready;
  if (command === "audit") {
    output.relationChecks = relationChecks;
    output.violations = sortedViolations;
  }
  output.auditDigest = sha256Digest(output);
  return output;
}

function main() {
  const [command, team, packPath, ...args] = process.argv.slice(2);
  if (!AUDIT_COMMANDS.has(command)) {
    process.stderr.write(`Error: unknown team-work audit command: ${command || ""}\n`);
    process.exitCode = 1;
    return;
  }
  if (args.length !== 0) schemaError(`invalid arguments for ${command}`);
  const pack = parseJson(fs.readFileSync(packPath, "utf8"), "contract pack is not valid JSON");
  const roster = parseJson(fs.readFileSync(0, "utf8"), "roster contract is not valid JSON");
  process.stdout.write(`${canonicalJson(runAudit(command, team, pack, roster))}\n`);
}

module.exports = {
  readLocalRows,
  runAudit,
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
