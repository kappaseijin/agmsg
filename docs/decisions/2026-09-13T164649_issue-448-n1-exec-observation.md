---
type: Design
title: "Issue #448: N1 の process-command 検証を exec 完了の観測にする"
description: >-
  N1 が launcher PID のコマンドラインを 1 回だけ採取し、exec 前の launcher 自身を「args 不一致の fail」と
  判定していた欠陥を直す。採取を binding 観測後の別枠の上限時間までの観測に変え、観測を 4 種に分類して
  pass / fail / unknown を決める。exec 未到達と「遅れて exec した」を区別する証跡を定める。実装は別 PR。
timestamp: "2026-09-13T16:46:49+09:00"
refs:
  - "#448"
  - "#434"
  - "#407"
  - "#395"
  - "#444"
---

# Issue #448: N1 の process-command 検証を exec 完了の観測にする

## 1. 決定

| 論点 | 決定 |
| --- | --- |
| 上限時間 | **別枠とする。** `N1_EXEC_TIMEOUT_SECONDS=10` を新設し、binding を観測した時点から数える（§4） |
| 観測 | 0.2 秒ごとに launcher PID のコマンドラインを採り、`launcher`、`claude`、`unrecognized`、`absent` の 4 種に分類する（§3） |
| 判定 | `claude` かつ args 一致で pass。`claude` かつ args 不一致だけが fail。それ以外はすべて unknown（§5） |
| 証跡 | `N1/<mode>/exec-observation.json` に経過秒と分類の遷移を残す。未到達なら停止直前の `ps -o stat=`、コマンドライン、launcher 出力の末尾を残す。さらに、判定を変えない 5 秒の遅延観測で「遅れて exec したか」を記録する（§6） |

README への影響: 無（gate harness は開発・受入用の内部ツール）。

## 2. 何が起きているか

### 2.1 現状（main `0c6faff`）

`launch_n1_case` は、binding を観測して `validate-binding` が通った後、launcher PID が生きていればコマンドラインを **1 回だけ** 採る（`scripts/pilot-gate-runner.sh:1320-1323`、`record_process_command` は `ps -ww -p <pid> -o command=`、同 `:1035-1047`）。
続く `validate-process-command` は、argv に期待どおりの `--session-id`（または `--resume`）と `--settings` が無ければ exit 1 を返す（`scripts/lib/pilot-gate-isolation.py:3093-3200`）。runner は exit 1 を fail にする（`pilot-gate-runner.sh:1343-1351`）。

**exec 前の launcher のコマンドラインは、この検査で必ず exit 1 になる。** launcher は `bash .../pilot-launcher.sh --team ... --fresh` であり、`--session-id` を持たないからである。

### 2.2 binding から exec までの間隔

runner のコメントは「binding は exec の直前に書かれる」としている（`pilot-gate-runner.sh:1306-1308`）。実際には、binding を公開してから exec するまでに次の処理がある（`scripts/pilot-launcher.sh:715-826`）。

```text
binding 公開
  actas_lock_state（claim の再確認）
  roster_preflight（node を起動）
  current_digest × 3（profile・guard・broker。それぞれ node を起動）
  guard・broker の実行属性の確認
  decisions・executions の log を排他作成
  AGMSG_PM_* の export、cd
exec "$CLAUDE_BIN" ...
```

node の起動を少なくとも 4 回含むので、binding から exec までは 1 秒に届きうる。runner は binding を 1 秒間隔で探し（`pilot-gate-runner.sh:951-975`）、その後に python の `validate-binding` を 1 回走らせてから `ps` を採る。
**`ps` を採る時点が exec の前か後かは、この 2 つの時間の兼ね合いで決まる。**

### 2.3 run3 と smoke の差は未説明のまま扱う

#407 run3（`da7e002`）では claude 本体が記録され、#434 の native smoke（`0c6faff`、2 回）では launcher 自身が記録された（#448 本文）。
§2.2 の競合で説明はつくが、**実測で確かめてはいない。** 本書は競合を前提に判定を直すのではなく、競合なのか launcher 側の別の欠陥なのかを証跡で切り分けられるようにする（§6）。

