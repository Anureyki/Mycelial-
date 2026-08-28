#!/usr/bin/env python3
"""
A case is one object, shared. Domain-agnostic.

The problem this solves is not legal or financial: a real matter has evidence,
obligations, participants and a history, and every agent that touches it keeps
its own partial copy. Legal writes a note, Accounting writes a transaction,
Hermes writes a memory, Trust writes another note - and nothing can answer
"what is the state of this case" because there is no such thing, only four
views that have already drifted.

So the case lives in ONE namespace and every agent references it by id.
`store_own_memory` deliberately namespaces per agent (`agent_<id>`); this uses
Hermes directly with a shared namespace so all four see the same bytes. An
agent that wants to add something appends an event; it does not keep a copy.

Two rules the layer must not break:

  - **Evidence never travels in the event envelope.** An event carries a type,
    a case id, an actor and a REFERENCE. Boss routes on the type and cannot
    inspect what is not there. That is structural, not a convention: the
    orchestrator practises no domain, so it must not be handed domain content
    to be tempted by.

  - **"Insufficient evidence" is a real status, not a failure.** An element
    that cannot be established from what is held must say so, distinctly from
    one that has been established and one that has been refuted. A case
    management tool that only records wins is a tool for losing slowly.
"""
import json
import uuid
from datetime import datetime

CASE_NAMESPACE = "cases"

# The closed set. A new event type is a deliberate addition, not something a
# caller invents - Boss routes on these and an unknown type routes nowhere.
EVENT_TYPES = (
    "case_opened",
    "document_added",
    "evidence_added",
    "participant_added",
    "element_updated",
    "obligation_recorded",
    "payment_recorded",
    "case_state_changed",
    "task_completed",
    "note_added",
)

CASE_STATES = (
    "open",             # exists, nothing asked for yet
    "gathering",        # collecting evidence
    "submitted",        # request or filing made, clock running
    "awaiting_response",
    "responded",        # the other side answered
    "escalated",        # taken to a higher forum or authority
    "resolved",
    "closed",
)

# Legal's frame. Not "which law applies" - which ELEMENTS are established.
# A claim stands or falls on these individually, and each can be short.
ELEMENT_STATES = (
    "open",                  # not yet examined
    "established",           # supported by evidence held in the case
    "insufficient_evidence", # examined, and what is held does not support it
    "disputed",              # the other side contests it
    "refuted",               # the evidence runs against it
    "not_applicable",
)

DEFAULT_ELEMENTS = (
    "barrier",
    "requested_accommodation",
    "supporting_evidence",
    "response",
    "current_status",
)


