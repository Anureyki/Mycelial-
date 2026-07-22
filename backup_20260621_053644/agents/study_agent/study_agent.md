---
agent_id: study.mycelial
type: Document Study & IPFS Storage
capabilities:
  - study_document
  - pin_to_ipfs
hooks: {}
permissions:
  - read: ~/mycelial/databases/sqlite/knowledge_base.db
  - write: ~/mycelial/databases/sqlite/knowledge_base.db
  - execute: /usr/bin/ipfs
---
# Study Agent

Fetches documents, pins them to IPFS, and stores CIDs in the knowledge base. Used by Boss to permanently store trusted resources.
