---
type: Plan
title: "Issue #164: watch poll と actas ownership mutation を同一 gate で直列化する"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/164"
timestamp: "2026-08-25T23:31:58+09:00"
updated: "2026-08-26T01:05:58+09:00"
---

# Issue #164: watch poll と actas ownership mutation を同一 gate で直列化する

## 主張

`watch.sh` の一 pair の message fetch・表示・read cursor consume を、同じ pair の actas
ownership mutation と同一 gate で直列化する。

この gate の線形化点を ownership change の完了点とする。
したがって、handover 完了前に見えた message は旧 owner へ、handover 完了後に送られた
message は新 owner へだけ届く。旧 watcher が handover 後に表示・消費する経路をなくす。

## 現在の根拠

現行 `scripts/watch.sh` は poll の先頭で `actas_lock_state` を読み、約 60 行後に
`storage_watch_after`、`printf`、`storage_read_cursor_consume` を行う。
この間に `session-end`、`drop`、stale GC、または新しい claim が lock を変えると、旧 watcher は
取得した row を表示し、shared read cursor を進められる。

Issue #164 の CI evidence はこの同じ窓から二つの症状を観測した。

| 症状 | 失敗 | 意味 |
| --- | --- | --- |
| 旧 watcher が本文を表示 | main ubuntu shard 2/4、line 259 | 誤配送 |
| 本文を表示しないが unread が 0 | PR #163 macOS shard 2/4、line 279 | consume して新 owner から消失 |

現HEAD `54ea9d9` では、`test_actas_integration.bats` は 15/15、
`test_actas_lock.bats` は 22/22 pass である。これは race が無い証拠ではなく、
現在の scheduler で窓に当たらなかった baseline である。

## 判断

採用するのは **per-lock-path ownership delivery gate** である。
gate は既存の SQLite runtime-lock ABI
`agmsg_runtime_lock_acquire` / `verify` / `release` を使い、resource を
`actas-delivery:<actas_lock_path>` とする。lock file path を resource にするので、
`actas_lock_release_all` と GC は team/name を filename から復号せず同じ gate を取得できる。

| 案 | 判断 | 理由 |
| --- | --- | --- |
| `storage_watch_after` 前または consume 前だけ再確認 | 不採用 | check と副作用の間に次の ownership change が入り、窓を短くするだけで保証にならない。 |
| 先に consume して後で表示 | 不採用 | stdout failure / handover 時に message loss を作る。既存の「表示しない・未読を残す」保証も壊す。 |
| watcher と全 ownership writer を同じ gate で直列化 | 採用 | ownership の完了と fetch/表示/consume の順序を一つに決め、旧 owner の post-handover side effect をなくす。 |

```mermaid
sequenceDiagram
  participant O as old watcher
  participant G as delivery gate
  participant L as actas lock
  participant N as new claim
  participant S as message store

  O->>G: acquire(pair)
  O->>L: state == mine
  O->>S: fetch, print, consume
  O->>G: release(pair)
  N->>G: acquire(pair)
  N->>L: release/claim ownership
  N->>G: release(pair)
  Note over N,S: claim returned; a later send is new-owner work
```

## gate 契約

### I164B: 一時的な SQLite 競合を恒久停止にしない

PR #165 の CI 失敗は gate の直列化そのものを否定しない。前任 watcher の終了直後に、後任 watcher
または despawn の writer が同じ runtime gate を取るとき、現在の実装は SQLite の一時的な
`BUSY` / `LOCKED` を通常の gate 不可と同じ non-zero に畳み込む。さらに `2>/dev/null` と `|| true`
が SQLite の理由を捨てるため、原因を区別できない。

ここでの fail-closed は、**分類できた確定失敗、または有界 retry を尽くした一時失敗では side effect を
行わない**ことである。一度の `BUSY` を「以後配信しない」または「role drop をしない」に変換することではない。

### 1. acquisition、失敗の分類、stale reclaim

`scripts/lib/actas-lock.sh` に、lock file path を受ける小さな helper を置く。

