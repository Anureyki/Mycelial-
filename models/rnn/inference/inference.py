#!/usr/bin/env python3
"""
RNN Inference – Classify text (billing vs general).
Used by Commerce Agent and AG Agent.
"""

import os
import sys
import yaml
import torch
import torch.nn as nn
import argparse
import pandas as pd
from collections import Counter

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

VOCAB_SIZE = config['model']['vocab_size']
EMBED_DIM = config['model']['embedding_dim']
HIDDEN_SIZE = config['model']['hidden_size']
NUM_LAYERS = config['model']['num_layers']
OUTPUT_SIZE = config['model']['output_size']
MAX_SEQ_LEN = config['model']['max_seq_len']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class RNNTextModel(nn.Module):
    def __init__(self, vocab_size, embed_dim, hidden_size, num_layers, output_size):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.rnn = nn.LSTM(embed_dim, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        emb = self.embedding(x)
        out, _ = self.rnn(emb)
        last = out[:, -1, :]
        return self.sigmoid(self.fc(last))

def build_vocab_from_texts(texts, vocab_size):
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

# Load model
model = RNNTextModel(VOCAB_SIZE, EMBED_DIM, HIDDEN_SIZE, NUM_LAYERS, OUTPUT_SIZE)
weights_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights", "best_model.pth")
if not os.path.exists(weights_path):
    print(f"❌ No model weights found at {weights_path}")
    sys.exit(1)
model.load_state_dict(torch.load(weights_path, map_location=DEVICE))
model.to(DEVICE)
model.eval()

# Build vocab from dataset (if available) – for inference we need a vocab file.
# For simplicity, we'll use a small fallback vocab.
fallback_texts = ["invoice payment due", "hello how are you", "sensor data VPD"]
vocab = build_vocab_from_texts(fallback_texts, VOCAB_SIZE)

def predict_text(text):
    tokens = tokenize(text, vocab, MAX_SEQ_LEN)
    tensor = torch.tensor(tokens, dtype=torch.long).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        prob = model(tensor).item()
    return prob

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str, help="Text to classify")
    parser.add_argument("--file", help="CSV file with 'text' column to classify")
    args = parser.parse_args()

    if args.text:
        prob = predict_text(args.text)
        label = "billing" if prob > 0.5 else "general"
        print(f"Text: {args.text}")
        print(f"  → {label} (confidence: {prob:.4f})")
    elif args.file:
        df = pd.read_csv(args.file)
        df['probability'] = df['text'].apply(predict_text)
        df['prediction'] = df['probability'].apply(lambda x: "billing" if x > 0.5 else "general")
        print(df[['text', 'prediction', 'probability']])
    else:
        print("Usage: python inference.py --text \"your text\" or --file input.csv")
