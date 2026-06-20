## Summary

<!-- One paragraph: what does this PR do and why? -->

## Related issues

<!-- Link any issues this PR closes or addresses. Use `Closes #123`. -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds behaviour)
- [ ] Breaking change (existing behaviour stops working)
- [ ] Documentation only
- [ ] Chore (CI, vendoring sync, repo hygiene)

## Plugin variant affected

- [ ] `plugins/llm-statusline.ts` (log panel)
- [ ] `plugins/llm-statusline.toast.ts` (toast popup)
- [ ] `python/` (vendored statusline script)
- [ ] `README.md` / docs
- [ ] CI / repo config

## Checklist

- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] Smoke test passes locally:
      `cd python && echo '{}' | CLAUDE_PROJECT_DIR=/tmp CLAUDE_SESSION_ID=test python3 session_tokens.py`
- [ ] No secrets, real API keys, or personal filesystem paths in the diff
- [ ] No new third-party runtime dependencies (Python stdlib + Node built-ins only)
- [ ] If vendored Python was synced from upstream, the diff against
      `philipecomputacao/claude-llm-quota-bar` is documented in the commit body
