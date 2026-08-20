#!/usr/bin/env python3
"""
Gamified data-collection campaigns - domain-agnostic.

The problem this solves is not specific to plants: an agent often can't do its
job because it lacks *labelled* domain data, and collecting that data is tedious
enough that it never happens. This turns the gap into a progression loop -
concrete quests, visible progress toward a real threshold, XP/levels/streaks -
so the volume actually accumulates.

Deliberately knows nothing about images. A campaign is "reach N verified
examples per label", and the caller supplies a counter that reports current
per-label counts. Grow Agent counts photos in training folders; Legal Agent
could count labelled clauses, Accounting categorised transactions, Maintenance
failure signatures. Same engine, different counter.

Two rules the game layer must not break:
  - The threshold is set by what the downstream trainer actually needs, not by
    what makes a satisfying progress bar. Hitting "100%" must mean trainable.
  - Candidate data from an automated source (web search, scraping) is never
    counted as progress until a human verifies it. Unverified candidates are
    tracked separately and are worth XP only once reviewed - otherwise the game
    incentivises bulk-importing noise, which is how a training set quietly
    becomes worse than no training set.
"""
import json
import time
from datetime import datetime

# Progression thresholds. XP is awarded per verified example, so these are
# tuned to feel reachable while collecting a real set (hundreds of items).
LEVEL_THRESHOLDS = [0, 50, 150, 350, 700, 1200, 2000]

XP_PER_VERIFIED_ITEM = 10
XP_PER_REVIEW = 3          # reviewing a candidate (accept or reject both count)
XP_STREAK_BONUS = 25       # awarded per consecutive active day


def level_for_xp(xp):
    level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
    return min(level, len(LEVEL_THRESHOLDS))


def xp_to_next_level(xp):
    for threshold in LEVEL_THRESHOLDS:
        if xp < threshold:
            return threshold - xp
    return 0


class QuestManager:
    """Campaign state lives in the owning agent's own memory namespace, using
    the same store/retrieve convention as everything else in this codebase, so
    it survives restarts without introducing new storage."""

    def __init__(self, agent, campaign_id):
        self.agent = agent
        self.campaign_id = campaign_id
        self.state_key = f"campaign_{campaign_id}"

    # ---------- state ----------
    def _load(self):
        raw = self.agent._unwrap_value(self.agent.retrieve_own_memory(self.state_key))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _save(self, state):
        self.agent.store_own_memory(self.state_key, json.dumps(state))
        return state

    def start_campaign(self, labels, threshold_per_label, description=""):
        existing = self._load()
        if existing:
            return existing
        return self._save({
            "campaign_id": self.campaign_id,
            "description": description,
            "labels": list(labels),
            "threshold_per_label": threshold_per_label,
            "xp": 0,
            "reviews_done": 0,
            "streak_days": 0,
            "last_active_date": None,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
        })

    # ---------- progression ----------
    def _touch_streak(self, state):
        today = datetime.now().date().isoformat()
        last = state.get("last_active_date")
        if last == today:
            return state
        if last:
            try:
                delta = (datetime.fromisoformat(today) - datetime.fromisoformat(last)).days
            except Exception:
                delta = 99
            if delta == 1:
                state["streak_days"] = state.get("streak_days", 0) + 1
                state["xp"] = state.get("xp", 0) + XP_STREAK_BONUS
            elif delta > 1:
                state["streak_days"] = 1
        else:
            state["streak_days"] = 1
        state["last_active_date"] = today
        return state

    def award(self, verified_items=0, reviews=0):
        """Called when real progress happens. verified_items must only count
        human-verified examples - see the module docstring."""
        state = self._load()
        if not state:
            return None
        state["xp"] = state.get("xp", 0) + (verified_items * XP_PER_VERIFIED_ITEM) + (reviews * XP_PER_REVIEW)
        state["reviews_done"] = state.get("reviews_done", 0) + reviews
        state = self._touch_streak(state)
        return self._save(state)

    # ---------- reporting ----------
    def status(self, counts):
        """counts: {label: verified_count} from the caller's counter."""
        state = self._load()
        if not state:
            return {"error": f"No campaign '{self.campaign_id}' started"}

        threshold = state["threshold_per_label"]
        per_label = []
        complete = 0
        for label in state["labels"]:
            have = int(counts.get(label, 0))
            done = have >= threshold
            if done:
                complete += 1
            per_label.append({
                "label": label,
                "have": have,
                "need": max(0, threshold - have),
                "percent": min(100, int(have * 100 / threshold)) if threshold else 0,
                "complete": done,
            })

        total_have = sum(p["have"] for p in per_label)
        total_needed = threshold * len(state["labels"]) if state["labels"] else 0
        xp = state.get("xp", 0)

        return {
            "campaign_id": self.campaign_id,
            "description": state.get("description", ""),
            "level": level_for_xp(xp),
            "xp": xp,
            "xp_to_next_level": xp_to_next_level(xp),
            "streak_days": state.get("streak_days", 0),
            "reviews_done": state.get("reviews_done", 0),
            "threshold_per_label": threshold,
            "labels_complete": complete,
            "labels_total": len(state["labels"]),
            "overall_percent": min(100, int(total_have * 100 / total_needed)) if total_needed else 0,
            "per_label": sorted(per_label, key=lambda p: (p["complete"], -p["need"])),
        }

    def next_quests(self, counts, limit=3):
        """Concrete next actions, hardest-gap-first. Returns quest dicts rather
        than prose so the narration layer can phrase them however it likes."""
        status = self.status(counts)
        if "error" in status:
            return []

        quests = []
        for p in status["per_label"]:
            if p["complete"]:
                continue
            # Ask for a reachable chunk, not the whole remaining gap - a quest
            # you can finish today is the point.
            chunk = 5 if p["need"] > 5 else p["need"]
            quests.append({
                "quest_id": f"{self.campaign_id}:collect:{p['label']}",
                "type": "collect",
                "label": p["label"],
                "ask": chunk,
                "remaining_after": max(0, p["need"] - chunk),
                "xp_reward": chunk * XP_PER_VERIFIED_ITEM,
                "priority": p["need"],
            })

        quests.sort(key=lambda q: -q["priority"])
        return quests[:limit]
