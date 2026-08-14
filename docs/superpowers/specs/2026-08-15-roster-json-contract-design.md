---
type: Design
title: "Issue #38: team / whoami JSON roster contract"
status: accepted
issue: "https://github.com/kappaseijin/agmsg/issues/38"
timestamp: "2026-08-15T02:20:04+09:00"
---

# Issue #38: team / whoami JSON roster contract

## 目的

外部の work-queue consumer が team roster と現在の identity lookup を機械可読に取得できるようにする。
この変更は G1 の roster contract だけを扱い、配送可否・受信 ACK・work item の状態は扱わない。

## 決定

team config の新規作成時は root に `schemaVersion: 1` を置き、各 member に `kind` と `role` を保存する。
`kind` は `seat`、`human`、`service` のいずれかであり、`role` は空でない明示文字列である。
agent 名や runtime 名を分割して role / kind を導出しない。

`join.sh` は既存の4必須引数を維持し、任意の `--role <role>` と `--kind <kind>` を受け付ける。
新規 member には、指定がなければ `role: "unassigned"` と `kind: "seat"` を明示保存する。
これは name/type からの推測ではなく、join が作る agent seat の安全な初期状態である。
既存 member の metadata は、明示 flag がない限り再 join で上書きしない。

既存 schema の config は、人間向けの既存 command で従来どおり読める。
一方 `--format json` は config の `schemaVersion`、team 名、member `kind`/`role`、registration のすべてを検査し、欠落・不正なら stdout を出さず exit 2 と `schema error:` を stderr に返す。
不完全な roster を成功 JSON として返さない。

## 公開 command

### `team.sh <team> --format json`

成功時は次を安定した field 名と member 名の昇順で返す。

```json
{
  "schemaVersion": 1,
  "team": "scale_exporter",
  "members": [
    {
      "name": "scale_exporter_architect_codex",
      "kind": "seat",
      "role": "architect",
      "registrations": [
        { "type": "codex", "project": "/absolute/project/path" }
      ]
    }
  ]
}
```

人間向けの `team.sh <team>` 出力は変更しない。

### `whoami.sh <project> <runtime> --format json`

`runtime` は type registry に登録された runtime 名であり、渡された project は既存の project resolver で正規化する。
成功 JSON は lookup context を `runtime` と `session.project` に、完全一致した registration を `registrations` に返す。

```json
{
  "schemaVersion": 1,
  "runtime": "codex",
  "session": { "project": "/absolute/project/path" },
  "registrations": [
    {
      "team": "scale_exporter",
      "name": "scale_exporter_architect_codex",
      "kind": "seat",
      "role": "architect",
      "registration": { "type": "codex", "project": "/absolute/project/path" }
    }
  ]
}
```

一致がなければ `registrations` は空配列である。
matching registration を含む config が legacy / malformed schema なら JSON mode は schema error で止まる。
無関係な team の legacy config は lookup を妨げない。
この command は live delivery や session liveness を主張しない。それらは #39 の delivery capability contract の責務である。

## 実装境界

- `scripts/lib/roster-contract.sh` が schema validation と stable JSON serialization を一元化する。
- `scripts/team.sh` は human branch を保持し、JSON branch だけを helper に委譲する。
- `scripts/whoami.sh` は human identity/suggestion branch を保持し、JSON branch では exact registration だけを helper から集める。
- `scripts/join.sh` は新規 config/new member の metadata を書く。rename/leave/reset は member object を移動・保持する既存実装のため変更しない。
- README を唯一の利用者向け説明として更新する。内部設計の経緯は README に入れない。

## 検証

- normal config: member metadata、複数 registration、stable order、whoami exact match を Bats で確認する。
- negative config: root version / member kind / member role / registration field の欠落を JSON exit 2 と空 stdout で確認する。
- compatibility: legacy config の human `team.sh` と human `whoami.sh` の結果を確認し、JSON mode だけ fail-closed であることを確認する。
- `bash -n`、対象 Bats、全 Bats、`git diff --check` と PR CI を実行する。
