---
type: Plan
title: "Issue #176 R3: delivery capability JSON の観測不能を unknown として保存する"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/176"
root_cause: R3
root_issue: "https://github.com/kappaseijin/agmsg/issues/176"
timestamp: "2026-08-26T14:30:23+09:00"
---

# Issue #176 R3: delivery capability JSON の運用計器

## 結論

R3 の残り 6 候補のうち、今回の実装対象は `delivery.sh status <type> <project> --format json` が公開する capability JSON だけである。

1 PR = **「capability JSON は、設定・roster・bridge metadata を読めなかった事実を確認済みの `false` として出力せず、`"unknown"` と evidence reason を出す」**という一主張にする。

対象は候補 2、3、4 を同じ証拠契約としてまとめる。
候補 1、5、6 は運用計器の producer ではないか、新しい状態契約を導入しなければ区別できないため、変更しない。

```mermaid
flowchart TD
    A[delivery.sh status --format json] --> B[delivery-capability.sh]
    B --> C{設定を読めたか}
    C -->|no| U1[configuration: unknown\ndeliverable: unknown]
    C -->|yes| D{roster を読めたか}
    D -->|no| U2[registration: unknown\nseats: []\ndeliverable: unknown]
    D -->|yes| E{Codex metadata を読めたか}
    E -->|no| U3[bridge: unknown\ndeliverable: unknown]
    E -->|yes, concrete absence/staleness| F[false と concrete reason]
    U1 --> G[consumer: non-dispatchable]
    U2 --> G
    U3 --> G
    F --> G
```

`"unknown"` は配送許可ではない。
internal consumer の `team-work-reconciler` は既に `deliverable === true && liveness === "alive"` だけを dispatchable とし、`false` と `"unknown"` をともに non-dispatchable にする。
この変更は fail-open ではなく、誤った「boot_required / disabled」という診断を未確認の状態へ戻す。

## 現在の証拠

`agmsg_delivery_capability_json_delivery_value` はすでに JSON boolean の `true` / `false` と文字列の `"unknown"` を出力できる。
README の Delivery capability JSON 節も、`"unknown"` を「current receiver を証明できないため non-dispatchable」と定義する。
従って schemaVersion の増分、field の追加・削除、既存 JSON consumer の fallback は不要である。

しかし producer には次の three-way collapse が残る。

| 候補 | 現在の collapse | 変更後の観測 | `false` のまま残す正対照 |
| --- | --- | --- | --- |
| 2. event-hook configuration | hooks path resolver failure、settings file 不在、SQLite JSON query failure を `off` にし、seat が `deliverable=false` になる | `configuration: unknown` と reason、seat/aggregate `runtime`・`liveness`・`deliverable` を `unknown` | 読める valid JSON に agmsg hook が無い状態は `configuration: off`、`deliverable=false` |
| 3. roster | `identities.sh ... || true` が query failure を zero-seat と同一視し、`registration: missing/no_registered_seat`、`deliverable=false` にする | `registration: unknown/roster_query_failed`、`seats: []`、aggregate を `unknown` | 成功した roster query が empty のときだけ `registration: missing/no_registered_seat`、`deliverable=false` |
| 4. Codex bridge metadata | `agmsg_delivery_capability_file_field` の empty value が read failure と missing/mismatch を同一視し、`metadata_mismatch`、`deliverable=false` にする | `bridge: unknown/bridge_metadata_unreadable`、seat/aggregate を `unknown` | metadata file 不在、readable だが field が空・不一致、dead PID は現在どおり `stale` / `false` |

候補 2 の `agmsg_delivery_capability_config_mode` は mode 文字列だけを返す。
このままでは resolver failure の `unknown` でさえ `agmsg_delivery_capability_seat` の「monitor/both 以外」分岐で `false` へ再び丸められる。
実装は config probe の state と reason を保持し、`unknown` のとき runtime probe を成功と解釈せず、seat と aggregate を unknown にする必要がある。

候補 3 は pipeline の成功/失敗を `|| true` で捨てている。
`identities.sh` の終了 status を `sort` の成功と区別して捕捉し、query failure のときは status JSON 自体は exit 0 で返すが、observer state を unknown にする。
公開 JSON が返せない transport error と、返せるが roster を観測できない状態を混同しないためである。

