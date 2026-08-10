"""
Parsing engine — multi-tier classification + extraction.
Handles food, workout, weight logging, water, and off-topic chat.
Understands casual and Hinglish input.

Three-tier food parsing:
  1. Local food database (unlimited, instant, ~150 common foods)
  2. FatSecret search API (free 5,000/day)
  3. Gemini AI (quota-limited — ONLY for ambiguous input parsing, NOT nutrition)

Photos and voice always go to Gemini (local DB can't see/hear).
Gemini must NEVER invent nutrition values — it classifies input and extracts
structure (quantity, food name, units). Actual nutrition comes from local DB
or FatSecret only.
"""

import json
from google import genai
from google.genai import types
import config
import portions
import fooddb

# Lazy client — created on first use, not at import time.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client

# JSON response schema for Gemini
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["food", "workout", "weight", "water", "chat"]},
        "item_name": {"type": "string"},
        "calories": {"type": "integer"},
        "protein_g": {"type": "integer"},
        "carbs_g": {"type": "integer"},
        "fat_g": {"type": "integer"},
        "fiber_g": {"type": "integer"},
        "sugar_g": {"type": "integer"},
        "confidence_notes": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "clarify_question": {"type": "string"},
        "clarify_options": {"type": "array", "items": {"type": "string"}},
        "exercise_name": {"type": "string"},
        "weight_kg": {"type": "number"},
        "sets": {"type": "integer"},
        "reps": {"type": "integer"},
        "ml": {"type": "integer"},
        "notes": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["type"],
}

# ─────────────────────────────────────────────────────────────
# Gemini prompt — CRITICAL: Gemini must NOT invent nutrition.
# It only classifies input and extracts structure (food name,
# quantity, units). Actual nutrition is always looked up from
# the local DB or FatSecret.
# ─────────────────────────────────────────────────────────────
UNIFIED_PROMPT = (
    "You are the engine of a personal health tracker. Read the input (text, a "
    "transcribed voice note, or a food photo; may be casual, misspelled, or "
    "Hinglish) and classify it.\n\n"
    "TYPE = one of:\n"
    '- "food": something eaten/drunk.\n'
    '- "workout": exercise/training done.\n'
    '- "weight": reporting body weight (e.g. "I weigh 76kg", "weight 76").\n'
    '- "water": drinking water (e.g. "drank 2 glasses water", "2 litre paani"). '
    'Fill ml (millilitres) — assume a glass ≈ 250ml, a bottle ≈ 500ml. '
    'Never classify juice/milk/soda as water.\n'
    '- "chat": anything else.\n\n'
    "IF food: Your job is ONLY to identify the food and quantity. Do NOT "
    "estimate calories or macros — the system looks those up from a database. "
    "Fill:\n"
    "- item_name: short English name of the food (e.g. 'boiled eggs', 'chapati', "
    "'chicken biryani')\n"
    "- calories: set to 0 (the system will fill this from the database)\n"
    "- protein_g, carbs_g, fat_g: set to 0 (the system will fill these)\n"
    "- confidence_notes: what portion/prep you identified (e.g. '2 large eggs, "
    "boiled' or '2 chapatis, home-style')\n"
    "- needs_clarification: true ONLY if the food is genuinely ambiguous with "
    "high calorie variance AND the user gave no portion info (e.g. just 'curry' "
    "or 'biryani' with no amount). Then put ONE short clarify_question and "
    "2-4 clarify_options.\n"
    "- For everyday or clearly-described foods, needs_clarification=false.\n\n"
    "IF workout: fill exercise_name (standardized English), weight_kg (0 = "
    "bodyweight), sets, reps (estimate if only one given), notes.\n\n"
    "IF weight: fill weight_kg (number, convert from lbs if needed) and notes.\n\n"
    "IF chat: fill 'reply' with one or two short, warm sentences matching the "
    "user's language, gently steering them back to logging meals or workouts."
)


def _to_parts(payload, prompt):
    """Convert payload into google-genai content parts, with the prompt first."""
    parts = [prompt]
    for p in payload:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict) and "data" in p:
            parts.append(types.Part.from_bytes(
                data=p["data"], mime_type=p.get("mime_type", "application/octet-stream")))
    return parts


class ParseError(Exception):
    """Friendly, user-facing AI failure (bad key, rate limit, network…)."""


