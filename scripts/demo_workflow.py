#!/usr/bin/env python3
"""
Demo: event-driven project workflow.

Drives a project through proposal -> negotiation -> signature -> payment ->
completion by calling Boss's `publish_event` task over HTTP A2A. Boss
publishes each stage to MQTT topic mycelial/project/<project_id>/stage;
Legal, Accounting, and Trust Agents (all subscribed via
AgentBase.subscribe_project_events()) react to the stages relevant to them
and write an audit-log entry (see their on_project_event overrides).

This requires the full stack to be running (./start_all.sh) - Boss on 8000,
MQTT broker reachable at localhost:1883, and Legal/Accounting/Trust Agents
up so they can react. Reactions are illustrative logging (see each agent's
on_project_event docstring), not autonomous contract drafting or invoicing.

Usage:
    python3 scripts/demo_workflow.py [project_id]
"""
import sys
import time
import json
import requests

BOSS_URL = "http://localhost:8000/execute"
STAGES = ["proposal", "negotiation", "signature", "payment", "completion"]


def publish_stage(project_id, stage, extra=None):
    payload = {
        "task": "publish_event",
        "args": {
            "project_id": project_id,
            "event_type": "stage",
            "data": {"stage": stage, **(extra or {})},
        },
        "sender": "demo_workflow",
    }
    resp = requests.post(BOSS_URL, json=payload, timeout=10)
    return resp.json()


def main():
    project_id = sys.argv[1] if len(sys.argv) > 1 else f"demo_project_{int(time.time())}"
    print(f"Driving project '{project_id}' through: {' -> '.join(STAGES)}\n")
    for stage in STAGES:
        print(f"--> publishing stage: {stage}")
        try:
            result = publish_stage(project_id, stage)
            print(f"    Boss response: {json.dumps(result)}")
        except requests.exceptions.ConnectionError:
            print("    ERROR: could not reach Boss at http://localhost:8000 - is the stack running (./start_all.sh)?")
            sys.exit(1)
        time.sleep(2)
    print(
        f"\nDone. Check logs/audit.log for '{project_id}' entries from legal_agent "
        f"(negotiation), accounting_agent (payment), and boss_agent (every stage) "
        f"to see the reactions:\n"
        f"  grep {project_id} logs/audit.log"
    )


if __name__ == "__main__":
    main()
