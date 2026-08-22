---
type: Verification
title: "Issue #98 read-only roster normalization checks"
description: "Record the installed normalizer and public JSON API results for the scale2sheet and scale_exporter teams without applying a migration."
tags:
  - agmsg
  - issue-98
  - verification
  - roster
status: complete
timestamp: "2026-08-22T08:16:10+09:00"
---

# Issue #98 Read-Only Roster Check

## 結論

installed agmsg `v1.2.1-114-g789879e` で、`scale2sheet` と `scale_exporter` はいずれも normalization check を通過しなかった。
両チームの `team.sh <team> --format json` も引き続き fail-closed で失敗した。

## Evidence Packet

| team | `roster-normalize.sh <team> --check` | `team.sh <team> --format json` |
| --- | --- | --- |
| scale2sheet | exit 2; `schema error: member kind must be seat, human, or service` | exit 2; `schema error: schemaVersion must be integer 1` |
| scale_exporter | exit 2; `schema error: member kind must be seat, human, or service` | exit 2; `schema error: schemaVersion must be integer 1` |

| 要素 | 記録 |
| --- | --- |
| value | 上表の4コマンドはすべて exit 2。ready を返したチームはない。 |
| cutoff | installed script `v1.2.1-114-g789879e` に対する `scale2sheet` と `scale_exporter` の各1回の読み取り。 |
| source | [Issue #98](https://github.com/kappaseijin/agmsg/issues/98)、`~/.agents/skills/agmsg/scripts/roster-normalize.sh`、`~/.agents/skills/agmsg/scripts/team.sh`。 |
| command | `bash ~/.agents/skills/agmsg/scripts/roster-normalize.sh <team> --check` と `bash ~/.agents/skills/agmsg/scripts/team.sh <team> --format json`。 |
| negative control | 実行した4コマンドはいずれも `--check` または JSON 読み取りであり、`--apply` 呼出は 0 件。normalizer と public JSON API は異なる schema error を返した。 |

## 未確認・既知の限界

`--apply`、team config の直接変更、他チームへの操作は実行していない。
この結果は migration 可否やデータ修正後の JSON API 成功を証明しない。