候補 4 は metadata を一度だけ読んで、読取失敗と readable content を区別する helper へ置き換える。
readable かつ empty/malformed field は writer 契約に反する concrete mismatch なので #162 の fail-closed `false` を維持する。
metadata file 自体の不存在も `bridge_metadata_missing` という concrete absence のまま `false` とする。

## Consumer と ABI の確認

| consumer | 現在の利用 | 今回の保証 |
| --- | --- | --- |
| `scripts/lib/team-work-reconciler.js` | exact seat を 1 件取得し、`deliverable === true && liveness === "alive"` のみ `available` | `"unknown"` は `unknown` となり dispatch しない。zero seat も既に unknown であることを regression test で固定する |
| `README.md` | public command と `true` / `false` / `"unknown"` の consumer rule | field shape と `schemaVersion: 1` は不変。`false` は concrete reason に限ることを追記する |
| `tests/test_delivery.bats` | JSON producer の status contract | 各観測不能経路と、同じ field shape の confirmed counter-control を別 test にする |
| `app/src-tauri/src/agmsg.rs` | human `delivery.sh status` の `mode:` 第一行だけを取得 | `--format json` を読まないため今回の ABI consumer ではない。変更しない |
| `scripts/doctor.sh` | human status の `mode:` を local variable へ読む | JSON producer ではない。変更しない |

互換性の注意点は、外部 consumer が `Boolean(payload.deliverable)` のように JSON string を truthy と誤解している場合である。
これは既存の `"unknown"` contract を破る consumer であり、今回それに合わせて `false` を残すことはしない。
README は strict boolean `true` comparison を明記し、internal consumer の同じ比較を test で固定する。

## 今回変更しない候補

| 候補 | 決定 | 理由・reopen 条件 |
| --- | --- | --- |
| 1. `delivery-rulefile.sh` の opencode / grok-build rule file | 変更しない | `set off` が file を削除するため、file 不在から explicit off と未設定を観測だけで区別できない。persistent marker または versioned state contract を新設する別設計が必要になったとき reopen する |
| 5. `app/src-tauri/src/agmsg.rs:agmsg_delivery_mode` | 変更しない | human output の caller-side fallback であり capability JSON を出力しない。type/project を渡す normal status は mode line を出す。no-line/zero の実例が得られ、desktop state contract を変える必要があるときだけ別 PR にする |
| 6. `scripts/doctor.sh` の initial `mode=off` | 変更しない | JSON producer ではなく human status consumer である。status command の non-zero は既に warning にし、#175 の `mode: unknown` は boring/off へ collapse しない。zero/no-line contract の実例が得られたときだけ別監査にする |

この除外は「R3 の残りを全て直す」ではなく、breaker が定めた「`delivery.sh status` に現れる運用計器だけ」という boundary を守るためである。

## 実装計画

### 1. configuration evidence を三値として保持する

`scripts/lib/delivery-capability.sh` の config probe を、mode だけでなく `state` と `reason` を保持する形にする。

- event-hook type の hooks path resolver failure は `unknown/hooks_path_unresolvable`。
- settings file 不在、実際の read/query failure、invalid JSON はそれぞれ `unknown` の reason を持つ。SQLite query の `|| echo 0` は残さない。
- valid JSON を読めて agmsg hook が無いと確認できたときだけ `off` とする。
- configuration state が unknown の seat は configuration evidence を最初に置き、runtime/liveness/deliverable を全て unknown にする。`monitor` / `both` / `turn` / `off` の confirmed branch は現状のままにする。

### 2. roster query の failure と empty を分ける

`agmsg_delivery_capability_json` で `identities.sh` と sorting の exit status を capture する。

- query failure なら `seats: []` を保ちつつ、aggregate evidence は `registration/unknown/roster_query_failed`、runtime/liveness/deliverable は unknown にする。
- success + zero seat だけが `registration/missing/no_registered_seat` と `false` になる。
- partial output を成功として採用しない。query error が 1 回でもあれば output を空へ clean-break し、unknown evidence のみにする。

