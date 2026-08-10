"""
Parsing engine — AI-first classification + extraction.
Handles food, workout, weight logging, water, and off-topic chat.
Understands casual and Hinglish input.

All food recognition and nutrition estimation is handled by Groq AI.
Photos go to Gemini vision (Groq has no vision model).
"""

import base64
import json
import openai
from openai import OpenAI
import config
import portions

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
# Groq prompt — classifies input and gives nutrition estimates.
# All food recognition is AI-driven (no local DB override).
# Estimates are shown to the user for confirmation before logging.
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
    "IF food: Identify the food(s) and quantity, then give your best nutrition "
    "estimate for the EXACT amount described. Fill:\n"
    "- item_name: short English name of the food(s) (e.g. '2 boiled eggs + 2 chapatis', "
    "'chicken biryani')\n"
    "- quantity: number of servings/items described (default 1)\n"
    "- unit: serving unit you assumed (e.g. 'egg', 'plate', 'bowl', 'cup', 'g')\n"
    "- calories, protein_g, carbs_g, fat_g: your best estimate for the exact quantity "
    "described, using standard portions (1 boiled egg ≈ 72 kcal / 6p / 0c / 5f, "
    "1 fried egg ≈ 91 / 6 / 1 / 7, 1 banana ≈ 89 kcal, 1 chapati ≈ 170 kcal, "
    "100g chicken breast ≈ 165 kcal / 31p).\n"
    "- serving_note: one line describing the portion you assumed "
    "(e.g. '2 large eggs, boiled, no oil')\n"
    "- confidence_notes: what portion/prep you identified\n"
    "- needs_clarification: FALSE for anything you can reasonably estimate. TRUE "
    "ONLY for foods with enormous calorie variance where the user gave no amount "
    "(e.g. bare 'chai', 'biryani', 'curry') — then put ONE short clarify_question "
    "and 2-4 clarify_options.\n"
    "IF workout: fill exercise_name (standardized English), weight_kg (0 = "
    "bodyweight), sets, reps (estimate if only one given), notes.\n\n"
    "IF weight: fill weight_kg (number, convert from lbs if needed) and notes.\n\n"
    "IF chat: fill 'reply' with one or two short, warm sentences matching the "
    "user's language, gently steering them back to logging meals or workouts.\n\n"
    "Return valid JSON. All keys MUST be lowercase (e.g. 'type', 'item_name', 'calories')."
)

# ─────────────────────────────────────────────────────────────
# Ambiguity-check prompt — sent AFTER classification for foods.
# Determines if the food is ambiguous enough to need clarification pills.
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
            return _norm_keys(json.loads(text))
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
        return _norm_keys(json.loads(text))
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
        result = _norm_keys(json.loads(text))
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


def _has_macros(d):
    """True when the AI provided a usable nutrition estimate."""
    return any(_int(d.get(k)) > 0 for k in ("calories", "protein_g", "carbs_g", "fat_g"))


def _norm_keys(d):
    """Normalize AI response keys to lowercase. Groq sometimes returns
    'TYPE' instead of 'type', 'ITEM_NAME' instead of 'item_name', etc."""
    if not isinstance(d, dict):
        return d
    return {k.lower(): v for k, v in d.items()}


def _shape_food(d, allow_clarify=True, pills=None, default_fallback="",
                source=None):
    """Shape a food result dict from AI estimates."""
    # the AI's estimated macros (shown to the user for confirmation)
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
        "confidence_notes": d.get("confidence_notes") or d.get("serving_note", ""),
        "needs_clarification": bool(d.get("needs_clarification")) and allow_clarify,
        "clarify_question": d.get("clarify_question", "") if allow_clarify else "",
        "clarify_options": d.get("clarify_options", []) if allow_clarify else [],
        "source": source or "groq_fallback",
        "matched_food": d.get("item_name", ""),
        "serving_g": _int(d.get("serving_g")) or 0,
        "qty": _int(d.get("quantity")) or 1,
    }
    # New pills-based clarification
    if pills and allow_clarify:
        result["pills"] = pills
        result["default_fallback"] = default_fallback
        result["needs_clarification"] = True
    return result


def parse(payload):
    """Single call: classify + extract. Returns a normalized dict.

    AI-first strategy:
      Groq classifies the input AND estimates macros — shown to the user
      for confirmation. Photos go to Gemini vision.
    """
    text_bits = " ".join(p for p in payload if isinstance(p, str))
    hint = portions.hint_for(text_bits)
    prompt = UNIFIED_PROMPT
    if hint:
        prompt = prompt + hint

    has_image = any(isinstance(p, dict) and "data" in p for p in payload)

    # AI path: classify input, extract structure + estimated macros
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
        food_name = d.get("item_name", "")
        estimated = _has_macros(d)

        # Photos: Gemini identifies the food. Show pills so the user picks
        # the preparation, otherwise use AI estimate directly.
        if has_image:
            amb = _check_ambiguity(food_name, text_bits or f"[photo: {food_name}]")
            if amb.get("pills"):
                return _shape_food(d, allow_clarify=True,
                                   pills=amb["pills"],
                                   default_fallback=food_name)
            return _shape_food(d, allow_clarify=True,
                               default_fallback=food_name)

        # AI estimated macros → show them directly for confirmation.
        if estimated:
            return _shape_food(d, allow_clarify=True,
                               source="ai_estimate")

        # No usable estimate & AI flagged ambiguity → clarification pills.
        amb = _check_ambiguity(food_name, text_bits)
        if amb.get("requires_clarification") and amb.get("pills"):
            return _shape_food(d, allow_clarify=True,
                               pills=amb["pills"],
                               default_fallback=amb.get("default_fallback", ""))

        return _shape_food(d, allow_clarify=True,
                           source="groq_fallback")

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
    """Food-only parse for clarification re-runs. One AI call."""
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
    return _shape_food(d, allow_clarify=(clarify_round < 2))


def reparse_food_with_answer(original_text, question, answer, clarify_round=1):
    """Re-estimate after the user answered a clarifying question. One call."""
    combined = (f"User input: {original_text}\n"
                f"Clarification — {question} Answer: {answer}")
    return parse_food([combined], clarify_round=clarify_round)


def parse_with_pill(pill_text, food_name=""):
    """Resolve a pill selection to full nutrition via AI.
    Returns a shaped food dict."""
    return parse([f"User input: {pill_text}"])
