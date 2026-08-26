---
type: ArchitectureDecision
title: "R1: BATS共有fixtureの実行環境を一箇所で隔離する設計"
status: proposed
root_cause: R1
issue: "https://github.com/kappaseijin/agmsg/issues/176"
related_issues:
  - "https://github.com/kappaseijin/agmsg/issues/171"
  - "https://github.com/kappaseijin/agmsg/issues/174"
  - "https://github.com/kappaseijin/agmsg/issues/148"
  - "https://github.com/kappaseijin/agmsg/issues/151"
  - "https://github.com/kappaseijin/agmsg/issues/164"
  - "https://github.com/kappaseijin/agmsg/issues/88"
producer: agmsg_programmer_codex
reviewer: agmsg_reviewer_claude
base_commit: "e2622f7668cd25c9f133fdba994b1cf54b64e081"
timestamp: "2026-08-26T09:17:09+09:00"
---

# R1: BATS共有fixtureの実行環境を一箇所で隔離する設計

## 決定

R1の横断PRは、`tests/test_helper.bash:setup_test_env`を使うBATS試験の既定実行環境を、親プロセスから切り離して一箇所で確立する。

このPRの主張は一つだけである。

> `setup_test_env`を呼んだ試験は、親のagmsg設定・agent実行環境・herdr/tmux接続先・ホーム・一時ディレクトリ・localeを既定動作へ解決しない。

Issue #174の`AGMSG_AGENT_PID`未固定は、この共有fixture PRへ吸収する。
Issue #171で既に固定された`AGMSG_STORAGE_PATH`も、同じ契約の一部として回帰試験を拡張する。
installed production copy（`~/.agents/skills/agmsg`）はテスト対象にもfixtureの入力にも使わない。

## 現状と根拠

`setup_test_env`は独立した`TEST_SKILL_DIR`、SQLite DB、`HOME`を作るが、`AGMSG_AGENT_PID`、`SKILL_DIR`、`RUN_DIR`、`TMPDIR`、runtime検出変数、herdr/tmux変数、localeを統一していない。
実測では109個のBATSファイル中92個が`test_helper`をloadしており、複数のsuiteが同じ既定値を個別に再設定している。

| 観測 | value | cutoff | source | command |
| --- | --- | --- | --- | --- |
| 共有helperをloadするBATS | 92 / 109 files | loadだけでは`setup_test_env`を呼ばないcustom harnessを含む | `tests/` | `rg -l '^load test_helper' tests --glob '*.bats'` と `rg --files tests -g '*.bats'` |
| 親storageを遮断する回帰 | 2 tests pass | helper単体だけで全surfaceの隔離を証明しない | `tests/test_fixture_helpers.bats` | `bats tests/test_fixture_helpers.bats` |
| #174の根因 | unset時は親process treeをwalkし、空文字ならbare session ID | `AGMSG_AGENT_PID=''`以外はambient parentを読む | `scripts/lib/resolve-project.sh` | `sed -n '280,322p' scripts/lib/resolve-project.sh` |
| CLI自動判定のenv入力 | Claude, Codex, Gemini, Grokのmanifest `detect=` | process-tree判定は別の入力 | `scripts/drivers/types/*/type.conf` | `rg '^detect=' scripts/drivers/types/*/type.conf` |
| #148のUTF-8対照 | test内でUTF-8 localeを明示し不正バイトと通常CR除去を検査 | fixtureの既定localeに依存させない | `tests/test_storage.bats` | `bats tests/test_storage.bats` |

個別の重複は、`AGMSG_AGENT_PID=''`を7 suite、`SKILL_DIR`を23 suite、`RUN_DIR`を8 suiteで設定している。
`test_spawn.bats`と`test_despawn.bats`には、実herdr/tmux接続を避けるための個別`unset`も残る。
この分散は、新規試験が隔離を忘れる余地を残す。

## fixture契約

`setup_test_env`は次の順で環境を確立する。

