#!/usr/bin/env python3
"""
Grower Client for Empirical Connections – Transformer Version
Supports:
  --mode real    : uses latest CSV from ~/grower-node/sensor_data
  --mode synth   : generates synthetic data on the fly (cannabis or mushroom)
  --type cannabis|mushroom   : for synthetic mode only
  --node-id      : identifier for multi-node testing
  --server       : Flower server address
  --epsilon      : differential privacy budget
"""

import argparse
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import flwr as fl
from diffprivlib.mechanisms import Laplace
import glob
import os
import sys
from datetime import datetime, timedelta

# -------------------------------
# 1. Transformer Model (replaces SimpleNN)
# -------------------------------
class TinyTransformer(nn.Module):
    def __init__(self, input_dim=6, d_model=32, nhead=4, num_layers=2, seq_len=10):
        super().__init__()
        self.embed = nn.Linear(input_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x: (batch, seq_len, input_dim)
        x = self.embed(x)               # (batch, seq_len, d_model)
        x = self.transformer(x)         # (batch, seq_len, d_model)
        x = x.mean(dim=1)               # (batch, d_model) – pool over time
        return self.sigmoid(self.fc(x))  # (batch, 1)

# -------------------------------
# 2. Synthetic Data Generator
# -------------------------------
def generate_synthetic_data(grow_type="cannabis", days=14, interval_hours=6, seq_len=10):
    """Returns X (sequences) and y (labels) for synthetic sensor data."""
    timestamps = [datetime.now() - timedelta(hours=i*interval_hours)
                  for i in range(days*24//interval_hours)]
    data = []
    for ts in timestamps:
        if grow_type == "cannabis":
            temp = np.random.normal(23.5, 0.5)
            hum = np.random.normal(78, 3)
            co2 = np.random.normal(420, 15)
            vpd = np.random.normal(0.6, 0.1)
            ec = np.random.normal(1000, 100)
            ph = np.random.normal(6.2, 0.2)
            day = (ts - timestamps[0]).days
            prob = 0.01 if day < 7 else 0.05
        else:  # mushroom
            temp = np.random.normal(20, 1)
            hum = np.random.normal(88, 3)
            co2 = np.random.normal(800, 100)
            vpd = np.random.normal(0.3, 0.1)
            ec = np.random.normal(500, 50)
            ph = np.random.normal(6.0, 0.3)
            day = (ts - timestamps[0]).days
            prob = 0.02 + 0.01 * day if day > 7 else 0.01
        contaminated = 1 if np.random.random() < prob else 0
        data.append([ts, grow_type, temp, hum, co2, vpd, ec, ph, contaminated])

    columns = ['timestamp', 'strain_phase', 'temperature', 'humidity', 'co2',
               'vpd', 'ec', 'ph', 'contaminated']
    df = pd.DataFrame(data, columns=columns)
    # Create sequences
    feature_cols = ['temperature', 'humidity', 'co2', 'vpd', 'ec', 'ph']
    X, y = create_sequences(df, seq_len=seq_len, feature_cols=feature_cols)
    return X, y

# -------------------------------
# 3. Sequence Creation
# -------------------------------
def create_sequences(df, seq_len=10, feature_cols=None):
    """Create sliding windows from DataFrame."""
    if feature_cols is None:
        feature_cols = ['temperature', 'humidity', 'co2', 'vpd', 'ec', 'ph']
    X, y = [], []
    for i in range(len(df) - seq_len):
        X.append(df[feature_cols].iloc[i:i+seq_len].values)
        y.append(df['contaminated'].iloc[i+seq_len])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)

# -------------------------------
# 4. Data Loader (Real or Synthetic)
# -------------------------------
def load_data(mode="real", grow_type="cannabis", seq_len=10):
    print(f"[Data] Loading data in mode: {mode}")
    if mode == "real":
        data_dir = os.path.expanduser("~/grower-node/sensor_data")
        csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
        csv_files = [f for f in csv_files if not f.endswith(".processed")]
        if not csv_files:
            raise FileNotFoundError("No CSV files found. Use --mode synth to generate synthetic data.")
        latest = max(csv_files, key=os.path.getctime)
        print(f"[Data] Using real CSV: {latest}")
        df = pd.read_csv(latest)
        # Ensure feature columns exist
        feature_cols = ['temperature', 'humidity', 'co2', 'vpd', 'ec', 'ph']
        missing = [c for c in feature_cols if c not in df.columns]
        if missing:
            raise KeyError(f"Missing columns: {missing}")
        if 'contaminated' not in df.columns:
            raise KeyError("CSV must have a 'contaminated' column (0/1)")
        X, y = create_sequences(df, seq_len=seq_len, feature_cols=feature_cols)
    else:
        print(f"[Data] Generating synthetic {grow_type} data...")
        X, y = generate_synthetic_data(grow_type=grow_type, seq_len=seq_len)

    print(f"[Data] X shape: {X.shape}, y shape: {y.shape}")
    return X, y

# -------------------------------
# 5. Flower Client with DP
# -------------------------------
class MycelialClient(fl.client.NumPyClient):
    def __init__(self, model, X_train, y_train, epsilon=0.5):
        self.model = model
        self.X_train = torch.tensor(X_train)
        self.y_train = torch.tensor(y_train)
        self.criterion = nn.BCELoss()
        self.optimizer = optim.Adam(model.parameters(), lr=0.01)
        self.epsilon = epsilon
        print(f"[Client] Initialized with DP epsilon={epsilon}")

    def get_parameters(self, config):
        return [val.cpu().numpy() for val in self.model.state_dict().values()]

    def set_parameters(self, parameters):
        params_dict = zip(self.model.state_dict().keys(), parameters)
        state_dict = {k: torch.tensor(v) for k, v in params_dict}
        self.model.load_state_dict(state_dict, strict=True)

    def fit(self, parameters, config):
        self.set_parameters(parameters)
        self.model.train()
        for epoch in range(5):  # local epochs
            outputs = self.model(self.X_train).squeeze()
            loss = self.criterion(outputs, self.y_train)
            self.optimizer.zero_grad()
            loss.backward()
            # Add differential privacy noise
            for param in self.model.parameters():
                if param.grad is not None:
                    mech = Laplace(epsilon=self.epsilon, sensitivity=1.0)
                    noise = mech.randomise(0)
                    param.grad += noise
            self.optimizer.step()
        return self.get_parameters({}), len(self.X_train), {}

# -------------------------------
# 6. Main Entry Point
# -------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Empirical Connections Grower Client (Transformer)")
    parser.add_argument("--mode", choices=["real", "synth"], default="real",
                        help="Use real CSV or generate synthetic data")
    parser.add_argument("--type", choices=["cannabis", "mushroom"], default="cannabis",
                        help="Type of synthetic data (only for --mode synth)")
    parser.add_argument("--node-id", type=str, default="node1",
                        help="Identifier for this client")
    parser.add_argument("--server", type=str, default="127.0.0.1:8081",
                        help="Flower server address")
    parser.add_argument("--epsilon", type=float, default=0.5,
                        help="Differential privacy epsilon")
    parser.add_argument("--seq_len", type=int, default=10,
                        help="Sequence length for transformer (number of past time steps)")
    args = parser.parse_args()

    print(f"=== Starting Mycelial Client (Transformer): {args.node_id} ===")
    print(f"Mode: {args.mode}, Server: {args.server}, DP ε={args.epsilon}, Seq len: {args.seq_len}")

    try:
        X, y = load_data(mode=args.mode, grow_type=args.type, seq_len=args.seq_len)
        input_dim = X.shape[2]  # number of features
        model = TinyTransformer(input_dim=input_dim, d_model=32, nhead=4, num_layers=2, seq_len=args.seq_len)
        client = MycelialClient(model, X, y, epsilon=args.epsilon)
        fl.client.start_numpy_client(server_address=args.server, client=client)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
