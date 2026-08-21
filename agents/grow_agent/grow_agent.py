#!/usr/bin/env python3
import sys
import os
import time
import json
import math
import re
import requests
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

# Try to import numpy for regression
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# Perception pipeline (YOLO + ViT + OCR, fused) - optional, degrades gracefully
# if the vision deps aren't installed. See services/vision/plant_perception.py.
sys.path.insert(0, os.path.join(project_root, "services", "vision"))
try:
    from plant_perception import fuse_observations, LOW_CONFIDENCE_THRESHOLD
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    LOW_CONFIDENCE_THRESHOLD = 0.55

try:
    from dataset_inventory import scan as scan_training_set, MIN_PER_CLASS, TRAINING_DIR
    DATASET_TOOLS_AVAILABLE = True
except ImportError:
    DATASET_TOOLS_AVAILABLE = False
    MIN_PER_CLASS = 100
    TRAINING_DIR = os.path.expanduser("~/mycelial/knowledge_base/grow_agent/training")

from core.quest_manager import QuestManager

VISION_CAMPAIGN_ID = "cannabis_vision"

# ---------------------------
# VPD calculation (if needed)
# ---------------------------
def svp_from_temp_c(temp_c):
    return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))

def calculate_vpd(temp_c, humidity_percent):
    if temp_c is None or humidity_percent is None:
        return None
    svp = svp_from_temp_c(temp_c)
    avp = svp * (humidity_percent / 100.0)
    return svp - avp

# ---------------------------
# Stage profiles for data-driven reservoir/nutrient decisions.
# Seeded from general hydroponic cultivation practice (same basis as the
# pH/ppm/ec ranges in check_stage) - adjustable defaults, meant to be tuned
# once enough historical data accumulates via get_grow_history.
# ---------------------------
STAGE_PROFILES = {
    "germination": {"nutrient_uptake": "minimal",  "expected_ppm_drift_per_day": 5,  "ph_drift_tolerance": 0.3, "monitoring_frequency": "low"},
    "seedling":    {"nutrient_uptake": "low",      "expected_ppm_drift_per_day": 15, "ph_drift_tolerance": 0.3, "monitoring_frequency": "low"},
    "early_veg":   {"nutrient_uptake": "moderate", "expected_ppm_drift_per_day": 30, "ph_drift_tolerance": 0.4, "monitoring_frequency": "medium"},
    "veg":         {"nutrient_uptake": "high",     "expected_ppm_drift_per_day": 50, "ph_drift_tolerance": 0.4, "monitoring_frequency": "high"},
    "flower":      {"nutrient_uptake": "very high","expected_ppm_drift_per_day": 60, "ph_drift_tolerance": 0.5, "monitoring_frequency": "high"},
}

ROOT_HEALTH_STABLE_KEYWORDS = ("white", "cream", "healthy", "tan")
ROOT_HEALTH_CRITICAL_KEYWORDS = ("brown", "slimy", "mushy", "rot", "black")
ODOR_STABLE_KEYWORDS = ("none", "no odor", "earthy", "neutral")
ODOR_CRITICAL_KEYWORDS = ("sour", "rotten", "ammonia", "sulfur", "sewage")
LEAF_PROBLEM_KEYWORDS = ("rot", "pest", "mold", "mildew", "disease", "infestation")
LEAF_SENESCENT_KEYWORDS = ("yellow", "yellowing")
LEAF_PRODUCTIVE_KEYWORDS = ("green", "vigorous", "healthy")

# ---------------------------
# Growth-stage morphology recognition.
# STAGE_ORDER defines the forward progression used to detect unexpected
# early transitions (e.g. environment-driven acceleration). DECLINE_KEYWORDS
# catch disease/decomposition signals, which are never treated as forward
# progress regardless of what else the description mentions.
#
# STAGE_MORPHOLOGY_CUES is keyed by species/crop and only hardcodes what's
# actually evidenced by this grow (cannabis - net-cup hydro, GH Flora line,
# serrated-leaf photos already reviewed). Any other species/crop (including
# fungi) has no keyword table and falls straight to the LLM fallback in
# _classify_growth_stage, which uses general botanical/mycological knowledge
# instead of a hardcoded list this agent would have to be manually kept
# "up to date" - that's the mechanism for staying current across organisms
# without fabricating cues we have no evidence for.
# ---------------------------
STAGE_ORDER = ["germination", "seedling", "early_veg", "veg", "flower"]

# Training/pruning events change what the plant needs, and the effect depends on
# how much photosynthetic capacity and stored nutrient was removed. Fan leaves
# are the plant's nitrogen reserve, not just its sails - removing the biggest
# ones takes away what it would otherwise draw on while regrowing.
TRAINING_EVENT_TYPES = {
    "topping":       {"severity": "moderate", "removes_capacity": False},
    "lollipopping":  {"severity": "moderate", "removes_capacity": True},
    "defoliation":   {"severity": "heavy",    "removes_capacity": True},
    "lst":           {"severity": "light",    "removes_capacity": False},
    "leaf_removal":  {"severity": "moderate", "removes_capacity": True},
}

# Direction of nutrient emphasis by stage. Deliberately expressed as multipliers
# on whatever recipe is already recorded rather than absolute ml, because the
# right absolute numbers depend on the product line, the source water, and the
# reservoir volume - all of which the agent already knows per-grow and none of
# which generalise. In the GH Flora trio: Gro carries nitrogen for leaf/stem,
# Micro carries nitrogen plus calcium, Bloom is the P-K side.
STAGE_FEED_EMPHASIS = {
    "germination":  {"note": "Plain pH-balanced water or a very dilute feed. Seed reserves cover this stage."},
    "seedling":     {"FloraMicro": 1.0, "FloraGro": 0.8, "FloraBloom": 0.5, "Cal-Mag": 1.0,
                     "note": "Light feed. Roots are small and burn easily."},
    "early_veg":    {"FloraMicro": 1.0, "FloraGro": 1.0, "FloraBloom": 0.7, "Cal-Mag": 1.0,
                     "note": "Balanced, leaning vegetative."},
    "veg":          {"FloraMicro": 1.15, "FloraGro": 1.4, "FloraBloom": 1.0, "Cal-Mag": 1.2,
                     "note": "Nitrogen-forward for leaf and stem. Raise Gro hardest."},
    "flower":       {"FloraMicro": 1.0, "FloraGro": 0.5, "FloraBloom": 2.0, "Cal-Mag": 1.2,
                     "note": "Back nitrogen off, drive P-K. Shift once pistils appear, not on a date."},
}

# Extra nitrogen emphasis while regrowing after capacity was removed.
REGROWTH_N_BOOST = 1.25

# What the agent needs to know, how often, and why. The point is not to poll the
# grower daily - it is to know what is missing when a decision actually depends
# on it, and to ask then. Intervals shorten as biomass grows because consumption
# is non-linear: a weekly check is ample when the plant is small relative to the
# reservoir and leaves it starving for days once the root mass fills it.
MONITORING_SCHEDULE = {
    "germination":  {"interval_days": 7, "params": ["ph", "temp"]},
    "seedling":     {"interval_days": 7, "params": ["ph", "ppm", "temp", "volume_liters"]},
    "early_veg":    {"interval_days": 5, "params": ["ph", "ppm", "temp", "volume_liters"]},
    "veg":          {"interval_days": 3, "params": ["ph", "ppm", "temp", "humidity", "volume_liters"]},
    "flower":       {"interval_days": 3, "params": ["ph", "ppm", "temp", "humidity", "volume_liters"]},
}

# Plain-language prompts, so the narration layer asks a person a question rather
# than naming a field.
PARAM_QUESTIONS = {
    "ph":            "What's the pH reading?",
    "ppm":           "What's the PPM?",
    "ec":            "What's the EC?",
    "temp":          "What's the water temperature?",
    "humidity":      "What's the humidity in the tent?",
    "volume_liters": "Roughly how many litres are in the reservoir right now?",
}

# Lighting properties, asked once per system rather than per reading. Height is
# the exception - it changes as the plant grows, and a canopy that reaches the
# fixture is what caused this grow's leaf cupping.
LIGHTING_QUESTIONS = {
    "light_schedule_hours": "How many hours a day are the lights on?",
    "light_height_cm":      "How far is the light above the canopy right now?",
    "light_wattage":        "What's the light's actual draw in watts?",
}

# A reservoir change is overdue faster than most schedules assume once the plant
# is large - this grow lost two weeks to exactly that.
RESERVOIR_CHANGE_INTERVAL_DAYS = {
    "germination": 14, "seedling": 10, "early_veg": 7, "veg": 7, "flower": 7,
}

# The plant lives in a system, and the system changes what the readings mean.
# Everything above this reasons about a generic reservoir; without a system
# model, "top up to 5L" or "ppm is low" can be actively wrong advice for the
# hardware actually in front of the grower.
GROW_SYSTEM_TYPES = {
    "lwc":          {"label": "low water culture", "aerated": True, "roots_in_water": True},
    "dwc":          {"label": "deep water culture", "aerated": True, "roots_in_water": True},
    "top_fed_dwc":  {"label": "top-fed / recirculating DWC", "aerated": True, "roots_in_water": True,
                     "note": "A top ring wets the medium while roots are still growing down to the "
                             "water line. Once roots reach the reservoir the ring matters less, but "
                             "it is what keeps the plant alive during the gap.",
                     "airlift": True},
    "ebb_flow":     {"label": "ebb and flow", "aerated": False, "roots_in_water": False},
    "coco":         {"label": "coco coir", "aerated": False, "roots_in_water": False},
    "soil":         {"label": "soil", "aerated": False, "roots_in_water": False},
}

GROW_MEDIA = {
    "none":          {"label": "bare root / water only", "holds_moisture": False},
    "clay_pebbles":  {"label": "expanded clay pebbles (LECA)", "holds_moisture": False,
                      "note": "Inert and free-draining - holds almost no water on its own, so the "
                              "medium dries fast if the top feed or water line does not reach it."},
    "rockwool":      {"label": "rockwool", "holds_moisture": True},
    "coco":          {"label": "coco coir", "holds_moisture": True},
    "soil":          {"label": "soil", "holds_moisture": True},
}

WATER_SOURCES = {
    # unbuffered: no carbonate/alkalinity to resist pH movement. Distilled has
    # none at all; RO membranes usually leave a little residual, so distilled
    # swings faster than RO despite both reading ~0 ppm.
    "ro":        {"baseline_ppm": 0, "strips_calmag": True, "unbuffered": True,
                  "residual_ppm": "5-20 typical"},
    "distilled": {"baseline_ppm": 0, "strips_calmag": True, "unbuffered": True,
                  "residual_ppm": "~0"},
    "tap":       {"baseline_ppm": None, "strips_calmag": False, "unbuffered": False},
    "well":      {"baseline_ppm": None, "strips_calmag": False, "unbuffered": False},
}

# Above this, a purchase recommendation is held for explicit user decision
# (via Boss's threshold check) rather than auto-narrated as resolved, even
# if Accounting confirms it's within budget.
PURCHASE_ESCALATION_THRESHOLD = 50.0

DECLINE_KEYWORDS = (
    "rot", "decompos", "damping off", "wilt", "collapse", "mushy stem",
    "mold", "fungal infection", "necrosis spreading", "black stem", "dying"
)

STAGE_MORPHOLOGY_CUES = {
    "cannabis": {
        "germination": ("cotyledon", "seed coat", "taproot only", "no true leaves"),
        # "blade" is the common grower term alongside point/prong/finger - leaving
        # it out sent a plain "9-blade leaves" description to the LLM fallback,
        # which misread it as flower. Deterministic cues should cover the
        # vocabulary people actually use.
        "seedling": ("single blade", "one-point leaf", "1-point leaf", "first true leaf",
                     "3-point leaf", "3-prong", "three point leaf", "three-prong",
                     "3-blade", "3 blade", "three-blade", "three blade"),
        "early_veg": ("5-point leaf", "5-prong", "five point leaf", "five-prong",
                      "five-finger leaf", "5-finger leaf",
                      "5-blade", "5 blade", "five-blade", "five blade"),
        "veg": ("7-point leaf", "7-prong", "seven point leaf", "9-point leaf",
                "multiple nodes", "bushy growth", "vigorous vegetative growth",
                "7-blade", "7 blade", "seven-blade", "seven blade",
                "9-blade", "9 blade", "nine-blade", "nine blade"),
        "flower": ("pistil", "white hair", "calyx", "calyxes", "flowering site",
                   "bud site", "stretch", "trichome"),
    }
}

class GrowAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="grow_agent",
            port=9009,
            capabilities=[
                "log_reading", "check_stage", "adjust_nutrients",
                "transition_stage", "log_water_change", "get_status",
                "set_germination_date", "set_current_nutrients",
                "add_reminder", "list_reminders", "complete_reminder",
                "add_note", "list_notes",
                "evaluate_reservoir", "evaluate_leaf", "get_grow_history", "evaluate_growth_stage",
                "verify_growth_stage",
                "assess_plant", "validate_environment_targets",
                "log_training_event", "recommend_feed", "plan_system_transition",
                "set_grow_system", "get_grow_system", "get_nutrient_history",
                "check_in", "analyze_consumption", "adjust_to_target_ppm",
                "training_quest_status", "source_training_candidates",
                "review_training_candidate", "list_training_candidates",
                "remove_plant", "list_vision_corrections", "recommend_purchase",
                "web_search",
                "prepare_dataset", "fit_linear_model", "predict_linear"
            ],
            role="gardener"
        )
        self.log("🌱 Grow Agent started with VPD + linear regression.")

    # ---------- Helper methods (unchanged) ----------
    def _unwrap_value(self, retrieval_result):
        if not isinstance(retrieval_result, dict):
            return None
        result = retrieval_result.get("result")
        if not isinstance(result, dict):
            return None
        entry = result.get("entry")
        if not isinstance(entry, dict):
            return None
        return entry.get("value")

    def _load_reminder_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("reminder_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_reminders(self):
        reminders = []
        for reminder_id in self._load_reminder_index():
            raw = self._unwrap_value(self.retrieve_own_memory(reminder_id))
            if not raw:
                continue
            try:
                reminders.append(json.loads(raw))
            except Exception:
                pass
        return reminders

    def _load_plant_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("plant_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_plants(self):
        plants = []
        for plant_id in self._load_plant_index():
            raw = self._unwrap_value(self.retrieve_own_memory(f"plant_{plant_id}"))
            if not raw:
                continue
            try:
                plants.append(json.loads(raw))
            except Exception:
                pass
        return plants

    def _load_note_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("note_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    @staticmethod
    def _uid():
        """Microsecond-precision id. Second-granularity keys silently overwrote
        records logged in the same second - two readings taken minutes apart but
        LOGGED back-to-back collided and one was lost before this was fixed."""
        return int(time.time() * 1_000_000)

    def _load_nutrient_history_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("nutrient_change_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_nutrient_history(self, plant_id=None):
        out = []
        for k in self._load_nutrient_history_index():
            raw = self._unwrap_value(self.retrieve_own_memory(k))
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if plant_id and rec.get("plant_id") and rec["plant_id"] != plant_id:
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r.get("timestamp") or "")

    def _detect_lagging_nutrients(self, threshold_ratio=0.5):
        """Find components that have not scaled with the rest of the recipe.

        Stage multipliers are applied to whatever is currently recorded, which
        assumes the current recipe was right for the previous stage. When one
        component has been left untouched while the others moved, multiplying it
        carries the lag forward instead of correcting it - which is how Cal-Mag
        sat at the same dose from week 1 to week 4 while everything else rose and
        the plant doubled. Returns {nutrient: {...}} for components whose growth
        is under threshold_ratio of the median growth across the recipe."""
        history = self._get_nutrient_history()
        if len(history) < 2:
            return {}
        first, current = history[0].get("nutrients", {}), history[-1].get("nutrients", {})
        growth = {}
        for name, start in first.items():
            s, c = self._parse_numeric(start), self._parse_numeric(current.get(name))
            if s is None or c is None or s <= 0:
                continue
            growth[name] = (c / s) - 1.0
        if len(growth) < 2:
            return {}
        ordered = sorted(growth.values())
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        if median <= 0:
            return {}

        lagging = {}
        for name, g in growth.items():
            if g < median * threshold_ratio:
                lagging[name] = {
                    "growth_pct": round(g * 100, 1),
                    "median_growth_pct": round(median * 100, 1),
                    # Catch-up brings it to the median the rest of the recipe moved by.
                    "catchup_multiplier": round((1 + median) / (1 + g), 3),
                    "since": history[0].get("timestamp"),
                }
        return lagging

    def _load_training_event_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("training_event_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_training_events(self):
        events = []
        for eid in self._load_training_event_index():
            raw = self._unwrap_value(self.retrieve_own_memory(eid))
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except Exception:
                pass
        return sorted(events, key=lambda e: e.get("timestamp") or "")

    def _get_all_notes(self):
        notes = []
        for note_id in self._load_note_index():
            raw = self._unwrap_value(self.retrieve_own_memory(note_id))
            if not raw:
                continue
            try:
                notes.append(json.loads(raw))
            except Exception:
                pass
        return notes

    def _load_reservoir_eval_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("reservoir_eval_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_reservoir_evals(self):
        evals = []
        for eval_id in self._load_reservoir_eval_index():
            raw = self._unwrap_value(self.retrieve_own_memory(eval_id))
            if not raw:
                continue
            try:
                evals.append(json.loads(raw))
            except Exception:
                pass
        return evals

    def _load_leaf_eval_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("leaf_eval_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_leaf_evals(self):
        evals = []
        for eval_id in self._load_leaf_eval_index():
            raw = self._unwrap_value(self.retrieve_own_memory(eval_id))
            if not raw:
                continue
            try:
                evals.append(json.loads(raw))
            except Exception:
                pass
        return evals

    def _load_stage_eval_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("stage_eval_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_stage_evals(self):
        evals = []
        for eval_id in self._load_stage_eval_index():
            raw = self._unwrap_value(self.retrieve_own_memory(eval_id))
            if not raw:
                continue
            try:
                evals.append(json.loads(raw))
            except Exception:
                pass
        return evals

    def _parse_numeric(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _make_recommendation(self, observation, reason, action, confidence):
        return {"observation": observation, "reason": reason, "action": action, "confidence": confidence}

    def _call_inference(self, prompt, timeout=30):
        try:
            resp = requests.post("http://localhost:8005/reason", json={"prompt": prompt}, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("result", "")
        except Exception as e:
            self.log(f"Inference fallback failed ({e}); defaulting to 'warning' classification.")
        return None

    @staticmethod
    def _join_wrapped(existing, addition):
        """Join a wrapped continuation line, dropping the fragment small models
        duplicate at the wrap point ("shows s" + "signs..." -> "shows signs...",
        "inadequate nutrient" + "nutrient supply" -> "inadequate nutrient supply").
        Only collapses an exact repeat or a very short prefix, and never a
        single-letter word that stands on its own - otherwise "eat a" + "apple
        pie" would lose the article."""
        if not existing:
            return addition
        last = existing.split()[-1] if existing.split() else ""
        first = addition.split()[0] if addition.split() else ""
        if last and first and (
            last.lower() == first.lower()
            or (len(last) <= 3
                and last.lower() not in ("a", "i")
                and first.lower().startswith(last.lower()))
        ):
            existing = " ".join(existing.split()[:-1])
        return f"{existing} {addition}".strip()

    def _call_inference_capability(self, prompt, capability="reasoning", timeout=120):
        """Text inference routed by capability rather than model name, so the
        brain behind e.g. 'synthesis' is a config choice (see
        config/model_routing.json) instead of something baked in here."""
        try:
            resp = requests.post(
                "http://localhost:8005/reason",
                json={"prompt": prompt, "capability": capability},
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("result", "")
                self.log(f"Capability '{capability}' call failed: {data.get('message', data.get('error'))}")
        except Exception as e:
            self.log(f"Capability '{capability}' call failed: {e}")
        return None

    def _call_inference_vision(self, prompt, image_path, timeout=180):
        """Verification tier for cases the local disease models can't judge -
        either low fusion confidence, or a species they have no class for.

        Asks for the 'vision' CAPABILITY rather than naming a model: the
        Inference Service resolves that against config/model_routing.json, which
        prefers a local Ollama vision model and only falls back to a cloud one
        if a key happens to be set. No vendor is named here on purpose - swapping
        the brain behind vision is a config edit, not an agent change."""
        try:
            resp = requests.post(
                "http://localhost:8005/reason",
                json={"prompt": prompt, "capability": "vision", "image_path": image_path},
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("result", "")
                self.log(f"Vision verification call failed: {data.get('message', data.get('error'))}")
        except Exception as e:
            self.log(f"Vision verification call failed: {e}")
        return None

    def _describe_fused_observation(self, fused):
        """Turns a fuse_observations() dict into the kind of plain-text symptom
        description evaluate_leaf/evaluate_growth_stage's existing keyword/LLM
        classification logic already expects - reuses that logic unchanged
        rather than building a parallel image-aware classifier."""
        parts = []
        if fused.get("species_supported") is False:
            # The local models have no class for this species - reporting their
            # nearest-class guess would be actively misleading (a cannabis leaf
            # comes back as a tomato virus), so say what's actually known instead.
            if fused.get("text"):
                parts.append("visible text: " + "; ".join(fused["text"][:3]))
            parts.append(fused.get("health_error") or "No local classification available for this species.")
            return "; ".join(parts)
        health = fused.get("health")
        if health and health.get("label"):
            parts.append(f"{health['label']} (model confidence {health['confidence']:.2f})")
        for d in fused.get("detections", [])[:5]:
            parts.append(f"{d['label']} detected (confidence {d['confidence']:.2f})")
        if fused.get("text"):
            parts.append("visible text: " + "; ".join(fused["text"][:3]))
        return "; ".join(parts) if parts else "No clear signal from the perception pipeline."

    def _load_vision_correction_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("vision_correction_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_all_vision_corrections(self):
        corrections = []
        for cid in self._load_vision_correction_index():
            raw = self._unwrap_value(self.retrieve_own_memory(cid))
            if not raw:
                continue
            try:
                corrections.append(json.loads(raw))
            except Exception:
                pass
        return corrections

    def _log_vision_correction(self, image_path, fused, verification_result):
        """Logs a low-confidence case for future retraining. Only useful when the
        verification tier actually returned something - a record with no
        verification_result is a wrong prediction with no ground truth to correct
        it against, which is worse than no data at all for a retraining set, so
        it's marked unusable rather than silently sitting in the index looking
        like a labelled example."""
        record = {
            "id": f"vision_correction_{self._uid()}",
            "timestamp": datetime.now().isoformat(),
            "image_path": image_path,
            "fused_observation": fused,
            "verification_result": verification_result,
            "verified": False,
            "usable_for_training": bool(verification_result),
        }
        self.store_own_memory(record["id"], json.dumps(record))
        index_raw = self._unwrap_value(self.retrieve_own_memory("vision_correction_index"))
        try:
            index = json.loads(index_raw) if index_raw else []
        except Exception:
            index = []
        index.append(record["id"])
        self.store_own_memory("vision_correction_index", json.dumps(index))
        return record

    def _training_counts(self):
        """Counter adapter for the data_collection_quest skill: reports how many
        images sit in each label folder. Only real files on disk count - this is
        what makes campaign progress mean 'trainable' rather than 'clicked a lot'."""
        if not DATASET_TOOLS_AVAILABLE:
            return {}, []
        scanned = scan_training_set()
        if not scanned:
            return {}, []
        classes, _duplicates, _unreadable = scanned
        counts = {label: len(files) for label, files in classes.items()}
        return counts, sorted(classes.keys())

    def _get_candidate_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("training_candidate_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_pending_candidates(self):
        pending = []
        for cid in self._get_candidate_index():
            raw = self._unwrap_value(self.retrieve_own_memory(cid))
            if not raw:
                continue
            try:
                c = json.loads(raw)
            except Exception:
                continue
            if c.get("status") == "awaiting_review":
                pending.append(c)
        return pending

    def _extract_search_items(self, search_result):
        """Pull {title, url} out of the tool-service search envelope, which nests
        results a few layers deep depending on which path served the query."""
        items = []

        def walk(node, depth=0):
            if depth > 6 or len(items) > 40:
                return
            if isinstance(node, dict):
                url = node.get("url") or node.get("link")
                if url and isinstance(url, str) and url.startswith("http"):
                    items.append({
                        "url": url,
                        "img_src": node.get("img_src") or "",
                        "title": node.get("title") or node.get("content", "")[:120],
                    })
                for v in node.values():
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node:
                    walk(v, depth + 1)
            elif isinstance(node, str) and node.strip()[:1] in ("{", "["):
                # Tool-service responses nest a JSON payload as a *string* inside
                # content[0].text - and search_structured's payload is an array,
                # so this must accept "[" as well as "{".
                try:
                    walk(json.loads(node), depth + 1)
                except Exception:
                    pass

        walk(search_result)
        return items

    NEGATION_CUES = ("no ", "not ", "without ", "never ", "free of ", "absence of ", "n't ")

    def _negation_aware_hit(self, text, keywords):
        """True if any keyword appears in a non-negated clause of text. Splits on
        clause boundaries so a negation word governs its whole clause - e.g. "no
        brown slime or rot" must not match "brown"/"rot" just because they appear
        as bare substrings; the clause as a whole is a negative/absence statement."""
        if not text:
            return False
        lowered = str(text).lower()
        for clause in re.split(r'[.;,]|\bbut\b|\bhowever\b', lowered):
            if any(neg in clause for neg in self.NEGATION_CUES):
                continue
            if any(k in clause for k in keywords):
                return True
        return False

    def _classify_by_keywords(self, text, stable_keywords, critical_keywords):
        """Returns 'stable', 'critical', or None (inconclusive - caller should escalate)."""
        if not text:
            return None
        if self._negation_aware_hit(text, critical_keywords):
            return "critical"
        if self._negation_aware_hit(text, stable_keywords):
            return "stable"
        return None

    def _classify_qualitative(self, text, stable_keywords, critical_keywords, field_label):
        """Keyword match first; falls back to LLM classification via _call_inference
        when the text doesn't match a known keyword. Returns (verdict, method)."""
        if not text:
            return "stable", "default"
        verdict = self._classify_by_keywords(text, stable_keywords, critical_keywords)
        if verdict is not None:
            return verdict, "keyword"
        prompt = (
            f"Classify this {field_label} observation as exactly one word - "
            f"stable, warning, or critical: \"{text}\""
        )
        result = self._call_inference(prompt)
        if result:
            lowered = result.strip().lower()
            for verdict in ("stable", "warning", "critical"):
                if verdict in lowered:
                    return verdict, "llm"
        return "warning", "llm_unavailable"

    def _get_species_for_plant(self, plant_id):
        if plant_id == "current_plant":
            return self._unwrap_value(self.retrieve_own_memory("current_species")) or "cannabis"
        plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
        return (plant or {}).get("species") or "cannabis"

    def _classify_stage_by_keywords(self, text, species):
        if not text:
            return None
        cues = STAGE_MORPHOLOGY_CUES.get(species, {})
        # Negation-aware, like the leaf/reservoir/decline classifiers. Raw
        # substring matching read "no pistils, no pre-flowers, no calyx
        # development observed" as flower evidence and auto-transitioned a
        # vegetative plant into flower - the words were present, the negation
        # was not considered. An absence statement is the OPPOSITE of a cue.
        matched = [stage for stage, keywords in cues.items()
                   if self._negation_aware_hit(text, keywords)]
        if not matched:
            return None
        # Multiple cues can match a mixed description (e.g. veg leaves + early
        # pistils) - the most advanced matching stage wins.
        return max(matched, key=lambda s: STAGE_ORDER.index(s))

    def _classify_growth_stage(self, text, species):
        """Returns (stage_or_None, method). method is 'keyword', 'llm', or 'llm_unavailable'."""
        if not text:
            return None, "none"
        stage = self._classify_stage_by_keywords(text, species)
        if stage is not None:
            return stage, "keyword"
        prompt = (
            f"A {species} plant shows this morphology: \"{text}\". "
            f"Which growth stage is it most likely in? Answer with exactly one word "
            f"from this list: {', '.join(STAGE_ORDER)}."
        )
        result = self._call_inference(prompt)
        if result:
            lowered = result.strip().lower()
            for candidate in STAGE_ORDER:
                if candidate.replace("_", " ") in lowered or candidate in lowered:
                    return candidate, "llm"
        return None, "llm_unavailable"

    def _verdict_score(self, verdict):
        return {"stable": 2, "warning": 1, "critical": 0}.get(verdict, 1)

    def _get_readings_for_plant(self, plant_id):
        index = self._unwrap_value(self.retrieve_own_memory("reading_index"))
        if not index:
            return []
        try:
            keys = json.loads(index)
        except Exception:
            return []
        readings = []
        for key in keys:
            raw = self._unwrap_value(self.retrieve_own_memory(key))
            if not raw:
                continue
            try:
                reading = json.loads(raw)
            except Exception:
                continue
            if reading.get("plant_id", "current_plant") == plant_id:
                readings.append(reading)
        readings.sort(key=lambda r: r.get("timestamp", ""))
        return readings

    def _collect_readings(self):
        """
        Attempt to collect all reading keys from the agent's memory.
        Since we can't list keys directly, we'll iterate over a range of timestamps
        (or we could store an index). For now, we'll try to get readings from
        a known pattern: we'll search for keys starting with "reading_".
        However, we don't have a search API, so we'll use a fallback: the user
        can manually trigger data preparation and we'll rely on the agent's
        own stored memories. But we can implement a simple scan by generating
        possible keys from the last 30 days? That's not reliable.
        Instead, we'll store a reading index when we log a reading.
        We'll add an index in memory called "reading_index".
        """
        # If we don't have an index, we'll build one from known keys?
        # For now, we'll ask the user to ensure they have logged readings.
        # We'll try to get all keys by using a pattern? Not possible without a list.
        # We'll implement a fallback: read all entries from memory? Not feasible.
        # We'll add a new task to build an index from existing readings.
        # But for now, we'll return an empty list and instruct the user.
        return []

    # ---------- Existing tasks (unchanged) ----------
    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "log_reading":
            reading = {
                "timestamp": datetime.now().isoformat(),
                "plant_id": args.get("plant_id", "current_plant"),
                "ph": args.get("ph"),
                "ppm": args.get("ppm"),
                "ec": args.get("ec"),
                "temp": args.get("temp"),
                "humidity": args.get("humidity"),
                # Volume is what makes ppm interpretable. 400ppm in 3L and 400ppm
                # in 5L are different amounts of nutrient, and without it there is
                # no way to tell whether a falling ppm means the plant is feeding
                # or the solution is being diluted - see analyze_consumption.
                "volume_liters": self._parse_numeric(args.get("volume_liters")),
                "stage": args.get("stage", "seedling"),
                "notes": args.get("notes", "")
            }
            # Also compute VPD if temp and humidity are present
            temp = self._parse_numeric(args.get("temp"))
            humidity = self._parse_numeric(args.get("humidity"))
            if temp is not None and humidity is not None:
                reading["vpd"] = calculate_vpd(temp, humidity)
            self.store_own_memory(f"reading_{self._uid()}", json.dumps(reading))
            # Also add to reading index (for data preparation)
            # We'll store a separate index list.
            index = self._unwrap_value(self.retrieve_own_memory("reading_index"))
            if not index:
                index = []
            else:
                try:
                    index = json.loads(index)
                except:
                    index = []
            key = f"reading_{self._uid()}"
            if key not in index:
                index.append(key)
            self.store_own_memory("reading_index", json.dumps(index))
            return {"result": "Reading logged", "reading": reading}

        elif task == "check_stage":
            stage = args.get("stage", "seedling")
            ranges = {
                "seedling": {"ph": (5.8, 6.0), "ppm": (200, 400), "ec": (0.4, 0.8)},
                "early_veg": {"ph": (5.8, 6.2), "ppm": (400, 600), "ec": (0.8, 1.2)},
                "veg": {"ph": (5.8, 6.2), "ppm": (600, 900), "ec": (1.2, 1.8)},
                "flower": {"ph": (5.8, 6.2), "ppm": (800, 1200), "ec": (1.6, 2.4)}
            }
            return {"result": ranges.get(stage, ranges["seedling"])}

        elif task == "adjust_nutrients":
            current = args.get("reading", {})
            stage = args.get("stage", "seedling")
            target_response = self.handle_task("check_stage", {"stage": stage}, sender)
            if "error" in target_response:
                return target_response
            target = target_response.get("result", {})
            if not target:
                return {"error": "No target ranges found"}
            ph = current.get("ph")
            ppm = current.get("ppm")
            ph_target = target.get("ph")
            ppm_target = target.get("ppm")
            if not ph_target or not ppm_target:
                return {"error": "Target ranges missing"}
            advice = []
            if ph is not None:
                if ph < ph_target[0] or ph > ph_target[1]:
                    advice.append(f"pH is {ph} – adjust to {ph_target[0]}-{ph_target[1]}")
            if ppm is not None:
                if ppm < ppm_target[0] or ppm > ppm_target[1]:
                    advice.append(f"ppm is {ppm} – target is {ppm_target[0]}-{ppm_target[1]}")
            if not advice:
                advice.append("All parameters are within target range.")
            return {"result": advice}

        elif task == "transition_stage":
            new_stage = args.get("new_stage")
            notes = args.get("notes", "")
            plant_id = args.get("plant_id", "current_plant")
            if not new_stage:
                return {"error": "Missing new_stage"}

            if plant_id != "current_plant":
                plants = self._get_all_plants()
                plant = next((p for p in plants if p.get("plant_id") == plant_id), None)
                if not plant:
                    return {"error": f"Unknown plant_id: {plant_id}"}
                transition = {
                    "timestamp": datetime.now().isoformat(),
                    "plant_id": plant_id,
                    "new_stage": new_stage,
                    "notes": notes,
                    "previous_stage": plant.get("stage", "unknown")
                }
                plant["stage"] = new_stage
                plant["logged_at"] = datetime.now().isoformat()
                self.store_own_memory(f"plant_{plant_id}", json.dumps(plant))
                self.store_own_memory(f"stage_transition_{plant_id}_{self._uid()}", json.dumps(transition))
                return {"result": f"Stage transitioned to {new_stage} for {plant_id}", "transition": transition}

            transition = {
                "timestamp": datetime.now().isoformat(),
                "plant_id": "current_plant",
                "new_stage": new_stage,
                "notes": notes,
                "previous_stage": self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            }
            self.store_own_memory("current_stage", new_stage)
            self.store_own_memory(f"stage_transition_{self._uid()}", json.dumps(transition))
            return {"result": f"Stage transitioned to {new_stage}", "transition": transition}

        elif task == "log_water_change":
            volume = args.get("volume")
            ph = args.get("ph")
            ppm = args.get("ppm")
            notes = args.get("notes", "")
            if not volume:
                return {"error": "Missing volume"}
            change = {
                "timestamp": datetime.now().isoformat(),
                "volume_liters": volume,
                "ph": ph,
                "ppm": ppm,
                "notes": notes
            }
            self.store_own_memory(f"water_change_{self._uid()}", json.dumps(change))
            return {"result": "Water change logged", "change": change}

        elif task == "get_status":
            stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            germination_date = self._unwrap_value(self.retrieve_own_memory("germination_date"))
            strain = self._unwrap_value(self.retrieve_own_memory("current_strain"))
            nutrients_raw = self._unwrap_value(self.retrieve_own_memory("current_nutrients"))
            current_nutrients = None
            if nutrients_raw:
                try:
                    current_nutrients = json.loads(nutrients_raw)
                except Exception:
                    current_nutrients = None
            pending_reminders = [
                r for r in self._get_all_reminders() if r.get("status") == "pending"
            ]
            return {
                "result": {
                    "current_stage": stage,
                    "germination_date": germination_date,
                    "current_strain": strain,
                    "current_nutrients": current_nutrients,
                    "other_plants": self._get_all_plants(),
                    "notes": self._get_all_notes(),
                    "pending_reminders": pending_reminders,
                    "last_reading": "Use log_reading to add a new reading",
                    "last_water_change": "Use log_water_change to log a water change"
                }
            }

        elif task == "set_germination_date":
            date_str = args.get("date")
            strain = args.get("strain", "")
            plant_id = args.get("plant_id")
            if not date_str:
                return {"error": "Missing date"}
            species = args.get("species", "cannabis")
            if plant_id:
                # Multi-plant tracking: store as its own record, keyed by plant_id,
                # so it doesn't clobber the legacy single-plant fields below.
                record = {
                    "plant_id": plant_id,
                    "germination_date": date_str,
                    "strain": strain,
                    "species": species,
                    "stage": args.get("stage", "seedling"),
                    "logged_at": datetime.now().isoformat()
                }
                self.store_own_memory(f"plant_{plant_id}", json.dumps(record))
                index = self._load_plant_index()
                if plant_id not in index:
                    index.append(plant_id)
                self.store_own_memory("plant_index", json.dumps(index))
                return {"result": f"Germination date set for {plant_id}", "plant": record}
            self.store_own_memory("germination_date", date_str)
            self.store_own_memory("current_species", species)
            if strain:
                self.store_own_memory("current_strain", strain)
            return {"result": f"Germination date set to {date_str}", "strain": strain}

        elif task == "set_current_nutrients":
            # A bare number is not a dose. "FloraMicro: 3.0" is ambiguous by a
            # factor of 3.79 between ml/L and ml/gal, and ambiguous again between
            # a per-volume rate and a total for the reservoir - which is the
            # difference between a correct mix and a badly wrong one. The record
            # is self-describing so that ambiguity can't be reintroduced.
            stage = args.get("stage", "unknown")
            unit = args.get("unit", "ml")
            basis = (args.get("basis") or "total").lower()
            if basis not in ("total", "per_liter", "per_gallon"):
                return {"error": "basis must be one of: total, per_liter, per_gallon"}
            # The volume the dose was actually mixed into - NOT the container's
            # rated capacity. A "5 gallon" bucket runs at 3-4.5 gal and a 5L unit
            # ran at 3.5-4L in early stages, so dosing against nameplate capacity
            # over-concentrates by however much the reservoir is underfilled.
            reservoir_liters = self._parse_numeric(
                args.get("volume_liters") or args.get("reservoir_liters")
            )
            if basis == "total" and reservoir_liters is None:
                return {"error": ("basis 'total' needs volume_liters - the ACTUAL volume mixed "
                                  "into, not the container's capacity. A total dose is meaningless "
                                  "without it, and capacity overstates it whenever the reservoir "
                                  "is not full.")}

            reserved = {"stage", "unit", "basis", "reservoir_liters", "volume_liters", "typical_working_liters"}
            nutrients = {k: v for k, v in args.items() if k not in reserved}
            if not nutrients:
                return {"error": "No nutrient values provided"}

            # Normalise to a per-litre concentration so recipes stay comparable
            # across reservoir sizes and unit conventions.
            L_PER_GAL = 3.785411784
            per_liter = {}
            for name, value in nutrients.items():
                v = self._parse_numeric(value)
                if v is None:
                    continue
                if basis == "total":
                    per_liter[name] = round(v / reservoir_liters, 4)
                elif basis == "per_gallon":
                    per_liter[name] = round(v / L_PER_GAL, 4)
                else:
                    per_liter[name] = round(v, 4)

            # A backfilled entry carries its real date and must NOT clobber the
            # current recipe - reconstructing history should never rewrite the
            # present.
            backfill_ts = args.get("timestamp")
            record = {
                "timestamp": backfill_ts or datetime.now().isoformat(),
                "stage": stage,
                "nutrients": nutrients,
                "unit": unit,
                "basis": basis,
                "reservoir_liters": reservoir_liters,
                "per_liter": per_liter,
                "backfilled": bool(backfill_ts),
                "source_note": args.get("source_note", ""),
            }
            if backfill_ts:
                hist_key = f"nutrient_change_{backfill_ts}"
                self.store_own_memory(hist_key, json.dumps(record))
                hist = self._load_nutrient_history_index()
                if hist_key not in hist:
                    hist.append(hist_key)
                self.store_own_memory("nutrient_change_index", json.dumps(sorted(hist)))
                return {"result": "Historical nutrient entry recorded", "nutrients": record}
            self.store_own_memory("current_nutrients", json.dumps(record))
            # Also append to a history index. "current_nutrients" is a single
            # overwritten slot, so every previous recipe was silently destroyed
            # by the next change - which loses exactly the thing that matters for
            # a grow: how feed strength moved over time relative to the plant's
            # size and the measured ppm. Keep each change as its own entry.
            hist_key = f"nutrient_change_{self._uid()}"
            self.store_own_memory(hist_key, json.dumps(record))
            hist = self._load_nutrient_history_index()
            hist.append(hist_key)
            self.store_own_memory("nutrient_change_index", json.dumps(hist))

            auto_transition = None
            current_stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            if stage != "unknown" and stage != current_stage:
                transition_result = self.handle_task("transition_stage", {
                    "new_stage": stage,
                    "notes": f"Auto-transitioned: nutrient recipe changed to the {stage} formula."
                }, sender)
                auto_transition = transition_result.get("transition")

            return {"result": "Current nutrients set", "nutrients": record, "auto_transition": auto_transition}

        elif task == "add_reminder":
            title = args.get("title")
            target_date = args.get("target_date")
            notes = args.get("notes", "")
            if not title or not target_date:
                return {"error": "Missing title or target_date"}
            reminder_id = f"reminder_{self._uid()}"
            reminder = {
                "id": reminder_id,
                "title": title,
                "target_date": target_date,
                "notes": notes,
                "created": datetime.now().isoformat(),
                "status": "pending"
            }
            self.store_own_memory(reminder_id, json.dumps(reminder))
            index = self._load_reminder_index()
            index.append(reminder_id)
            self.store_own_memory("reminder_index", json.dumps(index))
            return {"result": "Reminder added", "reminder": reminder}

        elif task == "list_reminders":
            return {"result": self._get_all_reminders()}

        elif task == "complete_reminder":
            reminder_id = args.get("id") or args.get("reminder_id")
            if not reminder_id:
                return {"error": "Missing reminder id"}
            raw = self._unwrap_value(self.retrieve_own_memory(reminder_id))
            if not raw:
                return {"error": f"Reminder {reminder_id} not found"}
            try:
                reminder = json.loads(raw)
            except Exception:
                return {"error": f"Reminder {reminder_id} is corrupted"}
            reminder["status"] = "completed"
            reminder["completed_at"] = datetime.now().isoformat()
            self.store_own_memory(reminder_id, json.dumps(reminder))
            return {"result": "Reminder marked completed", "reminder": reminder}

        elif task == "add_note":
            text = args.get("text") or args.get("notes")
            if not text:
                return {"error": "Missing note text"}
            note_id = f"note_{self._uid()}"
            note = {
                "id": note_id,
                "timestamp": datetime.now().isoformat(),
                "text": text,
                "plant_id": args.get("plant_id"),
                "category": args.get("category", "general"),
                "source": args.get("source", ""),
                "photo_refs": args.get("photo_refs", [])
            }
            self.store_own_memory(note_id, json.dumps(note))
            index = self._load_note_index()
            index.append(note_id)
            self.store_own_memory("note_index", json.dumps(index))
            return {"result": "Note added", "note": note}

        elif task == "list_notes":
            return {"result": self._get_all_notes()}

        elif task == "remove_plant":
            plant_id = args.get("plant_id")
            if not plant_id or plant_id == "current_plant":
                return {"error": "Provide a non-current_plant plant_id to remove"}
            index = self._load_plant_index()
            if plant_id not in index:
                return {"error": f"Unknown plant_id: {plant_id}"}
            index.remove(plant_id)
            self.store_own_memory("plant_index", json.dumps(index))
            self.forget_own_memory(f"plant_{plant_id}")
            return {"result": f"Removed {plant_id} from tracking"}

        elif task == "list_vision_corrections":
            return {"result": self._get_all_vision_corrections()}

        elif task == "recommend_purchase":
            # Direct A2A consultation with Accounting Agent - no Boss mediation
            # needed for the consult itself, only for the threshold decision
            # below. Sends a minimal structured request and gets back a minimal
            # structured constraint, never the underlying ledger.
            if not isinstance(args, dict) or not args.get("item") or args.get("estimated_cost") is None:
                return {"error": "Usage: {item, estimated_cost, [reason]}"}
            item = args["item"]
            estimated_cost = args["estimated_cost"]
            reason = args.get("reason", "")

            budget_response = self.send_a2a("accounting_agent", "check_budget_constraint", {
                "estimated_cost": estimated_cost,
                "purpose": f"grow: {item}"
            })
            constraint = budget_response.get("result", {}) if isinstance(budget_response, dict) else {}
            if not isinstance(constraint, dict) or "error" in constraint:
                constraint = {"within_budget": None, "available_discretionary": None, "note": "Budget check unavailable."}

            requires_escalation = (
                estimated_cost > PURCHASE_ESCALATION_THRESHOLD or constraint.get("within_budget") is False
            )

            recommendation = self._make_recommendation(
                observation=f"Recommending {item} ({reason})." if reason else f"Recommending {item}.",
                reason=constraint.get("note", "No budget data available."),
                action=f"Purchase {item} (~${estimated_cost:.2f})." if not requires_escalation else f"Hold for your decision - {item} (~${estimated_cost:.2f}) needs explicit sign-off.",
                confidence="high" if constraint.get("within_budget") is not None else "low"
            )
            recommendation["item"] = item
            recommendation["estimated_cost"] = estimated_cost
            recommendation["budget_constraint"] = constraint
            recommendation["requires_escalation"] = requires_escalation
            return {"result": recommendation}

        elif task == "evaluate_reservoir":
            plant_id = args.get("plant_id", "current_plant")
            stage = args.get("stage") or "seedling"
            profile = STAGE_PROFILES.get(stage, STAGE_PROFILES["seedling"])
            stage_ranges = self.handle_task("check_stage", {"stage": stage}, sender).get("result", {})

            ph = self._parse_numeric(args.get("ph"))
            ppm = self._parse_numeric(args.get("ppm"))
            reservoir_temp = self._parse_numeric(args.get("reservoir_temp"))
            water_level_change = args.get("water_level_change")
            root_health_text = args.get("root_health")
            odor_text = args.get("odor")
            biofilm = args.get("biofilm")

            prior_readings = self._get_readings_for_plant(plant_id)
            last_reading = prior_readings[-1] if prior_readings else None
            # A reservoir change resets the baseline. Without knowing one
            # happened, a deliberate fresh mix reads as drift - "ppm rose,
            # possible evaporation or salt buildup" - when the grower simply
            # replaced the solution. Comparing across a change is meaningless.
            reservoir_reset = bool(args.get("after_reservoir_change"))
            if reservoir_reset:
                last_reading = None

            findings = []
            scores = {}
            methods = {}

            ph_target = stage_ranges.get("ph")
            if ph is None:
                scores["ph"] = 1
                findings.append("No pH reading provided.")
            else:
                drift = None
                if last_reading and last_reading.get("ph") is not None:
                    drift = abs(ph - float(last_reading["ph"]))
                in_range = bool(ph_target) and ph_target[0] <= ph <= ph_target[1]
                if drift is not None and drift > profile["ph_drift_tolerance"] * 2:
                    scores["ph"] = 0
                    findings.append(f"pH drifted {drift:.2f} since last reading (tolerance {profile['ph_drift_tolerance']}).")
                elif drift is not None and drift > profile["ph_drift_tolerance"]:
                    scores["ph"] = 1
                    findings.append(f"pH drift {drift:.2f} is above the {stage} tolerance of {profile['ph_drift_tolerance']}.")
                elif not in_range:
                    scores["ph"] = 1
                    findings.append(f"pH {ph} is outside the {stage} target range {ph_target}.")
                else:
                    scores["ph"] = 2

            ppm_target = stage_ranges.get("ppm")
            if ppm is None:
                scores["ppm"] = 1
                findings.append("No PPM reading provided.")
            else:
                consumption = None
                if last_reading and last_reading.get("ppm") is not None:
                    consumption = float(last_reading["ppm"]) - ppm  # positive = plant consuming nutrients
                expected = profile["expected_ppm_drift_per_day"]
                in_range = bool(ppm_target) and ppm_target[0] <= ppm <= ppm_target[1]
                if consumption is not None and consumption < 0 and abs(consumption) > expected * 2:
                    scores["ppm"] = 0
                    findings.append(f"PPM rose by {abs(consumption):.0f} instead of declining - possible evaporation or salt buildup.")
                elif consumption is not None and consumption > expected * 3:
                    scores["ppm"] = 1
                    findings.append(f"PPM dropped {consumption:.0f}, faster than the ~{expected}/day expected for {stage}.")
                elif not in_range:
                    scores["ppm"] = 1
                    findings.append(f"PPM {ppm} is outside the {stage} target range {ppm_target}.")
                else:
                    scores["ppm"] = 2

            if reservoir_temp is None:
                scores["temp"] = 1
                findings.append("No reservoir temperature provided.")
            elif 64 <= reservoir_temp <= 72:
                scores["temp"] = 2
            elif 60 <= reservoir_temp < 64 or 72 < reservoir_temp <= 78:
                scores["temp"] = 1
                findings.append(
                    f"Reservoir temperature {reservoir_temp}F is outside the 64-72F (18-22C) band. "
                    "Where roots sit in solution this is a root-health parameter, not comfort - "
                    "warm water holds less dissolved oxygen, and the risk of root pathogens rises "
                    "above roughly 22C, more so as EC climbs."
                )
            else:
                scores["temp"] = 0
                findings.append(f"Reservoir temperature {reservoir_temp} is well outside the safe range.")

            root_verdict, root_method = self._classify_qualitative(
                root_health_text, ROOT_HEALTH_STABLE_KEYWORDS, ROOT_HEALTH_CRITICAL_KEYWORDS, "root health"
            )
            scores["root_health"] = self._verdict_score(root_verdict)
            methods["root_health"] = root_method
            if root_verdict != "stable":
                findings.append(f"Root health observation classified as {root_verdict} ({root_method}): \"{root_health_text}\"")

            odor_verdict, odor_method = self._classify_qualitative(
                odor_text, ODOR_STABLE_KEYWORDS, ODOR_CRITICAL_KEYWORDS, "reservoir odor"
            )
            if biofilm and str(biofilm).lower() not in ("false", "no", "none", "0"):
                odor_verdict = "critical" if odor_verdict == "stable" else odor_verdict
                findings.append("Biofilm/slime reported in reservoir.")
            scores["odor"] = self._verdict_score(odor_verdict)
            methods["odor"] = odor_method
            if odor_verdict != "stable" and odor_text:
                findings.append(f"Odor observation classified as {odor_verdict} ({odor_method}): \"{odor_text}\"")

            stability_score = sum(scores.values())
            if stability_score >= 8:
                band = "stable"
            elif stability_score >= 4:
                band = "warning"
            else:
                band = "critical"

            if band == "stable":
                observation = "; ".join(findings) if findings else f"pH, PPM, temperature, and root/odor observations are all within expected range for {stage}."
                reason = f"All monitored components scored within the stable band for {stage} (score {stability_score}/10)."
                action = "Continue monitoring. No reservoir change needed."
            else:
                observation = "; ".join(findings) if findings else "One or more reservoir components scored outside the stable band."
                weakest = min(scores, key=lambda k: scores[k])
                reason = f"Lowest-scoring component is {weakest} (score {scores[weakest]}/2) for {stage} stage."
                if band == "critical":
                    action = f"Intervene now: address {weakest} (see observation) before continuing the current protocol."
                else:
                    action = f"Increase monitoring frequency and re-check {weakest} within 24-48h; intervene if it doesn't improve."

            if any(m == "llm_unavailable" for m in methods.values()):
                confidence = "low"
            elif any(m == "llm" for m in methods.values()):
                confidence = "medium"
            else:
                confidence = "high"

            recommendation = self._make_recommendation(observation, reason, action, confidence)
            recommendation.update({
                "stability_score": stability_score,
                "stability_band": band,
                "component_scores": scores,
                "classification_methods": methods
            })

            record = {
                "id": f"reservoir_eval_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "stage": stage,
                "inputs": {
                    "ph": ph, "ppm": ppm, "reservoir_temp": reservoir_temp,
                    "water_level_change": water_level_change,
                    "root_health": root_health_text, "odor": odor_text, "biofilm": biofilm
                },
                "recommendation": recommendation
            }
            self.store_own_memory(record["id"], json.dumps(record))
            index = self._load_reservoir_eval_index()
            index.append(record["id"])
            self.store_own_memory("reservoir_eval_index", json.dumps(index))

            return {"result": recommendation, "record": record}

        elif task == "evaluate_leaf":
            plant_id = args.get("plant_id", "current_plant")
            symptom_text = args.get("symptom_text") or args.get("notes") or ""
            airflow_impact = args.get("airflow_impact")
            disease_signs = args.get("disease_signs")
            photo_path = args.get("photo_path")
            photo_refs = args.get("photo_refs", [])
            vision_note = None
            fused = None
            verification_text = None
            symptom_text_from_user = bool(symptom_text)

            if photo_path and VISION_AVAILABLE and not symptom_text:
                # Checkpointed: model loading + inference + a possible escalation call
                # can take a while, and shouldn't have to redo the (possibly slow) fusion
                # pass if the process restarts mid-evaluation - a retry on the same
                # plant/photo resumes from whatever was last saved instead of redoing it.
                checkpoint_id = f"evaluate_leaf_{plant_id}_{os.path.basename(photo_path)}"
                checkpoint = self.load_checkpoint(checkpoint_id)
                if checkpoint and checkpoint.get("status") == "completed":
                    fused = checkpoint["state"]["fused"]
                    symptom_text = checkpoint["state"]["symptom_text"]
                    verification_text = checkpoint["state"].get("verification_text")
                    vision_note = checkpoint["state"]["vision_note"] + " (resumed from checkpoint)"
                else:
                    fused = fuse_observations(photo_path, species=self._get_species_for_plant(plant_id))
                    self.save_checkpoint(checkpoint_id, {"fused": fused}, status="in_progress")
                    if "error" not in fused:
                        if fused["low_confidence"]:
                            verification = self._call_inference_vision(
                                # Plain sentences on purpose. Small local vision
                                # models (moondream) return an empty or degenerate
                                # completion for prompts with apostrophes, dash
                                # clauses, or meta-instructions like "in one or two
                                # sentences" - verified reproducibly. Keep it flat.
                                "Describe this plant leaf health. Color, spots, damage, pests, disease signs.",
                                photo_path
                            )
                            correction = self._log_vision_correction(photo_path, fused, verification)
                            verification_text = verification
                            symptom_text = verification or self._describe_fused_observation(fused)
                            if verification:
                                reason = ("no local model covers this species"
                                          if fused.get("species_supported") is False
                                          else f"local perception confidence was low ({fused['overall_confidence']:.2f})")
                                vision_note = f"Verified by the vision verification model ({reason}). Logged as {correction['id']} for future retraining."
                            else:
                                # Don't claim a verification that didn't happen - the
                                # escalation call failed, so this read is unverified.
                                vision_note = (
                                    "Escalation to the verification model was attempted but did not return a result "
                                    "(check ANTHROPIC_API_KEY / inference service). "
                                    + ("No local model covers this species, so no reliable read is available."
                                       if fused.get("species_supported") is False
                                       else f"Falling back to the low-confidence local read ({fused['overall_confidence']:.2f}).")
                                )
                        else:
                            symptom_text = self._describe_fused_observation(fused)
                            vision_note = f"Derived from local YOLO+ViT perception pipeline (confidence {fused['overall_confidence']:.2f})."
                        self.save_checkpoint(checkpoint_id, {
                            "fused": fused, "symptom_text": symptom_text,
                            "verification_text": verification_text, "vision_note": vision_note
                        }, status="completed")
                    else:
                        vision_note = f"Perception pipeline unavailable: {fused['error']}"
                if fused.get("text"):
                    photo_refs = photo_refs + [photo_path]

            airflow_flag = bool(airflow_impact) and str(airflow_impact).lower() not in ("false", "no", "none", "0")
            disease_flag = bool(disease_signs) and str(disease_signs).lower() not in ("false", "no", "none", "0")

            # No usable read at all: the local models don't cover this species and
            # the verification tier didn't answer either. symptom_text here is a
            # diagnostic message, not a symptom description - running it through
            # the keyword classifier below would classify the *error text* (e.g.
            # "local disease models cover only..." trips the "disease" keyword),
            # so short-circuit with an explicit inconclusive result instead.
            unreadable = (
                photo_path and not symptom_text_from_user
                and isinstance(fused, dict)
                and fused.get("species_supported") is False
                and not verification_text
            )
            if unreadable:
                recommendation = self._make_recommendation(
                    "No reliable read of this photo is available.",
                    (fused.get("health_error") or "Local models don't cover this species.")
                    + " The verification model didn't return a result either.",
                    "Describe the symptoms in text and I'll evaluate those, or set ANTHROPIC_API_KEY "
                    "to enable photo verification for this species.",
                    "low"
                )
                recommendation["classification"] = "inconclusive"
                if vision_note:
                    recommendation["vision_note"] = vision_note
                record = {
                    "id": f"leaf_eval_{self._uid()}",
                    "timestamp": datetime.now().isoformat(),
                    "plant_id": plant_id,
                    "photo_refs": photo_refs,
                    "recommendation": recommendation
                }
                self.store_own_memory(record["id"], json.dumps(record))
                index = self._load_leaf_eval_index()
                index.append(record["id"])
                self.store_own_memory("leaf_eval_index", json.dumps(index))
                return {"result": recommendation, "record": record}

            if disease_flag or airflow_flag or self._negation_aware_hit(symptom_text, LEAF_PROBLEM_KEYWORDS):
                classification = "problem"
            elif self._negation_aware_hit(symptom_text, LEAF_SENESCENT_KEYWORDS):
                classification = "senescent"
            elif self._negation_aware_hit(symptom_text, LEAF_PRODUCTIVE_KEYWORDS):
                classification = "productive"
            else:
                verdict, _method = self._classify_qualitative(
                    symptom_text, LEAF_PRODUCTIVE_KEYWORDS, LEAF_PROBLEM_KEYWORDS, "leaf health"
                )
                classification = {"stable": "productive", "warning": "senescent", "critical": "problem"}[verdict]

            if classification == "productive":
                observation = f"Leaf symptoms described as: \"{symptom_text}\"." if symptom_text else "No significant symptoms reported."
                reason = "Leaf shows healthy color/vigor with no disease, pest, or airflow signals."
                action = "Preserve the leaf."
                confidence = "high"
            elif classification == "senescent":
                observation = f"Leaf symptoms described as: \"{symptom_text}\"."
                reason = "Yellowing consistent with natural senescence; plant reallocating resources."
                action = "Monitor; allow natural senescence unless disease or airflow risk develops."
                confidence = "medium"
            else:
                extra = (" Airflow impact reported." if airflow_flag else "") + (" Disease signs reported." if disease_flag else "")
                observation = f"Leaf symptoms described as: \"{symptom_text}\".{extra}"
                reason = "Disease, pest, airflow obstruction, or severe damage signal detected."
                action = "Recommend intervention or removal."
                confidence = "high" if (disease_flag or airflow_flag) else "medium"

            recommendation = self._make_recommendation(observation, reason, action, confidence)
            recommendation["classification"] = classification
            if vision_note:
                recommendation["vision_note"] = vision_note

            record = {
                "id": f"leaf_eval_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "photo_refs": photo_refs,
                "recommendation": recommendation
            }
            self.store_own_memory(record["id"], json.dumps(record))
            index = self._load_leaf_eval_index()
            index.append(record["id"])
            self.store_own_memory("leaf_eval_index", json.dumps(index))

            return {"result": recommendation, "record": record}

        elif task == "get_grow_history":
            plant_id = args.get("plant_id", "current_plant")

            if plant_id == "current_plant":
                plant_record = {
                    "plant_id": "current_plant",
                    "germination_date": self._unwrap_value(self.retrieve_own_memory("germination_date")),
                    "strain": self._unwrap_value(self.retrieve_own_memory("current_strain")),
                    "species": self._unwrap_value(self.retrieve_own_memory("current_species")) or "cannabis",
                    "stage": self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                }
            else:
                plant_record = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)

            readings = self._get_readings_for_plant(plant_id)
            notes = [n for n in self._get_all_notes() if n.get("plant_id") == plant_id]
            reservoir_evals = [e for e in self._get_all_reservoir_evals() if e.get("plant_id") == plant_id]
            leaf_evals = [e for e in self._get_all_leaf_evals() if e.get("plant_id") == plant_id]
            stage_evals = [e for e in self._get_all_stage_evals() if e.get("plant_id") == plant_id]

            timeline = []
            for r in readings:
                timeline.append({"type": "reading", "timestamp": r.get("timestamp"), "data": r})
            for n in notes:
                timeline.append({"type": "note", "timestamp": n.get("timestamp"), "data": n})
            for e in reservoir_evals:
                timeline.append({"type": "reservoir_eval", "timestamp": e.get("timestamp"), "data": e})
            for e in leaf_evals:
                timeline.append({"type": "leaf_eval", "timestamp": e.get("timestamp"), "data": e})
            for e in stage_evals:
                timeline.append({"type": "stage_eval", "timestamp": e.get("timestamp"), "data": e})
            timeline.sort(key=lambda t: t.get("timestamp") or "")

            return {
                "result": {
                    "plant_id": plant_id,
                    "plant_record": plant_record,
                    "timeline": timeline,
                    "counts": {
                        "readings": len(readings),
                        "notes": len(notes),
                        "reservoir_evals": len(reservoir_evals),
                        "leaf_evals": len(leaf_evals),
                        "stage_evals": len(stage_evals)
                    }
                }
            }

        elif task == "evaluate_growth_stage":
            plant_id = args.get("plant_id", "current_plant")
            morphology_text = args.get("morphology_text") or args.get("notes") or ""
            species = args.get("species") or self._get_species_for_plant(plant_id)
            photo_path = args.get("photo_path")
            vision_note = None

            if photo_path and VISION_AVAILABLE and not morphology_text:
                # Checkpointed, same idiom as evaluate_leaf above - a retry on the
                # same plant/photo resumes instead of redoing the fusion pass.
                checkpoint_id = f"evaluate_growth_stage_{plant_id}_{os.path.basename(photo_path)}"
                checkpoint = self.load_checkpoint(checkpoint_id)
                if checkpoint and checkpoint.get("status") == "completed":
                    morphology_text = checkpoint["state"]["morphology_text"]
                    vision_note = checkpoint["state"]["vision_note"] + " (resumed from checkpoint)"
                else:
                    fused = fuse_observations(photo_path, species=species)
                    self.save_checkpoint(checkpoint_id, {"fused": fused}, status="in_progress")
                    if "error" not in fused:
                        if fused["low_confidence"]:
                            verification = self._call_inference_vision(
                                # Flat phrasing for the same reason as evaluate_leaf above.
                                "Describe this plant growth stage. Leaf shape and count, node "
                                "structure, any pistils or white hairs, any flower sites.",
                                photo_path
                            )
                            correction = self._log_vision_correction(photo_path, fused, verification)
                            morphology_text = verification or self._describe_fused_observation(fused)
                            vision_note = f"Local perception confidence was low ({fused['overall_confidence']:.2f}) - escalated to verification model. Logged as {correction['id']} for future retraining."
                        else:
                            morphology_text = self._describe_fused_observation(fused)
                            vision_note = f"Derived from local YOLO+ViT perception pipeline (confidence {fused['overall_confidence']:.2f})."
                        self.save_checkpoint(checkpoint_id, {"fused": fused, "morphology_text": morphology_text, "vision_note": vision_note}, status="completed")
                    else:
                        vision_note = f"Perception pipeline unavailable: {fused['error']}"

            if plant_id == "current_plant":
                current_stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                current_stage = (plant or {}).get("stage", "unknown")

            is_decline = self._negation_aware_hit(morphology_text, DECLINE_KEYWORDS)

            transitioned = None
            if is_decline:
                classification = "decline"
                observation = f"Morphology description flags a decline signal: \"{morphology_text}\"."
                reason = "Disease/decomposition keywords detected - this is never treated as forward stage progress."
                action = "Inspect immediately (roots, stem, affected tissue) before any other intervention; do not assume normal stage progression."
                confidence = "high"
            else:
                inferred_stage, method = self._classify_growth_stage(morphology_text, species)
                if inferred_stage is None:
                    classification = "inconclusive"
                    observation = f"No clear stage signal found in morphology description: \"{morphology_text}\"." if morphology_text else "No morphology description provided."
                    reason = "Neither known keyword cues nor inference could determine a growth stage."
                    action = "No change. Provide a more specific morphology description (leaf shape/count, presence of pistils/hairs, node count, etc.)."
                    confidence = "low"
                elif current_stage not in STAGE_ORDER:
                    classification = inferred_stage
                    observation = f"Morphology indicates {inferred_stage} (method: {method}); current tracked stage was '{current_stage}'."
                    reason = "Current stage wasn't a recognized stage to compare against, so the morphology read is applied directly."
                    action = f"Transition to {inferred_stage}."
                    confidence = "high" if method == "keyword" else "medium"
                    transition_result = self.handle_task("transition_stage", {
                        "plant_id": plant_id, "new_stage": inferred_stage,
                        "notes": f"Auto-transitioned from morphology evidence ({method}): {morphology_text}"
                    }, sender)
                    transitioned = transition_result.get("transition")
                else:
                    current_idx = STAGE_ORDER.index(current_stage)
                    inferred_idx = STAGE_ORDER.index(inferred_stage)
                    if inferred_idx > current_idx:
                        classification = inferred_stage
                        observation = f"Morphology indicates {inferred_stage} (method: {method}), ahead of tracked stage '{current_stage}'."
                        reason = "Leaf/plant structure has progressed further than the calendar/nutrient-tracked stage - likely an environment-driven early transition."
                        confidence = "high" if method == "keyword" else "medium"
                        # Only definitive keyword evidence auto-applies. A stage
                        # change moves feed weighting, pH/ppm targets and
                        # monitoring cadence, so an inference from a small local
                        # model recommends rather than applies - the same rule
                        # verify_growth_stage already follows. A 1.5b model read
                        # "9-blade leaves, no pistils, no calyx" as flower.
                        if method == "keyword":
                            action = f"Transition to {inferred_stage}."
                            transition_result = self.handle_task("transition_stage", {
                                "plant_id": plant_id, "new_stage": inferred_stage,
                                "notes": f"Auto-transitioned from morphology evidence ({method}): {morphology_text}"
                            }, sender)
                            transitioned = transition_result.get("transition")
                        else:
                            action = (f"Consider transitioning to {inferred_stage} - inferred by "
                                      f"{method}, not from a definitive morphological cue, so it is "
                                      "NOT auto-applied. Confirm against the actual marker for that "
                                      "stage before calling transition_stage.")
                    elif inferred_idx < current_idx:
                        classification = "regression"
                        observation = f"Morphology indicates {inferred_stage} (method: {method}), behind tracked stage '{current_stage}'."
                        reason = "A stage regression is unusual and is not auto-applied - could be plant stress, damage, or a misread description."
                        action = "Do not auto-transition backward. Investigate for plant stress, damage, or environmental cause before making any change."
                        confidence = "medium" if method == "keyword" else "low"
                    else:
                        classification = inferred_stage
                        observation = f"Morphology confirms tracked stage '{current_stage}' (method: {method})."
                        reason = "No discrepancy between morphology and tracked stage."
                        action = "No change needed."
                        confidence = "high" if method == "keyword" else "medium"

            recommendation = self._make_recommendation(observation, reason, action, confidence)
            recommendation["classification"] = classification
            if vision_note:
                recommendation["vision_note"] = vision_note

            record = {
                "id": f"stage_eval_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "species": species,
                "morphology_text": morphology_text,
                "previous_stage": current_stage,
                "transitioned": transitioned,
                "recommendation": recommendation
            }
            self.store_own_memory(record["id"], json.dumps(record))
            index = self._load_stage_eval_index()
            index.append(record["id"])
            self.store_own_memory("stage_eval_index", json.dumps(index))

            return {"result": recommendation, "record": record}

        elif task == "verify_growth_stage":
            # Cross-checks the tracked stage against real-world reference data,
            # not just the local keyword/morphology heuristic - looks up how long
            # this strain/type typically takes to reach each stage (autoflowers in
            # particular run on a fixed genetic clock, not photoperiod), combines
            # that with days-since-germination and any morphology notes, and asks
            # the LLM to reconcile all three. Never auto-transitions - stage changes
            # from this task are a recommendation for a human or Boss to confirm,
            # given the extra uncertainty layered on top of the morphology-only path.
            plant_id = args.get("plant_id", "current_plant")
            if plant_id == "current_plant":
                germination_date = self._unwrap_value(self.retrieve_own_memory("germination_date"))
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or "unspecified strain"
                current_stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                if not plant:
                    return {"error": f"Unknown plant_id: {plant_id}"}
                germination_date = plant.get("germination_date")
                strain = plant.get("strain") or "unspecified strain"
                current_stage = plant.get("stage", "unknown")

            morphology_text = args.get("morphology_text") or args.get("notes") or ""

            days_elapsed = None
            if germination_date:
                try:
                    g = datetime.fromisoformat(germination_date)
                    days_elapsed = (datetime.now() - g).days
                except Exception:
                    days_elapsed = None

            query = (f"{strain} day {days_elapsed} growth stage" if days_elapsed is not None
                      else f"{strain} vegetative stage how many weeks")
            search_result = self.search_public(query)
            search_snippet = json.dumps(search_result)[:1500]

            prompt = (
                f"A cannabis plant (strain: {strain}) is {days_elapsed if days_elapsed is not None else 'an unknown number of'} "
                f"days past germination (germination date: {germination_date or 'unknown'}). "
                f"It is currently tracked as '{current_stage}' stage. "
                f"Morphology/canopy notes: \"{morphology_text}\"\n\n"
                f"Web search reference (may be noisy - weigh it, don't trust it blindly):\n{search_snippet}\n\n"
                "Considering typical timelines for this strain/type AND the morphology notes together, "
                "which single stage is most accurate right now: germination, seedling, early_veg, veg, or flower? "
                "Reply with the stage name on the first line, then one sentence of justification on the second line."
            )
            llm_response = self._call_inference(prompt, timeout=45)

            inferred_stage = None
            justification = ""
            if llm_response:
                lines = [l.strip() for l in llm_response.strip().splitlines() if l.strip()]
                if lines:
                    first = lines[0].lower()
                    inferred_stage = next((s for s in STAGE_ORDER if s in first), None)
                    justification = " ".join(lines[1:]) if len(lines) > 1 else ""

            if inferred_stage is None:
                recommendation = self._make_recommendation(
                    f"Couldn't get a clear stage read from web+LLM verification (raw response: {llm_response!r}).",
                    "Reference lookup was inconclusive or the inference service didn't return a usable answer.",
                    f"Keep tracked stage '{current_stage}' unchanged; rely on morphology-only evaluate_growth_stage instead.",
                    "low"
                )
                recommendation["classification"] = "inconclusive"
            elif inferred_stage == current_stage:
                recommendation = self._make_recommendation(
                    f"Web+timeline verification confirms '{current_stage}' at {days_elapsed} days post-germination for {strain}. {justification}",
                    "Reference timeline and tracked stage agree.",
                    "No change needed.",
                    "medium"
                )
                recommendation["classification"] = current_stage
            else:
                recommendation = self._make_recommendation(
                    f"Web+timeline verification suggests '{inferred_stage}' (tracked stage is '{current_stage}') at {days_elapsed} days post-germination for {strain}. {justification}",
                    "Reference timeline/LLM read disagrees with the currently tracked stage.",
                    f"Consider transitioning to '{inferred_stage}' - call transition_stage to confirm, this task does not auto-apply it.",
                    "medium"
                )
                recommendation["classification"] = inferred_stage

            recommendation["days_since_germination"] = days_elapsed
            recommendation["search_query"] = query

            record = {
                "id": f"stage_verification_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "strain": strain,
                "previous_stage": current_stage,
                "recommendation": recommendation
            }
            self.store_own_memory(record["id"], json.dumps(record))

            return {"result": recommendation, "record": record}

        elif task == "log_training_event":
            # Topping, lollipopping, defoliation, LST. Recorded as a first-class
            # event rather than a free-text note so recommend_feed below can
            # actually reason about it - a plant regrowing removed capacity has
            # different needs from one that was never cut.
            plant_id = args.get("plant_id", "current_plant")
            event_type = (args.get("event_type") or "").lower().replace(" ", "_")
            if event_type not in TRAINING_EVENT_TYPES:
                return {"error": f"event_type must be one of: {', '.join(TRAINING_EVENT_TYPES)}"}
            profile = TRAINING_EVENT_TYPES[event_type]

            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                species = self._unwrap_value(self.retrieve_own_memory("current_species")) or "cannabis"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                stage = (plant or {}).get("stage", "unknown")
                species = (plant or {}).get("species", "cannabis")
                strain = (plant or {}).get("strain", "")

            is_auto = "auto" in str(strain).lower()
            concerns, guidance = [], []

            if profile["removes_capacity"]:
                guidance.append(
                    "Photosynthetic capacity and stored nitrogen were removed. Expect regrowth "
                    "demand: weight the feed toward nitrogen while the plant rebuilds leaf."
                )
            if stage == "flower":
                concerns.append(
                    "Removing capacity during flower costs bud development directly - the plant "
                    "cannot rebuild leaf and fill flower at the same time."
                )
            elif stage == "veg" and is_auto:
                concerns.append(
                    "Autoflower in late veg: the genetic clock does not pause for recovery, so "
                    "days spent regrowing leaf are days not spent building bud sites. The window "
                    "to feed nitrogen and recover is short."
                )
            if profile["severity"] == "heavy":
                concerns.append("Heavy removal - watch for stalled growth over the next few days.")

            guidance.append(
                "For airflow specifically, a clip fan costs the plant nothing; leaf removal buys "
                "airflow at the price of photosynthetic capacity."
            )

            record = {
                "id": f"training_event_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "event_type": event_type,
                "severity": profile["severity"],
                "removed_capacity": profile["removes_capacity"],
                "stage_at_event": stage,
                "species": species,
                "strain": strain,
                "detail": args.get("detail", ""),
                "concerns": concerns,
                "guidance": guidance,
            }
            self.store_own_memory(record["id"], json.dumps(record))
            index = self._load_training_event_index()
            index.append(record["id"])
            self.store_own_memory("training_event_index", json.dumps(index))

            recommendation = self._make_recommendation(
                f"{event_type.replace('_', ' ').title()} logged at {stage} stage.",
                "; ".join(concerns) if concerns else "No stage-specific risk flagged for this event.",
                " ".join(guidance),
                "high" if concerns else "medium",
            )
            recommendation["classification"] = "training_event"
            return {"result": recommendation, "record": record}

        elif task == "recommend_feed":
            # Stage-aware nutrient ratio, adjusted for recent training. Scales the
            # recipe already recorded for THIS grow rather than inventing absolute
            # ml, since the right numbers depend on product line, source water and
            # reservoir volume - all per-grow, none of which generalise.
            plant_id = args.get("plant_id", "current_plant")
            target_ppm = self._parse_numeric(args.get("target_ppm"))

            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                stage = (plant or {}).get("stage", "unknown")
                strain = (plant or {}).get("strain", "")

            raw = self._unwrap_value(self.retrieve_own_memory("current_nutrients"))
            current = json.loads(raw) if raw else {}
            base = current.get("nutrients") or {}
            if not base:
                return {"error": "No current recipe recorded - set_current_nutrients first"}

            emphasis = STAGE_FEED_EMPHASIS.get(stage, {})
            if "note" in emphasis and len(emphasis) == 1:
                return {"result": self._make_recommendation(
                    f"Stage '{stage}' does not use a scaled recipe.", emphasis["note"],
                    "No ratio change recommended.", "medium")}

            # Recent capacity-removing training raises nitrogen demand for regrowth.
            recent_events = [e for e in self._get_all_training_events()
                             if e.get("plant_id") == plant_id and e.get("removed_capacity")]
            regrowth = None
            if recent_events:
                last = recent_events[-1]
                try:
                    age_days = (datetime.now() - datetime.fromisoformat(last["timestamp"])).days
                except Exception:
                    age_days = 99
                if age_days <= 10:
                    regrowth = {"event": last["event_type"], "days_ago": age_days}

            # Correct components that never scaled with the rest before applying
            # the stage multiplier - otherwise the multiplier carries the lag
            # forward rather than fixing it.
            lagging = self._detect_lagging_nutrients()

            suggested, notes = {}, []
            for name, value in base.items():
                v = self._parse_numeric(value)
                if v is None:
                    continue
                mult = emphasis.get(name, 1.0)
                if regrowth and name in ("FloraGro", "FloraMicro"):
                    mult *= REGROWTH_N_BOOST
                if name in lagging:
                    mult *= lagging[name]["catchup_multiplier"]
                suggested[name] = round(v * mult, 1)

            for name, info in lagging.items():
                notes.append(
                    f"{name} has not kept pace: up {info['growth_pct']:.0f}% since the first "
                    f"recorded recipe while the recipe as a whole moved {info['median_growth_pct']:.0f}%. "
                    f"Applied a {info['catchup_multiplier']:.2f}x catch-up on top of the stage "
                    "multiplier, because scaling a stalled component just carries the lag forward."
                )

            if regrowth:
                notes.append(
                    f"Nitrogen raised further: {regrowth['event'].replace('_',' ')} "
                    f"{regrowth['days_ago']} day(s) ago removed capacity the plant is rebuilding."
                )
            if stage == "veg" and "auto" in str(strain).lower():
                notes.append(
                    "Autoflower in veg: shift to bloom weighting when pistils appear, not on a "
                    "date. Watch the nodes rather than the calendar."
                )
            notes.append(
                "Treat these as a starting ratio, not a recipe - mix, stir, and measure. "
                "Different product lines contribute EC differently, so hit the ppm target by "
                "meter rather than by arithmetic."
            )
            if target_ppm:
                notes.append(
                    f"Target {target_ppm:g} ppm. If you are on RO or distilled water the baseline "
                    "is ~0, so the whole reading is nutrient; on tap, subtract your source-water "
                    "ppm from both sides before scaling."
                )

            recommendation = self._make_recommendation(
                f"Feed ratio for {stage} stage" + (f" ({strain})" if strain else "") + ".",
                emphasis.get("note", ""),
                "; ".join(notes),
                "medium",
            )
            recommendation["classification"] = "feed_recommendation"
            recommendation["current"] = base
            recommendation["suggested"] = suggested
            recommendation["unit"] = current.get("unit", "ml")
            recommendation["basis"] = current.get("basis")
            recommendation["reservoir_liters"] = current.get("reservoir_liters")
            recommendation["regrowth_adjustment"] = regrowth
            return {"result": recommendation}

        elif task == "set_grow_system":
            # The environment the plant is operating in. Stored per plant so a
            # second grow on different hardware doesn't inherit the first one's
            # assumptions.
            plant_id = args.get("plant_id", "current_plant")
            system_type = (args.get("system_type") or "").lower().replace(" ", "_").replace("-", "_")
            if system_type and system_type not in GROW_SYSTEM_TYPES:
                return {"error": f"system_type must be one of: {', '.join(GROW_SYSTEM_TYPES)}"}
            medium = (args.get("medium") or "none").lower().replace(" ", "_")
            if medium not in GROW_MEDIA:
                return {"error": f"medium must be one of: {', '.join(GROW_MEDIA)}"}
            water_source = (args.get("water_source") or "").lower()

            record = {
                "plant_id": plant_id,
                "timestamp": datetime.now().isoformat(),
                "system_type": system_type,
                "system_label": GROW_SYSTEM_TYPES.get(system_type, {}).get("label", system_type),
                "medium": medium,
                # Capacity is the container's rating; typical_working_liters is what
                # it actually runs at. A 5 gal bucket is never filled to 5 gal.
                # Dosing must use working volume - capacity would over-concentrate.
                "reservoir_capacity_liters": self._parse_numeric(args.get("reservoir_liters")),
                "typical_working_liters": self._parse_numeric(args.get("typical_working_liters")),
                "water_source": water_source,
                "equipment": args.get("equipment", {}),
                # Lighting is an environment parameter, not decoration: schedule
                # drives heat load and VPD, and height is what the canopy runs
                # into - contact with the fixture caused this grow's leaf cupping.
                "light_schedule_hours": self._parse_numeric(args.get("light_schedule_hours")),
                "light_height_cm": self._parse_numeric(args.get("light_height_cm")),
                "light_wattage": self._parse_numeric(args.get("light_wattage")),
                # "overhead" = one fixture above the canopy, where height is a real
                # parameter and the canopy can reach it. "distributed" = multiple
                # bars around a reflective space, where there is nothing to run
                # into and height stops being a meaningful question.
                "light_mounting": (args.get("light_mounting") or "overhead").lower(),
                "location": args.get("location", ""),
                "notes": args.get("notes", ""),
            }
            self.store_own_memory(f"grow_system_{plant_id}", json.dumps(record))

            advisories = []
            st = GROW_SYSTEM_TYPES.get(system_type, {})
            md = GROW_MEDIA.get(medium, {})
            if st.get("note"):
                advisories.append(st["note"])
            if md.get("note"):
                advisories.append(md["note"])
            if st.get("aerated"):
                advisories.append(
                    "Aerated reservoir: the air stone runs continuously, not on a timer. Dissolved "
                    "oxygen is what keeps roots white and makes higher EC safe."
                )
            if st.get("airlift"):
                advisories.append(
                    "The top feed is an airlift - rising bubbles drag water up the tube, so the air "
                    "pump both oxygenates and moves the water, with no separate water pump. Its "
                    "critical property: lift depends on how deep the intake sits, so the feed WEAKENS "
                    "AND STOPS WHILE THE RESERVOIR STILL HAS WATER IN IT. For an established plant "
                    "with roots in solution that is cosmetic; for a seedling whose roots have not "
                    "reached the water line it is fatal, because the medium dries out while the "
                    "bucket still looks part full. Treat 'top feed has gone quiet' as a refill "
                    "trigger, not the water level itself."
                )
            if st.get("roots_in_water"):
                advisories.append(
                    "Roots sit in solution, so water temperature is a root-health parameter, not a "
                    "comfort setting. Warm water holds less oxygen - 18-22C is the working band."
                )
            ws = WATER_SOURCES.get(water_source, {})
            if ws.get("strips_calmag"):
                advisories.append(
                    f"{water_source.title()} source: baseline is ~0 ppm so the whole reading is "
                    "nutrient, and it carries no calcium or magnesium - Cal-Mag is required, not "
                    "optional."
                )
            if ws.get("unbuffered"):
                advisories.append(
                    "No carbonate buffering in the source water, so pH moves fast - especially at "
                    "low EC, where there is little else holding it. Swings of most of a pH point "
                    "between readings are the water, not sloppy technique. Two consequences: "
                    "Cal-Mag is doing double duty as both the Ca/Mg supply and most of what "
                    "buffering exists, and pH steadies as EC comes up - so mix nutrients fully "
                    "and let them circulate BEFORE chasing pH, or you will be correcting a "
                    "number that has not settled yet."
                    + (" Distilled has no residual at all, so it swings faster than RO."
                       if water_source == "distilled" else "")
                )
            record["advisories"] = advisories

            cap, work = record["reservoir_capacity_liters"], record["typical_working_liters"]
            if cap:
                advisories.append(
                    f"Capacity is {cap:g}L but that is a container rating, not an operating volume - "
                    "reservoirs run underfilled. Dose against the volume actually in the reservoir; "
                    "mixing for capacity over-concentrates by however far below full it sits."
                )
            record["advisories"] = advisories
            size_txt = (f", {work:g}L working of {cap:g}L capacity" if cap and work
                        else (f", {cap:g}L capacity" if cap else ""))
            recommendation = self._make_recommendation(
                f"System registered: {record['system_label']} with {GROW_MEDIA[medium]['label']}"
                + size_txt + ".",
                "Readings are interpreted against the system they came from - the same ppm or water "
                "level means different things in different hardware.",
                " ".join(advisories) if advisories else "No system-specific advisories.",
                "high",
            )
            recommendation["classification"] = "grow_system"
            recommendation["system"] = record
            return {"result": recommendation, "record": record}

        elif task == "adjust_to_target_ppm":
            # Close the gap between a measured ppm and the target. Deliberately
            # volume-free: scaling what was already added by the ratio of target
            # to measured lands on target whatever the true volume is, which
            # matters because reservoir volume here is estimated from bottles
            # poured in and an unmarked sight tube. The meter is the authority.
            measured = self._parse_numeric(args.get("measured_ppm"))
            target = self._parse_numeric(args.get("target_ppm"))
            added = args.get("added") or {}
            if measured is None or target is None:
                return {"error": "Usage: {measured_ppm, target_ppm, [added: {nutrient: ml}], [assumed_volume_liters]}"}
            if measured <= 0:
                return {"error": "measured_ppm must be positive"}

            factor = target / measured
            top_up = {}
            for name, ml in added.items():
                v = self._parse_numeric(ml)
                if v is None:
                    continue
                # Additional amount needed, not the new total.
                top_up[name] = round(v * (factor - 1), 1)

            assumed = self._parse_numeric(args.get("assumed_volume_liters"))
            implied_volume = round(assumed * factor, 1) if assumed else None

            notes = []
            if factor > 1:
                notes.append(f"Measured {measured:g} is {(1-1/factor)*100:.0f}% short of {target:g}.")
            elif factor < 1:
                notes.append(
                    f"Measured {measured:g} OVERSHOOTS {target:g}. Nutrient cannot be removed - "
                    "dilute with plain water instead, roughly "
                    f"{(1/factor - 1)*100:.0f}% more volume."
                )
            if implied_volume:
                notes.append(
                    f"Implied actual volume is about {implied_volume:g}L against the {assumed:g}L "
                    "assumed - the shortfall is dilution, not weak nutrient, so the volume estimate "
                    "is what was off."
                )
            notes.append("Add, circulate, re-measure. Two passes usually lands it.")

            recommendation = self._make_recommendation(
                f"{measured:g} ppm measured against a {target:g} ppm target.",
                f"Scaling factor {factor:.2f}x on what was already added.",
                " ".join(notes),
                "high",
            )
            recommendation["classification"] = "ppm_adjustment"
            recommendation["factor"] = round(factor, 3)
            recommendation["add_now"] = top_up if factor > 1 else {}
            recommendation["implied_volume_liters"] = implied_volume
            return {"result": recommendation}

        elif task == "analyze_consumption":
            # "Is it drinking water faster than nutrients, or the other way round?"
            # Answerable only with volume alongside ppm: nutrient mass is
            # volume x ppm, so comparing mass against volume between two readings
            # separates uptake from concentration. If ppm FALLS while volume FALLS
            # the plant is stripping the reservoir; if ppm RISES while volume
            # falls, transpiration is outrunning feeding and it wants water.
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            readings = [r for r in self._get_readings_for_plant(plant_id)
                        if r.get("ppm") is not None and r.get("volume_liters") is not None]
            if len(readings) < 2:
                have = len(readings)
                return {"result": {
                    "verdict": "insufficient_data",
                    "readings_with_volume": have,
                    "observation": (
                        f"Only {have} reading(s) carry both ppm and volume. This comparison needs "
                        "two, because nutrient mass is volume x ppm - ppm alone cannot distinguish "
                        "the plant feeding from the solution being topped up or evaporating."
                    ),
                    "action": "Log volume_liters alongside ppm from here on.",
                }}

            a, b = readings[-2], readings[-1]
            va, vb = float(a["volume_liters"]), float(b["volume_liters"])
            pa, pb = float(a["ppm"]), float(b["ppm"])
            ma, mb = va * pa, vb * pb
            water_used = (1 - vb / va) * 100 if va else 0
            nutrient_used = (1 - mb / ma) * 100 if ma else 0

            if vb > va:
                verdict = "topped_up"
                observation = (
                    f"Volume rose {va:g}L to {vb:g}L, so this spans a top-up or change - uptake "
                    "cannot be separated out across it."
                )
                action = "Compare two readings taken between top-ups for a clean consumption read."
            elif nutrient_used > water_used + 5:
                verdict = "feeding_faster_than_drinking"
                observation = (
                    f"Nutrient down {nutrient_used:.0f}% while water down {water_used:.0f}% "
                    f"({pa:g}ppm/{va:g}L -> {pb:g}ppm/{vb:g}L). Falling ppm against falling volume "
                    "means uptake outpaced water loss - evaporation alone would concentrate the "
                    "solution and raise ppm."
                )
                action = ("The plant is stripping the reservoir faster than the recipe replaces it. "
                          "Raise strength and shorten the interval between changes.")
            elif water_used > nutrient_used + 5:
                verdict = "drinking_faster_than_feeding"
                observation = (
                    f"Water down {water_used:.0f}% while nutrient down only {nutrient_used:.0f}% "
                    f"({pa:g}ppm/{va:g}L -> {pb:g}ppm/{vb:g}L). The solution is concentrating."
                )
                action = ("Top up with plain water rather than more nutrient, or strength will "
                          "climb on its own and risk burn.")
            else:
                verdict = "balanced"
                observation = (
                    f"Water and nutrient falling together ({water_used:.0f}% vs "
                    f"{nutrient_used:.0f}%) - uptake is proportional."
                )
                action = "Hold the current strength; top up to volume as needed."

            result = self._make_recommendation(observation, f"Verdict: {verdict}.", action,
                                               "high" if verdict != "balanced" else "medium")
            result["classification"] = verdict
            result["window"] = {"from": a.get("timestamp"), "to": b.get("timestamp")}
            result["water_used_pct"] = round(water_used, 1)
            result["nutrient_used_pct"] = round(nutrient_used, 1)
            return {"result": result}

        elif task == "check_in":
            # The active-participant task: work out what the agent needs to know
            # right now and ask for it, rather than waiting to be told.
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            sys_raw = self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
            system = json.loads(sys_raw) if sys_raw else None

            schedule = MONITORING_SCHEDULE.get(stage, MONITORING_SCHEDULE["veg"])
            readings = self._get_readings_for_plant(plant_id)
            latest = readings[-1] if readings else None
            now = datetime.now()

            days_since = None
            if latest and latest.get("timestamp"):
                try:
                    days_since = (now - datetime.fromisoformat(latest["timestamp"])).days
                except Exception:
                    pass

            # Missing = never captured, or absent from the most recent reading.
            missing, stale = [], []
            for p in schedule["params"]:
                if not latest or latest.get(p) is None:
                    missing.append(p)
            if days_since is not None and days_since >= schedule["interval_days"]:
                stale.append(f"last reading was {days_since} day(s) ago; "
                             f"{stage} wants one every {schedule['interval_days']}")

            triggers = []
            change_interval = RESERVOIR_CHANGE_INTERVAL_DAYS.get(stage, 7)
            if days_since is not None and days_since >= change_interval:
                triggers.append(
                    f"Reservoir change likely due - {days_since} day(s) since the last logged "
                    f"reading and {stage} wants a change about every {change_interval}."
                )
            if system and system.get("system_type") in ("dwc", "top_fed_dwc"):
                triggers.append("Water temperature matters here - roots sit in solution.")
            if system and WATER_SOURCES.get((system.get("water_source") or "").lower(), {}).get("unbuffered"):
                triggers.append("Unbuffered source water - pH needs checking more often than mineral water would.")
            if "auto" in str(strain).lower() and stage == "veg":
                triggers.append("Autoflower in veg - watch the nodes for pistils; that is the trigger to change feed weighting.")

            questions = [PARAM_QUESTIONS.get(p, f"What's the {p}?") for p in missing]

            # Lighting: asked once per system, except height which moves with the
            # canopy. Only asked when a light is actually registered.
            if system and system.get("equipment", {}).get("lighting"):
                distributed = system.get("light_mounting") == "distributed"
                for field, q in LIGHTING_QUESTIONS.items():
                    # Height is meaningless without a single fixture overhead.
                    if field == "light_height_cm" and distributed:
                        continue
                    if system.get(field) is None:
                        missing.append(field)
                        questions.append(q)
                if system.get("light_schedule_hours") and stage == "veg" and "auto" in str(strain).lower():
                    triggers.append(
                        f"Lights at {system['light_schedule_hours']:g}h/day. Autoflowers do not need a "
                        "light-cycle change to flower, so this stays as-is through the transition - "
                        "unlike a photoperiod, where the flip is the trigger."
                    )
            if not missing and not stale:
                questions.append("Anything changed since the last reading - top-up, change, or new growth?")

            recommendation = self._make_recommendation(
                (f"Last reading {days_since} day(s) ago." if days_since is not None
                 else "No readings logged yet."),
                ("Missing for this stage: " + ", ".join(missing)) if missing else "Have what this stage needs.",
                " ".join(questions),
                "high" if (missing or stale) else "medium",
            )
            recommendation["classification"] = "check_in"
            recommendation["stage"] = stage
            recommendation["days_since_last_reading"] = days_since
            recommendation["missing_params"] = missing
            recommendation["stale"] = stale
            recommendation["triggers"] = triggers
            recommendation["questions"] = questions
            return {"result": recommendation}

        elif task == "get_nutrient_history":
            # Feed changes alongside the ppm they actually produced. The recipe
            # alone doesn't say whether a change worked - only the measured
            # concentration that followed it does.
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            history = self._get_nutrient_history()
            readings = self._get_readings_for_plant(plant_id)

            timeline = []
            for rec in history:
                ts = rec.get("timestamp", "")
                after = [r for r in readings if (r.get("timestamp") or "") > ts]
                nxt = after[0] if after else None
                timeline.append({
                    "changed_at": ts,
                    "stage": rec.get("stage"),
                    "nutrients": rec.get("nutrients"),
                    "unit": rec.get("unit"),
                    "basis": rec.get("basis"),
                    "reservoir_liters": rec.get("reservoir_liters"),
                    "per_liter": rec.get("per_liter"),
                    "next_measured_ppm": nxt.get("ppm") if nxt else None,
                    "next_reading_at": nxt.get("timestamp") if nxt else None,
                })

            ppm_series = [{"at": r.get("timestamp"), "ppm": r.get("ppm"), "stage": r.get("stage")}
                          for r in readings if r.get("ppm") is not None]
            gap = None
            if len(history) < 2 and len(ppm_series) > 2:
                gap = ("Only %d recipe change(s) on record against %d ppm readings - feed changes "
                       "made before nutrient history was versioned were overwritten and are not "
                       "recoverable. The ppm series below is the reliable record for that period."
                       % (len(history), len(ppm_series)))

            return {"result": {
                "plant_id": plant_id,
                "recipe_changes": timeline,
                "ppm_series": ppm_series,
                "history_gap": gap,
            }}

        elif task == "get_grow_system":
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            raw = self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
            if not raw:
                return {"result": None, "note": "No system registered for this plant - set_grow_system first."}
            return {"result": json.loads(raw)}

        elif task == "plan_system_transition":
            # Moving between growing systems (LWC -> DWC, reservoir size change).
            # The risks are not the move itself but the discontinuities around it.
            plant_id = args.get("plant_id", "current_plant")
            from_system = args.get("from_system", "current system")
            to_system = args.get("to_system", "new system")
            new_liters = self._parse_numeric(args.get("new_reservoir_liters"))
            water_source = (args.get("water_source") or "").lower()

            stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            readings = self._get_readings_for_plant(plant_id)
            latest = readings[-1] if readings else {}
            raw = self._unwrap_value(self.retrieve_own_memory("current_nutrients"))
            current = json.loads(raw) if raw else {}

            steps = [
                "Have the new reservoir filled, temperature-matched, and pH-balanced BEFORE lifting "
                "anything. Free-hanging roots have no medium holding moisture and the fine hairs "
                "desiccate in open air - this is measured in seconds, not minutes.",
                "Match the new solution to the old on temperature and pH first. Shock at transfer "
                "comes from abrupt change in conditions, not from the move.",
                "Start the new reservoir at roughly the CURRENT EC, then ramp toward target over "
                "following days. Moving and raising strength at the same time makes it impossible "
                "to tell which caused any reaction.",
                "Keep the root mass intact. Roots already free-hanging in solution move with the net "
                "pot and suffer almost no mechanical disturbance - do not tease them apart.",
                "Re-check pH about an hour after transfer and again the next day. A larger volume "
                "buffers better, but the solution is still weakly buffered until EC comes up.",
            ]
            if new_liters and current.get("reservoir_liters"):
                try:
                    factor = new_liters / float(current["reservoir_liters"])
                    steps.append(
                        f"Volume changes by {factor:.2f}x ({current['reservoir_liters']:g}L -> "
                        f"{new_liters:g}L). Scale the recipe by the same factor to hold "
                        f"concentration, then adjust strength separately."
                    )
                except Exception:
                    pass
            if "ro" in water_source or "distil" in water_source:
                steps.append(
                    "RO/distilled source: baseline is ~0 ppm, so every ppm you read is nutrient. "
                    "Targets that assumed tap water will now come out stronger than intended, and "
                    "RO strips calcium and magnesium - Cal-Mag becomes more important, not less."
                )
            if "auto" in str(strain).lower():
                steps.append(
                    "Autoflower: the clock does not pause for transplant recovery, so do this while "
                    "still in veg if possible rather than mid-flower."
                )

            # System/medium-specific guidance - generic reservoir advice can be
            # actively wrong for the hardware actually in front of the grower.
            target_type = (args.get("to_system_type") or "").lower().replace(" ", "_").replace("-", "_")
            target_medium = (args.get("to_medium") or "").lower().replace(" ", "_")
            if target_type in ("dwc", "top_fed_dwc"):
                steps.append(
                    "DWC water level: start it high enough to reach the root mass, then once roots "
                    "are established drop it to leave a few cm of air gap below the net pot. That "
                    "gap grows the air roots that do the oxygen uptake - keeping the level jammed "
                    "against the pot permanently is a common way to drown a healthy root system."
                )
                steps.append(
                    "Run the air stone continuously from the moment the plant goes in. Dissolved "
                    "oxygen is what makes a higher EC safe for the roots."
                )
            if target_type == "top_fed_dwc":
                steps.append(
                    "The top ring is what bridges the gap before roots reach the water line - run it "
                    "until roots are visibly into the reservoir, since the medium above will not stay "
                    "wet on its own. With an already-large free-hanging root mass that window is "
                    "short, but do not skip it on the next grow from seed."
                )
            if target_medium == "clay_pebbles":
                steps.append(
                    "Clay pebbles are inert and free-draining - they buffer nothing. They will not "
                    "hold water between feeds and they will not hold nutrient either, so the "
                    "reservoir is the entire supply. Rinse them before use; the dust clouds a "
                    "reservoir and can clog an air stone."
                )

            record = {
                "id": f"transition_plan_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "from_system": from_system,
                "to_system": to_system,
                "stage_at_plan": stage,
                "reference_reading": {k: latest.get(k) for k in ("ph", "ppm", "temp")},
                "current_recipe": current,
                "new_reservoir_liters": new_liters,
                "water_source": args.get("water_source"),
                "steps": steps,
            }
            self.store_own_memory(record["id"], json.dumps(record))

            recommendation = self._make_recommendation(
                f"Transition plan: {from_system} -> {to_system} at {stage} stage.",
                "Risk is in the discontinuities - root desiccation, and abrupt temp/pH/EC change - "
                "not in the move itself.",
                " ".join(f"({i+1}) {s}" for i, s in enumerate(steps)),
                "high",
            )
            recommendation["classification"] = "transition_plan"
            recommendation["steps"] = steps
            return {"result": recommendation, "record": record}

        elif task == "assess_plant":
            # The cross-domain reasoning step. Everything else in this agent
            # judges ONE thing in isolation: the vision model sees only pixels,
            # _classify_qualitative sees one sentence, evaluate_reservoir scores
            # numbers deterministically. None of them ever look at the whole
            # picture together, so a conclusion that only emerges from combining
            # them (e.g. pale leaves + ppm well under target for the stage =
            # under-fed, not disease) could never be reached. This gathers the
            # full snapshot from memory and reasons over it in one pass.
            plant_id = args.get("plant_id", "current_plant")
            include_photo = args.get("photo_path")

            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or "cannabis"
                germination_date = self._unwrap_value(self.retrieve_own_memory("germination_date"))
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                if not plant:
                    return {"error": f"Unknown plant_id: {plant_id}"}
                stage, strain = plant.get("stage", "unknown"), plant.get("strain", "cannabis")
                germination_date = plant.get("germination_date")

            days = None
            if germination_date:
                try:
                    days = (datetime.now() - datetime.fromisoformat(germination_date)).days
                except Exception:
                    days = None

            readings = self._get_readings_for_plant(plant_id)
            res_evals = [e for e in self._get_all_reservoir_evals() if e.get("plant_id") == plant_id]
            leaf_evals = [e for e in self._get_all_leaf_evals() if e.get("plant_id") == plant_id]
            notes = [n for n in self._get_all_notes() if n.get("plant_id") == plant_id]
            targets = self.handle_task("check_stage", {"stage": stage}, sender).get("result", {})

            # Optional fresh visual input - the vision model still only sees the
            # image, but its description becomes one input to this synthesis
            # rather than a verdict on its own.
            vision_text = None
            if include_photo and VISION_AVAILABLE:
                vision_text = self._call_inference_vision(
                    "Describe this plant leaf health. Color, spots, damage, pests, disease signs.",
                    include_photo
                )

            def _recent(items, n=3):
                return items[-n:] if items else []

            snapshot = {
                "strain": strain,
                "stage": stage,
                "days_since_germination": days,
                "stage_targets": targets,
                "recent_readings": [
                    {k: r.get(k) for k in ("timestamp", "ph", "ppm", "temp", "humidity", "notes")}
                    for r in _recent(readings)
                ],
                "latest_reservoir_assessment": (
                    _recent(res_evals, 1)[0].get("recommendation") if res_evals else None
                ),
                "latest_leaf_assessment": (
                    _recent(leaf_evals, 1)[0].get("recommendation") if leaf_evals else None
                ),
                "recent_notes": [
                    {"timestamp": n.get("timestamp"), "category": n.get("category"), "text": n.get("text")}
                    for n in _recent(notes)
                ],
                "fresh_visual_observation": vision_text,
            }

            prompt = (
                "You are advising a grower on one cannabis plant. Below is everything currently known "
                "about it, gathered from sensor readings, prior assessments, and the grower notes.\n\n"
                f"{json.dumps(snapshot, indent=2, default=str)}\n\n"
                "Reason across ALL of this together, not each item separately. Look especially for "
                "conclusions that only appear when the sources are combined, and for any conflict "
                "between them.\n\n"
                "Answer in exactly these four lines, no other text.\n"
                "ASSESSMENT: one sentence on the plant overall state.\n"
                "PRIORITY: the single most important thing to address right now.\n"
                "ACTION: the concrete step to take, with numbers where relevant.\n"
                "CONFIDENCE: high, medium, or low, and why in a few words."
            )

            answer = self._call_inference_capability(prompt, capability="synthesis", timeout=240)
            # Accumulate continuation lines: the model wraps its answer, so
            # capturing only the marker line truncates every field mid-sentence.
            # A line belongs to the current field until the next marker appears.
            fields = ("ASSESSMENT", "PRIORITY", "ACTION", "CONFIDENCE")
            parsed, current = {}, None
            for line in (answer or "").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                marker = next((f for f in fields if stripped.upper().startswith(f)), None)
                if marker:
                    current = marker.lower()
                    parsed[current] = stripped.split(":", 1)[-1].strip()
                elif current:
                    parsed[current] = self._join_wrapped(parsed[current], stripped)
            parsed = {k: re.sub(r'\s{2,}', ' ', v).strip() for k, v in parsed.items() if v}

            recommendation = self._make_recommendation(
                parsed.get("assessment") or "Could not synthesize an assessment from the current data.",
                parsed.get("priority") or "No single priority identified.",
                parsed.get("action") or "No action determined.",
                # First word only, punctuation stripped - the model writes
                # "medium, because ..." and the bare word is the graded value.
                re.sub(r'[^a-z]', '', (parsed.get("confidence") or "low").split()[0].lower()) or "low"
            )
            recommendation["classification"] = "synthesis"
            recommendation["confidence_note"] = parsed.get("confidence")
            recommendation["synthesized_from"] = {
                "readings": len(readings), "reservoir_evals": len(res_evals),
                "leaf_evals": len(leaf_evals), "notes": len(notes),
                "fresh_photo": bool(vision_text),
            }

            record = {
                "id": f"assessment_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "snapshot": snapshot,
                "raw_response": answer,
                "recommendation": recommendation,
            }
            self.store_own_memory(record["id"], json.dumps(record))
            return {"result": recommendation, "record": record}

        elif task == "validate_environment_targets":
            # Text data points (pH, PPM/EC, temp, humidity, light) ARE things web
            # search can legitimately check: they're published, strain/stage/
            # medium-specific numbers, not a judgement about pixels. This looks up
            # the recommended ranges and compares them against both the hardcoded
            # STAGE_PROFILES/check_stage targets and the latest logged reading, so
            # a wrong built-in target gets caught instead of silently persisting.
            plant_id = args.get("plant_id", "current_plant")
            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or "cannabis"
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                stage = (plant or {}).get("stage", "unknown")
                strain = (plant or {}).get("strain", "cannabis")
            medium = args.get("medium", "DWC hydroponic")
            metrics = args.get("metrics") or ["pH", "PPM", "water temperature", "humidity", "light"]

            builtin = self.handle_task("check_stage", {"stage": stage}, sender).get("result", {})
            readings = self._get_readings_for_plant(plant_id)
            latest = readings[-1] if readings else {}

            findings = []
            for metric in metrics:
                query = f"{strain} {stage} stage {medium} recommended {metric} range cannabis"
                snippet = json.dumps(self.search_public(query))[:900]
                prompt = (
                    f"Grower question: what is the recommended {metric} range for cannabis in the "
                    f"'{stage}' stage grown in {medium}?\n\n"
                    f"Web search reference (noisy - weigh it, don't trust blindly):\n{snippet}\n\n"
                    f"Our system currently uses these built-in targets for this stage: {json.dumps(builtin)}\n"
                    f"Latest logged reading: {json.dumps({k: latest.get(k) for k in ('ph','ppm','temp','humidity')})}\n\n"
                    "Reply in exactly two lines.\n"
                    "Line 1: the recommended range as a concise value (e.g. '5.5-6.5' or '600-800 ppm').\n"
                    "Line 2: one sentence - does our current reading fall in that range, and if not what to change?"
                )
                answer = self._call_inference(prompt, timeout=45)
                # Small local models often echo the "Line 1:"/"Line 2:" scaffolding
                # from the prompt back into the answer - strip it so the stored
                # finding reads as a value, not as a transcript of the instructions.
                lines = [
                    re.sub(r'^line\s*\d+\s*[:.\-]\s*', '', l.strip(), flags=re.IGNORECASE)
                    for l in (answer or "").splitlines() if l.strip()
                ]
                lines = [l for l in lines if l]
                findings.append({
                    "metric": metric,
                    "researched_range": lines[0] if lines else None,
                    "assessment": " ".join(lines[1:]) if len(lines) > 1 else None,
                    "query": query,
                    "resolved": bool(lines),
                })

            record = {
                "id": f"env_validation_{self._uid()}",
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id,
                "stage": stage,
                "strain": strain,
                "medium": medium,
                "builtin_targets": builtin,
                "latest_reading": latest,
                "findings": findings,
            }
            self.store_own_memory(record["id"], json.dumps(record))
            return {"result": record}

        elif task == "training_quest_status":
            # Gamified view of the cannabis-vision data campaign. Uses the
            # generic core.quest_manager skill - the only cannabis-specific part
            # is the counter below (folder counts) and the label set.
            qm = QuestManager(self, VISION_CAMPAIGN_ID)
            counts, labels = self._training_counts()
            if not qm._load():
                qm.start_campaign(
                    labels=labels,
                    threshold_per_label=MIN_PER_CLASS,
                    description="Collect labelled cannabis leaf photos until a vision model can actually be trained."
                )
            status = qm.status(counts)
            status["next_quests"] = qm.next_quests(counts)
            status["training_dir"] = TRAINING_DIR
            pending = self._get_pending_candidates()
            status["candidates_awaiting_review"] = len(pending)
            return {"result": status}

        elif task == "source_training_candidates":
            # Candidate sourcing for labels the grower's own plant can't supply.
            # These are PROPOSALS, saved to a review queue - never counted as
            # training data until a human accepts them (see config/skills.json's
            # candidate_sourcing invariants). Provenance is recorded per item so
            # an unreviewed set is never mistaken for a licensed, labelled one.
            label = args.get("label")
            if not label:
                return {"error": "Missing label (e.g. nitrogen_deficiency)"}
            limit = int(args.get("limit", 5))
            query = args.get("query") or f"cannabis leaf {label.replace('_', ' ')} photo"

            # Image category - the plain "search" tool returns a single text
            # snippet with no URLs at all, which is useless for sourcing images.
            search_result = self.call_tool("searxng", "search_structured", {
                "query": query, "categories": "images", "max_results": limit * 3
            })
            candidates = []
            for item in self._extract_search_items(search_result)[:limit]:
                candidates.append({
                    "id": f"candidate_{int(time.time() * 1000)}_{len(candidates)}",
                    "label": label,
                    "query": query,
                    "source_url": item.get("url"),
                    "image_url": item.get("img_src"),
                    "source_title": item.get("title"),
                    "retrieved_at": datetime.now().isoformat(),
                    "status": "awaiting_review",
                })
            for c in candidates:
                self.store_own_memory(c["id"], json.dumps(c))
            index = self._get_candidate_index()
            index.extend(c["id"] for c in candidates)
            self.store_own_memory("training_candidate_index", json.dumps(index))

            return {"result": {
                "label": label,
                "query": query,
                "proposed": len(candidates),
                "candidates": candidates,
                "note": (
                    "These are unverified proposals, not training data. Review each one "
                    "(review_training_candidate) and only accepted images count toward the campaign. "
                    "Check licensing before using any web-sourced image for training."
                ),
            }}

        elif task == "review_training_candidate":
            candidate_id = args.get("candidate_id")
            decision = (args.get("decision") or "").lower()
            if not candidate_id or decision not in ("accept", "reject"):
                return {"error": "Usage: {candidate_id, decision: accept|reject}"}
            raw = self._unwrap_value(self.retrieve_own_memory(candidate_id))
            if not raw:
                return {"error": f"Unknown candidate: {candidate_id}"}
            candidate = json.loads(raw)
            candidate["status"] = "accepted" if decision == "accept" else "rejected"
            candidate["reviewed_at"] = datetime.now().isoformat()
            self.store_own_memory(candidate_id, json.dumps(candidate))

            # Reviewing earns XP either way - the goal is a clean set, and
            # rejecting noise is as valuable as accepting a good example.
            QuestManager(self, VISION_CAMPAIGN_ID).award(reviews=1)
            return {"result": {
                "candidate_id": candidate_id,
                "status": candidate["status"],
                "note": (
                    f"Accepted - download the image into {TRAINING_DIR}/{candidate['label']}/ "
                    "to have it counted." if decision == "accept" else "Rejected, not counted."
                ),
            }}

        elif task == "list_training_candidates":
            return {"result": self._get_pending_candidates()}

        elif task == "web_search":
            query = args.get("query") if isinstance(args, dict) else args[0] if args else None
            if not query:
                return {"error": "Missing query"}
            return self.search_public(query)

        # ---------- NEW: Linear Regression Tasks ----------
        elif task == "prepare_dataset":
            """Retrieve all readings and convert to a structured dataset (list of dicts)."""
            if not NUMPY_AVAILABLE:
                return {"error": "numpy is not installed. Please install it (pip install numpy) to use regression features."}
            index = self._unwrap_value(self.retrieve_own_memory("reading_index"))
            if not index:
                return {"error": "No readings found. Please log some readings first."}
            try:
                keys = json.loads(index)
            except:
                return {"error": "Invalid reading index."}
            readings = []
            for key in keys:
                raw = self._unwrap_value(self.retrieve_own_memory(key))
                if raw:
                    try:
                        readings.append(json.loads(raw))
                    except:
                        pass
            if not readings:
                return {"error": "No valid readings found."}
            return {"result": "Dataset prepared", "count": len(readings), "sample": readings[:5]}

        elif task == "fit_linear_model":
            if not NUMPY_AVAILABLE:
                return {"error": "numpy is not installed. Please install it (pip install numpy) to use regression features."}
            # Expect arguments: target (string), features (list of strings), maybe stage filter.
            target = args.get("target")
            features = args.get("features")
            if not target or not features:
                return {"error": "Missing target or features. Usage: fit_linear_model {target: 'vpd', features: ['temp', 'humidity']}"}
            # Get readings
            index = self._unwrap_value(self.retrieve_own_memory("reading_index"))
            if not index:
                return {"error": "No readings found."}
            try:
                keys = json.loads(index)
            except:
                return {"error": "Invalid reading index."}
            readings = []
            for key in keys:
                raw = self._unwrap_value(self.retrieve_own_memory(key))
                if raw:
                    try:
                        readings.append(json.loads(raw))
                    except:
                        pass
            if not readings:
                return {"error": "No valid readings found."}
            # Build X matrix and y vector
            X_rows = []
            y_vals = []
            for r in readings:
                row = []
                for f in features:
                    val = r.get(f)
                    if val is None:
                        val = 0.0
                    row.append(float(val))
                y = r.get(target)
                if y is None:
                    continue
                X_rows.append(row)
                y_vals.append(float(y))
            if len(X_rows) < 2:
                return {"error": "Not enough data points (need at least 2)."}
            X = np.array(X_rows)
            y = np.array(y_vals)
            # Add intercept term (column of ones)
            X = np.column_stack([np.ones(len(X)), X])
            # Solve least squares: coefficients = (X^T X)^-1 X^T y
            try:
                coeffs = np.linalg.lstsq(X, y, rcond=None)[0]
            except np.linalg.LinAlgError:
                return {"error": "Matrix is singular; cannot fit model."}
            # Store coefficients and feature names
            model = {
                "target": target,
                "features": features,
                "intercept": float(coeffs[0]),
                "coefficients": [float(c) for c in coeffs[1:]],
                "r2": None,  # could compute later
                "timestamp": datetime.now().isoformat()
            }
            self.store_own_memory("linear_model", json.dumps(model))
            return {"result": "Model fitted", "model": model}

        elif task == "predict_linear":
            if not NUMPY_AVAILABLE:
                return {"error": "numpy is not installed."}
            # Retrieve stored model
            raw_model = self._unwrap_value(self.retrieve_own_memory("linear_model"))
            if not raw_model:
                return {"error": "No fitted model found. Run fit_linear_model first."}
            try:
                model = json.loads(raw_model)
            except:
                return {"error": "Invalid model stored."}
            # Get input features from args
            input_values = []
            for f in model["features"]:
                val = args.get(f)
                if val is None:
                    return {"error": f"Missing feature: {f}"}
                input_values.append(float(val))
            X = np.array([1.0] + input_values)  # intercept + features
            prediction = np.dot(X, [model["intercept"]] + model["coefficients"])
            return {
                "result": "Prediction made",
                "target": model["target"],
                "features": model["features"],
                "input": input_values,
                "prediction": float(prediction)
            }

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = GrowAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
