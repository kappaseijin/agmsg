---
type: Implementation
title: Issue 253 isolated public-path comparison harness
status: fixed-head-review-pending
timestamp: 2026-09-06T09:20:00+09:00
---

Implement PR257's fixed F/O public-helper comparison in disposable source copies and separate HOME/root/DB directories. Clear host AGMSG/HERDR/TMUX and agent detection variables. Never call installed production helpers for the experiment. Source acquisition accepts local Git objects only; no remote fetch by the harness.

Preserve each public command's stdout/stderr/rc and correlate unique application requestId with all matching public message IDs across complete, quiescent fixture history. A requestId is not a protocol ACK or substitute message ID. Distinct send/claim ID namespaces remain explicit.

Run #37 tests unmodified as a separate reference result. Cover ID chains, identical-content concurrency, receiver interruption/concurrency, owner/lease/resume, scope/payload, failed mark-read and incomplete history. Failure seams change only disposable copies at public helper dependencies. Compatibility status is independent of harness execution and fault-detector controls; unsupported/unknown must survive aggregation. Native Monitor/live three-connection coverage is not inferred from this fixture.

A source or required public capability missing from a route is recorded as unknown/unsupported rather than silently using the fork. No private consumer SQL, shadow lease/ack, live pilot, upstream access or production change. Required provider extensions are returned as capability gaps, not implemented here. README impact: none.

## Reproduce the isolated experiment

Requirements: Python 3, Bash, Git, tar, sqlite3 and Bats. Both fixed objects must already exist in a local repository; the harness never fetches a remote. Supply a new, nonexistent output directory.

```sh
python3 scripts/issue253_public_path.py --local-repo /path/to/local/repository --output /tmp/issue253-new-packet
python3 -m unittest discover -s tests -p test_issue253_public_path.py -v
python3 tests/issue253_mutations.py
```

The CI job runs evaluator controls and mutations only. It does not fetch the official object or claim to run the F/O helper experiment. Verifier must independently run the local-object experiment. Each helper command preserves arguments, fixture overrides, PID, rc, stdout and stderr. The report identifies source commits/trees/archive hashes and the exact harness source hash. Failure and observation seams affect only copied drivers; before/after hashes and seam bodies are retained. Watch poll readiness is recorded for the target team/receiver. Synthetic bounded host processes give different real PIDs to the same SID test; they are not LLM or Monitor hosts.

## Producer observation

Confirmed: 2026-09-06T09:37:43+09:00.

Packet: `/tmp/agmsg253-run-05/`; report SHA256 `254a1c2593270d08b97693f9afa3cab011daf401bcd73645798767e5b1c03256`; harness SHA256 `efb726c2e3e31d316b6487218d407f30085a5e65f9de414fe7da87cdb1c92896`. Command: `python3 scripts/issue253_public_path.py --local-repo /Users/kappa/Dropbox/data/dev/codex_monitor_agents/agmsg-programmer --output /tmp/agmsg253-run-05`.

| Control | F observed status | O observed status |
| --- | --- | --- |
| parallel_send_scope_payload | pass | pass |
| id_chain | pass | pass |
| truncated_history | unknown | unknown |
| missing_history | unknown | unknown |
| inbox_before_receive | pass | pass |
| inbox_interrupted | unknown | unknown |
| inbox_parallel | incompatible | incompatible |
| inbox_mark_failure | unknown | unknown |
| claim_owner_release_expiry | pass | unsupported |
| watch_normal | pass | pass |
| watch_parallel | pass | incompatible |
| watch_interrupted | unknown | unknown |
| watch_mark_failure | unknown | unknown |
| watch_same_sid_resume | unknown | incompatible |

The harness completed with rc0. The unchanged F #37 reference tests passed all seven named cases. Eight evaluator tests passed, and all four mutations (omitted IDs, latest-only matching, rc0-as-ACK, hidden receiver) were killed. Unit output: `/tmp/agmsg253-unit-final.log`; mutation output: `/tmp/agmsg253-mutations-final.json`.

Both routes preserved exact IDs across the closed, quiescent history ID chain and scope/newline/tab controls. These are application correlations; the fake record URL is not a real Issue or protocol ACK. The F claim ID is numeric while the public history ID is opaque; both namespaces and the history-ID status command are retained. No consumer SQL mapping is added.

Both public inbox helpers displayed the same corresponding message to two receivers at the stdout-before-mark barrier. In the watch parallel control F counts were `[1,0]`, O counts `[1,1]`, with both receivers polled before send. O same-SID/different-host-PID counts were `[1,1]`. F same-SID counts were `[0,1]`, but readiness was not observed for both receivers, so that case is unknown. No scheduling window is promoted to a general exclusion proof.

Both inbox mark-read failure injections returned rc0 and redisplayed the same message. O reported the failure on stderr; F did not. Both interrupted watchers redisplayed the message after the disposable consume failure seam was restored to original source. Persistent watch mark failure replayed on O but not F in the bounded observation; receipt and cursor effects are not conflated with ACK. All protocol ACK fields remain unknown without matching public evidence.

The aggregate compatibility field is conservative over every exercised helper, including the supplemental F inbox path: both aggregates are incompatible because the inbox duplicate control fails. This does not attribute the inbox defect to F's watcher gate or claim helper. The per-helper matrix is the source for minimum capability requests. O has independent watcher duplicate observations and lacks the tested public claim operation. The experiment does not approve a B3-independent P2 path.

## Capability gaps for architect to breaker

Return only the observed gaps: atomic receiver/handoff ownership for the O inbox/watch paths, machine-readable handoff/ACK failure and recovery semantics, and the public owner/release/expiry semantics required by #37 but absent as the tested O claim operation. Whether an alternative official public operation can provide those semantics remains unknown; no private equivalent is implemented. Closed-fixture API history correlation passed, so this experiment alone does not require a send-ID extension; live history completeness remains unproved.

Independent verifier and Claude formal reviewer acceptance are pending. No root fix, production installation, live team/DB mutation, upstream access/posting, P2 pilot or permanent fallback was performed. The earlier development packets are exploratory and are not acceptance evidence. README impact remains none because this is an internal diagnostic harness, not an adopted user-facing public contract.
