#!/usr/bin/env python3
"""Build gate for the self-describing corpus @context (2026-11-17).

The vocabulary at config/context/mycos-corpus-v1.jsonld publishes what each
corpus metadata field MEANS, so an outside tool can read a MycOS corpus record
without the internal schema. This gate makes that boundary fail-closed:

- COMPLETENESS: every metadata field that appears in >=3 corpus works is declared
  (nothing trust-bearing crosses the boundary undescribed);
- NO DEAD VOCABULARY: every declared field appears in the corpus;
- ENUM ACCURACY: for each CLOSED enum (authority_class, claim_layer), every value
  the corpus actually uses is in the declared value-space - a NEW value can't ship
  until the vocabulary documents its meaning;
- ADAPTER: attach_context tags the record with the vocabulary URI, undeclared()
  flags a leak, describe() splits declared meaning from opaque content.

No network, no store writes.
"""
import collections
import glob
import json
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import corpus_context as cc  # noqa: E402

fails = []
MIN_FILES = 3           # a field in this many works is schema, not one-off content
CLOSED_ENUMS = ("authority_class", "claim_layer")


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


# --- scan the real corpus ---
top = collections.Counter()
sec = collections.Counter()
enum_vals = collections.defaultdict(set)
works = glob.glob(os.path.join(ROOT, "reference", "*", "*.json"))
for f in works:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    for k, v in d.items():
        top[k] += 1
        if k in CLOSED_ENUMS and isinstance(v, str):
            enum_vals[k].add(v)
    s = d.get("sections")
    items = s if isinstance(s, list) else (list(s.values()) if isinstance(s, dict) else [])
    for it in items:
        if isinstance(it, dict):
            for k, v in it.items():
                sec[k] += 1
                if k in CLOSED_ENUMS and isinstance(v, str):
                    enum_vals[k].add(v)

all_counts = top + sec
schema_fields = {k for k, n in all_counts.items() if n >= MIN_FILES and not k.startswith("_")}
declared = cc.declared_fields()

# --- 1. context loads and is shaped right ---
ctx = cc.load_context()
check("the @context file loads and has @context + definitions",
      isinstance(ctx.get("@context"), dict) and isinstance(ctx.get("definitions"), dict))
check("the vocabulary URI is a stable URN (no external domain dependency)",
      cc.CONTEXT_URI.startswith("urn:mycos:"))

# --- 2. completeness: every >=3-work field is declared ---
missing = sorted(schema_fields - declared)
check(f"every field in >={MIN_FILES} corpus works is declared "
      f"({len(schema_fields)} schema fields, {len(missing)} undeclared)", not missing)
if missing:
    print("     UNDECLARED SCHEMA FIELDS:", missing)

# --- 3. no dead vocabulary: every declared field appears in the corpus ---
dead = sorted(f for f in declared if all_counts.get(f, 0) == 0)
check(f"no dead vocabulary - every declared field appears in the corpus ({len(dead)} dead)", not dead)
if dead:
    print("     DECLARED BUT ABSENT:", dead)

# --- 4. every declared field has a human/machine-readable definition ---
undef = sorted(f for f in declared if not (cc.field_meaning(f) or {}).get("definition"))
check(f"every declared field carries a definition ({len(undef)} without)", not undef)
if undef:
    print("     DECLARED WITHOUT DEFINITION:", undef)

# --- 5. enum accuracy: closed enums enumerate every value the corpus uses ---
for field in CLOSED_ENUMS:
    declared_vals = set(cc.enum_values(field) or [])
    used = enum_vals.get(field, set())
    uncovered = sorted(used - declared_vals)
    check(f"closed enum '{field}' declares every value the corpus uses "
          f"({len(used)} used, {len(uncovered)} undeclared)", not uncovered)
    if uncovered:
        print(f"     UNDECLARED {field} VALUES:", uncovered)
    check(f"closed enum '{field}' is not marked open-vocabulary", not cc.is_open_vocabulary(field))

# --- 6. adapter behaviour ---
rec = {"title": "16 CFR Part 314", "authority_class": "regulation", "secret_internal_id": 42}
out = cc.attach_context(rec)
check("attach_context tags the record with the vocabulary URI and does not mutate the input",
      out.get("@context") == cc.CONTEXT_URI and "@context" not in rec)
desc = cc.describe(rec)
check("describe splits declared meaning (authority_class) from opaque content (secret_internal_id)",
      "authority_class" in desc["_boundary"]["declared"]
      and "secret_internal_id" in desc["_boundary"]["content"])
check("undeclared() flags a trust field the vocabulary does not declare (leak detector)",
      cc.undeclared(["authority_class", "made_up_trust_field"]) == ["made_up_trust_field"])

print("check_corpus_context: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
