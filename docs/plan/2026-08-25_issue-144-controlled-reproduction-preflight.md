---
type: Investigation
title: "Issue #144 controlled reproduction"
status: on_hold
issue: "https://github.com/kappaseijin/agmsg/issues/144"
timestamp: "2026-08-25T15:04:40+09:00"
updated: "2026-08-25T15:17:23+09:00"
---

# Issue #144 controlled reproduction

## 訂正

初版の「`HERDR_ENV` が unset なので herdr を操作できない」という前提は誤りだった。
`HERDR_ENV` は herdr 内からの実行を示す marker であり、CLI の操作可否ではない。
`env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_WORKSPACE_ID herdr workspace create --no-focus --label issue144-preflight`
は workspace を作成した。作成した空 workspace は確認後に閉じた。

以後の試験は、production pane に触れず、disposable clone
`/private/tmp/agmsg-issue144.qYwWSh/skill` と自分用 workspace `w5B` だけで実施した。

## 結果

crash は **0 回**であり、Issue #144 の fixture 採用条件である「独立した 2 crash run」を満たさない。
したがって CRASHED 正例の fixture、`paneLiveness=crashed` を根拠にした実装、synthetic fixture はいずれも作成しない。

| value | cutoff | source | command |
| --- | --- | --- | --- |
| crash profile の実 Codex `resume` は TUI prompt まで到達、`requires --cd` は 0 件 | 独立 2 run で crash diagnostic 又は bare shell fallback | own workspace `w5B:p1`; temporary `[tui] resume_cwd = "current"` profile | `CODEX_HOME=<temporary> codex resume 01a0378e-8503-7442-843e-aa3ea391bc7b` |
| healthy control は `CONTROL_OK` | live Codex prompt と 1 件の既知応答 | own workspace `w5B:p3`; `resume_cwd` なし profile | `CODEX_HOME=<temporary> codex -m gpt-5.6-sol 'Reply with CONTROL_OK only.'` |
| temporary clone は `issue144repro_architect_codex` を登録済み、しかし spawned child の `$agmsg actas` はその team を見つけられず team 選択を要求 | bridge/role-session を通る resume を 1 回以上測定する | `w5B:p2` の `scripts/team.sh` と `w5B:p1` の child tail | `scripts/join.sh ...`; `scripts/spawn.sh codex issue144repro_architect_codex ...` |

後者は fixture の代替根拠ではない。隔離 clone の roster と spawned child が参照する roster の境界を解決せず、role-session / bridge が live の状態を作ることは、production roster を変更せずには確認できなかった。

## 判断

`tui.resume_cwd = "current"` だけを原因として採用しない。
現在の実機 CLI では、同設定の real session resume が成功したためである。
また bridge 経由の crash も測定できていないので、単体 CLI の成功を「Issue #144 の解消」とも扱わない。

実装は **on hold** とする。再開条件は次のいずれかである。

1. production を変更しない完全隔離 install で、temporary roster を spawned child も参照できる経路を確立する。
2. 実インシデントの pane tail を、capture provenance を保ったまま受領する。

いずれの場合も、2 crash run、1 healthy bridge control、tail 前方の crash 語引用に対する live 負の対照を揃えてから fixture を最小化する。

## 非対象

- 本番 `~/.agents/skills/agmsg/`、既存 role profile、既存 team roster、既存 pane の変更
- synthetic crash fixture の作成
- Issue #138 の原因調査又は recovery
