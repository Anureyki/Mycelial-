#!/usr/bin/env python3
"""The independent observer. It catches events; it is not told what to record.

    python3 tools/eval_harness.py --ingest        # spool -> records
    python3 tools/eval_harness.py --pairs         # records -> training pairs
    python3 tools/eval_harness.py --verify        # is the chain intact

WHY IT VERIFIES INSTEAD OF TRANSCRIBING. An observer that writes down whatever
it is handed is not an observer, it is a scribe with extra steps. So this
independently checks what it can:

  the provenance hash        is looked up in the provenance store. An emitter
                             claiming an event id that does not exist gets a
                             record marked provenance_verified: false - and
                             that record is NOT usable as a positive example.
  the reason                 must be present and non-trivial, or the record is
                             quarantined rather than dropped.
  the classification         is the harness's, never the emitter's. Nothing in
                             the spool can label itself a positive example.

APPEND-ONLY, AND TAMPER-EVIDENT RATHER THAN MERELY INTENDED. Every record
carries the hash of the one before it. Deleting a record or editing one breaks
the chain at that point and --verify says where. File permissions express
intent; a hash chain produces evidence, and self-grading is exactly the failure
where intent is not enough.

WHAT A TRAINING PAIR IS, AND WHY RAW LOGS ARE NOT ONE.

    denied  + reason        NEGATIVE example. The reason is the whole value: a
                            denial without it teaches a model to avoid a shape
                            rather than to understand a rule, and a model that
                            has learned avoidance will route around the rule
                            the first time the shape changes.
    allowed + provenance    POSITIVE example, and only when the provenance
                            actually resolves. "It was allowed" with nothing
                            behind it is indistinguishable from "nobody
                            checked".

A record that fails either test becomes neither. It is kept, marked, and
excluded from the pairs - because dropping it would make an unverifiable
decision look like a decision that never happened.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SPOOL = os.path.join(ROOT, "state", "security_events")
RECORDS = os.path.join(ROOT, "datasets", "security_eval")
CHAIN_HEAD = os.path.join(RECORDS, "_head.json")

DENY_TYPES = {"acl_denied", "guard_denied", "channel_denied", "veto_fired",
              "artifact_rejected", "route_refused"}
ALLOW_TYPES = {"acl_allowed", "guard_allowed", "channel_allowed",
               "veto_passed", "artifact_signed", "route_selected"}
MIN_REASON_CHARS = 12


def _canonical(rec):
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _head_path(records=RECORDS):
    """THE HEAD BELONGS TO THE STORE, NOT TO THE MODULE.

    CHAIN_HEAD was a module constant bound to the default store, so every
    function that accepted a `records` argument honoured it for the RECORDS and
    ignored it for the HEAD. `ingest(records=somewhere_else)` therefore wrote
    its records to the other directory and stamped the head of the REAL one
    with a hash from records the real store does not contain - breaking the
    live chain from outside, using a parameter whose whole purpose was
    isolation.

    A parameterised store with a hardcoded head is not parameterised; it is a
    trap with a keyword argument on it. Found when a build gate was changed to
    ingest into a temp directory and the machine's own chain went red."""
    return os.path.join(records, "_head.json")


def _head(records=RECORDS):
    try:
        with open(_head_path(records), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"hash": "0" * 64, "count": 0}


def _provenance_exists(event_id):
    """Independent check. The emitter's claim is not evidence of itself."""
    if not event_id:
        return False
    try:
        from core.provenance_manager import ProvenanceManager
        pm = ProvenanceManager()
        for getter in ("get_event", "get_event_by_id", "event"):
            fn = getattr(pm, getter, None)
            if callable(fn):
                return bool(fn(event_id))
        # No lookup available is NOT a pass. Unverifiable and verified must
        # not produce the same record.
        return False
    except Exception:
        return False


def classify(ev, provenance_ok):
    """-> (usable, label, why). The HARNESS decides this, never the emitter."""
    t = ev.get("event_type")
    reason = (ev.get("reason") or "").strip()
    if t in DENY_TYPES:
        if len(reason) < MIN_REASON_CHARS:
            return False, "negative", (
                f"denied with no usable reason ({len(reason)} chars). A denial "
                f"without a reason teaches avoidance, not understanding.")
        return True, "negative", "denied, and the reason explains the rule"
    if t in ALLOW_TYPES:
        if not provenance_ok:
            return False, "positive", (
                "allowed, but the provenance event does not resolve. "
                "'It was allowed' with nothing behind it is indistinguishable "
                "from 'nobody checked'.")
        return True, "positive", "allowed, with provenance that resolves"
    return False, "unknown", f"{t!r} has no classification rule"


