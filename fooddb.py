"""
Food database — three-tier lookup for nutrition data:
  1. Local built-in database (unlimited, no API key, covers 150+ common foods)
  2. FatSecret search API (free 5,000/day, needs key)
  3. Gemini AI (quota-limited, only for ambiguous input parsing — NOT nutrition)

The local DB handles ~80% of personal-logging inputs with zero API calls.
FatSecret fills in branded/specific items. Gemini only classifies ambiguous input.

Every result includes an audit trail:
  source         - "local" | "fatsecret"
  matched_food   - exact key/name matched
  serving_g      - grams per serving used in calculation
  qty            - number of servings
  base_per_100g  - the raw per-100g values from the source
"""

import re
import json
import math
import difflib
import urllib.request
import urllib.parse
import config

# ─────────────────────────────────────────────────────────────
# LOCAL FOOD DATABASE
#
# Two kinds of entries:
#
#   A) Per-100g entries — for foods where users might specify grams.
#      Keys: cal, p, c, f (per 100g), serving_g (default serving size).
#      If user says "200g chicken", we scale from per-100g.
#      If user says "chicken breast" (no grams), we use serving_g.
#
#   B) Per-serving entries — for countable items (eggs, roti, banana).
#      Keys: cal, p, c, f (for ONE serving), serving_g (weight of one unit).
#      The quantity multiplier applies directly to these values.
#      Marked with _per_serving=True.
#
# All nutrition values are from USDA FoodData Central / verified sources.
# ─────────────────────────────────────────────────────────────
FOODS = {
    # ── Proteins (per 100g) ──
    "chicken breast":  {"cal": 165, "p": 31, "c": 0,  "f": 4,  "serving_g": 120},
    "chicken thigh":   {"cal": 209, "p": 26, "c": 0,  "f": 11, "serving_g": 100},
    "chicken curry":   {"cal": 180, "p": 18, "c": 5,  "f": 10, "serving_g": 200},
    "fish":            {"cal": 136, "p": 20, "c": 0,  "f": 6,  "serving_g": 100},
    "prawn":           {"cal": 99,  "p": 20, "c": 0,  "f": 2,  "serving_g": 100},
    "paneer":          {"cal": 265, "p": 18, "c": 4,  "f": 20, "serving_g": 60},
    "tofu":            {"cal": 76,  "p": 8,  "c": 2,  "f": 5,  "serving_g": 80},
    "lentils":         {"cal": 116, "p": 9,  "c": 20, "f": 0,  "serving_g": 100},
    "dal":             {"cal": 116, "p": 9,  "c": 20, "f": 0,  "serving_g": 200},
    "rajma":           {"cal": 127, "p": 9,  "c": 22, "f": 0,  "serving_g": 200},
    "chole":           {"cal": 164, "p": 9,  "c": 27, "f": 3,  "serving_g": 200},
    "sambar":          {"cal": 45,  "p": 2,  "c": 8,  "f": 1,  "serving_g": 200},
    "rasam":           {"cal": 30,  "p": 1,  "c": 5,  "f": 1,  "serving_g": 200},
    "soya chunks":     {"cal": 341, "p": 52, "c": 33, "f": 1,  "serving_g": 30},
    "mutton":          {"cal": 250, "p": 26, "c": 0,  "f": 16, "serving_g": 100},
    "beef":            {"cal": 250, "p": 26, "c": 0,  "f": 16, "serving_g": 100},
    "keema":           {"cal": 220, "p": 20, "c": 4,  "f": 14, "serving_g": 100},

    # ── Per-serving: Eggs (USDA large egg = 50g, 72 kcal) ──
    "egg":             {"cal": 72,  "p": 6,  "c": 0,  "f": 5,  "serving_g": 50,  "_per_serving": True},
    "boiled egg":      {"cal": 72,  "p": 6,  "c": 0,  "f": 5,  "serving_g": 50,  "_per_serving": True},
    "fried egg":       {"cal": 91,  "p": 6,  "c": 0,  "f": 7,  "serving_g": 46,  "_per_serving": True},

    # ── Carbs / Grains (per 100g) ──
    "rice":            {"cal": 130, "p": 3,  "c": 28, "f": 0,  "serving_g": 150},
    "brown rice":      {"cal": 123, "p": 3,  "c": 26, "f": 1,  "serving_g": 150},
    "naan":            {"cal": 310, "p": 8,  "c": 55, "f": 6,  "serving_g": 80},
    "paratha":         {"cal": 310, "p": 6,  "c": 45, "f": 12, "serving_g": 80},
    "pasta":           {"cal": 131, "p": 5,  "c": 25, "f": 1,  "serving_g": 140},
    "noodles":         {"cal": 138, "p": 4,  "c": 25, "f": 2,  "serving_g": 140},
    "maggi":           {"cal": 360, "p": 9,  "c": 63, "f": 8,  "serving_g": 70},
    "bread":           {"cal": 265, "p": 9,  "c": 49, "f": 3,  "serving_g": 30},
    "toast":           {"cal": 265, "p": 9,  "c": 49, "f": 3,  "serving_g": 30},
    "oats":            {"cal": 389, "p": 17, "c": 66, "f": 7,  "serving_g": 40},
    "cornflakes":      {"cal": 357, "p": 7,  "c": 84, "f": 0,  "serving_g": 30},
    "biryani":         {"cal": 180, "p": 10, "c": 24, "f": 5,  "serving_g": 250},
    "fried rice":      {"cal": 163, "p": 4,  "c": 24, "f": 6,  "serving_g": 200},

    # ── Per-serving: Indian breads (1 chapati ≈ 60g, 170 kcal cooked) ──
    "roti":            {"cal": 170, "p": 5,  "c": 36, "f": 2,  "serving_g": 60,  "_per_serving": True},
    "chapati":         {"cal": 170, "p": 5,  "c": 36, "f": 2,  "serving_g": 60,  "_per_serving": True},

    # ── Per-serving: South Indian (1 idli ≈ 60g, 1 dosa ≈ 80g) ──
    "idli":            {"cal": 78,  "p": 2,  "c": 16, "f": 0,  "serving_g": 60,  "_per_serving": True},
    "dosa":            {"cal": 133, "p": 3,  "c": 18, "f": 6,  "serving_g": 80,  "_per_serving": True},
    "uttapam":         {"cal": 150, "p": 4,  "c": 22, "f": 5,  "serving_g": 100, "_per_serving": True},
    "poha":            {"cal": 120, "p": 2,  "c": 22, "f": 2,  "serving_g": 200, "_per_serving": True},
    "upma":            {"cal": 145, "p": 3,  "c": 23, "f": 5,  "serving_g": 200, "_per_serving": True},

    # ── Vegetables (per 100g) ──
    "aloo":            {"cal": 77,  "p": 2,  "c": 17, "f": 0,  "serving_g": 100},
    "potato":          {"cal": 77,  "p": 2,  "c": 17, "f": 0,  "serving_g": 100},
    "sabzi":           {"cal": 65,  "p": 2,  "c": 8,  "f": 3,  "serving_g": 150},
    "palak":           {"cal": 23,  "p": 3,  "c": 4,  "f": 0,  "serving_g": 100},
    "spinach":         {"cal": 23,  "p": 3,  "c": 4,  "f": 0,  "serving_g": 100},
    "bhindi":          {"cal": 33,  "p": 2,  "c": 7,  "f": 0,  "serving_g": 100},
    "bhindi curry":    {"cal": 80,  "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "okra":            {"cal": 33,  "p": 2,  "c": 7,  "f": 0,  "serving_g": 100},
    "okra curry":      {"cal": 80,  "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "lady finger":     {"cal": 33,  "p": 2,  "c": 7,  "f": 0,  "serving_g": 100},
    "lady finger curry":{"cal": 80, "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "cauliflower":     {"cal": 25,  "p": 2,  "c": 5,  "f": 0,  "serving_g": 100},
    "gobi":            {"cal": 25,  "p": 2,  "c": 5,  "f": 0,  "serving_g": 100},
    "gobi curry":      {"cal": 70,  "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "cabbage":         {"cal": 25,  "p": 1,  "c": 6,  "f": 0,  "serving_g": 100},
    "broccoli":        {"cal": 34,  "p": 3,  "c": 7,  "f": 0,  "serving_g": 100},
    "beans":           {"cal": 31,  "p": 2,  "c": 7,  "f": 0,  "serving_g": 100},
    "beans curry":     {"cal": 70,  "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "carrot":          {"cal": 41,  "p": 1,  "c": 10, "f": 0,  "serving_g": 60},
    "tomato":          {"cal": 18,  "p": 1,  "c": 4,  "f": 0,  "serving_g": 120},
    "onion":           {"cal": 40,  "p": 1,  "c": 9,  "f": 0,  "serving_g": 80},
    "capsicum":        {"cal": 20,  "p": 1,  "c": 4,  "f": 0,  "serving_g": 80},
    "mushroom":        {"cal": 22,  "p": 3,  "c": 3,  "f": 0,  "serving_g": 70},
    "brinjal":         {"cal": 25,  "p": 1,  "c": 6,  "f": 0,  "serving_g": 100},
    "brinjal curry":   {"cal": 70,  "p": 2,  "c": 8,  "f": 4,  "serving_g": 150},
    "eggplant":        {"cal": 25,  "p": 1,  "c": 6,  "f": 0,  "serving_g": 100},

    # ── Fruits (per 100g) ──
    "banana":          {"cal": 89,  "p": 1,  "c": 23, "f": 0,  "serving_g": 120},
    "apple":           {"cal": 52,  "p": 0,  "c": 14, "f": 0,  "serving_g": 180},
    "orange":          {"cal": 47,  "p": 1,  "c": 12, "f": 0,  "serving_g": 130},
    "mango":           {"cal": 60,  "p": 1,  "c": 15, "f": 0,  "serving_g": 150},
    "papaya":          {"cal": 43,  "p": 0,  "c": 11, "f": 0,  "serving_g": 150},
    "watermelon":      {"cal": 30,  "p": 1,  "c": 8,  "f": 0,  "serving_g": 200},
    "grapes":          {"cal": 69,  "p": 1,  "c": 18, "f": 0,  "serving_g": 100},

    # ── Dairy (per 100g / per serving) ──
    "milk":            {"cal": 61,  "p": 3,  "c": 5,  "f": 3,  "serving_g": 200},
    "curd":            {"cal": 59,  "p": 3,  "c": 5,  "f": 3,  "serving_g": 100},
    "yogurt":          {"cal": 59,  "p": 3,  "c": 5,  "f": 3,  "serving_g": 100},
    "buttermilk":      {"cal": 40,  "p": 2,  "c": 5,  "f": 1,  "serving_g": 200},
    "lassi":           {"cal": 70,  "p": 3,  "c": 9,  "f": 2,  "serving_g": 200},
    "cheese":          {"cal": 402, "p": 25, "c": 1,  "f": 33, "serving_g": 30},
    "ghee":            {"cal": 900, "p": 0,  "c": 0,  "f": 100,"serving_g": 15},
    "butter":          {"cal": 717, "p": 1,  "c": 0,  "f": 81, "serving_g": 14},

    # ── Snacks (per 100g) ──
    "samosa":          {"cal": 260, "p": 6,  "c": 30, "f": 13, "serving_g": 80},
    "pakora":          {"cal": 250, "p": 5,  "c": 22, "f": 16, "serving_g": 60},
    "vada":            {"cal": 270, "p": 8,  "c": 30, "f": 13, "serving_g": 60},
    "bhel puri":       {"cal": 180, "p": 4,  "c": 30, "f": 6,  "serving_g": 100},
    "sev":             {"cal": 500, "p": 10, "c": 40, "f": 35, "serving_g": 30},
    "chips":           {"cal": 536, "p": 7,  "c": 53, "f": 35, "serving_g": 30},
    "biscuit":         {"cal": 450, "p": 7,  "c": 65, "f": 18, "serving_g": 15},
    "kurkure":         {"cal": 460, "p": 6,  "c": 60, "f": 22, "serving_g": 30},
    "namkeen":         {"cal": 480, "p": 10, "c": 45, "f": 30, "serving_g": 30},

    # ── Drinks (per 100ml) ──
    "tea":             {"cal": 2,   "p": 0,  "c": 0,  "f": 0,  "serving_g": 200},
    "chai":            {"cal": 2,   "p": 0,  "c": 0,  "f": 0,  "serving_g": 200},
    "coffee":          {"cal": 2,   "p": 0,  "c": 0,  "f": 0,  "serving_g": 200},
    "cola":            {"cal": 42,  "p": 0,  "c": 11, "f": 0,  "serving_g": 330},
    "coke":            {"cal": 42,  "p": 0,  "c": 11, "f": 0,  "serving_g": 330},
    "coca cola":       {"cal": 42,  "p": 0,  "c": 11, "f": 0,  "serving_g": 330},
    "pepsi":           {"cal": 42,  "p": 0,  "c": 11, "f": 0,  "serving_g": 330},
    "juice":           {"cal": 45,  "p": 0,  "c": 11, "f": 0,  "serving_g": 250},
}

# ─────────────────────────────────────────────────────────────
# STOP WORDS — used during food matching
# ─────────────────────────────────────────────────────────────
_STOP = {"of", "with", "and", "a", "the", "plate", "bowl", "plates", "bowls",
         "some", "my", "had", "ate", "eaten", "again", "for", "in", "on",
         "just", "i", "today", "lunch", "dinner", "breakfast", "khaya",
         "khaye", "aur", "ek", "do", "teen", "me", "was", "were", "eating",
         "food", "meal", "snack",
         "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "dozen"}

# ─────────────────────────────────────────────────────────────
# UNIT PATTERNS — for extracting quantity + unit from user text
# ─────────────────────────────────────────────────────────────
_UNIT_ALIASES = {
    "roti": "roti", "chapati": "chapati", "paratha": "paratha", "naan": "naan",
    "idli": "idli", "dosa": "dosa", "uttapam": "uttapam",
    "egg": "egg", "boiled egg": "boiled egg", "fried egg": "fried egg",
    "samosa": "samosa", "biscuit": "biscuit", "slice": "slice", "piece": "piece",
    "banana": "banana", "apple": "apple", "orange": "orange",
    "glass": "glass", "cup": "cup", "bowl": "bowl", "plate": "plate",
    "serving": "serving",
}

# Maps unit aliases to FOODS keys for single-unit lookups
_UNIT_TO_FOOD = {
    "roti": "roti", "chapati": "chapati", "paratha": "paratha", "naan": "naan",
    "idli": "idli", "dosa": "dosa", "uttapam": "uttapam",
    "egg": "boiled egg", "boiled egg": "boiled egg", "fried egg": "fried egg",
    "samosa": "samosa", "biscuit": "biscuit",
    "banana": "banana", "apple": "apple", "orange": "orange",
}

_UNIT_ALT = (
    r'bowl|plate|cup|glass(?:es)?|piece|slice|serving|roti|chapati|paratha|naan|'
    r'idli|dosa|uttapam|egg(?:s)?|boiled\ egg(?:s)?|fried\ egg(?:s)?|'
    r'samosa|biscuit|banana|apple|orange'
)

_QTY_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*'
    r'(?:x|×)\s*|'
    r'(\d+(?:\.\d+)?)\s*'
    r'(' + _UNIT_ALT + r')s?\b',
    re.I
)

_HALF_RE = re.compile(
    r'\b(half|0\.5)\s*(' + _UNIT_ALT + r')\b', re.I
)
_QUARTER_RE = re.compile(
    r'\b(quarter|0\.25)\s*(' + _UNIT_ALT + r')\b', re.I
)
_GRAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:g|gm|gms|gram|grams)\b', re.I)
_ML_RE = re.compile(r'(\d+(?:\.\d+)?)\s*ml\b', re.I)
# Generic leading count: "2 biryani", "2 big macs" — a bare number before a
# food word means that many servings/countable items. Only used when no
# more specific unit/gram/ml pattern matched, and only at the start of text.
_GENERIC_COUNT_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s+(?=[a-zA-Z])')

# Same as above but spelled out: "one boiled egg", "two biryani", "dozen eggs".
# Without this, "one" stays in the search query and FatSecret matches
# "Cherry Pie (One Crust)" instead of boiled eggs.
_WORD_NUMBERS = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "dozen": 12.0,
}
_WORD_COUNT_RE = re.compile(
    r'^\s*(' + '|'.join(sorted(_WORD_NUMBERS, key=len, reverse=True)) + r')\s+(?=[a-zA-Z])',
    re.I)


def _singular(word):
    """Singularize a plural word, keeping 'ss' intact ('glasses' → 'glass')."""
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def _extract_qty(text):
    """Extract quantity multiplier and unit from text.

    Returns (qty, unit_food_key_or_None, cleaned_text, gram_mode, ml_mode,
             raw_unit).
    raw_unit is the matched unit word (e.g. "cup", "piece") or None —
    used to match against FatSecret serving descriptions.
    """
    t = text.strip()

    # 1. Try "N × " or "Nx " patterns
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:x|×)\s*', t, re.I)
    if m:
        qty = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return qty, None, cleaned.strip(), False, False, None

    # 2. Try "N <unit>" patterns (roti, egg, bowl, etc.)
    m = _QTY_RE.search(t)
    if m:
        qty_str = m.group(1) or m.group(2)
        unit = _singular((m.group(3) or "").lower())
        qty = float(qty_str) if qty_str else 1.0
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return qty, unit_key, cleaned.strip(), False, False, unit

    # 3. Try "half/quarter <unit>"
    m = _HALF_RE.search(t)
    if m:
        unit = _singular(m.group(2).lower())
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return 0.5, unit_key, cleaned.strip(), False, False, unit

    m = _QUARTER_RE.search(t)
    if m:
        unit = _singular(m.group(2).lower())
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return 0.25, unit_key, cleaned.strip(), False, False, unit

    # 4. Try explicit grams: "200g chicken"
    m = _GRAM_RE.search(t)
    if m:
        grams = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return grams / 100.0, None, cleaned.strip(), True, False, None

    # 5. Try explicit ml: "500ml juice"
    m = _ML_RE.search(t)
    if m:
        ml = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return ml / 100.0, None, cleaned.strip(), True, True, None

    # 6. Generic leading count for countable foods without a known unit
    #    ("2 biryani", "2 big macs"). Not gram_mode — it multiplies servings.
    m = _GENERIC_COUNT_RE.match(t)
    if m:
        qty = float(m.group(1))
        cleaned = t[m.end():].strip()
        return qty, None, cleaned, False, False, None

    # 7. Same but spelled out ("one boiled egg", "two biryani", "dozen eggs").
    m = _WORD_COUNT_RE.match(t)
    if m:
        qty = _WORD_NUMBERS[m.group(1).lower()]
        cleaned = t[m.end():].strip()
        return qty, None, cleaned, False, False, None

    return 1.0, None, t, False, False, None


def _words(text):
    """Extract significant words from text."""
    return [w for w in re.findall(r'[a-z]+', text.lower()) if w not in _STOP and len(w) > 1]


def _singular_words(text):
    """Singularize every word so plural multi-word keys match
    ('chicken breasts' → 'chicken breast')."""
    return " ".join(_singular(w) for w in text.split())


def _calc_nutrition(base_cal, base_p, base_c, base_f, serving_g, qty, gram_mode=False):
    """Calculate nutrition from per-100g base, serving size, and quantity.

    If gram_mode=True, qty is already grams/100 (from explicit "200g" input),
    so we use it directly as the multiplier on per-100g values.
    Otherwise, nutrition = per-100g × (serving_g/100) × qty.
    """
    if gram_mode:
        mult = qty  # qty is already grams/100
    else:
        mult = (serving_g / 100.0) * qty

    return (
        round(base_cal * mult),
        round(base_p * mult, 1),
        round(base_c * mult, 1),
        round(base_f * mult, 1),
    )


def _calc_serving_nutrition(cal, p, c, f, qty):
    """Scale per-serving nutrition by quantity. Values are for ONE serving."""
    return (
        round(cal * qty),
        round(p * qty, 1),
        round(c * qty, 1),
        round(f * qty, 1),
    )


def _build_result(item_name, cal, p, c, f, source, matched_food,
                  serving_g, qty, notes="", needs_clarify=False,
                  clarify_q="", clarify_opts=None, fiber=None, sugar=None):
    """Build the standard food result dict with full audit trail."""
    return {
        "type": "food",
        "item_name": item_name,
        "calories": cal,
        "protein_g": p,
        "carbs_g": c,
        "fat_g": f,
        "fiber_g": fiber,
        "sugar_g": sugar,
        "confidence_notes": notes,
        "needs_clarification": needs_clarify,
        "clarify_question": clarify_q,
        "clarify_options": clarify_opts or [],
        # Audit trail
        "source": source,
        "matched_food": matched_food,
        "serving_g": serving_g,
        "qty": qty,
        "accuracy_warnings": _nutrition_warnings(cal, p, c, f),
    }


def _nutrition_warnings(cal, p, c, f):
    """Flag implausible calorie/macro combinations without changing data."""
    macro_cal = float(p or 0) * 4 + float(c or 0) * 4 + float(f or 0) * 9
    if cal and macro_cal and abs(macro_cal - cal) > max(50, cal * 0.2):
        return [f"Calories and macros differ by {round(abs(macro_cal-cal))} kcal; verify the label or portion."]
    return []


# ─────────────────────────────────────────────────────────────
# LOCAL FOOD LOOKUP
# ─────────────────────────────────────────────────────────────

def _match_food_words(query_words):
    """Find the best per-100g food by word overlap.

    A key only matches when ALL of its words appear in the query (e.g.
    'chicken tikka roll' must NOT become 'chicken breast' just because both
    contain 'chicken'). Returns (food_key, food_data) or None."""
    best = None
    best_score = (0, 0)
    for key, data in FOODS.items():
        if data.get("_per_serving"):
            continue  # skip per-serving entries for word matching
        key_words = set(key.split())
        overlap = len(set(query_words) & key_words)
        if overlap >= len(key_words):  # key fully covered by the query
            score = (overlap, len(key_words))
            if score > best_score:
                best = (key, data)
                best_score = score
    if best:
        return best
    # Retry with plural words singularized ("chicken breasts" → "chicken breast")
    singular_words = [_singular(w) for w in query_words]
    for key, data in FOODS.items():
        if data.get("_per_serving"):
            continue
        key_words = set(key.split())
        overlap = len(set(singular_words) & key_words)
        if overlap >= len(key_words):
            score = (overlap, len(key_words))
            if score > best_score:
                best = (key, data)
                best_score = score
    return best


def parse_local(text):
    """Parse food from text using the local database.

    Strategy:
      1. Try exact multi-item split (by +, and, with, comma)
      2. For each item: extract qty + unit, look up in FOODS
      3. For per-serving items: nutrition = serving_value × qty
      4. For per-100g items: nutrition = per100g × (serving_g/100) × qty
      5. For explicit grams: nutrition = per100g × (grams/100)
    """
    if not text or not text.strip():
        return None

    # Try multi-item first
    multi = _parse_multi(text)
    if multi:
        return multi

    # Multi-item separators present but one item didn't match (e.g.
    # "2 banana and 2 omelette") → return None so the caller routes the
    # WHOLE input to the AI instead of parsing only the first item.
    if re.search(r'[,;+]|\band\b|\bwith\b', text):
        return None

    return _parse_single(text)


def _parse_single(text):
    """Parse a single food item from text."""
    if not text or not text.strip():
        return None

    t = text.strip().lower()
    gram_mode = False

    # Local weighted references for these foods are cooked (oats are dry).
    # Route the opposite preparation to FatSecret instead of mis-scaling it.
    if "raw" in t and any(x in t for x in
                          ("rice", "chicken breast", "chicken thigh", "dal", "pasta", "noodles")):
        return None
    if "cooked" in t and "oats" in t:
        return None

    # Extract quantity + unit
    result = _extract_qty(t)
    qty, unit_key, cleaned, gram_mode, ml_mode, _raw_unit = result

    # Strategy 1: If we got a unit key (e.g. "2 eggs" → "boiled egg")
    if unit_key and unit_key in FOODS:
        data = FOODS[unit_key]
        if data.get("_per_serving"):
            cal, p, c, f = _calc_serving_nutrition(
                data["cal"], data["p"], data["c"], data["f"], qty)
        else:
            cal, p, c, f = _calc_nutrition(
                data["cal"], data["p"], data["c"], data["f"],
                data["serving_g"], qty, gram_mode)

        item_name = _item_name(unit_key, qty, gram_mode, ml_mode, t)
        return _build_result(
            item_name=item_name,
            cal=cal, p=p, c=c, f=f,
            source="local",
            matched_food=unit_key,
            serving_g=data["serving_g"],
            qty=qty,
            notes=f"local DB: {unit_key} ({data['serving_g']}g/serving, qty={qty}x)",
        )

    # Strategy 2: Try matching the cleaned text against FOODS keys
    # (longest match first to avoid substring collisions). Word-wrapped:
    # raw substring match made "cola" match inside "chocolate".
    food_keys_sorted = sorted(FOODS.keys(), key=len, reverse=True)
    t_singular = _singular_words(t)
    for key in food_keys_sorted:
        if re.search(rf'\b{re.escape(key)}\b', t) or re.search(rf'\b{re.escape(key)}\b', t_singular):
            data = FOODS[key]
            if data.get("_per_serving"):
                # Per-serving item matched by substring — use qty from extraction
                cal, p, c, f = _calc_serving_nutrition(
                    data["cal"], data["p"], data["c"], data["f"], qty)
                item_name = _item_name(key, qty, gram_mode, ml_mode, t)
                return _build_result(
                    item_name=item_name,
                    cal=cal, p=p, c=c, f=f,
                    source="local",
                    matched_food=key,
                    serving_g=data["serving_g"],
                    qty=qty,
                    notes=f"local DB: {key} ({data['serving_g']}g/serving, qty={qty}x)",
                )
            else:
                # Per-100g item — use default serving
                cal, p, c, f = _calc_nutrition(
                    data["cal"], data["p"], data["c"], data["f"],
                    data["serving_g"], qty, gram_mode)
                item_name = _item_name(key, qty, gram_mode, ml_mode, t)
                return _build_result(
                    item_name=item_name,
                    cal=cal, p=p, c=c, f=f,
                    source="local",
                    matched_food=key,
                    serving_g=data["serving_g"],
                    qty=qty,
                    notes=f"local DB: {key} ({data['serving_g']}g/serving, qty={qty}x)",
                )

    # Strategy 3: Word-based matching for per-100g items
    words = _words(t)
    if words:
        match = _match_food_words(words)
        if match:
            key, data = match
            cal, p, c, f = _calc_nutrition(
                data["cal"], data["p"], data["c"], data["f"],
                data["serving_g"], qty, gram_mode)
            item_name = _item_name(key, qty, gram_mode, ml_mode, t)
            return _build_result(
                item_name=item_name,
                cal=cal, p=p, c=c, f=f,
                source="local",
                matched_food=key,
                serving_g=data["serving_g"],
                qty=qty,
                notes=f"local DB: {key} ({data['serving_g']}g/serving, qty={qty}x, word match)",
            )

    # Strategy 4: Fuzzy typo match (e.g. "aaple" → "apple").
    # Only for short single-word queries and only if reasonably close.
    if t and len(cleaned) > 2 and " " not in cleaned.strip():
        fuzzy = _match_fuzzy(cleaned.strip())
        if fuzzy:
            key, ratio = fuzzy
            data = FOODS[key]
            cal, p, c, f = _calc_nutrition(
                data["cal"], data["p"], data["c"], data["f"],
                data["serving_g"], qty, gram_mode)
            item_name = _item_name(key, qty, gram_mode, ml_mode, t)
            return _build_result(
                item_name=item_name,
                cal=cal, p=p, c=c, f=f,
                source="local",
                matched_food=key,
                serving_g=data["serving_g"],
                qty=qty,
                notes=f"local DB: {key} ({data['serving_g']}g/serving, qty={qty}x, "
                      f"fuzzy {ratio:.0%})",
            )

    return None


def _match_fuzzy(text):
    """Typo-tolerant match of a short query against single-word food keys.
    Returns (food_key, similarity) if close enough, else None."""
    if not text or len(text) < 3:
        return None
    best, best_ratio = None, 0
    for key in FOODS:
        if "_" in key or " " in key or len(key) < 3:
            continue  # skip alias/multi-word keys — fuzzy is for clean single words
        kw = key.lower()
        if kw == text:
            return key, 1.0
        ratio = difflib.SequenceMatcher(None, text, kw).ratio()
        if ratio > best_ratio:
            best, best_ratio = key, ratio
    if best and best_ratio >= 0.75:
        return best, best_ratio
    return None


def _parse_multi(text):
    """Parse multiple food items separated by +, and, with, comma."""
    parts = re.split(r'[,;+]|\band\b|\bwith\b', text)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return None

    items = []
    total_cal = total_p = total_c = total_f = 0
    matched_names = []
    audit_parts = []

    for part in parts:
        d = _parse_single(part)
        if not d or d["calories"] <= 0:
            # One unrecognized item — return None so the caller routes the
            # WHOLE input to the AI instead of silently dropping this item
            # (e.g. "2 banana and 2 omelette" → AI parses both, not just banana).
            return None
        items.append(d)
        total_cal += d["calories"]
        total_p += d["protein_g"]
        total_c += d["carbs_g"]
        total_f += d["fat_g"]
        matched_names.append(d["item_name"])
        audit_parts.append(
            f"{d['matched_food']}: {d['serving_g']}g × {d['qty']}x = {d['calories']}kcal"
        )

    item_name = " + ".join(matched_names)
    return _build_result(
        item_name=item_name,
        cal=total_cal, p=total_p, c=total_c, f=total_f,
        source="local",
        matched_food="+".join(d["matched_food"] for d in items),
        serving_g=0,  # multi-item
        qty=1,
        notes=f"local DB: {len(items)} items — " + "; ".join(audit_parts),
    )


def _qty_word(qty):
    """Convert numeric qty to a readable word prefix."""
    words = {0.25: "¼", 0.5: "½", 1: "", 2: "2", 3: "3", 4: "4",
             5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10"}
    if qty in words:
        return words[qty]
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:.1f}"


def _item_name(key, qty, gram_mode, ml_mode=False, original_text=""):
    """Human-readable name for a parsed food. In gram/ml mode the qty is a
    per-100 multiplier, so show the real amount ('200g Dal') instead of a
    count ('2 Dal')."""
    if ml_mode:
        return f"{int(round(qty * 100))}ml {key.title()}" if qty * 100 == int(qty * 100) else f"{qty * 100:g}ml {key.title()}"
    if gram_mode:
        return f"{int(round(qty * 100))}g {key.title()}" if qty * 100 == int(qty * 100) else f"{qty * 100:g}g {key.title()}"
    if qty != 1:
        return f"{_qty_word(qty)} {key.title()}"
    explicit_one = bool(re.search(
        r'(?<![\d.])1(?:\.0+)?\s*(?:x\s*)?[a-z]|\bone\s+[a-z]',
        original_text, re.I))
    if explicit_one:
        return f"1 {key.title()}"
    return key.title()


# ─────────────────────────────────────────────────────────────
# FATSECRET API
# ─────────────────────────────────────────────────────────────
_fs_token = None
_fs_token_expires = 0


def _fs_get_token():
    """Get FatSecret OAuth2 token (client credentials)."""
    global _fs_token, _fs_token_expires
    import time
    if _fs_token and time.time() < _fs_token_expires:
        return _fs_token

    client_id = config.FATSECRET_CLIENT_ID
    client_secret = config.FATSECRET_CLIENT_SECRET
    if not client_id or not client_secret:
        return None

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        "https://oauth.fatsecret.com/connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read())
            _fs_token = d["access_token"]
            _fs_token_expires = time.time() + d.get("expires_in", 3600) - 60
            return _fs_token
    except Exception:
        return None


def _fs_search(query, limit=5):
    """Search FatSecret for a food query. Returns list of raw result dicts."""
    token = _fs_get_token()
    if not token:
        return []

    params = urllib.parse.urlencode({
        "method": "foods.search",
        "search_expression": query,
        "format": "json",
        "max_results": limit,
    })
    url = f"https://platform.fatsecret.com/rest/server.api?{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read())
            results = d.get("foods", {}).get("food", [])
            if isinstance(results, dict):
                results = [results]
            return results
    except Exception:
        return []


def _fs_get_food(food_id):
    """Fetch all serving sizes for a FatSecret food result."""
    token = _fs_get_token()
    if not token or not food_id:
        return None

    params = urllib.parse.urlencode({
        "method": "food.get.v2",
        "food_id": food_id,
        "format": "json",
    })
    url = f"https://platform.fatsecret.com/rest/server.api?{params}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("food")
    except Exception:
        return None


def _fs_parse_serving_description(description):
    """Parse FatSecret food_description into per-100g nutrition + serving info.

    FatSecret descriptions look like:
      "Per 100g - Calories: 231kcal | Fat: 3.57g | Carbs: 47.15g | Protein: 7.58g"
    or sometimes:
      "Per 1 cup - Calories: 231kcal | Fat: 3.57g | Carbs: 47.15g | Protein: 7.58g"

    Returns dict with per-100g values + serving info, or None.
    """
    if not description:
        return None

    # Parse the "Per X" part to determine the reference amount
    per_match = re.search(r'Per\s+(\d+(?:\.\d+)?)\s*(g|ml|cups?|pieces?|slices?|servings?)?',
                          description, re.I)
    ref_amount = 100.0
    ref_unit = "g"
    if per_match:
        ref_amount = float(per_match.group(1))
        ref_unit = (per_match.group(2) or "g").lower()
        if per_match.group(2) is None and ref_amount <= 2:
            # "Per 1 burger (215g)" — a count of items, not grams. Treat as
            # one item ≈ nominal 100g so we never scale the listed values by
            # 100x and invent calories.
            ref_unit = "item"
            ref_amount = 100.0

    # Parse macros
    cal = p = c = f = 0
    m = re.search(r'Calories:\s*(\d+)kcal', description)
    if m:
        cal = int(m.group(1))
    m = re.search(r'Protein:\s*([\d.]+)g', description)
    if m:
        p = round(float(m.group(1)), 1)
    m = re.search(r'Carbs:\s*([\d.]+)g', description)
    if m:
        c = round(float(m.group(1)), 1)
    m = re.search(r'Fat:\s*([\d.]+)g', description)
    if m:
        f = round(float(m.group(1)), 1)

    if cal == 0 and p == 0 and c == 0 and f == 0:
        return None

    orig_unit = ref_unit
    orig_amount = ref_amount

    # Convert non-gram units to approximate grams for normalization
    _UNIT_TO_GRAMS = {
        "cup": 240, "cups": 240,
        "piece": 100, "slice": 100, "serving": 100,
    }
    if ref_unit not in ("g", "ml"):
        ref_grams = _UNIT_TO_GRAMS.get(ref_unit, ref_unit)
        if isinstance(ref_grams, (int, float)):
            ref_amount = ref_grams  # now treat as grams
            ref_unit = "g"

    # Keep the values exactly as FatSecret listed them (per serving) — real
    # data, used when the user's unit matches the description's unit.
    per_serving = {"cal": cal, "p": p, "c": c, "f": f}

    # Normalize to per-100g if the reference isn't already 100 (ml ≈ g for
    # beverages: "Per 330ml" values are per 330ml, so scale to per-100ml).
    # Guard against malformed descriptions like "Per 0g".
    scaled = ref_unit in ("g", "ml") and ref_amount > 0 and ref_amount != 100.0
    if scaled:
        scale = 100.0 / ref_amount
        cal = round(cal * scale)
        p = round(p * scale, 1)
        c = round(c * scale, 1)
        f = round(f * scale, 1)

    return {
        "cal_per100g": cal,
        "p_per100g": p,
        "c_per100g": c,
        "f_per100g": f,
        "ref_amount": ref_amount,
        "ref_unit": ref_unit,
        "orig_unit": orig_unit,
        "orig_amount": orig_amount,
        "per_serving": per_serving,
        # Serving the description actually refers to, in grams. Real data
        # for "Per 200g" / "Per 1 burger (215g)"; None when we only have a
        # nominal 100g / 100ml equivalent.
        "real_serving_g": ref_amount if (ref_unit == "g" and scaled
                                         and orig_unit == "g") else None,
    }


def _fs_parse_metric_serving(food, summary=None):
    """Build normalized nutrition from a detailed FatSecret metric serving.

    Prefer the serving represented by the search summary (for example,
    39 pieces), then FatSecret's default, then any serving with a real g/ml
    amount. No piece-to-gram estimate is made locally.
    """
    if not food:
        return None
    servings = food.get("servings", {}).get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]

    metric = []
    for serving in servings:
        try:
            amount = float(serving.get("metric_serving_amount", 0))
        except (TypeError, ValueError):
            continue
        unit = str(serving.get("metric_serving_unit", "")).lower()
        if amount > 0 and unit in ("g", "ml"):
            metric.append((serving, amount, unit))
    if not metric:
        return None

    def rank(entry):
        serving, _amount, _unit = entry
        match = 0
        if summary:
            desc = str(serving.get("serving_description", "")).lower()
            measurement = str(serving.get("measurement_description", "")).lower()
            try:
                units = float(serving.get("number_of_units", 0))
            except (TypeError, ValueError):
                units = 0
            orig_unit = str(summary.get("orig_unit", "")).rstrip("s")
            if orig_unit and (orig_unit in desc or orig_unit in measurement):
                match += 1
            if units and abs(units - float(summary.get("orig_amount", 0))) < 0.01:
                match += 2
        return (match, 1 if str(serving.get("is_default", "")) == "1" else 0)

    serving, amount, unit = max(metric, key=rank)
    try:
        cal = float(serving.get("calories", 0))
        p = float(serving.get("protein", 0))
        c = float(serving.get("carbohydrate", 0))
        f = float(serving.get("fat", 0))
    except (TypeError, ValueError):
        return None
    if not any((cal, p, c, f)):
        return None

    scale = 100.0 / amount
    fiber = None if serving.get("fiber") in (None, "") else float(serving["fiber"])
    sugar = None if serving.get("sugar") in (None, "") else float(serving["sugar"])
    return {
        # Keep precision until the user's requested amount is calculated.
        "cal_per100g": cal * scale,
        "p_per100g": p * scale,
        "c_per100g": c * scale,
        "f_per100g": f * scale,
        "ref_amount": amount,
        "ref_unit": unit,
        "orig_unit": str(serving.get("measurement_description", "serving")).lower(),
        "orig_amount": float(serving.get("number_of_units", 1) or 1),
        "fiber_per100g": None if fiber is None else fiber * scale,
        "sugar_per100g": None if sugar is None else sugar * scale,
        "per_serving": {"cal": cal, "p": p, "c": c, "f": f,
                        "fiber": fiber, "sugar": sugar},
        "real_serving_g": amount if unit == "g" else None,
    }


def _fs_stem(w):
    """Tiny plural stemmer so 'macs' matches 'mac', 'eggs' matches 'egg'."""
    w = w.lower()
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def _fs_semantic_score(query_words, food_name):
    """Score how well a FatSecret result matches the user's query.

    Higher = better match. Uses word overlap + food name length.
    Penalizes brand-specific results (e.g. "Kellogg's" when user said "cornflakes").
    """
    name_lower = food_name.lower()
    name_words = {_fs_stem(w) for w in re.findall(r'[a-z]+', name_lower)}
    query_words = {_fs_stem(w) for w in query_words}

    # Exact word overlap
    overlap = len(query_words & name_words)

    # Bonus for matching the main food concept (first 2 significant words)
    query_bigrams = set()
    q_list = sorted(query_words)
    for i in range(len(q_list)):
        for j in range(i + 1, min(i + 3, len(q_list))):
            query_bigrams.add(f"{q_list[i]} {q_list[j]}")

    name_bigrams = set()
    n_list = sorted(name_words)
    for i in range(len(n_list)):
        for j in range(i + 1, min(i + 3, len(n_list))):
            name_bigrams.add(f"{n_list[i]} {n_list[j]}")

    bigram_overlap = len(query_bigrams & name_bigrams)

    # Penalize brand words (common brands that aren't food names)
    brands = {"kellogg", "nestle", "amul", "parle", "britannia", "haldiram",
              "mdh", "everest", "patanjali", "anna", "tata", "reliance"}
    brand_penalty = -2 if (name_words & brands) else 0

    # Prefer shorter names (more likely to be the generic food, not a variant)
    length_bonus = max(0, 5 - len(n_list))

    return overlap * 2 + bigram_overlap * 3 + brand_penalty + length_bonus


def _fs_pick_best(results, query_words, raw_unit=None):
    """Pick the best FatSecret result using semantic scoring.

    NOT highest calories — uses word overlap, bigram matching, brand penalty.
    Ties break in favor of a result whose serving unit matches the user's
    unit ("Per 1 cup" when the user said "cup").
    """
    if not results:
        return None

    scored = []
    for item in results:
        name = item.get("food_name", "")
        desc = item.get("food_description", "")
        parsed = _fs_parse_serving_description(desc)
        if not parsed or parsed["cal_per100g"] == 0:
            continue
        score = _fs_semantic_score(query_words, name)
        unit_match = bool(raw_unit) and raw_unit == parsed["orig_unit"]
        scored.append((score, 1 if unit_match else 0, parsed, name, item))

    if not scored:
        return None

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0]  # (score, unit_match, parsed, name, raw_item)


