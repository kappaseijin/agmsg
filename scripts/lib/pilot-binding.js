#!/usr/bin/env node

/*
 * G4-A pilot launcher binding contract (#236/#385).
 *
 * This module owns the pilot-only generation/binding schema.
 *
 * Important compatibility rule:
 *   scripts/session-identity.js remains unchanged. Pilot bindings keep
 *   schemaVersion=1 and form a strict superset of its existing schema by
 *   adding providerCommit.
 *
 * Path policy:
 *
 *   project
 *     Canonical filesystem identity. realpath() is intentional.
 *
 *   skillDir / teamsDir / runtime state / bindings / guard / broker
 *     Lexical absolute paths. Ancestor symlinks such as macOS
 *     /var -> /private/var must not rewrite these paths.
 *
 *   individual protected files/directories
 *     The filesystem entry itself must not be a symlink where explicitly
 *     checked. Ancestor symlinks are permitted.
 *
 * Commands:
 *
 *   prepare
 *     Publishes the next immutable generation binding and advances state.json.
 *     Exactly these nine options are required:
 *
 *       --mode
 *       --skill-dir
 *       --teams-dir
 *       --team
 *       --project
 *       --pid
 *       --pid-start
 *       --state-file
 *       --bindings-dir
 *
 *     AGMSG_PM_PILOT_SESSION_ID is also required:
 *
 *       fresh  -> launcher-generated UUID; this exact value is published.
 *       resume -> must equal the latest accepted binding's sessionId.
 *
 *   inspect
 *     Read-only resume inspection. It validates the current pilot identity,
 *     state/history, provider/policy/profile/guard/broker bindings and returns
 *     only the latest binding's sessionId on stdout. It never publishes.
 *
 *     Required options:
 *
 *       --skill-dir
 *       --teams-dir
 *       --team
 *       --project
 *       --state-file
 *       --bindings-dir
 */

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const {execFileSync} = require('child_process');

const SCHEMA_VERSION = 1;

const PILOT_AGENT = 'agmsg_pm_pilot_claude';
const PILOT_TYPE = 'claude-code';

const POLICY_VERSION = 'pm-pilot-pretool-v1';

const PROVIDER_COMMIT =
  '0b2117c5f91f7950cc196e52edb188748adfa50a';

const DIGEST_RE =
  /^sha256:[0-9a-f]{64}$/u;

const GENERATION_RE =
  /^[1-9][0-9]*$/u;

const PID_RE =
  /^[1-9][0-9]*$/u;

const CONTROL_RE =
  /[\u0000-\u001f\u007f]/u;

class PilotBindingError extends Error {
  constructor(reason) {
    super(reason);
    this.name = 'PilotBindingError';
    this.reason = reason;
  }
}

const fail = (reason) => {
  throw new PilotBindingError(reason);
};

const isObject = (value) =>
  Boolean(value) &&
  typeof value === 'object' &&
  !Array.isArray(value);

const text = (value, reason) => {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    CONTROL_RE.test(value)
  ) {
    fail(reason);
  }

  return value;
};

const positiveDecimal = (
  value,
  reason,
  pattern = GENERATION_RE,
) => {
  const result = text(value, reason);

  if (!pattern.test(result)) {
    fail(reason);
  }

  const number = Number(result);

  if (
    !Number.isSafeInteger(number) ||
    number < 1
  ) {
    fail(reason);
  }

  return result;
};

const readJsonStrict = (
  file,
  reason,
) => {
  let raw;

  try {
    raw = fs.readFileSync(
      file,
      'utf8',
    );
  } catch (_) {
    fail(reason);
  }

  try {
    return JSON.parse(raw);
  } catch (_) {
    fail(reason);
  }
};

/*
 * Canonical path is deliberately used only where physical filesystem
 * identity is part of the contract, most importantly project identity.
 */
const canonicalPath = (
  value,
  reason = 'path_unreadable',
) => {
  text(value, reason);

  try {
    return fs.realpathSync.native(value);
  } catch (_) {
    fail(reason);
  }
};

const lexicalAbsolutePath = (
  value,
  reason = 'path_invalid',
) => {
  return path.resolve(
    text(value, reason),
  );
};

/*
 * Contract digest:
 *
 *   sha256:<lowercase SHA-256 of raw file bytes>
 */
const sha256File = (filePath) => {
  text(
    filePath,
    'digest_path_invalid',
  );

  let bytes;

  try {
    bytes = fs.readFileSync(filePath);
  } catch (_) {
    fail('digest_unreadable');
  }

  return (
    'sha256:' +
    crypto
      .createHash('sha256')
      .update(bytes)
      .digest('hex')
  );
};

/*
 * Write a complete JSON document through a same-directory temporary file and
 * atomically rename it over the destination.
 *
 * The final file always ends in exactly one LF.
 */