- `scripts/lib/storage.sh` の runtime-lock ABI は SQLite の exit status と stderr を gate helper まで
  保持する。`tr` を末尾にした pipeline、`2>/dev/null`、`|| true` で acquire / owner / verify /
  release の SQL failure を成功に変換しない。これは gate primitive の前提であり、watcher / writer の
  統合には含めない。
- gate helper は各 SQL 試行を `transient`（SQLite の raw stderr が `SQLITE_BUSY` / `SQLITE_LOCKED`、
  または同じ SQLite error code を表す `database is busy` / `database is locked`）、`permanent`、
  `unknown` に分ける。`unknown` は retry せず fail-closed とする。live holder は SQL failure ではなく
  正常な contention として区別する。
- `try-acquire`、writer acquire、release の**全 non-zero return**は stderr に operation、resource、
  観測 owner、呼出し PID（`$$`）、classification と SQLite の未加工 stderr を残す。SQL を実行して
  いない経路は `sqlite_error=<not-invoked>` と理由を明示する。resource は
  `actas-delivery:<lock path>`、owner は PID だけであり、秘密値を含めない。
- watcher 用 `try-acquire` は live holder を待たず当該 poll を skip する。ただし transient error では、
  SQLite 待ち時間を含めて **200 ms 以下**の deadline 内で retry する。deadline 後はその poll だけを
  fail-closed で skip し、次 poll では改めて試す。
- ownership writer 用 acquire と通常 release は、既存の 50 x 0.1 s と同じ **5 s 以下の総 deadline**で
  transient を retry する。SQLite 内の busy timeout もこの deadline に算入し、外側の 50 回待機で
  さらに 5 s を重ねない。live holder は待機し、dead PID を確認できたときだけ既存 runtime-lock の
  `expected_owner` CAS で reclaim する。
- liveness を確認できない runtime lock は stale とみなさない。reclaim せず non-zero にする。
- release は runtime-lock row が自分の `$$` を持つ場合だけ行う。trap/cleanup が途中で走っても
  successor の gate を削除しない。

gate acquisition failure は fail-closed とする。
watcher はその pair の fetch・表示・consume・receipt 記録を一切行わず次 poll へ進む。
通常の ownership writer は lock file を変更せず non-zero を返す。despawn は後述の role-drop 例外とする。`actas-claim.sh` と
`agmsg_subscription_pairs` はこの non-zero を `status=ok` と解釈してはならず、monitor の
停止・再起動へ進まない。`actas-claim.sh` は
`status=unavailable team=<team> reason=ownership_gate_unavailable` と exit 3 を返す。
watcher の利用者向け diagnostic は pair と `ownership_gate_unavailable` を一度だけ残す。
上記の gate-operation diagnostic は、失敗の分類に必要な stderr として毎回残す。

**despawn は例外的に role drop を優先する。** control row を読んだ watcher は gate release が
transient / permanent のどちらで失敗しても、その失敗だけで `exit 1` して `reset.sh` を飛ばさない。
registration の drop と「live owner を残さない」処理を完了させて watcher を終了することが安全側である。
graceful `despawn.sh` は既存の `--timeout` 内で、registration が消え、lock owner が absent または
confirmed-dead であることを確認してから成功にする。runtime gate の stale reclaim は old watcher PID が
死んだことを確認した後の CAS に限る。release failure のために二重の 5 s 待機を重ねたり、10 s timeout を
延長したりしない。

### 2. watcher の critical section

side-effect-free な store existence / read cursor 取得の後、次の順で実行する。

1. pair gate を try-acquire する。失敗ならその pair を skip する。
2. gate を保持したまま `actas_lock_state` を再読する。
3. `other:<sid>` なら gate を release して、専用 watcher は現在の三つの reason を出して exit、
   broad watcher は pair だけ continue する。
4. `mine` または従来どおりの `free` だけが `storage_watch_after`、row decode、stdout、
   `storage_read_cursor_consume`、receipt 記録まで進む。
