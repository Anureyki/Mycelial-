#!/usr/bin/env python3
"""
RNN Training Script – Text sequences.
Reads config.yaml, loads dataset, trains model, saves weights.
"""

import os
import sys
import yaml
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, random_split
import pandas as pd
import numpy as np
from collections import Counter

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

DATA_FILE = config['training']['data_dir']
VOCAB_SIZE = config['model']['vocab_size']
EMBED_DIM = config['model']['embedding_dim']
HIDDEN_SIZE = config['model']['hidden_size']
NUM_LAYERS = config['model']['num_layers']
OUTPUT_SIZE = config['model']['output_size']
MAX_SEQ_LEN = config['model']['max_seq_len']
BATCH_SIZE = config['training']['batch_size']
EPOCHS = config['training']['epochs']
LR = config['training']['learning_rate']
WEIGHT_DECAY = config['training']['weight_decay']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ----------------------------
# 1. Tokenizer
# ----------------------------
def build_vocab(texts, vocab_size):
    all_words = ' '.join(texts).split()
    counter = Counter(all_words)
    most_common = counter.most_common(vocab_size - 2)
    vocab = {'<PAD>': 0, '<UNK>': 1}
    for word, _ in most_common:
        vocab[word] = len(vocab)
    return vocab

def tokenize(text, vocab, max_len):
    tokens = [vocab.get(word, 1) for word in text.split()]
    if len(tokens) > max_len:
        tokens = tokens[:max_len]
    else:
        tokens += [0] * (max_len - len(tokens))
    return tokens

# ----------------------------
# 2. Dataset
# ----------------------------
class TextDataset(Dataset):
    def __init__(self, data_file, vocab=None, max_len=100, build_vocab_flag=True):
        df = pd.read_csv(data_file)
        self.texts = df['text'].values
        self.labels = df['label'].values.astype(np.float32)
        self.max_len = max_len
        if build_vocab_flag:
            self.vocab = build_vocab(self.texts, VOCAB_SIZE)
        else:
            self.vocab = vocab
        self.tokenized = [tokenize(t, self.vocab, self.max_len) for t in self.texts]

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        X = torch.tensor(self.tokenized[idx], dtype=torch.long)
        y = torch.tensor(self.labels[idx], dtype=torch.float32).unsqueeze(0)
        return X, y

dataset = TextDataset(DATA_FILE)
vocab = dataset.vocab
val_size = int(len(dataset) * config['training']['validation_split'])
train_size = len(dataset) - val_size
train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
# Ensure validation uses same vocab
val_dataset.dataset.vocab = vocab

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False)

# ----------------------------
# 3. Model
# ----------------------------
class RNNTextModel(nn.Module):
    def __init__(self, vocab_size, embed_dim, hidden_size, num_layers, output_size):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.rnn = nn.LSTM(embed_dim, hidden_size, num_layers, batch_first=True, bidirectional=False)
        self.fc = nn.Linear(hidden_size, output_size)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x: (batch, seq_len)
        emb = self.embedding(x)                # (batch, seq_len, embed_dim)
        out, _ = self.rnn(emb)                 # (batch, seq_len, hidden_size)
        last = out[:, -1, :]                   # (batch, hidden_size)
        return self.sigmoid(self.fc(last))

model = RNNTextModel(VOCAB_SIZE, EMBED_DIM, HIDDEN_SIZE, NUM_LAYERS, OUTPUT_SIZE).to(DEVICE)
criterion = nn.BCELoss()
optimizer = optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=5, factor=0.5)

# ----------------------------
# 4. Training
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
print(f"Vocabulary size: {len(vocab)}")

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
