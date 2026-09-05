---
type: Implementation
title: Issue 261 explicit failure phase and parent status
status: fixed-head-diagnostic-pending
timestamp: 2026-09-06T08:50:00+09:00
---

The adopted contract SHA256 is ca19458037bdf4a6b91542dee762bac3e2c405e14d7828680690596576803794. Change only the target test and its diagnostic controls. Keep skip helper, production reaper, ownership predicates, fixture lifetime and deadlines unchanged.

The body runs in an unguarded asynchronous subshell with errexit enabled. Only the parent's wait is conditional, so it can record and return the child's status without putting the body in an if/OR evaluation context. The EXIT trap records the primary result before cleanup; individual cleanup signal/wait statuses and the parent wait result are separate. Markers contain phase, numeric status/PID and bounded kernel identity only, never full commands or secrets.

Controls execute the actual Bats test with isolated function substitutions: helper failure, naked body failure followed by a success marker, foreign PID contamination, reap failure, exit-wait failure and an unchanged positive control. An Ubuntu 24.04 job requires Bash5.2.21 and Bats1.13.0, runs the controls first, then one unmodified target/neighbour selection. Artifacts survive both success and failure. Local Bash5.3/Bats1.14 checks are development feedback only.

No root fix or unchanged rerun. Fixed-HEAD packet goes to architect/verifier/reviewer; non-reproduction does not determine the original cause. README impact: none.

## Development observations

The initial foreign-contamination control reached `foreign-alive` rather than failing at exclusion: the old `! pipeline` did not trigger errexit at that location. The diagnostic now explicitly exits 1 when the exclusion predicate fails, before calling the reaper. This establishes attribution for the injected condition, not the cause of the archived Ubuntu failure. Owned selection and production reaper code remain unchanged.

The parent reads the unique body's result from the packet and records the same phase/status with its completed wait. Missing or mismatched body results fail attribution instead of certifying success. The original cleanup function still ignores signal/wait nonzero statuses for its own return, as before; every such status is now separately recorded. A cleanup-end rc0 means function completion, not that every cleanup syscall succeeded.

Local control packets and Bats output: `/tmp/agmsg261-controls-second/`; command log: `/tmp/agmsg261-controls-second.log`. The local version is Bash5.3.12/Bats1.14.0, so this is not the required original-version confirmation. The dedicated job must first assert Bash5.2.21/Bats1.13.0 and pass all six controls before its target/neighbour observation. No WMI/Windows collector or #262 change is included.

All six local controls passed with matching parent/body phases and statuses (helper37, snapshot1, foreign-exclusion1, reap39, exit-wait41, positive0). Bash helper syntax, Python syntax and assertion baseline718 passed. Required pinned-version Ubuntu observation remains pending.
