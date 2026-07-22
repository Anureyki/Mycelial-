---
agent_id: commerce.mycelial
type: Commerce & Billing
capabilities:
  - invoice
  - billing
  - contract_analysis
hooks: {}
permissions:
  - read: ~/mycelial/databases/sqlite/knowledge_base.db
  - write: ~/mycelial/databases/sqlite/knowledge_base.db
---
# Commerce Agent

Handles billing, invoicing, contracts, and financial analysis.
