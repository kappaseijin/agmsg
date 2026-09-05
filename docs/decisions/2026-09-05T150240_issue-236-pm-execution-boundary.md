---
type: Design
title: "Issue #236: PM委譲義務の実行境界の成立性"
status: accepted-direction
timestamp: "2026-09-05T15:02:40+09:00"
issue: "https://github.com/kappaseijin/agmsg/issues/236"
source_head: "581970ad61e6d5ea1b4b52b31fa6e740ab427787"
---

# PM委譲義務の実行境界

## 結論と今回の範囲

command型PreToolUseを追加するだけでは、障害時を含む委譲の強制は成立しない。
正常に動けば拒否できるが、起動不能やtimeoutでは通常のpermission判定へ戻るためである。
ユーザー決定により、PMから汎用実行ツールを外し、連絡、記録、体制操作を引数制限付きの専用窓口へ限定するPM broker方式を採用する。
これは設計上の条件付き成立であり、現環境での拒否成功や導入可能性を実測済みとはしない。

本書の一主張は「PMの直接実作業を防ぐには、注意文の注入ではなく実行能力の境界が必要」である。
起草はagmsg_architect_codex、実装は別依頼後のagmsg_programmer_codex、formal reviewはagmsg_reviewer_claudeとする。
正式レビューは設計PRの固定HEAD全差分を対象にする。
今回は調査と設計文書だけを扱い、既存hook、起動設定、製品コードを変更しない。
#230、PR #233、remind-clickable-options.sh等の既存リマインダー修正は対象外とする。
内部運用の検討書なので今回のREADME変更はない。

## 根拠と確認範囲

確認日: 2026-09-05。ローカル確認の基準時刻は2026-09-05T15:02:40+09:00。

