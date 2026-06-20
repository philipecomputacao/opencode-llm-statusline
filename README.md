# opencode-llm-statusline

> **OpenCode plugin that wires [llm-quota-bar][upstream] into OpenCode's TUI.**
> One single-file TypeScript plugin (~340 LOC) — no transitive deps — that reuses the same
> multi-provider statusline bar used by [fcc-claude][fcc] users.

[upstream]: https://github.com/philipecomputacao/llm-quota-bar
[fcc]: https://github.com/philipecomputacao/free-claude-code-plus

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![status](https://img.shields.io/badge/status-stable-brightgreen.svg)
![opencode](https://img.shields.io/badge/opencode-%3E%3D0.5-blueviolet.svg)

---

## Why

OpenCode is an excellent TUI for any LLM, but its statusline is just a 1-liner with the
model name. When you're juggling **18+ LLM providers** (MiniMax, OpenRouter, OpenAI,
Codex, DeepSeek, Mistral, etc.) and want to know:

- how much of your **Token Plan** you've burned in the last 5h
- how many **credits** remain on OpenRouter
- your **burn rate** (tokens/minute) and projected cost
- the **context window** usage of the current model
- whether the model is approaching a **rate limit** or hitting **cache discount**

…you need a richer bar. Rather than rewriting that logic for OpenCode's data model, this
plugin **forwards the OpenCode event payload** to the same Python statusline script that
`llm-quota-bar` runs under Claude Code.

The result: **one bar, two editors.** Configure the providers, colours, and quota adapters
in one place; the bar looks identical in Claude Code and OpenCode.

---

## Features

- 🔌 **Drop-in install** — one TypeScript file, no `npm install` of runtime deps
- 🔁 **Reuses the upstream Python script** — zero duplication of provider logic
- 📊 **Multi-provider quota tracking** — MiniMax Token Plan, OpenRouter credits, OpenAI
  admin dashboard, Codex ChatGPT plan, DeepSeek balance, Mistral usage
- 🎨 **Colour-coded bars** — green/yellow/red threshold for burn rate, cost, and quota
- ⚡ **Live updates** — runs after every model response (`session.idle` event)
- 🪶 **Lightweight** — 340 LOC, no third-party runtime dependencies
- 🔒 **Read-only** — never writes outside `~/.claude/projects/<hash>/<sessionId>.jsonl`

---

## How it works

```
┌────────────────────┐  session.idle    ┌─────────────────────┐
│  OpenCode TUI      │ ───────────────► │  llm-statusline.ts  │
│  (any LLM model)   │                  │  (this plugin)      │
└────────────────────┘                  └──────────┬──────────┘
                                                    │
                                                    │ appendFileSync
                                                    ▼
                                       ~/.claude/projects/<hash>/
                                       <sessionId>.jsonl
                                                    │
                                                    │  CLAUDE_PROJECT_DIR
                                                    │  CLAUDE_SESSION_ID
                                                    ▼
                                       ┌─────────────────────┐
                                       │ session_tokens.py   │
                                       │ (claude-code-       │
                                       │  statusline)        │
                                       └──────────┬──────────┘
                                                  │ stdout
                                                  ▼
                                       client.app.log() → OpenCode log panel
```

1. OpenCode fires `session.idle` after every model response
2. The plugin **accumulates** the token usage in an in-memory `Map<sessionId, totals>`
3. It **appends** a single JSONL entry to `~/.claude/projects/<hash>/<sessionId>.jsonl`
   — the **exact same format** Claude Code uses natively
4. It **spawns** the Python statusline script with `CLAUDE_PROJECT_DIR` and
   `CLAUDE_SESSION_ID` set, passing a synthesised stdin payload
5. The script's rendered bar is **logged** via `client.app.log()` and shows in the
   OpenCode log panel (`:open-logs`)

The Python script is **completely provider-agnostic** — it doesn't know it's running
under OpenCode. The plugin synthesises the model id, working dir, and version string so
the bar looks identical to the Claude Code bar.

---

## Installation

### Prerequisites

- [OpenCode][oc] `>= 0.5`
- [Node.js][node] `>= 18` (for `node:child_process` types — bundled with OpenCode)
- [Python 3.10+][py] on `PATH` (the script uses match/case, `int | None` syntax)
- The upstream statusline script: [`llm-quota-bar`][upstream] (see below)

[oc]: https://opencode.ai
[node]: https://nodejs.org
[py]: https://www.python.org

### 1. Install the statusline script

This plugin is a **thin adapter** — it doesn't ship the bar rendering logic. You need
the upstream Python statusline:

```bash
# Clone the upstream into a stable location
git clone https://github.com/philipecomputacao/llm-quota-bar.git \
    ~/Projetos/projetos/llm-quota-bar

# Symlink into the Claude Code expected location
mkdir -p ~/.claude/statusline
ln -sf ~/Projetos/projetos/llm-quota-bar/session_tokens.py \
       ~/.claude/statusline/session_tokens.py
```

The plugin calls `python3 ~/.claude/statusline/session_tokens.py` after every model
response. Make sure that file exists and is executable.

### 2. Install the plugin

Pick **one** of the two install methods.

#### Option A — Per-project (recommended)

Add the plugin to your OpenCode config:

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "plugin": ["llm-statusline"],
  "model": "minimax/MiniMax-M3"   // any model — the plugin is model-agnostic
}
```

Then place this file at `~/.config/opencode/plugins/llm-statusline.ts`. Either:

```bash
# Clone into ~/.config/opencode/plugins/ (one folder per plugin)
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git \
    ~/.config/opencode/plugins/llm-statusline