const atomicWriteJson = (
  filePath,
  value,
) => {
  text(
    filePath,
    'atomic_path_invalid',
  );

  const directory =
    path.dirname(filePath);

  const base =
    path.basename(filePath);

  let directoryStat;

  try {
    directoryStat =
      fs.statSync(directory);
  } catch (_) {
    fail(
      'atomic_parent_unavailable',
    );
  }

  if (!directoryStat.isDirectory()) {
    fail(
      'atomic_parent_unavailable',
    );
  }

  const temporary =
    path.join(
      directory,
      `.${base}.tmp.${process.pid}.` +
        crypto.randomBytes(12).toString('hex'),
    );

  const body =
    `${JSON.stringify(value)}\n`;

  let fd;

  try {
    fd = fs.openSync(
      temporary,
      'wx',
      0o600,
    );

    fs.writeFileSync(
      fd,
      body,
      {
        encoding: 'utf8',
      },
    );

    fs.fsyncSync(fd);

    fs.closeSync(fd);
    fd = undefined;

    fs.renameSync(
      temporary,
      filePath,
    );
  } catch (_) {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd);
      } catch (_) {
        // best effort
      }
    }

    try {
      fs.unlinkSync(temporary);
    } catch (_) {
      // best effort
    }

    fail('atomic_write_failed');
  }
};

const registrationList = (record) => {
  if (!isObject(record)) {
    return [];
  }

  if (
    Array.isArray(record.registrations)
  ) {
    return record.registrations;
  }

  /*
   * Compatibility with the legacy single-registration representation used
   * by session-identity.js.
   */
  if (
    record.type !== undefined ||
    record.project !== undefined
  ) {
    return [record];
  }

  return [];
};

/*
 * The teams directory itself is a lexical namespace owned by the launcher.
 * Do not realpath() it: doing so would rewrite harmless ancestor symlinks such
 * as /var -> /private/var.
 *
 * Project identity remains canonical.
 */
const validateRosterUnique = ({
  teamsDir,
  team,
  agent = PILOT_AGENT,
  type = PILOT_TYPE,
  project,
}) => {
  const expectedTeamsDir =
    lexicalAbsolutePath(
      teamsDir,
      'roster_unreadable',
    );

  let teamsLstat;

  try {
    teamsLstat =
      fs.lstatSync(
        expectedTeamsDir,
      );
  } catch (_) {
    fail(
      'roster_unreadable',
    );
  }

  if (
    !teamsLstat.isDirectory() ||
    teamsLstat.isSymbolicLink()
  ) {
    fail(
      'roster_unreadable',
    );
  }

  const expectedTeam =
    text(
      team,
      'team_invalid',
    );

  const expectedAgent =
    text(
      agent,
      'agent_invalid',
    );

  const expectedType =
    text(
      type,
      'type_invalid',
    );

  const expectedProject =
    canonicalPath(
      project,
      'project_unreadable',
    );

  let entries;

  try {
    entries =
      fs
        .readdirSync(expectedTeamsDir)
        .sort();
  } catch (_) {
    fail(
      'roster_unreadable',
    );
  }

  const matches = [];

  for (const entry of entries) {
    const configFile =
      path.join(
        expectedTeamsDir,
        entry,
        'config.json',
      );

    if (!fs.existsSync(configFile)) {
      continue;
    }

    const config =
      readJsonStrict(
        configFile,
        'roster_unreadable',
      );

    if (
      !isObject(config) ||
      typeof config.name !== 'string'
    ) {
      fail(
        'roster_schema_invalid',
      );
    }

    if (config.name !== expectedTeam) {
      continue;
    }

    if (
      !isObject(config.agents)
    ) {
      fail(
        'roster_schema_invalid',
      );
    }

    const record =
      config.agents[expectedAgent];

    if (record === undefined) {
      continue;
    }

    if (!isObject(record)) {
      fail(
        'roster_schema_invalid',
      );
    }

    const registrations =
      registrationList(record);

    for (
      const registration
      of registrations
    ) {
      if (!isObject(registration)) {
        fail(
          'roster_schema_invalid',
        );
      }

      if (
        registration.type !==
          expectedType ||
        typeof registration.project !==
          'string'
      ) {
        continue;
      }

      const registrationProject =
        canonicalPath(
          registration.project,
          'roster_project_unreadable',
        );

      if (
        registrationProject ===
        expectedProject
      ) {
        matches.push({
          configFile,
          registration,
        });
      }
    }
  }

  if (matches.length !== 1) {
    fail(
      matches.length === 0
        ? 'roster_no_match'
        : 'roster_multiple_match',
    );
  }

  return {
    team: expectedTeam,
    agent: expectedAgent,
    type: expectedType,
    project: expectedProject,

    /*
     * Preserve the lexical teamsDir namespace.
     */
    configFile:
      matches[0].configFile,

    registration:
      matches[0].registration,
  };
};

const validateDigest = (
  value,
  reason,
) => {
  const digest =
    text(value, reason);

  if (!DIGEST_RE.test(digest)) {
    fail(reason);
  }

  return digest;
};

