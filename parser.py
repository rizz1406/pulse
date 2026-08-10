"""
Parsing engine — multi-tier classification + extraction.
Handles food, workout, weight logging, water, and off-topic chat.
Understands casual and Hinglish input.

Three-tier food parsing:
  1. Local food database (unlimited, instant, ~150 common foods)
  2. FatSecret search API (free 5,000/day)
  3. Groq AI (quota-limited — ONLY for ambiguous input parsing, NOT nutrition)

Photos and voice always go to Groq (local DB can't see/hear).
Groq must NEVER invent nutrition values — it classifies input and extracts
structure (quantity, food name, units). Actual nutrition comes from local DB
or FatSecret only.
"""

import base64
import json
import openai
from openai import OpenAI
import config
import portions
import fooddb

# Lazy client — created on first use, not at import time.
_client = None

# Groq model used for classification / clarification / chat.
GROQ_MODEL = "llama-3.3-70b-versatile"

_vision_client = None


def _get_vision_client():
    """OpenAI-compatible client for Gemini (vision only — Groq has no vision)."""
    global _vision_client
    if _vision_client is None:
        _vision_client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=config.GEMINI_API_KEY,
        )
    return _vision_client


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=config.GROQ_API_KEY,
        )
    return _client

# ─────────────────────────────────────────────────────────────
# Groq prompt — CRITICAL: Groq must NOT invent nutrition.
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

# ─────────────────────────────────────────────────────────────
# Ambiguity-check prompt — sent AFTER classification for foods
# that hit Groq (unknown to local DB). Determines if the food
# is ambiguous enough to need clarification pills.
# ─────────────────────────────────────────────────────────────
AMBIGUITY_PROMPT = (
    "You are a nutrition clarity assistant. Given a food name and the user's "
    "original input, decide if this food is AMBIGUOUS (high calorie variance "
    "depending on preparation) or UNAMBIGUOUS (calorie-stable regardless of "
    "how it's made).\n\n"
    "UNAMBIGUOUS examples: '2 boiled eggs', '100g chicken breast', "
    "'1 banana', '1 cup rice' — these have a narrow calorie range "
    "(±15%). Return requires_clarification=false.\n\n"
    "AMBIGUOUS examples: 'chai' (milk/sugar varies wildly), "
    "'masala omelette' (oil/ingredients vary), 'egg curry' (gravy richness), "
    "'biryani' (oil/ghee/portion). Return requires_clarification=true with "
    "2-4 pills representing the most common preparations.\n\n"
    "IMPORTANT — pills must use NATURAL units for the food:\n"
    "- Whole fruits/vegetables (apple, banana, mango): size or count "
    "('1 small', '1 medium', '1 large', '2 medium'). Never 'cup' or 'bowl'.\n"
    "- Drinks (chai, coffee): 'bottle', 'glass', 'cup' + prep details.\n"
    "- Solid meals (curry, biryani): 'plate', 'bowl', 'half plate'.\n\n"
    "Each pill MUST have:\n"
    "- label: short display name (e.g. 'Milk + 1 tsp Sugar')\n"
    "- text: full descriptive input for DB lookup (e.g. '1 cup chai with "
    "whole milk and 1 tsp sugar')\n\n"
    "Return JSON:\n"
    "{\n"
    '  "requires_clarification": true/false,\n'
    '  "question": "How was your {food} prepared?",\n'
    '  "pills": [{"label": "...", "text": "..."}, ...],\n'
    '  "default_fallback": "text of the most common/default pill"\n'
    "}"
)


def _to_parts(payload, prompt):
    """Convert payload into OpenAI chat content parts, with the prompt first."""
    content = [{"type": "text", "text": prompt}]
    for p in payload:
        if isinstance(p, str):
            content.append({"type": "text", "text": p})
        elif isinstance(p, dict) and "data" in p:
            data = p["data"]
            if not isinstance(data, str):
                data = base64.b64encode(data).decode("ascii")
            mime = p.get("mime_type", "application/octet-stream")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
    return content


class ParseError(Exception):
    """Friendly, user-facing AI failure (bad key, rate limit, network…)."""


PHOTO_PROMPT = (
    "This is a photo of food. Identify the dish and estimate the portion shown. "
    "Return JSON: {\"type\": \"food\", \"item_name\": \"...\", \"quantity\": N, "
    "\"unit\": \"...\", \"confidence_notes\": \"what you see in the image\"}. "
    "Use the most specific dish name (e.g. 'masala omelette' not just 'omelette'). "
    "Do NOT provide nutrition values — just identify what the food is."
)


