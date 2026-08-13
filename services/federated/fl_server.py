#!/usr/bin/env python3
"""
Mycelial Flower server.

Run as a subprocess by services/federated/service.py (port 8017) rather than as
a thread, because Flower's start_server() blocks until every round completes and
offers no graceful mid-run stop - a separate process can simply be terminated.

Replaces the retired agents/boss_agent/fl_server.py, which was five lines
duplicated twice in one file and bound 0.0.0.0:8081, colliding with Anansi.
"""
import argparse
import json
import os
import sys

import flwr as fl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy import build_strategy, STATE_DIR  # noqa: E402

DEFAULT_ADDRESS = "0.0.0.0:9092"  # NOT 8081 - that is Anansi
HISTORY_FILE = os.path.join(STATE_DIR, "history.json")


def main():
    parser = argparse.ArgumentParser(description="Mycelial federated learning server")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--min-clients", type=int, default=2)
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    args = parser.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    print(f"[FL] Serving on {args.address} | {args.rounds} rounds | "
          f"waiting for {args.min_clients} client(s)", flush=True)

    history = fl.server.start_server(
        server_address=args.address,
        config=fl.server.ServerConfig(num_rounds=args.rounds),
        strategy=build_strategy(min_clients=args.min_clients, rounds=args.rounds),
    )

    with open(HISTORY_FILE, "w") as f:
        json.dump({
            "losses_distributed": history.losses_distributed,
            "losses_centralized": history.losses_centralized,
            "metrics_distributed": history.metrics_distributed,
            "metrics_centralized": history.metrics_centralized,
        }, f, indent=2, default=str)

    print(f"[FL] Finished {args.rounds} round(s); history -> {HISTORY_FILE}", flush=True)


if __name__ == "__main__":
    main()