const assertExpected = (
  actual,
  expected,
  reason,
) => {
  if (
    expected !== undefined &&
    actual !== expected
  ) {
    fail(reason);
  }
};

/*
 * Validate the complete G4-A pilot binding schema.
 *
 * project remains physical/canonical identity.
 *
 * Runtime binding/state paths are not stored inside the binding itself, so
 * lexical-path policy does not change the existing session-identity schema.
 */
const validateBinding = (
  binding,
  expected = {},
) => {
  if (
    !isObject(binding) ||
    binding.schemaVersion !==
      SCHEMA_VERSION
  ) {
    fail(
      'binding_schema_invalid',
    );
  }

  const normalized = {
    schemaVersion:
      SCHEMA_VERSION,

    team:
      text(
        binding.team,
        'binding_team_invalid',
      ),

    agent:
      text(
        binding.agent,
        'binding_agent_invalid',
      ),

    type:
      text(
        binding.type,
        'binding_type_invalid',
      ),

    project:
      canonicalPath(
        text(
          binding.project,
          'binding_project_invalid',
        ),
        'binding_project_unreadable',
      ),

    sessionId:
      text(
        binding.sessionId,
        'binding_sessionId_invalid',
      ),

    generation:
      positiveDecimal(
        binding.generation,
        'binding_generation_invalid',
      ),

    pid:
      positiveDecimal(
        binding.pid,
        'binding_pid_invalid',
        PID_RE,
      ),

    pidStart:
      text(
        binding.pidStart,
        'binding_pidStart_invalid',
      ),

    profileDigest:
      validateDigest(
        binding.profileDigest,
        'binding_profileDigest_invalid',
      ),

    policyVersion:
      text(
        binding.policyVersion,
        'binding_policyVersion_invalid',
      ),

    guardDigest:
      validateDigest(
        binding.guardDigest,
        'binding_guardDigest_invalid',
      ),

    brokerDigest:
      validateDigest(
        binding.brokerDigest,
        'binding_brokerDigest_invalid',
      ),

    providerCommit:
      text(
        binding.providerCommit,
        'binding_providerCommit_invalid',
      ),
  };

  /*
   * Project is intentionally canonical in serialized bindings.
   */
  if (
    binding.project !==
    normalized.project
  ) {
    fail(
      'binding_project_mismatch',
    );
  }

  if (
    normalized.agent !== PILOT_AGENT ||
    normalized.type !== PILOT_TYPE
  ) {
    fail(
      'binding_launcher_mismatch',
    );
  }

  if (
    normalized.policyVersion !==
    POLICY_VERSION
  ) {
    fail(
      'binding_policy_mismatch',
    );
  }

  if (
    normalized.providerCommit !==
    PROVIDER_COMMIT
  ) {
    fail(
      'binding_provider_mismatch',
    );
  }

  if (
    !/^[0-9a-f]{40}$/u.test(
      normalized.providerCommit,
    )
  ) {
    fail(
      'binding_providerCommit_invalid',
    );
  }

  assertExpected(
    normalized.team,
    expected.team,
    'binding_launcher_mismatch',
  );

  assertExpected(
    normalized.agent,
    expected.agent,
    'binding_launcher_mismatch',
  );

  assertExpected(
    normalized.type,
    expected.type,
    'binding_launcher_mismatch',
  );

  assertExpected(
    normalized.project,
    expected.project,
    'binding_project_mismatch',
  );

  assertExpected(
    normalized.sessionId,
    expected.sessionId,
    'binding_session_mismatch',
  );

  assertExpected(
    normalized.generation,
    expected.generation,
    'binding_generation_mismatch',
  );

  assertExpected(
    normalized.pid,
    expected.pid,
    'binding_pid_mismatch',
  );

  assertExpected(
    normalized.pidStart,
    expected.pidStart,
    'binding_process_mismatch',
  );

  assertExpected(
    normalized.profileDigest,
    expected.profileDigest,
    'binding_profile_digest_mismatch',
  );

  assertExpected(
    normalized.guardDigest,
    expected.guardDigest,
    'binding_guard_digest_mismatch',
  );

  assertExpected(
    normalized.brokerDigest,
    expected.brokerDigest,
    'binding_broker_digest_mismatch',
  );

  assertExpected(
    normalized.policyVersion,
    expected.policyVersion,
    'binding_policy_mismatch',
  );

  assertExpected(
    normalized.providerCommit,
    expected.providerCommit,
    'binding_provider_mismatch',
  );

  return normalized;
};

/*
 * A missing state file means no accepted generation yet.
 *
 * Present but unreadable/malformed state is fail-closed.
 */
const readState = (stateFile) => {
  text(
    stateFile,
    'state_path_invalid',
  );

  try {
    return JSON.parse(
      fs.readFileSync(
        stateFile,
        'utf8',
      ),
    );
  } catch (error) {
    if (
      error &&
      error.code === 'ENOENT'
    ) {
      return null;
    }

    fail(
      'state_unreadable',
    );
  }
};

