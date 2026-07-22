---
agent_id: security.mycelial
type: Security & Validation
capabilities:
  - scan_file
  - quarantine
  - eliminate
  - check_domain
hooks:
  pre: pre_scan.sh
  post: post_scan.sh
permissions:
  - read: ~/mycelial/*
  - write: ~/mycelial/quarantine/
  - execute: ~/mycelial/hooks/*
---
