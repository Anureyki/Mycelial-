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
        return self._get(f"natal:{entity_id}")

    def get_event(self, event_id):
        return self._get(f"event:{event_id}")

    def put_natal(self, record):
        missing = [k for k in NATAL_REQUIRED if not record.get(k)]
        if missing:
            raise Refused(f"natal record missing {missing}")
        if self.get_natal(record["entity_id"]):
            raise Refused(f"a fixed natal record for {record['entity_id']!r} already exists; "
                          "it is written once and never overwritten")
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
