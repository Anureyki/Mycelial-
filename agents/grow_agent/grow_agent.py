#!/usr/bin/env python3
import sys
import os
import time
import json
import math
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
        "seedling": ("single blade", "one-point leaf", "1-point leaf", "first true leaf",
                     "3-point leaf", "3-prong", "three point leaf", "three-prong"),
        "early_veg": ("5-point leaf", "5-prong", "five point leaf", "five-prong",
                      "five-finger leaf", "5-finger leaf"),
        "veg": ("7-point leaf", "7-prong", "seven point leaf", "9-point leaf",
                "multiple nodes", "bushy growth", "vigorous vegetative growth"),
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

    def _call_inference_vision(self, prompt, image_path, timeout=60):
        """Verification tier for low-confidence local perception results - rarely
        hit (only when the YOLO+ViT fusion's overall_confidence is low), so the
        per-call API cost stays small even though it's a cloud model."""
        try:
            resp = requests.post(
                "http://localhost:8005/reason",
                json={"prompt": prompt, "model": "claude-sonnet-5", "image_path": image_path},
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
        record = {
            "id": f"vision_correction_{int(time.time())}",
            "timestamp": datetime.now().isoformat(),
            "image_path": image_path,
            "fused_observation": fused,
            "verification_result": verification_result,
            "verified": False,
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

    def _classify_by_keywords(self, text, stable_keywords, critical_keywords):
        """Returns 'stable', 'critical', or None (inconclusive - caller should escalate)."""
        if not text:
            return None
        lowered = str(text).lower()
        if any(k in lowered for k in critical_keywords):
            return "critical"
        if any(k in lowered for k in stable_keywords):
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
        lowered = text.lower()
        matched = [stage for stage, keywords in cues.items() if any(k in lowered for k in keywords)]
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
                "stage": args.get("stage", "seedling"),
                "notes": args.get("notes", "")
            }
            # Also compute VPD if temp and humidity are present
            temp = self._parse_numeric(args.get("temp"))
            humidity = self._parse_numeric(args.get("humidity"))
            if temp is not None and humidity is not None:
                reading["vpd"] = calculate_vpd(temp, humidity)
            self.store_own_memory(f"reading_{int(time.time())}", json.dumps(reading))
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
            key = f"reading_{int(time.time())}"
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
                self.store_own_memory(f"stage_transition_{plant_id}_{int(time.time())}", json.dumps(transition))
                return {"result": f"Stage transitioned to {new_stage} for {plant_id}", "transition": transition}

            transition = {
                "timestamp": datetime.now().isoformat(),
                "plant_id": "current_plant",
                "new_stage": new_stage,
                "notes": notes,
                "previous_stage": self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
            }
            self.store_own_memory("current_stage", new_stage)
            self.store_own_memory(f"stage_transition_{int(time.time())}", json.dumps(transition))
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
            self.store_own_memory(f"water_change_{int(time.time())}", json.dumps(change))
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
            stage = args.get("stage", "unknown")
            nutrients = {k: v for k, v in args.items() if k != "stage"}
            if not nutrients:
                return {"error": "No nutrient values provided"}
            record = {
                "timestamp": datetime.now().isoformat(),
                "stage": stage,
                "nutrients": nutrients
            }
            self.store_own_memory("current_nutrients", json.dumps(record))

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
            reminder_id = f"reminder_{int(time.time())}"
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
            note_id = f"note_{int(time.time())}"
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
            elif 65 <= reservoir_temp <= 75:
                scores["temp"] = 2
            elif 60 <= reservoir_temp < 65 or 75 < reservoir_temp <= 80:
                scores["temp"] = 1
                findings.append(f"Reservoir temperature {reservoir_temp} is outside the ideal 65-75F band.")
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
                "id": f"reservoir_eval_{int(time.time())}",
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
                    vision_note = checkpoint["state"]["vision_note"] + " (resumed from checkpoint)"
                else:
                    fused = fuse_observations(photo_path)
                    self.save_checkpoint(checkpoint_id, {"fused": fused}, status="in_progress")
                    if "error" not in fused:
                        if fused["low_confidence"]:
                            verification = self._call_inference_vision(
                                "Describe this plant leaf's health in one or two sentences - color, "
                                "spots, damage, pests, or disease signs. Be specific and concrete.",
                                photo_path
                            )
                            correction = self._log_vision_correction(photo_path, fused, verification)
                            symptom_text = verification or self._describe_fused_observation(fused)
                            vision_note = f"Local perception confidence was low ({fused['overall_confidence']:.2f}) - escalated to verification model. Logged as {correction['id']} for future retraining."
                        else:
                            symptom_text = self._describe_fused_observation(fused)
                            vision_note = f"Derived from local YOLO+ViT perception pipeline (confidence {fused['overall_confidence']:.2f})."
                        self.save_checkpoint(checkpoint_id, {"fused": fused, "symptom_text": symptom_text, "vision_note": vision_note}, status="completed")
                    else:
                        vision_note = f"Perception pipeline unavailable: {fused['error']}"
                if fused.get("text"):
                    photo_refs = photo_refs + [photo_path]

            lowered = symptom_text.lower()
            airflow_flag = bool(airflow_impact) and str(airflow_impact).lower() not in ("false", "no", "none", "0")
            disease_flag = bool(disease_signs) and str(disease_signs).lower() not in ("false", "no", "none", "0")

            if disease_flag or airflow_flag or any(k in lowered for k in LEAF_PROBLEM_KEYWORDS):
                classification = "problem"
            elif any(k in lowered for k in LEAF_SENESCENT_KEYWORDS):
                classification = "senescent"
            elif any(k in lowered for k in LEAF_PRODUCTIVE_KEYWORDS):
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
                "id": f"leaf_eval_{int(time.time())}",
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
                    fused = fuse_observations(photo_path)
                    self.save_checkpoint(checkpoint_id, {"fused": fused}, status="in_progress")
                    if "error" not in fused:
                        if fused["low_confidence"]:
                            verification = self._call_inference_vision(
                                "Describe this plant's growth stage in one or two sentences - leaf "
                                "shape/count, node structure, presence of pistils/hairs or flower sites. "
                                "Be specific and concrete.",
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

            lowered = morphology_text.lower()
            is_decline = any(k in lowered for k in DECLINE_KEYWORDS)

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
                        action = f"Transition to {inferred_stage}."
                        confidence = "high" if method == "keyword" else "medium"
                        transition_result = self.handle_task("transition_stage", {
                            "plant_id": plant_id, "new_stage": inferred_stage,
                            "notes": f"Auto-transitioned from morphology evidence ({method}): {morphology_text}"
                        }, sender)
                        transitioned = transition_result.get("transition")
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
                "id": f"stage_eval_{int(time.time())}",
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