1. `LC_ALL=C`と`LANG=C`を最初にexportする。fixture rootの作成は`$BATS_TEST_TMPDIR`配下の`mktemp` templateを使い、親`TMPDIR`へ最初の作成まで逃がさない。
2. fixture root内に`home/`、`tmp/`、`run/`、`db/`、`teams/`、`scripts/`を作り、`HOME`、`TMPDIR`、`SKILL_DIR`、`RUN_DIR`、`SCRIPTS`、`TYPES`、`DBPATH`をそのrootへ固定する。`CODEX_HOME`は親値をunsetし、Codexの既定解決もfixtureの`HOME`へ落とす。
3. 親から来た`AGMSG_*`を全てclearしてから、fixtureの既定として`AGMSG_STORAGE_PATH=<root>/db`、`AGMSG_STORAGE_DRIVER=sqlite`、`AGMSG_AGENT_PID=''`だけを明示する。試験固有の`AGMSG_*`は`setup_test_env`の後、又は`run env ...`で設定する。
4. `HERDR_*`、`TMUX`、`TMUX_*`をclearする。fixtureは実pane、実tmux server、又はhost workspaceを既定で選ばない。
5. copied manifestの`detect=`が列挙するruntime検出環境変数をclearする。現行値は`CLAUDE_CODE_SESSION_ID`、`CODEX_SANDBOX`、`CODEX_THREAD_ID`、`GEMINI_CLI`、`GEMINI_API_KEY`、`GROK_SESSION_ID`である。実装は固定リストを複製せず、fixture内の`type.conf`の`detect=`から変数名だけを読む。新しいagent typeを追加しても、親runtimeによる誤検出を再導入しない。
6. scriptsのコピーと`init-db.sh`は、この固定後に実行する。teardownは正確に`TEST_SKILL_DIR`だけを削除する。

| surface | 親からの漏洩 | R1後の既定 | 意図的な試験上書き |
| --- | --- | --- | --- |
| storage/config/plugin | `AGMSG_STORAGE_PATH`、`AGMSG_STORAGE_DRIVER`、`AGMSG_CONFIG`、`AGMSG_PLUGIN_DIRS` | fixture SQLite DBのみ | `run env AGMSG_*=`又はsetup後のexport |
| instance/role state | `AGMSG_AGENT_PID`と親process tree | empty pinによるbare session ID | instance-id試験がcommand-localにnumeric値を与える |
| fixture paths | `SKILL_DIR`、`RUN_DIR`、`HOME`、`TMPDIR`、`CODEX_HOME` | `<TEST_SKILL_DIR>`配下 | path解決を試す試験だけがlocal pathを設定する |
| terminal/pane | `HERDR_*`、`TMUX`、`TMUX_*` | 全てunset | spawn/despawnのfake herdr/tmux試験がsetup後に明示する |
| runtime detection | manifest由来の`detect=`環境変数 | 全てunset。process treeの試験対象とは混同しない | type detection試験が`run env`で明示する |
| locale | `LC_ALL`、`LANG` | `C` | #148はUTF-8 localeをその子processだけへ明示する |

`PATH`、`BATS_*`、`PWD`、CI credentialはR1でpinしない。
`PATH`をfixture用に狭めると、実機`sqlite3`、`node`、`git`と試験固有stubの探索契約を変えるためである。
外部CLIを完全にhermetic化する必要が生じたときは、別Issue・別PRでtoolchain seamを設計する。

## 実装計画

1. `tests/test_fixture_helpers.bats`へ、hostile parent environmentから`setup_test_env`を呼ぶ回帰試験を追加する。外部rootには壊れた`messages.db`、home config、run marker、temp markerを置き、親から全対象surfaceをpoisonする。
2. その子processで`setup_test_env`を実行し、実際のstorage driver/path、instance ID、lock/run path、spawn-optionsのhome path、`mktemp`出力、runtime type検出、herdr/tmuxの未設定を検証する。外部rootの内容が変化しないことも検査する。
3. `tests/test_helper.bash`へ一つのsanitization blockを実装する。上の順序を守り、manifest読取りはfixtureにcopy済みの`type.conf`だけを対象にする。
4. setup後に同じ値を再設定するだけの重複を機械的に除く。対象は`AGMSG_AGENT_PID=''`、`SKILL_DIR="$TEST_SKILL_DIR"`、`RUN_DIR="$SKILL_DIR/run"`、genericな`HERDR_*`/`TMUX*` unsetであり、fake runtimeやpath解決を試すlocal overrideは残す。
5. `setup_test_env`を使わない17ファイルと、`test_install.bats`のようなcustom install harnessは変更しない。各々は独自fixtureの境界を持つため、このPRに共有helperを強制しない。新しいcustom harnessを追加するPRは、同等のenvironment isolationをそのPRで示す。
6. #148のUTF-8試験を変更しない。global `LC_ALL=C`を導入しても、同試験は`run env LANG=<utf8> LC_ALL=<utf8>`でproductionの`LC_ALL=C` prefixを必要条件としている。production prefixを外すmutationがmacOSでKILLEDになることを再確認する。