const validateState = (
  state,
  expected = {},
) => {
  if (
    !isObject(state) ||
    state.schemaVersion !==
      SCHEMA_VERSION
  ) {
    fail(
      'state_schema_invalid',
    );
  }

  const normalized = {
    schemaVersion:
      SCHEMA_VERSION,

    team:
      text(
        state.team,
        'state_team_invalid',
      ),

    agent:
      text(
        state.agent,
        'state_agent_invalid',
      ),

    type:
      text(
        state.type,
        'state_type_invalid',
      ),

    project:
      canonicalPath(
        text(
          state.project,
          'state_project_invalid',
        ),
        'state_project_unreadable',
      ),

    latestGeneration:
      positiveDecimal(
        state.latestGeneration,
        'state_generation_invalid',
      ),

    /*
     * latestBinding is lexical by design.
     */
    latestBinding:
      lexicalAbsolutePath(
        state.latestBinding,
        'state_binding_invalid',
      ),
  };

  /*
   * Project remains canonical in serialized state.
   */
  if (
    state.project !==
    normalized.project
  ) {
    fail(
      'state_project_mismatch',
    );
  }

  /*
   * latestBinding must already be lexical-absolute-normalized. Do not
   * canonicalize it through realpath().
   */
  if (
    state.latestBinding !==
    normalized.latestBinding
  ) {
    fail(
      'state_binding_mismatch',
    );
  }

  if (
    normalized.agent !== PILOT_AGENT ||
    normalized.type !== PILOT_TYPE
  ) {
    fail(
      'state_launcher_mismatch',
    );
  }

  assertExpected(
    normalized.team,
    expected.team,
    'state_launcher_mismatch',
  );

  assertExpected(
    normalized.agent,
    expected.agent,
    'state_launcher_mismatch',
  );

  assertExpected(
    normalized.type,
    expected.type,
    'state_launcher_mismatch',
  );

  assertExpected(
    normalized.project,
    expected.project,
    'state_project_mismatch',
  );

  assertExpected(
    normalized.latestGeneration,
    expected.latestGeneration,
    'state_generation_mismatch',
  );

  assertExpected(
    normalized.latestBinding,
    expected.latestBinding,
    'state_binding_mismatch',
  );

  return normalized;
};

const bindingFiles = (
  bindingsDir,
) => {
  let entries;

  try {
    entries =
      fs.readdirSync(bindingsDir);
  } catch (_) {
    fail(
      'bindings_unreadable',
    );
  }

  const generations = [];

  for (const entry of entries) {
    if (
      !/^[1-9][0-9]*\.json$/u.test(
        entry,
      )
    ) {
      if (entry.startsWith('.')) {
        fail(
          'bindings_incomplete_publish',
        );
      }

      continue;
    }

    const generation =
      Number(
        entry.slice(0, -5),
      );

    if (
      !Number.isSafeInteger(
        generation,
      ) ||
      generation < 1
    ) {
      fail(
        'bindings_schema_invalid',
      );
    }

    generations.push(
      generation,
    );
  }

  generations.sort(
    (a, b) => a - b,
  );

  return generations;
};

/*
 * Validate immutable history against lexical state/binding paths.
 */
const validateHistory = ({
  bindingsDir,
  state,
  expected,
}) => {
  const generations =
    bindingFiles(bindingsDir);

  if (state === null) {
    if (
      generations.length !== 0
    ) {
      fail(
        'bindings_orphaned',
      );
    }

    return null;
  }

  const normalizedState =
    validateState(
      state,
      expected,
    );

  const latest =
    Number(
      normalizedState.latestGeneration,
    );

  if (
    generations.length !== latest
  ) {
    fail(
      'bindings_history_mismatch',
    );
  }

  for (
    let index = 0;
    index < generations.length;
    index += 1
  ) {
    if (
      generations[index] !==
      index + 1
    ) {
      fail(
        'bindings_history_mismatch',
      );
    }
  }

  /*
   * Deliberately lexical: do not realpath() either side.
   */
  const expectedLatestBinding =
    path.resolve(
      bindingsDir,
      `${latest}.json`,
    );

  if (
    normalizedState.latestBinding !==
    expectedLatestBinding
  ) {
    fail(
      'state_binding_mismatch',
    );
  }

  const latestRaw =
    readJsonStrict(
      expectedLatestBinding,
      'binding_unreadable',
    );

  const latestBinding =
    validateBinding(
      latestRaw,
      {
        team:
          normalizedState.team,

        agent:
          normalizedState.agent,

        type:
          normalizedState.type,

        project:
          normalizedState.project,

        generation:
          normalizedState.latestGeneration,
      },
    );

  return {
    state:
      normalizedState,

    binding:
      latestBinding,

    bindingFile:
      expectedLatestBinding,
  };
};

