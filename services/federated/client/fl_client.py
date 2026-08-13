#!/usr/bin/env python3
"""
Mycelial federated learning client (grower node).

Modernized from models/transformer/fl_client.py. The model, the synthetic data
generator, and the Laplace differential-privacy noise carried over unchanged;
the Flower wiring did not:

    fl.client.start_numpy_client(...)  ->  start_client(client=...to_client())
    server default 127.0.0.1:8081      ->  9092 (8081 is Anansi)

Usage:
    python fl_client.py --mode synth --node-id node1
    python fl_client.py --mode real  --node-id greenhouse-a --server 10.0.0.5:9092
"""
import argparse
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from flwr.client import NumPyClient, start_client

# model.py lives one level up and is shipped alongside this file on remote nodes.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model import (  # noqa: E402
    TinyTransformer, load_data, get_parameters, set_parameters, DEFAULT_SEQ_LEN,
)

try:
    from diffprivlib.mechanisms import Laplace
    _HAS_DIFFPRIVLIB = True
except ImportError:  # pragma: no cover - fallback keeps DP working without the dep
    _HAS_DIFFPRIVLIB = False


def _laplace_noise(epsilon, sensitivity=1.0):
    """One Laplace sample. Falls back to numpy if diffprivlib is unavailable,
    so a node missing that dependency still trains *with* privacy noise rather
    than silently training without it."""
    if _HAS_DIFFPRIVLIB:
        return Laplace(epsilon=epsilon, sensitivity=sensitivity).randomise(0)
    return float(np.random.laplace(loc=0.0, scale=sensitivity / epsilon))


class MycelialClient(NumPyClient):
    def __init__(self, model, X, y, epsilon=0.5, local_epochs=5, val_split=0.2):
        self.model = model
        self.epsilon = epsilon
        self.local_epochs = local_epochs

        # Hold out a validation slice so evaluate() reports generalization
        # rather than scoring the model on the data it just fit.
        split = max(1, int(len(X) * (1 - val_split)))
        self.X_train = torch.tensor(X[:split])
        self.y_train = torch.tensor(y[:split])
        self.X_val = torch.tensor(X[split:]) if split < len(X) else self.X_train
        self.y_val = torch.tensor(y[split:]) if split < len(y) else self.y_train

        self.criterion = nn.BCELoss()
        self.optimizer = optim.Adam(model.parameters(), lr=0.01)
        print(f"[Client] train={len(self.X_train)} val={len(self.X_val)} DP epsilon={epsilon}")

    def get_parameters(self, config):
        return get_parameters(self.model)

    def fit(self, parameters, config):
        set_parameters(self.model, parameters)
        self.model.train()
        last_loss = 0.0
        for _ in range(self.local_epochs):
            outputs = self.model(self.X_train).squeeze()
            loss = self.criterion(outputs, self.y_train)
            self.optimizer.zero_grad()
            loss.backward()
            # Differential privacy: perturb gradients before the step.
            for param in self.model.parameters():
                if param.grad is not None:
                    param.grad += _laplace_noise(self.epsilon)
            self.optimizer.step()
            last_loss = float(loss.item())
        return get_parameters(self.model), len(self.X_train), {"train_loss": last_loss}

    def evaluate(self, parameters, config):
        set_parameters(self.model, parameters)
        self.model.eval()
        with torch.no_grad():
            outputs = self.model(self.X_val).squeeze()
            loss = float(self.criterion(outputs, self.y_val).item())
            predictions = (outputs >= 0.5).float()
            accuracy = float((predictions == self.y_val).float().mean().item())
        return loss, len(self.X_val), {"accuracy": accuracy}


def main():
    parser = argparse.ArgumentParser(description="Mycelial federated learning client")
    parser.add_argument("--mode", choices=["real", "synth"], default="real")
    parser.add_argument("--type", choices=["cannabis", "mushroom"], default="cannabis")
    parser.add_argument("--node-id", default="node1")
    parser.add_argument("--server", default="127.0.0.1:9092")
    parser.add_argument("--epsilon", type=float, default=0.5)
    parser.add_argument("--seq-len", type=int, default=DEFAULT_SEQ_LEN)
    parser.add_argument("--local-epochs", type=int, default=5)
    args = parser.parse_args()

    print(f"=== Mycelial FL client: {args.node_id} ===")
    print(f"mode={args.mode} server={args.server} DP epsilon={args.epsilon}")

    try:
        X, y = load_data(mode=args.mode, grow_type=args.type, seq_len=args.seq_len)
    except (FileNotFoundError, KeyError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if len(X) == 0:
        print("ERROR: dataset produced 0 sequences - is seq_len longer than the data?")
        sys.exit(1)
    print(f"[Data] X={X.shape} y={y.shape}")

    model = TinyTransformer(input_dim=X.shape[2])
    client = MycelialClient(model, X, y, epsilon=args.epsilon,
                            local_epochs=args.local_epochs)

    try:
        start_client(server_address=args.server, client=client.to_client())
    except Exception as e:
        print(f"ERROR: could not run FL round against {args.server}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
