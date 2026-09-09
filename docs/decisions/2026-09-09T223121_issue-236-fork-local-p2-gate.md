---
type: Design
title: "Issue #236: fork-local providerによるP2 pilot gate再設計"
description: >-
  公式agmsgの採用待ちを、kappaseijin/agmsgで固定commit・capability・独立対照を
  検証するprovider契約へ置き換え、P2接続実装とIssue #236 closeの条件を定める。
timestamp: "2026-09-09T22:31:21+09:00"
updated: "2026-09-09T22:31:21+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/236"
source_head: "4fa160aebf7437c2fd686e256a2d7f877c8cebab"
producer: agmsg_architect_codex
reviewer: agmsg_reviewer_claude
---

# Issue #236 fork-local P2 gate再設計

## 決定

Issue #236のP2接続実装は、公式`fujibee/agmsg`へのPR又は採用を待たない。

providerは`kappaseijin/agmsg`の固定commitだけを採用し、B1/B2のversion付き公開command、
P2が実際に使うB3 capability、provider独立対照、consumer試験計画を一組で受入する。

`docs/decisions/2026-09-06T023404_issue-254-gates.json`をformat version 2へ更新し、
公式提供を要求する`official-b1-b2`を削除した。

この変更はP2接続実装又はpilot起動を解除しない。

providerのfixed commitが空、B1/B2の対照未受入、B3経路未宣言、又はconsumer計画未受入なら、
`connectionImplementation`は`blocked`のままである。

## 1. 訂正する依存境界

Issue #236のP2 pilotは、PM委譲強制機能の段階2である。

P2/pilotはIssue #222の別作業ではない。

従来はP2接続実装の前提を「公式B1/B2提供、B3公開契約、提供側対照、consumer計画」としていた。

公式へのPR提出が禁止されたため、公式提供を待つ条件は満たせない。

一方、fork内に存在する実装を、version・入口・対照なしに暗黙のfallbackとして使うことも許可しない。

新しい前提は、fork-local providerを明示的に採用し、その観測可能な契約を受入することである。

Issue #253がCLOSEDであることは、B3全機構、F/O互換、又はB3非依存を証明しない。

P2は必要なB3 capabilityを宣言してから判定する。

## 2. fork-local provider契約

provider manifestは少なくとも次を一つのschema versionで固定する。

| field | 値又は判定 | fail-closed条件 |
| --- | --- | --- |
| `repository` | `kappaseijin/agmsg` | 他repository、未解決root |
| `commit` | 40桁のGit commit。consumerと試験で同一 | 空、短縮SHA、不一致、未到達object |
| `contractSchemaVersion` | `1` | 未知又は欠落 |
| `b1.command` | `api.sh get teams <team> registrations --schema-version 1` | 非0、schema/status不正、部分結果 |
| `b2.command` | `api.sh get teams <team> actas-owner <agent> --schema-version 1` | 非0、unknown、owner/absenceの混同 |
| `b3.requiredCapabilities` | P2 adapterが使うmessage操作だけの列挙 | 空の暗黙許可、未宣言操作、検証不能な操作 |
| `evidence` | provider対照、consumer計画、review対象HEADのURL/digest | 一つでも欠落又は別commit |

manifestはprovider rootを任意環境変数から解決しない。

consumerはmanifestが指定したcanonical rootとcommitを照合してから公開commandを固定argvで呼ぶ。

内部SQLite、claim file、roster JSONをconsumerが直接読む経路は契約の代替にならない。

## 3. B1、B2、B3の限定

### B1 registration

B1は全registrationの`team / agent / type / project / canonicalProject` tupleを、集約せず完全に返す。

正常な空配列と`not_found`、`unknown`、部分読取を区別する既存schema version 1を使う。

同名agentの複数project/type、完全重複tuple、破損entry、source交換を正負対照にする。

### B2 actas owner

B2は対象agentのownerを副作用なしで`owned`、`stale`、`absent`、`not_found`、`unknown`へ分類する。

空owner、読取不能、競合、非regular pathを`absent`又はfreeへ縮退しない。

owner tokenはSIDだけでなくopaque tokenとして照合する。

### B3 P2 message path

