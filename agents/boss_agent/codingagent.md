---
agent_id: codingagent.mycelial
type: Coding & Automation
capabilities:
  - implement_recommendation
  - edit_file
  - run_command
  - crontab_add
  - crontab_list
  - crontab_remove
hooks:
  pre: pre_edit.sh
  post: post_edit.sh
permissions:
  - read: ~/mycelial/*
  - write: ~/AgTechAI/*.py
---
- implement_recommendation
