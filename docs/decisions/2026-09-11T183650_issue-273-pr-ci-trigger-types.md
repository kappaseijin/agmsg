---
type: Design
title: "Issue #273: pull_request の types に edited を加える"
description: >-
  base branch の付け替えや draft 解除で CI が起動しない問題について、2 案（types の追加と、
  close→reopen の運用化）を比べて採用案を定める。採用は edited の追加のみ。ready_for_review は加えない。
  job 単位の絞り込みは、必須チェックを skipped で上書きする危険があるため採らない。実装は別 PR。
timestamp: "2026-09-11T18:36:50+09:00"
refs:
  - "#273"
  - "#373"
  - "#276"
---

# Issue #273: pull_request の types に edited を加える

## 1. 断定

**`pull_request` を trigger に持つ 6 本の workflow すべてで、`types` を `[opened, synchronize, reopened, edited]` にする。**

- `ready_for_review` は加えない（§4.2）
- `edited` を job 単位の `if` で base 変更だけに絞ることはしない（§4.3）
- close→reopen の運用化（案 2）は採らない（§5）

README への影響: 無。AGENTS.md への影響: 無（運用手順を増やさないため）。

## 2. 問題

`pull_request` に `types` を書かないと、GitHub は `opened`、`synchronize`、`reopened` の 3 つだけで workflow を起動する。
base の付け替え（`edited`）と draft の解除（`ready_for_review`）では起動しない。

PR #266 は base を `diag/issue-255-collector-application` から `main` へ付け替えたが、CI が一度も起動しなかった（#273 本文）。

## 3. 現状（main `7e1a86c` で確認）

| workflow | `pull_request` の条件 | 必須チェック |
| --- | --- | --- |
| `tests.yml` | `branches: [main, integration/remote]` | `check`、`bats`（main の branch protection） |
| `verify-versions.yml` | `branches: [main, integration/remote]` | — |
| `issue253-controls.yml` | `branches: [main]` と `paths` | — |
| `issue253-actas-controls.yml` | `branches: [main]` と `paths` | — |
| `issue261-diagnostic.yml` | `branches: [main]` と `paths` | — |
| `pages.yml` | `paths` | — |

6 本とも `types` を書いていない。`app-release.yml`、`release.yml` は tag push だけで起動するので対象外である。

main の branch protection は `required_status_checks.strict: true` で、必須は `check` と `bats` の 2 つである。

`tests.yml` は concurrency を PR 番号で束ね、`cancel-in-progress` を有効にしている（`tests.yml:68-70`）。
同じ PR で新しい run が起動すると、実行中の run は取り消される。

## 4. 案 1（types の追加）の検討

### 4.1 `edited` は必要である

base の付け替えは `edited` イベントとして届き、payload に `changes.base` が入る。
`edited` を加えれば、付け替えた瞬間に新しい base で CI が起動する。

`tests.yml` の変更検出は、`github.event.pull_request.base.sha` と `head.sha` の差分から docs-only を判定する（`tests.yml:105-120`）。
`edited` の payload の `base.sha` は付け替え後の base を指すので、判定も新しい base に対して行われる。

### 4.2 `ready_for_review` は要らない

**draft の PR でも、CI は既に起動している。** 6 本の workflow には draft を条件にした箇所が 1 つも無い。

```text
grep -rn -i draft .github/workflows          ->  0 件
grep -rln pull_request .github/workflows     ->  6 件（検査が workflow を読めていることの正の対照）
```

draft を解除しても head は変わらない。同じ head に対する CI は、draft の間に `opened` か `synchronize` で既に走っている。
`ready_for_review` を加えると、同じ内容の run が 1 回増えるだけである。

**workflow に draft で skip する条件を足すなら、そのときに `ready_for_review` も加える。** 両者は対で扱う。

### 4.3 `edited` を base 変更だけに絞らない

`edited` は base の付け替えだけでなく、**タイトルと本文の編集でも発火する。** この repo では PM が `gh pr edit --body-file` で本文を差し替えることがある。

絞り込みの素直な方法は、先頭の job で `github.event.changes.base` の有無を見て、残りの job を `if` で skip することである。**これは採らない。**

GitHub は、条件で skip された job を必須チェックの判定では成功として扱う。
一方、branch protection は、同じ commit・同じ名前の check run のうち**最新のもの**を見る。

したがって次の経路で、**失敗していた必須チェックが「成功」に置き換わる。**

