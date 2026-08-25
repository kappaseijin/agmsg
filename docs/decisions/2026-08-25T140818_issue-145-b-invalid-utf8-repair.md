---
type: ImplementationSpecification
title: "Issue #145-B: SQLite の既存不正 UTF-8 行を明示修復する仕様"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/145"
design_commit: "78b2179d4da85356f18f5645296c3e6379e82de3"
base_commit: "d878396477a197148eaad7d81cf583d4af3e7a25"
timestamp: "2026-08-25T14:08:18+09:00"
---

# Issue #145-B: SQLite の既存不正 UTF-8 行を明示修復する仕様

## 目的

既存の SQLite store に混入した不正 UTF-8 byte を、operator が確認してから明示的に修復できる SQLite 専用 command を追加する。
表示経路の fail-soft 化（#145-A）や新規 write の validation（#146）とは分離し、通常の read/display path から永続 DB を変更しない。

## CLI 契約

```text
scripts/repair-invalid-utf8.sh --check <team>
scripts/repair-invalid-utf8.sh --apply <team> --backup <backup-path>
```

- `AGMSG_STORAGE_PATH` と `<team>` から既存の SQLite DB を `agmsg_db_path` で解決する。
- shared partition の DB を複数 team が共有する場合も、指定した `<team>` の row だけを scan/update する。
- `--check` は read-only scan とし、repairable body、unsupported field corruption、件数、status だけを出力する。body の raw value は出力しない。
- `--check` は scan 成功時に 0 を返し、検出結果は出力の status/count で示す。usage、DB、scan failure は nonzero とする。
- `--apply` は `--backup <path>` を必須とし、backup target が既存（file/directory/symlink）なら開始しない。backup path は source DB と同一視される場合も拒否する。
- `--apply` は repairable body 以外の corruption を検出したら `unsupported_corruption` として no-write で nonzero 終了する。
- raw body、sender、recipient、team、id、timestamp 等の値は stdout/stderr に再表示しない。

## Scan と修復規則

1. 指定した team に属する `events` の `message_sent` row（stable key=`seq`）と `messages` row（stable key=`id`）を個別に scan する。
2. scan 対象は `id, team, from_agent, to_agent, body, at`（events）と `team, from_agent, to_agent, body, created_at`（messages）。各値は SQLite の `hex(...)` で ASCII hex として取得し、raw byte を shell に渡さない。
3. 既存の #145-A と同一の UTF-8 policy（valid 1/2/3/4-byte sequence は保持、overlong/surrogate/上限超過/truncated/孤立 continuation/invalid lead は不正 byte ごとに U+FFFD=`EFBFBD`）で判定する。
4. body の不正だけを repairable とする。body 以外に不正が 1 件でもある row は `unsupported_corruption` として apply 全体を no-write にする。片方しか存在しない event/legacy row を勝手に作らず、存在する row だけを更新する。
5. `--apply` は全 scan が完了して unsupported が無いことを確認してから、source DB の `PRAGMA integrity_check` が正確に `ok` であることを確認する。
6. SQLite `.backup` snapshot を先に作成し、backup の存在と `PRAGMA integrity_check`=`ok` を確認してから `BEGIN IMMEDIATE` を開始する。
7. 各 update は stable primary key と original `hex(body)` を同時に `WHERE` へ入れ、`changes()=1` を要求する。1 件でも一致しなければ transaction を commit せず rollback して nonzero とする。修復後の source DB `integrity_check` が `ok` でなければ nonzero とし、backup からの復元は README の manual rollback boundary に委ねる。
8. `--apply` の更新は `CAST(X'<sanitized-hex>' AS TEXT)` を使う。成功時は再度 `--check` 相当の read-only scan で不正件数が 0 であることを確認し、valid row と backup の raw body は保持される。

## 出力契約

出力は stable table/key、field、status、count のみとし、body の内容・raw hex は含めない。最低限、各 table/field の `repairable` / `unsupported_corruption` と summary count を出力する。

## README 運用

README にユーザーが自己完結で実行できる順序を記載する。

1. 対象 team の agent を停止する。
2. `--check <team>` で検出結果と件数を確認する。
3. source DB と異なる未使用 path を指定して `--apply <team> --backup <path>` を実行する。
4. 再度 `--check <team>` を実行する。
5. read-only の `history.sh` で before/after row と rc=0 を確認する。`inbox.sh` は receipt/read state を変更するため production post-check に使わない。
6. apply、integrity check、post-check のいずれかが失敗したら停止し、backup snapshot からの復元は停止済み環境で人間が明示実行する。tool は自動 rollback しない。

## 検証計画

- 正の対照: `events` と `messages` に fixture `X'E696B02048454144208082'` を直接入れ、`--check` が repairable を検出し、`--apply --backup` 後に双方が U+FFFD byte (`E696B0204845414420EFBFBDEFBFBD`) へ変わる。backup は raw hex を保持し、valid row は不変、integrity_check は `ok`、history/inbox は正常動作する。
- 負の対照: valid UTF-8 のみの DB で `--check` が repairable/unsupported を 0 件と報告し、`--apply` が何も変更しない。
- unsupported control: body 以外の field に同じ不正 byte を入れ、`unsupported_corruption` と no-write、backup未作成を確認する。
- stale guard control: scan 後に body の original hex を変更した状態を模擬し、guarded update の `changes()=1` 失敗が rollback/no-write になることを確認する。
- mutation: sanitized update を raw pass-through または invalid-byte-delete に一時変更し、正の対照の expected hex/再 parse assertion が KILLED されることを確認する。
- Bash 3.2相当の環境で `bash -n`、CLI、fixture、mutationを実行する。

## 非対象

- #145-A の display sanitizer、#146 の writer validation
- read/display/export/import/sync path の自動 DB 更新
- SQLite schema migration、malformed UTF-8 以外の corruption の自動修復
- herdr-agent-monitor の未検証応急手順の転載