## 3. 観測の分類

1 回の観測で、launcher PID について次を採る。

1. `ps -ww -p <pid> -o command=`
2. 採れなければ、PID の生存を確かめる

コマンドラインを `shlex.split` し、先頭 2 トークンを見て分類する。

| 分類 | 条件 |
| --- | --- |
| `absent` | `ps` が失敗し、PID が生きていない |
| `launcher` | 先頭 2 トークンのどれかの canonical path が `<GATE_REPO>/scripts/pilot-launcher.sh` の canonical path と一致する |
| `claude` | `launcher` に当たらず、argv[0] が `CLAUDE_BIN` と文字列で一致するか、argv[0] の canonical path が `CLAUDE_BIN_CANONICAL` と一致する |
| `unrecognized` | 上のどれにも当たらない。`ps` は成功したが空、`shlex` で分解できない、または PID が生きているのに `ps` が失敗した場合も含む |

- runner は `CLAUDE_BIN` と `CLAUDE_BIN_CANONICAL` を既に持っている（`pilot-gate-runner.sh:393-399`）
- 現在の `claude` は Mach-O の実行ファイルである（`/Users/kappa/.local/bin/claude` → `.../versions/2.1.270`）。launcher は `exec "$CLAUDE_BIN"` で起動するので、argv[0] は `CLAUDE_BIN` になる
- 先頭 2 トークンを見るのは、`bash <launcher>` の形でも `<launcher>` 単独の形でも launcher を識別するためである
- **args の照合は `claude` に分類した後にだけ行う。** 照合は既存の `validate-process-command` の規則（mode ごとの `--session-id` / `--resume`、`--settings` の canonical 一致）をそのまま使う

exec はプロセスの置き換えなので、同じ PID の分類は `launcher` から `claude` へ一度だけ変わる。`claude` から `launcher` へ戻ることは無い。

## 4. 上限時間

**別枠の `N1_EXEC_TIMEOUT_SECONDS=10` とし、binding を観測した時点から数える。**

| 選択肢 | 評価 |
| --- | --- |
| `N1_START_TIMEOUT_SECONDS`（30 秒）の残りに収める | 採らない。現状、launcher PID の待機と binding の待機はそれぞれ 30 秒の deadline を個別に持つ（`pilot-gate-runner.sh:957,1057`）。「同じ予算」の起点が定まらず、binding が遅れた回ほど exec の観測時間が削られる。削られた結果の unknown は、launcher の欠陥なのか予算不足なのかを区別できない |
| **別枠 10 秒** | 採る。起点が binding の観測で固定され、未到達の意味が「binding の後 10 秒経っても exec しなかった」に定まる |

10 秒の根拠: §2.2 の処理は node の起動数回で、通常は 1〜2 秒程度と見込む。その 5 倍以上を取り、負荷の高い CI 相当の環境でも競合を未到達と取り違えないようにする。
**この値は見込みであり実測ではない。** §6 の経過秒の記録を使い、smoke の結果で見直す。

観測間隔は 0.2 秒とする（`wait_for_launcher_pid` と同じ、`pilot-gate-runner.sh:1071`）。

## 5. 判定

binding の観測から `N1_EXEC_TIMEOUT_SECONDS` まで、§3 の観測を繰り返す。

| 観測 | 判定 | reason |
| --- | --- | --- |
| `claude` に分類し、args が一致 | 観測を止めて **pass**（次の工程へ進む） | — |
| `claude` に分類し、args が不一致 | 観測を止めて **fail** | `claude_args_mismatch` |
| `absent`（`claude` を一度も観測していない） | 観測を止めて **unknown** | `launcher_exited_before_exec` |
| `unrecognized` | 観測を止めて **unknown** | `process_identity_unrecognized` |
| 上限まで `launcher` のまま | **unknown** | `exec_not_observed` |

