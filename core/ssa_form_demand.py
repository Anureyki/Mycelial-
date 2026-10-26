#!/usr/bin/env python3
"""ssa_form_demand: a pre-suit demand for the SSN consent record behind a collection.

    from core.ssa_form_demand import (new_cycle, authorize_cycle, draft, record_sent,
                                      record_response, status, cycle_report)

The principal's capability, recorded verbatim with its doctrine in
config/ssa_form_demand.json. What this module adds is the mechanics his rules
call for, and it enforces them rather than describing them:

  Rule 1  SSA-521 never appears in a demand. Checked on the drafted text.
  Rule 5  A federal agency is flagged to the routine-use track, every time.
  Rule 6  A financial institution cannot be drafted until eCBSV applicability is
          stated; when it applies, the retention window is computed against the
          statute (405b(e)(4), 2 years) and against the instructed 5 years, both
          shown, because only one of them is on the shelf.
  Rule 7  Nothing is ever sent from here. A demand is marked sent only when the
          principal reports he sent it, and only inside a cycle that carries his
          written authorization.

LEGAL DRAFTS; NOTHING LEAVES. The same boundary as core/dispute_letters.py.

A CITATION IS ONLY PRINTED IF ITS TEXT IS IN HAND. The resolver (Legal's own
lookup) is asked for every cite before it goes into a demand; a cite the shelf
cannot open is listed as unopened, never asserted. 15 U.S.C. 1681s-2 is held out
of the template: its text does not carry the proposition it was instructed for
(config `held_out`), and it stays out until the principal keeps or drops it.

Personal data - recipients, collection events, responses - lives under
state/legal/ssa_form_demand/ (gitignored, owner-only), never in the repo.
"""
import json
import os
import re
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "ssa_form_demand.json")


class Refused(Exception):
    """A rule the principal set would be broken. Nothing was written."""


def _cfg():
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def _dir():
    d = os.environ.get("SSA_DEMAND_DIR") or os.path.join(ROOT, "state", "legal", "ssa_form_demand")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _ledger_path():
    return os.path.join(_dir(), "ledger.json")


def load():
    try:
        with open(_ledger_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"cycles": {}, "demands": {}}


def _save(led):
    p = _ledger_path()
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(led, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, p)
    # VERIFY THE EFFECT: a ledger write that did not land is not a logged demand.
    if load() != led:
        raise Refused("ledger write did not land - nothing was recorded")


def _iso(d, what):
    try:
        return date.fromisoformat(str(d)[:10]).isoformat()
    except Exception:
        raise Refused(f"{what} must be a date, YYYY-MM-DD (got {d!r})")


def _today(today=None):
    return date.fromisoformat(str(today)[:10]) if today else date.today()


# --------------------------------------------------------------------- cycles

def new_cycle(label=""):
    led = load()
    cid = f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    led["cycles"][cid] = {"id": cid, "label": str(label)[:120],
                          "opened": date.today().isoformat(), "authorization": None}
    _save(led)
    return led["cycles"][cid]


def authorize_cycle(cycle_id, authorization_ref, authorized_on):
    """Rule 7: the principal's explicit written authorization for THIS cycle.
    `authorization_ref` names the writing (a signed note, a dated message);
    without it no demand in the cycle can be marked sent."""
    led = load()
    if cycle_id not in led["cycles"]:
        raise Refused(f"no cycle {cycle_id}")
    if not str(authorization_ref or "").strip():
        raise Refused("rule 7: authorization_ref must name the principal's written authorization")
    led["cycles"][cycle_id]["authorization"] = {"ref": str(authorization_ref)[:300],
                                                "on": _iso(authorized_on, "authorized_on")}
    _save(led)
    return led["cycles"][cycle_id]


# ----------------------------------------------------------------- the demand

