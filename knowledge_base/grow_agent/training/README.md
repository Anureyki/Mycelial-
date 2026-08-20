# Cannabis training images

Drop labelled photos here. **The folder name is the label** — an image in
`calmag_deficiency/` teaches the model that it shows a Cal/Mag deficiency, so a
misfiled photo actively teaches the wrong thing.

Check readiness at any point:

```bash
python3 services/vision/dataset_inventory.py
```

It reports per-class counts, duplicates, unreadable files, and an honest verdict
on whether there's enough to fine-tune yet. Run it before attempting training.

## Why this folder exists

The two vision checkpoints in `services/vision/plant_perception.py` are
PlantVillage models covering **pepper, potato, tomato only** (15 classes total —
verified by reading the model class lists directly). They have no cannabis class,
so a cannabis photo gets force-fit into the nearest tomato disease with a
plausible-looking confidence score. That is why `fuse_observations()` now refuses
to classify unsupported species instead of guessing.

No public cannabis leaf-health dataset exists to fix this — the Hub has only
trichome microscopy, strain listings, and lab analyte data. So the only route to
a cannabis model is images supplied by the grower.

## What makes the set trainable

**Volume.** Fine-tuning a pretrained backbone needs roughly **100 images per
class minimum**, ~300 to be comfortable. Below that the model scores well in
training and fails on real photos — turning "I don't know" into a confident wrong
answer, which is worse than no model.

**Fewer classes, filled properly.** Two well-populated conditions beat ten thin
ones. Start with the distinction you actually need (often just healthy vs. the
one deficiency you keep hitting) and add classes as images accumulate. Empty and
under-floor folders are excluded from training, not padded.

**Balance.** Keep counts within ~3:1 across classes. Otherwise the model learns
which class is more common rather than what the conditions look like.

**Variety.** Vary lighting, angle, distance, and background. If every `healthy/`
photo is under the LED and every deficiency photo is by a window, the model
learns the lighting, not the plant.

**Honest labels.** An uncertain photo is better left out than guessed at. Label
noise is the fastest way to a model that confidently repeats your mistakes.

## Note on web-search validation

Search cannot verify an image classification. Searching "cannabis nitrogen
deficiency" returns articles describing nitrogen deficiency whether or not the
photo shows it — that is confirmation, not validation, and it wraps wrong answers
in citations. Search is genuinely useful in the other direction: matching a
*written symptom description* against candidate conditions, and pulling treatment
references once a condition is established. `verify_growth_stage` in
`grow_agent.py` works because it checks a real number (days since germination),
not a guess about pixels.
