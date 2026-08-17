---
type: Decision
title: Codex 不在期間の Claude 単独運用と merge 方針
description: 両プロジェクト（agmsg / herdr-agent-monitor）の Codex 席が利用上限で停止した期間、agmsg チームを Claude 単独で運用し、CI clean な PR を merge する旨のユーザー決定を記録する。
tags:
  - agmsg
  - owner
  - cross-review
  - fail-closed-override
status: accepted
timestamp: "2026-08-17T06:00:00+09:00"
---

# Codex 不在期間の Claude 単独運用と merge 方針

## 背景

- `agmsg_owner_codex`（本チームの Codex 派生席）はトークン期限で停止していた。
- ユーザー指示により `agmsg_owner_claude`（`claude_product_owner` 派生、claude-code、
  model=`claude-sonnet-5` / effort=`max`）が owner を引き継いだ。
- 対向チーム herdr-agent-monitor も同時期に Codex 席が利用上限に達し（復帰予定
  2026-08-20T12:37+09:00）、`herdr-agent-monitor_owner_claude` へ交代済み（2026-08-17T05:50 通知）。
- 結果として、両チームとも `~/.agents/rules/agent-role.rule.md` が要求する
  クロスベンダー（Claude/Codex）レビューを**どちらの agent も**満たせない状態になった。
  同ルールは「例外は無い」と定めるが、これは agent 間のレビュー代替を禁じる規定であり、
  ユーザー自身の受入判断を制限するものではない（決定権はユーザーにある、
  `~/.agents/rules/agent-role.rule.md` 工程表参照）。

## ユーザー決定（2026-08-17、agmsg_owner_claudeへ直接）

> codexが戻ってくるまではclaudeのみで自走してください。PRがCI cleanならマージし、課題解決へ進めてください。

AskUserQuestion で提示した3択（Codex 復帰まで待つ／ユーザー自身が diff を見て判断／保留して他作業優先）
に対する回答。**単独 LLM レビューでの自己承認ではなく、ユーザー本人による受入判断**として扱う。

## 適用範囲・期限

- 対象: agmsg チームが Claude 単独で運用する期間中に作成・更新される PR。
- 期限: Codex 復帰（2026-08-20T12:37+09:00 予定）まで。復帰後は通常のクロスベンダー
  レビュー必須方針に戻る。本決定は自動延長しない。
- 対象外: 破壊的操作・公開範囲の変更など、`~/.agents/rules/autonomy.rule.md` が
  個別にユーザー確認を要求する事項。

## 適用第1号: PR #64（Issue #63）

- merge 直前に再確認: head `2fb38c9b6aa6b6407bb1875e6438434ca28cbf0c`、
  `mergeStateStatus=CLEAN`、`mergeable=MERGEABLE`、`statusCheckRollup` に非 SUCCESS なし。
- `gh pr comment` は `~/.agents/config/pr-account-policy.conf` の account-policy guard
  （cwd=`/Users/kappa/Dropbox/data/dev/agmsg` → role=creator → 期待ログイン
  `kappaseijin4codex`）に阻まれた。これは producer/reviewer のベンダー分離を守るための
  既存グローバル設定であり、Claude セッションが `kappaseijin4codex` を騙って回避すべきではない
  ため、コメント投稿は行わず、本決定書への記録に代えた。
  `gh pr merge` は同 guard の account-policy 対象（`pr create`/`pr comment`/`pr review`）に
  含まれないため、通常の owner-guard（destination owner チェックのみ）を経て実行する。
- merge 後、main 同期・作業ブランチ削除を行う（`~/.agents/rules/git.rule.md` の
  「マージ後の後片付け」によりユーザー承認不要）。

## 対向チームへの通知

`herdr-agent-monitor_owner_codex`（現 `herdr-agent-monitor_owner_claude`）へ agmsg 経由で
本方針を通知した（2026-08-17T06 台）。対向チームも独立に同様の fail-closed 方針へ到達しており、
今回のユーザー決定と矛盾しない。
