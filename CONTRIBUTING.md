# Contributing

Thanks for your interest in `opencode-llm-statusline`. Issues, pull requests,
and reproductions are welcome.

## Project Layout

```
opencode-llm-statusline/
├── plugins/
│   ├── llm-statusline.ts          # log-panel variant (v1.0.0)
│   └── llm-statusline.toast.ts    # toast variant (v1.1.0)
├── python/                        # vendored upstream statusline script
│   ├── session_tokens.py
│   ├── lib/                       # parser, display, pricing, fx, provider_quota
│   ├── pricing.json               # 400+ model prices
│   └── statusline.env.json        # default display toggles
├── .github/
│   ├── workflows/smoke-test.yml   # CI: smoke test the Python script
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md                # this file
├── SECURITY.md
├── LICENSE                        # Apache 2.0
└── .gitignore
```

## Setting Up a Dev Environment

```bash
git clone https://github.com/philipecomputacao/opencode-llm-statusline.git
cd opencode-llm-statusline

# Symlink the toast variant into OpenCode's plugin dir:
mkdir -p ~/.config/opencode/plugins
ln -sf "$(pwd)/plugins/llm-statusline.toast.ts" \
       ~/.config/opencode/plugins/llm-statusline.ts

# Open OpenCode in any directory and send a message; the toast should fire.
```

## Code Style

### TypeScript (`plugins/`)

- One file per plugin variant. Shared types live in the file that uses them.
- IIFE-style `use strict` not required; OpenCode plugins run inside an ESM
  context provided by the host.
- Keep dependencies to zero. The plugin imports only from `node:fs`,
  `node:path`, `node:os`, `node:child_process`, and `node:url`.
- Avoid `any`; cast the `client` once and document the surface you actually use.
- Never throw out of the event handler. Wrap user-visible work in
  `try { ... } catch { /* noop */ }` so a bad message cannot crash the TUI.

### Python (`python/`)

- Python 3 stdlib only. No `pip install` required at runtime.
- Match the upstream `claude-llm-quota-bar` style: `pathlib`, dataclasses,
  frozen slots, type hints, no third-party imports.
- When syncing the vendored copy from upstream, prefer `git diff` to verify
  the diff stays minimal (the whole point of vendoring is reproducibility, not
  forking).

## Commit Messages

Conventional Commits-lite in **PT-BR**, e.g.:

```text
feat(plugin): adiciona suporte a slash command
fix(parser): aceita sessoes sem timestamp inicial
chore: bump python vendored para v2.3.1
docs: corrige typo no troubleshooting
```

First line ≤ 72 chars, no period at end. Add a short body if the diff is
non-obvious. If an AI generated the diff, add:

```text
Co-Authored-By: <Model Name> <noreply@example.com>
```

## Pull Request Checklist

- [ ] Updated `CHANGELOG.md` under `[Unreleased]`.
- [ ] CI smoke test passes (`.github/workflows/smoke-test.yml`).
- [ ] Manual smoke test in OpenCode 1.17.8: toast appears with correct bar.
- [ ] No secrets, tokens, or `~/Users/<name>/` paths in the diff.

## Reporting Bugs

Use the **Bug Report** issue template. Include:

- OpenCode version (`opencode --version`).
- Plugin file in use (`llm-statusline.ts` or `llm-statusline.toast.ts`).
- OpenCode log excerpt (`~/.local/share/opencode/log/opencode.log`).
- Python script output when run manually:
  ```bash
  echo '{"model":{"id":"..."},"workspace":{"current_dir":"..."},"version":"opencode","context_window":{"used_percentage":0},"cost":{"total_duration_ms":0}}' \
    | CLAUDE_PROJECT_DIR=/tmp CLAUDE_SESSION_ID=test \
      python3 python/session_tokens.py
  ```

## Feature Requests

Use the **Feature Request** issue template. Note that any change to the
vendored Python script must remain compatible with the upstream
`claude-llm-quota-bar` repository to make future syncs painless.

## License

By contributing, you agree that your contributions will be licensed under
the Apache 2.0 License. See `LICENSE` for the full text.