B3を「既存B3全体」又は「公式採用済み」として要求しない。

P2接続実装の設計PRは、必要操作を`send`、`inbox`、`history`、`handoff receipt`、
`claim/release/ack`の各capabilityへ分解し、使うものだけをmanifestへ列挙する。

`claim/release/ack`を使わない経路は、固定HEADのconsumer回帰でその参照が0件であることを示す。

使う経路はteam、recipient、message ID、owner、失敗状態を公開結果で対応付ける。

fork内のprivate schemaを複写する、opaque IDを別IDとして推測する、又はunknownを成功へ変える実装は不合格である。

## 4. gate遷移

更新後の機械可読gateの初期状態は、`provider.status=unverified`、B1/B2=`implemented-unaccepted`、
B3=`path-unselected`、`connectionImplementation=blocked`である。

| 段階 | 必要な証拠 | 解除してよいもの | 解除しないもの |
| --- | --- | --- | --- |
| G1 provider contract | manifest schema、固定commit、B1/B2/B3宣言、reviewer受入 | provider契約の採用 | 接続実装 |
| G2 provider controls | B1/B2の正負対照、B3 capability別対照、commit/root不一致拒否、read-only確認 | `b1-b2-local-controls`等の個別state | pilot起動 |
| G3 consumer plan | P2 adapterの必要capability、操作別payload、宛先、failure/unknown、consumer対照のreviewer受入 | 接続実装の計画 | live接続 |
| G4 connection implementation | G1〜G3が同一provider commitで受入済み | P2接続実装の着手 | pilot起動、P2合格 |
| G5 pilot start | 接続実装固定HEADのreview、全required tests、隔離consumer統合 | 専用pilot起動 | 現PM交代、P2合格 |

`gates.json`のstateを`ready`へ変えるのはG1〜G3のartifact URL、commit、対照結果を同じPR又はIssue更新で再取得した後だけである。

「forkにcodeがある」「過去のfixtureがPASS」「Issue #253がCLOSED」だけでは遷移しない。

## 5. 必須対照

| 対象 | 正の対照 | 負の対照 |
| --- | --- | --- |
| provider pin | manifestのcommit/rootでB1/B2が応答する | commit不一致、別root、未知schemaを拒否 |
| B1 | 複数registration tupleを完全に返す | `LIMIT 1`、重複消去、破損entryの部分成功をKILL |
| B2 | alive ownerと正常absenceを別値で返す | 空/読取失敗/競合をabsenceへ縮退する変異をKILL |
| B3 | 宣言済みoperationが対応するmessage IDとfailure状態を返す | 未宣言operation、ID対応不能、unknown成功化を拒否 |
| consumer | manifestと一致する固定argvだけを呼ぶ | 内部file直読、wrapper差替え、環境変数rootを拒否 |

provider対照はP2 pilotのlive結果で代用しない。

consumer対照はproviderの自己申告で代用しない。

## 6. Issue #236の状態

Issue #236はこの設計のmerge後もOPENかつ`blocked:dependency`を維持する。

NOT_PLANNEDにはしない。

ユーザーが要求したPM委譲強制の必要性は残り、P0/P1の隔離試験も否定されていない。

CLOSEDにできるのは、次をGitHub正本で確認した場合だけである。

1. G1〜G3のfork-local provider gateが同一fixed commitで受入済みである。
2. P2接続実装が固定HEAD reviewとrequired CIを通過している。
3. 専用pilotでfresh/resume、IDを結ぶ受信→委譲→結果記録、故障復旧、現PM無変更が受入済みである。
4. #236本文、labels、P2 evidence、後続P3/P4との境界が同じ手番で更新されている。

P3の現PM交代とP4の横展開は別Issue又は明示した後続作業として残す。

## 7. 対象外

- `agguild`又は`agguild_pool`の新設、物理移行、G0解除
- P2 broker/adapter/collectorの実装、pilot登録、実送受信、現PMの変更
- Issue #222又は#253の再オープン
- `fujibee/agmsg`へのPR、push、採用依頼
- 既存forkを無条件の恒久fallbackへ変更すること

README影響はない。

Refs #236 #253 #254 #246 #247 #373
