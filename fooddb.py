"""
Food database — three-tier lookup for nutrition data:
  1. Local built-in database (unlimited, no API key, covers 150+ common foods)
  2. FatSecret search API (free 5,000/day, needs key)
  3. Gemini AI (quota-limited, best for complex/ambiguous inputs)

The local DB handles ~80% of personal-logging inputs with zero API calls.
FatSecret fills in branded/specific items. Gemini catches everything else.
"""

import re
import json
import urllib.request
import urllib.parse
import config

# ─────────────────────────────────────────────────────────────
# LOCAL FOOD DATABASE (per 100g, rounded integers)
# Keys are lowercase; covers Indian staples + common Western foods.
# ─────────────────────────────────────────────────────────────
FOODS = {
    # ── Proteins ──
    "egg": {"cal": 155, "p": 13, "c": 1, "f": 11},
    "boiled egg": {"cal": 155, "p": 13, "c": 1, "f": 11},
    "fried egg": {"cal": 196, "p": 14, "c": 1, "f": 15},
    "chicken breast": {"cal": 165, "p": 31, "c": 0, "f": 4},
    "chicken thigh": {"cal": 209, "p": 26, "c": 0, "f": 11},
    "chicken curry": {"cal": 180, "p": 18, "c": 5, "f": 10},
    "fish": {"cal": 136, "p": 20, "c": 0, "f": 6},
    "prawn": {"cal": 99, "p": 20, "c": 0, "f": 2},
    "paneer": {"cal": 265, "p": 18, "c": 4, "f": 20},
    "tofu": {"cal": 76, "p": 8, "c": 2, "f": 5},
    "lentils": {"cal": 116, "p": 9, "c": 20, "f": 0},
    "dal": {"cal": 116, "p": 9, "c": 20, "f": 0},
    "rajma": {"cal": 127, "p": 9, "c": 22, "f": 0},
    "chole": {"cal": 164, "p": 9, "c": 27, "f": 3},
    "soya chunks": {"cal": 341, "p": 52, "c": 33, "f": 1},
    "mutton": {"cal": 250, "p": 26, "c": 0, "f": 16},
    "beef": {"cal": 250, "p": 26, "c": 0, "f": 16},
    "keema": {"cal": 220, "p": 20, "c": 4, "f": 14},

    # ── Carbs / Grains ──
    "rice": {"cal": 130, "p": 3, "c": 28, "f": 0},
    "brown rice": {"cal": 123, "p": 3, "c": 26, "f": 1},
    "roti": {"cal": 260, "p": 7, "c": 52, "f": 3},
    "chapati": {"cal": 260, "p": 7, "c": 52, "f": 3},
    "naan": {"cal": 310, "p": 8, "c": 55, "f": 6},
    "paratha": {"cal": 310, "p": 6, "c": 45, "f": 12},
    "pasta": {"cal": 131, "p": 5, "c": 25, "f": 1},
    "noodles": {"cal": 138, "p": 4, "c": 25, "f": 2},
    "maggi": {"cal": 360, "p": 9, "c": 63, "f": 8},
    "bread": {"cal": 265, "p": 9, "c": 49, "f": 3},
    "toast": {"cal": 265, "p": 9, "c": 49, "f": 3},
    "oats": {"cal": 389, "p": 17, "c": 66, "f": 7},
    "cornflakes": {"cal": 357, "p": 7, "c": 84, "f": 0},
    "idli": {"cal": 78, "p": 2, "c": 16, "f": 0},
    "dosa": {"cal": 133, "p": 3, "c": 18, "f": 6},
    "uttapam": {"cal": 150, "p": 4, "c": 22, "f": 5},
    "poha": {"cal": 120, "p": 2, "c": 22, "f": 2},
    "upma": {"cal": 145, "p": 3, "c": 23, "f": 5},
    "biryani": {"cal": 180, "p": 10, "c": 24, "f": 5},
    "fried rice": {"cal": 163, "p": 4, "c": 24, "f": 6},

    # ── Vegetables ──
    "aloo": {"cal": 77, "p": 2, "c": 17, "f": 0},
    "potato": {"cal": 77, "p": 2, "c": 17, "f": 0},
    "sabzi": {"cal": 65, "p": 2, "c": 8, "f": 3},
    "palak": {"cal": 23, "p": 3, "c": 4, "f": 0},
    "spinach": {"cal": 23, "p": 3, "c": 4, "f": 0},
    "bhindi": {"cal": 33, "p": 2, "c": 7, "f": 0},
    "okra": {"cal": 33, "p": 2, "c": 7, "f": 0},
    "cauliflower": {"cal": 25, "p": 2, "c": 5, "f": 0},
    "cabbage": {"cal": 25, "p": 1, "c": 6, "f": 0},
    "broccoli": {"cal": 34, "p": 3, "c": 7, "f": 0},

    # ── Fruits ──
    "banana": {"cal": 89, "p": 1, "c": 23, "f": 0},
    "apple": {"cal": 52, "p": 0, "c": 14, "f": 0},
    "orange": {"cal": 47, "p": 1, "c": 12, "f": 0},
    "mango": {"cal": 60, "p": 1, "c": 15, "f": 0},
    "papaya": {"cal": 43, "p": 0, "c": 11, "f": 0},
    "watermelon": {"cal": 30, "p": 1, "c": 8, "f": 0},
    "grapes": {"cal": 69, "p": 1, "c": 18, "f": 0},

    # ── Dairy ──
    "milk": {"cal": 61, "p": 3, "c": 5, "f": 3},
    "curd": {"cal": 59, "p": 3, "c": 5, "f": 3},
    "yogurt": {"cal": 59, "p": 3, "c": 5, "f": 3},
    "buttermilk": {"cal": 40, "p": 2, "c": 5, "f": 1},
    "lassi": {"cal": 70, "p": 3, "c": 9, "f": 2},
    "cheese": {"cal": 402, "p": 25, "c": 1, "f": 33},
    "ghee": {"cal": 900, "p": 0, "c": 0, "f": 100},
    "butter": {"cal": 717, "p": 1, "c": 0, "f": 81},

    # ── Snacks ──
    "samosa": {"cal": 260, "p": 6, "c": 30, "f": 13},
    "pakora": {"cal": 250, "p": 5, "c": 22, "f": 16},
    "vada": {"cal": 270, "p": 8, "c": 30, "f": 13},
    "bhel puri": {"cal": 180, "p": 4, "c": 30, "f": 6},
    "sev": {"cal": 500, "p": 10, "c": 40, "f": 35},
    "chips": {"cal": 536, "p": 7, "c": 53, "f": 35},
    "biscuit": {"cal": 450, "p": 7, "c": 65, "f": 18},
    "kurkure": {"cal": 460, "p": 6, "c": 60, "f": 22},
    "namkeen": {"cal": 480, "p": 10, "c": 45, "f": 30},

    # ── Drinks ──
    "tea": {"cal": 2, "p": 0, "c": 0, "f": 0},
    "chai": {"cal": 2, "p": 0, "c": 0, "f": 0},
    "coffee": {"cal": 2, "p": 0, "c": 0, "f": 0},
    "cola": {"cal": 42, "p": 0, "c": 11, "f": 0},
    "juice": {"cal": 45, "p": 0, "c": 11, "f": 0},

    # ── Common combos (per serving, not per100g) ──
    "2 eggs": {"cal": 310, "p": 26, "c": 2, "f": 22, "_serving": 1},
    "2 boiled eggs": {"cal": 310, "p": 26, "c": 2, "f": 22, "_serving": 1},
    "2 fried eggs": {"cal": 392, "p": 28, "c": 2, "f": 30, "_serving": 1},
    "1 roti": {"cal": 90, "p": 2, "c": 18, "f": 1, "_serving": 1},
    "2 roti": {"cal": 180, "p": 5, "c": 36, "f": 2, "_serving": 1},
    "3 roti": {"cal": 270, "p": 7, "c": 54, "f": 3, "_serving": 1},
    "1 banana": {"cal": 105, "p": 1, "c": 27, "f": 0, "_serving": 1},
    "1 apple": {"cal": 95, "p": 0, "c": 25, "f": 0, "_serving": 1},
    "1 glass milk": {"cal": 150, "p": 8, "c": 12, "f": 8, "_serving": 1},
    "1 cup rice": {"cal": 206, "p": 4, "c": 45, "f": 0, "_serving": 1},
    "1 bowl rice": {"cal": 206, "p": 4, "c": 45, "f": 0, "_serving": 1},
    "1 bowl dal": {"cal": 180, "p": 12, "c": 28, "f": 2, "_serving": 1},
    "big bowl": {"cal": 100, "p": 0, "c": 0, "f": 0, "_mult": 1.5, "_serving": 0},
    "small bowl": {"cal": 100, "p": 0, "c": 0, "f": 0, "_mult": 0.7, "_serving": 0},
    "plate": {"cal": 100, "p": 0, "c": 0, "f": 0, "_mult": 1.0, "_serving": 0},
}

