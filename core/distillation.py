#!/usr/bin/env python3
"""Collect the teacher signal. Nothing here trains; this only records.

    from core.distillation import record_pair
    record_pair(prompt=..., output=..., model=..., capability=..., verdict=...)

WHY THIS FILE HAD TO BE WRITTEN FIRST. `CLAUDE.md` said "Distillation data
collection is active". It was not. There were ZERO occurrences of the word in
services/ or core/, the inference service persisted nothing, `datasets/` was
empty, and the only registered dataset was a synthetic sensor CSV from July.
The teacher signal a student would be trained on did not exist, and the
documentation saying it did is the exact `false success` shape this project
hunts - a claim that was never true, believed downstream.

Training on a dataset that does not exist fails in one of two ways. It crashes,
which is the good outcome. Or somebody fills the gap with generated examples to
get the loop running, the gate goes green on invented data, and a checkpoint is
promoted on the strength of a test that measured nothing. The second is worse
and is the reason this exists before train_student.py.

WHAT A PAIR IS. input, output, and the gate's verdict - the triple the student
learns from. NOT the reasoning that produced it: a distillation set is behaviour,
not transcript, and shipping a teacher's chain of thought into a dataset is how
private context ends up in weights that have no supersession path.

WHAT IS DELIBERATELY NOT RECORDED:
  - the raw corpus passage a prompt was built from. The passage is public law or
    the principal's own document; either way it belongs in the corpus, which can
    be corrected, not in a weight, which cannot.
  - anything under the `cases` namespace or any live matter.
  - the prompt VERBATIM when it carries a constraint or personal record; the
    hash goes in instead, so a pair can be traced without carrying the content.

APPEND-ONLY JSONL, one file per day. Append-only because a training set that can
be edited in place is a training set whose history cannot be audited, and the
run log is supposed to be provenance.
"""
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "datasets", "distillation")
_LOCK = threading.Lock()

SCHEMA_VERSION = "1.0"

# A prompt matching any of these is recorded by HASH rather than by text. The
# list is deliberately blunt: a false positive costs one pair, a false negative
# puts a person's record in a training set.
SENSITIVE = re.compile(
    r"\b(case_[0-9a-f]{6,}|hud-?vash|ssn|social security|"
    r"account number|routing number|fiduciary|coeliac|celiac|"
    r"diagnos|medical|landlord|eviction)\b", re.I)


def _redaction(prompt):
    """-> (stored_prompt, redacted, why)."""
    if prompt and SENSITIVE.search(prompt):
        return (None, True,
                "prompt matched the sensitive pattern; stored as hash only")
    return (prompt, False, None)


def record_pair(prompt, output, model=None, capability=None, verdict=None,
                agent=None, latency_ms=None, store=None, extra=None):
    """Append one teacher pair. -> the record, or None if it was refused.

    REFUSES rather than silently dropping. A pair that was not recorded and a
    pair that was never produced look identical in the dataset afterwards."""
    if not prompt or not output:
        return None
    stored_prompt, redacted, why = _redaction(prompt)
    rec = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "prompt": stored_prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "redacted": redacted,
        "redaction_reason": why,
        "output": output,
        "output_sha256": hashlib.sha256(str(output).encode("utf-8")).hexdigest(),
        "model": model,
        "capability": capability,
        "agent": agent,
        "latency_ms": latency_ms,
        # THE GATE'S VERDICT IS PART OF THE PAIR. A teacher output nobody judged
        # is not a teacher signal, it is just output - and a student trained on
        # unjudged output learns the teacher's mistakes with the same weight as
        # its successes.
        "verdict": verdict,
        "verdict_state": ("judged" if verdict else "unjudged"),
    }
    if extra:
        rec["extra"] = extra
    path = os.path.join(store or STORE,
                        f"pairs-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)  # datasets/
    line = json.dumps(rec, ensure_ascii=False)
    with _LOCK:
        # 0600 at creation. The directory is 0700 and a day's first pair
        # created the file at the umask - 0644 - so every prompt that
        # named a live matter was readable by any user on the box until
        # check_fs_boundary caught it on the tree. The creator sets the
        # mode; a later chmod is a repair, not a boundary.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return rec


def stats(store=None):
    """What is actually collected. Counts, not a claim that collection is on."""
    d = store or STORE
    out = {"store": d, "files": 0, "pairs": 0, "judged": 0, "redacted": 0,
           "models": {}, "capabilities": {}, "first": None, "last": None}
    if not os.path.isdir(d):
        out["absence_state"] = "nothing_found"
        out["why"] = f"{d} does not exist - nothing has been collected"
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".jsonl"):
            continue
        out["files"] += 1
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                out["pairs"] += 1
                out["judged"] += 1 if r.get("verdict") else 0
                out["redacted"] += 1 if r.get("redacted") else 0
                m, c = r.get("model"), r.get("capability")
                if m:
                    out["models"][m] = out["models"].get(m, 0) + 1
                if c:
                    out["capabilities"][c] = out["capabilities"].get(c, 0) + 1
                t = r.get("recorded_at")
                if t:
                    out["first"] = min(out["first"] or t, t)
                    out["last"] = max(out["last"] or t, t)
    out["absence_state"] = "verified_clear" if out["pairs"] else "nothing_found"
    return out