```text
1  head X で bats が FAILURE
2  PR の本文だけを編集する -> edited で新しい run が起動する
3  base 変更ではないので、bats job は skip される
4  head X の最新の bats は skipped（= 成功扱い）になる
5  merge できる状態になる
```

`tests.yml` は docs-only の PR で既に job を skip している（§4.1）。ただしそれは、対象の差分に対して一貫した判定である。
上の経路は、**差分と無関係な編集が判定を上書きする**点で性質が違う。

**この GitHub の挙動は、本書では実測していない。** 公開文書の記述に基づく。実装 PR で確かめる（§7）。
仮にこの挙動が無かったとしても、絞り込みの利点は run の回数を減らすことだけである。検査の正しさを危険にさらしてまで得るものではない。

### 4.4 絞り込まない場合の代償

タイトルと本文の編集のたびに、全 workflow が起動する。

- `cancel-in-progress` により、1 つの PR で同時に走る `tests.yml` は 1 本に保たれる
- 実行中の run は取り消されて、同じ head で最初からやり直しになる。結果は変わらず、終わるのが遅れるだけである
- 追加の run は、PR の編集回数と同じだけ増える

**これは費用の問題であって、正しさの問題ではない。** 費用が問題になった場合の手当ては §6 に置く。

## 5. 案 2（close→reopen の運用化）を採らない理由

| 観点 | 評価 |
| --- | --- |
| CI の起動 | 起動する（`reopened` は既定に含まれる） |
| base の鮮度 | **保証しない。** reopen では `refs/pull/N/merge` が再計算されず、古い base の snapshot を検査して緑を返しうる（#273 の訂正コメント、#276） |
| 実行の確実さ | 人が付け替えのたびに思い出す必要がある。忘れても何も起きないので、忘れたことに気づけない |
| 記録 | PR の履歴に close と reopen が残り、閉じた理由を読み手が推測することになる |

**鮮度を保証しない手順を正式な運用に据えると、「reopen した = 正しく検査した」と読まれる。** これは #276 が示した誤読そのものである。
案 1 なら、付け替えという操作そのものが CI を起動するので、人の記憶に頼らない。

## 6. 残るリスク

| リスク | 扱い |
| --- | --- |
| `edited`（base 変更）で起動した run が、新しい base の merge ref を検査しているかは未確認 | 実装 PR の受入試験で実測する（§7）。確かめられなければ、base 付け替え後の緑を merge の根拠にしない |
| 本文編集による run の増加 | 実装後に run 数を観測する。費用が問題になれば、PR 本文の編集を merge 直前にまとめる運用から検討する（skip による絞り込みは §4.3 の理由で最後の手段とする） |
| `strict: true` との関係 | base を `main` へ付け替えた PR は、head が `main` の先端を含むまで merge できない。追随には head の更新が要り、その時点で `synchronize` が CI を起動する。これは本件と独立に鮮度を補う |

## 7. 実装 PR への要件

実装は別 PR（programmer）。workflow ファイルを変更するので、merge は producer アカウントで行う（`git.rule.md`「workflowファイルを含むPR」）。

### 7.1 変更

6 本の workflow の `pull_request` に、次を加える。**既存の `branches` と `paths` は変えない。**

```yaml
  pull_request:
    types: [opened, synchronize, reopened, edited]
```

`types` を書くと既定の 3 つが置き換わる。**3 つを書き落とすと、push で CI が起動しなくなる。**

### 7.2 受入試験（verifier の実測）

| # | 操作 | 期待 |
| --- | --- | --- |
| 1 | 試験用 PR を別 branch を base にして作り、base を `main` へ付け替える | `tests.yml` の run が `event=pull_request`、action `edited` で起動する |
| 2 | 1 の run が checkout した merge commit の親 | 付け替え後の `main` の先端を含む。含まなければ §6 の 1 行目のリスクが現実であり、報告して止める |
| 3 | 同じ PR に commit を push する（負の対照） | `synchronize` で起動する。既定の 3 つを書き落としていないことを確かめる |
| 4 | draft で PR を作る（正の対照） | `opened` で起動する。§4.2 の前提を確かめる |
| 5 | 本文だけを編集する | `edited` で起動する。§4.4 の代償が想定どおりであることを確かめる |
| 6 | 5 の実行中に本文をもう一度編集する | 先の run が取り消され、run は 1 本だけ残る |

§4.3 の「skip された job が必須チェックを上書きする」挙動は、本件の実装では job を skip しないので確かめなくてよい。
将来 skip による絞り込みを検討する場合は、先にこの挙動を実測すること。
