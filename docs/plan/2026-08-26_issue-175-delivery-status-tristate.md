---
type: Plan
title: "Issue #175: delivery status の unknown を off と区別する"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/175"
root_cause: R3
root_issue: "https://github.com/kappaseijin/agmsg/issues/176"
timestamp: "2026-08-26T09:06:06+09:00"
---

# Issue #175: delivery status の unknown を off と区別する

## 属する根と主張

本 Issue は **R3: 代理指標が unknown を具体状態へ丸める** 子 Issue である。

JSON event-hook 型（`claude-code`、`codex`）の human `delivery.sh status` は、settings hooks file を読めないとき、第一行を `mode: unknown (unrecognized: ...)` と報告する。読めた file に agmsg hook が無いと確認できたときだけ `mode: off (...)` を報告する。`unknown` は `off` の別名でも自動設定の許可でもなく、設定漏れ・path 誤り・破損を調べるための観測結果である。

## 現在の証拠

`agmsg_delivery_status_default` は file が存在しない、resolver が空、または JSON として読めない三経路を `mode="off (unrecognized: ...)"` へ集約する。annotation はあるが第一語が `off` のため、先頭行だけを読む運用では「確認済みの無効」と「未確認」を区別できない。

一方 `delivery.sh set off codex <project>` は default apply が project の `.codex/hooks.json` を作成/更新し、agmsg hooks を除いた読める JSON を残す。そのため status は「settings を読め、agmsg hook が無い」ことを確認でき、`off` と報告できる。ただし source は「利用者が意図して off を選んだ」ことまでは記録しない。この PR の `off` は意図の推測ではなく、確認済み設定状態を表す。

現行 focused tests は、既存契約どおり `claude-code` の readable/no-hooks、missing project、malformed JSON を 3/3 green、doctor の unrecognized project を 1/1 green にした。これは旧 wording が試験で固定されている基準であり、新しい expected 値へ先に変えれば current HEAD は RED になる。

```mermaid
flowchart TD
    A[delivery.sh status: JSON event-hook type] --> B{settings file を解決して JSON として読めたか}
    B -->|no| C[mode: unknown (unrecognized: reason)]
    B -->|yes| D{agmsg SessionStart / Stop hook}
    D -->|monitor / turn / both| E[確認済み mode]
    D -->|none| F[mode: off (no agmsg delivery hooks installed...)]
    C --> G[設定漏れ・path・破損を確認。自動処理の根拠にしない]
    F --> H[自動 delivery は停止]
```

## 選択: 案A

採るのは **案A**: 1 PR で default JSON event-hook 型の human `mode` だけを三値化し、他の unknown 丸めは同じ手番で列挙するが変更しない。

案B（status の全フィールドを一度に三値化）は採らない。rule-file 型は `set off` 自体が file を削除するため、missing file と明示 off を区別するには persistent marker か新しい state contract が要る。capability JSON は human output と別 ABI で、runtime/receipt/seat aggregation の検証も必要である。これらを #175 に混ぜると「human mode の先頭状態を訂正する」一主張を越え、1 PR = 1 主張に反する。

今回の clean-break は default branch の旧 `mode: off (unrecognized: ...)` を残さないことにある。旧 prefix を互換出力として残したり、caller 側で両形式を受け入れる分岐を追加したりしない。

## 実装契約

1. `scripts/delivery.sh:agmsg_delivery_status_default` の missing settings file、unresolvable hooks path、invalid JSON の三分岐は、詳細理由を維持して `mode: unknown (unrecognized: ...)` を第一行に出す。exit status は現在どおり 0 とする。これは status が設定を読めなかった観測であり、CLI transport failure ではない。
2. settings file を読め、agmsg hook が 0 件の default JSON event-hook 型は、現在どおり `mode: off (no agmsg delivery hooks installed for this project)` を出す。`monitor`、`turn`、`both` の output と hooks mutation は変えない。
3. `scripts/doctor.sh` は変更しない。現在の `_boring` case は `off*` だけを collapse し、`unknown` は full block として残る。regression test でこの consumer が `nothing to report` へ丸めないことを固定する。
4. Claude Code template の actas/drop 分岐は旧 `mode: off (unrecognized: ...)` を `mode: unknown (unrecognized: ...)` に置換する。unknown は Monitor を起動せず、利用者へ設定を確認するよう伝える既存の fail-closed 動作を維持する。
5. README の Delivery modes 節に、`status` の `unknown` は mode `off` ではなく「hooks config を観測できない」こと、確認する path と明示 `/agmsg mode <choice>` を案内する説明を加える。README だけでこの出力を運用判断できる状態にする。

## 走査結果: 今回変更しない R3 候補