- **fail は「claude になったのに args が違う」ときだけとする。** launcher のコマンドラインを args 照合にかけない
- `unrecognized` を待ち続けない。exec は launcher から claude への 1 回の置き換えなので、それ以外の姿は正常な途中経過ではない
- pass の後に claude が終了した場合の扱いは、既存の後続工程（準備完了の検出、#444）に任せる
- すべての unknown と fail で、既存どおり `stop_native_case` を呼び、`pilot_ready=false` を保つ

## 6. 証跡

### 6.1 常に残すもの

`N1/<mode>/exec-observation.json`:

```json
{
  "schemaVersion": 1,
  "result": "claude_matched | claude_args_mismatch | exec_not_observed | launcher_exited_before_exec | process_identity_unrecognized",
  "timeoutSeconds": 10,
  "pollIntervalSeconds": 0.2,
  "bindingObservedElapsedFromLauncherStart": 0.0,
  "bindingFileMtime": "<RFC3339>",
  "elapsedSecondsToClaude": 0.0,
  "pollCount": 0,
  "transitions": [
    {"elapsedSeconds": 0.0, "class": "launcher"},
    {"elapsedSeconds": 0.8, "class": "claude"}
  ]
}
```

- `elapsedSecondsToClaude` は binding の観測から最初に `claude` を観測するまでの秒数である。到達しなければ `null`
- `transitions` は分類が変わった時点だけを記録する（毎回の観測は記録しない）
- `bindingFileMtime` は binding ファイルの mtime である。launcher が binding を書いた時刻と runner が観測した時刻の差を後から見られるようにする
- `process-command.raw` には、最後に観測したコマンドラインを残す（現状と同じファイル名）

### 6.2 未到達（`exec_not_observed`、`launcher_exited_before_exec`、`process_identity_unrecognized`）のとき

| ファイル | 内容 |
| --- | --- |
| `launcher-stat.txt` | 停止の直前に採った `ps -o stat= -p <pid>`。PID が無ければ `absent` |
| `process-command.raw` | 停止の直前に採ったコマンドライン |
| `launcher-output-tail.txt` | `pty.raw` の末尾 4096 byte を ANSI 除去したもの |
| `exec-late-observation.json` | 下の遅延観測の結果 |

**launcher の stderr は `stderr.raw` ではなく `pty.raw` にある。** launcher は PTY の中で動くので、`die` のメッセージは PTY に出る。`stderr.raw` は pty helper 自身の stderr である（`pilot-gate-runner.sh:1208-1220`）。

### 6.3 遅延観測（判定を変えない）

`exec_not_observed` のときだけ、判定を unknown に確定した後、停止する前にさらに 5 秒、同じ観測を続ける。

```json
{"lateWindowSeconds": 5, "lateClass": "claude | launcher | absent | unrecognized", "lateElapsedSecondsToClaude": 12.4}
```

- **遅延観測で `claude` を観測しても、判定は unknown のままとする。** pass へ変えない
- これで「上限を少し過ぎて exec した（上限が短い）」と「10 秒を過ぎても launcher のまま（launcher 側の別の欠陥）」を区別できる
- 5 秒は既存の `N1_EXIT_GRACE_SECONDS` と同じ値にする（`pilot-gate-runner.sh:46`）

### 6.4 切り分けの読み方

| 証跡 | 読み方 |
| --- | --- |
| pass、`transitions` の最初が `launcher`、`elapsedSecondsToClaude` が 1〜2 秒程度 | §2.2 の競合だった。run3 と smoke の差は採取の時点の違いで説明がつく |
| pass、`transitions` の最初から `claude` | 観測開始の時点で exec 済みだった（run3 と同じ状態）。この回だけでは競合の有無を判断しない |
| `exec_not_observed`、遅延観測で `claude` | 上限が短い。`N1_EXEC_TIMEOUT_SECONDS` を見直す |
| `exec_not_observed`、遅延観測も `launcher`、stat が `S` 系 | launcher が exec の前で止まっている。`launcher-output-tail.txt` と launcher 側を調べる |
| `launcher_exited_before_exec` | launcher が exec の前に終了した。`launcher-output-tail.txt` に `pilot-launcher:` で始まる `die` のメッセージがあるはずである |

