#!/usr/bin/env python3
"""
Analyzer Agent – Reads outcome logs, detects patterns, recommends MD upgrades.
Implements SDAR (Self-Distilled Agentic Reinforcement Learning) for signal gating.
"""

import os, sys, json, argparse, glob
from datetime import datetime
from collections import defaultdict

BASE = os.path.expanduser("~/mycelial")
KNOWLEDGE_DIR = os.path.join(BASE, "knowledge")
REPORTS_DIR = os.path.join(BASE, "reports")
AGENTS_DIR = os.path.join(BASE, "agents")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | analyzer_agent | {msg}\n")
    print(msg)

# ---------- SDAR: Signal Scoring ----------
def score_outcome(data):
    """Score the quality of an outcome signal (0.0–1.0)."""
    score = 0.5  # neutral base

    # Success boosts score
    if data.get("success", False):
        score += 0.3
    else:
        score -= 0.2

    # High confidence boosts score
    if data.get("confidence", 0) > 0.8:
        score += 0.2

    # Errors reduce score
    if data.get("error") and len(data["error"]) > 0:
        score -= 0.3

    # Multiple retries suggest instability
    if data.get("retries", 0) > 2:
        score -= 0.1

    # If task was complex and successful, boost
    if data.get("complexity", 0) > 0.7 and data.get("success", False):
        score += 0.1

    return max(0, min(1, score))

def should_learn_from(data):
    """Smart gate: only learn from good signals (score > 0.6)."""
    return score_outcome(data) > 0.6

# ---------- Core Analysis ----------
def analyze_outcomes():
    """Read all outcome JSON files, aggregate, and apply SDAR gating."""
    if not os.path.exists(KNOWLEDGE_DIR):
        log("⚠️ Knowledge directory not found.")
        return {}, 0, 0

    outcome_files = glob.glob(os.path.join(KNOWLEDGE_DIR, "outcome_*.json"))
    outcome_files += glob.glob(os.path.join(KNOWLEDGE_DIR, "learn_summary_*.json"))
    outcome_files += glob.glob(os.path.join(KNOWLEDGE_DIR, "output_*.json"))

    if not outcome_files:
        log("⚠️ No outcome files found.")
        return {}, 0, 0

    total_outcomes = 0
    learned_outcomes = 0
    failure_summary = defaultdict(lambda: {"total": 0, "failures": 0, "tasks": defaultdict(int), "learned": 0, "score_sum": 0.0})

    for fpath in outcome_files:
        try:
            with open(fpath, 'r') as f:
                data = json.load(f)
            agent = data.get('agent', 'unknown')
            task = data.get('task', 'unknown')
            success = data.get('success', False)

            # SDAR: score and gate
            score = score_outcome(data)
            learn = should_learn_from(data)

            total_outcomes += 1
            failure_summary[agent]["total"] += 1
            failure_summary[agent]["score_sum"] += score

            if learn:
                learned_outcomes += 1
                failure_summary[agent]["learned"] += 1

            if not success:
                failure_summary[agent]["failures"] += 1
                failure_summary[agent]["tasks"][task] += 1
                if "error" in data and data["error"]:
                    failure_summary[agent]["last_error"] = data["error"]

            if not learn and not success:
                # Bad signal – log but don't learn
                log(f"📉 Ignored low-quality signal from {agent}: score={score:.2f}")

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            log(f"⚠️ Error reading {fpath}: {e}")

    log(f"📊 Analyzed {total_outcomes} outcomes. Learned from {learned_outcomes} high-quality signals.")
    return failure_summary, total_outcomes, learned_outcomes

# ---------- Recommendation Generation ----------
def generate_recommendations(failure_summary):
    """Generate MD upgrade suggestions based on high-quality failures."""
    recommendations = []
    for agent, stats in failure_summary.items():
        if stats["failures"] == 0:
            continue

        failure_rate = stats["failures"] / max(stats["total"], 1)
        learned_rate = stats["learned"] / max(stats["total"], 1)

        # Only recommend if we have enough learned signals
        if stats["learned"] < 2:
            continue

        if failure_rate > 0.5 and learned_rate > 0.3:
            recommendations.append({
                "agent": agent,
                "issue": f"Failure rate {failure_rate:.2%} with {stats['learned']} learned signals",
                "suggestion": "Review agent logic or add more validation hooks.",
                "criticality": "high",
                "confidence": min(0.9, learned_rate + 0.2)
            })
        elif failure_rate > 0.2 and learned_rate > 0.2:
            recommendations.append({
                "agent": agent,
                "issue": f"Failure rate {failure_rate:.2%}",
                "suggestion": "Consider adding retries or pre-condition checks.",
                "criticality": "medium",
                "confidence": min(0.8, learned_rate + 0.1)
            })

        # Check for specific error patterns
        error = stats.get("last_error", "")
        if "FileNotFoundError" in error or "No such file" in error:
            recommendations.append({
                "agent": agent,
                "issue": "File not found errors",
                "suggestion": "Add a pre-action hook to validate file existence.",
                "criticality": "high",
                "confidence": 0.85
            })
        if "ImportError" in error or "ModuleNotFoundError" in error:
            recommendations.append({
                "agent": agent,
                "issue": "Missing dependencies",
                "suggestion": "Check requirements.txt or venv activation.",
                "criticality": "high",
                "confidence": 0.9
            })

    return recommendations

# ---------- Reporting ----------
def save_report(recommendations, failure_summary, total_outcomes, learned_outcomes):
    """Save report to REPORTS_DIR."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_outcomes": total_outcomes,
        "learned_outcomes": learned_outcomes,
        "learning_rate": learned_outcomes / max(total_outcomes, 1),
        "failure_summary": dict(failure_summary),
        "recommendations": recommendations,
        "criticality": "low"
    }
    if any(r.get("criticality") == "high" for r in recommendations):
        report["criticality"] = "high"
    elif recommendations:
        report["criticality"] = "medium"

    fname = os.path.join(REPORTS_DIR, f"analyzer_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(fname, 'w') as f:
        json.dump(report, f, indent=2)
    log(f"📄 Report saved to {fname}")
    return report

# ---------- Main Dispatch ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, help="Task: analyze_outcomes, generate_recommendations, or report_to_boss")
    args = parser.parse_args()

    if args.task == "analyze_outcomes":
        failure_summary, total, learned = analyze_outcomes()
        print(json.dumps(failure_summary, indent=2))
        return

    if args.task == "generate_recommendations":
        failure_summary, _, _ = analyze_outcomes()
        recs = generate_recommendations(failure_summary)
        print(json.dumps(recs, indent=2))
        return

    if args.task == "report_to_boss":
        failure_summary, total, learned = analyze_outcomes()
        recs = generate_recommendations(failure_summary)
        report = save_report(recs, failure_summary, total, learned)
        print(f"ANALYZER_REPORT: criticality={report['criticality']}, recommendations={len(report['recommendations'])}, learning_rate={report['learning_rate']:.2f}")
        return

    log(f"⚠️ Unknown task: {args.task}")
    sys.exit(1)

if __name__ == "__main__":
    main()
