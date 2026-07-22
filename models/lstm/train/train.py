#!/usr/bin/env python3
"""
LSTM Training Script – Time-series sensor data.
Reads config.yaml, loads dataset, trains model, saves weights.
"""

import os
import sys
import yaml
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, random_split
import numpy as np
import pandas as pd
from datetime import datetime

# ----------------------------
# 1. Load config
# ----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

DATA_FILE = config['training']['data_dir']
SEQ_LEN = config['model']['seq_len']
INPUT_SIZE = config['model']['input_size']
HIDDEN_SIZE = config['model']['hidden_size']
NUM_LAYERS = config['model']['num_layers']
OUTPUT_SIZE = config['model']['output_size']
BATCH_SIZE = config['training']['batch_size']
EPOCHS = config['training']['epochs']
LR = config['training']['learning_rate']
WEIGHT_DECAY = config['training']['weight_decay']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ----------------------------
# 2. Dataset
# ----------------------------
class SensorDataset(Dataset):
    def __init__(self, data_file, seq_len, input_size):
        df = pd.read_csv(data_file)
        # Features: columns except last (label)
        self.features = df.iloc[:, :-1].values.reshape(-1, seq_len, input_size)
        self.labels = df.iloc[:, -1].values.astype(np.float32)
        self.seq_len = seq_len
        self.input_size = input_size

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        X = torch.tensor(self.features[idx], dtype=torch.float32)
        y = torch.tensor(self.labels[idx], dtype=torch.float32).unsqueeze(0)
        return X, y

# Load dataset
dataset = SensorDataset(DATA_FILE, SEQ_LEN, INPUT_SIZE)
val_size = int(len(dataset) * config['training']['validation_split'])
train_size = len(dataset) - val_size
train_dataset, val_dataset = random_split(dataset, [train_size, val_size])

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False)

# ----------------------------
# 3. Model
# ----------------------------
class LSTMModel(nn.Module):
    def __init__(self, input_size, hidden_size, num_layers, output_size):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, bidirectional=False)
        self.fc = nn.Linear(hidden_size, output_size)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x: (batch, seq_len, input_size)
        lstm_out, _ = self.lstm(x)  # lstm_out: (batch, seq_len, hidden_size)
        # Take the last time step
        last_out = lstm_out[:, -1, :]  # (batch, hidden_size)
        out = self.fc(last_out)        # (batch, output_size)
        return self.sigmoid(out)

model = LSTMModel(INPUT_SIZE, HIDDEN_SIZE, NUM_LAYERS, OUTPUT_SIZE).to(DEVICE)
criterion = nn.BCELoss()
optimizer = optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=5, factor=0.5)

# ----------------------------
# 4. Training loop
# ----------------------------
def train_one_epoch(loader, model, optimizer, criterion):
    model.train()
    total_loss = 0
    correct = 0
    total = 0
    for X, y in loader:
        X, y = X.to(DEVICE), y.to(DEVICE)
        optimizer.zero_grad()
        outputs = model(X)
        loss = criterion(outputs, y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        preds = (outputs > 0.5).float()
        correct += (preds == y).sum().item()
        total += y.size(0)
    return total_loss / len(loader), correct / total

def evaluate(loader, model, criterion):
    model.eval()
    total_loss = 0
    correct = 0
    total = 0
    with torch.no_grad():
        for X, y in loader:
            X, y = X.to(DEVICE), y.to(DEVICE)
            outputs = model(X)
            loss = criterion(outputs, y)
            total_loss += loss.item()
            preds = (outputs > 0.5).float()
            correct += (preds == y).sum().item()
            total += y.size(0)
    return total_loss / len(loader), correct / total

best_val_acc = 0.0
weights_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights")
os.makedirs(weights_dir, exist_ok=True)

print(f"Training on {DEVICE}")

for epoch in range(1, EPOCHS+1):
    train_loss, train_acc = train_one_epoch(train_loader, model, optimizer, criterion)
    val_loss, val_acc = evaluate(val_loader, model, criterion)
    scheduler.step(val_loss)

    print(f"Epoch {epoch:3d}/{EPOCHS} | Train Loss: {train_loss:.4f} | Train Acc: {train_acc:.4f} | Val Loss: {val_loss:.4f} | Val Acc: {val_acc:.4f}")

    if val_acc > best_val_acc:
        best_val_acc = val_acc
        torch.save(model.state_dict(), os.path.join(weights_dir, "best_model.pth"))
        print(f"  ✓ New best model saved (Val Acc: {val_acc:.4f})")

print("Training complete.")
