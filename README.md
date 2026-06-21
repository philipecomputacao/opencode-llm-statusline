# opencode-llm-statusline

> Multi-provider quota + cost + burn-rate statusline plugin for [OpenCode][oc]'s TUI.
> Reuses the same Python rendering pipeline as [`claude-llm-quota-bar`][claude] —
> one bar, two editors.

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![release](https://img.shields.io/github/v/release/philipecomputacao/opencode-llm-statusline?color=brightgreen)](https://github.com/philipecomputacao/opencode-llm-statusline/releases)
![opencode](https://img.shields.io/badge/opencode-%3E%3D0.5-blueviolet.svg)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![typescript](https://img.shields.io/badge/typescript-ESM%20only-blue)
[![Maintained by @lpdigital.me](https://img.shields.io/badge/maintained%20by-%40lpdigital.me-E4405F.svg)](https://www.instagram.com/lpdigital.me/)

---

<table>
<tr>
<td width="120" align="center" valign="top">
<a href="https://www.instagram.com/lpdigital.me/"><img src="https://raw.githubusercontent.com/philipecomputacao/inventario-apis-gratuitas/main/assets/perfil-300.jpg" width="100" height="100" alt="@lpdigital.me" style="border-radius: 50%;" /></a>
</td>
<td valign="top">

**Plugin e curadoria por [@lpdigital.me](https://www.instagram.com/lpdigital.me/)** — Philipe compartilha plugins, automações e IA toda semana no Instagram. Manda um follow se a barra te ajudou.

</td>
</tr>
</table>

---

## What you see

```
[MiniMax-M3·minimax]  •  📁 ~/meu-projeto  •  📟 v1.17.8
⬆12.5K ⬇3.2K ⚡cache 25%  •  🧠 12% usado (88% livre)  •  ⏱ 1.2M / 50M tokens (2%)
🇧🇷 R$0,37 🇺🇸 $0.06  •  ⌛ 12m  •  ⚡ 1.3K t/m
```

*Three lines, updated after every model response — identical in Claude Code and OpenCode.*

### Features

- **Model & provider** — active model name with provider badge (MiniMax, OpenRouter, DeepSeek, Mistral, OpenAI, Codex, Anthropic)
- **Token counters** — ⬆ input / ⬇ output with cache read/write breakdown
- **Context window** — percentage used with color-coded warnings (🧠)
- **Provider quota** — remaining tokens on your Token Plan (⏱), with warn/alert thresholds
- **Real-time cost** — estimated spend in BRL and USD, with burn-rate emoji (🧊 calm / ⚡ busy / 🔥 heavy)
- **Session duration** — elapsed time since session start (⌛)
- **Two output modes** — sticky TUI toast (never disappears) or log panel (`:open-logs`)
- **Zero runtime deps** — the vendored Python script uses stdlib only; the plugin imports only Node built-ins

### Supported providers

| Provider | Quota endpoint | Env var |
|---|---|---|
| MiniMax | Token Plan API | `MINIMAX_API_KEY` |
| OpenRouter | Credits endpoint | `OPENROUTER_API_KEY` |
| DeepSeek | Balance API | `DEEPSEEK_API_KEY` |
| Mistral | Usage API | `MISTRAL_API_KEY` |
| OpenAI | Credit grants (admin key) | `OPENAI_API_KEY` |
| Codex | Session-based tracking | *(built-in)* |

*Missing a key? That segment is silently omitted — no errors, no stack traces.*

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
| **Toast popup** (v1.2.0) | `plugins/llm-statusline.toast.ts` | Sticky TUI toast (never disappears) + `:open-logs` backup | Users who want the bar always visible |

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

### 0. Install with an AI assistant (recommended if you're not technical)

Copy the prompt below and paste it into any AI chat (ChatGPT, Claude,
Gemini, etc.). The assistant will install this statusline plugin
end-to-end on your machine — no programming knowledge required on
your side.

<details>
<summary><strong>Click to reveal the install prompt — copy everything inside the box</strong></summary>

```plaintext
You are helping me install "opencode-llm-statusline", a plugin for
[OpenCode][oc] (the AI coding TUI) that adds a statusline showing
the active model, token usage, cost in USD, context window %, and
burn rate. It comes in two variants: a toast popup (v1.2.0, default)
or a log-panel renderer (v1.0.0). Both are written in TypeScript and
install as a single `.ts` file symlinked into the OpenCode plugins
directory.

Your job: install it on my machine end-to-end and verify it works.
Do not ask me coding questions — make sensible decisions and tell me
what you did. If you hit a step that needs my input (e.g. which
variant I prefer), ask exactly one focused question and continue.

=========================================================
STEP 1 — Detect my environment
=========================================================
Run these commands and remember the output:

  uname -a                          # OS family (Darwin / Linux / Windows-bash)
  command -v opencode               # is OpenCode already installed? which version?
  opencode --version                # need >= 0.5
  command -v node                   # need Node.js >= 18
  node --version
  command -v python3                # need Python 3.10+ (for the helper script)
  python3 --version
  echo "PLUG_DIR=${OPENCODE_PLUGINS_DIR:-$HOME/.config/opencode/plugins}"

If `opencode` is missing, tell me to install it from
https://opencode.ai/docs and stop. If `node` is missing, install it
from https://nodejs.org (LTS recommended) and stop. If `python3` is
missing, install it from https://python.org and stop.

=========================================================
STEP 2 — Choose the variant
=========================================================
Ask me ONE question: "Which variant do you want?"

- **Toast popup (v1.2.0, recommended)** — a non-intrusive toast at
  the top of the TUI that fades in/out. Good for short summaries
  (model, tokens, cost, ctx %).
- **Log panel (v1.0.0)** — appends each turn's usage to a dedicated
  log buffer inside the TUI. Good for long sessions where you want
  the historical context.

Default to toast unless I say otherwise. Remember my choice for
STEP 3.

=========================================================
STEP 3 — Clone and symlink
=========================================================
Run:

  git clone https://github.com/philipecomputacao/opencode-llm-statusline.git
  cd opencode-llm-statusline

  mkdir -p ~/.config/opencode/plugins

  # If I chose toast:
  ln -sf "$(pwd)/plugins/llm-statusline.toast.ts" \
         ~/.config/opencode/plugins/llm-statusline.ts

  # If I chose log panel:
  ln -sf "$(pwd)/plugins/llm-statusline.ts" \
         ~/.config/opencode/plugins/llm-statusline.ts

Verify the symlink:

  ls -l ~/.config/opencode/plugins/llm-statusline.ts
  # expect: ... -> /<absolute path>/llm-statusline.ts or .toast.ts

If the symlink points to a non-existent file, you cloned to a
different path than the symlink — `cd` into the clone dir and re-run
the `ln -sf` command with `$(pwd)` substituted for the current
absolute path.

=========================================================
STEP 4 — Register the plugin in OpenCode
=========================================================
Open the file ~/.config/opencode/config.json (or the equivalent
config file the opencode CLI uses — check `opencode --help` or
`opencode config docs`). Add the plugin entry:

  {
    "plugins": {
      "llm-statusline": {
        "path": "~/.config/opencode/plugins/llm-statusline.ts"
      }
    }
  }

If config.json already exists with other settings, MERGE this block
into the existing "plugins" object — don't overwrite anything else.
Verify the JSON is valid with:

  python3 -m json.tool < ~/.config/opencode/config.json > /dev/null \
      && echo "config.json is valid JSON"

If that command errors, paste me the error and the config.json
contents (without API keys) so I can see the syntax mistake.

=========================================================
STEP 5 — Verify it works
=========================================================
Restart OpenCode (close and reopen the TUI). Type any short prompt.
You should see either:

- A toast popup at the top of the TUI with the model name, token
  counts, and a cost line (if you chose the toast variant).
- A new "LLM" log panel that updates on each turn (if you chose
  the log panel variant).

If nothing appears, the most common cause is that the plugin path
in config.json doesn't match the symlink target. Run:

  ls -l ~/.config/opencode/plugins/llm-statusline.ts
  realpath ~/.config/opencode/plugins/llm-statusline.ts

Both should point to the same file inside the cloned repo. Fix
the config.json "path" if they don't match.

=========================================================
STEP 6 — Optional: enable cost tracking
=========================================================
The statusline shows cost in USD for any model with a known price.
No configuration needed — the bundled pricing data covers OpenAI,
Anthropic, Google, Mistral, and DeepSeek. If you route through a
custom gateway, you can extend the pricing list by editing
`lib/pricing.json` inside the cloned repo and restarting OpenCode.

=========================================================
DONE — Tell me what you did
=========================================================
Summarise in 3-5 bullet points:
- which variant I picked
- the absolute path to the symlink target
- the contents of my config.json plugins block
- whether STEP 5 verification passed
- any caveats or things I should know

If anything failed, give me the exact error message and the command
that produced it. Don't try to fix it silently — surface it.
```

</details>

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

For the **toast popup** variant (v1.2.0, recommended):

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
(variant 1.2.0) or the bar should appear in the log panel (variant 1.0.0).

---

## Configuration

This plugin ships with sensible defaults. Tweak via env vars:

| Env var | Default | Effect |
|---|---|---|
| `LLM_STATUSLINE_PYTHON` | `python3` | Python interpreter to spawn |
| `LLM_STATUSLINE_TOAST_MS` | `0` (sticky) | Toast duration in ms. 0 = never auto-dismiss |
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
   echo '{"model":{"id":"minimax/MiniMax-M3"},"workspace":{"current_dir":"/tmp"},"version":"1.17.8","context_window":{"used_percentage":0},"cost":{"total_duration_ms":0}}' \
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

Plugin e curadoria por **[@lpdigital.me](https://www.instagram.com/lpdigital.me/)**.

[oc]: https://opencode.ai
[claude]: https://github.com/philipecomputacao/claude-llm-quota-bar
[fcc]: https://github.com/philipecomputacao/free-claude-code-plus
[node]: https://nodejs.org
[py]: https://www.python.org
