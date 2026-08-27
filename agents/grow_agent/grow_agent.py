#!/usr/bin/env python3
import sys
import os
import time
import subprocess
import json
import math
import re
import requests
from datetime import datetime, timedelta

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

# Try to import numpy for regression
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# Perception pipeline (YOLO + ViT + OCR, fused). Deliberately NOT imported here.
#
# `import plant_perception` pulls in torch and ultralytics, which cost ~860MB of
# RSS on their own - before any model loads and whether or not a photo ever
# arrives. Importing it at module scope made this agent the largest process on
# the box by a factor of five, permanently, because Python cannot unload a
# module once imported. Lazy-importing would only move that cost to the first
# photo and then keep it forever.
#
# Instead perception runs as a short-lived subprocess that exits, so the memory
# is returned in full. See the __main__ block in services/vision/plant_perception.py.
VISION_SCRIPT = os.path.join(project_root, "services", "vision", "plant_perception.py")
VISION_AVAILABLE = os.path.exists(VISION_SCRIPT)
VISION_TIMEOUT = int(os.getenv("VISION_TIMEOUT", "300"))
LOW_CONFIDENCE_THRESHOLD = 0.55

try:
    # Full package path. This was a bare "from dataset_inventory import ..."
    # and only project_root is on sys.path, so it always raised ImportError and
    # DATASET_TOOLS_AVAILABLE was always False. The counter that decides
    # campaign progress therefore returned {} forever: every label read as 0
    # however many images were on disk, and the status looked like "nothing
    # collected yet" rather than "the counter is broken".
    from services.vision.dataset_inventory import (
        scan as scan_training_set, MIN_PER_CLASS, TRAINING_DIR)
    DATASET_TOOLS_AVAILABLE = True
except ImportError as _e:
    DATASET_TOOLS_AVAILABLE = False
    _DATASET_IMPORT_ERROR = str(_e)
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
# How a symptom is DISTRIBUTED across a leaf is what separates its causes.
# The keyword buckets below classify on naming instead: any mention of "yellow"
# read as senescence, and a pest was only found if the describer already used
# the word "pest". So "fine pale-yellow stippling scattered across the blade" -
# the textbook early sign of spider mites, and one of this campaign's own
# labels - came back as "yellowing consistent with natural senescence; plant
# reallocating resources", which is the opposite of the right answer and would
# have cost the grower the window in which mites are cheap to stop.
#
# Pattern is checked BEFORE the buckets. None of these assert a cause: each
# names what the pattern is consistent with and the one observation that would
# settle it, because a leaf photo cannot distinguish mites from thrips and
# should not pretend to.
LEAF_PATTERNS = (
    ("stippling",
     r"stipple|stippl\w*|speckl\w*|tiny (pale|white|yellow|light)? ?(dots|spots|specks)|"
     r"pinprick|pin-?head|flecking|silvering|bronz\w+|sand-?blasted",
     "spider mites or thrips feeding on the underside and puncturing cells one at a time",
     "Turn the leaf over and look at the underside with a loupe or a phone macro: mites "
     "show as moving specks and fine webbing in the vein junctions, thrips as black "
     "frass and silvery scarring. Check the newest growth too.",
     "problem"),
    ("interveinal",
     r"interveinal|between the veins|veins? (stay|remain|still) green|green veins",
     "a mobile-nutrient deficiency - magnesium and iron present this way, and this grow "
     "runs distilled water with no calcium or magnesium of its own",
     "Which leaves: mobile nutrients pull from the OLDEST growth first, immobile ones "
     "show on the newest. Note where it started before dosing.",
     "problem"),
    ("margin_burn",
     r"tip burn|burnt tips|crispy (tips|edges|margins)|margins? (browning|burn|scorch)|"
     r"edges? (curl|burn|brown)",
     "too much salt at the root or too much light at the canopy - nutrient burn works "
     "inward from the tip, light burn shows on whatever sits closest to the fixture",
     "Compare a leaf under the light with one in shade at the same height. If only the "
     "top ones are affected it is the light, not the feed.",
     "problem"),
    ("powder",
     r"powder\w*|white (dust|film|coating|patches)|fuzzy (white|grey|gray)",
     "powdery mildew, which spreads on humidity and still air rather than on contact",
     "Try to wipe it off. Mildew smears and returns; mineral residue from spray or hard "
     "water comes away and does not.",
     "problem"),
    ("uniform_lower_yellow",
     r"(lower|bottom|oldest) leaves? (yellow|fading|pale)|whole leaf (yellow|pale)|"
     r"uniform\w* yellow",
     "natural senescence or a nitrogen draw, both of which start on the oldest growth",
     "If it is confined to the lowest leaves and the top is deep green, it is the plant "
     "reallocating and needs nothing.",
     "senescent"),
)


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

# analyze_consumption resolution limits. Uptake is a slow signal: a 15L
# reservoir loses a few percent a day even under heavy draw, and volume read off
# an unmarked sight tube is good to maybe +/-10%. So a short window or a small
# delta cannot distinguish uptake from measurement error, and reporting
# "balanced" over six hours is false confidence rather than a finding.
MIN_CONSUMPTION_WINDOW_HOURS = 24
CONSUMPTION_NOISE_FLOOR_PCT = 5.0

# Extra nitrogen emphasis while regrowing after capacity was removed.
REGROWTH_N_BOOST = 1.25

# What the agent needs to know, how often, and why. The point is not to poll the
# grower daily - it is to know what is missing when a decision actually depends
# on it, and to ask then. Intervals shorten as biomass grows because consumption
# is non-linear: a weekly check is ample when the plant is small relative to the
# reservoir and leaves it starving for days once the root mass fills it.
# How long a recorded value stays USABLE before it needs re-asking.
#
# Distinct from how often a reading is wanted. The check-in previously looked
# only at the most recent reading, so a value logged two days ago at a reservoir
# change read as "missing" the moment a reading came in without it - and the
# grower got asked for the volume a day after setting it themselves.
#
# The horizons come from how fast each quantity can actually move:
#   volume - a 15L reservoir loses single-digit percent a day even under heavy
#     draw, which is inside the +/-10% a sight tube can be read to. Asking daily
#     is asking below the resolution of the answer. Resets on a water change.
#   humidity - moves with the room, but slowly when nothing is actively
#     humidifying, and it is not what a feed decision turns on.
#   ph, ppm, temp - genuinely move day to day. That is why they are measured.
PARAM_STALENESS_DAYS = {
    "volume_liters": 7,
    "humidity": 3,
    "temp": 2,
    "ph": 2,
    "ppm": 2,
}

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
                     # The trap that separates this from a plain DWC.
                     "strength_reaches_roots_immediately": True,
                     "concentration_note": (
                         "The top feed draws from the SAME reservoir, so raising reservoir "
                         "strength sprays that strength straight onto the root mass in the "
                         "medium. There is no grace period while roots grow down - in a plain "
                         "DWC the reservoir only reaches roots that reach it, and a top feed "
                         "removes that gap by design. The roots in medium are the MOST exposed "
                         "part of the plant, not the least."),
                     "buffering": (
                         "Clay pebbles have almost no cation exchange capacity, so nothing "
                         "moderates what arrives - unlike soil, the roots see exactly what is "
                         "sprayed. And between sprays the WATER evaporates off the pebble while "
                         "the dissolved nutrient stays behind, so what is left clinging to the "
                         "pebble is the same nutrient in less water - a higher concentration at "
                         "the root surface than the reservoir reads. Nothing is added; only "
                         "water leaves."),
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
        "stages": ("germination", "seedling", "early_veg", "veg", "flower"),
        "default_stage": "seedling",
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

# ---------------------------------------------------------------------------
# Prediction scoring - closing the loop between a claim and what happened.
#
# reasoning_context already records expected_effect at the moment a decision is
# made: a falsifiable claim, timestamped, with the reasoning and confidence
# attached. Nothing ever read it back. The agent stated testable claims and
# never graded itself, which is the difference between a system that keeps
# records and one that learns from them.
#
# Assertions are extracted DETERMINISTICALLY from the prediction text. A stated
# range like "600-900 ppm" is machine-checkable without a model, which matters
# on hardware where an inference call costs a minute or more - and a regex does
# not hallucinate agreement between a claim and a reading.
#
# The epistemic rules are the same ones the rest of this agent already follows:
#   - no observation after the prediction is UNDETERMINED, never a failure
#   - a reading within measurement noise of a boundary is INCONCLUSIVE, not a
#     pass and not a fail
#   - a prediction with no checkable assertion is UNSCORABLE, and saying so is
#     useful feedback: it means the claim was written unfalsifiably
# ---------------------------------------------------------------------------

# Instrument precision, from the meters actually in use. A reading this close to
# a stated boundary cannot decide the question either way.
# Below this many decided predictions, a hit rate is a description of those
# predictions rather than a measure of the agent's reliability.
MIN_PREDICTIONS_FOR_RELIABILITY = 5

MEASUREMENT_NOISE = {"ppm": 25.0, "ph": 0.1, "temp": 0.5, "ec": 0.05, "humidity": 3.0}

_RANGE_RE = re.compile(
    # The gap between a metric word and its numbers: "pH should hold 5.8-6.2"
    # needs 13 characters, so a 12-character window silently dropped it. Wide
    # enough for a natural clause, still narrow enough that the metric and the
    # number have to belong to the same phrase.
    r'(?:(?P<lead>ph|ppm|ec|temp|temperature|humidity)[^\d\n]{0,24})?'
    r'(?P<lo>\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?P<hi>\d+(?:\.\d+)?)'
    r'\s*(?P<trail>ppm|ph|ec|%|c\b|celsius)?', re.I)
_BOUND_RE = re.compile(
    r'(?P<dir>above|below|under|over|at least|no more than|at most)\s+'
    r'(?:ph\s*)?(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>ppm|ph|ec|%|c\b|celsius)?', re.I)

def _canonical_metric(*candidates):
    for c in candidates:
        if not c:
            continue
        c = c.strip().lower()
        if c in ("temperature", "c", "celsius"):
            return "temp"
        if c == "%":
            return "humidity"
        if c in ("ppm", "ph", "ec", "temp", "humidity"):
            return c
    return None


# ---------------------------------------------------------------------------
# Universal plant care - the layer under cultivation.
#
# This agent grew up around one cannabis plant in a reservoir, so everything
# assumed a cultivar with stages, a nutrient schedule and a solution to sample.
# That is the DEEP tier and it is right where it applies. But most plants a
# person owns are not being cultivated to harvest: an aloe, a San Pedro, a
# houseplant that is quietly dying. Those need "it is shrivelled and the soil is
# bone dry", not a flowering schedule.
#
# The refusal to assess a species the disease models have no class for confused
# two different questions. PlantVillage having no aloe class means it cannot name
# an aloe PATHOGEN. It says nothing about whether a plant is browning, wilting,
# etiolated or rotting - those are visible on any plant, and a general vision
# model can describe them. What turns a description into advice is knowing what
# is NORMAL for that species, which is a small table, not a trained model.
#
# Three tiers, deliberately:
#   universal  - water, light, drainage. Every plant.
#   profile    - what normal looks like for this species. A dozen fields.
#   cultivar   - stages, feed schedules, harvest. Only for what is cultivated.
# ---------------------------------------------------------------------------

# What "wrong" looks like, independent of species. The interpretation of each
# sign depends on the profile below - a shrivelled succulent means something
# very different from a shrivelled fern.
CARE_SIGNS = {
    "underwatered": ("shrivel", "shriveled", "shrivelled", "wrinkled", "puckered",
                     "limp", "drooping", "wilting", "wilted", "crispy", "dry soil",
                     "curling inward", "thin"),
    "overwatered":  ("mushy", "soft", "translucent", "yellowing at the base", "soggy",
                     "waterlogged", "rot", "rotting", "black at the base", "smells"),
    # Splaying flat and spreading outward is the classic low-light response in a
    # rosette succulent - the plant flattens to present more surface to weak
    # light. It was filed under thirst severity, which read a light problem as a
    # water problem on a real aloe kept in a kitchen with only ambient spill from
    # a grow tent nearby.
    "light_starved": ("leggy", "etiolated", "stretching", "stretched", "pale",
                      "splayed flat", "splaying", "flattened outward", "spreading outward",
                      "lying flat", "opening outward",
                      "reaching", "leaning toward", "elongated", "spindly"),
    "light_burned": ("bleached", "white patches", "sunburn", "scorched", "reddish",
                     "purple tinge", "brown tips"),
    "cold_damage": ("black spots", "mushy tips", "translucent patches"),
    "nutrient_poor": ("pale overall", "yellowing older leaves", "small new growth",
                      "stunted"),
}

# Cues that a sign has gone PAST its early stage. The first real test of this
# layer read an aloe with papery dead leaves as "the normal first sign of thirst,
# easily corrected" - right about the direction, wrong about how far along it
# was, and it therefore recommended nothing. A sign and its severity are
# different facts and the advice depends on both.
SEVERITY_CUES = {
    "advanced": ("papery", "dried", "dead", "crispy", "brittle", "shrivelled beyond",
                 "collapsed", "brown and dry",
                 "leaf litter", "desiccated", "several leaves lost", "hollow"),
    "widespread": ("most leaves", "across most", "all the leaves", "whole plant",
                   "throughout", "every leaf"),
}


# Small, honest profiles. Each says what normal is, what usually kills this
# plant, and how to read an ambiguous sign. Everything here is stated as
# reference, and observation of the actual plant outranks it.
SPECIES_PROFILES = {
    "aloe": {
        "temp_f_ok": (50, 85),
        "stages": ("establishing", "mature", "dormant", "flowering"),
        "default_stage": "mature",
        "common_name": "Aloe",
        "group": "succulent",
        "water": "Soak thoroughly, then let the soil dry COMPLETELY before watering again. Typically 2-3 weeks indoors.",
        "light": "Bright indirect to direct. Tolerates less, but stretches.",
        "soil": "Fast-draining, gritty. Never sitting in water.",
        "kills_it": "overwatered",
        "reads_differently": {
            "underwatered": ("Leaves thinning, curling inward or wrinkling is the NORMAL first sign "
                             "of thirst in a succulent and is easily corrected. This is the safe "
                             "direction to err in."),
            "light_starved": ("Splaying flat and spreading outward is how a rosette succulent reaches "
                              "for weak light - it flattens to present more surface. This does not "
                              "reverse on its own; the leaves already open stay open, and only new "
                              "growth comes in tighter once the light improves. Move it somewhere "
                              "brighter and expect the change to show over weeks, not days."),
            "overwatered": ("Mushy, translucent or yellowing leaves at the base is the dangerous "
                            "direction. Aloe rot moves fast and is usually fatal once it reaches "
                            "the crown. Stop watering, check drainage."),
        },
        "note": "Drought-adapted. When in doubt, wait. Far more aloes die from water than from thirst.",
    },
    "cactus": {
        "temp_f_ok": (45, 90),
        "stages": ("establishing", "mature", "dormant", "flowering"),
        "default_stage": "mature",
        "common_name": "Cactus (incl. San Pedro, peyote)",
        "group": "succulent",
        "water": "Heavy soak then complete dry-out. Much less in winter dormancy - some species none at all.",
        "light": "Bright. Acclimatise gradually to direct sun or it scorches.",
        "soil": "Mineral, very fast draining.",
        "kills_it": "overwatered",
        "reads_differently": {
            "underwatered": ("Ribbing tightening or the body puckering is normal thirst and reverses "
                             "within days of a soak."),
            "overwatered": ("Soft or discoloured base is basal rot and is usually terminal. "
                            "Columnar cacti like San Pedro show it at the soil line first."),
            "light_burned": ("A reddish or purple cast is often light stress rather than disease, "
                             "and is common after a move to stronger light."),
            "light_starved": ("A cactus reaching for light narrows at the growing tip - new growth "
                              "thinner than the body below it. That taper is permanent; only the "
                              "growth after the move comes in at full width."),
        },
        "note": "Slow growing - a change over days is unusual, so anything sudden is worth attention.",
    },
    "succulent": {
        "temp_f_ok": (50, 85),
        "stages": ("establishing", "mature", "dormant", "flowering"),
        "default_stage": "mature",
        "common_name": "Succulent (general)",
        "group": "succulent",
        "water": "Soak and dry completely between waterings.",
        "light": "Bright.",
        "soil": "Fast draining.",
        "kills_it": "overwatered",
        "note": "Stores water in its tissue, so it shows thirst late and rot early.",
    },
    "tropical_foliage": {
        "temp_f_ok": (60, 85),
        "stages": ("establishing", "mature"),
        "default_stage": "mature",
        "common_name": "Tropical foliage houseplant",
        "group": "foliage",
        "water": "Keep evenly moist, let the top inch dry. Do NOT let it dry out completely.",
        "light": "Bright indirect. Direct sun scorches.",
        "soil": "Retentive but draining.",
        "kills_it": "underwatered",
        "note": "Opposite regime to a succulent - drying out fully is damage here, not discipline.",
    },
    "cannabis": {
        "common_name": "Cannabis",
        "group": "cultivar",
        "water": "Depends on the system - see the grow system record.",
        "light": "High. Stage-dependent schedule.",
        "soil": "System-dependent.",
        "kills_it": "varies",
        "note": "Actively cultivated here - the full stage, feed and harvest model applies.",
        "cultivated": True,
    },
}

SPECIES_ALIASES = {
    "aloe vera": "aloe", "aloe": "aloe",
    "san pedro": "cactus", "peyote": "cactus", "cactus": "cactus",
    "echinopsis": "cactus", "lophophora": "cactus", "trichocereus": "cactus",
    "jade": "succulent", "echeveria": "succulent", "haworthia": "succulent",
    "succulent": "succulent",
    "pothos": "tropical_foliage", "monstera": "tropical_foliage",
    "philodendron": "tropical_foliage", "fern": "tropical_foliage",
    "houseplant": "tropical_foliage",
    "cannabis": "cannabis", "marijuana": "cannabis", "hemp": "cannabis",
}


# Target ranges per stage. Hoisted out of check_stage, which only ever answered
# "what should this stage be?" when asked - it never compared the answer against
# what was actually in the reservoir.
#
# That gap cost a real grow. Days 4 to 23 sat at 366-404 ppm while the plant
# moved through early_veg into veg, which wants 600-900. Every number needed to
# catch it was already recorded: the stage, the band, the reading. Nothing put
# them together and said so, and a first-time grower had no way to know the
# target had moved out from under a recipe that had not changed.
STAGE_TARGETS = {
    "germination": {"ph": (5.8, 6.2), "ppm": (100, 300), "ec": (0.2, 0.6)},
    "seedling":    {"ph": (5.8, 6.0), "ppm": (200, 400), "ec": (0.4, 0.8)},
    "early_veg":   {"ph": (5.8, 6.2), "ppm": (400, 600), "ec": (0.8, 1.2)},
    "veg":         {"ph": (5.8, 6.2), "ppm": (600, 900), "ec": (1.2, 1.8)},
    "flower":      {"ph": (5.8, 6.2), "ppm": (800, 1200), "ec": (1.6, 2.4)},
}

# How long a reading may sit outside its band before this stops being a note and
# becomes the headline. Below-target is the one that compounds silently: the
# plant does not wilt, it just builds less, and on an autoflower that time is
# never recovered because the clock does not wait.
DRIFT_PATIENCE_DAYS = 3


# A plant's ID is permanent; its LABEL is positional and computed.
#
# "Plant one" is not a name, it is a position, and positions move: plant one is
# harvested, plant two goes to a brother, a new seed germinates. The identity
# that history hangs off - readings, recipes, photos, lessons - has to survive
# all of that, so the id assigned at germination is never reused and never
# renumbered. What the grower calls it is worked out at question time from the
# plants that are still growing.
PLANT_STATUSES = ("active", "harvested", "gifted", "dead", "removed")


# Constants the agent can REPLACE with its own measurements.
#
# Every default in this file is somebody's guess - mine, mostly, informed by
# general horticulture rather than by this reservoir, this strain, this room.
# That is the difference between an agent that accumulates what it was told and
# one that learns: the second replaces the guess with what it measured, without
# anyone deciding for it what the new number should be.
#
# min_samples is the honesty gate. A rate computed from two readings is not a
# rate, and adopting it would be exactly the false confidence the resolution
# guards exist to prevent.
LEARNABLE = {
    "ppm_drift_per_day": {
        "default_from": "STAGE_PROFILES[stage]['expected_ppm_drift_per_day']",
        "min_samples": 4,
        "why": ("How fast THIS plant in THIS reservoir actually draws the solution down. The "
                "default is a generic figure per stage; the real one depends on root mass, "
                "reservoir volume, temperature and the plant itself."),
    },
    "ppm_measurement_noise": {
        "default_from": "MEASUREMENT_NOISE['ppm']",
        "min_samples": 3,
        "why": ("How much this meter and this grower's technique actually vary between readings "
                "taken close together. The +/-25 default is a guess about the instrument; the "
                "real figure is measurable from readings minutes apart, where nothing can have "
                "changed."),
    },
    "dose_response_factor": {
        "default_from": "1.0 (assumes scaling nutrients scales ppm linearly)",
        "min_samples": 3,
        "why": ("adjust_to_target_ppm assumes scaling the recipe by X scales ppm by X. Whether "
                "that holds for this water and these products is checkable: predict the ppm, "
                "measure it, and learn the correction."),
    },
}


# Sensor ingestion.
#
# The grower is a submarine veteran. Taking readings by hand is the thing this
# system exists to stop, not a habit to be nagged into. Bursty manual logs are
# not carelessness - they are what happens when someone makes themselves do a
# task they left a career to get away from.
#
# So the cadence problem gets solved by removing the human from it. Everything
# the learning layer is starved of - consumption rate, meter noise, gap-free
# deficit periods - is trivially available from a probe reporting hourly.
#
# Topic:   mycelial/sensor/<sensor_id>/reading
# Payload: {"ppm": 688, "ph": 6.15, "temp_c": 19.7, "volume_liters": 14.9,
#           "humidity": 54, "plant_id": "current_plant"}
#
# Raw samples are kept for the learning layer, which WANTS density - noise is
# measurable precisely because consecutive samples cannot differ for real
# reasons. What gets written as a logged reading is an aggregate, because an
# hourly ppm row is below the resolution of every question asked of it and would
# bury the record in rows that cannot support a conclusion.
SENSOR_TOPIC = "mycelial/sensor/+/reading"
SENSOR_AGGREGATE_HOURS = 6      # how often raw samples become a logged reading
SENSOR_BUFFER_MAX = 500         # rolling raw sample cap