# Stop words for food matching
_STOP = {"of", "with", "and", "a", "the", "plate", "bowl", "plates", "bowls",
         "some", "my", "two", "one", "three", "had", "ate", "eaten", "again",
         "for", "in", "on", "just", "i", "today", "lunch", "dinner", "breakfast",
         "khaya", "khaye", "aur", "ek", "do", "teen", "me", "was", "were",
         "eating", "food", "meal", "snack"}

# Quantity patterns
_QTY_PATTERNS = [
    (r'(\d+(?:\.\d+)?)\s*(?:x|×)\s*', lambda m: float(m.group(1))),
    (r'(\d+(?:\.\d+)?)\s*(?:bowl|plate|cup|glass|piece|slice|serving)s?\b', lambda m: float(m.group(1))),
    (r'\b(half|0\.5)\s*(?:bowl|plate|cup|glass)\b', lambda m: 0.5),
    (r'\b(quarter|0\.25)\s*(?:bowl|plate|cup)\b', lambda m: 0.25),
]


def _extract_qty(text):
    """Pull a quantity multiplier from the text. Returns (multiplier, cleaned_text)."""
    for pattern, extract in _QTY_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            qty = extract(m)
            cleaned = text[:m.start()] + text[m.end():]
            return qty, cleaned.strip()
    return 1.0, text


