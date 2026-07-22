---
agent_id: datagatherer.mycelial
type: Data Acquisition
capabilities:
  - search
  - check_updates
  - fetch_api
  - scrape_web
hooks:
  pre: pre_gather.sh
  post: post_gather.sh
permissions:
  - read: ~/mycelial/sources/
  - write: ~/mycelial/sources/
  - network: yes
---
- search
- fetch_url
- fetch_document
