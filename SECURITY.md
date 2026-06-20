# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.2.x   | :white_check_mark: |
| 1.1.x   | :white_check_mark: |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security issue in this plugin or the vendored Python
statusline, **please do not open a public GitHub issue**. Instead:

1. Email `philipecomputacao@gmail.com` with subject `[opencode-llm-statusline] security`.
2. Include reproduction steps, affected version, and impact assessment.
3. Allow up to 7 days for an initial response before any public disclosure.

## Scope

This project ships:

- A TypeScript plugin loaded by the OpenCode TUI.
- A vendored copy of the upstream Python statusline script (no network calls
  beyond the configured provider quota endpoints).

The plugin does **not**:

- Write to your filesystem outside `~/.cache/opencode-llm-statusline/` and the
  Claude-Code-compatible JSONL path it mirrors for the Python script.
- Make outbound network calls of its own.
- Read credentials from disk. All API keys are read from the parent shell
  environment (`MINIMAX_API_KEY`, `OPENROUTER_API_KEY`, `LLM_STATUSLINE_PYTHON`).

If you find any of the above behaving differently than documented, that's a bug
worth reporting.
