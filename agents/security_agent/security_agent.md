---
agent_id: security.mycelial
type: Security & Validation
capabilities:
  - scan_file
  - quarantine
  - eliminate
  - check_domain
permissions:
  - read: ~/mycelial/*
  - write: ~/mycelial/quarantine/
  - write: ~/mycelial/state/blocklist.txt
---