def _generate(payload, prompt):
    """One AI call returning parsed JSON dict.

    Text → Groq (llama-3.3). Images → Gemini vision (Groq has no vision model).
    """
    has_image = any(isinstance(p, dict) and "data" in p for p in payload)
    if has_image:
        if not config.GEMINI_API_KEY:
            return {"type": "chat", "error": "no_gemini_key"}
        prompt = PHOTO_PROMPT
        messages = [{"role": "user", "content": _to_parts(payload, prompt)}]
        try:
            response = _get_vision_client().chat.completions.create(
                model=config.GEMINI_VISION_MODEL,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content or "{}"
        except openai.APIError as e:
            msg = str(e).lower()
            if e.status_code == 401 or "api key" in msg or "unauthorized" in msg:
                return {"type": "chat", "error": "Invalid Gemini API key — check GEMINI_API_KEY"}
            if "quota" in msg or "429" in msg:
                return {"type": "chat", "error": "Gemini quota hit — photo analysis failed, try again later"}
            return {"type": "chat", "error": f"Photo analysis failed: {e}"}
        except Exception as e:
            return {"type": "chat", "error": f"Photo analysis failed — try again ({e})"}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"type": "chat", "error": "Photo analysis returned unparseable output — try again."}

    model = GROQ_MODEL
    messages = [{"role": "user", "content": _to_parts(payload, prompt)}]
    try:
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
    except openai.RateLimitError as e:
        raise ParseError("Groq rate limit hit — wait a minute and try again.") from e
    except openai.APIError as e:
        msg = str(e).lower()
        if e.status_code == 404 or "not found" in msg or "404" in msg:
            raise ParseError(
                "Groq model unavailable — check the model configured in parser.py.") from e
        if e.status_code == 401 or "api key" in msg or "unauthorized" in msg:
            return {"type": "chat", "error": "Invalid Groq API key — check GROQ_API_KEY"}
        if "timeout" in msg or "timed out" in msg:
            return {"type": "chat", "error": "AI timed out — try again"}
        if "connection" in msg or "network" in msg or "dns" in msg:
            return {"type": "chat", "error": "Network hiccup — try again"}
        return {"type": "chat", "error": f"AI error: {e}"}
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "quota" in msg or "rate limit" in msg:
            raise ParseError("Groq rate limit hit — wait a minute and try again.") from e
        if "404" in msg or "not found" in msg:
            raise ParseError(
                "Groq model unavailable — check the model configured in parser.py.") from e
        if "timeout" in msg or "timed out" in msg:
            return {"type": "chat", "error": "AI timed out — try again"}
        if "connection" in msg or "network" in msg or "dns" in msg:
            return {"type": "chat", "error": "Network hiccup — try again"}
        return {"type": "chat", "error": f"AI error: {e}"}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"type": "chat", "error": "AI returned unparseable output — try again."}


def _check_ambiguity(food_name, user_text):
    """Ask Groq if this food is ambiguous enough to need clarification pills.
    Returns a dict with requires_clarification, question, pills, default_fallback."""
    prompt = AMBIGUITY_PROMPT
    payload = [f"Food: {food_name}\nUser input: {user_text}"]
    messages = [{"role": "user", "content": _to_parts(payload, prompt)}]
    try:
        response = _get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
        result = json.loads(text)
    except Exception:
        return {"requires_clarification": False}
    # Validate shape
    if not isinstance(result, dict):
        return {"requires_clarification": False}
    result.setdefault("requires_clarification", False)
    result.setdefault("question", "")
    result.setdefault("pills", [])
    result.setdefault("default_fallback", "")
    # Ensure pills is a list of {label, text} dicts
    clean_pills = []
    for p in result.get("pills", []):
        if isinstance(p, dict) and "label" in p and "text" in p:
            clean_pills.append({"label": p["label"], "text": p["text"]})
    result["pills"] = clean_pills
    return result


def resolve_pill(pill_text, food_name=""):
    """Resolve a pill selection to full nutrition by looking up the pill text
    in the local DB / FatSecret. Returns a shaped food dict or None."""
    # First try the pill text directly (it's a descriptive string)
    result = fooddb.parse_food(pill_text)
    if result:
        return result
    # Try combining food name + pill text
    combined = f"{food_name} {pill_text}"
    result = fooddb.parse_food(combined)
    if result:
        return result
    return None


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


