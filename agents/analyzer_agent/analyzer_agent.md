---
agent_id: analyzer.mycelial
type: Outcome Analysis & Recommendation
capabilities:
  - analyze_outcomes
  - generate_recommendations
  - report_to_boss
permissions:
  - read: ~/mycelial/knowledge/
  - write: ~/mycelial/reports/
  - read: ~/mycelial/agents/*.md
---
# Analyzer Agent – Mycelial Network

## 🧠 Purpose

This agent reads task outcome logs from `~/mycelial/knowledge/`, detects patterns (e.g., recurring failures, specific error types), and generates recommendations for improving agent definitions (`.md` files) or hooks.

It produces a structured report for the Boss agent, which can then trigger regeneration (`sync_all`) if needed.

## 🔧 Capabilities

- **analyze_outcomes** – Scan all outcome JSON files, aggregate by agent and task, count successes/failures.
- **generate_recommendations** – Based on patterns, suggest changes to `.md` files (e.g., add a new capability, adjust a hook).
- **report_to_boss** – Write a summary report to `~/mycelial/reports/` and output a brief summary.

## 📂 Report Format

Reports are saved as `~/mycelial/reports/analyzer_report_<timestamp>.json` with fields:
- `timestamp`
- `failure_summary` (per agent)
- `recommendations` (list of suggested changes)
- `criticality` (low/medium/high)

## 🧪 Example Recommendation

```json
{
  "agent": "coding_agent",
  "issue": "File not found errors in 5 outcomes",
  "suggestion": "Add a pre-action hook to validate file existence before processing",
  "criticality": "high"
}
```

## 🔗 Relation to Other Agents

- **Boss Agent** – receives the report and decides whether to trigger `sync_all` or notify the human.
- **Coding Agent** – may be asked to implement suggested changes to hooks or scripts.

## 🔄 Version History

| Date | Version | Change |
|------|---------|--------|
| 2026‑06‑18 | 0.1 | Initial creation |

