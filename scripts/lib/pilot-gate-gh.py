#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import sys
import time


def die(message: str, code: int = 64) -> "NoReturn":
    print(f"pilot-gate-gh: {message}", file=sys.stderr)
    raise SystemExit(code)


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        die(f"missing environment: {name}")
    return value


def write_json_atomic(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_jsonl(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_store(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "nextId": 1, "comments": []}
    with open(path, "r", encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        die("store root is not object")
    if value.get("schemaVersion") != 1:
        die("store schema mismatch")
    if not isinstance(value.get("nextId"), int) or value["nextId"] < 1:
        die("store nextId invalid")
    if not isinstance(value.get("comments"), list):
        die("store comments invalid")
    return value


def parse_common(argv: list[str]) -> tuple[str, str, list[str]]:
    expected_repo = require_env("AGMSG_GATE_GH_REPO")
    expected_issue = require_env("AGMSG_GATE_GH_ISSUE")
    if len(argv) < 4 or argv[0:2] != ["issue", argv[1]]:
        die("unsupported command")
    return expected_repo, expected_issue, argv


def parse_repo_issue(argv: list[str], operation: str) -> tuple[str, str, dict[str, str]]:
    if len(argv) < 3 or argv[0] != "issue" or argv[1] != operation:
        die("unsupported command")
    issue = argv[2]
    options: dict[str, str] = {}
    index = 3
    while index < len(argv):
        option = argv[index]
        if option not in {"--repo", "--body-file", "--json"}:
            die(f"unsupported option: {option}")
        if index + 1 >= len(argv):
            die(f"missing value for {option}")
        if option in options:
            die(f"duplicate option: {option}")
        options[option] = argv[index + 1]
        index += 2
    return issue, options.get("--repo", ""), options


def main() -> int:
    store_dir = pathlib.Path(require_env("AGMSG_GATE_GH_STORE")).resolve(strict=True)
    log_path = pathlib.Path(require_env("AGMSG_GATE_GH_LOG"))
    expected_repo = require_env("AGMSG_GATE_GH_REPO")
    expected_issue = require_env("AGMSG_GATE_GH_ISSUE")

    argv = sys.argv[1:]
    append_jsonl(
        log_path,
        {
            "schemaVersion": 1,
            "atMonotonicNs": time.monotonic_ns(),
            "argv": argv,
            "cwd": os.getcwd(),
        },
    )

    if len(argv) < 3 or argv[0] != "issue":
        die("only issue comment/view are supported")

    operation = argv[1]
    if operation not in {"comment", "view"}:
        die("only issue comment/view are supported")

    issue, repo, options = parse_repo_issue(argv, operation)
    if issue != expected_issue:
        die("issue number outside isolated scope")
    if repo != expected_repo:
        die("repository outside isolated scope")

    store_path = store_dir / "comments.json"
    value = load_store(store_path)

    if operation == "comment":
        if set(options) != {"--repo", "--body-file"}:
            die("issue comment requires exactly --repo and --body-file")
        body_path = pathlib.Path(options["--body-file"])
        try:
            body_real = body_path.resolve(strict=True)
        except OSError as exc:
            die(f"body file unavailable: {exc}")
        allowed_root = pathlib.Path(require_env("AGMSG_GATE_GH_BODY_ROOT")).resolve(strict=True)
        try:
            body_real.relative_to(allowed_root)
        except ValueError:
            die("body file outside gate broker state root")
        if not body_real.is_file() or body_real.is_symlink():
            die("body file must be regular non-symlink file")
        body = body_real.read_text(encoding="utf-8")
        if len(body.encode("utf-8")) > 4096:
            die("body too large")
        comment_id = value["nextId"]
        value["nextId"] = comment_id + 1
        url = f"https://github.com/{expected_repo}/issues/{expected_issue}#issuecomment-{comment_id}"
        value["comments"].append(
            {
                "url": url,
                "body": body,
                "issue": int(expected_issue),
                "repo": expected_repo,
            }
        )
        write_json_atomic(store_path, value)
        print(url)
        return 0

    if set(options) != {"--repo", "--json"} or options.get("--json") != "comments":
        die("issue view supports exactly --repo ... --json comments")
    print(json.dumps({"comments": value["comments"]}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