def _shape_food(d, allow_clarify=True, audit=None, pills=None, default_fallback=""):
    """Shape a food result dict. If audit is provided, use it for nutrition
    (Groq's values are ignored — the database is the source of truth)."""
    if audit:
        # Use the database lookup result as the source of truth
        return audit

    # Fallback: if we somehow got here without a database lookup,
    # use Groq's values but log a warning
    cal = _int(d.get("calories"))
    p = _int(d.get("protein_g"))
    cb = _int(d.get("carbs_g"))
    ft = _int(d.get("fat_g"))
    # Safety: reconstruct calories from macros if missing
    macro_cal = p * 4 + cb * 4 + ft * 9
    if cal == 0 and macro_cal > 0:
        cal = macro_cal

    result = {
        "type": "food",
        "item_name": d.get("item_name") or "Unknown meal",
        "calories": cal, "protein_g": p, "carbs_g": cb, "fat_g": ft,
        "fiber_g": _int(d.get("fiber_g")), "sugar_g": _int(d.get("sugar_g")),
        "confidence_notes": d.get("confidence_notes", ""),
        "needs_clarification": bool(d.get("needs_clarification")) and allow_clarify,
        "clarify_question": d.get("clarify_question", "") if allow_clarify else "",
        "clarify_options": d.get("clarify_options", []) if allow_clarify else [],
        "source": "groq_fallback",
        "matched_food": d.get("item_name", ""),
        "serving_g": 0,
        "qty": 1,
    }
    # New pills-based clarification
    if pills and allow_clarify:
        result["pills"] = pills
        result["default_fallback"] = default_fallback
        result["needs_clarification"] = True
    return result


def parse(payload):
    """Single call: classify + extract. Returns a normalized dict.

    For food: tries local DB / FatSecret first (zero API cost).
    Only calls the AI for non-food or when local lookup fails.
    Photos go to Gemini vision (Groq has no vision model).
    """
    text_bits = " ".join(p for p in payload if isinstance(p, str))
    hint = portions.hint_for(text_bits)
    prompt = UNIFIED_PROMPT
    if hint:
        prompt = prompt + hint

    has_image = any(isinstance(p, dict) and "data" in p for p in payload)

    # Fast path: try local food DB for plain text (no API call needed)
    text_only = all(isinstance(p, str) for p in payload)
    if text_only and text_bits.strip():
        local_food = fooddb.parse_food(text_bits, payload)
        if local_food:
            return local_food

    # AI path: classify input, extract structure
    d = _generate(payload, prompt)
    kind = d.get("type", "food" if has_image else None) or d.get("type", "chat")

    # AI errors (bad key, quota, no Gemini key…) → friendly chat reply
    if kind == "chat" and d.get("error"):
        msg = d["error"]
        if msg == "no_gemini_key":
            return {"type": "chat", "reply": (
                "📷 I can't read photos yet — no Gemini API key is configured. "
                "Add GEMINI_API_KEY to .env / Render, or just type what you ate.")}
        return {"type": "chat", "reply": msg + " Or just type what you ate / did."}

    if kind == "food":
        # Groq identified the food but didn't give nutrition (we told it not to).
        # Try to look it up in the database using the identified name.
        food_name = d.get("item_name", "")
        audit = fooddb.parse_food(food_name) if food_name else None

        # If Groq gave specific portion info in confidence_notes, try to
        # reconstruct a more specific query
        if not audit and food_name:
            # Try the original text with the Groq-identified name
            audit = fooddb.parse_food(text_bits)

        # Photos: Gemini identified the food. Use local DB nutrition when the
        # name matches; otherwise show pills for user confirmation.
        if has_image and food_name:
            if audit:
                return _shape_food(d, allow_clarify=True, audit=audit)
            amb = _check_ambiguity(food_name, text_bits or f"[photo: {food_name}]")
            if amb.get("pills"):
                return _shape_food(d, allow_clarify=True, audit=None,
                                   pills=amb["pills"],
                                   default_fallback=food_name)
            return _shape_food(d, allow_clarify=True, audit=None,
                               default_fallback=food_name)

        # If no local match, check ambiguity for clarification pills
        if not audit and food_name:
            amb = _check_ambiguity(food_name, text_bits)
            if amb.get("requires_clarification") and amb.get("pills"):
                return _shape_food(d, allow_clarify=True, audit=None,
                                   pills=amb["pills"],
                                   default_fallback=amb.get("default_fallback", ""))

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


def parse_with_pill(pill_text, food_name=""):
    """Resolve a pill selection to full nutrition. Called when user taps a pill.
    Returns a shaped food dict with nutrition from local DB / FatSecret."""
    audit = resolve_pill(pill_text, food_name)
    if audit:
        audit["needs_clarification"] = False
        return audit
    # Fallback: ask Groq to parse the pill text as food
    return parse([f"User input: {pill_text}"])