def _resolve(resolver, cite, title_has=None):
    """-> (opened: bool, title, excerpt). Legal's own lookup decides; a miss is
    reported as unopened, never assumed."""
    if not resolver or not cite:
        return False, None, None
    try:
        hits = resolver(cite) or []
    except Exception:
        hits = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        ttl = str(h.get("title") or h.get("work") or "")
        if title_has and title_has.lower() not in ttl.lower():
            continue
        return True, ttl, re.sub(r"\s+", " ", str(h.get("text") or ""))[:400]
    return False, None, None


def _overlay(state, resolver):
    cfg = _cfg()
    st = (state or "").strip().upper()
    rows = []
    for item in cfg["state_overlay"].get(st, []):
        if item.get("lookup") is None:
            rows.append({"cite": item["cite"], "opened": False, "status": item.get("status")})
            continue
        ok, ttl, ex = _resolve(resolver, item["lookup"], item.get("title_has"))
        rows.append({"cite": item["cite"], "opened": ok, "work": ttl, "text_opens": ex,
                     "demand_item": item.get("demand_item"),
                     "status": None if ok else "not opened on the shelf - not asserted"})
    if st and st not in cfg["state_overlay"]:
        rows.append({"cite": None, "opened": False,
                     "status": f"no overlay mapped for {st}; only CA, TX and VA are mapped"})
    return rows


def _ecbsv_window(consent_date, today):
    if not consent_date:
        return None
    cd = date.fromisoformat(_iso(consent_date, "ecbsv.consent_date"))
    age_days = (today - cd).days
    cfg = _cfg()
    stat = cfg["citations"]["ecbsv_retention"]["statutory_years"]
    instr = cfg["five_year_window"]["instructed"]
    return {"consent_date": cd.isoformat(), "age_days": age_days,
            "statutory": {"years": stat, "cite": cfg["citations"]["ecbsv_retention"]["cite"],
                          "open": age_days <= stat * 365},
            "instructed": {"years": instr, "open": age_days <= instr * 365,
                           "basis": cfg["five_year_window"]["basis"]}}


