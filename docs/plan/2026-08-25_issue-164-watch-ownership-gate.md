---
type: Plan
title: "Issue #164: watch poll と actas ownership mutation を同一 gate で直列化する"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/164"
timestamp: "2026-08-25T23:31:58+09:00"
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

### 1. acquisition と stale reclaim

`scripts/lib/actas-lock.sh` に、lock file path を受ける小さな helper を置く。

- watcher 用 `try-acquire` は待たない。live gate holder、runtime DB error、owner PID の
  liveness が判断不能のいずれでも non-zero を返す。
- ownership writer 用 acquire は短い bounded wait をする。live holder は待機し、dead PID を
  確認できたときだけ既存 runtime-lock の `expected_owner` CAS で reclaim する。
- liveness を確認できない runtime lock は stale とみなさない。reclaim せず non-zero にする。
- release は runtime-lock row が自分の `$$` を持つ場合だけ行う。trap/cleanup が途中で走っても
  successor の gate を削除しない。

gate acquisition failure は fail-closed とする。
watcher はその pair の fetch・表示・consume・receipt 記録を一切行わず次 poll へ進む。
ownership writer は lock file を変更せず non-zero を返す。`actas-claim.sh` と
`agmsg_subscription_pairs` はこの non-zero を `status=ok` と解釈してはならず、monitor の
停止・再起動へ進まない。`actas-claim.sh` は
`status=unavailable team=<team> reason=ownership_gate_unavailable` と exit 3 を返す。
diagnostic は pair と `ownership_gate_unavailable` を一度だけ残す。

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

### 1. gate helper と caller failure を RED で固定する

変更対象は次の 7 file とする。

| file | 変更責務 |
| --- | --- |
| `scripts/lib/actas-lock.sh` | runtime-lock を包む per-path gate、claim/release/release-all/GC の gate 化 |
| `scripts/watch.sh` | gate 内の state→fetch→print→consume と cleanup、test barrier |
| `scripts/actas-claim.sh` | gate failure を `status=ok` にしない fail-closed mapping |
| `scripts/lib/subscription.sh` | active watcher startup で claim failure を購読成功にしない |
| `scripts/drivers/types/claude-code/template.md` | `ownership_gate_unavailable` を actas failure として扱い、既存 Monitor を止めない指示 |
| `README.md` | ownership を検証できない actas は delivery を変えず retry する、という利用上の結果 |
| `docs/actas.md` | gate の線形化点、短時間の retry、unknown gate holder を奪わない recovery 境界 |

`tests/test_actas_lock.bats` には、live gate owner を CAS reclaim しないこと、dead PID の gate は
CAS で reclaim できること、release が自己 owner に限定されることを追加する。

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

### 3. negative control と fixed-HEAD verification

GREEN 後、ownership writer 側の gate acquire/release だけを一時的に外す。
handover が旧 watcher の barrier 中に完了するため、step 3 の `done` 不在 assertion が落ちる（KILLED）。
変更を残さず復元し、同じ test と suite をもう一度 GREEN にする。

以下はこの checkout の current HEAD で実行可能であることを確認済みの command である。

```bash
bash -n scripts/lib/actas-lock.sh
bash -n scripts/watch.sh
bats tests/test_actas_lock.bats
bats tests/test_actas_integration.bats
rtk git diff --check
```

baseline は順に syntax pass、22/22 pass、15/15 pass、diff check pass だった。
実装時は RED / GREEN / KILLED / restore-GREEN の command、rc、final `HEAD` を残す。
`~/.agents/skills/agmsg`、live team data、production watcher を test bed にしない。

## 非対象

- `send.sh`、storage driver の message schema、read cursor schema の変更
- ownership state を remote store へ移すこと、global daemon の導入
- 発生頻度 telemetry。頻度は未測定だが、message 誤配送・消失を防ぐ本修正を待たせない
- Issue #162、#163 の bridge metadata / pane liveness

## PR 契約

- 1 PR = 「watch poll と actas ownership mutation を同一 gate で直列化する」だけ。
- producer: `agmsg_programmer_codex`、opener: `kappaseijin4codex`。
- formal reviewer: `agmsg_reviewer_claude`、review account: `kappaseijin4claude`。
- review は final `headRefOid` の全差分と上記 failure behavior を対象に一括で行う。新 HEAD ごとに
  test、CI、formal approval を再取得する。
