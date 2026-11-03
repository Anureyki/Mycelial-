"""ATI - Astrology Temporal Intelligence: an on-demand scoring layer. See ati/score.py.

Reads fixed natal records and event snapshots from shared memory, scores an
event's timestamp and type against the organ clock and the natal layers, and
returns a recommendation or an explicit refusal. No background process; it
speaks to nobody - Anansi assembles and speaks.
"""
from ati.score import score, score_snapshot, score_event, refusal  # noqa: F401
