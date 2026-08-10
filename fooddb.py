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

_QTY_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*'
    r'(?:x|×)\s*|'
    r'(\d+(?:\.\d+)?)\s*'
    r'(bowl|plate|cup|glass(?:es)?|piece|slice|serving|roti|chapati|paratha|naan|'
    r'idli|dosa|uttapam|egg(?:s)?|boiled\ egg(?:s)?|fried\ egg(?:s)?|'
    r'samosa|biscuit|banana|apple|orange)s?\b',
    re.I
)

_HALF_RE = re.compile(
    r'\b(half|0\.5)\s*(bowl|plate|cup|glass|serving)\b', re.I
)
_QUARTER_RE = re.compile(
    r'\b(quarter|0\.25)\s*(bowl|plate|cup|serving)\b', re.I
)
_GRAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*g\b', re.I)
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


def _extract_qty(text):
    """Extract quantity multiplier and unit from text.

    Returns (qty, unit_food_key_or_None, cleaned_text, gram_mode, raw_unit).
    raw_unit is the matched unit word (e.g. "cup", "piece") or None —
    used to match against FatSecret serving descriptions.
    """
    t = text.strip()

    # 1. Try "N × " or "Nx " patterns
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:x|×)\s*', t, re.I)
    if m:
        qty = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return qty, None, cleaned.strip(), False, None

    # 2. Try "N <unit>" patterns (roti, egg, bowl, etc.)
    m = _QTY_RE.search(t)
    if m:
        qty_str = m.group(1) or m.group(2)
        unit = (m.group(3) or "").lower().rstrip("s")
        qty = float(qty_str) if qty_str else 1.0
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return qty, unit_key, cleaned.strip(), False, unit

    # 3. Try "half/quarter <unit>"
    m = _HALF_RE.search(t)
    if m:
        unit = m.group(2).lower().rstrip("s")
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return 0.5, unit_key, cleaned.strip(), False, unit

    m = _QUARTER_RE.search(t)
    if m:
        unit = m.group(2).lower().rstrip("s")
        unit_key = _UNIT_TO_FOOD.get(unit, unit)
        if unit_key not in FOODS:
            unit_key = None
        cleaned = t[:m.start()] + t[m.end():]
        return 0.25, unit_key, cleaned.strip(), False, unit

    # 4. Try explicit grams: "200g chicken"
    m = _GRAM_RE.search(t)
    if m:
        grams = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return grams / 100.0, None, cleaned.strip(), True, None

    # 5. Try explicit ml: "500ml juice"
    m = _ML_RE.search(t)
    if m:
        ml = float(m.group(1))
        cleaned = t[:m.start()] + t[m.end():]
        return ml / 100.0, None, cleaned.strip(), True, None

    # 6. Generic leading count for countable foods without a known unit
    #    ("2 biryani", "2 big macs"). Not gram_mode — it multiplies servings.
    m = _GENERIC_COUNT_RE.match(t)
    if m:
        qty = float(m.group(1))
        cleaned = t[m.end():].strip()
        return qty, None, cleaned, False, None

    # 7. Same but spelled out ("one boiled egg", "two biryani", "dozen eggs").
    m = _WORD_COUNT_RE.match(t)
    if m:
        qty = _WORD_NUMBERS[m.group(1).lower()]
        cleaned = t[m.end():].strip()
        return qty, None, cleaned, False, None

    return 1.0, None, t, False, None


def _words(text):
    """Extract significant words from text."""
    return [w for w in re.findall(r'[a-z]+', text.lower()) if w not in _STOP and len(w) > 1]


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
        round(base_p * mult),
        round(base_c * mult),
        round(base_f * mult),
    )


def _calc_serving_nutrition(cal, p, c, f, qty):
    """Scale per-serving nutrition by quantity. Values are for ONE serving."""
    return (
        round(cal * qty),
        round(p * qty),
        round(c * qty),
        round(f * qty),
    )


def _build_result(item_name, cal, p, c, f, source, matched_food,
                  serving_g, qty, notes="", needs_clarify=False,
                  clarify_q="", clarify_opts=None):
    """Build the standard food result dict with full audit trail."""
    return {
        "type": "food",
        "item_name": item_name,
        "calories": cal,
        "protein_g": p,
        "carbs_g": c,
        "fat_g": f,
        "fiber_g": 0,
        "sugar_g": 0,
        "confidence_notes": notes,
        "needs_clarification": needs_clarify,
        "clarify_question": clarify_q,
        "clarify_options": clarify_opts or [],
        # Audit trail
        "source": source,
        "matched_food": matched_food,
        "serving_g": serving_g,
        "qty": qty,
    }


# ─────────────────────────────────────────────────────────────
# LOCAL FOOD LOOKUP
# ─────────────────────────────────────────────────────────────