const processIsSameLiveInstance = (
  binding,
  sessionIdentityScript,
) => {
  const pid =
    Number(binding.pid);

  try {
    process.kill(
      pid,
      0,
    );
  } catch (error) {
    if (
      error &&
      error.code === 'ESRCH'
    ) {
      return false;
    }

    fail(
      'process_liveness_unavailable',
    );
  }

  let observed;

  try {
    observed =
      execFileSync(
        process.execPath,
        [
          sessionIdentityScript,
          '--process-start',
          binding.pid,
        ],
        {
          encoding: 'utf8',
          stdio: [
            'ignore',
            'pipe',
            'ignore',
          ],
          timeout: 2000,
        },
      ).trim();
  } catch (_) {
    fail(
      'process_liveness_unavailable',
    );
  }

  return (
    observed ===
    binding.pidStart
  );
};

const nextGeneration = (
  previous,
) => {
  if (previous === null) {
    return '1';
  }

  const current =
    Number(previous);

  if (
    !Number.isSafeInteger(current) ||
    current < 1 ||
    current >= Number.MAX_SAFE_INTEGER
  ) {
    fail(
      'generation_exhausted',
    );
  }

  return String(
    current + 1,
  );
};

const buildBinding = ({
  identity,
  sessionId,
  generation,
  pid,
  pidStart,
  digests,
}) =>
  validateBinding(
    {
      schemaVersion:
        SCHEMA_VERSION,

      team:
        identity.team,

      agent:
        identity.agent,

      type:
        identity.type,

      project:
        identity.project,

      sessionId,

      generation,

      pid,

      pidStart,

      profileDigest:
        digests.profileDigest,

      policyVersion:
        POLICY_VERSION,

      guardDigest:
        digests.guardDigest,

      brokerDigest:
        digests.brokerDigest,

      providerCommit:
        PROVIDER_COMMIT,
    },
    {
      team:
        identity.team,

      agent:
        PILOT_AGENT,

      type:
        PILOT_TYPE,

      project:
        identity.project,

      sessionId,

      generation,

      pid,

      pidStart,

      profileDigest:
        digests.profileDigest,

      guardDigest:
        digests.guardDigest,

      brokerDigest:
        digests.brokerDigest,

      policyVersion:
        POLICY_VERSION,

      providerCommit:
        PROVIDER_COMMIT,
    },
  );

const buildState = ({
  identity,
  generation,
  bindingFile,
}) =>
  validateState(
    {
      schemaVersion:
        SCHEMA_VERSION,

      team:
        identity.team,

      agent:
        identity.agent,

      type:
        identity.type,

      project:
        identity.project,

      latestGeneration:
        generation,

      latestBinding:
        path.resolve(
          bindingFile,
        ),
    },
    {
      team:
        identity.team,

      agent:
        PILOT_AGENT,

      type:
        PILOT_TYPE,

      project:
        identity.project,

      latestGeneration:
        generation,

      latestBinding:
        path.resolve(
          bindingFile,
        ),
    },
  );

const prepareFresh = ({
  stateFile,
  bindingsDir,
  identity,
  digests,
  pid,
  pidStart,
  sessionId,
  sessionIdentityScript,
}) => {
  const requestedSessionId =
    text(
      sessionId,
      'pilot_session_id_invalid',
    );

  const currentState =
    readState(stateFile);

  const history =
    validateHistory({
      bindingsDir,
      state: currentState,
      expected: identity,
    });

  if (
    history &&
    processIsSameLiveInstance(
      history.binding,
      sessionIdentityScript,
    )
  ) {
    fail(
      'binding_process_live',
    );
  }

  const generation =
    nextGeneration(
      history
        ? history.state.latestGeneration
        : null,
    );

  const bindingFile =
    path.resolve(
      bindingsDir,
      `${generation}.json`,
    );

  const binding =
    buildBinding({
      identity,
      sessionId:
        requestedSessionId,
      generation,
      pid,
      pidStart,
      digests,
    });

  const state =
    buildState({
      identity,
      generation,
      bindingFile,
    });

  return {
    binding,
    state,
    bindingFile,
  };
};

const prepareResume = ({
  stateFile,
  bindingsDir,
  identity,
  digests,
  pid,
  pidStart,
  sessionId,
  sessionIdentityScript,
}) => {
  const requestedSessionId =
    text(
      sessionId,
      'pilot_session_id_invalid',
    );

  const currentState =
    readState(stateFile);

  if (currentState === null) {
    fail(
      'resume_state_missing',
    );
  }

  const history =
    validateHistory({
      bindingsDir,
      state: currentState,
      expected: identity,
    });

  if (!history) {
    fail(
      'resume_state_missing',
    );
  }

  if (
    requestedSessionId !==
    history.binding.sessionId
  ) {
    fail(
      'resume_session_mismatch',
    );
  }

  validateBinding(
    history.binding,
    {
      team:
        identity.team,

      agent:
        identity.agent,

      type:
        identity.type,

      project:
        identity.project,

      sessionId:
        requestedSessionId,

      profileDigest:
        digests.profileDigest,

      guardDigest:
        digests.guardDigest,

      brokerDigest:
        digests.brokerDigest,

      policyVersion:
        POLICY_VERSION,

      providerCommit:
        PROVIDER_COMMIT,
    },
  );

  if (
    processIsSameLiveInstance(
      history.binding,
      sessionIdentityScript,
    )
  ) {
    fail(
      'binding_process_live',
    );
  }

  const generation =
    nextGeneration(
      history.state.latestGeneration,
    );

  const bindingFile =
    path.resolve(
      bindingsDir,
      `${generation}.json`,
    );

  const binding =
    buildBinding({
      identity,
      sessionId:
        history.binding.sessionId,
      generation,
      pid,
      pidStart,
      digests,
    });

  const state =
    buildState({
      identity,
      generation,
      bindingFile,
    });

  return {
    binding,
    state,
    bindingFile,
  };
};

