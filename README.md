# crosshair

A lightweight, local control layer for [Cursor](https://cursor.com) that cuts
token usage in three ways:

1. **Model router** — blocks the wrong-size model for the task and tells you
   to switch (Opus → Sonnet for a `git commit`; Haiku → Sonnet for "debug this
   auth bug").
2. **Safepoint detector** — watches eight fatigue signals (token bloat, topic
   shift, error loops, file sprawl, completion markers, …) and, when the
   conversation is about to bloat, appends a **paste-ready handoff summary**
   you can drop into a fresh chat.
3. **`rtk` output compression** — rewrites noisy shell commands (`git status`,
   `pytest`, `tsc`, `grep`, `docker ps`, …) so Cursor sees a 5-line summary
   instead of 30 lines of porcelain.

Python 3.9+, stdlib only, 114 unit tests, nothing sent over the network.
Inspired by [model-matchmaker](https://github.com/coyvalyss1/model-matchmaker)
and [rtk](https://github.com/RohitJacob/rtk); rewritten from scratch as a
single Python package.

---

## Table of contents

- [Quick start](#quick-start)
- [Installation guide](#installation-guide)
- [Features](#features)
  - [1. Model router](#1-model-router)
  - [2. Safepoint detector](#2-safepoint-detector)
  - [3. Handoff summary](#3-handoff-summary)
  - [4. `rtk` — shell output compression](#4-rtk--shell-output-compression)
  - [5. Cursor hooks wired in](#5-cursor-hooks-wired-in)
  - [6. NDJSON analytics](#6-ndjson-analytics)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [Uninstall](#uninstall)
- [Privacy](#privacy)
- [Development](#development)
- [What this is _not_](#what-this-is-not)
- [License](#license)

---

## Quick start

```bash
git clone <this-repo> crosshair && cd crosshair
./install.sh
```

Restart Cursor (or open a new composer). That's it. Model guidance now fires
on every prompt; safepoint scoring runs in the background; `git status` and
friends return compressed output automatically.

Prefix any prompt with `!` to bypass the router for that one message.

---

## Installation guide

### Option A — one-liner (recommended)

```bash
./install.sh
```

Does all of:

- Verifies Python 3.9+.
- Creates a private venv at `~/.cursor/crosshair/venv/` so nothing pollutes
  your system Python.
- Installs the package in editable mode (so `git pull` picks up updates).
- Wires the hooks into `~/.cursor/hooks.json`, merging with any existing
  entries (and backing them up to `hooks.json.bak.<timestamp>`).
- Creates `~/.cursor/crosshair/state/` and `~/.cursor/crosshair/logs/`.

Re-runnable — it merges and dedupes, never duplicates hook entries.

**Flags** (pass through `./install.sh --flag`):

| Flag           | What it does                                                       |
| -------------- | ------------------------------------------------------------------ |
| `--no-rtk`     | Install router + safepoint hooks only; skip `rtk` rewrite hook     |
| `--dry-run`    | Print the hook plan without writing anything                       |
| `--hooks-file` | Use a custom `hooks.json` path (useful for testing)                |
| `--python`     | Explicit Python interpreter (the installer sets this automatically)|

Example — install everything except the shell rewriter:

```bash
./install.sh --no-rtk
```

### Option B — manual (no venv)

If you'd rather use your system Python or an existing venv:

```bash
python3 -m pip install -e .
python3 -m crosshair install --python "$(which python3)"
```

### Verifying the install

```bash
crosshair status         # empty until Cursor fires a hook
crosshair rtk list       # prints every supported shell command
```

Then in Cursor, try running `git status` from the agent — you should get the
compressed 5-line version.

### Updating

```bash
cd crosshair && git pull
./install.sh   # safe to re-run; merges rather than replaces
```

### Uninstall

```bash
crosshair uninstall      # removes crosshair entries from ~/.cursor/hooks.json
rm -rf ~/.cursor/crosshair   # optional: also wipe state and logs
```

The `uninstall` command is surgical — only the `command` strings that contain
`crosshair` are removed; any other hooks you have registered stay put.

---

## Features

### 1. Model router

Every prompt is classified against `config/default.json`'s rules _before_ it's
sent to the model. The router either lets it through, or blocks with a
recommendation:

- **Downgrade** — e.g. on Opus asking for `git commit -m "..."` → "this is a
  haiku-git-ops task; switch to Haiku and resend. Prefix `!` to override."
- **Upgrade** — e.g. on Haiku asking to "redesign the auth middleware" →
  "this likely needs Opus-class reasoning."
- **Pass** — no advice, the prompt goes through.

**How classification works.** Rules are ordered; first match wins. Each rule
supports:

- `pattern_any` — regex list; any match qualifies
- `pattern_none` — if any of these match, the rule is skipped (e.g. a git rule
  that explicitly skips when the prompt also mentions "bug" / "error")
- `min_words` / `max_words` — length gates
- `target` — the model family the rule recommends

Built-in rules cover:

| Rule                    | Target  | Trigger                                              |
| ----------------------- | ------- | ---------------------------------------------------- |
| `haiku-git-ops`         | Haiku   | `git status/commit/push`, rename, format, lint       |
| `opus-architecture`     | Opus    | "architecture", "trade-off", "redesign", …            |
| `opus-long-analytical`  | Opus    | prompt ≥ 200 words                                   |
| `opus-questions`        | Opus    | ≥ 100 words and contains a `?`                       |
| `sonnet-implementation` | Sonnet  | "build", "implement", "fix", "debug", "refactor"     |

Override a single prompt with a leading `!`. Add your own rules in your
user config (see [Configuration](#configuration)).

### 2. Safepoint detector

After every prompt we tally eight signals into a 0–100 score. Each threshold
crossing escalates the action crosshair appends to Cursor's response.

| Signal              | Fires when                                                 | Default weight |
| ------------------- | ---------------------------------------------------------- | -------------- |
| `token_bloat`       | running tokens cross 100K / 150K / 180K                    | 20 / 40 / 90   |
| `topic_shift`       | Jaccard similarity of your keywords drops below 0.15       | 30             |
| `completion_marker` | prompt includes "thanks", "lgtm", "let's move on", etc.    | 25             |
| `tool_volume`       | > 50 tool calls in the conversation                        | 15             |
| `file_sprawl`       | > 20 unique files edited                                   | 15             |
| `error_loop`        | same tool + failure type × 3                               | 20             |
| `time_gap`          | > 30 min since last prompt                                 | 10             |
| `session_length`    | > 50 user turns                                            | 15             |

**Escalation ladder:**

| Score  | Action                                                                 |
| ------ | ---------------------------------------------------------------------- |
| ≥ 50   | Soft note ("might be a good moment to summarise")                      |
| ≥ 70   | Wrap-up suggestion ("consider starting a fresh chat soon")             |
| ≥ 90   | Strong recommendation **+ handoff summary appended below the message** |

All weights, thresholds, completion markers, and stopwords are configurable.

### 3. Handoff summary

When the safepoint score goes critical, crosshair appends a paste-ready block
derived from **actual per-conversation state** (not just the current prompt):

```markdown
## Crosshair handoff

**Task**: Refactor auth middleware to support OAuth2 flows

**Latest message**: Now let's get the bar chart rendering

**Progress**
  - 12 file edit(s) across 6 file(s)
  - 40 tool call(s) (2 failure(s))
  - 18 assistant turn(s)

**Key files**
  - @src/auth/middleware.py
  - @src/auth/oauth.py
  - @tests/test_auth.py

**Outstanding**
  - Shell: pytest tests/test_auth.py failed (×2)

**Next steps**
  - Re-address the outstanding error(s): Shell: pytest tests/test_auth.py failed
  - Continue iterating on middleware.py, oauth.py
  - Pick up from: Now let's get the bar chart rendering

_Budget_: ~142,000 est. tokens, 18 user turn(s).
```

Paste into a new chat and continue without losing context. You can also
generate one on demand with `crosshair handoff [conversation-id]`.

### 4. `rtk` — shell output compression

`rtk` wraps the noisy commands Cursor runs all day and returns a structured
summary instead of the raw output. Same install/uninstall flow as the router —
pass `--no-rtk` to skip it.

**How it works:**

1. The `preToolUse` hook sees Cursor is about to run a `Shell` command.
2. `crosshair.rtk.rewrite.rewrite_command` parses the command line. It
   correctly handles:
    - compound operators (`&&`, `||`, `;`, `|`, `&`)
    - env-var prefixes (`sudo`, `FOO=bar`)
    - trailing redirects (`> file`, `2>&1`)
    - heredocs and arithmetic (`<<`, `$((`) — **skipped** to avoid breaking
      quoted scripts
    - commands already rewritten — **skipped** to keep the rewrite idempotent
3. Each matching segment becomes `crosshair rtk <original>`. Cursor runs
   _that_.
4. `crosshair rtk` dispatches to the matching Python filter, runs the real
   command, and returns compressed output. Exit codes and stderr are
   preserved.
5. If a filter crashes, we **fail open** — the original command still runs.

**Supported commands:**

| Category | Commands handled                                                          | Typical savings |
| -------- | ------------------------------------------------------------------------- | --------------- |
| Git      | `status`, `log`, `diff`, `add`, `commit`, `push`, `pull`, `branch`, `fetch` | 75–92 %       |
| Files    | `ls`, `cat`/`head`/`tail`, `grep`/`rg`, `find`, `tree`                    | 70–80 %         |
| Tests    | `pytest`, `cargo test`, `vitest`/`jest` (with `pnpm`/`npx`)               | 85–90 %         |
| Build    | `tsc`, `ruff`, `eslint`, `prettier`                                       | 70–80 %         |
| Infra    | `docker ps`, `docker images`, `docker logs`                               | 80–85 %         |

**Example — `git status` before and after:**

Before (raw, 17 lines):

```text
On branch rj/rohitjacob/rtk
Your branch is up to date with 'origin/rj/rohitjacob/rtk'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   config/default.json
        modified:   crosshair/analytics.py
        ...

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        crosshair/rtk/
        tests/test_rtk_filters.py
        ...
```

After (via `crosshair rtk git status`, 14 lines of meaning):

```text
branch: rj/rohitjacob/rtk...origin/rj/rohitjacob/rtk
7 modified, 5 untracked
  M  config/default.json
  M  crosshair/analytics.py
  M  crosshair/cli.py
  …
  ?? crosshair/rtk/
  ?? tests/test_rtk_filters.py
```

Same signal, ~60 % fewer tokens.

**Inspect and tune:**

```bash
crosshair rtk list                       # every supported command + estimated savings
crosshair rtk rewrite "git status && pytest -q"   # dry-run the rewriter
crosshair rtk gain                       # local savings summary (last 7 days)
crosshair rtk git status                 # invoke a filter manually
```

**Exclude specific binaries** if a filter is interfering:

```jsonc
{
  "rtk": {
    "enabled": true,
    "exclude_commands": ["curl", "playwright"],
    "max_lines_default": 200
  }
}
```

### 5. Cursor hooks wired in

Everything is a hook — no background daemon, no proxy, no UI automation.

| Hook                 | What crosshair does                                                       |
| -------------------- | ------------------------------------------------------------------------- |
| `sessionStart`       | Inject model-routing guidance into the system context                     |
| `beforeSubmitPrompt` | Router decision + safepoint score on every prompt                         |
| `afterAgentResponse` | Tally assistant tokens                                                    |
| `preToolUse`         | Rewrite `Shell` commands through `rtk` filters when a rule matches        |
| `postToolUse`        | Tally tool calls, record failures (for error-loop detection)              |
| `afterFileEdit`      | Track unique files edited (for handoff summary + file-sprawl signal)      |
| `preCompact`         | Observe native Cursor context compaction (strong signal of heavy session) |
| `stop`               | Log outcome                                                               |

Every hook fails open: if crosshair errors, Cursor's normal flow continues
unaffected.

### 6. NDJSON analytics

Every hook fires one append-only line to `~/.cursor/crosshair/logs/events.ndjson`,
and every `rtk` invocation to `~/.cursor/crosshair/logs/rtk.ndjson`. Both are
plain NDJSON — use `jq`, or:

```bash
crosshair analyze --days 7          # router decisions, safepoint actions, token estimates
crosshair analyze --json            # machine-readable
crosshair rtk gain                  # rtk-specific: saved tokens, top savings, passthroughs
```

---

## CLI reference

```bash
crosshair status                 # table of tracked conversations
crosshair show <conv-id>         # full state JSON for one conversation
crosshair handoff [<conv-id>]    # print the handoff summary (defaults to most recent)
crosshair analyze                # NDJSON log report (add --days 7 or --json)
crosshair reset [<conv-id>]      # clear state for one or all conversations
crosshair config                 # print active config paths
crosshair config --init          # write a user config stub at ~/.cursor/crosshair/config.json
crosshair install [--no-rtk]     # re-run hook install (used by ./install.sh)
crosshair uninstall              # remove crosshair entries from ~/.cursor/hooks.json

# rtk subcommands
crosshair rtk list               # every supported command + estimated savings
crosshair rtk gain               # local token-savings summary
crosshair rtk rewrite "<cmd>"    # dry-run: show how a command would be rewritten
crosshair rtk <cmd> [args…]      # run a command through its filter (passthrough if no rule)
```

Also available: `python3 -m crosshair <subcommand>` for environments without
the console script.

---

## Configuration

Default config: [`config/default.json`](config/default.json).
User overrides: `~/.cursor/crosshair/config.json`. They are **deep-merged**
over the defaults — only include keys you want to change.

Example: relax safepoint thresholds and add a custom routing rule.

```jsonc
{
  "safepoint": {
    "action_thresholds": { "note": 60, "suggest": 80, "strong": 95 }
  },
  "router": {
    "rules": [
      {
        "name": "custom-sql-pg-schema",
        "target": "sonnet",
        "pattern_any": ["\\bpg_dump\\b", "\\bpostgres schema\\b", "\\balembic\\b"]
      }
    ]
  },
  "rtk": {
    "exclude_commands": ["curl", "playwright"]
  }
}
```

Run `crosshair config` to print the exact paths in use.

---

## Uninstall

```bash
crosshair uninstall              # removes crosshair hooks from ~/.cursor/hooks.json
rm -rf ~/.cursor/crosshair       # optional: wipe venv, state, logs
```

`uninstall` only removes entries whose `command` string contains `crosshair`;
everything else in your `hooks.json` is preserved.

---

## Privacy

- **No network calls**, no telemetry, no proxy.
- Only the first 80 characters of each prompt are ever logged (configurable
  via `logging.truncate_prompts_to`).
- State and logs live under `~/.cursor/crosshair/`. Delete the directory to
  purge history.
- `rtk` runs commands with your real env + cwd, but captures output locally;
  nothing is sent anywhere.

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                        # 114 tests, <1s
```

The runtime package is stdlib-only; `pytest` and `pytest-cov` are the only
dev dependencies.

**Adding a new `rtk` filter:**

1. Write `your_filter(argv, ctx) -> FilterResult` in `crosshair/rtk/filters/`.
2. Append an `RtkRule` to `RTK_COMMANDS` in `crosshair/rtk/registry.py`.
3. Add a test in `tests/test_rtk_filters.py`.

The runner handles prefix stripping, dispatch, tracking, and fail-open
fallback — your filter only has to compress the output.

---

## What this is _not_

- **Not a proxy.** There's no HTTP server, no network interception. Everything
  is a hook script Cursor invokes.
- **Not a replacement for Cursor's auto-mode.** Auto-mode is server-side
  routing between a shortlist of models; crosshair runs client-side _before_
  a request is even sent. They coexist fine.
- **Not AppleScript / UI automation.** We don't drive the Cursor UI. The
  router tells you what to switch to; you (or your `!` override) click.
- **Not a shell proxy.** `rtk` rewrites commands via the `preToolUse` hook,
  which only fires when Cursor's agent triggers Shell. Your interactive
  terminal is untouched.

---

## License

MIT
