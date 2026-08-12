#!/usr/bin/env python3
"""
Provenance event schema - the canonical shape every artifact-touching
operation (human or agent) is recorded in. Mirrors core/schemas.py's
relationship-schema pattern: agents don't invent their own provenance
formats, they build events through new_provenance_event() and hand them to
the Provenance Service (services/provenance/service.py), which is the only
thing allowed to write to the provenance store.

Architecture this schema assumes (do not change without updating the
platform-wide provenance docs once those exist): Anansi is the Human
Experience Layer and only ever *displays* provenance; Boss is the
orchestrator and is the usual actor_type="agent" source of "orchestrate"
events; domain agents (Coding, Security, Legal, ...) are actor_type="agent"
sources of create/modify/execute events; a human operating through Anansi
is actor_type="human". The Provenance Service itself is actor_type="system"
for anything it derives rather than receives (e.g. verification runs).
"""
import hashlib
import uuid
from datetime import datetime

SCHEMA_VERSION = "1.0"

ACTOR_TYPES = {"human", "agent", "system"}

# One shared operation vocabulary for both human and agent actors (spec
# sections 3 + 4: "create|modify|review|approve|reject|execute|orchestrate"
# plus "instruction, approval, rejection, creation, modification, review,
# selection, override"). Deliberately NOT a separate "human operations"
# list - actor_type is the orthogonal discriminator, so a human
# modification is operation="modify" + actor_type="human", not a different
# vocabulary agents also have to know about.
OPERATIONS = {
    "create", "modify", "review", "approve", "reject", "execute",
    "orchestrate", "instruct", "select", "override",
}

# Operations that count as *authorship* (touching artifact content) versus
# supervisory/non-modifying ones. classify_origin() uses this split - an
# "approve" or "review" alone doesn't make an actor a co-author the way
# "modify" does.
AUTHORING_OPERATIONS = {"create", "modify", "execute"}

ORIGIN_CLASSIFICATIONS = {
    "HUMAN", "AI_GENERATED", "AI_ASSISTED", "AI_MODIFIED",
    "HUMAN_MODIFIED_AI", "MULTI_AGENT", "AI_ORCHESTRATED", "UNKNOWN",
}

# UNVERIFIED: claimed but never recorded through this service.
# RECORDED: an event chain exists, but nothing has cryptographically
#           checked the artifact content against artifact_hash yet.
# VERIFIED / INVALID: a verify call compared current content's hash to the
#           stored artifact_hash and it matched / didn't.
VERIFICATION_STATES = {"UNVERIFIED", "RECORDED", "VERIFIED", "INVALID"}


def now_iso():
    return datetime.now().isoformat()


def new_event_id():
    return f"ev_{uuid.uuid4().hex[:16]}"


def new_execution_id():
    return f"exec_{uuid.uuid4().hex[:12]}"


def new_artifact_id():
    return f"artifact_{uuid.uuid4().hex[:12]}"


def sha256_hex(content):
    """content may be str or bytes. Returns None (not a hash of empty
    input) when content is None - unavailable content must be represented
    as unavailable, never fabricated into a hash of nothing."""
    if content is None:
        return None
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def new_provenance_event(operation, actor_type, actor_id=None, artifact_id=None,
                          parent_artifact_id=None, execution_id=None, agent_id=None,
                          model_id=None, tools_used=None, input_artifacts=None,
                          output_artifacts=None, human_contribution=None,
                          metadata=None, event_id=None, timestamp=None):
    """Build a canonical provenance event dict. Fields the caller has no
    information for are left None/empty rather than guessed - unavailable
    information must be represented explicitly, not fabricated."""
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of {sorted(OPERATIONS)}, got {operation!r}")
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"actor_type must be one of {sorted(ACTOR_TYPES)}, got {actor_type!r}")
    if actor_type == "agent" and not agent_id:
        raise ValueError("actor_type='agent' requires agent_id")
    if human_contribution is None:
        human_contribution = actor_type == "human"
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or new_event_id(),
        "execution_id": execution_id,
        "artifact_id": artifact_id,
        "parent_artifact_id": parent_artifact_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "operation": operation,
        "agent_id": agent_id,
        "model_id": model_id,
        "tools_used": tools_used or [],
        "timestamp": timestamp or now_iso(),
        "input_artifacts": input_artifacts or [],
        "output_artifacts": output_artifacts or [],
        "human_contribution": human_contribution,
        "metadata": metadata or {},
    }


