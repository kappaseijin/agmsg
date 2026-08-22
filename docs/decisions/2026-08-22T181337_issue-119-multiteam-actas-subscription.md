---
type: Design
title: "Issue #119: actas名の複数チーム購読と排他ロック"
description: "actasした同名roleだけを、登録済み全チームの(team, agent)購読集合へ拡張し、claim・drop・readinessを同じ集合で整合させるTDD計画。"
tags:
  - agmsg
  - delivery
  - actas
  - subscription
  - issue-119
status: proposed
timestamp: "2026-08-22T18:13:37+09:00"
---

# Issue #119: actas名の複数チーム購読と排他ロック

## 結論

`/agmsg actas <name>` で起動する watcher は、開始projectだけでなく、同じruntimeで `<name>` が登録された**全team**の `(team, name)` を購読する。
通常の（`actas`なし）watcher は従来どおり `(project, runtime)` に属する登録だけを購読する。

この狭い分岐により、`scale_exporter` から `agmsg_pm_claude` 宛てに送った行を、`agmsg` projectで稼働中の `actas agmsg_pm_claude` watcher が受信できる。
一方、同名であっても未登録teamの行、別runtimeの登録、別agent名の行を名前だけで購読してはならない。

`actas` のpre-flight claim、live watcher、ready sentinel、`drop` のreleaseは、必ず同じ全team集合を用いる。
部分claim・drop後の他team lock残留・store障害を成功として扱わない。

## 現状と根拠

Issue #119 のverifier再現では、`scale_exporter -> agmsg_pm_claude`は約5秒のwatch観測中にstdout配信されず、`history.sh`は未読のままだった。
同じ受信者の`agmsg` team行はstdout配信され、既読になった。

原因は`identities.sh <project> <type>`がproject一致の登録だけを返すことにある。
`scripts/lib/subscription.sh`はその結果をactive nameでさらに絞り、`watch.sh`は同じproject限定のロジックを複製している。
storageのread cursorとSQL predicateはいずれも`(team, to_agent)`であるため、`scale_exporter/agmsg_pm_claude`は`agmsg/agmsg_pm_claude`の代替にならない。

## 採用する契約

### 1. 明示的なname全team resolver

`scripts/identities.sh`へ、既存の二引数ABIを保った明示的なquery modeを加える。

```text
identities.sh <project> <type>                         # 既存: project + type
identities.sh <project> <type> --name <name> --all-projects
```

後者だけが、全`teams/*/config.json`から「agent名が完全一致し、registrationの`type`が完全一致する」`team<TAB>agent`を重複なく安定順で返す。
`--all-projects`単独、未知option、空nameは非0で拒否する。
projectは呼出しABIとpath正規化のため受け取るが、all-projects modeでregistrationのproject一致には使わない。

これを通常watcherの暗黙の全team scanにしてはならない。
広い購読には必ず具体的なactive nameが必要である。

### 2. subscriptionとwatcher

`agmsg_subscription_pairs`は、`active_name`が空なら既存のproject resolverを、非空なら上記all-projects resolverを使う。
lockのskip、actas時のclaim、`agmsg_subscription_where`の`(team, to_agent)` predicateは維持する。

`scripts/watch.sh`は`lib/subscription.sh`をsourceし、重複したPAIRS絞込み・lock/claimブロックをこのhelperへ置換する。
これにより`watch.sh`とCodexの`watch-once.sh`でactive-nameの集合が分岐しない。
Codex bridgeが`--name`なしで渡す通常集合はproject scopedのままとし、既存のCodex actas receive-side caveatを拡張しない。

multi-teamの`PAIRS`に対し、DB-open healthcheckはteamを重複なく走査する。
storeが未作成なら従来どおり正常、存在するstoreを開けなければready sentinelを書かずstdoutへ一度だけerrorを出して終了する。

### 3. 排他とdrop

`actas-claim.sh`はall-projects resolverを使い、Monitorを止める前に全pairのlockをclaimする。
一つでも他sessionが保持していれば、同試行で取った全lockをreleaseし、`status=held team=... owner=...`で非0にする。
成功時だけ各teamのrole-session記録とsync autostartを実行する。

`reset.sh`の`drop`経路は、session ownerが持つ対象`name`のlockを**全teamで**releaseする。
registration削除自体は引き続き呼出しprojectだけに限定する。
これにより`drop`後に別projectの同名lockだけが残り、peer watcherを排除し続ける状態を防ぐ。
session-endのowner全lock releaseは既存契約どおり最終cleanupとして残す。

```mermaid
flowchart LR
  A[actas name] --> R[name + runtime の全team resolver]
  R --> C[全 pair をatomic claim]
  C -->|held| X[rollback and fail closed]
  C -->|all claimed| W[watch.sh / watch-once]
  W --> S[(team, agent) ごとのstore]
  S --> D[stdout delivery]
  A --> Q[drop]
  Q --> L[対象nameの全team lock release]
  L --> P[開始projectの通常購読へ復帰]
```

## TDD計画