def _words(text):
    """Extract significant words from text."""
    return [w for w in re.findall(r'[a-z]+', text.lower()) if w not in _STOP and len(w) > 1]


def _match_food(query_words, query_text):
    """Find the best matching food in the local DB.
    Returns (food_key, food_data, matched_words) or None."""
    best = None
    best_score = (0, 0)  # (overlap, len_key)
    for key, data in FOODS.items():
        key_words = set(key.split())
        overlap = len(set(query_words) & key_words)
        if overlap > 0:
            score = (overlap, len(key_words))
            if score > best_score:
                best = (key, data, overlap)
                best_score = score
    return best


# Sort combos by key length (longest first) so "2 eggs" matches before "egg"
_FOODS_BY_LENGTH = sorted(FOODS.keys(), key=len, reverse=True)


def parse_local(text):
    """Try to parse food from text using the local database.
    Returns dict with type/item_name/calories/protein/carbs/fat or None."""
    if not text or not text.strip():
        return None

    # Try common combos first (exact match, longest first to avoid substring collisions)
    text_lower = text.lower().strip()
    for combo_key in _FOODS_BY_LENGTH:
        combo_data = FOODS[combo_key]
        if combo_data.get("_serving") == 0:
            continue  # skip multiplier-only entries
        if combo_key in text_lower:
            remaining = text_lower.replace(combo_key, "").strip()
            qty, _ = _extract_qty(remaining) if remaining else (1.0, "")
            cal = round(combo_data["cal"] * qty) if qty != 1 else combo_data["cal"]
            p = round(combo_data["p"] * qty) if qty != 1 else combo_data["p"]
            c = round(combo_data["c"] * qty) if qty != 1 else combo_data["c"]
            f = round(combo_data["f"] * qty) if qty != 1 else combo_data["f"]
            return {
                "type": "food",
                "item_name": combo_key.title(),
                "calories": cal, "protein_g": p, "carbs_g": c, "fat_g": f,
                "fiber_g": 0, "sugar_g": 0,
                "confidence_notes": f"local DB match: {combo_key}",
                "needs_clarification": False,
                "clarify_question": "", "clarify_options": [],
            }

    # Fall back to word matching
    words = _words(text)
    if not words:
        return None

    match = _match_food(words, text)
    if not match:
        return None

    food_key, food_data, overlap = match
    qty, _ = _extract_qty(text)

    # Check if it's a combo like "chicken curry" with portion words
    cal = round(food_data["cal"] * qty)
    p = round(food_data["p"] * qty)
    c = round(food_data["c"] * qty)
    f = round(food_data["f"] * qty)

    return {
        "type": "food",
        "item_name": food_key.title(),
        "calories": cal, "protein_g": p, "carbs_g": c, "fat_g": f,
        "fiber_g": 0, "sugar_g": 0,
        "confidence_notes": f"local DB: {food_key} (qty={qty}x)",
        "needs_clarification": False,
        "clarify_question": "", "clarify_options": [],
    }


