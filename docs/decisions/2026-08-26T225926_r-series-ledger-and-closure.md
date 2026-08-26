---
type: Decision
title: R1〜R5 の決着と、着手しない項目の台帳
description: >-
  20 件の症状を 4 つの根へ還元した追跡（#176）と、R5 の epic（#178）を閉じるにあたり、
  修正した項目と「実害の観測が無いため着手しない」項目を区別して記録する。
timestamp: "2026-08-26T22:59:26+09:00"
---

# R1〜R5 の決着と、着手しない項目の台帳

## なぜこの記録を作るか

**epic を close すると、子の全部が「解決した」ように見える。**

**実際には 3 種類ある。**

| 区分 | 意味 |
| --- | --- |
| **修正** | 変更が入り、実測で効果を確認した |
| **決着（reopen 条件つき）** | **実害の観測が無いため着手しない。** 観測されたら開き直す |
| **未解決** | **実害があり、修復・検証が進行中。** epic を close しても解決扱いにしない |

**「決着」を「解決した」と書くと、次に同じ症状が出たときに
「一度直したはずだ」という誤った前提から調査が始まる。**

**「未解決」を「解決した」と書くのは、単に事実に反する。**
**期限に間に合わなかったものを、終わったことにしない。**

正本は GitHub（epic の close コメント）。この文書はその複製である。

## 経緯: 走査駆動をやめた

**当初、走査で 27 件の候補を得た。**

```
走査で見つけた候補   27 件
breaker の一次選別   12 件
**実害の帰属で再選別   1 件**
```

**きっかけは #198 だった。**

#198 は「実害の観測あり」として着手されたが、根拠にした失敗（#165）の真因は
**fd1 汚染（#167）と SQLite 失敗の分類（#166）**であり、
**#198 が変えようとしていた回数契約は、その失敗に関与していなかった。**

**その訂正を受けたあとも分類が見直されず、大きな投入が発生した。**

**同じ目で残り全部を見直した結果が、上記の 12 -> 1 である。**

### 得られた基準

**走査は現実より速く候補を生成する。**

**着手の条件を「実害の観測（CI 2 回以上、または本番 1 回）が
その項目に帰属すること」に固定する。**

**「整合性のため」「理論上あり得る」は実害ではない。**

## 修正した項目

| 根 | 内容 | 状態 |
| --- | --- | --- |
| R1 | 試験ハーネスが実環境から隔離されていない | 完了 |
| R2 | 失敗経路で理由が消える | **完了（6/6）** |
| R3 | 代理指標が unknown を具体状態へ丸める | 完了 |
| R4 | 配備と席のライフサイクル | 決定（配備はセッション境界のみ） |
| R5 | 待ちが観測条件 + 実時間 deadline に基づかない | 下記 |

**R2 の 6 件は「失敗が成功として扱われる経路」を潰したものである。**
うち R2-D は、**失敗が成功に化ける唯一の経路**だった。

## 着手しない項目（reopen 条件つき）

| 項目 | 実害の観測 | reopen 条件 |
| --- | --- | --- |
| R5-T2〜T5 | **なし**（整合性のみ） | focused suite が待ち起因で CI で落ちる |
| R5-P1 / P2 | **なし** | spawn / despawn の timeout が実時間と食い違う（例: `--timeout 10` で 10 秒を大きく超えて返る） |
| R5-P3（#198） | **なし**（誤帰属） | gate timeout が CI 2 回以上 or 本番 1 回、かつ診断が attempts 枯渇 + holder 生存を示す |
| R5-P4〜P7 | **なし** | **各 path について、その path の timeout / hang を 1 回観測する**（他 path の観測を根拠にしない） |
| R5-C1 / C2 | **なし**（protocol / availability の判断が要る） | 症状が観測される |

**いずれも「直っていない」のではなく「直す理由がまだ無い」である。**

## 帰属が未測定のまま実装した項目

**#189（R5-P9）は、機序の帰属が未測定のまま実装を許可した（2026-08-26 時点で進行中）。**

理由:

```
(1) 変更が「値を当てる」ものではなく**契約の強化**である
    （holder token が進む間は切らず、no-progress だけを deadline で fail-closed）
    -> 機序が何であれ方向が正しく、帰属が判明しても作り直しにならない

(2) 実害の観測が 1/80 であり、**診断を待っても packet が出る見込みが無い**
```

**#181（R5-P8）とはここが違う。** #181 は再試行の回数という**値**の変更なので、
**診断が「2 回目の INSERT が busy で落ちた」ことを示すまで修正へ進まない。**

**この違いを残しておく。** どちらも「未測定」だが、**未測定であることの重さが違う。**

## #181: 診断が見立てを否定した

**2026-08-26、診断を入れた試験を stub 無しでローカル反復し、実 packet を収穫した。**

```
valid attempt 51（最初の 50 は pass）
expected_event_count=10、**actual_event_count=9**
**child[9]** wait_exit=1、stdout 空
**stderr = team-work dispatch migration requires both legacy tables**
```

**source を辿ると `agmsg_storage_ensure_initialized` -> `migrate-team-work-dispatch.sh` で、
同 script が legacy table 片方だけの状態でこの stderr と exit 1 を出す。**

### 証跡（value / cutoff / source / command）

