#!/usr/bin/env node
'use strict';

/*
 * Issue #236 isolated fixture.
 *
 * This file deliberately does not participate in the installed launcher,
 * hooks, or PM configuration.  It is an executable model of the boundary
 * described by the accepted design so that the acceptance controls can be
 * run without granting or removing capabilities from a real seat.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const {spawnSync} = require('child_process');

const POLICY_VERSION = 'pm-broker-v1';
const FIXTURE_VERSION = 'pm-broker-fixture/1';
const TARGET_TEAM = 'agmsg';
const TARGET_AGENT = 'pm';
const ROOT_PREFIX = 'pm-broker-fixture-';

class Denied extends Error {
  constructor(reason) {
    super(reason);
    this.name = 'Denied';
  }
}

function deny(reason) {
  throw new Denied(reason);
}

function die(message) {
  throw new Error(message);
}

function ensureObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    die(label + ' must be an object');
  }
  return value;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    die('cannot read JSON ' + file + ': ' + error.message);
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', {mode: 0o600});
}

function appendJson(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n', {mode: 0o600});
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonical(value[key]);
    return result;
  }
  return value;
}

function digest(value) {
  return 'sha256:' + crypto.createHash('sha256')
    .update(JSON.stringify(canonical(value)), 'utf8')
    .digest('hex');
}

function randomId(prefix) {
  return prefix + '-' + crypto.randomBytes(8).toString('hex');
}

function rootPath(root, ...parts) {
  const resolvedRoot = path.resolve(root);
  const candidate = path.resolve(resolvedRoot, ...parts);
  if (candidate !== resolvedRoot &&
      !candidate.startsWith(resolvedRoot + path.sep)) {
    deny('path escapes fixture root');
  }
  return candidate;
}

function mustBeAbsoluteRoot(root) {
  const resolved = path.resolve(root);
  if (!path.isAbsolute(resolved) || path.basename(resolved).startsWith(ROOT_PREFIX) === false) {
    deny('fixture root is not an isolated temporary root');
  }
  return resolved;
}

function runGit(repo, args, options = {}) {
  const result = spawnSync('git', ['-C', repo, ...args], {
    encoding: 'utf8',
    timeout: options.timeout || 10000,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error) throw new Error('git ' + args.join(' ') + ': ' + result.error.message);
  if (result.status !== 0) {
    throw new Error('git ' + args.join(' ') + ' failed: ' +
      (result.stderr || result.stdout || '').trim());
  }
  return (result.stdout || '').trim();
}

function gitStatus(repo, args) {
  const result = spawnSync('git', ['-C', repo, ...args], {
    encoding: 'utf8',
    timeout: 10000,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error) return {status: 99, output: result.error.message};
  return {status: result.status, output: (result.stdout || result.stderr || '').trim()};
}

function currentHead(repo) {
  return runGit(repo, ['rev-parse', 'HEAD']);
}

function setupRepo(root) {
  const origin = rootPath(root, 'origin.git');
  const repo = rootPath(root, 'repo');
  fs.mkdirSync(repo, {recursive: true});
  runGit(repo, ['init']);
  runGit(repo, ['config', 'user.email', 'fixture@example.invalid']);
  runGit(repo, ['config', 'user.name', 'PM broker fixture']);
  runGit(repo, ['checkout', '-b', 'main']);
  fs.writeFileSync(path.join(repo, 'README.fixture'), 'isolated fixture\n');
  runGit(repo, ['add', 'README.fixture']);
  runGit(repo, ['commit', '-m', 'fixture base']);
  const head = currentHead(repo);
  // The shared checkout has a global push-owner guard. Seed a bare origin by
  // cloning the already-created local repository; the fixture never needs a
  // push side effect just to create its initial repository state.
  const clone = spawnSync('git', ['clone', '--bare', repo, origin], {
    encoding: 'utf8',
    timeout: 10000,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (clone.error || clone.status !== 0) {
    throw new Error('git clone --bare failed: ' +
      ((clone.stderr || clone.stdout || clone.error?.message || '').trim()));
  }
  runGit(repo, ['remote', 'add', 'origin', origin]);
  runGit(repo, ['fetch', 'origin']);
  runGit(repo, ['branch', '--set-upstream-to=origin/main', 'main']);
  return {origin, repo, head};
}

function defaultConfig() {
  return {
    schemaVersion: 1,
    policyVersion: POLICY_VERSION,
    tools: ['broker'],
    mcp: [],
    plugins: [],
    strictMcp: true,
    loadResult: 'known',
  };
}

function defaultRoster() {
  return [
    {
      team: 'agmsg',
      agent: 'pm',
      role: 'pm',
      seatType: 'pm',
      projectId: 'fixture-project',
      cwdId: 'same-cwd',
      policy: 'target',
    },
    {
      team: 'agmsg',
      agent: 'owner',
      role: 'owner',
      seatType: 'owner',
      projectId: 'fixture-project',
      cwdId: 'same-cwd',
      policy: 'ordinary',
    },
    {
      team: 'agmsg',
      agent: 'programmer',
      role: 'programmer',
      seatType: 'programmer',
      projectId: 'fixture-project',
      cwdId: 'same-cwd',
      policy: 'ordinary',
    },
    {
      team: 'other',
      agent: 'other-pm',
      role: 'pm',
      seatType: 'pm',
      projectId: 'fixture-project',
      cwdId: 'same-cwd',
      policy: 'ordinary',
    },
  ];
}

function initialState(root, fixedHead, repo) {
  const profile = {
    policyVersion: POLICY_VERSION,
    targetTeam: TARGET_TEAM,
    targetAgent: TARGET_AGENT,
    restrictedTools: ['broker'],
    deniedOperations: [
      'work',
      'absolute-path',
      'rtk',
      'shell-wrapper',
      'interpreter',
      'gui',
      'mcp',
      'child-agent',
    ],
  };
  return {
    schemaVersion: 1,
    fixtureVersion: FIXTURE_VERSION,
    fixedHead,
    team: TARGET_TEAM,
    projectId: 'fixture-project',
    cwdId: 'same-cwd',
    policyVersion: POLICY_VERSION,
    launchProfileDigest: digest(profile),
    repoId: 'fixture-origin-clone',
    repoPath: repo.repo,
    originPath: repo.origin,
    repoHead: repo.head,
    approvedProxy: {
      sandboxDigest: 'sha256:fixture-sandbox-v1',
      diffDigest: 'sha256:fixture-diff-v1',
      expectedHead: repo.head,
      account: 'kappaseijin4codex',
    },
    approvedDecision: {
      decisionId: 'decision-236',
      patchDigest: 'sha256:fixture-patch-v1',
      targets: ['rule', 'hot-cache'],
      expectedPreimage: 'sha256:empty',
    },
    runtime: {
      seat: 'worker',
      pidfile: 'worker.pid',
      lock: 'worker.lock',
      generation: 'stale-generation',
      active: false,
      owner: 'worker',
    },
    bot: {
      botId: 'kappaseijin4codex',
      owner: 'kappaseijin',
      repo: 'kappaseijin/private-fixture',
      visibility: 'private',
      permission: 'write',
      invitationId: 'invite-236',
    },
  };
}

function init(rootArg, fixedHead) {
  const root = mustBeAbsoluteRoot(rootArg);
  if (!fixedHead || !/^[0-9a-f]{7,64}$/u.test(fixedHead)) {
    deny('fixed HEAD is required');
  }
  fs.mkdirSync(root, {recursive: true});
  for (const directory of [
    'bindings', 'claims', 'records', 'markers', 'requests', 'proxy',
    'provision', 'applied', 'runtime', 'api',
  ]) {
    fs.mkdirSync(rootPath(root, directory), {recursive: true});
  }
  const repo = setupRepo(root);
  const state = initialState(root, fixedHead, repo);
  writeJson(rootPath(root, 'state.json'), state);
  writeJson(rootPath(root, 'config.json'), defaultConfig());
  writeJson(rootPath(root, 'roster.json'), defaultRoster());
  writeJson(rootPath(root, 'claims.json'), []);
  writeJson(rootPath(root, 'sessions.json'), []);
  writeJson(rootPath(root, 'runtime.json'), state.runtime);
  fs.writeFileSync(rootPath(root, 'runtime', 'worker.pid'), 'stale\n');
  fs.writeFileSync(rootPath(root, 'runtime', 'worker.lock'), 'stale-generation\n');
  writeJson(rootPath(root, 'plugin.json'), {name: 'fixture-plugin', loaded: false});
  writeJson(rootPath(root, 'mcp.json'), {name: 'fixture-mcp', loaded: false});
  writeJson(rootPath(root, 'api', 'fixture.json'), {writes: 0});
  return {root, repo: repo.repo, fixedHead};
}

function loadConfig(root) {
  let config;
  try {
    config = readJson(rootPath(root, 'config.json'));
  } catch (error) {
    deny('configuration load is unknown');
  }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    deny('configuration is malformed');
  }
  if (config.policyVersion !== POLICY_VERSION ||
      config.strictMcp !== true ||
      config.loadResult !== 'known' ||
      !Array.isArray(config.tools) ||
      !config.tools.includes('broker') ||
      config.tools.some((tool) => tool !== 'broker') ||
      !Array.isArray(config.plugins) ||
      !Array.isArray(config.mcp)) {
    deny('configuration is outside the fixed broker contract');
  }
  return config;
}

function loadState(root) {
  return readJson(rootPath(root, 'state.json'));
}

function loadRoster(root) {
  const roster = readJson(rootPath(root, 'roster.json'));
  if (!Array.isArray(roster)) die('roster is malformed');
  return roster;
}

function targetBinding(record) {
  return record.team === TARGET_TEAM &&
    record.agent === TARGET_AGENT &&
    record.role === 'pm' &&
    record.seatType === 'pm';
}

function runHook(root, hookArg) {
  if (!hookArg) return {status: 'not-configured'};
  const hook = path.resolve(hookArg);
  if (!fs.existsSync(hook)) return {status: 'unavailable'};
  try {
    if ((fs.statSync(hook).mode & 0o111) === 0) return {status: 'not-executable'};
  } catch (error) {
    return {status: 'unavailable'};
  }
  const result = spawnSync(hook, [], {
    cwd: root,
    // The hook is only a supplementary probe. Do not leak the parent
    // session's environment into the fixture or its evidence packet.
    env: {
      PATH: '/usr/bin:/bin:/usr/sbin:/sbin',
      AGMSG_PM_BROKER_ROOT: root,
    },
    encoding: 'utf8',
    // 200ms includes process startup on the shared macOS runner and caused a
    // trivial executable hook to be classified as timeout. The fixture still
    // bounds the hook, while leaving enough room to distinguish startup from a
    // deliberately sleeping hook.
    timeout: 1000,
    stdio: ['ignore', 'ignore', 'ignore'],
  });
  if (result.error && result.error.code === 'ETIMEDOUT') return {status: 'timeout'};
  if (result.error) return {status: 'failed'};
  if (result.status !== 0) return {status: 'failed-' + result.status};
  return {status: 'completed'};
}

function profileFor(record) {
  const target = targetBinding(record);
  if (target) {
    return {
      policyVersion: POLICY_VERSION,
      effectiveTools: ['broker'],
      allowedOperations: [
        'message', 'record', 'issue_view', 'pr_view', 'seat_status',
        'seat_start', 'git_maintenance', 'sync_origin_clone',
        'proxy_git_write', 'team_provision', 'bot_collaborator',
        'apply_decision', 'stale_runtime_cleanup', 'packet',
      ],
    };
  }
  return {
    policyVersion: 'ordinary-v1',
    effectiveTools: ['broker', 'direct-work'],
    allowedOperations: ['work', 'message', 'record', 'packet'],
  };
}

function launch(rootArg, actor, options) {
  const root = mustBeAbsoluteRoot(rootArg);
  const config = loadConfig(root);
  const roster = loadRoster(root);
  const matches = roster.filter((record) => record.agent === actor);
  if (matches.length !== 1) deny('identity roster is missing or ambiguous');
  const record = matches[0];
  if (!record.team || !record.projectId || !record.seatType) {
    deny('identity roster record is incomplete');
  }
  const sessions = readJson(rootPath(root, 'sessions.json'));
  const claims = readJson(rootPath(root, 'claims.json'));
  const activeForAgent = claims.filter((claim) =>
    claim.team === record.team && claim.agent === record.agent && claim.active);
  if (activeForAgent.length > 0 && !options.resume) {
    deny('active claim already exists');
  }
  let sessionId = options.resume || randomId('sid');
  const previous = sessions.filter((session) => session.sessionId === sessionId);
  if (options.resume && previous.some((session) => session.active)) {
    deny('parallel resume is active');
  }
  if (options.resume && previous.length === 0) {
    deny('resume session is unknown');
  }
  const generation = randomId('generation');
  const profile = profileFor(record);
  const binding = {
    schemaVersion: 1,
    fixtureVersion: FIXTURE_VERSION,
    team: record.team,
    agent: record.agent,
    role: record.role,
    seatType: record.seatType,
    projectId: record.projectId,
    cwdId: record.cwdId,
    sessionId,
    generation,
    policyVersion: profile.policyVersion,
    effectiveTools: profile.effectiveTools,
    allowedOperations: profile.allowedOperations,
    hook: runHook(root, options.hook),
    configExtensions: {
      plugins: config.plugins,
      mcp: config.mcp,
    },
  };
  const bindingDigest = digest(binding);
  const newSession = {
    sessionId,
    team: record.team,
    agent: record.agent,
    generation,
    bindingDigest,
    active: true,
    resumedFrom: options.resume || null,
  };
  const newClaim = {
    team: record.team,
    agent: record.agent,
    sessionId,
    generation,
    owner: record.agent,
    active: true,
  };
  for (const session of sessions) {
    if (session.sessionId === sessionId) session.active = false;
  }
  for (const claim of claims) {
    if (claim.sessionId === sessionId) claim.active = false;
  }
  sessions.push(newSession);
  claims.push(newClaim);
  writeJson(rootPath(root, 'sessions.json'), sessions);
  writeJson(rootPath(root, 'claims.json'), claims);
  writeJson(rootPath(root, 'bindings', sessionId + '.json'), binding);
  return {
    status: 'started',
    fixtureVersion: FIXTURE_VERSION,
    sessionId,
    generation,
    policy: profile.policyVersion,
    effectiveTools: profile.effectiveTools,
    hookStatus: binding.hook.status,
    launchProfileDigest: digest(profile),
  };
}

function closeSession(rootArg, sessionId) {
  const root = mustBeAbsoluteRoot(rootArg);
  const sessions = readJson(rootPath(root, 'sessions.json'));
  const claims = readJson(rootPath(root, 'claims.json'));
  let found = false;
  for (const session of sessions) {
    if (session.sessionId === sessionId) {
      session.active = false;
      found = true;
    }
  }
  for (const claim of claims) {
    if (claim.sessionId === sessionId) claim.active = false;
  }
  if (!found) deny('session is unknown');
  writeJson(rootPath(root, 'sessions.json'), sessions);
  writeJson(rootPath(root, 'claims.json'), claims);
  return {status: 'closed', sessionId};
}

function validateBinding(root, sessionId, agentArg) {
  const binding = readJson(rootPath(root, 'bindings', sessionId + '.json'));
  if (binding.sessionId !== sessionId) deny('binding session mismatch');
  if (agentArg && agentArg !== binding.agent) deny('agent argument does not match binding');
  const roster = loadRoster(root).filter((record) =>
    record.team === binding.team &&
    record.agent === binding.agent &&
    record.role === binding.role &&
    record.seatType === binding.seatType &&
    record.projectId === binding.projectId &&
    record.cwdId === binding.cwdId);
  if (roster.length !== 1) deny('identity roster is missing or ambiguous');
  const sessions = readJson(rootPath(root, 'sessions.json')).filter((session) =>
    session.sessionId === sessionId && session.active);
  if (sessions.length !== 1 || sessions[0].generation !== binding.generation) {
    deny('session generation is stale or ambiguous');
  }
  const claims = readJson(rootPath(root, 'claims.json')).filter((claim) =>
    claim.team === binding.team &&
    claim.agent === binding.agent &&
    claim.sessionId === binding.sessionId);
  if (claims.length !== 1 || !claims[0].active ||
      claims[0].generation !== binding.generation) {
    deny('claim is missing, stale, or ambiguous');
  }
  if (sessions[0].bindingDigest !== digest(binding)) deny('binding digest mismatch');
  return binding;
}

function output(value) {
  process.stdout.write(JSON.stringify(value) + '\n');
}

function operationMarker(root, event, value = {}) {
  appendJson(rootPath(root, 'markers', 'operations.jsonl'), {
    event,
    ...value,
  });
}

function requireString(value, field) {
  if (typeof value !== 'string' || value.length === 0) deny(field + ' is required');
  return value;
}

function requireExact(value, expected, field) {
  if (value !== expected) deny(field + ' does not match the registered target');
}

function performWork(root, binding, input) {
  if (!['fetch', 'remote_branches', 'pr_list'].includes(input.kind)) {
    deny('work kind is not one of the three delegated operations');
  }
  appendJson(rootPath(root, 'markers', 'direct-work.jsonl'), {
    team: binding.team,
    agent: binding.agent,
    cwdId: binding.cwdId,
    kind: input.kind,
  });
  return {marker: 'direct-work', kind: input.kind};
}

function performMessage(root, binding, input) {
  requireExact(binding.team, TARGET_TEAM, 'message team');
  const recipient = requireString(input.recipient, 'recipient');
  const roster = loadRoster(root);
  const match = roster.filter((record) => record.team === binding.team &&
    record.agent === recipient);
  if (match.length !== 1) deny('recipient is not a registered teammate');
  const body = requireString(input.body, 'message body');
  appendJson(rootPath(root, 'records', 'messages.jsonl'), {
    sender: binding.agent,
    team: binding.team,
    recipient,
    body,
  });
  return {sender: binding.agent, recipient};
}

function performRecord(root, input) {
  const target = input.target;
  if (!['plan', 'notes'].includes(target)) deny('record target is not allow-listed');
  const text = requireString(input.text, 'record text');
  const file = rootPath(root, 'records', target + '.md');
  if (fs.existsSync(file) && fs.lstatSync(file).isSymbolicLink()) {
    deny('record target is a symlink');
  }
  fs.appendFileSync(file, text + '\n');
  return {target};
}

function performIssueView(input) {
  if (input.kind === 'issue' && input.number === 236) {
    return {kind: 'issue', number: 236, state: 'OPEN', title: 'PM execution boundary'};
  }
  if (input.kind === 'pull_request' && input.number === 237) {
    return {kind: 'pull_request', number: 237, state: 'MERGED', head: 'c8e4b87'};
  }
  deny('requested issue or pull request is outside the specified scope');
}

function performSeatStatus(root, input) {
  const seat = requireString(input.seat, 'seat');
  const matches = loadRoster(root).filter((record) =>
    record.team === TARGET_TEAM && record.agent === seat);
  if (matches.length !== 1) deny('seat is not a registered target');
  return {agent: seat, role: matches[0].role, state: 'registered'};
}

function performSeatStart(root, input) {
  const seat = requireString(input.seat, 'seat');
  requireExact(input.profile, 'codex-worker', 'profile');
  if (seat !== 'programmer') deny('seat start target is not the fixed worker profile');
  appendJson(rootPath(root, 'records', 'seat-operations.jsonl'), {
    operation: 'start',
    seat,
    profile: input.profile,
  });
  return {seat, profile: input.profile};
}

function repoFromState(root, input) {
  const state = loadState(root);
  requireExact(input.repoId, state.repoId, 'repo id');
  const repo = path.resolve(state.repoPath);
  if (input.repoPath && path.resolve(input.repoPath) !== repo) {
    deny('repo path is not the registered repository');
  }
  return {state, repo};
}

function requireTrackedClean(repo) {
  const unstaged = gitStatus(repo, ['diff', '--quiet']);
  const staged = gitStatus(repo, ['diff', '--cached', '--quiet']);
  if (unstaged.status !== 0 || staged.status !== 0) {
    deny('tracked repository changes are present');
  }
}

function performGitMaintenance(root, input) {
  const {state, repo} = repoFromState(root, input);
  const operation = requireString(input.operation, 'git maintenance operation');
  if (operation === 'fetch_prune') {
    runGit(repo, ['fetch', '--prune', 'origin']);
  } else if (operation === 'sync_main') {
    requireTrackedClean(repo);
    requireExact(runGit(repo, ['branch', '--show-current']), 'main', 'current branch');
    runGit(repo, ['fetch', 'origin']);
    runGit(repo, ['merge', '--ff-only', 'origin/main']);
  } else if (operation === 'delete_merged_branch') {
    requireTrackedClean(repo);
    const branch = requireString(input.branch, 'branch');
    if (branch === 'main' || !/^[A-Za-z0-9._/-]+$/u.test(branch)) {
      deny('branch is not a deletable registered target');
    }
    const ancestor = gitStatus(repo, ['merge-base', '--is-ancestor', branch, 'main']);
    if (ancestor.status !== 0) deny('branch is not merged into main');
    runGit(repo, ['branch', '-d', branch]);
  } else if (operation === 'post_merge_verify') {
    requireTrackedClean(repo);
    const head = currentHead(repo);
    const remoteHead = runGit(repo, ['rev-parse', 'origin/main']);
    if (head !== remoteHead) deny('post-merge HEAD does not match origin/main');
    if (input.expectedHead) requireExact(input.expectedHead, head, 'expected HEAD');
  } else if (operation === 'worktree_prune') {
    runGit(repo, ['worktree', 'prune']);
  } else {
    deny('git operation is not an approved maintenance operation');
  }
  operationMarker(root, 'maintenance.' + operation, {repoId: state.repoId});
  return {operation, repoId: state.repoId};
}

function performSyncOriginClone(root, input) {
  const {state, repo} = repoFromState(root, input);
  requireTrackedClean(repo);
  requireExact(runGit(repo, ['branch', '--show-current']), 'main', 'current branch');
  runGit(repo, ['fetch', '--prune', 'origin']);
  runGit(repo, ['merge', '--ff-only', 'origin/main']);
  const head = currentHead(repo);
  requireExact(head, runGit(repo, ['rev-parse', 'origin/main']), 'synced HEAD');
  operationMarker(root, 'sync_origin_clone', {repoId: state.repoId, head});
  return {operation: 'sync_origin_clone', head};
}

function performProxyGitWrite(root, input) {
  const state = loadState(root);
  const approved = state.approvedProxy;
  requireExact(input.sandboxDigest, approved.sandboxDigest, 'sandbox constraints');
  requireExact(input.diffDigest, approved.diffDigest, 'agreed diff');
  requireExact(input.expectedHead, approved.expectedHead, 'expected HEAD');
  requireExact(input.account, approved.account, 'producer account');
  if (!['commit', 'push', 'pull_request'].includes(input.operation)) {
    deny('proxy git operation is not allow-listed');
  }
  appendJson(rootPath(root, 'proxy', 'effects.jsonl'), {
    operation: input.operation,
    account: input.account,
    diffDigest: input.diffDigest,
  });
  return {operation: input.operation, account: input.account};
}

function performTeamProvision(root, input) {
  requireExact(input.decisionId, 'decision-236', 'decision');
  requireExact(input.role, 'worker', 'role');
  requireExact(input.template, 'codex-worker', 'template');
  requireExact(input.pathId, 'worker-clone', 'path id');
  if (input.patch !== undefined) deny('arbitrary provision patch is not accepted');
  const base = rootPath(root, 'provision', 'worker');
  fs.mkdirSync(base, {recursive: true});
  fs.writeFileSync(path.join(base, 'AGENT.md'), '# fixture worker\n');
  fs.writeFileSync(path.join(base, 'config.toml'), 'role = "worker"\n');
  writeJson(path.join(base, 'registration.json'), {
    team: TARGET_TEAM, role: 'worker', template: 'codex-worker',
  });
  writeJson(path.join(base, 'layout.json'), {tab: 'worker', paneCount: 1});
  return {pathId: input.pathId, files: ['AGENT.md', 'config.toml', 'registration.json', 'layout.json']};
}

function performBotCollaborator(root, input) {
  const state = loadState(root);
  const bot = state.bot;
  requireExact(input.botId, bot.botId, 'bot id');
  requireExact(input.owner, bot.owner, 'bot owner');
  requireExact(input.repo, bot.repo, 'repository');
  requireExact(input.visibility, bot.visibility, 'repository visibility');
  requireExact(input.permission, bot.permission, 'permission');
  requireExact(input.invitationId, bot.invitationId, 'invitation');
  if (!['add', 'accept_invite'].includes(input.operation)) {
    deny('bot operation is not allow-listed');
  }
  appendJson(rootPath(root, 'api', 'calls.jsonl'), {
    operation: input.operation,
    botId: input.botId,
    repo: input.repo,
  });
  const api = readJson(rootPath(root, 'api', 'fixture.json'));
  api.writes += 1;
  writeJson(rootPath(root, 'api', 'fixture.json'), api);
  return {operation: input.operation, writes: api.writes};
}

function performApplyDecision(root, input) {
  const state = loadState(root);
  const approved = state.approvedDecision;
  requireExact(input.decisionId, approved.decisionId, 'decision');
  requireExact(input.patchDigest, approved.patchDigest, 'patch digest');
  requireExact(input.expectedPreimage, approved.expectedPreimage, 'preimage');
  if (!approved.targets.includes(input.target)) deny('decision target is not allow-listed');
  if (typeof input.content !== 'string' || input.content.length === 0) {
    deny('decision content is required');
  }
  const target = rootPath(root, 'applied', input.target + '.txt');
  if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) {
    deny('decision target is a symlink');
  }
  fs.writeFileSync(target, input.content + '\n');
  return {target: input.target};
}

function performStaleCleanup(root, input) {
  const state = loadState(root);
  const runtime = readJson(rootPath(root, 'runtime.json'));
  requireExact(input.seat, state.runtime.seat, 'runtime seat');
  requireExact(input.generation, state.runtime.generation, 'runtime generation');
  requireExact(input.owner, state.runtime.owner, 'runtime owner');
  if (runtime.active !== false) deny('runtime is still active');
  const pidfile = rootPath(root, 'runtime', state.runtime.pidfile);
  const lock = rootPath(root, 'runtime', state.runtime.lock);
  if (fs.existsSync(pidfile)) fs.unlinkSync(pidfile);
  if (fs.existsSync(lock)) fs.unlinkSync(lock);
  appendJson(rootPath(root, 'records', 'cleanup.jsonl'), {
    seat: input.seat, generation: input.generation,
  });
  return {removed: [state.runtime.pidfile, state.runtime.lock]};
}

function dispatch(root, binding, operation, input) {
  if (!binding.allowedOperations.includes(operation)) {
    deny('operation is not available to this binding');
  }
  switch (operation) {
    case 'work':
      return performWork(root, binding, input);
    case 'message':
      return performMessage(root, binding, input);
    case 'record':
      return performRecord(root, input);
    case 'issue_view':
    case 'pr_view':
      return performIssueView(input);
    case 'seat_status':
      return performSeatStatus(root, input);
    case 'seat_start':
      return performSeatStart(root, input);
    case 'git_maintenance':
      return performGitMaintenance(root, input);
    case 'sync_origin_clone':
      return performSyncOriginClone(root, input);
    case 'proxy_git_write':
      return performProxyGitWrite(root, input);
    case 'team_provision':
      return performTeamProvision(root, input);
    case 'bot_collaborator':
      return performBotCollaborator(root, input);
    case 'apply_decision':
      return performApplyDecision(root, input);
    case 'stale_runtime_cleanup':
      return performStaleCleanup(root, input);
    case 'packet':
      return makePacket(root, binding);
    default:
      deny('operation is not known');
  }
}

function requestPath(root, requestId) {
  if (!/^[A-Za-z0-9._-]+$/u.test(requestId)) deny('request ID is not safe');
  return rootPath(root, 'requests', requestId + '.json');
}

function invoke(rootArg, sessionId, operation, rawInput, options) {
  const root = mustBeAbsoluteRoot(rootArg);
  const binding = validateBinding(root, sessionId, options.agentArg);
  let input = {};
  if (rawInput) {
    try {
      input = JSON.parse(rawInput);
    } catch (error) {
      deny('structured input is not valid JSON');
    }
  }
  ensureObject(input, 'structured input');
  const requestId = input.requestId || randomId('request');
  const requestFile = requestPath(root, requestId);
  if (fs.existsSync(requestFile)) {
    const previous = readJson(requestFile);
    if (previous.status === 'outcome_unknown') {
      output({status: 'outcome_unknown', requestId, duplicate: true});
      process.exitCode = 3;
      return;
    }
    if (previous.status === 'completed') {
      output({status: 'completed', requestId, duplicate: true});
      return;
    }
  }
  writeJson(requestFile, {status: 'not_started', requestId, operation});
  let result;
  try {
    result = dispatch(root, binding, operation, input);
  } catch (error) {
    if (error instanceof Denied) {
      writeJson(requestFile, {status: 'not_started', requestId, operation, reason: error.message});
    }
    throw error;
  }
  if (input.simulateTimeoutAfter === true) {
    writeJson(requestFile, {
      status: 'outcome_unknown', requestId, operation,
      reason: 'timeout after operation boundary',
    });
    output({status: 'outcome_unknown', requestId});
    process.exitCode = 3;
    return;
  }
  writeJson(requestFile, {status: 'completed', requestId, operation});
  output({status: 'completed', requestId, result});
}

function makePacket(root, binding) {
  const state = loadState(root);
  const markerFile = rootPath(root, 'markers', 'direct-work.jsonl');
  const markerOutput = fs.existsSync(markerFile) ?
    fs.readFileSync(markerFile, 'utf8') : '<empty>';
  return {
    value: {
      pmDirectMarkerCount: markerOutput === '<empty>' ? 0 :
        markerOutput.trim().split('\n').filter(Boolean).length,
      activeBinding: binding ? binding.agent : null,
      fixtureVersion: FIXTURE_VERSION,
    },
    cutoff: 'fixed-head:' + state.fixedHead,
    source: 'tests/fixtures/pm-broker/broker.js',
    command: 'node tests/fixtures/pm-broker/broker.js packet <fixture-root>',
    cliVersion: process.version,
    launchProfileDigest: binding ? digest({
      policyVersion: binding.policyVersion,
      effectiveTools: binding.effectiveTools,
      allowedOperations: binding.allowedOperations,
    }) : null,
    fixedHead: state.fixedHead,
    effectiveTools: binding ? binding.effectiveTools : [],
    rawMarkerOutput: markerOutput,
    hook: binding ? binding.hook : null,
  };
}

function packet(rootArg) {
  const root = mustBeAbsoluteRoot(rootArg);
  const sessions = readJson(rootPath(root, 'sessions.json'));
  const active = sessions.filter((session) => session.active);
  let binding = null;
  if (active.length > 0) {
    binding = readJson(rootPath(root, 'bindings', active[active.length - 1].sessionId + '.json'));
  }
  output(makePacket(root, binding));
}

function main(argv) {
  const [command, ...args] = argv;
  if (command === 'init') {
    output(init(args[0], args[1]));
    return;
  }
  if (command === 'launch') {
    const root = args[0];
    const actor = args[1];
    const options = {hook: '', resume: ''};
    for (let i = 2; i < args.length; i += 1) {
      if (args[i] === '--hook') options.hook = args[++i];
      else if (args[i] === '--resume') options.resume = args[++i];
      else deny('unknown launcher option');
    }
    output(launch(root, actor, options));
    return;
  }
  if (command === 'close') {
    output(closeSession(args[0], args[1]));
    return;
  }
  if (command === 'invoke') {
    const options = {agentArg: ''};
    const root = args[0];
    const session = args[1];
    const operation = args[2];
    const rawInput = args[3] || '';
    for (let i = 4; i < args.length; i += 1) {
      if (args[i] === '--agent-arg') options.agentArg = args[++i];
      else deny('unknown broker option');
    }
    invoke(root, session, operation, rawInput, options);
    return;
  }
  if (command === 'packet') {
    packet(args[0]);
    return;
  }
  die('unknown command');
}

try {
  main(process.argv.slice(2));
} catch (error) {
  if (error instanceof Denied) {
    process.stderr.write(JSON.stringify({status: 'denied', reason: error.message}) + '\n');
    process.exitCode = 1;
  } else {
    process.stderr.write('pm-broker-fixture: ' + error.message + '\n');
    process.exitCode = 2;
  }
}
