# Boss Agent Knowledge Base (project-state CAG)

Static, cross-project reference material Boss should have on hand when
reasoning about a project or answering a question via `answer_question`:
project templates, standard workflow stage definitions, cross-domain
glossaries. Same mechanism as the other agents' knowledge bases (see
`knowledge_base/legal_agent/README.md`) - drop `.txt`/`.md`/`.json`/`.csv`
files in subfolders, sub-folder name becomes the category, refreshed every
5 minutes or on demand via the `refresh_cache` A2A task on port 8000.

This is deliberately separate from the relationship graph (`state/graph.db`,
see `core/graph_manager.py`) - the cache holds static background knowledge,
the graph holds the live, structured relationship data Boss aggregates from
Legal/Accounting/Trust agents.
