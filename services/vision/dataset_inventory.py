#!/usr/bin/env python3
"""
Inventory the cannabis training set and give an honest fine-tuning readiness
verdict.

Exists because the blocker on a cannabis vision model is data, not code: no
public cannabis leaf-health dataset exists (searched the Hub - only trichome
microscopy, strain listings, and lab analytes), and both PlantVillage
checkpoints in plant_perception.py cover pepper/potato/tomato only. So the
model can only be trained on images the grower supplies, and the first real
question is whether there are enough of them to be worth training on at all.

This reports what's actually there - counts, balance, duplicates, unreadable
files - and refuses to call a set trainable when it isn't. Run it before
attempting any fine-tune.

    python3 services/vision/dataset_inventory.py
"""
import os
import sys
import hashlib
from collections import defaultdict

TRAINING_DIR = os.path.expanduser("~/mycelial/knowledge_base/grow_agent/training")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp")

# Rough, deliberately conservative thresholds for fine-tuning a pretrained
# backbone (not training from scratch). Below MIN_PER_CLASS a class will look
# learned on the training set and fail on real photos, which is worse than
# having no model - it converts "I don't know" into a confident wrong answer.
MIN_PER_CLASS = 100
COMFORTABLE_PER_CLASS = 300
# Beyond this ratio between biggest and smallest class, the model mostly
# learns the class prior rather than the visual difference.
MAX_IMBALANCE_RATIO = 3.0


def scan():
    if not os.path.isdir(TRAINING_DIR):
        print(f"No training directory at {TRAINING_DIR}")
        return None
    classes = {}
    hashes = defaultdict(list)
    unreadable = []
    for name in sorted(os.listdir(TRAINING_DIR)):
        class_dir = os.path.join(TRAINING_DIR, name)
        if not os.path.isdir(class_dir):
            continue
        files = []
        for f in sorted(os.listdir(class_dir)):
            path = os.path.join(class_dir, f)
            if not os.path.isfile(path) or not f.lower().endswith(IMAGE_EXTS):
                continue
            try:
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
                hashes[digest].append(os.path.join(name, f))
                files.append(f)
            except Exception as e:
                unreadable.append((os.path.join(name, f), str(e)))
        classes[name] = files
    duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    return classes, duplicates, unreadable


def verdict(classes):
    populated = {c: len(f) for c, f in classes.items() if f}
    if not populated:
        return ("EMPTY",
                "No images yet. Drop labelled photos into the per-condition folders "
                "under knowledge_base/grow_agent/training/ - the folder name is the label.")

    total = sum(populated.values())
    smallest = min(populated.values())
    largest = max(populated.values())
    ratio = (largest / smallest) if smallest else float("inf")
    usable = [c for c, n in populated.items() if n >= MIN_PER_CLASS]

    lines = []
    if len(usable) < 2:
        state = "NOT TRAINABLE"
        lines.append(
            f"Only {len(usable)} class(es) reach the {MIN_PER_CLASS}-image floor "
            f"({total} images across {len(populated)} populated class(es))."
        )
        lines.append(
            "Fine-tuning needs at least two classes with enough examples to tell apart. "
            "Adding more images to the two conditions you most want distinguished "
            "beats spreading a small set across many folders."
        )
    elif len(usable) == 2:
        state = "TRAINABLE (narrow binary only)"
        lines.append(f"Two classes clear the floor: {', '.join(sorted(usable))}.")
        lines.append(
            "Train these two only. Folders below the floor must be excluded, not "
            "included with few examples - a thin class drags the whole model's "
            "reliability down while looking fine in training metrics."
        )
    else:
        state = "TRAINABLE (multi-class)"
        lines.append(f"{len(usable)} classes clear the floor: {', '.join(sorted(usable))}.")

    if usable and ratio > MAX_IMBALANCE_RATIO:
        lines.append(
            f"Class imbalance {ratio:.1f}:1 (largest {largest}, smallest {smallest}) "
            f"exceeds {MAX_IMBALANCE_RATIO}:1 - even out the counts or the model will "
            "mostly learn which class is more common."
        )
    if usable and largest < COMFORTABLE_PER_CLASS:
        lines.append(
            f"All classes are under {COMFORTABLE_PER_CLASS} images; expect a usable "
            "but shaky model. Treat its output as a hint to verify, not a diagnosis."
        )
    return state, "\n  ".join(lines)


def main():
    scanned = scan()
    if scanned is None:
        return 1
    classes, duplicates, unreadable = scanned

    print(f"Training set: {TRAINING_DIR}\n")
    width = max((len(c) for c in classes), default=10)
    total = 0
    for name, files in sorted(classes.items()):
        n = len(files)
        total += n
        flag = "" if n >= MIN_PER_CLASS else ("  (empty)" if n == 0 else "  (below floor)")
        print(f"  {name.ljust(width)}  {str(n).rjust(5)}{flag}")
    print(f"\n  {'TOTAL'.ljust(width)}  {str(total).rjust(5)}")

    if duplicates:
        dup_extra = sum(len(p) - 1 for p in duplicates.values())
        print(f"\nDuplicates: {dup_extra} redundant copy(ies) across {len(duplicates)} image(s).")
        print("  Identical files inflate counts without adding information - the model")
        print("  sees the same example repeatedly and overfits to it.")
        for paths in list(duplicates.values())[:5]:
            print(f"    {' == '.join(paths)}")

    if unreadable:
        print(f"\nUnreadable ({len(unreadable)}):")
        for path, err in unreadable[:5]:
            print(f"    {path}: {err}")

    state, detail = verdict(classes)
    print(f"\nVerdict: {state}\n  {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
