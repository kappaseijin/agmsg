---
type: Design
title: Issue #246 read-only evidence contract の限定補足
description: >-
  official registrations query の隔離適合試験で、session record と禁止対象 process の
  意味的な不変性を、偽陰性なく検証するための限定契約を定める。
timestamp: "2026-09-09T03:13:06+09:00"
updated: "2026-09-09T03:20:25+09:00"
issues: [246, 222]
source_design: docs/decisions/2026-09-08T203900_issue-246-official-registration照会契約と適合計画.md
failure_observation_ref: >-
  unpublished local programmer worktree only:
  feat/issue-246-registration-fixture@3b39c7620e57345dc8a724aa0f6cd43c2f6f4d7c
failure_observation_availability: not_pushed_not_github_reviewable
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #246 read-only evidence contract の限定補足

## 結論

元設計書 §6.1 の read-only 対照を、session record の件数比較および既存 sentinel PID の
生存確認で済ませてはならない。
session は対象 run 配下の record の同一性集合、process は fixture が一意に識別できる
起動禁止対象だけで前後比較する。

この補足は、Issue #246 の provider patch、5ファイル境界、8変異、公開契約、または
README を変更しない。設計差分は本 `docs/decisions/` 文書だけである。

`3b39c7620e57345dc8a724aa0f6cd43c2f6f4d7c` は、偽陰性を観測した未公開の
programmer ローカル worktree `feat/issue-246-registration-fixture` の commit である。
GitHub、origin、または formal review の取得対象ではない。この commit は問題発見の観測参照だけであり、
本書の受入根拠や次工程の固定 HEAD には使わない。後者は programmer が本設計の実装後に提示し、
verifier が隔離 export で再測定できる公開済みの固定 commit とする。

## 1. 対象と非対象

| evidence | 比較対象 | この補足で比較しないもの |
| --- | --- | --- |
| session | fixture の対象 run root 内にある session record | 件数だけ、fixture 外の run、access time |
| process | query が起動を禁止する engine / watcher / bridge / agent の候補 | host 全体の `ps`、通常の短命 helper |

claim、team config、DB/runtime の既存 read-only 対照は元設計書どおり維持する。
この補足は session と process の偽陰性だけを閉じる。

## 2. session evidence の正規化集合

fixture は query の前後で、対象 run root の session record をそれぞれ manifest 化する。
manifest の1要素は次の tuple とし、相対 path の byte 順で安定化する。

```text
relativePath, fileType, contentHash
```

- `relativePath` は fixture の対象 run root からの相対 path とする。絶対 path、inode、mtime、
  件数だけを比較キーにしない。
- `fileType` は `lstat` による型とする。regular file は内容 bytes の SHA-256、symbolic link は
  link target bytes の SHA-256 を `contentHash` に入れる。想定外の special file、読取不能、
  root 外へ解決される record は fixture 異常として test を fail-closed にする。
- session record の選択規則は test 内で1箇所の helper として固定する。既存の
  `proj.*.project` だけを数えるような、名称依存かつ件数だけの収集は不可とする。
- pre/post manifest は byte-for-byte 同一でなければならない。追加、削除、rename、型変更、
  内容だけの書換えのいずれも不一致である。

対象 run root は一時 fixture 内だけとし、常設 `~/.agents/skills/agmsg/run` や他チームの
session record を読むことも比較対象へ含めることも禁止する。

## 3. process evidence の境界

process evidence は host 全体の完全一致比較にしない。fixture ごとに衝突しない token を
生成し、query が起動を禁止する engine / watcher / bridge / agent process だけを、その token
または fixture が管理する child-process group / descendant boundary で識別する。

1. token は test ごとに生成し、fixture 外へ記録・再利用しない。token を command line または
   制御済みの child metadata に残し、観測 helper が同一 token の PID を列挙できるようにする。
2. pre snapshot は該当 PID が 0 件であることを確認する。既存 sentinel の生存は、この条件の
   代わりにならない。
3. query が終了した後の post snapshot も 0 件でなければならない。PID、開始時刻、command line
   の token 一致、または管理済み child 境界の少なくとも一つで、無関係な host process を混同しない。
4. test の cleanup は token/管理済み PID から得た fixture 所有範囲だけを停止し、cleanup の成否も
   assert する。global name match や無関係な PID の kill は禁止する。

通常の query が内部で `cat`、`sqlite3`、`jq` 等の短命 helper を起動しても、それは engine /
watcher / bridge / agent ではなく、query 終了後の token-bound 観測対象でもない。従ってこの契約の
process mutation とはしない。反対に、禁止対象 role として token-bound の長寿命 child が query 後にも
残るなら、その唯一の child でも read-only 違反とする。

## 4. 検出力を証明する変異

実装は各対照について baseline pass と対応する変異の KILL を同じ fixture contract で示す。
既存の8変異に置き換えず、read-only evidence の検出力として追加する。

| contract | baseline | 対応変異 | 必須結果 |
| --- | --- | --- | --- |
| session manifest | query 前後の manifest が同一 | query 成功前に既存 session record の bytes だけを書換える | read-only test は非0で KILLED |
| process boundary | token-bound 禁止対象 PID が pre/post とも 0 | query 成功前に token-bound engine/watcher/bridge/agent を表す長寿命 child を生成する | read-only test は非0で KILLED |

変異は対象 source の一点だけを変え、query の成功 envelope を保つ。これにより、query 自身の失敗ではなく
read-only evidence が変異を検出したことを示す。mutation child は cleanup の所有範囲内に置き、実行後に
0件へ戻ったことを記録する。

## 5. 修正後の一括 verifier 受入

programmer はこの補足を実装した固定 commit を渡す。verifier は残る2対照だけを再試験せず、同じ
隔離 export・同じ固定 commit について次を一括で独立再検証する。

1. patch artifact SHA とその改ざん負対照
2. provider patch の5ファイル境界、および設計補足 PR が `docs/decisions/` の1ファイルだけであること
3. 元設計 §6.1 の必須対照13件すべて
4. 元設計 §6.2 の既存8変異すべて
5. claim、config、DB/runtime、session、process を含む read-only 全対象
6. 本書 §4 の session/process 2変異と、各 baseline pass

report は `sourceCutoff`、`patchHead`、`patchApplied`、`contractStatus`、`mutationStatus`、
`readOnlyStatus`、`officialAvailability` を残し、各結果を `value / cutoff / source / command` と
固定 HEAD に結び付ける。二つの新しい KILL だけをもって Issue #246 の受入完了、PR merge、または
`blocked:dependency` 解除を主張しない。

## 6. 完了境界

本設計の完了は、固定 HEAD の formal review がこの限定契約を受入れることまでである。
受入後にだけ、PM は Issue #246 の機械可読な `blocked:dependency` 状態を解除し、programmer へ
一度だけ限定修正を依頼できる。

実装、verifier 実測、Issue のラベル変更、PR 作成・merge、README 更新は本設計の範囲外である。

Refs #246, #222
