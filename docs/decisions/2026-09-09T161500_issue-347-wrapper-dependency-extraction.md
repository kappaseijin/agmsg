---
type: Design
title: Issue #347 hunk ledger wrapper後依存名抽出
description: >-
  hunk-ledgerのcommand-position検査が既知wrapper後の実呼出名を抽出し、
  wrapper optionや引数を依存名へ誤認しない限定契約を定める。
timestamp: "2026-09-09T16:15:00+09:00"
updated: "2026-09-09T16:15:00+09:00"
issues: [347, 222, 254, 339]
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #347 hunk ledger wrapper後依存名抽出

## 決定

`used_names()`のcommand-position抽出は、既存の行頭・制御演算子・subshell・control keyword
直後に加え、既知wrapper列の後にある最初の実呼出名を抽出する。

対象wrapperは`run`、`command`、`nohup`、`exec`、`timeout`、`env`だけである。完全なshell parserを
名乗らず、未知または動的な構文を推測で依存名へ変えない。

## 1. wrapper grammar

command positionから、0個以上の次のwrapperを左から消費してから最初のnameを採る。

| wrapper | 消費する補助token | nameにしてはならないもの |
| --- | --- | --- |
| `run` | Batsの既存optionと`--`までのoption | `run`、option、option値 |
| `command` | `-`で始まるoptionと`--` | `command`、option |
| `nohup` / `exec` | `-`で始まるoptionと`--` | wrapper、option |
| `timeout` | option、option値、duration | `timeout`、duration、option |
| `env` | option、`NAME=VALUE`、`--` | `env`、環境代入、option |

wrapperは連鎖可能とする。例えば`run env A=B timeout 5 command target`は`target`を一つだけ
抽出する。quoted word、comment、argument position、assignment右辺はcommand positionではないため
抽出しない。正規表現だけで不完全なoption grammarを黙認せず、小さなtoken walkへ分離する。

## 2. 既存境界

行頭、`&&`、`||`、`;`、`|`、`(`、`` ` ``、`$(`、`if`、`then`、`else`、`do`、`until`、`while`直後の
開始位置は維持する。展開`${NAME}`も既存どおりuseである。

source/load可視性、`$SCRIPT_DIR`等の静的source解決、未知wrapper、完全なquote/shell parseは別主張B
以降であり、本PRへ含めない。動的sourceの全可視fail-closedは変更しない。

## 3. 受入対照

| case | expected |
| --- | --- |
| `run agmsg_runtime_lock_release_owned ...` | release helperを抽出 |
| 各wrapper単体・複数wrapper連鎖 | 最初の実呼出名だけを抽出 |
| `command -v target`、`timeout 5`、`env A=B` | wrapper/option/duration/assignmentを抽出しない |
| comment、quoted text、引数中のhelper名 | helperを抽出しない |
| 既存control/subshell位置 | 現行の抽出集合を維持 |
| current main ledger | 新規false positive 0 |

wrapper透過を無効にする変異は`run target`正対照でKILLする。`env`代入または`timeout`値をnameとして
採る変異は負対照でKILLする。mainの0件だけを根拠にせず、訂正前台帳の`run`実例を正対照にする。

## 4. 最小実装境界

| path | change |
| --- | --- |
| `scripts/internal/hunk-ledger.py` | command-position後のwrapper token walk |
| `tests/test_hunk_ledger.bats` | wrapper単体/連鎖、負対照、mutation、main台帳比較 |

README、ledger schema、source visibility、主張Bは変更しない。formal review後にprogrammerが実装し、
verifierは固定HEADで全対照・mutation・main台帳比較を独立に再測定する。

Refs #347, #222, #254, #339