5. 全ての continue / stdout failure / despawn / normal exit の前に gate を release する。

stdout と consume の間も gate を保つ。handover がその間に完了することを許すと、表示だけ・
consume だけのいずれかが旧 watcher に残るためである。
`send.sh` は gate を取らない。send と claim が重なったときは claim completion が順序を決める。
claim が return する前の message は旧 owner が処理してよく、return 後の message は旧 watcher が
処理できない。

### 3. 同じ gate を取る mutation

次の `scripts/lib/actas-lock.sh` の lock file mutation は、対象 `actas_lock_path` の gate 内で行う。

- `actas_lock_claim`（stale reclaim を含む）
- `actas_lock_release`
- `actas_lock_release_all`（列挙した各 lock file path ごと）
- `actas_lock_gc_stale`（削除前）

`actas_lock_state` は read-only なので gate を取得しない。watcher が gate を取ってから呼ぶことで、
state と後続の side effect は同じ ownership snapshot に属する。

`session-end.sh` が watcher を TERM した直後に release-all へ到達しても、進行中 poll が gate を
返すまで release は待つ。wait を超えた場合は lock を残して終了する。次の claim は dead owner を
確認して CAS reclaim するため、未知の gate holder を消して handover を急がない。

## 実装計画

### 1. gate primitive を先に固定する（PR-1）

I164B 後の実装は primitive・watcher・writer を三つの独立した PR に分ける。PR #165 は閉じず、
分割後にどの commit を採るかは PM が決める。

| PR | 一つの主張 | 変更対象 | 受入・非対象 |
| --- | --- | --- | --- |
| PR-1 | gate primitive は raw SQLite failure を分類・記録し、transient だけを有界 retry する | `scripts/lib/storage.sh`、`scripts/lib/actas-lock.sh`、`tests/test_actas_lock.bats`、必要最小限の `docs/actas.md` | watcher、claim、reset、template は触らない。runtime-lock release の `|| true` を残さない。 |
| PR-2 | watcher は診断可能な gate result を用いて poll を直列化し、despawn で role drop を飛ばさない | `scripts/watch.sh`、`scripts/despawn.sh`、`tests/test_actas_integration.bats`、`tests/test_watch.bats`、`tests/test_despawn.bats`、対応する docs | `actas_lock_*` の primitive 実装と writer caller は触らない。既存 timeout を延長しない。 |
| PR-3 | ownership writer は gate failure を成功扱いせず、retry 後の failure を呼出し元へ正しく返す | `scripts/actas-claim.sh`、`scripts/lib/subscription.sh`、`scripts/reset.sh`、`scripts/drivers/types/claude-code/template.md`、`README.md`、対応する docs と focused tests | watcher の poll / barrier は触らない。 |

PR-1 の `tests/test_actas_lock.bats` には、live gate owner を CAS reclaim しないこと、dead PID の
gate は CAS で reclaim できること、release が自己 owner に限定されることに加え、次を置く。

1. controlled `SQLITE_BUSY` / `SQLITE_LOCKED` stderr では deadline 内に再試行して成功する。
2. retry を尽くした transient と permanent / unknown error は side effect なしで non-zero となり、
   raw SQLite stderr・resource・owner・`$$` を含む diagnostic を残す。
3. release の SQLite failure を成功に偽装しない。successor owner の row / lock file を消さない。

### 2. #683 regression を観測可能な barrier で広げる

`tests/test_actas_integration.bats` の既存 #683 test を削らず、次の順に置き換える。

1. test-only `AGMSG_TEST_ACTAS_DELIVERY_GATE_BARRIER` を使い、旧 watcher が gate を取得して
   `state=mine` を確認した直後に `entered` marker を書いて待たせる。
2. `entered` を `wait_for_file` で確認してから、別 process で実際の
   `actas_lock_release T alice sid-old` と `actas_lock_claim T alice sid-new` を実行する。
   lock file を `echo` で直接上書きしない。
