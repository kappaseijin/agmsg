---
type: Design
title: "Issue #246: 非集約registrationの最小公開照会契約"
status: proposed
timestamp: "2026-09-05T23:55:14+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/246"
source_head: "e58dbafad5a84be625f070385bb0c076c3daa4db"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# 非集約registrationの最小公開照会契約

> ゲート訂正: [Issue #254の段階区分](2026-09-06T023404_issue-254-ledger-migration.md)を本書の旧ゲート記述より優先する。
> 実接続実装着手には公式提供版・B3採用経路・提供側独立対照・consumer試験計画の受入を要求し、実装後のconsumer統合結果はpilot起動前に要求する。
> 実TUIの運用受入はP2合格条件であり、前段へ循環させない。

## 主張と既存機能

agmsg提供側に、team内の全registration tupleと結果の完全性を返す最小のread-only公開入口を追加する案とする。
consumerは完全な結果だけを用い、一意・複数・正常0件・照会不明を区別する。
PM role/kind、account選択、G4、独自policyを上流APIへ含めない。
本書は設計・適合試験計画・提案パッチ構成の定義であり、実装・上流投稿は行わない。

公式cutoffの`api.sh`はJSONLのread-only入口であり、get_membersはtypesを集約しprojectをLIMIT 1で返す。
`identities.sh`は指定project/typeのteam/agentをDISTINCTし、whoamiは名前を集約する。
team-listのJSONはteam metadataであり全registrationの代用にならない。
上記は固定objectを直接読んだ事実であり、故障注入済みという意味ではない。
forkの`api.sh ... registrations`追加は設計入力に留め、空出力・破損等の契約をそのまま完成品としない。

## 方式比較と推奨

| 方式 | 判断 |
| --- | --- |
| membersの既存出力を変更 | 既存consumerの集約表示契約を壊すため不採用 |
| consumer側でteam JSONを読む | 内部schema readerの外付けになるため不採用 |
| 提供側api.shへ独立resource追加 | 推奨。既存reader・validationを再利用し、旧resourceは無変更 |

提案commandは`api.sh get teams <team> registrations --schema-version 1`とする。
schema-versionは必須とし、未対応版や未知optionを無視しない。
一つのJSON objectを一行で返す。既存JSONL resourceとの混同を避け、このresourceだけをversion付きenvelopeとして文書化する。

## 出力と終了状態

成功envelopeは`schemaVersion:1, resource:"registrations", team, status:"ok", complete:true, registrations:[...]`を持つ。
各行は`agent, type, project, canonicalProject`とする。teamはenvelopeにあり、tupleはteam/agent/type/canonicalProjectで対応する。
projectは登録値、canonicalProjectは提供側の既存project解決規則による照会時の正規形とする。
agent/type/projectの対応を崩さず、DISTINCTやLIMIT 1による集約・重複除去をしない。
同一tupleの重複も保存し、consumerは複数件と判定する。順序はagent/type/projectのbyte順で安定化する。
列挙上限を設ける場合は打切り成功を返さず`limit_exceeded`として失敗する。

| 状態 | status / reason | exit / registrations |
| --- | --- | --- |
| 全対象を正常に読取 | ok、complete=true | 0 / 全行。登録0件なら空配列 |
| teamが正常に存在しない | not_found / team_not_found、complete=false | 1 / null |
| JSON/型/必須field不正、未対応保存schema | unknown / data_invalidまたはstorage_schema_unsupported | 1 / null、complete=false |
| 読取・canonical化不能 | unknown / read_failedまたはproject_unresolvable | 1 / null、complete=false |
| 観測中にsource変更を検出 | unknown / concurrent_change | 1 / null、complete=false |
| 引数・API版が不正 | error / invalid_argumentまたはunsupported_schema_version | 2 / null、complete=false |

既存teamの空agents、remote memberの空registrationsは正常0行として表現できる。
legacy保存形式は提供側が現在受理する形に限り同じtupleへ正規化する。
未対応形式を空registrationsへ変換しない。
一件でも解決不能なら他の正常行だけを返さない。エラーはfield名と分類のみで、設定全文や認証値をstderrへ出さない。
API未提供でusage textが返る古い公式版は、consumerが非JSONまたはschema不一致として拒否する。

## read-onlyと整合性

既存提供側のteam名検証・保存形式解釈・project正規化を再利用し、外側agguildにその複製を持たせない。
join/reset/normalize/migrate/init-db/engine起動を呼ばない。
保存源を一度取得して全行を検証・buffer化し、成功判定前にstdoutへ行を流さない。
source identityと内容の再確認で変更を検出したらunknownとし、新状態へ自動追従しない。
これは単一照会の完全性であって、複数ファイルを跨ぐtransactionや返却後の登録固定を保証しない。
同値へ戻るABAや返却後の変更を防ぐleaseとして使わない。
consumerは利用直前に再照会し、process/claim等の別条件と合わせて判定する。

## 適合試験パッチの仕様

実装担当は公式cutoffへ当てられる小さなprovider patchとtest patchを、このrepository内でレビュー可能に準備する。
候補差分は`api.sh`の新route・reader、必要最小限の提供側helper、`tests/test_api_registrations.bats`、READMEの利用節に限定する。
既存membersの出力とexit codeは変更しない。

| 試験 | 必須期待値・対照 |
| --- | --- |
| 正常1名1tuple | 1行、complete=true、exit0 |
| 同名で2project/2type | 全tupleの対応が保存される。types×projectの誤った直積を作らない |
| 同cwd複数席・完全重複tuple | 行が消えない。一意consumerは拒否する |
| remote member・正常空team | complete=trueの0行とteam_not_foundを区別 |
| legacy形式と未知保存形式 | 前者は既存規則どおり、後者は失敗。勝手なfallbackなし |
| 一部破損・読取不能・missing project | 他の正常行があってもexit1、部分配列を返さない |
| 指定team外に壊れたteam | 対象teamの照会を汚染しない。全team走査をしていない対照 |
| source交換をbarrierで同期 | concurrent_changeまたは安全な単一snapshot。混合配列は不可 |
| readonly | 前後で設定/DB/runtimeの内容と更新状態が不変。新file/engineも0 |
| API版・文字・path | 未対応版拒否、空白/日本語/引用符がJSON round-tripする |

負の変異は`LIMIT 1`追加、`DISTINCT`追加、破損を空配列化、途中stdout出力の4種を含める。
それぞれ対応する試験が落ちることを確認し、正常1件の成功だけで契約を受け入れない。
workerの結果をproducerの独立検証と呼ばず、既存verifierがvalue/cutoff/source/commandと生出力を別contextで提出する。
formal reviewerは固定HEAD全差分を一括走査する。

## consumerと提供ゲート

agguildはschema一致・exit0・complete=trueを確認後、明示team/agent/type/canonicalProjectへ絞る。
0件は対象外、1件は登録一致、2件以上は曖昧とする。対象外はunknownと区別するが、いずれもPM認可の許可にはしない。
process binding・claim・account policyはconsumer側の別責務で、本APIが権限を返すわけではない。
READMEは新公開command・schema・失敗状態・read-only限界を利用者が単独で理解できるよう更新が必要である。
提案段階の本書ではREADMEと実装を変更しない。

P2解除は公式提供版＋固定契約＋独立正負対照＋consumer統合受入を条件とする。
forkの試作完成、上流への提案、未merge PRは公式提供の代わりにならない。
上流採用時にcommand/schemaが変われば対応表とconsumer試験を更新して再受入し、私設互換fallbackを残さない。
上流投稿と実装開始はmanagerからの別依頼とする。Issue #247のactas ownerとは別PRで扱う。
