#!/usr/bin/env python3
"""
Analyzer Agent – Outcome Analysis & Recommendation
Scans task outcome logs, detects patterns, generates recommendations.
"""
import sys
import os
import time
import json
import glob
import ast
from datetime import datetime
from collections import defaultdict

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
KNOWLEDGE_DIR = os.path.join(BASE, "knowledge")
REPORTS_DIR = os.path.join(BASE, "reports")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

class AnalyzerAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="analyzer_agent",
            port=9006,
            capabilities=["analyze_outcomes", "generate_recommendations", "report_to_boss"],
            role="analytics"
        )
        self.log("🔍 Analyzer Agent started.")

    def _load_outcomes(self):
        """Load all outcome JSON files from KNOWLEDGE_DIR."""
        outcomes = []
        pattern = os.path.join(KNOWLEDGE_DIR, "*.json")
        for filepath in glob.glob(pattern):
            try:
                with open(filepath, "r") as f:
                    content = f.read().strip()
                    try:
                        data = json.loads(content)
                        outcomes.append(data)
                    except json.JSONDecodeError:
                        lines = content.splitlines()
                        for line in lines:
                            line = line.strip()
                            if line:
                                try:
                                    data = json.loads(line)
                                    outcomes.append(data)
                                except json.JSONDecodeError:
                                    self.log(f"Skipping malformed line in {filepath}")
            except Exception as e:
                self.log(f"Failed to load {filepath}: {e}")
        return outcomes

    def _aggregate_outcomes(self, outcomes):
        stats = defaultdict(lambda: defaultdict(lambda: {"success": 0, "failure": 0}))
        errors_by_agent = defaultdict(list)
        for entry in outcomes:
            agent = entry.get("agent_id", "unknown")
            task = entry.get("task", "unknown")
            success = entry.get("success", False)
            stats[agent][task]["success" if success else "failure"] += 1
            if not success:
                errors_by_agent[agent].append({
                    "task": task,
                    "error": entry.get("error", "unknown error"),
                    "timestamp": entry.get("timestamp", "")
                })
        return stats, errors_by_agent

    def _generate_recommendations(self, stats, errors_by_agent):
        recommendations = []
        for agent, errors in errors_by_agent.items():
            error_types = defaultdict(int)
            for e in errors:
                error_text = e.get("error", "")
                if not error_text:
                    error_text = "Unknown error"
                if "file" in error_text.lower() or "not found" in error_text.lower():
                    error_types["FileNotFound"] += 1
                elif "timeout" in error_text.lower():
                    error_types["Timeout"] += 1
                elif "permission" in error_text.lower():
                    error_types["Permission"] += 1
                else:
                    error_types["Other"] += 1

            for err_type, count in error_types.items():
                if count >= 2:
                    if err_type == "FileNotFound":
                        rec = {
                            "agent": agent,
                            "issue": f"File not found errors ({count} occurrences)",
                            "suggestion": "Add a pre-action hook to validate file existence.",
                            "criticality": "high"
                        }
                    elif err_type == "Timeout":
                        rec = {
                            "agent": agent,
                            "issue": f"Timeout errors ({count} occurrences)",
                            "suggestion": "Increase timeout or optimise task logic.",
                            "criticality": "medium"
                        }
                    elif err_type == "Permission":
                        rec = {
                            "agent": agent,
                            "issue": f"Permission errors ({count} occurrences)",
                            "suggestion": "Check file permissions or run with appropriate privileges.",
                            "criticality": "high"
                        }
                    else:
                        rec = {
                            "agent": agent,
                            "issue": f"General errors ({count} occurrences)",
                            "suggestion": "Review task implementation and logs.",
                            "criticality": "low"
                        }
                    recommendations.append(rec)
        return recommendations

    def _store_memory_json(self, key, value):
        """Store a JSON‑serializable value as a JSON string."""
        self.store_own_memory(key, json.dumps(value))

    def _retrieve_memory_json(self, key):
        """Retrieve and parse a JSON string from memory."""
        raw = self.retrieve_own_memory(key)
        if not raw:
            return None
        # If it's already a dict/list, return it directly
        if isinstance(raw, (dict, list)):
            return raw
        # If it's a string, try to parse it
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw)
                    return parsed
                except (ValueError, SyntaxError):
                    return None
        return None

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "analyze_outcomes":
            outcomes = self._load_outcomes()
            if not outcomes:
                return {"result": "No outcome logs found."}
            stats, errors = self._aggregate_outcomes(outcomes)
            self._store_memory_json("last_analysis", {"stats": stats, "errors": errors})
            summary = {
                "total_entries": len(outcomes),
                "agents": list(stats.keys()),
                "error_count": sum(len(e) for e in errors.values())
            }
            return {"result": "Analysis complete", "summary": summary}

        elif task == "generate_recommendations":
            analysis = self._retrieve_memory_json("last_analysis")
            if analysis and "stats" in analysis and "errors" in analysis:
                stats = analysis["stats"]
                errors = analysis["errors"]
            else:
                outcomes = self._load_outcomes()
                if not outcomes:
                    return {"error": "No outcome logs to analyze."}
                stats, errors = self._aggregate_outcomes(outcomes)
                self._store_memory_json("last_analysis", {"stats": stats, "errors": errors})
            recommendations = self._generate_recommendations(stats, errors)
            # Ensure recommendations is a list of dicts
            if not isinstance(recommendations, list):
                recommendations = []
            self._store_memory_json("last_recommendations", recommendations)
            return {"result": "Recommendations generated", "recommendations": recommendations}

        elif task == "report_to_boss":
            recs = self._retrieve_memory_json("last_recommendations")
            if not recs:
                return {"error": "No recommendations available. Run generate_recommendations first."}
            # Normalize the retrieved data to a list of dicts
            if isinstance(recs, dict):
                # If the dict has a 'recommendations' key, use that value
                if "recommendations" in recs:
                    recs = recs["recommendations"]
                else:
                    # If it's a dict of recommendations, convert it to a list
                    recs = [recs]
            if not isinstance(recs, list):
                return {"error": f"Unexpected recommendations type: {type(recs)}. Expected list."}
            # Validate each entry is a dict
            recs = [r for r in recs if isinstance(r, dict)]
            if not recs:
                return {"error": "No valid recommendations found."}
            report = {
                "timestamp": datetime.now().isoformat(),
                "recommendations": recs,
                "criticality": "high" if any(r.get("criticality") == "high" for r in recs) else "low"
            }
            report_path = os.path.join(REPORTS_DIR, f"analyzer_report_{int(time.time())}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            self.log(f"Report saved to {report_path}")
            boss_response = self.send_a2a("boss_agent", "alert", {
                "message": f"Analyzer report: {len(recs)} recommendations, criticality {report['criticality']}",
                "report_path": report_path,
                "recommendations": recs
            })
            return {
                "result": "Report sent to Boss",
                "report_path": report_path,
                "boss_response": boss_response
            }

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = AnalyzerAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
