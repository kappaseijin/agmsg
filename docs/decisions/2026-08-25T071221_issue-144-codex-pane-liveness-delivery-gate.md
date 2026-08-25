---
type: Design
title: "Issue #144: Codex pane liveness を delivery capability の gate にする設計"
status: on_hold
issue: "https://github.com/kappaseijin/agmsg/issues/144"
timestamp: "2026-08-25T07:12:21+09:00"
updated: "2026-08-25T15:17:23+09:00"
---

# Issue #144: Codex pane liveness を delivery capability の gate にする設計

## Controlled reproduction の結果

この設計の CRASHED 正例は controlled reproduction の実 tail を前提としていた。
訂正後の隔離実測では `HERDR_ENV` 未設定でも自分用 workspace を作成できたが、temporary
`resume_cwd = "current"` profile で real session を `codex resume` した結果は live TUI であり、
`requires --cd` も bare shell fallback も得られなかった。healthy control は `CONTROL_OK` を返した。

さらに clone に登録した temporary role を spawned child の `$agmsg actas` が見つけられず、
bridge / role-session を通る control を isolation 内で測定できなかった。このため crash は 0/必要 2
であり、fixture の provenance 条件を満たさない。案 B の合成 fixture へは戻らず、実装全体を
**on hold** とする。詳細な値・command・限界は
`docs/plan/2026-08-25_issue-144-controlled-reproduction-preflight.md` に記録する。

## 主張

`scripts/delivery.sh status codex <project> --format json` の Codex seat に、bridge とは独立した pane 観測値 `paneLiveness` を加える。
既存の bridge / role-session による delivery 判定と `paneLiveness` の両方が確認できる場合だけ `deliverable: true` にする。

これにより、bridge が app-server 経由で応答を続けていても、spawn された Codex TUI が bare shell や crash 表示へ落ちた seat は dispatchable としない。

## 背景と境界

現行の Codex capability は role-session、`codex-bridge.<team>.<name>.pid`、`.meta`、bridge PID の組み合わせを観測する。
この経路は pane の TUI と別であり、Issue #144 の実インシデントでは bridge が生きたまま TUI だけが失われた。
したがって、bridge の `alive` は pane の操作可能性を含意しない。

Issue #138 の `pane read` 空白 / 凍結は、この同じ観測点で `unknown` と表す。
空白を crash と断定せず、bridge が生きていても `deliverable: true` を返さない。

この変更は検出だけを扱う。
`agent-hard-reset` は復旧手順のままにし、`spawn.sh` の readiness 統合、再起動、再送、`--fresh` の利用は別の主張とする。

## 公開 JSON 契約

`schemaVersion: 1` は維持する。
既存 consumer が未知 field を無視できる加法的変更として、top-level と各 `seats[]` 要素へ次を加える。

```json
{
  "deliverable": true,
  "paneLiveness": "live",
  "paneEvidence": [
    {
      "source": "herdr-pane-tail",
      "state": "live",
      "signature": "codex_input_prompt"
    }
  ]
}
```

seat の `paneLiveness` は `live`、`crashed`、`unknown` のいずれかとする。
`paneEvidence` は少なくとも `source`、`state`、`signature` または `reason` を持つ。
`signature` は語そのものではなく、外出しされた語彙内の安定 ID とする。
これにより、生の pane 内容や会話履歴を status JSON へ漏らさない。

複数 seat の top-level `paneLiveness` は、全 seat が同じ値の場合だけその値を返す。
異なる場合は `unknown` とし、`paneEvidence` に `multiple_seats` を残す。

## 判定

`B` を既存 bridge/role-session 判定（`true` / `false` / `"unknown"`）、`P` を seat の `paneLiveness` とする。
最終 `deliverable` は次の三値 AND で計算する。

| B | P | 最終 `deliverable` |
| --- | --- | --- |
| `false` | 任意 | `false` |
| `true` | `live` | `true` |
| `true` | `crashed` | `false` |
| `true` | `unknown` | `"unknown"` |
| `"unknown"` | `live` / `unknown` | `"unknown"` |
| `"unknown"` | `crashed` | `false` |

既存 consumer の契約どおり、`true` 以外は delivery gate を通過しない。
`unknown` は観測不能と crash を混同しないために残すが、fail-closed の dispatch 判定では `false` と同じく不通過である。

## 観測器

`scripts/lib/delivery-capability.sh` に Codex 専用の pane probe を置き、既存 `agmsg_delivery_capability_codex_bridge` の後で同じ `(team, name, project)` に対して呼ぶ。
probe は spawn が残す placement record を読み、project/type が一致する `herdr:<pane_id>` のみを対象にする。
tmux placement、placement 未記録、record 不整合、`herdr` 不在、`herdr pane read` の失敗は command 失敗ではなく `paneLiveness: "unknown"` と根拠へ変換する。

有効な target では `herdr pane read <pane_id> --source recent --lines 60` を実行し、末尾 8 行だけを照合する。
判断順序は次のとおりである。

1. 出力が空、読取りエラー、または placement を解決できない場合は `unknown`。
2. 末尾に live signature がある場合は `live`。
3. 末尾に crash signature または bare-shell prompt がある場合は `crashed`。
4. それ以外の非空出力は `unknown`。

live signature を crash signature より優先する。
会話履歴中の incident 引用は、末尾に現行 Codex prompt があれば `live` を覆さない。
一方で、process/bridge PID は従来どおり補強根拠に留め、pane の `live` を否定する単独根拠にしない。

## 外出しする語彙

live/crash 語彙は `scripts/drivers/types/codex/pane-liveness-signatures.sh` の一ファイルに置く。
probe 本体と Bats fixture はこのファイルの stable ID を参照し、語彙を別々に複製しない。

