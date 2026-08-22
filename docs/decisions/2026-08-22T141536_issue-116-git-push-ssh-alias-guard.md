---
type: Design
title: Issue #116 — git push owner guardの固定SSH alias許可境界
description: 承認済みbot用SSH aliasだけをdestination hostとして許可し、未知aliasと非許可ownerをtransport前に拒否する設計。
tags:
  - git
  - security
  - ssh
  - owner-guard
  - issue-116
status: proposed
timestamp: "2026-08-22T14:15:36+09:00"
---

# Issue #116 — git push owner guardの固定SSH alias許可境界

## 結論

`scripts/guards/git-push-owner-guard.sh` のdestination host許可は、`github.com`と次の三つの**コードリテラル**だけにする。

| 許可host | 用途 |
| --- | --- |
| `github.com` | canonical HTTPS / SSH endpoint |
| `github.com-kappaseijinsub` | 承認済みsub identityのSSH alias |
| `github.com-kappaseijin4claude` | 承認済みClaude bot identityのSSH alias |
| `github.com-kappaseijin4codex` | 承認済みCodex bot identityのSSH alias |

`github.com-*`のようなprefix許可、SSH configからの動的取込み、環境変数・Git config・policyファイルによる上書きは採用しない。
host判定とowner判定は別の防壁として維持し、allowlist owner以外は承認済みaliasを使っても拒否する。

## 調査結果

現行の`authorize_url()`は`PARSED_HOST = github.com`を要求するため、上記aliasを`git@<alias>:kappaseijin/fixture.git`として渡すとtransport前に拒否する。
ローカルの`ssh -G`では三aliasが`hostname github.com`へ解決する一方、未定義の`github.com-unrecognized`は自分自身へ解決する。

PMから提示された`github.com-*`を許可する試案を同じparserへ適用すると、既知aliasは許可されるが、未定義の`github.com-unrecognized`も許可された。
これは「未知hostをfail-closedで拒否する」Issue #3の保証を弱めるため採用しない。

この設計はclosed #3のfollow-upであり、open #104のrosterベースPR account routingやgh write guardには変更を加えない。

## 実装境界

`allowed_host()`を新設し、`ascii_lower`済みのhostと固定配列を完全一致で比較する。
`authorize_url()`は、既存どおり`parse_push_url()`成功後に`allowed_host "$PARSED_HOST"`、続いて`allowed_owner "$PARSED_OWNER"`を両方要求する。

既存のeffective URL解決は変更しない。
具体的にはremoteの全pushurl、`insteadOf` / `pushInsteadOf`による書換え、直接URLのsynthetic remote解決、`--repo`、`-c`、`--config-env`を通過した最終URLへ同じhost/owner判定を適用する。

SSH configはこのguardのpolicy sourceではない。
それはローカル接続設定であって、任意aliasを許可する根拠ではない。
新しいbot aliasを必要とする場合は、コードリテラルとBats試験を追加するPRで明示的に拡張する。

## TDD受入条件

既存の`tests/test_git_push_owner_guard.bats`に、ローカルbare remoteとfake SSHだけを使う次の試験を追加する。
第三者repositoryまたは実ネットワークへ接続してはならない。

| ID | 入力 | 期待 | 防ぐ回帰 |
| --- | --- | --- | --- |
| GPG-16 | 三つの承認済みalias × allowlisted owner | `status=0`、fake SSHとpre-receive markerあり | 正常bot aliasをtransport前に拒否する回帰 |
| GPG-17 | `github.com-unrecognized` × allowlisted owner | 非0、SSH logとmarkerなし | prefix許可で未知hostを通す回帰 |
| GPG-18 | `github.com-kappaseijin4codex.evil` × allowlisted owner | 非0、SSH logとmarkerなし | suffixを部分一致にして別hostを通す回帰 |
| GPG-19 | 承認済みalias × `thirdparty` owner | 非0、SSH logとmarkerなし | host許可がowner allowlistを迂回する回帰 |
| GPG-20 | canonical `github.com` × allowlisted owner | `status=0`、fake SSHとmarkerあり | alias拡張で既存canonical経路を壊す回帰 |

GPG-17〜19はtransport未開始を観測する負の対照である。
試験前にmarkerとSSH logを消し、失敗時に両方空であることを確認する。
GPG-16とGPG-20は、同じfixtureとfake SSHが正常な許可経路を検出できる正の対照である。

## 変更ファイルと検証

| 種別 | ファイル | 内容 |
| --- | --- | --- |
| 実装 | `scripts/guards/git-push-owner-guard.sh` | 固定`allowed_host()`と`authorize_url()`への接続 |
| 試験 | `tests/test_git_push_owner_guard.bats` | GPG-16〜20のlocal fake-SSH controls |
| 利用者資料 | `README.md`, `README.ja.md` | 許可host集合、未知alias拒否、拡張方法 |

実装PRでは次を実行する。

~~~text
BATS_SHELL=/bin/bash bats --print-output-on-failure tests/test_git_push_owner_guard.bats
bash -n scripts/guards/git-push-owner-guard.sh
git diff --check
~~~

実装者とformal reviewerは、PR head上でGPG-16〜20の正負対照と既存GPG-01〜15が通ることを確認する。
レビュー対象はこのhost許可だけであり、owner allowlist・Git URL parser・SSH config・#104のrouting guardへ無関係な変更を混ぜない。

## 残余リスク

このguardの保証は、従来どおり標準agent環境でPATH shimを通るGit実行と、文字列として解決されたdestination URLに限られる。
同一OSユーザーがshimを回避する、またはSSH接続を任意コマンドで置換する経路は、既存のuser-space guardの保証範囲外である。
本変更はその範囲を広げず、承認済みaliasの文字列だけをcanonical hostと同じfail-closed policyへ追加する。

## 一次資料

- [Issue #116](https://github.com/kappaseijin/agmsg/issues/116)
- [Issue #3](https://github.com/kappaseijin/agmsg/issues/3)
- `scripts/guards/git-push-owner-guard.sh:213-274`
- `tests/test_git_push_owner_guard.bats:85-190`
- `~/.ssh/conf.d/hosts/github.conf`（実在aliasの観測のみ。policy sourceではない）
