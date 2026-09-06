---
type: Implementation
title: Issue 253 actas and active-name watcher supplement
status: independent-review-pending
timestamp: 2026-09-06T11:05:00+09:00
---

Implement the accepted PR270 role-lock comparison in a separate PR. Preserve PR269 code and observations. Reuse its isolated fixture/public history infrastructure and fixed F/O local objects. Start two owned synthetic host processes with distinct real PIDs, claim the receiver role through public actas-claim.sh, then start active-name watch.sh only for successful claims. Retain explicit held responses, startup errors, and missing target-poll readiness as different observations. Test both different SIDs and the same SID with different PIDs. A held response must name the observed winning instance, not merely return nonzero.

Record the public message ID and both command/host identities. Limited role-path success requires one ready winner, one matching explicit held loser, exactly one observed handoff, and complete ID correlation. Missing startup/ready evidence is unknown. Role locks do not establish message ACK, status, recovery, host fencing or general exactly-once delivery. Provider implementation, B3/P2 adoption and Issue closure are excluded. README impact: none.

## Reproduction and producer observation

```sh
python3 scripts/issue253_actas.py --local-repo /path/to/local/repository --output /tmp/new-actas-packet
python3 -m unittest discover -s tests -p 'test_issue253*.py' -v
```

Requires local F/O objects from PR270, Git/tar/Python/Bash/sqlite3. No remote fetch is performed. Public actas claims run concurrently for two already-started synthetic host processes. The loser is not advanced into watch after a legitimate held refusal. The winner starts the real active-name watcher; readiness requires a successful target-pair storage poll and a still-live watcher. Observation instrumentation preserves the actual query, its output and status. Host processes are bounded sleep fixtures, not LLM or Monitor hosts.

Confirmed: 2026-09-06T11:07:45+09:00. Packet `/tmp/agmsg253-actas-final/`; report SHA256 `5be94decca7dc9bb18772b88c8434f88caddba1ffcb9724d8c4ed0740284fe53`. Harness SHA256 `3d3309375a052b40d75ed283f8cf4aead5fa0ee2e8385beae3cbb308525ec044`.

| Case | Role ownership | Display path | Boundaries | Handoffs |
| --- | --- | --- | --- | --- |
| F-different-sid | pass | unknown | ready, held | [0, 0] |
| F-same-sid | pass | pass | held, ready | [0, 1] |
| O-different-sid | pass | pass | held, ready | [0, 1] |
| O-same-sid | pass | unknown | held, ready | [0, 0] |

All four cases retain an exact opaque public message ID and two distinct real host PIDs. The same-SID cases use the same SID string with different PIDs. Every held response names the observed winning composite instance. Each case also executes an unregistered public actas request and classifies it as startup failure, not held. No hidden watcher is credited to the rejected instance.

A zero-display window remains unknown even when role ownership was observed. Wait for initial display is bounded at five seconds, followed by 0.4 seconds of observation. The two successful display paths are bounded observations, not general delivery guarantees. Message ACK, message status, recovery and general exclusion all remain unknown. PR269 code, results and acceptance are unchanged.

Sixteen unit tests (eight retained, eight supplemental) passed. Two temporary mutations were killed: treating missing readiness as held and ignoring the held owner. Raw evidence: `/tmp/agmsg253-actas-all-unit.log`, `/tmp/agmsg253-actas-mutations.json`. CI runs the new eight boundary tests only; local fixed-object comparison is independently repeated by verifier. Fault markers and command PID/rc/stdout/stderr plus injected driver before/after hashes are retained in the packet.

The limited role-lock path was observed to distinguish one successful owner from one explicit matching held refusal in all four cases. Display and message-level guarantees remain separate. Independent verifier evidence and full-diff Claude review are pending; only after those stages does PM relay the supported scope and remaining unknowns to breaker. No provider extension or P2 acceptance is proposed by this implementation.
