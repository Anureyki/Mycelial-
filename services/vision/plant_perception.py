#!/usr/bin/env python3
"""
Plant perception pipeline for Grow Agent.

Three specialist models, fused into one structured observation - reasoning
agents (grow_agent) consume this structured dict, never raw images:

- YOLO ("eyes"): object/region detection, fine-tuned on plant disease imagery
  (not stock COCO - COCO has no plant/leaf class, so a generic checkpoint
  would return empty detections on grow photos).
- A Vision Transformer ("interpreter"): whole-image plant-health classification,
  also fine-tuned on plant disease data.
- OCR (pytesseract): reads any visible text (nutrient bottle labels, handwritten
  log entries) - a separate concern from the two vision models above.

Both vision checkpoints are pulled from the Hugging Face Hub on first use
(cached locally after that) rather than fine-tuned from scratch here - real,
existing plant-specific models, not COCO-generic ones repurposed and hoped to work.
"""
import os

try:
    from ultralytics import YOLO
    from huggingface_hub import hf_hub_download
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

try:
    import torch
    from transformers import AutoImageProcessor, AutoModelForImageClassification
    from PIL import Image
    VIT_AVAILABLE = True
except ImportError:
    VIT_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image as PILImage
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

YOLO_REPO = "peachfawn/yolov8-plant-disease"
YOLO_FILENAME = "best.pt"
VIT_REPO = "gianlab/swin-tiny-patch4-window7-224-finetuned-plantdisease"

# Both checkpoints above are trained on PlantVillage, whose 15 classes cover
# ONLY these three species (verified by reading model.names / config.id2label
# directly, not assumed from the repo description). A photo of any other plant
# still gets forced into one of those 15 classes with a plausible-looking
# confidence score - e.g. a cannabis leaf reliably comes back as some
# "Tomato___*" disease. That output is meaningless, so callers must pass the
# species they're actually growing and fuse_observations will refuse to present
# a local classification for anything outside this set, routing it to the
# verification tier instead.
SUPPORTED_SPECIES = ("pepper", "potato", "tomato")

# Below this fused confidence, evaluate_leaf/evaluate_growth_stage in
# grow_agent.py escalate the case to a verification model rather than
# trusting the local pipeline's read.
LOW_CONFIDENCE_THRESHOLD = 0.55

_yolo_model = None
_vit_model = None
_vit_processor = None


def _get_yolo_model():
    global _yolo_model
    if _yolo_model is None and YOLO_AVAILABLE:
        weights_path = hf_hub_download(repo_id=YOLO_REPO, filename=YOLO_FILENAME)
        _yolo_model = YOLO(weights_path)
    return _yolo_model


def _get_vit_model():
    global _vit_model, _vit_processor
    if _vit_model is None and VIT_AVAILABLE:
        _vit_processor = AutoImageProcessor.from_pretrained(VIT_REPO)
        _vit_model = AutoModelForImageClassification.from_pretrained(VIT_REPO)
        _vit_model.eval()
    return _vit_model, _vit_processor


def detect_objects(image_path, conf_threshold=0.25):
    """YOLO pass - structured detections: [{"label", "confidence", "bbox"}]."""
    if not YOLO_AVAILABLE:
        return {"error": "ultralytics/huggingface_hub not installed"}
    try:
        model = _get_yolo_model()
    except Exception as e:
        return {"error": f"Could not load YOLO model: {e}"}
    try:
        results = model(image_path, conf=conf_threshold, verbose=False)
    except Exception as e:
        return {"error": f"YOLO inference failed: {e}"}
    detections = []
    if results and len(results) > 0:
        boxes = results[0].boxes
        if boxes is not None:
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = box.conf[0].item()
                cls = int(box.cls[0].item())
                label = model.names[cls] if hasattr(model, "names") else str(cls)
                detections.append({"label": label, "confidence": conf, "bbox": [x1, y1, x2, y2]})
    return detections


def classify_health(image_path):
    """ViT pass - {"label", "confidence", "top_k": [...]}."""
    if not VIT_AVAILABLE:
        return {"error": "transformers/torch not installed"}
    try:
        model, processor = _get_vit_model()
        image = Image.open(image_path).convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.nn.functional.softmax(logits, dim=-1)[0]
        top_idx = int(torch.argmax(probs).item())
        top_k_idx = torch.topk(probs, k=min(3, probs.shape[0])).indices.tolist()
        return {
            "label": model.config.id2label.get(top_idx, str(top_idx)),
            "confidence": float(probs[top_idx].item()),
            "top_k": [
                {"label": model.config.id2label.get(i, str(i)), "confidence": float(probs[i].item())}
                for i in top_k_idx
            ],
        }
    except Exception as e:
        return {"error": f"ViT classification failed: {e}"}


def read_text(image_path):
    """OCR pass - list of read text strings (nutrient labels, handwritten notes)."""
    if not OCR_AVAILABLE:
        return {"error": "pytesseract not installed"}
    try:
        image = PILImage.open(image_path)
        text = pytesseract.image_to_string(image)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return lines
    except pytesseract.TesseractNotFoundError:
        return {"error": "tesseract-ocr binary not installed (system package, not pip) - OCR unavailable"}
    except Exception as e:
        return {"error": f"OCR failed: {e}"}


def fuse_observations(image_path, species=None):
    """Runs all three passes and fuses them into one structured observation.
    overall_confidence is the minimum of the two vision models' confidence
    (OCR doesn't have a comparable confidence score) - the fused read is only
    as trustworthy as its least-confident component.

    species: what's actually being grown. If it isn't one of SUPPORTED_SPECIES,
    the two disease models have no class for it and would emit a confident-looking
    but meaningless label, so their output is dropped entirely and the result is
    marked low_confidence to force the caller's verification-tier escalation.
    OCR is species-independent and is kept either way."""
    if not os.path.exists(image_path):
        return {"error": f"Image not found: {image_path}"}

    species_norm = (species or "").strip().lower()
    species_supported = (not species_norm) or any(s in species_norm for s in SUPPORTED_SPECIES)

    text = read_text(image_path)

    if not species_supported:
        return {
            "image_path": image_path,
            "species": species,
            "species_supported": False,
            "detections": [],
            "detection_error": None,
            "health": None,
            "health_error": (
                f"Local disease models cover only {', '.join(SUPPORTED_SPECIES)} - "
                f"they have no class for '{species}', so no local classification was made."
            ),
            "text": text if isinstance(text, list) else [],
            "text_error": text.get("error") if isinstance(text, dict) else None,
            "overall_confidence": 0.0,
            "low_confidence": True,
        }

    detections = detect_objects(image_path)
    health = classify_health(image_path)

    confidences = []
    if isinstance(detections, list) and detections:
        confidences.append(max(d["confidence"] for d in detections))
    if isinstance(health, dict) and "confidence" in health:
        confidences.append(health["confidence"])
    overall_confidence = min(confidences) if confidences else 0.0

    return {
        "image_path": image_path,
        "species": species,
        "species_supported": True,
        "detections": detections if isinstance(detections, list) else [],
        "detection_error": detections.get("error") if isinstance(detections, dict) else None,
        "health": health if "error" not in (health if isinstance(health, dict) else {}) else None,
        "health_error": health.get("error") if isinstance(health, dict) else None,
        "text": text if isinstance(text, list) else [],
        "text_error": text.get("error") if isinstance(text, dict) else None,
        "overall_confidence": overall_confidence,
        "low_confidence": overall_confidence < LOW_CONFIDENCE_THRESHOLD,
    }
