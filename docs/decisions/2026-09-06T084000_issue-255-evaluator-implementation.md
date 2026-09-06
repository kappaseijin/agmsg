---
type: Implementation
title: Issue 255 evaluation v3 implementation
timestamp: 2026-09-06T08:40:00+09:00
status: implemented-independent-acceptance-pending
---

Implement the adopted semantics contract in one shared offline Python evaluator, called by both PowerShell evaluation entry points. Preserve raw producers, existing summaries and artifacts. Store a separate versioned evaluation with source SHA256 and executing Git commit. Keep all observation candidates rather than pairing by notification time. No WMI-only result can certify process time, generation, scope or comparison.

Controls first replace the obsolete known expectations: stable IDs under reordering, delayed notifications, PID reuse, missing required stop with unrelated stops present, old known fields, corrupt rows/clock/operation boundaries and independent subject rc. The PowerShell wrapper must run against the same controls on Windows, without WMI collection. Comparison commands stay blocked; CI runs offline semantics only, without a new Windows collection experiment.

Base is preserved local #263 followup ddf67d2. This is a stacked correction, not permission to merge #263. Formal review and independent verifier inspect a fixed HEAD; no self acceptance.

## 保存済みpacketの再評価

保存済み旧形式対照（verifier作成のsyntheticでありWindows実測ではない）`/tmp/agmsg-pr260-final-verifier.YtSsZe/source/evidence/eval-valid.jsonl`のSHA256は`8493e66b6b934fbc35a4dc0d1cc236b556cbbff54e60d0582e48f9fea703275a`。通知捕捉known、時計形式unknown（raw FILETIMEなし）、実時刻・実世代unknown、比較blockedとなる。旧known欄は昇格根拠にしない。

実Windows artifact 9978574473の保存packet `/tmp/agmsg263-second-artifact/preflight-0-66d57194a2414e32b645d5bc646153ad/packet.jsonl` のSHA256は`26f649ab0c4726ca923efda3ab7fee2cd4719c3e2395493670ce536d92844f8d`。通知捕捉known、時計形式known、実時刻・実世代unknown、subject rc0、比較blockedとなる。8 PIDの通知候補を保持する。必要対象の通知品質と、他の観測PIDの個別品質を分けて集約する。

確認方法は保存元bytesをコピーして`evaluate_file`へ渡し、元/コピーbytesの不変とsource hashをassertする。作業中対照の派生出力は`/var/folders/8q/zkxfsm6n2hl9j4cf44df_c4m0000gp/T/agmsg255-v3-saved-l_kjbf20/`。これはproducerの自己確認であり、固定HEADの独立受入はverifierへ渡す。

旧PowerShellがConvertFrom-Json/ConvertTo-Jsonを通すとUTC文字列を自動変換するため、evaluate modeは共通評価器のJSONを直接返す。raw生成3関数はbaseとbyte一致を確認。旧`DropStopEvent`による暗黙変形は拒否し、明示的に別packetを作る対照へ統一した。

現CLIは`python tests/windows/lifetime_packet.py <packet.jsonl>`で隣接する`packet.evaluation-v3.json`を追加する。既存の異なる派生出力も上書きせず、別出力先を要求する。`lifetime_replay.py`は既存hash manifestを検証後にv3だけを追加し、旧summary/gate/hashを変更しない。`preflight`/`condition`は実行開始前に非zeroでblockedを返す。新たな収集・比較・fixture変更は行わない。


## 検証と引渡し

`python3 -m unittest discover -s tests/windows -p 'test_lifetime_*.py' -v`で旧/新×rc0/7×5変形のPowerShell/Python一致を含む13試験がPASS。元行番号対照1試験を追加してPASS。固定commit後に14試験を一括再実行し、そのログを`/tmp/agmsg255-v3-fixed-tests.log`へ保存する。生データ収集や無変更CI rerunは行わない。

Python構文、diff whitespace、enforceable assertion baseline718を確認。README影響なし。固定HEAD全差分formal reviewとWindows独立実測は未完了であり、merge・比較再開は認めない。
