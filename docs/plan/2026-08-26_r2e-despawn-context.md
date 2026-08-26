---
type: Plan
title: "R2-E: despawn の lifecycle failure 契約"
status: decided
root_cause: R2
root_issue: "https://github.com/kappaseijin/agmsg/issues/179"
timestamp: "2026-08-26T11:07:22+09:00"
---

# R2-E: despawn の lifecycle failure 契約

## 判断（一行）

通常の `despawn` は lock state を確認できた場合だけ lifecycle cleanup へ進み、unknown は `free` に畳み込まず `status=unavailable`・非0で止める。`--force` は記録済み placement を使う明示的な物理停止の escape として残すが、registration reset / lock release が失敗したときは `status=forced` を出さず、記録を残して `status=partial`・非0で返す。

## 消費者と互換性

`despawn.sh` の stdout を機械判定する実行側消費者はない。呼び出しと表示契約は次だけだった。

| 消費者 | 現行の使い方 | R2-E で必要な変更 |
| --- | --- | --- |
| `scripts/drivers/types/claude-code/template.md` | 実行結果をユーザーへ表示し、`timeout` のみ `--force` を案内 | `unavailable` は復旧後 retry、`partial` は retry と明記する |
| `scripts/drivers/types/codex/template.md` | 同上 | 同上 |
| `README.md` / `README.ja.md` / `SKILL.md` | `ok` / `timeout` と `--force` の意味を説明 | 新しい失敗結果と force の限定を追記する |
| `tests/test_despawn.bats` | `status=ok` / `status=forced` を成功として確認 | failure が成功値でないことを追加確認する |

`llms.txt` はスクリプトへの索引だけであり、status 消費者ではない。従って成功時の `status=ok` と `status=forced`、timeout の exit 3 は維持でき、新しい failure status を追加しても呼び出し側のパースを破壊しない。

## 失敗境界

| 条件 | 結果 | 続けてよい操作 | 返却と再試行 |
| --- | --- | --- | --- |
| graceful の初回 state 照会または poll が unavailable | live / free を断定しない | control message、pane kill、reset、release、spawn record 削除を新たに行わない | `status=unavailable operation=lock-state`、非0。storage / lock の回復後に同じ graceful command を再試行する |
| registration reset が非0 | physical stop が既に行われていても lifecycle cleanup は未完了 | pane kill の巻戻しはしない。spawn record は残す | `status=partial operation=registration-reset`、非0。回復後に `--force` を再試行する |
| lock owner 読み取りまたは release が非0 | lock の有無・解放を成功と主張しない | pane kill の巻戻しはしない。spawn record は残す | `status=partial operation=lock-release`、非0。回復後に `--force` を再試行する |
| pane / tmux / herdr kill が非0 | 既存どおり best-effort | reset / release は続ける | 成否を lifecycle success 判定へ昇格しない |
| spawn record の `rm` が非0 | 既存どおり idempotent cleanup | ほかの完了済み semantic cleanup は有効 | 成功 status は変えない |

`actas_lock_state` は `free` / live-owner の分類と read failure を区別できなければならない。今の `actas_lock_owner` も unreadable を空 owner にしているため、R2-E の supporting change としてこの failure を呼び出し元へ返せるようにする。`|| echo free` だけを削除して helper が空 owner を成功扱いする経路を残してはならない。

`--force` は unknown を `free` とみなす fallback ではない。ユーザーが明示した placement への物理停止であり、graceful の state 照会に失敗しても選択できる。ただし reset / release が確認できない限り成功にはならない。新しい escape flag は不要である。

## 一 PR の境界と受入

これは一主張「`despawn` が lifecycle failure を成功表示しない」に収まる。変更範囲は `scripts/despawn.sh`、failure を公開する最小の `scripts/lib/actas-lock.sh`、`tests/test_despawn.bats`、および上記ユーザー向け説明に限る。R5-P2 の timeout deadline 化は混ぜない。

1. lock-state seam を非0にし、graceful が `status=unavailable`・非0となり、control row、placement kill、reset、record 削除のいずれも増えないことを確認する。
2. reset seam と lock-release seam を個別に非0にし、`status=ok` / `status=forced` が出ず、operation を含む診断と非0、retry 用 spawn record の保持を確認する。
3. 正の対照として、既知の `free`（Codex または stale owner）では従来どおり cleanup と `status=ok note=no-live-lock`、正常な force では `status=forced` を確認する。
4. 負の対照として、state failure case に `|| echo free` を戻す mutation が `status=ok` と cleanup を発生させ、1 の検査を red にすることを確認する。

## 根拠と限界

現在の `scripts/despawn.sh` は force で reset / release の non-zero を捨て、graceful では `actas_lock_state ... || echo free` 後に free cleanup を行う。`tests/test_despawn.bats` は stale lock が真に `free` の場合の cleanup を要求しているため、state failure と stale/free を同一視できない。README と driver template は timeout だけを force retry と説明しており、unavailable / partial の利用者導線を同じ PR で補う必要がある。

本記録は source、focused consumer scan、Issue #179 の live state に基づく設計判断である。実装・試験変更はしていない。
