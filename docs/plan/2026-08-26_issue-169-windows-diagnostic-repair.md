---
type: Plan
title: "Issue #169B: Windows teardown 診断を実際に読める証跡へ修復する"
status: decided
root_cause: R2
root_issue: "https://github.com/kappaseijin/agmsg/issues/176"
issue: "https://github.com/kappaseijin/agmsg/issues/169"
base_commit: "495abe302afac13060ae6ba87f8e370e58b997b6"
producer: agmsg_programmer_codex
reviewer: agmsg_reviewer_claude
timestamp: "2026-08-26T12:23:51+09:00"
---

# Issue #169B: Windows teardown 診断を実際に読める証跡へ修復する

## 結論

1 PR の主張は「Windows native の `teardown_test_env` failure packet が、実際の `rm` stderr と PID namespace を誤認しない process evidence を出す」である。cleanup policy は変えない。

run `32921756892` / job `98036575162` は、`rm` が exit 1、残留 leaf が `db/messages.db` であることを示した。一方で `rm stderr` の `rc=1` は元の `rm` ではなく、capture file を読む `cat` の失敗である。現行は `$TEST_SKILL_DIR/.teardown.{stdout,stderr}.$$` へ redirect しており、`rm -rf "$TEST_SKILL_DIR"` が途中まで消すと、そのcapture fileも消える。これはWindows redirectの失敗ではなく、削除対象内に証跡を置いた配置の失敗である。

また、artifactの PID は Git Bash が `$!` で作った MSYS PID である。現行 `instance-id.sh` はこの PID を `tasklist` へ渡すと生存中でも未検出になることを明記している。したがって packet の `tasklist: No tasks...` は app-server の死亡証拠ではない。`tasklist` を主判定へ昇格しない。

## 失敗時の契約

| 観測 | 成功 predicate | failure時の扱い |
| --- | --- | --- |
| `rm` stdout / stderr | deletion targetの**外側**に置いたcapture fileを、`rm` 完了後に読める | capture不能はmarker化し、`rm` は必ず一度実行して元のnonzeroを返す |
| MSYS artifact PID | `ps -l -p <msys-pid>` と `/proc/<pid>/cmdline` が読めれば、そのnamespaceのrecordを出す | `tasklist` の未検出をdeadと書かない |
| native process 補助照会 | MSYS PIDから得た `WINPID` に対する `tasklist`、及びtest rootで絞った `Win32_Process` record | helper failureはmarker。teardown statusを置換しない |
| file-handle owner | このPRでは判定しない | ownerが出ない限り、kill / wait / retry を追加しない |

## 実装境界

### 1. `rm` stderrを保持する

`teardown_test_env` は `BATS_TEST_TMPDIR` 配下に、`TEST_SKILL_DIR` の外側で一意なcapture directoryを作る。そこへstdout/stderrをredirectし、`rm` の終了後に読む。BATSがこの外側temporary rootの寿命を管理するため、削除中のfixtureがcaptureを消すことはない。

capture directoryを作れないときも、その診断不能を理由にteardownを早期returnしない。`rm -rf "$TEST_SKILL_DIR"` は一度実行し、capture unavailable markerと元の `rm` statusを返す。正常teardownは従来どおりheaderもdiagnosticも出さない。`rm || true`、retry、sleep、kill、targetの変更は入れない。

### 2. Windows PIDの二つのnamespaceを分けて記録する

Windows / Git Bashでは、artifactが指す `$!` のMSYS PIDをまず `ps -l -p` と `/proc/<pid>/cmdline` で記録する。`ps -l` の `WINPID` が得られたときだけ、同じnative PIDに対して `tasklist /FI "PID eq <WINPID>"` を補助記録する。出力は `msys_pid` と `winpid` を別字段として示し、raw MSYS PIDをtasklistへ渡した「not found」をliveness結論に使わない。

test rootをargvに含む未知のnative processを探す場合だけ、Windows標準のPowerShell `Get-CimInstance Win32_Process` を使い、`ProcessId`、`ParentProcessId`、`Name`、`CommandLine`を出す。検索needleはGit Bash pathと`cygpath -m`のnative pathの両方にし、当該test rootを含むrecordだけに限定する。PowerShell/CIMの失敗はmarkerであり、全process dumpやenvironment全量を出さない。

