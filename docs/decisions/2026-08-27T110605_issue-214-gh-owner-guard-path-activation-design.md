---
type: Design
title: "Issue #214: gh owner guard の PATH activation 設計"
status: proposed
issue: "https://github.com/kappaseijin/agmsg/issues/214"
timestamp: "2026-08-27T11:06:05+09:00"
---

# Issue #214: gh owner guard の PATH activation 設計

## 決定

installer は `~/.agents/bin/gh` の owner guard を生成した後、Bash と Zsh の interactive startup file に管理 block を冪等に配置する。

管理 block は `~/.agents/bin/gh-owner-guard-path.sh` を source する。

helper は `~/.agents/bin` の重複を除去してから PATH の先頭へ一度だけ置く。

これにより、`path_helper` が Homebrew を先頭化した後でも、次の user rc file で guard が再び先頭になる。

Zsh は `.zshrc` の末尾を唯一の配置点とする。

Zsh の `.zshrc` は interactive login と non-login の両方で `/etc/zprofile` より後に読まれるためである。

Bash は `.bashrc` の末尾を配置点とする。

login Bash では、先に読まれる `.bash_profile`、`.bash_login`、または `.profile` のうち実効 file に同じ管理 block を置く。

この二つの block が helper を二度 source しても、PATH 正規化は冪等なので結果は変わらない。

既存 `gh-write-owner-guard.sh`、account 選択、repository owner 判定、allowlist は変更しない。

## 原因

現在の interactive shell matrix では、Bash の login と non-login、Zsh の non-login は `~/.agents/bin/gh` を解決する。

Zsh の login shell だけは `/opt/homebrew/bin/gh` を解決する。

`/etc/zprofile` は `/usr/libexec/path_helper -s` を実行する。

その結果、`/opt/homebrew/bin` が `~/.agents/bin` より前に再配置される。

現在の `.zshrc` は Rancher Desktop の PATH 追加だけであり、guard directory を再先頭化しない。

したがって owner guard の launcher は存在しても、Zsh login shell の素の `gh` 呼出しへ制御が渡らない。

これは guard の認可ロジックの誤りではなく、launcher を選ぶ名前解決の欠落である。

## 根拠

| value | cutoff | source | command |
| --- | --- | --- |
| `zsh` login だけが `/opt/homebrew/bin/gh` を解決し、agents bin は PATH の後方にある。 | Issue #214 の再現 | local interactive shell | `/bin/zsh -ilc 'command -v gh; …'` |
| Bash login と non-login、Zsh non-login は `~/.agents/bin/gh` を解決する。 | 原因を Zsh login 初期化へ限定する | local interactive shell | `bash/zsh` の 4 条件 matrix |
| `/etc/zprofile` は `path_helper` を実行する。 | PATH 再構成の実行点 | `/etc/zprofile:10-12` | `sed -n '1,80p' /etc/zprofile` |
| `path_helper` の出力は Homebrew を agents bin より前に置く。 | Zsh login の実測と一致する順序 | `/usr/libexec/path_helper` | `/usr/libexec/path_helper -s` |
| `.zshrc` は agents bin を追加しない。 | Zsh login で順序を回復しない理由 | `~/.zshrc` | `sed -n '1,90p' ~/.zshrc` |
| `install_gh_owner_guard` は launcher を `~/.agents/bin/gh` に生成するだけで shell startup file を管理しない。 | installer 変更の必要性 | `install.sh:280-337` | `codebase-memory get_code_snippet` |
| Zsh login で agents bin を prepend すると guard を選び、append すると Homebrew を選ぶ。 | 固定方式の正負対照 | local interactive shell | `/bin/zsh -ilc 'PATH=…; command -v gh'` |

## 構成

```mermaid
flowchart TD
  A[interactive shell starts] --> B{shell and mode}
  B -->|Zsh login| C[/etc/zprofile path_helper]
  B -->|Zsh non-login| D[.zshrc]
  B -->|Bash non-login| E[.bashrc]
  B -->|Bash login| F[login file then .bashrc]
  C --> D
  D --> G[agmsg managed block]
  E --> G
  F --> G
  G --> H[gh-owner-guard-path helper]
  H --> I[deduplicate and prepend ~/.agents/bin]
  I --> J[command -v gh returns guard launcher]
  J --> K[existing owner guard authorization]
```

## Installer 契約

新しい source template は `scripts/guards/gh-owner-guard-path.sh` とする。

installer は template を `~/.agents/bin/gh-owner-guard-path.sh` へ temporary file 経由で配置し、実行可能にしてから rename する。

helper は POSIX shell で source できる形にする。

helper は PATH を colon-separated entry として扱い、`~/.agents/bin` と完全一致する全 entry を除去した後に、その directory を先頭へ追加して export する。

空 PATH entry と PATH 内の他 directory の相対順序は保持する。

helper 自身は `~/.agents/bin/gh` が agmsg launcher として検証済みのときだけ installer が配置する。

launcher が未生成、非 agmsg file、または helper 配置失敗なら、installer は rc file を変更せず nonzero で停止する。

