---
type: Design
title: Issue 253 最小公開受信引渡し・ACK回復契約
timestamp: 2026-09-06T09:50:00+09:00
status: proposed
official_cutoff: e58dbafad5a84be625f070385bb0c076c3daa4db
---

# 一件の主張

公開message IDを軸とする受信所有・引渡し確認・回復の責任境界を定める。
既存公式公開操作が満たす部分は再利用し、確認できない保証だけを提供側への要求案とする。
起点は[MKBRK5](https://github.com/kappaseijin/agmsg/issues/253#issuecomment-5555882746)。
producer=agmsg_architect_codex、formal reviewer=agmsg_reviewer_claude、独立実測=agmsg_verifier_codex。
rosterのcodex/claude-codeを確認し、固定HEAD全差分をreview対象とする。
提供側実装・上流投稿・実P2接続・pilot・B3非依存認定・P2(b)解除は行わない。
送信ID拡張、同名claim.sh、B3全機構の移植、恒久fork fallbackを必須にしない。

# 固定sourceと確認範囲

公式O commitは上記frontmatter、treeは`d5f966fae0289a30428e846af78098faca00eec6`。
既存ローカルGit objectを`rtk git cat-file blob <commit>:<path>`で読んだ。上流通信・公式helperの再実行はしていない。
確認日時: 2026-09-06T09:50:00+09:00。
対象は以下の公開入口と、その受信消費経路。固定版の全機能に能力がないという証明ではない。

| 公開入口 | 満たす範囲・source | 未成立の保証と対照 |
| --- | --- | --- |
| api.sh get teams TEAM messages | JSONLのopaque string IDと履歴pagination。get以外は拒否するroute | owner/ACK操作なし。閉鎖履歴相関とlive完全性は別。#257履歴打切り・並行同文対照を適用 |
| inbox.sh | 保存未読を表示し、表示IDだけをmark-read。mark失敗はstderr、exit0を維持 | 表示前のmessage単位所有とowner付きACK結果がない。並行receiver・mark失敗対照 |
| watch.sh | ACTIVE_NAME時はactas役割lockをclaimし、各pollでも他ownerを確認。stdout成功後にcursor consume | 役割lockはmessage別receiptではない。consume失敗は抑制。表示とconsume間の中断・並行起動・同SID別PID対照 |
| actas-claim.sh | project/type/name/sessionからteam/nameの役割lockを取得。heldは拒否、sessionをinstanceへ正規化 | 代替排他候補として残す。公開message ID単位の所有/ACK/release/expiryではない。active-name経路の開始・拒否理由を追加対照する |
| Codex watch-once.sh | 未読集合を検出するpending oracle。markしない。max_idは集合digest | digestはmessage IDでもACKでもない。これ単独ではhandoff保証なし |
| codex-bridge.js readInboxForPrompt | eligible-pairsで所属を再確認しinbox.shを呼ぶ | inboxのstdout/exitに依存する。owner付きmessage receiptを新設する代替公開操作とは確認できない |

api.shのstore情報から内部SQLをconsumerへ持ち込まない。
役割lockを使う経路は有望な排他候補だが、message引渡し状態の公開照合まで満たすと判断しない。
既存O経路だけで必須保証すべてを満たす組合せは、今回の読取範囲では確認できていない。
「claim.sh未対応」を「公式全操作で不可能」に読み替えない。

# 観測の扱い

[PR #269](https://github.com/kappaseijin/agmsg/pull/269)のproducer固定HEADは`326c44ee95500f1299c726b75d0d31b47b50b5ac`。
O watch並行・同SID別PIDの`[1,1]`、F watch並行`[1,0]`はproducer/保存packetに基づく報告であり、本書の独立再実測ではない。
F同SIDは片側ready未観測なのでunknown。F/O aggregate incompatibleをF watcher/claim全体の欠陥としない。
数値claim ID `1`のhandedOffとopaque history IDのnotFoundも別ID空間の観測である。
notFoundは未配送証明ではない。閉鎖履歴でのID相関PASSだけでsend-ID拡張を要求しない。

# 必要最小の公開契約案

以下は操作の意味を定める案で、CLI名・内部storage schema・実装方式の指定ではない。
既存公開操作による充足を対照で確認できた項目には、新しい同義操作を追加しない。

## IDと所有

公開のキーは`team / recipient / message_id`。message_idは履歴と同じopaque値として比較し、数値化・本文一致・最新行だけの検索をしない。
内部IDを使う提供側は公開IDへの一意対応を返す責任を持つ。曖昧/不明/別namespaceならunknownで止める。
ownerはprovider発行の不透明なreceiver-instance識別子とし、SIDだけ・PIDだけを所有証明にしない。
再開・同SID別実PIDは別instance。所有の再取得には新しいgeneration/fencing tokenを発行する。
これは協調プロトコルの所有権であり、同一OSユーザーに対する認証境界の代用ではない。

## 状態と公開結果

| 論理操作 | 成功結果と責任 | 拒否・不明時 |
| --- | --- | --- |
| acquire | 未ACK対象の所有を原子的に取得し、公開ID/owner/token/期限を返す | busy/rejected/unknownでは引渡し用payloadを出さず、consume/ACKもしない |
| handoff | 有効tokenを持つreceiverがhostへID付きpayloadを渡す | stdout書込だけはhandoff-attempt。host確認なしではacceptedとしない |
| acknowledge | hostの受領確認後、同じ公開ID/owner/tokenのACKを永続化し、確認可能なreceiptを返す | 拒否は理由付き。書込後応答喪失等はunknown。rc0だけでACK成功にしない |
| status | 公開IDとattempt tokenに対応するowner・状態・ACK結果を返す | notFound/照合不能はunknown。未配送や安全な再送とは解釈しない |
| release / expire | 未ACK所有を条件付きで解放。再取得は新token | 別owner/古いtokenのACK・releaseは拒否し現所有を変えない |

ACKはhostへの引渡し確認であり、業務完了ではない。業務返信は別requestId/受信message IDに関連付ける。
同じtokenのACK再試行は同じreceiptへ収束させる。ACK済みを再配布せず、statusで結果を回復できることを要する。
mark-readをACK成功の必須部分にする場合はACK結果と整合して永続化し、部分失敗を成功にしない。
既読projectionを別更新にする場合はACK receiptを配送抑止の正本とし、projection失敗を別状態で公開する。
実装方式は未決定だが「ACK成功なのに未読projectionだけで再配送」は許さない。

## 中断・期限・回復

取得前の中断は引渡しなし。取得後・引渡し前はreleaseまたは期限切れ後に再取得可能。
host受領後・ACK応答前はunknownとなり得る。まず公開statusで同attemptを照合し、無条件再送しない。
ACK未成立の再配送は同じmessage IDと新attemptを付け、host側の重複検出に必要な情報を保つ。
期限はproviderの時計で判定し、consumer時刻やPIDの生死だけで旧ownerを復活させない。
期限切れ後の旧ownerは新しいACK/release権限を持たない。
ただしleaseだけでは停止していた旧receiverが再開してpayloadを出す事象を防げない。
hostがtokenの有効性を受入境界で確認できない経路では、排他的な受理はunknownであり、exactly-onceを主張しない。
その場合は再配布を含むat-least-onceの限界としてbreakerへ返し、#37保証を黙って緩めない。

# 最小判別対照

偽陰性は「receiver片方が未起動なのに、一件だけの出力を排他成功とする」場合である。
両者の開始・購読到達または明示的な所有拒否を観測してから判定する。

| 対照 | 必須観測・期待値 |
| --- | --- |
| O actas+active-name watchの代替経路 | 両instance起動、所有獲得/held理由、同SID別PID。役割排他が通ってもmessage ACK照合を別判定 |
| 同じ公開IDの並行acquire | 一方だけ有効token。敗者はpayload/consume/ACKなし。ready欠落はunknown |
| opaque history IDと数値内部ID | 公開IDから同一receiptへ一意照合。別namespace/notFoundを未配送へ変換しない |
| host受領/ACK保存/応答の各中断 | attemptと永続ACKをstatusで回復。ACK失敗rc0、応答喪失、再配送を区別 |
| release/expiry/別owner/遅延旧owner | 新token発行、旧token拒否、host受理境界でfencing。確認不可なら保証unknown |

既存#257の同文並行送信・scope・改行tab・履歴打切り対照は保持する。
新しい私設SQL reader、shadow ACK、失敗を隠すfallbackは対照にも実装にも足さない。
独立verifierが固定F/O、両receiverの開始境界、故障注入到達、公開ID対応を再実測し、value/cutoff/source/commandと生出力を残す。
formal reviewerは固定HEAD全差分を一括確認する。本書の受入だけで提供側拡張へ着手しない。

# 返却と未確定

不足候補はmessage単位の所有、公開IDに対応する機械可読ACK/status、回復のtoken境界である。
役割lock経路の限定補完対照で満たす部分を確定し、不足分だけをbreakerへ返す。
一般的排他、host側fencing可否、live履歴完全性は未確認。#269 mergeは本設計開始条件ではないが独立受入を省略しない。
README影響は今回なし。提供側契約が採用され実装する別PRでは公開操作・出力・回復手順をREADMEへ自己完結で記載する。