`tasklist` は現在実行中のprocessをPID filterで出せるが、file handleのownerを出すものではない。[Microsoft tasklist](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist) と [Win32_Process の CommandLine](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-process) がこの分担の一次資料である。

### 3. handle ownerは無いものとして扱う

標準 `openfiles` はlocal handle trackingが既定で無効で、有効化にはrebootが必要である。hosted runnerのglobal設定・rebootはこのテストの範囲外である。[openfiles](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/openfiles) は今回使わない。file handleを検索できる [Sysinternals Handle](https://learn.microsoft.com/en-us/sysinternals/downloads/handle) は別downloadかつadmin privilegeを要するため、新しいCI依存にも加えない。

よってこのPRはhandle ownerを推定しない。次のnative packetで、actual `rm` stderr、MSYS/native PIDの対応、test-root process recordが揃ってもownerが出なければ、#169をevidence-neededのまま保持する。specific ownerが出た時だけ、別PRでそのPIDに限定したstop/waitを設計する。

## 受入れと対照

| 観点 | 正の対照 | KILLED / 負の対照 |
| --- | --- | --- |
| capture配置 | `rm` stubがtarget内の旧`.teardown.*`を消してからstderr sentinelとexit 23を返しても、sentinelを出す | capture parentを`TEST_SKILL_DIR`へ戻すとsentinelが消え、assertionがredになる |
| status保持 | forced failureは23を返す | `return "$rm_rc"` を0へ変えるとstatus assertionがredになる |
| MSYS PID | `ps -l`が返すMSYS PID / WINPIDを別表示し、tasklistにはWINPIDだけを渡すstubbed control | raw MSYS PIDをtasklistへ渡すmutationはargument/record assertionがredになる |
| normal teardown | writable fixtureはstatus 0、diagnosticなし、fixtureなし | failure-only headerをsuccessにも出す変更はquiet assertionがredになる |

`tests/test_fixture_helpers.bats` に Windows-native名のcapture regressionを追加し、`.github/workflows/tests.yml` の既存 `windows runtime (#567)` targetへ同fileを追加する。macOS/Linuxでのskipは正の対照に数えない。Windows CIでこのtestが実行され、partial deletionを模した`rm` stubについて、実際のGit Bash redirect・temporary pathを通るstderr sentinelが出ることがnative acceptanceである。

実際のWindows OSが返す `Device or resource busy` 等の文言は、次の#567再発でのみ観測できる。PR merge時にそれを既観測と主張しない。reopen判定は「packetにactual `rm` stderrがあり、`cannot read captured rm stderr` markerが無い」である。

## 一PRの範囲と順序

| 含める | 含めない |
| --- | --- |
| `tests/test_helper.bash` のcapture / snapshot diagnostic | `rm` policy、retry、sleep、kill、handle tool download |
| `tests/test_fixture_helpers.bats` のcapture・MSYS namespace回帰 | `test_codex_monitor.bats` / `test_codex_bridge_launcher.bats` のkill/wait対象変更 |
| Windows focused targetへのfixture test追加 | production launcher、monitor、SQLite schema、global runner設定 |
| この設計書 | #169のroot cause修復そのもの |

Lane Aの順序は **R1横断PR → #169B → R5-T1 → R5-P3** とする。R1、#169B、R5-T1はいずれも`tests/test_helper.bash`に触れるため並行実装しない。#169Bはlive failure packetに基づく小さなdiagnostic repairであり、R1 merge後のHEADへrebaseしてから着手する。R5-P3の`actas-lock.sh`とは直接衝突しない。

## 限界

この設計は `messages.db` を掴むownerを発見したとは主張しない。run `32921756892`の`tasklist`未検出は、MSYS/native PID namespaceの差を除外していないため死亡証拠ではない。今回得るのは、次のfailureから原因を限定するための読めるstderrと、誤ったPID結論を避けるdiagnostic contractである。
