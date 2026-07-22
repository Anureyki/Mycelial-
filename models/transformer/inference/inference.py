#!/usr/bin/env python3
"""
Transformer Inference – Classify text, generate responses, or retrieve RAG context.
Used by Boss, AG, Commerce, and PQA.
"""

import os
import sys
import yaml
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

MODEL_NAME = config['model']['name']
WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights")

# Check if fine-tuned weights exist
if os.path.exists(os.path.join(WEIGHTS_DIR, "pytorch_model.bin")):
    model = AutoModelForSequenceClassification.from_pretrained(WEIGHTS_DIR)
    tokenizer = AutoTokenizer.from_pretrained(WEIGHTS_DIR)
    print("✅ Loaded fine-tuned model.")
else:
    print("⚠️ No fine-tuned weights found. Loading base model.")
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

device = 0 if torch.cuda.is_available() else -1

# Use pipeline for easy inference
classifier = pipeline(
    "text-classification",
    model=model,
    tokenizer=tokenizer,
    device=device
)

def classify_text(text):
    """Return label and confidence."""
    result = classifier(text)
    return result[0]['label'], result[0]['score']

# Optionally, for generation (if using GPT‑2, etc.), you can use pipeline('text-generation')

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True, help="Text to classify")
    args = parser.parse_args()
    label, conf = classify_text(args.text)
    print(f"Text: {args.text}")
    print(f"  → {label} (confidence: {conf:.4f})")
