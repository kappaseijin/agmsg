---
type: Plan
title: "Issue #99: model-orchestration と spawn_options の突合"
description: "役割別モデル配置表をパースし、Claude の role overlay の model/effort と spawn_options.yaml を fail-closed に照合する stub test を追加する。"
tags:
  - agmsg
  - tests
  - spawn-options
  - model-orchestration
  - issue-99
timestamp: "2026-08-20T21:29:58+09:00"
---

# Issue #99: model-orchestration と spawn_options の突合実装計画

**Goal:** `~/.agents/rules/model-orchestration.rule.md` の役割別モデル配置表と、`~/.agmsg/config/spawn_options.yaml` の対応する `claude-code@<role>` セクションを、`model` と `effort` の両方で継続的に照合できる Bats stub test を追加する。

**Architecture:** テスト内の小さな `awk` パーサーで Markdown の役割表だけを読み、既存の `agmsg_spawn_options_section_value` を介して YAML の値を取得する。Codex の role overlay は `-p` で profile を選択し、model/effort を YAML に重複定義しない現行契約なので、照合対象は `claude-code` 行に限定する。対象ファイルが明示された場合は欠落も fail-closed、標準の開発者環境に外部ファイルがない場合だけ live test を skip する。

**Tech Stack:** Bash、awk、Bats。

**Spec:** [Issue #99](https://github.com/kappaseijin/agmsg/issues/99)

## Global Constraints

- 変更はテストと計画書に限定し、spawn の実装・role overlay の解決順序・Codex profile の契約を変更しない。
- Markdown 表の装飾（バッククォート、太字）と reviewer 行の注記を除去してから比較する。
- 欠落ファイル、役割表の未検出、対応 section/key の欠落は一致扱いにしない。
- 負の対照は実ファイルを変更せず、一時コピーで policy→YAML と YAML→policy の両方向の乖離を作り、追加 test が実際に非ゼロ終了することを確認する。
- 現行の live ファイルは専用の環境変数で明示して確認し、結果にはコマンド、終了状態、確認時刻、未確認範囲を残す。
- PR は Codex producer として `kappaseijin4codex` で作成し、formal reviewer は `kappaseijin4claude` に限定する。自己レビュー・未確認のマージは行わない。

```mermaid
flowchart LR
    R[model-orchestration.rule.md\nrole table] --> P[awk parser\nrole harness model effort]
    Y[spawn_options.yaml\nclaude-code@role] --> S[section value reader]
    P --> C{model and effort\nmatch?}
    S --> C
    C -->|yes| G[PASS]
    C -->|no or missing| F[FAIL closed]
```

## Task 1: 計画と test-first の RED を固定する

**Files:**

- Create: `docs/superpowers/plans/2026-08-20-issue-99-model-sync.md`
- Modify: `tests/test_spawn_options.bats`

- [x] Issue本文、現行ポリシー表、現行 YAML、既存の spawn-options reader を確認する。
- [x] 専用ブランチ `issue-99-model-sync` と clean baseline を確認する。
- [x] 照合 test の骨格を先に追加し、実装前に `command not found` の RED を観測する。

## Task 2: role table と YAML の model/effort 照合を実装する

**Files:**

- Modify: `tests/test_spawn_options.bats`

- [x] Markdown の該当表を行単位でパースし、Claude の role だけを `claude-code@<role>` にマッピングする。
- [x] `model` と `effort` を個別に比較し、期待値・実値・role を診断へ出す。
- [x] 欠落ファイル、表、section、`--model`、`--effort` を fail-closed にする。
- [x] インライン注記を含む reviewer 行を正規化し、Codex profile-only 行は現行契約どおり除外する。
- [x] fixture 相当のテストと、現行 workstation ファイルを対象にする live test を追加する。

## Task 3: 正負の対照と回帰を実測する

**Files:**

- Test: `tests/test_spawn_options.bats`

- [x] 対応する model を一時 YAML コピーだけで変更し、live test が exit 1 で終了することを確認する。
- [x] 対応する model を一時 policy コピーだけで変更し、live test が exit 1 で終了することを確認する。
- [x] 元の live ファイルで model/effort の一致が PASS になることを確認する。
- [x] target Bats、shell syntax、`git diff --check` を実行した。142 件回帰はユーザー指示により 110/142 で停止し、変更対象外の回帰は完走させていない。

## Task 4: PR と cross-vendor review を依頼する

**Files:**

- Update: Issue #99 対応の PR description

- [ ] 変更を意図したパスだけ stage、commit、push する。
- [ ] Issue #99 を closes する draft PR を `kappaseijin4codex` で作成する。同一目的の既存 PR があれば再利用する。
- [ ] exact head SHA、green test、両方向の KILLED 負の対照、live 一致確認を PR に記載する。
- [ ] `kappaseijin4claude` へ formal review を依頼し、review state を live query する。
- [ ] PR URL、head、検証結果、未確認範囲を `agmsg_pm_claude` に報告する。
