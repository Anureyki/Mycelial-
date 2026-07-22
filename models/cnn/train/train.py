#!/usr/bin/env python3
"""
CNN Training Script – Contamination detection from plant images.
Reads config.yaml, loads dataset, trains model, saves weights.
"""

import os
import sys
import yaml
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, random_split
import torchvision.transforms as transforms
import torchvision.models as models
from PIL import Image
import pandas as pd
import numpy as np
from datetime import datetime

# ----------------------------
# 1. Load config
# ----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

DATA_DIR = config['training']['data_dir']
LABELS_FILE = config['training']['labels_file']
BATCH_SIZE = config['training']['batch_size']
EPOCHS = config['training']['epochs']
LR = config['training']['learning_rate']
WEIGHT_DECAY = config['training']['weight_decay']
NUM_CLASSES = config['model']['num_classes']
INPUT_SIZE = config['model']['input_size']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ----------------------------
# 2. Dataset
# ----------------------------
class PlantDataset(Dataset):
    def __init__(self, labels_df, transform=None):
        self.labels = labels_df
        self.transform = transform

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        img_path = self.labels.iloc[idx]['image_path']
        label = self.labels.iloc[idx]['label']
        image = Image.open(img_path).convert('RGB')
        if self.transform:
            image = self.transform(image)
        return image, label

# Load labels
labels_df = pd.read_csv(LABELS_FILE)
# Ensure label column is integer (0/1)
labels_df['label'] = labels_df['label'].astype(int)

# Data transforms
train_transform = transforms.Compose([
    transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
    transforms.RandomHorizontalFlip(p=config['data_augmentation']['horizontal_flip']),
    transforms.RandomRotation(degrees=config['data_augmentation']['random_rotation']),
    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1),
    transforms.ToTensor(),
    transforms.Normalize(mean=config['data_augmentation']['normalize_mean'],
                         std=config['data_augmentation']['normalize_std'])
])

val_transform = transforms.Compose([
    transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=config['data_augmentation']['normalize_mean'],
                         std=config['data_augmentation']['normalize_std'])
])

# Split dataset
dataset = PlantDataset(labels_df, transform=train_transform)
val_size = int(len(dataset) * config['training']['validation_split'])
train_size = len(dataset) - val_size
train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
# Replace transform for validation
val_dataset.dataset.transform = val_transform

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False)

# ----------------------------
# 3. Model
# ----------------------------
def create_model():
    if config['model']['architecture'] == 'custom':
        # Simple CNN (good for small datasets)
        model = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.AdaptiveAvgPool2d((4, 4)),
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, 256),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(256, NUM_CLASSES)
        )
    else:
        # Pretrained ResNet18
        model = models.resnet18(pretrained=config['model']['pretrained'])
        model.fc = nn.Linear(model.fc.in_features, NUM_CLASSES)
    return model

model = create_model().to(DEVICE)
criterion = nn.CrossEntropyLoss()
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
    for images, labels in loader:
        images, labels = images.to(DEVICE), labels.to(DEVICE)
        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        _, predicted = torch.max(outputs, 1)
        total += labels.size(0)
        correct += (predicted == labels).sum().item()
    return total_loss / len(loader), correct / total

def evaluate(loader, model, criterion):
    model.eval()
    total_loss = 0
    correct = 0
    total = 0
    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(DEVICE), labels.to(DEVICE)
            outputs = model(images)
            loss = criterion(outputs, labels)
            total_loss += loss.item()
            _, predicted = torch.max(outputs, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
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