STOP_TERMS = {"the", "and", "auto", "autoflower", "plant", "current", "cannabis",
              "test", "vera", "unknown", "none"}


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
                "set_inventory", "get_inventory",
                "check_in", "analyze_consumption", "adjust_to_target_ppm",
                "training_quest_status", "source_training_candidates",
                "advance_training_campaign",
                "review_training_candidate", "list_training_candidates",
                "remove_plant", "list_vision_corrections", "recommend_purchase",
                "web_search",
                "prepare_dataset", "fit_linear_model", "predict_linear"
            ],
            role="gardener"
        )
        # Listen for probe traffic. Costs nothing when no sensor is publishing.
        self.enable_sensor_ingest()
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

    # Evidence taxonomy - what KIND of record this is. Distinguishing a
    # correction from a deliberate change is not cosmetic: the week-1 recipe on
    # plant #1 was a dilution fixing an over-concentration, but nothing marked it
    # as such, so the concentration-lag detector read it as an intended baseline
    # and reported the later recipe as a decline when it was a return to normal.
    EVIDENCE_KINDS = ("fact", "event", "reasoning", "note", "assessment", "correction")

    def _reasoning_context(self, args):
        """Optional causal context attached to a domain event. Structured state
        answers WHAT; this answers WHY, what was expected, and what it relates
        to - which otherwise only ever lives in free-text notes and gets lost to
        anything reasoning over the record."""
        if not isinstance(args, dict):
            return None
        ctx = {
            "reason": args.get("reason"),
            "observed_conditions": args.get("observed_conditions"),
            "decision": args.get("decision"),
            "expected_effect": args.get("expected_effect"),
            "confidence": args.get("confidence_note") or args.get("context_confidence"),
            "related_events": args.get("related_events") or [],
            # A correction says a previous record was wrong. Anything reading
            # history must be able to tell that apart from a normal change.
            "corrects": args.get("corrects"),
            "supersedes": args.get("supersedes"),
        }
        if not any(v for v in ctx.values()):
            return None
        kind = (args.get("evidence_kind") or "").lower()
        if kind and kind not in self.EVIDENCE_KINDS:
            kind = None
        ctx["evidence_kind"] = kind or ("correction" if (ctx["corrects"] or ctx["supersedes"]) else "event")
        ctx["source"] = args.get("source", "user")
        return {k: v for k, v in ctx.items() if v not in (None, [], "")}

    def _load_inventory_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("inventory_index"))
        if not raw:
            return []
        try:
            idx = json.loads(raw)
            return idx if isinstance(idx, list) else []
        except Exception:
            return []

    @staticmethod
    def _nutrient_keys(plant_id):
        """Per-plant nutrient storage. These were single global keys, so setting
        a recipe for a second plant overwrote the first one's and interleaved
        their histories - which would have made the concentration-lag detector
        compare a seedling against a veg plant. current_plant keeps the original
        key names so existing records stay readable."""
        if not plant_id or plant_id == "current_plant":
            return "current_nutrients", "nutrient_change_index"
        return f"current_nutrients_{plant_id}", f"nutrient_change_index_{plant_id}"

    def _load_nutrient_history_index(self, plant_id="current_plant"):
        _, index_key = self._nutrient_keys(plant_id)
        raw = self._unwrap_value(self.retrieve_own_memory(index_key))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_nutrient_history(self, plant_id="current_plant"):
        out = []
        for k in self._load_nutrient_history_index(plant_id):
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

    def _detect_lagging_nutrients(self, threshold_ratio=0.5, plant_id="current_plant"):
        """Find components that have not scaled with the rest of the recipe.

        Stage multipliers are applied to whatever is currently recorded, which
        assumes the current recipe was right for the previous stage. When one
        component has been left untouched while the others moved, multiplying it
        carries the lag forward instead of correcting it - which is how Cal-Mag
        sat at the same dose from week 1 to week 4 while everything else rose and
        the plant doubled. Returns {nutrient: {...}} for components whose growth
        is under threshold_ratio of the median growth across the recipe."""
        history = self._get_nutrient_history(plant_id)
        # A recipe recorded as a CORRECTION is not a baseline - it is a mistake
        # being undone. Comparing against it inverts the finding: plant #1's
        # week-1 entry was a dilution fixing an over-concentration that had
        # burned the roots, and reading it as intended made the later, normal
        # recipe look like a decline in strength.
        baseline_history = [
            h for h in history
            if (h.get("reasoning_context") or {}).get("evidence_kind") != "correction"
        ]
        # A correction must not serve as the BASELINE - but it is exactly what
        # should serve as CURRENT, because a correction of the present state IS
        # the present state. Excluding it from both ends meant a voided entry
        # stayed in force as "current": a test write that never went into the
        # reservoir kept being read as the live recipe even after a correction
        # explicitly voided it, and the lag it was masking silently disappeared.
        latest_any = history[-1] if history else None
        if len(baseline_history) >= 2:
            history = baseline_history
        elif len(history) >= 2:
            # Only corrections available - comparison would be misleading.
            return {}
        if len(history) < 2:
            return {}
        if latest_any is not None and latest_any is not history[-1]:
            history = history[:-1] + [latest_any] if len(history) > 1 else [latest_any]
        # Compare CONCENTRATION, not raw millilitres. Reservoir volume changes
        # between recipes, so raw ml is meaningless: 2.5ml in 3.5L is a stronger
        # feed than 3.0ml in 5L, and comparing the numbers alone reported that as
        # a 20% increase when the concentration actually fell 16%.
        first = history[0].get("per_liter") or history[0].get("nutrients", {})
        current = history[-1].get("per_liter") or history[-1].get("nutrients", {})
        if not history[0].get("per_liter") or not history[-1].get("per_liter"):
            # Without per-litre on both ends the comparison cannot be trusted.
            return {}
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
            # The recipe as a whole did not scale - concentration held flat or
            # fell while the plant grew. That is a stronger version of the same
            # problem, not an absence of one, and returning nothing here hid it:
            # this grow went seedling -> veg while per-litre strength dropped 16%
            # on the nitrogen carriers and 30% on Cal-Mag. Flag every component
            # that fell, and target the flat case at the stage multiplier alone.
            lagging = {}
            for name, g in growth.items():
                if g < 0:
                    lagging[name] = {
                        "growth_pct": round(g * 100, 1),
                        "median_growth_pct": round(median * 100, 1),
                        "catchup_multiplier": round(1.0 / (1 + g), 3),
                        "since": history[0].get("timestamp"),
                        "whole_recipe_regressed": True,
                    }
            return lagging

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

    def _run_perception(self, images, species=None):
        """Run the perception pipeline out of process and return
        {image_path: fused_observation}. Empty dict on any failure - callers
        already handle a missing fused read by falling back to text symptoms."""
        if isinstance(images, str):
            images = [images]
        if not VISION_AVAILABLE or not images:
            return {}
        try:
            proc = subprocess.run(
                [sys.executable, VISION_SCRIPT],
                input=json.dumps({"images": images, "species": species}),
                capture_output=True, text=True, timeout=VISION_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            self.log(f"perception timed out after {VISION_TIMEOUT}s for {images}", "ERROR")
            return {}
        except Exception as e:
            self.log(f"perception subprocess failed: {e}", "ERROR")
            return {}
        if proc.returncode != 0:
            self.log(f"perception exited {proc.returncode}: {proc.stderr[-400:]}", "ERROR")
            return {}
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1]).get("results", {})
        except Exception as e:
            self.log(f"perception returned unparseable output: {e}", "ERROR")
            return {}

    def _fuse_one(self, image_path, species=None):
        """Single-image convenience wrapper. Prefer _run_perception for batches -
        each call pays process start plus a ~5s import."""
        return self._run_perception([image_path], species=species).get(image_path)

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

    # Roughly how long each stage lasts from germination, for autoflowers.
    # Used ONLY to detect a contradiction, never to advance a stage on its own -
    # the plant decides when it transitions, not the calendar. See
    # verify_growth_stage, which reads morphology.
    STAGE_AGE_BOUNDS = {"germination": (0, 7), "seedling": (0, 21),
                        "veg": (14, 70), "flower": (35, 130)}

    # Stage progression order. A stage is never skipped backwards automatically.
    STAGE_ORDER = ("germination", "seedling", "early_veg", "veg", "flower")

    def assess_stage(self, plant_id="current_plant"):
        """Decide the stage from evidence, without waiting to be asked.

        The agent owns this. It had 25 days of readings and several photos of a
        plant with 7-9 blade fan leaves and still had "seedling" recorded,
        because nothing ever concluded a stage from evidence - the only
        auto-transition path fired when someone happened to pass a different
        stage to set_current_nutrients, and verify_growth_stage explicitly never
        transitions. So the stage only moved when a human noticed.

        Two different situations, treated differently on purpose:

        IMPOSSIBLE - the recorded stage cannot be true at this age. Seedling ends
        around 21 days; at 25 the label is simply wrong, and that is arithmetic
        rather than judgment. Staying at a provably wrong stage is worse than
        moving, because stage drives the nutrient band, so this transitions
        itself and records why.

        LIKELY - the age suggests a further stage but the current one is still
        possible. That is a judgment about the plant, not the calendar, and a
        plant can be stunted or running slow. This only recommends, and asks for
        the morphology that would settle it.

        Flower is never entered on age alone. Pistils are the trigger, and this
        function cannot see them."""
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        germ = self._unwrap_value(self.retrieve_own_memory("germination_date"))
        if isinstance(germ, str):
            germ = germ.strip().strip('"')
        if plant_id != "current_plant":
            plant = next((p for p in self._get_all_plants()
                          if p.get("plant_id") == plant_id), None)
            if not plant:
                return {"error": f"Unknown plant_id: {plant_id}"}
            stage, germ = plant.get("stage", "unknown"), plant.get("germination_date")
        if not germ:
            return {"assessment": "no germination date recorded - cannot assess stage from age",
                    "stage": stage, "acted": False}
        try:
            age = (datetime.now() - datetime.fromisoformat(str(germ)[:19])).days
        except Exception:
            return {"assessment": "germination date unparseable", "stage": stage, "acted": False}

        # STAGE_AGE_BOUNDS is a cannabis clock - germination, seedling, veg,
        # flower, with autoflower timings. It means nothing for an aloe, a
        # pepper, or anything else, and applying it would produce confident
        # nonsense about a plant whose life cycle this agent has no model of.
        species = self._get_species_for_plant(plant_id)
        if species and species.lower() != "cannabis":
            return {"assessment": (f"No stage model for {species}. The stage clock here is "
                                   "cannabis-specific and does not transfer."),
                    "stage": stage, "days": age, "species": species, "acted": False}

        # A germination date inferred FROM the stage cannot then be evidence
        # ABOUT the stage - that is circular, and it would auto-transition an
        # acquired plant on the strength of a guess.
        estimated = False
        if plant_id != "current_plant":
            _r = self._unwrap_value(self.retrieve_own_memory(f"plant_{plant_id}"))
            try:
                estimated = bool(json.loads(_r).get("germination_date_estimated")) if _r else False
            except Exception:
                estimated = False

        bounds = self.STAGE_AGE_BOUNDS.get(str(stage).lower())
        impossible = bool(bounds) and age > bounds[1] and not estimated
        if estimated and bounds and age > bounds[1]:
            return {"assessment": (f"'{stage}' sits outside the usual window for {age} days, but "
                                   "that age is estimated rather than observed, so it is not "
                                   "grounds to move the stage."),
                    "stage": stage, "days": age, "age_estimated": True, "acted": False,
                    "resolve_with": "verify_growth_stage with a photo - morphology is observable, the germination date is not"}

        # The furthest stage the age alone supports, never past veg.
        candidate = stage
        for s in self.STAGE_ORDER:
            if s == "flower":
                break
            b = self.STAGE_AGE_BOUNDS.get(s)
            if b and b[0] <= age <= b[1]:
                candidate = s
        if candidate == stage and not impossible:
            return {"assessment": f"'{stage}' is consistent with {age} days", "stage": stage,
                    "days": age, "acted": False}

        evidence = [f"{age} days since germination on {str(germ)[:10]}"]
        if bounds:
            evidence.append(f"'{stage}' spans roughly {bounds[0]}-{bounds[1]} days")
        readings = self._get_readings_for_plant(plant_id)
        if readings:
            evidence.append(f"{len(readings)} readings logged, most recent "
                            f"{str(readings[-1].get('timestamp'))[:10]}")

        # Impossible, but with nowhere to go: past veg the only stage forward is
        # flower, and flower is never entered on age. Say so rather than
        # attempting a transition to the stage it is already in.
        if impossible and candidate == stage:
            return {"assessment": (f"'{stage}' is past its usual window at {age} days, but the only "
                                   "stage forward is flower and that needs pistils, which this "
                                   "cannot see."),
                    "stage": stage, "days": age, "evidence": evidence, "acted": False,
                    "resolve_with": "check the nodes for pistils; transition_stage to flower if present"}

        if not impossible:
            return {"assessment": f"'{candidate}' is likely but '{stage}' is still possible at {age} days",
                    "stage": stage, "suggested": candidate, "days": age,
                    "evidence": evidence, "acted": False,
                    "resolve_with": "verify_growth_stage with a photo - morphology settles it, not the calendar"}

        result = self.handle_task("transition_stage", {
            "plant_id": plant_id,
            "new_stage": candidate,
            "notes": (f"Auto-assessed. '{stage}' is not possible at {age} days "
                      f"(it spans about {bounds[0]}-{bounds[1]}). " + "; ".join(evidence)),
            "reason": f"Recorded stage '{stage}' contradicted the plant's age of {age} days.",
            "observed_conditions": "; ".join(evidence),
            "decision": f"Moved to '{candidate}' on age, which is the furthest stage the age alone supports.",
            "expected_effect": f"Readings and feed are now judged against the {candidate} band "
                               "instead of a stage the plant has outgrown.",
            "confidence_note": "medium - age is decisive that the old stage is wrong, "
                               "but morphology decides which stage is right",
            "evidence_kind": "correction",
            "corrects": f"current_stage={stage}",
        }, "grow_agent")
        target_shift = self.stage_target_change(stage, candidate)
        return {"assessment": f"transitioned '{stage}' -> '{candidate}'", "stage": candidate,
                **({"target_change": target_shift} if target_shift else {}),
                "was": stage, "days": age, "evidence": evidence, "acted": True,
                "note": ("Moved on age because the old stage was impossible. Confirm with "
                         "verify_growth_stage if the morphology says otherwise. Flower is never "
                         "entered on age - pistils are the trigger."),
                "transition": result.get("transition") if isinstance(result, dict) else None}

    def _stage_age_conflict(self, stage, germination_date):
        """Flag a stage that cannot be right for the plant's age.

        A reading logged against the wrong stage is not cosmetic: stage drives
        the nutrient targets, so a 25-day-old plant still recorded as a seedling
        gets judged against seedling ppm bands. This surfaces the contradiction
        rather than silently correcting it - which stage it actually is depends
        on morphology this function cannot see."""
        if not stage or not germination_date:
            return None
        try:
            age = (datetime.now() - datetime.fromisoformat(str(germination_date)[:19])).days
        except Exception:
            return None
        bounds = self.STAGE_AGE_BOUNDS.get(str(stage).lower())
        if not bounds or bounds[0] <= age <= bounds[1]:
            return None
        return {
            "stage_recorded": stage,
            "days_since_germination": age,
            "expected_for_stage": f"{bounds[0]}-{bounds[1]} days",
            "warning": (f"Recorded stage is '{stage}' but the plant is {age} days old. "
                        f"Readings are being logged against '{stage}' nutrient targets, "
                        f"which are probably the wrong band."),
            "resolve_with": "verify_growth_stage with a photo, or transition_stage if you know",
        }

    def _vision_prompt_for(self, plant_id="current_plant"):
        """Build the verification prompt from what this agent already knows.

        The prompt used to be "Describe this plant leaf health. Color, spots,
        damage, pests, disease signs." - no species, no strain, no age, no
        system. A small vision model asked a contextless question answers about a
        generic plant and fills the gaps with stereotype: a real upload came back
        describing a greenhouse and mesh material that are not in the photo, and
        "adequate sunlight and nutrients" it has no way to see. The agent knew it
        was a 25-day-old Girl Scout Cookies autoflower in deep water culture and
        said none of it.

        Flat sentences on purpose. moondream returns empty or degenerate
        completions for apostrophes, dash clauses and meta-instructions - verified
        reproducibly - so this stays punctuation-light."""
        bits = []
        try:
            plant = self._get_plant_record(plant_id) if hasattr(self, "_get_plant_record") else None
        except Exception:
            plant = None
        strain = species = germ = None
        if isinstance(plant, dict):
            strain, species, germ = plant.get("strain"), plant.get("species"), plant.get("germination_date")
        # Legacy single-plant fields describe current_plant ONLY. Falling back to
        # them for a different plant_id would label another plant with this one's
        # strain, age and growing system.
        if not strain and plant_id == "current_plant":
            strain = self._unwrap_value(self.retrieve_own_memory("current_strain"))
            if isinstance(strain, str):
                strain = strain.strip().strip('"')
        if not germ and plant_id == "current_plant":
            germ = self._unwrap_value(self.retrieve_own_memory("germination_date"))
            if isinstance(germ, str):
                germ = germ.strip().strip('"')
        # Never assert a species that was not recorded. An unknown plant gets a
        # neutral prompt; asserting the wrong one is how a confident wrong answer
        # gets manufactured.
        bits.append(f"This is a {species} plant." if species else "This is a plant.")
        if strain:
            bits.append(f"The strain is {re.sub(r'[^A-Za-z0-9 ()]', ' ', str(strain))}.")
        if germ:
            try:
                age = (datetime.now() - datetime.fromisoformat(str(germ)[:19])).days
                if 0 <= age < 400:
                    bits.append(f"It is {age} days old.")
            except Exception:
                pass
        try:
            sysinfo = (self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
                       or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                           if plant_id == "current_plant" else None))
            if sysinfo and "dwc" in str(sysinfo).lower():
                bits.append("It grows in deep water culture hydroponics.")
        except Exception:
            pass
        bits.append("Describe the leaf health. Color, spots, damage, pests, disease signs.")
        bits.append("Only describe what you can see in this photo.")
        return " ".join(bits)

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
            # A counter that cannot count must say so, not report zero.
            self.log(f"training counts unavailable: {globals().get('_DATASET_IMPORT_ERROR')}")
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

    # Phrases where a problem keyword appears in a PROTECTIVE or hypothetical
    # context rather than as an observed symptom. A vision model describing a
    # scene says things like "foil used to protect the plant from pests" - the
    # keyword is present, nothing is negated, and nothing is wrong. Matching it
    # produced "Recommend intervention or removal" on a plant it had just called
    # healthy. This is a mitigation, not a fix: the real answer is structured
    # observations from the perception layer instead of keyword-matched prose.
    PROTECTIVE_CUES = ("protect", "prevent", "guard against", "deter", "in case of",
                       "to avoid", "used against", "treatment for", "resistant to")

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
            if any(cue in clause for cue in self.PROTECTIVE_CUES):
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

    _learned_loaded = False

    def _profile_for(self, species):
        if not self._learned_loaded:
            self.__class__._learned_loaded = True
            try:
                self._load_learned_profiles()
            except Exception as e:
                self.log(f"could not restore learned profiles: {e}")
        return self.__profile_for(species)

    def __profile_for(self, species):
        """Species profile, or None. Falls back through the alias table so
        "Aloe vera", "San Pedro" and "Trichocereus" all resolve."""
        if not species:
            return None
        s = str(species).strip().lower()
        key = SPECIES_ALIASES.get(s)
        if not key:
            for alias, target in SPECIES_ALIASES.items():
                if alias in s:
                    key = target
                    break
        return SPECIES_PROFILES.get(key or s)

    # Targeted yes/no probes for the states that decide severity.
    #
    # Asked because open description does not get there. On a real aloe with
    # papery dead leaves the model produced "brown and yellow leaves, and it
    # appears to be wilting... there is a book next to it" - directionally right,
    # far too shallow to judge how far gone the plant was, and inventing
    # furniture. A 1B model is much better at answering "is there X in this
    # image" than at writing a rich description, so the severity cues are asked
    # for directly instead of hoped for.
    #
    # Flat sentences, no apostrophes or dash clauses - moondream returns empty or
    # degenerate completions on those.
    CARE_PROBES = (
        ("papery dried dead leaves", "Are any leaves completely dry, papery or dead?"),
        ("leaves splayed flat", "Are the leaves lying flat and splayed outward instead of upright?"),
        ("leaves thin deflated", "Do the leaves look thin and deflated instead of thick and plump?"),
        ("most leaves affected", "Is the discolouration on most of the plant or only a few leaves?"),
        ("dry soil", "Does the soil look dry?"),
        ("mushy soft base", "Is the base of the plant soft, mushy or dark?"),
    )

    def _probe_photo(self, photo_path, plant_id="current_plant"):
        """Ask the vision model directly about the states that decide severity.
        Returns text to append to whatever it said in open description."""
        if not photo_path or not VISION_AVAILABLE:
            return ""
        prefix = self._vision_prompt_for(plant_id).split("Describe")[0].strip()
        found = []
        for cue, question in self.CARE_PROBES:
            try:
                ans = self._call_inference_vision(f"{prefix} {question}", photo_path, timeout=90)
            except Exception:
                continue
            if not ans:
                continue
            a = ans.strip().lower()
            # Only a clear yes counts. "no", "not really" and an empty answer all
            # mean the cue is not established - absence of a yes is not a yes.
            if a.startswith("yes") or " yes" in a[:40]:
                found.append(cue)
            elif cue == "most leaves affected" and "most" in a[:60]:
                found.append("most leaves")
        return (" " + ", ".join(found) + ".") if found else ""

    def stages_for_species(self, species):
        """Lifecycle vocabulary for a species.

        germination/seedling/veg/flower is a cannabis clock. An aloe does not
        have a vegetative stage in that sense - it establishes, matures, may go
        dormant, and rarely flowers indoors. Registering the aloe as "veg" was
        cannabis vocabulary applied to a succulent, which is the same error as
        running its age through the cannabis stage bounds."""
        profile = self._profile_for(species)
        if profile and profile.get("stages"):
            return list(profile["stages"]), profile.get("default_stage")
        return list(STAGE_ORDER), "seedling"

    def _care_signs_in(self, text):
        """Which care signs a description mentions. Negation-aware, like the
        other classifiers here - "no sign of rot" must not read as rot."""
        if not text:
            return []
        found = []
        for sign, cues in CARE_SIGNS.items():
            if self._negation_aware_hit(text, cues):
                found.append(sign)
        return found

    def learn_species_profile(self, species, plant_id=None):
        """Look a species up and build a care profile for it, rather than
        needing one hand-written in advance.

        SPECIES_PROFILES was authored by hand, which does not scale and does not
        need to: this agent already has search. An unfamiliar plant should send
        it to look, not stop it.

        What comes back is REFERENCE and is labelled as such. It is generic
        advice written by strangers about a plant that is not this one, so it
        ranks below anything actually observed here - the same rule as a product
        label losing to a meter reading. A learned profile carries its queries
        and its source so the provenance travels with it, and never silently
        becomes indistinguishable from something measured."""
        if not species:
            return {"error": "No species given"}
        sp = str(species).strip()
        existing = self._profile_for(sp)
        if existing and not existing.get("learned"):
            return {"species": sp, "already_known": True,
                    "profile": existing.get("common_name"),
                    "note": "A hand-written profile already covers this species."}

        queries = [
            f"{sp} plant care watering light soil requirements",
            f"{sp} overwatering vs underwatering signs symptoms",
            f"{sp} temperature range minimum tolerance",
        ]
        findings = []
        for q in queries:
            try:
                r = self.search_public(q)
            except Exception as e:
                self.log(f"species lookup failed for '{q}': {e}")
                continue
            findings.append({"query": q, "result": json.dumps(r)[:1500]})
        if not findings:
            return {"species": sp, "learned": False,
                    "error": "Search returned nothing - cannot build a profile."}

        prompt = (
            "You are recording plant care facts for a reference table. From the search "
            "results below, state ONLY what is supported by them. Return a single JSON "
            "object with these keys and nothing else:\n"
            '{"common_name":"","group":"","water":"","light":"","soil":"",'
            '"kills_it":"","temp_f_min":0,"temp_f_max":0,"note":""}\n'
            '"group" is one of: succulent, cactus, foliage, grass, tree, herb, other.\n'
            '"kills_it" is whichever of overwatered or underwatered is the more common '
            "cause of death for this plant, or an empty string if that is not clear.\n"
            "Leave any field empty rather than guessing.\n\n"
            f"Plant: {sp}\n\nSearch results:\n" +
            "\n".join(f"[{f['query']}]\n{f['result']}" for f in findings) +
            "\n\nJSON:"
        )
        raw = self._call_inference_capability(prompt, capability="reasoning", timeout=240)
        parsed = None
        if raw:
            try:
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                parsed = json.loads(m.group(0)) if m else None
            except Exception:
                parsed = None
        if not isinstance(parsed, dict) or not parsed.get("water"):
            return {"species": sp, "learned": False,
                    "searched": [f["query"] for f in findings],
                    "error": ("Could not extract a usable profile from the results. "
                              "Better to have none than an invented one.")}

        profile = {
            "common_name": parsed.get("common_name") or sp.title(),
            "group": parsed.get("group") or "other",
            "water": parsed.get("water", ""),
            "light": parsed.get("light", ""),
            "soil": parsed.get("soil", ""),
            "kills_it": parsed.get("kills_it") or "",
            "note": parsed.get("note", ""),
            # Provenance travels with it. This is reference, not observation.
            "learned": True,
            "source": "web search, synthesised - REFERENCE only, outranked by anything observed here",
            "learned_at": datetime.now().isoformat(),
            "queries": [f["query"] for f in findings],
        }
        try:
            lo, hi = int(parsed.get("temp_f_min") or 0), int(parsed.get("temp_f_max") or 0)
            if 0 < lo < hi < 130:
                profile["temp_f_ok"] = (lo, hi)
        except Exception:
            pass

        key = sp.lower()
        SPECIES_PROFILES[key] = profile
        SPECIES_ALIASES[key] = key
        self.store_own_memory(f"species_profile_{key.replace(' ','_')}", json.dumps(profile))
        idx = self._unwrap_value(self.retrieve_own_memory("species_profile_index"))
        try:
            idx = json.loads(idx) if idx else []
        except Exception:
            idx = []
        if key not in idx:
            idx.append(key)
            self.store_own_memory("species_profile_index", json.dumps(idx))
        self.log(f"learned a care profile for {sp} from search")
        return {"species": sp, "learned": True, "profile": profile,
                "note": ("Stored as reference. Generic advice about a plant that is not this "
                         "one - anything observed here outranks it.")}

    def _load_learned_profiles(self):
        """Bring learned profiles back after a restart."""
        idx = self._unwrap_value(self.retrieve_own_memory("species_profile_index"))
        try:
            idx = json.loads(idx) if idx else []
        except Exception:
            return 0
        n = 0
        for key in idx:
            raw = self._unwrap_value(self.retrieve_own_memory(f"species_profile_{key.replace(' ','_')}"))
            if not raw:
                continue
            try:
                SPECIES_PROFILES[key] = json.loads(raw)
                SPECIES_ALIASES[key] = key
                n += 1
            except Exception:
                continue
        if n:
            self.log(f"restored {n} learned species profile(s)")
        return n

    def check_target_drift(self, plant_id="current_plant"):
        """Is the reservoir where this stage needs it to be?

        Answers the question the grower actually needed answered on day 15 -
        "you are past early veg now, the target moved, your recipe did not" -
        rather than the retrospective one."""
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        if plant_id != "current_plant":
            plant = next((p for p in self._get_all_plants()
                          if p.get("plant_id") == plant_id), None)
            if not plant:
                return {"error": f"Unknown plant_id: {plant_id}"}
            stage = plant.get("stage", "unknown")
            if (self._get_species_for_plant(plant_id) or "").lower() != "cannabis":
                return {"applicable": False,
                        "reason": "Target bands here are for cannabis in a reservoir."}
        target = STAGE_TARGETS.get(str(stage).lower())
        if not target:
            return {"applicable": False, "stage": stage,
                    "reason": f"No target band defined for stage '{stage}'."}

        readings = [r for r in self._get_readings_for_plant(plant_id)
                    if self._parse_numeric(r.get("ppm")) is not None]
        if not readings:
            return {"applicable": False, "stage": stage, "reason": "No ppm readings yet."}
        readings.sort(key=lambda r: r.get("timestamp") or "")
        latest = readings[-1]
        ppm = self._parse_numeric(latest.get("ppm"))
        lo, hi = target["ppm"]

        # How long has it been out of band, in the SAME direction?
        streak, first_out = 0, None
        for r in reversed(readings):
            v = self._parse_numeric(r.get("ppm"))
            if v is None:
                continue
            below, above = v < lo, v > hi
            if (ppm < lo and below) or (ppm > hi and above):
                streak += 1
                first_out = r.get("timestamp")
            else:
                break
        days_out = 0
        if first_out:
            try:
                days_out = (datetime.now() - datetime.fromisoformat(first_out[:19])).days
            except Exception:
                days_out = 0

        if lo <= ppm <= hi:
            return {"applicable": True, "stage": stage, "ppm": ppm, "target": [lo, hi],
                    "status": "in_band",
                    "message": f"{ppm:.0f} ppm is inside the {lo}-{hi} band for {stage}."}

        direction = "below" if ppm < lo else "above"
        gap = (lo - ppm) if ppm < lo else (ppm - hi)
        urgent = days_out >= DRIFT_PATIENCE_DAYS or streak >= 3
        msg = (f"{ppm:.0f} ppm is {gap:.0f} {direction} the {lo}-{hi} band that {stage} needs"
               + (f", and has been for {days_out} day(s) across {streak} reading(s)."
                  if days_out or streak > 1 else "."))
        if direction == "below":
            action = (f"Raise the feed toward {lo}-{hi} ppm. Below-target does not look like a "
                      "problem day to day - the plant does not wilt, it just builds less - and "
                      "on an autoflower that growth is not recovered later, because the clock "
                      "does not wait for it.")
        else:
            action = (f"Bring it down toward {lo}-{hi} ppm, by dilution rather than by waiting "
                      "for uptake. Over-strength shows up as tip burn and root damage.")
        return {"applicable": True, "stage": stage, "ppm": ppm, "target": [lo, hi],
                "status": f"{direction}_target", "gap_ppm": round(gap),
                "days_out_of_band": days_out, "readings_out_of_band": streak,
                "urgent": urgent, "message": msg, "action": action}

    def stage_target_change(self, old_stage, new_stage):
        """What a stage transition does to the target. Said at the moment the
        stage changes, because that is when the recipe needs to move and when a
        first-time grower has no reason to know it."""
        a, b = STAGE_TARGETS.get(str(old_stage).lower()), STAGE_TARGETS.get(str(new_stage).lower())
        if not b:
            return None
        if not a:
            return {"new_target": list(b["ppm"]),
                    "message": f"{new_stage} wants {b['ppm'][0]}-{b['ppm'][1]} ppm."}
        if a["ppm"] == b["ppm"]:
            return None
        return {"old_target": list(a["ppm"]), "new_target": list(b["ppm"]),
                "message": (f"Target moved with the stage: {old_stage} wanted "
                            f"{a['ppm'][0]}-{a['ppm'][1]} ppm, {new_stage} wants "
                            f"{b['ppm'][0]}-{b['ppm'][1]}. The recipe has to move with it - "
                            "a dose that was right last stage is under-feeding in this one.")}

    def _plant_photos(self, plant_id):
        """Photos attributable to this plant. Matched by the evaluation records
        that reference them, not by filename, since uploads are named by
        timestamp and carry no plant in the name."""
        paths = set()
        for key in ("leaf_eval_index", "reservoir_eval_index"):
            raw = self._unwrap_value(self.retrieve_own_memory(key))
            try:
                keys = json.loads(raw) if raw else []
            except Exception:
                continue
            for k in keys:
                r = self._unwrap_value(self.retrieve_own_memory(k))
                if not r:
                    continue
                try:
                    rec = json.loads(r)
                except Exception:
                    continue
                if rec.get("plant_id") != plant_id:
                    continue
                for p_ in (rec.get("photo_refs") or []):
                    if isinstance(p_, str):
                        paths.add(p_)
        return sorted(paths)

    # Roughly how far into life each stage sits, for estimating the age of a
    # plant that arrived already grown. Midpoints, and only ever used to produce
    # an ESTIMATE that is labelled as one.
    STAGE_AGE_MIDPOINT = {"germination": 3, "seedling": 12, "early_veg": 20,
                          "veg": 35, "flower": 60}

    def _learned_store(self):
        raw = self._unwrap_value(self.retrieve_own_memory("learned_constants"))
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def learned(self, name, stage=None, default=None):
        """The measured value if the agent has earned one, else the default.

        Callers do not need to know whether a number was learned - they ask for
        it and get the best available. What changes is that after enough
        observations the answer stops being mine."""
        store = self._learned_store()
        key = f"{name}:{stage}" if stage else name
        rec = store.get(key)
        if rec and rec.get("adopted"):
            return rec["value"]
        return default

    def learn_from_observations(self, plant_id="current_plant"):
        """Recompute what can be measured, and adopt it where the evidence
        supports it. Nothing here is told to the agent - it is derived."""
        readings = sorted([r for r in self._get_readings_for_plant(plant_id)
                           if r.get("timestamp")], key=lambda r: r["timestamp"])
        store = self._learned_store()
        results = {}

        # --- consumption rate, per stage -----------------------------------
        by_stage = {}
        for a, b in zip(readings, readings[1:]):
            pa, pb = self._parse_numeric(a.get("ppm")), self._parse_numeric(b.get("ppm"))
            if pa is None or pb is None:
                continue
            if (a.get("stage") or "") != (b.get("stage") or ""):
                continue          # a stage change is a different regime
            try:
                hrs = (datetime.fromisoformat(b["timestamp"][:19])
                       - datetime.fromisoformat(a["timestamp"][:19])).total_seconds() / 3600
            except Exception:
                continue
            # Below the consumption window the difference is noise, not uptake.
            if hrs < MIN_CONSUMPTION_WINDOW_HOURS:
                continue
            # A refill or a feed change is not consumption.
            if pb > pa:
                continue
            by_stage.setdefault(a.get("stage"), []).append((pa - pb) / (hrs / 24))

        for stage, rates in by_stage.items():
            spec = LEARNABLE["ppm_drift_per_day"]
            key = f"ppm_drift_per_day:{stage}"
            default = (STAGE_PROFILES.get(stage) or {}).get("expected_ppm_drift_per_day")
            enough = len(rates) >= spec["min_samples"]
            rates_sorted = sorted(rates)
            median = rates_sorted[len(rates_sorted) // 2]
            store[key] = {
                "value": round(median, 1), "samples": len(rates), "default": default,
                "adopted": enough,
                "note": (f"Measured from {len(rates)} interval(s) of at least "
                         f"{MIN_CONSUMPTION_WINDOW_HOURS}h in the same stage."
                         if enough else
                         f"{len(rates)} sample(s); needs {spec['min_samples']} before this "
                         "replaces the default. Reported, not used."),
            }
            results[key] = store[key]

        # --- meter noise, from readings too close together to have changed ---
        pairs = []
        for a, b in zip(readings, readings[1:]):
            pa, pb = self._parse_numeric(a.get("ppm")), self._parse_numeric(b.get("ppm"))
            if pa is None or pb is None:
                continue
            try:
                mins = (datetime.fromisoformat(b["timestamp"][:19])
                        - datetime.fromisoformat(a["timestamp"][:19])).total_seconds() / 60
            except Exception:
                continue
            if 0 < mins <= 20:
                pairs.append(abs(pa - pb))
        if pairs:
            spec = LEARNABLE["ppm_measurement_noise"]
            enough = len(pairs) >= spec["min_samples"]
            store["ppm_measurement_noise"] = {
                "value": round(max(pairs), 1), "samples": len(pairs),
                "default": MEASUREMENT_NOISE["ppm"], "adopted": enough,
                "note": (f"Largest spread across {len(pairs)} reading pair(s) taken within 20 "
                         "minutes, where nothing can actually have changed."
                         if enough else
                         f"{len(pairs)} pair(s); needs {spec['min_samples']}. Reported, not used."),
            }
            results["ppm_measurement_noise"] = store["ppm_measurement_noise"]

        self.store_own_memory("learned_constants", json.dumps(store))
        adopted = [k for k, v in results.items() if v.get("adopted")]
        return {
            "plant_id": plant_id,
            "evaluated": len(results),
            "adopted": adopted,
            "constants": results,
            "note": ("A value is adopted only once there are enough samples to support it. "
                     "Until then the default stands and the measurement is shown beside it, so "
                     "a disagreement is visible before it is acted on."),
            "what_this_is": ("This is the agent replacing what it was TOLD with what it has "
                             "MEASURED. Nobody chooses the new number - it is computed from the "
                             "record, and the same computation on a different grow gives a "
                             "different answer."),
        }

    def enable_sensor_ingest(self):
        """Subscribe to the sensor topic. Safe to call when no sensor exists -
        a topic with no publisher costs nothing."""
        try:
            self._extra_subscriptions = list(
                set(getattr(self, "_extra_subscriptions", [])) | {SENSOR_TOPIC})
            if getattr(self, "mqtt_client", None) and self.mqtt_client.is_connected():
                self.mqtt_client.subscribe(SENSOR_TOPIC)
            self.log(f"sensor ingest listening on {SENSOR_TOPIC}")
            return True
        except Exception as e:
            self.log(f"could not subscribe to sensors: {e}")
            return False

    def on_mqtt_message(self, client, userdata, msg):
        """Route sensor traffic, defer everything else to the base class."""
        try:
            topic = msg.topic
            if topic.startswith("mycelial/sensor/"):
                parts = topic.split("/")
                sensor_id = parts[2] if len(parts) > 2 else "unknown"
                try:
                    data = json.loads(msg.payload.decode())
                except Exception:
                    self.log(f"sensor {sensor_id}: payload was not JSON, ignored")
                    return
                self.ingest_sensor_sample(sensor_id, data)
                return
        except Exception as e:
            self.log(f"sensor ingest error: {e}")
        super().on_mqtt_message(client, userdata, msg)

    def ingest_sensor_sample(self, sensor_id, data):
        """One raw sample. Buffered, not logged - see the note above."""
        if not isinstance(data, dict):
            return {"error": "sensor payload must be an object"}
        sample = {"at": datetime.now().isoformat(), "sensor_id": sensor_id}
        for src, dst in (("ppm", "ppm"), ("tds", "ppm"), ("ph", "ph"),
                         ("temp_c", "temp"), ("temp", "temp"), ("ec", "ec"),
                         ("humidity", "humidity"), ("volume_liters", "volume_liters")):
            v = self._parse_numeric(data.get(src))
            if v is not None:
                sample.setdefault(dst, v)
        if len(sample) <= 2:
            return {"error": "no recognised measurements in payload"}
        plant_id = data.get("plant_id", "current_plant")

        key = f"sensor_buffer_{plant_id}"
        raw = self._unwrap_value(self.retrieve_own_memory(key))
        try:
            buf = json.loads(raw) if raw else []
        except Exception:
            buf = []
        buf.append(sample)
        buf = buf[-SENSOR_BUFFER_MAX:]
        self.store_own_memory(key, json.dumps(buf))

        aggregated = self._maybe_aggregate(plant_id, buf)
        return {"buffered": len(buf), "aggregated": aggregated}

    def _maybe_aggregate(self, plant_id, buf):
        """Turn raw samples into one logged reading per window."""
        last = self._unwrap_value(self.retrieve_own_memory(f"sensor_last_agg_{plant_id}"))
        try:
            since = datetime.fromisoformat(str(last).strip('"')[:19]) if last else None
        except Exception:
            since = None
        now = datetime.now()
        if since and (now - since).total_seconds() < SENSOR_AGGREGATE_HOURS * 3600:
            return None
        window = []
        for s in buf:
            try:
                t = datetime.fromisoformat(s["at"][:19])
            except Exception:
                continue
            if since is None or t > since:
                window.append(s)
        if not window:
            return None

        agg, spread = {}, {}
        for field in ("ppm", "ph", "temp", "ec", "humidity", "volume_liters"):
            vals = [s[field] for s in window if field in s]
            if not vals:
                continue
            agg[field] = round(sum(vals) / len(vals), 2)
            if len(vals) > 1:
                spread[field] = round(max(vals) - min(vals), 2)

        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        args = dict(agg)
        args.update({
            "plant_id": plant_id,
            "stage": stage,
            "source": "sensor",
            "evidence_kind": "fact",
            "reason": (f"Automatic: mean of {len(window)} sensor sample(s) over the last "
                       f"{SENSOR_AGGREGATE_HOURS}h."),
            "observed_conditions": ("spread across the window: "
                                    + ", ".join(f"{k} +/-{v}" for k, v in spread.items())
                                    if spread else "single sample in window"),
            "confidence_note": "high - instrument reading, no human transcription step",
        })
        result = self.handle_task("log_reading", args, "sensor")
        self.store_own_memory(f"sensor_last_agg_{plant_id}", json.dumps(now.isoformat()))
        self.log(f"sensor: logged an aggregate of {len(window)} sample(s) for {plant_id}")
        return {"samples": len(window), "values": agg, "spread": spread,
                "logged": bool(result)}

    def sensor_status(self, plant_id="current_plant"):
        raw = self._unwrap_value(self.retrieve_own_memory(f"sensor_buffer_{plant_id}"))
        try:
            buf = json.loads(raw) if raw else []
        except Exception:
            buf = []
        if not buf:
            return {"connected": False,
                    "topic": SENSOR_TOPIC,
                    "note": ("No sensor samples received. Publish JSON to "
                             "mycelial/sensor/<id>/reading with any of ppm, ph, temp_c, ec, "
                             "humidity, volume_liters and it will be ingested automatically.")}
        last = buf[-1]
        try:
            age_min = (datetime.now() - datetime.fromisoformat(last["at"][:19])).total_seconds() / 60
        except Exception:
            age_min = None
        return {"connected": True, "samples_buffered": len(buf),
                "last_sample_minutes_ago": round(age_min) if age_min is not None else None,
                "sensors_seen": sorted({s.get("sensor_id") for s in buf if s.get("sensor_id")}),
                "fields": sorted({k for s in buf for k in s if k not in ("at", "sensor_id")}),
                "aggregate_window_hours": SENSOR_AGGREGATE_HOURS}

    def situation(self, plant_id="current_plant", target_ppm=None):
        """One coherent picture of where the reservoir stands, with every facet.

        The grower asked why, how and when about the SAME thing, and each one
        needed a separate intent, a separate task and a separate regex. That is
        backwards. There is one situation; a question word only chooses which
        part of it leads.

        So this assembles all of it once - state, cause, obstacles, path, timing
        - and the caller decides the ordering. A phrasing nobody anticipated
        still gets a complete answer, just arranged differently, instead of
        falling through to a status card because no pattern matched.

        Every facet is either measured or read from the record. None of it is
        generated."""
        facets = {}

        # WHAT - where things actually stand.
        drift = self.check_target_drift(plant_id)
        if drift.get("applicable"):
            facets["what"] = {
                "summary": drift.get("message"),
                "ppm": drift.get("ppm"),
                "band": drift.get("target"),
                "status": drift.get("status"),
            }

        # WHY - the reasoning recorded when it was set this way.
        why = self.explain_decision(plant_id)
        if why.get("found"):
            last = (why.get("decisions") or [])[-1:]
            facets["why"] = {
                "summary": (f"{last[0].get('reason')} {last[0].get('decision') or ''}".strip()
                            if last else None),
                "expected_at_the_time": last[0].get("expected") if last else None,
                "history": why.get("decisions"),
            }

        # WHY NOT NOW - what is in the way of changing it, and what clears each.
        blockers = self.blockers_for_change(plant_id, target_ppm)
        facets["blocked_by"] = {
            "summary": blockers.get("verdict"),
            "items": blockers.get("blockers"),
        }

        # HOW - what a change would actually cost, if a target was named.
        if target_ppm:
            dose = self.handle_task("adjust_to_target_ppm",
                                    {"plant_id": plant_id, "target_ppm": target_ppm}, "situation")
            dose = dose.get("result", dose) if isinstance(dose, dict) else {}
            if isinstance(dose, dict) and dose.get("add_now"):
                facets["how"] = {
                    "summary": ("Add " + ", ".join(f"{k} {v}ml"
                                                   for k, v in dose["add_now"].items()) + "."),
                    "factor": dose.get("factor"),
                    "caution": dose.get("top_fed_caution"),
                }

        # WHEN - the conditions that would make it the right moment.
        clears = [b.get("clears_when") for b in (blockers.get("blockers") or [])
                  if b.get("clears_when")]
        facets["when"] = {
            "summary": ("When: " + " And: ".join(clears)) if clears
                       else "No condition is outstanding - it can be done now.",
            "conditions": clears,
        }

        return {"plant_id": plant_id, "target_ppm": target_ppm, "facets": facets,
                "note": ("One situation, several facets. The question decides which leads, not "
                         "which parts exist.")}

    # Vocabulary that claims a request for this agent. It lived in Boss, which
    # meant the orchestrator carried the vocabulary of a domain it does not
    # practise, and a word nobody had thought to add went to a code model -
    # "DWC" answered as "Direct Water Cooker", a strain name as folklore.
    ROUTING_TERMS = (
    # lifecycle questions that name no plant and no measurement
    "stage", "how old", "days old", "taproot", "cotyledon", "true leaves",
    # photographs - what they buy and how often to take them
    "photo", "photos", "picture", "pictures", "\\bpics?\\b", "camera", "upload",
    # root establishment after a system change
    "roots?", "coloni[sz]", "establish", "transition", "transplant", "net ?pot",

    # systems and hardware
    "dwc", "lwc", "hydro", "hydroponic", "reservoir", "res", "bucket", "tent",
    "net pot", "clay pebble", "pebbles", "leca", "air stone", "airstone",
    "top feed", "air pump",
    # measurements and inputs
    "ppm", "\bph\b", "\bec\b", "tds", "nutrient", "nutrition", "feed", "feeding",
    "cal-?mag", "calmag", "flora ?(micro|gro|bloom)", "runoff",
    # plant and lifecycle
    "plant", "grow", "garden", "seedling", "germinat", "sprout", "veg\b",
    "vegetative", "flower", "bloom", "pistil", "calyx", "trichome", "harvest",
    "leaf", "leaves", "canopy", "node", "root", "roots", "strain",
    "autoflower", "auto-?flower", "photoperiod", "cultivar",
    # dosing phrasings that name no plant, no unit and no equipment. "How much
    # do I add to reach 800" is unambiguous inside a grow assistant and was
    # being answered by a CODE model as "add 200".
    "how much.*add", "add.*to reach", "to reach \\d", "reach \\d{3}",
    "top ?up", "how much more",
    # the act of keeping the record itself - asking how often to log is a grow
    # question even when it names no plant, no measurement and no equipment
    "reading", "readings", "log\b", "logging", "cadence", "how often",
    # actions
    "transplant", "defoliat", "lollipop", "topping", "water change", "top ?off",
    )

    _term_cache = {"terms": None, "at": 0}

    def routing_terms(self):
        """The fixed vocabulary plus the names of the plants actually being grown.

        A keyword list cannot know that "gsc" is a plant - only the agent
        holding the roster does. Registering a plant makes questions about it
        route correctly from that moment, with no edit anywhere else.

        The threshold here must match what _plant_from_text will accept. It did
        not: the resolver understood "gsc" but this dropped any token under four
        characters, so "what stage is gsc 2 at" was never claimed by this agent
        and reached a general code model, which refused it as a sensitive
        subject. Two places deriving plant identity by different rules is the
        bug; they use the same floor now."""
        c = self._term_cache
        if c["terms"] is None or time.time() - c["at"] > 300:
            live = []
            try:
                for p in self.active_plants():
                    for field in ("plant_id", "strain"):
                        v = str(p.get(field) or "").strip().lower()
                        if len(v) >= 3 and v not in ("current_plant", "test"):
                            live.append(re.escape(v))
                            parts = [w for w in re.split(r'[^a-z0-9]+', v) if w]
                            tail = parts[-1] if parts and parts[-1].isdigit() else None
                            for w in parts:
                                if len(w) < 3 or w.isdigit() or w in STOP_TERMS:
                                    continue
                                live.append(re.escape(w))
                                # "gsc 2", "gsc2", "gsc #2" - how the grower
                                # actually writes a numbered plant.
                                if tail:
                                    live.append(re.escape(w) + r'\s*#?\s*' + tail)
            except Exception as e:
                self.log(f"could not build live plant terms: {e}")
            c["terms"], c["at"] = sorted(set(live)), time.time()
        return {"agent": self.agent_id,
                "terms": list(self.ROUTING_TERMS) + c["terms"]}

    # What this agent can be asked, and what it uses to answer. Kept HERE
    # because choosing among a domain's own capabilities is domain reasoning -
    # Boss was making this choice and Boss does not know horticulture. Every
    # routing failure tonight was the middle layer guessing which of Grow's
    # tools applied: a drawdown answered with a list of conditions, a species
    # name picking the wrong plant, a care reading given for a reservoir
    # question. Boss decides WHICH AGENT. This decides which capability.
    QUESTION_SHAPES = (
        ("drawdown",   r"\bhow long\b.*\b(drop|fall|come down|draw|last|get (down )?to)\b|"
                       r"\b(drop|fall|draw down)\b.*\bto\s*\d{2,4}\b|"
                       r"\bhow (long|many days)\b.*\b\d{2,4}\b"),
        ("blockers",   r"\bwhy (can'?t|cant|not|won'?t|shouldn'?t)\b|\bwhat'?s (stopping|blocking)\b|"
                       r"\bwhy not (now|yet)\b|\bsafe to\b|\bshould i wait\b"),
        ("why",        r"\bwhy\b|\bhow come\b|\bwhat made\b|\bdid we (stop|choose|pick|decide)\b"),
        ("dose",       r"\bhow much\b|\bwhat do i (need|have) to add\b|\badd\b.*\bto (reach|hit|get)\b|"
                       r"\bto\s*\d{3,4}\s*ppm\b|\breach\s*\d{3,4}\b"),
        ("roots",      r"\broots?\b[^.]{0,50}(coloni[sz]|establish|reach|into the|down into|"
                       r"take hold|fill)|(coloni[sz]|establish)\w*[^.]{0,30}\b(root|pellet|"
                       r"pebble|medium|net ?pot)|after (the )?(transition|transplant|move)"),
        ("photos",     r"\b(photo|photos|picture|pictures|pic|pics|image|images|camera)\b"),
        ("cadence",    r"\bhow often\b|\b(reading|log|logging) (frequency|cadence|expectancy)\b"),
        ("deficit",    r"\b(loss|lost|cost|impact|stagnant|behind|deficit|underfed|set ?back)\b"),
        ("care",       r"\b(wilt|droop|shrivel|yellow|brown|mush|rot|leggy|dry|sick|dying|"
                       r"limp|flat|soft|curl|crisp|spot|pale|burn)\w*|"
                       r"\blooks? (bad|off|sad|rough|sick|wrong)\b|\bwhat'?s wrong\b"),
        ("stage",      r"\bstage\b|\bveg\b|\bflower\b|\bpistil\b|\bharvest\b|\bhow old\b"),
    )

    def amend_grow_system(self, plant_id="current_plant", **fields):
        """Add or correct individual facts on the system record.

        set_grow_system rebuilds the whole record from its arguments, so using
        it to fix one field silently drops every field not passed. A physical
        fact learned later - a clearance, a pump change - needs a merge, not a
        rewrite."""
        key = f"grow_system_{plant_id}"
        raw = self._unwrap_value(self.retrieve_own_memory(key))
        if not raw and plant_id == "current_plant":
            key, raw = "grow_system", self._unwrap_value(self.retrieve_own_memory("grow_system"))
        if not raw:
            return {"error": f"No system record for {plant_id}."}
        try:
            record = json.loads(raw)
        except Exception as e:
            return {"error": f"System record unreadable: {e}"}
        changed = {k: v for k, v in fields.items() if v is not None and record.get(k) != v}
        if not changed:
            return {"changed": {}, "record": record, "note": "nothing to change"}
        record.update(changed)
        record["amended_at"] = datetime.now().isoformat()
        self.store_own_memory(key, json.dumps(record))
        return {"changed": changed, "record": record}

    def measure_working_volume(self, plant_id="current_plant", reference_liters=None,
                               verdict="above", method="side_by_side_level",
                               solids_submerged=None, upper_hint=None, note="",
                               precision_liters=None):
        """Interpret a volume measurement made by comparing water levels.

        The trap is that a level comparison measures DISPLACEMENT, not water.
        A reference bucket holding R litres of plain water sits at some level.
        The operating bucket sits at a level set by its water V plus whatever
        its submerged solids displace, D:

            level_operating > level_reference  =>  V + D > R  =>  V > R - D

        Because D is never negative, seeing the operating bucket sit HIGHER
        than the reference does not establish that it holds more water. With a
        net pot, clay pebbles and a root mass under the line, a bucket holding
        slightly LESS than R can still read above it. Reading that observation
        as "the reservoir is bigger" would raise every dose against a volume
        the grow does not have.

        So this returns a bound and refuses a point estimate, because a point
        estimate is not what was measured. D is unknown here and the agent will
        not invent it - the whole comparison is blind to the one quantity that
        decides the answer."""
        ref = self._parse_numeric(reference_liters)
        if ref is None:
            return {"error": "Need the reference volume that was poured in."}

        stored = None
        sysraw = (self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
                  or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                      if plant_id == "current_plant" else None))
        try:
            stored = self._parse_numeric((json.loads(sysraw) or {}).get("typical_working_liters")) \
                if sysraw else None
        except Exception:
            pass

        # Whether anything is actually under the water line is a recorded fact
        # about this system, not something to assume. Assuming it cost this
        # analysis a wrong answer once: the net pot here sits clear of the
        # water, so the clay pebbles displace nothing, and treating them as
        # submerged turned a measurement that CONFIRMED the stored volume into
        # one that appeared to contradict it.
        if solids_submerged is None:
            try:
                sysrec = json.loads(sysraw) if sysraw else {}
            except Exception:
                sysrec = {}
            contacts = sysrec.get("medium_contacts_water")
            solids_submerged = True if contacts is None else bool(contacts)

        out = {"plant_id": plant_id, "method": method, "reference_liters": ref,
               "verdict": verdict, "stored_working_liters": stored,
               "solids_submerged": solids_submerged,
               "displacement_accounted": False}

        if method != "side_by_side_level" or not solids_submerged:
            # Nothing meaningful under the line, so level IS water. The limit is
            # now how finely two buckets can be compared by eye, which is far
            # coarser than the arithmetic - and a difference smaller than that
            # is not a finding.
            prec = self._parse_numeric(precision_liters) or 1.0
            est_lo, est_hi = ref - prec, (self._parse_numeric(upper_hint) or ref + prec)
            out.update({"water_liters_estimate": ref,
                        "water_liters_range": [round(est_lo, 1), round(est_hi, 1)],
                        "precision_liters": prec,
                        "displacement_accounted": True,
                        "confidence": "medium"})
            if stored is not None:
                gap = abs(ref - stored)
                out["difference_from_stored"] = round(gap, 2)
                if gap < prec:
                    out["verdict_vs_stored"] = "below_resolution"
                    out["conclusion"] = (
                        f"{ref:g}L against a stored {stored:g}L is a {gap:.1f}L difference, "
                        f"measured by eye to about +/-{prec:g}L. The measurement cannot see a "
                        f"difference that small, so it CONFIRMS the stored volume rather than "
                        f"revising it. Changing the figure here would be reading precision the "
                        f"method does not have.")
                else:
                    out["verdict_vs_stored"] = "differs"
                    out["conclusion"] = (
                        f"{ref:g}L against a stored {stored:g}L is {gap:.1f}L apart, larger than "
                        f"the +/-{prec:g}L this method resolves. Worth revising - dosing runs "
                        f"against the stored figure.")
            out["recorded_at"] = datetime.now().isoformat()
            if note:
                out["note"] = note
            try:
                self.store_own_memory(f"volume_observation_{int(time.time())}", json.dumps(out))
            except Exception as e:
                self.log(f"could not store volume observation: {e}")
            return out

        out.update({
            "water_liters_at_least": None,
            "water_liters_at_most": self._parse_numeric(upper_hint),
            "confidence": "low",
            "why_no_number": (
                f"The operating bucket reading above the {ref:g}L line means water plus "
                "submerged solids exceeds " f"{ref:g}L - not that the water does. A net pot, "
                "clay pebbles and a root mass all sit under the line and displace solution. "
                "How much they displace has never been measured, and it is the only quantity "
                "that turns this observation into a volume."),
            "consistent_with_stored": stored is not None and stored <= ref,
            "what_would_settle_it": (
                "Drain the operating reservoir back into the jugs it was filled from and "
                "count what comes out. That measures water and nothing else, and it is the "
                "only version of this comparison that displacement cannot distort."),
        })
        if stored is not None:
            out["effect_on_dosing"] = (
                f"Dosing currently runs against {stored:g}L. Raising it to {ref + 2:g}L on this "
                f"evidence would increase every dose by about "
                f"{((ref + 2 - stored) / stored * 100):.0f}% against a volume that has not been "
                "shown to exist. The stored figure is left alone until a drain-and-count.")
        out["recorded_at"] = datetime.now().isoformat()
        if note:
            out["note"] = note
        try:
            self.store_own_memory(f"volume_observation_{int(time.time())}", json.dumps(out))
        except Exception as e:
            self.log(f"could not store volume observation: {e}")
        return out

    def describe(self, task, result):
        """Put this agent's own result into words.

        These sentences were written inside Boss - the orchestrator was
        composing horticulture prose about reservoirs, deficiency signs and
        stage transitions for results it had only passed through. An agent that
        cannot say what it found in plain language is not finished, and the
        wording drifts from the reasoning when the two live in different files.

        Returns None for anything this agent has no words for, so the caller
        can fall back to its own generic rendering."""
        if task == "assess_care":
            inner = result.get("result", {}) if isinstance(result, dict) else {}
            inner = inner.get("result", inner) if isinstance(inner, dict) else {}
            if not isinstance(inner, dict) or not inner:
                return "I could not get a reading on that plant."
            name = inner.get("profile") or inner.get("species") or "that plant"
            bits = [f"Your {name}:"]
            if inner.get("signs"):
                bits.append(inner.get("assessment") or "")
            else:
                bits.append(f"Nothing flags a care problem right now.")
            t = inner.get("temperature") or {}
            if t.get("note"):
                bits.append(t["note"])
            if inner.get("action") and "No urgent" not in inner["action"]:
                bits.append(inner["action"])
            c = inner.get("care") or {}
            if c.get("water"):
                bits.append(f"Watering: {c['water']}")
            return " ".join(b for b in bits if b)


        if task == "evaluate_leaf":
            inner = result.get("result", {}) if isinstance(result, dict) else {}
            inner = inner.get("result", inner) if isinstance(inner, dict) else {}
            if not isinstance(inner, dict) or not inner:
                return "I couldn't get a read on that photo right now."
            classification = inner.get("classification", "unknown")
            lines = [inner.get("observation", "")]
            if inner.get("reason"):
                lines.append(inner["reason"])
            if inner.get("action"):
                lines.append(inner["action"])
            text = " ".join(l for l in lines if l)
            # Narration has to carry the confidence, not just the verdict.
            # "Looks healthy" was being said over a low-confidence result
            # reached by detecting nothing - which reads to the grower as a
            # clean bill of health when the system in fact could not assess
            # the plant at all.
            conf = (inner.get("confidence") or "").lower()
            if classification == "problem":
                text = f"Heads up - {text}"
            elif classification == "productive" and conf == "low":
                text = f"I couldn't really assess this one. {text}"
            elif classification == "productive":
                text = f"Looks healthy. {text}"
            vision_note = inner.get("vision_note")
            if vision_note and "escalated" in vision_note.lower():
                text += " (This one was uncertain enough locally that I double-checked it more carefully.)"
            return text

        if task == "log_reading":
            inner = result.get("result", {}) if isinstance(result, dict) else {}
            reading = inner.get("reading") if isinstance(inner, dict) else None
            if not isinstance(reading, dict):
                return "I wasn't able to log that reading."
            parts = []
            if reading.get("ppm") is not None:
                parts.append(f"{reading['ppm']} ppm")
            if reading.get("temp") is not None:
                parts.append(f"{reading['temp']}°C")
            if reading.get("ph") is not None:
                parts.append(f"pH {reading['ph']}")
            detail = ", ".join(parts) if parts else "that reading"
            return f"Got it - logged {detail} for the {reading.get('stage', 'current')} stage. I'll factor it into the reservoir trend."

        if task == "purchase_recommendation":
            inner = result.get("result", {}) if isinstance(result, dict) else {}
            rec = inner.get("result", {}) if isinstance(inner, dict) else {}
            if not isinstance(rec, dict) or not rec:
                return "I couldn't put together a recommendation for that."
            constraint = rec.get("budget_constraint", {}) or {}
            item = rec.get("item", "that")
            cost = rec.get("estimated_cost", 0)
            if rec.get("requires_escalation"):
                if constraint.get("within_budget") is False:
                    return (
                        f"I'd recommend picking up {item} (about ${cost:.2f}), but there isn't enough "
                        f"discretionary budget available right now (${constraint.get('available_discretionary', 0):.2f} free). "
                        f"Let me know if you want to move funds or go ahead anyway."
                    )
                return f"I'd recommend {item} (about ${cost:.2f}) - that's above the amount I'll auto-approve, so I'm holding it for your go-ahead."
            if constraint.get("within_budget") is True:
                return f"I'd recommend picking up {item} - about ${cost:.2f}, and that's within your discretionary budget."
            return f"I'd recommend {item} (about ${cost:.2f}). {constraint.get('note', '')}".strip()

        if task == "grow_status":
            status_resp = result.get("status", {}) if isinstance(result, dict) else {}
            if isinstance(status_resp, dict) and "error" in status_resp:
                return "I couldn't reach the grow system right now."
            status_inner = status_resp.get("result", status_resp) if isinstance(status_resp, dict) else {}
            r = status_inner.get("result", status_inner) if isinstance(status_inner, dict) else {}
            if not isinstance(r, dict) or not r:
                return "I don't have any grow data yet."

            history_resp = result.get("history", {}) if isinstance(result, dict) else {}
            history_inner = history_resp.get("result", {}) if isinstance(history_resp, dict) else {}
            history = history_inner.get("result", {}) if isinstance(history_inner, dict) else {}
            timeline = history.get("timeline", []) if isinstance(history, dict) else []

            # Lead with an alert only if the MOST RECENT check of that type is
            # still unresolved - a fresh stable reservoir_eval must supersede an
            # older warning, not get skipped past while hunting further back in
            # history for the last time something looked bad. The underlying
            # observation/reason/action/confidence shape already carries everything
            # needed to narrate this in plain language, with no agent/task names.
            alert_line = None
            latest_by_type = {}
            for entry in reversed(timeline):
                etype = entry.get("type")
                if etype in ("reservoir_eval", "leaf_eval", "stage_eval") and etype not in latest_by_type:
                    latest_by_type[etype] = entry

            reservoir_entry = latest_by_type.get("reservoir_eval")
            if reservoir_entry:
                rec = reservoir_entry.get("data", {}).get("recommendation", {})
                if rec.get("stability_band") in ("warning", "critical"):
                    urgency = "I'd address this now" if rec.get("stability_band") == "critical" else "I recommend addressing it today"
                    alert_line = f"{rec.get('observation', 'Something in the reservoir needs attention.')} {urgency}."

            if not alert_line:
                leaf_entry = latest_by_type.get("leaf_eval")
                if leaf_entry:
                    rec = leaf_entry.get("data", {}).get("recommendation", {})
                    if rec.get("classification") == "problem":
                        alert_line = f"{rec.get('observation', 'A leaf issue was spotted.')} {rec.get('action', '')}".strip()

            if not alert_line:
                stage_entry = latest_by_type.get("stage_eval")
                if stage_entry:
                    rec = stage_entry.get("data", {}).get("recommendation", {})
                    if rec.get("classification") in ("decline", "regression"):
                        alert_line = f"{rec.get('observation', '')} {rec.get('action', '')}".strip()

            if alert_line:
                lines = [alert_line]
            else:
                stage = r.get("current_stage", "unknown")
                strain = r.get("current_strain")
                plant_label = f"Your {strain}" if strain else "Your plant"
                lines = [f"{plant_label} is in the {stage} stage and everything looks stable."]

            nutrients = r.get("current_nutrients")
            if isinstance(nutrients, dict) and nutrients.get("nutrients"):
                # Always state the unit and what the dose is measured against -
                # a bare "FloraMicro 3.0" is ambiguous by ~3.79x.
                unit = nutrients.get("unit") or ""
                n_str = ", ".join(f"{k} {v}{unit}" for k, v in nutrients["nutrients"].items())
                basis, litres = nutrients.get("basis"), nutrients.get("reservoir_liters")
                if basis == "total" and litres:
                    qualifier = f" per {litres:g}L reservoir"
                elif basis == "per_liter":
                    qualifier = " per litre"
                elif basis == "per_gallon":
                    qualifier = " per gallon"
                else:
                    qualifier = ""
                lines.append(f"Current feed: {n_str}{qualifier}.")

            # Only round up the rest of the garden when the question was
            # about the garden. Asked about one plant, answer about that
            # plant - a roundup buries the answer that was actually wanted.
            # The dashboard is where everything lives. In chat, a question
            # gets an answer - the roundup only appears when the roundup is
            # what was asked for.
            if isinstance(result, dict) and result.get("roundup"):
                for p in r.get("other_plants") or []:
                    lines.append(f"Also coming along: {p.get('strain', 'another plant')}, {p.get('stage', 'unknown')} stage.")

                reminders = r.get("pending_reminders") or []
                if reminders:
                    reminder_str = "; ".join(f"{rem.get('title')} (due {rem.get('target_date')})" for rem in reminders)
                    lines.append(f"Also on your list: {reminder_str}.")

            return "\n".join(lines)
        return None

    def parse_reading(self, text):
        """Pull a reservoir reading out of plain language, or None.

        Knowing that "6.15ph" is a pH and that an F reading must be converted
        before it is stored is horticulture, and it lived in the orchestrator -
        in two separate copies that had already drifted apart. Two independent
        signals are required so that a bare number in conversation is not
        recorded as a measurement."""
        t = text or ""
        ppm = re.search(r'(\d+(?:\.\d+)?)\s*ppm', t, re.IGNORECASE)
        ph = re.search(r'(\d+(?:\.\d+)?)\s*ph\b', t, re.IGNORECASE) or \
            re.search(r'\bph\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)', t, re.IGNORECASE)
        tc = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*c\b', t, re.IGNORECASE)
        tf = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*f\b', t, re.IGNORECASE)
        signals = sum(1 for m in (ppm, ph, tc, tf) if m)
        if signals == 0:
            return None
        if signals == 1:
            # One unit-labelled value is a measurement unless the sentence is
            # about where the grower WANTS it. Two signals were required
            # because "add 200 to reach 800" must not be logged as a reading -
            # but that is target language, not a question of how many numbers
            # were said. A follow-up "6.07ph" after correcting pH is a real
            # measurement and refusing it loses the observation that shows the
            # correction worked.
            if re.search(r"\b(raise|lower|set|keep|hold|target|reach|adjust|bring|"
                         r"push|drop it|get it|should|want|aim|instead of|up to|"
                         r"down to|add)\b", t, re.IGNORECASE):
                return None
        out = {}
        if ppm:
            out["ppm"] = float(ppm.group(1))
        if ph:
            out["ph"] = float(ph.group(1))
        temp_c = float(tc.group(1)) if tc else ((float(tf.group(1)) - 32) * 5 / 9 if tf else None)
        if temp_c is not None:
            out["temp"] = round(temp_c, 1)
        return out or None

    def ingest(self, prompt):
        """A reservoir reading mentioned in passing is recorded before anything
        else happens - vision is slow and can time out, and the numbers must not
        be lost with it. The photo can be retaken; the reservoir at that moment
        cannot."""
        return self.log_from_text(prompt or "")

    def log_from_text(self, prompt, plant_id="current_plant"):
        """Record a reading stated in conversation, stamped with the right stage.

        Which stage an unstamped reading belongs to is this agent's call, not
        the caller's - it is the one that knows the plant's age and what
        "unknown" should fall back to."""
        reading = self.parse_reading(prompt)
        if not reading:
            return {"logged": False, "reason": "no reading found in text"}
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "seedling"
        if stage == "unknown":
            stage = "seedling"
        args = dict(reading, stage=stage, plant_id=plant_id)
        return {"logged": True, "reading": reading,
                "result": self.handle_task("log_reading", args, self.agent_id)}

    def answer(self, prompt, plant_id=None):
        """Answer a grow question by deciding, here, what this agent needs to do.

        The whole prompt arrives from Boss unparsed. This agent resolves which
        plant it is about, works out what is being asked, calls its own
        capabilities, and returns facts. It does not narrate - that is Anansi's
        job - and it does not decide whether the question belongs to it at all -
        that is Boss's.

        The point is that adding a capability here does not require a change
        anywhere else. Every failure tonight came from a new ability being
        reachable from exactly one route through Boss, and the grower not
        happening to use that route."""
        lp = (prompt or "").lower()
        plant_id = plant_id or self._plant_from_text(prompt) or "current_plant"
        nums = [float(x) for x in re.findall(r'\b(\d{2,4})\b', prompt or "")
                if 50 <= float(x) <= 3000]

        shape = next((name for name, pat in self.QUESTION_SHAPES if re.search(pat, lp)), None)
        parts = []

        if shape == "drawdown":
            # "how long to fall to 238" names the destination only. The starting
            # point is not missing - it is the current reading, and refusing to
            # answer because the grower did not repeat a number the system
            # already holds is the system's failure, not theirs.
            if len(nums) >= 2:
                frm, to = max(nums), min(nums)
            elif len(nums) == 1:
                readings = self._get_readings_for_plant(plant_id) or []
                cur, _age = self._last_known_param("ppm", readings)
                cur = self._parse_numeric(cur)
                frm, to = (cur, nums[0]) if cur and cur > nums[0] else (None, None)
            else:
                frm = to = None
            d = self.project_drawdown(plant_id, frm, to) if frm and to else {"error": "no range"}
            if not d.get("error"):
                parts.append(f"From {d['from_ppm']} to {d['to_ppm']} is about {d['days']} day(s) "
                             f"at {d['rate_ppm_per_day']} ppm/day. That rate is a {d['rate_source']}.")
                for k in ("why_not_measured", "volume_note"):
                    if d.get(k):
                        parts.append(d[k])
                return {"answered_as": "drawdown", "plant_id": plant_id,
                        "text": " ".join(parts), "facts": d}

        if shape == "care":
            c = self.assess_care(prompt, plant_id=plant_id)
            parts.append(c.get("assessment") or "")
            if c.get("action") and "No urgent" not in c["action"]:
                parts.append(c["action"])
            return {"answered_as": "care", "plant_id": plant_id,
                    "text": " ".join(p for p in parts if p), "facts": c}

        if shape == "roots":
            r = self.estimate_root_establishment(plant_id)
            parts = [r.get("assessment") or "", r.get("what_carries_it") or "",
                     r.get("what_confirms_it") or "", r.get("why_not_measured") or "",
                     r.get("missing") or ""]
            return {"answered_as": "root_establishment", "plant_id": plant_id,
                    "text": " ".join(x for x in parts if x), "facts": r}

        if shape == "photos":
            pc = self.photo_cadence(plant_id)
            parts = [pc.get("recommended") or "", pc.get("why") or "",
                     pc.get("what_a_photo_buys_now") or "",
                     pc.get("limit") or "",
                     pc.get("what_a_photo_does_NOT_feed") or "",
                     pc.get("training_status") or "",
                     pc.get("what_would_change_that") or "",
                     pc.get("applies_to") or ""]
            return {"answered_as": "photo_cadence", "plant_id": plant_id,
                    "text": " ".join(x for x in parts if x), "facts": pc}

        if shape == "cadence":
            c = self.reading_cadence(plant_id)
            parts = [f"Every {c.get('recommended_days')} days at {c.get('stage')} stage.",
                     c.get("recommended_because") or "", c.get("maximum_because") or ""]
            o = c.get("observed") or {}
            if o.get("median_gap_days") is not None:
                parts.insert(1, f"You are averaging one every {o['median_gap_days']} days, "
                                f"longest gap {o['longest_gap_days']}.")
            return {"answered_as": "cadence", "plant_id": plant_id,
                    "text": " ".join(p for p in parts if p), "facts": c}

        if shape == "deficit":
            d = self.analyze_deficit(plant_id)
            if not d.get("error"):
                parts = [d.get("consequence") or "", d.get("why_no_number") or "",
                         d.get("what_would_make_it_answerable") or ""]
                return {"answered_as": "deficit", "plant_id": plant_id,
                        "text": " ".join(p for p in parts if p), "facts": d}

        if shape == "stage":
            st = self.assess_stage(plant_id)
            parts.append(st.get("assessment") or "")
            drift = self.check_target_drift(plant_id)
            if drift.get("applicable"):
                parts.append(drift.get("message") or "")
            return {"answered_as": "stage", "plant_id": plant_id,
                    "text": " ".join(p for p in parts if p), "facts": st}

        # A situation is a story about a reservoir. A plant that has no
        # readings of its own has no reservoir story, and answering "how is the
        # aloe doing" with the cannabis tank's blockers is not a near miss - it
        # is a different plant. Fall back to what IS known about it: how it
        # looks and how far along it is.
        if not (self._get_readings_for_plant(plant_id) or []):
            care = self.assess_care(prompt, plant_id=plant_id)
            st = self.assess_stage(plant_id)
            parts = [care.get("assessment") or "", st.get("assessment") or ""]
            act = care.get("action") or ""
            if act and "No urgent" not in act:
                parts.append(act)
            text = " ".join(p for p in parts if p)
            if text:
                return {"answered_as": "condition", "plant_id": plant_id,
                        "text": text, "facts": {"care": care, "stage": st}}

        # Everything else is a facet of the current situation - state, cause,
        # obstacles, cost, timing - ordered by what was asked.
        target = max([n for n in nums if 300 <= n <= 2000], default=None)
        sit = self.situation(plant_id, target)
        facets = sit.get("facets") or {}
        lead = {"blockers": "blocked_by", "why": "why", "dose": "how"}.get(shape) or (
            "when" if re.search(r"\bwhen\b|\bhow soon\b", lp) else "what")
        order = [lead] + [f for f in ("what", "blocked_by", "why", "how", "when") if f != lead]
        for name in order:
            f = facets.get(name) or {}
            if not f.get("summary"):
                continue
            parts.append(f["summary"])
            if name == "blocked_by":
                for x in (f.get("items") or [])[:3]:
                    parts.append(f"{x['detail']} {x['why']} Clears when: {x['clears_when']}")
            elif name == "how" and f.get("caution"):
                parts.append(f["caution"])
        return {"answered_as": f"situation:{lead}", "plant_id": plant_id,
                "text": " ".join(p for p in parts if p), "facts": sit}

    # "plant one", "plant #2", "my first autoflower". People refer to plants by
    # position at least as often as by name, and a strain shared between two
    # plants cannot disambiguate them at all - both of these are Girl Scout
    # Cookies.
    _ORDINALS = {"one": 1, "first": 1, "1st": 1, "two": 2, "second": 2, "2nd": 2,
                 "three": 3, "third": 3, "3rd": 3, "four": 4, "fourth": 4}

    def _plant_from_text(self, prompt):
        """Which of THIS agent's plants the text refers to, or None.

        Lives here because the agent knows its own plants. A species name is a
        category and never selects one on its own - both cannabis plants share
        it, and "the cannabis plant" once resolved to a day-old seedling.

        An id like gsc_auto_2 is never how the grower says it. They say "Gsc 2",
        "GSC number two", "the second GSC". So the id is decomposed into the
        words in it and the number on the end, and a word shared by two plants
        needs the number with it - "gsc" alone cannot choose between them."""
        lp = (prompt or "").lower()

        # "number two" / "two" -> 2, so a spoken ordinal reaches the same path
        # as a digit.
        for word, n in sorted(self._ORDINALS.items(), key=lambda kv: -len(kv[0])):
            lp = re.sub(r'\b(?:number\s+)?' + word + r'\b', str(n), lp)

        plants = self.active_plants()
        order = ["current_plant"] + [p.get("plant_id") for p in plants
                                     if p.get("plant_id") != "current_plant"]

        # "plant 2", "grow #2", "plant two" - position in the roster.
        m = re.search(r'\b(?:plant|grow)\s*#?\s*(\d+)\b', lp)
        if m:
            i = int(m.group(1))
            if 1 <= i <= len(order):
                return order[i - 1]

        # A species names a plant only when one living plant IS that species.
        counts, owner = {}, {}
        for p in plants:
            sp = (self._get_species_for_plant(p.get("plant_id")) or "").lower()
            if not sp:
                continue
            counts[sp] = counts.get(sp, 0) + 1
            owner.setdefault(sp, p.get("plant_id"))
        for sp, n in counts.items():
            if n == 1 and re.search(r'\b' + re.escape(sp) + r'\b', lp):
                return owner[sp]
        species = {sp for sp, n in counts.items() if n > 1}

        def tokens(value):
            return [w for w in re.split(r'[^a-z0-9]+', str(value or "").lower()) if w]

        best = None
        for p in plants:
            pid = p.get("plant_id")
            for field in ("plant_id", "strain"):
                v = str(p.get(field) or "").lower().strip()
                if not v or v in species:
                    continue
                if re.search(r'\b' + re.escape(v) + r'\b', lp):
                    if best is None or len(v) > best[0]:
                        best = (len(v), pid)
                    continue
                parts = tokens(v)
                tail = parts[-1] if parts and parts[-1].isdigit() else None
                for w in parts:
                    if len(w) < 3 or w.isdigit() or w in STOP_TERMS or w in species:
                        continue
                    if not re.search(r'\b' + re.escape(w) + r'\b', lp):
                        continue
                    shared = sum(1 for q in plants if w in tokens(q.get(field)))
                    if shared > 1:
                        # Ambiguous on its own - the number has to be there too.
                        if not tail or not re.search(r'\b' + re.escape(tail) + r'\b', lp):
                            continue
                        score = len(w) + len(tail)
                    else:
                        score = len(w) + (len(tail) if tail and
                                          re.search(r'\b' + re.escape(tail) + r'\b', lp) else 0)
                    if best is None or score > best[0]:
                        best = (score, pid)
        return best[1] if best else None

    def project_drawdown(self, plant_id="current_plant", from_ppm=None, to_ppm=None):
        """How long to fall from one ppm to another, and how much that is worth.

        A fair question - "if I go to 800, how long until it draws down to 238
        like the 5L did" - and the honest answer has two halves that must not be
        blurred. The arithmetic is easy. The RATE is the thing, and this grow has
        never produced a measurable one, because no two readings sit 24h apart in
        the same stage. So the projection is reported against the generic
        per-stage figure and labelled as exactly that: not this plant.

        Volume is the part most likely to mislead. The 5L tank and the 14.9L
        reservoir hold different amounts of nutrient at the same ppm, so a plant
        drinking at the same rate moves the number roughly three times slower in
        the larger one. Comparing a drawdown across a system change without
        saying that would be a wrong answer wearing a number."""
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        if plant_id != "current_plant":
            p = next((x for x in self._get_all_plants()
                      if x.get("plant_id") == plant_id), None)
            stage = (p or {}).get("stage", "unknown")

        readings = [r for r in self._get_readings_for_plant(plant_id)
                    if self._parse_numeric(r.get("ppm")) is not None]
        readings.sort(key=lambda r: r.get("timestamp") or "")
        if from_ppm is None and readings:
            from_ppm = self._parse_numeric(readings[-1].get("ppm"))
        if from_ppm is None or to_ppm is None:
            return {"error": "Need a starting and a target ppm."}
        if to_ppm >= from_ppm:
            return {"error": f"{to_ppm:.0f} is not below {from_ppm:.0f} - nothing to draw down."}

        # What this grow has actually shown, if anything.
        measured = self.learned("ppm_drift_per_day", stage=stage)
        generic = (STAGE_PROFILES.get(stage) or {}).get("expected_ppm_drift_per_day")
        rate = measured or generic
        if not rate:
            return {"error": f"No drawdown rate available for stage '{stage}'."}

        # Volume scales it. ppm is a concentration, so the same uptake in litres
        # of solution moves a big reservoir's number more slowly.
        sysraw = (self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
                  or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                      if plant_id == "current_plant" else None))
        litres = None
        try:
            litres = self._parse_numeric((json.loads(sysraw) or {}).get("reservoir_liters")) if sysraw else None
        except Exception:
            pass
        if not litres:
            hist = self._get_nutrient_history(plant_id)
            litres = self._parse_numeric(hist[-1].get("reservoir_liters")) if hist else None

        days = (from_ppm - to_ppm) / rate
        out = {
            "plant_id": plant_id, "stage": stage,
            "from_ppm": round(from_ppm), "to_ppm": round(to_ppm),
            "rate_ppm_per_day": rate,
            "rate_source": ("measured from this grow" if measured
                            else f"generic figure for {stage} - NOT measured from this plant"),
            "days": round(days, 1),
            "reservoir_liters": litres,
        }
        if not measured:
            out["confidence"] = "low"
            out["why_not_measured"] = (
                "This grow has never produced a usable drawdown rate: no two readings sit at "
                "least 24h apart within the same stage, and a shorter gap cannot separate uptake "
                "from meter noise. A probe reporting hourly, or readings spaced a few days apart, "
                "would replace this generic number with the plant's own within a week.")
        if litres:
            out["volume_note"] = (
                f"At {litres:g}L this is roughly {litres / 5:.1f}x the 5L tank, so the same plant "
                "drinking at the same rate moves the ppm number that much more slowly. A drawdown "
                "seen in the small tank does not transfer - the plant did not change, the "
                "denominator did.")
        return out

    def blockers_for_change(self, plant_id="current_plant", target_ppm=None):
        """What is actually stopping a change right now, and what clears it.

        The grower asked three times why they could not push to 800 and got
        history, then dose arithmetic, then schedule triggers - none of which is
        the question. "Why not now" wants the list of things in the way and the
        condition that removes each one. Anything else reads as evasion.

        Every blocker here names its own exit. A constraint with no stated way
        out is indistinguishable from a refusal."""
        out = []
        now = datetime.now()

        # 1. Attribution. Two changes inside one reading window cannot be told apart.
        hist = self._get_nutrient_history(plant_id)
        if hist:
            last = hist[-1].get("timestamp") or hist[-1].get("changed_at") or ""
            try:
                hrs = (now - datetime.fromisoformat(last[:19])).total_seconds() / 3600
            except Exception:
                hrs = None
            if hrs is not None and hrs < 48:
                out.append({
                    "blocker": "attribution",
                    "detail": f"The feed was last changed {hrs:.0f}h ago.",
                    "why": ("Change strength again before a reading lands and the next number "
                            "cannot be attributed to either change. You lose the ability to say "
                            "which one did what."),
                    "clears_when": "A reading has been taken at the current strength, ideally two.",
                })

        # 2. Root establishment after a system move.
        sysraw = (self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
                  or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                      if plant_id == "current_plant" else None))
        system = {}
        try:
            system = json.loads(sysraw) if sysraw else {}
        except Exception:
            pass
        moved = system.get("changed_at") or system.get("logged_at")
        if moved:
            try:
                days = (now - datetime.fromisoformat(str(moved)[:19])).days
            except Exception:
                days = None
            if days is not None and days <= 7:
                out.append({
                    "blocker": "root_establishment",
                    "detail": f"The system was rebuilt {days} day(s) ago.",
                    "why": ("Roots disturbed by a move are rebuilding, and a damaged root system "
                            "cannot take up a full-strength solution - the surplus just raises "
                            "concentration around tissue that is not drinking."),
                    "clears_when": "New white water roots are visible in the reservoir.",
                })

        # 3. Top-fed exposure - the increase reaches the medium roots first.
        if system.get("system_type") == "top_fed_dwc":
            out.append({
                "blocker": "top_fed_exposure",
                "detail": "The top ring sprays reservoir solution onto the roots in the medium.",
                "why": ("Raising the reservoir does not hold the increase away from the plant - "
                        "it delivers it, at full strength, to the root mass in the pebbles, "
                        "which is the most exposed part of the system rather than the least."),
                "clears_when": ("Roots are established in the reservoir itself, so there is a "
                                "second and more forgiving feeding path."),
            })

        # 4. Is there even a deficiency driving this?
        drift = self.check_target_drift(plant_id)
        if drift.get("applicable") and drift.get("status") == "in_band":
            band = drift.get("target") or []
            out.append({
                "blocker": "no_deficiency",
                "detail": f"{drift.get('ppm'):.0f} is already inside the {band[0]}-{band[1]} band.",
                "why": ("Nothing is short. Raising strength here is a bet on faster growth, not a "
                        "correction of a problem - so the downside is real and the upside is "
                        "speculative."),
                "clears_when": ("The plant shows it wants more - ppm falling faster than expected "
                                "between readings, or a stage change moving the band up."),
            })

        verdict = ("Nothing is blocking it." if not out else
                   ("Not yet - " + str(len(out)) + " thing(s) in the way."))
        return {"plant_id": plant_id, "target_ppm": target_ppm,
                "blockers": out, "verdict": verdict,
                "note": ("These are reasons to wait, not a refusal. Each one names the condition "
                         "that clears it, and the grower decides.")}

    def explain_decision(self, plant_id="current_plant", topic=""):
        """Why is it the way it is - answered from what was recorded at the time.

        reasoning_context has been capturing reason, decision, expected_effect
        and observed_conditions on every change for weeks, and nothing ever read
        them back. So "why did we stop at the low end for 688 instead of 800"
        got a status card, when the answer was written down the day it happened.

        Corrections are surfaced too. A record that says a previous entry was
        wrong is the most useful kind to find when asking how something got the
        way it is."""
        history = self._get_nutrient_history(plant_id)
        entries = []
        for e in history:
            n = e.get("nutrients") if isinstance(e.get("nutrients"), dict) else {}
            rc = e.get("reasoning_context") if isinstance(e.get("reasoning_context"), dict) else {}
            src = rc or {k: n[k] for k in ("reason", "decision", "expected_effect",
                                           "observed_conditions", "evidence_kind")
                         if k in n}
            if not src.get("reason") and not src.get("decision"):
                continue
            entries.append({
                "at": (e.get("timestamp") or e.get("changed_at") or "")[:19],
                "stage": e.get("stage"),
                "kind": src.get("evidence_kind") or "event",
                "reason": src.get("reason"),
                "decision": src.get("decision"),
                "expected": src.get("expected_effect"),
                "observed": src.get("observed_conditions"),
                "measured_after": e.get("next_measured_ppm"),
            })
        if not entries:
            return {"found": False,
                    "note": ("No decision on record carries its reasoning. Changes logged with "
                             "reason/decision/expected_effect can be explained later; ones "
                             "logged without cannot.")}
        entries.sort(key=lambda x: x["at"])
        # Consecutive entries carrying the same reasoning are one decision
        # recorded twice, not two decisions. Reading them back as two makes the
        # history look indecisive when it was not.
        deduped = []
        for e in entries:
            prev = deduped[-1] if deduped else None
            if prev and (e.get("reason"), e.get("decision")) == (prev.get("reason"),
                                                                 prev.get("decision")):
                prev["at"] = e["at"]          # keep the later timestamp
                continue
            deduped.append(e)
        entries = deduped
        # A test artifact and its retraction are noise when asking why something
        # is the way it is - unless the question is about the retraction.
        if "test" not in (topic or "").lower():
            entries = [e for e in entries
                       if "TEST" not in (e.get("reason") or "")[:8]
                       and "VOIDS" not in (e.get("reason") or "")[:8]]
        return {"found": True, "decisions": entries[-4:], "total_recorded": len(entries)}

    # Species the local disease models carry a class for. Mirrors
    # services/vision/plant_perception.py SUPPORTED_SPECIES - a photo of
    # anything else can still be DESCRIBED, it just cannot have a pathogen
    # named, which are two different questions.
    PATHOGEN_MODEL_SPECIES = ("pepper", "potato", "tomato")

    def _source_candidates(self, label, limit=5, query=None):
        """Propose candidate images for one label. Proposals only.

        config/skills.json states the invariant plainly: search PROPOSES, a
        human DISPOSES. Nothing here is training data - every item lands
        awaiting_review with its provenance attached, because a training set of
        unknown-origin images is a licensing and label-noise problem, not an
        asset."""
        query = query or f"cannabis leaf {label.replace('_', ' ')} photo"
        # Image category: the plain "search" tool returns one text snippet with
        # no URLs at all, which is useless for sourcing images.
        search_result = self.call_tool("searxng", "search_structured", {
            "query": query, "categories": "images", "max_results": limit * 3
        })
        seen = {c.get("image_url") for c in (self._get_pending_candidates() or [])}
        candidates = []
        for item in self._extract_search_items(search_result):
            if len(candidates) >= limit:
                break
            img = item.get("img_src")
            if not img or img in seen:
                continue          # don't queue the same image twice
            seen.add(img)
            candidates.append({
                "id": f"candidate_{int(time.time() * 1000)}_{len(candidates)}",
                "label": label,
                "query": query,
                "source_url": item.get("url"),
                "image_url": img,
                "source_title": item.get("title"),
                "retrieved_at": datetime.now().isoformat(),
                "status": "awaiting_review",
            })
        for c in candidates:
            self.store_own_memory(c["id"], json.dumps(c))
        if candidates:
            index = self._get_candidate_index()
            index.extend(c["id"] for c in candidates)
            self.store_own_memory("training_candidate_index", json.dumps(index))
        return candidates

    def advance_training_campaign(self, per_label=5, max_labels=2, labels=None):
        """Decide which labels the campaign is short on, and go get candidates.

        This is the part that was missing. Every piece of the loop already
        existed - the campaign knew it needed 10 labels, sourcing could fetch
        proposals, review could accept them - but nothing ever decided to run
        it. Capability without initiative: 3 candidates sat awaiting review for
        days while 9 labels stayed empty.

        Deliberately on demand. Nothing in this system runs on a timer, for the
        same reason the supervisor stopped doing so.

        A well-run grow cannot supply most of these labels - that is the point.
        The grower prevents pests, so their own plants will never photograph a
        spider mite infestation, and the only honest source is elsewhere."""
        qm = QuestManager(self, VISION_CAMPAIGN_ID)
        counts, label_set = self._training_counts()
        if not qm._load():
            qm.start_campaign(
                labels=label_set, threshold_per_label=MIN_PER_CLASS,
                description="Collect labelled cannabis leaf photos until a vision model can actually be trained.")
        wanted = labels or [q["label"] for q in qm.next_quests(counts, limit=max_labels)]
        if not wanted:
            return {"sourced": [], "note": "No incomplete labels - nothing to source."}

        sourced, failed = [], []
        for label in wanted[:max_labels]:
            try:
                got = self._source_candidates(label, limit=per_label)
                sourced.append({"label": label, "queued": len(got),
                                "ids": [c["id"] for c in got]})
                if not got:
                    failed.append(f"{label}: search returned nothing usable")
            except Exception as e:
                failed.append(f"{label}: {e}")
        pending = len(self._get_pending_candidates() or [])
        out = {"sourced": sourced, "awaiting_review": pending,
               "training_dir": TRAINING_DIR}
        if failed:
            # A sourcing run that found nothing must say so, distinctly from
            # one that found things to be fine.
            out["failures"] = failed
        out["next_step"] = (
            f"{pending} candidate(s) are proposals, not training data. Review them - "
            "accept downloads the image into the label's folder and counts it, reject "
            "discards it. Nothing is counted until a human decides.")
        return out

    def _fetch_candidate_image(self, candidate):
        """Download an ACCEPTED candidate into its label folder.

        Accepting used to only set a status and print "download the image
        yourself to have it counted" - so an accepted candidate changed
        nothing, and the campaign counter never moved. The loop had a human
        gate with no door behind it."""
        url = candidate.get("image_url")
        if not url:
            return {"fetched": False, "why": "candidate carries no image url"}
        label = candidate.get("label") or "unlabelled"
        dest_dir = os.path.join(TRAINING_DIR, label)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            resp = requests.get(url, timeout=20, stream=True,
                                headers={"User-Agent": "Mycelial/grow_agent"})
            if resp.status_code != 200:
                return {"fetched": False, "why": f"HTTP {resp.status_code}"}
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("image/"):
                return {"fetched": False, "why": f"not an image ({ctype or 'no content-type'})"}
            ext = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/webp": ".webp"}.get(ctype.split(";")[0], ".img")
            body = b""
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > 8 * 1024 * 1024:
                    return {"fetched": False, "why": "image larger than 8MB"}
            if len(body) < 1024:
                return {"fetched": False, "why": f"only {len(body)} bytes - not a usable image"}
            path = os.path.join(dest_dir, f"{candidate['id']}{ext}")
            with open(path, "wb") as f:
                f.write(body)
            # Provenance travels with the file. A folder of images whose origin
            # is unknown cannot be licensed, audited, or trusted as labels.
            with open(path + ".provenance.json", "w") as f:
                json.dump({k: candidate.get(k) for k in
                           ("id", "label", "query", "source_url", "image_url",
                            "source_title", "retrieved_at", "reviewed_at")}, f, indent=2)
            return {"fetched": True, "path": path, "bytes": len(body)}
        except Exception as e:
            return {"fetched": False, "why": str(e)}

    def _leaf_patterns_in(self, text):
        """Every distribution the description matches, most specific first.

        Returning only the FIRST match dropped real evidence the moment a grower
        reported two things at once - "crispy burnt tips on that one leaf and
        the spots on the other" is two different causes on one plant, and
        carrying only one of them turns a complete report into a partial
        diagnosis. Two signs together also mean something a single sign does
        not: salt or light stress alongside a pest is a plant under two loads,
        and the pest is the one that multiplies."""
        low = (text or "").lower()
        found = []
        for name, rx, consistent_with, settle_it, klass in LEAF_PATTERNS:
            m = re.search(rx, low)
            if m:
                # A negated mention ("no stippling", "no burnt tips") is not a
                # sign. Test the text that actually matched - testing the
                # pattern's NAME silently skipped every pattern whose name was
                # not itself a word in the description, which was all of them
                # except stippling.
                if not self._negation_aware_hit(low, (m.group(0),)):
                    continue
                found.append({"pattern": name, "consistent_with": consistent_with,
                              "what_would_settle_it": settle_it, "classification": klass})
        return found

    def _leaf_pattern_hit(self, text):
        """The single most significant distribution, or None. A problem outranks
        senescence: an ageing lower leaf is not the headline when something is
        also feeding on the plant."""
        found = self._leaf_patterns_in(text)
        if not found:
            return None
        problems = [f for f in found if f["classification"] == "problem"]
        lead = problems[0] if problems else found[0]
        return dict(lead, also=[f for f in found if f is not lead])

    # A germinating seed fails in ways a leaf never does, and the care signs
    # built for leaves answered a seedling with "underwatered, light starved,
    # no urgent action" - a leaf verdict applied to something with no leaves.
    # These are the failures that are decidable from a description, each with
    # the observation that confirms it, because none can be settled from a
    # photo alone.
    GERMINATION_SIGNS = (
        ("etiolated",
         # The commonest way a seedling is lost, and it was missing from this
         # list entirely - so a stretched, unpigmented seedling was matched
         # against husk and orientation instead. A seedling reaches for light
         # it cannot find by spending its seed reserves on stem, and it only
         # gets one set of reserves.
         r"light ?starv\w*|etiolat\w*|leggy|stretch\w*|long (white|pale) (stem|hypocotyl)|"
         r"(pale|yellow|white|unpigmented|colourless|colorless)[^.]{0,30}"
         r"(cotyledon|seed ?leaves|leaves)|"
         r"(cotyledon|seed ?leaves)[^.]{0,25}(pale|yellow|never green\w*|not green)",
         "the seedling is spending its seed reserves reaching for light it is not "
         "getting. Cotyledons stay pale because they never got enough light to make "
         "chlorophyll, and the stem stretches - which is why a starved seedling is "
         "both taller and weaker than a healthy one of the same age",
         "Get light on it today, close - a seedling wants a bright source within "
         "reach, not a distant one. Then bury the stretched stem: the hypocotyl will "
         "root along its length, which shortens the plant back to a stable height and "
         "recovers the leverage it lost. Reserves are finite and are being spent now.",
         "urgent"),
        ("inverted",
         r"(cotyledon|hypocotyl|shoot|seed ?leaves|sprout)\w*[^.]{0,40}"
         r"(below|beneath|under|out of the bottom|hanging down|pointing down)|"
         r"(nothing|no)\s+(green|shoot|sprout)[^.]{0,30}(above|up)",
         "The seed may be oriented the wrong way up. A taproot grows DOWN and a "
         "shoot grows UP; a pale hooked structure below the net pot with nothing "
         "green above it is consistent with the seed having been set inverted, or "
         "with the shoot having been dragged down when the root took hold.",
         "Look at which end is which before moving anything. The root is white, "
         "featureless and tapers; the shoot end carries the two seed leaves and is "
         "thicker and often still wearing the husk. If the shoot is genuinely "
         "heading down, re-seat the seed with the root pointing down - a seedling "
         "can recover from this within the first few days and cannot once the "
         "hypocotyl has hardened.",
         "urgent"),
        ("helmet",
         # The alternation has to be grouped. Written flat, a bare "husk"
         # matched and "husk released cleanly" was reported as helmet head.
         r"(husk|shell|casing|helmet)[^.]{0,30}"
         r"(stuck|still on|clamped|attached|has ?n[o']t (come|released)|won'?t come)|"
         r"(stuck|still on|clamped|attached)[^.]{0,20}(husk|shell|casing|helmet)",
         "the seed casing has not released the cotyledons - 'helmet head'. The "
         "leaves cannot open while it is clamped shut",
         "Raise humidity around the seedling for a few hours to soften it, then "
         "ease it off with tweezers. Do not pull it dry; the cotyledons tear.",
         "soon"),
        ("root_short_of_water",
         r"root[^.]{0,40}(not reach|short of|above the water|does not touch|"
         r"dangl\w+|hanging)|gap[^.]{0,20}water",
         "the taproot may not be reaching solution yet. In low water culture the "
         "root has to bridge the gap on its own, and the medium above dries fast",
         "Keep the plug damp from the top until the root is visibly in the water. "
         "Raising the level to touch the net pot instead invites rot at the collar.",
         "soon"),
        ("damping_off",
         r"pinched|thin\w* at the (base|collar|soil line)|dark(ened)? at the (base|stem)|"
         r"collapsed at the (base|soil)|fell over",
         "damping off - a fungal collapse at the stem base that is fatal once "
         "established and moves fast in still, wet, warm conditions",
         "Increase airflow and let the surface dry between waterings. An affected "
         "seedling rarely recovers; the value of spotting it is protecting the "
         "others sharing the lid.",
         "urgent"),
    )

    def assess_germination(self, plant_id="current_plant", description=""):
        """Judge a germinating seed on its own terms.

        Everything else in this agent assumes leaves to look at. A seed has a
        root, a shoot and a husk, and it fails by orientation, by a husk that
        will not release, by a root that never reaches water, or by collapsing
        at the collar. None of those are visible to a leaf classifier."""
        text = (description or "").lower()
        stage = "unknown"
        plant = next((p for p in self._get_all_plants()
                      if p.get("plant_id") == plant_id), None)
        if plant:
            stage = plant.get("stage", "unknown")

        age_days = None
        try:
            g = (plant or {}).get("germination_date")
            if g:
                age_days = (datetime.now() - datetime.fromisoformat(g[:10])).days
        except Exception:
            pass

        found = []
        for name, rx, means, do, urgency in self.GERMINATION_SIGNS:
            m = re.search(rx, text)
            if not m:
                continue
            # Negation has to be judged on what sits immediately BEFORE the
            # match, not across the whole clause. Several of these signs are
            # inherently negative statements - "the root does not reach the
            # water" REPORTS the problem - and a clause-wide check threw them
            # away for containing the word "not".
            before = text[max(0, m.start() - 18):m.start()]
            if re.search(r"\b(no|not|never|without|free of|clear of)\b\s*$", before) or \
               re.search(r"\b(no|not|never|without)\b[^.]{0,12}$", before):
                continue
            found.append({"sign": name, "means": means,
                          "what_to_do": do, "urgency": urgency})

        out = {"plant_id": plant_id, "stage": stage, "age_days": age_days,
               "signs": [f["sign"] for f in found], "findings": found}
        if not description:
            out["assessment"] = "Nothing described - there is no evidence either way."
            out["confidence"] = "low"
            return out

        urgent = [f for f in found if f["urgency"] == "urgent"]
        if urgent:
            lead = urgent[0]
            out["assessment"] = f"{lead['sign'].replace('_', ' ').title()}: {lead['means']}"
            out["action"] = lead["what_to_do"]
            out["urgency"] = "act today"
        elif found:
            lead = found[0]
            out["assessment"] = f"{lead['sign'].replace('_', ' ').title()}: {lead['means']}"
            out["action"] = lead["what_to_do"]
            out["urgency"] = "soon"
        else:
            out["assessment"] = ("Nothing in the description matches a known germination "
                                 "failure. That is an absence of findings, not a finding "
                                 "of health - a seed gives very little to read.")
            out["urgency"] = "none"
        # A photo cannot settle which end is which. Say so rather than implying
        # the agent looked.
        out["confidence"] = "medium" if found else "low"
        out["limit"] = ("Judged from a description, not from the plant. Which end of a "
                        "seedling is root and which is shoot is the one thing worth "
                        "confirming by eye before acting on it.")
        return out

    # Root extension in aerated solution, veg, healthy plant. A GENERIC figure -
    # this grow has never measured its own, and the honest use of it is a
    # bracket, not a date.
    ROOT_EXTENSION_CM_PER_DAY = (1.0, 3.0)

    def infer_system_change(self, plant_id="current_plant"):
        """When the plant actually moved, derived from what it was mixed into.

        A plan is not an event: plan_system_transition writes what to do, and
        nothing writes that it was done. Reporting "not on the record" from
        that absence was wrong - the record holds it plainly, in the volume.
        You do not mix 14.9L of nutrients for a 5L bucket, so the first recipe
        at the new working volume IS the move, to within one feed.

        Derived, not asserted: it returns the bracket it can defend - the last
        record at the old volume and the first at the new one."""
        hist = sorted((self._get_nutrient_history(plant_id) or []),
                      key=lambda r: r.get("timestamp") or "")
        seen = []
        for r in hist:
            lit = self._parse_numeric(r.get("reservoir_liters"))
            ts = r.get("timestamp")
            if lit and ts:
                seen.append((ts, lit))
        if len(seen) < 2:
            return {"inferred": False, "why": "fewer than two recorded volumes"}
        for (prev_ts, prev_l), (ts, lit) in zip(seen, seen[1:]):
            if lit >= prev_l * 1.5:
                try:
                    days = (datetime.now() - datetime.fromisoformat(ts[:19])).days
                except Exception:
                    days = None
                return {"inferred": True,
                        "moved_on": ts, "days_since": days,
                        "from_liters": prev_l, "to_liters": lit,
                        "last_seen_at_old_volume": prev_ts,
                        "how": (f"First recipe mixed at {lit:g}L, after one at {prev_l:g}L. "
                                f"A {lit / prev_l:.1f}x jump in working volume is a change of "
                                "vessel, not a top-up, so the move is dated to that feed - "
                                "accurate to within one mixing."),
                        "confidence": "medium"}
        return {"inferred": False,
                "why": "no volume increase of 1.5x or more in the nutrient history"}

    def estimate_root_establishment(self, plant_id="current_plant", transitioned_on=None):
        """How long until roots bridge the medium and reach the reservoir.

        The question after a system change is not "did it survive" but "when is
        it drinking from the new vessel", and those are different dates. Two
        facts decide it and both are already on the record: how far the roots
        have to travel, and whether anything keeps the medium wet while they do.

        A plant whose roots were ALREADY free-hanging in solution is not
        colonising anything - it moved with its root mass intact and the gap is
        already bridged. That is a different answer from a plant starting in dry
        medium, and conflating them is how a grower gets told to wait a week for
        something that already happened."""
        sysraw = (self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}"))
                  or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                      if plant_id == "current_plant" else None))
        try:
            sysrec = json.loads(sysraw) if sysraw else {}
        except Exception:
            sysrec = {}

        gap_cm = self._parse_numeric(sysrec.get("net_pot_water_gap_cm"))
        contacts = sysrec.get("medium_contacts_water")
        top_feed = (sysrec.get("equipment") or {}).get("top_feed_ring")
        system = sysrec.get("system_label") or sysrec.get("system_type") or "unknown"

        out = {"plant_id": plant_id, "system": system,
               "net_pot_water_gap_cm": gap_cm,
               "medium_contacts_water": contacts,
               "top_feed": top_feed}

        # Was the root mass already in solution before the move?
        prior_free_hanging = None
        for key in self._load_note_index() or []:
            raw = self._unwrap_value(self.retrieve_own_memory(key))
            if raw and "free-hanging" in str(raw).lower():
                prior_free_hanging = True
                break
        plans = [k for k in ("transition_plan",) if k]
        out["roots_were_free_hanging_before_move"] = prior_free_hanging

        # Days since the move, if the record holds one.
        days, moved_on, how = None, None, None
        if transitioned_on:
            try:
                days = (datetime.now() - datetime.fromisoformat(str(transitioned_on)[:19])).days
                moved_on, how = transitioned_on, "supplied by the grower"
            except Exception:
                days = None
        if days is None:
            # Derive it. The volume the plant was last mixed into says when the
            # vessel changed, and reporting absence without checking that was
            # the bug here.
            inf = self.infer_system_change(plant_id)
            if inf.get("inferred"):
                days, moved_on, how = inf["days_since"], inf["moved_on"], inf["how"]
                out["transition_source"] = "derived from the nutrient history"
        out["days_since_transition"] = days
        out["moved_on"] = moved_on
        if how:
            out["how_that_date_was_reached"] = how
        if days is None:
            out["missing"] = (
                "No move is visible in the record - no plan, and no jump in working volume "
                "in the nutrient history. Tell me the date and it gets written down.")

        lo, hi = self.ROOT_EXTENSION_CM_PER_DAY
        if gap_cm:
            out["travel_cm"] = gap_cm
            out["days_to_reach_solution"] = [round(gap_cm / hi, 1), round(gap_cm / lo, 1)]
            out["rate_used"] = (f"{lo}-{hi} cm/day, a generic figure for veg in aerated "
                                "solution - NOT measured from this plant")
            out["confidence"] = "low"

        if prior_free_hanging:
            out["assessment"] = (
                "Roots were already free-hanging in solution before the move, so this is not "
                "colonisation from scratch. An intact root mass transferred with its net pot "
                "is drinking from the new reservoir immediately; what takes time is filling "
                "the larger volume, not reaching it.")
        elif gap_cm:
            out["assessment"] = (
                f"Roots have roughly {gap_cm:g} cm to travel from the base of the net pot to "
                f"the water line. At {lo}-{hi} cm/day that is "
                f"{round(gap_cm / hi, 1)}-{round(gap_cm / lo, 1)} days, faster at the warm end "
                "of the 18-22C band and slower below it.")
        else:
            out["assessment"] = (
                "The gap between the net pot and the water line is not recorded, and that "
                "distance is the whole of this question.")

        if top_feed:
            out["what_carries_it"] = (
                f"The {top_feed} top feed is what keeps the pebbles wet until the roots arrive. "
                "Its critical property: an airlift's lift depends on how deep the intake sits, "
                "so the feed WEAKENS AND STOPS WHILE THE RESERVOIR STILL HAS WATER IN IT. For a "
                "plant already drinking from solution that is cosmetic. For roots still crossing "
                "the gap it is fatal - the medium dries while the bucket still looks part full. "
                "Treat 'the top feed has gone quiet' as a refill trigger, not the water level.")

        out["what_confirms_it"] = (
            "Two independent signs, and the second is the one that does not lie: roots visible "
            "below the net pot when you lift the lid, and consumption climbing. A plant that has "
            "reached the reservoir drinks measurably faster - analyze_consumption separates the "
            "plant feeding from the solution concentrating, so a rising uptake against steady "
            "volume is the root mass arriving.")
        out["why_not_measured"] = (
            "No root-growth rate has ever been measured on this grow, so the bracket above is "
            "generic. A photo of the root zone at the same angle every few days would give this "
            "plant its own number within a week.")
        return out

    def photo_cadence(self, plant_id="current_plant"):
        """How often a photo is worth taking, and what it actually buys.

        Asked as "how often should I post pictures to help train you". The
        honest answer needs three separate facts that are easy to conflate:
        what a photo feeds today, what it does NOT feed, and whether anything
        is learning from it. Answering with a number alone would imply a
        training loop that does not exist."""
        species = (self._get_species_for_plant(plant_id) or "unknown").lower()
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        if plant_id != "current_plant":
            p = next((x for x in self._get_all_plants()
                      if x.get("plant_id") == plant_id), None)
            stage = (p or {}).get("stage", "unknown")

        # What this plant's photos have actually produced.
        evals = read = 0
        try:
            for e in self._get_all_leaf_evals():
                if e.get("plant_id") != plant_id:
                    continue
                evals += 1
                rec = e.get("recommendation") or {}
                blob = f"{rec.get('observation','')}{rec.get('reason','')}"
                if "No reliable read" not in blob and "no class" not in blob:
                    read += 1
        except Exception as exc:
            self.log(f"photo_cadence: could not count evals: {exc}")

        corrections = len(self._get_all_vision_corrections() or [])
        pathogen_named = species in self.PATHOGEN_MODEL_SPECIES

        out = {
            "plant_id": plant_id, "species": species, "stage": stage,
            "photos_assessed": evals, "produced_a_finding": read,
            "retraining_cases_logged": corrections,
            "pathogen_naming_available": pathogen_named,
        }

        out["what_a_photo_buys_now"] = (
            "A care read: visible symptoms - browning, wilting, stretch, curl - "
            "described and matched against what is normal for this species. "
            + (f"{read} of {evals} photos of this plant produced one."
               if evals else "No photos of this plant have been assessed yet."))

        if not pathogen_named:
            out["limit"] = (
                f"No pathogen can be NAMED for {species}: the local disease models carry "
                f"classes for {', '.join(self.PATHOGEN_MODEL_SPECIES)} only. That does not "
                "block a symptom description - those are visible on any plant - but it does "
                "mean a photo will not come back with a disease name.")

        # The part that answers "to help predictions".
        out["what_a_photo_does_NOT_feed"] = (
            "Stage assessment and prediction scoring do not read photos. Stage is derived "
            "from age and reading history; predictions are scored against measured ppm, pH "
            "and temperature. So more photos will not sharpen a stage call or a drawdown "
            "projection - only readings and time do that.")

        out["recommended"] = (
            "Symptom-driven, not calendar-driven: photograph when something LOOKS different, "
            "and once at each stage transition as a baseline. A weekly frame of a plant that "
            "looks the same as last week adds a duplicate, not evidence.")
        out["why"] = (
            "A care read is a comparison against normal, so its value comes from catching "
            "change. Photos taken on a schedule while nothing changes produce near-identical "
            "assessments, and 20 photos of one healthy plant teach less than 5 taken at "
            "moments that differ.")

        # "To help train" - say plainly whether that loop exists.
        out["training_status"] = (
            f"{corrections} low-confidence case(s) have been logged for future retraining, "
            "but nothing consumes them: datasets/ and weights/ are empty and no training run "
            "has happened. So photos are not currently improving the model for this or any "
            "other cannabis plant - they are being kept so that they can.")
        out["what_would_change_that"] = (
            "Variety is what a training corpus needs, not volume: different stages, "
            "different symptoms, consistent framing and light. If the aim is a cannabis-aware "
            "model, photographs of a DEFICIENCY or a pest are worth many of a healthy plant, "
            "because a corpus of healthy frames teaches a model to say 'fine'.")
        out["applies_to"] = (
            "This holds for any cannabis plant, not just this one - the pathogen models cover "
            "no cultivar, and the stage clock is per plant but photo-blind either way.")
        return out

    def reading_cadence(self, plant_id="current_plant"):
        """How often this system needs a reading, derived from what its own
        analyses require rather than from a generic recommendation.

        Every number here is a constraint that already exists in this file. The
        point is that cadence is not a preference - each analysis has a minimum
        spacing below which it cannot conclude anything, and a maximum gap above
        which it cannot prove anything.

        The two failure directions are different and both real:

        TOO CLOSE wastes effort and produces nothing. analyze_consumption
        refuses a window under 24h because a 15L reservoir shifts a few percent
        a day and volume read off an unmarked sight tube is good to about
        +/-10%. Two readings an hour apart cannot separate uptake from
        measurement error, so the extra reading buys no information.

        TOO FAR APART loses evidence permanently. analyze_deficit could only
        prove 9 days of a deficit that ran longer, because a 9-day gap between
        readings contains no measurement to attribute to either side of it. The
        plant was underfed the whole time; the record can only defend part of
        it."""
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
        if plant_id != "current_plant":
            p = next((x for x in self._get_all_plants()
                      if x.get("plant_id") == plant_id), None)
            stage = (p or {}).get("stage", "unknown")
        sched = MONITORING_SCHEDULE.get(str(stage).lower())
        target_days = sched["interval_days"] if sched else 3

        # What the grower is ACTUALLY doing, from the record.
        readings = sorted([r for r in self._get_readings_for_plant(plant_id)
                           if r.get("timestamp")], key=lambda r: r["timestamp"])
        gaps = []
        for a, b in zip(readings, readings[1:]):
            try:
                h = (datetime.fromisoformat(b["timestamp"][:19])
                     - datetime.fromisoformat(a["timestamp"][:19])).total_seconds() / 3600
                if h > 0:
                    gaps.append(h)
            except Exception:
                continue
        observed = None
        if gaps:
            gaps_sorted = sorted(gaps)
            median_h = gaps_sorted[len(gaps_sorted) // 2]
            observed = {
                "readings": len(readings),
                "median_gap_days": round(median_h / 24, 1),
                "longest_gap_days": round(max(gaps) / 24, 1),
                "gaps_over_target": sum(1 for g in gaps if g / 24 > target_days),
            }

        return {
            "plant_id": plant_id,
            "stage": stage,
            "recommended_days": target_days,
            "recommended_because": (
                f"{stage} wants a reading every {target_days} day(s). Intervals shorten as the "
                "plant grows because consumption is non-linear: a weekly check is ample while the "
                "plant is small relative to the reservoir and leaves it starving for days once "
                "the root mass fills it."),
            "minimum_useful_spacing_hours": MIN_CONSUMPTION_WINDOW_HOURS,
            "minimum_because": (
                f"Readings closer than {MIN_CONSUMPTION_WINDOW_HOURS}h cannot measure uptake. "
                f"A reservoir this size shifts a few percent a day and volume off an unmarked "
                f"sight tube is good to about +/-10%, so anything under the "
                f"{CONSUMPTION_NOISE_FLOOR_PCT:.0f}% noise floor is measurement error, not "
                "consumption. The extra reading costs effort and buys nothing."),
            "maximum_useful_gap_days": target_days * 2,
            "maximum_because": (
                "A gap contains no measurement, so nothing inside it can be attributed to either "
                "side. This is not hypothetical here: the deficit analysis can only prove 9 days "
                "of an underfeed that ran longer, because a 9-day gap sits inside it."),
            "observed": observed,
            "sensors": {
                "ph_temp": ("Hourly is genuinely useful. These move on that timescale and are "
                            "measured precisely, so more samples means real signal."),
                "ppm_volume": ("Hourly is mostly noise. Log an hourly sensor as a DAILY "
                               "aggregate - min, max and mean - rather than as 24 readings, or "
                               "the resolution guards will correctly refuse almost all of it and "
                               "the record fills with rows that cannot support a conclusion."),
                "why": ("More data is only better when it is above the resolution of the question "
                        "being asked. Sampling faster than the thing changes does not add "
                        "information, it adds rows."),
            },
        }

    def analyze_deficit(self, plant_id="current_plant"):
        """What a period below target actually cost, measured rather than guessed.

        The grower asked "what is the expected loss for plant one being stagnant
        for two weeks" and got a status card. It is a fair question and the data
        to answer part of it is already recorded - but only part, and the honest
        answer has to separate the two.

        What CAN be measured: how many days each reading sat below the band its
        stage required, and by how much. That is arithmetic over the record.

        What CANNOT: yield in grams. Nothing here has ever weighed a harvest, so
        any percentage would be invented. Growth response to nutrient
        availability is non-linear, strain-specific and confounded by light,
        root health and temperature, and a number with no measurement behind it
        would be exactly the false confidence the rest of this agent refuses to
        produce."""
        stage_hist = []
        readings = [r for r in self._get_readings_for_plant(plant_id)
                    if self._parse_numeric(r.get("ppm")) is not None]
        if len(readings) < 2:
            return {"error": "Not enough readings to characterise a deficit."}
        readings.sort(key=lambda r: r.get("timestamp") or "")

        germ = self._unwrap_value(self.retrieve_own_memory("germination_date"))
        if isinstance(germ, str):
            germ = germ.strip().strip('"')

        periods, current = [], None
        for r in readings:
            ppm = self._parse_numeric(r.get("ppm"))
            stage = (r.get("stage") or "unknown").lower()
            band = STAGE_TARGETS.get(stage, {}).get("ppm")
            if not band:
                continue
            lo, hi = band
            below = ppm < lo
            ts = r.get("timestamp")
            if below:
                if current is None:
                    current = {"from": ts, "to": ts, "stage": stage, "band": [lo, hi],
                               "min_ppm": ppm, "max_gap": lo - ppm, "readings": 1}
                else:
                    current["to"] = ts
                    current["readings"] += 1
                    current["min_ppm"] = min(current["min_ppm"], ppm)
                    current["max_gap"] = max(current["max_gap"], lo - ppm)
                    if stage != current["stage"]:
                        current["stage"] = f"{current['stage']}->{stage}"
                        current["band"] = [lo, hi]
            elif current:
                periods.append(current)
                current = None
        if current:
            periods.append(current)
        if not periods:
            return {"plant_id": plant_id, "deficit_periods": [],
                    "finding": "No reading has sat below its stage band."}

        def days(a, b):
            try:
                return max(0, (datetime.fromisoformat(b[:19]) - datetime.fromisoformat(a[:19])).days)
            except Exception:
                return 0

        total_days = 0
        for p_ in periods:
            p_["days"] = days(p_["from"], p_["to"])
            total_days += p_["days"]
            p_["from"] = p_["from"][:10]
            p_["to"] = p_["to"][:10]

        worst = max(periods, key=lambda x: x["days"])
        strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
        auto = "auto" in str(strain).lower()

        consequence = [
            (f"{total_days} day(s) below target across {len(periods)} period(s). The longest ran "
             f"{worst['days']} day(s) in {worst['stage']}, bottoming at {worst['min_ppm']:.0f} ppm "
             f"against a {worst['band'][0]}-{worst['band'][1]} band - {worst['max_gap']:.0f} ppm short."),
            ("Below-target feeding does not damage the plant, it slows it. Less nitrogen and "
             "magnesium available means less leaf area built, and less leaf area means less "
             "photosynthesis for the days that follow, so the shortfall compounds while it lasts."),
        ]
        if auto:
            consequence.append(
                "This is an autoflower, which is what makes it matter. A photoperiod plant is "
                "simply vegged longer to make the growth back. An autoflower starts flowering on "
                "a genetic clock regardless, so the biomass not built during those days is the "
                "biomass it enters flower with - and final yield tracks the size at flower onset "
                "more than anything that happens after.")
        return {
            "plant_id": plant_id,
            "total_days_below_target": total_days,
            "deficit_periods": periods,
            "worst_period": worst,
            "consequence": " ".join(consequence),
            # The line this agent will not cross.
            "yield_estimate": None,
            "why_no_number": ("No harvest has ever been weighed here, so there is nothing to "
                              "calibrate a percentage against. Growth response to nutrient "
                              "availability is non-linear, strain-specific, and confounded by "
                              "light, root health and temperature. A figure would be invented, "
                              "and an invented figure is worse than the measured deficit above, "
                              "which is real."),
            "what_would_make_it_answerable": (
                "Weigh this harvest and record it against this deficit. One data point does not "
                "make a model, but it is the first one, and after a few grows the agent can say "
                "what a deficit of this size cost THIS grower on THIS strain - which is worth "
                "more than any published figure."),
        }

    def acquire_plant(self, plant_id, stage, species="cannabis", strain="",
                      source="", prior_system="", prior_medium="",
                      estimated_age_days=None, note=""):
        """Register a plant that arrived already growing.

        set_germination_date refuses without a date, which is right for a seed
        you started and wrong for a plant someone hands you: you know its stage,
        maybe roughly its age, and nothing about the day it cracked.

        The germination date is therefore ESTIMATED and marked as such, because
        the difference matters downstream. assess_stage uses age to decide
        whether a recorded stage is impossible, and it must not transition a
        plant on the strength of a date that was inferred from the very stage it
        is checking - that is circular. Everything before acquisition is
        recorded as unknown rather than absent, so the agent can say what it does
        not know instead of reasoning as though the history were complete."""
        if not plant_id:
            return {"error": "Missing plant_id"}
        if not stage:
            return {"error": ("Missing stage. For an acquired plant the stage on arrival is "
                              "what is actually observable - the germination date is not.")}
        allowed, _ = self.stages_for_species(species)
        if stage not in allowed:
            return {"error": f"'{stage}' is not a stage for {species}. Valid: {', '.join(allowed)}"}

        est_age = self._parse_numeric(estimated_age_days)
        inferred = est_age is None
        if inferred:
            est_age = self.STAGE_AGE_MIDPOINT.get(str(stage).lower())
        germ_est = None
        if est_age:
            germ_est = (datetime.now() - timedelta(days=int(est_age))).date().isoformat()

        acquired_at = datetime.now().isoformat()
        record = {
            "plant_id": plant_id,
            "species": species,
            "strain": strain or "Unknown",
            "stage": stage,
            "status": "active",
            "logged_at": acquired_at,
            "acquired_at": acquired_at,
            "origin": "acquired",
            "source": source or "unknown",
            "germination_date": germ_est,
            # The flag other reasoning has to respect. A date worked backwards
            # from a stage cannot then be used as evidence about that stage.
            "germination_date_estimated": True,
            "age_basis": ("grower's estimate" if not inferred
                          else f"inferred from arriving at '{stage}'"),
            "prior_system": prior_system or "unknown",
            "prior_medium": prior_medium or "unknown",
            "history_known_from": acquired_at,
            "history_before_acquisition": "unknown - not observed by this system",
            "note": note,
        }
        self.store_own_memory(f"plant_{plant_id}", json.dumps(record))
        index = self._load_plant_index()
        if plant_id not in index:
            index.append(plant_id)
            self.store_own_memory("plant_index", json.dumps(index))

        advisories = [
            ("Nothing is known about this plant before today. Feed response, past "
             "deficiencies and how it was watered are all unobserved, so early "
             "recommendations lean on the stage rather than on its history."),
        ]
        if germ_est:
            advisories.append(
                f"Germination estimated at {germ_est} ({record['age_basis']}). Treated as an "
                "estimate everywhere - stage will not be auto-corrected from it.")
        if prior_medium and prior_medium.lower() in ("soil", "coco"):
            advisories.append(
                f"Coming out of {prior_medium}: roots grown in medium are structurally different "
                "from water roots, and the changeover is the risky part, not the destination. "
                "Use plan_system_transition before moving it.")
        return {"result": f"Acquired {plant_id} at stage '{stage}'", "plant": record,
                "advisories": advisories}

    def archive_plant(self, plant_id, status="harvested", note="", outcome=None,
                      photos="purge"):
        """Retire a plant without erasing it.

        remove_plant called forget_own_memory on the record, so handing a plant
        to someone else deleted everything it had taught: its readings, the
        recipes that worked, the corrections. A plant leaving the tent is the
        moment its history becomes most useful, because the outcome is finally
        known and can be attached to everything that led to it."""
        if status not in PLANT_STATUSES:
            return {"error": f"status must be one of: {', '.join(PLANT_STATUSES)}"}
        if plant_id == "current_plant":
            return {"error": ("current_plant is the legacy single-plant slot, not an "
                              "archivable identity. Give it a real plant_id first.")}
        raw = self._unwrap_value(self.retrieve_own_memory(f"plant_{plant_id}"))
        if not raw:
            return {"error": f"Unknown plant_id: {plant_id}"}
        try:
            rec = json.loads(raw)
        except Exception:
            return {"error": f"Could not read record for {plant_id}"}

        readings = self._get_readings_for_plant(plant_id)
        recipes = self._get_nutrient_history(plant_id)
        rec.update({
            "status": status,
            "archived_at": datetime.now().isoformat(),
            "archive_note": note,
            "outcome": outcome or {},
            # A compact summary so the record stays useful without rereading
            # everything it points at.
            "summary": {
                "readings": len(readings),
                "recipe_changes": len(recipes),
                "first_reading": (readings[0].get("timestamp") if readings else None),
                "last_reading": (readings[-1].get("timestamp") if readings else None),
                "final_stage": rec.get("stage"),
            },
        })
        self.store_own_memory(f"plant_{plant_id}", json.dumps(rec))

        index = self._load_plant_index()
        if plant_id in index:
            index.remove(plant_id)
            self.store_own_memory("plant_index", json.dumps(index))
        arch = self._unwrap_value(self.retrieve_own_memory("plant_archive_index"))
        try:
            arch = json.loads(arch) if arch else []
        except Exception:
            arch = []
        if plant_id not in arch:
            arch.append(plant_id)
            self.store_own_memory("plant_archive_index", json.dumps(arch))

        # Photos. Once the lessons are out of them, the images are just bytes:
        # the agent does not reread a picture to know what nutrient burn looked
        # like on day 12 - that is in the evaluation record and the lesson.
        #
        # The one thing lost by deleting is TRAINING data, since a local vision
        # model fine-tuned on this grow would want the originals. So "train"
        # moves them into the training set instead of destroying them, and the
        # freed figure is always reported so the trade is visible.
        photo_paths = self._plant_photos(plant_id)
        freed = 0
        moved = []
        if photos in ("purge", "train"):
            train_dir = os.path.join(project_root, "knowledge_base", "grow_agent",
                                     "training", "archived", plant_id)
            for pth in photo_paths:
                try:
                    if not os.path.exists(pth):
                        continue
                    size = os.path.getsize(pth)
                    if photos == "train":
                        os.makedirs(train_dir, exist_ok=True)
                        dest = os.path.join(train_dir, os.path.basename(pth))
                        os.replace(pth, dest)
                        moved.append(dest)
                    else:
                        os.remove(pth)
                    freed += size
                except Exception as e:
                    self.log(f"could not {photos} {pth}: {e}")
        rec["photos"] = {"disposition": photos, "count": len(photo_paths),
                         "freed_mb": round(freed / 1e6, 1),
                         "moved_to_training": len(moved)}
        self.store_own_memory(f"plant_{plant_id}", json.dumps(rec))

        return {"result": f"{plant_id} archived as {status}", "plant": rec,
                "photos_freed_mb": round(freed / 1e6, 1),
                "note": ("History kept under the same id. Readings, recipes and lessons stay "
                         "queryable; the plant just stops counting as active, so positions "
                         "like 'plant one' now refer to what is still growing.")}

    def active_plants(self):
        """Plants still growing, in the order they were started. What a position
        like "plant one" actually refers to."""
        out = []
        strain = self._unwrap_value(self.retrieve_own_memory("current_strain"))
        stage = self._unwrap_value(self.retrieve_own_memory("current_stage"))
        if strain or stage:
            out.append({"plant_id": "current_plant", "strain": strain,
                        "stage": stage, "status": "active"})
        for pid in self._load_plant_index():
            raw = self._unwrap_value(self.retrieve_own_memory(f"plant_{pid}"))
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if rec.get("status", "active") == "active":
                out.append(rec)
        return out

    def archived_plants(self):
        raw = self._unwrap_value(self.retrieve_own_memory("plant_archive_index"))
        try:
            ids = json.loads(raw) if raw else []
        except Exception:
            ids = []
        out = []
        for pid in ids:
            r = self._unwrap_value(self.retrieve_own_memory(f"plant_{pid}"))
            if r:
                try:
                    out.append(json.loads(r))
                except Exception:
                    pass
        return out

    def temp_verdict(self, species, temp_f=None, temp_c=None):
        """Whether temperature can explain a symptom, stated either way.

        Ruling a cause OUT is as useful as ruling it in. An aloe browning at 72F
        is not cold-stressed - aloe is comfortable to about 50F - and leaving
        that hypothesis open sends the grower after the wrong fix."""
        if temp_f is None and temp_c is not None:
            temp_f = temp_c * 9 / 5 + 32
        profile = self._profile_for(species)
        band = (profile or {}).get("temp_f_ok")
        if temp_f is None or not band:
            return None
        lo, hi = band
        if temp_f < lo:
            return {"temp_f": round(temp_f), "verdict": "too cold",
                    "note": f"Below {lo}F, cold damage is plausible for a {profile['common_name']}."}
        if temp_f > hi:
            return {"temp_f": round(temp_f), "verdict": "too hot",
                    "note": f"Above {hi}F, heat stress is plausible."}
        return {"temp_f": round(temp_f), "verdict": "fine",
                "note": (f"{round(temp_f)}F is inside the {lo}-{hi}F band "
                         f"{'an' if profile['common_name'][0].lower() in 'aeiou' else 'a'} "
                         f"{profile['common_name']} is comfortable in, so temperature does NOT "
                         "explain the symptoms. Look elsewhere.")}

    def assess_care(self, description, species=None, plant_id=None,
                    temp_f=None, temp_c=None, light_note=None):
        """Species-aware care reading from a plain description of the plant.

        Deliberately does NOT need a disease model. Whether something is
        shrivelled, mushy, leggy or bleached is visible on any plant; what makes
        it actionable is knowing what normal is for THIS species, which is the
        profile table."""
        if plant_id and not species:
            species = self._get_species_for_plant(plant_id)
        profile = self._profile_for(species)
        signs = self._care_signs_in(description)

        if not profile and species:
            # Do not stop at "I have no profile for this". The agent has search;
            # an unfamiliar plant is a cue to go and look, not a dead end.
            self.log(f"no profile for {species} - looking it up")
            learned = self.learn_species_profile(species)
            if learned.get("learned"):
                profile = self._profile_for(species)

        if not profile:
            return {
                "species": species,
                "known_profile": False,
                "signs": signs,
                "assessment": (f"No care profile for {species or 'this plant'}, so what is normal "
                               "for it is unknown. " +
                               (f"Visible signs: {', '.join(signs)}." if signs
                                else "Nothing in the description flags a care problem.")),
                "confidence": "low",
                "resolve_with": "Add a profile for this species so the signs can be interpreted.",
            }

        temp = self.temp_verdict(species, temp_f=temp_f, temp_c=temp_c)
        lowered = (description or "").lower()
        advanced = any(c in lowered for c in SEVERITY_CUES["advanced"])
        widespread = any(c in lowered for c in SEVERITY_CUES["widespread"])
        severity = ("advanced" if advanced else "early") if signs else "none"

        lines, actions = [], []
        for sign in signs:
            reading = (profile.get("reads_differently") or {}).get(sign)
            if reading:
                lines.append(f"{sign.replace('_',' ')}: {reading}")
            else:
                lines.append(f"{sign.replace('_',' ')} noted.")
            if sign == profile.get("kills_it"):
                article = "an" if profile['common_name'][0].lower() in "aeiou" else "a"
                actions.append(f"This is the direction that usually kills {article} {profile['common_name']}. "
                               "Treat it as urgent.")
            elif advanced:
                # A sign in the "safe" direction stops being safe once it has
                # gone this far. Tissue that is papery is not coming back, and
                # the reassurance that it is "easily corrected" is wrong.
                actions.append(f"This has gone past the early stage - dried or collapsed tissue "
                               f"does not recover. Act now rather than waiting; the plant is "
                               f"further along than the sign alone suggests"
                               + (" and it is across most of the plant." if widespread else "."))

        if not signs:
            art = "an" if profile['common_name'][0].lower() in "aeiou" else "a"
            lines.append(f"Nothing in the description flags a care problem for {art} "
                         f"{profile['common_name']}.")

        return {
            "species": species,
            "profile": profile["common_name"],
            "group": profile["group"],
            "known_profile": True,
            "signs": signs,
            **({"temperature": temp} if temp else {}),
            "severity": severity,
            "widespread": widespread,
            "assessment": " ".join(lines),
            "care": {"water": profile["water"], "light": profile["light"], "soil": profile["soil"]},
            "note": profile.get("note"),
            "action": " ".join(actions) if actions else "No urgent action indicated.",
            # A description is somebody's words about a photo, not a measurement.
            "confidence": "high" if (signs and advanced) else ("medium" if signs else "low"),
            "basis": "Care profile plus the description given. Reference, not a measurement - "
                     "what the plant is actually doing outranks this table.",
        }

    def _get_species_for_plant(self, plant_id):
        """Species for a plant, or None when it genuinely is not known.

        This used to default to "cannabis" for anything unrecognised, which is
        how an aloe photo would have been described to the vision model as a
        25-day-old Girl Scout Cookies autoflower in deep water culture. A default
        that asserts a species is worse than no species: the guard that refuses
        to classify an unsupported plant relies on being told the truth, and the
        prompt builder repeats whatever it is given."""
        if plant_id == "current_plant":
            return self._unwrap_value(self.retrieve_own_memory("current_species")) or "cannabis"
        plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
        if plant is None:
            return None
        return plant.get("species") or None

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

    def _extract_assertions(self, text):
        """Falsifiable claims in a prediction. Returns [] when the prediction
        says nothing checkable - "nitrogen-forward weighting" is a real
        intention and not a testable one."""
        if not text:
            return []
        found = []
        for m in _RANGE_RE.finditer(text):
            metric = _canonical_metric(m.group("lead"), m.group("trail"))
            if not metric:
                continue
            lo, hi = float(m.group("lo")), float(m.group("hi"))
            if lo > hi:
                lo, hi = hi, lo
            found.append({"metric": metric, "kind": "range", "lo": lo, "hi": hi,
                          "text": m.group(0).strip()})
        for m in _BOUND_RE.finditer(text):
            metric = _canonical_metric(m.group("unit"))
            if not metric:
                # The metric often precedes the bound rather than following the
                # number - "keep ppm above 600" names it before "above". Look
                # back a short way rather than only forward.
                back = re.findall(r'(ph|ppm|ec|temp|temperature|humidity)',
                                  text[max(0, m.start() - 30):m.start()], re.I)
                metric = _canonical_metric(back[-1]) if back else None
            if not metric:
                continue
            d = m.group("dir").lower()
            found.append({"metric": metric, "kind": "min" if d in ("above", "over", "at least") else "max",
                          "value": float(m.group("val")), "text": m.group(0).strip()})
        # de-duplicate overlapping matches on the same metric+span
        seen, out = set(), []
        for a in found:
            key = (a["metric"], a["kind"], a.get("lo"), a.get("hi"), a.get("value"))
            if key not in seen:
                seen.add(key)
                out.append(a)
        return out

    def _check_assertion(self, assertion, observed):
        """held / failed / inconclusive against one observed value."""
        metric = assertion["metric"]
        noise = MEASUREMENT_NOISE.get(metric, 0.0)
        if assertion["kind"] == "range":
            lo, hi = assertion["lo"], assertion["hi"]
            if lo - noise <= observed <= hi + noise:
                # inside, but is it too close to a boundary to be sure?
                if observed < lo or observed > hi:
                    return "inconclusive", (f"{observed:g} sits within measurement noise "
                                            f"(+/-{noise:g}) of the {lo:g}-{hi:g} band")
                return "held", f"{observed:g} is inside {lo:g}-{hi:g}"
            return "failed", f"{observed:g} is outside {lo:g}-{hi:g}"
        target = assertion["value"]
        if assertion["kind"] == "min":
            if observed >= target + noise:
                return "held", f"{observed:g} is above {target:g}"
            if observed <= target - noise:
                return "failed", f"{observed:g} is below {target:g}"
            return "inconclusive", f"{observed:g} is within noise (+/-{noise:g}) of {target:g}"
        if observed <= target - noise:
            return "held", f"{observed:g} is below {target:g}"
        if observed >= target + noise:
            return "failed", f"{observed:g} is above {target:g}"
        return "inconclusive", f"{observed:g} is within noise (+/-{noise:g}) of {target:g}"

    def _collect_predictions(self, plant_id):
        """Every recorded expected_effect for this plant, from any record type
        that carries reasoning_context."""
        preds = []
        for entry in self._get_nutrient_history(plant_id):
            ctx = entry.get("reasoning_context") or {}
            if ctx.get("expected_effect"):
                preds.append({"source": "nutrient_change",
                              "timestamp": entry.get("timestamp") or entry.get("changed_at"),
                              "expected_effect": ctx["expected_effect"],
                              "decision": ctx.get("decision"),
                              "confidence": ctx.get("confidence")})
        for r in self._get_readings_for_plant(plant_id):
            ctx = r.get("reasoning_context") or {}
            if ctx.get("expected_effect"):
                preds.append({"source": "reading",
                              "timestamp": r.get("timestamp"),
                              "expected_effect": ctx["expected_effect"],
                              "decision": ctx.get("decision"),
                              "confidence": ctx.get("confidence")})
        return [p for p in preds if p.get("timestamp")]

    def _score_prediction(self, pred, readings):
        """Score one prediction against every reading that came AFTER it."""
        assertions = self._extract_assertions(pred["expected_effect"])
        if not assertions:
            return {**pred, "verdict": "unscorable",
                    "why": ("no falsifiable claim in the prediction - it states an "
                            "intention rather than a measurable outcome"),
                    "checks": []}
        later = [r for r in readings if (r.get("timestamp") or "") > pred["timestamp"]]
        if not later:
            return {**pred, "verdict": "undetermined",
                    "why": "no reading recorded after this prediction yet",
                    "checks": []}
        checks = []
        for a in assertions:
            vals = [(r["timestamp"], self._parse_numeric(r.get(a["metric"])))
                    for r in later if self._parse_numeric(r.get(a["metric"])) is not None]
            if not vals:
                checks.append({**a, "verdict": "undetermined",
                               "why": f"no {a['metric']} reading after this prediction"})
                continue
            ts, observed = vals[0]           # the first observation that could test it
            verdict, why = self._check_assertion(a, observed)
            checks.append({**a, "verdict": verdict, "why": why,
                           "observed": observed, "observed_at": ts})
        decided = [c["verdict"] for c in checks if c["verdict"] in ("held", "failed")]
        if not decided:
            overall = "inconclusive" if any(c["verdict"] == "inconclusive" for c in checks) else "undetermined"
        elif all(v == "held" for v in decided):
            overall = "held"
        elif all(v == "failed" for v in decided):
            overall = "failed"
        else:
            overall = "mixed"
        return {**pred, "verdict": overall, "checks": checks,
                "why": "; ".join(c["why"] for c in checks if c.get("why"))}

    def _last_known_param(self, param, readings):
        """Most recent recorded value for a parameter and its age in days.

        Searches back through history rather than looking only at the latest
        reading, because a reading that omits a field does not erase what was
        measured before it."""
        for r in sorted(readings, key=lambda x: x.get("timestamp") or "", reverse=True):
            v = r.get(param)
            if v is None:
                continue
            try:
                age = (datetime.now() - datetime.fromisoformat(r["timestamp"][:19])).days
            except Exception:
                age = None
            return v, age
        return None, None

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

        if task == "score_predictions":
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            readings = sorted(self._get_readings_for_plant(plant_id),
                              key=lambda r: r.get("timestamp") or "")
            preds = self._collect_predictions(plant_id)
            if not preds:
                return {"plant_id": plant_id, "scored": 0,
                        "note": ("No prediction has been recorded for this plant. A prediction "
                                 "is an expected_effect attached to a decision - without one "
                                 "there is nothing to grade.")}
            scored = [self._score_prediction(p, readings) for p in preds]
            tally = {}
            for r in scored:
                tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
            decided = tally.get("held", 0) + tally.get("failed", 0)
            result = {
                "plant_id": plant_id,
                "scored": len(scored),
                "tally": tally,
                # Deliberately absent when nothing was decidable. A hit rate over
                # zero decided predictions is not 0% and not 100% - it is unknown,
                # and reporting a number there would be exactly the false
                # confidence the resolution guards exist to prevent.
                "hit_rate": round(tally.get("held", 0) / decided, 2) if decided else None,
                "predictions": scored,
            }
            # A hit rate over a handful of predictions describes those
            # predictions, not the agent's reliability. Same discipline as the
            # consumption resolution floor: report the number, refuse the
            # inference the sample cannot carry.
            if decided and decided < MIN_PREDICTIONS_FOR_RELIABILITY:
                result["reliability"] = (
                    f"{decided} decided prediction(s) - too few to characterise how "
                    f"reliable this agent's expectations are. The rate describes these "
                    f"predictions only.")
            if tally.get("unscorable"):
                result["note"] = (f"{tally['unscorable']} prediction(s) state an intention rather "
                                  "than a measurable outcome. Writing expected_effect with a "
                                  "number and a unit makes it gradeable.")
            # The scorecard is itself evidence - an assessment, not an event.
            uid = self._uid()
            self.store_own_memory(f"scorecard_{uid}", json.dumps({
                "timestamp": datetime.now().isoformat(),
                "plant_id": plant_id, "evidence_kind": "assessment",
                "tally": tally, "hit_rate": result["hit_rate"],
            }))
            return result

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
            ctx = self._reasoning_context(args)
            if ctx:
                reading["reasoning_context"] = ctx
            # Every reading is a chance to notice the stage is wrong. Doing this
            # only when asked is how a 25-day-old plant stayed recorded as a
            # seedling through a month of readings and several photos.
            try:
                stage_check = self.assess_stage(reading.get("plant_id", "current_plant"))
            except Exception as e:
                self.log(f"stage self-assessment failed: {e}")
                stage_check = None
            # Also compute VPD if temp and humidity are present
            temp = self._parse_numeric(args.get("temp"))
            humidity = self._parse_numeric(args.get("humidity"))
            if temp is not None and humidity is not None:
                reading["vpd"] = calculate_vpd(temp, humidity)
            # Compute the key ONCE. Calling _uid() again for the index produced a
            # different microsecond value, so the index pointed at keys that did
            # not exist and readings were saved but orphaned. At the old second
            # granularity the two calls happened to agree, which hid the bug.
            reading_key = f"reading_{self._uid()}"
            self.store_own_memory(reading_key, json.dumps(reading))
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
            if reading_key not in index:
                index.append(reading_key)
            self.store_own_memory("reading_index", json.dumps(index))
            out = {"result": "Reading logged", "reading": reading}
            # Every reading is checked against what THIS stage needs. Reporting a
            # number without saying whether it is the right number is what let a
            # grow sit 200ppm under target for nineteen days.
            try:
                drift = self.check_target_drift(reading.get("plant_id", "current_plant"))
                if drift.get("applicable") and drift.get("status") != "in_band":
                    out["off_target"] = drift
            except Exception as e:
                self.log(f"target drift check failed: {e}")
            # Surfaced on the reading itself so a stage correction is visible at
            # the moment it happens, not only to whoever thinks to ask later.
            if stage_check and stage_check.get("acted"):
                out["stage_corrected"] = stage_check
            elif stage_check and stage_check.get("suggested"):
                out["stage_suggestion"] = stage_check
            return out

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
            # A stage must belong to THIS species' lifecycle. "veg" is meaningless
            # for an aloe, and accepting it is how the aloe ended up recorded in a
            # cannabis stage an hour after the code stopped doing that.
            _sp = self._get_species_for_plant(args.get("plant_id", "current_plant"))
            _allowed, _default = self.stages_for_species(_sp)
            if new_stage and new_stage not in _allowed:
                return {"error": (f"'{new_stage}' is not a stage for {_sp or 'this species'}. "
                                  f"Valid: {', '.join(_allowed)}."),
                        "species": _sp, "valid_stages": _allowed}
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
                    "event_type": "stage_transition",
                    "new_stage": new_stage,
                    "notes": notes,
                    "previous_stage": plant.get("stage", "unknown")
                }
                ctx = self._reasoning_context(args)
                if ctx:
                    transition["reasoning_context"] = ctx
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
                "plant_id": args.get("plant_id", "current_plant"),
                "event_type": "reservoir_change",
                "volume_liters": volume,
                "ph": ph,
                "ppm": ppm,
                "ph_before": args.get("ph_before"),
                "ppm_before": args.get("ppm_before"),
                "notes": notes
            }
            ctx = self._reasoning_context(args)
            if ctx:
                change["reasoning_context"] = ctx
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
            stage_conflict = self._stage_age_conflict(stage, germination_date)
            return {
                "result": {
                    "current_stage": stage,
                    # Surfaced, not corrected: which stage it actually is depends
                    # on morphology, and a reading logged against the wrong stage
                    # is judged against the wrong nutrient band.
                    **({"stage_conflict": stage_conflict} if stage_conflict else {}),
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
            nut_plant_id = args.get("plant_id", "current_plant")
            cur_key, idx_key = self._nutrient_keys(nut_plant_id)
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

            # Everything that is NOT a nutrient. Adding reasoning_context without
            # extending this swept reason/decision/expected_effect into the
            # recipe itself, so the feed read back as "confidence_note high...ml".
            reserved = {
                "stage", "unit", "basis", "reservoir_liters", "volume_liters",
                "typical_working_liters", "plant_id", "timestamp", "source_note",
                # reasoning_context fields
                "reason", "observed_conditions", "decision", "expected_effect",
                "confidence_note", "context_confidence", "related_events",
                "corrects", "supersedes", "evidence_kind", "source",
            }
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

            # Refuse a recipe identical to the one already in force.
            #
            # Two byte-identical entries were written a minute apart on
            # 2026-08-22, the second carrying no reasoning at all. A duplicate is
            # not a second feed - nothing went into the reservoir - and it makes
            # the history claim a change happened when none did. The lag detector
            # then compares a recipe against itself, and anyone reading the
            # timeline sees a feed event that never occurred.
            #
            # An explicit re-affirmation is still allowed via allow_duplicate,
            # for the case where the same recipe genuinely was re-mixed.
            if not args.get("allow_duplicate"):
                prior = self._get_nutrient_history(nut_plant_id)
                if prior and prior[-1].get("per_liter") == per_liter:
                    return {"result": {
                        "recorded": False,
                        "reason": ("Identical to the recipe already in force, so nothing was "
                                   "recorded. Recording it again would show a feed change that "
                                   "did not happen."),
                        "current": per_liter,
                        "hint": "Pass allow_duplicate=true if the same recipe was genuinely re-mixed.",
                    }}

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
            ctx = self._reasoning_context(args)
            if ctx:
                record["reasoning_context"] = ctx
            if backfill_ts:
                hist_key = f"nutrient_change_{backfill_ts}"
                self.store_own_memory(hist_key, json.dumps(record))
                hist = self._load_nutrient_history_index(nut_plant_id)
                if hist_key not in hist:
                    hist.append(hist_key)
                self.store_own_memory(idx_key, json.dumps(sorted(hist)))
                return {"result": "Historical nutrient entry recorded", "nutrients": record}
            self.store_own_memory(cur_key, json.dumps(record))
            # Also append to a history index. "current_nutrients" is a single
            # overwritten slot, so every previous recipe was silently destroyed
            # by the next change - which loses exactly the thing that matters for
            # a grow: how feed strength moved over time relative to the plant's
            # size and the measured ppm. Keep each change as its own entry.
            hist_key = f"nutrient_change_{self._uid()}"
            self.store_own_memory(hist_key, json.dumps(record))
            hist = self._load_nutrient_history_index(nut_plant_id)
            hist.append(hist_key)
            self.store_own_memory(idx_key, json.dumps(hist))

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
            # Archive rather than delete. This used to forget the record
            # outright, so handing a plant to someone else erased everything it
            # had taught - every reading, every recipe that worked. Pass
            # hard_delete=true to actually destroy it.
            if not args.get("hard_delete"):
                return {"result": self.archive_plant(
                    plant_id, args.get("status", "removed"), args.get("note", ""))}
            index.remove(plant_id)
            self.store_own_memory("plant_index", json.dumps(index))
            self.forget_own_memory(f"plant_{plant_id}")
            return {"result": f"Deleted {plant_id} and its record permanently"}

        elif task == "list_vision_corrections":
            return {"result": self._get_all_vision_corrections()}

        elif task == "refer_equipment_purchase":
            # The grower shows Grow an invoice because the hardware matters to
            # the grow. The spend is Accounting's, and a purchase recorded only
            # in a plant's notes is invisible to every question about what this
            # grow cost. Minimal payload: what was bought, from whom, how much.
            # The order email also carried a name, address and phone - none of
            # which belong in a ledger entry, so none of which are sent.
            if not isinstance(args, dict) or args.get("amount") is None:
                return {"error": "Usage: {item, amount, [payee], [date], [documentation_ref]}"}
            payload = {k: args.get(k) for k in
                       ("item", "amount", "payee", "date", "documentation_ref",
                        "category", "project_id") if args.get(k) is not None}
            payload.setdefault("category", "grow_equipment")
            out = self.refer_finding(
                "accounting_agent", "equipment_purchase", payload,
                why=args.get("why") or "grow hardware bought for the current plant")
            # Grow keeps its own note of the hardware; Accounting keeps the money.
            if out.get("accepted") and args.get("item"):
                self.handle_task("add_note", {
                    "plant_id": args.get("plant_id", "current_plant"),
                    "category": "equipment",
                    "source": "referred to accounting_agent",
                    "text": f"{args['item']} purchased. Ledger holds the cost; "
                            f"this note holds that the hardware exists.",
                }, self.agent_id)
            return {"result": out}

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
                    findings.append(f"PPM rose by {abs(consumption):.0f} instead of declining. That usually means water left "
                                    f"and nutrient stayed - evaporation or uptake of water faster than nutrient - "
                                    f"so the same feed is now sitting in less water.")
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
            else:
                # This compared a CELSIUS reading against a FAHRENHEIT band.
                # Every reading this system stores is in Celsius - log_reading
                # stores temp in C and the plain-language parser converts F to C
                # before storing - so 18.7C fell below the 60F floor and scored
                # 0/2, "well outside the safe range", when 18.7C is 65.7F and
                # sits dead centre of the band. A healthy reservoir was being
                # reported as critical, for every reading ever evaluated.
                #
                # Celsius is the canonical unit. A value above 45 is taken as
                # Fahrenheit because no reservoir runs at 45C - that is lethal -
                # and an explicit temp_unit overrides the guess.
                unit = str(args.get("temp_unit") or "").strip().lower()[:1]
                temp_c = reservoir_temp
                if unit == "f" or (not unit and reservoir_temp > 45):
                    temp_c = (reservoir_temp - 32) * 5 / 9
                temp_f = temp_c * 9 / 5 + 32
                shown = f"{temp_c:.1f}C ({temp_f:.0f}F)"
                if 18 <= temp_c <= 22:
                    scores["temp"] = 2
                elif 15.5 <= temp_c < 18 or 22 < temp_c <= 25.5:
                    scores["temp"] = 1
                    findings.append(
                        f"Reservoir temperature {shown} is outside the 18-22C (64-72F) band. "
                        "Where roots sit in solution this is a root-health parameter, not comfort - "
                        "warm water holds less dissolved oxygen, and the risk of root pathogens rises "
                        "above roughly 22C, more so as EC climbs."
                    )
                else:
                    scores["temp"] = 0
                    findings.append(
                        f"Reservoir temperature {shown} is well outside the safe range "
                        "(18-22C / 64-72F).")

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
            # A plant that is not being cultivated does not need a leaf-disease
            # verdict, it needs care advice. Routing an aloe through the cannabis
            # path was the mistake: the disease models have no class for it, so
            # the answer was always going to be a refusal or a guess, when the
            # useful question - is it thirsty, drowning, or reaching for light -
            # needs no disease model at all.
            _species = self._get_species_for_plant(plant_id)
            _profile = self._profile_for(_species)
            if _profile and not _profile.get("cultivated"):
                _desc = args.get("symptom_text") or args.get("notes") or ""
                if not _desc and args.get("photo_path") and VISION_AVAILABLE:
                    _desc = self._call_inference_vision(
                        self._vision_prompt_for(plant_id), args["photo_path"]) or ""
                    # Open description alone misses severity. Ask directly.
                    _desc += self._probe_photo(args["photo_path"], plant_id)
                care = self.assess_care(_desc, species=_species, plant_id=plant_id)
                care["classification"] = "care_assessment"
                care["description_used"] = _desc[:400]
                care["routed"] = (f"{_species} is not a cultivated species here, so this is a care "
                                  "reading rather than a disease diagnosis.")
                return {"result": care}

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
                    fused = self._fuse_one(photo_path, species=self._get_species_for_plant(plant_id))
                    self.save_checkpoint(checkpoint_id, {"fused": fused}, status="in_progress")
                    if "error" not in fused:
                        if fused["low_confidence"]:
                            verification = self._call_inference_vision(
                                self._vision_prompt_for(plant_id), photo_path
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

            # Distribution first. "Yellow" alone cannot separate a mite
            # stipple from an ageing fan leaf, and reading it as senescence
            # spends the window in which mites are still cheap to stop.
            pattern = self._leaf_pattern_hit(symptom_text)
            if disease_flag or airflow_flag or self._negation_aware_hit(symptom_text, LEAF_PROBLEM_KEYWORDS):
                classification = "problem"
            elif pattern:
                classification = pattern["classification"]
            elif self._negation_aware_hit(symptom_text, LEAF_SENESCENT_KEYWORDS):
                classification = "senescent"
            elif self._negation_aware_hit(symptom_text, LEAF_PRODUCTIVE_KEYWORDS):
                classification = "productive"
            else:
                verdict, _method = self._classify_qualitative(
                    symptom_text, LEAF_PRODUCTIVE_KEYWORDS, LEAF_PROBLEM_KEYWORDS, "leaf health"
                )
                classification = {"stable": "productive", "warning": "senescent", "critical": "problem"}[verdict]

            # How much weight the evidence actually carries.
            #
            # A "productive" verdict is usually reached by finding NOTHING - no
            # disease, pest or airflow keyword matched. Absence of a detected
            # problem is not evidence of health, and it was being reported at the
            # same confidence as a positive finding. On a real upload the local
            # models could not cover the species at all, the fallback returned a
            # generic description ("a small green plant... appears healthy and
            # well-cared for... adequate sunlight"), and that became
            # "productive / high" - certainty manufactured out of a stereotype.
            unverified_vision = (not symptom_text_from_user) and bool(vision_note) and \
                ("no local model covers this species" in (vision_note or "")
                 or "low confidence" in (vision_note or "").lower())
            if classification == "productive":
                observation = f"Leaf symptoms described as: \"{symptom_text}\"." if symptom_text else "No significant symptoms reported."
                reason = "Leaf shows healthy color/vigor with no disease, pest, or airflow signals."
                action = "Preserve the leaf."
                if unverified_vision:
                    confidence = "low"
                    reason = ("No problem signal was detected, but no local model covers this "
                              "species and the fallback description was generic. This is an "
                              "absence of findings, not a finding of health.")
                    action = "Preserve the leaf, but treat this as unassessed - describe what you see if anything looks off."
                elif not symptom_text_from_user and not symptom_text:
                    confidence = "low"
                    reason = "Nothing was reported and nothing was detected - there is no evidence either way."
                else:
                    confidence = "high" if symptom_text_from_user else "medium"
            elif pattern and classification == "problem":
                shapes = [pattern["pattern"]] + [a["pattern"] for a in pattern.get("also", [])]
                observation = (f"Leaf symptoms described as: \"{symptom_text}\". "
                               f"Distribution reads as "
                               f"{', '.join(x.replace('_', ' ') for x in shapes)}.")
                reason = f"The pattern is what decides this: {pattern['consistent_with']}."
                action = pattern["what_would_settle_it"]
                for extra in pattern.get("also", []):
                    reason += (f" Separately, {extra['pattern'].replace('_', ' ')} is "
                               f"consistent with {extra['consistent_with']}.")
                    action += " " + extra["what_would_settle_it"]
                if pattern.get("also"):
                    reason += (" Two signs at once is itself the finding: they are "
                               "different causes, and only one of them spreads.")
                # A photo cannot separate mites from thrips, so this names a
                # candidate and the check that settles it - never a diagnosis.
                confidence = "medium" if symptom_text_from_user else "low"
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
                    fused = self._fuse_one(photo_path, species=species)
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

        elif task == "learn_species_profile":
            return {"result": self.learn_species_profile(
                args.get("species"), args.get("plant_id"))}

        elif task == "check_target_drift":
            return {"result": self.check_target_drift(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

        elif task == "learn_from_observations":
            return {"result": self.learn_from_observations(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

        elif task == "sensor_status":
            return {"result": self.sensor_status(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

        elif task == "ingest_sensor_sample":
            return {"result": self.ingest_sensor_sample(
                args.get("sensor_id", "manual"), args)}

        elif task == "situation":
            return {"result": self.situation(
                args.get("plant_id", "current_plant"),
                self._parse_numeric(args.get("target_ppm")))}

        elif task == "describe":
            return {"result": {"text": self.describe(args.get("task") or "",
                                                     args.get("payload"))}}

        elif task == "assess_germination":
            return {"result": self.assess_germination(
                args.get("plant_id", "current_plant"),
                args.get("description") or args.get("symptom_text") or "")}

        elif task == "infer_system_change":
            return {"result": self.infer_system_change(args.get("plant_id", "current_plant"))}

        elif task == "estimate_root_establishment":
            return {"result": self.estimate_root_establishment(
                args.get("plant_id", "current_plant"), args.get("transitioned_on"))}

        elif task == "photo_cadence":
            return {"result": self.photo_cadence(args.get("plant_id", "current_plant"))}

        elif task == "resolve_plant":
            # Which plant a prompt is about. Only this agent holds the roster,
            # so only it can answer - a caller defaulting to "current_plant"
            # files a photo of one plant against another.
            return {"result": {"plant_id": self._plant_from_text(args.get("prompt") or "")}}

        elif task == "amend_grow_system":
            fields = {k: v for k, v in (args or {}).items() if k != "plant_id"}
            return {"result": self.amend_grow_system(
                args.get("plant_id", "current_plant"), **fields)}

        elif task == "measure_working_volume":
            return {"result": self.measure_working_volume(
                args.get("plant_id", "current_plant"),
                args.get("reference_liters"), args.get("verdict", "above"),
                args.get("method", "side_by_side_level"),
                # No default here: absent means "ask the system record",
                # and a default in this layer silently overrides that.
                args.get("solids_submerged"),
                args.get("upper_hint"), args.get("note", ""),
                args.get("precision_liters"))}

        elif task == "parse_reading":
            # Read-only: what the agent would extract, without recording it.
            return {"result": {"reading": self.parse_reading(args.get("prompt") or "")}}

        elif task == "log_from_text":
            return {"result": self.log_from_text(
                args.get("prompt") or "", args.get("plant_id") or "current_plant")}

        elif task == "project_drawdown":
            return {"result": self.project_drawdown(
                args.get("plant_id", "current_plant"),
                self._parse_numeric(args.get("from_ppm")),
                self._parse_numeric(args.get("to_ppm")))}

        elif task == "blockers_for_change":
            return {"result": self.blockers_for_change(
                args.get("plant_id", "current_plant"),
                self._parse_numeric(args.get("target_ppm")))}

        elif task == "explain_decision":
            return {"result": self.explain_decision(
                args.get("plant_id", "current_plant"), args.get("topic", ""))}

        elif task == "reading_cadence":
            return {"result": self.reading_cadence(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

        elif task == "analyze_deficit":
            return {"result": self.analyze_deficit(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

        elif task == "acquire_plant":
            return {"result": self.acquire_plant(
                args.get("plant_id"), args.get("stage"), args.get("species", "cannabis"),
                args.get("strain", ""), args.get("source", ""),
                args.get("prior_system", ""), args.get("prior_medium", ""),
                args.get("estimated_age_days"), args.get("note", ""))}

        elif task == "archive_plant":
            return {"result": self.archive_plant(
                args.get("plant_id"), args.get("status", "harvested"),
                args.get("note", ""), args.get("outcome"),
                args.get("photos", "purge"))}

        elif task == "list_plants":
            return {"result": {"active": self.active_plants(),
                               "archived": self.archived_plants()}}

        elif task == "assess_care":
            desc = args.get("description") or args.get("notes") or args.get("text") or ""
            return {"result": self.assess_care(
                desc, species=args.get("species"), plant_id=args.get("plant_id"),
                temp_f=args.get("temp_f"), temp_c=args.get("temp_c"),
                light_note=args.get("light_note"))}

        elif task == "assess_stage":
            return {"result": self.assess_stage(
                args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant")}

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
            ctx = self._reasoning_context(args)
            if ctx:
                record["reasoning_context"] = ctx
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

            cur_key, _ = self._nutrient_keys(plant_id)
            raw = self._unwrap_value(self.retrieve_own_memory(cur_key))
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
            lagging = self._detect_lagging_nutrients(plant_id=plant_id)

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
                if info.get("whole_recipe_regressed"):
                    notes.append(
                        f"{name} concentration FELL {abs(info['growth_pct']):.0f}% per litre since the "
                        f"first recorded recipe (recipe overall {info['median_growth_pct']:.0f}%), while "
                        "the plant advanced stages. Restored to at least the earlier strength before "
                        "the stage multiplier - the feed got weaker as demand grew."
                    )
                else:
                    notes.append(
                        f"{name} has not kept pace: up {info['growth_pct']:.0f}% since the first "
                        f"recorded recipe while the recipe as a whole moved {info['median_growth_pct']:.0f}%. "
                        # "Applied" read as though the dose had already gone into
                        # the reservoir, so the grower reasonably asked why it was
                        # still being flagged. The catch-up is applied to the
                        # SUGGESTION; nothing has been added to the tank until the
                        # grower doses it and it is recorded.
                        f"The suggested figure below already includes a "
                        f"{info['catchup_multiplier']:.2f}x catch-up on top of the stage multiplier, "
                        "because scaling a stalled component just carries the lag forward. "
                        "This stays flagged until a recipe is recorded with the higher amount in it."
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

        elif task == "apply_feed_recommendation":
            # Closes the loop between recommending a feed and recording that it
            # happened.
            #
            # recommend_feed produced a 1.45x FloraBloom catch-up and the grower
            # dosed it - but nothing wrote the resulting recipe back, so the two
            # most recent entries were byte-identical and the lag detector kept
            # seeing FloraBloom at the same concentration and kept re-recommending
            # the same correction. A recommendation acted on and never recorded is
            # indistinguishable from one ignored.
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            rec = self.handle_task("recommend_feed", {"plant_id": plant_id})
            rec = rec.get("result", rec) if isinstance(rec, dict) else {}
            if not isinstance(rec, dict) or "suggested" not in rec:
                return {"error": "No feed recommendation available to apply",
                        "detail": str(rec)[:200]}

            suggested = rec.get("suggested") or {}
            # The grower may have rounded to what a syringe can actually measure.
            # What went in the reservoir is the record; the suggestion is not.
            actual = args.get("actual") if isinstance(args, dict) else None
            applied = actual if isinstance(actual, dict) and actual else suggested
            if not applied:
                return {"error": "Nothing to apply"}

            payload = dict(applied)
            payload["basis"] = rec.get("basis") or "total"
            payload["unit"] = rec.get("unit") or "ml"
            vol = rec.get("reservoir_liters")
            if vol:
                payload["reservoir_liters"] = vol
            payload["plant_id"] = plant_id
            payload["evidence_kind"] = "event"
            payload["reason"] = args.get("reason") or (
                "Applied the feed recommendation. " + (rec.get("action") or "")[:300])
            payload["decision"] = ("Dosed as recommended." if applied is suggested
                                   else "Dosed close to the recommendation, rounded to what the syringe measures.")
            payload["expected_effect"] = args.get("expected_effect") or (
                "The lagging component should now track the rest of the recipe, "
                "and the next lag check should not re-flag it.")
            payload["confidence_note"] = args.get("confidence_note") or "high - dose applied by the grower"
            payload["observed_conditions"] = args.get("observed_conditions", "")

            result = self.handle_task("set_current_nutrients", payload)
            lag_after = self._detect_lagging_nutrients(plant_id=plant_id)
            return {"result": {
                "applied": applied,
                "was_suggestion": actual is None,
                "recorded": result.get("result", result) if isinstance(result, dict) else result,
                "lagging_after": lag_after,
                "note": ("Recorded. The lag check now reports "
                         + (", ".join(lag_after) if lag_after else "nothing lagging")
                         + "."),
            }}

        elif task == "set_inventory":
            # What the grower actually has on hand. Product label guidance is
            # recorded as REFERENCE, never as the operating rate: manufacturer
            # charts are written to sell product and assume generic conditions -
            # they do not know the source water carries no minerals, what the
            # reservoir volume is, or how this plant responded last week.
            # Observed plant response and measured readings are the authority.
            item_id = args.get("item_id") or (args.get("name") or "").lower().replace(" ", "_")
            if not item_id:
                return {"error": "Missing item_id or name"}
            record = {
                "item_id": item_id,
                "name": args.get("name", item_id),
                "category": args.get("category", "nutrient"),
                "analysis": args.get("analysis", {}),
                "label_guidance": args.get("label_guidance", {}),
                "label_basis": args.get("label_basis"),
                "on_hand": args.get("on_hand", True),
                "notes": args.get("notes", ""),
                "updated": datetime.now().isoformat(),
            }
            self.store_own_memory(f"inventory_{item_id}", json.dumps(record))
            index = self._load_inventory_index()
            if item_id not in index:
                index.append(item_id)
            self.store_own_memory("inventory_index", json.dumps(index))
            return {"result": f"Inventory item recorded: {record['name']}", "item": record}

        elif task == "get_inventory":
            items = []
            for iid in self._load_inventory_index():
                raw = self._unwrap_value(self.retrieve_own_memory(f"inventory_{iid}"))
                if not raw:
                    continue
                try:
                    items.append(json.loads(raw))
                except Exception:
                    pass
            return {"result": {
                "items": items,
                "note": ("Label guidance is reference only. Operating rates come from measured "
                         "readings and observed plant response - see nutrient history."),
            }}

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
            plant_id = args.get("plant_id", "current_plant")
            measured = self._parse_numeric(args.get("measured_ppm"))
            target = self._parse_numeric(args.get("target_ppm"))
            added = args.get("added") or {}
            # Fall back to what the agent already knows. Requiring the grower to
            # restate their own last reading and their own current recipe made a
            # simple question ("how much do I add to reach 800") unanswerable
            # when every input was already on record.
            if measured is None:
                _rs = [r for r in self._get_readings_for_plant(plant_id)
                       if self._parse_numeric(r.get("ppm")) is not None]
                if _rs:
                    _rs.sort(key=lambda r: r.get("timestamp") or "")
                    measured = self._parse_numeric(_rs[-1].get("ppm"))
            if not added:
                _cur_key, _ = self._nutrient_keys(plant_id)
                _raw = self._unwrap_value(self.retrieve_own_memory(_cur_key))
                if _raw:
                    try:
                        added = (json.loads(_raw).get("nutrients") or {})
                    except Exception:
                        added = {}
                    added = {k: v for k, v in added.items()
                             if self._parse_numeric(v) is not None}
            if measured is None or target is None:
                return {"error": "Usage: {measured_ppm, target_ppm, [added: {nutrient: ml}], [assumed_volume_liters]}"}
            if measured <= 0:
                return {"error": "measured_ppm must be positive"}

            # In a top-fed system a strength increase is not held in the
            # reservoir until roots reach it - it is sprayed onto the roots that
            # are already there. Said at the point the increase is calculated,
            # because that is when the grower is deciding.
            _sysraw = self._unwrap_value(self.retrieve_own_memory(f"grow_system_{plant_id}")) \
                      or (self._unwrap_value(self.retrieve_own_memory("grow_system"))
                          if plant_id == "current_plant" else None)
            _topfed = False
            _medium = ""
            try:
                _sys = json.loads(_sysraw) if _sysraw else {}
                _topfed = _sys.get("system_type") in ("top_fed_dwc",)
                _medium = _sys.get("medium") or ""
            except Exception:
                pass

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
            # The distinction that separates a top-fed system from a plain DWC,
            # said when the grower is deciding rather than after they dosed.
            if _topfed and factor > 1:
                recommendation["top_fed_caution"] = (
                    "This is a top-fed system, so the increase does NOT sit in the reservoir "
                    "waiting for roots to reach it - the ring draws from the same solution and "
                    "sprays it straight onto the root mass in the medium. Roots up in the "
                    "pebbles are the most exposed part of the plant, not the least."
                    + (" Clay pebbles have almost no cation exchange capacity, so nothing "
                       "buffers what arrives. And between sprays the water evaporates off the "
                       "pebble while the dissolved nutrient stays put, so the film left on it is "
                       "the same nutrient in less water - stronger at the root surface than the "
                       "reservoir reads. Nothing is added; only water leaves."
                       if "pebble" in str(_medium).lower() or "clay" in str(_medium).lower()
                       else "")
                    + " A single root reaching the water is the least exposed part of the system; "
                      "raising strength to acclimatise it front-loads the dose onto everything else.")
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

            # Resolution guards before any conclusion.
            hours = None
            try:
                hours = (datetime.fromisoformat(b["timestamp"]) -
                         datetime.fromisoformat(a["timestamp"])).total_seconds() / 3600.0
            except Exception:
                pass
            if hours is not None and hours < MIN_CONSUMPTION_WINDOW_HOURS and vb <= va:
                res = self._make_recommendation(
                    f"Only {hours:.1f}h between these readings.",
                    f"Uptake is a slow signal - a reservoir this size shifts a few percent a day at "
                    f"most, so a window under {MIN_CONSUMPTION_WINDOW_HOURS}h cannot separate it from "
                    "measurement error.",
                    "Compare readings at least a day apart. Watch ppm and pH in the meantime - those "
                    "move measurably on this timescale and are measured precisely.",
                    "low")
                res["classification"] = "window_too_short"
                res["hours"] = round(hours, 1)
                return {"result": res}

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
            elif max(abs(water_used), abs(nutrient_used)) < CONSUMPTION_NOISE_FLOOR_PCT:
                verdict = "below_resolution"
                observation = (
                    f"Water moved {water_used:.1f}% and nutrient {nutrient_used:.1f}% - both under the "
                    f"{CONSUMPTION_NOISE_FLOOR_PCT:.0f}% floor. Volume read off an unmarked sight tube "
                    "is good to roughly a tenth, so changes this small are indistinguishable from "
                    "measurement error."
                )
                action = ("No conclusion drawn. Calibrating the reservoir with a known measure would "
                          "lower this floor and make the comparison usable sooner.")
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
            # Stage and strain live in per-plant records for anything other than
            # the legacy single-plant slot. Reading the global slot regardless of
            # plant_id reported a day-0 seedling as "veg" and asked it for PPM,
            # when the protocol is plain water with no nutrients at that stage.
            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            else:
                plant = next((p for p in self._get_all_plants() if p.get("plant_id") == plant_id), None)
                if not plant:
                    return {"error": f"Unknown plant_id: {plant_id}"}
                stage = plant.get("stage", "unknown")
                strain = plant.get("strain", "")
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

            # Missing = never recorded at all, or the last recorded value is
            # older than that parameter can stay useful. Carrying a value forward
            # is the point: the grower should not be asked to re-state a reservoir
            # volume they set themselves two days ago.
            missing, stale, carried = [], [], {}
            for p in schedule["params"]:
                last_val, last_age = self._last_known_param(p, readings)
                if last_val is None:
                    missing.append(p)
                    continue
                horizon = PARAM_STALENESS_DAYS.get(p, 3)
                if last_age is not None and last_age > horizon:
                    missing.append(p)
                elif last_age is not None and last_age > 0:
                    carried[p] = {"value": last_val, "days_old": last_age}
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
            if carried:
                recommendation["carried_forward"] = carried
            recommendation["stale"] = stale
            recommendation["triggers"] = triggers
            recommendation["questions"] = questions
            return {"result": recommendation}

        elif task == "get_nutrient_history":
            # Feed changes alongside the ppm they actually produced. The recipe
            # alone doesn't say whether a change worked - only the measured
            # concentration that followed it does.
            plant_id = args.get("plant_id", "current_plant") if isinstance(args, dict) else "current_plant"
            history = self._get_nutrient_history(plant_id)
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
            # Out of a MEDIUM is a different operation from hydro to hydro, and
            # the generic advice below is written for the latter: it says roots
            # move with the net pot and suffer almost no disturbance, which is
            # true of water roots and false of a soil root ball. Soil roots and
            # water roots are structurally different - soil roots have root hairs
            # adapted to air pockets in medium and largely die back in standing
            # water, so the plant has to grow a new root system while living off
            # the old one.
            _from_medium = str(from_system).lower() in ("soil", "coco", "peat", "medium", "potting soil")
            _to_water = str(to_system).lower() in ("dwc", "lwc", "top_fed_dwc", "hydro", "rdwc")
            new_liters = self._parse_numeric(args.get("new_reservoir_liters"))
            water_source = (args.get("water_source") or "").lower()

            if plant_id == "current_plant":
                stage = self._unwrap_value(self.retrieve_own_memory("current_stage")) or "unknown"
                strain = self._unwrap_value(self.retrieve_own_memory("current_strain")) or ""
            else:
                _p = next((x for x in self._get_all_plants() if x.get("plant_id") == plant_id), None)
                stage = (_p or {}).get("stage", "unknown")
                strain = (_p or {}).get("strain", "")
            readings = self._get_readings_for_plant(plant_id)
            latest = readings[-1] if readings else {}
            _cur_key, _ = self._nutrient_keys(plant_id)
            raw = self._unwrap_value(self.retrieve_own_memory(_cur_key))
            current = json.loads(raw) if raw else {}

            # A move out of medium into water needs its own steps, first,
            # because the generic ones assume roots that are already in solution.
            medium_steps = []
            if _from_medium and _to_water:
                medium_steps = [
                    "Soak the root ball in room-temperature water and let the medium fall away "
                    "on its own. Do not pick or rinse soil off under a tap - the fine root hairs "
                    "are what feed the plant and they strip off with the soil.",
                    "Expect to lose most of the existing root function anyway. Soil roots are "
                    "adapted to air pockets in medium and largely die back in standing water, so "
                    "the plant has to build a water root system while living off the old one. "
                    "That is the real cost of this move and it is paid over one to two weeks.",
                    "Keep the water line HIGH at first so the remaining roots stay wet, then drop "
                    "it as white water roots appear. This is the opposite of a hydro-to-hydro "
                    "move, where the level starts low to force roots down.",
                    "Feed weak - roughly half the target for the stage - until new white root "
                    "growth is visible. A damaged root system cannot take up a full-strength "
                    "solution and the excess just raises EC around dying tissue.",
                    "Watch for browning or sliming in the first week. In soil that is a fungal "
                    "problem; here it is usually the old roots rotting, which is expected in "
                    "small amounts and dangerous if it spreads to the crown.",
                    "If this plant is late in veg or showing preflower, consider NOT moving it. "
                    "An autoflower on a fixed clock cannot spare one to two weeks of stalled "
                    "growth to rebuild roots, and there is no extra veg time to make it back.",
                ]

            steps = medium_steps + [
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

        elif task == "advance_training_campaign":
            return {"result": self.advance_training_campaign(
                int(args.get("per_label", 5)), int(args.get("max_labels", 2)),
                args.get("labels"))}

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
            candidates = self._source_candidates(
                label, int(args.get("limit", 5)), args.get("query"))
            return {"result": {
                "label": label,
                "query": candidates[0]["query"] if candidates else args.get("query"),
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

            fetch = {"fetched": False}
            if decision == "accept":
                fetch = self._fetch_candidate_image(candidate)
                candidate["local_path"] = fetch.get("path")
                if not fetch.get("fetched"):
                    # Say so. An accept that silently failed to fetch would
                    # report success while the label folder stayed empty.
                    candidate["status"] = "accept_failed"
                    candidate["fetch_error"] = fetch.get("why")
            self.store_own_memory(candidate_id, json.dumps(candidate))

            # Reviewing earns XP either way - the goal is a clean set, and
            # rejecting noise is as valuable as accepting a good example.
            QuestManager(self, VISION_CAMPAIGN_ID).award(reviews=1)
            if decision != "accept":
                note = "Rejected, not counted."
            elif fetch.get("fetched"):
                note = (f"Accepted and downloaded to {fetch['path']} "
                        f"({fetch['bytes'] // 1024} KB), with its provenance beside it. "
                        "It counts toward the campaign now.")
            else:
                note = (f"Accepted, but the image could not be downloaded: "
                        f"{fetch.get('why')}. Nothing was counted.")
            return {"result": {
                "candidate_id": candidate_id,
                "status": candidate["status"],
                "fetched": fetch.get("fetched", False),
                "local_path": candidate.get("local_path"),
                "note": note,
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
