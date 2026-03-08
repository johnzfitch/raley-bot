## What

## Why

## Checklist

- [ ] No secrets (cookies, tokens, API keys) in diff
- [ ] Seven invariants preserved (curl-only, single cookie path, 0o600, URL encoding, cookie sanitization, read tools don't write, no secrets in output)
- [ ] Tests pass (`pytest`)
- [ ] No network calls in tests
