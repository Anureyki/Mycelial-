# Accounting Agent Knowledge Base

Same mechanism as the Legal Agent's knowledge base (see
`knowledge_base/legal_agent/README.md`) — plain text/markdown/json/csv files,
indexed by the CAG layer in `core/base_agent.py`, sub-folder = category.

- `irs_forms/` — IRS form instructions/summaries (1099, W-2, 1040, etc.).
  Public domain, fetch from irs.gov.
- `gaap_ifrs/` — GAAP/IFRS standard summaries. Check licensing before
  including verbatim standard text (FASB/IFRS Foundation material is often
  restricted); prefer your own summaries or openly licensed material.
- `instruments/` — **your own** promissory notes / loan agreements. Private
  data — place manually, never auto-fetched or fabricated.
- `statements/` — **your own** bank/credit/utility statements. Private data —
  same rule: manual placement only.
- `trust_estate/` — **your own** trust/estate documents. Private data.

The last three folders are expected to contain sensitive personal/financial
records. Nothing in this codebase fetches, generates, or guesses their
contents — the agent only ever reads what you put there.

## Refresh
Poll interval and manual trigger work the same as Legal Agent, on port 9012:

```
curl -X POST http://localhost:9012/execute -H 'Content-Type: application/json' \
  -d '{"task":"refresh_cache","args":[]}'
```

`sample_placeholder.txt` files exist only to exercise the cache pipeline in
testing - delete them once real sources are in place.
