# Trust Agent Knowledge Base

Same mechanism as Legal/Accounting Agents (see `knowledge_base/legal_agent/README.md`).

- `statutes/` — trust/probate code sections relevant to your jurisdiction (public domain source text).
- `trust_templates/` — your own trust document templates.
- `dictionary/` — fiduciary/trust term definitions (check licensing before including copyrighted material).

Refresh: poll every 5 minutes, or trigger manually:
```
curl -X POST http://localhost:9013/execute -H 'Content-Type: application/json' \
  -d '{"task":"refresh_cache","args":[]}'
```
