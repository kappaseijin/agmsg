---
type: Implementation
title: Issue #246 official registration-query fixture
description: Official cutoffへ5ファイルpatchを隔離適用して検証するfixture。
timestamp: "2026-09-08T23:00:00+09:00"
issues: [246, 222, 236, 239, 244]
---

# Issue #246 official registration-query fixture

`patches/issue246-registration.patch`はofficial cutoff
`e58dbafad5a84be625f070385bb0c076c3daa4db`に対する5ファイルだけのunified diffである。

- `scripts/api.sh`
- `scripts/lib/api-registrations.sh`
- `tests/test_api_registrations.bats`
- `tests/test_api.bats`
- `README.md`

`scripts/issue246_registration_query.py`はcutoff Git objectを隔離rootへexportし、patchをcheck後に適用して、構文と2つのfocused Bats suiteをreport.jsonへ記録する。現役install、team DB、mainのprovider sourceは変更しない。`readOnlyStatus`はsuiteの成功コードだけでなく、session manifest・token-bound禁止PIDの対照と、その2変異がKILLEDのときだけpassにする。

8変異の実測は`2026-09-08T220000_issue-246-mutation-evidence.md`に保持する。無効controlをKILLEDへ数えず、8件の有効変異だけがKILLEDである。

## fixture実測

`/private/tmp/issue246-fixture-run/report.json` を再取得した結果は `status=pass`。

| value | cutoff | source | command |
| --- | --- | --- | --- |
| patch apply | `e58dbafad5a84be625f070385bb0c076c3daa4db` | `report.json` | fixture command |
| `bash -n` | 同上 + patch | `syntax.rc=0` | fixture command |
| registrations | 同上 + patch | `registrations.rc=0`, 14/14 | fixture command |
| api | 同上 + patch | `api.rc=0`, 19/19 | fixture command |

`tests/issue246_mutations.py`は各ケースごとにcutoffを新規exportしてpatchを適用し、source anchorを一度だけ置換する。`bash -n`が成功し、対応するBats caseが非0となるときだけKILLEDである。provider契約の対象は LIMIT 1、rows DISTINCT、validation bypass、early stdout、schema bypass、target scope、final snapshot bypass、tuple pairの8件である。別のread-only集合は、既存session record bytesの改変とtoken-bound禁止childの生成をKILLEDする。session manifestは相対path・lstat type・content hashをbyte順で比較し、symbolic linkはlink target bytesをhash化する。processはfixture tokenと一致する禁止対象だけをpre/postで0件と確認し、cleanup後も0件を確認する。

report.jsonは`sourceCutoff`、`patchHead`、`patchApplied`、`contractStatus`、`mutationStatus`、`readOnlyStatus`、`officialAvailability`を持つ。fail-closed対照は、要求sourceをdirectoryにしたread failureとcanonical化失敗をendpointへ注入したproject解決失敗である。read-only対照はconfig・DB/runtime・claimに加え、run rootのsession manifestとfixture tokenに結び付く禁止PIDのpre/post 0件を確認する。canonical化失敗は既存helperが入力pathを返すため実filesystemだけでは再現不能であり、既存のsnapshot race barrierと同じくtest専用環境変数でそのfail-closed分岐を実行する。公式採用前であることは`officialAvailability: not_adopted`で明示する。

実行:

```sh
python3 scripts/issue246_registration_query.py --output /tmp/issue246-fixture
python3 -m unittest tests/test_issue246_registration_query.py -v
python3 tests/issue246_mutations.py
```

Refs #246, #222, #236, #239, #244