3. handover は gate に阻まれて `done` marker を書けないことを観測する。固定 sleep で時刻を
   推測しない。
4. barrier を release し、`done` marker と `actas_lock_owner=T/alice:sid-new` を観測してから
   `send.sh` で `after the handover` を送る。
5. 既存の三つの assertion をすべて残す: 旧 stdout に本文が無い、旧 watcher は停止する、
   `storage_list_unread T alice` が 1 件である。

barrier は `AGMSG_TEST_` 名の test seam であり、未設定の production path は一切待たない。
`sleep 4` は削除し、marker / `wait_for_pid_exit` だけで状態を観測する。これは race を隠すための
待ち短縮ではなく、race の中心を毎回同じ位置へ置くための同期である。

PR-2 は watcher の stderr を `$BATS_TEST_TMPDIR` の file に取る。`test_watch` の closed-stdout case と
`test_despawn` の graceful case から `2>/dev/null` / `>/dev/null 2>&1` を除き、失敗時にはその file を
assertion diagnostic として表示する。CI rerun は診断を捨てないこの状態で一度だけ行い、人工的な負荷再現・
timeout 延長・sleep 増量はしない。

### 3. negative control と fixed-HEAD verification

GREEN 後、ownership writer 側の gate acquire/release だけを一時的に外す。
handover が旧 watcher の barrier 中に完了するため、step 3 の `done` 不在 assertion が落ちる（KILLED）。
変更を残さず復元し、同じ test と suite をもう一度 GREEN にする。

I164B の KILLED も二つ残す。PR-1 で transient classifier を permanent に変えると controlled busy test が
retry 回数 / success assertion で落ちる。PR-2 で despawn の release-error branch を旧 `exit 1` に戻すと、
graceful test は registration と logical ownership が消える assertion で落ちる。どちらも production DB や
live team data を使わない。

以下はこの checkout の current HEAD で実行可能であることを確認済みの command である。

```bash
bash -n scripts/lib/actas-lock.sh
bash -n scripts/watch.sh
bats tests/test_actas_lock.bats
bats tests/test_actas_integration.bats tests/test_watch.bats tests/test_despawn.bats
bats tests/test_delivery.bats
rtk git diff --check
```

I164B の plan-only baseline は syntax pass、`test_actas_lock.bats` 22/22、
`test_actas_integration.bats` / `test_watch.bats` / `test_despawn.bats` は 57/58、
`test_delivery.bats` 199/199、diff check pass だった。57/58 の既存失敗は
`test_watch.bats` の #197（unopenable DB diagnostic）であり、focused 再実行も 1/1 fail した。
この checkout の `54ea9d9..HEAD` に `tests/` / `scripts/` 差分は無く、今回の plan-only 差分の
回帰ではない。PR-2 の stderr capture は gate failure の証跡を残すためのものであり、#197 を直した
ことにはしない。#197 は別途 triage し、I164B では timeout 延長や assertion 緩和で隠さない。

実装時は RED / GREEN / KILLED / restore-GREEN の command、rc、final `HEAD` を残す。
`~/.agents/skills/agmsg`、live team data、production watcher を test bed にしない。

## 非対象

- `send.sh`、storage driver の message schema、read cursor schema の変更
- ownership state を remote store へ移すこと、global daemon の導入
- 発生頻度 telemetry。頻度は未測定だが、message 誤配送・消失を防ぐ本修正を待たせない
- Issue #162、#163 の bridge metadata / pane liveness

## PR 契約

- 1 PR = 上表の一つの主張だけ。PR-1 → PR-2 → PR-3 の順に、前 PR の fixed HEAD を次 PR の base にする。
- producer: `agmsg_programmer_codex`、opener: `kappaseijin4codex`。
- formal reviewer: `agmsg_reviewer_claude`、review account: `kappaseijin4claude`。
- review は各 final `headRefOid` の全差分と、その PR の failure behavior を対象に一括で行う。新 HEAD ごとに
  test、CI、formal approval を再取得する。
