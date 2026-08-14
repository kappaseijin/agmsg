---
type: Design
title: "Issue #39: delivery capability status JSON contract"
status: accepted
issue: "https://github.com/kappaseijin/agmsg/issues/39"
timestamp: "2026-08-15T03:13:06+09:00"
---

# Issue #39: delivery capability status JSON contract

## 目的

work-queue consumer が、hook の設定有無ではなく、特定の agent seat が今この時点で message handoff を受けられる根拠を機械可読に取得できるようにする。
この contract は receiver への handoff と task の完了を明確に分離する。

## 公開 command

```bash
scripts/delivery.sh status <type> <project_path> --format json
```

`--format` を省略した既存の human output は変更しない。
JSON mode は `<type>` と `<project_path>` の両方を必須にする。

## JSON contract

成功時は `schemaVersion: 1` を含む一つの JSON object を stdout に返す。

```json
{
  "schemaVersion": 1,
  "type": "codex",
  "project": "/absolute/project/path",
  "runtime": "alive",
  "sessionId": "session-id",
  "deliverable": true,
  "liveness": "alive",
  "receipt": {
    "state": "handedOff",
    "queued": 0,
    "claimed": 0,
    "handedOff": 1,
    "unknown": 0,
    "ackSemantics": "receiver_handoff_not_task_completion"
  },
  "evidence": [
    { "source": "bridge", "state": "alive", "sessionId": "session-id" }
  ],
  "seats": [
    {
      "team": "example",
      "name": "programmer",
      "runtime": "alive",
      "sessionId": "session-id",
      "deliverable": true,
      "liveness": "alive",
      "receipt": {
        "state": "handedOff",
        "queued": 0,
        "claimed": 0,
        "handedOff": 1,
        "unknown": 0,
        "ackSemantics": "receiver_handoff_not_task_completion"
      },
      "evidence": [
        { "source": "bridge", "state": "alive", "sessionId": "session-id" }
      ]
    }
  ]
}
```

`runtime` と `liveness` の値は `alive`、`stale`、`missing`、`unknown`、複数 seat の集約だけに使う `mixed` のいずれかである。
`sessionId` は確認できる一つの session にだけ文字列で入り、確認できない場合または複数 seat の集約では `null` である。

`deliverable` は JSON boolean の `true` / `false`、または文字列の `"unknown"` である。

- `true`: role と対応する runtime が live で、session binding まで確認できる。
- `false`: configured delivery が無効、または stale / missing runtime という失敗根拠を確認できる。
- `"unknown"`: hook だけ、role-session だけ、または対応 type の runtime を観測できないなど、成功も失敗も十分に証明できない。

JSON consumer は `true` 以外を delivery gate の不通過として扱う。
設定済み hook は evidence にはなるが、単独では `true` を作らない。

## runtime ごとの根拠

### Claude Code

Claude Code は `run/ready.<team>__<name>` の role-scoped readiness sentinel と、その記録 session が live であることを確認する。
この sentinel は `watch.sh` が exclusive watcher の subscription を確立した後にだけ作るため、role と watcher の対応を表せる。

- live readiness sentinel: `alive` / `true`
- stale watcher pidfile または stale readiness owner: `stale` / `false`
- monitor/both 設定で active watcher が見つからない: `missing` / `false`
- broad watcher のみで role-scoped binding を観測できない: `unknown` / `"unknown"`

`turn` は次の agent turn での inbox check を設定するだけであり、現在の live receiver を証明しない。
従って `turn` のみを `deliverable: true` としない。

### Codex

Codex は `(team, name)` ごとの `role-session` record、`codex-bridge.<team>.<name>.pid`、対応 `.meta` の project/type/pid 整合性を検査する。

- live bridge + matching metadata + matching role-session: `alive` / `true`
- stale pidfile / metadata mismatch / dead bridge: `stale` / `false`
- session binding がない、または bridge と session の結び付きを確認できない: `unknown` / `"unknown"`

Codex の app-server が起動しているだけ、trust prompt の前、または between-turn hook が設定されているだけでは成功にしない。

### その他の type

この issue では Claude Code watcher と Codex bridge 以外の runtime liveness を推測しない。
監視 API がない type は `unknown` / `"unknown"` とし、evidence に `runtime_unobservable` を残す。
`off` は明示的な delivery 無効なので `missing` / `false` である。

## receipt state

各 seat の `receipt` は既存の `message-status.sh --format json` をそのまま machine-readable に再利用する。
`state` は message がない `none`、`queued`、`claimed`、`handedOff`、`unknown`、複数状態を集約する `mixed` のいずれかである。
`handedOff` は receiver が stdout / bridge へ handoff した ACK であり、task completion、Issue close、PR merge を意味しない。

top-level `receipt` は各 seat の count を合算する。seat が一つならその seat の state、複数で異なる nonzero state があれば `mixed` とする。

## 集約と error policy

`seats` は team、name の昇順で安定化する。
一 seat の top-level field はその seat を写す。
複数 seat の `runtime` / `liveness` は全 seat が同じ場合だけその値を返し、それ以外は `mixed` とする。
`deliverable` は全 seat が `true` なら `true`、全 seat が `false` なら `false`、それ以外は `"unknown"` とする。
登録 seat がないときは `seats: []`、`runtime/liveness: "missing"`、`deliverable: false` とする。

type/project の引数不備または unknown `--format` は stderr に usage を出して exit 2 とする。
runtime probe 自体が観測不能でも command を失敗にせず、JSON 内の `unknown` evidence で表現する。

## 実装境界

- `scripts/lib/delivery-capability.sh` に JSON serialization と type-specific probe を集約する。
- `scripts/delivery.sh` は options parser と JSON branch を追加し、human branch は既存 `do_status` を維持する。
- 既存 `message-status.sh --format json` を receipt の唯一の source of truth とする。human output の scraping はしない。
- README には command、state の意味、`true` だけを dispatchable とする使い方を自己完結で記載する。

## 検証

- hook only、turn、unstarted Codex、metadata mismatch、dead bridge が false success にならないこと。
- role-scoped live Claude watcher と live Codex bridge + seat が `true` を返すこと。
- receipt の `handedOff` が task completion を主張しないこと。
- unknown runtime が `unknown` を返すこと。
- human status output と既存 Bats regression を保持すること。