def draft(cycle_id, recipient, requester, resolver=None, today=None):
    """Draft one demand into a cycle. -> the ledger entry, with `text` (markdown).

    recipient: {name, type, state, address?, collection_events: [{date?, context}],
                routine_use?: bool, ecbsv?: {applies: bool, consent_date?: YYYY-MM-DD}}
    requester: {name, mailing_address}"""
    cfg = _cfg()
    today = _today(today)
    led = load()
    if cycle_id not in led["cycles"]:
        raise Refused(f"no cycle {cycle_id} - open one with new_cycle")
    r = dict(recipient or {})
    rtype = r.get("type")
    if rtype not in cfg["recipient_types"]:
        raise Refused(f"recipient type must be one of {cfg['recipient_types']}")
    if not str(r.get("name") or "").strip():
        raise Refused("recipient name is required")
    events = [e for e in (r.get("collection_events") or []) if isinstance(e, dict)]
    if not events:
        raise Refused("at least one collection event is required - the demand is per collection")
    req = dict(requester or {})
    if not (str(req.get("name") or "").strip() and str(req.get("mailing_address") or "").strip()):
        raise Refused("requirement One: the demand names the requester - name and mailing address")

    flags, ecbsv = [], None
    # Rule 6
    if rtype == "financial_institution":
        e = r.get("ecbsv") or {}
        if e.get("applies") is None:
            raise Refused("rule 6: before any demand to a financial institution, confirm whether "
                          "eCBSV applies (ecbsv.applies true/false)")
        if e.get("applies"):
            ecbsv = _ecbsv_window(e.get("consent_date"), today) or {
                "consent_date": None, "note": "eCBSV applies; consent date unknown, so whether "
                                              "the retention window is open cannot be computed"}
            flags.append("ecbsv")
    # Rule 5
    if rtype == "federal_agency":
        flags.append("federal_routine_use_track")

    def cite_line(key):
        c = cfg["citations"][key]
        ok, ttl, _ = _resolve(resolver, c["lookup"])
        return c["cite"], ok

    unopened = []
    fcra, ok_b = cite_line("fcra_permissible_purpose")
    if not ok_b:
        unopened.append(fcra)
    cons, ok_c = cite_line("ecbsv_consent")
    ret, _ = cfg["citations"]["ecbsv_retention"]["cite"], ok_c
    if not ok_c:
        unopened.extend([cons, ret])
    pa, ok_p = cite_line("privacy_act_routine_use")
    if rtype == "federal_agency" and not ok_p:
        unopened.append(pa)

    overlay = _overlay(r.get("state"), resolver)
    did = f"{cycle_id}:{len([d for d in led['demands'].values() if d['cycle'] == cycle_id]) + 1}"

    lines = [f"# Demand for SSN consent documentation — {r['name']}", "",
             f"Date: {today.isoformat()}", "",
             f"From: {req['name']}", f"{req['mailing_address']}", "",
             f"To: {r['name']}" + (f"\n{r['address']}" if r.get("address") else ""), "",
             "## What I am requesting", "",
             "For each occasion listed below on which you collected, used, verified or disclosed my "
             "Social Security number, produce the documentation of my consent:", ""]
    for i, ev in enumerate(events, 1):
        lines.append(f"{i}. {ev.get('date') or 'date unknown to me'} — {ev.get('context') or 'context not stated'}")
    lines += ["",
              f"- **Form SSA-89** ({cfg['ssa_89']['name']}), if you verified my SSN through the "
              f"Social Security Administration's {cfg['ssa_89']['program']} program, which operates "
              "on the number holder's signed SSA-89; or any equivalent written consent you relied on.",
              f"- The **permissible purpose** under {fcra} on which you obtained any consumer report "
              "in connection with that collection."]
    if ecbsv:
        lines.append(f"- Because you are a financial institution using eCBSV: the written, including "
                     f"electronic, consent required by {cons}, and the records {ret} requires you to "
                     f"retain to demonstrate compliance.")
    if rtype == "federal_agency":
        lines.append(f"- If any disclosure was made under a routine use ({pa}), identify the routine "
                     "use and the system of records notice that publishes it.")
    for o in overlay:
        if o.get("opened") and o.get("demand_item"):
            lines.append(f"- {o['demand_item'][0].upper() + o['demand_item'][1:]} ({o['cite']}).")
    lines += ["", "## How to respond", "",
              f"Please respond in writing within {cfg['ignore_days']} days of receipt, to the address "
              "above. If you hold no such documentation for an occasion, please say so in writing for "
              "that occasion.", "",
              "This is a request for records. It is not a complaint or a claim.", "",
              f"{req['name']}"]
    text = "\n".join(lines)

    # Rule 1, on the text itself
    for bad in cfg["never_cite"]:
        if bad.lower() in text.lower():
            raise Refused(f"rule 1: {bad} appears in the drafted demand")

    entry = {"id": did, "cycle": cycle_id, "drafted_on": today.isoformat(), "status": "drafted",
             "recipient": {k: r.get(k) for k in ("name", "type", "state", "address")},
             "collection_events": events, "flags": flags, "ecbsv": ecbsv,
             "overlay": overlay, "unopened_citations": unopened,
             "held_out": [h["cite"] for h in cfg["held_out"]],
             "form_demanded": "SSA-89 or equivalent consent" + (" + eCBSV consent records" if ecbsv else ""),
             "sent": None, "response": None, "text": text}
    led["demands"][did] = entry
    _save(led)
    return entry


# -------------------------------------------------------- what came back

