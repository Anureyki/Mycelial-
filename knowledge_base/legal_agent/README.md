# Legal Agent Knowledge Base

Drop plain-text or markdown source documents in the subfolders below. The
agent's CAG layer (`AgentBase.init_cag`, see `core/base_agent.py`) walks this
tree at startup and on every refresh, indexing every `.txt`, `.md`, `.json`,
and `.csv` file it finds. Sub-folder name becomes the document's `category`.

- `statutes/` — U.S. Code text (e.g. Title 26 sections), one file per section
  or logical chunk. **The federal statutory text itself is public domain** —
  pull it yourself from an official/public source (uscode.house.gov,
  law.cornell.edu/uscode) and drop the plain text in here. This repo does not
  ship any statute text.
- `irs_publications/` — IRS regulations/publications, same idea. Public
  domain (U.S. government works), fetch from irs.gov.
- `dictionary/` — legal term definitions. **Black's Law Dictionary is
  copyrighted** — do not copy its text in here without a license. Either
  license it, or use an open/CC-licensed source (e.g. Cornell LII's Wex), or
  write your own paraphrased definitions.
- `contract_templates/` — your own contract templates/clause library.

## File format
Plain text or markdown, one document per file. Filename is used as part of
the doc id, so name files descriptively, e.g. `26_usc_61_gross_income.txt`.

## Refresh
The agent polls this directory every 5 minutes (see `watch_interval` in
`legal_agent.py`'s `init_cag` call) and re-indexes changed/added/removed
files. You can also trigger it on demand via the `refresh_cache` A2A task,
or from cron:

```
curl -X POST http://localhost:9011/execute -H 'Content-Type: application/json' \
  -d '{"task":"refresh_cache","args":[]}'
```

`sample_placeholder.txt` in each folder is NOT real legal content — it only
exists so `query_cache` / `parse_contract` have something to retrieve during
testing. Delete it once you've added real sources.

## Transcripts

`transcripts/` holds talks, lectures and recorded streams ingested with
`tools/ingest_media.py`. Every file opens with a **stance** line, which is the
point of the directory:

| Stance | Means |
|--------|-------|
| `authority` | Binding or persuasive authority |
| `primary_source` | A primary source document |
| `commentary` | Commentary *about* the law, not the law |
| `advocacy` | An argument being made, recorded as an argument — **not** a statement of what the law is |
| `unknown` | Standing not established |

This tree is searched by CAG similarity. It is **not** `reference/`, which holds
codified rules retrieved by exact headword or citation. Filing commentary where
the agent looks for authority is how an opinion gets read back as law.

```bash
python3 tools/ingest_media.py <url|audio-file> \
  --agent legal_agent --title "..." --stance advocacy --source "..."
curl -X POST localhost:9011/execute -d '{"task":"refresh_cache","args":{}}'
```

Captions are used when a video has them; local Whisper runs only when it does
not, because a 110-minute transcription on this CPU costs hours.