class ChainBroken(RuntimeError):
    pass


def ingest(spool=SPOOL, records=RECORDS, quarantine=True):
    """Spool -> records. VERIFIES BEFORE IT WRITES, and refuses a broken chain.

    THE ORDER MATTERS AND THIS HAD IT WRONG. Verification that runs at READ
    time means bad data is already in the store by the time anybody looks -
    and worse, this used to append happily onto a chain that was already
    broken, which buries the break under later records and makes the store
    look longer and healthier the more it is used. A corrupted chain must stop
    accepting, not keep growing.

    Two write-time refusals:

      the chain is checked BEFORE the first append. A broken store admits
      nothing further until somebody resolves it.
      a record that cannot be verified goes to quarantine, NOT to records.
      pairs() reads records only, so an unverifiable decision can never reach
      the training set by being marked and then filtered later.
    """
    os.makedirs(records, exist_ok=True)

    ok, probs = verify(records)
    if not ok:
        raise ChainBroken(
            "the record chain is broken; refusing to append.\n  " +
            "\n  ".join(probs[:3]) +
            "\n  Appending onto a broken chain buries the break under later "
            "records. Resolve it before collecting more.")
    seen = set()
    for f in glob.glob(os.path.join(records, "records-*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(json.loads(line)["source_sha256"])
                except Exception:
                    pass

    # _head(records), NOT _head(). The same bug as the write path below and
    # the one in verify(): three call sites, one of them fixed is none of them
    # fixed. This read is what gave a fresh store its first record a seq and a
    # prev_hash from a DIFFERENT chain, so the new store was born broken.
    head = _head(records)
    prev, count = head["hash"], head["count"]
    written = skipped = quarantined = 0
    out_path = os.path.join(
        records, f"records-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        for f in sorted(glob.glob(os.path.join(spool, "emitted-*.jsonl"))):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        skipped += 1
                        continue
                    src = hashlib.sha256(line.strip().encode()).hexdigest()
                    if src in seen:
                        skipped += 1
                        continue
                    seen.add(src)
                    pok = _provenance_exists(ev.get("provenance_event"))
                    usable, label, why = classify(ev, pok)
                    rec = {
                        "seq": count + 1,
                        # UNKNOWN WHEN THE EMITTER DID NOT SAY. Every record
                        # written before 2026-09-12 predates the field, and
                        # `unknown` is not `production` - see pairs().
                        "origin": ev.get("origin") or "unknown",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "event_type": ev.get("event_type"),
                        "agent": ev.get("agent"),
                        "resource": ev.get("resource"),
                        "action": ev.get("action"),
                        "decision": ev.get("decision"),
                        "reason": ev.get("reason"),
                        "provenance_event": ev.get("provenance_event"),
                        "provenance_verified": pok,
                        "label": label,
                        "usable_as_pair": usable,
                        "why": why,
                        "source_sha256": src,
                        "prev_hash": prev,
                    }
                    rec["record_hash"] = hashlib.sha256(
                        _canonical(rec)).hexdigest()
                    line_out = (json.dumps(rec, ensure_ascii=False,
                                           sort_keys=True) + "\n").encode()
                    # QUARANTINE AT WRITE TIME. A record the harness could not
                    # verify never enters the store pairs() reads from, so it
                    # cannot reach the training set by being written first and
                    # filtered afterwards. It is KEPT, because discarding it
                    # would make an unverifiable decision indistinguishable
                    # from one that never happened.
                    if quarantine and not usable:
                        qfd = os.open(os.path.join(records, "_quarantine.jsonl"),
                                      os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                        try:
                            os.write(qfd, line_out)
                        finally:
                            os.close(qfd)
                        quarantined += 1
                        continue
                    os.write(fd, line_out)
                    prev, count = rec["record_hash"], count + 1
                    written += 1
    finally:
        os.close(fd)
    # THE CLASSIFICATION TRAVELS WITH THE DATA. mycelial-core may not import
    # this repository, so a sensitivity rule that lived only here would be
    # invisible to the trainer that actually moves weights. Writing it into
    # the records directory means the obligation arrives with the rows.
    # AN EXISTING DECLARATION IS NOT OVERWRITTEN. The classification belongs
    # to the DATA; this only fills it in when the directory has none. Writing
    # over it would let an ingest silently reclassify a store - and the
    # name-based fallback answers `unknown` for any path that is not a
    # recognised source, so overwriting would downgrade a correct declaration
    # to unknown purely because of where the rows were sitting.
    _man = os.path.join(records, "_sensitivity.json")
    if not os.path.exists(_man):
        try:
            from core.data_sensitivity import classify as _classify
            with open(_man, "w", encoding="utf-8") as fh:
                json.dump(_classify(os.path.basename(records.rstrip("/"))), fh,
                          indent=2)
        except Exception:                           # noqa: BLE001
            pass
    with open(_head_path(records), "w", encoding="utf-8") as fh:
        json.dump({"hash": prev, "count": count,
                   "updated": datetime.now(timezone.utc).isoformat()}, fh, indent=2)
    return {"written": written, "skipped": skipped,
            "quarantined": quarantined, "chain_count": count}


def _reviews(records=RECORDS):
    try:
        with open(os.path.join(records, "_origin_review.json"),
                  encoding="utf-8") as fh:
            return json.load(fh).get("reviews") or []
    except Exception:
        return []


def effective_origin(rec, reviews=()):
    """-> the origin to ACT on: what the record carries, or a reviewed finding.

    ONE RESOLVER, BECAUSE THERE ARE THREE READERS. pairs() builds the audit
    view, mycelial-core's training/harness_data.py builds the gradient, and
    core/drift_monitor.py raises alerts - and every one of them has to answer
    "is this real?" the same way. Fixing that in one reader and not the others
    is how the origin filter came to protect pairs() while the trainer read
    straight past it.

    A reviewed determination BEATS the stored field only where the stored field
    is absent. A record that says what it is outranks somebody's later reading
    of it - the same order of precedence this system uses everywhere else: the
    measurement over the claim about the measurement."""
    o = rec.get("origin")
    if o in ("production", "test"):
        return o
    seq = rec.get("seq")
    for r in reviews:
        if r.get("seq_from") <= seq <= r.get("seq_to"):
            return r.get("determination") or "unknown"
    return "unknown"


def read_records(records=RECORDS):
    out = []
    for f in sorted(glob.glob(os.path.join(records, "records-*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return sorted(out, key=lambda r: r.get("seq", 0))


def verify(records=RECORDS):
    """-> (ok, problems). A broken link names the record it broke at."""
    recs, problems = read_records(records), []
    prev = "0" * 64
    for r in recs:
        if r.get("prev_hash") != prev:
            problems.append(
                f"seq {r.get('seq')}: chain broken - prev_hash {str(r.get('prev_hash'))[:12]} "
                f"but the previous record hashes to {prev[:12]}. A record was "
                f"deleted, reordered, or edited at or before this point.")
        body = {k: v for k, v in r.items() if k != "record_hash"}
        if hashlib.sha256(_canonical(body)).hexdigest() != r.get("record_hash"):
            problems.append(f"seq {r.get('seq')}: record_hash does not match "
                            f"its own contents - this record was edited.")
        prev = r.get("record_hash") or prev
    head = _head(records)
    if recs and head.get("hash") != prev:
        problems.append("the chain head does not match the last record - "
                        "records were removed from the end.")
    return (not problems), problems


# THE MODEL'S INPUT FORMAT, DUPLICATED ACROSS THE REPO BOUNDARY ON PURPOSE.
#
# mycelial-core has the same string in training/harness_data.py :: text_for().
# It cannot be imported: that repo's check_boundary.py fails its build if it
# reaches into this one, and CLAUDE.md is explicit that the eval contract is
# COPIED rather than imported - one import is a coupling that would mean this
# repo cannot change without risking a training run, while two copies is a
# divergence something can check for.
#
# This is the something. tools/check_contracts.py asserts the two produce
# byte-identical output for the same inputs, so a change to either is caught
# here rather than by a model quietly receiving a string it has never seen and
# answering anyway - which nothing in a forward pass can report.
def model_input(agent, action, resource):
    """The exact string the model is trained on and audited against."""
    return f"agent={agent} action={action} resource={resource}"


def pairs(records=RECORDS, include_unknown_origin=False,
          dp_engaged=False, epsilon_spent=None):
    """-> training pairs. Only records the harness could verify.

    ORIGIN IS A SECOND GATE, AND IT IS AS STRICT AS PROVENANCE.

    This module already refuses a positive example whose provenance does not
    resolve, on the reasoning that "it was allowed" with nothing behind it is
    indistinguishable from "nobody checked". An event whose ORIGIN is unknown
    is indistinguishable from a test fixture, and the same conclusion follows:
    it does not become a training pair.

    That is expensive exactly once. Every record written before 2026-09-12
    lacks the field, so the usable set drops to what has been collected since
    - and the alternative is training a student on 1,405 pairs of which the
    top six scenarios, all of them build-gate probes, are 21.3% of the whole.
    A set that rebuilds in a week of ordinary operation is cheaper than a
    checkpoint nobody can trust, and a checkpoint cannot be corrected the way
    a record can.

    `include_unknown_origin` exists for auditing the old records, never for
    building a training set. The counts are reported by main() either way, so
    the loss is visible rather than silent."""
    # THE SENSITIVITY GATE, BEFORE A SINGLE PAIR IS BUILT.
    #
    # This module's own run record carries the sentence "the data is the
    # system's own security decisions, not the principal's records". True
    # today, and a claim in a comment - nothing rechecked it, and the
    # financial programme is about to point the same machinery at an asset
    # registry holding institutions, balances and benefit records.
    #
    # So the class is declared in config/data_sensitivity.json and the
    # obligation follows from the declaration. An undeclared source is
    # REFUSED, not assumed operational: a source nobody classified is the one
    # most likely to be new, and new is when somebody has just pointed the
    # trainer at something personal.
    # THE MANIFEST IN THE DIRECTORY WINS over the directory's name. The
    # classification is a property of the DATA, not of where it happens to be
    # sitting - a store copied to a temp path is the same rows - and this is
    # the same record that travels to mycelial-core, so one source of truth
    # serves both. A directory with neither a manifest nor a declared name is
    # unknown, and unknown refuses.
    from core.data_sensitivity import (gate as _sensitivity_gate,
                                       classify as classify_source)
    # BOTH CLAIMS, MOST RESTRICTIVE BINDS. The manifest used to win outright,
    # which let a directory declared personal_financial by name carry a
    # manifest saying system_operational and be ALLOWED. Data that can lower
    # its own restriction is not classified, it is self-certified.
    _man = os.path.join(records, "_sensitivity.json")
    _src = os.path.basename(records.rstrip("/"))
    _claims = []
    if os.path.exists(_man):
        try:
            with open(_man, encoding="utf-8") as fh:
                _m = json.load(fh)
            # A manifest that EXISTS is an explicit claim, so it is marked
            # as one. That matters in both directions: it can tighten a
            # path-derived classification, and it can never loosen one.
            if _m.get("class"):
                _claims.append({"class": _m["class"], "claimed": True})
            elif _m.get("source"):
                _claims.append(classify_source(_m["source"]))
        except Exception:                           # noqa: BLE001
            # A manifest that exists and cannot be read is a DECLARATION OF
            # UNKNOWN, not silence - it was written deliberately and cannot be
            # read, which is a finding. Ignoring it would infer "fine" from
            # "broken".
            _claims.append({"class": "unknown", "claimed": True})
    _sensitivity_gate(_src, dp_engaged=dp_engaged, epsilon_spent=epsilon_spent,
                      also=_claims)

    out = []
    _rev = _reviews(records)
    for r in read_records(records):
        if not r.get("usable_as_pair"):
            continue
        o = effective_origin(r, _rev)
        if o == "test":
            continue
        if o == "unknown" and not include_unknown_origin:
            continue
        out.append({
            "label": r["label"],
            "input": model_input(r.get("agent"), r.get("action"),
                                 r.get("resource")),
            "output": r.get("decision"),
            "rationale": r.get("reason"),
            "provenance_event": r.get("provenance_event"),
            "seq": r.get("seq"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    if a.ingest:
        r = ingest()
        print(f"  ingested {r['written']} new record(s), skipped {r['skipped']}, "
              f"quarantined {r['quarantined']}, chain length {r['chain_count']}")
    if a.verify:
        ok, probs = verify()
        print(f"  chain {'INTACT' if ok else 'BROKEN'}")
        for p in probs:
            print(f"    {p}")
        if not ok:
            return 1
    if a.pairs:
        p = pairs()
        neg = sum(1 for x in p if x["label"] == "negative")
        print(f"  {len(p)} usable pair(s): {neg} negative, {len(p)-neg} positive")
        for x in p[:4]:
            print(f"    [{x['label']:8}] {x['input'][:56]} :: "
                  f"{str(x['rationale'])[:52]}")
    if a.stats or not any((a.ingest, a.pairs, a.verify)):
        recs = read_records()
        usable = sum(1 for r in recs if r.get("usable_as_pair"))
        print(f"  {len(recs)} record(s), {usable} usable as pairs, "
              f"{len(recs)-usable} kept but excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