def record_sent(demand_id, sent_on, method):
    """The principal reports he sent it. Rule 7: only inside an authorized cycle."""
    led = load()
    d = led["demands"].get(demand_id)
    if not d:
        raise Refused(f"no demand {demand_id}")
    auth = (led["cycles"].get(d["cycle"]) or {}).get("authorization")
    if not auth:
        raise Refused("rule 7: this cycle has no written authorization on record - "
                      "authorize_cycle first; nothing was marked sent")
    if not str(method or "").strip():
        raise Refused("method is required (e.g. certified mail, return receipt)")
    d["sent"] = {"on": _iso(sent_on, "sent_on"), "method": str(method)[:120],
                 "under_authorization": auth["ref"]}
    d["status"] = "sent"
    _save(led)
    return d


def record_response(demand_id, received_on, content, form_produced,
                    refused=False, stated_purpose=None, actual_use=None, purpose_matches_use=None):
    """What came back. A mismatch is the principal's finding (purpose_matches_use
    False), or a stated purpose and actual use both given and different."""
    led = load()
    d = led["demands"].get(demand_id)
    if not d:
        raise Refused(f"no demand {demand_id}")
    if not d.get("sent"):
        raise Refused("this demand is not recorded as sent - a response to an unsent demand "
                      "is not evidence of anything")
    if purpose_matches_use is None and stated_purpose and actual_use:
        purpose_matches_use = (str(stated_purpose).strip().lower() == str(actual_use).strip().lower())
    d["response"] = {"received_on": _iso(received_on, "received_on"), "content": str(content)[:2000],
                     "form_produced": bool(form_produced), "refused": bool(refused),
                     "stated_purpose": stated_purpose, "actual_use": actual_use,
                     "purpose_matches_use": purpose_matches_use}
    d["status"] = "responded"
    _save(led)
    return d


def _escalation(d, today):
    """-> list of reasons (requirement Three)."""
    out = []
    resp = d.get("response")
    if resp:
        if resp.get("refused"):
            out.append("refused")
        if resp.get("form_produced") and resp.get("purpose_matches_use") is False:
            out.append("purpose_mismatch")
    elif d.get("sent"):
        waited = (today - date.fromisoformat(d["sent"]["on"])).days
        if waited > _cfg()["ignore_days"]:
            out.append(f"ignored_{waited}_days")
    return out


def complaint_outline(d, conduct_state=None):
    """Requirement Four: a DRAFT FCRA 1681s complaint outline for a mismatch,
    routed through the existing complaint lane. Nothing is filed."""
    from core.complaint_lane import route, Refused as LaneRefused
    try:
        venues = route("credit_reporting", conduct_state=conduct_state)
    except LaneRefused as exc:
        venues = {"refused": str(exc)}
    resp = d.get("response") or {}
    return {
        "kind": "draft_fcra_1681s_complaint_outline", "demand": d["id"],
        "subject": "credit_reporting", "authority": "15 U.S.C. 1681s (administrative enforcement)",
        "venues": venues,
        "facts": [f"Demand sent {d['sent']['on']} by {d['sent']['method']} to {d['recipient']['name']}.",
                  f"Response {resp.get('received_on')}: form produced; stated purpose "
                  f"{resp.get('stated_purpose')!r}; actual use {resp.get('actual_use')!r}."],
        "doctrine_rule_3": _cfg()["doctrine"][2]["text"],
        "caution": "A regulator complaint does not toll 15 U.S.C. 1681p. The clock keeps running.",
        "status": "draft - nothing filed"}


def status(today=None):
    today = _today(today)
    led = load()
    rows = []
    for d in led["demands"].values():
        esc = _escalation(d, today)
        row = {"id": d["id"], "recipient": d["recipient"]["name"], "status": d["status"],
               "escalation": esc}
        if "purpose_mismatch" in esc:
            row["complaint_outline"] = complaint_outline(d, d["recipient"].get("state"))
        rows.append(row)
    return {"demands": rows, "escalated": [r for r in rows if r["escalation"]]}


# ---------------------------------------------------------------- the report