def parse_fatsecret(text):
    """Parse food from text using FatSecret search.

    Returns dict with audit trail or None.
    """
    if not config.FATSECRET_CLIENT_ID:
        return None

    words = _words(text)
    if not words:
        return None

    # Extract qty from the original text
    qty, unit_key, cleaned, gram_mode, ml_mode, raw_unit = _extract_qty(text.lower())

    # Build search query from significant words
    search_words = _words(cleaned) if cleaned else words
    search_query = " ".join(search_words[:4])

    results = _fs_search(search_query, limit=5)
    if not results:
        return None

    best = _fs_pick_best(results, words, raw_unit)
    if not best:
        return None

    score, unit_match, parsed, name, raw_item = best

    # Search summaries often say only "Per 39 pieces". Fetch the detailed
    # serving so FatSecret supplies the real metric weight behind that count.
    # Without it, an explicit gram request cannot be converted safely.
    if parsed["orig_unit"] not in ("g", "ml"):
        detailed = _fs_get_food(raw_item.get("food_id"))
        metric = _fs_parse_metric_serving(detailed, parsed)
        if metric:
            parsed = metric
        elif gram_mode:
            return None

    # Serving selection — data-driven, from the FatSecret description:
    #   * user gave explicit grams/ml → scale per-100g by that amount
    #   * user's unit matches the description unit ("1 cup" + "Per 1 cup")
    #     → use the listed per-serving values directly
    #   * description lists a real gram serving other than 100 ("Per 200g")
    #     → use it as the default serving
    #   * otherwise → nominal 100g/100ml serving
    per = parsed["per_serving"]
    if gram_mode:
        serving_g = 100
        cal, p, c, f = _calc_nutrition(
            parsed["cal_per100g"], parsed["p_per100g"],
            parsed["c_per100g"], parsed["f_per100g"],
            serving_g, qty, gram_mode)
    elif raw_unit and raw_unit == parsed["orig_unit"] and per["cal"] > 0:
        # User's unit matches FatSecret's unit (e.g. "10 pieces" + "Per 5 pieces").
        # Scale: multiplier = qty / orig_amount (e.g. 10/5 = 2x).
        multiplier = qty / parsed["orig_amount"] if parsed["orig_amount"] else qty
        serving_g = parsed["ref_amount"] if parsed["orig_unit"] == "cup" else 100
        cal = round(per["cal"] * multiplier)
        p = round(per["p"] * multiplier, 1)
        c = round(per["c"] * multiplier, 1)
        f = round(per["f"] * multiplier, 1)
    elif parsed["real_serving_g"]:
        serving_g = parsed["real_serving_g"]
        cal, p, c, f = _calc_nutrition(
            parsed["cal_per100g"], parsed["p_per100g"],
            parsed["c_per100g"], parsed["f_per100g"],
            serving_g, qty, gram_mode)
    elif parsed["orig_unit"] not in ("g", "ml"):
        # A count-based serving with no metric weight is still valid as the
        # listed serving, but it must never be labelled or scaled as 100g.
        serving_g = 0
        cal = round(per["cal"] * qty)
        p = round(per["p"] * qty, 1)
        c = round(per["c"] * qty, 1)
        f = round(per["f"] * qty, 1)
    else:
        serving_g = 100
        cal, p, c, f = _calc_nutrition(
            parsed["cal_per100g"], parsed["p_per100g"],
            parsed["c_per100g"], parsed["f_per100g"],
            serving_g, qty, gram_mode)

    fiber = sugar = None
    if parsed.get("fiber_per100g") is not None:
        mult = qty if gram_mode else serving_g * qty / 100.0
        fiber = round(parsed["fiber_per100g"] * mult, 1)
    if parsed.get("sugar_per100g") is not None:
        mult = qty if gram_mode else serving_g * qty / 100.0
        sugar = round(parsed["sugar_per100g"] * mult, 1)

    # Build description of what we matched
    desc = raw_item.get("food_description", "")
    per_info = f" ({desc})" if desc else ""

    if gram_mode:
        amount = qty * 100
        unit = "ml" if ml_mode else "g"
        # Display the user's food words; keep FatSecret's normalized match in
        # matched_food/audit metadata instead of replacing the visible title.
        item_name = f"{amount:g}{unit} {search_query.title()}"
        portion_note = (f"requested {amount:g}{unit}; FatSecret metric reference "
                        f"{parsed['ref_amount']:g}{parsed['ref_unit']}")
    else:
        item_name = name.title()
        portion_note = f"qty={qty}x"

    return _build_result(
        item_name=item_name,
        cal=cal, p=p, c=c, f=f,
        source="fatsecret",
        matched_food=name,
        serving_g=serving_g,
        qty=qty,
        notes=f"FatSecret: {name}{per_info} [score={score}, {portion_note}]",
        fiber=fiber, sugar=sugar,
    )


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT — three-tier lookup
# ─────────────────────────────────────────────────────────────