```
value    valid attempt 51、actual_event_count=9 / expected 10、child[9] wait_exit=1
cutoff   **valid attempt 51 で停止**（fan-out 10 の最大 60 のうち、最初の実 packet を得た時点）
         valid attempts=51、actual packet=1、第 2 段 40-way は条件未成立
source   **PR #201 の旧 HEAD `ea72704a6f4736932ccde72d077aa7230cbe03dc`**
         raw: /tmp/agmsg-i181.O2nu0U/attempts-fanout10-valid/attempt-51/{stdout,stderr}.txt
command  rtk bats --print-output-on-failure --filter 'FRESH' tests/test_storage.bats
         **stub / shim / wrapper / force は無し**
```

**live の PR #201 HEAD は `2d3bb346c11b2271d64315604a11998be977982c` である。**

**この packet は旧 HEAD の証跡であり、新 HEAD の根拠に混ぜない。**

### 現 HEAD で独立に再現した（別証跡）

**verifier が live HEAD `2d3bb346c11b2271d64315604a11998be977982c` で再測定した。**

```
value    **Stage1**  fan-out 10、**60/60 すべて pass**（actual packet 0）
         **Stage2**  同じ実経路で fan-out 40、**attempt 6** で actual packet
                     expected=40、**actual=39**、db_exists=1、events_table=1
                     **child[6] のみ** wait_exit=1、stdout 空
                     **stderr = team-work dispatch migration requires both legacy tables**
                     他 39 child は success

cutoff   Stage1 が 0 packet のときだけ Stage2 を最大 30 回
         **Stage2 の最初の actual packet で停止（6/30）**

source   raw: /tmp/agmsg-i181-current.epK83o/attempts-fanout40/attempt-06/{stdout,stderr}.txt
         全 attempt: .../attempts-fanout10/ と .../attempts-fanout40/

command  AGMSG_TEST_P8_FANOUT_CHILDREN=10|40 rtk bats --print-output-on-failure \
           --filter 'FRESH' tests/test_storage.bats
         **child 経路に stub / shim / wrapper / forced failure は無し**

classification
         **2 回目 INSERT の SQLite stderr ではない。**
         **migrate-team-work-dispatch.sh:15 の partial legacy table guard。**
         storage.sh:299-305 の `agmsg_storage_ensure_initialized` から呼ばれる
         initialization / migration 失敗
```

**旧 HEAD（10-way で 51 回目）と現 HEAD（40-way で 6 回目）は別の source である。**

**それぞれ独立に測って、同じ機序へ到達した。**

**Stage1 が現 HEAD で 60/60 通ったことを「直った」と読まないこと。**
**発生率が低いだけで、fan-out を増やせば 6 回目で出る。**

### 強制 control は実行していない

**verifier は forced exit73 control を実行しなかった。**

理由: **実 packet を既に収穫していたため、actual evidence と混同しない。**

**強制した失敗の packet は「診断が動くこと」しか示さない。**
**両方あると、報告を読む側がどちらを根拠にしているか分からなくなる。**

### 見立ては外れていた

| | 見立て | 実測 |
| --- | --- | --- |
| 原因 | **2 回目 INSERT の SQLite busy** | **init / migration の失敗** |
| 経路 | `storage_send` の retry | `ensure_initialized` の migration |

**したがって「再試行 1 回 -> N 回」の変更は行わない。**

**ただし migration は別分類にしない。#181 の範囲内で、原子化により修復する。**

**breaker が着手時に定めた条件が、そのまま発火した。**

> 診断が別の経路（init の失敗、send.sh の前段）を示したら、
> **再試行契約の変更は台帳へ戻す。**

### これが #198 との違いである

```
**#198**  「実害あり」という**誤帰属のまま着手**し、大きな投入が発生した
**#181**  **診断を先に置いたので、修正へ進む前に止まれた**
```

**同じ構造の課題で、順序を変えただけで結果が変わった。**

**「失敗を分類する設計を入れる前に、失敗の理由が残る形にする」**
（`fail-closed-clean-break`）が、実際に効いた事例である。

### 検査が見ていなかった分を、検査のせいだと書く

**verifier の報告には次の一行がある。**

> attempt 42 は同じ Bats 失敗だが、**runner が captured output を出さず packet 未露出**

**「51 回目で初めて起きた」ではない。「42 回目にも起きたが、見えなかった」である。**

**この区別が無ければ、発生率を 1/51 と誤って見積もっていた。**

## 未解決（この epic を close しても、終わっていない）

**epic を close する時点で、次の 2 件は解決していない。**

| Issue | 状態 | 次の担当 | close できない理由 |
| --- | --- | --- | --- |
| **#181** | **未解決** | Lane A（programmer）、reviewer = reviewer_claude | 原因は確定したが、**原子化の修復が未実装** |
| **#189** | **未解決** | Lane B（programmer2）、reviewer = reviewer_claude | **実装中** |

**#181 の修復内容:**

```
init-db.sh:318-319 の schema / triggers 適用を
**単一トランザクション**（BEGIN IMMEDIATE … COMMIT）に包み、**中間状態を消す**。

**migration guard は変えない**（`1|0` で止まるのは正しい適用）。
**guard に fallback を足さない。lock も足さない**（原子性で足りる）。
```

**この 2 件は epic の close によって「解決した」ことにならない。**

**ユーザーへの報告も、修正・決着・未解決の 3 区分で書く。**

## 検査そのものについての記録

**#169 の close 判定で、`resource busy` によるログ grep を使おうとして失敗した。**

```
修正後の run   0 件
**修正前の run   0 件**  <- 正の対照が取れなかった
```

**この grep は当該症状を検出できていない。** 0 件を根拠にできない。

**判定は job の成否のみで行った**（#196 以降 9/9 green、以前の失敗率 約 30%）。

**0 件を結論にする前に、在ると分かっているもので検査が反応するかを確かめる。**
本件はその手順で自分の検査を捨てられた。