```

…or symlink the file from this repo:

```bash
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git \
    ~/Projetos/projetos/opencode-llm-statusline
ln -sf ~/Projetos/projetos/opencode-llm-statusline/plugins/llm-statusline.ts \
       ~/.config/opencode/plugins/llm-statusline.ts
```

#### Option B — Symlink the whole folder

If you want the plugin to live inside a project repo (e.g. so the team shares the
version), symlink each file from the project's `tools/` folder into the OpenCode
plugins dir.

### 3. Configure the providers (optional but recommended)

For **quota** info to render, set the matching API keys in your shell. The plugin
**does not** read them — the Python script does — but they need to be in the
environment when OpenCode spawns the script:

```bash
# ~/.zshrc or ~/.bashrc
export MINIMAX_API_KEY=sk-cp-...
export OPENROUTER_API_KEY=sk-or-...
export MISTRAL_API_KEY=ms-...
export DEEPSEEK_API_KEY=sk-...
export OPENAI_API_KEY=sk-...    # admin key for credit_grants
```

Or in the fcc-claude managed `~/.fcc/.env` (the script reads both):

```bash
# ~/.fcc/.env
MINIMAX_API_KEY=sk-cp-...
OPENROUTER_API_KEY=sk-or-...
```

If a key is missing, the matching quota segment is silently **omitted** — no errors,
no stack traces. See [`llm-quota-bar`][upstream] for the full provider list.

---

## Usage

Once installed, the bar appears in the **OpenCode log panel** after every model
response. To see it:

1. Run any prompt in OpenCode
2. Wait for the model to respond
3. Open the log panel: **<kbd>Ctrl+x</kbd> <kbd>l</kbd>** (or `:open-logs` in the TUI)

You should see something like:

```
[info] [minimax/MiniMax-M3·minimax] • 📁 ~/Projetos/foo • 📟 v2.1.170
       ⬆1.0M ⬇48k ↻R2.8M • ⏱ 40% usado (60% livre) (reset 2h48m) • 🧠 12% usado (88% livre)
       🇧🇷 R$1.61 🇺🇸 $0.312 • ⌛ 25m • ⚡ 42951t/m
```

The **bar's content** depends entirely on the model you used and what providers have
keys set. See the [`llm-quota-bar` README][upstream] for the full breakdown of
every field.

### Custom Python interpreter

By default the plugin calls `python3`. Override with `LLM_STATUSLINE_PYTHON`:

```bash
# Use a specific Python (e.g. venv with extra deps)
export LLM_STATUSLINE_PYTHON=/Users/me/.venv/bin/python3
```

Legacy: `MINIMAX_STATUSLINE_PYTHON` is also honoured.

---

## Configuration

This plugin has **no JSON config** of its own. All behaviour is controlled by:

| Source | Controls |
|---|---|
| [`llm-quota-bar/statusline.env.json`][upstream-cfg] | which segments to show, colour thresholds, FX cache TTL |
| `~/.claude/settings.local.json` | global Claude Code statusline command (if you want the bar in the Claude Code box itself too) |
| `~/.fcc/.env` and shell env | API keys for live quota lookups |

[upstream-cfg]: https://github.com/philipecomputacao/llm-quota-bar/blob/main/statusline.env.json

To change the **python interpreter** or the **statusline script path**, set
`LLM_STATUSLINE_PYTHON` and edit the `SCRIPT` constant in the plugin source. Both are
intentionally hard-coded to keep the plugin dependency-free.

---

## Development

The plugin is intentionally a single ~340-LOC file so it's easy to audit. To hack on it:

```bash
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git
cd opencode-llm-statusline

