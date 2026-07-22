#!/usr/bin/env python3
"""
LSTM Inference – Predict contamination from sensor sequence.
Used by AG Agent.
"""

import os
import sys
import yaml
import torch
import torch.nn as nn
import numpy as np
import argparse

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

INPUT_SIZE = config['model']['input_size']
HIDDEN_SIZE = config['model']['hidden_size']
NUM_LAYERS = config['model']['num_layers']
OUTPUT_SIZE = config['model']['output_size']
SEQ_LEN = config['model']['seq_len']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class LSTMModel(nn.Module):
    def __init__(self, input_size, hidden_size, num_layers, output_size):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_out = lstm_out[:, -1, :]
        return self.sigmoid(self.fc(last_out))

model = LSTMModel(INPUT_SIZE, HIDDEN_SIZE, NUM_LAYERS, OUTPUT_SIZE)
weights_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights", "best_model.pth")
if not os.path.exists(weights_path):
    print(f"❌ No model weights found at {weights_path}")
    sys.exit(1)
model.load_state_dict(torch.load(weights_path, map_location=DEVICE))
model.to(DEVICE)
model.eval()

def predict_sequence(sequence):
    """
    sequence: list of lists, shape (seq_len, input_size)
    Returns: probability of contamination (0-1)
    """
    if len(sequence) != SEQ_LEN:
        print(f"❌ Expected sequence length {SEQ_LEN}, got {len(sequence)}")
        return None
    tensor = torch.tensor(sequence, dtype=torch.float32).unsqueeze(0).to(DEVICE)  # (1, seq_len, input_size)
    with torch.no_grad():
        prob = model(tensor).item()
    return prob

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", nargs=6, type=float, help="Single time step (6 features) – will repeat to create sequence for testing")
    parser.add_argument("--file", help="CSV file with sequence data (rows = time steps, cols = features)")
    args = parser.parse_args()

    if args.file:
        import pandas as pd
        df = pd.read_csv(args.file)
        if df.shape[1] != INPUT_SIZE:
            print(f"❌ CSV must have exactly {INPUT_SIZE} columns.")
            sys.exit(1)
        seq = df.values.tolist()
        prob = predict_sequence(seq)
        if prob is not None:
            print(f"Contamination probability: {prob:.4f}")
    elif args.sequence:
        # Create a sequence by repeating the given step SEQ_LEN times (for quick test)
        seq = [list(args.sequence) for _ in range(SEQ_LEN)]
        prob = predict_sequence(seq)
        if prob is not None:
            print(f"Contamination probability: {prob:.4f}")
    else:
        print("Usage: python inference.py --sequence <6 values> or --file <csv>")
