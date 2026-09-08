---
type: Evidence
title: Issue #246 registration query mutation evidence
description: 隔離した候補実装に対する契約試験の変異検査の途中証跡。
timestamp: "2026-09-08T22:00:00+09:00"
---

# Issue #246 registration query mutation evidence

## 対象と方法

候補は `feat/issue-246-registration-query` の `eb850a6698ab81986b9ac49830b7dddbdeb75d83`。
各変異は `/private/tmp/agmsg-246-controls.f7CWse/<case>` の独立した `git archive` 展開先だけへ加えた。
実行は各対応ケースについて `bats tests/test_api_registrations.bats -f <test name>`。
本リポジトリの作業ツリー、通常の agmsg install、team DB は変更していない。

## 途中結果

| case | 変異 | 対応検査 | value | 判定 |
| --- | --- | --- | --- | --- |
| limit | rows query に `LIMIT 1` | duplicate tuples remain visible | exit 1 | KILLED |
| validation | snapshot validation を `ok` 固定 | corruption does not become an empty successful array | exit 1 | KILLED |
| stdout | 成功 envelope 前に `partial` を stdout へ出力 | one tuple is returned in a complete envelope | exit 1 | KILLED |
| distinct-at-emit | 最初に一致した `SELECT json_object` を `SELECT DISTINCT` 化 | duplicate tuples remain visible | exit 0 | INVALID CONTROL |
| distinct-rows | registration rows query の `json_object` を `DISTINCT` 化 | duplicate tuples remain visible | exit 1 | KILLED |
| schema-bypass | `storage_schema_unsupported` の分岐を成功扱い | unsupported storage schema fails closed | exit 1 | KILLED |
| target-scope | query target を別teamへ固定 | corrupting another team does not affect the target | exit 1 | KILLED |
| final-snapshot | final fingerprint/byte比較を無効化 | source exchange is reported instead of returning mixed data | exit 1 | KILLED |
| tuple-pair | rows query の project expression を `/tmp/project-z` 固定へ変更 | multiple projects and types preserve pairs without a cartesian product | `bash -n` pass、exit 1 | KILLED |

`distinct-at-emit` は rows query ではなく envelope 生成の `SELECT` を変更したため無効対照である。
最初の tuple-pair 置換は Bash 構文エラーとなったため破棄し、同じ隔離方式で rows query の project expressionだけを固定化して再実行した。
再実行では `bash -n` が通り、Bats は `bad_pair == 0` の期待で失敗した。

## 未完了

有効な独立変異検査は8件すべて KILLED した。
各ケースは隔離 archive だけを変更しており、候補実装・通常 install・team DB は変更していない。
