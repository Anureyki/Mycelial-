#!/usr/bin/env python3
"""
Fine‑tune a transformer model (DistilBERT) on custom text data.
Used for: intent classification, RAG embedding fine‑tuning, etc.
"""

import os
import sys
import yaml
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer,
    EarlyStoppingCallback
)
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

MODEL_NAME = config['model']['name']
MAX_LEN = config['model']['max_seq_len']
BATCH_SIZE = config['training']['batch_size']
EPOCHS = config['training']['epochs']
LR = config['training']['learning_rate']
WEIGHT_DECAY = config['training']['weight_decay']
DATA_FILE = config['training']['data_dir']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Check if dataset exists
if not os.path.exists(DATA_FILE):
    print(f"❌ Dataset not found: {DATA_FILE}. Creating synthetic dataset for testing.")
    # Create synthetic dataset for testing
    os.makedirs("dataset", exist_ok=True)
    texts = [
        "I need to pay my invoice", "When is the payment due", "My bill is outstanding",
        "What is the VPD today", "The temperature is high", "Contamination detected",
        "Please send me the receipt", "I want to transfer money", "Thank you for your help",
        "The mycelium is growing", "Sensor data shows normal", "Check the pH level"
    ] * 50
    labels = [0 if i < 6 else 1 for i in range(12)] * 50  # 0=billing, 1=agriculture
    df = pd.DataFrame({'text': texts, 'label': labels})
    df.to_csv(DATA_FILE, index=False)
    print(f"✅ Synthetic dataset created with {len(df)} samples.")

# Load dataset
df = pd.read_csv(DATA_FILE)
train_df, val_df = train_test_split(df, test_size=0.2, random_state=42)

# Tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

class TextDataset(Dataset):
    def __init__(self, df, tokenizer, max_len):
        self.df = df
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        text = str(self.df.iloc[idx]['text'])
        label = int(self.df.iloc[idx]['label'])
        encoding = self.tokenizer(
            text,
            truncation=True,
            padding='max_length',
            max_length=self.max_len,
            return_tensors='pt'
        )
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'labels': torch.tensor(label, dtype=torch.long)
        }

train_dataset = TextDataset(train_df, tokenizer, MAX_LEN)
val_dataset = TextDataset(val_df, tokenizer, MAX_LEN)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE)

# Model
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2)
model.to(DEVICE)

# Training arguments
training_args = TrainingArguments(
    output_dir='./results',
    num_train_epochs=EPOCHS,
    per_device_train_batch_size=BATCH_SIZE,
    per_device_eval_batch_size=BATCH_SIZE,
    warmup_steps=config['training']['warmup_steps'],
    weight_decay=WEIGHT_DECAY,
    logging_dir='./logs',
    logging_steps=10,
    eval_strategy='epoch',
    save_strategy='epoch',
    load_best_model_at_end=True,
    metric_for_best_model='accuracy',
    report_to='none'
)

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=1)
    return {
        'accuracy': accuracy_score(labels, preds),
        'f1': f1_score(labels, preds, average='weighted')
    }

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    compute_metrics=compute_metrics,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=3)]
)

# Train
print("🚀 Starting fine-tuning...")
trainer.train()

# Save best model
weights_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights")
os.makedirs(weights_dir, exist_ok=True)
model.save_pretrained(weights_dir)
tokenizer.save_pretrained(weights_dir)

print(f"✅ Model saved to {weights_dir}")
