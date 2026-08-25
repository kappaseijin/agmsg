---
type: Investigation
title: "Issue #144 controlled reproduction preflight"
status: blocked_environment
issue: "https://github.com/kappaseijin/agmsg/issues/144"
timestamp: "2026-08-25T15:04:40+09:00"
---

# Issue #144 controlled reproduction preflight

## 結果

Issue #144 の実装前提である controlled reproduction は、この実行環境では開始していない。
`HERDR_ENV` が unset であり、herdr skill は `HERDR_ENV=1` 以外から pane / workspace を
操作しないよう要求するためである。外部から herdr pane を作成・操作して、必要な
herdr/agmsg 経由の resume 条件を偽装しなかった。

## 証拠

| value | cutoff | source | command |
| --- | --- | --- | --- |
| `HERDR_ENV` は空、`printenv` は non-zero | `HERDR_ENV=1` でなければ herdr 操作をしない | current execution environment | `printenv HERDR_ENV` |
| disposable clone は未作成 | config / storage / pane の実行証跡が無い状態で fixture を作らない | clone command の `&&` 後続は環境 check で停止 | `printenv HERDR_ENV && mktemp -d ... && git clone ...` |

## 判断

crash tail は 0 件、healthy control は 0 件である。従って「2 回の独立再現」と
「healthy role が live prompt」をともに満たしていない。合成 fixture は作らず、
`paneLiveness=crashed` を出荷する実装を保留する。

次の試行は `HERDR_ENV=1` の pane から、使い捨て clone・temporary `CODEX_HOME`・
`AGMSG_SPAWN_OPTIONS_FILE`・`AGMSG_STORAGE_PATH`・herdr workspace を全て分離して実施する。
同一条件を 2 run、`resume_cwd` を持たない role の healthy control を 1 run 行い、
採取するのは各 run の pane tail のみとする。

## 非対象

- 本番 `~/.agents/skills/agmsg/`、既存 role profile、既存 team roster、既存 pane の変更
- synthetic crash fixture の作成
- Issue #138 の原因調査又は recovery
