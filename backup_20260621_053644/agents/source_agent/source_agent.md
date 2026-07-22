---
agent_id: source.mycelial
type: Trusted Source Query
capabilities:
  - query_sources
  - save_source
hooks: {}
permissions:
  - read: ~/mycelial/databases/sqlite/trusted_sources.db
  - write: ~/mycelial/databases/sqlite/trusted_sources.db
---
# Source Agent

Queries and manages the trusted sources database. Used by Boss to find authoritative resources for research.