const publishGeneration = ({
  bindingFile,
  binding,
  stateFile,
  state,
}) => {
  if (
    fs.existsSync(bindingFile)
  ) {
    fail(
      'binding_generation_exists',
    );
  }

  atomicWriteJson(
    bindingFile,
    binding,
  );

  atomicWriteJson(
    stateFile,
    state,
  );
};

const parseOptionPairs = ({
  argv,
  command,
  allowed,
  required,
}) => {
  if (
    argv[0] !== command
  ) {
    fail(
      'command_invalid',
    );
  }

  if (
    (argv.length - 1) % 2 !== 0
  ) {
    fail(
      'arguments_invalid',
    );
  }

  const options =
    Object.create(null);

  for (
    let index = 1;
    index < argv.length;
    index += 2
  ) {
    const key =
      argv[index];

    const value =
      argv[index + 1];

    if (
      typeof key !== 'string' ||
      !key.startsWith('--') ||
      value === undefined
    ) {
      fail(
        'arguments_invalid',
      );
    }

    const name =
      key.slice(2);

    if (
      !allowed.has(name) ||
      Object.prototype.hasOwnProperty.call(
        options,
        name,
      )
    ) {
      fail(
        'arguments_invalid',
      );
    }

    options[name] =
      value;
  }

  for (const name of required) {
    if (
      options[name] === undefined
    ) {
      fail(
        'arguments_invalid',
      );
    }
  }

  return options;
};

const parsePrepareArgs = (argv) => {
  const names = [
    'mode',
    'skill-dir',
    'teams-dir',
    'team',
    'project',
    'pid',
    'pid-start',
    'state-file',
    'bindings-dir',
  ];

  const allowed =
    new Set(names);

  const required =
    new Set(names);

  const options =
    parseOptionPairs({
      argv,
      command: 'prepare',
      allowed,
      required,
    });

  if (
    options.mode !== 'fresh' &&
    options.mode !== 'resume'
  ) {
    fail(
      'mode_invalid',
    );
  }

  return options;
};

const parseInspectArgs = (argv) => {
  const names = [
    'skill-dir',
    'teams-dir',
    'team',
    'project',
    'state-file',
    'bindings-dir',
  ];

  const allowed =
    new Set(names);

  const required =
    new Set(names);

  return parseOptionPairs({
    argv,
    command: 'inspect',
    allowed,
    required,
  });
};

/*
 * Validate that the file itself is a regular non-symlink entry, but preserve
 * the lexical absolute path instead of returning realpath().
 *
 * This is critical for /var and /tmp based trees on macOS.
 */
const statRegularFile = (
  file,
  reason,
) => {
  const lexical =
    lexicalAbsolutePath(
      file,
      reason,
    );

  let lstat;

  try {
    lstat =
      fs.lstatSync(
        lexical,
      );
  } catch (_) {
    fail(reason);
  }

  if (
    !lstat.isFile() ||
    lstat.isSymbolicLink()
  ) {
    fail(reason);
  }

  return lexical;
};

const statRealDirectory = (
  directory,
  reason,
) => {
  const lexical =
    lexicalAbsolutePath(
      directory,
      reason,
    );

  let lstat;

  try {
    lstat =
      fs.lstatSync(
        lexical,
      );
  } catch (_) {
    fail(reason);
  }

  if (
    !lstat.isDirectory() ||
    lstat.isSymbolicLink()
  ) {
    fail(reason);
  }

  return lexical;
};