def _parse_tiered_multi(text):
    """Parse mixed local/FatSecret items without dropping any component."""
    clean_text = re.sub(r'^\s*user\s+input\s*:\s*', '', text, flags=re.I)
    parts = [p.strip() for p in re.split(r'[,;+]|\band\b|\bwith\b', clean_text)
             if p.strip()]
    if len(parts) <= 1:
        return None

    items = []
    for part in parts:
        item = parse_local(part)
        if not item or item["calories"] <= 0:
            item = parse_fatsecret(part)
        if not item or item["calories"] <= 0:
            return None
        items.append(item)

    return _build_result(
        item_name=" + ".join(item["item_name"] for item in items),
        cal=sum(item["calories"] for item in items),
        p=round(sum(item["protein_g"] for item in items), 1),
        c=round(sum(item["carbs_g"] for item in items), 1),
        f=round(sum(item["fat_g"] for item in items), 1),
        source="local" if all(item["source"] == "local" for item in items)
        else "fatsecret",
        matched_food="+".join(item["matched_food"] for item in items),
        serving_g=0,
        qty=1,
        notes="; ".join(item["confidence_notes"] for item in items),
    )


def parse_food(text, payload=None):
    """Three-tier food parsing:
    1. Local DB (unlimited, instant)
    2. FatSecret API (free 5k/day)
    3. Gemini (handled by caller — only for ambiguous input parsing)

    Returns dict with audit trail or None.
    """
    if not text:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text.strip():
        return None

    # Tier 1: Local database
    local = parse_local(text)
    if local and local["calories"] > 0:
        return local

    multi = _parse_tiered_multi(text)
    if multi:
        return multi

    # Tier 2: FatSecret
    fs = parse_fatsecret(text)
    if fs and fs["calories"] > 0:
        return fs

    # Tier 3: Gemini (caller handles this)
    return None