| 場所 | 現在の丸め | #175 での扱い |
| --- | --- | --- |
| `scripts/lib/delivery-rulefile.sh`、`opencode` / `grok-build` plug | rule file 不在を `mode: off` とする。`set off` も file を消すため、現行状態だけでは未設定と区別不能 | R3 follow-up。marker を入れるか、mode 契約を再定義する必要があり対象外 |
| `scripts/lib/delivery-capability.sh:agmsg_delivery_capability_config_mode` | missing/invalid JSON hook file の query failure が `off` へ落ち、seat は `deliverable: false` になる | R3 follow-up。machine JSON ABI を human wording と同時に変えない |
| 同 capability の `identities.sh ... || true` と zero-seat aggregate | roster read failure が「registered seat なし」になり `deliverable: false` となり得る | R3 follow-up。read failure と confirmed empty roster を分ける |
| `agmsg_delivery_capability_file_field` と Codex metadata mismatch | file read failure/empty field が stale + `deliverable: false` に収束する | R3 follow-up（#162 の型）。metadata missing と unreadable を分ける |
| `app/src-tauri/src/agmsg.rs:agmsg_delivery_mode` | `mode:` line 不在を `Ok("off")` にする | desktop app の別 PR。shell output contract を先に変え、app fallback は列挙のみ |
| `scripts/doctor.sh` 初期値 | output に mode line が無いと local default は `off` だが、status non-zero は warning にする | #175 の new `unknown` output は既存 case で collapse されない。no-line/zero の契約は別監査 |

`hermes` の `mode: off` は manual-only type という manifest 契約で、unknown の代理ではない。`codex-shim.sh` は `mode: monitor` の完全一致だけで monitor 起動するため、新しい `unknown` では起動せず、変更不要である。

## 変更範囲

| ファイル | 変更 |
| --- | --- |
| `scripts/delivery.sh` | default human status の unknown prefix と説明を更新 |
| `tests/test_delivery.bats` | missing / malformed settings の RED、Codex の `set off` 正対照 |
| `tests/test_doctor.bats` | unknown を doctor が boring/off と扱わない consumer regression |
| `scripts/drivers/types/claude-code/template.md` | actas/drop の first-line contract を unknown へ更新 |
| `tests/test_claude_template.bats` | old prefix 不在と new prefix 2 箇所を固定 |
| `README.md` | `status` の unknown 運用を自己完結で説明 |
| `docs/plan/2026-08-26_issue-175-delivery-status-tristate.md` | この設計記録と R3 audit ledger |

rule-file 型の persistence、delivery capability JSON、desktop app fallback、`~/.agents/skills/agmsg` の production config、settings の自動修復、watcher 起動、global state migration は変更・試験台にしない。

## 検証計画

1. **RED / 負対照**: bare temp project の `codex` status（`.codex/hooks.json` 無し）は第一行が `mode: unknown (unrecognized: no settings file found at ...)` であり、`mode: off` で始まらない。既存 `claude-code` missing project と malformed JSON test も同じ `unknown` 期待値へ先に更新し、current HEAD で RED を確認する。
2. **正対照**: `delivery.sh set off codex "$TEST_PROJECT"` 後は `.codex/hooks.json` が readable で、第一行が従来どおり `mode: off (no agmsg delivery hooks installed for this project)` になる。`monitor`、`turn`、`both` の existing tests も green とする。これは file 不在と確認済み no-hooks を同じ `off` にしていない検査である。
3. **consumer / clean-break**: doctor の missing project は `mode: unknown` と詳細を出し、`nothing to report` を出さない。Claude template は `mode: unknown (unrecognized: ...)` を actas/drop 各一箇所に持ち、旧 `mode: off (unrecognized: ...)` は source template から 0 件にする。`codex-shim.sh` の exact `mode: monitor` positive control は変更せず、unknown が monitor launch を許可しないことを示す。
4. **KILLED**: implementation の missing-file branch を一時的に旧 `mode="off (unrecognized: ...)"` へ戻す。bare Codex negative control と template old-prefix absence assertion は RED にならなければならない。直ちに `unknown` へ復元して focused suite を green に戻す。この mutation は今回の unknown→off 丸めそのものを殺す。
5. **実行**: `bash -n scripts/delivery.sh scripts/doctor.sh`、`bats tests/test_delivery.bats`、`bats tests/test_doctor.bats`、`bats tests/test_claude_template.bats`、`rtk git diff --check <base>..<head>`、fixed HEAD の CI、Claude formal reviewer の一括 review を使う。`delivery.sh status codex ...` の behavior は Windows 固有ではないため、OS 別 runner の新規 fixture は足さない。

## PR 契約

1 PR = 「default JSON event-hook 型の human delivery status が、設定を観測できない `unknown` を確認済み `off` として先頭表示しない」という一主張にする。producer は `agmsg_programmer_codex`、formal reviewer は `agmsg_reviewer_claude`。review 対象は fixed HEAD の PR 全差分とする。

PR を merge しても R3 全体は close しない。上表の残件を Issue #176 の R3 監査として追跡し、同じ root の走査なしに #175 を「根治」とは扱わない。