def validate_event(doc):
    """Returns (is_valid, [error, ...]). Does not mutate doc."""
    errors = []
    if not isinstance(doc, dict):
        return False, ["event must be a dict"]
    for field in ("event_id", "operation", "actor_type", "timestamp"):
        if not doc.get(field):
            errors.append(f"missing required field: {field}")
    if doc.get("operation") is not None and doc["operation"] not in OPERATIONS:
        errors.append(f"invalid operation: {doc.get('operation')!r}")
    if doc.get("actor_type") is not None and doc["actor_type"] not in ACTOR_TYPES:
        errors.append(f"invalid actor_type: {doc.get('actor_type')!r}")
    if doc.get("actor_type") == "agent" and not doc.get("agent_id"):
        errors.append("actor_type='agent' requires agent_id")
    return (len(errors) == 0), errors


def classify_origin(events):
    """Derive an origin classification from an artifact's provenance
    events. `events` must be chronologically ordered, oldest first. This
    is the ONLY place origin classification is computed - it is always
    derived, never set directly by a caller (spec section 5).

    Precedence, in order:
      1. No authoring events at all (only review/approve/instruct/...)  -> UNKNOWN
      2. Authoring events exist, all actor_type="human"                 -> HUMAN
      3. Authoring events exist, all actor_type="agent":
           - any "orchestrate" event anywhere in the history            -> AI_ORCHESTRATED
           - more than one distinct agent_id authored                  -> MULTI_AGENT
           - a human touched it non-authoringly (reviewed/approved/..)  -> AI_ASSISTED
           - otherwise                                                  -> AI_GENERATED
      4. Both human and agent authorship exist:
           - first author human, last author agent                     -> AI_MODIFIED
           - first author agent, last author human                     -> HUMAN_MODIFIED_AI
           - more than one distinct agent_id authored                  -> MULTI_AGENT
           - otherwise                                                  -> AI_ASSISTED
    """
    if not events:
        return "UNKNOWN"

    authoring = [e for e in events if e.get("operation") in AUTHORING_OPERATIONS]
    if not authoring:
        return "UNKNOWN"

    human_authors = [e for e in authoring if e.get("actor_type") == "human"]
    agent_authors = [e for e in authoring if e.get("actor_type") == "agent"]
    distinct_agents = {e["agent_id"] for e in agent_authors if e.get("agent_id")}
    has_orchestrate_event = any(e.get("operation") == "orchestrate" for e in events)

    if not agent_authors:
        return "HUMAN"

    if not human_authors:
        if has_orchestrate_event:
            return "AI_ORCHESTRATED"
        if len(distinct_agents) > 1:
            return "MULTI_AGENT"
        human_touched_at_all = any(e.get("actor_type") == "human" for e in events)
        return "AI_ASSISTED" if human_touched_at_all else "AI_GENERATED"

    first_author, last_author = authoring[0], authoring[-1]
    if first_author["actor_type"] == "human" and last_author["actor_type"] == "agent":
        return "AI_MODIFIED"
    if first_author["actor_type"] == "agent" and last_author["actor_type"] == "human":
        return "HUMAN_MODIFIED_AI"
    if len(distinct_agents) > 1:
        return "MULTI_AGENT"
    return "AI_ASSISTED"


def verification_status(has_record, stored_hash, current_hash):
    """has_record: whether an artifact row exists at all in the store.
    current_hash: sha256 of content supplied for this verification check,
    or None if the caller only wants the recorded status without checking
    fresh content."""
    if not has_record:
        return "UNVERIFIED"
    if current_hash is None:
        return "RECORDED"
    return "VERIFIED" if stored_hash == current_hash else "INVALID"