| 主張 | value / cutoff | source / command | 限界 |
| --- | --- | --- | --- |
| 注意文は実行拒否ではない | breakerは入力hook成功後のBash結果3件を確認。実行前拒否なら対象操作は走らない | [breaker断定](https://github.com/kappaseijin/agmsg/issues/236#issuecomment-5549817379)、`rtk gh issue view 236 --repo kappaseijin/agmsg --json title,state,comments` | 原セッションの実測はbreakerによる。architectの再実験ではない |
| cwdではPMを一意に決められない | 同じprojectにPMとownerが登録されている | 正式`team.sh agmsg`、breakerの`whoami.sh`結果 | rosterの存在は現在の実行主体の証明ではない |
| 既存再開記録は認可には不足 | 保存失敗でも成功扱い、SID逆引きは最初の一致で終了 | 固定HEADの`actas-claim.sh`、`role-session.sh`を読取。対象ファイルの`rtk git diff -- ...`は空 | 保存用契約を壊さず、認可用の厳格な照合を別に設ける必要がある |
| 能力を絞るCLI入口が存在する | ローカルCLIは2.1.261、helpに`--restricted`、`--tools`、`--strict-mcp-config`がある | `claude --version`、`claude --help` | 存在確認だけ。対象PMのCLI 2.1.260での動作試験とは別 |

breakerの限定証拠は`/private/tmp/agmsg-issue236-breaker.ip5pbb9j/evidence.json`にある。
原記録は同packetの`session_source`で、関係行は6109–6113、6115、6400、7338–7339、7348/7350、7360/7362、7369/7371である。
packetの限定項目を読み、実測の帰属を保持した。
一時ファイルの残存を永続的な受入証拠にせず、後工程の試験packetは秘密値を除いた専用artifactに保存する。

履歴検索は`ctx search 'Issue 236 PM PreToolUse' --workspace /Users/kappa/Dropbox/data/dev/agmsg --refresh off`で応答が得られず中断した。
履歴検索の空出力を不存在証拠には使わない。
コード探索はcodebase-memoryから開始したが、`actas_lock_state`の索引行と現ファイルにずれがあったため、該当ファイルを直接読み直した。

## hook方式の限界と候補比較

公開仕様では、PreToolUseは有効なdeny判断またはexit 2で実行前に拒否できる。
一方、command hookの起動不能とtimeoutは拒否にならない。
SDK callbackのPreToolUse timeoutは別契約で、tool callをblockする。
この差をcommand hookにも適用できるとは解釈しない。
PostToolUseは実行後なので予防にはならない。[公式hook仕様](https://code.claude.com/docs/en/hooks#timeouts)

| 方式 | 防げる範囲 | 残る問題 | 判定 |
| --- | --- | --- | --- |
| 注意文 + command型PreToolUse | 正常時に判定できた対象操作 | hook未起動、timeout、判定対象外ツール、記述を変えたcommand | 強制境界として不採用 |
| SDK callbackで全toolを仲介 | callback到達時の拒否。timeoutの非阻止問題を改善できる | ハーネス移行、例外時と未登録時の確認、操作の意味分類、迂回toolの制限が別途必要 | 比較候補。今回の最小導入には選ばない |
| PMの能力制限 + 専用操作窓口 | 提供していない実行能力を直接使えない。窓口の異常で汎用shellへ戻さない | 窓口と起動契約の新設、既存PM操作の移設、実機受入が必要 | ユーザー採用決定。実装と受入は未実施 |

`git`や`gh`の文字列禁止だけでは、別インタプリタ、別tool、ラッパー、GUI経由で同じ実作業を行える。
逆に全Bashを拒否するだけでは、現在Bash経由のagmsg連絡まで止まる。
したがって禁止語を増やす方式でなく、PMに残す操作を列挙する。
自然言語の目的を完全に分類する保証は置かない。
PMが文脈を読んで会話することと、成果物の調査を自分で実行することは、専用窓口の提供範囲で分ける。

## セッション識別の契約

既存の[actas-claim.sh](https://github.com/kappaseijin/agmsg/blob/581970ad61e6d5ea1b4b52b31fa6e740ab427787/scripts/actas-claim.sh)は明示agent名を受け、claim後に再開用recordをbest-effortで保存する。
[role-session.sh](https://github.com/kappaseijin/agmsg/blob/581970ad61e6d5ea1b4b52b31fa6e740ab427787/scripts/lib/role-session.sh)の`agmsg_role_session_lookup_by_sid`は一意性を確認しない。
[session-start.sh](https://github.com/kappaseijin/agmsg/blob/581970ad61e6d5ea1b4b52b31fa6e740ab427787/scripts/session-start.sh)はsession IDとCLI PIDを組み合わせて並行resumeを分離するが、PID未解決時はbare SIDへ縮退する。
これらは配送と再開の材料として使い、単独で「認可済みPM」と判定しない。

新しいPM起動契約では、信頼するlauncherが明示的な`team / agent / seat type / policy version`を固定し、起動したprocess generationへ結び付ける。
session IDは再開の識別子、process generationは今回の実行個体の識別子として別々に保持する。
親processまたは専用channelに保持したbindingを正本とし、tool引数のagent名、cwd、プロンプトのactas宣言、書換可能な環境変数だけから権限を取得させない。

1. 起動前に正式rosterと明示agentを照合する。欠落、複数一致、未対応seat typeは起動拒否とする。
2. 起動個体とclaimのownerを照合する。bare SIDだけ、古いPID、読取失敗、複数recordを許可根拠にしない。
3. 認可用bindingを作れなければ汎用toolを持つPMを開始しない。初期claimはlauncher側が行い、PMのBash許可をbootstrap例外にしない。
4. resume時にも今回のgenerationを再検証する。PMからownerへのactasによる権限昇格は許さず、別席起動として扱う。
5. 同じcwdのownerやprogrammerの起動にはPM制限を誤適用しない。cwdはproject整合性の検査にだけ使う。

これは信頼したlauncherと実行ハーネスの内側で、モデルの誤操作を防ぐ契約である。
同じOSユーザーの別processによる認可file改ざん、管理者による起動引数変更、ハーネス自体の欠陥まで防ぐとは主張しない。
そこまで含めるなら別OS権限等の隔離が必要で、別途採否を決める。

## PMに残す操作と実行境界

CLIの`--tools`はbuilt-in toolの集合を制限するが、MCPには別の制限が必要である。
`--strict-mcp-config`は指定MCP構成だけを使う入口になる。
`--restricted`は補助として利用候補になるが、それだけで全調査能力を除けるとはしない。[公式CLI仕様](https://code.claude.com/docs/en/cli-reference)
`--allowedTools`を許可集合の強制と誤認せず、denyと利用可能toolの制限を区別する。[公式permissions仕様](https://code.claude.com/docs/en/permissions)

専用窓口を**PM broker**と呼ぶ。
PM brokerは任意command、任意URL、任意path、任意コードを実行するAPIを持たず、次の構造化操作だけを扱う。

| PMの責務 | 残す操作 | 禁止する拡張 |
| --- | --- | --- |
| 会話、受信、委譲 | team内のinbox/history/send、他PMとの連絡。送信元はbindingから固定 | 任意sender詐称、他席の履歴全量読取、shell式を引数として評価 |
| 提示と記録 | 指定PLAN/NOTESへの追記、指定Issue/PRの連絡用取得とコメント、承認済み判断の状態反映 | 任意file編集、任意GitHub API、PR一括棚卸し、設計や実装の生成処理 |
| 体制操作 | 登録済み席の状態取得、固定profileによる起動。停止は既存の所有権条件を維持 | 任意argv、別profileへの差替え、汎用pane command、端末への任意文字投入 |

元の`fetch`、remote branch棚卸し、PR一覧集計はbroker操作に含めず、workerへ依頼する。
特定PRの状態確認と全PRを調べる実作業を同じAPIへまとめない。
起動、記録の各操作は固定実行ファイルと構造化引数を使い、message本文をshellへ渡して評価しない。
記録先はcanonical pathと固定許可先を照合し、symlinkによる設定fileへの逸脱を拒否する。

PMの汎用Bash、PowerShell、コード実行、任意file read/write、調査用Web/GUI/MCP、任意構成の子agent起動を提供しない。
委譲は登録済み実務席へ行い、PM自身が別権限の子processを作る迂回路を残さない。
必要な規則と依頼文脈は起動時の固定資料とbrokerの限定取得で供給する。
新しいtool、plugin、skillのcommand実行も能力追加として検査対象にし、未確認ならPM起動を拒否する。

```mermaid
flowchart TD
  L[信頼するlauncher] --> I{rosterと実行個体のbinding成立}
  I -->|不成立| X[PM起動拒否]
  I -->|成立| P[汎用実行toolを持たないPM]
  P --> B{PM brokerの操作認可}
  B -->|許可| C[連絡 / 記録 / 固定profileの席操作]
  B -->|範囲外または異常| D[拒否と診断]
  C --> W[実務は登録済み担当席へ委譲]
```

## 故障と移行の扱い

hookは拒否理由の説明補助に留め、hookを削除した状態でも汎用toolの不在を維持する。
brokerのidentity解決失敗、設定破損、未対応操作、起動失敗は操作を実行せずエラーにする。
broker timeoutで既に実行中の許可操作が自動的に取消されたとは扱わない。
request IDと`not_started / completed / outcome_unknown`を分け、結果不明の書込を自動再送しない。
診断にはpolicy version、generation、tool/operation、判定理由、request IDだけを残し、受信本文や秘密値を複製しない。

移行は隔離fixtureの受入後にPM席だけを制限付きlauncherで再起動する。
旧PMの汎用実行経路を同時稼働させたまま完了にしない。
本番の他席や全チーム共通hookを試験台にしない。
brokerが使えなければPMはユーザーへ状況を返し、汎用Bashへfallbackしない。

## 後工程の受入条件

programmerが隔離fixtureを実装し、独立実測は既存verifier等の別task context、formal reviewはClaude reviewerが担当する。
今回architectは以下を実行していない。

| 条件 | 必要な対照と期待値 |
| --- | --- |
| 元の3操作 | PMで実行marker 0、委譲先で同一fixtureのmarker 1。拒否文だけを証拠にしない |
| 偽陰性対照 | 実行可能な無害stubが単独でmarker 1になることを先に確認。実行物不存在を拒否成功と数えない |
| 同cwd別席 | PM拒否とowner/programmer許可。既存roleの差を保持 |
| identity異常 | 未claim、record欠落、複数record、stale generation、並行resume、偽agent引数で拒否 |
| 別表記と別tool | 絶対path、rtk、shell包囲、インタプリタ、GUI、MCP、子agentの迂回がPMから利用不能 |
| hook故障 | hook削除、非実行可能、exit 1、timeoutでも元3操作のmarker 0 |
| 構成変動 | plugin追加、MCP追加、resume、設定破損、未知toolで能力が増えない。読込結果不明なら起動拒否 |
| PMの正常操作 | agmsg連絡、依頼の記録、指定PR確認、固定profileの席操作が通る。本文のshell文字はデータのまま |
| broker異常 | identity/config障害では副作用0、実行後timeoutではoutcome_unknownを返し重複実行しない |

packetには`value / cutoff / source / command`、CLI版、起動profile digest、固定HEAD、実効tool一覧、markerの生出力を残す。
CLI helpの存在と実効tool一覧、hook登録と発火、発火と拒否、通知と実行開始を別々に判定する。
全条件を通すまで「強制済み」「fail-closed導入済み」と報告しない。

## 次善策の位置付けと採用決定

比較した次善策は事後監査であり、今回の採用方式ではない。
対象PMのtool eventを独立監視し、直接実作業を検出したらユーザーへ通知し、以後の作業を担当席へ戻す。
監視欠落はhealthyにせずunknownとして報告する。
ただし、これは実行済みの操作を取消さず、監視と停止の間の次操作も保証しないため、#236の「実行前に防止」の完了条件を満たさない。
この案は採用したPM broker方式からの自動fallbackではない。

ユーザーはPM専用の能力制限とbroker導入を採用し、設計PRの作成を依頼した。
方式の採用はformal review完了や実装受入を意味しない。
最初の実装依頼は起動と元3操作の隔離対照を含む検証可能な一単位に限定し、本番導入を別ゲートにする。
今回の成果物は設計PRのみで、Issueをcloseしない。
実装開始と設定変更は別の進行依頼に従う。
