# agmsg

*[日本語](README.ja.md)*

[![CI](https://img.shields.io/github/actions/workflow/status/fujibee/agmsg/tests.yml?branch=main&label=CI&logo=github)](https://github.com/fujibee/agmsg/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/fujibee/agmsg?label=release)](https://github.com/fujibee/agmsg/releases/latest)
[![license](https://img.shields.io/github/license/fujibee/agmsg)](LICENSE)

Cross-agent messaging for CLI AI agents. No daemon, no network, no complexity.

> **For AI agents:** see [`/llms.txt`](llms.txt) for a quick, machine-friendly orientation.

<a href="https://www.producthunt.com/products/agmsg?utm_source=badge-top-post-badge&utm_medium=badge" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="https://api.producthunt.com/widgets/embed-image/v1/top-post-badge.svg?post_id=1165435&theme=dark&period=daily">
    <img src="https://api.producthunt.com/widgets/embed-image/v1/top-post-badge.svg?post_id=1165435&theme=light&period=daily"
         alt="agmsg — #5 Product of the Day on Product Hunt" width="250" height="54">
  </picture>
</a>

You stop being the copy-paste courier between your agents. Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, and any other CLI agent message each other directly through a shared local SQLite database — no human in the middle.

<p align="center">
  <img src="docs/logos/supported-agents.png" width="780"
       alt="Supported agents: Claude Code, Codex, Gemini, GitHub Copilot, Antigravity, OpenCode, Hermes">
</p>

**What it isn't:**

- Not MCP. No MCP server, no extra runtime — just `bash` + `sqlite3`.
- Not subagents. agmsg connects *peer* sessions across different tools. `spawn` can launch a new peer agent in its own terminal, but it's an independent session you talk to over agmsg — not a child process this one manages.
- Not a broker. The SQLite file holds the local queue and its short receiver leases; there is no daemon, socket, or remote service.

## Demo

Two `monitor`-mode Claude Code instances, left alone in the same team, play tic-tac-toe against each other with no human in the loop — each picks up the other's move in real time:

![Two Claude Code agents autonomously playing tic-tac-toe over agmsg](docs/agmsg-demo.gif)

In real use it looks like this — Claude Code asking Codex for a code review and getting it back, all over agmsg:

![Claude Code and Codex exchanging code review messages via agmsg](docs/screenshot.png)

## Quick Start

**Requires:** `bash` and `sqlite3`. macOS ships both. On a minimal Linux box (some Debian/Ubuntu containers, Alpine) you may need to install `sqlite3` first — `sudo apt-get install -y sqlite3` or your distro's equivalent.

```bash
# 1. Install — npx is the fastest path, no clone needed
npx agmsg

# 2. Restart Claude Code / Codex / Gemini CLI / Antigravity / OpenCode to pick up the new skill

# 3. Run the command — it will prompt for team and agent name on first use
#    Claude Code:  /agmsg
#    Codex:        $agmsg
#    Gemini CLI:   $agmsg
#    Antigravity:  $agmsg
#    OpenCode:     $agmsg
```

That's it. The slash command prompts you for a team name and an agent name on first use, then asks you to pick a [delivery mode](#delivery-modes) (default on Claude Code and Codex: `monitor` — real-time push; Codex delivers it through a bridge). After that, you talk to your agent naturally — see [First run](#first-run) below.

Prefer to inspect the code first, track the latest `main`, or pick a custom command name? See [Install](#install) below for the `setup.sh` one-liner, `git clone`, and the Claude Code plugin marketplace paths.

To sync a team between two installs through the self-hosted reference server, follow [Remote setup](docs/remote-setup.md).

## How it works

agmsg is a thin transport. Each agent has a hook (or a Monitor stream, depending on delivery mode) that reads from a shared SQLite file and surfaces incoming messages as text the agent can react to. `send.sh` queues a row; a receiver takes a short exclusive lease, hands its text to the host, then persists a receipt. There is no daemon, no socket, no broker — the file is the shared floor and the agents take turns on it.

The store is WAL-mode SQLite, so multiple readers and a single writer coexist without conflicts. A receipt means only that the receiver handed the message to its host (stdout or an inline Codex turn); it does **not** mean the LLM completed the requested task. History is durable: messages stay in the DB after the session ends, and `history.sh` can replay an old room into a fresh agent.

## Install

agmsg ends up at `~/.agents/skills/agmsg/` no matter which install path you take. Pick whichever fits your setup.

**Which path gets the latest?** The `git clone` and `setup.sh` (curl) paths install straight from `main`, so they're always current. The **npm package and the Claude Code plugin are cut from tagged releases on a cadence**, so they can lag `main` by a few fixes — fine for almost everyone, but if you specifically want a just-merged change, clone the repo. You can always check exactly what you're running with `/agmsg version` (or `scripts/version.sh`): a tagged release reads like `v1.0.3`, while a checkout ahead of the last release reads like `v1.0.3-6-g1a2b3c4` (6 commits past `v1.0.3`).

### npm / npx

```bash
npx agmsg            # one-shot, no global install
# or
npm i -g agmsg && agmsg install
```

The npm package is a thin bootstrapper that downloads and runs the canonical `setup.sh`. Published from this repo via [npm Trusted Publisher (OIDC)](https://docs.npmjs.com/trusted-publishers) with [SLSA provenance](https://slsa.dev/) — the attestation is visible at <https://www.npmjs.com/package/agmsg>.

### Claude Code plugin marketplace

Inside Claude Code:

```
/plugin marketplace add fujibee/agmsg
/plugin install agmsg@fujibee-agmsg
/reload-plugins
/agmsg
```

The plugin install path drops the skill into `~/.claude/plugins/cache/`; the first invocation of `/agmsg` runs a bootstrap that populates `~/.agents/skills/agmsg/` (database, scripts, team registry) so the runtime is identical to a script install. If your environment lacks `sqlite3` (some minimal Linux containers don't ship it by default), the bootstrap will surface a clear error message — install `sqlite3` and re-invoke `/agmsg`.

### Direct script

Clone the repo first, then run the installer — this is also the path that always tracks the latest `main`:

```bash
git clone https://github.com/fujibee/agmsg.git
cd agmsg
./install.sh              # Interactive (asks command name, default: agmsg)
./install.sh --cmd m      # Non-interactive with custom command name
./install.sh --agent-type gemini    # Install a Gemini-oriented SKILL.md
./install.sh --agent-type opencode  # OpenCode-only: sets shared skill to OpenCode template
```

The **command name** determines:
- Skill folder: `~/.agents/skills/<cmd>/`
- Claude Code / Copilot CLI: `/<cmd>`
- Codex / Gemini CLI / Antigravity: `$<cmd>`

`--cmd` and `--agent-type` are only available via the direct-script path; the `npm` and plugin paths always install as `agmsg` and auto-detect the host agent type.

After install, **restart your agent** (Claude Code / Codex / Gemini CLI / Copilot CLI / Antigravity / OpenCode) so it picks up the new skill.

### GitHub CLI destination guard

When `gh` is installed, `install.sh` also installs `~/.agents/bin/gh`. Put
`~/.agents/bin` before the real GitHub CLI on the agent PATH if it is not
already there. The launcher fixes both the guard script and the real `gh`
executable to absolute paths at install time; it does not search PATH again.

The guard allows read-only commands and destination-checks GitHub writes. A
write is allowed only when the resolved URL is exactly on `github.com` and the
owner is `kappaseijin` or `kappaseijinjp`. It resolves destinations in this
order: explicit `-R`/`--repo`, `GH_REPO`, `gh repo set-default --view`, then the
current checkout. `GH_HOST` and malformed, missing, ambiguous, or failed
resolver results are rejected. `gh api` is read-only only for GET (or when no
method/body is requested); `-f`/`-F`, `--field`/`--raw-field`, and `--input`
make an omitted-method request non-read-only. Unknown commands, aliases that
execute commands, and extensions that execute or install commands fail closed.

For pull-request writes, account routing is applied after the immutable
destination check. Explicit `GH_CONFIG_DIR`, `GH_TOKEN`, or `GITHUB_TOKEN`
values are preserved and the existing cwd-based `pr-account-policy.conf` is
then enforced. When none is set, an unambiguous agmsg seat type is resolved by
`whoami.sh`: `claude-code` selects `kappaseijin4claude` and `codex` selects
`kappaseijin4codex`. If that token is acquired successfully, it is used for
the invocation and the static cwd policy is skipped. Missing or ambiguous
identity, unsupported commands, and token lookup failure keep the static policy
in force. Tokens are never printed or logged.

The installer refuses to overwrite a non-agmsg `~/.agents/bin/gh`. If `gh` is
not installed, it leaves the guard uninstalled and prints a message; rerun
`./install.sh --update` after installing `gh`. `uninstall.sh` removes only the
agmsg-generated guard.

### Git push destination guard

When Git is installed, `install.sh` also installs `~/.agents/bin/git`. Put
`~/.agents/bin` before the real Git executable on every agent PATH, then verify
the effective command with:

```bash
PATH="$HOME/.agents/bin:$PATH" command -v git
```

It must print `~/.agents/bin/git` (with the home directory expanded). The
launcher pins the guard and the real Git executable to absolute paths at
install time. `git push` checks every effective push URL, including all
`remote.*.pushurl` entries and `url.*.insteadOf` / `url.*.pushInsteadOf`
rewrites. Writes are allowed only when the resolved host is exactly one of
`github.com`, `github.com-kappaseijinsub`, `github.com-kappaseijin4claude`, or
`github.com-kappaseijin4codex`, and the owner is `kappaseijin` or
`kappaseijinjp`. Malformed URLs, local paths, unknown hosts, third-party
owners, and unresolved destinations fail closed before transport starts.
Direct URLs are resolved through a temporary synthetic remote so the same
rewrite rules are checked. Git aliases and `git send-pack` are rejected;
read-only commands and `git clone` continue to use the real Git executable.

The four SSH host names above are a fixed code-literal policy boundary. An
arbitrary `github.com-*` name, an SSH config alias, or an environment/config
override does not extend it. Adding an approved alias requires changing the
guard's code-literal allowlist and adding local fake-SSH positive and negative
tests in a reviewed PR.

The installer refuses to overwrite a non-agmsg `~/.agents/bin/git`. If Git is
not found, it leaves the guard uninstalled and prints a message; rerun
`./install.sh --update` after installing Git. `uninstall.sh` removes only the
agmsg-generated Git guard. This is a user-space PATH guard: invoking Git by an
absolute path, changing PATH, replacing the shim, or using another user is
outside its guarantee.

### Windows: Git Bash & Codex

agmsg's implementation is the Bash script set under `scripts/`, so on Windows the
scripts run through **Git Bash** (Git for Windows, with `sqlite3` available on the
Git Bash PATH). There is no PowerShell reimplementation.

- In Windows environments, Claude Code naturally works with Bash/Git Bash for
  these script calls, but native Windows Codex commands and hooks often start
  from PowerShell. Keep the actual agmsg execution path pinned to Git Bash so
  all agents share the same `$HOME` and SQLite database.
- **Codex delivery hooks** are wrapped automatically. On native Windows Codex runs
  hook commands via PowerShell, which cannot execute a bare `.sh` path, so agmsg
  emits a `commandWindows` entry that invokes Git Bash (`& $bash -lc '...'`). No
  setup needed — see `windows_wrap()` in `scripts/delivery.sh`.
- **Interactive / agent-typed commands** call the scripts through Git Bash, e.g.
  `bash -lc 'scripts/whoami.sh "$(pwd)" codex'`.
- Heads-up: a bare `bash` in PowerShell usually resolves to the **WSL** shim
  (`WindowsApps\bash.exe`), which has a separate `$HOME` and database — agents
  would then talk to a different DB than Claude Code. Pin Git Bash in your
  PowerShell profile so everything shares one database:

  ```powershell
  Set-Alias bash 'C:\Program Files\Git\bin\bash.exe'
  ```

## First run

Open your project in your agent (Claude Code, Codex, Gemini CLI, etc.) and run:

```
/agmsg              # Claude Code, Copilot CLI
$agmsg              # Codex, Gemini CLI, Antigravity
```

On first use it asks for a **team name** (joins an existing team or creates a new one) and an **agent name** for this project — that's the whole onboarding. After that, talk to your agent naturally:

- *"send alice a message saying the deploy is done"*
- *"check my messages"*
- *"who's on the team"*

The agent picks the right subcommand and runs it for you. You don't need to memorize anything below — the script reference further down is for automation, scripts, and CI.

For renaming a team, leaving, joining the same team from a second project, or clearing a project's registrations, see [docs/teams.md](docs/teams.md).

### Multiple roles per project (`actas` / `drop`)

Same project, same agent type, different role — for example a `tech-lead` identity for architecture reviews and a `biz-analyst` identity for requirements work, both living on top of the same workspace. Toolset and assets are shared; only the role differs.

```
/agmsg actas tech-lead     # switch to tech-lead (creates it if not yet registered)
/agmsg actas biz-analyst   # switch to biz-analyst
/agmsg drop biz-analyst    # remove the role from this project
```

`actas <name>` is **exclusive across sessions**: it switches sending and receiving to the exact `<name>` for every locally registered team of the current runtime, claims the matching locks, and refuses if another session already holds any of them. `drop` removes the registration only from the current project and releases this session's locks for that name across all teams. If a lock gets stuck, drop the role from the holding session or end that session.

See [docs/actas.md](docs/actas.md) for the full mechanics — exclusivity model, recovery, liveness / PID recycling, Codex caveat.

### Spawn a new agent (`spawn`)

Where `actas` switches *this* session to a different role, `spawn` brings up a **separate agent process** that takes a role on boot — handy for fanning out collaborators.

```
/agmsg spawn codex reviewer            # new codex agent, joins and becomes "reviewer"
/agmsg spawn claude-code alice --window  # new claude-code agent in a fresh tmux window
/agmsg spawn codex reviewer --boot-prompt "review the diff on this branch"  # joins AND starts the task
```

`spawn <type> <name>` pre-joins `<name>`, then launches the target CLI with the actas slash command (`/<your-command> actas <name>`, matching your install command name) as its initial prompt. If the current session is inside **tmux**, it opens in a new pane (or `--window` for a new window, `--split h|v` for the direction); otherwise it opens a new **OS terminal** window.

Pass `--boot-prompt <text>` to hand the new agent an initial task: the boot prompt becomes the actas slash command followed (newline-separated) by your text, so the agent claims its identity **and** acts on the task in the same first turn. This is the only way to give a one-shot goal to a **codex** peer, which has no Monitor and so never notices a message you `send` after it goes idle.

By default `spawn` **blocks until the new agent is actually listening** — its watcher attaches and touches a readiness sentinel — then prints `status=ready`, so you can send work the moment `spawn` returns without losing it to the agent's cold start. Use `--no-wait` for fire-and-forget, or `--ready-timeout <secs>` to bound the wait (default 90; on timeout it prints `status=timeout` and exits 3 so a caller can re-spawn). Codex skips the wait (it has no Monitor).

Options: `--boot-prompt <text>` (initial task; see above), `--project <path>` (default: current project), `--team <team>` (auto-resolved when the project has a single team), and `--terminal <tmpl>` / `$AGMSG_TERMINAL` / config `spawn.terminal` to override the terminal command on the non-tmux path (a `{cmd}` placeholder is replaced with the path to the generated boot script). On macOS the default opens whichever terminal you're currently in (iTerm or Terminal, via `$TERM_PROGRAM`) using `open -a` — a plain app launch, so it does **not** trigger the Automation/AppleScript permission prompts that scripting the terminal directly would.

To always pass a given agent type extra CLI flags on spawn (e.g. a default permission mode or sandbox policy), set them in a YAML **spawn options** file — one section per type, a flat `--flag: value` map underneath. Path: `$AGMSG_SPAWN_OPTIONS_FILE`, else `~/.agmsg/config/spawn_options.yaml`; a missing file or section is a no-op. An optional `<type>@<role>` section appends its tokens after the base `<type>` tokens. `spawn` resolves the role from `--role <role>` first, then from the second-to-last `_`-separated component of a three-or-more-component agent name; if neither applies, it uses only the base section.

```yaml
claude-code:
  --permission-mode: acceptEdits
  --dangerously-skip-permissions: true   # a `true` value emits the flag with no argument

codex:
  --sandbox: workspace-write
  --dangerously-skip-permissions: false  # a `false` value suppresses the flag entirely

codex@architect:
  -p: architect
  -c: model_reasoning_effort=xhigh

codex@programmer:
  -p: programmer
  -c: model_reasoning_effort=medium
```

For example, `spawn codex project_architect_codex` applies the `codex@architect` overlay. An overlay key replaces the same key from the base section; an overlay value of `false` suppresses that key entirely. An explicit role must contain only letters, digits, `_`, or `-`; an unsafe value is rejected before spawn creates or joins anything.

To make role configuration fail closed for a type, add the separate metadata
section below. Missing metadata, a missing type key, or `false` preserves the
optional behavior. With `true`, `spawn` requires a role and the matching
`<type>@<role>` section before it resolves a team or pre-joins the agent; an
empty section is valid for types without a profile contract. Metadata is never
emitted as CLI argv.

```yaml
agmsg.require-role-overlay:
  claude-code: true
  codex: true
```

When `codex: true`, the role overlay must select exactly one profile with
`-p: alias`, `--profile: alias`, `-p=alias: true`, or `--profile=alias: true`.
The base `codex:` section must not contain a profile flag, including a
`false` value used to suppress it. The alias must start with a letter or digit
and contain only letters, digits, `.`, `_`, or `-`. `spawn` verifies the
readable file `$CODEX_HOME/<alias>.config.toml`; when `CODEX_HOME` is unset it
uses `~/.codex/`. The verified absolute directory is exported as `CODEX_HOME`
in the generated boot script. A missing, unreadable, duplicate, or malformed
profile fails before registration or terminal launch. Policy-disabled Codex
configurations keep the existing pass-through behavior.

Eight of the nine agent types are spawnable — `claude-code`, `codex`, `grok-build`, `cursor`, `gemini`, `antigravity`, `copilot`, `opencode`. `hermes` is not: its CLI has no mode that starts an interactive session pre-seeded with an initial prompt (#279). macOS is the primary target; Linux and Windows are best-effort (please open an issue/PR if your terminal isn't handled). Headless environments — no tmux **and** no usable terminal — error out, since the agent CLIs need an interactive terminal.

### Tear down a spawned agent (`despawn`)

`despawn` is the inverse of `spawn` — it cleanly tears down a member you brought up.

```
/agmsg despawn reviewer          # graceful: the member drops its role and closes its own pane
/agmsg despawn alice --force     # force: tear it down from here when its watcher can't respond
```

By default `despawn <name>` is **graceful**: it sends a `ctrl:despawn` control message to `<name>`, whose watcher drops its own role (releasing the actas lock and registration) and closes its own tmux pane — ending the agent. It blocks until the role is released, up to `--timeout <secs>` (default 30), then prints `status=ok`. If the member's watcher never responds it prints `status=timeout` and exits 3 — retry with `--force`.

`--force` skips the message and tears the member down from the placement recorded at spawn time: it kills the member's tmux pane/window and drops its registration. Use it when the member's watcher can't respond — a dead watcher, or a **codex** member (no Monitor, so graceful has nothing to act on). A member started by hand (no spawn placement record) can't be `--force`d; despawn says so and leaves it for you to close.

Despawn only acts on the named member — the session running `despawn` is never torn down, and a broad-subscription watcher ignores a `ctrl:despawn` aimed at another role.

### Bring a role back with its context (session resume)

A role remembers the session that last embodied it: sessions are named
`<team>-<agent>`, and `spawn` **resumes a role's previous session by default** —
so re-spawning after a `despawn`, crash, or restart comes back in the prior
conversation, not blank (`--fresh` forces new). With
[tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect), one `~/.tmux.conf`
line re-seats every role pane into its session after a tmux-server restart.

See **[docs/session-resurrect.md](docs/session-resurrect.md)** for the tmux-resurrect
setup, how it resolves each pane, what does and doesn't come back automatically, and
the manual fallback.

## Delivery modes

How incoming messages reach your agent. Pick one at first join via the prompt, or change it later with `/agmsg mode <name>`.

| mode | mechanism | latency | who it's for |
|---|---|---|---|
| **`monitor`** (default on Claude Code; on OpenCode with the opencode-sentinel plugin) | SessionStart hook → Monitor tool → blocking SQLite stream | ~5s | Claude Code users wanting real-time push |
| **`turn`** (default on Codex / Copilot CLI / OpenCode without the plugin) | Stop hook fires `check-inbox.sh` between assistant turns | until your next interaction | Codex / Copilot CLI / OpenCode users not running monitor; Claude Code users on a quieter loop |
| **`both`** | monitor primary, turn as per-session safety net | ~5s; falls back to turn-end on watcher failure | belt-and-suspenders |
| **`off`** | no automatic delivery | manual `/agmsg` only | minimalists |

### Picking a mode

```
/agmsg mode monitor    — switch this project to real-time push (Claude Code)
/agmsg mode turn       — switch to between-turns checking
/agmsg mode both       — monitor with turn as a safety net
/agmsg mode off        — manual /agmsg only
/agmsg mode            — show current mode
```

Settings are per-project. Each `<project>/.claude/settings.local.json` gets exactly the hooks the chosen mode needs — repeated `set` calls are idempotent.

`delivery.sh status` reports `mode: unknown (unrecognized: ...)` when the
settings file cannot be resolved, is missing, or is not valid JSON. `unknown`
is not the same as `off`: it means the configuration was not observable, so
automatic delivery is not started. Check the reported project path and either
restore the settings file or explicitly configure a mode with `/agmsg mode
<monitor|turn|both|off>`. A readable settings file with no agmsg hooks reports
`mode: off (no agmsg delivery hooks installed for this project)`.

**Monitor priming**: in `monitor` mode, the receiving agent doesn't react to its first inbound message until it has taken at least one turn this session. If you've just started a fresh session and a teammate has already sent something, nudge the agent with any short message ("hi") to prime it — subsequent messages stream in real time.

### Migrating from legacy `hook on/off`

`hook on` is now a thin alias for `mode turn` (with a one-line deprecation hint). To switch to real-time push:

```
/agmsg mode monitor
```

The command updates `db/config.yaml`, rewrites the project's hook entries, and prints an `AGMSG-DIRECTIVE` that activates `monitor` in the current session — no agent restart needed.

## Usage

### Claude Code

```
/agmsg                                  — check inbox (all teams)
/agmsg history                          — message history
/agmsg team                             — list team members
/agmsg send <agent> <message>           — send message
/agmsg mode <monitor|turn|both|off>     — switch delivery mode
/agmsg mode                             — show current mode
/agmsg actas <name>                     — switch to another role (same name across locally registered teams; create here if needed)
/agmsg drop <name>                      — remove a role from this project
/agmsg spawn <type> <name>              — launch a new agent (claude-code/codex) that takes <name>
/agmsg despawn <name> [--force]         — tear down a member you spawned (graceful, or --force)
/agmsg hook on | off                    — legacy aliases (mode turn | off)
/agmsg version                          — show the installed version (git-describe provenance)
/agmsg reset                            — clear current project registration
```

### Codex

```
$agmsg                          — or /skills → agmsg
```

Codex supports `mode monitor` through an app-server bridge, plus `mode turn` and `mode off`.

> ⚠️ **Monitor mode changes how Codex starts — enable it knowing that.** Codex has no Monitor tool, so `mode monitor` prints a shell function that makes `codex` route through agmsg's monitor shim in your interactive shell. In monitor-mode projects the shim routes interactive launches through a bridge that turns incoming agmsg messages into turns on the current Codex thread; `codex exec` and non-monitor projects pass straight through to the real Codex. It depends on Codex app-server behavior and has a known limitation (orphans on TUI close — #149).

If you prefer a global PATH shim, run `~/.agents/skills/<cmd>/scripts/drivers/types/codex/codex-shim-install.sh install` and put `~/.agents/bin` before the real Codex binary on PATH. You can also launch with `~/.agents/skills/<cmd>/scripts/drivers/types/codex/codex-monitor.sh`. Codex sandboxing must allow writes to the skill's `db/`, `teams/`, and `run/` dirs — `install.sh` configures those `writable_roots` when `~/.codex/config.toml` exists. Setup notes and internals: [docs/codex-monitor-beta.md](docs/codex-monitor-beta.md).

### GitHub Copilot CLI

```
/agmsg                          — invokes the agmsg skill
```

The Copilot installer drops a `SKILL.md` at `~/.copilot/skills/agmsg/` so `/agmsg` is auto-discovered. Per-project hooks live at `<project>/.github/hooks/agmsg.json`. Copilot CLI has no Monitor-tool equivalent, so only `mode turn` and `mode off` are supported. Asking for `monitor` or `both` is rejected with an error.

### OpenCode

```
$agmsg
```

Install with `./install.sh` (when `~/.config/opencode/` exists, the OpenCode-typed skill is placed automatically alongside the default Codex-typed shared skill). Use `--agent-type opencode` only for OpenCode-only environments where Codex is not installed. OpenCode supports `mode monitor` (via the external [`opencode-sentinel`](https://github.com/tsukimiya/opencode-sentinel) plugin; without it the rule instructs a fallback to turn mode, which the agent follows rather than agmsg enforcing it), `mode turn`, and `mode off`. `spawn opencode` is available via `opencode --prompt` (TUI mode, which stays resident after the boot prompt's turn). `both` is not supported.

This makes OpenCode useful as a local coding agent, including configurations backed by local providers such as Ollama.

See [docs/opencode.md](docs/opencode.md) for full setup instructions.

### Shell (any agent)

```bash
~/.agents/skills/<cmd>/scripts/send.sh <team> <from> <to> "<message>" [--force]
~/.agents/skills/<cmd>/scripts/inbox.sh <team> <agent_id>
~/.agents/skills/<cmd>/scripts/message-status.sh <team> <agent_id> [--format human|json]
~/.agents/skills/<cmd>/scripts/history.sh <team> [agent_id] [limit]
~/.agents/skills/<cmd>/scripts/join.sh <team> <agent_id> <type> <project_path> [--role <role>] [--kind <seat|human|service>] [--force]
~/.agents/skills/<cmd>/scripts/team.sh <team> [--format human|json]
~/.agents/skills/<cmd>/scripts/roster-normalize.sh <team> --check|--apply
~/.agents/skills/<cmd>/scripts/team-work.sh <validate|self-check|g4-audit|g4-bootstrap|g4-transition|observe|queue|audit|reconcile|watchdog|dispatch|dispatch-ack|claim|ack|renew|release|set-state|link-pr|writeback> <team> <contract-pack.json> ...
~/.agents/skills/<cmd>/scripts/whoami.sh <project_path> [type] [--format human|json]
~/.agents/skills/<cmd>/scripts/delivery.sh set <mode> <type> <project_path>
~/.agents/skills/<cmd>/scripts/delivery.sh status [<type> <project_path>] [--format human|json]
~/.agents/skills/<cmd>/scripts/reset.sh [--no-resolve] <project_path> <type> [agent_id] [session_id]
```

`send.sh` takes four positional arguments — `<team> <from> <to> "<message>"` — plus an optional `--force`. The flag may appear before, between, or after the positional arguments. Unknown options and extra arguments fail with a diagnostic; use `--` before a positional value that intentionally starts with `-`. Quote the message so the shell sees it as one argument; an unquoted message with spaces will be misparsed. Both `from` and `to` must already be registered in `<team>`; an unregistered name errors out (listing the currently registered names) instead of silently storing an undeliverable message. Pass `--force` to bypass this check for an intentional pre-registration send.

All four positional inputs must be valid UTF-8. `send.sh` checks them before loading storage or creating a database; malformed input exits non-zero, reports the field, 1-based byte position, offending byte in hexadecimal, and a repair instruction, and is not queued. `--force` bypasses roster membership only and cannot bypass this UTF-8 check. Fix the caller's string construction and retry the command.

### Machine-readable team roster

New teams created by `join.sh` have a versioned roster contract. Give a
member's role and kind explicitly when registering it:

```bash
~/.agents/skills/<cmd>/scripts/join.sh demo architect codex "$(pwd)" \
  --role architect --kind seat
~/.agents/skills/<cmd>/scripts/team.sh demo --format json
~/.agents/skills/<cmd>/scripts/whoami.sh "$(pwd)" codex --format json
```

`--kind` is one of `seat`, `human`, or `service`. If omitted, `join.sh`
records the explicit defaults `kind: seat` and `role: unassigned`; it never
derives either field from the agent name or runtime. A later `join.sh` with
`--role` or `--kind` updates that member's metadata.

The JSON commands return `schemaVersion: 1`, stable member records
(`name`, `kind`, `role`, and `registrations`), and for `whoami.sh` the lookup
`runtime`, resolved `session.project`, and exact matching registrations.
`whoami.sh` returns an empty `registrations` array when no member matches.
They report roster structure only: use `message-status.sh` or
`delivery.sh status` for delivery/liveness information.

Existing team configs remain usable through the human default output. JSON
mode does not guess missing fields: a legacy or incomplete matching config
returns exit code 2 with `schema error:` on stderr and no JSON on stdout.

#### Normalize a legacy roster

If a team's JSON roster predates the versioned contract and is missing only the
root `schemaVersion`, use the normalizer. It adds the integer `1` after the
complete candidate passes the same roster validator used by `team.sh`; it does
not infer or change member roles, kinds, or registrations.

Test against a disposable copy first. The copy must contain the installed
script tree and the target team's config at the matching paths:

```bash
skill="$HOME/.agents/skills/agmsg"
team=demo
scratch="$(mktemp -d "${TMPDIR:-/tmp}/agmsg-roster.XXXXXX")"
mkdir -p "$scratch/scripts" "$scratch/teams/$team"
cp -R "$skill/scripts/." "$scratch/scripts/"
cp "$skill/teams/$team/config.json" "$scratch/teams/$team/config.json"

bash "$scratch/scripts/roster-normalize.sh" "$team" --check
bash "$scratch/scripts/roster-normalize.sh" "$team" --apply
bash "$scratch/scripts/team.sh" "$team" --format json
```

When the copy reports `status: "ready"` without changing its config and then
reports `status: "applied"`, run the same apply command on the installed team:

```bash
bash "$skill/scripts/roster-normalize.sh" "$team" --apply
bash "$skill/scripts/team.sh" "$team" --format json
```

`--check` never writes the config or acquires the registry lock. `--apply`
re-reads under the per-team lock and publishes the validated candidate with an
atomic replacement. A current roster returns
`status: "already_current"`; invalid JSON or incomplete member metadata exits
with code `2`, prints a `schema error:` on stderr, emits no JSON on stdout, and
leaves the config unchanged. If the error names a missing role, kind, or
registration field, update that member through `join.sh` with explicit
`--role` and `--kind` (and `--force` only when the existing registration is
intentionally being updated); do not hand-edit `config.json`. Remove the
disposable copy after verification if it contains sensitive roster data.

### Delivery capability JSON

Use the JSON status command before automatically assigning work to a seat:

```bash
~/.agents/skills/<cmd>/scripts/delivery.sh status codex "$(pwd)" --format json
~/.agents/skills/<cmd>/scripts/delivery.sh status claude-code "$(pwd)" --format json
```

The result has `schemaVersion: 1`, the requested `type` and `project`, an
aggregate `runtime`, `liveness`, `sessionId`, `deliverable`, receipt counts,
evidence, and one record per registered role in `seats`. A consumer may
dispatch to a role only when its `deliverable` is the JSON boolean `true`.

- `true` means agmsg observed a live, role-bound receiver.
- `false` means delivery is off, missing, or stale with a concrete reason.
- `"unknown"` means the runtime cannot prove a current receiver; treat it as
  non-dispatchable until it becomes `true`.

For Claude Code, `true` requires a live exclusive `watch.sh` watcher for that
role, not merely a SessionStart hook. For Codex, it requires a live bridge with
matching metadata and the role's recorded Codex session. Each Codex seat also
reports `seats[].paneLiveness` as `"live"`, `"crashed"`, or `"unknown"`, based
on the recorded herdr placement. A `"crashed"` pane vetoes that seat's
`deliverable`; `"live"` and `"unknown"` preserve the bridge result, so an
unreadable or still-starting pane is not treated as a crash. The aggregate
object has no `paneLiveness` field, and pane text is never emitted in JSON
evidence. Other runtimes report `"unknown"` when agmsg has no type-specific
liveness probe; configuration alone never makes them dispatchable.

The nested `receipt` records queued, claimed, handed-off, and legacy-unknown
messages. `handedOff` acknowledges delivery to the receiver only. It does not
mean the role finished the task, closed an Issue, or merged a PR.
### Read-only work-state contract check

`validate` and `self-check` validate a versioned work-state contract pack. They
require `node` on `PATH`, read the selected team's `team.sh --format json`
roster, and never modify the pack, team config, GitHub, messages, leases, or
agents.

```bash
~/.agents/skills/<cmd>/scripts/team-work.sh validate demo .team-work.json
~/.agents/skills/<cmd>/scripts/team-work.sh self-check demo .team-work.json
```

The pack is JSON with `schemaVersion: 1`, the exact `team`, and a non-empty
`workItems` array. Each item requires a unique `workItem.id`, an issue source,
an `ownerSeat`, one or more `workKinds`, a `revision`, a
`classificationBasis`, and boolean `writebackRequired`. `ownerSeat` must be an
existing roster member with `kind: "seat"`; human and service identities are
not valid owners. Supported work kinds are `implementation`, `writeback`,
`inventory`, `closeout`, and `reconciliation`.

```json
{
  "schemaVersion": 1,
  "team": "demo",
  "workItems": [{
    "schemaVersion": 1,
    "workItem": {
      "id": "issue:40",
      "source": {"kind": "issue", "repository": "kappaseijin/agmsg", "number": 40}
    },
    "ownerSeat": "architect",
    "workKinds": ["implementation"],
    "relations": [{
      "kind": "pull_request",
      "repository": "kappaseijin/agmsg",
      "number": 46,
      "relation": "contributes"
    }],
    "revision": 1,
    "classificationBasis": {
      "contentDigest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "refs": [{"kind": "issue", "repository": "kappaseijin/agmsg", "number": 40}]
    },
    "writebackRequired": false
  }]
}
```

`relations` may be empty before a PR exists. A `contributes` PR relation needs
its repository and positive number. A `closes` relation additionally needs a
`closingIssue` object with the same repository and number as `workItem.source`;
otherwise validation fails. `classificationBasis` needs a lowercase
`sha256:` digest and at least one issue, pull request, commit, or evidence
reference.

`validate` prints compact JSON such as
`{"schemaVersion":1,"valid":true,"team":"demo","workItemCount":1}`.
`self-check` adds a canonical JSON representation plus `contractDigest` and
per-item `envelopeDigest` values. Object key order and whitespace do not change
these SHA-256 digests; array order remains significant. Invalid packs return
exit code 2 with `schema error:` on stderr. Usage errors, a missing pack, a
missing Node runtime, or an unavailable roster return nonzero without treating
the contract as empty or valid.

### Work-item leases and local state

After a pack validates, `team-work.sh` can record an explicit local work-item
lease and its append-only revision chain. Mutating commands require both
`node` and `sqlite3` on `PATH`. They initialize the same local store used by
agmsg messages: `$AGMSG_STORAGE_PATH/messages.db` when
`AGMSG_STORAGE_PATH` is set, otherwise the skill's `db/messages.db`.

```bash
# The declared owner obtains a 300-second lease (the default TTL is 300).
~/.agents/skills/<cmd>/scripts/team-work.sh claim demo .team-work.json issue:41 architect 300

# The active lease holder records delivery acknowledgement, renews, then releases.
~/.agents/skills/<cmd>/scripts/team-work.sh ack demo .team-work.json issue:41 architect received
~/.agents/skills/<cmd>/scripts/team-work.sh renew demo .team-work.json issue:41 architect 600
~/.agents/skills/<cmd>/scripts/team-work.sh release demo .team-work.json issue:41 architect

# An exact roster manager may update local work state and references.
~/.agents/skills/<cmd>/scripts/team-work.sh set-state demo .team-work.json issue:41 manager in_progress
~/.agents/skills/<cmd>/scripts/team-work.sh link-pr demo .team-work.json issue:41 manager kappaseijin/agmsg 47 contributes
~/.agents/skills/<cmd>/scripts/team-work.sh writeback demo .team-work.json issue:41 manager "merged PR verified locally"
```

| Command | Authorized caller | Result |
| --- | --- | --- |
| `claim` | Declared owner seat or exact `kind: seat`, `role: manager` member | Takes an absent or expired lease. A manager claim makes that manager the lease holder. |
| `ack`, `renew`, `release` | The exact, unexpired lease holder | Acknowledges with evidence, extends the TTL, or clears the lease. |
| `set-state`, `link-pr`, `writeback` | The active holder, or an exact manager seat | Appends a local state, PR-link, or writeback revision. |

Authorization comes only from `team.sh --format json`: names that merely look
like managers, and `human` or `service` members, are rejected. An active owner
cannot have its ACK, renewal, or release performed by a manager or another
seat. A manager may record state, PR-link, and writeback changes without taking
the owner's live lease. `ttl-seconds` is a non-negative integer; zero is useful
only for deterministic expiry tests. An expired lease cannot be renewed or
released; an authorized owner or manager must claim it again.

`set-state` accepts `acknowledged`, `in_progress`, `blocked`, or `completed`;
`claim` itself creates `claimed`. `link-pr` accepts a positive PR number and
one unique `contributes` or `closes` relation. A `closes` relation must use the
same repository as the work item's issue source. `writeback` and `link-pr`
record local evidence only: they do not query GitHub, write a GitHub comment,
close an Issue, or merge a PR.

Each successful mutation returns compact JSON with `team`, `workItemId`,
`revision`, `state`, `leaseOwner`, `leaseExpiresAt`, `envelopeDigest`, and
`lastAction`. The current snapshot is stored in `team_work_current`; SQLite
triggers append the resulting immutable JSON snapshot to
`team_work_revisions` in the same transaction. A changed contract or envelope
digest, a duplicate PR relation, an unauthorized caller, or a competing live
lease fails closed with exit code 2 and leaves both tables unchanged. These
commands do not modify the contract pack, team configuration, `messages`,
`message_claims`, or `message_receipts`.

### GitHub live audit and work queue

Use these commands to compare the pack and its local lease rows with live
GitHub Issue / PR state. They require `node` and use authenticated `gh` plus
`sqlite3` when those live sources are available on `PATH`; an unavailable live
source becomes an `unknown` result. They idempotently ensure the local
store's schema exists -- a team's first-ever `observe`/`queue`/`audit` call
sees a genuinely empty store and classifies packed work normally rather than
reporting `unknown` for lack of a store -- but never write a lease, dispatch,
or message row.

```bash
# See every packed item's observed GitHub/local state.
~/.agents/skills/<cmd>/scripts/team-work.sh observe demo .team-work.json

# Emit only work that is currently ready to claim.
~/.agents/skills/<cmd>/scripts/team-work.sh queue demo .team-work.json

# Include closing-relation checks and every safety violation.
~/.agents/skills/<cmd>/scripts/team-work.sh audit demo .team-work.json
```

The command fetches each packed source Issue and related PR with GitHub
GraphQL. Both `Issue.closedByPullRequestsReferences` and
`PullRequest.closingIssuesReferences` are retrieved through every page. A
`closes` relation must occur on both sides; a `contributes` relation must not
close the source Issue. Pack relations and local `link-pr` records are both
checked. A missing page, API error, unknown relation, one-sided closing
relation, or a local row whose contract/envelope digest no longer matches the
pack is never treated as "no work".

Every successful observation is one canonical JSON object. It includes
`contractDigest`, `sourceDigest`, and `auditDigest`, plus this
`classificationBasis`:

| Status | Meaning | `queue.ready` |
| --- | --- | --- |
| `ready` | One or more packed source Issues are open and have no live matching lease. | Those work items, in pack order. |
| `fully_allocated` | Open packed Issues all have matching, unexpired local leases or dispatch ledger entries. | Empty. |
| `quiescent` | Every packed source Issue is closed and its checked relations are complete. | Empty. |
| `unknown` | Evidence is unavailable, incomplete, contradictory, or locally stale. | Empty; do not dispatch from this result. |

`classificationBasis` also records `readyCount`, `openItemCount`,
`allocatedItemCount`, `closedItemCount`, a stable `reasons` array, and the
same `sourceDigest`. `observe` returns all item summaries; `queue` adds the
ready list; `audit` adds `relationChecks` and `violations`. The output uses
recursively sorted object keys, so its digests and exact JSON are stable for
the same pack, live responses, and local state.

Each item summary exposes the local row's workflow state as the additive
`localState.workflowState` field; it is `null` when no local row exists. An open
item whose workflow state is `blocked` is withheld from `queue.ready` and from
reconcile/dispatch ready selection. If that leaves no ready item, the
classification is `unknown` with the stable `blocked_work_item` reason in
`classificationBasis.reasons`; this condition is not copied into
`audit.violations`. If another unleased item remains, the classification stays
`ready` and only that other item appears in the ready list. An active blocked
lease remains `fully_allocated`, while a row returned to `acknowledged` or
`in_progress` can become ready again after its lease expires.

Malformed packs, invalid command syntax, a missing Node runtime, or an
unavailable roster remain nonzero errors. In contrast, a live GitHub/local
source failure returns a valid `unknown` result so a reconciler can record the
reason while safely withholding work. These commands never create or update a
GitHub Issue/PR, update a lease, send a message, spawn an agent, or decide a
remediation action. Their work universe is the supplied pack only; they do not
discover unlisted repository Issues.

### G4 state/coverage audit (Phase 1A)

`g4-audit` is the read-only Phase 1A implementation of the G4 state/coverage
contract. It is a separate input from the work-item contract pack above and
audits only the GitHub scopes explicitly declared in that input:

```bash
~/.agents/skills/<cmd>/scripts/team-work.sh g4-audit demo .g4-state-pack.json
```

The pack is JSON with `schemaVersion: 1`, the exact `team`, at least one
`scopes` declaration, and an `entries` array (which may be empty only when the
declared scopes return no matching open Issues). A scope has exactly these
fields:

```json
{
  "id": "demo-open-issues",
  "repository": "kappaseijin/example",
  "issueState": "OPEN",
  "labelsAll": [],
  "basis": {
    "contentDigest": "sha256:<64 lowercase hex>",
    "refs": [{"kind": "git", "repository": "kappaseijin/example", "commit": "<immutable commit>"}]
  }
}
```

`repository` is an explicit `owner/name`; `issueState` is currently only
`OPEN`; and `labelsAll` is a unique list of labels that every returned Issue
must contain. `basis.refs` is non-empty and must identify immutable evidence
(`git`, `github_issue`, `github_pull_request`, `commit`, or `evidence`). The
scope `basis.contentDigest` is the SHA-256 digest of the recursively
canonicalized scope object with `basis` removed.

Each entry maps one covered Issue exactly once:

```json
{
  "schemaVersion": 1,
  "source": {"repository": "kappaseijin/example", "number": 42},
  "state": "ready",
  "ownerSeat": "demo_programmer_codex",
  "workKinds": ["implementation"],
  "basis": {
    "contentDigest": "sha256:<64 lowercase hex>",
    "refs": [{"kind": "github_issue", "repository": "kappaseijin/example", "number": 42}]
  },
  "revision": 1,
  "entryDigest": "sha256:<64 lowercase hex>"
}
```

`ownerSeat` must be an exact roster member with `kind: "seat"`. Supported
`workKinds` are `implementation`, `writeback`, `inventory`, `closeout`, and
`reconciliation`. Entry `state` is only `ready`, `blocked`, or `unknown`;
`quiescent` is an aggregate audit result, never an entry state. A `ready`
entry has no `blocker`. A `blocked` entry requires a stable lower-snake-case
`reasonCode` and one v1 `releasePredicate`:

```json
"blocker": {
  "reasonCode": "upstream_issue",
  "releasePredicate": {
    "kind": "issue_closed",
    "repository": "kappaseijin/example",
    "number": 41
  }
}
```

The other predicate kinds are `pull_request_merged`, `review_approved` (with
`headOid`), `not_before` (JST RFC3339 time with `+09:00`),
`issue_comment_digest` (with `commentId` and a SHA-256 `contentDigest`), and
`all_of` (a non-empty list of predicates). An `unknown` entry cannot carry a
blocker and is never a claim or dispatch basis. `entryDigest`, when present,
is the canonical SHA-256 of the entry with only `entryDigest` removed. The
entry basis digest is calculated with both `basis` and `entryDigest` removed.
Object keys are sorted recursively for all digests; array order remains
significant.

The audit executes an explicit read-only GraphQL query for each scope, follows
every page, filters `labelsAll`, de-duplicates scope overlap, and compares the
resulting sorted `(repository, number)` set with `entries`. The result includes
`packDigest`, `coverageDigest`, `sourceDigest`, `auditDigest`, `scopeAudits`,
`coverage`, `entries`, `ready`, and `classificationBasis`:

| Status | Meaning | `ready` |
| --- | --- | --- |
| `complete` | Live scope reads completed and entry coverage matches exactly. | Valid `ready` entries only. |
| `quiescent` | All declared scopes returned no matching open Issues and `entries` is empty. | Empty. |
| `unknown` | A source/pagination error, duplicate, coverage mismatch, unknown entry, or unresolved blocker predicate prevents a safe result. | Empty; do not claim or dispatch. |

`classificationBasis` also reports `scopeCount`, `coverageCount`,
`entryCount`, `readyCount`, `blockedCount`, `unknownCount`, `coverageDigest`,
and a stable `reasons` array. A false blocker predicate remains a blocked
entry but makes the aggregate audit `unknown`; a true predicate is recorded as
a fresh observation and does not itself change the entry.

Malformed packs return exit code `2` with `schema error:`. GitHub transport,
GraphQL, pagination, or coverage failures return a valid `unknown` JSON result
with exit code `0`, so callers can fail closed without confusing source
failure with an invalid pack.

Phase 1A `g4-audit` does not initialize or mutate SQLite, create a G4 ledger,
bootstrap or transition state, pull work, dispatch messages, change GitHub
labels, or call any GitHub write method. `g4-pull` remains a Phase 2 command and
is not published here. The Phase 1B commands below require the #97 recovery
work and the #98 roster/delivery acceptance gates to be live.

### G4 bootstrap ledger (Phase 1B)

After those gates are accepted, an exact roster member with `kind: "seat"` and
`role: "manager"` or `role: "pm"` can record the first local snapshot for
every Issue in a fresh, complete audit:

```bash
~/.agents/skills/<cmd>/scripts/team-work.sh g4-bootstrap demo \
  .g4-state-pack.json demo_manager https://example.test/g4-bootstrap
```

The command accepts exactly the team, G4 state pack, manager seat, and a
non-empty evidence string shown above. It re-reads every declared GitHub scope
and succeeds only when `classificationBasis.status` is `complete`, the live
coverage matches the pack exactly, and each entry is an initial `revision: 1`.
The manager seat must be the exact roster identity with `kind: "seat"` and
`role: "manager"` or `role: "pm"`; an owner seat, human, service, missing, or
any identity with a different kind or role is rejected before the live read.

Bootstrap is initial-only and all-or-nothing. It writes each source to the
team-local SQLite `team_work_g4_current` table and relies on its append-only
`team_work_g4_revisions` trigger to record revision 1. If any source already
has a current row, the entire operation is rejected and no source is partially
inserted. Retry after refreshing the pack and audit; a repeated bootstrap
returns `bootstrapped: false` with remediation code `already_bootstrapped`.
Unknown, incomplete, mismatched, or unavailable audit evidence returns
`bootstrapped: false` with remediation code `audit_incomplete`.

Successful output is canonical JSON containing `schemaVersion`, `command`,
`team`, `managerSeat`, `evidence`, `packDigest`, `coverageDigest`,
`auditDigest`, sorted `sources`, `revision: 1`, and `bootstrapped: true`.
Rejected operational results retain the identity fields, return an empty
`sources` array and `bootstrapped: false`, and provide a stable
`remediation[0].code`. The SQLite ledger is the only mutation target: this
command never writes GitHub, changes labels, sends messages, or dispatches
work. When the command returns a JSON result with one or more remediation
items, it exits with code `1`; a successful `bootstrapped: true` result exits
with code `0`. Malformed input and usage/schema errors still exit with code
`2` and write `schema error:` to stderr.

### G4 blocked-to-ready transition (Phase 1B)

After a source has been bootstrapped, an exact roster seat with `kind: "seat"`
and `role: "manager"` or `role: "pm"` can record only the next expected
revision when the saved blocker predicate is freshly true:

```bash
~/.agents/skills/<cmd>/scripts/team-work.sh g4-transition demo \
  .g4-state-pack.json kappaseijin/example 42 1 demo_manager \
  https://example.test/g4-transition
```

The command accepts exactly the team, G4 state pack, source repository, Issue
number, expected current revision, manager seat, and non-empty evidence. The
submitted entry must be `revision: expected-revision + 1`, must change exactly
from `blocked` to `ready`, and must preserve the source, owner seat, work kinds,
and immutable basis references. The current local row must still be the
expected revision and blocked; stale, skipped, unsupported, or immutable-field
changes are rejected without mutation.

Each transition performs a fresh complete scope audit and directly
re-evaluates the release predicate saved in the current blocked row. A false,
unknown, unavailable, or incomplete observation is fail-closed. A successful
transition updates exactly one `team_work_g4_current` row in a local
`BEGIN IMMEDIATE` transaction; the append-only trigger records the next
revision. The command never writes GitHub, changes labels, creates claims,
dispatches work, or invokes `g4-pull`. Retry with a refreshed pack and the
current revision after a rejection; rejected results retain the identity and
digest fields with `transitioned: false` and a stable
`remediation[0].code`. A JSON result with remediation items exits with code
`1`, while a successful `transitioned: true` result exits with code `0`.
Malformed input and usage/schema errors remain exit code `2` with
`schema error:` on stderr. This is distinct from `g4-audit`, whose valid `unknown`
JSON result exits with code `0`.

### PM-absent pull workflow

Use this workflow when a team does not want a manager to be the only person
who can start already-assigned work. It is an operating procedure over two
explicit packs: the G4 state pack records state and coverage; the work-item
contract pack supplies `queue` and the lease commands. They must name the
same source Issue and declared `ownerSeat`, but `g4-bootstrap` does not make
`queue` read the G4 ledger automatically.

Before rolling the procedure out to any team, verify that its own roster is
schema v1 and that the manager and owner are exact `kind: "seat"` members. A
multi-team rollout repeats this check and keeps one roster, state pack,
work-item pack, and local store per team; it does not share a claim across
teams.

```bash
# Read the target team's public roster before preparing its packs.
~/.agents/skills/<cmd>/scripts/team.sh demo --format json

# An exact manager creates the initial all-or-nothing G4 snapshot once the
# G4 audit is complete. The evidence string is an operator-owned record.
~/.agents/skills/<cmd>/scripts/team-work.sh g4-bootstrap demo \
  .g4-state-pack.json demo_manager 'https://example.test/g4-bootstrap'

# The declared owner reads the ordinary work queue, then claims only its own
# ready item. The owner records the lease acknowledgement before starting.
~/.agents/skills/<cmd>/scripts/team-work.sh queue demo .team-work.json
~/.agents/skills/<cmd>/scripts/team-work.sh claim demo .team-work.json \
  issue:42 demo_programmer_codex 300
~/.agents/skills/<cmd>/scripts/team-work.sh ack demo .team-work.json \
  issue:42 demo_programmer_codex received
```

The `claim` command also permits an exact manager by its existing authority,
but the pull procedure deliberately has the declared owner make this claim.
That keeps work moving when no manager is dispatching. It is not an
implementation of `g4-pull`: that exact-owner-only command and any pull-only
pilot are Phase 2 work and are not published by agmsg.

Do not turn a blocked G4 item ready by claiming it or changing a GitHub label.
The exact manager must submit the next pack revision through `g4-transition`;
the command requires the saved release predicate to be freshly true and
rejects an old revision or incomplete audit.

```bash
# The submitted entry for issue 42 must be revision 2 and ready; `1` is the
# expected current ledger revision. The manager supplies its own evidence.
~/.agents/skills/<cmd>/scripts/team-work.sh g4-transition demo \
  .g4-state-pack.json kappaseijin/example 42 1 demo_manager \
  'https://example.test/g4-transition'
```

Run the existing reconciler and watchdog independently. If `reconcile` reports
`orphan_ready`, the ready item's owner has no single live, deliverable roster
registration. Treat that as the PM/owner liveness alarm: restore or explicitly
reassign the owner through the normal team procedure, rather than spawning a
closed role or silently claiming on its behalf. Neither command creates a
claim or starts an agent.

```bash
~/.agents/skills/<cmd>/scripts/team-work.sh reconcile demo .team-work.json \
  /tmp/demo-reconciler-heartbeat.json
~/.agents/skills/<cmd>/scripts/team-work.sh watchdog demo .team-work.json \
  /tmp/demo-reconciler-heartbeat.json 900
```

When a manager is actively directing a handoff, use the existing
`dispatch` then `dispatch-ack` path below instead of this owner pull path. Do
not combine both paths for the same lease epoch.

### Reconciler, watchdog, and dispatch gate

`reconcile` and `watchdog` run independently of an interactive agent command.
They consume the same contract pack, roster, live GitHub audit, local lease
state, and delivery-capability JSON as the commands above. They never call
`send.sh`, `spawn.sh`, or herdr, and never mutate GitHub. A closed or non-live
role is reported for remediation; it is not started automatically.

```bash
# Emit findings and remediation. Without the optional path this is read-only.
~/.agents/skills/<cmd>/scripts/team-work.sh reconcile demo .team-work.json

# Atomically replace only this requested heartbeat file after one reconcile run.
~/.agents/skills/<cmd>/scripts/team-work.sh reconcile demo .team-work.json \
  /tmp/demo-reconciler-heartbeat.json

# Read a heartbeat from a separate process. The default stale limit is 900 seconds.
~/.agents/skills/<cmd>/scripts/team-work.sh watchdog demo .team-work.json \
  /tmp/demo-reconciler-heartbeat.json 900
```

`reconcile` returns canonical JSON with `result` (`healthy` or `attention`),
`findings`, `remediation`, the G2 `sourceDigest`/`auditDigest`, and a
`reconcileDigest`. It detects `expired_lease`, `upstream_closed`,
`orphan_ready`, `writeback_required`, and `stale_state` independently.
`orphan_ready` means the audit already classified the item as `ready` (see the
table above -- an open source with no live matching lease) but its packed
`ownerSeat` does not currently resolve to exactly one live, deliverable
roster registration. This is the PM/owner liveness signal: it fires exactly
as designed against an isolated or test roster with no matching registered
seat, not only against a real owner who has stopped running, so seeing it in
a sandboxed dry run is expected and is not evidence of a lease or dispatch
defect.
It changes no SQLite state unless an explicit heartbeat path is supplied; the
parent directory must already exist. The heartbeat records `cycleId`,
`startedAt`, `finishedAt`, `result`, and `sourceDigest` in canonical JSON.

`watchdog` only reads that heartbeat. It returns canonical JSON with status
`healthy`, `stale`, or `unknown`, plus `alarm`. A fresh quiescent reconcile is
healthy and does not produce an alarm. A missing, malformed, future-dated, or
old heartbeat is never treated as quiescent.

Dispatch is deliberately a two-stage local state transition. First define an
explicit JSON allowlist of existing seat names, then create a `dispatching`
entry. The default ACK deadline is 120 seconds.

```bash
export TEAM_WORK_DISPATCH_ALLOWLIST='["demo_programmer_codex"]'

# Only an exact roster manager may create a dispatching entry.
~/.agents/skills/<cmd>/scripts/team-work.sh dispatch demo .team-work.json \
  issue:42 demo_manager_codex 120

# The declared owner must ACK the exact, unexpired epoch before starting work.
~/.agents/skills/<cmd>/scripts/team-work.sh dispatch-ack demo .team-work.json \
  issue:42 demo_programmer_codex '<lease-epoch-from-dispatch>' received

# An exact manager can explicitly close an expired dispatch epoch.
~/.agents/skills/<cmd>/scripts/team-work.sh dispatch-abandon demo .team-work.json \
  issue:42 demo_manager_codex '<expired-lease-epoch>' timeout-recovery
```

`dispatch` requires all of the following: the packed Issue is currently
`ready`; the target is its exact `kind: "seat"` owner; that owner is in
`TEAM_WORK_DISPATCH_ALLOWLIST`; and exactly one of its registered delivery
runtimes reports both `deliverable: true` and `liveness: "alive"`. `false`,
`"unknown"`, ambiguous registrations, stale runtime evidence, a missing
allowlist, or an active lease produce `state: "not_dispatchable"` with
remediation and no ledger write. A caller that is not an exact manager seat is
rejected with a schema error before any ledger operation.

On success, `dispatch` writes an append-only local dispatch ledger containing
the queue digest, lease epoch/deadline, and canonical delivery evidence. Its
JSON reports `state: "dispatching"` and `sendInvoked: false`: queuing or
sending a message is not task ownership. It does not create a G2 work-item
lease yet.

`dispatch-ack` requires the declared owner, the same lease epoch, an unexpired
dispatch, an open and complete source audit, and fresh live delivery evidence.
Only then does one SQLite transaction change the dispatch ledger to `claimed`
and create the corresponding G2 `team_work_current` lease. A wrong, late, or
unavailable ACK returns `acknowledged: false` and leaves both ledgers unchanged.
`dispatch-abandon` is a manager-only recovery command: the manager, pack
digests, declared owner, exact epoch, and an expired (`lease_expires_at <= now`)
row must match in one guarded transaction, and its evidence must be non-empty.
It returns `abandoned: true`, stores the evidence in `recovery_evidence`, and
keeps the current row and append-only revision history; an unexpired G2 claim
causes a no-mutation refusal. `abandoned` is terminal evidence, not an active
allocation. A later `dispatch` may replace only an expired `abandoned` row with
a new epoch (`last_action: "dispatch-replace"`); expired `dispatching` or
`claimed` rows must be abandoned first. An ACK for the old epoch returns
`acknowledged: false` with `dispatch_epoch_invalid` and creates no claim.

The first team-work initialization after upgrade automatically migrates the
legacy dispatch tables transactionally. It validates state, revision chain, and
JSON before copying; a copy failure rolls back the legacy schema. Fresh and
already-migrated stores are idempotent.

Neither command creates a GitHub mutation, sends a message, operates a herdr
pane, or spawns an agent. Production dispatch remains disabled until the #98
roster/delivery gate and the Phase 3 prerequisite are satisfied.

### Message delivery state

`send.sh` prints `Queued message #<opaque-id> … delivery not yet acknowledged.` The identifier is storage-driver-defined and is not necessarily numeric. That is intentionally not a delivery-success claim. Inspect one receiver's state with either the human default or JSON for automation:

```bash
~/.agents/skills/<cmd>/scripts/message-status.sh myteam alice
~/.agents/skills/<cmd>/scripts/message-status.sh myteam alice --format json
```

`send.sh`'s own output names the exact follow-up command for the message it just queued, so you don't have to eyeball aggregate counts or scan `history.sh` for it:

```bash
~/.agents/skills/<cmd>/scripts/message-status.sh myteam alice --id <the-id-send.sh-printed>
```

| State | Meaning |
| --- | --- |
| `queued` | No receiver currently holds the message. |
| `claimed` | A receiver has a short exclusive lease while preparing host handoff. |
| `handedOff` | A durable receipt records host handoff. This is **not** LLM task completion. |
| `unknown` | A legacy row has `read_at` but no receipt, so handoff cannot be proven. |
| `notFound` (`--id` only) | No message with that id exists for this team/agent — check the id, team, and recipient you passed. |

`history.sh` uses the same distinction: `●` queued, `○` receiver handoff acknowledged, and `?` legacy/unknown receipt. If a receiver exits before acknowledging, its lease expires and the message remains eligible for another receive attempt.

`reset.sh` normally resolves `<project_path>` to the registered project associated with the current session or an ancestor/worktree. When the argument is already the exact stored path (for example, while cleaning up an orphaned registration), pass `--no-resolve`; the flag may appear before, between, or after the positional arguments. If no registration is removed, the command prints both the searched path and the argument path, and suggests `--no-resolve` when they differ.

### Read-only JSON API

External tools should read agmsg through `api.sh`, not through `db/` or
`teams/` files:

```bash
~/.agents/skills/<cmd>/scripts/api.sh get teams <team> members
~/.agents/skills/<cmd>/scripts/api.sh get teams <team> registrations
~/.agents/skills/<cmd>/scripts/api.sh get teams <team> messages
```

The `registrations` resource emits one JSONL object per registration, so each
agent/type/project pair stays associated:

```json
{"team":"agsuite","agent":"alice","type":"claude-code","project":"/work/agmsg"}
```

The existing `members` resource remains unchanged for compatibility; it is an
agent-level aggregate and may return only one project.

## FAQ / Design notes

**Is this MCP? Do I need an MCP server?**

No. agmsg is standalone — `bash` + `sqlite3`, no server, no daemon, no network. The two stacks are orthogonal: you can run agmsg alongside any MCP setup you already have.

**Concurrent writes to the same channel — do they conflict?**

The store is SQLite in WAL mode. Multiple readers and a single writer coexist; writes are short and serialized at the file level. In practice, two agents sending into the same team don't collide.

**Does SQLite guarantee turn order? Is there a lock or token?**

SQLite guarantees the ordering of the log itself — every row has a monotonic id and timestamp. Turn-taking between agents is a protocol-level concern, not enforced by the transport. The floor is intentionally dumb; the protocol lives in your prompts.

**Two Claude Code instances grab the same task — claim/lock?**

Receiver delivery uses a short message lease, so competing adapters do not both hand the same message to a host at once. This is deliberately narrower than task ownership: a handoff receipt does not assign the requested work or prove it completed. Use `actas` to keep two sessions from holding the same role, and put task ownership / completion rules in your team protocol.

**Runaway loops — where does the stop condition live?**

At the protocol/prompt level, not the transport. Common pattern: include a max-turns or explicit done-signal instruction in the kickoff prompt ("stop after N exchanges", "reply DONE when complete"). agmsg won't cut a conversation off for you.

**What's carried on a handoff — context, diffs, or just text?**

Plain text. Messages are short — a sentence, a request, a path. Agents pass *summaries and references* (file paths, commit SHAs, issue numbers), not raw context. Transport is the message; semantic packing is up to the prompt.

**What if the output exceeds the receiver's context window?**

Use the summary + file-reference pattern: write the artifact to disk, send a one-line pointer. The DB stores messages, not files.

**Does it hold up with more than 2 agents?**

Yes. Teams are N-agent. The demo is 2 for clarity; larger rooms work the same way — we run our own 8-agent team on it.

**Does context persist across sessions?**

Yes. Messages live in SQLite and survive sessions. `history.sh <team>` replays the room.

**Can I re-seed a fresh agent from an old room?**

The message store is effectively a replay log. There's no one-shot "rehydrate from room X" command yet, but `history.sh` gives you the transcript and you can prompt a new agent with it. Treat persistence as the unlock that makes that possible.

**A receiving session isn't picking up messages — is there another way to reach it?**

No, and that's by design: agmsg has exactly one delivery path, the SQLite queue plus whichever delivery mode (`monitor`/`turn`/`both`) is configured for the receiving session — there's no daemon or side channel that can push text into a specific terminal/pane for you (see "Is this MCP?" above; the same "no server, no daemon" boundary applies here). A terminal multiplexer's own text-injection commands, where available, are outside agmsg's scope and are not guaranteed to reliably submit a full turn to every CLI.

The message itself is never lost — it stays queued in the store regardless of whether the receiving session is currently consuming it — so the fix is getting that session to consume, not finding an alternate transport:

1. Check the session actually has a delivery mode configured: `/agmsg mode` (or `message-status.sh`/`delivery.sh status` from the shell) — `off` means nothing will arrive until `/agmsg` is run manually. This is the most common cause: a session that joined via a path other than `/agmsg actas <name>` (a script calling `actas-claim.sh` directly, for instance) can end up with no delivery hook installed at all.
2. In `monitor` mode, remember Monitor priming (above): a session that hasn't taken a turn yet this session won't react to what's already queued — nudge it with any short message.
3. If the session's process itself is gone, there's nothing left to nudge — restart/respawn it. The undelivered messages are still in the queue and `history.sh`/`inbox.sh` will hand them to the new session.

**How do you pronounce it?**

"AG message" (ay-jee message), or spelled out as A-G-M-S-G. Either is fine.

## Update

```bash
cd agmsg
git pull
./install.sh --update
```

DB and team configs are preserved. Only scripts and assets are updated.

The script tree is staged and moved into place by rename, so an already-running
watcher keeps reading the old file it has open instead of reading a partially
overwritten file. Restart running agent sessions after the update to use the new
scripts. If the staging move cannot be performed safely, the update fails
loudly; it does not fall back to an in-place overwrite. The no-interruption
guarantee is verified on macOS/Linux; Windows/MSYS2 behavior remains an
explicitly unverified edge case.

## Uninstall

An `uninstall.sh` copy ships inside every install, so this works whether you
installed via `git clone`, `npx agmsg`, or the curl one-liner:

```bash
~/.agents/skills/agmsg/uninstall.sh              # Interactive (confirms each step)
~/.agents/skills/agmsg/uninstall.sh --yes        # Remove everything
~/.agents/skills/agmsg/uninstall.sh --keep-data  # Remove skill but keep DB and teams
```

(If you have a `git clone` checkout handy, `./uninstall.sh` from the repo root works the same way.)

Auto-detects installed skill directories and cleans up: skill files, slash commands, hooks, AGENTS.md sections, and team configs.

## Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AGMSG_STORAGE_PATH` | `<skill>/db` | Directory holding the SQLite message store (`messages.db`). Override to relocate the store — handy for tests, sandboxes, or running isolated instances. |
| `AGMSG_PLUGIN_DIRS` | (unset) | `:`-separated extra directories to search for external drivers, in addition to `<skill>/plugins`. Each holds `<axis>/<name>/` subdirs. Drivers found here are still ignored until opted into with `agmsg plugin trust`. See [docs/plugins.md](docs/plugins.md). |

The message store path resolves as **`AGMSG_STORAGE_PATH` (env) > built-in default**. (A config-file layer is planned to slot in between the two as part of the storage-driver work; the intended order is env > config > default.) The override is scoped to the SQLite store only — team configs under `teams/` are unaffected.

```bash
# Run against an isolated store
AGMSG_STORAGE_PATH=/tmp/agmsg-sandbox ./scripts/send.sh myteam alice bob "hi"
```

### Repairing invalid UTF-8 already in a store

Use the repair command for the selected `<team>` when a stopped agent's `history.sh` or `inbox.sh` fails
with malformed-JSON or invalid-UTF-8 errors. The display sanitizer keeps future
reads available, but it does not rewrite an already-corrupt database row.

Stop the agents using the store before changing it. The command is SQLite-only
and resolves the database from `AGMSG_STORAGE_PATH` (or the installed skill's
`db/messages.db` when the variable is unset):

```bash
SKILL=~/.agents/skills/agmsg

# Read-only: inspect both the events and legacy messages tables.
"$SKILL/scripts/repair-invalid-utf8.sh" --check <team>

# Explicit write: choose a backup path that does not exist yet.
"$SKILL/scripts/repair-invalid-utf8.sh" --apply <team> \
  --backup /path/to/new/messages.db.backup
```

`--check` returns zero when the scan completed; inspect
`repairable_count=0 unsupported_count=0` rather than using the exit code alone
to decide that the store is clean. It never initializes, updates, or marks a
message read. `--apply` first requires a new backup file and a successful
SQLite `integrity_check`, then repairs only invalid `body` fields inside a
`BEGIN IMMEDIATE` transaction. It uses the original primary key and original
body bytes as an update guard. Invalid bytes in team, sender, recipient, id,
or timestamp fields are reported as `unsupported_corruption` and cause a
no-write failure.

On a production-sized store with tens of thousands of rows, `--check` may take
around two minutes, depending on the machine and store size, and may produce no
output while scanning. Do not interrupt it merely because the output is quiet.
For reference, one 31 MB store with 12,445 `messages` rows and 14,135 `events`
rows took 121 seconds to complete. `--check` is read-only, so interrupting it
does not change the database; do not interrupt `--apply` after it starts.

After a successful apply, run `--check` again and use the read-only history
command to verify the result:

```bash
"$SKILL/scripts/repair-invalid-utf8.sh" --check <team>
"$SKILL/scripts/history.sh" <team> [agent_id] [limit]
```

Do not use `inbox.sh` as the production post-check: it changes read/receipt
state. If apply, an integrity check, or the post-check fails, stop and restore
the saved backup manually while the agents remain stopped. The repair command
does not perform an automatic production rollback.

### Sandbox compatibility (Claude Code)

Claude Code's sandbox restricts filesystem writes to the project directory. In `monitor` mode, `watch.sh` runs inside the sandbox and needs to write pidfiles and SQLite WAL files under `~/.agents/skills/agmsg/`. If you have sandboxing enabled, add an allowlist entry to your settings:

**`~/.claude/settings.json`** (user-level — applies to all projects):

```json
{
  "sandbox": {
    "filesystem": {
      "allowWrite": [
        "~/.agents/skills/agmsg/"
      ]
    }
  }
}
```

The allowlist does not enable sandboxing by itself. Use `/sandbox` in Claude Code to choose a sandbox mode, or add `"enabled": true` alongside `"filesystem"` under `"sandbox"` to configure it in settings. The allowlist has no effect until sandboxing is enabled.

This can also go in project-level `.claude/settings.local.json` if you prefer per-project scope. The allowlist merges across all settings scopes and takes effect immediately — no restart needed.

If you installed agmsg under a custom command name (e.g. `m`), adjust the path accordingly (`~/.agents/skills/m/`).

### Sandbox compatibility (Codex)

Codex may run shell commands in a workspace-write sandbox. agmsg stores its
SQLite database and team metadata under `~/.agents/skills/<cmd>/` by default,
which is outside most project workspaces. If the sandbox cannot write there,
commands that append or update state can fail with errors such as
`sqlite3.OperationalError: unable to open database file`.

This affects operations such as:

- sending messages (`send.sh` writes to `db/messages.db`)
- claiming or acknowledging inbox handoffs (`inbox.sh`, `check-inbox.sh`, and `watch.sh` update the message store)
- joining, resetting, switching roles, or changing delivery mode (`teams/` and
  config/state files may be updated)

If you use Codex with filesystem sandboxing enabled, allow writes to the agmsg
skill storage directory in your Codex config.

Example `~/.codex/config.toml`:

```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
writable_roots = [
  "~/.agents/skills/agmsg/db",
  "~/.agents/skills/agmsg/teams",
]
```

If you installed agmsg under a custom command name, adjust the path accordingly:

```toml
[sandbox_workspace_write]
writable_roots = [
  "~/.agents/skills/m/db",
  "~/.agents/skills/m/teams",
]
```

You can also allow the whole skill directory if your Codex setup supports that:

```toml
[sandbox_workspace_write]
writable_roots = [
  "~/.agents/skills/agmsg",
]
```

Codex only supports `mode turn` and `mode off`; it does not have Claude Code's
Monitor tool. The sandbox allowlist is still required for writes performed by
manual `$agmsg` commands and turn-end inbox checks.

Some Codex runtimes or automations may inject a managed permission profile for a
single run. In that case, the run-specific writable roots must also include the
agmsg storage paths; the user-level config alone may not be enough.

## Tests

```bash
bats tests/    # requires bats-core: brew install bats-core
```

## Architecture

```
~/.agents/skills/<cmd>/           # Folder name = command name
├── SKILL.md                      # Skill definition (read by CC & Codex)
├── agents/
│   └── openai.yaml               # Codex metadata
├── scripts/                      # Bash scripts (the type-agnostic engine)
│   ├── lib/                      # Sourced helper libraries
│   └── drivers/types/<name>/     # Built-in agent-type drivers (manifest + runtime)
├── plugins/<axis>/<name>/        # External drivers you opt into (agmsg plugin trust)
├── db/messages.db                # SQLite WAL-mode message store
└── teams/                        # Team configs (self-contained)
    └── <team>/
        └── config.json
```

- **Storage**: Single SQLite file with WAL mode
- **Concurrency**: Multiple readers + 1 writer, no conflicts
- **Dependencies**: `bash`, `sqlite3` (no Python required)
- **Auto detection**: Stop hook checks inbox after each response (60s cooldown, configurable via `hook.check_interval`)
- **No daemon**: Direct filesystem access
- **No network**: Everything local

## Plugins

agmsg's pluggable units are **drivers** grouped by axis (`types` for agent
runtimes; `storage` and `delivery` to follow). Built-ins ship under
`scripts/drivers/`; you can drop your own under `<skill>/plugins/<axis>/<name>/`
(or point `AGMSG_PLUGIN_DIRS` at a directory) to extend agmsg without forking.

Because a driver is shell code that runs with your privileges, **external drivers
are never loaded until you opt in** — an unexpected drop-in is ignored (with a
warning) until you run `agmsg plugin trust <axis>/<name>`. List what's discovered
and its trust state with `agmsg plugin list`.

Full discovery order, the trust model, and authoring guidance:
[docs/plugins.md](docs/plugins.md) (design rationale in
[ADR 0002](docs/adr/0002-driver-discovery-and-plugin-opt-in.md)).

## Building on agmsg

Writing something *outside* agmsg's own scripts that reads or drives agmsg —
a GUI app, a bot, a derivative project (`agmsg-shogi`, `agmsg-go`,
`agmsg-mcp`, …)? Read data via `scripts/api.sh` (JSON out, no need to touch
`messages.db` or `teams/*/config.json` directly — those are internal and free
to change), and write through the existing scripts (`send.sh`, `join.sh`,
…) rather than the database. Full guidance:
[docs/building-on-agmsg.md](docs/building-on-agmsg.md).

## Community

- **Product Hunt**: #5 Product of the Day, [2026-06-09 launch](https://www.producthunt.com/products/agmsg) — 219 upvotes, 39 comments
- **Community projects** (also on the [showcase](https://agmsg.cc)): [`agkanban`](https://github.com/lucianlamp/agkanban) — multi-agent kanban board that pairs with agmsg; [`agmsg-office`](https://github.com/shinshin86/agmsg-office) — replays message logs as characters speaking on a stage; [`agmsg-viewer`](https://github.com/utenadev/agmsg-viewer) — message history in a chat interface in the browser; [`agmsg-bubblelog`](https://github.com/dreiachse-cyber/agmsg-bubblelog) — replays a team's log locally as a messenger-style thread; [`agmsg-tui`](https://github.com/rrrrnmtsu/agmsg-tui) — Rust/ratatui terminal client, friendly to SSH, mosh, and tmux
- **External contributors**: [@MiuraKatsu](https://github.com/MiuraKatsu) (Gemini support + whoami auto-detect), [@roundrop](https://github.com/roundrop) (Copilot CLI support), [@TOMONOSUKEJP](https://github.com/TOMONOSUKEJP) (native Windows / Git Bash), [@kenshin-yamada](https://github.com/kenshin-yamada) (watcher scoping fix), [@utenadev](https://github.com/utenadev) (OpenCode contribution), [@lucianlamp](https://github.com/lucianlamp) (native Windows PowerShell helpers), [@tatsuya6502](https://github.com/tatsuya6502) (sandboxed Bash tool support), [@Masashi-Ono0611](https://github.com/Masashi-Ono0611) (project-path validation, watchdog and watcher fixes), [@chemica-tan](https://github.com/chemica-tan) (Windows codex bridge: project compare and port parsing), [@otsune](https://github.com/otsune) (Git Bash quoting from PowerShell), [@tsukimiya](https://github.com/tsukimiya) (OpenCode: resident spawn and monitor delivery)

## Project site (agmsg.cc)

[agmsg.cc](https://agmsg.cc) is an Astro project under [`site/`](site/).

- **Source of truth:** `site/` (Astro + Tailwind). The built output is **not** committed — CI builds it.
- **Local preview:**
  ```bash
  cd site
  npm install
  npm run dev        # http://localhost:4321, live reload
  # or, to serve the production build:
  npm run build && npm run preview
  ```
- **Publish:** pushing to `main` with changes under `site/` runs [`.github/workflows/pages.yml`](.github/workflows/pages.yml), which builds the site and deploys it to GitHub Pages. The custom domain is set by `site/public/CNAME`.
- The agent-types gallery is generated at build time from `scripts/drivers/types/*/type.conf`, so adding an agent type surfaces it on the site automatically.

`docs/` is developer documentation (markdown, ADRs, spec) read on GitHub — it is **not** the published site.

## Contributing

See [Design & Architecture](docs/design.md) for developer documentation — identity model, data storage, hook system, and script responsibilities.

If agmsg saves you copy-paste round-trips, a GitHub star helps other people find it.

## License

MIT
