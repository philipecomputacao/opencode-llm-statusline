# opencode-llm-statusline

> Multi-provider quota + cost + burn-rate bar inside [OpenCode][oc]'s TUI.
> Reuses the same Python rendering pipeline as [`claude-llm-quota-bar`][claude] —
> one bar, two editors.

[oc]: https://opencode.ai
[claude]: https://github.com/philipecomputacao/claude-llm-quota-bar

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![status](https://img.shields.io/badge/status-stable-brightgreen.svg)
![opencode](https://img.shields.io/badge/opencode-%3E%3D0.5-blueviolet.svg)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![typescript](https://img.shields.io/badge/typescript-ESM%20only-blue)

> **Status:** repo is **private** while it stabilises. Flip the visibility
> setting on GitHub when you're ready to publish.

---

## Why

OpenCode's built-in statusline is a one-liner with the model name. If you
juggle multiple LLM providers (MiniMax, OpenRouter, DeepSeek, Mistral, OpenAI,
Codex, etc.) you usually want to know at a glance:

- How much of your **Token Plan** has been burned in the last 5h
- How many **credits** remain on OpenRouter
- Your **burn rate** (tokens/min) and projected cost
- The **context window** usage for the current model
- Whether you're approaching a **rate limit** or hitting **cache discounts**

Rather than reimplement that logic against OpenCode's event payloads, this
plugin **forwards the OpenCode session into the same Python statusline script**
that powers [`claude-llm-quota-bar`][claude] under Claude Code. The bar looks
identical in both editors.

## Two variants — pick one

| Variant | File | Output | Best for |
|---|---|---|---|
| **Log panel** (v1.0.0) | `plugins/llm-statusline.ts` | bar in OpenCode's log panel (`:open-logs`) | Quiet users who don't want popups |
| **Toast popup** (v1.1.0) | `plugins/llm-statusline.toast.ts` | 30-second TUI toast in the corner | Users who want the bar visible at all times |

Both variants share the same Python script and the same quota adapters.

## How it works

```
┌──────────────────┐  session.idle    ┌──────────────────────┐
│  OpenCode TUI    │ ───────────────► │  llm-statusline*.ts  │
│  (any LLM model) │                  │  (plugin)            │
└──────────────────┘                  └──────────┬───────────┘
                                                  │
                                                  │ append JSONL
                                                  ▼
                                    ~/.claude/projects/<hash>/
                                    <sessionId>.jsonl
                                                  │
                                                  │ CLAUDE_PROJECT_DIR
                                                  │ CLAUDE_SESSION_ID
                                                  ▼
                                    ┌──────────────────────┐
                                    │ python/session_tokens.py
                                    │ (vendored self-contained)
                                    └──────────┬───────────┘
                                               │ stdout (3 lines)
                                               ▼
                          client.app.log()       OR       client.tui.showToast()
                          (log panel)                     (toast popup)
```

1. OpenCode fires `session.idle` after each model response.
2. The plugin queries `client.session.messages()` for token totals from the
   AssistantMessage payload.
3. It writes a Claude-Code-compatible JSONL entry so the Python script can read
   it like a real Claude Code session.
4. It spawns `python/session_tokens.py` with the project cwd, session id, and a
   synthesised stdin payload.
5. The script's rendered 3-line bar is delivered through `client.app.log()`
   (log panel variant) or `client.tui.showToast()` (toast variant).

## Installation

### Prerequisites

- [OpenCode][oc] `>= 0.5` (tested on `1.17.8`)
- [Node.js][node] `>= 18` (for `node:child_process` and `node:url`)
- [Python 3.10+][py] on `PATH` (the script uses match/case, `int | None`)
- A POSIX-ish shell (Linux, macOS, WSL). Not tested on Windows native.

### 1. Clone this repo

```bash
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git
cd opencode-llm-statusline
```

### 2. Symlink the variant you want

For the **toast popup** variant (v1.1.0, recommended):

```bash
mkdir -p ~/.config/opencode/plugins
ln -sf "$(pwd)/plugins/llm-statusline.toast.ts" \
       ~/.config/opencode/plugins/llm-statusline.ts
```

For the **log panel** variant (v1.0.0):

```bash
ln -sf "$(pwd)/plugins/llm-statusline.ts" \
       ~/.config/opencode/plugins/llm-statusline.ts
```

### 3. Register the plugin

Edit `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "plugin": ["./plugins/llm-statusline.ts"]
}
```

The `./` is resolved relative to `~/.config/opencode/` and points at the
symlink you created above.

### 4. (Optional) Set provider API keys

The Python script reads keys from the **shell environment** when it runs.
Without keys, the corresponding quota segment is silently omitted — no
errors, no stack traces.

```bash
# ~/.zshrc or ~/.bashrc — REPLACE the placeholder values with your real keys.
export MINIMAX_API_KEY=<your-MiniMax-token-plan-key>
export OPENROUTER_API_KEY=<your-openrouter-key>
export DEEPSEEK_API_KEY=<your-deepseek-key>
export MISTRAL_API_KEY=<your-mistral-key>
# Admin-only — for OpenAI credit-grants dashboard:
export OPENAI_API_KEY=<your-openai-admin-key>
```

See the full list of supported providers and quota endpoints in the
[`claude-llm-quota-bar` README][claude].

### 5. Restart OpenCode

Plugins are loaded at boot. Send any prompt and the toast should fire
(variant 1.1.0) or the bar should appear in the log panel (variant 1.0.0).

---

## Configuration

This plugin ships with sensible defaults. Tweak via env vars:

| Env var | Default | Effect |
|---|---|---|
| `LLM_STATUSLINE_PYTHON` | `python3` | Python interpreter to spawn |
| `OPENCODE_PROJECT_DIR` | (shell cwd) | Override the folder shown in the bar |

All other behaviour (which segments to render, colour thresholds, FX cache
TTL, etc.) lives in the vendored
[`python/statusline.env.json`](python/statusline.env.json) — copy it to
`~/.config/opencode-llm-statusline/statusline.env.json` (or set the
`STATUSLINE_ENV` env var) to override.

---

## Troubleshooting

### Toast never appears

1. Confirm OpenCode loaded the plugin:
   ```bash
   tail -100 ~/.local/share/opencode/log/opencode.log | grep -i "llm-statusline"
   ```
   You should see `Plugin loaded` for each OpenCode boot.
2. Confirm the symlink resolves:
   ```bash
   ls -la ~/.config/opencode/plugins/llm-statusline.ts
   ```
   The target must exist.
3. Run the Python script by hand:
   ```bash
   echo '{"model":{"id":"minimax/MiniMax-M3"},"workspace":{"current_dir":"/tmp"},"version":"opencode","context_window":{"used_percentage":0},"cost":{"total_duration_ms":0}}' \
     | CLAUDE_PROJECT_DIR=/tmp CLAUDE_SESSION_ID=test \
       python3 "$(pwd)/python/session_tokens.py"
   ```
   It should print three lines. If it errors, the Python script itself is
   broken (file a bug).

### Toast shows "📁 ~" instead of the project folder

OpenCode 1.17.8 plugin SDK does not expose tool-call parts (see
[CHANGELOG.md](CHANGELOG.md#110---2026-06-20) for details), so the plugin
can't see where the agent is reading files from. Two workarounds:

- Launch OpenCode from inside the project:
  ```bash
  cd /path/to/project
  opencode
  ```
- Or set `OPENCODE_PROJECT_DIR=/path/to/project` in the shell before
  launching OpenCode.

### Bar is missing the `⏱` quota segment

The `⏱` segment only renders when the active model matches a provider with a
wired-up quota adapter **and** the matching API key is in the environment.
See [`claude-llm-quota-bar`'s provider table][claude] for the full list.

### `python3` not found

Set `LLM_STATUSLINE_PYTHON` to the full path, e.g.:

```bash
export LLM_STATUSLINE_PYTHON=/opt/homebrew/bin/python3.12
```

---

## Development

```bash
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git
cd opencode-llm-statusline

# Symlink for live testing
ln -sf "$(pwd)/plugins/llm-statusline.toast.ts" \
       ~/.config/opencode/plugins/llm-statusline.ts

# Run the CI smoke test locally (Python + TypeScript syntax)
.github/workflows/smoke-test.yml
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for commit conventions, code style,
and the PR checklist.

### Syncing the vendored Python script

The `python/` directory is a periodic copy of
[`claude-llm-quota-bar`][claude]'s `session_tokens.py` and friends. To sync:

```bash
# From this repo
git clone https://github.com/philipecomputacao/claude-llm-quota-bar.git /tmp/cb
diff -ruN /tmp/cb/session_tokens.py python/session_tokens.py
diff -ruN /tmp/cb/lib python/lib
diff -ruN /tmp/cb/pricing.json python/pricing.json
diff -ruN /tmp/cb/statusline.env.json python/statusline.env.json
# Apply the diffs manually. Keep this repo's commit history clean by
# committing the sync as chore(python): sync from upstream <sha>.
```

---

## Related projects

- [`claude-llm-quota-bar`][claude] — the upstream Claude Code statusline.
  This plugin is a thin adapter that runs the same script under OpenCode.
- [`free-claude-code-plus`][fcc] — the fcc-claude fork that motivated the
  multi-provider design.

## License

[Apache 2.0](LICENSE)

[claude]: https://github.com/philipecomputacao/claude-llm-quota-bar
[fcc]: https://github.com/philipecomputacao/free-claude-code-plus
[node]: https://nodejs.org
[py]: https://www.python.org