const prepareFilesystemContext = ({
  options,
}) => {
  /*
   * Keep launcher/runtime namespace lexical.
   *
   * Do not canonicalize skillDir or teamsDir: macOS standard ancestor
   * symlinks must not change values handed back to pilot-launcher.sh.
   */
  const skillDir =
    lexicalAbsolutePath(
      options['skill-dir'],
      'skill_dir_unreadable',
    );

  const teamsDir =
    lexicalAbsolutePath(
      options['teams-dir'],
      'roster_unreadable',
    );

  /*
   * Project identity is intentionally different: it is a physical filesystem
   * identity shared with session-identity.js.
   */
  const project =
    canonicalPath(
      options.project,
      'project_unreadable',
    );

  const stateFile =
    lexicalAbsolutePath(
      options['state-file'],
      'state_path_invalid',
    );

  const bindingsDir =
    lexicalAbsolutePath(
      options['bindings-dir'],
      'bindings_unreadable',
    );

  /*
   * Verify the namespace roots themselves exist and are not symlink entries.
   * Ancestors are permitted to be symlinks.
   */
  statRealDirectory(
    skillDir,
    'skill_dir_unreadable',
  );

  statRealDirectory(
    teamsDir,
    'roster_unreadable',
  );

  const stateParent =
    path.dirname(stateFile);

  statRealDirectory(
    stateParent,
    'state_parent_unreadable',
  );

  statRealDirectory(
    bindingsDir,
    'bindings_unreadable',
  );

  const identity = {
    team:
      text(
        options.team,
        'team_invalid',
      ),

    agent:
      PILOT_AGENT,

    type:
      PILOT_TYPE,

    project,
  };

  return {
    skillDir,
    teamsDir,
    project,
    stateFile,
    bindingsDir,
    identity,
  };
};

const loadContractFiles = ({
  skillDir,
  project,
}) => {
  /*
   * project is canonical, so profilePath is naturally canonical as well.
   */
  const profilePath =
    statRegularFile(
      path.join(
        project,
        '.claude',
        'settings.local.json',
      ),
      'profile_unavailable',
    );

  /*
   * skillDir is lexical, so guard/broker/sessionIdentity paths remain lexical.
   */
  const guardPath =
    statRegularFile(
      path.join(
        skillDir,
        'scripts',
        'pm-pilot-pretool-guard',
      ),
      'guard_unavailable',
    );

  const brokerPath =
    statRegularFile(
      path.join(
        skillDir,
        'scripts',
        'p2-consumer-broker.sh',
      ),
      'broker_unavailable',
    );

  const sessionIdentityScript =
    statRegularFile(
      path.join(
        skillDir,
        'scripts',
        'session-identity.js',
      ),
      'session_identity_unavailable',
    );

  return {
    profilePath,
    guardPath,
    brokerPath,
    sessionIdentityScript,

    digests: {
      profileDigest:
        sha256File(profilePath),

      guardDigest:
        sha256File(guardPath),

      brokerDigest:
        sha256File(brokerPath),
    },
  };
};

const revalidateMutableInputs = ({
  teamsDir,
  identity,
  profilePath,
  guardPath,
  brokerPath,
  digests,
}) => {
  validateRosterUnique({
    teamsDir,
    ...identity,
  });

  if (
    sha256File(profilePath) !==
    digests.profileDigest
  ) {
    fail(
      'profile_changed',
    );
  }

  if (
    sha256File(guardPath) !==
    digests.guardDigest
  ) {
    fail(
      'guard_changed',
    );
  }

  if (
    sha256File(brokerPath) !==
    digests.brokerDigest
  ) {
    fail(
      'broker_changed',
    );
  }
};

const inspectResume = ({
  stateFile,
  bindingsDir,
  identity,
  digests,
  sessionIdentityScript,
}) => {
  const currentState =
    readState(stateFile);

  if (currentState === null) {
    fail(
      'resume_state_missing',
    );
  }

  const history =
    validateHistory({
      bindingsDir,
      state: currentState,
      expected: identity,
    });

  if (!history) {
    fail(
      'resume_state_missing',
    );
  }

  validateBinding(
    history.binding,
    {
      team:
        identity.team,

      agent:
        identity.agent,

      type:
        identity.type,

      project:
        identity.project,

      profileDigest:
        digests.profileDigest,

      guardDigest:
        digests.guardDigest,

      brokerDigest:
        digests.brokerDigest,

      policyVersion:
        POLICY_VERSION,

      providerCommit:
        PROVIDER_COMMIT,
    },
  );

  if (
    processIsSameLiveInstance(
      history.binding,
      sessionIdentityScript,
    )
  ) {
    fail(
      'binding_process_live',
    );
  }

  return history.binding.sessionId;
};

