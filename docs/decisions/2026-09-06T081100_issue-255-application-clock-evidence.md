---
type: Evidence
title: "Issue #255: WMI event generation time does not establish process lifetime"
timestamp: "2026-09-06T08:11:00+09:00"
status: clock-contract-blocked
issue: "https://github.com/kappaseijin/agmsg/issues/255"
pr: "https://github.com/kappaseijin/agmsg/pull/263"
---

# Observation and boundary

Windows collection captured the root and short child's start/stop records, confirmed subscription ready/end, and saved the complete packet on preflight failure. The evaluator returned unknown rather than accepting the root registration against an event interval that starts after registration.

This is not evidence of a missing WMI start event. The events are present. The missing evidence is the correspondence between process lifetime and WMI event generation time. Microsoft documents TIME_CREATED as the time **the event was generated**, not a process CreationDate:
https://learn.microsoft.com/en-us/previous-versions/windows/desktop/krnlprov/win32-processstarttrace

| value | cutoff | source | command |
| --- | --- | --- | --- |
| fixed HEAD `cb0f903d9a4fbbe4ff8c7219e2b28ad122611cb0` | source of failed preflight | PR #263 / run 33997760834 | `rtk gh run view 33997760834 --repo kappaseijin/agmsg --json jobs` |
| adapter/generation controls passed, real preflight failed, artifact upload succeeded | target steps, not overall CI | job 101391202645 | `rtk gh api repos/kappaseijin/agmsg/actions/jobs/101391202645/logs --allow-escape-sequences` |
| root native PID 9820, CreationDate `2026-09-05T23:07:19.6706080+00:00` (quoted raw value) | process snapshot metadata | packet root-binding | GET artifact 9978574473 zip, parse JSONL |
| root binding actor FILETIME `134331232401849024` | registration observation | same record | same packet |
| root start event TIME_CREATED `134331232404239398` | generated after registration, difference 239.0374 ms | process-start PID 9820 | same packet |
| child PID 5948 start `134331232404239405`, stop `134331232404239407` | event-generation span 2 ticks; not an established process lifetime | child start/stop | same packet |
| collector exit 0; subject exit 0; quality unknown | statuses are independent | execution.json / packet.summary.json | same artifact |
| zip SHA256 `6c329e7e3d068344c879bfcd50da6683a1da0e1b80ae345d1a60b870b3eb9920` | downloaded bytes | `/tmp/agmsg263-second-artifact.zip` | Python hashlib.sha256 |
| packet SHA256 `26f649ab0c4726ca923efda3ab7fee2cd4719c3e2395493670ce536d92844f8d` | full extracted packet | `/tmp/agmsg263-second-artifact/preflight-0-66d57194a2414e32b645d5bc646153ad/packet.jsonl` | Python hashlib.sha256 |

# Current state

- PR remains draft. Three-condition comparison is not authorized by the verifier gate and has not run.
- Initial adapter test failure was removed by explicitly using Git Bash and LF script files. The second run's nine offline tests passed; this does not establish the clock contract.
- Native process event collection, packet persistence and unknown evaluation worked in this run. No claim of product cleanup success, full OS event coverage, actual child lifetime, or termination actor is made.
- Architect and PM received the evidence and a request to resolve the clock contract. No unchanged rerun or permissive evaluator change is justified.
- Local follow-up work adds explicit native ps path/LF output, startup-cost metadata and native wait boundaries; it does not resolve the clock issue.
- `run cancel` and `run download` were refused as unclassified by gh-write-owner-guard. No cancellation occurred. Artifact retrieval used the classified read-only GET API. This did not change repository or CI state.

# Download verification follow-up

All 15 downloaded files match the producer's SHA256 manifest after interpreting Windows path separators as path separators. The original replay implementation used native backslash paths literally on POSIX; a focused replay control reproduced this failure. The writer now emits portable slash paths and the reader validates either separator without permitting traversal. The corrupt-byte control rejects changed bytes. Local behavior controls: 10 pass.

This artifact is still a failed preflight: it contains only the rc=0 case and has no accepted gate.json. Byte preservation does not imply an accepted lifecycle/clock contract.
