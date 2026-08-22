---
type: Verification
title: "Issue #88 macOS hang artifact and f7de22a path verification"
description: "Verify whether one cancelled macOS hang artifact exercises the wait path repaired by upstream commit f7de22a."
tags:
  - agmsg
  - issue-88
  - verification
  - github-actions
status: complete
timestamp: "2026-08-21T17:47:24+09:00"
---

# Issue #88 Hang Artifact Verification

## 結論

`f7de22a` の修正経路と、run `32211975474` の macOS 4/4 hang artifact は直接一致しない。
artifact は `scripts/remote.sh sync start` から `scripts/internal/remote-sync.mjs` を起動したテストを記録している。
一方、`f7de22a` は `scripts/internal/roster-sync-driver.sh`、`scripts/lib/roster-journal.sh`、`tests/test_roster_journal.bats` を変更している。

したがって、この artifact 単体を根拠に Issue #88 の close は提案しない。

## Evidence Packet

| 要素 | 記録 |
| --- | --- |
| value | `hang-samples-macos-latest-4` artifact `9353222416` の `hang-samples.txt` は、`test_remote_status_liveness.bats` の `remote.sh sync start testteam` と `remote-sync.mjs run --team testteam` を記録した。artifact 全体で remote 経路 marker は 48 件、対象 test marker は 166 件、roster 同期 marker は 0 件だった。 |
| cutoff | 読み取った対象は [run 32211975474](https://github.com/kappaseijin/agmsg/actions/runs/32211975474) の未失効 artifact 1件だけ。再現実験は実施しない。 |
| source | [Issue #88](https://github.com/kappaseijin/agmsg/issues/88)、[artifact 9353222416](https://api.github.com/repos/kappaseijin/agmsg/actions/artifacts/9353222416/zip)、[commit f7de22a](https://github.com/kappaseijin/agmsg/commit/f7de22aac933df9a44bac6e91ac968fa4f090e49)、`scripts/remote.sh:1683`、`scripts/internal/roster-sync-driver.sh:103`。 |
| command | `gh api .../actions/artifacts/9353222416/zip` で取得・展開し、`rg -o` で経路 marker を計数した。GitHub API で run metadata と commit の変更ファイルを取得し、codebase-memory で `_remote_sync_engine_start` と `_roster_inner_state` を確認した。 |
| negative control | 同一 artifact 検索で対象 test/remote 経路を検出できたため、roster marker 0 件は空 artifact や検索不能を示す値ではない。 |

## 未確認・既知の限界

process snapshot は実行されたソース行や根本原因を完全には証明しない。
両経路が registry lock を共有する可能性は残るが、artifact に roster-sync driver の実行証跡はない。
`f7de22a` 後の 8 shard 実行がハング 0 件という Issue コメントの観測は、この記録では再集計していない。
