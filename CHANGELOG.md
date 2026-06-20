# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `plugins/llm-statusline.toast.ts`: toast duration default increased from
  30 s to 300 s (5 min) so the bar stays visible between model responses
  instead of disappearing after 30 s. Can be overridden with
  `LLM_STATUSLINE_TOAST_MS`.
- `plugins/llm-statusline.toast.ts`: bar is now also logged via
  `client.app.log` on every successful toast, so it is always visible in
  `:open-logs` as a fallback when the toast eventually auto-dismisses.
- Both plugins: `version` field in the stdin payload was hardcoded to the
  string `"opencode"` (rendering `📟 vopencode` in the bar). The plugin now
  spawns `opencode --version` at init time and uses the real version number
  (e.g. `1.17.8` → `📟 v1.17.8`). Falls back to `"opencode"` on failure.

### Added

- `LLM_STATUSLINE_TOAST_MS` environment variable to override the toast
  duration in milliseconds (default `30000`).
- `client.app.log` calls in the toast plugin so users can see when the
  plugin loads, when a bar is deduplicated, and when internal calls fail.

## [1.1.0] - 2026-06-20

### Added

- New `plugins/llm-statusline.toast.ts` server plugin that emits the rendered
  bar as a TUI toast (30s popup) on every `session.idle`, instead of writing to
  the log panel.
- Vendored `python/` directory: a self-contained copy of the upstream statusline
  script (`session_tokens.py`, `lib/`, `pricing.json`, `statusline.env.json`).
  The plugin no longer depends on any Claude Code install at the filesystem level.
- `OPENCODE_PROJECT_DIR` environment variable as an optional override for the
  folder displayed in the toast (useful when launching OpenCode from `~` while
  working in a deeper project).

### Changed

- Cache directory moved from `~/.cache/llm-quota-bar/` to
  `~/.cache/opencode-llm-statusline/` to keep this plugin's state separate from
  the upstream Claude Code statusline.
- `.gitignore` now ignores `python/__pycache__/` and `python/lib/__pycache__/`.

### Known Limitations

- OpenCode 1.17.8 plugin SDK does not expose tool-call parts (`info.parts` is
  empty; `message.part.updated` events carry only `text` / `reasoning` /
  `step-finish` types). The toast therefore reflects the shell cwd (or the
  `OPENCODE_PROJECT_DIR` override) rather than the project the agent is actively
  reading.
- OpenCode's `tui.showToast` does not render ANSI escape codes; colors from the
  Python script are stripped before display. The Claude Code statusline keeps
  its full color output via the vendored script.

## [1.0.0] - 2026-06-20

### Added

- Initial release (`plugins/llm-statusline.ts`).
- Forwards OpenCode `session.idle` events to a `session_tokens.py` script,
  writing a Claude-Code-compatible JSONL so the upstream statusline logic
  (token totals, cache R/W, burn rate, cost, provider quota) can be reused
  unchanged.
- Logs the rendered bar through `client.app.log()` (visible in OpenCode's
  log panel).

[Unreleased]: https://github.com/philipecomputacao/opencode-llm-statusline/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/philipecomputacao/opencode-llm-statusline/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/philipecomputacao/opencode-llm-statusline/releases/tag/v1.0.0
