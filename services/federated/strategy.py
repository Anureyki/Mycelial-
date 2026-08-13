#!/usr/bin/env python3
"""
FedAvg strategy for Mycelial, with per-round metric capture.

Flower's start_server() only returns its History once every round has finished.
That is too late to answer "what round are we on?" while training is running, so
this strategy writes each round's result to disk as it completes and the control
service reads that file.
"""
import json
import os
import threading

from flwr.server.strategy import FedAvg

BASE = os.path.expanduser("~/mycelial")
STATE_DIR = os.path.join(BASE, "state", "federated")
ROUNDS_FILE = os.path.join(STATE_DIR, "rounds.json")


def _weighted_average(metrics):
    """Aggregate client metrics weighted by each client's example count.

    A plain mean would let a node with 10 samples count as much as one with
    10,000 - which defeats the point of averaging across uneven grow sites.
    """
    if not metrics:
        return {}
    total = sum(num for num, _ in metrics)
    if total == 0:
        return {}
    keys = set()
    for _, m in metrics:
        keys.update(k for k, v in m.items() if isinstance(v, (int, float)))
    return {
        key: sum(num * m.get(key, 0) for num, m in metrics) / total
        for key in keys
    }


class MycelialFedAvg(FedAvg):
    """FedAvg that records each round to state/federated/rounds.json."""

    def __init__(self, *args, total_rounds=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.total_rounds = total_rounds
        self._lock = threading.Lock()
        os.makedirs(STATE_DIR, exist_ok=True)
        self._write({"total_rounds": total_rounds, "completed": 0, "rounds": []})

    def _write(self, payload):
        tmp = f"{ROUNDS_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, ROUNDS_FILE)  # atomic: readers never see a half-written file

    def _read(self):
        try:
            with open(ROUNDS_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"total_rounds": self.total_rounds, "completed": 0, "rounds": []}

    def _record(self, server_round, kind, num_clients, loss, metrics):
        with self._lock:
            state = self._read()
            entry = next((r for r in state["rounds"] if r["round"] == server_round), None)
            if entry is None:
                entry = {"round": server_round}
                state["rounds"].append(entry)
            entry[kind] = {
                "clients": num_clients,
                "loss": loss,
                "metrics": metrics or {},
            }
            state["completed"] = max(state.get("completed", 0), server_round)
            self._write(state)

    def aggregate_fit(self, server_round, results, failures):
        params, metrics = super().aggregate_fit(server_round, results, failures)
        self._record(server_round, "fit", len(results),
                     None, _weighted_average(
                         [(r.num_examples, r.metrics) for _, r in results]))
        return params, metrics

    def aggregate_evaluate(self, server_round, results, failures):
        loss, metrics = super().aggregate_evaluate(server_round, results, failures)
        self._record(server_round, "evaluate", len(results), loss,
                     _weighted_average(
                         [(r.num_examples, r.metrics) for _, r in results]))
        return loss, metrics


def build_strategy(min_clients=2, rounds=0):
    """FedAvg configured so a round only runs once enough nodes are present."""
    return MycelialFedAvg(
        total_rounds=rounds,
        min_fit_clients=min_clients,
        min_evaluate_clients=min_clients,
        min_available_clients=min_clients,
        fit_metrics_aggregation_fn=_weighted_average,
        evaluate_metrics_aggregation_fn=_weighted_average,
    )
