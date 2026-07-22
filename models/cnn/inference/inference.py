#!/usr/bin/env python3
"""
CNN Inference – Given an image path, return prediction.
Used by AG Agent and Commerce Agent.
"""

import os
import sys
import yaml
import torch
import torchvision.transforms as transforms
from PIL import Image
import argparse

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

INPUT_SIZE = config['model']['input_size']
NUM_CLASSES = config['model']['num_classes']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
THRESHOLD = config['inference']['confidence_threshold']

# Load model (same architecture as training)
def create_model():
    import torch.nn as nn
    if config['model']['architecture'] == 'custom':
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
        import torchvision.models as models
        model = models.resnet18(pretrained=False)
        model.fc = nn.Linear(model.fc.in_features, NUM_CLASSES)
    return model

model = create_model()
weights_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights", "best_model.pth")
if not os.path.exists(weights_path):
    print(f"❌ No model weights found at {weights_path}")
    sys.exit(1)
model.load_state_dict(torch.load(weights_path, map_location=DEVICE))
model.to(DEVICE)
model.eval()

# Transform
transform = transforms.Compose([
    transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=config['data_augmentation']['normalize_mean'],
                         std=config['data_augmentation']['normalize_std'])
])

def predict_image(image_path):
    """Return predicted class (0=healthy, 1=contaminated) and confidence."""
    if not os.path.exists(image_path):
        return None, None
    image = Image.open(image_path).convert('RGB')
    tensor = transform(image).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        outputs = model(tensor)
        probs = torch.softmax(outputs, dim=1)
        confidence, predicted = torch.max(probs, 1)
    return predicted.item(), confidence.item()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Path to image file")
    args = parser.parse_args()
    pred, conf = predict_image(args.image)
    if pred is None:
        print("Error processing image.")
    else:
        label = "Contaminated" if pred == 1 else "Healthy"
        print(f"Prediction: {label} (confidence: {conf:.3f})")
