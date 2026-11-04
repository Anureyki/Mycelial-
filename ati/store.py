#!/usr/bin/env python3
"""ATI records in ONE shared namespace, the way core/case_manager keeps cases.

    store = HermesStore(agent)          # any AgentBase - its send_a2a reaches Hermes
    store.put_natal(record)             # written once; a second write is refused
    store.put_event(snapshot)           # written by any agent
    store.get_natal(entity_id) / store.get_event(event_id)

Personal data (birth time and place) lives in the memory service under state/,
never in the repository. Scoring only reads; these writers exist because a
record has to get there somehow, and they refuse rather than overwrite.
"""
import json

NAMESPACE = "ati"
NATAL_REQUIRED = ("entity_id", "birth_datetime_utc", "birth_location")
EVENT_REQUIRED = ("event_id", "entity_id", "timestamp_utc", "event_type", "source_agent")


class Refused(Exception):
    pass


ABSENT_MARKERS = ("absent",)


def _is_absent(v):
    return v is None or v == "" or (isinstance(v, str) and v.startswith(ABSENT_MARKERS))


def with_pillars(record):
    """BaZi pillars are DERIVED whenever birth data is on the record - never left
    for someone to hand-enter (principal, 2026-11-03). Returns the record with
    `bazi_pillars` filled from ati/bazi.py, or raises Refused when birth data is
    present and derivation is impossible: a record that could carry its pillars
    and does not is refused rather than stored."""
    if not _is_absent(record.get("bazi_pillars")):
        return record
    local = record.get("birth_datetime_local")
    tz = str(record.get("birth_timezone") or "").split(" ")[0]
    loc = record.get("birth_location") or {}
    if not (local and tz):
        raise Refused("birth data is present but birth_datetime_local / birth_timezone are missing - "
                      "pillars cannot be derived, and a natal record without them is refused")
    from ati.bazi import pillars
    try:
        p = pillars(local, tz, lon=loc.get("lon") if isinstance(loc, dict) else None)
    except Exception as exc:
        raise Refused(f"pillar derivation failed: {exc}")
    out = dict(record)
    out["bazi_pillars"] = {k: p[k] for k in ("year", "month", "day", "hour")}
    out["bazi_derivation"] = {"basis": p["basis"], "flags": p["flags"], "method": p["method"]}
    out.pop("bazi_pillars_status", None)
    return out


def _unwrap(raw, depth=8):
    seen = 0
    while isinstance(raw, dict) and seen < depth:
        for k in ("result", "entry", "value", "memory", "data"):
            if k in raw:
                raw = raw[k]
                break
        else:
            return None
        seen += 1
    return raw if isinstance(raw, str) else None


class HermesStore:
    def __init__(self, agent):
        self.agent = agent

    def _get(self, key):
        raw = _unwrap(self.agent.send_a2a("hermes", "retrieve_memory", [NAMESPACE, key]))
        try:
            d = json.loads(raw) if raw else None
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _put(self, key, rec):
        self.agent.send_a2a("hermes", "store_memory", [NAMESPACE, key, json.dumps(rec), "true"])

    def get_natal(self, entity_id):
        """The latest version. Versions are append-only: v1 at natal:<id>,
        later ones at natal:<id>@v<n>, listed in natal_versions:<id>."""
        idx = self._get(f"natal_versions:{entity_id}") or {}
        latest = (idx.get("versions") or [])[-1:] if isinstance(idx, dict) else []
        return self._get(latest[0]) if latest else self._get(f"natal:{entity_id}")

    def amend_natal(self, entity_id, additions, reason):
        """Append a new version that FILLS absent fields and changes nothing that
        was recorded. The prior version stays readable; the new one names it."""
        cur = self.get_natal(entity_id)
        if not cur:
            raise Refused(f"no natal record for {entity_id!r} to amend")
        changed = [k for k, v in additions.items() if k in cur and not _is_absent(cur[k]) and cur[k] != v]
        if changed:
            raise Refused(f"an amendment may only fill absent fields; these are recorded: {changed}")
        idx = self._get(f"natal_versions:{entity_id}") or {"versions": [f"natal:{entity_id}"]}
        n = len(idx["versions"]) + 1
        key = f"natal:{entity_id}@v{n}"
        new = dict(cur, **additions)
        new["version"] = n
        new["supersedes"] = idx["versions"][-1]
        new["amendment_reason"] = reason
        self._put(key, new)
        idx["versions"].append(key)
        self._put(f"natal_versions:{entity_id}", idx)
        return new

    def get_event(self, event_id):
        return self._get(f"event:{event_id}")

    def put_natal(self, record):
        missing = [k for k in NATAL_REQUIRED if not record.get(k)]
        if missing:
            raise Refused(f"natal record missing {missing}")
        if self.get_natal(record["entity_id"]):
            raise Refused(f"a fixed natal record for {record['entity_id']!r} already exists; "
                          "it is written once and never overwritten")
        record = with_pillars(record)
        self._put(f"natal:{record['entity_id']}", record)
        return record

    def put_event(self, snapshot):
        missing = [k for k in EVENT_REQUIRED if not snapshot.get(k)]
        if missing:
            raise Refused(f"event snapshot missing {missing}")
        self._put(f"event:{snapshot['event_id']}", snapshot)
        return snapshot


class MemoryStore(HermesStore):
    """Same contract, held in a dict - for gates and dry runs, so nothing
    reaches the live store (never test through a write path)."""
    def __init__(self):
        self.d = {}

    def _get(self, key):
        return self.d.get(key)

    def _put(self, key, rec):
        self.d[key] = json.loads(json.dumps(rec))