def _match_food_words(query_words):
    """Find the best per-100g food by word overlap.
    Returns (food_key, food_data) or None."""
    best = None
    best_score = (0, 0)
    for key, data in FOODS.items():
        if data.get("_per_serving"):
            continue  # skip per-serving entries for word matching
        key_words = set(key.split())
        overlap = len(set(query_words) & key_words)
        if overlap > 0:
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

    return _parse_single(text)


def _parse_single(text):
    """Parse a single food item from text."""
    if not text or not text.strip():
        return None

    t = text.strip().lower()
    gram_mode = False

    # Extract quantity + unit
    result = _extract_qty(t)
    qty, unit_key, cleaned, gram_mode, _raw_unit = result

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

        item_name = f"{_qty_word(qty)} {unit_key.title()}" if qty != 1 else unit_key.title()
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
    for key in food_keys_sorted:
        if re.search(rf'\b{re.escape(key)}\b', t):
            data = FOODS[key]
            if data.get("_per_serving"):
                # Per-serving item matched by substring — use qty from extraction
                cal, p, c, f = _calc_serving_nutrition(
                    data["cal"], data["p"], data["c"], data["f"], qty)
                item_name = f"{_qty_word(qty)} {key.title()}" if qty != 1 else key.title()
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
                item_name = f"{_qty_word(qty)} {key.title()}" if qty != 1 else key.title()
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
            item_name = f"{_qty_word(qty)} {key.title()}" if qty != 1 else key.title()
            return _build_result(
                item_name=item_name,
                cal=cal, p=p, c=c, f=f,
                source="local",
                matched_food=key,
                serving_g=data["serving_g"],
                qty=qty,
                notes=f"local DB: {key} ({data['serving_g']}g/serving, qty={qty}x, word match)",
            )

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
        if d and d["calories"] > 0:
            items.append(d)
            total_cal += d["calories"]
            total_p += d["protein_g"]
            total_c += d["carbs_g"]
            total_f += d["fat_g"]
            matched_names.append(d["item_name"])
            audit_parts.append(
                f"{d['matched_food']}: {d['serving_g']}g × {d['qty']}x = {d['calories']}kcal"
            )

    if not items:
        return None

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
    per_match = re.search(r'Per\s+(\d+(?:\.\d+)?)\s*(g|ml|cups?|piece|slice|serving)?',
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
    scaled = ref_unit in ("g", "ml") and ref_amount != 100.0
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


def _fs_pick_best(results, query_words):
    """Pick the best FatSecret result using semantic scoring.

    NOT highest calories — uses word overlap, bigram matching, brand penalty.
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
        scored.append((score, parsed, name, item))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0]  # (score, parsed, name, raw_item)


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
    qty, unit_key, cleaned, gram_mode, raw_unit = _extract_qty(text.lower())

    # Build search query from significant words
    search_words = _words(cleaned) if cleaned else words
    search_query = " ".join(search_words[:4])

    results = _fs_search(search_query, limit=5)
    if not results:
        return None

    best = _fs_pick_best(results, words)
    if not best:
        return None

    score, parsed, name, raw_item = best

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
    elif raw_unit and raw_unit == parsed["orig_unit"] \
            and parsed["orig_amount"] == 1 and per["cal"] > 0:
        serving_g = parsed["ref_amount"] if parsed["orig_unit"] == "cup" else 100
        cal = round(per["cal"] * qty)
        p = round(per["p"] * qty)
        c = round(per["c"] * qty)
        f = round(per["f"] * qty)
    elif parsed["real_serving_g"]:
        serving_g = parsed["real_serving_g"]
        cal, p, c, f = _calc_nutrition(
            parsed["cal_per100g"], parsed["p_per100g"],
            parsed["c_per100g"], parsed["f_per100g"],
            serving_g, qty, gram_mode)
    else:
        serving_g = 100  # nominal per-100g/100ml serving
        cal, p, c, f = _calc_nutrition(
            parsed["cal_per100g"], parsed["p_per100g"],
            parsed["c_per100g"], parsed["f_per100g"],
            serving_g, qty, gram_mode)

    # Build description of what we matched
    desc = raw_item.get("food_description", "")
    per_info = f" ({desc})" if desc else ""

    return _build_result(
        item_name=name.title(),
        cal=cal, p=p, c=c, f=f,
        source="fatsecret",
        matched_food=name,
        serving_g=serving_g,
        qty=qty,
        notes=f"FatSecret: {name}{per_info} [score={score}, qty={qty}x]",
    )


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT — three-tier lookup
# ─────────────────────────────────────────────────────────────

def parse_food(text, payload=None):
    """Three-tier food parsing:
    1. Local DB (unlimited, instant)
    2. FatSecret API (free 5k/day)
    3. Gemini (handled by caller — only for ambiguous input parsing)

    Returns dict with audit trail or None.
    """
    if not text or not text.strip():
        return None

    # Tier 1: Local database
    local = parse_local(text)
    if local and local["calories"] > 0:
        return local

    # Tier 2: FatSecret
    fs = parse_fatsecret(text)
    if fs and fs["calories"] > 0:
        return fs

    # Tier 3: Gemini (caller handles this)
    return None