const runPrepareCli = (argv) => {
  const options =
    parsePrepareArgs(argv);

  const context =
    prepareFilesystemContext({
      options,
    });

  const {
    skillDir,
    teamsDir,
    project,
    stateFile,
    bindingsDir,
    identity,
  } = context;

  validateRosterUnique({
    teamsDir,
    ...identity,
  });

  const contract =
    loadContractFiles({
      skillDir,
      project,
    });

  const {
    profilePath,
    guardPath,
    brokerPath,
    sessionIdentityScript,
    digests,
  } = contract;

  const pid =
    positiveDecimal(
      options.pid,
      'process_pid_invalid',
      PID_RE,
    );

  const pidStart =
    text(
      options['pid-start'],
      'process_start_invalid',
    );

  const sessionId =
    text(
      process.env
        .AGMSG_PM_PILOT_SESSION_ID ||
        '',
      'pilot_session_id_invalid',
    );

  let observedPidStart;

  try {
    observedPidStart =
      execFileSync(
        process.execPath,
        [
          sessionIdentityScript,
          '--process-start',
          pid,
        ],
        {
          encoding: 'utf8',
          stdio: [
            'ignore',
            'pipe',
            'ignore',
          ],
          timeout: 2000,
        },
      ).trim();
  } catch (_) {
    fail(
      'process_start_unavailable',
    );
  }

  if (
    observedPidStart !== pidStart
  ) {
    fail(
      'process_start_mismatch',
    );
  }

  const prepare =
    options.mode === 'fresh'
      ? prepareFresh
      : prepareResume;

  const prepared =
    prepare({
      stateFile,
      bindingsDir,
      identity,
      digests,
      pid,
      pidStart,
      sessionId,
      sessionIdentityScript,
    });

  revalidateMutableInputs({
    teamsDir,
    identity,
    profilePath,
    guardPath,
    brokerPath,
    digests,
  });

  publishGeneration({
    bindingFile:
      prepared.bindingFile,

    binding:
      prepared.binding,

    stateFile,

    state:
      prepared.state,
  });

  return {
    status: 'ok',

    mode:
      options.mode,

    team:
      identity.team,

    agent:
      identity.agent,

    type:
      identity.type,

    project:
      identity.project,

    sessionId:
      prepared.binding.sessionId,

    generation:
      prepared.binding.generation,

    pid:
      prepared.binding.pid,

    pidStart:
      prepared.binding.pidStart,

    /*
     * Runtime paths remain lexical.
     */
    bindingFile:
      prepared.bindingFile,

    stateFile,

    /*
     * profilePath is based on canonical project.
     */
    profilePath,

    profileDigest:
      prepared.binding.profileDigest,

    /*
     * guard/broker paths remain based on lexical skillDir.
     */
    guardPath,

    guardDigest:
      prepared.binding.guardDigest,

    brokerPath,

    brokerDigest:
      prepared.binding.brokerDigest,

    policyVersion:
      prepared.binding.policyVersion,

    providerCommit:
      prepared.binding.providerCommit,
  };
};

const runInspectCli = (argv) => {
  const options =
    parseInspectArgs(argv);

  const context =
    prepareFilesystemContext({
      options,
    });

  const {
    skillDir,
    teamsDir,
    project,
    stateFile,
    bindingsDir,
    identity,
  } = context;

  validateRosterUnique({
    teamsDir,
    ...identity,
  });

  const contract =
    loadContractFiles({
      skillDir,
      project,
    });

  const {
    profilePath,
    guardPath,
    brokerPath,
    sessionIdentityScript,
    digests,
  } = contract;

  revalidateMutableInputs({
    teamsDir,
    identity,
    profilePath,
    guardPath,
    brokerPath,
    digests,
  });

  const sessionId =
    inspectResume({
      stateFile,
      bindingsDir,
      identity,
      digests,
      sessionIdentityScript,
    });

  revalidateMutableInputs({
    teamsDir,
    identity,
    profilePath,
    guardPath,
    brokerPath,
    digests,
  });

  const historyAgain =
    validateHistory({
      bindingsDir,
      state:
        readState(stateFile),
      expected:
        identity,
    });

  if (
    !historyAgain ||
    historyAgain.binding.sessionId !==
      sessionId
  ) {
    fail(
      'resume_state_changed',
    );
  }

  validateBinding(
    historyAgain.binding,
    {
      team:
        identity.team,

      agent:
        identity.agent,

      type:
        identity.type,

      project:
        identity.project,

      sessionId,

      profileDigest:
        digests.profileDigest,

      guardDigest:
        digests.guardDigest,

      brokerDigest:
        digests.brokerDigest,

      policyVersion:
        POLICY_VERSION,

      providerCommit:
        PROVIDER_COMMIT,
    },
  );

  return sessionId;
};

if (require.main === module) {
  try {
    const command =
      process.argv[2];

    if (command === 'prepare') {
      const result =
        runPrepareCli(
          process.argv.slice(2),
        );

      process.stdout.write(
        `${JSON.stringify(result)}\n`,
      );
    } else if (
      command === 'inspect'
    ) {
      const sessionId =
        runInspectCli(
          process.argv.slice(2),
        );

      process.stdout.write(
        `${sessionId}\n`,
      );
    } else {
      fail(
        'command_invalid',
      );
    }
  } catch (error) {
    const reason =
      error instanceof PilotBindingError
        ? error.reason
        : 'internal_error';

    process.stderr.write(
      `${JSON.stringify({
        status: 'unidentifiable',
        reason,
      })}\n`,
    );

    process.exit(1);
  }
}

module.exports = {
  POLICY_VERSION,
  PILOT_AGENT,
  PILOT_TYPE,
  PROVIDER_COMMIT,
  PilotBindingError,
  atomicWriteJson,
  canonicalPath,
  inspectResume,
  prepareFresh,
  prepareResume,
  publishGeneration,
  readState,
  runInspectCli,
  runPrepareCli,
  sha256File,
  validateBinding,
  validateRosterUnique,
  validateState,
};