#!/usr/bin/env python3
"""
Shared model + data utilities for Mycelial federated learning.

Salvaged from the retired models/transformer/fl_client.py. The model and the
synthetic data generator were the only parts of that file worth keeping - the
Flower wiring around them used APIs that have since been removed.

Both the FL server (for initial parameters) and every client import from here,
so the architecture cannot drift between them. When deploying a client to a
remote grower node, ship this file alongside client/fl_client.py.
"""
import os
import glob
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

FEATURE_COLS = ["temperature", "humidity", "co2", "vpd", "ec", "ph"]
DEFAULT_SEQ_LEN = 10


class TinyTransformer(nn.Module):
    """Sequence classifier over sensor readings -> contamination probability."""

    def __init__(self, input_dim=6, d_model=32, nhead=4, num_layers=2):
        super().__init__()
        self.embed = nn.Linear(input_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x: (batch, seq_len, input_dim)
        x = self.embed(x)
        x = self.transformer(x)
        x = x.mean(dim=1)  # pool over time
        return self.sigmoid(self.fc(x))


def create_sequences(df, seq_len=DEFAULT_SEQ_LEN, feature_cols=None):
    """Sliding windows over a dataframe -> (X, y)."""
    if feature_cols is None:
        feature_cols = FEATURE_COLS
    X, y = [], []
    for i in range(len(df) - seq_len):
        X.append(df[feature_cols].iloc[i:i + seq_len].values)
        y.append(df["contaminated"].iloc[i + seq_len])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def generate_synthetic_data(grow_type="cannabis", days=14, interval_hours=6,
                            seq_len=DEFAULT_SEQ_LEN):
    """Synthetic sensor readings, for testing an FL round without real data."""
    timestamps = [
        datetime.now() - timedelta(hours=i * interval_hours)
        for i in range(days * 24 // interval_hours)
    ]
    rows = []
    for ts in timestamps:
        day = (ts - timestamps[0]).days
        if grow_type == "cannabis":
            temp, hum, co2 = np.random.normal(23.5, 0.5), np.random.normal(78, 3), np.random.normal(420, 15)
            vpd, ec, ph = np.random.normal(0.6, 0.1), np.random.normal(1000, 100), np.random.normal(6.2, 0.2)
            prob = 0.01 if day < 7 else 0.05
        else:  # mushroom
            temp, hum, co2 = np.random.normal(20, 1), np.random.normal(88, 3), np.random.normal(800, 100)
            vpd, ec, ph = np.random.normal(0.3, 0.1), np.random.normal(500, 50), np.random.normal(6.0, 0.3)
            prob = 0.02 + 0.01 * day if day > 7 else 0.01
        contaminated = 1 if np.random.random() < prob else 0
        rows.append([ts, grow_type, temp, hum, co2, vpd, ec, ph, contaminated])

    df = pd.DataFrame(rows, columns=["timestamp", "strain_phase", *FEATURE_COLS, "contaminated"])
    return create_sequences(df, seq_len=seq_len)


def load_data(mode="real", grow_type="cannabis", seq_len=DEFAULT_SEQ_LEN,
              data_dir="~/grower-node/sensor_data"):
    """Real CSV from the grower node, or synthetic if asked."""
    if mode == "real":
        data_dir = os.path.expanduser(data_dir)
        csv_files = [f for f in glob.glob(os.path.join(data_dir, "*.csv"))
                     if not f.endswith(".processed")]
        if not csv_files:
            raise FileNotFoundError(
                f"No unprocessed CSVs in {data_dir}. Use --mode synth to generate data instead."
            )
        latest = max(csv_files, key=os.path.getctime)
        df = pd.read_csv(latest)
        missing = [c for c in FEATURE_COLS if c not in df.columns]
        if missing:
            raise KeyError(f"{latest} is missing required columns: {missing}")
        if "contaminated" not in df.columns:
            raise KeyError(f"{latest} must have a 'contaminated' column (0/1)")
        return create_sequences(df, seq_len=seq_len)

    return generate_synthetic_data(grow_type=grow_type, seq_len=seq_len)


def get_parameters(model):
    """Model weights as the list of numpy arrays Flower exchanges."""
    return [val.cpu().numpy() for val in model.state_dict().values()]


def set_parameters(model, parameters):
    """Load Flower's numpy arrays back into a torch model."""
    state_dict = {
        k: torch.tensor(v)
        for k, v in zip(model.state_dict().keys(), parameters)
    }
    model.load_state_dict(state_dict, strict=True)
