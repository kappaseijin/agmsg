---
type: Design
title: Issue #280 manager authority のpm語彙訂正
description: >-
  gh pr mergeのmanager専用gateで、実rosterのpmを唯一の管理席roleとして用いる
  語彙訂正と、実rosterに対応した検査契約を定める。
timestamp: "2026-09-09T11:40:00+09:00"
updated: "2026-09-09T11:40:00+09:00"
issues: [280, 222, 239]
supersedes: docs/decisions/2026-09-09T110000_issue-280-manager-only-pr-merge.md
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #280 manager authority のpm語彙訂正

## 決定

Issue #280でいう「manager席」は、現行agmsg rosterの機械可読なrole値`pm`を指す。
`gh pr merge`は一意な`kind: seat`かつ`role: pm`のsessionだけを許可する。
`manager`は人間向け責務名であり、このguardのroster比較値に使わない。

`pm`と`manager`をORで許可しない。現行rosterに存在しない`manager`を許すと、語彙不整合を
fail-closedにできず、将来の誤登録をmanager authorityへ昇格させるためである。

## 根拠

`agmsg_pm_claude`の実roster roleは`pm`であり、`manager` roleを持つagmsg席は無い。
PR #368の`selected_role != manager`実装は、正規PMのmergeを拒否する。これはproducer拒否を
証明するfixtureが実rosterの語彙と異なっていた偽陽性である。

既存設計にもmanagerとpmを別のroster語彙として扱う箇所がある。責務名をそのままmachine enumへ
投影せず、今回の認可値を`pm`へ固定する。

## 実装・検査の訂正

| case | required result |
| --- | --- |
| exact `kind=seat, role=pm` manager session | 正しいaccount・destinationならmerge writeを1回許可 |
| `role=manager` fixture | nonzero、merge write log空 |
| producer / owner / reviewer等の非pm | nonzero、merge write log空 |
| unknown / multiple / read failure | nonzero、merge write log空 |

role比較を`pm`から`manager`へ戻すmutationと、role gate削除・unknown許可のmutationをそれぞれ
KILLする。manager名のfixtureだけを通す試験は削除し、実rosterに対応する`pm`の正対照を必須にする。

PR #368はこの訂正を含む新HEADで全差分reviewをやり直すまでmergeしない。
Issue #239、`pr merge`以外のwrite、GitHub review/CI判定、roster migrationは対象外である。

Refs #280, #222, #239