これにより、shell integration だけを残して実 gh を露出させる partial install を作らない。

管理 block は次の marker に囲む。

```sh
# >>> agmsg gh owner guard PATH >>>
if [ -r "$HOME/.agents/bin/gh-owner-guard-path.sh" ]; then
  . "$HOME/.agents/bin/gh-owner-guard-path.sh"
fi
# <<< agmsg gh owner guard PATH <<<
```

installer は `.zshrc`、`.bashrc`、実効 Bash login file の末尾に block を追加する。

Bash login file は `.bash_profile`、`.bash_login`、`.profile` の順で最初に存在する file とする。

どれも存在しない場合は `.bash_profile` を新規作成する。

各 file では marker pair が無い場合だけ末尾に追加する。

marker pair が一組だけで正しい順序なら、block 本文を canonical form へ置換する。

marker が複数、片側だけ、または逆順なら、その file は変更せず installer を nonzero にする。

file 更新は同じ directory の temporary file と rename を使う。

既存 file の内容と mode は保持する。

`install.sh --update` と通常 install は同じ integration function を呼ぶ。

## Uninstall 契約

uninstall は `.zshrc`、`.bashrc`、`.bash_profile`、`.bash_login`、`.profile` のうち存在する file から完全な marker block だけを atomic に除去する。

marker が壊れている file は変更せず nonzero にする。

すべての managed block を安全に除去できた場合だけ、`~/.agents/bin/gh-owner-guard-path.sh` と既存 `~/.agents/bin/gh` launcher を除去する。

この順序により、source block を残して helper だけを消す partial uninstall を作らない。

ユーザーが marker 外へ書いた shell 設定は変更しない。

## 受入テスト

1. fresh install は fake HOME に agmsg launcher と helper を配置し、Bash と Zsh の各 startup file へ canonical block を一組だけ置くことを確認する。

2. install を二回実行しても block 数、helper content、PATH 正規化の結果が変わらないことを確認する。

3. fake real `gh` を agents bin より先に置いた環境で、Bash と Zsh の login と non-login の各 shell が `command -v gh` と `which gh` の両方で fake HOME の agmsg launcher を返すことを確認する。

4. Zsh login の fixture は PATH を Homebrew 相当の fake real `gh` が先行する状態へ再構成してから `.zshrc` を読む。

この negative baseline で managed block を除くと real `gh` が選ばれ、block を含めると launcher が選ばれることを確認する。

5. helper の `prepend` を `append` へ一箇所だけ変える mutation は、Zsh login の expected launcher assertion で `KILLED` になることを確認する。

6. `.zshrc` の source block を一箇所だけ除く mutation は、Zsh login の real `gh` selection で `KILLED` になることを確認する。

7. malformed marker、user-owned `gh`、launcher 生成失敗、helper 配置失敗では rc file が変更されず installer が nonzero になることを確認する。

8. uninstall は canonical block と helper を除去し、marker 外の shell 設定を保持することを確認する。

9. existing `tests/test_gh_write_owner_guard.bats` はそのまま実行し、PATH activation の変更が owner、account、repository resolver の認可契約を変えないことを確認する。

テストは temporary HOME と fake executable だけを使う。

実際の GitHub write、実 user shell rc file、`/etc/paths.d` は変更しない。

## 変更箇所

| path | change |
| --- | --- |
| `scripts/guards/gh-owner-guard-path.sh` | PATH を正規化する新しい POSIX source helper を追加する。 |
| `install.sh` | launcher 検証後の helper 配置、Bash/Zsh marker block の atomic な install and update を追加する。 |
| `uninstall.sh` | managed block を先に除去し、成功時だけ helper を除去する。 |
| `tests/test_install.bats` | temporary HOME の startup matrix、idempotence、marker failure、uninstall を検証する。 |
| `tests/test_gh_write_owner_guard.bats` | 既存認可契約の回帰を継続して検証する。 |
| `README.md` と `README.ja.md` | install、startup matrix の確認、uninstall、PATH と直接 executable の保証境界を自己完結で説明する。 |

## 不採用案

| 案 | 不採用理由 |
| --- | --- |
| `7000_llm.sh` だけの変更 | Bash dotfiles へ依存し、Zsh login の `path_helper` 後に実行されない。 |
| shell alias または function | interactive command は覆えても、child script の `gh` 解決を覆えない。 |
| `/etc/paths.d` の順序変更 | root 権限と OS 全体への変更を要求し、agmsg installer の責務を越える。 |
| guard の owner/account 判定を変更 | 問題は guard の入口に到達しないことであり、認可ロジックを変えても修正にならない。 |

## 保証境界

この変更は、installer 実行後の標準 Bash/Zsh interactive startup で名前解決される `gh` を守る。

実 gh の絶対 path 実行、PATH を意図的に書き換えた同一 command line、launcher または user rc file の手動改変、別ユーザーと root は user-space launcher の外である。

README はこの境界と確認コマンドを利用者向けに記載する。

## 引き渡し

この設計の review 合格後、programmer は Issue #214 だけを含む implementation PR を作成する。

実装 PR は guard 認可ロジックの変更を含めない。