def cross_reference():
    """Doctrine rule 4. Named with their real state; neither is a claim on record yet."""
    return [
        {"theory": "Continuing violation - HUD-VASH annual renewals",
         "state": "argued in the 2026-09-23 reply filing (knowledge_base/legal_agent/filings); "
                  "not yet a claim on record"},
        {"theory": "DoDI 1000.30 lifetime-restriction argument",
         "state": "authority shelved (DoDI 1000.30, SSN reduction); not yet a claim on record"}]


def cycle_report(cycle_id, today=None):
    """Requirement Six: one markdown file per cycle, summary table at the top."""
    today = _today(today)
    cfg = _cfg()
    led = load()
    cyc = led["cycles"].get(cycle_id)
    if not cyc:
        raise Refused(f"no cycle {cycle_id}")
    ds = [d for d in led["demands"].values() if d["cycle"] == cycle_id]
    out = [f"# ssa_form_demand — {cycle_id}" + (f" ({cyc['label']})" if cyc.get("label") else ""),
           "", f"Generated {today.isoformat()}. Opened {cyc['opened']}. Written authorization: "
           + (f"{cyc['authorization']['ref']} ({cyc['authorization']['on']})" if cyc.get("authorization")
              else "**none on record - nothing in this cycle may be sent (rule 7)**"), "",
           "| Recipient | Form demanded | Produced | Mismatch | Escalation |",
           "|---|---|---|---|---|"]
    for d in ds:
        resp = d.get("response") or {}
        esc = _escalation(d, today)
        produced = ("yes" if resp.get("form_produced") else "no") if resp else "—"
        mism = "yes" if resp.get("purpose_matches_use") is False else ("no" if resp else "—")
        out.append(f"| {d['recipient']['name']} | {d['form_demanded']} | {produced} | {mism} | "
                   f"{', '.join(esc) if esc else d['status']} |")
    out += ["", "## Doctrine (standing, verbatim)", ""]
    out += [f"{x['rule']}. {x['text']}" for x in cfg["doctrine"]]
    out += ["", "## Cross-reference (rule 4)", ""]
    out += [f"- {x['theory']}: {x['state']}" for x in cross_reference()]
    out += ["", "## Held out of every demand", ""]
    out += [f"- {h['cite']} - instructed for {h['instructed_for']}. {h['finding']} Status: {h['status']}."
            for h in cfg["held_out"]]
    for d in ds:
        out += ["", "---", "", f"## {d['id']} — {d['recipient']['name']} ({d['recipient']['type']}, "
                f"{d['recipient'].get('state') or 'state not given'})", ""]
        if d["flags"]:
            out.append(f"Flags: {', '.join(d['flags'])}")
        if "federal_routine_use_track" in d["flags"]:
            out.append("Rule 5: federal agency - routine-use track, separate treatment.")
        if d.get("ecbsv"):
            out.append(f"eCBSV (rule 6): {json.dumps(d['ecbsv'])}")
        out.append("State overlay: " + ("; ".join(
            f"{o['cite']} - " + ("opened" if o.get("opened") else o.get("status") or "not opened")
            for o in d["overlay"]) or "none mapped"))
        if d["unopened_citations"]:
            out.append(f"Citations the shelf could not open (not asserted): {', '.join(d['unopened_citations'])}")
        s, r = d.get("sent"), d.get("response")
        out.append(f"Sent: {s['on']} by {s['method']}" if s else "Sent: not yet")
        if r:
            out.append(f"Response {r['received_on']}: form produced {r['form_produced']}; refused "
                       f"{r['refused']}; content: {r['content']}")
        esc = _escalation(d, today)
        if "purpose_mismatch" in esc:
            out.append("Draft FCRA 1681s complaint outline: "
                       + json.dumps(complaint_outline(d, d["recipient"].get("state")), ensure_ascii=False))
        out += ["", "### Demand text", "", d["text"]]
    md = "\n".join(out) + "\n"
    path = os.path.join(_dir(), f"{cycle_id}.md")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {"path": path, "demands": len(ds), "markdown": md}