実装は次のRED試験を先に追加し、各試験を最小変更でGREENにする。
外部送信、production roster変更、実ネットワークは使わず、`setup_test_env`のisolated skill copyとSQLiteだけを使う。

| ID | 試験先 | setup / 操作 | 期待と負の対照 |
| --- | --- | --- | --- |
| MT-1 | 新規`tests/test_identities.bats` | `team-a/alice`を`proj-a`、`team-b/alice`を`proj-b`、同名の別runtimeと別agentも登録 | all-projects + `--name alice`は`team-a/alice`,`team-b/alice`のみ。既存二引数は`proj-a`だけ。未登録name・all-projects単独は非0/空集合 |
| MT-2 | `tests/test_actas_integration.bats` | 二teamに同じ`alice`を登録し`actas-claim`を`proj-a`から実行 | 両lockが同じownerでclaimされる。一方を他ownerが保持した負の対照では非0、先に取ったlockも残らない |
| MT-3 | `tests/test_watch.bats` | `actas` modeのwatcherを`proj-a`から起動後、`team-a/alice`と`team-b/alice`へ新規送信 | 二つともstdout配信・各pair cursor進行。`team-b/bob`、別runtime、未登録teamの行はstdoutにもcursorにも出ない。Issue #119 verifierの修正前未配信観測を負の対照の一次証跡とする |
| MT-4 | `tests/test_watch_once.bats` | `--name alice`で二teamに未読を作り、`--team team-b`も別途指定 | name modeは二teamのpendingを検出し、`--team`は`team-b`だけに狭める。`--name`なしの通常bridge集合はproject外を増やさない |
| MT-5 | 新規`tests/test_reset.bats`と既存watch health試験 | multi-team claim後に`reset.sh ... alice <owner>`、さらにpartitionedな二team storeを用意 | project-aのregistrationだけを削除し、両teamのalice lockをreleaseする。二番目の既存storeを開けない負の対照ではreadyなし・非0で、片方だけのhealthcheckに後退しない |

MT-3のdeliveryはstartup前メッセージではなく、ready sentinel後に`send.sh --force`で作る。
受信しないことの主張はstdout不在だけにせず、対応pairのread cursorが進まないことを同時に確認する。
これで「たまたま遅かった」を不在と誤認しない。

## 実装順と変更範囲

1. MT-1を追加し、`identities.sh`のname全team query contractを実装する。
2. MT-2を追加し、`actas-claim.sh`を新resolverへ接続する。rollbackを先にGREENにする。
3. MT-3とMT-5を追加し、`subscription.sh`と`watch.sh`を共通集合へ接続する。per-team healthcheckとready sentinelのfail-closed順序を保つ。
4. MT-4を追加し、`watch-once.sh`の既存helper経路と`--team` narrowingを確認する。
5. `reset.sh`のtarget-name全team releaseを追加する。
6. `README.md`、`README.ja.md`、`docs/actas.md`を更新する。READMEには「actas時だけ同名登録teamを受信」「通常監視はproject限定」「dropはregistrationを現在projectからだけ外すが全team lockを解放」「未登録teamは購読しない」を自己完結で書く。

変更対象は`identities.sh`、`lib/subscription.sh`、`watch.sh`、`actas-claim.sh`、`reset.sh`、Codex one-shotの回帰試験、利用者資料に限定する。
send、history、inboxの名前だけを広く検索する実装、roster schema、remote protocol、delivery modeの既定値は非対象である。

## 検証コマンド

実装PRのheadで、少なくとも次を実行する。

```text
BATS_SHELL=/bin/bash bats --print-output-on-failure \
  tests/test_identities.bats tests/test_actas_integration.bats \
  tests/test_watch.bats tests/test_watch_once.bats tests/test_reset.bats
bash -n scripts/identities.sh scripts/lib/subscription.sh scripts/watch.sh \
  scripts/actas-claim.sh scripts/reset.sh
git diff --check
```

formal reviewerはMT-2のpartial-claim rollback、MT-3の二team cursor、MT-5のdrop後lock不存在を独立に確認する。
verifierはIssue #119の隔離再現と同じ条件で修正後stdout deliveryを計測し、`value / cutoff / source / command`と修正前のIssue commentを分けて報告する。

## 未確認・限界

この設計は、local rosterに登録済みの同名seatだけを対象にする。
登録されていない外部team、別runtimeの同名、remote server上にのみ存在する未pull rosterは受信対象にしない。
動的なroster追加は既存どおりwatcher再起動まで反映しない。
これは全team resolverを採用しても、watcherのsubscriptionを起動時に固定する現在の故意の性質である。

## 一次資料

- [Issue #119](https://github.com/kappaseijin/agmsg/issues/119)
- [Issue #119 verifier evidence](https://github.com/kappaseijin/agmsg/issues/119#issuecomment-5379440853)
- `scripts/identities.sh:1-58`
- `scripts/lib/subscription.sh:20-104`
- `scripts/watch.sh:462-575,642-800`
- `scripts/actas-claim.sh:1-143`
- `scripts/reset.sh:1-242`
- `scripts/session-end.sh:1-91`