## 受入れと対照

| 観点 | 正の検査 | 負の対照・KILLED条件 |
| --- | --- | --- |
| 外部storageを読まない | poisoned `AGMSG_STORAGE_PATH/messages.db`のままfixture DBを初期化できる | storage pin/scrubを外すと壊れた外部DBでinitが失敗する |
| 親agent PIDを読まない | `AGMSG_AGENT_PID=4242`を注入してもinstance IDはbare SID | empty pinを外すと`sid.4242`となる |
| 外部home/run/tmpを使わない | resolverと`mktemp`の出力が全てfixture root内、外部marker不変 | 各fixture path pinを外すと外部path assertionが失敗する |
| 実pane/runtimeを選ばない | hostile `HERDR_*`/`TMUX*`とdetect envがhelper後に不在、stubbed process-treeではdefault type | prefix/detect scrubを外すとhostile値又はCodex検出が現れる |
| locale回帰を隠さない | #148の子process UTF-8対照がpassし、通常CR除去もpass | `sqlite.sh`の`LC_ALL=C`を外すmutationはmacOSで`Illegal byte sequence`となりKILLED |

実装者はまず新しいfixture試験をREDで確認し、次にsanitizationを実装する。
focused suiteは少なくとも`test_fixture_helpers`、`test_storage`、`test_instance_id`、`test_spawn`、`test_despawn`、`test_watch`、`test_watch_install_changed`、`test_actas_integration`、`test_delivery`、`test_doctor`、`test_role_session`を実行する。
その後、重複sweepの残りを`rg`で再確認し、意図的なlocal overrideだけが残ることをPR本文へ記録する。

## R1横断audit

| Issue | 現行の根 | このPRでの扱い | 理由 |
| --- | --- | --- | --- |
| #171 | inherited storage path | 吸収済みの契約を拡張 | current helperはstorageだけ先行して固定済み |
| #174 | inherited agent PID / process tree | 吸収 | `AGMSG_AGENT_PID=''`を共有既定へ移す。単独PRを作らない |
| #148 | UTF-8 localeでproduction修正が必要な回帰 | locale対照として維持 | fixture既定と子processのUTF-8上書きを分け、mutation検出を守る |
| #151 | role-child countを時点値で読むwait | 除外 | current sourceはidentityと実時間deadlineを使う。environment pinでは状態観測を直せない |
| #164 | watcher/releaseの非同期状態をcountでassert | 除外 | observable conditionへの待機という別root。fixture環境を固定してもrelease競合を解消しない |
| #88 | engine/readiness lifecycleの待機・hang | 除外 | parent environmentではなくprocess lifecycleの観測契約。新規修正が必要ならbreakerが別rootとして着手可否を断定する |

`root:R1`ラベルは横断的な試験信頼性の追跡であり、全Issueを同一実装PRへ入れる根拠ではない。
待機回数・fixed sleep・deadlineの変更はこのPRに含めない。

## 今回扱わない範囲

- production scriptの環境変数仕様、storage driver、agent type検出、spawn経路の変更
- installed production copyの修復又はその直接実行
- `PATH`のhermetic化、CI matrix/shard、retry、job timeoutの変更
- #88、#151、#164のwait/count/sleep実装変更
- `setup_test_env`を使わないcustom harnessのfixture統合
- #174を独立PRとして処理すること

## handoff

producerは`agmsg_programmer_codex`、formal reviewerは`agmsg_reviewer_claude`とする。
PRはこの設計書、`tests/test_helper.bash`、fixture回帰試験、及び機械的に確認された重複削除だけを含める。
新しいHEADごとに、reviewerはhostile-environment試験、#148のlocale mutation evidence、sweepの例外一覧、全差分を一括確認する。