def _generate(payload, prompt):
    """One Gemini call returning parsed JSON dict."""
    contents = _to_parts(payload, prompt)
    try:
        resp = _get_client().models.generate_content(
            model=config.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
    except Exception as e:
        msg = str(e).lower()
        if "api key" in msg or "unauthorized" in msg or "permission" in msg:
            raise ParseError(
                "Couldn't talk to Gemini — the API key looks invalid. Check GEMINI_API_KEY.") from e
        if "429" in msg or "rate" in msg or "quota" in msg:
            raise ParseError("Gemini rate limit hit — wait a minute and try again.") from e
        if "timeout" in msg or "connection" in msg or "network" in msg:
            raise ParseError("Network hiccup talking to Gemini — try again.") from e
        raise ParseError(f"Gemini error: {e}") from e
    return json.loads(resp.text)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _shape_food(d, allow_clarify=True, audit=None):
    """Shape a food result dict. If audit is provided, use it for nutrition
    (Gemini's values are ignored — the database is the source of truth)."""
    if audit:
        # Use the database lookup result as the source of truth
        return audit

    # Fallback: if we somehow got here without a database lookup,
    # use Gemini's values but log a warning
    cal = _int(d.get("calories"))
    p = _int(d.get("protein_g"))
    cb = _int(d.get("carbs_g"))
    ft = _int(d.get("fat_g"))
    # Safety: reconstruct calories from macros if missing
    macro_cal = p * 4 + cb * 4 + ft * 9
    if cal == 0 and macro_cal > 0:
        cal = macro_cal

    return {
        "type": "food",
        "item_name": d.get("item_name") or "Unknown meal",
        "calories": cal, "protein_g": p, "carbs_g": cb, "fat_g": ft,
        "fiber_g": _int(d.get("fiber_g")), "sugar_g": _int(d.get("sugar_g")),
        "confidence_notes": d.get("confidence_notes", ""),
        "needs_clarification": bool(d.get("needs_clarification")) and allow_clarify,
        "clarify_question": d.get("clarify_question", "") if allow_clarify else "",
        "clarify_options": d.get("clarify_options", []) if allow_clarify else [],
        "source": "gemini_fallback",
        "matched_food": d.get("item_name", ""),
        "serving_g": 0,
        "qty": 1,
    }


def parse(payload):
    """Single call: classify + extract. Returns a normalized dict.

    For food: tries local DB / FatSecret first (zero API cost).
    Only calls Gemini for non-food or when local lookup fails.
    """
    text_bits = " ".join(p for p in payload if isinstance(p, str))
    hint = portions.hint_for(text_bits)
    prompt = UNIFIED_PROMPT
    if hint:
        prompt = prompt + hint

    # Fast path: try local food DB for plain text (no API call needed)
    text_only = all(isinstance(p, str) for p in payload)
    if text_only and text_bits.strip():
        local_food = fooddb.parse_food(text_bits, payload)
        if local_food:
            return local_food

    # Gemini path: classify input, extract structure
    d = _generate(payload, prompt)
    kind = d.get("type", "chat")

    if kind == "food":
        # Gemini identified the food but didn't give nutrition (we told it not to).
        # Try to look it up in the database using the identified food name.
        food_name = d.get("item_name", "")
        audit = fooddb.parse_food(food_name) if food_name else None

        # If Gemini gave specific portion info in confidence_notes, try to
        # reconstruct a more specific query
        if not audit and food_name:
            # Try the original text with the Gemini-identified name
            audit = fooddb.parse_food(text_bits)

        return _shape_food(d, allow_clarify=True, audit=audit)

    if kind == "workout":
        return {"type": "workout",
                "exercise_name": d.get("exercise_name") or "Unknown",
                "weight_kg": _float(d.get("weight_kg")),
                "sets": _int(d.get("sets")), "reps": _int(d.get("reps")),
                "notes": d.get("notes", "")}
    if kind == "weight":
        return {"type": "weight", "weight_kg": _float(d.get("weight_kg")),
                "notes": d.get("notes", "")}
    if kind == "water":
        return {"type": "water", "ml": _int(d.get("ml")) or 250}
    return {"type": "chat", "reply": (d.get("reply") or "").strip() or
            "I'm your food & workout tracker — tell me what you ate or lifted. 💪"}


def parse_food(payload, clarify_round=0):
    """Food-only parse for clarification re-runs. One Gemini call."""
    prompt = UNIFIED_PROMPT
    text_bits = " ".join(p for p in payload if isinstance(p, str))
    hint = portions.hint_for(text_bits)
    if hint:
        prompt = prompt + hint
    if clarify_round >= 2:
        prompt = (prompt + "\n\nThe user has already answered enough questions. "
                  "Set needs_clarification=false and give your best final estimate now.")
    prompt = prompt + "\n\n(Treat this input as FOOD.)"
    d = _generate(payload, prompt)
    # For clarification, we need to look up nutrition from the database
    food_name = d.get("item_name", "")
    audit = fooddb.parse_food(food_name) if food_name else None
    if not audit:
        audit = fooddb.parse_food(text_bits)
    return _shape_food(d, allow_clarify=(clarify_round < 2), audit=audit)


def reparse_food_with_answer(original_text, question, answer, clarify_round=1):
    """Re-estimate after the user answered a clarifying question. One call."""
    combined = (f"User input: {original_text}\n"
                f"Clarification — {question} Answer: {answer}")
    return parse_food([combined], clarify_round=clarify_round)