### 3. Codex metadata read を一回で判定する

`agmsg_delivery_capability_file_field` を、reader error と readable content を区別できる helper に置換する。

- metadata read error は `bridge/unknown/bridge_metadata_unreadable` と three unknown values を返し、PID/identity comparison へ進まない。
- readable content の missing field、identity/project/type/PID mismatch は既存の `bridge/stale/metadata_mismatch`、`false` を維持する。
- metadata file 不在、empty pidfile、dead PID も既存の concrete stale/missing evidence を保つ。
- legacy `team=` / `name=` parser、metadata writer、bridge restart、role-session binding、pane-liveness には触れない。

### 4. README と consumer regression を同期する

README の capability JSON 節へ、`false` が only concrete disabled/missing/stale evidence であり、config/roster/metadata の read failure は `"unknown"` になることを追記する。
`team-work-reconciler` の source logic は strict comparison のままとし、以下の test を追加する。

- fake capability JSON が `deliverable: "unknown"` の exact seat を返すと `state: not_dispatchable`、`delivery.status: unknown`、ledger unchanged。
- fake capability JSON が seats empty を返すと同じく unknown/non-dispatchable。
- `deliverable: false` の confirmed state は existing `boot_required` test を維持する。

## 検証計画

| test | 期待値 | 偽陰性を防ぐ対照 |
| --- | --- | --- |
| missing hooks file + registered Claude seat | config unknown、seat/aggregate unknown、non-dispatchable | `set off` 済みの readable valid settings は configuration off / false |
| invalid hooks JSON + registered Codex seat | config unknown、false ではない | valid JSON で no agmsg hook は false のまま |
| `identities.sh` が non-zero | seats empty、registration unknown、aggregate unknown | successful empty roster は no_registered_seat / false |
| live Codex fixture の meta read だけを failure にする | bridge unknown、false ではない | 同じ fixtureを reader normal で run すると true。readable identity mismatch は stale / false |
| reconciler consumes unknown/empty seats | ledger unchanged、not_dispatchable、delivery status unknown | exact true/alive fixture は dispatching。false fixture は existing boot_required |

metadata read failure は permission bit に依存させない。
test-local `cat` wrapper を PATH の先頭に置き、target meta path だけ non-zero にし、他の path は real `cat` へ委譲する。
実装側が metadata を実際に read-error として検出していることを deterministic に確認できる。

KILLED mutation は、各 producer failure branch を一時的に旧 `false` / `metadata_mismatch` / `no_registered_seat` へ戻す。
対応する unknown assertion と reconciler ledger-invariance assertion が RED にならなければならない。
直ちに unknown 実装へ復元して GREEN を取る。

実装後の最低 verification は次である。

```bash
bash -n scripts/lib/delivery-capability.sh scripts/delivery.sh
bats tests/test_delivery.bats
bats tests/test_team_work_reconciler.bats
rtk git diff --check <base>..<head>
```

fixed HEAD で CI と `agmsg_reviewer_claude` の全差分一括 review を取得する。

## PR と ownership boundary

producer は `agmsg_programmer2_codex`（Lane B）、formal reviewer は `agmsg_reviewer_claude`、root cause は R3、root Issue は #176 とする。

ただし、実装対象の `scripts/lib/delivery-capability.sh`、`scripts/lib/team-work-reconciler.js`、対応 Bats tests、README は Lane B の当初列挙 (`scripts/watch.sh`、`despawn.sh`、`delivery.sh`) の外にある。
従って manager は、起点依頼でこの 4 領域を `agmsg_programmer2_codex` の明示的な Lane B ownership に追加してから実装を開始する。
ownership が更新されるまで、source edit・PR 作成・review request は開始しない。

PR description は `Part of #176` を使い、epic を閉じる `Closes #176` を書かない。

## 非対象

- human `delivery.sh status` の #175 contract、rule-file persistence、desktop app fallback、doctor no-line audit
- schemaVersion の増分、新しい JSON field、legacy parser、external client fallback
- production hooks / roster / bridge metadata の変更、watcher/bridge restart、global skill installation
- R3 全体の close。merge 後も Issue #176 は root tracking を継続する
