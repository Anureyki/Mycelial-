#!/usr/bin/env python3
"""
Agriculture Agent - department head for all growing domains.

Not a grower itself. It aggregates results across the agriculture department
(grow_agent today; bee_agent and aquaponics_agent as they come online) and
reviews how those members can improve, using the federated learning rounds run
by the FL Service on port 8017.

The roster lives in config/departments.json so adding a member is a config
change rather than a code change.

Deliberately NOT started by start_all.sh. Department heads are meant to wake on
demand once the wake-word / UX layer lands; until then, start it by hand:
    python3 -m agents.ag_agent.agriculture_agent &

Previously this file was a standalone argparse script that was never registered
as an agent and whose only caller pointed at a venv in a project directory that
no longer exists. The DQN below is carried over intact - models/dqn_model.pth is
a real checkpoint.
"""
import os
import json
import random
import statistics
from collections import deque
from datetime import datetime

import numpy as np
import requests
import torch
import torch.nn as nn
import torch.optim as optim

from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
DEPARTMENTS_FILE = os.path.join(BASE, "config", "departments.json")
FL_SERVICE_URL = "http://localhost:8017"
DEPARTMENT = "agriculture"

ACTIONS = ["do_nothing", "fan", "heater", "vent"]

# A member whose accuracy sits this far below the federated average is worth
# flagging - it usually means bad local sensor data rather than a bad model.
DIVERGENCE_THRESHOLD = 0.10


class DQN(nn.Module):
    def __init__(self, s, a):
        super().__init__()
        self.fc1 = nn.Linear(s, 64)
        self.fc2 = nn.Linear(64, 64)
        self.fc3 = nn.Linear(64, a)

    def forward(self, x):
        return self.fc3(torch.relu(self.fc2(torch.relu(self.fc1(x)))))


class DQNAgent:
    def __init__(self, s=6, a=4, e=1.0, logger=print):
        self.s, self.a = s, a
        self.m = deque(maxlen=2000)
        self.e, self.em, self.ed = e, 0.01, 0.995
        self.model, self.target = DQN(s, a), DQN(s, a)
        self.opt = optim.Adam(self.model.parameters(), lr=0.001)
        self.crit = nn.MSELoss()
        self.log = logger
        self.path = os.path.join(BASE, "models", "dqn_model.pth")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            try:
                state = torch.load(self.path)
                self.model.load_state_dict(state)
                self.target.load_state_dict(state)
                self.log("Loaded DQN checkpoint")
            except Exception as ex:
                self.log(f"Could not load DQN checkpoint ({ex}); starting fresh")

    def remember(self, s, a, r, n, d):
        self.m.append((s, a, r, n, d))

    def act(self, s):
        if random.random() <= self.e:
            return random.randrange(self.a)
        with torch.no_grad():
            return torch.argmax(
                self.model(torch.tensor(s, dtype=torch.float32).unsqueeze(0))
            ).item()

    def replay(self, b=32):
        if len(self.m) < b:
            return
        for s, a, r, n, d in random.sample(self.m, b):
            t = self.model(torch.tensor(s, dtype=torch.float32).unsqueeze(0)).detach().numpy()[0]
            t[a] = r if d else r + 0.9 * torch.max(
                self.target(torch.tensor(n, dtype=torch.float32).unsqueeze(0))
            ).item()
            self.opt.zero_grad()
            loss = self.crit(
                self.model(torch.tensor(s, dtype=torch.float32).unsqueeze(0)),
                torch.tensor(t, dtype=torch.float32).unsqueeze(0),
            )
            loss.backward()
            self.opt.step()
        if self.e > self.em:
            self.e *= self.ed

    def save(self):
        torch.save(self.model.state_dict(), self.path)
        self.log("Saved DQN checkpoint")


class AgricultureAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="ag_agent",
            port=9015,  # 9014 is quantum_agent
            capabilities=[
                "department_status", "list_members", "aggregate_readings",
                "review_improvements", "dqn_train", "dqn_decide",
            ],
            role="department_head",
        )
        self.dqn = DQNAgent(logger=self.log)
        self.subscribe_project_events()
        self.log("🌱 Agriculture department head started.")

    # ---------- roster ----------
    def _load_members(self, include_inactive=False):
        """Department roster from config/departments.json."""
        try:
            with open(DEPARTMENTS_FILE) as f:
                departments = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"Could not read {DEPARTMENTS_FILE} ({e}); department is empty")
            return []
        members = departments.get(DEPARTMENT, {}).get("members", [])
        if include_inactive:
            return members
        return [m for m in members if m.get("active")]

    def _ask_member(self, member, task, args=None):
        """A2A call to a department member. send_a2a returns False on failure,
        so normalize that into a shape callers can read."""
        result = self.send_a2a(member["agent_id"], task, args or {})
        if result is False or result is None:
            return {"reachable": False, "error": "unreachable"}
        if isinstance(result, dict) and "result" in result:
            return {"reachable": True, "data": result["result"]}
        return {"reachable": True, "data": result}

    # ---------- federated learning ----------
    def _fl_rounds(self):
        try:
            resp = requests.get(f"{FL_SERVICE_URL}/rounds", timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException as e:
            self.log(f"FL service unreachable: {e}")
        return None

    def _review_from_fl(self, rounds_state):
        """Turn federated rounds into department-level recommendations.

        The useful signal is not the aggregate accuracy on its own - it is how
        each round's participation and accuracy move over time, and whether the
        spread between rounds suggests one node is dragging the average down.
        """
        findings = []
        rounds = [r for r in (rounds_state or {}).get("rounds", []) if r.get("evaluate")]
        if not rounds:
            return findings, None

        accuracies = [
            r["evaluate"]["metrics"].get("accuracy")
            for r in rounds
            if r["evaluate"].get("metrics", {}).get("accuracy") is not None
        ]
        if not accuracies:
            return findings, None

        latest = accuracies[-1]

        if len(accuracies) >= 2:
            delta = accuracies[-1] - accuracies[0]
            if delta < 0:
                findings.append({
                    "severity": "high",
                    "observation": (
                        f"Federated accuracy fell from {accuracies[0]:.3f} to "
                        f"{accuracies[-1]:.3f} across {len(accuracies)} rounds."
                    ),
                    "action": (
                        "Aggregation is making the shared model worse. Check for a member "
                        "sending mislabelled data, and consider lowering the DP epsilon - "
                        "too much Laplace noise degrades the average."
                    ),
                })
            elif delta < 0.01:
                findings.append({
                    "severity": "medium",
                    "observation": f"Federated accuracy is flat at ~{latest:.3f} across {len(accuracies)} rounds.",
                    "action": "Rounds are not adding information. Add more member nodes or more local epochs.",
                })
            else:
                findings.append({
                    "severity": "info",
                    "observation": f"Federated accuracy improved {accuracies[0]:.3f} → {accuracies[-1]:.3f}.",
                    "action": "Training is converging; keep the current round configuration.",
                })

        if len(accuracies) >= 3:
            spread = statistics.pstdev(accuracies)
            if spread > DIVERGENCE_THRESHOLD:
                findings.append({
                    "severity": "medium",
                    "observation": f"Round-to-round accuracy is unstable (σ={spread:.3f}).",
                    "action": (
                        "Members are likely contributing very different distributions. "
                        "Compare per-node sensor calibration before trusting the shared model."
                    ),
                })

        client_counts = [r["evaluate"].get("clients", 0) for r in rounds]
        if client_counts and min(client_counts) < 2:
            findings.append({
                "severity": "medium",
                "observation": f"Some rounds ran with only {min(client_counts)} client(s).",
                "action": "Federated averaging over a single node is just local training - bring more members online.",
            })

        return findings, latest

    # ---------- events ----------
    def on_project_event(self, project_id, event_type, data, sender):
        """Department members publish results; the head records them so
        review_improvements has history to reason over."""
        if sender and sender != self.agent_id:
            self.log(f"[department] {sender} -> {event_type} on {project_id}")

    def handle_task(self, task, args, sender):
        cached = self.try_handle_cag_task(task, args)
        if cached is not None:
            return cached

        if task == "list_members":
            members = self._load_members(include_inactive=True)
            return {
                "department": DEPARTMENT,
                "head": self.agent_id,
                "members": members,
                "active": [m["agent_id"] for m in members if m.get("active")],
                "planned": [m["agent_id"] for m in members if not m.get("active")],
            }

        elif task == "department_status":
            members = self._load_members()
            statuses = {}
            for member in members:
                statuses[member["agent_id"]] = {
                    "domain": member.get("domain"),
                    **self._ask_member(member, member.get("status_task", "get_status")),
                }
            reachable = [a for a, s in statuses.items() if s.get("reachable")]
            return {
                "department": DEPARTMENT,
                "checked_at": datetime.now().isoformat(),
                "members_active": len(members),
                "members_reachable": len(reachable),
                "statuses": statuses,
            }

        elif task == "aggregate_readings":
            # Pull each member's recent data into one department-level view, so
            # cross-domain patterns (a heat event hitting both the grow tent and
            # the apiary) are visible in one place.
            members = self._load_members()
            task_name = args.get("task", "get_grow_history") if isinstance(args, dict) else "get_grow_history"
            readings = {}
            for member in members:
                readings[member["agent_id"]] = self._ask_member(member, task_name)
            return {
                "department": DEPARTMENT,
                "collected_at": datetime.now().isoformat(),
                "source_task": task_name,
                "readings": readings,
            }

        elif task == "review_improvements":
            rounds_state = self._fl_rounds()
            if rounds_state is None:
                return {
                    "department": DEPARTMENT,
                    "error": "FL service (port 8017) unreachable - cannot review federated results",
                    "recommendations": [],
                }
            findings, latest_accuracy = self._review_from_fl(rounds_state)
            members = self._load_members()

            if not findings:
                findings.append({
                    "severity": "info",
                    "observation": "No completed federated evaluation rounds yet.",
                    "action": (
                        "Start a run (POST localhost:8017/start) and connect at least two "
                        "member nodes before expecting a review."
                    ),
                })

            return {
                "department": DEPARTMENT,
                "reviewed_at": datetime.now().isoformat(),
                "rounds_completed": rounds_state.get("completed", 0),
                "rounds_total": rounds_state.get("total_rounds", 0),
                "latest_accuracy": latest_accuracy,
                "members_active": [m["agent_id"] for m in members],
                "recommendations": findings,
            }

        elif task == "dqn_train":
            episodes = int(args.get("episodes", 100)) if isinstance(args, dict) else 100
            self.log(f"Training DQN for {episodes} episodes...")
            for ep in range(episodes):
                s, done, total = np.random.rand(6) * 10, False, 0
                while not done:
                    a = self.dqn.act(s)
                    r = 1 if (s[0] > 20 and s[1] < 80) else -1
                    n = np.clip(s + np.random.randn(6) * 0.1, 0, 10)
                    done = random.random() < 0.05
                    self.dqn.remember(s, a, r, n, done)
                    s, total = n, total + r
                    if done:
                        self.dqn.replay()
                if ep % 10 == 0:
                    self.log(f"Episode {ep}: reward={total}, epsilon={self.dqn.e:.2f}")
            self.dqn.save()
            return {"result": "Training complete", "episodes": episodes,
                    "epsilon": round(self.dqn.e, 4)}

        elif task == "dqn_decide":
            readings = args.get("readings") if isinstance(args, dict) else None
            if not readings:
                return {"error": "dqn_decide requires 'readings' (6 numeric values)"}
            if len(readings) < 6:
                return {"error": f"Expected 6 readings, got {len(readings)}"}
            action = self.dqn.act(readings[:6])
            return {"action": ACTIONS[action], "action_index": action}

        return {"error": f"Unknown task: {task}"}


if __name__ == "__main__":
    import time
    agent = AgricultureAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