## 7. 実装 PR への要件

実装は別 PR（`agmsg_programmer_claude`）とする。変更対象は `scripts/pilot-gate-runner.sh`、`scripts/lib/pilot-gate-isolation.py`、`tests/` である。

- 分類は `pilot-gate-isolation.py` の read-only helper に置く。`validate-process-command` の args 照合規則は変えずに再利用する
- runner の定数に `N1_EXEC_TIMEOUT_SECONDS=10` と `N1_EXEC_POLL_SECONDS=0.2` を足す
- `pilot-gate-runner.sh:1306-1308` の「binding は exec の直前に書かれる」というコメントは、§2.2 の実態に合わせて直す

### 7.1 試験（bats、fixture）

| ID | stub の振る舞い | 期待 | 検出する失敗モード |
| --- | --- | --- | --- |
| N1E-01 | launcher のコマンドラインを 1 秒返した後、args 一致の claude へ変わる | pass、`elapsedSecondsToClaude` が約 1、`transitions` が launcher → claude | 1 回採取への退行 |
| N1E-02 | **launcher のコマンドラインを返し続ける** | unknown `exec_not_observed`、`launcher-stat.txt` と `launcher-output-tail.txt` がある、fail にならない | exec 前を fail に畳む（本 Issue の欠陥） |
| N1E-03 | **args 不一致の claude**（`--settings` が別パス） | fail `claude_args_mismatch` | fail の経路が消える |
| N1E-04 | launcher のまま PID が消える | unknown `launcher_exited_before_exec` | PID 消滅の見逃し |
| N1E-05 | どちらでもないコマンドライン（例: `sleep 60`） | unknown `process_identity_unrecognized` を即時に返し、上限まで待たない | 未知の姿を待ち続ける・pass にする |
| N1E-06 | 上限を 2 秒過ぎてから claude へ変わる | unknown `exec_not_observed`、遅延観測は `claude` | 遅延観測で pass に変えてしまう |
| N1E-07 | fresh の args を持つ claude を resume の case で観測する | fail `claude_args_mismatch` | mode ごとの args 照合の退行 |
| N1E-08 | `CLAUDE_BIN` が symlink で、argv[0] が symlink のパス | `claude` に分類され pass | canonical 比較の欠落 |

N1E-02 と N1E-03 は #448 本文が指定した負の対照である。
**N1E-02 は、判定の中から launcher の分類を取り除くと fail に変わる。N1E-03 は、args 照合を取り除くと pass に変わる。** 実装 PR で、この 2 つの変異がそれぞれ KILLED になることを示す。

### 7.2 native smoke（verifier、merge 後）

- #434 の smoke を再実施し、fresh と resume の両方で marker 送信まで到達することを確かめる
- 報告には、各 case の `elapsedSecondsToClaude` を含める。未到達なら §6.2 の 4 ファイルの要点を含める

## 8. 報告事項（本書の範囲外）

**インストール済みの Claude Code は 2.1.270 である**（`claude --version`、`~/.local/bin/claude` の参照先 `versions/2.1.270`）。
#444 の実装は、準備完了の検出文字列を確かめた版を `N1_READY_VERIFIED_CLI_VERSIONS="2.1.268"` に固定している（`pilot-gate-runner.sh:51`）。

本書の修正で exec の観測を通過しても、同じ環境では準備完了の検出が `ready_patterns_unverified_for_cli_version` の unknown で止まる見込みである。smoke を再実施する前に、verifier が 2.1.270 で準備完了と阻害画面の文字列を実測し、版の一覧へ加える必要がある（#444 設計 §5.3 の手順）。

## 9. 非対象

- #434 の native smoke の再実施そのもの、#407 の 4 回目の実行
- #444 の再修正
- `pilot-launcher.sh` の変更（binding から exec までの処理は変えない）
