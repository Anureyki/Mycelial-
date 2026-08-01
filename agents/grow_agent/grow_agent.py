#!/usr/bin/env python3
import sys
import os
import time
import json
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class GrowAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="grow_agent",
            port=9009,
            capabilities=[
                "log_reading", "check_stage", "adjust_nutrients",
                "transition_stage", "log_water_change", "get_status",
                "set_germination_date", "add_reminder", "list_reminders",
                "web_search"
            ],
            role="gardener"
        )
        self.log("🌱 Grow Agent started.")

    # ---------- Reminder storage helpers ----------
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

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "log_reading":
            reading = {
                "timestamp": datetime.now().isoformat(),
                "ph": args.get("ph"),
                "ppm": args.get("ppm"),
                "ec": args.get("ec"),
                "temp": args.get("temp"),
                "humidity": args.get("humidity"),
                "stage": args.get("stage", "seedling")
            }
            self.store_own_memory(f"reading_{int(time.time())}", json.dumps(reading))
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

        # ----- NEW TASKS -----
        elif task == "transition_stage":
            new_stage = args.get("new_stage")
            notes = args.get("notes", "")
            if not new_stage:
                return {"error": "Missing new_stage"}
            # Store the transition in memory
            transition = {
                "timestamp": datetime.now().isoformat(),
                "new_stage": new_stage,
                "notes": notes,
                "previous_stage": self.retrieve_own_memory("current_stage") or "unknown"
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
            pending_reminders = [
                r for r in self._get_all_reminders() if r.get("status") == "pending"
            ]
            return {
                "result": {
                    "current_stage": stage,
                    "germination_date": germination_date,
                    "current_strain": strain,
                    "pending_reminders": pending_reminders,
                    "last_reading": "Use log_reading to add a new reading",
                    "last_water_change": "Use log_water_change to log a water change"
                }
            }

        elif task == "set_germination_date":
            date_str = args.get("date")
            strain = args.get("strain", "")
            if not date_str:
                return {"error": "Missing date"}
            self.store_own_memory("germination_date", date_str)
            if strain:
                self.store_own_memory("current_strain", strain)
            return {"result": f"Germination date set to {date_str}", "strain": strain}

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

        elif task == "web_search":
            query = args.get("query") if isinstance(args, dict) else args[0] if args else None
            if not query:
                return {"error": "Missing query"}
            return self.search_public(query)

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = GrowAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