# Make your edits
$EDITOR plugins/llm-statusline.ts

# Symlink into OpenCode's plugin dir for live testing
ln -sf "$(pwd)/plugins/llm-statusline.ts" \
       ~/.config/opencode/plugins/llm-statusline.ts

# Restart OpenCode and trigger a model response
# Watch the log panel (:open-logs) for the rendered bar
```

### Type-checking

```bash
# tsc is bundled with OpenCode's node_modules; use it directly
npx --no-install tsc --noEmit plugins/llm-statusline.ts
```

### Manual smoke test

The plugin has no unit tests because all rendering happens in the upstream Python
script (which has its own test suite). To smoke-test the **adapter** logic:

```bash
# Make sure the script exists and is callable
python3 ~/.claude/statusline/session_tokens.py < /dev/null

# Should exit 0 with no output (no session yet, no project)
```

Then trigger any prompt in OpenCode and verify the bar appears in the log panel.

---

## Architecture decisions

| Decision | Why |
|---|---|
| **Single-file TypeScript** | Audit surface is ~340 LOC; no transitive deps to vet |
| **No `npm install`** | OpenCode's plugin loader runs the `.ts` file directly; bundling would force every user to install deps |
| **Reuse upstream Python script** | Multi-provider logic, pricing, and quota adapters are upstream concerns — duplicating them would create drift |
| **JSONL shim into `~/.claude/projects/`** | Claude Code's parser already handles that format; we get the same code path for free |
| **`Map<sessionId, totals>` accumulator** | OpenCode fires `session.idle` once per model response; we aggregate to match Claude Code's per-message JSONL format |
| **Synthesise stdin payload** | The Python script expects `{model, workspace, version, context_window, cost}` on stdin — OpenCode doesn't provide all of these, so we fill in defaults |

---

## Compatibility

| OpenCode | Plugin | Notes |
|---|---|---|
| `>= 0.5` | ✅ | Tested |
| `< 0.5` | ❌ | Plugin API changed; older versions emit different event shapes |

The plugin also tries to handle a generic `event` callback for OpenCode versions that
emit a different shape than `session.idle`:

```ts
event: ({ event }: { event: { type?: string } }) => {
  if (event?.type !== "session.idle") return Promise.resolve()
  return handleSessionIdle(client, projectDir, event)
}
```

If your OpenCode version fires a different event name, edit the predicate on line ~330.

---

## Troubleshooting

### No bar in the log panel

1. Check that the upstream script exists:
   ```bash
   ls -la ~/.claude/statusline/session_tokens.py
   # Should be a symlink or copy of the upstream script
   ```
2. Check the OpenCode logs for `[llm-statusline]` lines:
   ```
   [error] [llm-statusline] spawn error: ...
   [error] [llm-statusline] exit=1 stderr=...
   ```
3. Run the script manually to see real errors:
   ```bash
   CLAUDE_PROJECT_DIR="$PWD" CLAUDE_SESSION_ID=debug \
       python3 ~/.claude/statusline/session_tokens.py
   ```

### Quota segment missing

The `⏱` segment only shows when both:
- the **active model** matches a provider with a quota adapter (see [upstream][upstream])
- the matching **API key** is set in the env (`MINIMAX_API_KEY`, etc.)

If you're using a model with no adapter (e.g. `llamacpp/...`, `ollama/...`), the segment
is **omitted by design** — the bar shows no `⏱` token.

### `PYTHON` not found

The plugin calls `python3` by default. On some systems (e.g. Homebrew on Apple Silicon),
`python3` isn't on `PATH`. Set:

```bash
export LLM_STATUSLINE_PYTHON=/opt/homebrew/bin/python3
```

### Stale quota data

The upstream script caches quota responses for 60s in
`~/.cache/llm-quota-bar/provider-quota.json`. To force a refresh:

```bash
rm ~/.cache/llm-quota-bar/provider-quota.json
```

---

## Related projects

- **[llm-quota-bar][upstream]** — the upstream Python statusline script. This
  plugin is a thin adapter for it.
- **[free-claude-code-plus][fcc]** — the fcc-claude fork that this plugin was
  originally built for. The statusline bar was added to fcc-claude first and then
  factored out into the upstream repo.
- **[OpenCode][oc]** — the AI coding TUI this plugin targets.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