def _now():
    return datetime.now().isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CaseManager:
    """Reads and writes one shared case object through Hermes.

    Constructed with the calling agent so it can use that agent's A2A
    transport and be attributed as the actor - but the NAMESPACE is shared, so
    which agent is holding the handle changes only the attribution, never the
    data."""

    def __init__(self, agent, case_id=None):
        self.agent = agent
        self.case_id = case_id

    # ---------- storage ----------
    @staticmethod
    def _unwrap(raw, depth=8):
        """Dig the stored string out of however many envelopes it arrived in.

        Hermes returns {"result": {"result": {"value": "..."}}} depending on the
        path taken, and only grow_agent happens to have a private helper for
        it. Without unwrapping, _read returned the ENVELOPE - a dict with no
        case_id - and every reader but the writer raised KeyError on a case
        that was stored perfectly well."""
        seen = 0
        while isinstance(raw, dict) and seen < depth:
            # "entry" is the one that mattered: Hermes returns
            # {"result": {"entry": {..., "value": "<json>"}}} and omitting it
            # made every read return None on a case that had stored perfectly.
            for k in ("result", "entry", "value", "memory", "data"):
                if k in raw:
                    raw = raw[k]
                    break
            else:
                return None
            seen += 1
        return raw if isinstance(raw, str) else None

    def _read(self, case_id=None):
        cid = case_id or self.case_id
        if not cid:
            return None
        raw = self._unwrap(self.agent.send_a2a("hermes", "retrieve_memory",
                                               [CASE_NAMESPACE, cid]))
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) and d.get("case_id") else None
        except Exception:
            return None

    def _write(self, case):
        case["updated_at"] = _now()
        self.agent.send_a2a("hermes", "store_memory",
                            [CASE_NAMESPACE, case["case_id"], json.dumps(case), "true"])
        return case

    def _index(self):
        raw = self._unwrap(self.agent.send_a2a("hermes", "retrieve_memory",
                                               [CASE_NAMESPACE, "case_index"]))
        try:
            idx = json.loads(raw) if raw else []
            return idx if isinstance(idx, list) else []
        except Exception:
            return []

    def _save_index(self, idx):
        self.agent.send_a2a("hermes", "store_memory",
                            [CASE_NAMESPACE, "case_index", json.dumps(idx), "true"])

    # ---------- lifecycle ----------
    def open_case(self, title, kind="", participants=None, elements=None):
        cid = _uid("case")
        case = {
            "case_id": cid,
            "title": title,
            "kind": kind,
            "state": "open",
            "opened_at": _now(),
            "opened_by": self.agent.agent_id,
            "participants": list(participants or []),
            "documents": [],
            "evidence": [],
            "obligations": [],
            "complaint_numbers": [],
            "elements": {name: {"state": "open", "evidence_ids": [], "note": ""}
                         for name in (elements or DEFAULT_ELEMENTS)},
            "events": [],
        }
        self.case_id = cid
        self._append(case, "case_opened", summary=title)
        self._write(case)
        idx = self._index()
        if cid not in idx:
            idx.append(cid)
            self._save_index(idx)
        return case

    # ---------- events ----------
    def _append(self, case, etype, summary="", ref=None, extra=None):
        """Append to the timeline. `ref` is an ID, never content.

        Anything a downstream reader needs beyond the reference it must fetch
        from the case itself - which is what keeps the envelope safe to route."""
        if etype not in EVENT_TYPES:
            raise ValueError(f"unknown event type {etype!r}; expected one of {EVENT_TYPES}")
        ev = {"event_id": _uid("ev"), "ts": _now(), "type": etype,
              "actor": self.agent.agent_id, "summary": summary[:280], "ref": ref}
        if extra:
            ev.update({k: v for k, v in extra.items() if k not in ("evidence", "content")})
        case.setdefault("events", []).append(ev)
        return ev

    def envelope(self, case_id, event):
        """What Boss is given: type, case, actor, reference. No content.

        Boss routes on `type`. It is not handed the evidence, the document body
        or the summary of what was found, because an orchestrator that can read
        domain content will eventually reason about it."""
        return {"case_id": case_id, "event_id": event["event_id"],
                "type": event["type"], "actor": event["actor"], "ts": event["ts"],
                "ref": event.get("ref")}

    # ---------- content ----------
    def add_document(self, kind, title, ref, note=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        doc = {"doc_id": _uid("doc"), "kind": kind, "title": title, "ref": ref,
               "note": note, "added_at": _now(), "added_by": self.agent.agent_id}
        case["documents"].append(doc)
        ev = self._append(case, "document_added", summary=f"{kind}: {title}", ref=doc["doc_id"])
        self._write(case)
        return {"document": doc, "event": ev, "envelope": self.envelope(case["case_id"], ev)}

    def add_evidence(self, supports, kind, summary, doc_id=None, weight="unweighted"):
        """Attach evidence to an ELEMENT. `supports` names which one."""
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        if supports not in case["elements"]:
            return {"error": f"unknown element {supports!r}; case has {sorted(case['elements'])}"}
        item = {"evidence_id": _uid("ev_item"), "supports": supports, "kind": kind,
                "summary": summary, "doc_id": doc_id, "weight": weight,
                "added_at": _now(), "added_by": self.agent.agent_id}
        case["evidence"].append(item)
        case["elements"][supports]["evidence_ids"].append(item["evidence_id"])
        ev = self._append(case, "evidence_added",
                          summary=f"evidence for {supports}", ref=item["evidence_id"])
        self._write(case)
        return {"evidence": item, "event": ev, "envelope": self.envelope(case["case_id"], ev)}

    def set_element(self, name, state, note=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        if state not in ELEMENT_STATES:
            return {"error": f"unknown element state {state!r}; expected {ELEMENT_STATES}"}
        if name not in case["elements"]:
            case["elements"][name] = {"state": "open", "evidence_ids": [], "note": ""}
        case["elements"][name].update({"state": state, "note": note,
                                       "assessed_at": _now(),
                                       "assessed_by": self.agent.agent_id})
        ev = self._append(case, "element_updated", summary=f"{name} -> {state}", ref=name)
        self._write(case)
        return {"element": name, "state": state, "event": ev,
                "envelope": self.envelope(case["case_id"], ev)}

    def set_state(self, state, note=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        if state not in CASE_STATES:
            return {"error": f"unknown case state {state!r}; expected {CASE_STATES}"}
        prior = case.get("state")
        case["state"] = state
        ev = self._append(case, "case_state_changed",
                          summary=f"{prior} -> {state}. {note}".strip(), ref=state)
        self._write(case)
        return {"from": prior, "to": state, "event": ev,
                "envelope": self.envelope(case["case_id"], ev)}

    def add_participant(self, role, name, note=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        p = {"role": role, "name": name, "note": note, "added_at": _now()}
        case["participants"].append(p)
        ev = self._append(case, "participant_added", summary=f"{role}: {name}", ref=role)
        self._write(case)
        return {"participant": p, "event": ev, "envelope": self.envelope(case["case_id"], ev)}

    def add_complaint_number(self, number, forum=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        rec = {"number": number, "forum": forum, "added_at": _now()}
        case["complaint_numbers"].append(rec)
        ev = self._append(case, "note_added", summary=f"complaint {number} ({forum})", ref=number)
        self._write(case)
        return {"complaint": rec, "event": ev, "envelope": self.envelope(case["case_id"], ev)}

    def complete_task(self, what, outcome=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        ev = self._append(case, "task_completed", summary=f"{what}. {outcome}".strip(), ref=what)
        self._write(case)
        return {"event": ev, "envelope": self.envelope(case["case_id"], ev)}

    # ---------- obligations (Accounting's frame) ----------
    # Bookkeeping asks what was paid. A case asks what is OWED, on what
    # cadence, WHO IS AUTHORISED to pay it, and whether there is evidence the
    # payment was actually made. A ledger entry answers the last of those and
    # is silent on the other three, which is how a rent obligation met by the
    # wrong payor, or met and undocumented, looks identical to one in good
    # standing.
    def add_obligation(self, name, amount, cadence="monthly", due_day=None,
                       authorized_payors=None, note=""):
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        ob = {"obligation_id": _uid("ob"), "name": name,
              "amount": amount, "cadence": cadence, "due_day": due_day,
              "authorized_payors": list(authorized_payors or []),
              "note": note, "payments": [],
              "added_at": _now(), "added_by": self.agent.agent_id}
        case.setdefault("obligations", []).append(ob)
        ev = self._append(case, "obligation_recorded",
                          summary=f"{name} {amount} {cadence}", ref=ob["obligation_id"])
        self._write(case)
        return {"obligation": ob, "event": ev, "envelope": self.envelope(case["case_id"], ev)}

    def record_payment(self, obligation_id, amount, paid_on, payor,
                       evidence_ref="", note=""):
        """A payment with WHO paid it and what proves it.

        Both are recorded even when absent, because "paid, no proof" and
        "paid by someone not authorised" are the two states that matter in a
        dispute and neither is visible in an amount."""
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        ob = next((o for o in case.get("obligations", [])
                   if o["obligation_id"] == obligation_id), None)
        if not ob:
            return {"error": f"no such obligation: {obligation_id}"}
        authorised = (not ob["authorized_payors"]) or payor in ob["authorized_payors"]
        pay = {"payment_id": _uid("pay"), "amount": amount, "paid_on": paid_on,
               "payor": payor, "evidence_ref": evidence_ref, "note": note,
               "payor_authorized": authorised,
               "has_evidence": bool(evidence_ref),
               "recorded_at": _now(), "recorded_by": self.agent.agent_id}
        ob["payments"].append(pay)
        ev = self._append(case, "payment_recorded",
                          summary=f"{ob['name']} {amount} by {payor}", ref=pay["payment_id"])
        self._write(case)
        return {"payment": pay, "obligation": ob["name"], "event": ev,
                "envelope": self.envelope(case["case_id"], ev)}

    def obligation_status(self, case_id=None):
        case = self._read(case_id)
        if not case:
            return {"error": f"no such case: {case_id or self.case_id}"}
        out = []
        for ob in case.get("obligations", []):
            if ob.get("voided"):
                continue
            pays = ob.get("payments", [])
            undocumented = [p for p in pays if not p["has_evidence"]]
            unauthorised = [p for p in pays if not p["payor_authorized"]]
            out.append({
                "obligation_id": ob["obligation_id"], "name": ob["name"],
                "amount": ob["amount"], "cadence": ob["cadence"],
                "due_day": ob.get("due_day"),
                "authorized_payors": ob["authorized_payors"] or ["(none recorded)"],
                "payments_recorded": len(pays),
                "payments_without_evidence": len(undocumented),
                "payments_by_unauthorized_payor": len(unauthorised),
                "standing": ("no payments recorded" if not pays else
                             "contestable - payments lack evidence" if undocumented else
                             "contestable - paid by an unauthorised payor" if unauthorised else
                             "documented"),
            })
        return {"case_id": case["case_id"], "obligations": out,
                "note": ("An obligation with no recorded authorised payor cannot be "
                         "assessed for authority - that is a gap in the record, not a pass.")
                if any(not o["authorized_payors"] or o["authorized_payors"] == ["(none recorded)"]
                       for o in out) else ""}

    # ---------- voiding ----------
    def void_item(self, kind, item_id, reason):
        """Mark a document, evidence item or obligation void, with a reason.

        Not deleted. A case's history is evidence in its own right, and an item
        that silently disappears is indistinguishable from one that was never
        there. Voided items keep their place in the timeline and drop out of
        every standing calculation.

        This exists because demo data reached a live case: placeholder
        documents and a fabricated rent figure were written while proving the
        machinery worked, and leaving them next to a real ledger is how a
        record stops being trustworthy."""
        case = self._read()
        if not case:
            return {"error": f"no such case: {self.case_id}"}
        buckets = {"document": ("documents", "doc_id"),
                   "evidence": ("evidence", "evidence_id"),
                   "obligation": ("obligations", "obligation_id"),
                   # A complaint number had no void path and a fabricated one
                   # sat in a live fair-housing case. Of everything in a case
                   # object this is the field least tolerant of an invented
                   # value: it addresses a real docket at a real agency.
                   "complaint": ("complaint_numbers", "number"),
                   "participant": ("participants", "name")}
        if kind not in buckets:
            return {"error": f"kind must be one of {sorted(buckets)}"}
        field, idkey = buckets[kind]
        item = next((x for x in case.get(field, []) if x.get(idkey) == item_id), None)
        if not item:
            return {"error": f"no such {kind}: {item_id}"}
        item["voided"] = True
        item["voided_at"] = _now()
        item["voided_by"] = self.agent.agent_id
        item["void_reason"] = reason
        # An element must not still count evidence that has been voided.
        if kind == "evidence":
            el = case.get("elements", {}).get(item.get("supports"))
            if el and item_id in el.get("evidence_ids", []):
                el["evidence_ids"].remove(item_id)
        ev = self._append(case, "note_added",
                          summary=f"VOIDED {kind}: {reason}"[:280], ref=item_id)
        self._write(case)
        return {"voided": kind, "id": item_id, "reason": reason, "event": ev,
                "envelope": self.envelope(case["case_id"], ev)}

    # ---------- reading ----------
    def get(self, case_id=None):
        return self._read(case_id) or {"error": f"no such case: {case_id or self.case_id}"}

    def timeline(self, case_id=None, limit=100):
        case = self._read(case_id)
        if not case:
            return {"error": f"no such case: {case_id or self.case_id}"}
        evs = sorted(case.get("events", []), key=lambda e: e.get("ts") or "")
        return {"case_id": case["case_id"], "title": case.get("title"),
                "state": case.get("state"), "events": evs[-limit:]}

    def summary(self, case_id=None):
        """State of the case without any evidence content - safe to narrate."""
        case = self._read(case_id)
        if not case:
            return {"error": f"no such case: {case_id or self.case_id}"}
        el = case.get("elements", {})
        return {
            "case_id": case["case_id"], "title": case.get("title"),
            "state": case.get("state"), "opened_at": case.get("opened_at"),
            "participants": len(case.get("participants", [])),
            "documents": len([d for d in case.get("documents", []) if not d.get("voided")]),
            "evidence_items": len([e for e in case.get("evidence", []) if not e.get("voided")]),
            "obligations": len([o for o in case.get("obligations", []) if not o.get("voided")]),
            "voided_items": len([x for f in ("documents", "evidence", "obligations")
                                 for x in case.get(f, []) if x.get("voided")]),
            "complaint_numbers": [c["number"] for c in case.get("complaint_numbers", [])
                                  if not c.get("voided")],
            "elements": {k: v.get("state") for k, v in el.items()},
            "unestablished": [k for k, v in el.items()
                              if v.get("state") in ("open", "insufficient_evidence")],
            "events": len(case.get("events", [])),
        }

    def list_cases(self):
        out = []
        for cid in self._index():
            c = self._read(cid)
            if c:
                out.append({"case_id": cid, "title": c.get("title"),
                            "state": c.get("state"), "opened_at": c.get("opened_at")})
        return out