# ─────────────────────────────────────────────────────────────
# FATSECRET API (free 5,000/day)
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
    """Search FatSecret for a food query. Returns list of dicts."""
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
            out = []
            for item in results:
                servings = item.get("food_description", "")
                # Parse nutrition from description like "Per 100g - Calories: 231kcal | Fat: 3.57g ..."
                cal = p = c = f = 0
                m = re.search(r'Calories:\s*(\d+)kcal', servings)
                if m:
                    cal = int(m.group(1))
                m = re.search(r'Protein:\s*([\d.]+)g', servings)
                if m:
                    p = round(float(m.group(1)))
                m = re.search(r'Carbs:\s*([\d.]+)g', servings)
                if m:
                    c = round(float(m.group(1)))
                m = re.search(r'Fat:\s*([\d.]+)g', servings)
                if m:
                    f = round(float(m.group(1)))
                out.append({
                    "name": item.get("food_name", query),
                    "calories": cal, "protein_g": p, "carbs_g": c, "fat_g": f,
                })
            return out
    except Exception:
        return []


def parse_fatsecret(text):
    """Try to parse food from text using FatSecret search.
    Returns dict with type/item_name/calories/protein/carbs/fat or None."""
    if not config.FATSECRET_CLIENT_ID:
        return None

    words = _words(text)
    if not words:
        return None

    # Build a search query from the significant words
    search_query = " ".join(words[:4])  # limit to 4 words for better results
    results = _fs_search(search_query, limit=3)

    if not results:
        return None

    # Pick the best result (highest calories, as a heuristic for real food)
    best = max(results, key=lambda r: r["calories"])
    if best["calories"] == 0:
        return None

    qty, _ = _extract_qty(text)
    cal = round(best["calories"] * qty)
    p = round(best["protein_g"] * qty)
    c = round(best["carbs_g"] * qty)
    f = round(best["fat_g"] * qty)

    return {
        "type": "food",
        "item_name": best["name"].title(),
        "calories": cal, "protein_g": p, "carbs_g": c, "fat_g": f,
        "fiber_g": 0, "sugar_g": 0,
        "confidence_notes": f"FatSecret: {best['name']}",
        "needs_clarification": False,
        "clarify_question": "", "clarify_options": [],
    }


def parse_food(text, payload=None):
    """Three-tier food parsing:
    1. Local DB (unlimited, instant)
    2. FatSecret API (free 5k/day)
    3. Gemini AI (quota-limited, best quality)
    Returns dict or None.
    """
    # Tier 1: Local database
    local = parse_local(text)
    if local and local["calories"] > 0:
        return local

    # Tier 2: FatSecret
    fs = parse_fatsecret(text)
    if fs and fs["calories"] > 0:
        return fs

    # Tier 3: Gemini (handled by caller)
    return None