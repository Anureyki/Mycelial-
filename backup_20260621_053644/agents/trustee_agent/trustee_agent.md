---
agent_id: trustee.mycelial
type: Data Sovereignty & Fiduciary
capabilities:
  - verify_sovereignty
  - audit_trust
hooks: {}
permissions:
  - read: ~/mycelial/databases/sqlite/knowledge_base.db
  - write: ~/mycelial/databases/sqlite/knowledge_base.db
---
# Trustee Agent

Ensures data sovereignty, fiduciary duties, and trust compliance.
