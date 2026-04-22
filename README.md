# crosshair

A lightweight control layer for [Cursor](https://cursor.com) that:

1. **Routes prompts** to the cheapest-adequate model.
2. **Detects _safepoints_** — moments where a new chat or mid-conversation
   summary would save tokens.
3. **Compresses shell output** (via the built-in `rtk` filters) so `git status`,
   `pytest`, `tsc`, etc. don't dump hundreds of lines into the context window.

Python 3.9+, zero runtime dependencies. Inspired by
[model-matchmaker](https://github.com/coyvalyss1/model-matchmaker) and
[rtk](https://github.com/RohitJacob/rtk) but rewritten from scratch as a single
Python package: unit-tested, per-conversation state, topic-shift detection,
auto-generated handoff summaries, and a command filter layer.

---

## What it does

Every time you hit **Send** in Cursor, crosshair:

1. **Classifies the prompt** against configurable rules and either:
   - _blocks_ with a cheaper-model recommendation (e.g. "you're on Opus for a
     `git commit` — switch to Haiku")
   - _blocks_ with a stronger-model recommendation (e.g. "this architecture
     question needs Opus")
   - passes the prompt through untouched
2. **Scores the conversation for fatigue** across eight signals. If the score
   crosses your thresholds it appends an advisory message, and at the highest
   level it appends a **paste-ready handoff summary** so you can start a fresh
   chat without losing context.

And every time Cursor is about to run a shell command, `rtk` transparently
rewrites it (e.g. `git status` → `crosshair rtk git status`) so the agent sees
a 5-line summary instead of 30 lines of porcelain. Details in
[§ rtk — output compression](#rtk--output-compression).

Prefix any prompt with `!` to bypass the router entirely.

## Why rewrite in Python (vs. the bash original)

- **No bash-quoting trap.** `model-matchmaker` embeds Python inside
  `python3 -c '...'` inside a bash script; its README dedicates an entire
  section to debugging exit-code-2 breakage caused by quoting. Crosshair is a
  real Python package so that class of bug can't exist.
- **Unit tests.** Classifier rules, safepoint scoring, and handoff generation
  all have pytest coverage.
- **Config lives in JSON.** Add / reorder routing rules without touching code.
- **Per-conversation state.** We actually know how many tool calls, file edits,
  and tokens have passed through this conversation — so safepoint decisions are
  based on reality, not the current prompt alone.

## Install

```bash
git clone <this-repo> crosshair && cd crosshair
./install.sh
```

This creates `~/.cursor/crosshair/venv/`, installs the package in editable
mode, wires the hooks into `~/.cursor/hooks.json` (existing entries are
preserved and backed up), and creates `~/.cursor/crosshair/{state,logs}/`.

Restart Cursor or open a fresh composer for the hooks to activate.

Manual install (if you don't want the venv):

```bash
python3 -m pip install -e .
python3 -m crosshair install --python "$(which python3)"
```

## Hooks wired

| Hook                 | What crosshair does                                                       |
| -------------------- | ------------------------------------------------------------------------- |
| `sessionStart`       | Inject model-routing guidance into the system context                     |
| `beforeSubmitPrompt` | Router decision + safepoint score on every prompt                         |
| `afterAgentResponse` | Tally assistant tokens                                                    |
| `preToolUse`         | Rewrite `Shell` commands through `rtk` filters when they match a rule     |
| `postToolUse`        | Tally tool calls, record failures (for error-loop detection)              |
| `afterFileEdit`      | Track unique files edited (for handoff summary + file-sprawl signal)      |
| `preCompact`         | Observe native Cursor context compaction (strong signal of heavy session) |
| `stop`               | Log outcome                                                               |

Pass `--no-rtk` to `./install.sh` (or `crosshair install`) if you want to skip
the preToolUse rewrite hook.

## Safepoint signals

Each contributes to a 0–100 score; thresholds are configurable.

| Signal              | Fires when                                                 | Default weight |
| ------------------- | ---------------------------------------------------------- | -------------- |
| `token_bloat`       | running tokens cross 100K / 150K / 180K                    | 20 / 40 / 90   |
| `topic_shift`       | Jaccard similarity of your keywords drops below 0.15       | 30             |
| `completion_marker` | prompt includes "thanks", "lgtm", "let's move on", etc.    | 25             |
| `tool_volume`       | > 50 tool calls                                            | 15             |
| `file_sprawl`       | > 20 unique files edited                                   | 15             |
| `error_loop`        | same tool + failure type × 3                               | 20             |
| `time_gap`          | > 30 min since last prompt                                 | 10             |
| `session_length`    | > 50 user turns                                            | 15             |

Score ≥ **50** → soft note; ≥ **70** → wrap-up suggestion; ≥ **90** → strongly
recommend a new chat and append the handoff summary below the message.

## Handoff summary (example)

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

Paste this into a fresh chat and continue.

## CLI

```bash
crosshair status            # table of tracked conversations
crosshair show <conv-id>    # full state JSON for one conversation
crosshair handoff [<id>]    # print the handoff summary (defaults to most recent)
crosshair analyze           # NDJSON log report (add --days 7 or --json)
crosshair reset [<id>]      # clear state for one or all conversations
crosshair config --init     # write a user config stub at ~/.cursor/crosshair/config.json
crosshair uninstall         # remove crosshair entries from ~/.cursor/hooks.json

# rtk (output compression) subcommands
crosshair rtk list          # every supported command + its estimated savings
crosshair rtk gain          # token savings from rtk filters (last 7 days)
crosshair rtk rewrite "git status && pytest"   # show how a command would be rewritten
crosshair rtk git status    # run a filter directly (same entry Cursor calls)
```

## rtk — output compression

`rtk` wraps the noisy commands Cursor runs all day (`git status`, `ls`, `cat`,
`grep`, `pytest`, `tsc`, `ruff`, `docker ps`, …) and returns a compressed
summary instead of the raw output. The same knobs apply as for model routing:
turn it off with `--no-rtk` on install, and everything is controlled from
`config/default.json`.

How it works:

1. The `preToolUse` hook sees Cursor is about to run a `Shell` command.
2. `crosshair.rtk.rewrite.rewrite_command` parses the command line (handling
   `&&`, `||`, `;`, `|`, env-var prefixes like `sudo VAR=val`, and trailing
   redirects) and rewrites each segment that matches a rule to
   `crosshair rtk <original>`.
3. Cursor runs that rewritten command instead.
4. `crosshair rtk` dispatches to the matching Python filter, which executes
   the original command and returns compressed output.

If a filter crashes for any reason we **fail open** and just passthrough — no
broken workflows.

| Category | Commands handled                                                     | Typical savings |
| -------- | -------------------------------------------------------------------- | --------------- |
| Git      | `status`, `log`, `diff`, `add`, `commit`, `push`, `pull`, `branch`, `fetch` | 75–92 %   |
| Files    | `ls`, `cat`/`head`/`tail`, `grep`/`rg`, `find`, `tree`              | 70–80 %         |
| Tests    | `pytest`, `cargo test`, `vitest`/`jest` (with `pnpm`/`npx`)         | 85–90 %         |
| Build    | `tsc`, `ruff`, `eslint`, `prettier`                                 | 70–80 %         |
| Infra    | `docker ps`, `docker images`, `docker logs`                         | 80–85 %         |

Run `crosshair rtk list` for the full registry and `crosshair rtk gain` to see
how many tokens you've saved locally.

You can exclude specific binaries (e.g. `curl`, `playwright`) from rewriting:

```jsonc
{
  "rtk": {
    "enabled": true,
    "exclude_commands": ["curl", "playwright"],
    "max_lines_default": 200
  }
}
```

## Configuration

Default rules and thresholds live in [`config/default.json`](config/default.json).
User overrides live at `~/.cursor/crosshair/config.json` and are deep-merged
over the defaults — you only need to include keys you want to change.

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
  }
}
```

Run `crosshair config` to print the exact paths in use.

## Privacy

- Everything is local: no network calls, no telemetry, no proxy.
- Only the first 80 characters of each prompt are ever logged (configurable).
- State and logs live under `~/.cursor/crosshair/`. Delete the directory to
  purge history.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

The whole package is stdlib-only so `pytest` + `pytest-cov` are the only dev
dependencies.

## What this is _not_

- A proxy. There is no HTTP server or network interception. This is purely a
  hook script that Cursor invokes.
- A replacement for Cursor's auto-mode. Auto-mode is server-side routing
  between a shortlist of models; crosshair runs client-side before a request
  is even sent. The two can coexist.
- AppleScript-based auto-switch. We don't drive the UI. We just tell you what
  to switch to and wait for you to do it (or override with `!`).

## License

MIT