初期分類は次の ID 群を想定する。

| state | signature ID | 用途 |
| --- | --- | --- |
| `live` | `codex_input_prompt`, `codex_interrupt_hint`, `codex_shortcuts_hint` | 現在の Codex TUI が見えている根拠 |
| `crashed` | `resume_cwd_requires_cd`, `queued_follow_up_inputs`, `command_not_found`, `segmentation_fault`, `panic`, `bare_shell_prompt` | TUI の crash / stuck / shell fallback の根拠 |

語彙変更時は probe、fixture、README の state 説明を同じ PR で更新する。
語彙の文言自体は Codex TUI の変更で不安定なので、実装は ID と正規表現の対応だけをこの一ファイルへ限定する。

## テストと対照

`tests/test_delivery.bats` の既存「live Codex bridge requires its recorded seat」を、bridge fixture に valid な `herdr:` placement と fake `herdr` を加える形で拡張する。
status JSON の人間向け出力は変更しない。

| fixture | 期待値 | 検査対象 |
| --- | --- | --- |
| 現行 Codex prompt を含む末尾 | `paneLiveness=live`、bridge が live なら `deliverable=true` | live 正の対照 |
| controlled reproduction で採取した crash pane tail | `paneLiveness=crashed`、bridge が live でも `deliverable=false` | crash 検出の正の対照 |
| scrollback 前方に crash 語を引用し、末尾に live prompt | `paneLiveness=live`、`deliverable=true` | 履歴引用による偽陽性の負の対照 |
| 非空だが語彙に一致しない末尾、空出力、herdr エラー | `paneLiveness=unknown`、bridge が live でも `deliverable="unknown"` | #138 と観測不能の fail-closed 対照 |
| crash fixture の active signature を非一致値へ mutation | crash を検出する assertion が失敗する（KILLED） | 語彙検査が常に成功する偽陰性の防止 |

この mutation-kill は、fixture 内にたまたま存在する語で済ませない。
実装 PR では active signature を変えた実行が KILLED になった生出力を残す。
健康 pane の履歴に crash 語を引用する fixture は、tail-only 判定のために必須とする。

## 実クラッシュ fixture の代替方針

herdr-agent-monitor の PM 席が停止しており、捕獲済み pane 末尾は当面受領できない。
Issue 本文にある `tui.resume_cwd = 'current' requires --cd` は実インシデント由来の
diagnostic だが、採取済みの pane tail そのものではない。したがって、既知 signature を
寄せ集めた fixture（案 B）だけを CRASHED 正例の根拠にはしない。

**案 A を採用し、再現不能なら案 C へ戻る。**
使い捨て clone、使い捨て role、隔離 `AGMSG_STORAGE_PATH`、隔離 herdr workspace を使い、
temporary role の Codex config だけに `[tui] resume_cwd = "current"` を置いて
herdr/agmsg 経由の resume を起動する。既存 role の config、production DB、team roster、
既存 pane は変更しない。

採用可能な fixture は次をすべて満たす controlled reproduction の末尾 8 行から最小化する。

1. `requires --cd` を含む resume failure 又は bare shell fallback が、独立した 2 run で同じく現れる。
2. 同じ clone / bridge 条件で `resume_cwd` を持たない temporary role は Codex live prompt になる。
3. crash diagnostic を過去の会話として引用した healthy tail は `live` のままである。
4. crash signature を非一致値へ変える mutation が CRASHED assertion を KILLED する。

fixture の provenance は `controlled-reproduction` と明記し、捕獲済み production pane tail と
同一視しない。実運用 tail が後日得られたら、同じ stable signature ID に対する追加 fixture
として扱い、controlled fixture を黙って差し替えない。

この再現が失敗する、又は failure が live prompt と bare shell / diagnostic を区別できない場合、
実装を保留する（案 C）。案 B の合成 fixture は parser の単体確認には使えるが、CRASHED の
受入れ正例・語彙追加・`deliverable=false` の出荷根拠にはしない。

## 実装単位と非対象

この設計から作る実装 PR は 1 主張だけとする。

- `delivery.sh status --format json` の Codex seat が pane liveness を出し、最終 `deliverable` がそれを AND する。

今回扱わないものは次のとおりである。

- `spawn.sh` の readiness / pane 起動待ちへの統合。
- `agent-hard-reset` の復旧手順、pane の close、再spawn、再送。
- Issue #138 の原因推定、長文投入との相関、blank/frozen の恒久対処。
- Claude Code や他 vendor の pane 判定。

README 更新は実装 PR に含める。
コマンド、`paneLiveness`、三値 AND、`true` だけが dispatchable であることを、README だけで利用者が理解できるように追記する。

## 根拠

- [Issue #144](https://github.com/kappaseijin/agmsg/issues/144): bridge が正常でも Codex TUI が落ちる実インシデントと受入条件。
- `scripts/lib/delivery-capability.sh`: 現在の Codex bridge / role-session の capability 判定。
- `scripts/spawn.sh`: herdr 起動時に `(team, name)` の placement record へ `herdr:<pane_id>` を記録する経路。
- `~/.agents/bin/verify-agent-idle.sh`: pane 末尾優先、live 優先、process を補強根拠とする先行観測。
- `docs/decisions/2026-08-24T221749_issue-138-pane-log-correlation.md`: blank/frozen を原因断定せず unknown として記録する制約。
- [Issue #144](https://github.com/kappaseijin/agmsg/issues/144): `resume_cwd = "current"` と `requires --cd` の実インシデント記述、および既知 signature / tail-first の受入条件。

## 確認日時

2026-08-25T07:12:21+09:00
