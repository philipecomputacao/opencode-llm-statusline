---
name: Bug report
about: Something is broken in the plugin or the vendored Python script
title: "[bug] "
labels: bug
---

## What happened

<!-- A clear, one-paragraph description of the bug. -->

## Steps to reproduce

1.
2.
3.

## Expected behaviour

<!-- What you expected to see, including the bar contents. -->

## Actual behaviour

<!-- What you actually saw (toast empty, wrong folder, crash, etc). -->

## Environment

- OpenCode version (`opencode --version`):
- Plugin variant (`llm-statusline.ts` or `llm-statusline.toast.ts`):
- Python version (`python3 --version`):
- Operating system:

## Logs

Paste the relevant excerpt of `~/.local/share/opencode/log/opencode.log`
(grep for `llm-statusline`):

```text
<paste here>
```

If the Python script itself failed, run it manually and paste the output:

```bash
echo '{"model":{"id":"<model-id>"},"workspace":{"current_dir":"<abs-path>"},"version":"opencode","context_window":{"used_percentage":0},"cost":{"total_duration_ms":0}}' \
  | CLAUDE_PROJECT_DIR=<abs-path> CLAUDE_SESSION_ID=test \
    python3 python/session_tokens.py
```
